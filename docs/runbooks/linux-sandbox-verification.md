# Linux / WSL2 sandbox verification runbook

> **対象**: Linux native と WSL2 環境で、claude-org-ja の Layer 2 / 3 / 4
> 防御が role × pattern ごとに **設計通りに発火** していることを実機で
> 確認するための手順書。
>
> **このドキュメントの位置づけ**: 設計の正本ではなく、**運用 / リリース時
> の検証チェックリスト**。何を / なぜ防ぐのかは下記 SoT を参照する。
> 本 runbook は「どう確認するか」のみを述べる。
>
> - Layer 2 / 3 / 4 の構造と role × pattern ごとの prescriptive 表面:
>   [`docs/contracts/role-pattern-sandbox-contract.md`](../contracts/role-pattern-sandbox-contract.md)
> - bwrap ランチャーの protocol surface (case A bootstrap fallback /
>   `failIfUnavailable` / `sandbox_deny_skipped` event):
>   [`docs/contracts/sandbox-launcher-contract.md`](../contracts/sandbox-launcher-contract.md)
> - 既存の handcraft profile + Pre-Phase 0 spike の経緯:
>   [`docs/sandbox-probe/notes/sandbox-probe-runbook.md`](../sandbox-probe/notes/sandbox-probe-runbook.md)
>
> **Refs**: claude-org-ja#380. Phase 2 hook attach: claude-org-ja#420.
> Phase 3 case A 実装フォローアップ: claude-org-ja#392。

---

## 0. 検証スコープと前提

| 項目 | 値 |
|---|---|
| 対象 OS | Linux x86_64 / WSL2 (Ubuntu 22.04 以降想定) |
| 対象 Claude Code | sandbox 機能対応版 (`claude --version` で `sandbox.filesystem.*` を読む build) |
| 対象 ランタイム | `claude-org-runtime` ≥0.1.9, <0.2 ([`requirements.txt`](../../requirements.txt) ピン) |
| 対象 schema | [`tools/org_extension_schema.json`](../../tools/org_extension_schema.json) `worker_roles[*].sandbox_by_pattern` を持つ Phase 1 PR4 以降 |
| 対象 hook 群 | [`.hooks/`](../../.hooks/) Phase 2 hook attach (PR #420) 以降 |
| **検証対象外** | macOS (sandbox-exec / `bwrap` 不在), Windows native PowerShell, Codespaces / DevContainer 内のネストされた sandbox, case A bootstrap fallback の launcher 側実装 (#392 で別管理) |

`bubblewrap` (`bwrap`) が無い、または起動に失敗する環境では Layer 3 が
fall-open する (§5)。Layer 2 / Layer 4 だけ検証してよいケースもあるが、
**Layer 3 を含めた full 検証を実行する場合は §1 の前提インストールが
必須**。fall-open 状態で「全 row 通った」と見えるのは sandbox が無効に
なっているだけなので、必ず §2 で `/sandbox` ステータスを先に確認する。

---

## 1. 前提インストール / 環境確認

### 1.1 必須コマンド存在確認

```bash
command -v bwrap   # /usr/bin/bwrap
command -v socat   # /usr/bin/socat
command -v jq      # /usr/bin/jq
command -v claude  # ~/.local/bin/claude など
```

`bwrap` / `socat` 未導入の場合 (Ubuntu / Debian):

```bash
sudo apt-get update
sudo apt-get install -y bubblewrap socat jq
```

WSL2 で `bwrap` が `Operation not permitted` で起動失敗する場合は、user
namespaces が有効か確認する:

```bash
sysctl kernel.unprivileged_userns_clone   # 1 が期待値
unshare --user --pid echo ok              # ok が出れば user namespaces OK
```

### 1.2 バージョン確認

```bash
bwrap --version   # bubblewrap 0.5.x 〜 0.10.x が contract scope
claude --version
.venv/bin/claude-org-runtime --version 2>/dev/null \
  || pip show claude-org-runtime | grep -E '^(Name|Version)'
jq --version
```

`claude-org-runtime` が `>=0.1.9` を満たさない場合、`worker_roles[*].
sandbox_by_pattern` を消費する emit ロジックが古く、**Layer 3 sandbox
ブロックが `.claude/settings.local.json` に書き出されない可能性が高い**。
その場合は `pip install -U 'claude-org-runtime>=0.1.9,<0.2'`
([`requirements.txt`](../../requirements.txt) と整合) で更新してから検証
を再走する。バージョンが古いまま検証完了とした場合、§4 / §5 / §6 の
Layer 3 row は「sandbox 未 emit による silent fall-open」を見ているだけ
になる。

### 1.3 自動チェック

§1.1 / §1.2 の確認は以下のスモークテストでも実行できる:

```bash
bash tests/sandbox/test_role_pattern_smoke.sh
```

[`tests/sandbox/test_role_pattern_smoke.sh`](../../tests/sandbox/test_role_pattern_smoke.sh)
は §1 (前提コマンド) / §3 (schema 整合) / §4 (hook 発火) のうち
スクリプト化可能な assertion をまとめて実行する。E2E spawn (§3) と
`/sandbox` 表示確認 (§2) は Claude Code を実際に起動する必要があるため
本 runbook で手動実行する。

---

## 2. `/sandbox` ステータス確認

Claude Code 起動後、各 role の pane で `/sandbox` を実行し、以下を確認:

| 表示項目 | 期待値 |
|---|---|
| Sandbox status | `Enabled` (Disabled の場合は §1.1 の `bwrap` 不在 / Layer 3 fail-open) |
| Sandbox profile / `additionalDirectories` | role × pattern ごとに [`docs/contracts/role-pattern-sandbox-contract.md`](../contracts/role-pattern-sandbox-contract.md) §3 / §4 の prescribed surface と一致 |
| `denyRead` | 同 §3.1.1 / §4.1.1 の credential 集合 (§5.1 の WSL 適応抑制例外を考慮) |
| case-A `suppressed_entries` | 通常は空。何か出ていれば §5.2 case A 想定外 — `.state/state.db` の `sandbox_deny_skipped` event を併読 |
| case-E `suppressed_entries` | WSL では `~/.aws/**` / `~/.ssh/**` 等が抑制されるのが正常。`$comment` の `platform=wsl, layer-3 entries suppressed: [...]` が表示される ([`docs/contracts/sandbox-launcher-contract.md`](../contracts/sandbox-launcher-contract.md) §3.4) |
| `failIfUnavailable` | 各 role の §4.2 表通り。dispatcher は **prescribed `true`** だが現行 schema は `false` を emit する既知 gap (同 §4.2 「Prescribed (not yet implemented)」) |

`/sandbox` が `Disabled` を返した場合は **そこで検証を停止** し、§1.1 の
`bwrap` インストール / 起動可否を解決してから再走する。fall-open 状態
での「Layer 3 deny が効いている」観測は無意味。

---

## 3. Secretary → Dispatcher → Worker spawn E2E チェックリスト

`/org-start` 直後に以下を順に確認する。各 row の確認コマンドは
operator 側の shell で叩く想定。

### 3.1 Secretary pane

```bash
# 1. cwd が claude_org_path 配下
pwd     # /home/<user>/work/org/claude-org-ja などプロジェクトルート

# 2. .claude/settings.local.json が secretary template から生成されている
jq '.permissions.allow | length, .permissions.deny | length' \
   .claude/settings.local.json
# allow / deny ともに非空 (空の場合は claude-org-runtime settings generate 未実行)

# 3. sandbox ブロックが emit されているか (Phase 1 PR3 = roles.secretary.sandbox)
jq 'has("sandbox")' .claude/settings.local.json
# true 期待。false の場合は claude-org-runtime <0.1.9 の可能性 (§1.2 参照)
```

Secretary pane で `/sandbox` を実行し、§2 表通りの表示を確認。

### 3.2 Dispatcher pane

dispatcher は `<claude_org_path>/.dispatcher/` を cwd として
`permission_mode=bypassPermissions` で起動する。

```bash
realpath .dispatcher
# /home/<user>/work/org/claude-org-ja/.dispatcher

jq '.permissions.allow, .hooks.PreToolUse[].hooks[].command' \
   .dispatcher/.claude/settings.local.json
# permissions.allow に Bash(claude :*) と Bash(sleep:*) のみ
# hooks に block-dispatcher-out-of-scope.sh / block-git-push.sh /
# block-dangerous-git.sh / block-no-verify.sh / block-workers-delete.sh
```

dispatcher pane で `/sandbox` を実行:

- 期待: sandbox enabled、`additionalDirectories` は `<claude_org_path>`
  配下の dispatcher 関連サブツリー (Phase 1 PR3 で `roles.dispatcher.sandbox`
  本体が emit される)。
- 既知 gap: `failIfUnavailable=false` (Prescribed `true`、§4.2)。
  bwrap 不在環境で fall-open する場合、bypassPermissions と相まって
  Layer 2 / Layer 3 の両方が無効化される。**この状態で dispatcher pane を
  運用に使ってはならない** — 必ず §1.1 の bwrap 導入を確認する。

### 3.3 Worker pane (dispatcher → spawn 経由で確認)

実際の spawn は dispatcher が `mcp__renga-peers__spawn_claude_pane` 経由
で行う。operator は spawn 後の worker pane で以下を確認:

```bash
# 1. cwd が worker_dir
pwd     # Pattern A: <workers_dir>/<project>/
        # Pattern B (default): <workers_dir>/<project>/.worktrees/<task>/
        # Pattern B (live_repo_worktree): <claude_org_path>/.worktrees/<task>/
        # Pattern C: <workers_dir>/<task>/

# 2. CLAUDE.md (Pattern A/B/C) または CLAUDE.local.md (B-live_repo_worktree
#    / C-gitignored_repo_root) が brief として配置されている
ls -la CLAUDE.md CLAUDE.local.md 2>&1 | grep -v 'No such'

# 3. .claude/settings.local.json が worker template から生成されている
jq '.permissions.allow | length, .permissions.deny | length' \
   .claude/settings.local.json
# 非空

# 4. Phase 2 hook attach 確認 (PR #420 以降)
jq '.hooks.PreToolUse[] | select(.matcher=="Bash") | .hooks[].command' \
   .claude/settings.local.json
# 期待: block-git-push.sh / block-dangerous-git.sh / block-no-verify.sh /
# block-org-structure.sh の 4 件 (Bash matcher)
# Phase 2 以前は block-dangerous-git.sh / block-no-verify.sh が含まれず、
# repo-shared 経由の継承に依存していた

# 5. sandbox ブロック emit (Phase 1 PR4 = worker_roles.default.sandbox_by_pattern)
jq '.sandbox' .claude/settings.local.json
# 期待: filesystem.additionalDirectories に worker_dir、Pattern B の場合は
# 加えて {base_clone}/.git/worktrees/{task_id} / objects / refs/heads/{branch_ref}
# / packed-refs と {claude_org_path}/knowledge/raw が並ぶ。
# null の場合は claude-org-runtime <0.1.9 の可能性 (§1.2)
```

worker pane で `/sandbox` を実行し、§2 表通り。

---

## 4. Pattern B worktree worker commit 検証 (Phase 2 hook attach + pin v0.1.9)

Pattern B (`<workers_dir>/<project>/.worktrees/<task>/`) は git worktree の
metadata 共有が絡むため、commit が成功するかを必ず確認する。

### 4.1 worktree git metadata 整合

```bash
# .git は file (worktree pointer) であり directory ではない
[[ -f .git ]] && echo "ok: .git is a file"
cat .git
# 期待: "gitdir: <workers_dir>/<project>/.git/worktrees/<task>"

# 共通 object store と worktree-private metadata の両方が読める
git rev-parse --git-dir   # <workers_dir>/<project>/.git/worktrees/<task>
git rev-parse --git-common-dir  # <workers_dir>/<project>/.git
```

### 4.2 commit が通ること

```bash
# 任意の許可パス内ファイルを変更
echo "smoke" >> README.md   # または worker_dir 内の任意ファイル
git add README.md
git commit -m "smoke(test): worktree commit verification"

# 期待: 成功。pre-commit hook (block-no-verify / block-dangerous-git) は
# Bash matcher PreToolUse hook 経由で git commit を見るが、--no-verify を
# 含まないため通る。
```

worker_roles.default.sandbox_by_pattern.B の `additionalDirectories` が
[`docs/contracts/role-pattern-sandbox-contract.md`](../contracts/role-pattern-sandbox-contract.md)
§4.2.1 の表通りに `<base_clone>/.git/worktrees/<task>`, `objects`,
`refs/heads/<branch>`, `packed-refs` を含んでいない場合、git は packed-refs
書き換えや objects 追加で `EACCES` を返す。**「commit が通る」ことが Pattern B
sandbox スキーマ整合の最も実用的な smoke**。

### 4.3 cross-worktree isolation (negative)

`<base_clone>/.git/worktrees/<other_task>/` への書き込みが拒否される
ことを確認する (sibling worktree の HEAD / index 破壊を防ぐ)。Layer 3 の
`additionalDirectories` が `<task_id>` 個別 path のみを mount している
ことの確認。

```bash
# 別 worktree の HEAD への書き込み試行 (期待: 失敗)
echo malicious > "$(git rev-parse --git-common-dir)/worktrees/other_task/HEAD" \
  || echo "ok: cross-worktree write blocked"
```

実環境に sibling worktree が無い場合は本 row はスキップ可。Layer 3 単体
での isolation 確認は §6.1 の「許可外パスへの書き込み」row でも代替できる。

---

## 5. Fail-open / fail-closed semantics の現状

[`docs/contracts/sandbox-launcher-contract.md`](../contracts/sandbox-launcher-contract.md)
§3 (case A bootstrap fallback) と §4.1 (`failIfUnavailable` 再 semantics)
が **prescribed**。本セクションは **現状の挙動** を確認する手順を述べる。

### 5.1 case E (runtime 側 symlink-escape 抑制)

WSL2 で `~/.aws` が `/mnt/c/Users/<user>/.aws` への symlink になっている
場合、`claude-org-runtime` の `render_role_with_metadata()` が `realpath`
で escape を検出し、Layer 3 `denyRead` から該当 entry を抑制する
([`docs/contracts/role-pattern-sandbox-contract.md`](../contracts/role-pattern-sandbox-contract.md)
§1.3)。

```bash
# WSL 上で worker .claude/settings.local.json の $comment を確認
jq '.["$comment"], .sandbox.filesystem.denyRead' \
   .claude/settings.local.json
# 期待 (WSL): $comment に "platform=wsl, layer-3 entries suppressed: [...]"
# 期待 (Linux native): $comment 無し or "platform=linux, ..."、denyRead に
# ~/.aws/** / ~/.ssh/** が残る
```

case E が走った entry は Layer 2 `permissions.deny` 側 (`Read(~/.aws/*)`
等) でカバーされている (`worker_roles.default.permissions.deny`)。
**case E 自体は設計通りの挙動**であり、抑制があること自体は異常ではない。

### 5.2 case A (launcher 側 bwrap bootstrap fallback) — **未実装**

bwrap が起動時に `Can't create file at <path>` / `Can't mount tmpfs on
<path>` で transient failure を返した場合、prescribed では launcher が
該当 entry を drop して 1 度 retry する ([`docs/contracts/sandbox-launcher-contract.md`](../contracts/sandbox-launcher-contract.md)
§3.2)。

**現状**:

- launcher は Claude Code core 側 (upstream) に存在し、本 repo には無い
  ([`docs/contracts/sandbox-launcher-contract.md`](../contracts/sandbox-launcher-contract.md)
  §1.1 / §6.1)。
- case A の retry algorithm が upstream Claude Code core で実装済みか否か
  は本 repo 側からは検証できない。**本 runbook ではこの確認は行わない**。
- case A 実装と `sandbox_deny_skipped` event の emit (case_a 側) は
  claude-org-ja#392 でフォローアップ管理されている。
- それまでは:
  - bwrap が transient mount failure を返すと **bwrap 自体が起動失敗**
    して Claude Code が落ちるか、`failIfUnavailable=false` 時に **silent
    fall-open** する。
  - silent fall-open は `/sandbox` で `Disabled` または partial state と
    して観測できるが、event row として記録されないため retro / curator
    から検出はできない。

### 5.3 `failIfUnavailable` の現状ふるまい

`tools/org_extension_schema.json` は **全 role で `failIfUnavailable=false`**
を emit する ([`docs/contracts/sandbox-launcher-contract.md`](../contracts/sandbox-launcher-contract.md)
§4.2 表)。

| 環境 | bwrap 状況 | 期待 (prescribed §4.1) | 実際 (今日) |
|---|---|---|---|
| Linux native | `bwrap` 存在 + 正常起動 | sandbox enabled、Layer 3 enforced | 同左 |
| Linux native | `bwrap` 不在 | role ごとに §4.2 表 (dispatcher は fail-closed) | **全 role fall-open** (`failIfUnavailable=false` 一律 emit) |
| WSL2 | `bwrap` 存在 + case E 抑制あり | sandbox enabled、case E entries は Layer 2 で代替 | 同左 |
| WSL2 | `bwrap` 起動失敗 (case A trigger) | launcher が retry して partial_success or fall-open | retry 未実装。落ちるか fall-open |

**運用上の含意**: dispatcher pane は `bypassPermissions` で起動するため
Layer 2 が無効化される。bwrap 不在環境で dispatcher を運用すると **Layer 2
無効 + Layer 3 fall-open + Layer 4 hook のみ**となり、credential 系の
読み取りが Bash 経由で素通りする。bwrap 導入は dispatcher 起動の
**事実上の前提条件**として扱う ([`docs/contracts/role-pattern-sandbox-contract.md`](../contracts/role-pattern-sandbox-contract.md)
§3.2.4 Bash-redirect carve-out 参照)。

### 5.4 観測手順

```bash
# 1. /sandbox で現在の状態を確認 (§2)
# 2. fall-open 疑いの場合、worker dir で credential read を試行
cat ~/.aws/credentials  # 存在する場合のみ
cat ~/.ssh/id_*         # 存在する場合のみ
# 期待 (sandbox enabled): Permission denied / No such file (denyRead 効果)
# 観測 (fall-open):       中身が読める → 即座に運用停止し bwrap 導入

# 3. .state/state.db の sandbox_deny_skipped event 確認 (将来 case A 実装後)
sqlite3 .state/state.db \
  "SELECT occurred_at, payload_json FROM events
   WHERE kind='sandbox_deny_skipped' ORDER BY occurred_at DESC LIMIT 10;"
# 現状 case_e (runtime 側) の event のみ期待。case_a 側は #392 完了まで
# 出ない。
```

---

## 6. セキュリティ境界チェック (acceptance criteria)

[`tests/sandbox/test_role_pattern_smoke.sh`](../../tests/sandbox/test_role_pattern_smoke.sh)
で自動化されている row と、手動で確認すべき row を分けて記載する。

### 6.1 worker write to disallowed path is **denied** (Layer 4)

| 経路 | コマンド (worker pane で実行) | 期待 |
|---|---|---|
| Edit / Write tool | (Claude Code 内で `Write` ツールに `file_path: "/tmp/evil.sh"` を渡す) | hook `check-worker-boundary.sh` が `exit 2` で deny。stderr に "許可パス外" |
| Bash redirect | `echo x > /tmp/evil.sh` | **Layer 4 では検出不可** ([`docs/contracts/role-pattern-sandbox-contract.md`](../contracts/role-pattern-sandbox-contract.md) §4.1.2 「Bash-mediated writes outside `<worker_dir>/` are NOT caught」)。Layer 3 `additionalDirectories` 外の write として bwrap が拒否することを期待 |
| Bash org-structure | `mkdir -p .claude/settings` (worker dir 内 org-structure dir) | hook `block-org-structure.sh` (Bash matcher) が deny |

自動化部分: `tests/sandbox/test_role_pattern_smoke.sh` で
`check-worker-boundary.sh` / `block-org-structure.sh` への直接 hook 呼び出し
で確認 (`Edit | Write` matcher 経路)。Bash redirect の Layer 3 確認は
worker pane 起動が必要なので手動。

### 6.2 worker write to `knowledge/raw/YYYY-MM-DD-*.md` is **allowed**

```bash
# worker pane の Edit / Write tool 経由で:
# file_path: "<claude_org_path>/knowledge/raw/2026-05-11-test.md"
# 期待: hook 通過。validation-depth full の worker のみが運用上書く
```

自動化: スモークテストで `check-worker-boundary.sh` に正しい kebab-case
ファイル名を渡し exit 0 を確認。kebab-case 違反 (例:
`2026-05-11-Test_File.md`) は exit 2 になることも併せて確認する
([`.hooks/check-worker-boundary.sh`](../../.hooks/check-worker-boundary.sh)
許可パス 3)。

### 6.3 `.env` / credentials `denyRead` is effective

```bash
# worker pane で:
echo SECRET=x > .env   # worker_dir に dummy 配置
cat .env               # 期待 (Layer 3 enabled): Permission denied
```

| Layer | 期待 |
|---|---|
| Layer 2 (`Read` tool) | Claude Code の `Read` ツール経由は `worker_roles.default.permissions.deny` の `Read(.env)` で deny |
| Layer 3 (Bash 経由) | `bwrap` の `denyRead` mount で `cat` syscall も deny。WSL で symlink escape あり時は case E 抑制 → Layer 2 fallback のみ |

自動化: スモークテストで schema が `worker_roles.default.permissions.deny`
に credential entries を含むこと、`sandbox_by_pattern.A/B/C.filesystem.denyRead`
に対応 entries が並ぶことを assertion する。実 syscall レベルの deny 確認
は worker pane が要るので手動。

### 6.4 dispatcher write boundary (Layer 4 のみ)

dispatcher は `bypassPermissions` のため Layer 2 不在。
`block-dispatcher-out-of-scope.sh` (Edit/Write/NotebookEdit matcher) のみが
write boundary を enforces ([`docs/contracts/role-pattern-sandbox-contract.md`](../contracts/role-pattern-sandbox-contract.md)
§3.2.3)。

```bash
# dispatcher pane の Edit / Write tool で:
# file_path: "<claude_org_path>/tools/evil.py"
# 期待: hook が exit 2 で deny
```

自動化: スモークテストで `block-dispatcher-out-of-scope.sh` への直接
hook 呼び出し。

---

## 7. 検証完了の判定

下記が全て埋まれば 1 回の Linux/WSL2 sandbox verification 完了:

1. §1.1 / §1.2 (前提コマンド + バージョン) → スモークテスト合格
2. §2 (`/sandbox` ステータス) → secretary / dispatcher / worker pane で
   `Enabled` 表示 + `additionalDirectories` / `denyRead` が contract 通り
3. §3 (E2E spawn チェックリスト) → 全 row 確認
4. §4 (Pattern B commit) → smoke commit 成功 + cross-worktree isolation
5. §5.4 (fall-open 観測) → credential read が **deny** されている
6. §6 (acceptance criteria) → 自動化 row はスモークテスト通過、手動 row
   は worker pane で確認

想定外 (期待と異なる挙動) があれば下記いずれかの形で残す:

- contract 側のずれ: contract docs を更新 (本 runbook ではない)
- runtime 側のバグ: claude-org-runtime issue / PR を別途立てる
- launcher 側 (Claude Code core): #392 のフォローアップに集約
- 本 runbook の記述漏れ: 本 runbook を更新 (PR 化)

---

## 8. 関連 Issue / 参照

- claude-org-ja#380 (本 runbook + smoke test の親 Issue)
- claude-org-ja#420 (Phase 2 worker git guardrails — hook attach + Layer 2
  deny family)
- claude-org-ja#414 (Phase 3 prerequisite — sandbox launcher contract)
- claude-org-ja#392 (Phase 3 case A 実装 / launcher 側フォローアップ)
- claude-org-ja#378 (Phase 1 schema sandbox surface — `sandbox_by_pattern`)
- claude-org-ja#376 (Phase 0 親 epic)

外部:

- bubblewrap(1) man page (`man bwrap` / Debian package `bubblewrap`
  0.5.x〜0.10.x scope)
- Claude Code sandbox feature (built-in, upstream)
