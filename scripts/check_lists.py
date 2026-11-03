"""2つのリストを一発で突き合わせる（抽出×2 → 比較 → レポート表示）。

ポテトはこれを1回の Bash で呼ぶ。個別に列指定が要るときだけ extract_items.py / compare_lists.py を使う。

使い方:
  python scripts/check_lists.py <基準ファイル> <対象ファイル> [--report report.md]
          [--cols-base 型式=列名 数量=列名 ...] [--cols-target 型式=列名 ...]
終了コード: 差分なし 0 / 差分あり 1 / 抽出失敗 2
"""
import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, *args], capture_output=True, text=True, encoding="utf-8")


def extract(src: str, cols: list[str]) -> str:
    dst = str(Path(src).with_suffix("")) + ".normalized.csv"
    cmd = [str(HERE / "extract_items.py"), src, dst]
    if cols:
        cmd += ["--cols", *cols]
    r = run(cmd)
    if r.returncode != 0:
        err = [ln for ln in (r.stdout + r.stderr).splitlines() if ln.strip()]
        print(f"抽出失敗: {src}\n{err[-1] if err else ''}".rstrip())
        print("→ 列名を確認: python scripts/extract_items.py", src, "--show-columns")
        sys.exit(2)
    print(r.stdout.strip())
    return dst


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("target")
    ap.add_argument("--report", default="report.md")
    ap.add_argument("--cols-base", nargs="*", default=[])
    ap.add_argument("--cols-target", nargs="*", default=[])
    a = ap.parse_args()

    b = extract(a.base, a.cols_base)
    t = extract(a.target, a.cols_target)
    r = run([str(HERE / "compare_lists.py"), b, t, "--report", a.report])
    print(r.stdout.rstrip())
    if r.stderr.strip():
        print(r.stderr.rstrip(), file=sys.stderr)
    sys.exit(r.returncode)


if __name__ == "__main__":
    main()
