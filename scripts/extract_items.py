"""PDF / Excel / CSV のリスト（BOM・購買リスト・見積書・発注書）を標準CSVに正規化する。
列: 型式, 型式KEY, 品名, 数量, 単価, 金額, メーカ, 備考

使い方:
  python extract_items.py bom.xlsx bom.csv
  python extract_items.py 購買.pdf buy.csv
  python extract_items.py bom.xlsx --show-columns
  python extract_items.py bom.xlsx bom.csv --cols 型式=部品型番 数量=数
依存: pip install pandas openpyxl pdfplumber
"""
import argparse
import re
import sys
import unicodedata

import pandas as pd

# 列名の候補（小文字・正規化後で部分一致）
COL_GUESS = {
    "型式": ["型式", "型番", "品番", "部品番号", "model", "part", "p/n", "pn"],
    "品名": ["品名", "商品名", "名称", "品目", "description", "item"],
    "数量": ["数量", "数", "qty", "quantity", "個数"],
    "単価": ["単価", "unit price", "unitprice", "price"],
    "金額": ["金額", "amount", "小計", "合計"],
    "メーカ": ["メーカ", "メーカー", "maker", "manufacturer", "mfr"],
    "備考": ["備考", "note", "remarks", "符号", "ref"],
}
# 見積・発注書の集計行は除外
SKIP_ROW = re.compile(r"(?:小計|合計|消費税|税込|税抜|送料|値引|total|subtotal|tax)", re.I)


def to_num(series) -> pd.Series:
    """'¥12,300' '1,200 円' などを数値に。"""
    s = series.astype(str).str.replace(r"[¥￥,円\s]", "", regex=True)
    return pd.to_numeric(s.str.extract(r"(-?[\d.]+)")[0], errors="coerce").fillna(0)


def norm_key(s) -> str:
    """型式の表記ゆれを吸収: 全角→半角、大文字化、空白・ハイフン類を除去。"""
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return ""
    s = unicodedata.normalize("NFKC", str(s)).upper()
    return re.sub(r"[\s\-‐‑–—_/.]", "", s)


def load_table(path: str) -> pd.DataFrame:
    p = path.lower()
    if p.endswith((".xlsx", ".xlsm", ".xls")):
        return pd.read_excel(path, dtype=str)
    if p.endswith((".csv", ".tsv")):
        sep = "\t" if p.endswith(".tsv") else ","
        return pd.read_csv(path, dtype=str, sep=sep)
    if p.endswith(".pdf"):
        import pdfplumber

        frames = []
        with pdfplumber.open(path) as pdf:
            text_strategy = {"vertical_strategy": "text", "horizontal_strategy": "text"}
            for page in pdf.pages:
                tables = page.extract_tables()  # 罫線あり
                if not tables:
                    tables = page.extract_tables(text_strategy)  # 罫線なし
                for table in tables:
                    if not table or len(table) < 2:
                        continue
                    header = [str(h or "").strip() for h in table[0]]
                    df = pd.DataFrame(table[1:], columns=header)
                    frames.append(df)
        if not frames:
            sys.exit("ERROR: PDFから表を抽出できませんでした（スキャンPDFの可能性）")
        # 列構成が同じ表だけ連結（ページ跨ぎ対応）
        base = list(frames[0].columns)
        same = [f for f in frames if list(f.columns) == base]
        return pd.concat(same, ignore_index=True)
    sys.exit(f"ERROR: 未対応の拡張子: {path}")


def guess_cols(df: pd.DataFrame, override: dict) -> dict:
    mapping = dict(override)
    lowered = {c: unicodedata.normalize("NFKC", str(c)).lower() for c in df.columns}
    for std, cands in COL_GUESS.items():
        if std in mapping:
            continue
        for col, low in lowered.items():
            if any(k in low for k in cands):
                mapping[std] = col
                break
    return mapping


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst", nargs="?")
    ap.add_argument("--show-columns", action="store_true")
    ap.add_argument("--cols", nargs="*", default=[], help="型式=列名 数量=列名 ...")
    a = ap.parse_args()

    df = load_table(a.src)
    df.columns = [str(c).strip() for c in df.columns]

    if a.show_columns:
        print("columns:", list(df.columns))
        print(df.head(5).to_string())
        return

    override = dict(kv.split("=", 1) for kv in a.cols)
    m = guess_cols(df, override)
    if "型式" not in m and "品名" in m:
        m["型式"] = m["品名"]  # 見積書などで型式列がない場合は品名をキーにする
    if "型式" not in m:
        sys.exit(f"ERROR: 型式/品名列を判定できません。--cols 型式=<列名> を指定。columns={list(df.columns)}")

    out = pd.DataFrame()
    out["型式"] = df[m["型式"]].astype(str).str.strip()
    out["型式KEY"] = out["型式"].map(norm_key)
    qty = df[m["数量"]] if "数量" in m else 1
    out["数量"] = to_num(pd.Series(qty, index=df.index))
    out["単価"] = to_num(df[m["単価"]]) if "単価" in m else 0
    out["金額"] = to_num(df[m["金額"]]) if "金額" in m else out["数量"] * out["単価"]
    out["品名"] = df[m["品名"]].astype(str).str.strip() if "品名" in m and m["品名"] != m["型式"] else ""
    out["メーカ"] = df[m["メーカ"]].astype(str).str.strip() if "メーカ" in m else ""
    out["備考"] = df[m["備考"]].astype(str).str.strip() if "備考" in m else ""
    out = out[(out["型式KEY"] != "") & ~out["型式"].str.contains(SKIP_ROW)]

    # 同一型式が複数行なら数量を合算（符号違いで分かれている場合に対応）
    agg = out.groupby("型式KEY", as_index=False).agg(
        型式=("型式", "first"), 品名=("品名", "first"), 数量=("数量", "sum"),
        単価=("単価", "max"), 金額=("金額", "sum"), メーカ=("メーカ", "first"),
        備考=("備考", lambda s: " / ".join(x for x in s if x and x != "nan")),
    )
    dst = a.dst or a.src.rsplit(".", 1)[0] + ".normalized.csv"
    agg.to_csv(dst, index=False, encoding="utf-8-sig")
    print(f"OK: {len(df)}行 → {len(agg)}型式  列対応={m}  → {dst}")


if __name__ == "__main__":
    main()
