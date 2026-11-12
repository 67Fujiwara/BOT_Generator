#!/usr/bin/env python3
"""トリクシー用: 代替品探しの決定的な部分。

  sites list | add <名前> <検索URL（{q} が型式）> [--note …] | remove <名前> | on <名前> | off <名前>
  spec  <PDF|URL|txt>...  [--out <txt>]          # データシートやページの本文を読む（PDF は日本語 CID 対応）
  price <型式> [--sites 名前,名前] [--json] [--dry-run]   # 登録サイトを並列で検索して価格を拾う（JSON-LD → 本文の近傍の円表記）
  quotes <型式>... [--score <xlsx>...]           # レックスの相見積（samples/rex/スコア）からその型式の商社価格を拾う
  score <候補.json> [--out <md>] [--xlsx <xlsx>] [--w-price 0.6 --w-spec 0.3 --w-avail 0.1]
                                                   # 最安値に一番重みを付けたスコアで並べ、第一候補/第二候補/不適合/罠 を表にする
  xref add <元型式> <候補型式> <採用|不採用> [--why …] | xref list [<元型式>]   # 学習（次回から即答）
保存先: app/parts/（sites.json / xref.json / cache/。git 対象外）。--store で差し替え可（テスト用）。
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STORE = Path(os.environ.get("BOT_PARTS_DIR") or ROOT / "app" / "parts")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
      "Accept-Language": "ja,en;q=0.8"}
DEFAULT_SITES = [
    {"name": "ファーネル", "url": "https://jp.element14.com/search?st={q}", "on": True, "note": "element14 Japan"},
    {"name": "MISUMI", "url": "https://jp.misumi-ec.com/vona2/result/?Keyword={q}", "on": True, "note": ""},
    {"name": "Amazon", "url": "https://www.amazon.co.jp/s?k={q}", "on": True, "note": "出品者が Amazon 以外のものは罠として印を付ける"},
    {"name": "メイカーズ", "url": "", "on": False, "note": "検索 URL を教えてください（{q} の所に型式が入る形）"},
    {"name": "モノタロウ", "url": "https://www.monotaro.com/s/?q={q}", "on": True, "note": ""},
    {"name": "RS", "url": "https://jp.rs-online.com/web/c/?searchTerm={q}", "on": True, "note": "RS Japan"},
    {"name": "Digi-Key", "url": "https://www.digikey.jp/ja/products/result?keywords={q}", "on": True, "note": ""},
    {"name": "Mouser", "url": "https://www.mouser.jp/c/?q={q}", "on": True, "note": ""},
]
TRAP_WORDS = [("生産終了", r"生産終了|廃番|廃止|discontinued|obsolete|販売終了"), ("海外発送", r"海外発送|海外から|輸入品|並行輸入|ships from"),
              ("最低注文数", r"最低注文|最小注文|MOQ|minimum order"), ("中古・訳あり", r"中古|訳あり|refurbished|used")]


def norm(s) -> str:
    s = unicodedata.normalize("NFKC", str(s or "")).upper()
    return re.sub(r"[\s\-_/\.()（）]", "", s)


# ---------- sites ----------
def _sites_path(store: Path | None) -> Path:
    return (store or STORE) / "sites.json"


def load_sites(store: Path | None = None) -> list[dict]:
    p = _sites_path(store)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    return [dict(x) for x in DEFAULT_SITES]


def save_sites(sites: list[dict], store: Path | None = None) -> None:
    p = _sites_path(store)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(sites, ensure_ascii=False, indent=1), encoding="utf-8")


def fmt_sites(sites: list[dict]) -> str:
    lines = []
    for s in sites:
        flag = "●" if s.get("on") and s.get("url") else "○"
        lines.append(f"{flag} {s['name']}: {s.get('url') or '（URL 未設定）'}" + (f"  — {s['note']}" if s.get("note") else ""))
    return "\n".join(lines) + "\n（● 検索する / ○ 検索しない）"


# ---------- spec (read documents) ----------
def _fetch(url: str, timeout: float = 12.0) -> str:
    import httpx
    r = httpx.get(url, headers=UA, timeout=timeout, follow_redirects=True)
    r.raise_for_status()
    return r.text


def _strip_html(h: str) -> str:
    h = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", h)
    h = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>|</h\d>", "\n", h)
    t = html.unescape(re.sub(r"<[^>]+>", " ", h))
    t = re.sub(r"[ \t　]+", " ", t)
    return re.sub(r"\n\s*\n+", "\n", t).strip()


def _jsonld(h: str) -> list[dict]:
    out = []
    for m in re.finditer(r"(?is)<script[^>]+application/ld\+json[^>]*>(.*?)</script>", h):
        try:
            d = json.loads(m.group(1).strip())
        except ValueError:
            continue
        out += d if isinstance(d, list) else [d]
    return out


def cmd_spec(paths: list[str], out: str | None) -> int:
    parts = []
    for s in paths:
        if re.match(r"^https?://", s):
            try:
                h = _fetch(s)
            except Exception as e:  # noqa: BLE001
                parts.append(f"=== {s}\n取れない: {type(e).__name__}: {str(e)[:120]}（WebFetch で読んでください）"); continue
            ld = [d for d in _jsonld(h) if isinstance(d, dict) and d.get("@type") in ("Product", "ProductModel")]
            txt = _strip_html(h)
            parts.append(f"=== {s}\n" + (("構造化データ: " + json.dumps(ld, ensure_ascii=False)[:3000] + "\n") if ld else "") + txt[:12000])
        else:
            p = Path(s)
            if not p.exists():
                parts.append(f"=== {s}\n見つからない"); continue
            if p.suffix.lower() == ".pdf":
                sys.path.insert(0, str(Path(__file__).resolve().parent))
                from pdftext import text_of
                parts.append(f"=== {s}\n" + text_of(p)[:40000])
            else:
                parts.append(f"=== {s}\n" + p.read_text(encoding="utf-8", errors="replace")[:40000])
    text = "\n\n".join(parts)
    if out:
        Path(out).write_text(text, encoding="utf-8"); print(f"書き出し: {out}（{len(text)} 文字）")
    else:
        print(text)
    return 0


# ---------- price ----------
PRICE_RE = re.compile(r"(?:¥|￥|JPY\s?)\s?([\d,]{2,9})(?:\.\d+)?|([\d,]{2,9})\s?円")


def _to_price(m: re.Match) -> float | None:
    s = (m.group(1) or m.group(2) or "").replace(",", "")
    try:
        v = float(s)
    except ValueError:
        return None
    return v if 1 <= v < 100_000_000 else None


def extract_hits(site: str, model: str, h: str, url: str) -> list[dict]:
    """ページから (型式に関係しそうな) 価格の候補を拾う。JSON-LD の Product/Offer を優先、無ければ本文の型式の近くの円表記。"""
    hits, key = [], norm(model)
    for d in _jsonld(h):
        items = []
        if isinstance(d, dict) and d.get("@type") in ("Product", "ProductModel"):
            items = [d]
        elif isinstance(d, dict) and d.get("@type") == "ItemList":
            items = [x.get("item", x) for x in d.get("itemListElement", []) if isinstance(x, dict)]
        for it in items:
            if not isinstance(it, dict):
                continue
            offers = it.get("offers") or {}
            offers = offers if isinstance(offers, list) else [offers]
            for of in offers:
                if not isinstance(of, dict):
                    continue
                price = of.get("price") or of.get("lowPrice")
                try:
                    price = float(str(price).replace(",", "")) if price is not None else None
                except ValueError:
                    price = None
                if price is None:
                    continue
                seller = of.get("seller", {}).get("name", "") if isinstance(of.get("seller"), dict) else str(of.get("seller") or "")
                hits.append({"site": site, "title": str(it.get("name", ""))[:120], "model_found": str(it.get("mpn") or it.get("sku") or it.get("model") or ""),
                             "price": price, "currency": of.get("priceCurrency", "JPY"), "url": it.get("url") or of.get("url") or url,
                             "stock": str(of.get("availability", "")).split("/")[-1], "seller": seller, "how": "json-ld"})
    if not hits:
        t = _strip_html(h)
        tn = norm(t)
        # 本文の中で型式が出る位置の前後 400 文字にある円表記を拾う（表示の順で最初の 5 件）
        for m in re.finditer(re.escape(key), tn):
            pass
        for m in re.finditer(re.escape(model), t, flags=re.I):
            seg = t[max(0, m.start() - 400): m.end() + 400]
            for pm in PRICE_RE.finditer(seg):
                v = _to_price(pm)
                if v:
                    hits.append({"site": site, "title": seg[max(0, pm.start() - 60): pm.start()].strip().replace("\n", " ")[-80:],
                                 "model_found": model, "price": v, "currency": "JPY", "url": url, "stock": "", "seller": "", "how": "text"})
            if len(hits) >= 5:
                break
    return hits[:8]


def add_traps(hits: list[dict], site_name: str, page_text: str) -> None:
    jp = [x["price"] for x in hits if x.get("currency", "JPY") == "JPY" and x.get("price")]
    med = sorted(jp)[len(jp) // 2] if jp else None
    for x in hits:
        traps = []
        if site_name == "Amazon" and x.get("seller") and "amazon" not in x["seller"].lower():
            traps.append("マーケットプレイス出品（Amazon 以外の出品者）")
        if med and x.get("price") and x["price"] < 0.6 * med:
            traps.append("相場より極端に安い（偽物・別物の疑い）")
        for label, pat in TRAP_WORDS:
            if re.search(pat, (x.get("title") or "") + " " + page_text[:20000], flags=re.I):
                traps.append(label)
        if x.get("currency", "JPY") != "JPY":
            traps.append(f"通貨が {x['currency']}（送料・関税別）")
        x["traps"] = sorted(set(traps))


def cmd_price(model: str, only: str | None, as_json: bool, dry: bool, store: Path | None) -> int:
    sites = [s for s in load_sites(store) if s.get("on") and s.get("url")]
    if only:
        names = {n.strip() for n in only.split(",")}
        sites = [s for s in sites if s["name"] in names]
    if not sites:
        print("検索するサイトがありません（⚙ の「部品」か parts.py sites で登録）"); return 2
    from urllib.parse import quote
    jobs = [(s["name"], s["url"].replace("{q}", quote(model))) for s in sites]
    if dry:
        for n, u in jobs:
            print(f"{n}: {u}")
        return 0
    cache = (store or STORE) / "cache"; cache.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")

    def one(job):
        n, u = job
        try:
            h = _fetch(u)
        except Exception as e:  # noqa: BLE001
            return {"site": n, "url": u, "error": f"{type(e).__name__}: {str(e)[:100]}", "hits": []}
        (cache / f"{n}_{norm(model)}.html").write_text(h, encoding="utf-8", errors="replace")
        hits = extract_hits(n, model, h, u)
        add_traps(hits, n, _strip_html(h))
        for x in hits:
            x["date"] = today
        return {"site": n, "url": u, "error": "", "hits": hits}

    with ThreadPoolExecutor(max_workers=6) as ex:
        results = list(ex.map(one, jobs))
    if as_json:
        print(json.dumps({"model": model, "date": today, "results": results}, ensure_ascii=False, indent=1)); return 0
    print(f"型式 {model} の価格（{today}）")
    for r in results:
        if r["error"]:
            print(f"- {r['site']}: 取れない（{r['error']}）→ 必要なら WebFetch で {r['url']} を読む"); continue
        if not r["hits"]:
            print(f"- {r['site']}: 該当なし（{r['url']}）"); continue
        for x in r["hits"]:
            tr = f"  ⚠ {' / '.join(x['traps'])}" if x.get("traps") else ""
            print(f"- {r['site']}: ¥{x['price']:,.0f}  {x['title'][:50]}  {x['stock']}  {x['url']}{tr}")
    return 0


# ---------- quotes (Rex's score sheets) ----------
def cmd_quotes(models: list[str], scores: list[str] | None) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from fill_prices import read_score
    files = [Path(s) for s in scores] if scores else sorted((ROOT / "samples" / "rex" / "スコア").glob("*.xls*"))
    if not files:
        print("相見積（スコア）のファイルがありません（samples/rex/スコア/ に置くか --score で指定）"); return 0
    found = 0
    for f in files:
        try:
            prices, vendors, _meta = read_score(str(f), None, {})
        except Exception as e:  # noqa: BLE001
            print(f"- {f.name}: 読めない（{type(e).__name__}: {str(e)[:80]}）"); continue
        for m in models:
            key = norm(m)
            row = {v: prices[v].get(key) for v in vendors if prices.get(v, {}).get(key) is not None}   # read_score は {商社: {型式: 単価}}
            if row:
                found += 1
                print(f"- {m}（{f.name}）: " + ", ".join(f"{re.sub(r'\s*単価$', '', v)} ¥{p:,.0f}" for v, p in row.items()))
    if not found:
        print("相見積に該当の型式はありません: " + ", ".join(models))
    return 0


# ---------- score ----------
def cmd_score(path: str, out: str | None, xlsx: str | None, wp: float, ws: float, wa: float) -> int:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    base, cands = d.get("base", {}), d.get("candidates", [])
    qty = float(base.get("qty") or 1)
    for c in cands:
        q = c.get("quote") or {}
        qmin = min([v for v in q.values() if isinstance(v, (int, float))], default=None)
        web = c.get("price")
        c["_price"] = min([p for p in (web, qmin) if p is not None], default=None)
        c["_price_from"] = "相見積" if (qmin is not None and (web is None or qmin <= web)) else ("Web" if web is not None else "")
    priced = [c["_price"] for c in cands if c.get("_price")]
    pmin = min(priced) if priced else None
    for c in cands:
        ps = (pmin / c["_price"]) if (pmin and c.get("_price")) else 0.0
        ss = max(0.0, 1 - 0.15 * len(c.get("diffs") or []))
        st = (c.get("stock") or "").strip()
        av = 1.0 if re.search(r"在庫あり|InStock|即納|\d+個", st) else (0.0 if re.search(r"在庫なし|OutOfStock|取り寄せ|納期", st) else 0.5)
        pen = 0.2 * len(c.get("traps") or [])
        c["_score"] = round(100 * (wp * ps + ws * ss + wa * av) - 100 * pen, 1) if c.get("must_ok") else None
    first = sorted([c for c in cands if c.get("must_ok") and not c.get("relaxed")], key=lambda c: -c["_score"])
    second = sorted([c for c in cands if c.get("must_ok") and c.get("relaxed")], key=lambda c: -c["_score"])
    bad = [c for c in cands if not c.get("must_ok")]
    bp = base.get("price")

    def row(c):
        price = f"¥{c['_price']:,.0f}（{c['_price_from']}）" if c.get("_price") else "不明"
        save = f"¥{(bp - c['_price']) * qty:,.0f}" if (bp and c.get("_price")) else "—"
        return (f"| {c.get('maker','')} | {c.get('model','')} | {price} | {save} | {c['_score']} | {c.get('stock','') or '—'} | "
                f"{'、'.join(c.get('diffs') or []) or 'なし'} | {'⚠ ' + '、'.join(c['traps']) if c.get('traps') else '—'} | {c.get('source','')} {c.get('date','')} |")
    head = "| メーカー | 型式 | 価格（出どころ） | 削減額×数量 | スコア | 在庫・納期 | 元品との違い | 安さの罠 | 出典・日付 |\n|---|---|---|---|---|---|---|---|---|"
    lines = [f"## 代替品: {base.get('model','')}（{base.get('maker','')}）", ""]
    lines.append(f"元品: {('¥%s' % format(bp, ',.0f')) if bp else '価格不明'} × {qty:g} 個。スコア = 100×（{wp}×最安値比 + {ws}×仕様一致 + {wa}×在庫）− 20×罠の数。最安値に一番重み。")
    lines += ["", "### 第一候補（同等スペック）", head] + ([row(c) for c in first] or ["| — | 該当なし | | | | | | | |"])
    lines += ["", "### 第二候補（スペックを少し前後させた）", head] + ([row(c) for c in second] or ["| — | 該当なし | | | | | | | |"])
    if bad:
        lines += ["", "### 不適合（必須条件を満たさない）", "| メーカー | 型式 | 理由 |", "|---|---|---|"] + [f"| {c.get('maker','')} | {c.get('model','')} | {'、'.join(c.get('diffs') or ['必須条件 NG'])} |" for c in bad]
    traps = [(c.get("model"), t) for c in cands for t in (c.get("traps") or [])]
    if traps:
        lines += ["", "### 安さの罠（要注意）"] + [f"- {m}: {t}" for m, t in traps]
    text = "\n".join(lines)
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True); Path(out).write_text(text, encoding="utf-8")
    if xlsx:
        import pandas as pd
        rows = [{"区分": k, "メーカー": c.get("maker", ""), "型式": c.get("model", ""), "価格": c.get("_price"), "価格の出どころ": c.get("_price_from", ""),
                 "削減額×数量": ((bp - c["_price"]) * qty if (bp and c.get("_price")) else None), "スコア": c.get("_score"), "在庫・納期": c.get("stock", ""),
                 "元品との違い": "、".join(c.get("diffs") or []), "安さの罠": "、".join(c.get("traps") or []), "出典": c.get("source", ""), "URL": c.get("url", ""), "日付": c.get("date", "")}
                for k, group in (("第一候補", first), ("第二候補", second), ("不適合", bad)) for c in group]
        Path(xlsx).parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows).to_excel(xlsx, index=False)
    print(text)
    if out or xlsx:
        print("\n保存: " + " / ".join(p for p in (out, xlsx) if p))
    return 0


# ---------- xref (learning) ----------
def _xref_path(store: Path | None) -> Path:
    return (store or STORE) / "xref.json"


def cmd_xref(args, store: Path | None) -> int:
    p = _xref_path(store)
    items = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    if args.xcmd == "add":
        items.append({"base": args.base, "alt": args.alt, "verdict": args.verdict, "why": args.why or "", "at": datetime.now().strftime("%Y-%m-%d")})
        p.parent.mkdir(parents=True, exist_ok=True); p.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"覚えた: {args.base} → {args.alt}（{args.verdict}{'：' + args.why if args.why else ''}）"); return 0
    shown = [x for x in items if not args.base or norm(x["base"]) == norm(args.base)]
    if not shown:
        print("学習した代替品はまだありません" if not args.base else f"{args.base} の学習はまだありません"); return 0
    for x in shown:
        print(f"- {x['at']} {x['base']} → {x['alt']}: {x['verdict']}{'（' + x['why'] + '）' if x['why'] else ''}")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="代替品探し（トリクシー）")
    ap.add_argument("--store", default=None, help="保存フォルダ（既定 app/parts）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sites"); ss = s.add_subparsers(dest="scmd", required=True)
    ss.add_parser("list")
    a = ss.add_parser("add"); a.add_argument("name"); a.add_argument("url"); a.add_argument("--note", default="")
    for n in ("remove", "on", "off"):
        ss.add_parser(n).add_argument("name")
    sp = sub.add_parser("spec"); sp.add_argument("paths", nargs="+"); sp.add_argument("--out", default=None)
    pr = sub.add_parser("price"); pr.add_argument("model"); pr.add_argument("--sites", default=None); pr.add_argument("--json", action="store_true"); pr.add_argument("--dry-run", action="store_true")
    q = sub.add_parser("quotes"); q.add_argument("models", nargs="+"); q.add_argument("--score", nargs="*", default=None)
    sc = sub.add_parser("score"); sc.add_argument("path"); sc.add_argument("--out", default=None); sc.add_argument("--xlsx", default=None)
    sc.add_argument("--w-price", type=float, default=0.6); sc.add_argument("--w-spec", type=float, default=0.3); sc.add_argument("--w-avail", type=float, default=0.1)
    x = sub.add_parser("xref"); xs = x.add_subparsers(dest="xcmd", required=True)
    xa = xs.add_parser("add"); xa.add_argument("base"); xa.add_argument("alt"); xa.add_argument("verdict", choices=["採用", "不採用"]); xa.add_argument("--why", default="")
    xl = xs.add_parser("list"); xl.add_argument("base", nargs="?", default=None)
    args = ap.parse_args(argv)
    store = Path(args.store) if args.store else None
    if args.cmd == "sites":
        sites = load_sites(store)
        if args.scmd == "list":
            print(fmt_sites(sites)); return 0
        if args.scmd == "add":
            if "{q}" not in args.url:
                print("URL に {q}（型式が入る所）を含めてください。例: https://example.com/search?q={q}"); return 2
            sites = [s for s in sites if s["name"] != args.name] + [{"name": args.name, "url": args.url, "on": True, "note": args.note}]
        else:
            hit = next((s for s in sites if s["name"] == args.name), None)
            if not hit:
                print(f"{args.name} は登録されていません"); return 3
            if args.scmd == "remove":
                sites = [s for s in sites if s["name"] != args.name]
            else:
                hit["on"] = args.scmd == "on"
        save_sites(sites, store); print(fmt_sites(sites)); return 0
    if args.cmd == "spec":
        return cmd_spec(args.paths, args.out)
    if args.cmd == "price":
        return cmd_price(args.model, args.sites, args.json, args.dry_run, store)
    if args.cmd == "quotes":
        return cmd_quotes(args.models, args.score)
    if args.cmd == "score":
        return cmd_score(args.path, args.out, args.xlsx, args.w_price, args.w_spec, args.w_avail)
    return cmd_xref(args, store)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
