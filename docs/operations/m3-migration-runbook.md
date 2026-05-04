# M3 ../workers/ Physical Migration Runbook

**Owner:** secretary (operator). Worker scope ends at tooling; live `../workers/` mv is performed under this runbook in a follow-on ops issue.

**Refs:** Issue #267 / `migration-strategy.md` §M3 / `directory-layout.md` §5
**Tooling:** `tools/state_db/migrate_workers.py`, `tools/state_db/curator_archive.py`

This runbook drives the one-shot migration of the flat `<workers_root>/` (≈130 dirs) into the 3-tier `<project>/_runs/<workstream>/<run>/` layout.

---

## 0. Prerequisites

- All active worker panes suspended (`/org-suspend`).
- Active runs in DB equal 0:
  ```bash
  sqlite3 .state/state.db "SELECT COUNT(*) FROM runs WHERE status IN ('in_use','review')"
  ```
- All target dirs live on the same drive as the source (`C:` for the production setup); cross-drive paths fail the `--keep-compat` junction step. The migrator's preflight surfaces this automatically.
- A current backup of `../workers/` exists (`Compress-Archive` or copy to a separate drive).
- The repository at `state-db-hierarchy-design/inventory.json` is up to date.

## 1. Pre-flight (DB / drift)

```bash
python -m tools.state_db.importer --rebuild --no-strict
python -m tools.state_db.drift_check     # expect: 0 differences
```

Snapshot every project's worktrees so they can be cross-checked after each step:

```bash
for proj in ccmux claude-org claude-org-en claude-org-runtime core-harness; do
  git -C ../workers/$proj worktree list > .state/m3-pre-$proj-worktrees.txt
done
```

## 2. Generate plan

```bash
python -m tools.state_db.migrate_workers \
  --plan \
  --inventory ../workers/state-db-hierarchy-design/inventory.json \
  --workers-root ../workers \
  > .state/m3-plan.txt
python -m tools.state_db.migrate_workers \
  --plan --json \
  --inventory ../workers/state-db-hierarchy-design/inventory.json \
  --workers-root ../workers \
  > .state/m3-plan.json
```

**Sanity check the plan:**
- Operation count ≈ 167 (130 entries + ensure_dirs + 4 project renames).
- The 3-step `claude-org` ⇄ `claude-org-en` swap is present and ordered: `claude-org → claude-org-ja-tmp`, then `claude-org-en → claude-org`, then `claude-org-ja-tmp → claude-org-ja`.
- `ccmux → renga` precedes any move targeting `renga/_runs/...`.
- All `move_run` targets sit under `<workers_root>/<project>/_runs/<workstream>/<run>/` (or `_archive/<YYYY-Qx>/...` for `archive_candidate`).
- The `_research/_runs/{ccswarm,anthropic,claude-org-audit,_solo}` cluster is materialised via `ensure_dir`.
- No preflight `warning:` lines on stderr (cross-drive / source missing / target conflict).

If anything is off, **stop here** and rebuild the inventory before going further.

## 3. Apply by tier (subset-at-a-time)

To stage the migration in tiers, take `.state/m3-plan.json`, copy it per tier, and **delete the operations you want to defer** in each copy. Then feed each trimmed file through `--from-manifest`:

```bash
cp .state/m3-plan.json .state/m3-plan-step1-scratch.json   # then edit
python -m tools.state_db.migrate_workers \
  --apply --confirm \
  --from-manifest .state/m3-plan-step1-scratch.json \
  --manifest      .state/m3-executed-step1.json
```

`--from-manifest` replays the supplied operations exactly; it does NOT regenerate the plan from inventory. The `workers_root` and `archive_quarter` baked into the manifest are honoured (so `--workers-root` here is ignored). Preflight (cross-drive / source-missing / target-conflict) still runs against the loaded ops. Use `--manifest` to choose where the executed-op log is written (the migrator persists it incrementally after every successful op so a mid-batch failure still leaves a valid rollback record).

Recommended tier order:

1. **`_scratch` cluster** — lowest risk, no worktrees.
2. **`_research` cluster** — read-only audit dirs.
3. **Project renames** (`ccmux → renga`, then 3-step `claude-org` swap) — highest risk; treat as one atomic step.
4. **Remaining run moves** under `claude-org-ja/`, `claude-org/`, `core-harness/`, `renga/`, `claude-org-runtime/`.
5. **`archive_candidate`** — moves into `_archive/<YYYY-Qx>/...`.

To apply the entire plan in one shot (no staging):

```bash
python -m tools.state_db.migrate_workers \
  --apply --confirm \
  --inventory ../workers/state-db-hierarchy-design/inventory.json \
  --workers-root ../workers \
  --manifest .state/m3-executed.json \
  --keep-compat                          # OFF by default; ON during tier 3
```

**Per-step verification:**

```bash
# 1. worktrees still alive
for proj in renga claude-org-ja claude-org claude-org-runtime core-harness; do
  git -C ../workers/$proj worktree list
done
# 2. each worktree healthy
for wt in $(git -C ../workers/claude-org-ja worktree list --porcelain | awk '/^worktree/{print $2}'); do
  git -C "$wt" status >/dev/null && echo OK $wt
done
# 3. DB ↔ FS sync
python -m tools.state_db.importer --rebuild --no-strict
python -m tools.state_db.drift_check
```

If any worktree reports `fatal: not a git repository`, run `git -C <project> worktree repair` (the migrator already does this for project-tier ops; the repair is idempotent).

## 4. The `claude-org` ⇄ `claude-org-en` swap (3 steps)

This is the only step that uses the intermediate slug. The migrator emits the three rename ops back-to-back; do not interleave anything else.

```
mv ../workers/claude-org          ../workers/claude-org-ja-tmp
mv ../workers/claude-org-en       ../workers/claude-org
mv ../workers/claude-org-ja-tmp   ../workers/claude-org-ja
```

After step 1 and step 3, `git worktree repair` runs against the **current** path of the moved repo (steps 1 and 3 carry `claude-org`'s 31 worktrees). After step 2, `git worktree repair` runs against `claude-org` (now the former `claude-org-en`).

**Verification specific to the swap:**

```bash
git -C ../workers/claude-org-ja remote -v   # → suisya-systems/claude-org-ja
git -C ../workers/claude-org    remote -v   # → suisya-systems/claude-org
```

If origins are crossed, **rollback immediately** (see §6).

## 5. Backward-compat junctions (optional)

`--keep-compat` lays down NTFS junctions from old paths to new paths so legacy tooling that hard-codes `../workers/<old-name>` keeps working for ~1 week. Junctions are single-drive only on Windows; the migrator's preflight already checks this.

After 7 days of zero hits in audit logs, remove junctions:

```bash
python -m tools.state_db.migrate_workers --rollback --manifest .state/m3-manifest-step-N.json --workers-root ../workers
```

— but only if the rest of the migration is being undone too. To remove **only** the junctions while keeping the moves, delete each one manually:

```bash
cmd /c rmdir ../workers/<old-name>
```

## 6. Rollback

If a step fails or an audit reveals a wrong target, run:

```bash
python -m tools.state_db.migrate_workers --rollback \
  --manifest .state/m3-manifest-step-N.json \
  --workers-root ../workers
```

This walks the manifest in reverse, removes any junctions placed by `--keep-compat`, and renames each `dst` back to `src`. Worktree fixups re-run for project-tier ops.

`ensure_dir` ops are intentionally **not** undone — empty `_runs/_solo/` shells are harmless and may already contain content from concurrent migrations.

After rollback, re-run `python -m tools.state_db.importer --rebuild --no-strict` to bring the DB back in line.

## 7. After all tiers applied

```bash
# every dir is at its target — re-running --plan against the same
# inventory after a successful migration should yield only ensure_dir
# lines: the planner's "src missing && dst present → already migrated"
# guard skips moves whose source has been consumed.
python -m tools.state_db.migrate_workers --plan \
  --inventory ../workers/state-db-hierarchy-design/inventory.json \
  --workers-root ../workers
```

```bash
# hot/cold curator dry-run
python -m tools.state_db.curator_archive --dry-run --workers-root ../workers
```

Any candidates returned here are *expected* (90-day-old `lifecycle='active'` dirs that should now be archived). Apply with `--apply` once the migration is otherwise stable.

## 8. Curator batch (post-migration, ongoing)

Schedule `curator_archive` weekly:

```bash
python -m tools.state_db.curator_archive --apply --workers-root ../workers
python -m tools.state_db.curator_archive --purge   # delete_pending → physical rm
```

`--apply` moves `lifecycle='active'` rows whose dir mtime > 90 days into `_archive/<YYYY-Qx>/<project>/<workstream>/<run>/`, updating `worker_dirs.abs_path` and flipping `lifecycle='archived'`. `--purge` physically removes `delete_pending` rows and their on-disk dirs.

## 9. Failure modes & escape hatches

| Symptom | Likely cause | Action |
|---|---|---|
| `preflight: source missing` | inventory drifted from FS | rebuild inventory, re-plan |
| `preflight: target already exists` | partial earlier run, or stale junction | inspect target; `rollback` the failing manifest |
| `git worktree status` reports `not a git repository` after a project rename | `git worktree repair` was skipped | re-run `git -C <project> worktree repair` (idempotent) |
| `cross-drive path detected` warning | source / target span different drives | drop `--keep-compat`; use manifest-only mode |
| DB `drift_check` non-zero post-apply | importer not re-run, or abs_path mismatch | re-run importer; if drift persists, rollback |
| `curator_archive --apply` errors mid-batch | one row's mv failed | the script rolls that row back automatically; inspect, fix, re-run |
