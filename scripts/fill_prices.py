"""相見積もりのスコア（Excel）から商社ごとの金額を拾い、BOM（Excel）の各行に転記した新しい Excel を作る。
元の BOM もスコアも編集しない。出力は <BOM名>_見積.xlsx（--out で変更可）。書式は BOM のまま、右端に商社ごとの列を足す。

使い方:
  python scripts/fill_prices.py <BOM.xlsx> <スコア.xlsx>
  python scripts/fill_prices.py <BOM.xlsx> <スコア.xlsx> --show-columns      列の判定だけ見る
  python scripts/fill_prices.py BOM.xlsx score.xlsx --vendors "A商社=A社単価" "B商社=B社 単価"   商社列を手で指定
  python scripts/fill_prices.py BOM.xlsx score.xlsx --key 型式=部品型番 --score-key 型式=品番  型式列を手で指定
突き合わせのキーは型式（全角半角・大文字小文字・ハイフン・空白・スラッシュの違いは同一視）。
型式が一致しない行は空欄のまま残し、標準出力に「要確認」として列挙する。勝手に似た型式を当てない。
終了コード: 全行転記 0 / 未転記あり 1 / 失敗 2
"""
import argparse
import sys
import unicodedata
from pathlib import Path

import openpyxl
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_items import COL_GUESS, norm_key, to_num  # noqa: E402

VENDOR_HINT = ("商社", "社", "見積", "単価", "price", "quote", "vendor", "supplier")
NOT_VENDOR = ("数量", "qty", "quantity", "型式", "型番", "品番", "品名", "名称", "メーカ", "備考", "no", "№", "符号", "定価", "合計", "小計")


def text(s) -> str:
    """表示用: 全角→半角などは正規化するが大文字小文字は保つ。"""
    return unicodedata.normalize("NFKC", str(s)).strip() if s is not None and str(s) != "nan" else ""


def nfkc(s) -> str:
    """照合用: text() を小文字化。"""
    return text(s).lower()


def find_header_row(ws, key_override: str | None, limit: int = 15) -> tuple[int, int]:
    """型式らしい見出しがある行と列（1 始まり）を返す。"""
    cands = [nfkc(key_override)] if key_override else [nfkc(k) for k in COL_GUESS["型式"]]
    for r in range(1, min(ws.max_row, limit) + 1):
        for c in range(1, ws.max_column + 1):
            v = nfkc(ws.cell(r, c).value)
            if v and any(v == k or k in v for k in cands):
                return r, c
    sys.exit("ERROR: 型式列が見つかりません。--key 型式=<列名> を指定してください。")


def read_score(path: str, key_override: str | None, vendors: dict[str, str]) -> tuple[dict[str, dict[str, float]], list[str], dict]:
    """スコアを読み、{商社名: {型式KEY: 単価}} と商社名の並びを返す。"""
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb.active
    rows = [[c for c in r] for r in ws.iter_rows(values_only=True)]
    wb.close()
    df = pd.DataFrame(rows)
    # 見出し行: 型式らしい文字がある最初の行
    cands = [nfkc(key_override)] if key_override else [nfkc(k) for k in COL_GUESS["型式"]]
    hr, kc = None, None
    for r in range(min(len(df), 15)):
        for c in range(df.shape[1]):
            v = nfkc(df.iat[r, c])
            if v and any(v == k or k in v for k in cands):
                hr, kc = r, c
                break
        if hr is not None:
            break
    if hr is None:
        sys.exit("ERROR: スコアの型式列が見つかりません。--score-key 型式=<列名> を指定してください。")
    # 見出し名: 見出し行＋その1つ上（商社名が上段に書かれる2段見出しに対応）
    names = {}
    for c in range(df.shape[1]):
        top = text(df.iat[hr - 1, c]) if hr > 0 else ""
        cur = text(df.iat[hr, c])
        names[c] = (f"{top} {cur}".strip() if top and top != cur else cur) or f"col{c + 1}"
    body = df.iloc[hr + 1:].reset_index(drop=True)
    keys = body[kc].map(norm_key)

    if vendors:  # 手動指定: 表示名=列名
        vcols = {}
        for disp, colname in vendors.items():
            hit = [c for c, n in names.items() if nfkc(colname) == n.lower() or nfkc(colname) in n.lower()]
            if not hit:
                sys.exit(f"ERROR: スコアに列『{colname}』がありません。columns={list(names.values())}")
            vcols[disp] = hit[0]
    else:  # 自動: 数値が主体で、数量などの既知列ではない列を商社列とみなす
        vcols = {}
        for c in range(df.shape[1]):
            if c == kc:
                continue
            n = names[c].lower()
            if any(x in n for x in NOT_VENDOR) and not any(x in n for x in VENDOR_HINT):
                continue
            col = to_num(body[c])
            nonblank = body[c].map(lambda v: nfkc(v) != "")
            if nonblank.sum() == 0 or (col[nonblank] > 0).mean() < 0.6:
                continue
            vcols[names[c] or f"col{c + 1}"] = c
    if not vcols:
        sys.exit(f"ERROR: 商社の金額列を判定できません。--vendors 表示名=列名 で指定。columns={list(names.values())}")

    out = {}
    for disp, c in vcols.items():
        vals = to_num(body[c])
        out[disp] = {k: float(v) for k, v in zip(keys, vals) if k and v}
    info = {"header_row": hr + 1, "key_col": names[kc], "vendors": {d: names[c] for d, c in vcols.items()},
            "columns": list(names.values())}
    return out, list(vcols), info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bom")
    ap.add_argument("score")
    ap.add_argument("--out")
    ap.add_argument("--show-columns", action="store_true")
    ap.add_argument("--key", nargs="*", default=[], help="BOM 側: 型式=列名")
    ap.add_argument("--score-key", nargs="*", default=[], help="スコア側: 型式=列名")
    ap.add_argument("--vendors", nargs="*", default=[], help="表示名=スコアの列名 ...")
    ap.add_argument("--with-amount", action="store_true", help="単価×数量の金額列も足す")
    a = ap.parse_args()
    key_b = dict(kv.split("=", 1) for kv in a.key).get("型式")
    key_s = dict(kv.split("=", 1) for kv in a.score_key).get("型式")
    vendors = dict(kv.split("=", 1) for kv in a.vendors)

    prices, order, info = read_score(a.score, key_s, vendors)
    wb = openpyxl.load_workbook(a.bom)
    ws = wb.active
    hr, kc = find_header_row(ws, key_b)
    qty_col = None
    if a.with_amount:
        for c in range(1, ws.max_column + 1):
            if any(k in nfkc(ws.cell(hr, c).value) for k in COL_GUESS["数量"]):
                qty_col = c
                break

    if a.show_columns:
        print("BOM 見出し行:", hr, " 型式列:", ws.cell(hr, kc).value)
        print("スコア 見出し行:", info["header_row"], " 型式列:", info["key_col"])
        print("スコアの列:", info["columns"])
        print("商社列の判定:", info["vendors"])
        return

    # 右端に商社列を追加
    base_col = ws.max_column + 1
    cols = {}
    for i, v in enumerate(order):
        c = base_col + i * (2 if qty_col else 1)
        ws.cell(hr, c, v if v.endswith("単価") else f"{v} 単価")
        cols[v] = c
        if qty_col:
            ws.cell(hr, c + 1, v[:-2] + "金額" if v.endswith("単価") else f"{v} 金額")
    filled, missing, seen = 0, [], set()
    for r in range(hr + 1, ws.max_row + 1):
        model = ws.cell(r, kc).value
        k = norm_key(model)
        if not k:
            continue
        seen.add(k)
        hit = False
        for v in order:
            p = prices[v].get(k)
            if p is not None:
                ws.cell(r, cols[v], p)
                if qty_col:
                    q = to_num(pd.Series([ws.cell(r, qty_col).value])).iloc[0]
                    ws.cell(r, cols[v] + 1, p * q)
                hit = True
        if hit:
            filled += 1
        else:
            missing.append(str(model))
    extra = sorted({k for v in order for k in prices[v]} - seen)

    out = a.out or str(Path(a.bom).with_name(Path(a.bom).stem + "_見積.xlsx"))
    wb.save(out)
    print(f"OK: 商社 {len(order)} 社 {order}  転記 {filled} 行 / BOM {filled + len(missing)} 行  → {out}")
    print(f"列対応: BOM型式={ws.cell(hr, kc).value}  スコア型式={info['key_col']}  商社={info['vendors']}")
    if missing:
        print(f"要確認: スコアに見つからず空欄のまま {len(missing)} 行: " + ", ".join(missing))
    if extra:
        print(f"要確認: スコアにあるが BOM にない型式 {len(extra)} 件: " + ", ".join(extra))
    sys.exit(1 if missing else 0)


if __name__ == "__main__":
    main()
