"""検証用の入力ファイルを tests/fixtures/ に生成する（毎回同じ内容。git には入れない）。"""
import shutil
import sys
from pathlib import Path

import openpyxl
import pandas as pd

OUT = Path(__file__).resolve().parent / "fixtures"


def write_pdf(path: Path, pages: list[list[tuple[int, int, str]]]):
    """依存なしの最小 PDF 書き出し（Helvetica、ASCII のみ）。検図テスト用。"""
    objs = []  # (num, body bytes)
    font_num = 3 + 2 * len(pages)
    page_nums = []
    for i, items in enumerate(pages):
        content = "BT /F1 12 Tf " + " ".join(f"1 0 0 1 {x} {y} Tm ({t}) Tj" for x, y, t in items) + " ET"
        cnum, pnum = 3 + 2 * i, 4 + 2 * i
        objs.append((cnum, f"<< /Length {len(content)} >>\nstream\n{content}\nendstream".encode()))
        objs.append((pnum, f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents {cnum} 0 R /Resources << /Font << /F1 {font_num} 0 R >> >> >>".encode()))
        page_nums.append(pnum)
    objs.append((font_num, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"))
    objs.insert(0, (2, f"<< /Type /Pages /Kids [{' '.join(f'{n} 0 R' for n in page_nums)}] /Count {len(pages)} >>".encode()))
    objs.insert(0, (1, b"<< /Type /Catalog /Pages 2 0 R >>"))
    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for num, body in sorted(objs):
        offsets[num] = len(out)
        out += f"{num} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    n = max(offsets) + 1
    out += f"xref\n0 {n}\n0000000000 65535 f \n".encode()
    for k in range(1, n):
        out += f"{offsets[k]:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {n} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(bytes(out))


def main():
    OUT.mkdir(exist_ok=True)
    for stale in ("kizuki.json", "tasks.json"):      # 前回のケースが残した保存ファイルは消す（id が 1 から始まる前提）
        (OUT / stale).unlink(missing_ok=True)
    shutil.rmtree(OUT / "drawing_画像", ignore_errors=True)
    (OUT / "memo.md").write_text("# 定例会議 議事録\n\n- 決定事項: 盤図の改訂は来週後半\n- ToDo: 端子台の型式を確認\n\n| 項目 | 担当 |\n|---|---|\n| 見積 | 藤原 |\n", encoding="utf-8")
    try:                                   # Adobe-Japan1 の CID フォント（埋め込みなし）だけで書いた PDF。pdftext の読み取りテスト用
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        from reportlab.pdfgen import canvas
        pdfmetrics.registerFont(UnicodeCIDFont("HeiseiKakuGo-W5"))
        c = canvas.Canvas(str(OUT / "cid_jp.pdf")); c.setFont("HeiseiKakuGo-W5", 12)
        c.drawString(72, 760, "電気図面 検図 マニュアル 端子台 KM1"); c.showPage(); c.save()
    except Exception:
        pass
    # ポテト用: BOM(Excel) と 発注書(CSV、列名違い・小計行・全角型式)
    pd.DataFrame({"型式": ["KV-8000", "KV-N24AR", "MR-J4-10A", "ＦＸ５Ｕ－32MR"], "品名": ["PLC", "拡張", "サーボ", "PLC"],
                  "数量": [1, 2, 3, 1], "単価": [150000, 30000, 80000, 50000], "メーカ": ["KEYENCE", "KEYENCE", "三菱", "三菱"]}
                 ).to_excel(OUT / "bom.xlsx", index=False)
    t = pd.DataFrame({"部品型番": ["kv8000", "KV-N24AR", "MR-J4-10A", "OMRON-G2R"], "名称": ["PLC", "拡張", "サーボ", "リレー"],
                      "数": [1, 3, 3, 5], "単価": [150000, 30000, 85000, 500], "メーカー": ["KEYENCE", "KEYENCE", "三菱", "OMRON"]})
    t.loc[len(t)] = ["小計", "", "", 300000, ""]
    t.to_csv(OUT / "order.csv", index=False)

    # レックス用: タイトル行付き BOM と 2段見出しのスコア
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(["制御盤 BOM（案件X）"]); ws.append(["No", "型式", "品名", "メーカ", "数量", "備考"])
    for i, (m, n, mk, q) in enumerate([("KV-8000", "PLC", "KEYENCE", 1), ("KV-N24AR", "拡張", "KEYENCE", 2),
                                        ("MR-J4-10A", "サーボ", "三菱", 3), ("ＦＸ５Ｕ－32MR", "PLC", "三菱", 1),
                                        ("G2R-1-SN", "リレー", "OMRON", 10)], 1):
        ws.append([i, m, n, mk, q, ""])
    wb.save(OUT / "bom2.xlsx")
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(["", "", "", "A商社", "B商社", "C商社"]); ws.append(["品番", "品名", "数量", "単価", "単価", "単価"])
    for row in [["kv8000", "PLC", 1, 150000, 148000, 151000], ["KV-N24AR", "拡張", 2, 30000, 29500, None],
                ["MR-J4-10A", "サーボ", 3, 80000, 81000, 79000], ["FX5U-32MR", "PLC", 1, 50000, 49000, 52000],
                ["XW2B-20G4", "端子台", 2, 1200, 1100, 1300]]:
        ws.append(row)
    wb.save(OUT / "score2.xlsx")

    # スリンキー用: フィラー入りの文字起こし
    (OUT / "raw.txt").write_text(
        "えーと、本日の、あの、打ち合わせですが、まあ、KV-8000 の納期の件ですね。\n"
        "えー、その その 納期は、うーん、11月20日で、はい、確定しました。\n"
        "あのー、担当は藤原さんで、えっとー、発注書は、なんか、PDF で送ります。\n", encoding="utf-8")
    (OUT / "tasks.json").write_text("[]", encoding="utf-8")   # ジェシー用: 空のタスク置き場
    write_pdf(OUT / "drawing.pdf", [
        # p.1: KM1 twice (ok), KM-2 vs KM2 (表記ゆれ), wire 101 twice (ok), wire 205 once (孤立線番), page "1/3", dwg no E-1001
        [(60, 700, "KM1"), (300, 700, "KM1"), (60, 650, "KM-2"), (300, 650, "KM2"), (60, 600, "101"), (300, 600, "101"),
         (60, 550, "205"), (420, 80, "E-1001"), (500, 60, "1/3")],
        # p.2: X001 once (孤立符号), page "3/3" (2/3 missing), dwg no E-1002 (図番不一致)
        [(60, 700, "X001"), (60, 650, "PB1"), (300, 650, "PB1"), (420, 80, "E-1002"), (500, 60, "3/3")],
    ])
    print(f"fixtures → {OUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
