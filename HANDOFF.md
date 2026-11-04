# bot-team 引き継ぎメモ（2026-11-03）

Claude Code でこのリポジトリを開いたら、まずこのファイルを読むこと。

## 目的

ローレン・タン（SpaceXAI、Grok Bot 開発者、X: @poteto）の「役割ごとにボットを分け、管理役の下にぶら下げ、検証をボット側に組み込んで自分はレビューに専念する」運用を Claude Code で再現する。
参考: Peter Yang のポッドキャスト「We Built Grok Bot. Here Are Our 14 Best Bots」（YouTube: https://youtu.be/xZ5TEaleUdg、要点は 14:16 と 38:59）。

本人のボット例: Matcha（エンジニアリード。分解だけして自分では作業しない）、エンジニアボット群（マージ前に自分でテスト）、Dr. Eggbot（ボットを設計・監査するボット）、出張予約をするアシスタント。

## 方針（決定済み）

- Grok Bot ではなく **Claude Code で自作**する。
- ボット1体 = `.claude/agents/<name>.md` 1枚（＋必要ならスクリプト）。入口（UI）に依存させない。
- 数える・突き合わせる・変換するは **Python スクリプトで決定的に**やり、LLM は列の判定・結果の要約・曖昧箇所の指摘だけ。
- 入口は **自作のローカルチャット画面**（Claude Code デスクトップではなく、最初から自作で進める判断済み）。
- 重視する条件: 拡張性 / 精度 / 応答速度 / 操作性 / 自分仕様に改造しやすい / ボット待ちの間に他作業（CAD・PLC）が重くならない。
- 文章系は Haiku、判断系は Sonnet など、ボットごとにモデルを変える。ツールは最小限。
- ボットを増やすときは「スクリプトで決定的にできる部分はどこか」を最初に切り分ける。
- **速度は全ボットの設計条件**（2026-11-03 決定）。ツール往復の最小化（決定的処理は1スクリプト・1 Bash）、役割に足りる最小モデル、ツール不要なら `tools: []`、追加時に実測を HANDOFF に記録。

## 今あるボット

| 呼び名 | id | 役割 | ツール | グループ |
|---|---|---|---|---|
| ポテト | potato | 2つのリスト（BOM / 購買リスト / 正式見積PDF / 発注書Excel）を突き合わせ、漏れ・余分・数量不一致・単価不一致・金額合計差を報告 | Read, Bash, Glob | 購買・リスト |
| レックス | rex | 購買の相見積もりスコア(Excel)から商社ごとの金額を拾い、BOM(Excel)に転記した `<BOM名>_見積.xlsx` を作る。右端に商社ごとの単価・金額列。型式不一致は空欄＋要確認 | Read, Bash, Glob | 購買・リスト |
| エイリアン | alien | 電気図面 PDF の検図。`check_drawing.py` で決定的チェック＋切り抜き画像、ページ画像を目視、指摘を画像付きで返す | Bash, Read, Glob | 設計 |
| ジェシー | jessie | タスク管理。文から仕事と納期を拾って `tasks.py` で登録、納期なしは聞き返す。右パネル「タスク」タブにチェックボックスで常設、期限で催促ポップアップ | Bash | 管理 |
| バズ | buzz | 検証役。`tests/run_tests.py` を1回の Bash で回して PASS/FAIL を報告。直さない | Bash, Read, Glob | 検証 |
| スリンキー | slinky | 録音/文字起こし → `transcribe.py`（文字起こし＋フィラー除去）→ 議事録 md（決定事項/ToDo/論点）。日時・参加者が不明なら1回聞く | Bash, Read, Glob, Write | 文章 |
| ハム（旧ポーク） | ham | 走り書きの短文を意味を変えずに整える（既定は社内メール向け です・ます調。チャット/メール/報告書/英語を指定可）。model: haiku | なし | 文章 |

ポテトのルール: 入力ファイルは編集しない。表記ゆれ（全角半角・大文字小文字・ハイフン・スペース・スラッシュ）はスクリプトが吸収するが、それ以上の「似た型式」は一致扱いにせず「要確認」として両方を並べる。
ハムのルール: 意味・数値・型式を変えない。整えた文だけ返す。
全ボット共通（`.claude/bot_rules.md`、2026-11-03 決定）: 足りない情報は穴埋めせず、先に1回にまとめて聞いて止まる。答えが来てから本来の出力だけ返す。サーバーが直接呼びのシステムプロンプト末尾にこのファイルを自動で足す。CLAUDE.md にも同じ要約がある。

## リポジトリ構成

```
bot-team/
  CLAUDE.md               プロジェクト説明（各ボットの呼び方、共通ルール、入口の説明）
  HANDOFF.md              このファイル
  .claude/agents/         potato.md  ham.md  rex.md  buzz.md  slinky.md  jessie.md  alien.md
  .claude/bot_rules.md    全ボット共通ルール（足りない情報は先に聞く）。server.py が毎回システムプロンプトに追記
  scripts/
    extract_items.py      PDF/Excel/CSV → 標準CSV（型式,型式KEY,品名,数量,単価,金額,メーカ,備考）
                          列名自動判定、--show-columns、--cols 型式=<列名> で手動指定
                          罫線なしPDFは text strategy にフォールバック
                          小計/合計/消費税/送料/値引の行は除外
                          型式列がなければ品名をキーにする
    compare_lists.py      標準CSV 2つを比較 → report.md（漏れ/余分/数量不一致/単価不一致/金額合計差）
                          終了コード: 差分なし 0 / あり 1
    fill_prices.py        レックス用。BOM.xlsx＋スコア.xlsx → <BOM名>_見積.xlsx（openpyxl で BOM の書式を保ったまま右端に列追加）
                          見出し行は先頭15行から型式らしい見出しを探す。スコアは2段見出し（上段に商社名）に対応
                          商社列は自動判定（数値主体で数量などでない列）、--vendors 表示名=列名 で手動指定、--show-columns で確認
                          終了コード: 全行転記 0 / 要確認あり 1 / 失敗 2
    transcribe.py         スリンキー用。音声→文字起こし（openai: gpt-4o-transcribe / whisper: faster-whisper large-v3）→ clean() でフィラー・言い直し除去
                          build_prompt(用語, 直前末尾) / looks_hallucinated / low_confidence / transcribe_chunk を server.py の /transcribe/chunk も使う
                          .txt を渡せば整形だけ。25MB 超は ffmpeg で 10 分分割。OPENAI_API_KEY は ⚙ の「文字起こし API キー」→ _sdk_env() で渡す
    check_drawing.py      エイリアン用。pdfplumber で文字＋座標、page.to_image で画像化。TAG_RE（機器符号）/ WIRE_RE（線番）/ PAGE_RE（n/N）/ DWG_RE（図番）
                          で表記ゆれ・孤立符号・孤立線番・ページ欠け・図番不一致・文字なしを検出し、find-NN.png（赤枠）と report.md を出す。crop サブコマンドで目視箇所の切り抜き
                          符号の正規表現は一般的な略号で仮置き。実図面で見直す。終了コード 0/1/2
    tasks.py              ジェシー用。app/tasks.json に登録/完了/一覧。parse_due() が「すぐ/今週中/今月中旬/11/20/金曜/3日後/なし」を
                          due_start〜due_end に変換（解釈不能は終了コード 3）。status() が 期限切れ/すぐ/今日まで/期間内/予定/納期なし。--store で保存先差し替え（テスト用）
    check_lists.py        上2つをまとめて実行（抽出×2→比較→レポート標準出力）。ポテトはこれを1回の Bash で呼ぶ
                          終了コード: 差分なし 0 / あり 1 / 抽出失敗 2（--cols-base / --cols-target で列指定）
  app/
    server.py             FastAPI。ブラウザからの文＋ファイルを Claude Agent SDK に渡す
                          ボット指定時は親を挟まず直接呼ぶ（定義本文=システムプロンプト、tools/model も定義どおり、setting_sources=[]）
                          「ウッディ」（おまかせ役、id は _auto のまま）だけ親エージェント経由で委譲（CLAUDE.md を読む）。顔だけのシルエット shape: woody
                          GET/POST /settings で API キーを app/config.json に保存、POST /settings/test で疎通確認
                          ボットごとに SDK セッションを保持（会話の続き）、history.json に会話ログ
                          エンドポイント: GET / , /bots , /history/{bot} , /inbox , /settings , /samples/{bot} , /version ,
                          POST /chat (SSE) , /settings , /settings/test , /samples/{bot} , /transcribe/chunk , /transcribe/finish , /shutdown , DELETE /samples/{bot}
                          GET/POST /tasks , POST /tasks/{id}/(done|undone|remove|reminded|snooze|due)
                          静的配信: /static , /files/inbox , /files/samples（ボットが作った画像をチャットに出す用。config.json などは公開しない）
                          config.json: api_key, openai_api_key, cf_account_id, cf_api_token, transcribe_engine(auto|cloudflare|openai|whisper|remote), remote_transcribe_url/token, glossary, chunk_sec
    static/index.html     3カラムUI（左: ボット一覧 / 中央: 会話 / 右: 詳細・作業ログ・ファイル）
                          画面どこでもドロップ、＋ボタン、貼り付け。送信キーは ⚙ で切替（Enter / Ctrl+Enter、localStorage に記憶）
                          フロントマター record: true のボット（スリンキー）では 🎙 でライブ録音: マイク＋任意でタブ音声を AudioContext で合成、
                          chunk_sec（既定 45 秒）ごとに stop→start で完結した webm を /transcribe/chunk に順番に送り（prev=直前の末尾、glossary=用語）、
                          文字をチャットに逐次表示。停止で /transcribe/finish に全文保存 → txt を添付してスリンキーへ自動送信
                          （SYNAPSE の client-recorder.ts / worker-transcribe.ts を移植。外部サーバーモードはその Worker にそのまま転送できる）
  inbox/                  ドロップしたファイルの保存先（git 対象外）
  samples/<bot>/<ラベル>/  ボットごとの参考サンプル（git 対象外。右パネルから追加・削除。フロントマター samples: で枠を定義）
  tests/                  run_tests.py（バズが回す）/ cases.json（8 ケース: potato 4, rex 3, slinky 1）/ make_fixtures.py（入力を生成、fixtures/ は git 対象外）
  requirements.txt        pandas openpyxl pdfplumber fastapi uvicorn python-multipart claude-agent-sdk
  update.bat              更新を一発で: git checkout -- . → git pull → pip install -r → run.bat（Windows 側で agents/*.md や requirements.txt が勝手に「変更あり」になり pull が止まる事故が 2 回あったため）
  setup.bat / setup.ps1   新しい PC の初期設定を一発で: Python 3.10+ 検出（なければ winget）→ .venv → pip → 自動起動登録 → 起動。再実行しても安全
  run.bat / run.sh        窓なしで裏起動（.venv があればその pythonw、なければ PATH の pythonw）＋ブラウザを開く
                          起動済みなら GET /version で印を比べ、同じなら開くだけ、古ければ POST /shutdown で止めて入れ替える（git pull 後も run.bat だけでよい）
  autostart_on/off.bat    Windows ログオン時の自動起動を登録 / 解除（スタートアップにショートカット）
                          終了は画面左上 ⏻ → POST /shutdown。pythonw のときの出力は app/server.log（git 対象外）
                          server.py 冒頭の _hide_child_windows() が anyio.open_process を包み、Windows では claude.exe に CREATE_NO_WINDOW を付ける（送信のたびに黒い窓が出ない）
```

`.claude/agents/*.md` のフロントマターに `group:` と `color:` を足すと左一覧のグループ分けと色に反映される（Claude Code 自体はこの2項目を無視する想定）。
アイコンはボット id から生成するブロブ型 SVG（点の数 3〜7 を滑らかに閉じた形＋目）。形は id で固定、色は `color:` か既定パレット。左上光源の放射状グラデーション＋drop-shadow で立体風。瞳は全アイコンがマウスを追う（mousemove → requestAnimationFrame で `.pupils` を最大 3.2 単位ずらす）。選択中のボットだけ SMIL でパスがモーフィングし、まばたきする（prefers-reduced-motion では止まる）。

## 動作確認の状況

- 確認済み: extract/compare スクリプト（Excel と罫線なしPDFのサンプルで差分検出OK）、サーバー起動、/bots、/history、/inbox、ファイル保存、SSE配信、UI。
- **未確認: Claude Agent SDK への接続部分**（この作業環境に SDK がなかった）。`server.py` の `ClaudeAgentOptions(cwd, setting_sources=["project"], allowed_tools=[... "Agent","Task"], permission_mode="acceptEdits", resume=...)` と `AssistantMessage / TextBlock / ToolUseBlock / ResultMessage` のインポートは公式ドキュメントに合わせて書いたが、実行はしていない。最初の1回はここで止まる可能性が高い。
- 認証: 画面左上 ⚙ の設定ダイアログで API キーを保存（`app/config.json`、git 対象外、`ClaudeAgentOptions(env=...)` で渡すので環境変数より優先）、または `ANTHROPIC_API_KEY`、または Claude Code ログイン。ダイアログに「接続テスト」あり（haiku で1往復）。
- キー保存時は `CLAUDE_CONFIG_DIR=app/claude_home`（git 対象外）も渡し、`~/.claude` の Claude.ai ログインを Claude Code に見せない。これをしないと期限切れのログインが優先されて `Failed to authenticate: OAuth session expired` になる（2026-11-03 実機で発生）。キー未保存なら従来どおり `~/.claude` のログインを使う。
- 2026-11-03 追記: `claude-agent-sdk` 0.2.177 のソースと突き合わせ、`server.py` が使う名前（`query`, `ClaudeAgentOptions`, `AssistantMessage`, `ResultMessage`, `TextBlock`, `ToolUseBlock`）と引数（`cwd`, `setting_sources=["project"]`, `allowed_tools`, `permission_mode="acceptEdits"`, `resume`）、`ResultMessage.session_id` はすべて存在することを確認済み。wheel に Claude Code CLI 2.1.317 が同梱されているので、別途 `npm install -g @anthropic-ai/claude-code` は不要。実際に API を呼ぶところまでは未実行。
- 同日、`scripts/extract_items.py` と `compare_lists.py` を合成データ（Excel の BOM と CSV の発注書、列名違い・全角型式・小計行入り）で再確認し、漏れ/余分/数量不一致/単価不一致/金額合計差がすべて検出された。

## 応答速度（2026-11-03 実測、同じ1文をハム（旧ポーク）に）

| 経路 | 時間 | 備考 |
|---|---|---|
| ウッディ → 親が委譲 → ハム → 親が言い直し | 13〜30 s | LLM 3回直列。親が前置きや件名を勝手に足す |
| ハム（旧ポーク）直接（haiku） | 3.8〜3.9 s | 整えた文だけ返る |
| ハム（旧ポーク）直接（sonnet） | 2.5 s | 1回のみの計測。haiku と大差なし |
| レックス直接（sonnet） | 10.8 s | 合成 BOM＋スコアで Bash 1回（fill_prices.py）。このクラウド環境での計測 |
| エイリアン直接（sonnet） | 25.0 s | 合成 2 ページ PDF。Bash 1 回＋Glob＋ページ画像 Read 2 回。指摘 5 件を画像付きで返した |
| ジェシー直接（sonnet） | 22.8 s / 6.4 s | 2 件のうち納期なしの 1 件を聞き返し＋1 件登録 / 答えを受けて登録。Bash 1 回ずつ |
| バズ直接（haiku） | 16.5 s | 8 ケースを Bash 1回で実行。大半はスクリプト実行時間（pandas 起動×8） |
| ポテト直接（sonnet） | 未計測 | 2026-11-03 に Bash 往復を 3〜4 回 → 1 回（check_lists.py）に削減。実ファイルで計測して埋める |

直接経路の内訳はおおむね Claude Code 起動 1〜2 s ＋ LLM 1回。さらに縮めるなら (1) `include_partial_messages=True` で文字を流しながら表示（体感）、(2) `ClaudeSDKClient` でプロセスを常駐させて起動分を削る、の順。

## 次にやること（優先順）

- **レックス: 本物のサンプルが届いたら** 右パネルの BOM / スコア 枠に入れ、`python scripts/fill_prices.py <BOM> <スコア> --show-columns` で列判定を確認し、`COL_GUESS` / `VENDOR_HINT` / `NOT_VENDOR` を実物に合わせる。合成データでは 2段見出し（上段商社名）、タイトル行付き BOM、全角型式、片方だけにある型式を確認済み。

0. 別 PC で `setup.bat` を一度通す（2026-11-03 作成、Windows 実機では未実行。PowerShell が止まったらその画面の文面を貼る）。
1. ~~SDK 疎通~~ 2026-11-03 に Windows 実機で画面→SDK→Claude Code まで到達を確認（認証エラーが Claude Code から返った）。残りは API キーを ⚙ で保存して返事が来ることの確認。
   Python 3.10 以上を用意（3.8 では `claude-agent-sdk` が見つからず pip が失敗する。pyenv-win なら `pyenv install --list` に出る最新の 3.12.x を `pyenv install` → リポジトリ内で `pyenv local 3.12.x`。2026-11-03 時点の一覧は 3.12.8 まで）→ `pip install -r requirements.txt` → `run.bat` → ハムに1文投げて SDK 接続を通す。エラーが出たら server.py の SDK 呼び出し部分を直す。
2. 本物の見積PDF・発注書Excelでポテトを走らせ、`--show-columns` で列判定を確認し、`COL_GUESS` に候補を足す。
3. ~~`tests/` にサンプル入力と期待レポートを置き、検証役ボット（verifier）を追加して全テストを回させる。~~ 2026-11-03 完了（バズ）。ボットやスクリプトを足したら `tests/cases.json` にケースを足す。
- **採用エンジン（2026-11-03 決定）: Cloudflare Workers AI の Whisper を REST で直接**（`scripts/transcribe.py` の `_cloudflare`、`CF_ACCOUNT_ID` / `CF_API_TOKEN` は ⚙ から）。SYNAPSE 経由も OpenAI も不要。ファイル入力のときは ffmpeg で 60 秒ごとに分割して送る。このセッションからは api.cloudflare.com に出られず、リクエストの形と応答の解釈・エラー文のみ疑似応答で確認済み。実機で最初の 1 回を見ること（401=トークン、403=権限、404=アカウント ID、429=無料枠）。
- **SYNAPSE を外部サーバーとして使う場合**（採用せず、手順だけ残す）: `docs/synapse/README.md` の手順で `worker/minutes.ts` を SYNAPSE に足して `npm run deploy`（このセッションからは SYNAPSE への push 権限が無く、ファイルと手順だけ用意した）。トークンは SYNAPSE の `APP_TOKEN`。既存の `/api/transcribe` は英語専用なので流用不可。
- **エイリアン: 実図面で未検証**（合成 PDF のみ）。最初の実図面で `TAG_RE`（符号の略号）、`DWG_RE`（図番の形）、タイトル欄の位置（右下 25%）を合わせる。孤立線番は誤検出が多い想定なので件数が多ければ閾値や範囲を絞る。CAD からの PDF は文字が取れる前提。スキャンは「文字なし」扱い。
- **スリンキー ライブ録音: 実機・実エンジンで未検証**（この環境はエンジン応答をスタブにしてブラウザの流れだけ確認）。初回は ⚙ でエンジンと用語を入れ、短い会議で試す。タブ音声は Chrome/Edge で「タブの音声を共有」を有効にすること。
- **スリンキー: 実際の録音で未検証**（この環境に OpenAI キーなし）。整形と鍵なし時のエラー文だけ確認済み。最初の1回は短い録音で `python scripts/transcribe.py <音声>` を手で回し、文字起こし精度とフィラー除去のやり過ぎ（`FILLERS_BOUNDED` の「はい」「ね」など）を見る。エンジン候補は中立ベンチマークが無いので実音声で比較して決める。
4. ボット階層の本体（lead / worker / verifier）に着手。最初は「リード1・ワーカー2・検証1」の小さい構成から。
5. 将来: ブラウザを使うボットを足すときは右パネルの作業ログをスクリーンショット表示に戻す。長時間処理はクラウドセッションに逃がす。

## 会社ファイルを扱う上での注意

Claude Code / API 側のプライバシー設定（学習に使われない設定）を先に確認する。`inbox/` と `app/history.json` は git に入れない（.gitignore 済み）。

2026-11-03: ポーク → ハム（id も pork → ham、ファイルは ham.md）。アイコンは Toy Story 風の固定シルエット（woody/potato/ham/rex/buzz/slinky）を index.html の SHAPES に定義。
