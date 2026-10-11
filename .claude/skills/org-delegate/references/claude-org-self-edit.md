# claude-org 自身を編集するタスクの特例

目次: 1. self-edit ロールの settings 生成 / 2. ワーカー指示は `CLAUDE.local.md`（完了時の cleanup 責務を含む） / 3. live repo worktree / 4. 「ルート CLAUDE.md は無視」の明示 / 5. `.claude/**` 編集の send_keys 事前承認ハンドシェイク

> **前提（Pattern 判定）**: 対象ファイルが gitignored（例: `docs/internal/`, `notes/`, `tmp/` 配下の内部メモ）の場合は、SKILL.md **Step 0.7「ターゲットファイル gitignore 事前チェック」**により **Pattern C 強制**となる（Step 1 のディレクトリパターン判定基準には到達しない）。本ドキュメントの特例（hook 除外・`CLAUDE.local.md`）は Pattern B / C いずれでも適用するが、Pattern C の場合 worktree は作らないため WORKER_DIR は対象ファイルにアクセスできる既存リポジトリ root を指定する。以下は Pattern B（tracked ファイル編集）を主に想定した手順。

claude-org リポジトリのスキル / ドキュメント / 設定を編集するワーカーを派遣するとき、通常の worktree 準備のままでは以下の事故が発生する:

- `block-org-structure.sh` hook が `.claude/skills/` などへの Edit / Write を拒否する（`bypassPermissions` モードでも exit code 2 により確認プロンプトが出る）
- ルートの `CLAUDE.md` は Secretary（窓口）用の指示なので、ワーカーがこれを読んで「あなたは窓口」と誤認する

このため claude-org 自己編集タスクでは以下 3 点が通常手順に加わる。1 と 2 は `tools/gen_delegate_payload.py apply` が self-edit 判定時に自動で行い、窓口が手で行う手順は無い。

## 1. `claude-org-self-edit` ロールで settings.local.json を生成する

ワーカー `.claude/settings.local.json` は `claude-org-runtime settings generate` で **schema-driven に生成**する（手書き編集は窓口の `permissions.deny` で禁止されている）。self-edit タスクでは `gen_delegate_payload.py apply` が `--role claude-org-self-edit` を付けて呼ぶ（[`tools/gen_delegate_payload.py`](../../../../tools/gen_delegate_payload.py) の settings 生成部）。

`claude-org-self-edit` ロールは schema 上で `block-org-structure.sh` hook が **既に除外**された状態で定義されている（`Edit|Write` / `Bash` matcher 双方）。`check-worker-boundary.sh` / `block-git-push.sh` などその他の hook は通常どおり残る。生成済み JSON を手で再編集してはならない（drift CI が fail する）。新しい role を追加する場合は、**ja の `tools/org_extension_schema.json`（drift validator `tools/check_role_configs.py` の org-extension 側の正典）と `claude-org-runtime` にバンドルされた merged role schema（generator の正典）の両方に追加する PR が必要**: ja 側だけでは generator が新 role を知らず生成に失敗し、runtime 側だけでは drift CI が fail する。両者の一致は [`tools/check_runtime_schema_drift.py`](../../../../tools/check_runtime_schema_drift.py) が CI で検査する。framework 側の schema 形（`worker_roles` の許容形状定義そのもの）を変える場合は `claude-org-runtime` 側のみで完結する。

## 2. ワーカー指示は `CLAUDE.md` ではなく `CLAUDE.local.md` に書く

ルートの `CLAUDE.md` は Secretary 用の指示なので、ワーカー用 CLAUDE.md で上書きしてはならない（他ロールが壊れる）。
ワーカーへの指示は `{worker_dir}` 直下の `CLAUDE.local.md` に書く（git 管理外。出力先の切り替えは [`tools/gen_worker_brief.py`](../../../../tools/gen_worker_brief.py) と `gen_delegate_payload.py` が自動で行う）。Pattern B（tracked ファイル編集）なら `{worker_dir}` は worktree 直下、Pattern C 強制（gitignored サブモード）なら `{worker_dir}` は対象ファイルにアクセスできる既存リポジトリ root を指す。

> **Pattern C 強制での同 repo 排他**: `CLAUDE.local.md` と `.claude/settings.local.json` はファイル名固定なので、同一 repo root に対する Pattern C 強制ワーカーは **2 本以上同時起動しない**（先行ワーカーの指示・権限を上書きするため）。窓口側で順次化すること（A/B との競合だけでなく C/C も排他。この規則の記載はここだけ）。

Claude Code は同一ディレクトリの `CLAUDE.md` と `CLAUDE.local.md` の両方を読み込むため、ワーカーには両方が見える。brief 本文・instruction の「CLAUDE.md」表記は生成器が `{claude_md_filename}` 変数（[`.claude/skills/org-delegate/references/instruction-template.md`](instruction-template.md)）で `CLAUDE.local.md` に置き換えるので、窓口が手で読み替える必要は無い。ルートの `CLAUDE.md`（Secretary 指示）はいかなる場合も上書きしない。

### ワーカー完了時の Secretary 側 cleanup 責務

`CLAUDE.local.md` は **生成して終わりではなく、ワーカー完了時に Secretary が回収する**責務まで含む。回収経路はパターンで分かれる:

- **Pattern B（`live_repo_worktree`）**: ブリーフは worktree 直下（`{claude_org_path}/.worktrees/{task_id}/CLAUDE.local.md`）にあるので、close 時の `git -C {claude_org_path} worktree remove .worktrees/{task_id}` がディレクトリごと回収する。個別削除は不要。
- **Pattern C（`gitignored_repo_root`）**: `worker_dir` が claude-org repo root 自身なので、worktree remove も dir 削除も効かない。`{claude_org_root}/CLAUDE.local.md` を **個別に削除しないと残留**し、次回 `/org-start` で Secretary が「窓口かつワーカー」という矛盾した role identity（冒頭の「あなたは窓口ではなくワーカーである」）を context に読み込む。gitignored なので CI でも検出されない。

Pattern C の個別削除は close phase の責務で、判定条件・呼び出しコード・引数（`worker_dir_abs=` の明示）の正本は [`.claude/skills/org-pull-request/SKILL.md`](../../org-pull-request/SKILL.md) 2b-ii の Pattern C 特例 cleanup（実体は [`tools/run_complete_on_merge.py`](../../../../tools/run_complete_on_merge.py) の `cleanup_pattern_c_local_md`）。`.claude/settings.local.json` の自動削除は対象外（worker 由来 / Secretary 由来の切り分けが要るため）。

## 3. Pattern B の worktree base は **Secretary の live repo** に置く（live_repo_worktree variant）

claude-org 自己編集タスクで Pattern B（worktree）を採る場合、worktree base は **通常パターンの `{workers_dir}/{project_slug}/.worktrees/{task_id}/` ではなく Secretary 自身の live repo の `.worktrees/`** に置く:

```
{claude_org_path}/.worktrees/{task_id}/
```

理由（明文化までの経緯は [`.claude/skills/org-delegate/references/rationale.md`](rationale.md) §12）:

- **単一 `.git/` を Secretary と worker が共有**するため、push / pull の二段クローン同期（Secretary 側 clone と workers 側 clone の間で `git pull` を挟む手数）が要らない
- Secretary の repo がそのまま canonical local clone として機能し、追加の indirection が無い
- `git worktree list` から live worker branch が常に見えるので、Secretary が状態確認するときに `cd` が要らない

通常 Pattern B（self-edit ではない）と本特例の使い分け:

| 条件 | worktree base | `pattern_variant` |
|---|---|---|
| Pattern B + `role == claude-org-self-edit` | `{claude_org_path}/.worktrees/{task_id}/` | `live_repo_worktree` |
| Pattern B + `role == default`（通常プロジェクト） | `{workers_dir}/{project_slug}/.worktrees/{task_id}/` | `null` |

`tools/resolve_worker_layout.py` は **Pattern B かつ self-edit role** のとき自動的に `pattern_variant='live_repo_worktree'` を選び `worker_dir` を上記 live repo パスに設定する。TOML `[worker]` ブロックで `pattern_variant='live_repo_worktree'` を明示しても同じ結果になる。

## 4. `CLAUDE.local.md` 冒頭で「ルート CLAUDE.md は無視」を明示する

この明示は self-edit 用 brief テンプレート [`tools/templates/worker_brief_self_edit.md`](../../../../tools/templates/worker_brief_self_edit.md) の冒頭に固定で入っており、生成器が必ず出力する（窓口が書く手順は無い）。この明示がないと、ワーカーがルート CLAUDE.md を先に読んで Secretary として振る舞い始める（/org-start の実行を促す等）。

## 5. root `.claude/**` 編集タスクの send_keys 事前承認ハンドシェイク

### スコープ境界

本節の対象は **claude-org root の `.claude/**`（self-edit）のみ**:

- **対象**: claude-org repo root 直下の `.claude/**` への Edit / Write を含む委譲（**深さを問わず全て対象**）。直下の `.claude/settings.json`・`.claude/skills/**/SKILL.md`（`.in` ソース含む）本体だけでなく、`.claude/skills/**/references/**` の brief 規範 prose も等しく対象とする。とりわけ全 worker の brief 規範を形作る高影響テンプレート — [`.claude/skills/org-delegate/references/worker-claude-template.md`](worker-claude-template.md) / [`.claude/skills/org-delegate/references/instruction-template.md`](instruction-template.md) / [`.claude/skills/org-delegate/references/ack-template.md`](ack-template.md) / 本ファイル自身（[`.claude/skills/org-delegate/references/claude-org-self-edit.md`](claude-org-self-edit.md)） — は必ずハンドシェイク対象に含める（これらは例示であり、線引きの正は `references/**` を含む root `.claude/**` 全体。列挙外のファイルも対象）。worker が承認無しで自分の brief 規範を書き換える anti-laundering 穴を塞ぐためであり、過去に `references/` 配下の brief テンプレート編集がハンドシェイク不発で無ゲート成功した（発生事例 #612）。**本節（スコープ境界）の定義自体の編集もハンドシェイク対象**とし、worker がゲート定義を自己縮小すること（`references/**` を対象から外す等）を禁止する。
- **対象外**: `.dispatcher/` / `.curator/`（別ロールの runtime ディレクトリであり `.claude/` ではない）、および worker dir 生成物の `.claude/settings.local.json`（`claude-org-runtime settings generate` が配置する生成物で、worker 自身が編集することはない）

> **正準定義（単一 SoT）**: ハンドシェイク必須パスの正準定義は本節（スコープ境界）が単一の SoT である。[`.claude/skills/org-delegate/SKILL.md`](../SKILL.md) の send_keys 事前承認の記述は本節への back-reference（`§5 を一次参照`）であり、対象パスの線引きを独立に再定義しない。

### 背景（2 層ガード）

`.claude/` 配下への書き込みは通常 2 層でガードされる: `block-org-structure.sh` hook は Edit / Write に限り通すが、auto-mode 分類器が「ユーザー承認の無い `.claude/` 編集」をブロックする。**ただし self-edit ワーカー（`claude-org-self-edit` role）ではこの hook ガードが存在しない**: `block-org-structure.sh` は role 定義で drop され、残る `check-worker-boundary.sh` は WORKER_DIR（Pattern B の live-repo worktree。`references/**` を含む）内を一律許可し、生成された `settings.local.json` も `.claude/**` への Edit / Write に allow / deny / ask を一切持たない。したがって self-edit ワーカーにとって**本ハンドシェイク（分類器を通す send_keys 承認）が `.claude/**` 編集の唯一のゲート**であり、上記スコープ境界の線引き精度がそのままゲート網羅性になる（`references/**` の取りこぼしが無ゲート編集を許した直接原因）。分類器の承認は **send_keys による端末入力（worker の会話に user message として届く形）でのみ通る**。peer message（`send_message`）は user 入力にならないため承認として機能しない。

> **輸送層 両系（`ORG_TRANSPORT`: 既定 `renga` / opt-in `broker`）**: **輸送層の切り替えは [`tools/self_edit_approval.py`](../../../../tools/self_edit_approval.py) が `tools/transport.py` の `resolve()` 経由で自動的に行う**（`renga` なら renga CLI 打鍵、`broker` なら broker ペインの実体である detached tmux session への `tmux send-keys` 打鍵）ので、窓口が transport を見て呼び分ける必要はない。以下はその内側で何が起きているかの説明である。本ハンドシェイクの `send_keys` は **既定 `renga`**（`ORG_TRANSPORT` 無設定）で `mcp__renga-peers__send_keys`。`ORG_TRANSPORT=broker`（opt-in・切戻し可）では **`mcp__renga-peers__send_keys` → `mcp__org-broker__send_keys`** に機械置換される（raw キー入力 = 端末への承認文入力という性質・引数形は同一なので、承認文が user message として届き分類器を通る論理は両系で不変）。なお spawn 時の初回承認プロンプト自体は、broker では Claude Code の **folder-trust プロンプト + channel sidecar の dev-channel 承認（「Load development channel?」、spawn-flow 3-3b 再導入）の 2 段**になる（renga は dev-channel のみ。push 一次採用に伴う加算であり置換ではない。設計 transport-lab `docs/design/broker-native-roles.md` §9.5）が、それは本節の「`.claude/` 編集の事前承認」とは別レイヤー（spawn 儀式の差。pane-layout / spawn-flow 側）。詳細は [`docs/contracts/backend-interface-contract.md`](../../../../docs/contracts/backend-interface-contract.md) Surface 8（ratified 2026-06-14。push 一次への additive 改訂 S3 が ratified 済み（2026-06-15）・既存 ratified 本文不変更）と [`.claude/skills/org-delegate/references/renga-error-codes.md`](renga-error-codes.md) の broker 節を参照。既定 renga の手順は不変（broker は加算）。（**既定の二フレーム注記（Refs #604）**: ここでの「既定 `renga`」は**運用既定**（broker 実走 dogfood が Epic #6 Issue G まで未活性）の意。別に**コード既定**として `tools/transport.py: DEFAULT_TRANSPORT` が runtime 0.1.28 (Epic #586) で `broker` にフリップ済みで、ja 生成器・`transport.resolve()` はこのコードフレームで render するため生成面は「既定 `broker`」と表示する — 両フレームは指す対象（運用経路 vs コード定数）が異なり矛盾しない。総説は root `CLAUDE.md`「輸送層（transport）両系」節。）

### ハンドシェイク（固定手順）

deadlock（worker が届かない承認を待ち続ける）と空打ち Enter（承認文の無い送信）を防ぐため、以下に固定する:

1. **窓口**: ディスパッチャーから `DELEGATE_COMPLETE` を受信したら、SKILL.md Step 5 の挨拶送信に**続けて**、承認ハンドシェイクを [`tools/self_edit_approval.py`](../../../../tools/self_edit_approval.py) の `send` で 1 コマンド実行する。**手で 3 段の tool call を打たない**（理由は直後の「なぜ 1 コマンドなのか」）:
   ```bash
   python3 tools/self_edit_approval.py send \
     --task {task_id} \
     --file {対象ファイル 1} \
     --file {対象ファイル 2}
   # 対象ペインは既定で worker-{task_id}。別タブ等で名前解決が怪しいときだけ --pane-id <数値 id> を渡す。
   # broker 実走時のみ --target %N が必須（下記）。
   # 送信前に文面だけ確認したいときは --dry-run。
   ```
   承認文の必須 3 要素 — **対象ファイルの列挙** / **task_id** / **「窓口経由のユーザー承認」の明記** — は**ツールが組み立てる**ので、窓口が文面を書き起こすことはない（`--file` が 0 個なら exit 2 で拒否される。列挙の無い承認は何も承認していないため）。輸送層は `tools/transport.py` の `resolve()` で解決され、`renga` なら renga CLI、`broker` なら tmux 打鍵に自動で切り替わる（`broker` の場合のみ **`--target %N` の明示が要る**: 論理名 `worker-{task_id}` は broker 自身のペイン台帳にしか無く tmux 側に照合材料が無いため、`list_panes` で読んだ pane id を渡す。明示が無ければ**打鍵前に** exit 2 で止まる）。なお **broker は transport であって端末ではない** — daemon が駆動する terminal adapter が `tmux` のときだけ CLI 打鍵経路があるので、ツールは `daemon.json` の `backend` を読んで確認し、`wezterm` / `herdr` や判定不能なら**打鍵せず** exit 2 で止まる（その構成では本節の手動 3 段に戻り、**Enter を単独で送ったことを inspect で必ず確認する**）。tmux socket は `$ORG_BROKER_SOCKET` を既定で読む。

   ツールは内部で **text 送信 → 着弾確認 → Enter 単独送信 → submit 確認**を順に行い、**どの段でも検証できなければ exit 10 で停止する**（`failures[]` に失敗した段、`remedy[]` に戻り先が出る）。成功時のみ `self_edit_approval_sent` を 1 行記帳するので、承認を送らずに派遣したケースは後から `python3 tools/self_edit_approval.py audit` で検出できる。

   > **なぜ 1 コマンドなのか（この分割は消してはならない）**:
   >
   > - **text と `enter=true` を同一呼び出しに載せると submit されない**: text が貼り付け扱いになり末尾 Enter が貼り付けに吸収されるため、承認文は worker 入力欄に**未送信 draft として滞留**する。したがって text 送信と Enter は**必ず別呼び出しに分ける**必要がある — この分割自体は依然として必須で、ツールは内部でこれを行っている（renga CLI でも同じ: `renga send <TEXT> --enter` は data と `\r` を無遅延で連続 write するため同じ罠を踏む。`renga/src/app/codex_peer.rs:196-203`）。**「1 回で送ればいい」に戻さないこと。**
   > - **分割した手順を人間が手で踏むと、最後の Enter を落としたときに無言で失敗する**: 承認文が入力欄に見えているので `inspect_pane` の見た目は「届いている」、worker は user message を受け取っていないので待機し、窓口からは作業中と区別がつかず、`.state/` に痕跡も残らない。そのため分割はツールの内部に閉じ込め、各段の失敗を exit 非 0 で顕在化させる（発生事例は [`.claude/skills/org-delegate/references/rationale.md`](rationale.md) §7）。
   > - **実行中ペインへの Escape はターン中断になる**: draft 滞留（`prior_draft` / `not_submitted` で停止した場合）からの復旧で入力欄を消す前に、必ず `inspect_pane` で worker が **idle** であることを確認する。busy（ターン実行中）のペインへ Escape を送ると走行中のターンを中断させてしまう。なお **Enter 自体は busy でも積まれる**ので、承認送信のために idle を待つ必要はない（待つと承認が遅れて worker が止まる）。
2. **worker brief**: root `.claude/**`（深さ不問。`.claude/skills/**/references/**` の brief 規範 prose を含む）を対象に含む委譲の brief（`CLAUDE.local.md` / 指示メッセージ）には次の趣旨を必ず書く:
   > 本タスクは `.claude/` 編集を含む。**編集前に、承認入力（対象ファイル列挙 + task_id + 「窓口経由のユーザー承認」）が会話に user message として存在することを確認**せよ。存在しなければ編集を開始せず、`send_message(to_id="secretary")` で窓口に承認入力を要求して待機せよ。
3. **worker**: 上記の確認が取れてから編集を開始する。承認文に列挙されていないファイルへの `.claude/` 編集が必要になった場合はスコープ拡張として扱い、[`.claude/skills/org-escalation/SKILL.md`](../../org-escalation/SKILL.md) 経由でエスカレーションする。