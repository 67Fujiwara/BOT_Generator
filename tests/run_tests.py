"""全ボットのスクリプトを検証する（バズが1回の Bash で呼ぶ）。
tests/cases.json の各ケースを実行し、終了コードと標準出力の含む/含まない文字列を確かめる。

使い方:
  python tests/run_tests.py            全ケース
  python tests/run_tests.py --case rex  名前に rex を含むケースだけ
  python tests/run_tests.py -v          失敗ケースの標準出力も表示
終了コード: 全部 PASS 0 / FAIL あり 1
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default="")
    ap.add_argument("-v", action="store_true")
    a = ap.parse_args()

    r = subprocess.run([sys.executable, str(HERE / "make_fixtures.py")], capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        sys.exit("検証用ファイルの生成に失敗:\n" + (r.stderr or r.stdout).strip().splitlines()[-1])
    fx = str((HERE / "fixtures").relative_to(ROOT)).replace("\\", "/")
    cases = [c for c in json.loads((HERE / "cases.json").read_text(encoding="utf-8")) if a.case in c["name"]]
    if not cases:
        sys.exit(f"ケースがありません: --case {a.case}")

    fails = 0
    for c in cases:
        cmd = c["cmd"].replace("{fx}", fx).split()
        if cmd[0] == "python":
            cmd[0] = sys.executable
        t0 = time.time()
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        out = r.stdout + r.stderr
        problems = []
        if "exit" in c and r.returncode != c["exit"]:
            problems.append(f"終了コード {r.returncode}（期待 {c['exit']}）")
        problems += [f"含まれない: {s!r}" for s in c.get("contains", []) if s not in out]
        problems += [f"含まれてはいけない: {s!r}" for s in c.get("not_contains", []) if s in out]
        status = "PASS" if not problems else "FAIL"
        fails += bool(problems)
        print(f"{status}  {c['name']}  ({time.time() - t0:.1f}s)")
        for p in problems:
            print(f"      - {p}")
        if problems and a.v:
            print("      --- 出力 ---\n" + "\n".join("      " + ln for ln in out.strip().splitlines()[-25:]))
    print(f"\n結果: {len(cases) - fails} PASS / {fails} FAIL / 全 {len(cases)} ケース")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
