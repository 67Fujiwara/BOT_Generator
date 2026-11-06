#!/usr/bin/env python3
"""ボット間の連絡タグ。返事の中の [[頼む: 相手: 内容]] / [[聞く: 相手: 内容]] を拾って本文から取り除く。

- 頼む: 相手に仕事を渡す。相手の返事は同じチャットに出る。送り手は待たない。
- 聞く: 相手に質問して返事を待ち、その返事を使って送り手が回答を続ける。

使い方: python scripts/relay.py parse "<返事の文>"  → JSON {"text": 残り本文, "relays": [{"kind","to","text"}]}
"""
from __future__ import annotations

import json
import re
import sys

TAG_RE = re.compile(r"\[\[\s*(頼む|聞く)\s*[:：]\s*([^:：\]\n]+?)\s*[:：]\s*(.+?)\s*\]\]", re.S)


def parse(text: str) -> tuple[str, list[dict]]:
    relays: list[dict] = []

    def _take(m: re.Match) -> str:
        relays.append({"kind": m.group(1), "to": m.group(2).strip().strip("「」\"' "), "text": m.group(3).strip()})
        return ""

    cleaned = TAG_RE.sub(_take, text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, relays


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[0] != "parse":
        print(__doc__)
        return 2
    cleaned, relays = parse(" ".join(argv[1:]))
    print(json.dumps({"text": cleaned, "relays": relays}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
