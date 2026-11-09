#!/usr/bin/env python3
"""成果物のチェック用に、ファイルの中身を 1 回で要約して標準出力に出す（バズが使う）。

  python scripts/inspect_file.py <ファイル>...
Excel: シート・行数・列名・列ごとの空欄数・先頭 5 行。CSV: 行数・列・先頭 5 行。
md/txt: 行数・見出し・先頭 40 行・穴埋め語（【確認】/TODO/要確認/（要記入）/XXX）の数。PDF: ページ数と先頭の文字。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

HOLES = ["【確認", "TODO", "要確認", "（要記入）", "XXX", "○○", "〇〇", "unknown", "None"]


def holes(text: str) -> str:
    found = {h: text.count(h) for h in HOLES if h in text}
    return "穴埋め語: " + (", ".join(f"{k}×{v}" for k, v in found.items()) if found else "なし")


def show_table(p: Path) -> None:
    import pandas as pd
    if p.suffix.lower() == ".csv":
        df = pd.read_csv(p, encoding="utf-8-sig")
        sheets = {"(csv)": df}
    else:
        sheets = pd.read_excel(p, sheet_name=None)
    for name, df in sheets.items():
        print(f"  シート {name}: {len(df)} 行 × {len(df.columns)} 列")
        print("  列: " + ", ".join(str(c) for c in df.columns))
        empties = {str(c): int(df[c].isna().sum()) for c in df.columns if df[c].isna().sum()}
        if empties:
            print("  空欄: " + ", ".join(f"{c}×{n}" for c, n in empties.items()))
        with pd.option_context("display.max_columns", 30, "display.width", 200):
            print("  先頭:\n" + "\n".join("    " + l for l in df.head(5).to_string(index=False).splitlines()))
        print("  " + holes(df.astype(str).to_csv(index=False)))


def show_text(p: Path) -> None:
    text = p.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    heads = [l for l in lines if re.match(r"^#{1,4}\s", l)]
    print(f"  {len(lines)} 行, {len(text)} 文字")
    if heads:
        print("  見出し: " + " / ".join(h.strip("# ").strip() for h in heads[:20]))
    print("  " + holes(text))
    print("  先頭:\n" + "\n".join("    " + l for l in lines[:40]))


def show_pdf(p: Path) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from pdftext import page_texts        # 日本語 CID フォント対応
    recs = page_texts(p)
    empties = [r["page"] for r in recs if not r["text"].strip()]
    first = next((r for r in recs if r["text"].strip()), None)
    print(f"  {len(recs)} ページ。文字が取れないページ: {empties or 'なし'}")
    if first:
        print(f"  p.{first['page']}（{first['engine']}）の先頭: {first['text'][:300]!r}")
        print("  " + holes("\n".join(r["text"] for r in recs)))


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__); return 2
    rc = 0
    for s in argv:
        p = Path(s)
        print(f"== {s}")
        if not p.exists():
            print("  見つからない"); rc = 2; continue
        try:
            ext = p.suffix.lower()
            if ext in (".xlsx", ".xlsm", ".xls", ".csv"):
                show_table(p)
            elif ext in (".md", ".txt", ".json", ".py", ".yaml", ".yml"):
                show_text(p)
            elif ext == ".pdf":
                show_pdf(p)
            else:
                print(f"  {p.stat().st_size} バイト（中身は見ない形式）")
        except Exception as e:  # noqa: BLE001
            print(f"  読めない: {type(e).__name__}: {e}"); rc = 2
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
