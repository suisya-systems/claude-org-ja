# baseline observations (Issue #376 Pre-Phase 0, Iteration 1)

実機 probe を回さず、コードベース静的解析のみで分かった事実を整理する。実測待ちの開項目は `probes/checklist.md` で追跡。

## 1. 確定事実 (静的解析で 100% 確認)

### 1.1 worker は repo-shared `.claude/settings.json` を継承していない

- 根拠:
  - `claude-org-runtime` (v0.1.2) bundled `role_configs_schema.json` に `sandbox` field が **存在しない** (`/home/.../site-packages/claude_org_runtime/settings/role_configs_schema.json` を grep したが該当無し)
  - `tools/org_extension_schema.json` の `worker_roles.default.hooks.PreToolUse` に `block-dangerous-git.sh` / `block-no-verify.sh` が **含まれない** (`org_extension_schema.json:302-330`)
  - 本 worker の実 settings (`/home/happy_ryo/work/org/workers/sandbox-probe/.claude/settings.local.json`) は schema 通りで、上記 2 hook を持たず、`sandbox` ブロックも無い
  - worker の cwd (`/home/.../workers/<project>/`) は `claude-org-ja/.claude/settings.json` のツリー外のため、Claude Code の cwd-based settings 検索ロジックでは `claude_org_path/.claude/settings.json` には届かない
- 帰結:
  - worker には `git reset --hard`, `git branch -D`, `git commit --no-verify` が **schema/hook いずれでも止まらない**
  - worker には `.env` / `**/credentials*` / `**/*.pem` の sandbox denyRead が **効いていない**
  - audit B2-1 は **実機確認なくして codebase だけで confirm 可能**

### 1.2 dispatcher の cwd は `claude_org_path/.dispatcher/` で repo-shared を継承する経路にある

- 根拠:
  - `tools/org_extension_schema.json:166` で dispatcher の `settings_paths` は `[".dispatcher/.claude/settings.local.json"]`、つまり cwd は `claude_org_path/.dispatcher/` 配下
  - Claude Code は cwd → 親 → home の順に settings.json を merge する。`.dispatcher/` の親は `claude_org_path/` で、そこに repo-shared `.claude/settings.json` がある
- 帰結:
  - dispatcher の **hook layer** (block-no-verify, block-dangerous-git) は repo-shared 経由で有効化される (`worker_roles.dispatcher.required_hooks` でも明示)
  - dispatcher の **sandbox layer** が同じ経路で有効化されるかは未確認 (B1-1 の核心)
  - dispatcher の **permissions.allow/deny layer** は `bypassPermissions` モードで無効化 (`org_extension_schema.json:163-165` の description で明記)

### 1.3 schema 側 forbidden_allow_exact が worker から network 系を closed-world で外している

- 根拠: `tools/org_extension_schema.json:11-13` の `forbidden_allow_exact` に `Bash(gh:*)` 等が含まれる前提 (audit §1, §5)
- 帰結: worker からの `gh api` / `curl` / `cargo fetch` は **permissions.allow 不在**で実行ブロック。sandbox.network ブロックは現行 `claude-org-ja/.claude/settings.json` に **無い** (filesystem のみ)
- これは Phase 4 (network policy) を本 epic 非目標化した判断と整合

### 1.4 worker schema の deny は `git push *` / `rm -rf *` / `rm -r *` のみ

- 根拠: `tools/org_extension_schema.json:242-247` (および `worker_roles.default.permissions.deny`)
- 帰結: 本 worker でも `git reset --hard`, `git branch -D`, `git commit --no-verify`, `git -C <other> ...` 系はすべて schema deny に含まれていないため、Claude Code 側 perms 層では **通る**

### 1.5 worker の hook bind と repo-shared hook bind は別物

- worker (`worker_roles.default.hooks`):
  - Edit|Write: `check-worker-boundary.sh`, `block-org-structure.sh`
  - Bash: `block-git-push.sh`, `block-org-structure.sh`
- repo-shared (`claude-org-ja/.claude/settings.json:60-74`):
  - Bash: `block-no-verify.sh`, `block-dangerous-git.sh`
- 共通点: 両者とも `${CLAUDE_PROJECT_DIR}` または `{claude_org_path}` で `.hooks/` を参照する形式。worker から repo-shared hook を bind するには **command path を `{claude_org_path}/.hooks/...` で書けば良く、ファイル自体は移植不要**。これが `profiles/profile-baseline.json` の設計根拠。

## 2. 強い推定 (静的解析 + audit + 公式 docs から)

### 2.1 dispatcher で sandbox は発火しない可能性が高い (B1-1 の予想)

- 根拠 (間接):
  - Claude Code 公式 docs では `bypassPermissions` は「permission system を skip して tool を即実行する」と記述。sandbox は OS-level の bubblewrap fork なので **layer は分離している**はずだが、`bypassPermissions` は内部的に sandbox の seccomp/bwrap も skip している可能性
  - audit B1-1 で「実機確認 spike が必要」とあり、現時点で文書ベースの確証は取れていない
- 結論: **実測必須**。本 spike の本筋。

### 2.2 worker で `~/.ssh/*` / `~/.aws/*` は **読めてしまう** 可能性が高い

- 根拠:
  - worker schema deny に `Read(~/.ssh/*)` / `Read(~/.aws/*)` 無し (`org_extension_schema.json:242-247`)
  - worker は repo-shared `.claude/settings.json:55-56` の `Read(~/.ssh/*)` / `Read(~/.aws/*)` deny を継承しない (1.1 と同根)
  - Claude Code 組込 credential 保護 (`docs/verification.md:418`) は `cat ~/.ssh/id_rsa` を deny する観察があるが、これは **claude-builtin** であり sandbox/perms 非依存
- 結論: 7.2 (`cat ~/.ssh/id_rsa`) は claude-builtin で deny される可能性が高い。一方 7.3 (`cat ~/.config/gh/hosts.yml`) は claude-builtin の保護対象外と推定 → **読める** 可能性が高い

### 2.3 `git -C <base_repo> reset --hard` は schema deny を bypass する

- 根拠: schema deny pattern は `Bash(git push *)` 等の文字列前置。`Bash(git -C ... reset --hard)` には match しない
- 結論: Phase 2 (Issue #379) で worker schema deny に `Bash(git -C * reset --hard*)` 等を追加 + hook の `block-dangerous-git.sh` を worker hook に追加、の二重防御が必要 (`profile-tightened.json` の設計根拠)

### 2.4 sandbox の `additionalDirectories` 未指定で worker cwd 自体は write 可

- 根拠: Claude Code の sandbox は cwd を default で write 可とする (公式 docs の sandbox 概要)
- 結論: worker の write は cwd 内なら通る。`additionalDirectories: [worker_dir]` を明示しても挙動は同じだが、明示することで Pattern B/C 移行時の差分を可視化できる (`profile-tightened.json` の意図)

## 3. 未確定の開項目 (実機 probe iteration で埋める)

| 項目 | 影響先 | 必要 probe |
|---|---|---|
| dispatcher での sandbox 発火可否 | Issue #378 schema 設計 (sandbox 列 dispatcher 行) | checklist 1.1〜1.5 |
| worker での `~/.ssh/*` 読み取り (claude-builtin の有無) | Phase 2 schema deny に Read() 追加要否 | checklist 7.2, 7.6 |
| `git -C <other>` 形式の hook catch 範囲 | `block-dangerous-git.sh` の正規表現拡張要否 | checklist 5.8, 5.9 |
| `additionalDirectories` を Pattern B base_repo に追加した場合の挙動 | Issue #378 schema の `{base_repo}` placeholder 設計 | (Pattern B 専用 profile が必要、本 iteration スコープ外) |
| `failIfUnavailable` を `true` (fail-closed) にした場合の起動失敗有無 | Phase 3 の環境別 matrix | (別 iteration、CI / 開発環境別に分けて測る) |

## 4. 周辺メモ

- **本 worker (sandbox-probe) の cwd は git repo ではない** (Pattern A、send_plan.json の `base_repo: null`)。本 iteration では **`git init` してから commit する** 構成にし、リモートは持たない。push は窓口経由。
- **Knowledge curation contract の carve-out** (`docs/contracts/knowledge-curation-contract.md:116-128`) は audit が指摘した通り task-derived な carve-out で hook 動的判定。本 spike では取り扱わない。
- **Pattern C (gitignored repo root) 系** (audit B0-4) は本 iteration スコープ外。次 iteration の優先度は低い (現状では Pattern A の最低保証を確定する方が先)。
