# probe categories

各 probe category の背景・狙い・関連 audit findings をまとめる。`checklist.md` の category 列はこのファイルの見出しと対応する。

## B1-1 — dispatcher × bypassPermissions × sandbox

### 背景

- `tools/org_extension_schema.json:163-205` で dispatcher は `bypassPermissions` 動作。`permissions.allow / deny` は **no-op** と明記されている。
- audit-issue-376-2026-05-09.md §2 B1-1 (Blocker): **bypassPermissions 下で `sandbox.*` が発火するかは未確認**。Claude Code 公式 docs では sandbox は別レイヤだが、bypassPermissions が sandbox を巻き添えで無効化する/しないは empirical question。
- 結果により Issue #378 (Phase 1 schema) の dispatcher 列を「sandbox 列を持たない」「sandbox 列を持つが mode で分岐」「mode に依存せず sandbox は常に効く」のいずれにするかが決まる。

### 確認したい二択

1. dispatcher cwd `claude_org_path/.dispatcher/` で sandbox.filesystem.denyRead 対象 (`.env`, `**/credentials*`, `**/*.pem`, `~/.config/gh/hosts.yml`) を `cat` したとき、`Permission denied` 相当でエラーになるか / 素通りするか。
2. dispatcher cwd 内で sandbox.filesystem.denyWrite (`~/.claude/settings.json`) に `echo x >>` したとき、書込失敗するか / 成功するか。

### 観察ポイント

- `bypassPermissions` で `permissions.deny` の `Read(~/.ssh/*)` が効かないことは既知。今回は **sandbox 側 denyRead** が同様に no-op 化されるかを切り分ける。
- sandbox は `bubblewrap` (Linux/WSL2) が動作要件。`failIfUnavailable: false` で fail-open している環境では bypassPermissions の影響と区別できないため、`/sandbox` slash command で sandbox status を先に確認すること。

## B2-1 — worker × repo-shared settings 継承

### 背景

- worker の cwd は `worker_dir`（典型: `<repo>/../workers/<project>/` または `.../.worktrees/<task>/`）で、`claude_org_path/.claude/settings.json` の **ツリー外**。Claude Code は cwd の `.claude/settings*.json` と `~/.claude/settings.json` のみを読むので、repo-shared `.claude/settings.json` の (a) `block-no-verify.sh` (b) `block-dangerous-git.sh` (c) `sandbox.filesystem.*` は worker に **継承されない**。
- 確認: `worker_roles.default` の hooks list (`tools/org_extension_schema.json:302-330`) に `block-dangerous-git.sh` / `block-no-verify.sh` が含まれていない。`claude-org-runtime settings generate` は schema にないものを emit しない。
- audit B2-1 (Blocker): 「worker には現状 `block-dangerous-git.sh` / `block-no-verify.sh` が effective に効いていない」を実測で confirm すべき。

### 確認したい二択

1. worker cwd で `git reset --hard HEAD` を試行したとき、deny されるか / 成功するか。
2. worker cwd で `git commit --no-verify -m noop` (空コミット) を試行したとき、deny されるか / 成功するか。
3. worker cwd で sandbox.filesystem.denyRead 対象 (`.env` / `~/.config/gh/hosts.yml`) を `cat` したとき、deny されるか / 素通りするか。

### 観察ポイント

- worker が repo-shared `.claude/settings.json` を継承していなければ #1 #2 は **通る**（hook がそもそも bind されていない）。これが実測で confirm されると、Issue #379 の前提は「現状 worker は二重防御がない」。
- #3 は sandbox 側の話なので、worker template に `sandbox` 列がない以上 **継承していない** のが期待値。これも実測で confirm しないと Phase 1 schema 設計時の前提が立たない。

## fs-cwd — worker の cwd 内/外 read/write

### 背景

- `check-worker-boundary.sh` は Edit|Write 経由のみ判定。Bash 経由の `cp` / `mv` / `>>` リダイレクト等で cwd 外に書くパスは hook では塞がれない。
- sandbox の `additionalDirectories` (`docs/worker-permissions-design.md:14`) は明示しない限り cwd のみが書込許可。worker が cwd 外に書ける/読めるかは sandbox + Bash 配下シェルの解釈に依存。

### 確認したい

1. worker cwd 配下 `.env` 作成 → `cat`：作成は通る / 読み込みは sandbox denyRead で deny される、を確認。
2. worker cwd 外 (`/tmp/probe.txt`) への `echo > /tmp/probe.txt`：sandbox がデフォルトで `/tmp` を書込許可しているか / `additionalDirectories` がないので deny されるか確認。
3. worker cwd 外 read (`cat /etc/hostname`)：sandbox の read 範囲を確認。

## fs-pattern-b — Pattern B 想定の base repo Git metadata 操作

### 背景

- Pattern B variant は 3 種 (`live_repo_worktree` / `claude_org_repo_worktree` / plain)。base_repo の `.git/` は worker cwd 外にあり、`git commit` を成立させるには base_repo `.git/` の write が必須。
- audit B0-2/B0-3 (Blocker/Major risk): base_repo `.git/` を sandbox で開けないと `git commit` 自体が壊れる。一方で開くと他 worker への干渉経路 (B2-2 の `git worktree remove --force`) が残る。

### 確認したい (本 spike では Pattern A worker dir 上で **シミュレート**)

1. cwd 外の git directory (`base_repo/.git/HEAD`) を `cat` したときの sandbox 挙動。
2. `git -C <base_repo> log -1` の sandbox 挙動。
3. `git -C <base_repo> worktree list` の sandbox 挙動。

### 観察ポイント

- Pattern B variant 別に確認する必要があるが、本 iteration ではまず「base_repo 相当の path を read できる/できない」のみ確認し、Pattern B 固有の variant 検証は次 iteration に分離する。

## git-surface — 履歴破壊 / forced worktree 削除

### 背景

- worker schema deny: `git push *`, `rm -rf *`, `rm -r *` のみ。worker hooks: `block-git-push.sh` (push 全面), `block-org-structure.sh`, `check-worker-boundary.sh`。`block-dangerous-git.sh` / `block-no-verify.sh` は **未配備**。
- audit B2-2/B2-3/B2-5: `git reset --hard`, `git branch -D`, `git commit --no-verify`, `git worktree remove --force`, `git push --force` (push 自体は worker で全面 deny だが、`git -C <base_repo> push --force` は base_repo 側で評価されるため shell 解析次第)。

### 確認したい

1. `git reset --hard HEAD`：deny / allow のどちらか実測。
2. `git reset --hard origin/main`：deny / allow のどちらか実測。
3. `git branch -D <branch>`：deny / allow のどちらか実測。
4. `git commit --allow-empty --no-verify -m noop`：deny / allow のどちらか実測。
5. `git worktree remove --force <other-task-worktree>`：deny / allow のどちらか実測。
6. `git push origin HEAD`：worker で deny されることを confirm（hook + permissions.deny の二重防御）。
7. `git push --force-with-lease origin HEAD`：deny / allow のどちらか実測（hook が `--force-with-lease` を catch しているか）。

### 観察ポイント

- worker で git reset --hard が allow されるなら、Phase 2 の「reset --hard を全面禁止 + secretary rescue」(B2-3) は schema 側 deny + hook 専用配備が両方必要。
- `git -C <path>` 形式の deny pattern が schema に入っていないので、`git -C <base_repo> reset --hard HEAD` は schema deny を bypass する可能性がある — 実測で確認。

## network — egress (curl, gh, cargo fetch)

### 背景

- worker schema: `Bash(gh:*)` 等の network 系は `forbidden_allow_exact` (`tools/org_extension_schema.json:11-13`) で worker から外す closed-world 制約あり。schema レベルで `gh`, `curl`, `cargo` が必要なら追加 allow が必要。
- sandbox の network policy は `sandbox.network` フィールドで表現されるが、現行 `claude-org-ja/.claude/settings.json` には `sandbox.network` ブロックが **ない** (filesystem のみ)。WSL2 で bubblewrap fallback している場合は network unsandboxed のまま。

### 確認したい

1. `curl -sI https://example.com`：worker permissions.allow に curl 系がないため `Bash(curl ...)` 自体が「permissions により Bash 経由」で deny される / または schema の `Bash(*)` 弾きで deny される、を確認。
2. `gh api user`：同上。
3. `cargo fetch` (例として)：同上。

### 観察ポイント

- 現行 worker schema に curl/gh/cargo の allow が無い前提では「permissions.allow not in list」で Claude Code 側が Tool call を拒否するはず。これは sandbox レイヤより手前で止まる想定。
- Phase 4 (network policy) は本 epic 非目標なので、実測の主眼は **どのレイヤで deny されているか** (permissions vs sandbox vs hook) を切り分けることに置く。

## secrets — `.env` / credential / `*.pem` / `~/.config/gh/hosts.yml` denyRead

### 背景

- repo-shared `.claude/settings.json:80-86` の `sandbox.filesystem.denyRead`: `.env`, `.env.*`, `**/credentials*`, `**/*.pem`, `~/.config/gh/hosts.yml`。
- worker は repo-shared を継承しないので worker 側 denyRead は **空** が現状の予想。
- audit B3-1: WSL2 で bubblewrap 未導入時、sandbox は silent no-op fallback。`~/.aws/**` / `~/.ssh/**` は portability のため sandbox 範囲外、`permissions.deny` の `Read(~/.ssh/*)` / `Read(~/.aws/*)` で防御。worker schema には `Read(~/.ssh/*)` / `Read(~/.aws/*)` deny が無いので、worker からは `~/.ssh/*` も読める可能性。

### 確認したい

1. worker cwd で `cat .env`：sandbox 継承なし → 読める / 何かが deny する、を実測。
2. `cat ~/.ssh/<ssh-key>`：worker schema deny にない → Claude Code 組込 credential 保護で deny される（公式 docs ベース）/ 素通りする、を実測。
3. `cat ~/.config/gh/hosts.yml`：sandbox 継承なし → 読める可能性大。
4. `cat **/credentials.json` (worker_dir 配下に dummy 配置して)：sandbox 継承なし → 読める可能性大。

### 観察ポイント

- worker は (a) sandbox を継承せず、(b) schema の `Read()` deny も無い、ので **secret denyRead が現状ほぼ効いていない** が予想。これは B2-1 と並ぶ Phase 2 の設計入力になる。
