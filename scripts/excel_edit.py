#!/usr/bin/env python3
"""Excel / CSV を決まった操作だけで変えて、変えたセルを記録する（ボットが表を変えるのはこれだけ）。

  python scripts/excel_edit.py apply <ファイル> [--out <別名>] [--set "シート!C5=値" ...] [--clear "シート!C5" ...]
      [--insert-rows "シート!5:7"] [--delete-rows "シート!5"] [--insert-cols "シート!C:E"] [--delete-cols "シート!C"]
      [--json <操作.json>]
  python scripts/excel_edit.py enforce <控え> <ファイル> [--allow "シート!C3" ...] [--since <epoch>]

apply: 書いた順に変えて保存し（--out なら別名、無ければ上書き）、変わったセルを app/edit_log.jsonl に記録する。
  「シート!」を省くと開いているシート。行は 5 か 5:7、列は C か C:E（Excel と同じ書き方）。
  --set の値: = で始まれば式、数に見えれば数（先頭が 0 のものは文字のまま）、空なら消す、ほかは文字。
  --json: [{"op": "set", "cell": "Sheet!C5", "value": 3}, {"op": "clear", "cell": "C6"},
           {"op": "insert_rows", "at": "Sheet!5", "n": 2}, ...]。value はそのまま使う。
  .csv（UTF-8）は set / clear だけ。
enforce: 控えと比べて、--since 以降に apply が記録したセルと --allow のセル以外の変更を控えに戻す（サーバーが返事の後に使う）。
  シートが消えた・名前が変わった・記録のないシートが増えたときは、ファイルを丸ごと控えに戻す。
  図・グラフ・画像が減ったのは戻せない（openpyxl では残せない）ので、注意として出すだけ。
終了コード: apply 変えた 0 / 参照が変・読めない・変えるものがない 2
           enforce 範囲外の変更なし 0 / 戻した 1 / 読めない 2
"""
from __future__ import annotations

import argparse
import codecs
import csv
import io
import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

import openpyxl
from openpyxl.cell.cell import MergedCell
from openpyxl.utils.cell import column_index_from_string, coordinate_from_string, get_column_letter
from openpyxl.utils.exceptions import CellCoordinatesException

sys.path.insert(0, str(Path(__file__).resolve().parent))
import excel_diff  # noqa: E402

LOG = Path(__file__).resolve().parent.parent / "app" / "edit_log.jsonl"
CSV_SHEET = "(csv)"                       # excel_diff が CSV に付けるシート名
MAX_ROW, MAX_COL = 1_048_576, 16_384
SPANS = {"insert_rows": "row", "delete_rows": "row", "insert_cols": "col", "delete_cols": "col"}  # openpyxl のメソッド名
Scope = dict[str, set[str]]               # シート名 → 控えと違ってよいセル。キーにあるシートは増えてもよい


class RefError(ValueError):
    """参照が変・ないシート・できない操作。"""


def _key(path) -> str:
    return os.path.normcase(os.path.realpath(path))


def _split(ref) -> tuple[str | None, str]:
    sheet, _, addr = str(ref).rpartition("!")
    return sheet.strip("'") or None, addr.strip().upper().replace("$", "")


def _coord(addr: str) -> tuple[int, int]:
    """'C5' → (行 5, 列 3)。"""
    try:
        col, row = coordinate_from_string(addr)
        c = column_index_from_string(col)
    except (CellCoordinatesException, ValueError):
        row = c = 0
    if not (1 <= row <= MAX_ROW and 1 <= c <= MAX_COL):
        raise RefError(f"セル番地が読めない: {addr or '（空）'}")
    return row, c


def _span(at, kind: str, n) -> tuple[str | None, int, int]:
    """'Sheet!5:7' → (シート, 先頭, 個数)。"""
    sheet, addr = _split(at)
    lo, colon, hi = addr.partition(":")
    try:
        num = int if SPANS[kind] == "row" else column_index_from_string
        first, last = num(lo), num(hi or lo)
        if n is not None:
            if colon:
                raise ValueError
            last = first + int(n) - 1
    except (ValueError, TypeError):
        raise RefError(f"{kind} の位置が読めない: {at}") from None
    if not (1 <= first <= last):
        raise RefError(f"{kind} の位置が読めない: {at}")
    return sheet, first, last - first + 1


def _value(text: str):
    if re.fullmatch(r"-?(0|[1-9]\d*)", text):
        return int(text)
    if re.fullmatch(r"-?(0|[1-9]\d*)\.\d+", text):
        return float(text)
    return text or None


def _sheet(wb, name: str | None):
    if name is None:
        return wb.active
    if name not in wb.sheetnames:
        raise RefError(f"シートがない: {name}（あるのは {'、'.join(wb.sheetnames)}）")
    return wb[name]


def _load(path: Path):
    return openpyxl.load_workbook(path, keep_vba=path.suffix.lower() == ".xlsm")


def _apply_xlsx(wb, ops: list[dict]) -> None:
    for op in ops:
        kind = op.get("op")
        if kind in ("set", "clear"):
            sheet, addr = _split(op.get("cell", ""))
            ws = _sheet(wb, sheet)
            cell = ws.cell(*_coord(addr))
            if isinstance(cell, MergedCell):
                raise RefError(f"結合セルの途中は変えられない: {ws.title}!{cell.coordinate}")
            cell.value = op.get("value") if kind == "set" else None
        elif kind in SPANS:
            sheet, first, amount = _span(op.get("at", ""), kind, op.get("n"))
            getattr(_sheet(wb, sheet), kind)(first, amount)
        else:
            raise RefError(f"知らない操作: {kind}")


def _apply_csv(rows: list[list[str]], ops: list[dict]) -> None:
    for op in ops:
        kind = op.get("op")
        if kind not in ("set", "clear"):
            raise RefError(f".csv でできるのは set / clear だけ: {kind}")
        sheet, addr = _split(op.get("cell", ""))
        if sheet not in (None, CSV_SHEET):
            raise RefError(f".csv にシートはない: {sheet}")
        row, col = _coord(addr)
        r, c = row - 1, col - 1
        value = "" if kind == "clear" or op.get("value") is None else str(op["value"])
        if value == "" and (r >= len(rows) or c >= len(rows[r])):
            continue
        rows.extend([] for _ in range(r + 1 - len(rows)))
        rows[r].extend("" for _ in range(c + 1 - len(rows[r])))
        rows[r][c] = value


def _read_csv(path: Path) -> tuple[list[list[str]], tuple[bool, str]]:
    """(行, (BOM の有無, 改行))。"""
    raw = path.read_bytes()
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline="")))
    return rows, (raw.startswith(codecs.BOM_UTF8), "\r\n" if b"\r\n" in raw else "\n")


def _write_csv(path: Path, rows: list[list[str]], fmt: tuple[bool, str]) -> None:
    buf = io.StringIO(newline="")
    csv.writer(buf, lineterminator=fmt[1]).writerows(rows)
    path.write_bytes((codecs.BOM_UTF8 if fmt[0] else b"") + buf.getvalue().encode("utf-8"))


def apply(src: Path, out: Path, ops: list[dict]) -> dict:
    """ops を順に当てて out に保存し、記録する。前の版との差（excel_diff.diff の形）を返す。"""
    with tempfile.TemporaryDirectory() as tmp:
        before = Path(tmp) / src.name
        shutil.copy2(src, before)
        if src.suffix.lower() == ".csv":
            rows, fmt = _read_csv(src)
            _apply_csv(rows, ops)
            _write_csv(out, rows, fmt)
        else:
            wb = _load(src)
            _apply_xlsx(wb, ops)
            wb.save(out)
        return record(out, before)


def record(out_path, before_path) -> dict:
    """out_path を before_path と比べ、変わったセルと増えたシートを記録する（apply と fill_prices が使う）。差を返す。"""
    d = excel_diff.diff(Path(before_path), Path(out_path))
    cells = {s: [k for k, _, _ in ch] for s, ch in d["cells"].items()}
    if cells or d["added"]:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"file": _key(out_path), "at": time.time(), "cells": cells,
                                "added_sheets": d["added"]}, ensure_ascii=False) + "\n")
        if LOG.stat().st_size > 2_000_000:
            lines = LOG.read_text(encoding="utf-8").splitlines(keepends=True)
            LOG.write_text("".join(lines[len(lines) // 2:]), encoding="utf-8")
    return d


def load_scope(file, since: float = 0, source=None) -> Scope:
    """file について since 以降に記録されたセルとシート。source（file を作った元）で記録したものも含める。"""
    scope: Scope = {}
    keys = {_key(file)} | ({_key(source)} if source else set())
    try:
        text = LOG.read_text(encoding="utf-8")
    except FileNotFoundError:
        return scope
    for line in text.splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("file") not in keys or e.get("at", 0) < since:
            continue
        for s, cells in e.get("cells", {}).items():
            scope.setdefault(s, set()).update(cells)
        for s in e.get("added_sheets", []):
            scope.setdefault(s, set())
    return scope


def out_of_scope(d: dict, scope: Scope) -> list[tuple[str, str]]:
    return [(s, k) for s, ch in d["cells"].items() for k, _, _ in ch if k not in scope.get(s, ())]


def _restore_xlsx(before: Path, after: Path, cells: list[tuple[str, str]]) -> None:
    src, wb = openpyxl.load_workbook(before), _load(after)
    for s, k in cells:
        ws = wb[s]
        for rng in [r for r in ws.merged_cells.ranges if k in r]:
            ws.unmerge_cells(str(rng))
        old, new = src[s][k], ws[k]
        new.value, new.number_format = old.value, old.number_format
    wb.save(after)


def _restore_csv(before: Path, after: Path, keep: set[tuple[str, str]]) -> None:
    # 控えを土台に範囲内のセルだけ載せる（後から足された列の空のカンマが残らない）
    rows, fmt = _read_csv(before)
    new, _ = _read_csv(after)

    def at(k):
        r, c = _coord(k)
        return new[r - 1][c - 1] if r <= len(new) and c <= len(new[r - 1]) else ""
    _apply_csv(rows, [{"op": "set", "cell": k, "value": at(k)} for _, k in keep])
    _write_csv(after, rows, fmt)


def _line(s: str, k: str, old: tuple, new: tuple) -> str:
    (ov, of), (nv, nf) = old, new
    what = f"{excel_diff._show(nv)} → {excel_diff._show(ov)}" if ov != nv else f"表示形式 {nf or '標準'} → {of or '標準'}"
    return f"戻した: {k if s == CSV_SHEET else f'{s}!{k}'} ({what})"


def enforce(before: Path, after: Path, scope: Scope, d: dict | None = None) -> list[str]:
    """scope の外の変更を控え before に戻し、戻したことを行で返す（何も戻さなければ空）。d は before と after の差（あれば使う）。"""
    d = excel_diff.diff(before, after) if d is None else d
    whole = ([f"シート「{s}」が消えた" for s in d["removed"]] + [f"シート名が変わった（{s}）" for s in d["renamed"]]
             + [f"記録のないシート「{s}」が増えた" for s in d["added"] if s not in scope])
    if whole:
        shutil.copyfile(before, after)    # copy2 だと更新時刻まで戻り、サーバーが「変わったファイル」として拾えない
        return [f"丸ごと控えに戻した: {'、'.join(whole)}"]
    bad = out_of_scope(d, scope)
    if not bad:
        return []
    changed = [(s, k) for s, ch in d["cells"].items() for k, _, _ in ch]
    if len(bad) == len(changed) and not d["added"]:
        shutil.copyfile(before, after)    # 全部が範囲外なら丸ごと戻すほうが、excel_diff の見ない書式・列幅・図まで戻る
    elif after.suffix.lower() == ".csv":
        _restore_csv(before, after, set(changed) - set(bad))
    else:
        _restore_xlsx(before, after, bad)
    vals = {(s, k): (o, n) for s, ch in d["cells"].items() for k, o, n in ch}
    lines = [_line(s, k, *vals[s, k]) for s, k in bad]
    return lines[:30] + ([f"…ほか {len(lines) - 30} セル"] if len(lines) > 30 else [])


class _Op(argparse.Action):
    """--set / --clear / --insert-rows … / --json を、書いた順のまま 1 つの列にためる。"""

    def __call__(self, parser, ns, value, option_string=None):
        ns.ops = [*ns.ops, (self.const, value)]


def _ops(pairs: list[tuple[str, str]], is_csv: bool) -> list[dict]:
    ops: list[dict] = []
    for kind, text in pairs:
        if kind == "json":
            items = json.loads(Path(text).read_text(encoding="utf-8"))
            if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
                raise RefError(f'--json は操作のリスト（[{{"op": "set", ...}}, ...]）: {text}')
            ops += items
        elif kind == "set":
            ref, eq, raw = text.partition("=")
            if not eq:
                raise RefError(f"--set は「シート!セル=値」の形で: {text}")
            ops.append({"op": "set", "cell": ref, "value": raw if is_csv else _value(raw)})
        elif kind == "clear":
            ops.append({"op": "clear", "cell": text})
        else:
            ops.append({"op": kind, "at": text})
    return ops


def _allowed(refs: list[str], after: Path) -> Scope:
    scope: Scope = {}
    default = None
    for ref in refs:
        sheet, addr = _split(ref)
        if sheet is None and default is None:
            if after.suffix.lower() == ".csv":
                default = CSV_SHEET
            else:
                wb = openpyxl.load_workbook(after, read_only=True)
                default = wb.active.title
                wb.close()
        row, col = _coord(addr)
        scope.setdefault(sheet or default, set()).add(f"{get_column_letter(col)}{row}")
    return scope


def _apply_cli(a) -> int:
    src = Path(a.file)
    out = Path(a.out) if a.out else src
    try:
        if src.suffix.lower() not in (".xlsx", ".xlsm", ".csv"):
            raise RefError(f".xlsx / .xlsm / .csv だけ: {src.name}")
        if out.suffix.lower() != src.suffix.lower():
            raise RefError(f"--out の拡張子は元と同じにする: {out.name}")
        ops = _ops(a.ops, src.suffix.lower() == ".csv")
        if not ops:
            raise RefError("変えるものがない（--set / --clear などを付ける）")
        d = apply(src, out, ops)
    except RefError as e:
        print(f"中止: {e}")
        return 2
    except Exception as e:  # noqa: BLE001
        print(f"読めない・保存できない: {type(e).__name__}: {e}")
        return 2
    print("\n".join(excel_diff.summary(d) or ["変更なし"]))
    print(f"出力: {out}")
    return 0


def _enforce_cli(a) -> int:
    before, after = Path(a.before), Path(a.file)
    try:
        scope = load_scope(after, a.since)
        for s, cells in _allowed(a.allow, after).items():
            scope.setdefault(s, set()).update(cells)
        lines = enforce(before, after, scope)
        d = excel_diff.diff(before, after)
    except RefError as e:
        print(f"中止: {e}")
        return 2
    except Exception as e:  # noqa: BLE001
        print(f"読めない: {type(e).__name__}: {e}")
        return 2
    warn = [f"注意: {k}が {o} → {n} に減った（戻せない）" for k, (o, n) in d["objects"].items() if n < o]
    print("\n".join((lines or ["範囲外の変更なし"]) + warn))
    return 1 if lines else 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Excel / CSV を決まった操作だけで変えて、変えたセルを記録する")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("apply")
    p.add_argument("file")
    p.add_argument("--out")
    for flag in ("set", "clear", "insert-rows", "delete-rows", "insert-cols", "delete-cols", "json"):
        p.add_argument(f"--{flag}", action=_Op, const=flag.replace("-", "_"), dest="ops", default=[])
    e = sub.add_parser("enforce")
    e.add_argument("before")
    e.add_argument("file")
    e.add_argument("--allow", action="append", default=[])
    e.add_argument("--since", type=float, default=0)
    a = ap.parse_args(argv)
    return _apply_cli(a) if a.cmd == "apply" else _enforce_cli(a)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
