# SYNAPSE に議事録用の文字起こしルートを足す手順

bot-team のスリンキー（ライブ録音）は、外部サーバーモードで `POST /api/minutes/transcribe` に区切りを送る。
SYNAPSE（67Fujiwara/SYNAPSE）には 2026-11-03 時点でこのルートが無く、既存の `/api/transcribe` は英語専用なので、以下を一度だけ行う。

## 1. ファイルを置く
`docs/synapse/minutes.ts` を SYNAPSE の `worker/minutes.ts` にコピーする。

## 2. worker/index.ts に 2 行足す
先頭の import 群に:
```ts
import { minutes } from './minutes';
```
`const app = new Hono<{ Bindings: Env }>();` と認証ミドルウェア（`app.use('/api/*', ...)`）の**後ろ**に:
```ts
app.route('/', minutes);
```
認証ミドルウェアより後ろに置くことで、このルートにも APP_TOKEN の Bearer 認証が効く。

## 3. デプロイ
SYNAPSE のフォルダで:
```bash
npm run deploy
```
（`wrangler.toml` には `[ai] binding = "AI"` が既にあるので追加設定は不要）

## 4. 確認
```bash
curl -X POST https://synapse-api.yuutafujiwara67.workers.dev/api/minutes/transcribe -H "Authorization: Bearer <APP_TOKEN>" -F seq=0
```
`{"error":"audio file required"}` が返れば OK（ルートがあり、認証も通っている）。
`{"error":"unauthorized"}` ならトークン違い。404 なら手順 2 の route が効いていない。

## 5. bot-team 側の設定（⚙ → ライブ録音・文字起こしの設定）
- エンジン: 外部サーバー
- URL: `https://synapse-api.yuutafujiwara67.workers.dev/api/minutes/transcribe`
- トークン: SYNAPSE の `APP_TOKEN`（SYNAPSE アプリの Settings → App token と同じ値。忘れたら Cloudflare ダッシュボード → Workers & Pages → synapse-api → 設定 → 変数とシークレット で付け直し、SYNAPSE アプリ側の設定も更新）
- 用語: 案件名・人名・製品名をカンマ区切り

## 送受信の形（参考）
送る: multipart `audio`(webm) / `seq` / `startSec` / `language`(ja) / `glossary` / `prev`
返る: `{ seq, startSec, text, segments:[{start,end,text}], dropped? }`
