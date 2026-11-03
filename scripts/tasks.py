"""タスクの登録・一覧・完了（ジェシーが1回の Bash で呼ぶ）。保存先は app/tasks.json（git 対象外）。
納期は「すぐ / 今日中 / 明日 / 今週中 / 来週 / 今月中旬 / 月末 / 来月上旬 / 11/20 / 20日 / 金曜 / 3日後」などの言い方を
決定的に日付の範囲（due_start〜due_end）へ直す。解釈できない言い方は登録せずに終了コード 3 で返す（ジェシーが聞き返す）。

使い方:
  python scripts/tasks.py add "ダイヤに発注書を送る" --due 来週          → 登録: #3 …（来週: 11/10〜11/14）
  python scripts/tasks.py add "見積依頼" --due なし                        → 納期なしで登録（催促しない）
  python scripts/tasks.py list [--all] [--json]                            → 未完了一覧（--all で完了も）
  python scripts/tasks.py done 3 / done 発注書                              → 完了（番号か、タイトルの一部）
  python scripts/tasks.py undone 3 / remove 3 / due 3 今月中旬 / note 3 "メモ"
  python scripts/tasks.py parse 来週                                        → 解釈の確認だけ
終了コード: 成功 0 / 該当なし・複数該当 1 / 引数エラー 2 / 納期を解釈できない 3
"""
import argparse
import calendar
import json
import re
import sys
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path

import os

ROOT = Path(__file__).resolve().parent.parent
STORE = Path(os.environ.get("BOT_TASKS_FILE") or ROOT / "app" / "tasks.json")   # テストでは BOT_TASKS_FILE で差し替える
WEEKDAYS = {"月": 0, "火": 1, "水": 2, "木": 3, "金": 4, "土": 5, "日": 6}


def _load() -> list[dict]:
    try:
        return json.loads(STORE.read_text(encoding="utf-8")) if STORE.exists() else []
    except (OSError, ValueError):
        return []


def _save(tasks: list[dict]):
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(tasks, ensure_ascii=False, indent=1), encoding="utf-8")


def _month_end(y: int, m: int) -> date:
    return date(y, m, calendar.monthrange(y, m)[1])


def _add_months(d: date, n: int) -> tuple[int, int]:
    m = d.month - 1 + n
    return d.year + m // 12, m % 12 + 1


def parse_due(text: str, today: date | None = None) -> dict | None:
    """納期の言い方 → {label, start, end, asap}。解釈できなければ None。"""
    today = today or date.today()
    t = unicodedata.normalize("NFKC", (text or "")).strip().replace(" ", "")
    if not t:
        return None
    low = t.lower()
    if low in ("なし", "未定", "いつでも", "none", "-", "期限なし", "納期なし"):
        return {"label": "納期なし", "start": None, "end": None, "asap": False}
    out = {"label": t, "asap": False}

    def rng(s: date, e: date):
        out["start"], out["end"] = s.isoformat(), e.isoformat()
        return out

    if re.fullmatch(r"(すぐ|至急|今すぐ|asap|急ぎ|なるはや|早急に?)", low):
        out["asap"] = True
        return rng(today, today)
    if re.fullmatch(r"(今日中?|本日中?)", t):
        return rng(today, today)
    if t in ("明日", "明日中"):
        return rng(today + timedelta(1), today + timedelta(1))
    if t in ("明後日", "あさって"):
        return rng(today + timedelta(2), today + timedelta(2))
    m = re.fullmatch(r"(\d+)日後", t)
    if m:
        d = today + timedelta(int(m.group(1)))
        return rng(d, d)
    m = re.fullmatch(r"(\d+)週間?後", t)
    if m:
        d = today + timedelta(7 * int(m.group(1)))
        return rng(d, d)
    # 今週 / 来週 / 再来週（平日、金曜まで）
    mon = today - timedelta(today.weekday())
    if re.fullmatch(r"(今週中?|週内|今週末まで)", t):
        return rng(today, max(today, mon + timedelta(4)))
    if re.fullmatch(r"(来週中?|来週末まで)", t):
        return rng(mon + timedelta(7), mon + timedelta(11))
    if re.fullmatch(r"(来週前半)", t):
        return rng(mon + timedelta(7), mon + timedelta(9))
    if re.fullmatch(r"(来週後半)", t):
        return rng(mon + timedelta(10), mon + timedelta(11))
    if re.fullmatch(r"(再来週中?)", t):
        return rng(mon + timedelta(14), mon + timedelta(18))
    # 今月 / 来月 の 上旬・中旬・下旬・末・中
    m = re.fullmatch(r"(今月|来月|再来月)?(上旬|中旬|下旬|月末|末|中|いっぱい)", t) or re.fullmatch(r"(今月|来月|再来月)(上旬|中旬|下旬|末|中|いっぱい)?", t)
    if m:
        which, part = m.group(1) or "今月", m.group(2) or "中"
        y, mo = _add_months(today, {"今月": 0, "来月": 1, "再来月": 2}[which])
        first, last = date(y, mo, 1), _month_end(y, mo)
        s, e = {"上旬": (first, date(y, mo, 10)), "中旬": (date(y, mo, 11), date(y, mo, 20)), "下旬": (date(y, mo, 21), last),
                "月末": (date(y, mo, 25), last), "末": (date(y, mo, 25), last), "中": (first, last), "いっぱい": (first, last)}[part]
        if which == "今月":
            s = max(s, today)
        if e < today:
            return None
        return rng(s, e)
    # 曜日（次の該当日。今日ならそのまま）
    m = re.fullmatch(r"(今週|来週)?([月火水木金土日])曜?日?(まで|中)?", t)
    if m and m.group(2):
        wd = WEEKDAYS[m.group(2)]
        base = mon + timedelta(7) if m.group(1) == "来週" else today
        d = base + timedelta((wd - base.weekday()) % 7) if m.group(1) != "来週" else mon + timedelta(7 + wd)
        return rng(today if d >= today else d, d)
    # 日付: YYYY-MM-DD / YYYY/M/D / M/D / M月D日 / D日
    m = re.fullmatch(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?(まで)?", t)
    if m:
        d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return rng(min(today, d), d)
    m = re.fullmatch(r"(\d{1,2})[/月](\d{1,2})日?(まで)?", t)
    if m:
        mo, dd = int(m.group(1)), int(m.group(2))
        y = today.year if (mo, dd) >= (today.month, today.day) else today.year + 1
        try:
            d = date(y, mo, dd)
        except ValueError:
            return None
        return rng(min(today, d), d)
    m = re.fullmatch(r"(\d{1,2})日(まで)?", t)
    if m:
        dd = int(m.group(1))
        y, mo = (today.year, today.month) if dd >= today.day else _add_months(today, 1)
        try:
            d = date(y, mo, dd)
        except ValueError:
            return None
        return rng(today, d)
    return None


def status(task: dict, today: date | None = None) -> str:
    """表示用の状態: 完了 / 期限切れ / 今日まで / 期間内 / 予定 / 納期なし / すぐ"""
    today = today or date.today()
    if task.get("done"):
        return "完了"
    if not task.get("due_end"):
        return "納期なし"
    s, e = date.fromisoformat(task["due_start"]), date.fromisoformat(task["due_end"])
    if task.get("asap"):
        return "すぐ"
    if today > e:
        return "期限切れ"
    if today == e:
        return "今日まで"
    if s <= today:
        return "期間内"
    return "予定"


def fmt(task: dict, today: date | None = None) -> str:
    st = status(task, today)
    due = task.get("due_label", "")
    if task.get("due_end"):
        s, e = task["due_start"], task["due_end"]
        span = f"{s[5:].replace('-', '/')}" + (f"〜{e[5:].replace('-', '/')}" if s != e else "")
        due = f"{due}: {span}"
    mark = "✓" if task.get("done") else "□"
    return f"{mark} #{task['id']} {task['title']}（{due}）[{st}]" + (f" メモ: {task['note']}" if task.get("note") else "")


def find(tasks: list[dict], key: str) -> list[dict]:
    key = key.strip().lstrip("#")
    if key.isdigit():
        return [t for t in tasks if t["id"] == int(key)]
    k = unicodedata.normalize("NFKC", key).lower()
    return [t for t in tasks if k in unicodedata.normalize("NFKC", t["title"]).lower() and not t.get("done")]


def add_task(title: str, due_text: str, note: str = "", today: date | None = None) -> dict:
    due = parse_due(due_text, today)
    if due is None:
        raise ValueError(due_text)
    tasks = _load()
    t = {"id": (max((x["id"] for x in tasks), default=0) + 1), "title": title.strip(), "due_label": due["label"],
         "due_start": due["start"], "due_end": due["end"], "asap": due["asap"], "done": False,
         "created": datetime.now().isoformat(timespec="minutes"), "done_at": None, "last_reminded": None, "note": note}
    tasks.append(t)
    _save(tasks)
    return t


def main():
    global STORE
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default=None, help="保存先（既定 app/tasks.json）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add"); a.add_argument("title"); a.add_argument("--due", required=True); a.add_argument("--note", default="")
    l = sub.add_parser("list"); l.add_argument("--all", action="store_true"); l.add_argument("--json", action="store_true")
    for c in ("done", "undone", "remove", "reminded"):
        sub.add_parser(c).add_argument("key")
    d = sub.add_parser("due"); d.add_argument("key"); d.add_argument("text")
    n = sub.add_parser("note"); n.add_argument("key"); n.add_argument("text")
    p = sub.add_parser("parse"); p.add_argument("text")
    args = ap.parse_args()
    if args.store:
        STORE = Path(args.store)

    if args.cmd == "parse":
        r = parse_due(args.text)
        print(json.dumps(r, ensure_ascii=False) if r else "解釈できません")
        sys.exit(0 if r else 3)
    if args.cmd == "add":
        try:
            t = add_task(args.title, args.due, args.note)
        except ValueError:
            print(f"納期を解釈できません: 「{args.due}」。すぐ / 今日中 / 明日 / 今週中 / 来週 / 今月中旬 / 月末 / 11/20 / 20日 / 金曜 / 3日後 / なし のように指定してください。")
            sys.exit(3)
        print("登録: " + fmt(t))
        return
    tasks = _load()
    if args.cmd == "list":
        shown = tasks if args.all else [t for t in tasks if not t.get("done")]
        if args.json:
            print(json.dumps([{**t, "status": status(t)} for t in shown], ensure_ascii=False))
        elif not shown:
            print("未完了のタスクはありません。")
        else:
            order = {"期限切れ": 0, "すぐ": 1, "今日まで": 2, "期間内": 3, "予定": 4, "納期なし": 5, "完了": 6}
            for t in sorted(shown, key=lambda x: (order[status(x)], x.get("due_end") or "9999", x["id"])):
                print(fmt(t))
        return
    hits = find(tasks, args.key)
    if len(hits) != 1:
        print("該当なし" if not hits else "複数該当: " + " / ".join(f"#{t['id']} {t['title']}" for t in hits) + " → 番号で指定してください")
        sys.exit(1)
    t = hits[0]
    if args.cmd == "done":
        t["done"], t["done_at"] = True, datetime.now().isoformat(timespec="minutes"); print("完了: " + fmt(t))
    elif args.cmd == "undone":
        t["done"], t["done_at"] = False, None; print("未完了に戻しました: " + fmt(t))
    elif args.cmd == "remove":
        tasks.remove(t); print(f"削除: #{t['id']} {t['title']}")
    elif args.cmd == "reminded":
        t["last_reminded"] = datetime.now().isoformat(timespec="minutes")
    elif args.cmd == "due":
        due = parse_due(args.text)
        if due is None:
            print(f"納期を解釈できません: 「{args.text}」"); sys.exit(3)
        t.update({"due_label": due["label"], "due_start": due["start"], "due_end": due["end"], "asap": due["asap"]}); print("納期を変更: " + fmt(t))
    elif args.cmd == "note":
        t["note"] = args.text; print("メモ: " + fmt(t))
    _save(tasks)


if __name__ == "__main__":
    main()
