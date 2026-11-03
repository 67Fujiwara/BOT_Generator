"""会議音声 → 文字起こし → フィラー（えー・あの・まあ 等）除去 → 整った本文を標準出力＋ファイルに出す。

使い方:
  python scripts/transcribe.py <音声ファイル> [--engine openai|whisper] [--model 名前] [--out <出力先.txt>] [--glossary "案件名,人名,用語"]
  python scripts/transcribe.py <既存の文字起こし.txt> --clean-only      文字起こし済みテキストの整形だけ
エンジン:
  openai  … OpenAI 音声認識 API（既定 gpt-4o-transcribe）。環境変数 OPENAI_API_KEY（画面 ⚙ の「文字起こし API キー」）が必要。
            25MB を超える音声は ffmpeg で 10 分ごとに分割して順に送る（ffmpeg が PATH にあること）。
  whisper … faster-whisper（ローカル、鍵不要、既定 large-v3）。pip install faster-whisper が必要。初回はモデルを落とす。
対応形式: mp3 m4a wav mp4 webm ogg flac（API の対応に準じる）
終了コード: 成功 0 / 失敗 2
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

MAX_BYTES = 24 * 1024 * 1024
TEXT_EXT = {".txt", ".md", ".srt", ".vtt"}

# 文頭・読点の直後・文中の独立したフィラーを除く。意味を持ちうる語（「その」「まあ」単独など）は句読点に挟まれた時だけ消す。
FILLERS_ANYWHERE = r"(?:えー+っ?と+ー?|えっ?と+ー?|えー+|あー+|あのー+|あのう|んー+|うーん+|えぇ+|ええと|ええっと|ま+ぁ+|んと|なんかー+)"
FILLERS_BOUNDED = r"(?:あの|その|まあ|なんか|はい|うん|ええ|そうですね|ですね|ね|さ)"
PUNCT = r"[、。,.，．!?！？…\s]"


def clean(text: str) -> str:
    t = text.replace("\r", "")
    t = re.sub(r"[ \t　]+", " ", t)
    # 独立したフィラー（前後が句読点か文頭文末）
    t = re.sub(rf"(^|{PUNCT}){FILLERS_ANYWHERE}(?={PUNCT}|$)", r"\1", t, flags=re.M)
    t = re.sub(rf"(^|{PUNCT}){FILLERS_BOUNDED}(?={PUNCT}|$)", r"\1", t, flags=re.M)
    # 文中に埋まった長いフィラー（えーっと 等）は語境界に関係なく消す
    t = re.sub(r"えー+っ?と+ー?|えっ?と+ー?|あのー+|えー+、?|あー+、?|んー+、?", "", t)
    # 同じ語の連続（言い直し）: 「その その」「確認 確認」
    t = re.sub(r"(\S{1,6})(?:[、 ]\1){1,}", r"\1", t)
    # 句読点の重複・空の文・余白
    t = re.sub(r"[、,]{2,}", "、", t)
    t = re.sub(r"(?:^|(?<=。))[、 ]+", "", t, flags=re.M)
    t = re.sub(r"[、 ]+(?=[。！？!?])", "", t)
    t = re.sub(r"。{2,}", "。", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return "\n".join(ln.strip() for ln in t.split("\n")).strip()


# ---- 区切り送信（ライブ録音）用: 用語プロンプト・幻聴判定 ----------------------------------
PROMPT_CHARS = 360   # Whisper 系の initial_prompt が効くのはおよそ 224 トークンまで
HALLUCINATION = [
    re.compile(p) for p in (
        r"ご視聴ありがとうございました", r"チャンネル登録", r"高評価", r"字幕(は|を)?", r"次回(の動画)?も", r"最後まで(ご覧|見て)",
        r"(?i)thanks? for watching", r"(?i)subtitles? by", r"(?i)like and subscribe", r"(?i)\b[a-z0-9-]+\.(com|org|net)\b",
        r"^[\s。、.,!?！？…♪♫-]*$",
    )
]


def build_prompt(glossary: str, prev: str) -> str:
    """用語（案件名・人名・社内用語）を先に、直前の区切りの末尾を後ろに。"""
    g = "、".join(t.strip() for t in re.split(r"[,、\n]", glossary or "") if t.strip())
    head = f"会議の文字起こし。用語: {g}。" if g else "会議の文字起こし。"
    tail = (prev or "").strip()[-max(0, PROMPT_CHARS - len(head)):]
    return (head + tail)[:PROMPT_CHARS]


def looks_hallucinated(text: str) -> bool:
    t = text.strip()
    if not t:
        return True
    if any(r.search(t) for r in HALLUCINATION):
        return True
    parts = [x.strip() for x in re.split(r"[。.!?！？\n]+", t) if x.strip()]
    return len(parts) >= 4 and len(set(parts)) == 1     # 「はい。はい。はい。はい。」


def low_confidence(segments: list[dict]) -> bool:
    if not segments:
        return False
    ns = sum(s.get("no_speech_prob", 0) or 0 for s in segments) / len(segments)
    lp = sum(s.get("avg_logprob", 0) or 0 for s in segments) / len(segments)
    return ns > 0.6 or lp < -1.0


def transcribe_chunk(path: Path, engine: str, model: str | None, prompt: str, start_sec: float = 0.0) -> dict:
    """1 区切りを文字にして {text, segments, dropped} を返す。segments の時刻は会議開始からの秒。"""
    if engine == "whisper":
        text, segs = _whisper(path, model or "large-v3", prompt)
    else:
        text, segs = _openai(path, model or os.environ.get("SLINKY_OPENAI_MODEL", "gpt-4o-transcribe"), prompt)
    text = text.strip()
    if not text or looks_hallucinated(text) or low_confidence(segs):
        return {"text": "", "segments": [], "dropped": text or "(empty)"}
    return {"text": text, "segments": [{"start": start_sec + s["start"], "end": start_sec + s["end"], "text": s["text"]}
                                       for s in segs if s.get("text", "").strip()] or
            [{"start": start_sec, "end": start_sec, "text": text}]}


def _openai(path: Path, model: str, prompt: str | None) -> tuple[str, list[dict]]:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY がありません。画面左上の ⚙ で「文字起こし API キー」を保存してください。")
    try:
        from openai import OpenAI
    except ImportError as e:
        raise RuntimeError("openai パッケージがありません: pip install openai") from e
    client = OpenAI(api_key=key)
    kw = {"model": model, "language": "ja", "response_format": "text"}
    if prompt:
        kw["prompt"] = prompt
    with path.open("rb") as f:
        r = client.audio.transcriptions.create(file=f, **kw)
    return (r if isinstance(r, str) else getattr(r, "text", str(r))), []


_WHISPER = {}


def _whisper(path: Path, model: str, prompt: str | None) -> tuple[str, list[dict]]:
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise RuntimeError("faster-whisper がありません: pip install faster-whisper（ローカル実行。初回はモデルを数GB落とします）") from e
    if model not in _WHISPER:
        _WHISPER[model] = WhisperModel(model, device="auto", compute_type="auto")
    segments, _ = _WHISPER[model].transcribe(str(path), language="ja", vad_filter=True, beam_size=5,
                                             initial_prompt=prompt or None, condition_on_previous_text=False)
    segs = [{"start": s.start, "end": s.end, "text": s.text.strip(), "no_speech_prob": s.no_speech_prob, "avg_logprob": s.avg_logprob}
            for s in segments]
    return "\n".join(s["text"] for s in segs), segs


def split_audio(path: Path) -> list[Path]:
    """25MB 超なら ffmpeg で 10 分ごとに分割。"""
    if path.stat().st_size <= MAX_BYTES:
        return [path]
    tmp = Path(tempfile.mkdtemp(prefix="slinky_"))
    out = tmp / f"part_%03d{path.suffix}"
    r = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(path), "-f", "segment", "-segment_time", "600",
                        "-c", "copy", str(out)], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("ERROR: 25MB を超える音声の分割に ffmpeg が必要です（winget install Gyan.FFmpeg）。\n" + r.stderr.strip())
    parts = sorted(tmp.glob(f"part_*{path.suffix}"))
    if not parts:
        sys.exit("ERROR: 分割結果がありません")
    return parts


def transcribe_file(path: Path, engine: str, model: str | None, glossary: str) -> str:
    """ファイル 1 本を文字にする。25MB 超は分割し、前の部分の末尾を次のプロンプトに渡す。"""
    texts, prev = [], ""
    parts = split_audio(path) if engine == "openai" else [path]
    for i, part in enumerate(parts, 1):
        try:
            fn = _whisper if engine == "whisper" else _openai
            text, _ = fn(part, model or ("large-v3" if engine == "whisper" else os.environ.get("SLINKY_OPENAI_MODEL", "gpt-4o-transcribe")),
                         build_prompt(glossary, prev))
        except RuntimeError as e:
            sys.exit(f"ERROR: {e}")
        texts.append(text.strip())
        prev = text[-300:]
        if len(parts) > 1:
            print(f"[{i}/{len(parts)}] {part.name} 完了", file=sys.stderr)
    return "\n".join(texts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--engine", choices=["openai", "whisper"], default=os.environ.get("SLINKY_ENGINE", "openai"))
    ap.add_argument("--model", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--clean-only", action="store_true")
    ap.add_argument("--glossary", default=os.environ.get("SLINKY_GLOSSARY", ""), help="案件名・人名・社内用語（カンマ区切り）")
    a = ap.parse_args()
    src = Path(a.src)
    if not src.exists():
        sys.exit(f"ERROR: ファイルがありません: {src}")

    if a.clean_only or src.suffix.lower() in TEXT_EXT:
        raw = src.read_text(encoding="utf-8", errors="replace")
    else:
        raw = transcribe_file(src, a.engine, a.model, a.glossary)

    text = clean(raw)
    out = Path(a.out) if a.out else src.with_name(src.stem + "_文字起こし.txt")
    out.write_text(text, encoding="utf-8")
    print(f"OK: {len(raw)}文字 → 整形後 {len(text)}文字  → {out}", file=sys.stderr)
    print(text)


if __name__ == "__main__":
    main()
