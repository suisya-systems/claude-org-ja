# 次 probe iteration 提案 (Issue #376 Pre-Phase 0 follow-up)

本 spike (Iteration 1) は handcraft profile + checklist + runbook までで止めた。次 iteration の最初に **どの組合せで実機 probe を回すか** を 3 案提示する。各案は 1 worker dir + 1 dispatcher pane で完結するサイズに絞っており、独立並行可。

## 提案 A — B1-1 単発 (dispatcher × bypassPermissions × sandbox)

**目的**: `bypassPermissions` 動作下で sandbox が発火するかを **最短** で実測する。Phase 1 schema 設計の最大の分岐点を解消する。

**手順 (runbook §3 を実行)**:

1. dispatcher pane を立ち上げ、cwd が `claude_org_path/.dispatcher/` であることを確認
2. `/sandbox` で sandbox status を Enabled (= bubblewrap 有り) 状態にする
3. `cat ~/.config/gh/hosts.yml` / `cat ./.env` (dummy 配置) / `cat ./creds.pem` (dummy 配置) / `echo x >> ~/.claude/settings.json.sandbox-test` の 4 試行
4. checklist 1.1〜1.5 を埋める

**所要**: 1 worker dir なし、dispatcher 既存利用。30 分 / 1 commit。

**得られる帰結**: 4 試行が全 deny / 全 allow / 一部 deny のいずれかで Phase 1 schema の dispatcher 行設計が確定。

**推奨度**: ★★★ (Phase 1 着手前提。最優先案)

---

## 提案 B — B2-1 + git-surface 一括 (worker × repo-shared 不継承の影響範囲)

**目的**: 「worker が repo-shared を継承していない」事実を実機 confirm し、副作用として現状 worker で **危険な git 操作がどれだけ通るか** を一覧化する。Phase 2 (Issue #379) の hook 配備対象を fix する。

**手順 (runbook §2, §4 を順に)**:

1. probe 用 worker dir `sandbox-probe-iter1` を新設
2. `claude-org-runtime settings generate --role default` で baseline (現行 schema) の settings.local.json を emit
3. checklist 2.1〜2.5 を実測 (B2-1)
4. 続けて checklist 5.1〜5.5, 5.8 を実測 (git-surface, 履歴破壊系)
5. `profiles/profile-baseline.json` を適用、Claude Code 再起動
6. 同 row を再走し、deny に転じることを confirm
7. `profiles/profile-tightened.json` を適用、`git -C` 形式 (5.8, 5.9) も deny されることを confirm

**所要**: probe 用 worker dir 1 つ + Claude Code 3 回再起動。1〜1.5 hr / 1〜2 commit。

**得られる帰結**:
- Phase 2 で `block-dangerous-git.sh` / `block-no-verify.sh` を worker schema に追加する設計の妥当性確認
- `git -C <other>` 形式が hook で catch されるかの実証 (hook 拡張要否の判断材料)
- baseline → tightened の差分が「期待通り防御強化」になっているか確認 (regression が無いか含め)

**推奨度**: ★★★ (Phase 2 着手前提。提案 A と独立並行可)

---

## 提案 C — secrets denyRead 重点 (sandbox failIfUnavailable と claude-builtin 保護の切り分け)

**目的**: secret denyRead が 3 layer (perms `Read()` deny / sandbox `denyRead` / claude-builtin) のどこで止まっているか切り分ける。Phase 3 の環境別 matrix の入力 + Phase 2 の `Read()` deny 追加可否の判断材料。

**手順**:

1. probe 用 worker dir `sandbox-probe-iter1` を流用 (提案 B 後)
2. checklist 7.1〜7.6 を baseline (= 現行 worker schema) で実測
3. ダミー secret を 3 種配置: `.env`, `~/.config/gh/hosts.yml` (本物がある環境では skip)、`worker_dir/creds/credentials.json`
4. baseline の状態で deny される row / 通る row を記録
5. `profile-tightened.json` (Read() deny + sandbox denyRead 二重) を適用、Claude Code 再起動
6. 同 row を再走し、どの layer が新たに deny に転じたか観察 (`/sandbox` で status を都度確認)
7. `failIfUnavailable: true` (fail-closed) も別ファイルで試し、bubblewrap 不在環境での起動失敗を観察 (Phase 3 入力)

**所要**: 提案 B の worker dir 流用なら 30〜45 分 / 1 commit。`failIfUnavailable: true` の実験は別 worker dir 推奨 (起動が落ちる前提)。

**得られる帰結**:
- claude-builtin が `~/.ssh` / `~/.aws` を実際に守っているか実証
- sandbox layer のみで `~/.config/gh/hosts.yml` を守れるか実証 (Phase 2 で `Read()` deny を追加するか sandbox に寄せるかの設計判断)
- fail-closed 切替時の挙動 1 サンプル (Phase 3 matrix の最初の埋め込み)

**推奨度**: ★★ (Phase 2/3 の設計入力。提案 B 完了後が効率的だが、独立しても可)

---

## 推奨実施順

1. **提案 A** (B1-1) — Phase 1 schema 設計の前提のため最優先。dispatcher 既存利用なので worker dir 不要。
2. **提案 B** (B2-1 + git-surface) — Phase 2 hook 配備設計の前提。worker dir を新設する。
3. **提案 C** (secrets) — Phase 2/3 の補強。提案 B の worker dir を流用すると効率的。

3 案いずれも `probes/checklist.md` の対応 row を埋め、想定外があれば次々 iteration に新 row を追加する。本 spike の harness (`probes/`, `profiles/`, `docs/sandbox-probe-runbook.md`) はそのまま流用可能。
