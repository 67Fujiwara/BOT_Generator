// 議事録用の文字起こしルート（Whisper large-v3-turbo on Workers AI、PC 向け）。
// SYNAPSE の /api/transcribe を「長い会議を 45 秒ずつ送る」前提に書き直したもの。
// SYNAPSE の worker/minutes.ts として置き、worker/index.ts で app.route('/', minutes) する。認証は index.ts の /api/* ミドルウェア（APP_TOKEN）がそのまま効く。
//
// 1 リクエスト = 会議の 1 区切り（目安 30〜60 秒の webm/opus、数百 KB）。
// クライアント (client-recorder.ts) が区切りごとに順番に送り、前の区切りの末尾を `prev` に、
// 案件名・人名・社内用語を `glossary` に入れる。Whisper はこの 2 つで固有名詞と文の切れ目が安定する。

import { Hono } from 'hono';
import type { Env } from './env';

export interface TranscribeChunkResult {
  seq: number;
  /** 会議開始からの秒（区切りの先頭） */
  startSec: number;
  text: string;
  /** 区切り内の文ごとの時刻（会議開始からの秒に直してある） */
  segments: { start: number; end: number; text: string }[];
  /** 幻聴や無音として捨てた場合に元の文を入れる（デバッグ用） */
  dropped?: string;
}

const MAX_BYTES = 25 * 1024 * 1024;
/** Whisper の initial_prompt はおよそ 224 トークンまでしか効かない。用語 + 直前の末尾でこの程度に収める。 */
const PROMPT_CHARS = 360;

/** 無音・BGM だけの区切りで Whisper が作る決まり文句。日本語と英語。 */
const HALLUCINATION = [
  /ご視聴ありがとうございました/, /チャンネル登録/, /高評価/, /字幕(は|を)?/, /次回(の動画)?も/, /最後まで(ご覧|見て)/,
  /thanks? for watching/i, /subtitles? by/i, /like and subscribe/i, /\b[a-z0-9-]+\.(com|org|net)\b/i,
  /^[\s。、.,!?！？…♪♫-]*$/,
];

function looksHallucinated(text: string): boolean {
  const t = text.trim();
  if (!t) return true;
  if (HALLUCINATION.some((re) => re.test(t))) return true;
  // 同じ短い句の繰り返し（「はい。はい。はい。はい。」）
  const parts = t.split(/[。.!?！？\n]+/).map((s) => s.trim()).filter(Boolean);
  if (parts.length >= 4 && new Set(parts).size === 1) return true;
  return false;
}

function lowConfidence(segments: { no_speech_prob?: number; avg_logprob?: number }[] | undefined): boolean {
  if (!segments || segments.length === 0) return false;
  const noSpeech = segments.reduce((a, s) => a + (s.no_speech_prob ?? 0), 0) / segments.length;
  const logprob = segments.reduce((a, s) => a + (s.avg_logprob ?? 0), 0) / segments.length;
  return noSpeech > 0.6 || logprob < -1.0;
}

function buildPrompt(glossary: string, prev: string): string {
  // 用語を先に（切り詰められるのは末尾）、その後に直前の文の末尾。
  const g = glossary.split(/[,、\n]/).map((s) => s.trim()).filter(Boolean).join('、');
  const head = g ? `会議の文字起こし。用語: ${g}。` : '会議の文字起こし。';
  const tail = prev.trim().slice(-Math.max(0, PROMPT_CHARS - head.length));
  return (head + tail).slice(0, PROMPT_CHARS);
}

export const minutes = new Hono<{ Bindings: Env }>();

minutes.post('/api/minutes/transcribe', async (c) => {
  if (!c.env.AI) return c.json({ error: 'Workers AI binding "AI" is not configured' }, 501);
  const form = await c.req.formData();
  const audio = form.get('audio');
  if (!(audio instanceof File) || audio.size === 0) return c.json({ error: 'audio file required' }, 400);
  if (audio.size > MAX_BYTES) return c.json({ error: `audio too large (max ${MAX_BYTES / 1024 / 1024}MB); send shorter chunks` }, 413);

  const seq = Number(form.get('seq') ?? 0) || 0;
  const startSec = Number(form.get('startSec') ?? 0) || 0;
  const language = String(form.get('language') ?? 'ja').slice(0, 5) || 'ja';
  const glossary = String(form.get('glossary') ?? '').slice(0, 2000);
  const prev = String(form.get('prev') ?? '').slice(-2000);

  // base64 化。一気に展開すると引数の上限で落ちるので 32KB ずつ。
  const bytes = new Uint8Array(await audio.arrayBuffer());
  let bin = '';
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));

  const out = (await c.env.AI.run('@cf/openai/whisper-large-v3-turbo', {
    audio: btoa(bin),
    task: 'transcribe',
    language,
    vad_filter: true,
    initial_prompt: buildPrompt(glossary, prev),
    beam_size: 5,
    // 区切りの中で前の文に引きずられて同じ句を繰り返す事故を防ぐ。文脈は initial_prompt で渡している。
    condition_on_previous_text: false,
  })) as { text?: string; segments?: { start?: number; end?: number; text?: string; no_speech_prob?: number; avg_logprob?: number }[] };

  const text = (out.text ?? '').trim();
  const base: TranscribeChunkResult = { seq, startSec, text: '', segments: [] };
  if (!text || looksHallucinated(text) || lowConfidence(out.segments)) return c.json({ ...base, dropped: text || '(empty)' });

  return c.json({
    ...base,
    text,
    segments: (out.segments ?? [])
      .filter((s) => (s.text ?? '').trim())
      .map((s) => ({ start: startSec + (s.start ?? 0), end: startSec + (s.end ?? 0), text: (s.text ?? '').trim() })),
  } satisfies TranscribeChunkResult);
});

/*
組み込み:
  import { minutes } from './minutes/worker-transcribe';
  app.route('/', minutes);

wrangler.toml:
  [ai]
  binding = "AI"

D1 に置くなら:
  CREATE TABLE meetings (id TEXT PRIMARY KEY, title TEXT, started_at TEXT NOT NULL, glossary TEXT);
  CREATE TABLE transcript_chunks (
    meeting_id TEXT NOT NULL, seq INTEGER NOT NULL, start_sec REAL NOT NULL,
    text TEXT NOT NULL, segments TEXT NOT NULL, PRIMARY KEY (meeting_id, seq));
*/
