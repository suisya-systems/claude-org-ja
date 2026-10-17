# 第 1 段階後の /doctor prompt-audit の結果と突き合わせ

2026-10-12 に、統合用ブランチ `modernize/prompts-2026-10` の先端 ead58d8 を対象に claude -p で 1 回実行した。モデルは Opus 5.5。測り方は着手前（[`docs/design/prompt-modernization/prompt-audit-before.md`](./prompt-audit-before.md)）と同じで、対象は既定の範囲（root・`.dispatcher`・`.curator` の CLAUDE.md、rules、skills）。

作業コピーはリポジトリの外（`/home/happy_ryo/work/org/ja-audit-after`）に置いた。祖先ディレクトリに CLAUDE.md は無い。リポジトリ配下の `.worktrees/` に置いた最初の実行では、祖先にある main チェックアウトの古い root CLAUDE.md まで読み込まれた。この実行は無効とし、比較には使っていない。

着手前との対象範囲の差:

- `~/.claude/skills/runtime-release-with-paired-ja-sync`（プロジェクト版の古い写し）は 2026-10-12 に削除済み。着手前の #22 はこの写しが対象だったが、今回は存在しない。ユーザー階層で残っているのは `~/.claude/CLAUDE.md` と `~/.claude/skills/yomiyasu` だけ。
- 祖先の CLAUDE.md の混入は着手前・今回ともに無い。着手前の記録は Assumptions の「Not present」に「ancestor CLAUDE.md」を挙げており、`../../CLAUDE.md` を引く指摘も無い。今回の記録も前提節に同じ記載がある。
- `.dispatcher` だけを対象にした別実行の結果は比較に使わない。そこにしか無い指摘は末尾の「参考」に挙げた。

以下は実行結果の報告をそのまま収めたもの（実行中の途中経過の行は除いた）。提案パッチは収めておらず、適用もしていない。見出しの階層だけ 1 段下げた。

## Prompt audit of the Claude Code setup in claude-org-ja (worktree `ja-audit-after`, HEAD ead58d8)

The proposed fixes are in a patch: 33 files, 77 hunks, high- and medium-confidence findings only. It applies cleanly to this worktree (`git apply --check`). Nothing has been applied to the repo.

The full test suite gives the same result with and without the patch: 2,335 passed, 6 skipped, 1 failed. The failure is `test_docs_projection_is_consistent`, which already fails on the unpatched copy. The scratch copy has no local files and no network access, which probably explains it.

### Assumptions

- **Scope.** In the project:
  - root `CLAUDE.md`, plus nested `.dispatcher/CLAUDE.md` and `.curator/CLAUDE.md`
  - `.claude/rules/pr-ci-watch.md`
  - all 25 project skills under `.claude/skills/`, with their `references/`. Where a skill is built from a `SKILL.md.in` template, the template is the edit target and the patch also includes the rebuilt `SKILL.md`.

  User-level files (any edit there would affect every project): `~/.claude/CLAUDE.md` and `~/.claude/skills/yomiyasu`.

  Report only: the user-scope plugins (ponytail, wabun, eli5, wezterm-panes, frontend-design, rust-analyzer-lsp) and the Anthropic synced skills.
- **Not present:** `CLAUDE.local.md`, `AGENTS.md`, `.claude/CLAUDE.md`, ancestor CLAUDE.md files, `@` imports, commands, agents, output styles, a managed-policy CLAUDE.md.
- **Not loaded here:** the `codex` plugin and the plugin copy of `skill-creator` are installed only for `/mnt/c/Users/iwama`, so they are out of scope. The codex plugin calls OpenAI models (GPT-5.4 and others); I only note that. The synced copy of skill-creator does load here and is covered.
- **Not read:** settings files, `.mcp.json`, `~/.claude.json`.
- **Target model:** Claude Opus 5.5, the model running this audit. No file in scope pins a different model.
- **Owner plan already in the repo.** `docs/design/prompt-modernization-and-repo-split.md` §1-1 freezes all transport-layer wording for phase 1. Transport findings are therefore flag-only, with no edit proposed.
- **Earlier audit.** `docs/design/prompt-modernization/prompt-audit-before.md` holds the audit run before this cleanup. Where a finding was already raised there, I note whether it is still open.

### Summary

Most of the earlier audit's high-confidence findings are fixed. Fixed or gone:
- the subagent background-flag conflict
- the "4 conditions" count
- the dangling references in org-escalation
- the stale cwd claim in org-delegate
- the "Sonnet is unstable" reason in `.dispatcher/CLAUDE.md`
- the "Phase 2" notes in work-discovery
- the user-level fork of runtime-release
- the herdr check and the dogfood files

The three most important problems left:

1. **org-pull-request still pre-approves something the project forbids.** Its allowed-tools list still includes the direct `tools/pr-watch.*` starts (`org-pull-request/SKILL.md.in:27-29`). `.claude/rules/pr-ci-watch.md` and the hook forbid exactly that; the hook fired three times during this audit. The same file also says pr-watch depends on the working directory (`:100-102`), but `tools/pr_watch.py:77-79` anchors the database path to the script's own location. And it points to a memory note, `feedback_pr_issue_english` ("PRs in English"), that does not exist. The repo's recent PR titles are Japanese.
2. **Facts the code now contradicts.**
   - work-discovery says the old ranking axes are "not used for ranking" (`work-discovery/SKILL.md:175`). The code sorts by clause first and then by those axes (`tools/work_discovery_goals.py:980`).
   - skill-audit prints `skill_count`, which no step defines; Step 1 defines `work_skill_count` (`skill-audit/SKILL.md.in:124` vs `:45`).
   - The two org-setup files contradict each other on whether subdirectory sessions read the parent's settings (`org-setup/SKILL.md:31` vs `references/permissions.md:218`).
   - org-setup gives the wrong reason for the dispatcher's `bypassPermissions` ("Sonnet constraint", `permissions.md:245`). org-start says the real reason is that the safety classifier blocks spawning worker agents (`org-start/SKILL.md:419,440`).
3. **History text inside instructions.** Issue numbers, milestone labels, "旧/従来どおり/化済み" ("old / as before / already converted") wording, dated incident stories and pinned model names ("Sonnet 5") are spread across about 15 files. The largest is `.dispatcher/CLAUDE.md:82`, which spends most of one bullet on the 2026-07-30 to 08-19 relay outage. That line was edited today (bd0a7b6) and still carries the story.

| Group | Findings |
|---|---|
| 1 (dated prompt text: emphasis, reminders, choreography) | 22 |
| 2 (stale facts, conflicts, history, triggers) | 58 |
| 3 (tool descriptions) | not applicable. Skill-description issues are counted in Group 2. |
| 4 (request code) | not applicable. No API request code is in scope. |

### High confidence

| # | Location | Evidence | Pattern | Why it's wrong now | Action |
|---|---|---|---|---|---|
| 1 | org-pull-request/SKILL.md.in:27-29 | `Bash(bash tools/pr-watch.sh:*)`, plus 2 lines for `pr-watch.ps1` | G2 conflict between files | The rule file and `.hooks/block-adhoc-pr-watch.sh` forbid direct starts. Still open from before-audit #2. | remove |
| 2 | org-pull-request/SKILL.md.in:100-102 | 「`state.db` を相対パスで開くため…Issue #398 で根本対応中」 | G2 stale fact | `tools/pr_watch.py:77-79` anchors on `tools/..`. Only the relative start and the log `tee` target depend on cwd. | rewrite |
| 3 | org-pull-request/SKILL.md.in:53 | `feedback_pr_issue_english`（PR / Issue は英語） | G2 dangling reference | The slug exists nowhere. Recent ja PR titles are Japanese. The real memory note covers renga and the runtime only. Still open from before #10. | rewrite: follow the target repo's language |
| 4 | org-pull-request/SKILL.md.in:84, :97 vs :80 | 「~60s 経過した時点で events テーブルを定期 poll」 vs 「能動 poll する必要はない」 | G2 contradiction within the file | :80 is newer (blame 2026-07-13). :84 also quotes a CI timing figure. Still open from before #31. | rewrite :84 and :97 |
| 5 | pr-watch-pane/SKILL.md.in:110-112 | 「`ORG_TRANSPORT` 未設定で silent no-op」 | G2 stale fact | `tools/peer_notify.py:85-127` resolves an unset value to broker. The file's own :216-219 already says so. | rewrite |
| 6 | work-discovery/SKILL.md:175 | 「（ランクには使っていない）」 | G2 stale fact / conflict | `tools/work_discovery_goals.py:980` sorts by clause, then by `legacy_key`. Root `CLAUDE.md:47` agrees with the code. | rewrite |
| 7 | skill-audit/SKILL.md.in:124 | `skill_count={skill_count}` | G2 stale fact | Step 1 (:45) defines `work_skill_count`. | rewrite |
| 8 | org-conveyor/SKILL.md:66 vs :26-28 | 「直接使う transport ツールは…`list_panes` だけ」 | G2 contradiction within the file | The frontmatter allows `check_messages` and the comment names it. | rewrite |
| 9 | org-conveyor/references/exit-conditions.md:38 | 「反復数は観測可能性サマリの出力回数に一致させる」 | G2 conflict | SKILL.md:185-190 prints that summary several times per loop, so counting summaries would end the loop early. | rewrite |
| 10 | org-setup/SKILL.md:31 vs references/permissions.md:218 | 「親ディレクトリの設定は**読み込まれない**」 | G2 conflict between files | :31 dates from 2026-04-30. permissions.md (2026-09-30) says the root `settings.local.json` is read by subdirectory sessions. | rewrite the older text |
| 11 | org-setup/references/permissions.md:245 | 「Sonnet 制約により `bypassPermissions`」 | G2 conflict between files | Newer org-start text (:419, :440) and `registry/org-config.md:25` give the classifier reason. | rewrite |
| 12 | org-attach/SKILL.md:144 | 「旧版 fallback（現行配備の全 backend）」 | G2 stale fact | The newer `capability-first-drive-operational-gate.md:41-46` says only `org-broker` takes the fallback path. | rewrite |
| 13 | org-attention-start/SKILL.md:122 | 「org-start Block A-1」 | G2 broken reference | org-start has "Block A" (:150) and no A-1. | rewrite |
| 14 | org-delegate/references/claude-org-self-edit.md:12 | 「以下 3 点…窓口が手で行う手順は無い」 | G2 contradiction within the file | The table of contents lists 5 sections, and §5 is a manual approval step. | rewrite |
| 15 | dispatcher-resume/SKILL.md.in:10 | 「キュレーターは生きている前提」 | G2 conflict | The curator is not resident (`CLAUDE.md:17`, `.curator/CLAUDE.md:7`). Still open from before #12. | rewrite (together with #29) |

### Medium confidence

The patch covers every row here unless the Action column says otherwise.

| # | Location | Pattern | Action |
|---|---|---|---|
| 16 | `.dispatcher/CLAUDE.md:82` (2026-07-30 incident, "旧形も後方互換", Issue #941/#955/#971) and `:83` ("旧 `(target_line + cursor)` …誤発火していた") | G2 history / G1d migration-relative | :83 is rewritten to the current rule plus its reason (in patch). :82 is a long bullet whose wording interleaves with the hook-backed command, so its rewrite is left to whoever does phase 1. It would keep the one command and the reason "this is the only automatic check that detects a silent relay failure", and move the story to `references/rationale.md`. |
| 17 | `CLAUDE.md:81` 「本 Issue は明文化のみが対象で…別 Issue で扱う」 | G2 history (scope note about the Issue itself) | remove. Still open from before #28. |
| 18 | `.curator/CLAUDE.md:11` 「旧仕様の `/loop 30m` は廃止済み」 | G1d migration-relative | rewrite to 「1 回実行して終わる」. Still open from before #29. |
| 19 | org-delegate/SKILL.md.in:98,100,102 and org-start/SKILL.md:433-435: "Sonnet 5" | G2 pinned model name | → 「Sonnet（`sonnet` エイリアス）」 / 「`sonnet` 可」. Still open from before #37. |
| 20 | org-delegate/SKILL.md.in:179 「…3 条件は省略禁止。」 and :92 「同一制約を spawn-flow 3-5a にも置く（片方だけだと…）」 | G1a / G1d notes aimed at maintainers, not at the running model | remove both |
| 21 | org-delegate/SKILL.md.in:250 and org-pull-request/SKILL.md.in:312, both pointing to old "museum copy" documents | G1c a prohibition that points at the thing it forbids, plus history | remove |
| 22 | pr-watch-pane/SKILL.md.in:31 `feedback-dispatcher-visibility` | G2 dangling reference | remove |
| 23 | pr-watch-pane/SKILL.md.in:33 and org-pull-request/SKILL.md.in:186: scope notes like 「既存挙動。本 skill は形を変えない」 | G1d text about the PR that wrote it | remove. Still open from before #34 (only partly covered: :90-91 and :270-271 are not in the patch). |
| 24 | org-start/SKILL.md:140-142, the "never delete" rule stated twice in 3 lines | G1a / G1c | merge into one line that keeps the reason |
| 25 | org-start/SKILL.md:277 「（2026-09-25 にワーカーで実際に起きた。…#185）」 | G2 incident story | remove; the rule already carries its reason |
| 26 | org-resume/SKILL.md:96 and org-setup/SKILL.md:63: bold "重要" / 「絶対に」 with no reason | G1a | rewrite at normal volume, with the reason |
| 27 | org-setup/SKILL.md:192 and permissions.md:73,75 「⚠️ main pull 後の 1 回必須」 | G2 time-sensitive content | rewrite as a one-time step after cloning. Still open from before #49. |
| 28 | dispatcher-resume/SKILL.md.in:41-42 vs Step 0 (:133-167) | G2 conflict within the file (always update, vs only update what was observed) | rewrite |
| 29 | dispatcher-resume/SKILL.md.in:9 vs secretary-resume:7, both triggering on 「resume」「引き継ぎから再開」 | G2 overlapping triggers | narrow the dispatcher trigger to "secretary typed `/dispatcher-resume`" |
| 30 | dispatcher-resume/SKILL.md.in:197 (wording from the Issue's acceptance criteria) and :318 (「schema 追加前の旧形式」) | G2 / G1d | remove |
| 31 | secretary-resume/SKILL.md.in:100,106 「従来どおり」 | G1d | rewrite |
| 32 | org-attach/SKILL.md:3-14, a description listing 8 near-identical trigger phrases plus the procedure | G2 trigger enumeration | rewrite to one paragraph covering the intent categories |
| 33 | work-discovery/SKILL.md:88-89 「旧手順」, :137 「Issue #829」, :208 「Issue #830」 | G1d / G2 | rewrite / remove |
| 34 | org-conveyor/SKILL.md:188-189, an update every 5 minutes, suppressed when nothing changed | G1f fixed update cadence | remove; item 2 (on each state change) already covers it |
| 35 | 「Issue #755」 in org-curate.in:150,162, skill-audit.in:35,59, skill-eligibility-check:85, signals.md:75, org-retro.in:94,121 | G2 history | remove the issue numbers; the surrounding text already states the rule |
| 36 | org-curate/SKILL.md.in:32-34 「（オンデマンド化）…ゲートは存在しない」 and skill-audit.in:137 「オンデマンド化後の流れ」 | G1d | rewrite to the current behaviour only |
| 37 | wsl-windows-chrome-screenshot/SKILL.md:46 hard-codes `Ubuntu-24.04\home\happy_ryo\…` | G2 operator-specific path in a shared skill | rewrite to `wslpath -w` |
| 38 | org-down/SKILL.md:52, typo 「jud断」 | matches no pattern; a typo | fix (one-character hunk) |
| 39 | org-curate/SKILL.md.in:54-107 (the "0-A" decision block) | G1d patch accretion, and the file contradicts itself: :90 calls the send harmless, :82 says it can close the real curator early | **Not in patch** (the send itself is a fragile operation, so I'd rather the user review it). Replacement text: 「Step 7 の送信義務は、このインスタンスが dispatcher から起動指示を受けたかだけで決まる。受けていれば結果に関わらず `to_id="dispatcher"` に送る（不送信はペインリーク）。人間が自分のペインで `/org-curate` を打った起動のときだけ省略できる。`curate-inflight.json` は別インスタンスのマーカーでもあり得るので判断材料にしない。」 |
| 40 | org-pull-request.in, pr-watch-pane.in, org-delegate.in: allowed-tools | G2 config gap | **Not in patch.** The skill bodies run `python3 tools/watcher_restart_guard.py` and `python3 tools/self_edit_approval.py` but don't list them. Add `Bash(python3 tools/watcher_restart_guard.py:*)` and `Bash(python3 tools/self_edit_approval.py:*)`. Still open from before #47. |
| 41 | org-start/SKILL.md:116, a ~1,400-character bullet that restates its conclusion 4 times | G1c repetition | **Not in patch.** Proposed text is in batch B, item M2 (it keeps each reason once). |
| 42 | org-start/SKILL.md:352-354 (repeats Block C2/C4, with #119/#747); org-down:65-93 (gate stated three times); org-suspend.in:239/328/389/397/424 (hook-enforced rule repeated 5 times) | G1c / G1d | **Not in patch.** Keep one statement in each place. For org-suspend, follow the pattern of commits ead58d8 and bb0e6f1: "hook が deny する". |
| 43 | Heavy bold across files: org-start ~186 spans in 445 lines, pr-watch-pane ~132, org-pull-request ~103, org-conveyor 66, work-discovery 65, dispatcher-resume 65 | G1a | **Not in patch.** Keep bold only on the few irreversible-operation rules: no blind Enter, the CLEAR at D-5, the checks before close, the merge gate. |
| 44 | work-discovery:101-126 (version archaeology), :135 (「必ず」 three times in one cell); org-retro.in:105-138 ("Set E", Issue #68/#753); dynamic-ports.md:8,45 (Issue #637); runtime-release:67-68 (dated example); org-start:49-57,171 and org-suspend.in:124-134,159 (M4 milestones); org-start:39-45,405 and permissions.md:61 (renga 0.14/0.18 history) | G2 history / G1d | **Not in patch** (each needs a paragraph-sized rewrite). Replacement texts are in the batch B and D findings. |

### Flag only (no edit proposed)

- **Transport wording frozen by the owner plan (§1-1).**
  - org-setup/SKILL.md:26: says an unset `ORG_TRANSPORT` makes it a no-op as renga. The code projects to broker (`tools/org_setup_prune.py:736-758`, `tools/transport.py:19-22`).
  - Unset `ORG_TRANSPORT` is resolved two ways: `${ORG_TRANSPORT:-renga}` in org-start/SKILL.md:73, but `${ORG_TRANSPORT:-broker}` in org-down/SKILL.md:142. One session picks different tool names. Still open from before #23.
  - org-conveyor/SKILL.md:91 and the fragments `org-pull-request-{transport,recv-model}.broker.md`: say `notify_peer` decides from the raw environment variable. The plan already lists this as a known error.
  - Also: renga-error-codes.md:169 and claude-org-self-edit.md:80 (「既定 renga（無設定）」); the org-attention-start header :31, which is the only attention skill not generated from a template; org-attach:55-66; org-start:59-67; the issue/version history in the `dual-system-header-*` fragments.

  All of these go into phase 2.
- **org-delegate/SKILL.md.in:236 vs capability-first-drive-operational-gate.md §6.** This `list_peers` call is not listed in the gate's table of call sites. It is a safety gate, so I flag rather than rewrite.
- **dispatcher-resume/SKILL.md.in:346, "print nothing each cycle".** This looks like an update suppressor, but `.hooks/check-loop-directive.sh` checks this block verbatim. Keep it.
- **org-start/SKILL.md:346, re-arming `/loop`.** A workaround for a dispatcher behaviour. The repo can't show whether it still happens, so re-test before removing it.
- **org-resume:53 lacks `--no-strict`** while the other rebuild commands have it. You decide which is right.
- **skill-audit check 1.2 never runs.** No skill has `origin:` (0 of 26), so check 1.2 (`audit-checklist.md:14-18`) has nothing to check. Meanwhile org-retro's work-skill template asks every new skill for an `origin` and a 背景 (backstory) section, which builds history text into each new skill.
- **org-conveyor `/verify`.** This session's built-in skills include `run` but no `verify`. Confirm the name. Still open from before #43.
- **Memory-note references in a shared repo** (runtime-release `[[…]]`, `feedback-*` in the conveyor references): they point outside the project. The conveyor merge gate also differs from the "CI green = auto-merge" memory note. That conflict is outside the project; you decide.
- **User-level files (would affect all projects).** `~/.claude/CLAUDE.md` is clean: one reasoned safety rule. `~/.claude/skills/yomiyasu` is vendored from upstream, so flag only.
- `.claude/rules/pr-ci-watch.md` is clean.

### Plugins and synced skills (report only)

- **ponytail** (user scope): `SKILL.md:28` says "ACTIVE EVERY RESPONSE". Its SessionStart and SubagentStart hooks inject that into every session and every subagent, including non-coding ones like this audit. That is a G1d re-insertion crutch. It also sets a cap of "at most three short lines" (`:68`, G1f). Both cut against this repo's ask-back and escalation rules. (M)
- **skill-creator** (synced copy): `SKILL.md:306`, "your thinking time is not the blocker… we are trying to create billions" (prose that steers thinking depth, plus a stakes booster). `:122`, "ALWAYS use this exact template", which the file's own :139 calls a yellow flag. `:67`, a "make descriptions pushy" claim about the model's tendencies. (H / M / L)
- **frontend-design:** the two-pass plan-then-review script at `:47-53` (L). Its list of named design defaults is the form the audit says to keep.
- **wabun, wezterm-panes, eli5, rust-analyzer-lsp:** clean. Other synced Anthropic skills: clean, or reasoned capitals only.
- **codex** (not loaded here): its failure handling contradicts itself, "return nothing" vs "report the failure" (`agents/codex-rescue.md:42` vs `skills/codex-result-handling/SKILL.md:17`).

### Proposed diff

- **Patch:** `/tmp/claude-1000/audit-scratch/prompt-audit-after.patch` (33 files, 77 hunks, `diff -ruN a b`, apply with `-p1`). It covers findings #1-38. For #16 it covers only line 83, and for #23 only pr-watch-pane:33 and org-pull-request:186.
- **Generated files:** the `.in` templates were edited and `tools/gen_skill_prose.py` rebuilt the outputs. `--check` reports no drift between the templates and the rebuilt `SKILL.md` files. The patch includes both.
- **Tests:** `test_check_curate_threshold`, `test_gen_skill_prose`, `test_dispatcher_resume_loop_invariant` and `test_check_group_b_selectors` pass (153 tests). The full suite matches the unpatched copy (one existing failure, described at the top).
- **Integration branch:** the owner plan routes phase-1 changes through `modernize/prompts-2026-10`. Apply the patch there, not to main.

## 着手前との突き合わせ

判定は、今回の監査が「Still open from before #N」と書いたものはそれに従った。今回の監査が触れていない指摘は、着手前に引用された文言を ead58d8 の該当ファイルで grep して判定した。行番号はいずれも ead58d8 のもの。

### 着手前の指摘（#1〜54）

件数: 解消 21、残存 31、判定対象外 2（リポジトリ外のプラグインで、今回の監査が再指摘していないもの）。

| 着手前 # | 判定 | 根拠 |
|---|---|---|
| 1 | 解消 | `CLAUDE.md` から `run_in_background` の記述が消え、`org-delegate/SKILL.md.in:76` は「前景の Agent 起動は hook が deny」の 1 本だけになった |
| 2 | 残存 | 今回 #1 |
| 3 | 解消 | 「並走の runtime PR」の文言が `CLAUDE.md` に無い |
| 4 | 残存 | `CLAUDE.md:65` の末尾「2 回目以降は cold-start に落ちる前に check_messages 再 drain で済む」が残っている。`dispatcher-resume/SKILL.md.in:72-112` の `already_consumed` と `DISPATCHER_RESUME_INDETERMINATE` に触れていない。今回の監査は再指摘していない |
| 5 | 解消 | `org-delegate/SKILL.md.in:173` は「3 条件」 |
| 6 | 解消 | `org-delegate/SKILL.md.in:329` は、cwd に依存するのは `python -c` だけで、tool は repo root を自分で解決すると書いている |
| 7 | 残存 | 今回 #2 |
| 8 | 解消 | `org-delegate/SKILL.md.in:260` に 5 番目のトリガー（監視ロールの blocking wait・lifecycle 変更）がある |
| 9 | 解消 | `feedback_relay_user_decisions` と「3. ブロック報告」が `org-escalation` に無い |
| 10 | 残存 | 今回 #3 |
| 11 | 解消 | 「sonnet だと不安定」「Opus 固定」が `.dispatcher/CLAUDE.md` に無い |
| 12 | 残存（一部） | secretary-resume（`:8` は「ディスパッチャーは既に生きている前提」）と org-resume は直った。dispatcher-resume の `:10` が残る（今回 #15） |
| 13 | 解消 | 「キュレーター → 窓口に escalate」が skills に無い |
| 14 | 解消 | `pane-layout.md.in:34` は spawn-flow の 3-1b / 3-1c を指している |
| 15 | 解消 | secretary-handover / secretary-resume に `py -3 tools/journal_append` も `skipping)` も無い |
| 16 | 解消 | `org-setup/SKILL.md:204` の denyRead 一覧に `~/.config/gh` が無く、`.gitignore:23` の参照も無い |
| 17 | 解消 | `permissions.md:495` は「素の `git fetch:*` は allow、`git -C * fetch` 等は deny」と書き直されている |
| 18 | 解消 | work-discovery に「別 Phase」の記述が無い（今回の要約も「解消」に挙げている） |
| 19 | 解消 | 「一括提案になる見込み」「窓口が手動で行う」が `skill-audit` に無い |
| 20 | 解消 | 「15 kind」が runtime-release に無い |
| 21 | 残存 | `org-conveyor/SKILL.md:91`。今回の Flag only（輸送層） |
| 22 | 解消 | ユーザー階層の写しを削除済み（対象範囲の差を参照） |
| 23 | 残存 | 今回の Flag only（`org-start/SKILL.md:73` と `org-down/SKILL.md:142`） |
| 24 | 残存 | 今回の Flag only（`org-pull-request-{transport,recv-model}.broker.md` の断片） |
| 25 | 残存 | 今回の「Plugins」節の ponytail |
| 26 | 判定対象外 | wezterm-panes はリポジトリ外のプラグインで、今回の監査は「clean」としている |
| 27 | 残存 | `org-setup/SKILL.md:13,15,114-119` に `python` が、`permissions.md:173-174` に `py -3` が残る。今回の監査は再指摘していない |
| 28 | 残存 | 今回 #17 |
| 29 | 残存 | 今回 #18 |
| 30 | 残存（一部） | パイロットの経緯は消えた。`CLAUDE.md:57,64,73,83,91` の見出し・本文に Issue 番号が残る。今回の要約 3 が「Issue numbers」として扱っている |
| 31 | 残存 | 今回 #4 |
| 32 | 解消 | `org-suspend/SKILL.md.in` に renga 1.x の記述が無い |
| 33 | 残存（一部） | 着手前に引用された文言は消えた。同じファイル群の経緯記述が今回 #42・#44 に挙がっている |
| 34 | 残存 | 今回 #23 |
| 35 | 残存（一部） | org-start と skill-eligibility-check は直った。`skill-audit/SKILL.md.in:137` の「オンデマンド化後」と org-curate が残る（今回 #36） |
| 36 | 残存 | `org-suspend/SKILL.md.in:15,133` は `python -m` で `--root` が無い。`org-resume/SKILL.md:53` は `--no-strict` が無い（今回の Flag only） |
| 37 | 残存 | 今回 #19 |
| 38 | 解消 | `worker-claude-template.md` は削除済みで、rebase の決まりは `tools/templates/worker_brief_normal.md` と `worker_brief_self_edit.md` に入っている |
| 39 | 解消 | 着手前の「Step 0.7 の節が存在しない」は当たらなくなった（`org-delegate/SKILL.md.in:181` に Step 0.7 がある）。同じファイルの別の食い違いが今回 #14 に挙がっている |
| 40 | 残存 | 今回 #39 |
| 41 | 残存 | `org-retro/SKILL.md.in:24` の `dual-system-header-long` の include と `:13` の `send_message` が残る。今回の監査は再指摘していない |
| 42 | 残存 | `org-conveyor/references/scope-contract.md:27,32`、`exit-conditions.md:32` の `feedback-*`、`pr-watch-pane/SKILL.md.in:31`（今回 #22 と Flag only） |
| 43 | 残存 | 今回の Flag only（`/verify`） |
| 44 | 残存 | 今回 #16 |
| 45 | 残存 | 今回 #32 と Flag only（org-attach:55-66） |
| 46 | 残存 | `pr-watch-pane/SKILL.md.in:4-7` の description は変わっていない。今回の監査は再指摘していない |
| 47 | 残存 | 今回 #40 |
| 48 | 残存 | 今回の Flag only（org-attention-start のヘッダ） |
| 49 | 残存 | 今回 #27 |
| 50 | 解消 | work-discovery に `/home/happy_ryo` の固定パスが無い |
| 51 | 解消 | instruction-template に「長文プロンプト形」「gpt-5.5」が無い |
| 52 | 残存（一部） | 「直近の実例」は消えた。runtime-release:67-68 の日付付きの例などが今回 #44 に挙がっている |
| 53 | 残存 | 今回の Flag only（yomiyasu、上流のもの） |
| 54 | 判定対象外 | プラグイン間のトリガー重複。今回の監査は触れていない |

### 着手前の「Low confidence / flag only」

| 項目 | 判定 | 根拠 |
|---|---|---|
| 存在しない `/org-resume` dogfood scan への参照 | 解消 | dogfood 関連ファイルを削除済み（今回の要約） |
| capability-first gate の呼び出し元一覧に org-delegate の `list_peers` が無い | 残存 | 今回の Flag only |
| `.dispatcher/CLAUDE.md` の `plan_version < 2` 分岐 | 解消 | 記述が `.dispatcher/CLAUDE.md` に無い |
| dispatcher-resume から追跡外の `knowledge/raw` へのリンク | 解消 | `dispatcher-resume/SKILL.md.in` に `knowledge/raw` が無い |
| org-dashboard の 8099 固定 | 解消 | `org-dashboard/SKILL.md:40-43` は 8099→8100→8101 の順で探す |
| wsl-windows-chrome-screenshot のオペレーター固有パス | 残存 | 今回 #37 |
| org-suspend の手順番号「4.」の重複 | 解消 | 該当箇所に重複が無い |
| org-down の誤字「jud断」 | 残存 | 今回 #38 |
| オペレーターの memory との食い違い | 残存 | 今回の Flag only |
| `~/.claude/CLAUDE.md` と `.claude/rules/pr-ci-watch.md` は問題なし | 変化なし | 今回も問題なし |

### 今回の新規の指摘

着手前に無かったもの: High の #5〜#14、Medium の #20・#21・#24〜#26・#28〜#31・#33〜#35・#41〜#43。Flag only のうち、輸送層の org-setup:26・renga-error-codes:169・claude-org-self-edit:80・org-start:59-67・`dual-system-header-*` 断片、dispatcher-resume:346、org-start:346、skill-audit の check 1.2。プラグインのうち skill-creator（synced 版）と frontend-design。

### 残存と新規の分類

#### 輸送層の記述（第 2 段階へ）

計画書 §1-1 の凍結対象。第 1 段階では直さない。

- `ORG_TRANSPORT` 未設定時の解決が食い違う: `org-start/SKILL.md:73`（renga）と `org-down/SKILL.md:142`（broker）。org-setup:26 も「無設定は renga として no-op」と書くが、コードは broker に写像する（着手前 #23、今回 Flag only）
- `notify_peer` が raw env で判定するという記述: `org-conveyor/SKILL.md:91` と `org-pull-request-{transport,recv-model}.broker.md`（着手前 #21・#24）
- `pr-watch-pane/SKILL.md.in:110-112` の「`ORG_TRANSPORT` 未設定で silent no-op」（今回 #5）。今回の監査は High の rewrite に分類したが、未設定時の既定 transport の記述なので凍結対象に入れる
- 「既定 renga（無設定）」の記述: renga-error-codes:169、claude-org-self-edit:80、org-start:59-67、`dual-system-header-*` 断片の Issue・版の経緯（今回 Flag only）
- org-attention-start のヘッダが手書きで renga のツール名だけを持つ（着手前 #48）
- org-retro の輸送層ヘッダの include と `send_message` の allowed-tools（着手前 #41）。ヘッダの文言は凍結対象。include を外すのは文言の変更ではないが、第 2 段階でヘッダを整理するときにまとめて扱う
- org-attach:55-66（今回 Flag only）

#### 直す

下の「Issue 案」にまとめた。

#### 直さない（理由付き）

- dispatcher-resume:346「サイクルごとに何も出力しない」: `.hooks/check-loop-directive.sh` がこのブロックを逐語で照合している。消すと hook が壊れる
- org-start:346 の `/loop` 再武装: ディスパッチャーの挙動への回避策で、現象がまだ起きるかをリポジトリから確かめられない。再試験で再現しないと分かるまで消さない
- 太字の多さ（今回 #43）: 全ファイルに及び、どこまで減らすかの基準が無い。各ファイルを別の理由で触るときに、取り消せない操作の決まり以外の太字を減らす
- リポジトリ外のもの（ponytail・skill-creator・frontend-design・codex の各プラグイン、`~/.claude/skills/yomiyasu`、オペレーターの memory との食い違い、着手前 #26・#54）: リポジトリの変更では直せない。扱いはオーナーが決める

### 「直す」の Issue 案

1. **org-pull-request と pr-watch-pane の古い事実と、禁止されている起動の事前許可を直す**
   org-pull-request の allowed-tools が、rule と hook が禁じる `tools/pr-watch.*` の直接起動を事前許可している（今回 #1）。cwd 依存の説明（#2）・存在しない memory 参照（#3）・events の poll を巡る同一ファイル内の矛盾（#4）・`feedback-*` 参照と PR のスコープ注記（#22・#23）も残っている。本文が呼ぶ `watcher_restart_guard.py` と `self_edit_approval.py` が allowed-tools に無い（#40）。
2. **コードや他のファイルと食い違っている記述を直す**
   work-discovery のランク（#6）、skill-audit の `skill_count`（#7）、org-conveyor の transport ツールと反復数（#8・#9）、org-attach の fallback（#12）、org-attention-start の「Block A-1」（#13）、claude-org-self-edit の「3 点」（#14）を直す。あわせて次も揃える。
   - `CLAUDE.md:65` の再送時の説明を、dispatcher-resume の `already_consumed` と `INDETERMINATE` に合わせる（着手前 #4）
   - org-conveyor の `/verify` の名前を確かめる（着手前 #43）
   - state_db importer の再構築コマンドを org-start / org-resume / org-suspend で揃える（着手前 #36）
   - capability-first gate の表に org-delegate の `list_peers` を足す
3. **org-setup の 2 ファイルの食い違いとコマンド名を直す**
   親ディレクトリの設定を読むかどうか（#10）と、ディスパッチャーが `bypassPermissions` を使う理由（#11）が、`SKILL.md` と `permissions.md` で食い違っている。「main pull 後の 1 回必須」（#27）を初回セットアップの手順として書き直す。`python` / `py -3` を `python3` に揃える（Windows の代替として明示する箇所は残す。着手前 #27）。
4. **キュレーター常駐の前提と resume のトリガー重複を片付ける**
   dispatcher-resume:10 は、常駐しないキュレーターを「生きている前提」と書いている（#15）。Step 0 と冒頭の更新規則が食い違う（#28）。secretary-resume と「resume」「引き継ぎから再開」で発動が重なる（#29）。Issue 由来の文言（#30）と「従来どおり」（#31）も直す。
5. **常時読み込まれる CLAUDE.md の経緯記述を rationale へ移す**
   `.dispatcher/CLAUDE.md:82-83` の relay 障害の経緯（#16）、root `CLAUDE.md` の見出しの Issue 番号（着手前 #30）と「本 Issue は明文化のみ」（#17）を移す。`.curator/CLAUDE.md:11` の「旧仕様」（#18）も対象。決まりと理由は残し、経緯は `references/rationale.md` へ移す。
6. **skills 本文の経緯・移行表現・固定値を消す**
   - 消す・言い換えるもの: 「Sonnet 5」の固定（#19）、保守者向けの注記（#20・#21）、事故の日付（#25）、Issue 番号（#33・#35）、「オンデマンド化後」（#36）、版の経緯（#41・#42・#44）
   - 重複を 1 つにまとめるもの: 二重の禁止（#24）、理由の無い強調（#26）、固定間隔の更新（#34）
   - その他: オペレーター固有のパス（#37）、誤字（#38）、org-attach の description（#32）
   ファイル単位に分けて出してよい。
7. **org-curate の 0-A 送信判定を 1 つの規則に書き直す**
   Step 0-A は同じ規則を何度も書いたうえで、送信は無害（:90）と早期 close の危険（:82）が食い違っている（#39、着手前 #40）。今回の監査は「送信そのものが壊れやすい操作」として、パッチに入れずに文案だけを出した。人のレビューを前提に別 Issue にする。
8. **skill-audit の check 1.2 と org-retro の work-skill テンプレートを見直す**
   `origin:` を持つ skill が 0 件のため、check 1.2 は何も検査していない。一方で org-retro のテンプレートは新しい skill に `origin` と「背景」節を求め、指示書に経緯を書き込ませている。どちらに揃えるかを決める。

## 参考: `.dispatcher` 単独実行にだけある指摘

比較には使っていない。既定範囲の実行と重なるもの（`.dispatcher/CLAUDE.md:82-83` の経緯、輸送層の既定の二系統）は省いた。

- `.dispatcher/references/pane-close.md:5`「broker の受信はナッジ + `check_messages` の pull」: 契約（`docs/contracts/backend-interface-contract.md` の Surface 8 改訂）は push が一次。輸送層の記述なので第 2 段階へ
- `.dispatcher/CLAUDE.md:71` の renga のバージョン固定（0.14.0+ / 2.0.0）: 輸送層。第 2 段階へ
- `.dispatcher/CLAUDE.md:50` の事故事例の日付と、`:42,48,52` の見出しの「（重要）」: Issue 案 5 に含めてよい
- `.dispatcher/references/worker-monitoring.md.in` の約 60 箇所にある `(Issue #N)` などの括弧書き: Issue 案 5 に含めてよい
