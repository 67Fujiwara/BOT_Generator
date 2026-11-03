"""検証用の入力ファイルを tests/fixtures/ に生成する（毎回同じ内容。git には入れない）。"""
import sys
from pathlib import Path

import openpyxl
import pandas as pd

OUT = Path(__file__).resolve().parent / "fixtures"


def main():
    OUT.mkdir(exist_ok=True)
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
    print(f"fixtures → {OUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
