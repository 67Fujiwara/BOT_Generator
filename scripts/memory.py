"""ボットごとの「覚えている指示」（やり方の好み）。保存先 app/memory/<bot>.json（git 対象外）。
ボットは返事の中に [[覚える: …]] / [[全員で覚える: …]] / [[忘れる: キーワード]] と書くだけ（server.py が拾って保存し、表示からは消す）。

使い方（手で確認するとき）:
  python scripts/memory.py list <bot|_all>
  python scripts/memory.py add <bot|_all> "画像リンクは書かず、ページ・対象・指摘・根拠だけ文字で返す"
  python scripts/memory.py remove <bot|_all> <番号 or キーワード>
"""
import json
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "app" / "memory"
ALL = "_all"
TAG_RE = re.compile(r"\[\[\s*(覚える|全員で覚える|忘れる)\s*[:：]\s*(.+?)\s*\]\]", re.S)


def _path(bot: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_\-]", "_", bot) or "bot"
    return DIR / f"{safe}.json"


def load(bot: str) -> list[dict]:
    p = _path(bot)
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    except (OSError, ValueError):
        return []


def save(bot: str, items: list[dict]):
    DIR.mkdir(parents=True, exist_ok=True)
    _path(bot).write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")


def add(bot: str, text: str) -> dict:
    text = " ".join(text.split())
    items = load(bot)
    norm = unicodedata.normalize("NFKC", text).lower()
    for it in items:
        if unicodedata.normalize("NFKC", it["text"]).lower() == norm:
            return it                                   # 同じ指示は二重に持たない
    it = {"text": text, "at": datetime.now().isoformat(timespec="minutes")}
    items.append(it)
    save(bot, items)
    return it


def remove(bot: str, key: str) -> list[dict]:
    """番号（1 始まり）かキーワードに一致する指示を消し、消したものを返す。"""
    items = load(bot)
    key = key.strip()
    if key.isdigit() and 1 <= int(key) <= len(items):
        gone = [items.pop(int(key) - 1)]
    else:
        k = unicodedata.normalize("NFKC", key).lower()
        gone = [it for it in items if k in unicodedata.normalize("NFKC", it["text"]).lower()]
        items = [it for it in items if it not in gone]
    save(bot, items)
    return gone


def apply_tags(bot: str, text: str) -> tuple[str, list[str]]:
    """返事の中の [[…]] を実行して本文から取り除く。(本文, 何をしたかの短い説明) を返す。"""
    notes = []

    def _do(m):
        kind, body = m.group(1), m.group(2).strip()
        if kind == "覚える":
            add(bot, body); notes.append(f"覚えた: {body}")
        elif kind == "全員で覚える":
            add(ALL, body); notes.append(f"全員で覚えた: {body}")
        else:
            gone = remove(bot, body) + (remove(ALL, body) if bot != ALL else [])
            notes.append("忘れた: " + (" / ".join(g["text"] for g in gone) if gone else f"（該当なし: {body}）"))
        return ""

    cleaned = TAG_RE.sub(_do, text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, notes


def prompt_block(bot: str) -> str:
    """システムプロンプトに足す「覚えている指示」。無ければ空文字。"""
    mine, everyone = load(bot), (load(ALL) if bot != ALL else [])
    if not mine and not everyone:
        return ""
    lines = ["## 覚えている指示（本人から言われた、やり方の好み。必ず守る）"]
    lines += [f"- {it['text']}" for it in everyone]
    lines += [f"- {it['text']}" for it in mine]
    return "\n".join(lines)


def main():
    if len(sys.argv) < 3:
        print(__doc__); sys.exit(2)
    cmd, bot = sys.argv[1], sys.argv[2]
    if cmd == "list":
        items = load(bot)
        print("\n".join(f"{i}. {it['text']}（{it['at']}）" for i, it in enumerate(items, 1)) or "（なし）")
    elif cmd == "add":
        print("覚えた: " + add(bot, " ".join(sys.argv[3:]))["text"])
    elif cmd == "remove":
        gone = remove(bot, " ".join(sys.argv[3:]))
        print("忘れた: " + (" / ".join(g["text"] for g in gone) if gone else "（該当なし）"))
    else:
        sys.exit(2)


if __name__ == "__main__":
    main()
