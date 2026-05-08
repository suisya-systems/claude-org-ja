# sandbox-probe runbook (Issue #376 Pre-Phase 0)

## 0. 目的とスコープ

- 目的: Issue #376 の Pre-Phase 0 spike として **B1-1 (dispatcher × bypassPermissions × sandbox)** と **B2-1 (worker × repo-shared settings 継承)** を実機で再現できる手順を確定する。
- 本ドキュメントが完成しても、**実機実行は本 spike のスコープ外**。実行は次 iteration の probe-worker (本 worker と同等の harness) で行う。
- 検証深度 minimal。fmt/lint は走らせない (handcraft profile JSON は `jq empty` で構文確認のみ)。

## 1. 前提条件

| 前提 | 確認コマンド | 期待値 |
|---|---|---|
| Linux または WSL2 | `uname -a` | Linux x86_64 / WSL2 |
| `bubblewrap` 導入 | `command -v bwrap` | `/usr/bin/bwrap` 等 |
| `socat` 導入 | `command -v socat` | `/usr/bin/socat` 等 |
| `jq` 導入 | `command -v jq` | `/usr/bin/jq` 等 |
| Claude Code 起動可 | `claude --version` | sandbox 対応版 (cf. docs/verification.md:430-441) |
| `claude-org-runtime` 導入 | `claude-org-runtime --version` | 0.1.2 以上 (本 spike 確認時点) |

bubblewrap/socat 不在環境では `sandbox.failIfUnavailable: false` により sandbox は **silent no-op fallback**。この場合 B1-1 / B2-1 の実測は意味を成さないため、まず `bwrap` を入れてから実行する。

## 2. probe 用 worker の準備 (B2-1)

### 2.1 Pattern A worker dir を 1 本立てる

dispatcher 経由で本 worker (sandbox-probe) と同形のディレクトリを派遣するか、手動で `/home/$USER/work/org/workers/sandbox-probe-iter1/` を切る。本 spike worker (`/home/happy_ryo/work/org/workers/sandbox-probe`) を再利用してもよい。

### 2.2 settings.local.json を generate

```bash
cd /home/happy_ryo/work/org/claude-org-ja
.venv/bin/claude-org-runtime settings generate \
  --role default \
  --worker-dir /home/$USER/work/org/workers/sandbox-probe-iter1 \
  --claude-org-path /home/$USER/work/org/claude-org-ja \
  --out /home/$USER/work/org/workers/sandbox-probe-iter1/.claude/settings.local.json
```

### 2.3 観察ポイント A: emit された JSON に `sandbox` ブロックがない

```bash
jq 'has("sandbox")' /home/$USER/work/org/workers/sandbox-probe-iter1/.claude/settings.local.json
# 期待値: false (本 spike 時点の bundled schema に sandbox field 無し)
```

### 2.4 観察ポイント B: emit された hooks に `block-dangerous-git.sh` / `block-no-verify.sh` がない

```bash
jq '.hooks.PreToolUse[].hooks[].command' /home/$USER/work/org/workers/sandbox-probe-iter1/.claude/settings.local.json
# 期待値:
#   "bash \"$CLAUDE_ORG_PATH/.hooks/check-worker-boundary.sh\""
#   "bash \"$CLAUDE_ORG_PATH/.hooks/block-org-structure.sh\""
#   "bash \"$CLAUDE_ORG_PATH/.hooks/block-git-push.sh\""
#   "bash \"$CLAUDE_ORG_PATH/.hooks/block-org-structure.sh\""
# block-dangerous-git.sh / block-no-verify.sh は **含まれない**
```

### 2.5 観察ポイント C: worker に repo-shared 設定が継承されない

```bash
# claude-org-ja repo の sandbox / dangerous-git は repo-shared に書かれている
jq '.sandbox, .hooks' /home/$USER/work/org/claude-org-ja/.claude/settings.json

# worker の cwd は worker_dir で repo-shared のツリー外
realpath /home/$USER/work/org/workers/sandbox-probe-iter1
# /home/.../workers/sandbox-probe-iter1 (claude-org-ja のサブパスではない)
```

→ **B2-1 の確定**: worker は `block-dangerous-git.sh` / `block-no-verify.sh` を継承していない、かつ `sandbox.filesystem.*` も継承していない。

### 2.6 動作確認: probe で実測する 5 row (`probes/checklist.md` 2.x)

worker を Claude Code で起動し、auto モードで以下を順に試す。

| # | コマンド | 観察 |
|---|---|---|
| 2.1 | `git reset --hard HEAD` (worker dir で) | hook/perms で deny されるか / 通るか |
| 2.2 | `git commit --allow-empty --no-verify -m probe` | 同上 |
| 2.3 | `cat ./.env` (`echo SECRET=x > .env` で事前作成) | 通る (sandbox 継承なし) ことの確認 |
| 2.4 | `git branch -D probe-tmp` (`git branch probe-tmp` で事前作成) | 通る ことの確認 |
| 2.5 | `/sandbox` slash command | sandbox status を表示 (Disabled / Enabled / fail-open) |

## 3. probe 用 dispatcher の準備 (B1-1)

### 3.1 dispatcher pane を起動 (renga layout で標準的に立つ pane)

dispatcher は `bypassPermissions` モードで起動する想定。renga layout: `dispatcher.json` 等。本 runbook 単独では renga を起動せず、すでに立っている dispatcher pane の有無を `mcp__renga-peers__list_peers` で確認すれば足りる。

### 3.2 dispatcher の cwd と settings.local.json を確認

```bash
# dispatcher の cwd
realpath /home/$USER/work/org/claude-org-ja/.dispatcher
# 期待値: /home/.../.dispatcher (claude-org-ja 直下)

# dispatcher の settings.local.json
jq '.permissions, .hooks, .sandbox' /home/$USER/work/org/claude-org-ja/.dispatcher/.claude/settings.local.json
# 期待値: permissions allow に Bash(claude :*) と Bash(sleep:*) のみ、hooks に block-dispatcher-out-of-scope.sh + block-git-push.sh + block-dangerous-git.sh + block-workers-delete.sh + block-no-verify.sh、sandbox は無し (schema に無いため未 emit)
```

### 3.3 dispatcher の cwd は claude_org_path 配下なので repo-shared `.claude/settings.json` を **継承する**

- `claude_org_path/.claude/settings.json:60-91` の `hooks` (block-no-verify, block-dangerous-git) と `sandbox` は dispatcher にも有効である **可能性が高い** (Claude Code の継承ルール: cwd は `.dispatcher/` だが settings.json は親方向に検索される、設定優先度は cwd > parent > home)。実機で `/sandbox` で確認。
- ただし dispatcher は **bypassPermissions 動作**なので `permissions.deny` は無効化される。**sandbox は別レイヤとして残るかが本 probe の核心**。

### 3.4 動作確認: probe で実測する 5 row (`probes/checklist.md` 1.x)

dispatcher pane に対して `mcp__renga-peers__send_message` で probe コマンドを送るか、対話的にプロンプトする。

| # | コマンド (dispatcher で実行) | 観察 |
|---|---|---|
| 1.1 | `cat ~/.config/gh/hosts.yml` | sandbox denyRead で deny されるか / 素通りするか |
| 1.2 | `cat ./.env` (事前に dispatcher cwd に dummy .env 配置) | 同上 |
| 1.3 | `cat ./creds.pem` (dummy 配置) | 同上 |
| 1.4 | `echo x >> ~/.claude/settings.json.sandbox-test` | sandbox denyWrite で deny されるか |
| 1.5 | `/sandbox` slash command | sandbox status |

### 3.5 観察ポイント: 結果の場合分け

| 結果 | 解釈 | Phase 1 schema 影響 |
|---|---|---|
| 1.1〜1.4 すべて deny | bypassPermissions は permissions.allow/deny のみ無効化、sandbox は別レイヤとして発火 | dispatcher にも sandbox 列を持たせる設計 |
| 1.1〜1.4 一部 deny / 一部 allow | sandbox の発火条件が更に細かい (cwd か絶対 path か等) | 各 deny pattern の文法を再確認、Phase 1 で表現を統一 |
| 1.1〜1.4 すべて allow | bypassPermissions が sandbox も巻き込んで無効化 | dispatcher の防御は hook 一本、Phase 1 schema に sandbox 列を入れない |
| `/sandbox` で Disabled | 環境の bubblewrap 不在で fail-open silent | **probe 結果は無効**、bubblewrap 入れて再実行 |

## 4. profile 切替検証 (Pattern A worker)

### 4.1 baseline 適用

```bash
cp /home/$USER/work/org/workers/sandbox-probe/profiles/profile-baseline.json \
   /home/$USER/work/org/workers/sandbox-probe-iter1/.claude/settings.local.json

# placeholder 置換
sed -i "s|{worker_dir}|/home/$USER/work/org/workers/sandbox-probe-iter1|g; s|{claude_org_path}|/home/$USER/work/org/claude-org-ja|g" \
       /home/$USER/work/org/workers/sandbox-probe-iter1/.claude/settings.local.json

jq empty /home/$USER/work/org/workers/sandbox-probe-iter1/.claude/settings.local.json
```

Claude Code を再起動し、checklist 2.x / 5.x / 7.x を再走。baseline では:
- `git reset --hard HEAD` → **deny by hook** (block-dangerous-git.sh) 期待
- `git commit --no-verify` → **deny by perms or hook** 期待
- `cat .env` (worker cwd) → **deny by sandbox** 期待
- `cat ~/.config/gh/hosts.yml` → **deny by sandbox** 期待

### 4.2 tightened 適用

```bash
cp /home/$USER/work/org/workers/sandbox-probe/profiles/profile-tightened.json \
   /home/$USER/work/org/workers/sandbox-probe-iter1/.claude/settings.local.json

sed -i "s|{worker_dir}|/home/$USER/work/org/workers/sandbox-probe-iter1|g; s|{claude_org_path}|/home/$USER/work/org/claude-org-ja|g" \
       /home/$USER/work/org/workers/sandbox-probe-iter1/.claude/settings.local.json
```

Claude Code を再起動し、追加で:
- `git -C /home/$USER/work/org/claude-org-ja reset --hard HEAD` → **deny by perms** 期待
- `git worktree remove --force ../<other>` → **deny by perms** 期待
- `cat ~/.aws/credentials` (dummy) → **deny by sandbox + perms** 期待 (二重防御)

## 5. 検証完了の判定

下記すべてが埋まったら 1 iteration 完了:

1. `probes/checklist.md` の **観測結果** 列が全 row 埋まる (un-tested は理由を残して可)
2. `probes/checklist.md` の **結論** 列に「allow / deny by X」が書かれる
3. baseline と tightened での差分が「期待通り」「想定外」のいずれかで分類されている
4. 想定外があれば次 iteration の checklist row として追加

## 6. 想定リスクと回避

- **誤って `git reset --hard` 等が通り、worker dir のデータが消失**: worker dir は probe 専用に新設し、本物のリポジトリと混在させない。`git stash` で必要な変更は退避してから走らせる。
- **dispatcher が誤って `git push --force` を発火**: dispatcher は permissions.deny が無効化されている。**本 runbook では dispatcher 上で push 系を試行しない**。push 系の検証は worker でのみ行う。
- **bubblewrap 起動失敗で sandbox が silent fallback**: 必ず `/sandbox` で status を先に確認。Disabled なら手順を止めて bubblewrap を導入。
- **`additionalDirectories` の path にユーザー名が含まれて他環境に持ち出せない**: profile JSON は placeholder 形式で commit し、適用時のみ `sed` で置換。本 spike の handcraft profile はそうなっている。

## 7. 関連資料

- audit-issue-376-2026-05-09.md (B0/B1/B2/B3 詳細、`/home/happy_ryo/work/org/workers/claude-org-ja/tmp/audit-issue-376-2026-05-09.md`)
- claude-org-ja `docs/verification.md:386-457` (sandbox 実機検証手順、bubblewrap/socat 前提)
- claude-org-ja `tools/org_extension_schema.json` (worker_roles と forbidden_allow_exact)
- claude-org-ja `.claude/settings.json` (repo-shared 防御の現状)
- claude-org-ja `.hooks/block-dangerous-git.sh` 等 (hook 実装)
- 本 worker の `probes/checklist.md`, `probes/categories.md`, `profiles/*.json`
