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
| ポーク | pork | 走り書きの短文を意味を変えずに整える（既定は社内メール向け です・ます調。チャット/メール/報告書/英語を指定可）。model: haiku | なし | 文章 |

ポテトのルール: 入力ファイルは編集しない。表記ゆれ（全角半角・大文字小文字・ハイフン・スペース・スラッシュ）はスクリプトが吸収するが、それ以上の「似た型式」は一致扱いにせず「要確認」として両方を並べる。
ポークのルール: 意味・数値・型式を変えない。主語が抜けて意味が取れない箇所は `【確認: 〜？】` を入れて返す。整えた文だけ返す。

## リポジトリ構成

```
bot-team/
  CLAUDE.md               プロジェクト説明（ポテト・ポークの呼び方、入口の説明）
  HANDOFF.md              このファイル
  .claude/agents/         potato.md  pork.md
  scripts/
    extract_items.py      PDF/Excel/CSV → 標準CSV（型式,型式KEY,品名,数量,単価,金額,メーカ,備考）
                          列名自動判定、--show-columns、--cols 型式=<列名> で手動指定
                          罫線なしPDFは text strategy にフォールバック
                          小計/合計/消費税/送料/値引の行は除外
                          型式列がなければ品名をキーにする
    compare_lists.py      標準CSV 2つを比較 → report.md（漏れ/余分/数量不一致/単価不一致/金額合計差）
                          終了コード: 差分なし 0 / あり 1
    check_lists.py        上2つをまとめて実行（抽出×2→比較→レポート標準出力）。ポテトはこれを1回の Bash で呼ぶ
                          終了コード: 差分なし 0 / あり 1 / 抽出失敗 2（--cols-base / --cols-target で列指定）
  app/
    server.py             FastAPI。ブラウザからの文＋ファイルを Claude Agent SDK に渡す
                          ボット指定時は親を挟まず直接呼ぶ（定義本文=システムプロンプト、tools/model も定義どおり、setting_sources=[]）
                          「おまかせ」だけ親エージェント経由で委譲（CLAUDE.md を読む）
                          GET/POST /settings で API キーを app/config.json に保存、POST /settings/test で疎通確認
                          ボットごとに SDK セッションを保持（会話の続き）、history.json に会話ログ
                          エンドポイント: GET / , /bots , /history/{bot} , /inbox , /settings , POST /chat (SSE) , /settings , /settings/test , /shutdown
    static/index.html     3カラムUI（左: ボット一覧 / 中央: 会話 / 右: 詳細・作業ログ・ファイル）
                          画面どこでもドロップ、＋ボタン、貼り付け。Enter送信、Shift+Enter改行
  inbox/                  ドロップしたファイルの保存先（git 対象外）
  tests/                  まだ空。サンプル入力→期待レポートを置く予定
  requirements.txt        pandas openpyxl pdfplumber fastapi uvicorn python-multipart claude-agent-sdk
  setup.bat / setup.ps1   新しい PC の初期設定を一発で: Python 3.10+ 検出（なければ winget）→ .venv → pip → 自動起動登録 → 起動。再実行しても安全
  run.bat / run.sh        窓なしで裏起動（.venv があればその pythonw、なければ PATH の pythonw）＋ブラウザを開く。起動済みなら開くだけ
  autostart_on/off.bat    Windows ログオン時の自動起動を登録 / 解除（スタートアップにショートカット）
                          終了は画面左上 ⏻ → POST /shutdown。pythonw のときの出力は app/server.log（git 対象外）
```

`.claude/agents/*.md` のフロントマターに `group:` と `color:` を足すと左一覧のグループ分けと色に反映される（Claude Code 自体はこの2項目を無視する想定）。
アイコンはボット id から生成するブロブ型 SVG（点の数 3〜7 を滑らかに閉じた形＋目）。形は id で固定、色は `color:` か既定パレット。選択中のボットだけ SMIL でパスがモーフィングし、まばたきする（prefers-reduced-motion では止まる）。

## 動作確認の状況

- 確認済み: extract/compare スクリプト（Excel と罫線なしPDFのサンプルで差分検出OK）、サーバー起動、/bots、/history、/inbox、ファイル保存、SSE配信、UI。
- **未確認: Claude Agent SDK への接続部分**（この作業環境に SDK がなかった）。`server.py` の `ClaudeAgentOptions(cwd, setting_sources=["project"], allowed_tools=[... "Agent","Task"], permission_mode="acceptEdits", resume=...)` と `AssistantMessage / TextBlock / ToolUseBlock / ResultMessage` のインポートは公式ドキュメントに合わせて書いたが、実行はしていない。最初の1回はここで止まる可能性が高い。
- 認証: 画面左上 ⚙ の設定ダイアログで API キーを保存（`app/config.json`、git 対象外、`ClaudeAgentOptions(env=...)` で渡すので環境変数より優先）、または `ANTHROPIC_API_KEY`、または Claude Code ログイン。ダイアログに「接続テスト」あり（haiku で1往復）。
- 2026-11-03 追記: `claude-agent-sdk` 0.2.177 のソースと突き合わせ、`server.py` が使う名前（`query`, `ClaudeAgentOptions`, `AssistantMessage`, `ResultMessage`, `TextBlock`, `ToolUseBlock`）と引数（`cwd`, `setting_sources=["project"]`, `allowed_tools`, `permission_mode="acceptEdits"`, `resume`）、`ResultMessage.session_id` はすべて存在することを確認済み。wheel に Claude Code CLI 2.1.317 が同梱されているので、別途 `npm install -g @anthropic-ai/claude-code` は不要。実際に API を呼ぶところまでは未実行。
- 同日、`scripts/extract_items.py` と `compare_lists.py` を合成データ（Excel の BOM と CSV の発注書、列名違い・全角型式・小計行入り）で再確認し、漏れ/余分/数量不一致/単価不一致/金額合計差がすべて検出された。

## 応答速度（2026-11-03 実測、同じ1文をポークに）

| 経路 | 時間 | 備考 |
|---|---|---|
| おまかせ → 親が委譲 → ポーク → 親が言い直し | 13〜30 s | LLM 3回直列。親が前置きや件名を勝手に足す |
| ポーク直接（haiku） | 3.8〜3.9 s | 整えた文だけ返る |
| ポーク直接（sonnet） | 2.5 s | 1回のみの計測。haiku と大差なし |
| ポテト直接（sonnet） | 未計測 | 2026-11-03 に Bash 往復を 3〜4 回 → 1 回（check_lists.py）に削減。実ファイルで計測して埋める |

直接経路の内訳はおおむね Claude Code 起動 1〜2 s ＋ LLM 1回。さらに縮めるなら (1) `include_partial_messages=True` で文字を流しながら表示（体感）、(2) `ClaudeSDKClient` でプロセスを常駐させて起動分を削る、の順。

## 次にやること（優先順）

0. 別 PC で `setup.bat` を一度通す（2026-11-03 作成、Windows 実機では未実行。PowerShell が止まったらその画面の文面を貼る）。
1. ~~SDK 疎通~~ 2026-11-03 に Windows 実機で画面→SDK→Claude Code まで到達を確認（認証エラーが Claude Code から返った）。残りは API キーを ⚙ で保存して返事が来ることの確認。
   Python 3.10 以上を用意（3.8 では `claude-agent-sdk` が見つからず pip が失敗する。pyenv-win なら `pyenv install --list` に出る最新の 3.12.x を `pyenv install` → リポジトリ内で `pyenv local 3.12.x`。2026-11-03 時点の一覧は 3.12.8 まで）→ `pip install -r requirements.txt` → `run.bat` → ポークに1文投げて SDK 接続を通す。エラーが出たら server.py の SDK 呼び出し部分を直す。
2. 本物の見積PDF・発注書Excelでポテトを走らせ、`--show-columns` で列判定を確認し、`COL_GUESS` に候補を足す。
3. `tests/` にサンプル入力と期待レポートを置き、検証役ボット（verifier）を追加して全テストを回させる。
4. ボット階層の本体（lead / worker / verifier）に着手。最初は「リード1・ワーカー2・検証1」の小さい構成から。
5. 将来: ブラウザを使うボットを足すときは右パネルの作業ログをスクリーンショット表示に戻す。長時間処理はクラウドセッションに逃がす。

## 会社ファイルを扱う上での注意

Claude Code / API 側のプライバシー設定（学習に使われない設定）を先に確認する。`inbox/` と `app/history.json` は git に入れない（.gitignore 済み）。
