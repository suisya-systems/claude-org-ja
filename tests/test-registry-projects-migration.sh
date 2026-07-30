#!/usr/bin/env bash
# Tests for the registry/projects.md tracked -> operator-local transition
# (Issue #811).
#
# なぜこのテストが要るか:
#   「gitignore に足せばローカルファイルは守られる」は **誤り** である。gitignore は
#   untracked ファイルにしか効かない。tracked だったパスを `git rm --cached` +
#   gitignore で untrack するコミットは、**pull した側では通常の削除として伝播** し、
#   working tree のファイルが消える。この非対称（移行を行った checkout ではファイルが
#   残るのに、pull 側では消える）が移行の唯一の危険点なので、挙動を推測ではなく
#   実 git で固定する。
#
# ここで固定する 4 つの事実:
#   1. 移行を行った checkout では `git rm --cached` 後もローカルファイルが残る
#   2. その checkout で gitignore が効き、status がクリーンになる（再 commit 事故が起きない）
#   3. **clean な** working tree を持つ他 checkout が pull すると、ファイルは消える（危険）
#   4. **dirty な**（operator 固有行を持つ）他 checkout が pull すると、git は
#      pull 自体を拒否してファイルを守る（loud failure であって silent loss ではない）
#   5. docs の退避 → pull → 復元 レシピが実際にファイルを復元する
#
# 事実 3/4 の差は運用手順に直結する（3 は事前退避が必須、4 は git が止めてくれる）ので
# docs/operations/registry-projects-migration.md と併せて読むこと。
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

PASS=0
FAIL=0
TEST_NUM=0
TMPDIRS=()

cleanup() {
  local d
  for d in ${TMPDIRS[@]+"${TMPDIRS[@]}"}; do
    [[ -n "$d" && -d "$d" ]] && chmod -R u+w "$d" 2>/dev/null
    [[ -n "$d" && -d "$d" ]] && find "$d" -mindepth 1 -delete 2>/dev/null
    [[ -n "$d" && -d "$d" ]] && rmdir "$d" 2>/dev/null
  done
  return 0
}
trap cleanup EXIT

assert_eq() {
  local expected="$1" actual="$2" msg="$3"
  ((TEST_NUM++))
  if [[ "$expected" == "$actual" ]]; then
    echo "ok $TEST_NUM - $msg"
    ((PASS++))
  else
    echo "not ok $TEST_NUM - $msg"
    echo "    expected: [$expected]"
    echo "    actual:   [$actual]"
    ((FAIL++))
  fi
}

git_q() { git -C "$1" "${@:2}" >/dev/null 2>&1; }

file_state() { [[ -f "$1" ]] && echo present || echo absent; }

# ---------------------------------------------------------------------------
# Fixture: origin repo with a TRACKED registry/projects.md, plus two clones
# taken *before* the migration commit (= existing operator checkouts).
# ---------------------------------------------------------------------------
SB="$(mktemp -d)"
TMPDIRS+=("$SB")

ORIGIN="$SB/origin"
mkdir -p "$ORIGIN/registry"
git_q "$ORIGIN" init -q -b main || git -C "$ORIGIN" init -q -b main
git -C "$ORIGIN" config user.email test@example.com
git -C "$ORIGIN" config user.name test
printf '| 通称 | プロジェクト名 |\n|---|---|\n| 時計アプリ | clock-app |\n' \
  > "$ORIGIN/registry/projects.md"
git_q "$ORIGIN" add -A
git_q "$ORIGIN" commit -m "pre-migration: projects.md is tracked"

CLEAN="$SB/clean-checkout"
DIRTY="$SB/dirty-checkout"
git_q "$SB" clone -q "$ORIGIN" "$CLEAN"
git_q "$SB" clone -q "$ORIGIN" "$DIRTY"
for c in "$CLEAN" "$DIRTY"; do
  git -C "$c" config user.email test@example.com
  git -C "$c" config user.name test
done

# The dirty checkout carries uncommitted operator-specific rows — this models
# the real hazard (the operator's live registry rows are never committed).
printf '| 通称 | プロジェクト名 |\n|---|---|\n| 時計アプリ | clock-app |\n| 社内案件 | operator-only |\n' \
  > "$DIRTY/registry/projects.md"

# ---------------------------------------------------------------------------
# The migration commit itself, performed in the origin checkout.
# ---------------------------------------------------------------------------
git_q "$ORIGIN" rm --cached registry/projects.md
printf 'registry/projects.md\n' > "$ORIGIN/.gitignore"
printf '| 通称 | プロジェクト名 |\n|---|---|\n| 時計アプリ | clock-app |\n' \
  > "$ORIGIN/registry/projects.example.md"
git_q "$ORIGIN" add .gitignore registry/projects.example.md
git_q "$ORIGIN" commit -m "migrate: projects.md -> operator-local"

# 1. `git rm --cached` unstages without touching the working tree.
assert_eq "present" "$(file_state "$ORIGIN/registry/projects.md")" \
  "migrating checkout: git rm --cached keeps the local projects.md"

# 2. gitignore then hides it, so the file cannot be re-committed by accident.
#    (This is the property that prevents a repeat of the 2026-07-31 near-miss
#    where operator rows were about to enter history via an implicit commit.)
assert_eq "" "$(git -C "$ORIGIN" status --porcelain registry/projects.md)" \
  "migrating checkout: projects.md is untracked AND ignored (clean status)"

# 3. HAZARD: a clean checkout loses the file on pull. gitignore does NOT
#    protect it — the commit deletes a tracked path, and git applies that.
git_q "$CLEAN" pull origin main
assert_eq "absent" "$(file_state "$CLEAN/registry/projects.md")" \
  "clean checkout: pull DELETES projects.md (gitignore does not protect it)"

# 4. SAFETY: a dirty checkout is protected — git refuses the pull rather than
#    discarding uncommitted operator rows. Loud failure, not silent loss.
dirty_pull_exit=0
git -C "$DIRTY" pull origin main >/dev/null 2>&1 || dirty_pull_exit=$?
assert_eq "1" "$([[ $dirty_pull_exit -ne 0 ]] && echo 1 || echo 0)" \
  "dirty checkout: pull is REFUSED (non-zero exit) instead of discarding rows"
assert_eq "present" "$(file_state "$DIRTY/registry/projects.md")" \
  "dirty checkout: projects.md survives the refused pull"
assert_eq "1" "$(grep -c 'operator-only' "$DIRTY/registry/projects.md")" \
  "dirty checkout: operator-specific row is intact after the refused pull"

# 5. The documented recipe (back up -> pull -> restore) recovers the file in
#    the clean-checkout case. This is what
#    docs/operations/registry-projects-migration.md tells operators to run.
RECIPE="$SB/recipe-checkout"
git_q "$SB" clone -q "$ORIGIN" "$RECIPE" 2>/dev/null || true
# Re-create the pre-migration state for the recipe rehearsal: check out the
# parent commit so projects.md is tracked and present again.
git -C "$RECIPE" config user.email test@example.com
git -C "$RECIPE" config user.name test
git_q "$RECIPE" checkout -q HEAD~1
printf '| 通称 | プロジェクト名 |\n|---|---|\n| 社内案件 | operator-only |\n' \
  > "$RECIPE/registry/projects.md"
cp "$RECIPE/registry/projects.md" "$SB/projects.md.bak"     # step 1: back up
git_q "$RECIPE" checkout -q -- registry/projects.md          # discard local edit
git_q "$RECIPE" checkout -q main                             # step 2: advance
[[ -f "$RECIPE/registry/projects.md" ]] || \
  cp "$SB/projects.md.bak" "$RECIPE/registry/projects.md"    # step 3: restore
assert_eq "present" "$(file_state "$RECIPE/registry/projects.md")" \
  "recipe: back up -> advance -> restore recovers projects.md"
assert_eq "1" "$(grep -c 'operator-only' "$RECIPE/registry/projects.md")" \
  "recipe: restored file still carries the operator-specific row"
assert_eq "" "$(git -C "$RECIPE" status --porcelain registry/projects.md)" \
  "recipe: restored file is ignored, so it cannot re-enter history"

# 6. The committed template survives in every checkout — it is the tracked
#    artifact that /org-start regenerates the local registry from.
assert_eq "present" "$(file_state "$CLEAN/registry/projects.example.md")" \
  "clean checkout: the tracked template is present after pull"

# 7. The hazard is not specific to `pull`: any operation that moves HEAD
#    across the migration commit materializes the deletion. Pinned because it
#    was hit for real during this task's own rebase onto the migrated main,
#    and the runbook's advice ("back up first") only holds if operators know
#    it applies to rebase / branch switching too.
SWITCH="$SB/switch-checkout"
git_q "$SB" clone -q "$ORIGIN" "$SWITCH"
git -C "$SWITCH" config user.email test@example.com
git -C "$SWITCH" config user.name test
git_q "$SWITCH" checkout -q HEAD~1          # pre-migration commit: file tracked
assert_eq "present" "$(file_state "$SWITCH/registry/projects.md")" \
  "branch switch: projects.md is present on the pre-migration commit"
git_q "$SWITCH" checkout -q main            # cross the migration commit
assert_eq "absent" "$(file_state "$SWITCH/registry/projects.md")" \
  "branch switch: crossing the migration commit ALSO deletes projects.md"

# 8. This repo's real .gitignore must cover BOTH the registry and the backup
#    the runbook tells operators to make. The backup holds the same operator
#    data, so leaving it commitable would re-open the disclosure this change
#    closes — a later `git add -A` would stage it.
#    Uses check-ignore against the actual repo, not the fixture, so the rule
#    is pinned where it ships.
for candidate in registry/projects.md registry/projects.md.bak; do
  ignored=no
  git -C "$REPO_ROOT" check-ignore -q "$candidate" && ignored=yes
  assert_eq "yes" "$ignored" "repo .gitignore covers $candidate"
done

# --- Summary ---
echo "# $PASS passed, $FAIL failed out of $TEST_NUM tests"
[[ $FAIL -eq 0 ]]
