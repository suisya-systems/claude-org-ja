# ワーカーへの指示テンプレート

org-broker の send_message（`to_id="worker-{task_id}"`）で送信する、タスク固有の指示。
権限・報告先・SUSPEND対応・知見記録の指示は worker-claude-template.md（CLAUDE.md経由）に一元化されているため、ここでは繰り返さない。

> **輸送層（transport）両系 — 既定 `broker` / opt-in `renga`**: 本ファイル（および各スキル）の peer message・pane 操作は `mcp__org-broker__*` で書いてあり、**`ORG_TRANSPORT` 無設定＝既定 `broker`** ではそのまま従えばよい。`ORG_TRANSPORT=renga`（opt-in、切戻し可）では MCP サーバー名が `renga-peers` になり、**完全修飾名が `mcp__org-broker__*` → `mcp__renga-peers__*`** に機械置換される（引数形・セマンティクスは同一なので操作の論理は変わらない）。輸送依存で手順が変わる差は次の 3 点:
>
> - **受信モデル（既定 = push 一次 = `claude/channel` / pull フォールバック）**: 既定 broker は **push 一次**に設計されている（runtime push-first 0.1.24+、設計 SoT は transport-lab `docs/design/broker-native-roles.md` §9）: 各ペイン同居の **channel sidecar**（`server:org-broker-channel`）が broker キューを ~1 秒間隔で claim→push し、`notifications/claude/channel` で本文を idle セッションへ注入する（「受けたら即応答」契機が生まれる）。ワーカー ack（`to_id="worker-{task_id}"`）・retro gate ack（`to_id="dispatcher"`）・ディスパッチャー handover 経路の `send_message` / `check_messages` / `send_keys` / `inspect_pane` は同じツール名（`mcp__org-broker__*`）で動く。**pull はフォールバック層**: sidecar 不在 / unhealthy（heartbeat timeout で `delivery_mode=PULL`）/ channel 非対応ペイン（codex pull-peer）/ claude.ai login 不在時は、各役割が自身の cadence で能動的に `check_messages` する（役割別 cadence: worker=ターン境界 / 完了後 bounded `/loop`・dispatcher=`/loop 3m`・secretary=ターン冒頭。「ナッジを見たら `check_messages`」prose は**撤回せず**この fallback cadence として読む）。`ORG_TRANSPORT=renga`（opt-in）では、ワーカー報告・ディスパッチャー応答が `<channel source="renga-peers" …>` として in-band で push される（renga の in-band push と broker push 一次は同じ即応契機）。契約面は Surface 8 + push-primary amendment で push 一次が **ratified 済み**（2026-06-15、S3。pull は fallback として retain・renga 不変）。
> - **spawn 儀式（既定 = folder-trust 承認 + dev-channel sidecar 承認の 2 段）**: 子ペイン起動時、既定 broker は `--mcp-config <broker>` を注入し Claude Code の **folder-trust プロンプト**を `send_keys(enter=true)` で機械承認する**のに加えて**、push 一次のため channel sidecar を `--dangerously-load-development-channels server:org-broker-channel` で load し dev-channel 承認プロンプト（spawn-flow 3-3b）を `send_keys(enter=true)` で機械承認する（folder-trust + dev-channel の 2 段承認。詳細は [`.dispatcher/references/spawn-flow.md`](../../../../.dispatcher/references/spawn-flow.md) 3-2 / 3-3b、設計は broker-native-roles.md §9.5）。`ORG_TRANSPORT=renga`（opt-in）では `--dangerously-load-development-channels server:renga-peers` を注入し「Load development channel?」を Enter 承認する 1 段。**注: attention watcher は transport 非依存の CLI ペインであり、folder-trust / dev-channel いずれの 2 段承認の対象外**（spawn 儀式の反転に巻き込まない）。
> - **エラー分岐（既定 = broker 拡張コード込み）**: 既定 broker は shared codes（`pane_not_found` / `last_pane` / `invalid-params`、Surface 6）に加え broker 固有 `[token_invalid]` / `[session_invalid]` / `[tool_not_authorized]` / `[no_backend]`（= adapter_unavailable）/ `[nudge_failed]` / `[peer_not_found]` / `[name_taken]` / `[unknown_tool]` を返しうる（未知コードは default-branch で escalate）。`ORG_TRANSPORT=renga` 時は broker 固有コードは発生せず shared codes + renga 固有コードのみ。
>
> 契約面の正本は [`docs/contracts/backend-interface-contract.md`](../../../../docs/contracts/backend-interface-contract.md) Surface 8（broker auth & delivery、ratified 2026-06-14）+ 末尾「Ratified amendment (2026-06-15): push-primary delivery」（S3。**broker push 一次が既定の契約**、pull は structural fallback として retain）、設計 SoT は transport-lab `docs/design/broker-native-roles.md` §9（push 一次）/ `docs/design/ja-migration-plan.md` §5・§8。**opt-in `renga` は削除せず常時有効な fallback として維持する**（切戻しの安全装置）。broker 実走（dogfood）は Epic #6 Issue G スコープであり、本ファイルの既定運用経路ではない（**既定の二フレーム注記（Refs #604）**: ここでの「既定 `broker`」は**コード既定**フレーム — `tools/transport.py: DEFAULT_TRANSPORT` が runtime 0.1.28 (Epic #586) で `broker` にフリップ済みで、ja 生成器・`transport.resolve()` はこのコードフレームで render するため生成面はこう表示する。別に**運用既定**フレームがあり、broker 実走 dogfood が Epic #6 Issue G まで未活性のため運用上の既定経路は `renga`。両フレームは指す対象（コード定数 vs 運用経路）が異なり矛盾しない。総説は root [`CLAUDE.md`](../../../../CLAUDE.md)「輸送層（transport）両系」節。）

## ultracode タスクでの worker 着手規約（Issue #554）

worker brief（worker の行動規範ファイル＝既定 `CLAUDE.md`、claude-org 自己編集タスクは `CLAUDE.local.md`）に ultracode 使用許可があるタスクでは、ultracode は **dispatcher がペインに `send_keys` で打鍵する `ultracode` kickoff（user turn）**でのみ武装される（この指示本文＝`send_message` / `check_messages` 経由の peer/channel メッセージ内に `ultracode` と書いても武装しない＝実走確定）。したがって worker 側の規約（下記テンプレ「作業の進め方」節にも worker へ届く形で記載）:

- brief に ultracode 許可があるなら、**この peer message では本作業を始めず**、dispatcher の `ultracode` send_keys kickoff turn に応答して本作業を開始する（その応答全体が武装されるため）。peer message の指示はリファレンス。許可が無ければ通常どおりこの指示で着手してよい。
- 武装の詳細・dispatcher 手順の SoT は [`.dispatcher/references/spawn-flow.md`](../../../../.dispatcher/references/spawn-flow.md) の **3-5a**。許可文言を本テンプレに転記する必要はない（SoT は worker brief の implementation 指示）。
- ultracode の位置づけ（実装と Codex 前セルフレビュー収束に使う前段。最終 Codex ゲートは維持）は下記テンプレ「検証深度: full」節に条件付きで載せ、worker に届くようにしている。

## テンプレート

```
以下のタスクを遂行してください。詳細な行動規範は CLAUDE.md に記載されています。

## タスク
{タスクの目的と期待する成果物を具体的に記述}

## プロジェクトの準備
重要: あなたの作業ディレクトリは CLAUDE.md に記載された絶対パスです。
まず `pwd` を実行し、CLAUDE.md の作業ディレクトリと一致することを確認してください。
全てのファイル作成はこのディレクトリ内に限定。`..` への移動や claude-org 構造の再現は禁止です。
{ディレクトリパターンに応じて以下のいずれかを指示}

### パターン A（プロジェクトディレクトリ・初回）の場合:
作業ディレクトリは窓口（secretary）が spawn 前に準備済みです（clone / `git init` 等は窓口側で完了している）。
起動時の最初のアクション: `pwd` を実行し、CLAUDE.md に記載された想定ディレクトリと一致していることを確認してください。
ワーカー側で `git clone` / `git init` を実行しないこと。claude-org の構造を真似たディレクトリ（.claude/, .state/ 等）を作らないこと。
{必要に応じて clone 元 URL / ローカルパス / 新規プロジェクトかどうか等の情報のみ記載}

### パターン A（プロジェクトディレクトリ・再利用）の場合:
このディレクトリは以前のタスクで使用されたプロジェクトディレクトリです。既存のファイルとgit履歴が残っています。
clone は不要です。{引き継ぎ事項があれば記載}

### パターン B（worktree）の場合:
このディレクトリは git worktree として準備済みです。ブランチ `{branch_name}` にチェックアウトされています。
clone は不要です。そのまま作業を開始してください。

### パターン C（エフェメラル）の場合:
作業ディレクトリは窓口（secretary）が spawn 前に準備済みです（clone / `git init` 等は窓口側で完了している）。
起動時の最初のアクション: `pwd` を実行し、CLAUDE.md に記載された想定ディレクトリと一致していることを確認してください。
ワーカー側で `git clone` / `git init` を実行しないこと。claude-org の構造を真似たディレクトリ（.claude/, .state/ 等）を作らないこと。
{必要に応じて clone 元 URL / ローカルパス / 新規プロジェクトかどうか等の情報のみ記載}

## ブランチ戦略
{ブランチ名の指定、またはmainで直接作業等}

## 作業の進め方
auto モードで直接作業してください。Plan モードは使用しないこと。
**ultracode 許可タスクのみ**（worker brief の「実装ガイダンス」に ultracode 使用許可がある場合）: **この peer message では本作業を始めない**。本作業は dispatcher が打鍵する `ultracode` send_keys kickoff（user turn）に応答して開始すること（その turn でのみ multi-agent workflow / Workflow tool が武装される。peer message / `check_messages` 経由の本文では武装しない）。許可が無ければこの指示でそのまま着手してよい。

## 制約
{使用言語、フレームワーク、テスト要件等があれば記載}

## 検証深度: {full | minimal}
この行はテンプレートから**削除せず必ず送信する**。窓口は 2 値のどちらか 1 つだけを埋める。
既定は `full`。trivial fix のときのみ `minimal` を窓口側で選択して埋めること。

- **full**（新機能実装 / 修正 / リファクタ / テスト追加 / hook・skill・設定編集など、コードまたは挙動の変更を伴うもの）
  - **Knowledge 層プライバシー（`knowledge/raw/` への記録時、full モードのみ該当）**: `knowledge/raw/` および `knowledge/curated/` は公開 OSS リポジトリにコミットされる。operator の固有名・社内システム識別子・顧客データ・シークレット・社内 URL などの operator-private な内容を、これらディレクトリに書き込んではならない。当該情報を含む学びがあった場合は記録せず、窓口（secretary）にエスカレーションすること。
  - **codex の有無に関わらず必須**: 既存テストスイート / lint / type-check 等のリポジトリ通常検証を green まで実行し、通常の完了報告フォーマット（成果物説明・残作業・PR 草案・振り返り記録・**人間向け理解サマリ**）で報告
  - **人間向け理解サマリ（`full` 限定・必須）**: 窓口がコードを精読せずに「何を承認しようとしているか」を把握し、そのままユーザーへの承認提示に使えるよう、完了報告に以下 3 点を必ず含める。これは `awaiting_review` (REVIEW) 遷移を起こす full モード完了報告（`worker_completed`）の一部であり、contract の不変条件（[`docs/contracts/delegation-lifecycle-contract.md`](../../../../docs/contracts/delegation-lifecycle-contract.md) T4 / T5）は変えず、報告フォーマットだけを拡張するもの:
    1. **最重要の変更点（N 個）**: このタスクで実際に変えたことを効果の大きい順に N 個（目安 3〜5 個）。1 項目 1〜2 行で、diff を開かなくても要旨が掴めるように書く
    2. **要確認ファイル / hunk**: 人間が承認前に必ず目を通すべきファイル（と該当する関数 / hunk）。「全部見て」ではなく「ここだけは見てほしい」に絞る
    3. **設計判断と理由**: 採用した設計上の選択と、なぜそれを選んだか。却下した代替案があれば 1 行添える
    - minimal モードではこのサマリは不要（trivial fix に負荷をかけない。下記 minimal の 1 行 `done:` 報告のまま）
  - **ultracode（許可があるタスクのみ）**: brief に ultracode 使用許可があり、kickoff の user turn で武装されている場合、実装と **Codex 前のセルフレビュー収束**に ultracode（multi-agent workflow / Workflow tool）を使ってよい（複数観点の並列レビューで指摘の種を潰し、Codex の周回数を減らす）。最終ゲート（下記 Codex Blocker / Major ゼロ）は **ultracode の有無に関わらず従来どおり維持**する。ultracode は Codex ゲートの**前段**であって置き換えではない。武装されていなければ ultracode は使わない（武装は本文ではなく dispatcher の `send_keys` user turn が担う。[`.dispatcher/references/spawn-flow.md`](../../../../.dispatcher/references/spawn-flow.md) 3-5a 参照）
  - **追加ゲート（任意）**: commit 完了後に `codex` CLI が available なら **`codex exec review`（review surface）**で Codex 差分セルフレビューを実行する（`codex exec` 直打ちの長文プロンプト形は廃止。review surface は中小 diff で約 2 倍速く、安全側 Blocker/Major のパリティは同等）
    - 確認コマンド: `command -v codex`（Bash/zsh）/ `Get-Command codex -ErrorAction SilentlyContinue`（PowerShell）
    - codex 未導入環境ではセルフレビューを skip し、上記の通常検証だけで完了報告に進む（以下のラウンド規律は適用されない）
    - **安全機構に掛かって review 処理が進められない場合（`available` だが safety block）は別扱い**: diff 内容がモデル安全分類器に掛かり `codex exec review` を完了できないときの skip は「codex 未導入 skip」とは**意味が異なる** — ゲート**未成立**であって「codex clean」ではない（回避の言い換え・プロンプト改変はしない）。正式リカバリは**新規セッション化**（同一 worktree・commit を引き継いだ continuation spawn で review 再実行し、完了報告に引き継いだ HEAD SHA を明記。窓口にセッション再起動を依頼）。リカバリ不能時は「codex clean」と**報告せず**「ゲート未成立（safety block、HEAD=`<sha>`）」と明示して窓口の判断を仰ぐ
  - **以下は codex を実行した場合のみ適用**:
    - Blocker / Major は修正コミットを積んでから完了報告
    - **round は既定上限 3**（brief の「実装ガイダンス」で別値が明示指定されていればそちらが優先）。**上限に達したら round N+1 に自走で入らず**、残っている Blocker / Major 指摘 + 自己評価（設計問題化しているのか、別問題が順に露見する健全な収束の途中なのか）を添えて窓口に報告して停止する（別問題が毎 round 出るケースでも上限で止め、続行判断は人間に委ねる）
    - **同一指摘（例: loose match 精緻化 / 型絞り等）が 3 ラウンド消えない場合は、上限に達する前でも即座に設計問題として報告する**。同じ指摘 / 箇所が修正しても再燃するのは修正アプローチ自体の問題のサインで、別問題が各 1 round で順に解消する健全な収束（上限まで継続可）とは区別する。即完了報告し、窓口に仕様縮小 / 設計見直しの判断を仰ぐ（無限ループ防止）
    - Minor / Nit は原則残置。README / Issue / PR 本文に既知制限として明記する
    - `codex:rescue` スキルは使用しないこと（18 分超ハング事例あり、`codex exec review` / `codex exec` 系の直打ちが安定）。`gpt-5.5-codex` モデルと API キー surface は ChatGPT アカウントで実行不可（`-m gpt-5.5` を明示）
  - レビュー指示例（diff セルフレビュー）: `codex exec review --base origin/main -m gpt-5.5 -c model_reasoning_effort=medium`（`--base` にはブランチのベース（通常 `origin/main`）を渡す。**ローカル `main` ではなく remote-tracking の `origin/main`** を使うのは、共有 clone のローカル `main` が古いと別タスク差分を巻き込む誤レビューになるため。codex 内蔵レビュープロンプトが Blocker/Major 相当を返す）
  - **正規実行形（diff セルフレビュー = review surface）**: `codex exec review --base origin/main -m gpt-5.5 -c model_reasoning_effort=medium < /dev/null` — **前景実行**して出力（Blocker/Major 相当）を読んでから次に進む。stdin は `< /dev/null` で閉じる。背景化 `&` + ログ redirect は完了を待たず・指摘を読まずに完了報告してゲートを素通りする事故を招くため避ける（fast な review surface は前景で待てば自然にゲートが効く）。コピペするコマンドにリダイレクト記号を含む `<main>` / `<N>` 等のプレースホルダを残さない（`< main` と誤解釈され落ちる）。**`origin/main` 参照前に `git fetch origin` を 1 回**（着手時 pull 済みでも review 直前に remote-tracking ref を最新化。fetch 不能でも review 自体は継続）。
    - **large diff（100 行超目安）では effort を上げない**（high-effort review は大 diff でスケールせず直打ちより遅くなる実測あり）。review surface は危険側 Major は守るが benign な safe-side false-negative / ReDoS 級を取りこぼしうる（深掘りが要る変更は design review 併用を窓口に相談）
    - **重い `codex exec` プロンプト（デザインレビュー等、長時間ハングしうる）を背景実行する場合**（`knowledge/curated/codex.md` のハングガード参照）: `mkdir -p tmp` で出力先を保証し `> tmp/codex-review-round1.log 2>&1 < /dev/null &` でラウンド別ログに直書き（`| tail` パイプ禁止）。完了検知はマーカーでなく**プロセス終了**で待つ（例: `until ! tasklist | grep -qi codex; do sleep 10; done`）。Windows PowerShell 制約言語モードの `rejected: blocked by policy` 多発は別手段フォールバックで完走するため**レビュー失敗と誤判定しない**。判定サマリだけで done にせず、ログ末尾（`tail -c 8000`）で**残留リスク自己申告**を確認し未実行分を手動再実行してから「codex clean」と報告する

- **minimal**（trivial fix: CI 出力整形 / typo / コメント修正 / 既存テスト形式合わせ等、指示で変更箇所が 1 ファイル数行に限定されるもの）
  - 指示された fix を反映 → `git add` → `git commit` 直行
  - Codex セルフレビュー・追加テスト実行・差分確認を超えた動作検証は**一切禁止**
  - 完了報告は窓口（`secretary`）に送信する 1 行:
    - `done: {commit SHA 短縮形} {変更ファイル名}`（例: `done: be8f497 tests/test-block-pretooluse-hooks.sh`）
    - SHA は `git rev-parse --short HEAD`、ファイル名は単独ファイルなら 1 つ、複数なら空白区切り
    - これ以外の情報（成果物説明・PR 草案・残論点等）は不要。push / PR 起票は窓口側で実施する
  - 振り返り記録（`knowledge/raw/`）は minimal では**不要**（trivial fix に再利用可能な学びはない前提）。非自明な発見があれば `full` と同様 1 件作ってよい

**選択は窓口の責任**。ワーカーは指示に書かれた値（`full` or `minimal`）にそのまま従い、自分で切り替え判断をしないこと。派遣時にこの行自体が送信されなかった・または値が不明瞭だった場合はワーカーから窓口に確認すること（勝手に `full` にフォールバックしない）。
```

## cross-cutting operational change の場合の consistency grep target list

運用モード・共通設定・命名規約のような **cross-cutting な変更**（1 ファイルに閉じず、複数のロール / skill / 設定 / ドキュメントを横断する変更）を委譲するときは、ワーカー指示の「制約」または「タスク」セクションに、整合性確認のための grep スコープを明示する。スコープを書かないと、ワーカーは目に付いたファイルだけ直して、別ロール側 / docs 側の同名参照を取りこぼす（rename / mode 変更で起きやすい）。

### 「cross-cutting」と判定する例

- **運用モード変更**: Plan / auto / `bypassPermissions` 等のデフォルト切り替え
- **permissions / hook 設定の wholesale 変更**: `.claude/settings*.json` の allow / deny / hooks を横断的に書き換える
- **communication channel / MCP server 名変更**: org-broker の peer 名・MCP サーバ名・ロール識別子の rename（例: `foreman` → `dispatcher`）
- **共通 flag / env var の追加削除**: 全ロール / 複数 skill が読む環境変数や CLI flag

逆に、1 つの skill / 1 つのロール内に閉じる挙動変更（例: `org-retro` 内のフォーマット調整）は cross-cutting ではないので、このセクションは不要。

### 推奨 grep target ディレクトリ

cross-cutting と判定したら、**最低限以下を grep スコープとしてワーカー指示に列挙する**。プロジェクト構成によって存在しないものは適宜削る:

- `.claude/` — skill 本体（`skills/`）に加えて `settings.json` / `settings.local.json` まで含めること。permissions / hook / env 変更は設定本体に残ることが多く、`.claude/skills/` だけ走査すると正典設定を取りこぼす
- `registry/` — projects.md / org-config.md / worker-directory.md
- `knowledge/curated/` — 蓄積された運用知見（旧名で書かれた pattern が残りやすい）
- `dashboard/` — JSON 生成スクリプト・テンプレ
- `.dispatcher/` — ディスパッチャー（dispatcher）ロールの runtime / プロンプト
- `.curator/` — キュレーターロールの runtime / プロンプト
- `.hooks/` — PreToolUse / PostToolUse の hook スクリプト本体（hook ファイル名・ロール識別子の参照が残る）
- `docs/` — 公開ドキュメント
- `tools/` — チェッカ・補助スクリプト（`check_role_configs.py` 等）
- `tests/` — hook / runner / checker のテスト（rename / mode 変更で fixture 名が漏れると CI を壊す）

ワーカー指示の例:

```
## 制約
- 旧名 `foo` の参照が以下のディレクトリに残っていないか grep し、見つけたら全て新名 `bar` に置換すること:
  - .claude/                （settings.json / settings.local.json も含む）
  - registry/
  - knowledge/curated/
  - dashboard/
  - .dispatcher/
  - .curator/
  - .hooks/
  - docs/
  - tools/
  - tests/
- grep コマンド例（Bash / Git Bash / WSL）: `grep -rn "foo" .claude/ registry/ knowledge/curated/ dashboard/ .dispatcher/ .curator/ .hooks/ docs/ tools/ tests/`
- grep コマンド例（PowerShell）: `Select-String -Path .claude\,registry\,knowledge\curated\,dashboard\,.dispatcher\,.curator\,.hooks\,docs\,tools\,tests\ -Pattern "foo" -Recurse`
```

委譲時点で旧名 / 新名が確定していない場合は、ワーカーに「対象パターンを検出して一覧化 → 窓口に確認 → 置換」の 2 段で動かすこと。

## 監視ロール待ち合わせ設計を含む委譲の brief 必須文言

監視ロール（dispatcher / curator 等の /loop 常駐・定期 polling ロール）への待ち合わせ・spawn 連携・lifecycle を変更する委譲では、ワーカー指示の「制約」セクションに以下の必須文言を**そのまま**含める（**ファイル変更が 1 件でも省略しない**。design review 側のトリガー・3 問プロンプトは [`.claude/skills/org-delegate/references/codex-design-review.md`](codex-design-review.md) を参照）:

> - 監視ロールに**ブロッキング待ちを追加してはならない**（sleep / busy-wait / 同期 join での完了待ち禁止）
> - spawn 後は**即時復帰**し、監視ループに制御を返すこと
> - 完了通知の検出は**ループ通常サイクル**（次回 polling）で行う
> - **timeout はループ側で管理**する（spawn 呼び出し側で待たない）

## doc-audit role 専用: write 成果物の chunk 転送方式

doc-audit role では Edit / Write / MultiEdit / NotebookEdit が deny され、Bash heredoc も deny-circumvention safeguard でブロックされる。AUDIT.md 等の成果物書き出しが必要なタスクでは、ワーカーが本文を抱えてブロックする事故が再現性高く発生する（実例: 2026-05-03 readme-drift-audit, 26 findings × 7 repos）。

doc-audit role + write 成果物がある委譲では、以下の文言を「制約」セクションに **必ず追加** する:

> 成果物（{ARTIFACT_NAME}）はファイルに書き出さず、本文を 8000 字程度のチャンクに分割して org-broker `mcp__org-broker__send_message(to_id="secretary")` で順送信してください。各チャンク冒頭に `[CHUNK n/N]` ヘッダを付け、最後に `[CHUNK_END]` を送信。窓口側で結合し `{worker_dir}/{ARTIFACT_NAME}` として書き出します。Edit/Write を試みてはいけません（deny される）。

`{ARTIFACT_NAME}` は AUDIT.md / REPORT.md など実際のファイル名に置換する。

## 使用時の注意

- タスクの記述は具体的に。曖昧な指示はワーカーの判断コストを上げる
- 制約がある場合は必ず明示する

## 自動展開テンプレート (helper-rendered)

`claude-org-runtime dispatcher delegate-plan --locale-json <path-to-ja_locale.json>` は task JSON に `instruction_vars` が含まれる場合、以下の strict テンプレートを変数展開してワーカー指示として書き出す（日本語の挨拶文や `(なし)` などの defaults はリポジトリルート直下の `tools/ja_locale.json` で上書きする。ディスパッチャーは `cwd=".dispatcher/"` で動くため、実際の呼び出しでは `--locale-json ../tools/ja_locale.json --template-repo ..` のように 1 段上を指す。詳細は `.dispatcher/CLAUDE.md` のコマンド例参照）。`instruction` フィールドが直接指定されている場合はそちらが優先され、本テンプレートは使われない（backward-compat）。**この直指定経路では上記「作業の進め方」節の ultracode 待機文も自動では入らない**ため、ultracode 許可タスクを直指定 instruction で派遣する場合は、dispatcher が待機文を body に prepend するか、reference の send_message を送らず send_keys のみを着手トリガにすること（[`.dispatcher/references/spawn-flow.md`](../../../../.dispatcher/references/spawn-flow.md) 3-5a 手順1）。

変数一覧 (helper 側で参照):

- `task_description` (必須): タスクの目的と期待する成果物
- `dir_setup` (必須): プロジェクト準備指示。窓口がパターン A/B/C を resolve 済みの文字列で渡す
- `branch_strategy` (必須): ブランチ戦略。worktree 配備で main 既定にすると誤誘導するため必須
- `verification_depth` (必須): `full` または `minimal`
- `constraints` (任意): 制約。省略時は "(なし)"
- `report_target` (任意): 完了報告先 peer 名。省略時は `secretary`
- `claude_md_filename` (任意): ワーカーが読む行動規範ファイル名。省略時は `CLAUDE.md`。claude-org 自己編集タスクでは `CLAUDE.local.md` を渡すこと（`references/claude-org-self-edit.md` 参照）

未知の変数キーは input_invalid として弾く。`verification_depth` が `full` / `minimal` 以外でも input_invalid。

<!-- AUTO-EXPAND-TEMPLATE-START -->
```
以下のタスクを遂行してください。詳細な行動規範は {claude_md_filename} に記載されています。

## タスク
{task_description}

## プロジェクトの準備
重要: あなたの作業ディレクトリは {claude_md_filename} に記載された絶対パスです。
まず `pwd` を実行し、{claude_md_filename} の作業ディレクトリと一致することを確認してください。
全てのファイル作成はこのディレクトリ内に限定。`..` への移動や claude-org 構造の再現は禁止です。

{dir_setup}

## ブランチ戦略
{branch_strategy}

## 作業の進め方
auto モードで直接作業してください。Plan モードは使用しないこと。
**ultracode 許可タスクのみ**（worker brief の「実装ガイダンス」に ultracode 使用許可がある場合）: **この peer message では本作業を始めない**。本作業は dispatcher が打鍵する `ultracode` send_keys kickoff（user turn）に応答して開始すること（その turn でのみ multi-agent workflow / Workflow tool が武装される。peer message / `check_messages` 経由の本文では武装しない）。許可が無ければこの指示でそのまま着手してよい。

## 制約
{constraints}

## 検証深度: {verification_depth}
- full: 既存テストスイート / lint / type-check 等の通常検証を green まで実行し、commit 完了後に codex CLI が available なら **`codex exec review --base origin/main -m gpt-5.5 -c model_reasoning_effort=medium`（review surface）**で差分セルフレビュー（直打ち長文プロンプト形は廃止。中小 diff で約2倍速・安全側パリティ同等。large diff では effort を上げない）。Blocker / Major は修正コミットを積んでから完了報告。**Codex round は既定上限 3**（brief の実装ガイダンスで別値指定があればそちら優先）: 上限到達時は round N+1 に自走で入らず、残指摘 + 自己評価（設計問題化か収束途中か）を添えて窓口に報告して停止（別問題が毎 round 出ても上限で止め、続行判断は人間）。同一指摘が 3 ラウンド消えない場合は上限前でも即設計問題として報告（別問題が各 1 round で順に解消する健全な収束とは区別）。Minor / Nit は残置（既知制限として明記）。**codex が available だが安全機構に掛かって review 処理を進められない skip は「codex 未導入 skip」と別扱い**（ゲート未成立であって codex clean ではない。回避の言い換えはしない）: 正式リカバリ = 新規セッション化（同一 worktree・commit を引き継いだ continuation spawn で review 再実行、完了報告に引き継いだ HEAD SHA を明記）、リカバリ不能なら「codex clean」と報告せず「ゲート未成立（safety block、HEAD=`<sha>`）」と明示。**ultracode（許可があるタスクのみ）**: kickoff で武装されていれば、実装と Codex 前のセルフレビュー収束に ultracode を使ってよい（最終の Codex ゲート（Blocker / Major ゼロ）は従来どおり維持。ultracode は前段であって置き換えではない）。武装されていなければ使わない。**完了報告には人間向け理解サマリを必ず含める**: (1) 最重要の変更点を効果順に N 個（目安 3〜5 個、各 1〜2 行）、(2) 人間が承認前に必ず目を通すべきファイル / hunk、(3) 設計判断とその理由（却下した代替案があれば 1 行）。窓口がコードを精読せずユーザーへの承認提示に使える土台にするため。**Knowledge 層プライバシー（`knowledge/raw/` 記録時、full モードのみ）**: `knowledge/raw/` および `knowledge/curated/` は公開 OSS リポジトリにコミットされる。operator の固有名・社内システム識別子・顧客データ・シークレット・社内 URL などの operator-private な内容を書き込んではならない。当該情報を含む学びは記録せず窓口（secretary）にエスカレーションすること。
- minimal: trivial fix のみ。Codex セルフレビュー・追加検証は禁止。完了報告は 1 行 (`done: <SHA> <files>`) で窓口に送信。

## 報告先
完了・進捗・ブロックは org-broker で `to_id="{report_target}"` に送信。push / PR 作成は窓口側で実施。

## SUSPEND 対応
"SUSPEND:" で始まるメッセージを受け取ったら作業を中断して状況を報告してください。
```
<!-- AUTO-EXPAND-TEMPLATE-END -->

このテンプレート本体は helper が読むため、上記マーカーコメントの位置と code fence は変更しないこと（変更時は `claude-org-runtime` 側のパース実装 (`claude_org_runtime.dispatcher.runner.load_instruction_template`) との整合も確認すること）。
