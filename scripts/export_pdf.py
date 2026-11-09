"""発注書(Excel)をPDFにエクスポートする。ポテトが「不一致なし」のときだけ呼ぶ。

使い方:
  python scripts/export_pdf.py <発注書.xlsx> [--out 出力.pdf]
既定の出力先: 入力と同じフォルダの <同名>.pdf。既存ファイルは上書きせず終了コード 3 で止まる。
入力Excelは読み取り専用で開き、保存しない。Windows + Microsoft Excel + pywin32 が必要。
終了コード: 成功 0 / 失敗 2 / 出力先が既に存在 3
"""
import argparse
import sys
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("xlsx")
    ap.add_argument("--out")
    a = ap.parse_args()

    src = Path(a.xlsx).resolve()
    if src.suffix.lower() not in (".xlsx", ".xlsm", ".xls"):
        print(f"ERROR: Excelファイルではありません: {src}")
        sys.exit(2)
    if not src.exists():
        print(f"ERROR: ファイルが見つかりません: {src}")
        sys.exit(2)
    out = Path(a.out).resolve() if a.out else src.with_suffix(".pdf")
    if out.exists():
        print(f"出力先が既に存在するため中止: {out}")
        sys.exit(3)

    try:
        import win32com.client
    except ImportError:
        print("ERROR: pywin32 が未インストールです (pip install pywin32)")
        sys.exit(2)

    excel = wb = None
    try:
        excel = win32com.client.DispatchEx("Excel.Application")
        excel.Visible = False
        excel.DisplayAlerts = False
        wb = excel.Workbooks.Open(str(src), ReadOnly=True, UpdateLinks=0)
        wb.ExportAsFixedFormat(0, str(out))  # 0 = xlTypePDF（ブック全体、各シートの印刷設定のまま）
    except Exception as e:
        print(f"ERROR: PDF出力に失敗: {e}")
        sys.exit(2)
    finally:
        if wb is not None:
            wb.Close(SaveChanges=False)
        if excel is not None:
            excel.Quit()

    if not out.exists():
        print("ERROR: PDFが作成されませんでした")
        sys.exit(2)
    print(f"PDF出力: {out}")


if __name__ == "__main__":
    main()
