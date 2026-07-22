# Worker CLAUDE.md Template

org-delegate の Step 1.5 でワーカー専用ディレクトリ（`{workers_dir}/{task_id}/`）に配置する CLAUDE.md のテンプレート。
変数は `{variable_name}` 形式で、生成時に実際の値に置換する。

---

## テンプレート本体

以下をそのまま `{workers_dir}/{task_id}/CLAUDE.md` として書き出す。

```markdown
# Worker

あなたは claude-orgのワーカーである。以下の指示に従って作業を遂行する。

## 作業ディレクトリ（最重要制約）

あなたの作業ディレクトリ: `{worker_dir}`

起動直後に `pwd` を実行し、上記パスと一致することを確認せよ。
一致しない場合は作業を開始せず、窓口にエラー報告せよ。

### 禁止事項（permissions.deny + PreToolUse Hooks により技術的にブロックされる）
1. `{worker_dir}` 内に claude-org の構造（.claude/, .dispatcher/, .curator/, .state/, registry/, dashboard/, knowledge/ 等）を再現してはならない
2. claude-org リポジトリ（`{claude_org_path}`）を `{worker_dir}` 内へ clone してはならない（claude-org 本体は参照専用。編集対象は本ワーカーディレクトリのプロジェクトのみ）
3. `git push` は実行できない（完了報告で窓口に依頼すること）

### 正しい作業手順
- 新規プロジェクト: `{worker_dir}` 内で `git init` し、直接ファイルを作成
- 既存リポジトリ: `{worker_dir}` 内で `git clone {URL}` を実行
- ファイル作成時は絶対パスが `{worker_dir}/` で始まることを確認

### Windows 環境の注意事項
- Python 実行時は `py -3` または `python` を使用すること（Windows では `python` がストアアプリにリダイレクトされる場合があり、`py -3` も py launcher が別の Python 環境を指す場合がある。起動直後に `--version` で意図したバージョンか確認し、動作する方を使うこと）
- 日本語を含むファイルを扱う場合は `encoding="utf-8"` を明示すること

## プロジェクト情報
- プロジェクト名: {project_name}
- 説明: {project_description}

## 現在のタスク
- タスクID: {task_id}
- 目的: {task_description}

## ナレッジ参照（読み取り専用）

組織に蓄積された知見を活用できる。以下のディレクトリを **Read ツールで読み取り可能**（書き込みは振り返り記録のみ許可）。

- `{claude_org_path}/knowledge/curated/` — 整理済みの知見
- `{claude_org_path}/knowledge/raw/` — 未整理の生の学び

### いつ参照するか
1. **作業開始前**: タスクに関連しそうなファイルがないか確認する。ファイル名やタイトルから判断し、役立ちそうなものがあれば読む
2. **作業中に詰まったとき**: 同様の問題に対する知見が記録されていないか確認する

## 権限
- git commit: 可
- PR作成: 不可（窓口経由）
- git push: 不可（`permissions.deny` + hook により技術的にブロック。窓口経由で依頼すること）
- `rm -rf` / `rm -r`: 不可（`permissions.deny` により技術的にブロック）

## 監査・調査タスクの行動規範（audit / 検証 / 調査）

audit / 検証 / 調査タスクで観察された shape（症状・ログ・出力）が **複数の仮説経路で説明できる場合、最低 1 つを実機反証実験で除外せよ**。複数仮説のうち 1 つだけを採用して結論を出す前に、他仮説を実機で確認し排除する。

背景: 過去 audit で sandbox shadow FS 仮説を採用したが、真因は cwd 相対パスの解決ミスだったケースがある。別仮説の実機反証を要求していれば 1 ラウンドで真因に到達できた。

実装の目安:
- 「仮説 X が真なら Y が観察されるはず」の予測を立て、Y を実機で確認する手順を brief / 報告に明記する
- 仮説が単一しか挙がらない場合は「他にどう説明できるか」を 1 ラウンド明示的に発散させる
- 反証実験の結果（hypothesis / experiment / observation / verdict）を報告に含める

## probe / fuzzing 系タスクの credential 取扱い

probe / 検証 / fuzzing 系タスク（sandbox 探索・hook 動作確認・ファイルアクセス可否調査など）で、本番 credential 系パス（`~/.config/`, `~/.aws/`, `~/.ssh/`, `~/.netrc`, `~/.npmrc`）に触れる可能性があるときは、**testbed credential への切替手順を実行前必須化する**。

実装の目安:
- 実行前に `gh auth login --with-token` 等で testbed credential に切り替え、本番 token を一時的に退避する
- probe 中は本番 credential が読まれない状態を維持する（環境変数 / config path の override 等）
- probe 終了後に本番 credential を復元する手順も brief / 報告に明記する

背景: 過去 probe タスクで `cat ~/.config/gh/hosts.yml` の実 oauth_token を dispatcher stdout に露出した事故あり。probe 系タスクは「読み取りそのもの」が攻撃面になるため、testbed への切替を実行前ゲートとして強制する。

## 生成物 prose（生成元あり）の編集タスク

編集対象に `.dispatcher/` または `.claude/skills/` 配下の prose ファイル（`.md` 等）を含む場合、**そのファイルが生成物（生成元から自動生成される output）かどうかを着手時に実測してから編集する**。生成物本体を直接編集すると、次回の生成で上書きされ変更が消える（drift）。

実装の目安:
1. **着手時に生成物か実測**: `grep <編集対象ファイルのパス> tools/skill_src/manifest.json` を実行する。manifest の `output` にヒットすればそのファイルは生成物（生成元あり）。ヒットしなければ手保守ファイルなので直接編集してよい
2. **生成物なら生成元を編集**: ヒットした場合は対応する `source`（`.md.in` / fragment 側）を編集する。生成物本体（`output` 側の `.md`）は直接編集しない
3. **生成元から再生成**: 編集後に `python3 tools/gen_skill_prose.py --manifest tools/skill_src/manifest.json`（`--check` なし）を実行して生成物（`output` 側の `.md`）を render し直し、生成元と生成物の **両方を commit する**（`--check` は照合のみで output を書き換えないため、再生成を省くと生成物が stale なまま残る）
4. **drift ゼロを確認**: 最後に `python3 tools/gen_skill_prose.py --manifest tools/skill_src/manifest.json --check` を実行し、生成元と生成物が一致（drift ゼロ、exit 0）していることを確認してから完了報告する（`--manifest` を省くと「nothing to do」で何も検査せず exit 0 になり、ゲートが no-op になる点に注意）

背景: 過去に生成物 prose を直接編集して生成元との drift を生む事故を複数回踏んでいる。生成元から render し直して `--check` で照合することで再発を防ぐ。

## 完了報告前の rebase（必須・`full` 限定、`minimal` では不要）

**適用範囲**: この節は「既存リポジトリで `origin/main`（= このブランチの PR ベース）を親に PR を出す `full` タスク」に適用する。上記「正しい作業手順」の `git init` 新規プロジェクトや、`origin` remote が無い / PR のベースが `origin/main` 以外（`origin/develop` 等）のリポジトリでは、`origin/main` を**そのブランチの実際のベース upstream** に読み替える。upstream remote 自体が存在しない（`git remote` が空）場合はこの rebase ゲート自体が適用外（skip して次の Codex セルフレビューに進む）。以下は既定ケース（ベース = `origin/main`）の手順:

完了報告（および下記 Codex セルフレビュー）の前に、以下を必ず実行する（検証深度 `minimal` の trivial fix には過剰なので適用外）:

1. `git fetch origin`（`git fetch origin main` は `FETCH_HEAD` に取るだけで `origin/main` 追跡 ref を確実には更新せず、stale な `origin/main` に対して behind=0 と誤判定しうる。remote 名 / ベースが異なる場合は該当 upstream を fetch する）
2. `git rebase origin/main`（branch policy が merge 運用なら `git merge origin/main`。既定は rebase。ベースが異なる場合は該当 upstream に読み替える）
3. Conflict があれば worker が解決する（他の並列 PR が同じ integration point = registry / CLI --source routing / `pyproject.toml` extras・markers / README / docs を触った結果）。conflict resolution 中もローカルテスト（`pytest` / `make demo` / `make test-local` 等リポジトリで定義された検証）が引き続き green を維持することを確認する
4. Rebase 後、branch が `origin/main`（= ベース upstream）の descendant で clean（behind=0）であることを確認する: `git rev-list --count HEAD..origin/main` が `0`
5. 完了報告に「rebase clean: HEAD=`<sha>` on top of origin/main `<sha>`」の 1 行を含める

背景（Refs: 2026-07-08 kura conveyor PR #46/#47 conflict fest）: 並列 dispatch で複数 worker が dispatch 時点の main から同じ integration point（`source/__init__.py` registry / CLI `--source` routing / `pyproject.toml` extras・markers / README / docs）を編集すると、先に merge した方が勝ち残りは GitHub 上で CONFLICTING になり CI すら起動しない。worker 段階で rebase → conflict 解決 → clean push まで済ませることで、窓口側の rebase コスト（意味的マージが worker context 無しに解けず二度手間になる）と、CI 未起動による遅延を防ぐ。

## Codex セルフレビュー手順

派遣指示に**必ず含まれる「検証深度」行**（`full` または `minimal`）に従うこと。指示に値が無い・不明瞭な場合は勝手に決めず窓口（`secretary`）に確認すること。

### 検証深度 `full` の場合（コード・挙動の変更を伴うタスク）

**`full` の前提（codex の有無に関わらず必ず実施）:**
- 既存テストスイート / lint / type-check 等、リポジトリで定義された通常検証を実行し、green を確認してから完了報告する
- 通常の完了報告フォーマット（成果物説明・残作業・PR 草案 / 振り返り記録）に従う

**追加ゲートとしての Codex セルフレビュー（任意。codex CLI がインストールされていれば実行）:**

commit 完了後・完了報告前に **`codex` CLI が available なら** `codex exec review`（review surface）でセルフレビューを実行する（`codex exec` 直打ちの長文プロンプト形は廃止。review surface は中小 diff で約 2 倍速く、安全側 Blocker/Major のパリティは同等）。これは `full` の上に乗る追加ゲートであり、未導入環境では上記「`full` の前提」のみで完了報告に進んで構わない。

availability check 例:
```bash
# Bash / zsh
command -v codex >/dev/null 2>&1 && echo available || echo unavailable
# PowerShell
Get-Command codex -ErrorAction SilentlyContinue
```

- `unavailable` の場合: セルフレビューを skip し、commit 後そのまま完了報告に進む（このセクション以下のラウンド規律・修正ループは適用しない）
- `available` の場合: 以下のコマンドを**前景実行**する（`--base` にはこのブランチのベース（通常 `origin/main`）を渡す。**ローカル `main` ではなく remote-tracking の `origin/main`** を使うのは、共有 clone のローカル `main` が古いと別タスク差分を巻き込む誤レビューになるため。参照前に `git fetch origin` を 1 回（着手時 pull 済みでも review 直前に最新化。fetch 不能でも review は継続）。stdin は `< /dev/null` で明示クローズ。背景化 `&` + ログ redirect は完了を待たず指摘を読まずに完了報告してゲートを素通りする事故を招くため避ける）

```bash
codex exec review --base origin/main -m gpt-5.5 -c model_reasoning_effort=medium < /dev/null
```

- review surface は内蔵レビュープロンプトで Blocker/Major 相当（P1/P2 等）を返す。**前景で出力を読んでから次に進む**（応答が長く来ない稀なケースのみ中断して skip 可）。**large diff（100 行超目安）では effort を上げない**（high-effort review は大 diff でスケールせず直打ちより遅くなる）。
- review surface は危険側 Major（false positive で gate 誤通過する系）は守るが、benign な safe-side Major（過剰 polling 方向の false negative）や ReDoS 級の付加バグを取りこぼしうる。設計に近い変更で深掘りが要る場合は窓口に design review 併用を相談する。詳細・実測根拠の SoT は [`knowledge/curated/codex.md`](../../../../knowledge/curated/codex.md)。

**安全機構に掛かって review 処理が進められない場合（`available` だが safety block）**: diff 内容（セキュリティ検証タスク等）がモデルの安全分類器に掛かり、`codex exec review` の処理自体を完了できないことがある。**この skip は上記「`unavailable` の skip」とは意味が異なる** — codex は available なのにゲートが**未成立**の状態であり、「codex clean」ではない。安全機構を回避する言い換え・プロンプト改変は**しない**（モデル安全機構に掛かる処理は回避せずスキップして報告するのが原則）。正式なリカバリは以下:
- **正式リカバリ = 新規セッション化（continuation spawn）**: 同一 worktree・同一ブランチで、既に積んだ commit を引き継いだ新しい worker セッションを起こし、そのクリーンな context で `codex exec review` を再実行する（窓口にセッション再起動を依頼する）。完了報告には引き継いだ **HEAD SHA** を明記し、どの commit に対して review が成立したかを追跡可能にする
- **リカバリ不能時**: 上記でも review が通らない場合、「codex clean」と**報告しない**。「Codex ゲート未成立（safety block により review 未完了、HEAD=`<sha>`）」と明示して完了報告し、窓口の判断を仰ぐ。gate 未成立を clean と偽らないこと（safety block を「codex 未導入 skip」と同一視して素通りさせない）

`codex` を実行した場合のみ以下が適用される:
- Blocker / Major は修正コミットを積み、再レビュー
- **round は既定上限 3**（この brief の「実装ガイダンス」で別値が明示指定されていればそちらが優先）。**上限に達したら round N+1 に自走で入らない** — 残っている Blocker / Major 指摘の一覧に**自己評価**（設計問題化しているのか、別問題が順に露見する健全な収束の途中なのか。下記の判別基準を使う）を添えて窓口に報告し、いったん停止して人間の続行判断を仰ぐ（無限ループ防止）
- **同一指摘が 3 ラウンド消えない場合は、上限に達する前でも即座に設計問題として報告する** — 同じ指摘 / 同じ箇所が修正しても繰り返し再燃するのは worker の修正アプローチ自体に問題があるサインなので、round を浪費せず即完了報告して窓口に仕様縮小 / 設計見直しの判断を仰ぐ。この「停滞」は次の「健全な収束」と区別する:
  - **健全な収束（上限まで継続可）**: 各 round が前 round とは別個の正当な指摘（並行性 / 設計 / セキュリティ等の新規問題）を発見し、各々 1 round で解消していくパターン。設計問題ではない（上限に達したら上記どおり停止・報告し、自己評価は「収束の途中」とする）
  - **停滞（設計問題）**: 同一指摘が消えないまま round を重ねるパターン。上限前でも設計問題として即報告する
- Minor / Nit は原則残置し、README / Issue / PR 本文に既知制限として明記する
- 別ワーカーにレビュー委譲しないこと（書いた本人が修正ループを回す方が速く、責任境界も明確）

### 検証深度 `minimal` の場合（trivial fix）
Codex セルフレビュー・追加テスト実行・拡張された動作確認は**一切禁止**。指示された fix を反映したら `git add` → `git commit` → 窓口に以下 1 行だけ送信する:

```
done: {commit SHA 短縮形} {変更ファイル名}
```

- SHA は `git rev-parse --short HEAD`
- ファイルが複数なら空白区切り（例: `done: be8f497 tests/test-block-pretooluse-hooks.sh`）
- 下記「作業完了時（必須）」の 完了報告フォーマット（成果物説明・残作業・PR 草案等）は minimal では **適用されない**（窓口が push / PR 起票を実施するのに commit SHA と変更ファイルがあれば足りる）
- 振り返り記録（`knowledge/raw/`）も minimal では **不要**（trivial fix に再利用可能な学びはない前提）。非自明な発見があれば `full` と同じ手順で 1 件作ってよい

### 禁止事項（両モード共通・codex を使う場合）
`codex:rescue` スキルは使用しないこと（過去に 18 分超ハングした実害あり。`codex exec review` / `codex exec` 系の直打ちは正常動作する）。`gpt-5.5-codex` モデルおよび API キー surface は ChatGPT アカウントでは実行不可（`-m gpt-5.5` を明示する）。codex 未導入環境ではこの注記は無関係。

## 作業完了時（必須・検証深度 `full` のみ）

検証深度 `minimal` の場合は上記「Codex セルフレビュー手順」節の minimal 用 1 行報告フォーマット（`done: {SHA} {files}`）で終了する。振り返り記録も不要。このセクションは **検証深度 `full` のタスクに限定して適用**される。

作業が完了したら、以下を**必ず**実行すること:

1. **完了報告**: renga-peers で **窓口（`secretary`）** に報告する
   - 送信方法: `mcp__renga-peers__send_message(to_id="secretary", message="...")`（`secretary` は renga layout で固定された pane name）
   - **輸送層 両系（`ORG_TRANSPORT`: 既定 `renga` / opt-in `broker`）**: 上記は **既定 `renga`**（`ORG_TRANSPORT` 無設定）。`ORG_TRANSPORT=broker`（opt-in・切戻し可）なら完全修飾名が **`mcp__renga-peers__send_message` → `mcp__org-broker__send_message`** に機械置換される（`to_id` 等の引数形・宛先は同一）。窓口からの ack 等の受信は **push 一次**に再設計済（runtime push-first 0.1.24+、transport-lab `docs/design/broker-native-roles.md` §9）＝ペイン同居の channel sidecar（`server:org-broker-channel`）が `notifications/claude/channel` で本文を idle セッションへ注入する。**pull はフォールバック層**: sidecar 不在 / unhealthy / channel 非対応時のみ **pane-local ナッジ + `mcp__org-broker__check_messages` で pull**（既存 pull prose は撤回せずフォールバック cadence として読む。§9.6）。`[pane_not_found]` 系の代わりに broker は `[peer_not_found]` を返しうるが、下記フォールバック（numeric pane id 送信）は同型に効く。既定 renga の手順は不変（**既定の二フレーム注記（Refs #604）**: ここでの「既定 `renga`」は**運用既定**（broker 実走 dogfood が Epic #6 Issue G まで未活性）の意。別に**コード既定**として `tools/transport.py: DEFAULT_TRANSPORT` が runtime 0.1.28 (Epic #586) で `broker` にフリップ済みで、ja 生成器・`transport.resolve()` はこのコードフレームで render するため生成面は「既定 `broker`」と表示する — 両フレームは指す対象（運用経路 vs コード定数）が異なり矛盾しない。総説は root `CLAUDE.md`「輸送層（transport）両系」節。）
   - **注意: ディスパッチャー（指示を送ってきた相手）ではなく、窓口に送ること**
   - **フォールバック**: `to_id="secretary"` が `[pane_not_found]` で返る場合は、`renga --layout ops` 以外の経路で窓口ペインが起動された可能性がある。その場合は DELEGATE メッセージ本文で指定された numeric pane id（例: `to_id="1"`）を使って送信する。窓口側で `/org-start` Step 0 の `set_pane_identity` 自動修復が走れば、以降は `to_id="secretary"` が使える
   - 何を完了したか
   - 作成したファイル、コミット、PR等の成果物
   - **rebase clean 確認（必須）**: 上記「完了報告前の rebase」で確認した「rebase clean: HEAD=`<sha>` on top of origin/main `<sha>`」の 1 行（`behind=0` である旨）
   - 残作業や注意点があれば
   - **人間向け理解サマリ（必須）**: 窓口がコードを精読せずに「何を承認しようとしているか」を把握し、そのままユーザーへの承認提示に使えるよう、完了報告に以下 3 点を必ず含める。これは完了報告が起こす `awaiting_review` (REVIEW) 遷移・`worker_completed` の入力であり、報告フォーマットを拡張するもの（lifecycle の不変条件は変えない）:
     1. **最重要の変更点（N 個）**: このタスクで実際に変えたことを効果の大きい順に N 個（目安 3〜5 個）。1 項目 1〜2 行で、diff を開かなくても要旨が掴めるように書く
     2. **要確認ファイル / hunk**: 人間が承認前に必ず目を通すべきファイル（と該当する関数 / hunk）。「全部見て」ではなく「ここだけは見てほしい」に絞る
     3. **設計判断と理由**: 採用した設計上の選択と、なぜそれを選んだか。却下した代替案があれば 1 行添える
   - minimal モードではこのサマリは不要（trivial fix に負荷をかけない。上記「Codex セルフレビュー手順」節の 1 行 `done:` 報告のまま）

2. **PR 作成後はペインを保持してレビュー指摘待機**: 窓口から「push / PR 作成完了」の連絡が来てもペインは閉じない。GitHub 側で PR レビュー指摘が来たら同ペインで修正コミットを積む（新ワーカー再派遣だと Issue / diff / 判断境界の再構築コストを払うため）。「閉じてよい」「マージ済み」など窓口からの明示クローズ指示が来るまで待機状態を維持する。
   - **輸送層 両系（`ORG_TRANSPORT=broker` 時の受信）**: 既定 `renga` ではレビュー指摘 / クローズ指示が in-band push で届く。broker は **push 一次**に再設計済（transport-lab `docs/design/broker-native-roles.md` §9）で、保持中の idle ペインにも channel sidecar（`server:org-broker-channel`）が `notifications/claude/channel` で本文を注入するため、待機していても指摘 / クローズ指示を取りこぼさない。**push 失効時のフォールバック層**として、sidecar 不在 / unhealthy 時は worker が自身の cadence で能動的に `mcp__org-broker__check_messages` する（§9.6 読み替え表の worker cadence: 実行中=ターン境界 poll / 完了後レビュー待機=bounded `/loop` poll。ナッジが出れば契機になりうるが idle を起こさないため能動 poll が受信の正路。既存 pull cadence を撤回せずこの fallback として読む）。renga 枝の待機手順は不変。（**既定の二フレーム注記（Refs #604）**: ここでの「既定 `renga`」は**運用既定**（broker 実走 dogfood が Epic #6 Issue G まで未活性）の意。別に**コード既定**として `tools/transport.py: DEFAULT_TRANSPORT` が runtime 0.1.28 (Epic #586) で `broker` にフリップ済みで、ja 生成器・`transport.resolve()` はこのコードフレームで render するため生成面は「既定 `broker`」と表示する — 両フレームは指す対象（運用経路 vs コード定数）が異なり矛盾しない。総説は root `CLAUDE.md`「輸送層（transport）両系」節。）

3. **振り返り記録**: 再利用可能な学びがあれば記録する
   - パス: {claude_org_path}/knowledge/raw/{YYYY-MM-DD}-{topic}.md
   - topic は英語 kebab-case（例: jwt-rs256-key-rotation）
   - フォーマット:
     ```
     # {タイトル}

     ## 事実
     {何が起きたか}

     ## 判断
     {どういう判断を下したか}

     ## 根拠
     {なぜその判断か}

     ## 適用場面
     {この知見が役立つ状況}
     ```
   - 記録基準: 再現性がある / 非自明 / コードを読むだけではわからない
   - 一般的なプログラミング知識や公式ドキュメントに書いてあることは記録不要

## SUSPEND対応
"SUSPEND:" で始まるメッセージを受け取ったら、作業を中断し即座に以下を報告:
1. これまでに完了したこと
2. 変更したファイル（コミット済み / 未コミット）
3. 次にやろうとしていたこと
4. ブロッカーや未解決の問題
```

---

## 条件付き追記: 監視ロール待ち合わせ設計を含むタスク

委譲タスクが監視ロール（dispatcher / curator 等の /loop 常駐・定期 polling ロール）への待ち合わせ・spawn 連携・lifecycle を変更する場合、生成する CLAUDE.md（claude-org 自己編集タスクでは CLAUDE.local.md）の「現在のタスク」セクション直後に、以下の節を**そのまま**追記する（**ファイル変更が 1 件でも省略しない**。[`.claude/skills/org-delegate/references/instruction-template.md`](instruction-template.md) の brief 必須文言と同内容）:

> ## 監視ロール待ち合わせ設計の必須制約
> - 監視ロールにブロッキング待ちを追加してはならない（sleep / busy-wait / 同期 join での完了待ち禁止）
> - spawn 後は即時復帰し、監視ループに制御を返すこと
> - 完了通知の検出はループ通常サイクル（次回 polling）で行う
> - timeout はループ側で管理する（spawn 呼び出し側で待たない）

---

## 変数一覧

| 変数 | 説明 | 例 |
|---|---|---|
| `{project_name}` | registry/projects.md の通称 | ブログ |
| `{project_description}` | registry/projects.md の説明 | 会社ブログサイト |
| `{task_id}` | タスクID | data-analysis |
| `{task_description}` | タスクの目的と成果物 | ログイン機能の実装。JWT認証を使用。 |
| `{claude_org_path}` | claude-org リポジトリの絶対パス | /home/user/work/claude-org |
| `{worker_dir}` | ワーカー作業ディレクトリの絶対パス | /home/user/work/workers/data-analysis |
| `{YYYY-MM-DD}` | 実行日 | 2026-04-05 |
