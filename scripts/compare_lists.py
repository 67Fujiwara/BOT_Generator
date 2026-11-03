"""正規化済みCSV 2つを突き合わせて差分レポート(Markdown)を出す。

使い方:
  python compare_lists.py base.csv target.csv --report report.md
  base = 正（基準）, target = チェック対象
終了コード: 差分なし=0, 差分あり=1（CIやフックで使える）
"""
import argparse
import sys

import pandas as pd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("target")
    ap.add_argument("--report", default="report.md")
    a = ap.parse_args()

    b = pd.read_csv(a.base, dtype={"型式KEY": str}).fillna("").set_index("型式KEY")
    t = pd.read_csv(a.target, dtype={"型式KEY": str}).fillna("").set_index("型式KEY")

    missing = b.index.difference(t.index)  # 基準にあって対象にない
    extra = t.index.difference(b.index)    # 対象にあって基準にない
    both = b.index.intersection(t.index)
    qty_ng = [k for k in both if float(b.at[k, "数量"]) != float(t.at[k, "数量"])]
    has_price = "単価" in b.columns and "単価" in t.columns and (b["単価"].sum() > 0 or t["単価"].sum() > 0)
    price_ng = [k for k in both if has_price and float(b.at[k, "単価"]) != float(t.at[k, "単価"])]

    lines = [f"# リスト比較レポート", "",
             f"- 基準: `{a.base}`（{len(b)}型式）",
             f"- 対象: `{a.target}`（{len(t)}型式）", ""]
    total = len(missing) + len(extra) + len(qty_ng) + len(price_ng)
    if has_price:
        bs, ts = b["金額"].sum(), t["金額"].sum()
        lines += [f"- 金額合計: 基準 {bs:,.0f} / 対象 {ts:,.0f} / 差 {ts - bs:+,.0f}", ""]
    if total == 0:
        lines += ["## 結果: 不一致なし ✅"]
    else:
        lines += [f"## 結果: 不一致 {total} 件", ""]
        if len(missing):
            lines += [f"### 漏れ（基準にあるが対象にない） {len(missing)}件", "",
                      "| 型式 | 数量 | メーカ | 備考 |", "|---|---|---|---|"]
            for k in missing:
                r = b.loc[k]
                lines.append(f"| {r['型式']} | {r['数量']:g} | {r['メーカ']} | {r['備考']} |")
            lines.append("")
        if len(extra):
            lines += [f"### 余分（対象にあるが基準にない） {len(extra)}件", "",
                      "| 型式 | 数量 | メーカ | 備考 |", "|---|---|---|---|"]
            for k in extra:
                r = t.loc[k]
                lines.append(f"| {r['型式']} | {r['数量']:g} | {r['メーカ']} | {r['備考']} |")
            lines.append("")
        if qty_ng:
            lines += [f"### 数量不一致 {len(qty_ng)}件", "",
                      "| 型式 | 基準 | 対象 | 差 |", "|---|---|---|---|"]
            for k in qty_ng:
                bq, tq = float(b.at[k, "数量"]), float(t.at[k, "数量"])
                lines.append(f"| {b.at[k, '型式']} | {bq:g} | {tq:g} | {tq - bq:+g} |")
            lines.append("")
        if price_ng:
            lines += [f"### 単価不一致 {len(price_ng)}件", "",
                      "| 型式 | 基準単価 | 対象単価 | 差 |", "|---|---|---|---|"]
            for k in price_ng:
                bp, tp = float(b.at[k, "単価"]), float(t.at[k, "単価"])
                lines.append(f"| {b.at[k, '型式']} | {bp:,.0f} | {tp:,.0f} | {tp - bp:+,.0f} |")
            lines.append("")

    with open(a.report, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n".join(lines))
    sys.exit(0 if total == 0 else 1)


if __name__ == "__main__":
    main()
