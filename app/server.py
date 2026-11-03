"""bot-team チャット入口。ブラウザからの文章＋ドロップしたファイルを Claude Agent SDK に渡す。

起動:  run.bat（窓を出さずに裏で起動してブラウザを開く）/ python app/server.py --open
終了:  画面左上の ⏻ ボタン（POST /shutdown）
依存:  pip install fastapi uvicorn python-multipart claude-agent-sdk
認証:  画面左上の ⚙ で API キーを保存（app/config.json）するか、
       環境変数 ANTHROPIC_API_KEY か、Claude Code にログイン済みであること
"""
import asyncio
import json
import os
import re
import shutil
import socket
import sys
import threading
import webbrowser
import time
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent.parent      # bot-team/


def _hide_child_windows():
    """Agent SDK が起動する Claude Code (claude.exe) にコンソール窓を出させない。
    pythonw（窓なし）から子プロセスを起動すると Windows が新しい窓を付けるので、
    SDK が使う anyio.open_process を包んで CREATE_NO_WINDOW を足す。Windows 以外は素通し。"""
    import subprocess

    import anyio

    orig = anyio.open_process
    flag = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0

    async def open_process_no_window(*args, **kw):
        if flag:
            kw["creationflags"] = kw.get("creationflags", 0) | flag
        return await orig(*args, **kw)

    anyio.open_process = open_process_no_window


_hide_child_windows()
INBOX = ROOT / "inbox"
AGENTS_DIR = ROOT / ".claude" / "agents"
HOST, PORT = "127.0.0.1", 8765
CODE_STAMP = f"{os.path.getmtime(__file__):.0f}-{os.path.getsize(__file__)}"   # 起動時のコードの印。古い常駐を見分ける

app = FastAPI()
app.mount("/static", StaticFiles(directory=ROOT / "app" / "static"), name="static")

HISTORY = ROOT / "app" / "history.json"   # ボットごとの会話ログ（左の一覧のプレビュー用）
CONFIG = ROOT / "app" / "config.json"     # 画面から保存した設定（API キーなど。git 対象外）
_sessions: dict[str, str] = {}             # bot id → SDK session id（会話の続きを保つ）
_history: dict[str, list] = json.loads(HISTORY.read_text(encoding="utf-8")) if HISTORY.exists() else {}
PALETTE = ["#F28B82", "#7BCF9C", "#8AB4F8", "#FDD663", "#C58AF9", "#78D9EC", "#FF8BCB", "#F6A96B"]  # 柔らかめ


def _save_history():
    HISTORY.write_text(json.dumps(_history, ensure_ascii=False, indent=1), encoding="utf-8")


def _load_config() -> dict:
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8")) if CONFIG.exists() else {}
    except (OSError, ValueError):
        return {}


def _save_config(cfg: dict):
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        CONFIG.chmod(0o600)  # 自分だけ読める（Windows では無視される）
    except OSError:
        pass


def _sdk_env() -> dict[str, str]:
    """画面で保存した API キーがあれば、環境変数より優先して Claude Code に渡す。"""
    key = _load_config().get("api_key", "")
    return {"ANTHROPIC_API_KEY": key} if key else {}


def _auth_hint(text: str) -> str:
    if "api key" in text.lower() or "authentication" in text.lower() or "not logged in" in text.lower():
        return text + "\n→ 左上の ⚙ から API キーを設定してください。"
    return text


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


def _agent_def(bot_id: str) -> dict | None:
    """ボット定義（フロントマター＋本文）。本文はそのままシステムプロンプトに使う。"""
    for p in AGENTS_DIR.glob("*.md"):
        f = _front(p)
        if f.get("name", p.stem) == bot_id:
            txt = p.read_text(encoding="utf-8")
            body = txt.split("---", 2)[2].strip() if txt.startswith("---") else txt
            return {**f, "body": body}
    return None


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


@app.get("/version")
def version():
    return {"stamp": CODE_STAMP}


@app.post("/shutdown")
def shutdown():
    """画面の ⏻ から呼ばれる。応答を返してからプロセスを終了する。"""
    threading.Timer(0.4, os._exit, [0]).start()
    return {"ok": True}


@app.get("/settings")
def get_settings():
    key = _load_config().get("api_key", "")
    return {
        "api_key_set": bool(key),
        "api_key_hint": f"…{key[-4:]}" if key else "",
        "env_key_set": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "config_file": str(CONFIG.relative_to(ROOT)),
    }


@app.post("/settings")
def set_settings(api_key: str = Form("")):
    key = api_key.strip().strip('"').strip("'")
    cfg = _load_config()
    if key:
        if not key.startswith("sk-ant-"):
            return {"ok": False, "error": "API キーは sk-ant- で始まります。コピーし直してください。"}
        cfg["api_key"] = key
    else:
        cfg.pop("api_key", None)
    _save_config(cfg)
    return {"ok": True, **get_settings()}


@app.post("/settings/test")
async def test_settings():
    """保存したキーで最小のやり取りを1回行い、通るか確かめる。"""
    try:
        from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

        opts = ClaudeAgentOptions(cwd=str(ROOT), env=_sdk_env(), max_turns=1, model="haiku",
                                  allowed_tools=[], permission_mode="default")
        t0 = time.time()
        async for msg in query(prompt="「OK」とだけ返してください。", options=opts):
            if isinstance(msg, ResultMessage):
                if msg.is_error:
                    return {"ok": False, "error": _auth_hint(msg.result or "エラー")}
                return {"ok": True, "sec": round(time.time() - t0, 1), "reply": (msg.result or "").strip()[:40]}
        return {"ok": False, "error": "応答がありませんでした"}
    except ImportError:
        return {"ok": False, "error": "claude-agent-sdk が入っていません: pip install claude-agent-sdk"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": _auth_hint(f"{type(e).__name__}: {e}")}


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

    agent = _agent_def(bot_id) if bot_id != "_auto" else None   # 指定ボットは親を挟まず直接呼ぶ
    prompt = message.strip()
    if nick and not agent:
        prompt = f"{nick}、{prompt}" if prompt else f"{nick}、お願いします"
    elif not prompt:
        prompt = "お願いします"
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

            if agent:
                # 直接呼び: ボット定義の本文をシステムプロンプトに、ツールとモデルも定義どおり。
                # ツールなしのボットは Claude Code の既定プロンプトも外して文章だけ返す。
                tools = agent["tools"]
                sys_prompt = ({"type": "preset", "preset": "claude_code", "append": agent["body"]}
                              if tools else agent["body"])
                opts = ClaudeAgentOptions(
                    cwd=str(ROOT),
                    system_prompt=sys_prompt,
                    model=agent.get("model") or None,
                    tools=tools,
                    allowed_tools=tools,
                    setting_sources=[],                  # CLAUDE.md の「必ず委譲」を読ませない
                    permission_mode="acceptEdits",
                    resume=_sessions.get(bot_id),
                    env=_sdk_env(),
                )
            else:
                # おまかせ: 親エージェントが CLAUDE.md を見て適したボットに委譲する
                opts = ClaudeAgentOptions(
                    cwd=str(ROOT),
                    setting_sources=["project"],            # .claude/agents と CLAUDE.md を読む
                    allowed_tools=["Read", "Glob", "Grep", "Bash", "Agent", "Task"],
                    permission_mode="acceptEdits",
                    resume=_sessions.get(bot_id),
                    env=_sdk_env(),
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
                    # 委譲ありの経路ではサブエージェント分も流れてくるので、最後の1件だけ採用する
                    sid = getattr(msg, "session_id", None)
                    if sid:
                        _sessions[bot_id] = sid
            _history[bot_id].append({"role": "bot", "text": "\n\n".join(reply), "at": time.time()})
            _save_history()
            yield _sse({"type": "done", "sec": round(time.time() - t0, 1)})
        except ImportError:
            yield _sse({"type": "error", "text": "claude-agent-sdk が入っていません: pip install claude-agent-sdk"})
        except Exception as e:  # noqa: BLE001
            yield _sse({"type": "error", "text": _auth_hint(f"{type(e).__name__}: {e}")})

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _sse(obj: dict) -> str:
    return "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"


def _port_in_use() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sk:
        return sk.connect_ex((HOST, PORT)) == 0


def _replace_stale_server(url: str) -> bool:
    """動いているサーバーが古いコードなら止める。止められたら True。"""
    import urllib.request

    try:
        with urllib.request.urlopen(f"{url}/version", timeout=2) as r:
            if json.loads(r.read()).get("stamp") == CODE_STAMP:
                return False                       # 同じコードが動いている
    except Exception:  # noqa: BLE001  /version が無い＝古い
        pass
    print("bot-team: old server is running, asking it to stop")
    try:
        urllib.request.urlopen(urllib.request.Request(f"{url}/shutdown", method="POST"), timeout=2).read()
    except Exception:  # noqa: BLE001  /shutdown も無いほど古い
        print("bot-team: the old server cannot be stopped from here; close its console window and run run.bat again")
        return False
    for _ in range(40):                            # 最大 8 秒待つ
        time.sleep(0.2)
        if not _port_in_use():
            return True
    return False


if __name__ == "__main__":
    import argparse

    import uvicorn

    ap = argparse.ArgumentParser()
    ap.add_argument("--open", action="store_true", help="起動後にブラウザを開く")
    args = ap.parse_args()
    url = f"http://{HOST}:{PORT}"

    if sys.stdout is None or sys.stderr is None:  # pythonw（窓なし）のときはログをファイルへ
        log = open(ROOT / "app" / "server.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = log
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    if _port_in_use() and not _replace_stale_server(url):   # 同じコードが動いていればブラウザを開くだけ
        print(f"bot-team: already running at {url}")
        if args.open:
            webbrowser.open(url)
        sys.exit(0)
    if args.open:
        threading.Timer(1.0, webbrowser.open, [url]).start()
    print(f"bot-team: {url}")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
