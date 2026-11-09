#!/usr/bin/env python3
"""PDF の文字を日本語 CID フォント（Adobe-Japan1）対応で取り出す（全ボット共通）。

日本語の CAD / 見積 / マニュアル PDF は、文字が Adobe-Japan1 の CID 番号で入っていて ToUnicode が無いことが多く、
そのままだと「(cid:1234)」や空文字になる。ここでは 3 段構えで取る:
  1. pdfplumber（pdfminer）で取り出す
  2. 「(cid:N)」が残っていれば Adobe-Japan1 の CID→Unicode 表（pdfminer 同梱）で文字に直す
  3. それでも空なら pypdfium2（PDFium。独自の CID 表を持つ）で取り直す
全部空なら「文字なし（画像か、文字がアウトライン化されている）」として返す。

  python scripts/pdftext.py <PDF> [--pages 1,3-5] [--out <txt>] [--json]
ほかのスクリプトからは page_texts(path) / fix_cid(text) を使う。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

CID_RE = re.compile(r"\(cid:(\d+)\)")
_JP_RE = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff\uff01-\uff9f]")
_umap_h = None


def _unicode_map():
    """Adobe-Japan1 の CID → Unicode（横書き）。pdfminer 同梱の表。"""
    global _umap_h
    if _umap_h is None:
        try:
            from pdfminer.cmapdb import CMapDB
            _umap_h = CMapDB.get_unicode_map("Adobe-Japan1", False)
        except Exception:  # noqa: BLE001
            _umap_h = False
    return _umap_h


def fix_cid(text: str) -> str:
    """「(cid:1234)」を Adobe-Japan1 の文字に直す。直せないものは「〓」にする。"""
    if not text or "(cid:" not in text:
        return text
    um = _unicode_map()

    def _one(m):
        cid = int(m.group(1))
        if um:
            try:
                return um.get_unichr(cid)
            except Exception:  # noqa: BLE001
                pass
        return "〓"
    return CID_RE.sub(_one, text)


def _good(text: str) -> bool:
    t = (text or "").strip()
    return bool(t) and "(cid:" not in t and t.count("〓") <= max(2, len(t) // 10)


def _pdfium_texts(path: Path, pages: list[int]) -> dict[int, str]:
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return {}
    out = {}
    try:
        doc = pdfium.PdfDocument(str(path))
        for i in pages:
            try:
                tp = doc[i - 1].get_textpage()
                out[i] = tp.get_text_range() or ""
            except Exception:  # noqa: BLE001
                out[i] = ""
    except Exception:  # noqa: BLE001
        return {}
    return out


def page_texts(path: Path, pages: list[int] | None = None) -> list[dict]:
    """[{page, text, engine}] を返す。engine は pdfminer / adobe-japan1 / pdfium / none。"""
    import pdfplumber
    path = Path(path)
    out = []
    with pdfplumber.open(str(path)) as pdf:
        wanted = pages or list(range(1, len(pdf.pages) + 1))
        wanted = [p for p in wanted if 1 <= p <= len(pdf.pages)]
        need_pdfium = []
        for i in wanted:
            t = (pdf.pages[i - 1].extract_text() or "").strip()
            engine = "pdfminer"
            if "(cid:" in t:
                t2 = fix_cid(t)
                if _good(t2):
                    t, engine = t2, "adobe-japan1"
            if _good(t):
                out.append({"page": i, "text": t, "engine": engine})
            else:
                out.append({"page": i, "text": t, "engine": engine})
                need_pdfium.append(i)
    if need_pdfium:
        alt = _pdfium_texts(path, need_pdfium)
        for rec in out:
            if rec["page"] in alt and _good(alt[rec["page"]]):
                rec["text"], rec["engine"] = alt[rec["page"]].strip(), "pdfium"
            elif rec["page"] in need_pdfium:
                if not rec["text"].strip():
                    rec["engine"] = "none"
    return out


def text_of(path: Path, pages: list[int] | None = None, with_headers: bool = True) -> str:
    parts = []
    for r in page_texts(path, pages):
        body = r["text"] if r["text"].strip() else "（文字なし: 画像ページか、文字がアウトライン化されている）"
        parts.append(f"=== p.{r['page']} ===\n{body}" if with_headers else body)
    return "\n".join(parts)


def parse_pages(spec: str | None) -> list[int] | None:
    if not spec:
        return None
    got = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            got += list(range(int(a), int(b) + 1))
        elif part:
            got.append(int(part))
    return got


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="PDF の文字を取り出す（日本語 CID フォント対応）")
    ap.add_argument("pdf")
    ap.add_argument("--pages", default=None)
    ap.add_argument("--out", default=None, help="テキストを書き出すファイル")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    p = Path(a.pdf)
    if not p.exists() or p.suffix.lower() != ".pdf":
        print(f"PDF が見つからない: {a.pdf}"); return 2
    recs = page_texts(p, parse_pages(a.pages))
    if a.json:
        print(json.dumps(recs, ensure_ascii=False, indent=1)); return 0
    text = "\n".join(f"=== p.{r['page']} ({r['engine']}) ===\n" + (r["text"] or "（文字なし: 画像ページか、文字がアウトライン化されている）") for r in recs)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
        print(f"書き出し: {a.out}（{len(recs)} ページ）")
    else:
        print(text)
    empties = [r["page"] for r in recs if not r["text"].strip()]
    if empties:
        print(f"\n文字が取れないページ: {', '.join(map(str, empties))}（スキャン画像なら show_image.py pages で画像にして目視）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
