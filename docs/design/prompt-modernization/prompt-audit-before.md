# 着手前の /doctor prompt-audit の結果

2026-10-12、統合用ブランチ ddb8257 に対して claude -p で実行。Opus 5.5。プラグイン・ユーザー設定に関する指摘はオーナーへ報告済みでリポジトリの対象外。

以下は実行結果の報告をそのまま収めたもの（提案パッチは収めていない。適用もしていない）。見出しの階層だけ 1 段下げた。

## Prompt audit — claude-org-ja (worktree `ja-audit`, HEAD ddb8257)

### Assumptions (Step 0)

- **Scope.** Claude Code config that loads into sessions here:
  - root `CLAUDE.md`, plus nested `.dispatcher/CLAUDE.md` and `.curator/CLAUDE.md`
  - `.claude/rules/pr-ci-watch.md`
  - `.claude/skills/**` (31 skill dirs incl. references; `.md.in` sources are the edit targets)
  - `~/.claude/CLAUDE.md` and `~/.claude/skills/{runtime-release-with-paired-ja-sync,yomiyasu}` (any edit there **affects all projects**)
  - enabled plugins, report only: ponytail 4.9.0 (incl. its SessionStart/SubagentStart hook injection), wabun 0.11.0, wezterm-panes 1.1.0, frontend-design, eli5
- **Not present.** No `CLAUDE.local.md`, `AGENTS.md`, `.claude/CLAUDE.md`, ancestor CLAUDE.md, `commands/`, `agents/`, `output-styles/`, managed-policy CLAUDE.md, or `@` imports.
- **Skipped.** codex and skill-creator plugins: installed only at `/mnt/c/Users/iwama` project scope, not loaded here. Settings files, `.mcp.json` and `~/.claude.json`: not read, by rule.
- **Target model.** claude-opus-5-5 (this session). No file pins another model.
- **Generated files.** `SKILL.md` files that have a sibling `SKILL.md.in` are rendered by `tools/gen_skill_prose.py` (`--check` exists for drift CI). The patch edits the `.in` sources, so the rendered files must be regenerated in the same change.
- **Owner plan already in the repo.**
  - `docs/design/prompt-modernization-and-repo-split.md` (2026-10-12) §1-1 freezes all transport-layer wording in phase 1: the fragments `tools/skill_src/fragments/*.md`, the `transport-duality.md` files, the "既定 renga/broker" (default renga/broker) text, and the "Epic #6 Issue G" caveat.
  - Findings that fall in frozen text are therefore **flag** only, cited to that plan.
  - Several findings below overlap the plan's inventory; this audit adds file:line evidence and verification.

### Summary

Highest impact:

1. **Instructions that contradict the running harness or the repo.**
   - The rule for launching subagents disagrees between the two files that state it:
     - `CLAUDE.md:82` says the Agent tool no longer has `run_in_background`.
     - `org-delegate/SKILL.md.in:65,76` says it is required.
     - This session's Agent tool does still have the parameter.
   - `org-pull-request` allowed-tools pre-approve the direct `tools/pr-watch.*` starts that `.claude/rules/pr-ci-watch.md:9` and the hook forbid.
   - Several skills say to run commands with `python` or `py -3`. Neither exists on this host (`which` finds only `python3`). In secretary-handover/resume the failure is also masked by `|| echo`, so those journal events are silently never recorded.
2. **The curator is still described as resident in six places**, although CLAUDE.md:17, org-start and `.curator/CLAUDE.md` say it is on-demand: secretary-resume, dispatcher-resume, org-resume, pane-layout, renga-error-codes.
3. **Same-name skill conflict in user-level config (affects all projects).**
   - `~/.claude/skills/runtime-release-with-paired-ja-sync` is an older fork of the project skill.
   - It prescribes a byte-copy of `org_extension_schema.json`, which the project copy (`:355`) forbids.
   - It also has no fail-closed tag gate.
   - The ponytail plugin injects a "lazy senior developer" persona into every session and subagent. That conflicts with this repo's secretary role and its ask-back and escalation rules.

| Group | Findings |
|---|---|
| Group 1 (dated prompt text) | ~12 |
| Group 2 (stale facts, conflicts, history) | ~85 |
| Group 3 (tool descriptions) | not applicable; skill-description trigger issues are under Group 2 |
| Group 4 (request code) | not applicable; no API request-building code is in scope |

### Findings — High confidence

| # | Location | Evidence | Pattern | Why | Action |
|---|---|---|---|---|---|
| 1 | CLAUDE.md:82 vs org-delegate/SKILL.md.in:65,76 | "`run_in_background` が廃止…呼び出し側で指定するパラメータは無い" vs "`run_in_background=true` 必須" | G2 conflict / volatile | The current harness's Agent schema does include `run_in_background` (this session). The hook denies only a present non-`true` value, so `true` is correct either way. CLAUDE.md:82 (blame 2026-08-19) is newer, but the harness contradicts it. | rewrite CLAUDE.md:82 to cover both harness shapes; keep the skill (patch) |
| 2 | org-pull-request/SKILL.md.in:27-29 | `Bash(bash tools/pr-watch.sh:*)` ×3 | G2 conflict | `.claude/rules/pr-ci-watch.md:9` forbids it, and the `block-adhoc-pr-watch.sh` hook denies it (it fired twice during this audit). | remove (patch) |
| 3 | CLAUDE.md:127 | "並走の runtime PR が…mapping を追加する" | G1d migration-relative / G2 stale | Already shipped: `tools/templates/attention.example.json:28` sets `secretary_awaiting_user: urgent`. | rewrite to present tense (patch) |
| 4 | CLAUDE.md:65 | "2 回目以降は cold-start に落ちる前に check_messages 再 drain で済む" | G2 stale | `dispatcher-resume/SKILL.md.in:78-119` now has an `already_consumed` guard and can also return `DISPATCHER_RESUME_INDETERMINATE` (`:97`). The note "skill 側に未記載" (not in the skill) is still accurate, so the paragraph stays. | rewrite the tail (patch) |
| 5 | org-delegate/SKILL.md.in:174,180 | "以下 4 条件" / "4 条件は省略禁止" | G2 contradicted | Only 3 bullets follow. | rewrite → 3 (patch) |
| 6 | org-delegate/SKILL.md.in:284 | "state.db を相対パスで開く tool…Issue #398 で根本対応中" | G2 stale | Fixed in 7c8d639; `tools/journal_append.py:48` resolves the repo root itself. Only the inline `python -c` snippet stays cwd-relative. | rewrite (patch) |
| 7 | org-pull-request/SKILL.md.in:103 | same cwd claim for pr_watch | G2 stale | `tools/pr_watch.py:78-79` anchors on `tools/..`. Only `--repo` omission depends on cwd. | rewrite (patch) |
| 8 | org-delegate/SKILL.md.in:257-262 vs references/codex-design-review.md:15 | the "のみ" (only) list omits the 5th trigger | G2 conflict | A reader who stops at SKILL suppresses the design review for lifecycle and monitoring changes. | add bullet (patch) |
| 9 | org-escalation/SKILL.md.in:38, :81 | `feedback_relay_user_decisions_to_workers`; 「3. ブロック報告」 | G2 dangling refs | The slug exists nowhere; the section was removed in 2edff0c. | rewrite (patch) |
| 10 | org-pull-request/SKILL.md.in:53 | `feedback_pr_issue_english` | G2 dangling ref | No such memory exists. The real one (`feedback_renga_english_pr_issue`) is scoped to renga/runtime, and ja PRs are Japanese. | rewrite (patch) |
| 11 | .dispatcher/CLAUDE.md:71 vs org-start/SKILL.md:525-536 | "auto classifier は sonnet だと不安定…Opus 固定が原則" (Opus fixed because the classifier is unstable on Sonnet) | G2 conflict | org-start (newer) says the classifier is independent of session model and allows `sonnet` on request. | rewrite (patch) |
| 12 | secretary-resume/SKILL.md.in:8,164; dispatcher-resume/SKILL.md.in:10,421; org-resume/SKILL.md:112 | "キュレーターは既に生きている" (curator is already alive) | G2 conflict | CLAUDE.md:17, `.curator/CLAUDE.md`, and secretary-resume's own `:33` say otherwise. | rewrite (patch) |
| 13 | references/pane-layout.md.in:60; renga-error-codes.md:25 | "キュレーター → 窓口に escalate" (escalate via curator to secretary) | G2 conflict | No resident curator. | rewrite (patch) |
| 14 | references/pane-layout.md.in:43 | "(`SKILL.md` Step 3-1c 参照)" | G2 stale path | 3-1c lives in `.dispatcher/references/spawn-flow.md:51`. | rewrite (patch) |
| 15 | secretary-handover/SKILL.md:135-137; secretary-resume/SKILL.md.in:157-159 | `py -3 tools/journal_append.py … \|\| echo "(…skipping)"` | G2 volatile command | `py` is absent here, and the fallback hides the failure. `tools/journal_append.sh` takes key=value. | rewrite (patch) |
| 16 | org-setup/SKILL.md:206, :150 | lists `~/.config/gh`; "`.gitignore:23-25`" | G2 contradicted | `tools/org_setup_prune.py:79-106` puts gh in the remove list; the entries are at `.gitignore:55-56`. | rewrite (patch) |
| 17 | org-setup/references/permissions.md:496 | "git fetch / pull / remote … deny family" | G2 conflict | The same file `:392` and `tools/org_extension_schema.json:522` allow `git fetch` for workers. | rewrite (patch) |
| 18 | work-discovery/SKILL.md:36, :46 | "定常トリガ…と post-merge 統合は別 Phase" (scheduled trigger and post-merge hookup are a later phase) | G2 stale / G1d | Both shipped (`.dispatcher/CLAUDE.md:259-288`, `org-pull-request/SKILL.md.in:334-338`). | rewrite / remove (patch) |
| 19 | skill-audit/SKILL.md.in:120-121, :136, :161 | "owner 未記載…一括提案になる見込み" (all skills lack an owner); "窓口が手動で行う" (secretary does it by hand) | G2 stale + conflict | 11 skills now carry `owner:`. Hand edits by the secretary contradict CLAUDE.md:66 and org-retro (newer). | remove / rewrite (patch) |
| 20 | runtime-release-with-paired-ja-sync/SKILL.md:221 (project) | "15 kind ぶん" (templates for 15 kinds) | G2 contradicted | The template now has 16. | drop the count (patch) |
| 21 | org-conveyor/SKILL.md:117 | "notify_peer は raw env 判定" (picks transport from the raw env var) | G2 stale | `tools/peer_notify.py:85-96` uses `transport.resolve()`. | rewrite → "`notify_peer` は `transport.resolve()`（コード既定フレーム）で active transport を選ぶ" (not in patch, see note) |
| 22 | ~/.claude/skills/runtime-release-with-paired-ja-sync/SKILL.md:110,115,178 (**all projects**) | byte-copy schema; parallel 1 PR per target; bare tag push | G2 same-name conflict | The project copy (`:355`, `:231-244`, `:132-190`) forbids or replaces each of these. | **flag**: the user decides whether to delete the user copy or re-sync it. A project reason does not justify editing ~/.claude. |
| 23 | org-start/SKILL.md:103-105 vs org-down/SKILL.md:151-155 (and pr-watch-pane) | `${ORG_TRANSPORT:-renga}` vs `${ORG_TRANSPORT:-broker}` | G2 conflict in executable branches | With the var unset, start drives renga while down drives broker. | **flag**: transport wording is frozen by the owner plan §1-1; choose a branch rule in phase 2. |
| 24 | fragments `org-pull-request-recv-model.broker.md:3`, `org-pull-request-transport.broker.md:1,3` | "peer_notify は raw env 判定" | G2 stale | Same code evidence as #21. | **flag**: frozen fragments; the plan already lists transport-duality.md:7 as a known error for phase 2. |
| 25 | ponytail hook injection (plugin, report only) | "PONYTAIL MODE ACTIVE… ACTIVE EVERY RESPONSE"; "Never stall on an answer you can default"; "at most three short lines" | G1a pressure, G1f numeric ceiling, G2 conflict | Collides with CLAUDE.md:3, :21 (ask back when ambiguous), :131 (escalate), and citation-or-abstain output. The skill's own description excludes non-coding use. | **flag**: e.g. turn ponytail off by default for this project (`ponytail-help/SKILL.md:46-61` documents how). |
| 26 | wezterm-panes control/SKILL.md:60-61,87-92 (plugin) | text + immediate `\r`; blind Enter | G2 conflict | CLAUDE.md:33 ("盲目 Enter は禁止", blind Enter forbidden) and :65 (two-step send). | **flag** |

Note on #21: org-conveyor:117 sits in transport prose, and the owner plan §1-1 may treat it as frozen, so it is left out of the patch. Apply the text above if the plan does not cover it.

### Findings — Medium confidence

The proposed text is given in the Action column; these hunks are not in the patch file. Apply them after #1-21 if wanted.

| # | Location | Pattern | Action |
|---|---|---|---|
| 27 | org-setup/SKILL.md:13,15,113-118,183,194,200-203; permissions.md:62,109-115,249-251; secretary-resume/SKILL.md.in:79; .dispatcher/CLAUDE.md:209 | G2 volatile command: `python` / `py -3` absent on this host (an activated venv could supply `python`, hence medium) | `python` → `python3` in commands and allowed-tools. Keep `py -3` only where an explicit Windows alternative is given. |
| 28 | CLAUDE.md:98 | G2 history: "本 Issue は明文化のみが対象で、guard / CI 実装は別 Issue で扱う" (this Issue only writes the rule down) | remove that sentence; it is issue-scope meta. Keep the violation example as the reason. (In patch.) |
| 29 | .curator/CLAUDE.md:11 | G1d migration-relative: "旧仕様の /loop 30m は廃止済み" | rewrite → "（1 回実行して終わる）" (in patch) |
| 30 | CLAUDE.md:57,64,69,88,90,100,108,138,140 | G2 history: "Issue #320 / #464 / #515 / #554 / #475 / #312 / #28 / #297 / #301" as section labels; CLAUDE.md:71 pilot narrative "2026-06-12 のパイロット 2 走…18 分" | Strip the Issue tags from headings and keep the reasons. Shorten :71 to "極小タスクに限り、ワーカーレーン同等の Codex ゲートを保ったまま subagent で直処理してよい（ユーザー承認済みの方針）". Matches the user's own memory note "No transient IDs in long-lived docs". |
| 31 | org-pull-request/SKILL.md.in:85, :98 vs :81 | G2 intra-file conflict on polling events vs relying on relay | rewrite :85 → "relay も直 push も成立しない環境では、push 後に events テーブルを間隔を置いて引く"; rewrite :98 → "push 通知は早期通知として扱い、確定は events 行の head + status で行う" |
| 32 | org-suspend/SKILL.md.in:63,263,298-299,581 | G2 stale: renga 1.x/1.4 paths | `tools/check_renga_compat.py:108-113` floor is 2.0.0; drop the renga 1.x mentions (transport-adjacent, so check against plan §1-1) |
| 33 | org-suspend/SKILL.md.in:137-146; pr-watch-pane/SKILL.md.in:278-283; org-start/SKILL.md:433-440,267,284,450; codex-design-review.md:57-59 | G2 history narrative: self-declared records, duplicated rationale, wall-clock tables, "本 PR では実装しない" (not implemented in this PR) | remove; rationale.md files already keep the provenance |
| 34 | pr-watch-pane/SKILL.md.in:39,124-125,336-337 | G1d: authoring-PR scope notes ("既存挙動。本 skill は形を変えない", existing behaviour unchanged by this skill) | remove |
| 35 | org-start/SKILL.md:7 (description), :37-45; org-curate/SKILL.md.in:7,34; skill-audit/SKILL.md.in:146; skill-eligibility-check/SKILL.md:73 | G1d migration-relative: "移行済み" (already migrated), "廃止された" (was abolished), "オンデマンド化後" (after the on-demand change), "旧版" (old version) | state the current rule only, e.g. "キュレーターは起動しない（ディスパッチャーが worker クローズ時に必要なときだけ一時起動する）" |
| 36 | org-start:79-87, org-resume:23-29, org-suspend.in:401-411 | G1d plus an inconsistent importer command (`--root` / `--no-strict` missing in one file each) | unify to `python3 -m tools.state_db.importer --db .state/state.db --root . --rebuild --no-strict` with a present-tense SoT sentence |
| 37 | org-delegate/SKILL.md.in:98,100,102; org-start/SKILL.md:531-533 | G2 pinned model name "Sonnet 5" | → "Sonnet（`sonnet` エイリアス）" |
| 38 | org-delegate/SKILL.md.in:141,361; references/worker-claude-template.md:3,10 | G2 stale | The template is not what reaches workers (`tools/gen_worker_brief.py:415` renders `tools/templates/worker_brief_*.md`), so the rebase rule SKILL:141 relies on never reaches workers. Already plan §1-2 (W2); **flag** the rebase-porting decision. |
| 39 | references/claude-org-self-edit.md:3,31,12-24,35-42 | G2 stale: SKILL Step 0.7 sections no longer exist; manual steps contradict SKILL:253 | repoint to `references/delegate-flow-details.md` §1; describe what `gen_delegate_payload.py apply` does automatically |
| 40 | org-curate/SKILL.md.in:106-109,257-263,287-295 | G1c: one rule stated 4 times with differing wording | Step 7 → "送信要否は Step 0-A の判定に従う" |
| 41 | org-retro/SKILL.md.in:13,24 | G2 verbose: a ~1.5 KB transport header in a skill that never messages or touches panes | remove the include and the `send_message` allowed-tool (fragment frozen; removing the *include* is arguably not a wording change, so confirm against plan §1-1) |
| 42 | org-conveyor/SKILL.md:73,75; scope-contract.md:27,33; exit-conditions.md:32; pr-watch-pane/SKILL.md.in:37 | G2 dangling `feedback-*` refs | remove the parentheticals; the reasons are inline |
| 43 | org-conveyor/SKILL.md:52,170; verify-evidence.md:7 | G2 harness claim: "`/verify` 組込みスキル" (built-in `/verify` skill) | no such built-in in the current skill list; user to confirm the name (`/run`?) |
| 44 | .dispatcher/CLAUDE.md:21,27,35,58,164-170 | G2 history / G1c speculative paths (`--overflow-to-new-tab` "今日は到達しない", unreachable today) | drop the Issue lineage; move the unreachable-path detail to spawn-flow 3-1b; move incident dates and counts to worker-monitoring.md (the :21 transport block is frozen, so flag that part) |
| 45 | org-attach/SKILL.md:9-14 (description), :55-66 | G2 trigger enumeration; meta-paragraph | description → "組織ペインを tmux で直接見たい・入りたい（attach コマンドを知りたい）ときに発動。状況一覧・委譲・watcher 停止には使わない。"; remove :55-66 |
| 46 | pr-watch-pane/SKILL.md.in:4-13 (description) | G2 trigger text carries behaviour | → "PR の CI / マージ監視を専用ペイン pr-watch-<PR> で起動する。CI 監視の唯一の正規経路で、PR 作成直後や再 push 後に使う。「CI 監視をペインで」「PR <N> の CI を見張って」等で発動。" |
| 47 | pr-watch-pane/SKILL.md.in:15-27, org-pull-request/SKILL.md.in:11-33 | G2 config: the body requires `python3 tools/watcher_restart_guard.py check`, which is not in allowed-tools | add `Bash(python3 tools/watcher_restart_guard.py:*)` |
| 48 | org-attention-start/SKILL.md (whole) | G2: hand-written renga-only tool names, while its generated sibling org-attention-stop renders broker | convert to `SKILL.md.in` like attention-stop (transport-related; check against plan §1-1) |
| 49 | org-setup/SKILL.md:194; permissions.md:62 | G2 time-sensitive: "⚠️ main pull 後の 1 回必須…Issue #429 Task C" (must run once after pulling main) | → "初回セットアップ時（および候補リスト変更後）に 1 回実行する" |
| 50 | work-discovery/SKILL.md:246 | G2 hardcoded operator path `/home/happy_ryo/work/org/claude-org-ja` (already wrong in this worktree) | → "窓口セッションの CWD はリポジトリルート" |
| 51 | references/instruction-template.md.in:73,216 | G1d "長文プロンプト形は廃止" (long-prompt form abolished) + unmeasured `gpt-5.5` speed claim | → "差分セルフレビューは `codex exec review` で行う。" |
| 52 | runtime-release (project) :345-346, :368-372; org-pull-request Issue tags (:54-303); ack-template.md.in:11; dogfood-protocol.md:5,35; claude-org-self-edit.md:70 | G2 history narrative | remove the archaeology and keep the reasons; rewrite "直近の実例は" (most recent example) → "実例として" (as an example) |
| 53 | ~/.claude/skills/yomiyasu/SKILL.md, references (**all projects**, vendored MIT) | G1c intra-file repetition; tension with domains/tech.md:109 | **flag** (upstream-owned) |
| 54 | Plugins: ponytail-audit trigger overlap with skill-audit / wabun:kansa; wezterm-panes `❯` readiness false positive; suiko vs yomiyasu trigger overlap | G2 trigger collision | **flag** (report only) |

### Low confidence / flag only

- dogfood-protocol.md:86 and org-pull-request:297 reference a `/org-resume` dogfood scan that doesn't exist. The plan already deletes dogfood (W2-4).
- capability-first-drive-operational-gate.md §6: the call-site table claims to be exhaustive but omits the org-delegate `list_peers` call at SKILL.md.in:239.
- .dispatcher/CLAUDE.md:80 `plan_version < 2` branch: probably unreachable at the runtime>=0.1.49 pin, but it guards a worker-killing blind Enter. Keep.
- dispatcher-resume/SKILL.md.in:60,342-346 link a git-ignored `knowledge/raw` note; cite `tests/test_dispatcher_resume_loop_invariant.py` instead.
- org-dashboard/SKILL.md:38,43-47: hardcoded port 8099 (`dashboard/server.py:50` falls back to 8100/8101); no Linux opener.
- wsl-windows-chrome-screenshot/SKILL.md:55: an operator-specific UNC path in a tracked skill.
- org-suspend/SKILL.md.in:587/594 has step "4." twice; org-down/SKILL.md:54 has the typo "jud断".
- Conflicts with operator-local memory, outside the project, so no edit is proposed:
  - org-conveyor INV-1 and the merge gates vs memory "CI green = auto-merge"
  - `[[…]]` memory links in runtime-release.
- `~/.claude/CLAUDE.md`: clean. Its single rule ("PR は origin にのみ") is one reasoned-by-context safety constraint, and no project file conflicts with it.
- `.claude/rules/pr-ci-watch.md`: clean.

### Proposed diff

`/tmp/claude-1000/audit/prompt-audit.patch` holds 34 edits across 17 files, covering high-confidence rows #1-20 plus #28-29. The patch was verified to apply with `git apply --check`.

After applying, re-render the generated SKILL.md files with `tools/gen_skill_prose.py` and run `--check`. Nothing has been applied to the repo.
