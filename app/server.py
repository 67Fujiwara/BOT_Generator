"""bot-team チャット入口。ブラウザからの文章＋ドロップしたファイルを Claude Agent SDK に渡す。

起動:  run.bat（窓を出さずに裏で起動してブラウザを開く）/ python app/server.py --open
終了:  画面左上の ⏻ ボタン（POST /shutdown）
依存:  pip install fastapi uvicorn python-multipart claude-agent-sdk
認証:  画面左上の ⚙ で API キーを保存（app/config.json）するか、
       環境変数 ANTHROPIC_API_KEY か、Claude Code にログイン済みであること
"""
import asyncio
import functools
import json
import os
import re
import shutil
import socket
import sys
import threading
import webbrowser
import time
from datetime import datetime, timedelta
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
SAMPLES = ROOT / "samples"                  # ボットごとの参考サンプル（samples/<bot>/<ラベル>/）。git 対象外
AGENTS_DIR = ROOT / ".claude" / "agents"
BOT_RULES = ROOT / ".claude" / "bot_rules.md"   # 全ボット共通ルール。直接呼びのシステムプロンプト末尾に足す
HOST, PORT = "127.0.0.1", 8765
CODE_STAMP = f"{os.path.getmtime(__file__):.0f}-{os.path.getsize(__file__)}"   # 起動時のコードの印。古い常駐を見分ける

app = FastAPI()
app.mount("/static", StaticFiles(directory=ROOT / "app" / "static"), name="static")
# ボットが作った画像（検図の切り抜きなど）をチャットに表示するため、inbox と samples だけ読み取り公開する
INBOX = ROOT / "inbox"
SAMPLES = ROOT / "samples"
INBOX.mkdir(exist_ok=True); SAMPLES.mkdir(exist_ok=True)
app.mount("/files/inbox", StaticFiles(directory=INBOX), name="files-inbox")
app.mount("/files/samples", StaticFiles(directory=SAMPLES), name="files-samples")

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


CLAUDE_HOME = ROOT / "app" / "claude_home"   # キー使用時の Claude Code 設定置き場（~/.claude のログイン情報と切り離す。git 対象外）


def _sdk_env() -> dict[str, str]:
    """画面で保存した API キーがあれば、環境変数より優先して Claude Code に渡す。
    そのときは CLAUDE_CONFIG_DIR をアプリ専用にして、~/.claude にある Claude.ai のログイン
    （期限切れだと "OAuth session expired" になる）を Claude Code が見に行かないようにする。"""
    cfg = _load_config()
    env = {}
    if cfg.get("openai_api_key"):                 # スリンキーの文字起こし用（scripts/transcribe.py が読む）
        env["OPENAI_API_KEY"] = cfg["openai_api_key"]
    if cfg.get("glossary"):
        env["SLINKY_GLOSSARY"] = cfg["glossary"]
    if cfg.get("cf_account_id") and cfg.get("cf_api_token"):   # Cloudflare Workers AI（無料枠）
        env["CF_ACCOUNT_ID"], env["CF_API_TOKEN"] = cfg["cf_account_id"], cfg["cf_api_token"]
    eng = cfg.get("transcribe_engine", "auto")
    if eng == "auto":
        eng = "cloudflare" if cfg.get("cf_api_token") else "openai" if cfg.get("openai_api_key") else "whisper"
    if eng in ("openai", "whisper", "cloudflare"):
        env["SLINKY_ENGINE"] = eng
    key = cfg.get("api_key", "")
    if key:
        CLAUDE_HOME.mkdir(parents=True, exist_ok=True)
        env.update({"ANTHROPIC_API_KEY": key, "CLAUDE_CONFIG_DIR": str(CLAUDE_HOME)})
    return env


def _auth_hint(text: str) -> str:
    low = text.lower()
    if "oauth" in low or "authenticat" in low or "not logged in" in low:
        return text + "\n→ Claude.ai のログインが切れています。左上の ⚙ で API キーを保存するか、コマンド窓で claude を起動して /login し直してください。"
    if "api key" in low:
        return text + "\n→ 左上の ⚙ から API キーを設定してください。"
    return text


def _front(path: Path) -> dict:
    txt = path.read_text(encoding="utf-8")
    head = txt.split("---")[1] if txt.startswith("---") else ""
    out = {}
    for key in ("name", "description", "model", "group", "color", "shape", "record", "tasks", "say"):
        m = re.search(rf"^{key}:\s*(.+)$", head, re.M)
        if m:
            out[key] = m.group(1).strip().strip('"\'')   # YAML で "#FDD663" のように引用符付きでも可
    for key in ("tools", "samples"):
        m = re.search(rf"^{key}:\s*\[(.*)\]", head, re.M) or re.search(rf"^{key}:\s*\n((?:\s+-\s*.+\n?)+)", head, re.M)
        out[key] = [t.strip(" -") for t in re.split(r"[,\n]", m.group(1)) if t.strip(" -")] if m else []
    return out


def _safe_name(name: str) -> str:
    name = Path(name).name.strip() or "file"
    return re.sub(r'[\\/:*?"<>|]', "_", name)


def list_samples(bot_id: str, labels: list[str]) -> dict[str, list[str]]:
    """{ラベル: [ROOT からの相対パス]}。定義にあるラベルは空でも出す。"""
    out = {lab: [] for lab in labels} if labels else {}
    base = SAMPLES / bot_id
    if base.is_dir():
        for d in sorted(p for p in base.iterdir() if p.is_dir()):
            out.setdefault(d.name, [])
            out[d.name] = [str(p.relative_to(ROOT)) for p in sorted(d.iterdir())
                           if p.is_file() and not p.name.startswith(".") and not p.name.endswith(".pdf.txt")]   # .pdf.txt は抽出キャッシュ
    if not out:
        out["サンプル"] = []
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
            "tools": f["tools"], "samples": f["samples"],
            "color": f.get("color", PALETTE[sum(bid.encode()) % len(PALETTE)]),   # 既定色は id から決める（ファイルが増えても変わらない）
            "shape": f.get("shape", ""), "record": f.get("record", "").lower() in ("true", "yes", "1"),
            "tasks": f.get("tasks", "").lower() in ("true", "yes", "1"), "say": f.get("say", ""),
            "preview": (last.get("text") or "")[:60].replace("\n", " "),
            "status": "",
        })
    return bots


@app.get("/history/{bot}")
def history(bot: str):
    return _history.get(bot, [])


@app.get("/")
def index():
    # 画面は毎回ディスクから読み直す（更新後に古い画面がキャッシュで残らないように）
    return FileResponse(ROOT / "app" / "static" / "index.html", headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


@app.get("/bots")
def bots():
    return list_bots()


@app.get("/inbox")
def inbox():
    """右パネル「コンピューター」用: 最近置かれたファイル。"""
    items = sorted(INBOX.rglob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [{"path": str(p.relative_to(ROOT)), "at": p.stat().st_mtime} for p in items if p.is_file() and p.name != ".gitkeep"][:30]


@app.get("/samples/{bot}")
def get_samples(bot: str):
    f = _agent_def(bot)
    return list_samples(bot, f["samples"] if f else [])


@app.post("/samples/{bot}")
async def add_samples(bot: str, label: str = Form("サンプル"), files: list[UploadFile] = File(default=[])):
    if not _agent_def(bot):
        return {"ok": False, "error": "そのボットはありません"}
    d = SAMPLES / _safe_name(bot) / _safe_name(label)
    d.mkdir(parents=True, exist_ok=True)
    for f in files:
        if f.filename:
            with (d / _safe_name(f.filename)).open("wb") as out:
                shutil.copyfileobj(f.file, out)
    return {"ok": True, **get_samples(bot)}


@app.delete("/samples/{bot}")
def delete_sample(bot: str, path: str):
    target = (ROOT / path).resolve()
    if not str(target).startswith(str((SAMPLES / bot).resolve()) + os.sep) or not target.is_file():
        return {"ok": False, "error": "そのファイルは消せません"}
    target.unlink()
    return {"ok": True, **get_samples(bot)}


# ---- ライブ録音: ブラウザが 45 秒ごとに区切りを送る → 文字＋時刻を返す ------------------------
def _engine_for_chunks(cfg: dict) -> str:
    e = cfg.get("transcribe_engine", "auto")
    if e != "auto":
        return e
    if cfg.get("remote_transcribe_url"):
        return "remote"
    if cfg.get("cf_account_id") and cfg.get("cf_api_token"):
        return "cloudflare"
    return "openai" if cfg.get("openai_api_key") else "whisper"


@app.post("/transcribe/chunk")
async def transcribe_chunk(audio: UploadFile = File(...), seq: int = Form(0), startSec: float = Form(0.0),
                           language: str = Form("ja"), glossary: str = Form(""), prev: str = Form(""),
                           meeting: str = Form("")):
    import sys as _sys
    _sys.path.insert(0, str(ROOT / "scripts"))
    import transcribe as tr   # scripts/transcribe.py

    cfg = _load_config()
    d = INBOX / _safe_name(meeting or datetime.now().strftime("%Y%m%d-%H%M%S"))
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"chunk-{seq:03d}.webm"
    with path.open("wb") as out:
        shutil.copyfileobj(audio.file, out)
    if path.stat().st_size < 2000:
        return {"seq": seq, "startSec": startSec, "text": "", "segments": [], "dropped": "(empty)"}

    engine = _engine_for_chunks(cfg)
    base = {"seq": seq, "startSec": startSec}
    try:
        if engine == "remote":                      # SYNAPSE などの /api/minutes/transcribe と同じ形で転送する
            import httpx
            url = cfg.get("remote_transcribe_url", "")
            if not url:
                return {**base, "error": "外部サーバーの URL が未設定です（⚙ の文字起こし設定）"}
            headers = {"Authorization": f"Bearer {cfg['remote_transcribe_token']}"} if cfg.get("remote_transcribe_token") else {}
            form = {"seq": str(seq), "startSec": str(startSec), "language": language, "glossary": glossary or cfg.get("glossary", ""), "prev": prev}
            async with httpx.AsyncClient(timeout=120) as cl:
                with path.open("rb") as f:
                    r = await cl.post(url, data=form, files={"audio": (path.name, f, "audio/webm")}, headers=headers)
            if r.status_code != 200:
                return {**base, "error": f"外部サーバー {r.status_code}: {r.text[:200]}"}
            return r.json()
        prompt = tr.build_prompt(glossary or cfg.get("glossary", ""), prev)
        if engine == "openai" and cfg.get("openai_api_key"):
            os.environ["OPENAI_API_KEY"] = cfg["openai_api_key"]
        if engine == "cloudflare":
            os.environ["CF_ACCOUNT_ID"], os.environ["CF_API_TOKEN"] = cfg.get("cf_account_id", ""), cfg.get("cf_api_token", "")
        loop = asyncio.get_running_loop()
        res = await loop.run_in_executor(None, functools.partial(tr.transcribe_chunk, path, engine, None, prompt, startSec))
        return {**base, **res}
    except Exception as e:  # noqa: BLE001
        return {**base, "error": f"{type(e).__name__}: {e}"}


@app.post("/transcribe/finish")
def transcribe_finish(meeting: str = Form(""), text: str = Form("")):
    """ライブ録音の全文（時刻付き）を inbox/<meeting>/文字起こし_raw.txt に保存してパスを返す。"""
    d = INBOX / _safe_name(meeting or datetime.now().strftime("%Y%m%d-%H%M%S"))
    d.mkdir(parents=True, exist_ok=True)
    p = d / "文字起こし_raw.txt"
    p.write_text(text, encoding="utf-8")
    return {"ok": True, "path": str(p.relative_to(ROOT))}


# ---- タスク（ジェシー）: scripts/tasks.py と同じ app/tasks.json を読む ----------------------------
def _tasks_mod():
    import sys as _sys
    if str(ROOT / "scripts") not in _sys.path:
        _sys.path.insert(0, str(ROOT / "scripts"))
    import tasks as tk
    return tk


def _task_view(t: dict, tk) -> dict:
    return {**t, "status": tk.status(t), "text": tk.fmt(t)}


@app.get("/tasks")
def get_tasks(all: bool = False):
    tk = _tasks_mod()
    order = {"期限切れ": 0, "すぐ": 1, "今日まで": 2, "期間内": 3, "予定": 4, "納期なし": 5, "完了": 6}
    ts = [t for t in tk._load() if all or not t.get("done")]
    ts.sort(key=lambda x: (order[tk.status(x)], x.get("due_end") or "9999", x["id"]))
    return {"today": datetime.now().date().isoformat(), "tasks": [_task_view(t, tk) for t in ts]}


@app.post("/tasks")
def add_task(title: str = Form(...), due: str = Form(""), note: str = Form("")):
    tk = _tasks_mod()
    if not title.strip():
        return {"ok": False, "error": "タスク名を入れてください"}
    try:
        t = tk.add_task(title, due or "なし", note)
    except ValueError:
        return {"ok": False, "error": f"納期「{due}」を解釈できません。すぐ / 今週中 / 今月中旬 / 11/20 / なし のように入れてください。"}
    return {"ok": True, "task": _task_view(t, tk)}


@app.post("/tasks/{task_id}/{action}")
def task_action(task_id: int, action: str, text: str = Form("")):
    tk = _tasks_mod()
    tasks = tk._load()
    t = next((x for x in tasks if x["id"] == task_id), None)
    if not t:
        return {"ok": False, "error": "そのタスクはありません"}
    now = datetime.now().isoformat(timespec="minutes")
    if action == "done":
        t["done"], t["done_at"] = True, now
    elif action == "undone":
        t["done"], t["done_at"] = False, None
    elif action == "remove":
        tasks.remove(t)
    elif action == "reminded":
        t["last_reminded"] = now
    elif action == "snooze":                      # 2 時間後まで催促しない
        t["last_reminded"] = now
        t["snooze_until"] = (datetime.now() + timedelta(hours=2)).isoformat(timespec="minutes")
    elif action == "due":
        due = tk.parse_due(text)
        if due is None:
            return {"ok": False, "error": f"納期「{text}」を解釈できません"}
        t.update({"due_label": due["label"], "due_start": due["start"], "due_end": due["end"], "asap": due["asap"]})
    else:
        return {"ok": False, "error": "不明な操作"}
    tk._save(tasks)
    return {"ok": True}


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
    cfg = _load_config()
    key = cfg.get("api_key", "")
    okey = cfg.get("openai_api_key", "")
    rtok = cfg.get("remote_transcribe_token", "")
    return {
        "api_key_set": bool(key),
        "api_key_hint": f"…{key[-4:]}" if key else "",
        "openai_key_set": bool(okey),
        "openai_key_hint": f"…{okey[-4:]}" if okey else "",
        "transcribe_engine": cfg.get("transcribe_engine", "auto"),
        "cf_account_id": cfg.get("cf_account_id", ""),
        "cf_token_set": bool(cfg.get("cf_api_token")),
        "remote_transcribe_url": cfg.get("remote_transcribe_url", ""),
        "remote_token_set": bool(rtok),
        "glossary": cfg.get("glossary", ""),
        "chunk_sec": int(cfg.get("chunk_sec", 45)),
        "env_key_set": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "config_file": str(CONFIG.relative_to(ROOT)),
    }


@app.post("/settings")
def set_settings(api_key: str = Form(None), openai_api_key: str = Form(None),
                 transcribe_engine: str = Form(None), remote_transcribe_url: str = Form(None),
                 remote_transcribe_token: str = Form(None), glossary: str = Form(None), chunk_sec: str = Form(None),
                 cf_account_id: str = Form(None), cf_api_token: str = Form(None)):
    """送られてきた項目だけ更新する。空文字は削除。"""
    cfg = _load_config()
    if cf_account_id is not None:
        cfg["cf_account_id"] = cf_account_id.strip()
    if cf_api_token is not None:
        tok = cf_api_token.strip()
        if tok:
            cfg["cf_api_token"] = tok
        elif cf_api_token == "":
            cfg.pop("cf_api_token", None)
    if transcribe_engine is not None:
        if transcribe_engine not in ("auto", "openai", "whisper", "remote", "cloudflare"):
            return {"ok": False, "error": "エンジンの指定が不正です"}
        cfg["transcribe_engine"] = transcribe_engine
    if remote_transcribe_url is not None:
        url = remote_transcribe_url.strip()
        if url and not url.startswith("https://"):
            return {"ok": False, "error": "外部サーバーの URL は https:// で始めてください"}
        cfg["remote_transcribe_url"] = url
    if remote_transcribe_token is not None:
        tok = remote_transcribe_token.strip()
        if tok:
            cfg["remote_transcribe_token"] = tok
        elif remote_transcribe_token == "":
            cfg.pop("remote_transcribe_token", None)
    if glossary is not None:
        cfg["glossary"] = glossary.strip()
    if chunk_sec is not None and chunk_sec.strip().isdigit():
        cfg["chunk_sec"] = max(20, min(90, int(chunk_sec)))
    if api_key is not None:
        key = api_key.strip().strip('"').strip("'")
        if key and not key.startswith("sk-ant-"):
            return {"ok": False, "error": "Anthropic の API キーは sk-ant- で始まります。コピーし直してください。"}
        cfg["api_key"] = key
        if not key:
            cfg.pop("api_key", None)
    if openai_api_key is not None:
        okey = openai_api_key.strip().strip('"').strip("'")
        if okey and not okey.startswith("sk-"):
            return {"ok": False, "error": "OpenAI の API キーは sk- で始まります。コピーし直してください。"}
        cfg["openai_api_key"] = okey
        if not okey:
            cfg.pop("openai_api_key", None)
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
    if agent and agent.get("tasks", "").lower() in ("true", "yes", "1"):
        tk = _tasks_mod()
        open_tasks = [t for t in tk._load() if not t.get("done")]
        head = "現在の未完了タスク:\n" + ("\n".join(tk.fmt(t) for t in open_tasks) if open_tasks else "（なし）")
        prompt = head + "\n\n依頼:\n" + prompt
    if agent:
        smp = [(lab, f) for lab, fs in list_samples(bot_id, agent["samples"]).items() for f in fs]
        if smp:
            prompt += "\n\n参考サンプル（形式の確認用。編集しない）:\n" + "\n".join(f"- {lab}: {f}" for lab, f in smp)

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
                body = agent["body"]
                if BOT_RULES.exists():
                    body += "\n\n" + BOT_RULES.read_text(encoding="utf-8").strip()
                sys_prompt = ({"type": "preset", "preset": "claude_code", "append": body}
                              if tools else body)
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

            async def run(options):
                """1 回分の送受信。SSE イベントを yield する。"""
                async for msg in query(prompt=prompt, options=options):
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

            try:
                async for ev in run(opts):
                    yield ev
            except Exception as e:  # noqa: BLE001
                # 「前の会話が見つからない」は続きを諦めて新しい会話でやり直す（設定置き場の切替・履歴掃除の後に起きる）
                if "No conversation found" in str(e) and getattr(opts, "resume", None):
                    _sessions.pop(bot_id, None)
                    reply.clear()
                    yield _sse({"type": "tool", "text": "前の会話が見つからないので新しい会話で送り直します"})
                    opts.resume = None
                    async for ev in run(opts):
                        yield ev
                else:
                    raise
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
