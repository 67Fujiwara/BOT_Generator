#!/usr/bin/env python3
"""気づきメモ（アンディ用）。MC（メソッドチェンジ）のタネになる「気づき」を貯めて、MC にしたものに印を付ける。

使い方（1 回の Bash で複数行まとめてよい）:
  python scripts/kizuki.py add "<気づきの文>" [--note "<補足>"]
  python scripts/kizuki.py list [--all]            # 既定は未着手だけ。--all で MC化・見送りも
  python scripts/kizuki.py mc <id> [--file <MCシートのパス>]   # MC にした印
  python scripts/kizuki.py skip <id>               # 見送り
  python scripts/kizuki.py undo <id>               # 未着手に戻す
  python scripts/kizuki.py note <id> "<補足>"
  python scripts/kizuki.py remove <id>
保存先は app/kizuki.json（git 対象外）。--store か環境変数 BOT_KIZUKI_FILE で差し替えられる。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STORE = Path(os.environ.get("BOT_KIZUKI_FILE") or ROOT / "app" / "kizuki.json")
STATUS = {"open": "未着手", "mc": "MC化", "skip": "見送り"}


def _load(store: Path = None) -> list[dict]:
    p = store or STORE
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def _save(items: list[dict], store: Path = None) -> None:
    p = store or STORE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")


def add(text: str, note: str = "", store: Path = None) -> dict:
    items = _load(store)
    it = {"id": (max((x["id"] for x in items), default=0) + 1), "text": text.strip(), "note": note.strip(),
          "status": "open", "created": datetime.now().isoformat(timespec="minutes"), "mc_at": None, "mc_file": ""}
    items.append(it)
    _save(items, store)
    return it


def fmt(it: dict) -> str:
    extra = f"  メモ: {it['note']}" if it.get("note") else ""
    mc = f"  → {it['mc_file']}" if it.get("mc_file") else ""
    return f"[{it['id']}] {it['created'][:10]} {STATUS.get(it['status'], it['status'])}  {it['text']}{extra}{mc}"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="気づきメモ")
    ap.add_argument("--store", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add"); a.add_argument("text"); a.add_argument("--note", default="")
    l = sub.add_parser("list"); l.add_argument("--all", action="store_true")
    m = sub.add_parser("mc"); m.add_argument("id", type=int); m.add_argument("--file", default="")
    for name in ("skip", "undo", "remove"):
        sub.add_parser(name).add_argument("id", type=int)
    n = sub.add_parser("note"); n.add_argument("id", type=int); n.add_argument("text")
    args = ap.parse_args(argv)
    store = Path(args.store) if args.store else None

    if args.cmd == "add":
        if not args.text.strip():
            print("気づきの文が空です"); return 2
        it = add(args.text, args.note, store)
        print("登録: " + fmt(it)); return 0
    items = _load(store)
    if args.cmd == "list":
        shown = items if args.all else [x for x in items if x["status"] == "open"]
        if not shown:
            print("気づきはまだありません" if args.all else "未着手の気づきはありません"); return 0
        for it in shown:
            print(fmt(it))
        print(f"（未着手 {sum(1 for x in items if x['status']=='open')} / MC化 {sum(1 for x in items if x['status']=='mc')} / 見送り {sum(1 for x in items if x['status']=='skip')}）")
        return 0
    it = next((x for x in items if x["id"] == args.id), None)
    if not it:
        print(f"id {args.id} の気づきはありません"); return 3
    if args.cmd == "mc":
        it["status"], it["mc_at"], it["mc_file"] = "mc", datetime.now().isoformat(timespec="minutes"), args.file
    elif args.cmd == "skip":
        it["status"] = "skip"
    elif args.cmd == "undo":
        it["status"], it["mc_at"], it["mc_file"] = "open", None, ""
    elif args.cmd == "note":
        it["note"] = args.text.strip()
    elif args.cmd == "remove":
        items.remove(it)
    _save(items, store)
    print(("削除: " if args.cmd == "remove" else "更新: ") + fmt(it)); return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
