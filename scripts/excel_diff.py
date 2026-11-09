#!/usr/bin/env python3
"""2 つの Excel / CSV をセル単位で比べて、変わったところだけを出す（サーバーとバズが使う）。

  python scripts/excel_diff.py <前の版> <後の版> [--max 30]

見るもの: シートの増減、セルの値・式・表示形式、図・グラフ・画像・コメント・マクロの数。
終了コード: 変更なし 0 / 変更あり 1 / 読めない 2
"""
from __future__ import annotations

import argparse
import csv
import sys
import zipfile
from pathlib import Path

OBJECTS = {"画像": "xl/media/", "グラフ": "xl/charts/chart", "図形・描画": "xl/drawings/drawing",
           "コメント": "xl/comments", "マクロ": "xl/vbaProject.bin"}


def _cells(path: Path) -> dict[str, dict[str, tuple]]:
    """{シート名: {"B3": (値または式, 表示形式)}}。空のセルは入れない。"""
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
        from openpyxl.utils import get_column_letter
        return {"(csv)": {f"{get_column_letter(c + 1)}{r + 1}": (v, "") for r, row in enumerate(rows)
                          for c, v in enumerate(row) if v != ""}}
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=False)
    out = {}
    for ws in wb.worksheets:
        out[ws.title] = {c.coordinate: (c.value, c.number_format if c.number_format != "General" else "")
                         for row in ws.iter_rows() for c in row if c.value is not None}
    return out


def _objects(path: Path) -> dict[str, int]:
    if path.suffix.lower() == ".csv":
        return {}
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
    return {k: sum(n.startswith(p) for n in names) for k, p in OBJECTS.items()}


def diff(before: Path, after: Path) -> dict:
    a, b = _cells(before), _cells(after)
    added, removed = [s for s in b if s not in a], [s for s in a if s not in b]
    renamed = list(zip(removed, added))          # 名前だけ変わったシート（pandas で書き直すと Sheet → Sheet1 になる）も中身を比べる
    pairs = [(s, s, s) for s in b if s in a] + [(f"{o} → {n}", o, n) for o, n in renamed]
    cells = {}
    for label, so, sn in pairs:
        old, new = a[so], b[sn]
        ch = [(k, old.get(k, (None, "")), new.get(k, (None, ""))) for k in sorted(set(old) | set(new), key=_order)
              if old.get(k) != new.get(k)]
        if ch:
            cells[label] = ch
    oa, ob = _objects(before), _objects(after)
    return {"added": added[len(renamed):], "removed": removed[len(renamed):], "cells": cells,
            "renamed": [f"{o} → {n}" for o, n in renamed],
            "objects": {k: (oa[k], ob[k]) for k in oa if oa[k] != ob.get(k, 0)}}


def _order(coord: str) -> tuple[int, str]:
    col = coord.rstrip("0123456789")
    return int(coord[len(col):]), col.rjust(3)


def _span(coords: list[str]) -> str:
    from openpyxl.utils.cell import column_index_from_string, coordinate_from_string, get_column_letter
    pts = [coordinate_from_string(c) for c in coords]
    cols = [column_index_from_string(c) for c, _ in pts]
    rows = [r for _, r in pts]
    lo, hi = f"{get_column_letter(min(cols))}{min(rows)}", f"{get_column_letter(max(cols))}{max(rows)}"
    return lo if lo == hi else f"{lo}:{hi}"


def _show(v) -> str:
    return "（空）" if v is None or v == "" else repr(v) if isinstance(v, str) and v.strip() != v else str(v)


def summary(d: dict, max_cells: int = 30) -> list[str]:
    """人とボットが読む行。変更がなければ空リスト。"""
    lines = ([f"シートを追加: {s}" for s in d["added"]] + [f"シートを削除: {s}" for s in d["removed"]]
             + [f"シート名を変更: {s}" for s in d["renamed"]])
    lines += [f"{k}: {o} → {n}（消えた・増えた）" for k, (o, n) in d["objects"].items()]
    shown = 0
    for sheet, ch in d["cells"].items():
        lines.append(f"{sheet}: {len(ch)} セル（{_span([k for k, _, _ in ch])} の範囲）")
        for k, (ov, of), (nv, nf) in ch:
            if shown >= max_cells:
                break
            fmt = f"（表示形式 {of or '標準'} → {nf or '標準'}）" if of != nf else ""
            lines.append(f"  {k}: {_show(ov)} → {_show(nv)}{fmt}" if ov != nv else f"  {k}: {_show(nv)}{fmt}")
            shown += 1
    total = sum(len(ch) for ch in d["cells"].values())
    if total > shown:
        lines.append(f"  …ほか {total - shown} セル")
    return lines


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("before")
    ap.add_argument("after")
    ap.add_argument("--max", type=int, default=30)
    a = ap.parse_args(argv)
    try:
        lines = summary(diff(Path(a.before), Path(a.after)), a.max)
    except Exception as e:  # noqa: BLE001
        print(f"読めない: {type(e).__name__}: {e}")
        return 2
    print("\n".join(lines) if lines else "変更なし")
    return 1 if lines else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
