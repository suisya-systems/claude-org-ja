> **このドキュメントは歴史的参考資料です。** Secretary / worker / dispatcher の標準オペレーションでは参照しないでください。本手順自体が `claude-org-runtime settings generate` 等の runtime CLI に依存するため、runtime CLI 障害時のフォールバックとしても機能しません。標準経路 (`tools/gen_delegate_payload.py apply`) が想定外の出力を返した場合は、手動再現せずに Issue を切り、resolver / runtime 側のバグが直るまで該当 delegation を pause してください。例外的に手作業を行うかどうかはユーザーの明示判断に委ね、Secretary が自走で本ドキュメントに reach した場合は protocol 違反です。

# Legacy hand-typed delegation path (museum copy)

This file preserves the pre-Issue-283 manual delegation procedure for archaeological reference. It was extracted from `.claude/skills/org-delegate/references/delegate-flow-details.md` § "Legacy hand-typed paths" and the `### legacy / fallback 経路` subsection of `.claude/skills/org-delegate/SKILL.md` (Issue #313).

## Why this is no longer in the active skill

Documenting a hand-typed fallback inside the active skill behaved like an "easy button": when `gen_delegate_payload.py apply` produced an unexpected layout, Secretary defaulted to switching paths instead of treating the resolver output as a regression. Concrete failures historically caused by the legacy reach include:

- **Settings env mismatch** — copying `.claude/settings.local.json` from a sibling worktree without updating `WORKER_DIR`, blocking the new worker on its first Edit/Write via the boundary hook (session #13).
- **drift_check breakage** — manually editing `.state/org-state.md` sections that are DB-owned (`Worker Directory Registry` / `Active Work Items`), causing the next snapshotter run to overwrite the changes and triggering drift_check failures.
- **T1 reservation skipped** — manual `DELEGATE` skips `runs.status='queued'`, so the dispatcher watch loop loses queue visibility and two delegations on the same project both choose Pattern A and collide on the base clone.
- **Pattern misclassification carry-over** — when the resolver itself was wrong (e.g., Pattern A misjudgment for a self-edit task because the Worker Directory Registry was stale), reaching for the manual path masked the underlying resolver bug rather than filing it.

Today, if `gen_delegate_payload.py apply` errors or produces a wrong layout, the canonical response is to **file an Issue against `gen_delegate_payload.py` (or its resolver) and pause the affected delegation until the underlying bug is fixed**. Whether to invoke any manual workaround at all is a user judgment call — Secretary must not self-grant the exception. Note that the procedure below also depends on `claude-org-runtime` and is therefore not a general fallback when the runtime CLI itself is unavailable; in that case, restoring the runtime CLI is the prerequisite.

## Legacy procedure (verbatim, do not use)

Two pre-Issue-283 paths used to be supported for callers that already worked in that idiom:

- `python tools/gen_worker_brief.py --config <path>.toml --out <CLAUDE.md>` — the original brief renderer. Still works exactly as before. New code should prefer the `from-task` subcommand because it derives `worker.dir` / `worker.pattern` / `worker.role` deterministically from registry and state.db rather than asking the operator to fill them in.
- Manually issuing the `DELEGATE:` message via `mcp__renga-peers__send_message` — fine in the past for one-off ad-hoc dispatches. The `gen_delegate_payload preview` command can still be used to draft the body without writing anything.

Operationally that meant:

- `python tools/gen_worker_brief.py --config <task>.toml --out <CLAUDE.md>` で brief を手動生成
- `claude-org-runtime settings generate` を **`--role` を窓口で確定させた上で** 直接呼ぶ
- DELEGATE 本文は `.claude/skills/org-delegate/references/delegate-flow-details.md` §3 のテンプレートに従って手書きし、`mcp__renga-peers__send_message(to_id="dispatcher", message=…)` で送る

Both paths skip the T1 reservation and therefore do not surface the queued state to the dispatcher's watch loop.
