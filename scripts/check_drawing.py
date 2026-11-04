"""電気図面 PDF の検図（エイリアンが 1 回の Bash で呼ぶ）。
ページを画像にし、文字と座標を抜いて決定的なチェックをかけ、指摘ごとに赤枠付きの切り抜き画像と report.md を作る。

使い方:
  python scripts/check_drawing.py <図面.pdf> [--manual <マニュアル.pdf|フォルダ> ...] [--out <出力フォルダ>] [--dpi 110]
      --manual を付けると、先にマニュアル（PDF / txt / md）の本文をページ番号付きで出力する（検図の規定として読む用）。
      PDF は抽出した本文を同じ場所に <名前>.txt でキャッシュする（元 PDF より新しければ再利用）。
  python scripts/check_drawing.py crop <図面.pdf> <ページ番号> --text "KM1" [--out <フォルダ>] [--label "指摘"]
  python scripts/check_drawing.py crop <図面.pdf> <ページ番号> --region 0.6,0.7,0.9,0.95 [--out …]   （左上 x,y と右下 x,y をページ幅・高さの割合で）
出力（<図面名>_検図/）:
  page-01.png …          ページ全体の画像（エイリアンが目視する用）
  find-01.png …          指摘箇所の切り抜き（赤枠）
  report.md              指摘一覧（ページ / 種類 / 内容 / 画像）
  findings.json          同じ内容の機械可読版
チェック内容（決定的）:
  表記ゆれ   同じ符号が 全角/半角・ハイフン有無・大文字小文字・1とl・0とO の違いで複数の書き方をしている（例 KM1 / ＫＭ１ / KM-1）
  孤立符号   機器符号（KM1, X001, TB1-3 など）が図面全体で 1 箇所にしか出ない（片側の記入漏れ・誤記の疑い）
  孤立線番   3〜4 桁の線番が 1 箇所にしか出ない（線番は両端に書くのが普通）
  ページ欠け 「n/N」形式のページ番号に抜けがある
  図番不一致 タイトル欄（右下）にある図番らしい文字列がページごとに違う
  文字なし   テキストが取れないページ（スキャン画像）→ 目視のみ
終了コード: 指摘なし 0 / 指摘あり 1 / 失敗 2
"""
import argparse
import json
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

import pdfplumber

TAG_RE = re.compile(r"^(?:KM|MC|MS|CR|RY|R|X|Y|M|T|C|F|FU|CB|MCCB|ELB|ELCB|NFB|SW|COS|PB|PBL|LS|PS|PXS|TB|PLC|SS|BZ|PL|CP|TR|THR|OL|SOL|SV|FAN|INV|VFD|SVR|AMP|ENC|HMI|PT|CT|MCB|TH|FLS|LV|HT|CN|JB)-?\d{1,4}[A-Z]?(?:-\d{1,3})?$", re.I)
WIRE_RE = re.compile(r"^\d{3,4}$")
PAGE_RE = re.compile(r"^(\d{1,3})/(\d{1,3})$")
DWG_RE = re.compile(r"^[A-Z]{1,4}[-_]?\d{3,}(?:[-_][A-Z0-9]+)*$", re.I)


def die(msg: str):
    print(msg)
    sys.exit(2)


def norm_tag(s: str) -> str:
    """表記ゆれを吸収したキー（比較用）。"""
    t = unicodedata.normalize("NFKC", s).upper()
    t = re.sub(r"[\s\-‐‑–—_]", "", t)
    return t.replace("O", "0").replace("L", "1").replace("I", "1")


def render_pages(pdf, out: Path, dpi: int) -> list[Path]:
    paths = []
    for i, page in enumerate(pdf.pages, 1):
        p = out / f"page-{i:02d}.png"
        page.to_image(resolution=dpi).save(p)
        paths.append(p)
    return paths


def crop_word(pdf, page_no: int, bbox, out: Path, name: str, dpi: int = 160, pad: float = 90.0) -> Path:
    page = pdf.pages[page_no - 1]
    x0, y0, x1, y1 = bbox
    box = (max(0, x0 - pad), max(0, y0 - pad), min(page.width, x1 + pad), min(page.height, y1 + pad))
    im = page.crop(box).to_image(resolution=dpi)
    im.draw_rect((x0, y0, x1, y1), stroke="red", stroke_width=3)
    p = out / name
    im.save(p)
    return p


def analyze(pdf_path: Path, out: Path, dpi: int) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    findings = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        pages = render_pages(pdf, out, dpi)
        tags = defaultdict(list)      # norm → [(page, text, bbox)]
        wires = defaultdict(list)
        page_nums, dwg_nos, empty = {}, {}, []
        for pno, page in enumerate(pdf.pages, 1):
            words = page.extract_words(keep_blank_chars=False, use_text_flow=False)
            if not words:
                empty.append(pno)
                continue
            W, H = page.width, page.height
            for w in words:
                t, bb = w["text"].strip(), (w["x0"], w["top"], w["x1"], w["bottom"])
                tn = unicodedata.normalize("NFKC", t)
                m = PAGE_RE.match(tn)
                if m and w["top"] > H * 0.7:
                    page_nums[pno] = (int(m.group(1)), int(m.group(2)))
                if TAG_RE.match(tn):
                    tags[norm_tag(t)].append((pno, t, bb))
                elif WIRE_RE.match(tn):
                    wires[tn].append((pno, t, bb))
                if DWG_RE.match(tn) and w["x0"] > W * 0.55 and w["top"] > H * 0.75 and len(tn) >= 6:
                    dwg_nos.setdefault(pno, []).append(tn)

        def add(kind, pno, text, bbox, note, conf="中"):
            findings.append({"kind": kind, "page": pno, "text": text, "bbox": bbox, "note": note, "confidence": conf})

        # 表記ゆれ: 同じキーに複数の書き方
        for key, occ in tags.items():
            forms = sorted({t for _, t, _ in occ})
            if len(forms) > 1:
                for pno, t, bb in occ[:4]:
                    add("表記ゆれ", pno, t, bb, f"同じ符号が {' / '.join(forms)} と書かれている", "高")
        # 孤立符号 / 孤立線番
        for key, occ in tags.items():
            if len(occ) == 1:
                pno, t, bb = occ[0]
                add("孤立符号", pno, t, bb, "この符号は図面全体で 1 箇所だけ。片側の記入漏れか誤記の疑い", "中")
        for key, occ in wires.items():
            if len(occ) == 1:
                pno, t, bb = occ[0]
                add("孤立線番", pno, t, bb, "この線番は 1 箇所だけ（線番は両端に書く）。欠けか別の数字の疑い", "低")
        # ページ欠け
        if page_nums:
            total = max(n for _, n in page_nums.values())
            have = {n for n, _ in page_nums.values()}
            missing = [n for n in range(1, total + 1) if n not in have]
            if missing:
                add("ページ欠け", 0, "", None, f"ページ番号 {', '.join(map(str, missing))} が見当たらない（全 {total} ページ表記）", "高")
        # 図番不一致
        if len(dwg_nos) >= 2:
            sets = {p: set(v) for p, v in dwg_nos.items()}
            common = set.intersection(*sets.values())
            if not common:
                for p, v in list(sets.items())[:6]:
                    add("図番不一致", p, ", ".join(sorted(v)), None, "タイトル欄の図番らしい文字列がページごとに違う", "低")
        for pno in empty:
            add("文字なし", pno, "", None, "テキストが取れない（スキャン画像）。目視で確認", "高")

        # 切り抜き画像
        for i, f in enumerate(findings, 1):
            if f["bbox"]:
                f["image"] = crop_word(pdf, f["page"], f["bbox"], out, f"find-{i:02d}.png").name
            elif f["page"]:
                f["image"] = f"page-{f['page']:02d}.png"
            else:
                f["image"] = ""

    order = {"高": 0, "中": 1, "低": 2}
    findings.sort(key=lambda f: (order[f["confidence"]], f["page"]))
    rep = [f"# 検図レポート: {pdf_path.name}", "", f"- ページ数: {len(pages)}", f"- 機械チェックの指摘: {len(findings)} 件", ""]
    if not findings:
        rep.append("機械チェックでは指摘なし。")
    for i, f in enumerate(findings, 1):
        where = f"p.{f['page']}" if f["page"] else "全体"
        rep.append(f"## {i}. [{f['kind']}] {where} {f['text']}".rstrip())
        rep.append(f"- 内容: {f['note']}（確度: {f['confidence']}）")
        if f["image"]:
            rep.append(f"- 画像: {f['image']}")
        rep.append("")
    (out / "report.md").write_text("\n".join(rep), encoding="utf-8")
    (out / "findings.json").write_text(json.dumps(findings, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"pages": [p.name for p in pages], "findings": findings, "out": str(out)}


MANUAL_CHARS = 60000   # マニュアル本文の出力上限（超えた分は .txt を Read するよう案内）


def manual_text(path: Path) -> str:
    """マニュアルの本文（ページ番号付き）。PDF は .txt にキャッシュ。"""
    if path.suffix.lower() in (".txt", ".md"):
        return path.read_text(encoding="utf-8", errors="replace")
    cache = path.with_suffix(path.suffix + ".txt")
    if cache.exists() and cache.stat().st_mtime >= path.stat().st_mtime:
        return cache.read_text(encoding="utf-8", errors="replace")
    parts = []
    with pdfplumber.open(str(path)) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            t = (page.extract_text() or "").strip()
            parts.append(f"=== p.{i} ===\n{t if t else '（文字なし: 画像ページ）'}")
    text = "\n".join(parts)
    cache.write_text(text, encoding="utf-8")
    return text


def print_manuals(paths: list[str]):
    files = []
    for p in paths:
        pp = Path(p)
        if pp.is_dir():
            files += sorted(x for x in pp.iterdir() if x.suffix.lower() in (".pdf", ".txt", ".md") and not x.name.endswith(".pdf.txt"))
        elif pp.exists():
            files.append(pp)
        else:
            print(f"（マニュアルが見つかりません: {p}）")
    if not files:
        print("（マニュアルなし）")
        return
    budget = MANUAL_CHARS
    for f in files:
        try:
            text = manual_text(f)
        except Exception as e:  # noqa: BLE001
            print(f"## マニュアル: {f.name} → 読めません（{type(e).__name__}: {e}）")
            continue
        pages = text.count("=== p.") or 1
        print(f"## マニュアル: {f.name}（{pages} ページ、{len(text)} 文字）")
        if len(text) > budget:
            cache = f if f.suffix.lower() in (".txt", ".md") else f.with_suffix(f.suffix + ".txt")
            print(text[:budget] + f"\n…（以下省略。全文は {cache} を Read）")
            budget = 0
        else:
            print(text)
            budget -= len(text)
        print()


def main():
    argv = sys.argv[1:]
    if argv and argv[0] == "crop":
        ap = argparse.ArgumentParser(prog="check_drawing.py crop")
        ap.add_argument("pdf"); ap.add_argument("page", type=int)
        ap.add_argument("--text"); ap.add_argument("--region"); ap.add_argument("--out"); ap.add_argument("--label", default="")
        a = ap.parse_args(argv[1:])
        pdf_path = Path(a.pdf); out = Path(a.out) if a.out else pdf_path.with_name(pdf_path.stem + "_検図")
        out.mkdir(parents=True, exist_ok=True)
        with pdfplumber.open(str(pdf_path)) as pdf:
            page = pdf.pages[a.page - 1]
            if a.text:
                key = norm_tag(a.text)
                hit = [w for w in page.extract_words() if norm_tag(w["text"]) == key or unicodedata.normalize("NFKC", w["text"]) == unicodedata.normalize("NFKC", a.text)]
                if not hit:
                    die(f"ERROR: p.{a.page} に「{a.text}」が見つかりません")
                w = hit[0]; bbox = (w["x0"], w["top"], w["x1"], w["bottom"])
            elif a.region:
                fx0, fy0, fx1, fy1 = map(float, a.region.split(","))
                bbox = (fx0 * page.width, fy0 * page.height, fx1 * page.width, fy1 * page.height)
            else:
                die("ERROR: --text か --region を指定")
            n = len(list(out.glob("find-*.png"))) + 1
            p = crop_word(pdf, a.page, bbox, out, f"find-{n:02d}.png", pad=60 if a.region else 90)
        print(f"OK: {p}")
        return
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf"); ap.add_argument("--out"); ap.add_argument("--dpi", type=int, default=110)
    ap.add_argument("--manual", nargs="*", default=[], help="マニュアル PDF/txt/md かそのフォルダ。先に本文を出力する")
    a = ap.parse_args(argv)
    pdf_path = Path(a.pdf)
    if not pdf_path.exists():
        die(f"ERROR: ファイルがありません: {pdf_path}")
    if a.manual:
        print("# マニュアル（検図の規定として優先する）")
        print_manuals(a.manual)
        print("# 検図")
    out = Path(a.out) if a.out else pdf_path.with_name(pdf_path.stem + "_検図")
    try:
        r = analyze(pdf_path, out, a.dpi)
    except Exception as e:  # noqa: BLE001
        die(f"ERROR: 解析できません（PDF ではないか壊れています）: {type(e).__name__}: {e}")
    print((out / "report.md").read_text(encoding="utf-8"))
    print(f"ページ画像: {out}/page-01.png … page-{len(r['pages']):02d}.png")
    sys.exit(1 if r["findings"] else 0)


if __name__ == "__main__":
    main()
