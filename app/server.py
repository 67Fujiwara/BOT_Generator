"""bot-team チャット入口。ブラウザからの文章＋ドロップしたファイルを Claude Agent SDK に渡す。

起動:  python app/server.py   → http://127.0.0.1:8765
依存:  pip install fastapi uvicorn python-multipart claude-agent-sdk
認証:  ANTHROPIC_API_KEY か、Claude Code にログイン済みであること
"""
import asyncio
import json
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent.parent      # bot-team/
INBOX = ROOT / "inbox"
AGENTS_DIR = ROOT / ".claude" / "agents"
HOST, PORT = "127.0.0.1", 8765

app = FastAPI()
app.mount("/static", StaticFiles(directory=ROOT / "app" / "static"), name="static")

HISTORY = ROOT / "app" / "history.json"   # ボットごとの会話ログ（左の一覧のプレビュー用）
_sessions: dict[str, str] = {}             # bot id → SDK session id（会話の続きを保つ）
_history: dict[str, list] = json.loads(HISTORY.read_text(encoding="utf-8")) if HISTORY.exists() else {}
PALETTE = ["#E8503A", "#2E8B57", "#1F5FBF", "#D98A1F", "#7B4FB3", "#17A2A8", "#B33A6E", "#6D6E71"]


def _save_history():
    HISTORY.write_text(json.dumps(_history, ensure_ascii=False, indent=1), encoding="utf-8")


def _front(path: Path) -> dict:
    txt = path.read_text(encoding="utf-8")
    head = txt.split("---")[1] if txt.startswith("---") else ""
    out = {}
    for key in ("name", "description", "model", "group", "color"):
        m = re.search(rf"^{key}:\s*(.+)$", head, re.M)
        if m:
            out[key] = m.group(1).strip()
    m = re.search(r"^tools:\s*\[(.*)\]", head, re.M) or re.search(r"^tools:\s*\n((?:\s+-\s*.+\n?)+)", head, re.M)
    out["tools"] = [t.strip(" -") for t in re.split(r"[,\n]", m.group(1)) if t.strip(" -")] if m else []
    return out


def list_bots() -> list[dict]:
    """.claude/agents/*.md のフロントマターから一覧を作る。"""
    bots = []
    for i, p in enumerate(sorted(AGENTS_DIR.glob("*.md"))):
        f = _front(p)
        bid = f.get("name", p.stem)
        d = f.get("description", "")
        nick = d.split("。")[0] if "。" in d else bid
        last = (_history.get(bid) or [{}])[-1]
        bots.append({
            "id": bid, "nick": nick, "file": p.name,
            "desc": d.split("。", 1)[1].strip() if "。" in d else d,
            "group": f.get("group", "未分類"), "model": f.get("model", "既定"),
            "tools": f["tools"], "color": f.get("color", PALETTE[i % len(PALETTE)]),
            "preview": (last.get("text") or "")[:60].replace("\n", " "),
            "status": "",
        })
    return bots


@app.get("/history/{bot}")
def history(bot: str):
    return _history.get(bot, [])


@app.get("/")
def index():
    return FileResponse(ROOT / "app" / "static" / "index.html")


@app.get("/bots")
def bots():
    return list_bots()


@app.get("/inbox")
def inbox():
    """右パネル「コンピューター」用: 最近置かれたファイル。"""
    items = sorted(INBOX.rglob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [{"path": str(p.relative_to(ROOT)), "at": p.stat().st_mtime} for p in items if p.is_file() and p.name != ".gitkeep"][:30]


@app.post("/chat")
async def chat(message: str = Form(""), bot: str = Form(""), new_session: bool = Form(False),
               files: list[UploadFile] = File(default=[])):
    bot_id = bot or "_auto"
    nick = next((b["nick"] for b in list_bots() if b["id"] == bot_id), "")
    if new_session:
        _sessions.pop(bot_id, None)
        _history[bot_id] = []

    # ドロップされたファイルは inbox/日時/ に保存して、パスを本文に添える
    saved: list[Path] = []
    if files:
        d = INBOX / datetime.now().strftime("%Y%m%d-%H%M%S")
        d.mkdir(parents=True, exist_ok=True)
        for f in files:
            if not f.filename:
                continue
            dst = d / Path(f.filename).name
            with dst.open("wb") as out:
                shutil.copyfileobj(f.file, out)
            saved.append(dst)

    prompt = message.strip()
    if nick:
        prompt = f"{nick}、{prompt}" if prompt else f"{nick}、お願いします"
    if saved:
        prompt += "\n\nファイル:\n" + "\n".join(f"- {p.relative_to(ROOT)}" for p in saved)

    _history.setdefault(bot_id, []).append({"role": "me", "text": message.strip(),
                                            "files": [p.name for p in saved], "at": time.time()})

    async def stream():
        yield _sse({"type": "start", "prompt": prompt})
        reply = []
        try:
            from claude_agent_sdk import ClaudeAgentOptions, query
            from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock

            opts = ClaudeAgentOptions(
                cwd=str(ROOT),
                setting_sources=["project"],            # .claude/agents と CLAUDE.md を読む
                allowed_tools=["Read", "Glob", "Grep", "Bash", "Agent", "Task"],
                permission_mode="acceptEdits",
                resume=_sessions.get(bot_id),
            )
            t0 = time.time()
            async for msg in query(prompt=prompt, options=opts):
                if isinstance(msg, AssistantMessage):
                    for b in msg.content:
                        if isinstance(b, TextBlock) and b.text.strip():
                            reply.append(b.text)
                            yield _sse({"type": "text", "text": b.text})
                        elif isinstance(b, ToolUseBlock):
                            label = b.name
                            if b.name in ("Agent", "Task"):
                                label = f"{b.input.get('subagent_type', '')} に委譲"
                            elif b.name == "Bash":
                                label = (b.input.get("command") or "")[:90]
                            yield _sse({"type": "tool", "text": label})
                elif isinstance(msg, ResultMessage):
                    sid = getattr(msg, "session_id", None)
                    if sid:
                        _sessions[bot_id] = sid
                    _history[bot_id].append({"role": "bot", "text": "\n\n".join(reply), "at": time.time()})
                    _save_history()
                    yield _sse({"type": "done", "sec": round(time.time() - t0, 1)})
        except ImportError:
            yield _sse({"type": "error", "text": "claude-agent-sdk が入っていません: pip install claude-agent-sdk"})
        except Exception as e:  # noqa: BLE001
            yield _sse({"type": "error", "text": f"{type(e).__name__}: {e}"})

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _sse(obj: dict) -> str:
    return "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"


if __name__ == "__main__":
    import uvicorn
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    print(f"bot-team: http://{HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
