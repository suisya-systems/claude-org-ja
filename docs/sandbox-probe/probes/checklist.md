# probe checklist (Issue #376 Pre-Phase 0, Iteration 1)

5 列フォーマット: **category / 試行コマンド / 期待される allow or deny / 観測結果 / 結論**。

- 「期待される allow or deny」は本 iteration の机上推定 (audit-issue-376-2026-05-09.md と現行 schema/hooks の静的解析から)。
- 「観測結果」「結論」は **本 iteration では未実測**。次 iteration の最初で実機 probe を回したのち埋める。
- "deny by X" は X が拒否レイヤ: `perms` (settings.local.json permissions.deny / closed-world allow 不在), `hook` (.hooks/*.sh), `sandbox` (sandbox.filesystem.*), `claude-builtin` (Claude Code 組込 credential 保護), `none` (どこも止めない=allow)。

> ⚠️ **安全実行のための前提**: 4.x / 5.8 / 5.9 行は base_repo を対象にした破壊的 / push 系の試行を含む。**実機 probe 時はこれらの行で `$SCRATCH_BASE_REPO` を実 claude-org-ja clone ではなく専用の disposable clone (例: `git clone /home/$USER/work/org/claude-org-ja /tmp/sandbox-probe-scratch && cd /tmp/sandbox-probe-scratch && git remote remove origin`) にすること**。`$CLAUDE_ORG_PATH` をそのまま渡すと未保存変更や push 経路に副作用を生む。1.x / 2.x / 3.x / 6.x / 7.x は worker cwd or read-only なので scratch 不要。

## 1. B1-1: dispatcher × bypassPermissions × sandbox

| # | category | 試行コマンド | 期待される allow or deny | 観測結果 | 結論 |
|---|---|---|---|---|---|
| 1.1 | B1-1 | dispatcher cwd で `cat ~/.config/gh/hosts.yml` | **不明 (探索目標)**: bypassPermissions が sandbox を巻き込まなければ deny by sandbox / 巻き込めば allow (= bypassPermissions が sandbox も無効化) | 未実測 | — |
| 1.2 | B1-1 | dispatcher cwd で `cat ./.env` (`.env` を事前に作成) | 同上 (deny by sandbox / または allow) | 未実測 | — |
| 1.3 | B1-1 | dispatcher cwd で `cat ./creds.pem` (dummy 配置) | 同上 | 未実測 | — |
| 1.4 | B1-1 | dispatcher cwd で `echo x >> ~/.claude/settings.json.sandbox-test` | 同上 (deny by sandbox denyWrite / または allow) | 未実測 | — |
| 1.5 | B1-1 | dispatcher で `/sandbox` slash command 実行 | sandbox status を確認できる (Linux/WSL2 で `bubblewrap` 有 → enabled、無 → fail-open silent) | 未実測 | — |

## 2. B2-1: worker × repo-shared settings 継承

| # | category | 試行コマンド | 期待される allow or deny | 観測結果 | 結論 |
|---|---|---|---|---|---|
| 2.1 | B2-1 | worker cwd で `git reset --hard HEAD` | **allow (none)** — schema deny に reset --hard 無し、worker hook に block-dangerous-git.sh 無し | 未実測 | — |
| 2.2 | B2-1 | worker cwd で `git commit --allow-empty --no-verify -m probe` | **allow (none)** — schema deny に --no-verify 無し、worker hook に block-no-verify.sh 無し | 未実測 | — |
| 2.3 | B2-1 | worker cwd で `cat ./.env` (.env 事前作成) | **allow (none)** — worker は repo-shared `.claude/settings.json` の sandbox を継承しない | 未実測 | — |
| 2.4 | B2-1 | worker cwd で `git branch -D probe-tmp` (probe-tmp を事前作成) | **allow (none)** — schema deny に branch -D 無し | 未実測 | — |
| 2.5 | B2-1 | worker `/sandbox` slash command | sandbox 設定が空 / disabled として表示される (worker 側 settings.local.json に `sandbox` ブロック無し) | 未実測 | — |

## 3. fs-cwd: cwd 内/外 read/write

| # | category | 試行コマンド | 期待される allow or deny | 観測結果 | 結論 |
|---|---|---|---|---|---|
| 3.1 | fs-cwd | `echo data > $WORKER_DIR/probe.txt` | allow | 未実測 | — |
| 3.2 | fs-cwd | `echo data > /tmp/probe.txt` | **不明**: sandbox の `additionalDirectories` 未指定だが Claude Code default で `/tmp` が allow されるか実測必要 | 未実測 | — |
| 3.3 | fs-cwd | `cat /etc/hostname` | **不明**: sandbox の read 範囲確認。allow が予想 (Linux 共通 read)。 | 未実測 | — |
| 3.4 | fs-cwd | `echo data > /home/$USER/probe.txt` (HOME 直下) | **不明**: HOME 直下が write 可かは sandbox 実装依存。 | 未実測 | — |
| 3.5 | fs-cwd | `cat $CLAUDE_ORG_PATH/.claude/settings.json` (cwd 外 read) | **不明**: sandbox に明示 deny 無し → allow が予想。 | 未実測 | — |

## 4. fs-pattern-b: base_repo Git metadata (Pattern A 上のシミュレート)

| # | category | 試行コマンド | 期待される allow or deny | 観測結果 | 結論 |
|---|---|---|---|---|---|
| 4.1 | fs-pattern-b | `cat $SCRATCH_BASE_REPO/.git/HEAD` | **不明**: cwd 外 .git read。allow 予想 (sandbox に deny 無し)。Pattern B 想定。 | 未実測 | — |
| 4.2 | fs-pattern-b | `git -C $SCRATCH_BASE_REPO log -1` | **不明**: schema allow `Bash(git log:*)` だが `git -C` 形式は string match で外れる可能性あり。 | 未実測 | — |
| 4.3 | fs-pattern-b | `git -C $SCRATCH_BASE_REPO worktree list` | **不明**: 同上、`git worktree:*` allow を `git -C` で評価するか実測必要。 | 未実測 | — |
| 4.4 | fs-pattern-b | `git -C $SCRATCH_BASE_REPO status` | **不明**: 同上。 | 未実測 | — |
| 4.5 | fs-pattern-b | `echo x > $SCRATCH_BASE_REPO/.git/PROBE` | **allow 予想 / sandbox 次第** — Bash の `>` redirection は Edit/Write tool 経路ではないので `check-worker-boundary.sh` は発火しない。worker cwd 外 write の防御は sandbox 側のみで、`additionalDirectories` 未指定時の挙動を実測する。 | 未実測 | — |

## 5. git-surface: 履歴破壊 / 強制 worktree 操作

| # | category | 試行コマンド | 期待される allow or deny | 観測結果 | 結論 |
|---|---|---|---|---|---|
| 5.1 | git-surface | `git reset --hard HEAD` | **allow (none)** — 2.1 と同根、worker schema/hook に block 無し | 未実測 | — |
| 5.2 | git-surface | `git reset --hard origin/main` | **allow (none)** — 同上 | 未実測 | — |
| 5.3 | git-surface | `git branch -D probe-tmp` | **allow (none)** — 2.4 と同根 | 未実測 | — |
| 5.4 | git-surface | `git commit --no-verify --allow-empty -m probe` | **allow (none)** — 2.2 と同根 | 未実測 | — |
| 5.5 | git-surface | `git worktree remove --force ../other-task` | **allow (none)** — schema allow `Bash(git worktree:*)`、hook に worktree 限定無し (audit B2-2) | 未実測 | — |
| 5.6 | git-surface | `git push origin HEAD` | **deny by perms + hook** — schema deny `Bash(git push *)` + worker hook `block-git-push.sh` の二重防御 | 未実測 | — |
| 5.7 | git-surface | `git push --force-with-lease origin HEAD` | **deny by perms + hook** — 5.6 と同根。`--force-with-lease` も `git push` 文字列前置で deny に該当する想定 | 未実測 | — |
| 5.8 | git-surface | `git -C $SCRATCH_BASE_REPO reset --hard HEAD` | **allow (none)** — schema deny は `Bash(git reset --hard*)` ではなく `Bash(git push *)` のみ。`git -C` 形式の deny も無い。Phase 2 の対応必須サンプル。 | 未実測 | — |
| 5.9 | git-surface | `git -C $SCRATCH_BASE_REPO push origin HEAD` | **deny by perms + hook** — `block-git-push.sh` は `git -C` 形式も catch するか hook 実装次第 (要 hook 実装側 confirm)。 | 未実測 | — |

## 6. network: egress

| # | category | 試行コマンド | 期待される allow or deny | 観測結果 | 結論 |
|---|---|---|---|---|---|
| 6.1 | network | `curl -sI https://example.com` | **deny by perms (closed-world)** — worker permissions.allow に curl 系なし、Bash 経由は許可リスト不一致で permission prompt → auto モードで deny | 未実測 | — |
| 6.2 | network | `gh api user` | **deny by perms (closed-world + forbidden_allow_exact)** — `Bash(gh:*)` は worker から外す制約 (`tools/org_extension_schema.json:11-13`) | 未実測 | — |
| 6.3 | network | `cargo fetch` | **deny by perms (closed-world)** — worker allow に cargo 系なし | 未実測 | — |
| 6.4 | network | `python3 -c "import urllib.request as u; u.urlopen('https://example.com').read()"` | **不明**: `Bash(python3:*)` は worker 側 allow に **無い** (secretary 側のみ)。permission deny by perms が予想。 | 未実測 | — |
| 6.5 | network | `nc -zv localhost 22` | **deny by perms (closed-world)** | 未実測 | — |

## 7. secrets: denyRead

| # | category | 試行コマンド | 期待される allow or deny | 観測結果 | 結論 |
|---|---|---|---|---|---|
| 7.1 | secrets | worker cwd で `cat ./.env` | **allow (none)** — worker に sandbox 継承無し | 未実測 | — |
| 7.2 | secrets | `cat ~/.ssh/id_rsa` | **deny by claude-builtin** (公式 docs ベース) — worker schema に `Read(~/.ssh/*)` deny 無いが Claude Code 組込 credential 保護は別レイヤ | 未実測 | — |
| 7.3 | secrets | `cat ~/.config/gh/hosts.yml` | **allow (none)** — sandbox 継承無し、組込 credential 保護対象外と推定 | 未実測 | — |
| 7.4 | secrets | worker cwd で `cat ./creds/credentials.json` (dummy 配置) | **allow (none)** — sandbox 継承無し | 未実測 | — |
| 7.5 | secrets | worker cwd で `cat ./key.pem` (dummy 配置) | **allow (none)** — sandbox 継承無し | 未実測 | — |
| 7.6 | secrets | `cat ~/.aws/credentials` | **deny by claude-builtin** (推定) — 7.2 と同根 | 未実測 | — |

## 観察対象 hook / sandbox レイヤの cheat sheet

| レイヤ | 配置 | worker に効く? | dispatcher に効く? | 備考 |
|---|---|---|---|---|
| `worker_dir/.claude/settings.local.json` permissions/hooks | `worker_dir/` | ✅ | — | `claude-org-runtime settings generate` 出力 |
| `claude_org_path/.claude/settings.json` permissions/hooks/sandbox | `claude_org_path/` | ❌ (cwd 外) | ✅ | secretary (cwd === `claude_org_path`) と dispatcher (cwd === `.dispatcher/`、親方向 search で hit) のみ継承。worker cwd は worker_dir でツリー外。 |
| `~/.claude/settings.json` user global | `~` | ✅ | ✅ | 通常空 / 個人設定。本 epic では触らない |
| Claude Code 組込 credential 保護 (`~/.ssh`, `~/.aws`) | builtin | ✅ | ✅ | sandbox 非依存 |
| `tools/org_extension_schema.json` `forbidden_allow_exact` | schema | ✅ (closed_world) | ✅ | drift CI が enforce、runtime 発火ではない |

## 凡例 (再掲)

- **allow**: 動作する / 副作用が起きる
- **deny by perms**: settings.local.json `permissions.deny` または allow リスト不一致 (`closed_world`) で Claude Code 側が tool call を拒否
- **deny by hook**: `.hooks/*.sh` が exit 2
- **deny by sandbox**: bubblewrap (Linux/WSL2) / Seatbelt (macOS) で OS レベル deny
- **deny by claude-builtin**: Claude Code 組込の credential 保護
- **none**: どこも止めない = 実質 allow
