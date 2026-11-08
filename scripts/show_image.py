#!/usr/bin/env python3
"""チャットに画像を出すための道具（全ボット共通）。画面は Markdown の ![説明](/files/<ROOTからの相対パス>) を画像として表示する。

  python scripts/show_image.py show <画像ファイル>...                       # そのまま表示用の行を出す
  python scripts/show_image.py pages <PDF> [--pages 1,3-5] [--dpi 110]      # PDF のページを画像にして表示用の行を出す
  python scripts/show_image.py crop <画像|PDF> --region x0,y0,x1,y1 [--page N] [--label 名前]
                                                                             # 一部を切り抜く（割合 0〜1。左上と右下）
出力はすべて <元ファイル名>_画像/ に入る。inbox/ と samples/ の外にあるファイルは inbox/画像_<日時>/ にコピーしてから配信する。
標準出力に出た ![…](/files/…) の行をそのまま返事に貼ればよい。
"""
from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVED = (ROOT / "inbox", ROOT / "samples")
NO_COPY = False   # --here: 配信できない場所でもコピーしない（テスト用）
IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


def url(p: Path) -> str:
    """ROOT からの相対パスを /files/… に。配信できない場所なら inbox にコピーする。"""
    p = p.resolve()
    if not NO_COPY and not any(str(p).startswith(str(s.resolve()) + "/") or str(p).startswith(str(s.resolve()) + "\\") for s in SERVED):
        dst_dir = ROOT / "inbox" / ("画像_" + datetime.now().strftime("%Y%m%d-%H%M%S"))
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / p.name
        shutil.copy2(p, dst)
        p = dst
    try:
        rel = p.relative_to(ROOT).as_posix()
    except ValueError:
        rel = p.name
    return "/files/" + rel


def md(p: Path, label: str) -> str:
    return f"![{label}]({url(p)})"


def out_dir(src: Path) -> Path:
    d = src.with_name(src.stem + "_画像")
    d.mkdir(parents=True, exist_ok=True)
    return d


def parse_pages(spec: str | None, n: int) -> list[int]:
    if not spec:
        return list(range(1, n + 1))
    got: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            got += list(range(int(a), int(b) + 1))
        elif part:
            got.append(int(part))
    return [p for p in got if 1 <= p <= n]


def cmd_show(paths: list[str]) -> int:
    rc = 0
    for s in paths:
        p = Path(s)
        if not p.exists():
            print(f"見つからない: {s}"); rc = 2; continue
        if p.suffix.lower() == ".pdf":
            print(f"{p.name} は PDF です。pages で画像にしてください: python scripts/show_image.py pages \"{s}\""); rc = 2; continue
        if p.suffix.lower() not in IMG_EXT:
            try:
                from PIL import Image
                dst = out_dir(p) / (p.stem + ".png")
                Image.open(p).save(dst)
                p = dst
            except Exception as e:  # noqa: BLE001
                print(f"画像として開けない: {s}（{e}）"); rc = 2; continue
        print(md(p, p.name))
    return rc


def cmd_pages(pdf: str, pages: str | None, dpi: int) -> int:
    import pdfplumber
    src = Path(pdf)
    if not src.exists() or src.suffix.lower() != ".pdf":
        print(f"PDF が見つからない: {pdf}"); return 2
    d = out_dir(src)
    with pdfplumber.open(str(src)) as doc:
        wanted = parse_pages(pages, len(doc.pages))
        if not wanted:
            print(f"ページ指定が範囲外です（全 {len(doc.pages)} ページ）"); return 2
        for i in wanted:
            p = d / f"page-{i:02d}.png"
            if not p.exists():
                doc.pages[i - 1].to_image(resolution=dpi).save(str(p))
            print(md(p, f"{src.name} p.{i}"))
    return 0


def cmd_crop(target: str, region: str, page: int, label: str | None, dpi: int) -> int:
    from PIL import Image
    src = Path(target)
    if not src.exists():
        print(f"見つからない: {target}"); return 2
    try:
        x0, y0, x1, y1 = [min(1.0, max(0.0, float(v))) for v in region.split(",")]
    except ValueError:
        print("--region は x0,y0,x1,y1（0〜1 の割合、左上と右下）で指定してください"); return 2
    if x1 <= x0 or y1 <= y0:
        print("--region の右下が左上より大きくなるようにしてください"); return 2
    d = out_dir(src)
    if src.suffix.lower() == ".pdf":
        import pdfplumber
        with pdfplumber.open(str(src)) as doc:
            if not 1 <= page <= len(doc.pages):
                print(f"ページ {page} はありません（全 {len(doc.pages)} ページ）"); return 2
            pg = doc.pages[page - 1]
            box = (pg.width * x0, pg.height * y0, pg.width * x1, pg.height * y1)
            im = pg.crop(box).to_image(resolution=dpi).original
        base = f"p{page}"
    else:
        im0 = Image.open(src)
        w, h = im0.size
        im = im0.crop((int(w * x0), int(h * y0), int(w * x1), int(h * y1)))
        base = src.stem
    n = len(list(d.glob("crop-*.png"))) + 1
    name = (label or f"{base} 切り抜き{n}")
    p = d / f"crop-{n:02d}.png"
    im.save(str(p))
    print(md(p, name))
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="チャットに画像を出す")
    ap.add_argument("--here", action="store_true", help="inbox にコピーしない（テスト用）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("show"); s.add_argument("paths", nargs="+")
    pg = sub.add_parser("pages"); pg.add_argument("pdf"); pg.add_argument("--pages", default=None); pg.add_argument("--dpi", type=int, default=110)
    c = sub.add_parser("crop"); c.add_argument("target"); c.add_argument("--region", required=True); c.add_argument("--page", type=int, default=1)
    c.add_argument("--label", default=None); c.add_argument("--dpi", type=int, default=160)
    a = ap.parse_args(argv)
    global NO_COPY
    NO_COPY = a.here
    if a.cmd == "show":
        return cmd_show(a.paths)
    if a.cmd == "pages":
        return cmd_pages(a.pdf, a.pages, a.dpi)
    return cmd_crop(a.target, a.region, a.page, a.label, a.dpi)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
