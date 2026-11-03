"""会議音声 → 文字起こし → フィラー（えー・あの・まあ 等）除去 → 整った本文を標準出力＋ファイルに出す。

使い方:
  python scripts/transcribe.py <音声ファイル> [--engine openai|whisper] [--model 名前] [--out <出力先.txt>]
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


def transcribe_openai(path: Path, model: str) -> str:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        sys.exit("ERROR: OPENAI_API_KEY がありません。画面左上の ⚙ で「文字起こし API キー」を保存してください。")
    try:
        from openai import OpenAI
    except ImportError:
        sys.exit("ERROR: openai パッケージがありません: pip install openai")
    client = OpenAI(api_key=key)
    texts = []
    for i, part in enumerate(split_audio(path), 1):
        with part.open("rb") as f:
            r = client.audio.transcriptions.create(model=model, file=f, language="ja", response_format="text")
        texts.append(r if isinstance(r, str) else getattr(r, "text", str(r)))
        print(f"[{i}] {part.name} 完了", file=sys.stderr)
    return "\n".join(t.strip() for t in texts)


def transcribe_whisper(path: Path, model: str) -> str:
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        sys.exit("ERROR: faster-whisper がありません: pip install faster-whisper（ローカル実行。初回はモデルを数GB落とします）")
    m = WhisperModel(model, device="auto", compute_type="auto")
    segments, _ = m.transcribe(str(path), language="ja", vad_filter=True)
    return "\n".join(s.text.strip() for s in segments)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--engine", choices=["openai", "whisper"], default=os.environ.get("SLINKY_ENGINE", "openai"))
    ap.add_argument("--model", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--clean-only", action="store_true")
    a = ap.parse_args()
    src = Path(a.src)
    if not src.exists():
        sys.exit(f"ERROR: ファイルがありません: {src}")

    if a.clean_only or src.suffix.lower() in TEXT_EXT:
        raw = src.read_text(encoding="utf-8", errors="replace")
    elif a.engine == "openai":
        raw = transcribe_openai(src, a.model or os.environ.get("SLINKY_OPENAI_MODEL", "gpt-4o-transcribe"))
    else:
        raw = transcribe_whisper(src, a.model or "large-v3")

    text = clean(raw)
    out = Path(a.out) if a.out else src.with_name(src.stem + "_文字起こし.txt")
    out.write_text(text, encoding="utf-8")
    print(f"OK: {len(raw)}文字 → 整形後 {len(text)}文字  → {out}", file=sys.stderr)
    print(text)


if __name__ == "__main__":
    main()
