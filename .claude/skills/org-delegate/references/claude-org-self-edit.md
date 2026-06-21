# claude-org 自身を編集するタスクの特例

> **前提（Pattern 判定）**: 対象ファイルが gitignored（例: `docs/internal/`, `notes/`, `tmp/` 配下の内部メモ）の場合は、SKILL.md **Step 0.7「ターゲットファイル gitignore 事前チェック」**により **Pattern C 強制**となる（Step 1 のディレクトリパターン判定基準には到達しない）。本ドキュメントの特例（hook 除外・`CLAUDE.local.md`）は Pattern B / C いずれでも適用するが、Pattern C の場合 worktree は作らないため WORKER_DIR は対象ファイルにアクセスできる既存リポジトリ root を指定する。以下は Pattern B（tracked ファイル編集）を主に想定した手順。

claude-org リポジトリのスキル / ドキュメント / 設定を編集するワーカーを派遣するとき、通常の worktree 準備のままでは以下の事故が発生する:

- `block-org-structure.sh` hook が `.claude/skills/` などへの Edit / Write を拒否する（`bypassPermissions` モードでも exit code 2 により確認プロンプトが出る）
- ルートの `CLAUDE.md` は Secretary（窓口）用の指示なので、ワーカーがこれを読んで「あなたは窓口」と誤認する

このため、claude-org 自己編集タスクでは **Step 1.5 のワーカーディレクトリ準備時に以下 3 点を通常手順に追加する**。

## 1. `claude-org-self-edit` ロールで settings.local.json を生成する

ワーカー `.claude/settings.local.json` は `claude-org-runtime settings generate` で **schema-driven に生成**する（手書き編集は窓口の `permissions.deny` で禁止されている）。claude-org 自己編集タスクでは `--role claude-org-self-edit` を指定すること:

```bash
claude-org-runtime settings generate \
  --role claude-org-self-edit \
  --worker-dir {worker_dir} \
  --claude-org-path {claude_org_path} \
  --out {worker_dir}/.claude/settings.local.json
```

`claude-org-self-edit` ロールは schema 上で `block-org-structure.sh` hook が **既に除外**された状態で定義されている（`Edit|Write` / `Bash` matcher 双方）。`check-worker-boundary.sh` / `block-git-push.sh` などその他の hook は通常どおり残る。生成済み JSON を手で再編集してはならない（drift CI が fail する。新しい role を追加する場合、**ja の `tools/org_extension_schema.json`（drift validator `tools/check_role_configs.py` の org-extension 側の正典）と `claude-org-runtime` にバンドルされた merged role schema（generator `claude-org-runtime settings generate` の正典）の両方に追加する PR が必要**: ja 側だけ追加しても generator が新 role を知らず生成失敗、runtime 側だけ追加しても drift CI が fail する。framework 側の schema 形（`worker_roles` の許容形状定義そのもの）を変える場合は `claude-org-runtime` 側のみで完結する。両者の同期未整備状態は `docs/internal/phase4-completion-2026-05-02.md:71-77` に follow-up として記録）。

## 2. ワーカー指示は `CLAUDE.md` ではなく `CLAUDE.local.md` に書く

ルートの `CLAUDE.md` は Secretary 用の指示なので、ワーカー用 CLAUDE.md で上書きしてはならない（他ロールが壊れる）。
ワーカーへの指示は `{worker_dir}` 直下の `CLAUDE.local.md` に書く（git 管理外）。Pattern B（tracked ファイル編集）なら `{worker_dir}` は worktree 直下、Pattern C 強制（gitignored サブモード）なら `{worker_dir}` は対象ファイルにアクセスできる既存リポジトリ root を指す。

> **Pattern C 強制での同 repo 排他**: `CLAUDE.local.md` と `.claude/settings.local.json` はファイル名固定なので、同一 repo root に対する Pattern C 強制ワーカーは **2 本以上同時起動しない**（先行ワーカーの指示・権限を上書きするため）。窓口側で順次化すること。SKILL.md Step 0.7「Pattern C 強制（gitignored サブモード）」節は A/B との競合のみ言及していたが、C/C も同様に排他とする（同節「並行作業との競合」項に併記済み）。

Claude Code は同一ディレクトリの `CLAUDE.md` と `CLAUDE.local.md` の両方を読み込むため、ワーカーには両方が見える。

### 通常手順の読み替え（重要）

claude-org 自己編集タスクでは、SKILL.md Step 1.5 および `worker-claude-template.md` / `instruction-template.md` が「CLAUDE.md を生成 / 配置 / 確認」と指示している箇所を、すべて **`CLAUDE.local.md` に読み替える**こと:

- Step 1.5 共通手順の「CLAUDE.md を生成する（テンプレートの変数を置換）」 → 生成先を `CLAUDE.local.md` にする。`worker-claude-template.md` の本文をそのままテンプレートとして流用してよい
- Step 1.5 共通手順（配置後）の「生成した CLAUDE.md に『作業ディレクトリ（最重要制約）』セクションが含まれていることを確認」 → 生成した `CLAUDE.local.md` を対象に確認する
- `instruction-template.md` の「詳細な行動規範は CLAUDE.md に記載されています」「CLAUDE.md に記載された絶対パス」 → 該当箇所を `CLAUDE.local.md` に書き換えてワーカーへ送信する
- 参考 work-skill セクション追加先も `CLAUDE.local.md` に向けること

ルートの `CLAUDE.md`（Secretary 指示）はいかなる場合も上書きしない。

### ワーカー完了時の Secretary 側 cleanup 責務（Issue #478）

`CLAUDE.local.md` は **生成して終わりではなく、ワーカー完了時に Secretary が回収する**責務まで含む。回収経路はパターンで分かれる:

- **Pattern B（`live_repo_worktree`）**: ブリーフは worktree 直下（`{claude_org_path}/.worktrees/{task_id}/CLAUDE.local.md`）にあるので、close 時の `git -C {claude_org_path} worktree remove .worktrees/{task_id}` がディレクトリごと回収する。個別削除は不要。
- **Pattern C（`gitignored_repo_root`）**: `worker_dir` が claude-org repo root 自身なので、worktree remove も dir 削除も効かない。`{claude_org_root}/CLAUDE.local.md` を **個別に削除しないと残留**し、次回 `/org-start` で Secretary が「窓口かつワーカー」という矛盾した role identity（冒頭の「あなたは窓口ではなくワーカーである」）を context に読み込む。gitignored なので CI でも検出されず地層化する（Issue #478 の発生事例: `lt-lapras-392778-01`）。

Pattern C の個別削除は close phase の責務として [`.claude/skills/org-pull-request/SKILL.md`](../../org-pull-request/SKILL.md) 2b-ii に組み込まれている。実体は [`tools/run_complete_on_merge.py`](../../../../tools/run_complete_on_merge.py) の `cleanup_pattern_c_local_md(conn, task_id=..., claude_org_root=..., worker_dir_abs=...)`:

- 判定: `runs.pattern == 'C'` AND `worker_dir == claude_org_root`（schema 拡張不要。ephemeral C は `worker_dir != root` で自動的に no-op）。`worker_dir` は `runs.worker_dir_id` → `worker_dirs` の join を優先し、join が NULL（= `remove_worker_dir()` 済み）のときだけ `worker_dir_abs` 引数にフォールバックする（live 行を stray な引数で override させない）。
- 動作: `{claude_org_root}/CLAUDE.local.md` を `Path.unlink(missing_ok=True)` で削除し、`events` に `pattern_c_cleanup`（payload: `task` / `removed_path` / `mode`）を 1 行残す。idempotent（ファイル不在なら `mode=skip`、エラーで止めない）。
- **順序非依存（Issue #486）**: close phase の StateWriter ブロックは `remove_worker_dir()` で `worker_dirs` 行を DELETE する（`runs.worker_dir_id` は `ON DELETE SET NULL`）。cleanup を行削除の後に呼ぶと join が `abs_path=NULL` を返し no-op 化するため、呼び出し側は削除した `abs_path` を `worker_dir_abs=` に明示で渡し、検出が行削除の前後どちらでも壊れないようにする。
- PR 起点のクローズで `tools/run_complete_on_merge.py --pr <PR>` を呼ぶ場合は merge 記録時に自動で同 cleanup が走る（このパスは `remove_worker_dir()` を呼ばないので join フォールバックで足りる）。ただし gitignored タスクは PR を生まないことが多いため、close phase の StateWriter ブロックでの明示呼び出しが本筋。

> **scope**: 本 Issue のスコープは `CLAUDE.local.md` のみ。`.claude/settings.local.json` の自動削除は worker 由来 / Secretary 由来（renga-peers MCP allow 等で Secretary 自身も使う）の切り分け設計が要るため別 Issue。

## 3. Pattern B の worktree base は **Secretary の live repo** に置く（live_repo_worktree variant）

claude-org 自己編集タスクで Pattern B（worktree）を採る場合、worktree base は **通常パターンの `{workers_dir}/{project_slug}/.worktrees/{task_id}/` ではなく Secretary 自身の live repo の `.worktrees/`** に置く:

```
{claude_org_path}/.worktrees/{task_id}/
```

これは session #11–#12 を通じて全 claude-org self-edit ワーカー (PR #276, #279, #280, #282, #288, #291, #294, #293, #295, #296) が採ってきた de facto 慣行で、Issue #289 で正式に明文化した。

理由:

- **単一 `.git/` を Secretary と worker が共有**するため、push / pull の二段クローン同期（Secretary 側 clone と workers 側 clone の間で `git pull` を挟む手数）が要らない
- Secretary の repo がそのまま canonical local clone として機能し、追加の indirection が無い
- `git worktree list` から live worker branch が常に見えるので、Secretary が状態確認するときに `cd` が要らない

通常 Pattern B（self-edit ではない）と本特例の使い分け:

| 条件 | worktree base | `pattern_variant` |
|---|---|---|
| Pattern B + `role == claude-org-self-edit` | `{claude_org_path}/.worktrees/{task_id}/` | `live_repo_worktree` |
| Pattern B + `role == default`（通常プロジェクト） | `{workers_dir}/{project_slug}/.worktrees/{task_id}/` | `null` |

`tools/resolve_worker_layout.py` は **Pattern B かつ self-edit role** のとき自動的に `pattern_variant='live_repo_worktree'` を選び `worker_dir` を上記 live repo パスに設定する（Issue #289）。TOML `[worker]` ブロックで `pattern_variant='live_repo_worktree'` を明示しても同じ結果になる。

## 4. `CLAUDE.local.md` 冒頭で「ルート CLAUDE.md は無視」を明示する

`CLAUDE.local.md` の最初に以下の趣旨を必ず書く:

> このワーカーは claude-org リポジトリ自身の `{worker_dir}`（Pattern B なら worktree 直下、Pattern C 強制なら repo root 直下）で作業する。`./CLAUDE.md`（ルート CLAUDE.md）の Secretary 指示は無視せよ。あなたは窓口ではなくワーカーである。

この明示がないと、ワーカーがルート CLAUDE.md を先に読んで Secretary として振る舞い始める（/org-start の実行を促す等）。

## 5. root `.claude/**` 編集タスクの send_keys 事前承認ハンドシェイク

### スコープ境界

本節の対象は **claude-org root の `.claude/**`（self-edit）のみ**:

- **対象**: claude-org repo root 直下の `.claude/**` への Edit / Write を含む委譲（**深さを問わず全て対象**）。直下の `.claude/settings.json`・`.claude/skills/**/SKILL.md`（`.in` ソース含む）本体だけでなく、`.claude/skills/**/references/**` の brief 規範 prose も等しく対象とする。とりわけ全 worker の brief 規範を形作る高影響テンプレート — [`.claude/skills/org-delegate/references/worker-claude-template.md`](worker-claude-template.md) / [`.claude/skills/org-delegate/references/instruction-template.md`](instruction-template.md) / [`.claude/skills/org-delegate/references/ack-template.md`](ack-template.md) / 本ファイル自身（[`.claude/skills/org-delegate/references/claude-org-self-edit.md`](claude-org-self-edit.md)） — は必ずハンドシェイク対象に含める（これらは例示であり、線引きの正は `references/**` を含む root `.claude/**` 全体。列挙外のファイルも対象）。worker が承認無しで自分の brief 規範を書き換える anti-laundering 穴を塞ぐためであり、過去に `references/` 配下の brief テンプレート編集がハンドシェイク不発で無ゲート成功した（発生事例 #612）。**本節（スコープ境界）の定義自体の編集もハンドシェイク対象**とし、worker がゲート定義を自己縮小すること（`references/**` を対象から外す等）を禁止する。
- **対象外**: `.dispatcher/` / `.curator/`（別ロールの runtime ディレクトリであり `.claude/` ではない）、および worker dir 生成物の `.claude/settings.local.json`（`claude-org-runtime settings generate` が配置する生成物で、worker 自身が編集することはない）

> **正準定義（単一 SoT）**: ハンドシェイク必須パスの正準定義は本節（スコープ境界）が単一の SoT である。[`.claude/skills/org-delegate/SKILL.md`](../SKILL.md) の send_keys 事前承認の記述は本節への back-reference（`§5 を一次参照`）であり、対象パスの線引きを独立に再定義しない。

### 背景（2 層ガード）

`.claude/` 配下への書き込みは通常 2 層でガードされる: `block-org-structure.sh` hook は Edit / Write に限り通すが、auto-mode 分類器が「ユーザー承認の無い `.claude/` 編集」をブロックする。**ただし self-edit ワーカー（`claude-org-self-edit` role）ではこの hook ガードが存在しない**: `block-org-structure.sh` は role 定義で drop され、残る `check-worker-boundary.sh` は WORKER_DIR（Pattern B の live-repo worktree。`references/**` を含む）内を一律許可し、生成された `settings.local.json` も `.claude/**` への Edit / Write に allow / deny / ask を一切持たない。したがって self-edit ワーカーにとって**本ハンドシェイク（分類器を通す send_keys 承認）が `.claude/**` 編集の唯一のゲート**であり、上記スコープ境界の線引き精度がそのままゲート網羅性になる（`references/**` の取りこぼしが無ゲート編集を許した直接原因）。分類器の承認は **send_keys による端末入力（worker の会話に user message として届く形）でのみ通る**。peer message（`send_message`）は user 入力にならないため承認として機能しない。

> **輸送層 両系（`ORG_TRANSPORT`: 既定 `renga` / opt-in `broker`）**: 本ハンドシェイクの `send_keys` は **既定 `renga`**（`ORG_TRANSPORT` 無設定）で `mcp__renga-peers__send_keys`。`ORG_TRANSPORT=broker`（opt-in・切戻し可）では **`mcp__renga-peers__send_keys` → `mcp__org-broker__send_keys`** に機械置換される（raw キー入力 = 端末への承認文入力という性質・引数形は同一なので、承認文が user message として届き分類器を通る論理は両系で不変）。なお spawn 時の初回承認プロンプト自体は、broker では Claude Code の **folder-trust プロンプト + channel sidecar の dev-channel 承認（「Load development channel?」、spawn-flow 3-3b 再導入）の 2 段**になる（renga は dev-channel のみ。push 一次採用に伴う加算であり置換ではない。設計 transport-lab `docs/design/broker-native-roles.md` §9.5）が、それは本節の「`.claude/` 編集の事前承認」とは別レイヤー（spawn 儀式の差。pane-layout / spawn-flow 側）。詳細は [`docs/contracts/backend-interface-contract.md`](../../../../docs/contracts/backend-interface-contract.md) Surface 8（ratified 2026-06-14。push 一次への additive 改訂 S3 が ratified 済み（2026-06-15）・既存 ratified 本文不変更）と [`.claude/skills/org-delegate/references/renga-error-codes.md`](renga-error-codes.md) の broker 節を参照。既定 renga の手順は不変（broker は加算）。（**既定の二フレーム注記（Refs #604）**: ここでの「既定 `renga`」は**運用既定**（broker 実走 dogfood が Epic #6 Issue G まで未活性）の意。別に**コード既定**として `tools/transport.py: DEFAULT_TRANSPORT` が runtime 0.1.28 (Epic #586) で `broker` にフリップ済みで、ja 生成器・`transport.resolve()` はこのコードフレームで render するため生成面は「既定 `broker`」と表示する — 両フレームは指す対象（運用経路 vs コード定数）が異なり矛盾しない。総説は root `CLAUDE.md`「輸送層（transport）両系」節。）

### ハンドシェイク（固定手順）

deadlock（worker が届かない承認を待ち続ける）と空打ち Enter（承認文の無い送信）を防ぐため、以下に固定する:

1. **窓口**: ディスパッチャーから `DELEGATE_COMPLETE` を受信したら、SKILL.md Step 5 の挨拶送信に**続けて**、承認文を send_keys で worker ペインへ入力しておく:
   ```
   mcp__renga-peers__send_keys(
     target="worker-{task_id}",
     text="承認します: 本タスク ({task_id}) における {対象ファイルの列挙} の編集を承認します。これは窓口経由のユーザー承認です。",
     enter=true
   )
   ```
   承認文の必須 3 要素: **対象ファイルの列挙** / **task_id** / **「窓口経由のユーザー承認」の明記**。
2. **worker brief**: root `.claude/**`（深さ不問。`.claude/skills/**/references/**` の brief 規範 prose を含む）を対象に含む委譲の brief（`CLAUDE.local.md` / 指示メッセージ）には次の趣旨を必ず書く:
   > 本タスクは `.claude/` 編集を含む。**編集前に、承認入力（対象ファイル列挙 + task_id + 「窓口経由のユーザー承認」）が会話に user message として存在することを確認**せよ。存在しなければ編集を開始せず、`send_message(to_id="secretary")` で窓口に承認入力を要求して待機せよ。
3. **worker**: 上記の確認が取れてから編集を開始する。承認文に列挙されていないファイルへの `.claude/` 編集が必要になった場合はスコープ拡張として扱い、[`.claude/skills/org-escalation/SKILL.md`](../../org-escalation/SKILL.md) 経由でエスカレーションする。

## 根拠

`knowledge/curated/delegation.md` の「claude-org 自身を編集するワーカーは worktree 内の設定を事前に調整する」セクション参照。
