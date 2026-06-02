# Worker Git Guardrails Design (Phase 2)

> **Status**: **Normative target design + current enforcement gap**.
>
> This document is the Phase 2 deliverable in the multi-phase effort to
> harden the sandbox boundary for every role × directory-pattern combination
> in the claude-org organization. It is layered on
> [`docs/contracts/role-pattern-sandbox-contract.md`](./role-pattern-sandbox-contract.md)
> (Phase 0, the prescriptive surface and current-state attribution for
> filesystem reads / writes per role × pattern), and it sits ahead of the
> Phase 1 schema mechanization that emits a `sandbox` block from
> [`tools/org_extension_schema.json`](../../tools/org_extension_schema.json).
>
> Phase 2 narrows scope to **worker-specific Git guardrails on Pattern B
> worktrees** — the directory pattern where a worker's `.git` is a *file*
> pointing back into a shared base clone's `.git/worktrees/<task_id>/`, and
> the common object store / branch ref namespace / repo `.git/config` are
> shared with the secretary and with sibling-task workers. The worker must
> not corrupt that shared metadata, and the contract mechanism must keep it
> from doing so even when a misbehaving prompt instructs the worker to.
>
> Each design row carries **two distinct claims**, mirroring the Phase 0
> contract style:
>
> 1. **Prescriptive surface** — what guardrail the role × subcommand
>    combination *should* enforce on Pattern B. This is the normative target.
> 2. **Current enforcement state** — which Layer (2 / 3 / 4) actually
>    enforces it today against `worker_roles.default` and
>    `worker_roles.claude-org-self-edit` as they are emitted by the released
>    [`claude-org-runtime`](https://pypi.org/project/claude-org-runtime/) +
>    [`tools/org_extension_schema.json`](../../tools/org_extension_schema.json),
>    and which surfaces remain `gap` (i.e. covered only by convention or by
>    a single layer that can be bypassed).
>
> Phase 2 does **not** introduce a hook script change, a schema change, or
> a tightening of `permissions.deny` in the codebase. Where the prescriptive
> surface and the current state diverge, this document marks the row
> `gap → Phase 1` (schema-level path deny) or `gap → Phase 2.x` (a separate
> command-level / git-aware-wrapper deliverable spun out of this design).
>
> **Out-of-scope by design (Codex Nit)**: `git push` itself is **not
> redesigned** here. Worker `permissions.deny` ([`Bash(git push *)`](../../tools/org_extension_schema.json))
> plus [`.hooks/block-git-push.sh`](../../.hooks/block-git-push.sh) is the
> existing double defense and stays as-is. Force-push and friends remain in
> [`.hooks/block-dangerous-git.sh`](../../.hooks/block-dangerous-git.sh)
> scope; this document recommends only that the script gets *attached* to
> the worker template (it is not today — see §5).
>
> **Adverse design rejected up front (Codex Blocker)**: an earlier sketch
> proposed *"git operations are allowed if and only if they touch the
> current worktree's branch / metadata; deny everything else"*. That design
> is **unimplementable in the current claude-org mechanism** and is not
> pursued. The reasons are spelled out in §1.3.

---

## 1. Scope and design constraints

### 1.1 What this document fixes

The Phase 0 contract pinned the Pattern B Git metadata boundary as a
**path table** (per
[`docs/contracts/role-pattern-sandbox-contract.md`](./role-pattern-sandbox-contract.md)
§4.2.1): which paths under the base clone's `.git/` a Pattern B worker may
read or write, and which it may not. Phase 0 stopped short of saying *which
git subcommands actually touch which path*. Without that mapping, the
worker template cannot decide which subcommands to deny — `git status`
touches the per-worktree index, `git fetch` writes the base clone's
`refs/remotes/`, `git worktree prune` rewrites the base clone's
`.git/worktrees/` administrative dirs, and so on; the danger is not
uniform across "git" as a verb.

Phase 2 bridges that gap: it classifies each git subcommand by the
**Pattern B metadata boundary category** it can write to, applies a
guardrail policy to each category, and audits the existing hook scripts
for fitness. The output of this doc is a design that the next-phase
implementer (schema or hook plumbing) can mechanize without re-deriving
the categories or re-litigating the rejected designs.

### 1.2 Mechanism limits inherited from Phase 0

Three Phase 0 facts constrain every design choice in this document:

- **Layer 4 hooks see strings, not git semantics.** Every existing hook in
  [`.hooks/`](../../.hooks/) that targets git subcommands (`block-git-push.sh`,
  `block-dangerous-git.sh`, `block-no-verify.sh`) is a `Bash`-matcher hook
  that inspects `tool_input.command` as a shell string and checks for
  literal substrings or per-segment patterns. None of them resolves
  `git rev-parse --git-dir`, `git rev-parse --git-common-dir`, the worktree
  path, or the target branch / ref. They cannot tell whether `git checkout
  feature-x` touches *this* worktree or a sibling. This is documented in
  the segment-split design at
  [`.hooks/block-dangerous-git.sh`](../../.hooks/block-dangerous-git.sh)
  §"検知方針".
- **Layer 3 sandbox sees syscalls, not git semantics.** The bubblewrap
  mount-namespace controls reads / writes at the file path level. A
  `denyWrite` on `<base_clone>/.git/worktrees/<other_task>/` blocks every
  syscall against that path regardless of whether `git`, `cat`, or
  `python` issued it — but it cannot distinguish "git did this for a
  legitimate reason" from "git did this because the user invoked
  `worktree remove`". The sandbox is the right layer for path-isolation
  guarantees and the wrong layer for subcommand-meaning judgments.
- **Layer 2 `permissions.deny` is also string-level**, glob over
  `Bash(...)` invocations. It can reject `Bash(git push --force*)` but it
  cannot reject `Bash(git checkout other-branch)` *only when other-branch
  is not this worktree's branch* — that is a per-call computation outside
  Claude Code's permission language.

Any guardrail that requires "did this command target *this* worktree's
metadata" is therefore not expressible as a hook + schema combination
**without a new git-aware wrapper that runs as Layer 4 and shells out to
git itself for the resolution**. This document calls out which rules
require such a wrapper as a separate deliverable rather than smuggling
them into the existing hook chain.

### 1.3 Adverse design rejected (Codex Blocker)

The naive Phase 2 design is:

> *"Allow any git subcommand that affects only the current worktree (its
> own branch, its own index, its own HEAD). Deny anything that affects a
> sibling worktree or the shared base."*

This design **cannot be implemented** with the current mechanism because:

1. The Layer 4 Bash hooks operate on `tool_input.command` as a string. To
   know that `git checkout feature-X` is "current-worktree-only", the
   hook would have to (a) parse the command, (b) shell out to
   `git -C "$cwd" rev-parse --git-dir` and to
   `git -C "$cwd" rev-parse --git-common-dir`, (c) resolve `feature-X` to
   a ref and check whether any other worktree's `HEAD` resolves to it,
   (d) check whether the operation will rewrite a `refs/heads/feature-X`
   that another worktree has checked out. None of the existing hooks do
   any of these; the segment-split utility at
   [`.hooks/lib/segment-split.sh`](../../.hooks/lib/segment-split.sh) is
   regex-only and explicitly does not resolve subshells, alternate
   `$()` syntax, or backslash-escapes.
2. The Layer 3 sandbox cannot project a "current-worktree" identity onto
   `<base_clone>/.git/worktrees/<task_id>/` paths in a way that allows
   only this `<task_id>`. The sandbox profile is per-worker (the
   worker's `<task_id>` is known at generation time), so a per-task
   `additionalDirectories` entry could in principle isolate the per-
   worktree dir. But the **shared base** dirs (`refs/heads/`,
   `objects/`, `packed-refs`, `config`) are write-needed for ordinary
   commits — a pure path deny on those breaks `git commit`. This is the
   Phase 0 §4.2.1 finding; it is not re-derivable in the hook layer.
3. Even if (1) and (2) were solvable, the cost of adding a git-resolution
   shell-out to every `Bash` invocation is significant: hooks run on
   *every* Bash call, not just git ones, and the `git` shell-out would
   add tens of milliseconds per Bash tool invocation in steady state.
   The performance cost is not trivially absorbable.

The conclusion the Codex pre-design review reached, which this document
adopts, is:

> **Design as either (a) all-deny-by-default with a narrow allow list at
> the subcommand level, or (b) deny-by-category at the boundary level, with
> a git-aware wrapper as a separate deliverable for any rule that requires
> per-call git-state resolution.**

This document picks **(a) for the worker template** (no new wrapper, but
with the existing scripts attached and tightened) and explicitly carves
**(b) into a Phase 2.x git-aware-wrapper item** for the few rules that
genuinely need it (notably `worktree` subcommand differentiation and
cross-branch operations). §8.3 enumerates the wrapper's scope.

### 1.4 What this document does NOT change

- **No code or test changes in this PR.** The deliverable is design only,
  to be mechanized in a follow-up.
- **No change to how `git push` is handled.** The existing
  [`.hooks/block-git-push.sh`](../../.hooks/block-git-push.sh) +
  `permissions.deny` double defense stays. §9 documents the boundary.
- **No change to the Phase 0 path-table contract.** Phase 0 §4.2.1 remains
  authoritative for the boundary. Phase 2 maps subcommands to those
  boundaries; it does not redraw the boundaries themselves.
- **No new role.** This document recommends changes to the existing
  `worker_roles.default` and `worker_roles.claude-org-self-edit`
  templates only.

---

## 2. Pattern B Git metadata boundary recap

The five boundary categories Phase 0 §4.2.1 fixed are summarized below.
Section 3 maps every relevant git subcommand into these categories.

| Category | Path examples (per Phase 0 §4.2.1) | Read | Write |
|---|---|---|---|
| **(B1)** Current worktree local metadata | `<base_clone>/.git/worktrees/<task_id>/HEAD`, `.../index`, `.../ORIG_HEAD`, `.../logs/HEAD`, `.../*.lock` | allowed | allowed |
| **(B2)** Shared base metadata (write-needed) | `<base_clone>/.git/objects/`, `<base_clone>/.git/refs/heads/<this_branch>`, `<base_clone>/.git/packed-refs`, `<base_clone>/.git/gc.lock` | allowed | allowed (git rewrites these as part of normal commits) |
| **(B3)** Shared base metadata (read-only) | `<base_clone>/.git/HEAD`, `<base_clone>/.git/config`, `<base_clone>/.git/refs/heads/<other_branch>`, `<base_clone>/.git/refs/remotes/`, `<base_clone>/.git/hooks/` | allowed | **denied** |
| **(B4)** Other worktree metadata | `<base_clone>/.git/worktrees/<other_task>/**` | denied | denied |
| **(B5)** Working-tree boundary (the base clone's working tree files) | `<base_clone>/...` (anything not under `.git/`) | denied | denied |

In addition, **(N)** remote / network operations and **(V)** verification-
bypass flags are mechanism-orthogonal to the path table — they are
classified here for completeness because they are the locus of the
existing hook chain.

---

## 3. Subcommand classification

### 3.1 Classification axes

For each git subcommand the worker plausibly invokes, this table records:

- **Boundary category written** — which of (B1) / (B2) / (B3) / (B4) /
  (B5) the subcommand can write to in the worst case (i.e. with adverse
  flags). A subcommand that *only reads* is marked `–` in the write
  column.
- **Boundary category read** — same, for reads. Reads are usually
  uncontroversial because (B1) / (B2) / (B3) are all read-allowed for
  Pattern B; the column is filled in to flag (B4) / (B5) reads that the
  Phase 0 contract denies.
- **Network / verification axis** — whether the subcommand crosses the
  network (N) or has a verification-bypass option (V) regardless of
  boundary category.
- **Phase 2 disposition** — `allow` / `allow w/ flag deny` / `deny` /
  `deny + wrapper`. The wrapper case names which Phase 2.x rule applies.

### 3.2 Master subcommand × boundary table

The columns track *which categories the subcommand can write to in the
worst case* — i.e. with the most adverse standard flag combination. Where
a flag is the only thing that pushes the subcommand into a worse
category, that flag is named in the "Adverse flag" column.

| Subcommand | Reads | Writes | Network | Verify-bypass | Adverse flag(s) | Phase 2 disposition |
|---|---|---|---|---|---|---|
| `git status` | (B1)(B2)(B3) | – | – | – | – | **allow** (no boundary write) |
| `git log` | (B1)(B2)(B3) | – | – | – | – | **allow** |
| `git diff` | (B1)(B2)(B3) | – | – | – | – | **allow** |
| `git show` | (B1)(B2)(B3) | – | – | – | – | **allow** |
| `git ls-files` / `ls-tree` / `cat-file` | (B1)(B2)(B3) | – | – | – | – | **allow** |
| `git rev-parse` | (B1)(B2)(B3) | – | – | – | – | **allow** |
| `git add` | (B1) | (B1) | – | – | – | **allow** |
| `git commit` | (B1)(B2)(B3) | (B1)(B2) | – | **(V)** `--no-verify` | `--no-verify` | **allow w/ flag deny** ([`.hooks/block-no-verify.sh`](../../.hooks/block-no-verify.sh) — §5) |
| `git restore` (worktree only) | (B1)(B2) | (B1) (current worktree files only via index) | – | – | `--source=<other-ref>` is read-only against (B3) | **allow** |
| `git restore --staged` | (B1) | (B1) (index only) | – | – | – | **allow** |
| `git stash` (push / pop / list / show / drop) | (B1)(B2) | (B1)(B2) (refs/stash for the current worktree) | – | – | – | **allow** |
| `git branch` (list) | (B2)(B3) | – | – | – | – | **allow** |
| `git branch <new>` (create) | (B2)(B3) | (B2) (new ref under `refs/heads/`) | – | – | – | **allow** (but see (B3) write below) |
| `git branch -d / -D / --delete --force` | (B2)(B3) | (B2)(B3) (mutates refs of *other* branches) | – | – | `-D`, `--delete --force` | **deny** ([`.hooks/block-dangerous-git.sh`](../../.hooks/block-dangerous-git.sh) — §5) |
| `git branch -m / --move` | (B2)(B3) | (B3) (rewrites another branch's ref) | – | – | – | **deny + wrapper** (rename of a branch checked out by a sibling worktree breaks (B4) isolation; current hook does not cover this — §8.3) |
| `git checkout <branch>` (switch on this worktree) | (B1)(B2)(B3) | (B1)(B2) (rewrites this worktree's HEAD; touches refs of the target) | – | – | `--force`, `-B`, `--orphan` | **allow w/ flag deny + wrapper** (a checkout to a branch held by a sibling worktree fails by default; the wrapper-needed case is the `-B` recreate, which can stomp another worktree's ref — §8.3) |
| `git switch <branch>` | (B1)(B2)(B3) | (B1)(B2) | – | – | `-c`, `-C`, `--force` | **allow w/ flag deny + wrapper** (same as checkout) |
| `git checkout --` / `git restore .` (discard) | (B1)(B2) | (B1) (working tree) | – | – | – | **allow** (discards in this worktree only; comparable to `reset --hard` *for tracked files* but does not move HEAD) |
| `git reset` (mixed / soft) | (B1)(B2)(B3) | (B1) (HEAD ref of this worktree, optionally index) | – | – | – | **allow** |
| `git reset --hard` | (B1)(B2)(B3) | (B1) (HEAD of this worktree + working tree) | – | – | `--hard` | **deny** (existing [`.hooks/block-dangerous-git.sh`](../../.hooks/block-dangerous-git.sh) — §5) |
| `git clean -fd` | – | (B5)(working tree of this worktree) | – | – | `-f`, `-d`, `-x`, `-X` | **deny** (recommended new entry in `block-dangerous-git.sh` — §5; not currently covered, see TODO at the head of [`.hooks/block-dangerous-git.sh`](../../.hooks/block-dangerous-git.sh)) |
| `git rebase` (interactive or not) | (B1)(B2)(B3) | (B1)(B2) (rewrites HEAD + creates objects; can move ref) | – | – | `-i`, `--force-rebase`, `--exec` | **allow w/ flag deny + wrapper** (rebase onto a branch checked out by sibling worktree races (B4) — wrapper item §8.3; non-interactive same-branch rebase is fine) |
| `git merge` | (B1)(B2)(B3) | (B1)(B2) | – | **(V)** `--no-verify` (commit hook bypass) | `--no-verify`, `--allow-unrelated-histories` | **allow w/ flag deny** (`--no-verify` covered by [`.hooks/block-no-verify.sh`](../../.hooks/block-no-verify.sh) for the resulting commit step; **gap → §5.2 recommend extending hook to merge invocations**) |
| `git cherry-pick` | (B1)(B2)(B3) | (B1)(B2) | – | – | `--no-commit` allows staging, but commit step still hits hook | **allow** |
| `git revert` | (B1)(B2)(B3) | (B1)(B2) | – | – | – | **allow** |
| `git fetch` | (B3) | (B3) (writes `refs/remotes/`) | **(N)** | – | – | **allow** (fetch is read-only on local boundaries except `refs/remotes/` which is in (B3); **gap**: Phase 0 §4.2.1 marks (B3) write as **denied**, so this row is a Phase 0 contradiction unless `refs/remotes/` is carved out separately. **Recommend Phase 0 amendment: add a row for `refs/remotes/<remote>/` allowing reads and *git-mediated writes via fetch* but denying direct edits.** Until the Phase 0 carve-out lands, the conservative reading is "deny `git fetch` from worker" — see §8.1) |
| `git pull` | (B1)(B2)(B3) | (B1)(B2)(B3) | **(N)** | **(V)** `--no-verify` (merge / rebase hook bypass) | `--rebase`, `--ff-only` | **deny** (recommended; pull = fetch + merge/rebase, and the worker has no need to pull from a remote in steady state — the secretary owns network ops. **gap → Phase 1 (deny at Layer 2) or §5.2 (Layer 4 hook)**) |
| `git push` | (B3) | (B3)/network | **(N)** | **(V)** `--no-verify` | force, force-with-lease, no-verify | **deny** (already double-defended at Layer 2 + Layer 4 — §9) |
| `git remote add / set-url / remove` | (B3) | (B3) (writes `.git/config`) | – | – | – | **deny** (mutates shared `config` — Phase 0 §4.2.1 denies (B3) writes; **gap → Phase 1 path deny**) |
| `git config` (write form: `--global`, `--local`, `--worktree`) | (B3) | (B3) (`--local` writes shared `config`); home (`--global`) | – | – | `--global`, `--local` | **deny w/ flag deny** (recommend per-flag deny at Layer 2; **gap → §5.2**) |
| `git config` (read form: `--get`) | (B3) | – | – | – | – | **allow** |
| `git tag` (lightweight or annotated) | (B2)(B3) | (B2) (writes `refs/tags/`) | – | – | – | **allow** (refs/tags is in (B3) shared but not in the cross-worktree-conflict surface; **note**: a worker creating tags is unusual but not boundary-violating) |
| `git tag -d` | (B2)(B3) | (B3) (deletes `refs/tags/`) | – | – | `-d` | **deny** (mutates shared tag namespace — recommend `block-dangerous-git.sh` extension; **gap → §5.2**) |
| `git gc` | (B2) | (B2) (rewrites `objects/`, `packed-refs`) | – | – | – | **deny** (touches the shared object store under concurrent worktree load; running `gc` from one worktree while another worktree is mid-write is documented-unsafe in upstream git. **gap → Phase 1 path-deny is wrong layer (gc legitimately writes to (B2)); recommend Layer 2 `Bash(git gc*)` deny.**) |
| `git fsck` | (B1)(B2) | – | – | – | – | **allow** |
| `git reflog` (read) | (B1) | – | – | – | – | **allow** |
| `git reflog expire / delete` | (B1) | (B1) | – | – | `--expire`, `--delete` | **deny** (rewrites the audit trail; not needed by worker) |
| `git worktree list` | (B2)(B3) | – | – | – | – | **allow** |
| `git worktree add` | (B2)(B3) | (B3)(B4) (creates a *new* `.git/worktrees/<task>/` dir) | – | – | – | **deny** (nesting Pattern B inside another Pattern B is an org-structure violation; the worker's job ends in its `<task_id>` worktree and the secretary owns worktree topology. **gap → §6**) |
| `git worktree remove` | (B2)(B3) | (B4) (removes `.git/worktrees/<other_task>/`) | – | – | `--force` | **deny** (worker must not remove any worktree, including its own — the secretary owns teardown. The current `profile-tightened` denies only the `--force` form at Layer 2, see [`docs/sandbox-probe/profiles/profile-tightened.json`](../sandbox-probe/profiles/profile-tightened.json); the bare form is not blocked anywhere. **gap → §6**) |
| `git worktree prune` | (B2) | (B2) (rewrites `.git/worktrees/` administrative dirs; can resurrect or discard stale worktree dirs) | – | – | – | **deny** (rewrites a path category Phase 0 §4.2.1 marks shared; running prune from one worktree while a sibling is checked out is a known foot-gun. **gap → §6**) |
| `git worktree lock / unlock / repair / move` | (B2)(B3)(B4) | (B2)(B3)(B4) | – | – | – | **deny** (all of these touch sibling-worktree administrative state — §6) |
| `git filter-branch`, `git filter-repo` | (B2) | (B2) (rewrites refs and objects en masse) | – | – | – | **deny** (history rewrite — never legitimate from a worker; **gap → §5.2 recommend Layer 2 deny**) |
| `git replace` | (B2) | (B3) (writes `refs/replace/`) | – | – | – | **deny** (rewrites object identity — same family as filter-branch; **gap → §5.2**) |
| `git update-ref` (write form) | (B2)(B3) | (B2)(B3) (arbitrary ref writes) | – | – | `-d`, `--stdin` | **deny** (low-level escape hatch around all the above; should never run from worker; **gap → §5.2**) |
| `git symbolic-ref HEAD <ref>` | (B1)(B2) | (B1) (rewrites this worktree's HEAD) | – | – | – | **allow w/ flag deny** (write form is fine for *this* worktree; cross-worktree HEAD writes are blocked by Phase 0 (B4) path deny once Phase 1 lands) |
| `git pack-refs` | (B2) | (B2) (rewrites `packed-refs`) | – | – | – | **allow** (legitimate; same write surface as `gc` but specifically for refs — but cross-worktree race risk is similar; **note**: low-priority deny candidate, not blocking) |
| `git submodule` (any) | varies | varies | **(N)** | – | – | **deny** (worker template does not run submodule operations; submodule add/update reach the network and rewrite shared `.gitmodules`. **gap → §5.2**) |
| `git lfs` (any) | varies | varies | **(N)** | – | – | **deny** (same family as fetch/push for LFS objects; out-of-scope) |
| `git bisect` (start / good / bad / reset) | (B1)(B2)(B3) | (B1) (this worktree's HEAD + bisect log) | – | – | – | **allow** (operates on this worktree only) |
| `git apply`, `git am` | (B1) | (B1) (working tree + index for am) | – | – | `--ignore-whitespace`, `--reject` | **allow** |
| `git mv` | (B1) | (B1) | – | – | – | **allow** |
| `git rm` | (B1) | (B1) | – | – | – | **allow** |
| `git notes add / append / edit` | (B2) | (B2) (`refs/notes/`) | – | – | – | **allow** |
| `git notes remove --all`, `--prune` | (B2) | (B2) | – | – | `--all`, `--prune` | **deny** (mass mutation; same family as filter-branch — gap, low priority) |

**Reading the table.** Rows marked **allow** are the normative allow list
for the worker template. Rows marked **deny** must be denied by either an
existing hook (cell names the script), a recommended hook extension
(`§5.2 ...`), or a path-level Layer 3 deny that Phase 1 will emit
(`§8.1 ...`). Rows marked **deny + wrapper** are the items that motivate
the §8.3 git-aware-wrapper deliverable and are *not* deniable by the
current mechanism.

---

## 4. Per-bucket guardrail policy

This section consolidates §3.2 by boundary category, and for each category
states (a) what is allowed, (b) what is denied, (c) which Layer enforces,
(d) and why.

### 4.1 (B1) Current worktree local metadata — *allow-by-default*

**Scope.** `<base_clone>/.git/worktrees/<task_id>/{HEAD,index,ORIG_HEAD,logs,*.lock}`.

**Policy.** All reads and all writes are allowed. Every routine
worker subcommand (`commit`, `add`, `status`, `stash`, `restore`) writes
here.

**Layer.** Layer 3 `additionalDirectories` must include this path. Phase 0
§4.2.1 already specifies it. **Current state**: `worker_roles.default`
emits `additionalDirectories: [<worker_dir>]` only; this **does not
include** the per-worktree metadata dir. The handcraft tightened profile
has the same gap. **Gap → Phase 1**: emit `additionalDirectories` per
Phase 0 §4.2.1's prescription.

**Why allow.** Phase 0 §4.2.1 row 3 directly: *Git operations from inside
`<worker_dir>` (commit, branch, status, stash) need to read and write
HEAD, index, refs, etc. for this worktree.* No deny is consistent with the
worker doing its job.

### 4.2 (B2) Shared base metadata (write-needed) — *allow w/ subcommand-level flag deny*

**Scope.** `<base_clone>/.git/objects/`, `<base_clone>/.git/refs/heads/<this_branch>`,
`<base_clone>/.git/packed-refs`, `<base_clone>/.git/gc.lock`,
`<base_clone>/.git/refs/tags/`.

**Policy.** Reads always allowed. Writes allowed when initiated by an
in-scope subcommand (`commit`, `branch <new>`, `tag`, `stash`, `merge`,
`cherry-pick`, `revert`). Writes **denied** when initiated by:

- `git gc` — concurrent-worktree race risk; **Layer 2 deny** recommended
  (§5.2).
- `git filter-branch`, `git filter-repo`, `git replace`,
  `git update-ref` (write form), `git reflog expire/delete`, mass
  `git notes` mutation — history-rewrite family; **Layer 2 deny**
  recommended (§5.2).
- `git fetch` writing to `refs/remotes/` — see §4.3 below; treated as
  (B3) write because fetch is the only legitimate writer.

**Layer.** Layer 3 `additionalDirectories` must include `<base_clone>/.git/objects/`
and `<base_clone>/.git/refs/heads/` (or just `<base_clone>/.git/` minus
the (B4) carve-out — Phase 0 §4.2.1 favors the precise pair). Layer 2 +
Layer 4 enforce the per-subcommand flag denies for the items above.

**Why allow at the path level but deny at the subcommand level.** Path
denying (B2) breaks `commit`, which is the worker's primary verb. The
narrow flag denies on the high-risk subcommands keep the path open while
removing the rewrite escape hatches.

### 4.3 (B3) Shared base metadata (read-only) — *path-level write deny*

**Scope.** `<base_clone>/.git/HEAD`, `<base_clone>/.git/config`,
`<base_clone>/.git/refs/heads/<other_branch>`,
`<base_clone>/.git/refs/remotes/`, `<base_clone>/.git/hooks/`.

**Policy.** Reads always allowed. Writes denied at the **path level**
(Layer 3 `denyWrite`, Phase 1 mechanization).

**Subcommand consequences.** `git fetch` is the only legitimate writer to
`refs/remotes/`. Phase 0 §4.2.1 currently has a contradiction here: it
denies all (B3) writes but `git fetch` writes to a (B3) sub-path
legitimately. **Recommend Phase 0 amendment**: split (B3) into

- (B3a) deny: `<base_clone>/.git/HEAD`, `<base_clone>/.git/config`,
  `<base_clone>/.git/refs/heads/<other>`, `<base_clone>/.git/hooks/`,
- (B3b) allow git-mediated write: `<base_clone>/.git/refs/remotes/`,
  on the assumption that `git fetch` itself is allowed.

If the worker does **not** run `git fetch` (the conservative posture this
document recommends, §8.1), the (B3a) / (B3b) split collapses back to a
clean (B3) deny.

**Layer.** Layer 3 `denyWrite` is the right layer; Layer 2 cannot
distinguish "git fetch wrote here" from "echo wrote here", and the
mount-namespace already collapses both cases. **Gap → Phase 1**: the
schema has no `sandbox` field today; the only place this is currently
expressed is the handcraft profile, and even that profile does not
enumerate the (B3) path subset.

**Why path-level rather than subcommand-level.** Subcommand denies are
brittle (`git remote add` vs `git -C <other> remote add` vs
`git config remote.foo.url`); path-level captures them all uniformly.

### 4.4 (B4) Other worktree metadata — *path-level deny*

**Scope.** `<base_clone>/.git/worktrees/<other_task>/**` for any
`<other_task> != <task_id>`.

**Policy.** All reads and all writes denied. Phase 0 §4.2.1 row 4
directly.

**Subcommand consequences.** The subcommands that touch (B4) are:

- `git worktree remove`, `git worktree prune`, `git worktree lock /
  unlock / repair / move` — covered by §6.
- `git branch -m / --move`, `git branch -D` of an in-use branch,
  `git checkout -B`, `git switch -C` — these mutate refs that another
  worktree's `HEAD` resolves to. Path-level deny on (B4) does not catch
  these because the *ref* is in (B2) (shared `refs/heads/`) and the
  *worktree's HEAD pointer* is in (B4) but is a *read* during the
  operation. The git-aware wrapper item (§8.3) is required here.

**Layer.** Layer 3 `denyWrite` (and `denyRead`) — Phase 1.

### 4.5 (B5) Working-tree boundary — *path-level deny*

**Scope.** `<base_clone>/...` outside of `.git/`. The worker's working
tree is `<worker_dir>/`, never the base clone's checkout.

**Policy.** All reads and all writes denied (the worker should not even
*read* the base clone's working files; if it needs cross-branch source it
uses `git show <ref>:<path>` or `git cat-file`, both of which read from
(B2) objects and not from the base clone working tree).

**Layer.** Layer 3 `additionalDirectories` should *not* include the base
clone's working tree, only its `.git/` subtree (with the (B4) carve-out).
**Gap → Phase 1**: today the handcraft tightened profile has only
`additionalDirectories: [<worker_dir>]`, which means git operations
needing (B1) and (B2) writes work only because Layer 3 is suppressed on
WSL — this is the Phase 0 §4.2 finding *"only works because Layer 3 is
suppressed on WSL"* and is the same gap.

### 4.6 (N) Network — *deny by default*

**Scope.** Subcommands that cross the network: `git fetch`, `git pull`,
`git push`, `git submodule add/update`, `git lfs *`, `git remote *`
(when fetching/pushing), `git clone`.

**Policy.** All denied. The worker has no business reaching the network in
steady state — the secretary owns push and any pre-task fetch. The
existing `Bash(git push *)` deny + [`.hooks/block-git-push.sh`](../../.hooks/block-git-push.sh)
covers `push`. The other network verbs are **gaps**; recommend Layer 2
deny entries (§5.2).

**Layer.** Layer 2 `permissions.deny` is sufficient — the network calls
go through `Bash`, and pattern strings catch them. Layer 3 cannot block
network without a network namespace; that is out of scope.

### 4.7 (V) Verification bypass — *flag-level deny*

**Scope.** `--no-verify` on `git commit`, `git push`, `git merge`,
`git pull`, `git am --no-verify`, environment variable bypass via
`HUSKY=0` / `SKIP_SECRET_SCAN=1` set inline by the worker.

**Policy.** All denied. The pre-commit secret scan and any project pre-
commit hook chain must run. Override discipline: never from the worker —
see §7.

**Layer.** Layer 4
[`.hooks/block-no-verify.sh`](../../.hooks/block-no-verify.sh) for
`commit` / `push`. **Gap**: the script does not currently catch `merge`,
`pull`, or `am`. §5.2 recommends the extension.

---

## 5. Hook fitness audit

### 5.1 Current attachment state (concrete)

The four existing scripts and their attachment to worker templates,
verified against [`tools/org_extension_schema.json`](../../tools/org_extension_schema.json)
`worker_roles.default.hooks.PreToolUse` and `worker_roles.claude-org-self-edit.hooks.PreToolUse`:

| Script | `worker_roles.default` | `worker_roles.claude-org-self-edit` | Repo-shared `.claude/settings.json` |
|---|---|---|---|
| [`.hooks/block-git-push.sh`](../../.hooks/block-git-push.sh) | **attached** (Bash matcher) | **attached** (Bash matcher) | attached |
| [`.hooks/block-org-structure.sh`](../../.hooks/block-org-structure.sh) | **attached** (Edit\|Write + Bash) | not attached (self-edit by definition writes to org structure) | attached |
| [`.hooks/check-worker-boundary.sh`](../../.hooks/check-worker-boundary.sh) | **attached** (Edit\|Write) | **attached** (Edit\|Write) | n/a |
| [`.hooks/block-dangerous-git.sh`](../../.hooks/block-dangerous-git.sh) | **NOT attached** | **NOT attached** | attached (bound only when worker cwd is inside the claude-org repo) |
| [`.hooks/block-no-verify.sh`](../../.hooks/block-no-verify.sh) | **NOT attached** | **NOT attached** | attached (same cwd-tree limitation) |

The Phase 0 §4.1.2 contract already calls this out as a gap. Phase 2's
contribution is to recommend the **specific attachment** for each hook
and to identify the (small) set of changes the existing scripts need.

### 5.2 Per-hook recommendation

#### 5.2.1 [`.hooks/block-git-push.sh`](../../.hooks/block-git-push.sh)

- **Disposition: leave attached as-is.**
- Rationale: regex catches `git push` plus options form (`git -C ... push`),
  unwraps `eval` / `bash -c`. The double defense with `permissions.deny
  Bash(git push *)` is correct and matches §9.
- No change recommended.

#### 5.2.2 [`.hooks/block-dangerous-git.sh`](../../.hooks/block-dangerous-git.sh)

- **Disposition: attach to `worker_roles.default` and
  `worker_roles.claude-org-self-edit` (Bash matcher); extend coverage.**
- Rationale: the script's existing surface (force-push, `reset --hard`,
  `branch -D`) is exactly what the worker needs. The cwd-tree
  non-inheritance gap (Phase 0 §4.1.2) means today these protections are
  absent for any worker outside the claude-org repo tree. Attaching at
  the schema level closes that gap uniformly.
- **Coverage extensions recommended** (file already lists most as
  `TODO(Phase 2)` at its head):
  - `git clean -fd` / `-x` / `-X` (working-tree wipe).
  - `git checkout -- .` / `git checkout -- <path>` discard form (the
    script's `TODO(Phase 2)` line names this).
  - `git restore --source=<ref> --worktree .` discard form.
  - `git tag -d` (mutates shared tag namespace — §3.2).
  - `git update-ref -d` (low-level ref deletion).
  - `git reflog expire / delete` with `--all` or `--expire-unreachable=now`.
  - `git filter-branch`, `git filter-repo`, `git replace`,
    `git submodule add / deinit / update --remote`,
    `git lfs *`, `git config --global / --local / --worktree` write
    forms (these may be cleaner as separate `permissions.deny` entries
    rather than as new branches in this script — see §8.2).

#### 5.2.3 [`.hooks/block-no-verify.sh`](../../.hooks/block-no-verify.sh)

- **Disposition: attach to `worker_roles.default` and
  `worker_roles.claude-org-self-edit` (Bash matcher); extend the
  subcommand list.**
- Rationale: same cwd-tree non-inheritance gap; same fix (attach at
  schema level).
- **Coverage extensions recommended**:
  - `git merge --no-verify` (the script currently checks only `commit`
    / `push`).
  - `git pull --no-verify` (covered transitively by `pull = fetch +
    merge`, but explicit catch is safer; if `pull` is itself denied per
    §4.6 / §5.2.4 this is moot).
  - `git am --no-verify`.
  - Inline env-variable bypass: `HUSKY=0 git commit ...`, `SKIP_SECRET_SCAN=1
    git commit ...` — the script's `expand_known_vars` already handles
    `VAR=value` for `--no-verify`-bearing variables; extend to detect
    these specific env names regardless of value.

#### 5.2.4 New worker-template `permissions.deny` entries (no script change)

Several items in §3.2 are best handled as Layer 2 globs rather than as
new branches in `block-dangerous-git.sh`, because the patterns are
narrow and adding them to a script grows the maintenance surface:

```jsonc
"deny": [
  // existing entries above ...
  "Bash(git fetch *)", "Bash(git fetch)",
  "Bash(git pull *)", "Bash(git pull)",
  "Bash(git remote add *)", "Bash(git remote set-url *)",
  "Bash(git remote remove *)", "Bash(git remote rm *)",
  "Bash(git submodule *)",
  "Bash(git lfs *)",
  "Bash(git gc *)", "Bash(git gc)",
  "Bash(git filter-branch *)", "Bash(git filter-repo *)",
  "Bash(git replace *)",
  "Bash(git update-ref *)",
  "Bash(git config --global *)", "Bash(git config --local *)",
  "Bash(git config --worktree *)",
  "Bash(git reflog expire *)", "Bash(git reflog delete *)",
  "Bash(git worktree add *)",
  "Bash(git worktree remove *)", "Bash(git worktree remove)",
  "Bash(git worktree prune *)", "Bash(git worktree prune)",
  "Bash(git worktree lock *)", "Bash(git worktree unlock *)",
  "Bash(git worktree repair *)", "Bash(git worktree move *)",
  "Bash(git -C * fetch *)", "Bash(git -C * pull *)",
  "Bash(git -C * worktree *)",
  // ... (the -C variants for each above; the schema generator can
  // expand these from a single source list — see §8.2)
]
```

Note that `git worktree:*` is currently in the **allow** list of
`worker_roles.default` ([`tools/org_extension_schema.json`](../../tools/org_extension_schema.json)
line 293). The recommendation is to **drop `Bash(git worktree:*)` from
allow** and **deny the entire subcommand** at Layer 2 (the worker has
no legitimate use of any `git worktree` verb — see §6).

#### 5.2.5 [`.hooks/block-org-structure.sh`](../../.hooks/block-org-structure.sh)

- **Disposition (git-guardrails scope): leave attached for
  `worker_roles.default`; this git-guardrails work makes no git-related
  change to it.** This script is filesystem-boundary scoped and does not
  need to know about git. The Bash-half regex on `mkdir|touch|cp|mv` plus
  org-structure dirnames does not double-catch git operations because
  git invocations against `.claude/`, `.dispatcher/`, `.state/` etc. are
  rare and would surface as Edit/Write tool calls before reaching git.
- **Note (later change, out of git scope):** the org-structure block's
  **Edit/Write half** was subsequently scoped to claude-org itself — when
  `WORKER_DIR` is outside `CLAUDE_ORG_PATH` (a target-repo worker),
  `WORKER_DIR/.claude/` is allowed in full (the target repo's own Claude Code
  config); in-org behavior is unchanged. The **Bash half is intentionally NOT
  relaxed** (it blocks `.claude/` for all hook-running roles as before),
  because a shell-string grep cannot safely scope relative/variable references
  away from claude-org's own `.claude/` — only the `realpath`-based Edit/Write
  half can. So this hook is no longer literally byte-for-byte "as-is", but the
  git-guardrails disposition above (no git-related change) still holds. See
  [`docs/contracts/role-pattern-sandbox-contract.md`](./role-pattern-sandbox-contract.md)
  §1.2 for the current Decision rule.

#### 5.2.6 New git-aware wrapper (separate deliverable)

The few rules that need per-call git-state resolution (rebase across
sibling-checked-out branches, `branch -m` on an in-use branch, `checkout
-B` of a ref another worktree holds) are not deniable with the existing
machinery. §8.3 carves these into a separate Phase 2.x deliverable.
**This document does not specify the wrapper's implementation**; it only
records the rules that need it.

### 5.3 Why "git-aware wrapper" is a separate deliverable

A git-aware wrapper, were it built, would:

- Run as a Layer 4 `Bash`-matcher hook before the existing chain.
- Parse the command into `(subcmd, refs, paths)` triples (using `git
  rev-parse` / `git for-each-ref` to resolve the refs).
- Cross-reference target refs against `git worktree list --porcelain`
  to detect sibling-worktree conflicts.
- Reject or allow based on the resolved state.

Such a wrapper is a non-trivial implementation (parsing git invocations
correctly is its own subproject; the `segment-split` library at
[`.hooks/lib/segment-split.sh`](../../.hooks/lib/segment-split.sh) is
not sufficient), and bolting it in opportunistically risks creating a
half-correct guardrail that obscures the real boundary. Spinning it into
its own design phase keeps Phase 2 focused on the items that *are*
deniable with the current mechanism, and lets the wrapper be designed
end-to-end. **Phase 2 is complete without it**, since:

- The path-level (B4) deny (Phase 1) covers the *write* side of cross-
  worktree corruption uniformly.
- The remaining wrapper-only items (rebase / branch -m / checkout -B
  cross-checkout cases) all *fail closed* in upstream git itself when
  the target ref is held by another worktree (`fatal: 'feature-x' is
  already checked out at '...'`), so the worst case is a worker error,
  not a cross-task corruption.

The wrapper is therefore a *quality* improvement (better error messages,
catching the cases git itself doesn't), not a *correctness* requirement.

---

## 6. Worktree remove / prune coverage gap

### 6.1 Current state

- `worker_roles.default.permissions.allow` lists `Bash(git worktree:*)`
  ([`tools/org_extension_schema.json`](../../tools/org_extension_schema.json)
  line 293). All `git worktree` subcommands are therefore Layer 2-allowed.
- `worker_roles.claude-org-self-edit.permissions.allow` is identical
  (line 357).
- The handcraft tightened profile at
  [`docs/sandbox-probe/profiles/profile-tightened.json`](../sandbox-probe/profiles/profile-tightened.json)
  denies only `Bash(git worktree remove --force*)` (and the `git -C *`
  variant). The bare `git worktree remove`, `git worktree prune`,
  `git worktree lock / unlock / repair / move`, and `git worktree add`
  are **not denied at any layer today** for either worker template.
- [`.hooks/block-dangerous-git.sh`](../../.hooks/block-dangerous-git.sh)
  has no `git worktree` branch in its `segment_has_git_subcmd` checks
  (lines 114–143 cover only push / reset / branch).

### 6.2 Recommended worker template changes

**Drop `Bash(git worktree:*)` from `permissions.allow`** in both
`worker_roles.default` and `worker_roles.claude-org-self-edit`.

**Add to `permissions.deny`** (Layer 2):

```jsonc
"deny": [
  // ...
  "Bash(git worktree)",
  "Bash(git worktree *)",
  "Bash(git -C * worktree)",
  "Bash(git -C * worktree *)"
]
```

This is intentionally a **full deny** of the `git worktree` verb. The
worker has no legitimate use of *any* `git worktree` subcommand:

- `git worktree list` — informational; the worker already knows it is
  in a Pattern B worktree (its `WORKER_DIR` env var is set).
- `git worktree add` — creating a *new* worktree from inside an existing
  worktree is a topology operation owned by the secretary.
- `git worktree remove` — Pattern B teardown is the secretary's job per
  the worker's role contract; removing one's own worktree from inside it
  is a foot-gun (the next operation cwd's into a non-existent dir).
- `git worktree prune` — rewrites the base clone's `.git/worktrees/`
  administrative dir, a (B2) shared-write that races sibling worktrees.
- `git worktree lock / unlock / repair / move` — administrative
  operations on the topology; secretary scope.

### 6.3 Why prune matters specifically

`git worktree prune` deletes administrative dirs under `<base_clone>/.git/worktrees/`
for any worktree whose linked working tree is missing or whose `gitdir`
file is stale. From inside a Pattern B worker, prune cannot distinguish
"this dir is stale" from "this dir is in active use by a sibling
worker who happens to be in a transient state". The race window is
small but real, and prune is destructive on hit.

The Phase 0 §4.2.1 (B4) path deny will also cover this once Phase 1
mechanizes it (prune writes to `<base_clone>/.git/worktrees/<other>/`),
but the path deny does not surface a useful error to the worker — the
syscall just fails. The Layer 2 deny + a hook message gives the worker
a clear "do not run prune; ask the secretary" affordance.

### 6.4 Self-edit role exception consideration

`worker_roles.claude-org-self-edit` operates on the live claude-org
repo. The secretary itself runs in that same repo. A self-edit worker
running `git worktree prune` is even more dangerous than a default
worker — it would prune the secretary's own administrative dirs.

**Recommendation: same full deny applies to self-edit.** No carve-out.

---

## 7. False-positive recovery procedure

The existing scripts produce false positives on edge cases (commit
messages containing `--force`, etc.). The Phase 2 worker contract
specifies the recovery path:

### 7.1 Worker behavior (mandatory)

- The worker **does not override the hook** in its own `settings.local.json`.
  The settings file is generator-only per Phase 0 §3.1.2 (and a
  `required_deny` glob at the secretary role enforces it for writes from
  the secretary's tools); the worker has no rewrite path even if it
  wanted one.
- The worker **does not edit its own `.claude/settings.local.json`**
  at runtime. [`.hooks/check-worker-boundary.sh`](../../.hooks/check-worker-boundary.sh)
  + [`.hooks/block-org-structure.sh`](../../.hooks/block-org-structure.sh)
  block this for `Edit` / `Write` tool calls; the discipline statement
  here is for the case the worker considers a `Bash(sed -i ...)`
  workaround. **That is also forbidden**.
- The worker **does not retry the operation in a transformed form**
  intended to defeat the hook (e.g. inserting a no-op pipe to break
  the regex). `block-dangerous-git.sh` and `block-no-verify.sh` both
  have known-limitation notes that say *"その場合は別表現に書き換える
  こと"* for genuine false positives — that guidance applies to a human
  user, not to the worker.

### 7.2 Worker reporting (mandatory)

When a hook denies a command the worker believed legitimate:

- **Report to secretary** via `mcp__renga-peers__send_message(to_id="secretary", ...)`:
  - The exact command string the worker tried to run.
  - The intent (what the worker was trying to accomplish).
  - The target path / ref / branch the operation would have touched.
  - The hook's stderr message (i.e. which script denied it and why).
- Wait for secretary disposition. Do not proceed.

### 7.3 Secretary disposition (out of scope for this doc)

The secretary's response options — which include "run the operation
manually in a separate shell as the human", "amend the hook script and
re-attach", "rephrase the operation", or "decline the change" — are
spelled out in the secretary role contract at
[`docs/contracts/role-contract.md`](./role-contract.md) and in the
escalation skill. Phase 2 only fixes that **the worker is not the
decision authority** and **the worker does not hand-edit settings or
hooks**.

### 7.4 What this contract intentionally does not specify

A non-trivial false-positive volume from a newly-attached
`block-dangerous-git.sh` / `block-no-verify.sh` would be a signal to
revisit the script's heuristics, not to relax the worker contract. That
operational tuning is out of scope here; the script authors at
[`.hooks/block-dangerous-git.sh`](../../.hooks/block-dangerous-git.sh)
§"既知の制限" already describe the trade-off.

---

## 8. Phase 1 prerequisite mapping

This section maps every Phase 2 recommendation to its mechanization
layer. Implementers reading this doc should treat the §8.1 items as
**blocking on Phase 1** (they require schema-level `sandbox` field
emission) and the §8.2 items as **immediately mechanizable** (they only
need `permissions.deny` + hook attachment changes).

### 8.1 Items requiring schema-level `sandbox` deny (Phase 1)

| Phase 2 rule | Phase 1 mechanism | Phase 0 row reference |
|---|---|---|
| (B1) allow + (B2) allow + (B3) deny + (B4) deny + (B5) deny | `sandbox.filesystem.additionalDirectories: [<worker_dir>, <base_clone>/.git/worktrees/<task_id>, <base_clone>/.git/objects, <base_clone>/.git/refs/heads]` + `sandbox.filesystem.denyWrite: [<base_clone>/.git/worktrees/*, <base_clone>/.git/HEAD, <base_clone>/.git/config, <base_clone>/.git/hooks]` | Phase 0 §4.2.1 row table; mechanization sketch at Phase 0 §4.2.1 closing JSONC block |
| `~/.aws/**`, `~/.ssh/**` `denyRead` (with adaptive WSL suppression) | `sandbox.filesystem.denyRead: ["~/.aws/**", "~/.ssh/**"]` | Phase 0 §1.3 |
| `~/.claude/**` `denyWrite` | `sandbox.filesystem.denyWrite: ["~/.claude/**"]` | Phase 0 §3.1 (analogous) |
| Worker working-tree boundary (B5) | `additionalDirectories` does not include `<base_clone>/` outside the carve-outs above | Phase 0 §4.2.1 row 5 |

The Phase 1 schema work needs `worker_roles[*].sandbox` as a structured
field — paths templated by `{worker_dir}`, `{base_clone}`, `{task_id}`,
`{home_dir}`. The current schema has only `permissions` / `hooks` /
`env` (`tools/org_extension_schema.json` `worker_roles[*]`); the
`sandbox` field is the new addition. Drift coverage in
[`tools/check_role_configs.py`](../../tools/check_role_configs.py) must
be extended in lockstep.

### 8.2 Items mechanizable without schema-level changes

These are immediate Layer 2 / Layer 4 items the schema can absorb today,
without the `sandbox` field:

| Phase 2 rule | Mechanism | Where in this doc |
|---|---|---|
| Attach `block-dangerous-git.sh` to both worker templates (Bash matcher) | Add to `worker_roles.default.hooks.PreToolUse[Bash].hooks` and same for `claude-org-self-edit` | §5.2.2 |
| Attach `block-no-verify.sh` to both worker templates (Bash matcher) | Same | §5.2.3 |
| Drop `Bash(git worktree:*)` from `permissions.allow` and add full `Bash(git worktree*)` deny | Edit `worker_roles.default.permissions.{allow,deny}` and same for self-edit | §6.2 |
| `Bash(git fetch *)` / `Bash(git pull *)` deny | Add to `worker_roles.*.permissions.deny` | §5.2.4 |
| `Bash(git remote *)` deny | Same | §5.2.4 |
| `Bash(git submodule *)` / `Bash(git lfs *)` deny | Same | §5.2.4 |
| `Bash(git gc *)` deny | Same | §5.2.4 |
| `Bash(git filter-branch *)` / `Bash(git filter-repo *)` / `Bash(git replace *)` / `Bash(git update-ref *)` deny | Same | §5.2.4 |
| `Bash(git config --global / --local / --worktree *)` deny | Same | §5.2.4 |
| `Bash(git reflog expire / delete *)` deny | Same | §5.2.4 |
| Extend `block-dangerous-git.sh` to cover `git clean -fd`, `git checkout -- .`, `git tag -d`, etc. | Edit the script | §5.2.2 |
| Extend `block-no-verify.sh` to cover `merge`, `pull`, `am`, `HUSKY=0`, `SKIP_SECRET_SCAN=1` env-var bypass | Edit the script | §5.2.3 |

**Implementation note on `-C` variants**: every `Bash(git X *)` deny
needs a `Bash(git -C * X *)` twin to cover the explicit-cwd form. The
handcraft profile at
[`docs/sandbox-probe/profiles/profile-tightened.json`](../sandbox-probe/profiles/profile-tightened.json)
already does this manually. **Recommend** the schema generator absorb
the `-C` expansion as a normalization step, so the schema source lists
each pattern once. The expansion lives in the generator
(`claude-org-runtime settings generate`), not in the schema body.

### 8.3 Items requiring a new git-aware wrapper (Phase 2.x)

| Rule | Why a wrapper is needed |
|---|---|
| `git rebase` onto a branch checked out by a sibling worktree | Resolution requires `git worktree list --porcelain` cross-reference |
| `git branch -m / --move` of a ref a sibling worktree's `HEAD` resolves to | Same |
| `git checkout -B <ref>` / `git switch -C <ref>` where `<ref>` is held by a sibling worktree | Same |
| `git symbolic-ref` write form across worktrees | Same |
| Detecting that the *intent* of a command is to touch (B4) when no flag string betrays it | Subcommand resolution by argument count + ref existence |

Phase 2 does **not** specify the wrapper's implementation. Spin into a
follow-up design phase. As noted in §5.3, the wrapper is a *quality*
improvement; upstream git itself fails closed on the cross-checkout
cases, so a Phase 1-only mechanization is correctness-complete.

---

## 9. `git push` — no redesign

**Disposition: keep the existing double defense.** No change recommended
in Phase 2.

The worker `permissions.deny` includes `Bash(git push *)` and
`Bash(git push)` — verified at
[`tools/org_extension_schema.json`](../../tools/org_extension_schema.json)
`worker_roles.default.permissions.deny` (and same for self-edit). The
Layer 4 hook [`.hooks/block-git-push.sh`](../../.hooks/block-git-push.sh)
catches `eval` / `bash -c` unwrappings and `git -C ... push` forms. The
two layers together close the catalog of push bypasses Claude Code's
Bash tool can express.

Force-push (`--force`, `-f`, `--force-with-lease`, bundled-short-option
forms) is in [`.hooks/block-dangerous-git.sh`](../../.hooks/block-dangerous-git.sh)
scope. Once §5.2.2 attaches that script to both worker templates,
force-push has the same double-defense status as plain push.

**This document does not propose**:

- Any Layer 2 carve-out that allows the worker to push to a "safe"
  remote.
- Any conditional push permission tied to branch name, ref pattern, or
  remote.
- Any wrapper-mediated push.

The secretary owns push, per the role contract at
[`docs/contracts/role-contract.md`](./role-contract.md). Phase 2 does
not loosen that boundary.

---

## 10. Open questions and out-of-scope

### 10.1 (B3) `refs/remotes/` carve-out

Phase 0 §4.2.1 marks (B3) writes as denied, but `git fetch` legitimately
writes to `refs/remotes/`. §4.3 / §8.1 propose splitting (B3) into
(B3a) deny / (B3b) allow-via-fetch. **This is a Phase 0 contract
amendment**, not a Phase 2 design decision. Until Phase 0 is amended,
this document recommends the conservative posture of denying `git fetch`
from worker (§5.2.4). The trade-off is that workers cannot run
`git fetch && git log origin/main..HEAD` style commands — those are
useful but not strictly necessary; the secretary can hand the worker the
commit list out-of-band.

### 10.2 Self-edit role parity

`worker_roles.claude-org-self-edit` shares the same Pattern B
constraints as `worker_roles.default` for git operations (the self-edit
role's distinguishing feature is its filesystem write permission to
`.dispatcher/`, `.curator/`, etc., not its git permissions). Every §5 /
§6 / §8.2 recommendation applies symmetrically. The only place self-
edit diverges is `block-org-structure.sh` non-attachment, which is
correct and unchanged.

### 10.3 `worker_roles.doc-audit`

The `doc-audit` role has `Edit` / `Write` denied at Layer 2 outright, so
the §3.2 allow/deny split for git verbs that *don't* mutate the working
tree (status, log, diff, show, rev-parse) applies, and the rest of §3.2
is moot — the worker cannot create the commits that need (B1) / (B2)
writes. **No Phase 2 changes recommended for `doc-audit`.**

### 10.4 Pattern A and Pattern C

Pattern A workers have an independent `.git/` directory and Pattern C
workers either have no git or `git init` their own. Neither shares
metadata with the secretary or sibling workers, so the (B4) / (B5)
denies do not apply. The §5.2 hook attachments and §6 `worktree` deny
**still apply** for defense-in-depth (a Pattern A worker should not run
`git worktree prune` even on its own clone, and the
`block-dangerous-git.sh` / `block-no-verify.sh` attachments are
worker-template-wide). **The §8.1 path-level Phase 1 emit is Pattern-B-
specific** because the path categories (B1)–(B5) are Pattern-B-specific.

### 10.5 Verification

This is a design document. There is no code or test to verify. The
acceptance criteria are:

- §1.3 adverse design is rejected with reasons.
- §3.2 master subcommand table exists and is consistent with Phase 0
  §4.2.1.
- §5.1 hook attachment claims are verifiable against
  [`tools/org_extension_schema.json`](../../tools/org_extension_schema.json)
  `worker_roles.default.hooks.PreToolUse` and the self-edit twin.
- §6.2 recommendation contradicts the current `Bash(git worktree:*)`
  allow and explains why the worker has no legitimate worktree-verb use.
- §7 reporting protocol matches the existing
  [`docs/contracts/role-contract.md`](./role-contract.md) escalation
  contract.
- §8.1 / §8.2 / §8.3 split is exhaustive (every §3.2 deny row is mapped
  to exactly one of the three).
- §9 leaves the existing push double-defense untouched.

---

## 11. Summary of recommended changes (cheat sheet)

| Item | Layer | Where | Phase |
|---|---|---|---|
| Attach `block-dangerous-git.sh` to `worker_roles.default` and `claude-org-self-edit` (Bash matcher) | 4 | [`tools/org_extension_schema.json`](../../tools/org_extension_schema.json) | 2 (immediate) |
| Attach `block-no-verify.sh` to both worker templates (Bash matcher) | 4 | Same | 2 (immediate) |
| Drop `Bash(git worktree:*)` from `permissions.allow`; add `Bash(git worktree*)` deny family | 2 | Same | 2 (immediate) |
| Add Layer 2 deny for fetch / pull / remote / submodule / lfs / gc / filter-branch family / config write / reflog mutation | 2 | Same | 2 (immediate) |
| Extend `block-dangerous-git.sh` to cover `clean -fd`, `checkout -- .`, `tag -d`, `update-ref -d`, etc. | 4 | [`.hooks/block-dangerous-git.sh`](../../.hooks/block-dangerous-git.sh) | 2 (immediate) |
| Extend `block-no-verify.sh` to cover `merge`, `pull`, `am`, env-var bypass | 4 | [`.hooks/block-no-verify.sh`](../../.hooks/block-no-verify.sh) | 2 (immediate) |
| Emit `sandbox.filesystem.{additionalDirectories,denyWrite,denyRead}` per Phase 0 §4.2.1 | 3 | Schema + runtime generator | 1 (blocking) |
| Amend Phase 0 §4.2.1 to split (B3) for `refs/remotes/` carve-out (or leave as conservative deny) | 0 | [`docs/contracts/role-pattern-sandbox-contract.md`](./role-pattern-sandbox-contract.md) | 0 (amendment) |
| Design and implement git-aware wrapper for cross-worktree ref operations | 4 | New script under `.hooks/` | 2.x (separate) |

End of design.
