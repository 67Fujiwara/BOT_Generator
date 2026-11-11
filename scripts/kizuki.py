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
  python scripts/kizuki.py schedule --every "5時間"        # 「気づき、何かある？」の間隔（30分 / 2時間 / 1日1回 / なし）
  python scripts/kizuki.py schedule --hours 8-19 --weekdays on   # 声をかける時間帯（平日だけ / 終日は --weekdays off --hours 0-24）
  python scripts/kizuki.py schedule --show
保存先は app/kizuki.json（git 対象外）。--store か環境変数 BOT_KIZUKI_FILE で差し替えられる。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STORE = Path(os.environ.get("BOT_KIZUKI_FILE") or ROOT / "app" / "kizuki.json")
STATUS = {"open": "未着手", "mc": "MC化", "skip": "見送り"}
CONFIG = ROOT / "app" / "config.json"      # 声かけの間隔はここ（画面の ⚙ と同じ値。ブラウザが 1 分ごとに読む）
EVERY_RE = re.compile(r"^\s*(?:(\d+(?:\.\d+)?)\s*(分|時間|h|m|min)?|(\d+)\s*日\s*(?:に)?\s*1\s*回|毎日|1日1回|(なし|止め|しない|off|0))\s*(?:ごと|毎|おき)?\s*$", re.I)


def parse_every(text: str) -> int | None:
    """「5時間」「30分」「1日1回」「なし」→ 分。解釈できなければ None。"""
    t = (text or "").strip().replace("ｈ", "h").replace("ごとに", "ごと")
    m = EVERY_RE.match(t)
    if not m:
        return None
    if m.group(4):
        return 0
    if m.group(3) or "毎日" in t or "1日1回" in t:
        return 24 * 60 * (int(m.group(3)) if m.group(3) else 1)
    n = float(m.group(1))
    unit = (m.group(2) or "").lower()
    return int(round(n * 60)) if unit in ("時間", "h") or (unit == "" and n <= 12) else int(round(n))


def load_schedule() -> dict:
    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8")) if CONFIG.exists() else {}
    except (OSError, ValueError):
        cfg = {}
    return {"every": int(cfg.get("andy_every", 120)), "weekdays": bool(cfg.get("andy_weekdays", True)),
            "hours": list(cfg.get("andy_hours", [8, 19]))}


def save_schedule(sch: dict) -> None:
    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8")) if CONFIG.exists() else {}
    except (OSError, ValueError):
        cfg = {}
    cfg.update({"andy_every": int(sch["every"]), "andy_weekdays": bool(sch["weekdays"]), "andy_hours": [int(sch["hours"][0]), int(sch["hours"][1])]})
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")


def fmt_schedule(sch: dict) -> str:
    e = sch["every"]
    every = "声かけなし" if e <= 0 else (f"{e // 1440} 日に 1 回" if e % 1440 == 0 else f"{e // 60} 時間ごと" if e % 60 == 0 else f"{e} 分ごと")
    h0, h1 = sch["hours"]
    when = ("平日" if sch["weekdays"] else "毎日") + ("" if (h0, h1) == (0, 24) else f"の {h0}〜{h1} 時")
    return f"{every}（{when}）"


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
    ev = sub.add_parser("every"); ev.add_argument("text")          # 間隔の言い方 → 分（確認用。保存しない）
    sc = sub.add_parser("schedule"); sc.add_argument("--every", default=None); sc.add_argument("--hours", default=None)
    sc.add_argument("--weekdays", default=None, choices=["on", "off"]); sc.add_argument("--show", action="store_true")
    args = ap.parse_args(argv)
    store = Path(args.store) if args.store else None

    if args.cmd == "every":
        v = parse_every(args.text)
        print("解釈できません" if v is None else f"{v} 分"); return 0 if v is not None else 3
    if args.cmd == "schedule":
        sch = load_schedule()
        if args.every is not None:
            ev = parse_every(args.every)
            if ev is None:
                print(f"間隔「{args.every}」を解釈できません。30分 / 2時間 / 1日1回 / なし のように言ってください"); return 3
            sch["every"] = ev
        if args.hours:
            m = re.fullmatch(r"\s*(\d{1,2})\s*[-〜~]\s*(\d{1,2})\s*", args.hours)
            if not m or not (0 <= int(m.group(1)) < int(m.group(2)) <= 24):
                print(f"時間帯「{args.hours}」は 8-19 のように書いてください（0〜24）"); return 3
            sch["hours"] = [int(m.group(1)), int(m.group(2))]
        if args.weekdays:
            sch["weekdays"] = args.weekdays == "on"
        if not args.show:
            save_schedule(sch)
        print(("今の設定: " if args.show else "設定した: ") + fmt_schedule(sch)); return 0
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
