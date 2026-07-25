#!/usr/bin/env python3
# Phase 5 shim audit: confirmed minimal as of 2026-05-04 (#130)
"""Role-based settings.local.json integrity checker (Step B shim).

The validation engine now lives in ``core_harness.validator``. This
module is a thin CLI shim that:

* Loads the org-extension data (``tools/org_extension_schema.json``)
  and merges it with the framework JSON Schema retrieved from the
  pinned ``core_harness`` package via
  ``core_harness.schema.load_framework_schema()``.
* Re-exports the public engine symbols (``Finding``,
  ``validate_config``, ``validate_schema_integrity``,
  ``check_worker_settings``) so existing callers — including the test
  suite under ``tests/test_check_role_configs.py`` — keep using
  ``check_role_configs`` as the import surface unchanged.
* Overrides ``extract_role_blocks`` with a bilingual replacement
  that accepts either the canonical ja heading from
  ``org_extension_schema.json`` (``docs_section``) or one of its
  English aliases (Issue #340, Option A — "make the parser
  bilingual"). The ja repo's ``permissions.md`` ships ja headings;
  the en mirror translates them to English. Mapping each ja heading
  to the canonical role key and accepting en aliases as alternate
  section markers lets the same parser project roles out of either
  localisation without forcing the en mirror to keep ja anchors.
  Matching is anchored at the start of the heading and word-bounded
  so short aliases (``Lead``, ``Worker``) do not get picked up by
  unrelated sub-headings such as ``## Dispatcher (Lead-owned)``.
  Options (B) and (C) from Issue #340 were considered: (B) requires
  every translation to preserve hidden ja anchors (brittle); (C) —
  schema-driven ``permissions.md`` — is the long-term clean path but
  a much bigger refactor. (A) is the least-invasive fix that
  unblocks ``test_docs_projection_is_consistent`` on both
  localisations.
* Keeps the ja-specific behaviour (``check_docs``, ``check_on_disk``,
  ``run``, the CLI argparser, exit-code contract) here, since those
  read from the ja repo layout (permissions.md docs projection, the
  worker-tracked settings file walk).

Exit codes: 0 = OK, non-zero = drift detected.

Run ``python tools/check_role_configs.py --help`` for options.
"""

from __future__ import annotations

import argparse
import json
import posixpath
import re
import subprocess
import sys
from pathlib import Path

from core_harness.schema import load_framework_schema, merge_schemas
from core_harness.validator import (
    Finding,
    check_worker_settings,
    validate_config,
    validate_schema_integrity,
)


# Issue #340: ja → en heading aliases. Keys are the ja heading strings
# that org_extension_schema.json declares as ``docs_section``; values
# are the lists of English heading prefixes the en mirror's
# permissions.md may use for the same role. Multiple aliases are
# allowed because the surrounding codebase mixes ``Lead`` (the
# org-skill name) and ``Secretary`` (the schema description) for the
# 窓口 role; either is acceptable as an English heading.
_JA_TO_EN_ROLE_HEADING_ALIASES: dict[str, tuple[str, ...]] = {
    "ユーザー共通": ("User-wide", "User Common", "User common"),
    "窓口": ("Lead", "Secretary"),
    "ディスパッチャー": ("Dispatcher",),
    "キュレーター": ("Curator",),
    "ワーカー": ("Worker",),
}


def _heading_matches(heading: str, marker: str) -> bool:
    """Return True iff ``heading`` opens with ``marker`` as a whole word.

    The heading argument is the line *after* the ``## `` prefix has
    been stripped. We require the marker to appear at position 0 and
    to be followed either by end-of-line or by a non-word character
    (whitespace, ``(``, ``（``, ``:``, ``：`` …). Substring matching
    inside the line is *not* enough — Issue #340 review showed that a
    short alias like ``Lead`` would otherwise be picked up by an
    unrelated heading such as ``## Dispatcher (Lead-owned)``.
    """
    if not heading.startswith(marker):
        return False
    rest = heading[len(marker) :]
    if rest == "":
        return True
    nxt = rest[0]
    return not (nxt.isalnum() or nxt == "_")


def extract_role_blocks(md_text: str, roles: dict) -> dict:
    """Extract the first ```json code block under each role's docs heading.

    Bilingual replacement for
    ``core_harness.validator.extract_role_blocks``: a section matches
    if its ``## ``-prefixed heading line *opens with* either the
    canonical ja ``docs_section`` declared in the schema or one of the
    English aliases from ``_JA_TO_EN_ROLE_HEADING_ALIASES`` (Issue
    #340, Option A). Roles whose ``docs_section`` is null/missing are
    skipped, mirroring the upstream contract. Word-boundary anchoring
    (see ``_heading_matches``) prevents short en aliases like ``Lead``
    from being picked up by unrelated sub-headings.
    """
    results: dict = {}
    sections = re.split(r"(?m)^## ", md_text)
    for role_name, role_def in roles.items():
        marker = role_def.get("docs_section")
        if not marker:
            continue
        markers = [marker, *_JA_TO_EN_ROLE_HEADING_ALIASES.get(marker, ())]
        block = None
        for section in sections[1:]:
            heading = section.splitlines()[0]
            if any(_heading_matches(heading, m) for m in markers):
                m = re.search(r"```json\n(.*?)\n```", section, re.DOTALL)
                if m:
                    try:
                        block = json.loads(m.group(1))
                    except json.JSONDecodeError as exc:
                        block = {"__parse_error__": str(exc)}
                break
        results[role_name] = block
    return results


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCHEMA = REPO_ROOT / "tools" / "org_extension_schema.json"
DEFAULT_PERMISSIONS_MD = (
    REPO_ROOT
    / ".claude"
    / "skills"
    / "org-setup"
    / "references"
    / "permissions.md"
)

__all__ = [
    "Finding",
    "REPO_ROOT",
    "DEFAULT_SCHEMA",
    "DEFAULT_PERMISSIONS_MD",
    "load_schema",
    "validate_config",
    "validate_schema_integrity",
    "extract_role_blocks",
    "check_worker_settings",
    "check_docs",
    "check_hook_command_paths",
    "check_on_disk_hook_paths",
    "check_on_disk",
    "run",
    "main",
]


def load_schema(path: Path) -> dict:
    """Load the org-extension data and return the merged framework +
    extension dict.

    ``path`` points at the org-extension JSON. The framework JSON
    Schema is fetched from the pinned ``core_harness`` package (so the
    exact ``requirements.txt`` pin governs validator behaviour). The
    returned dict is what every downstream engine function expects
    (``global``, ``required_hook_scripts``, ``roles``,
    ``worker_roles``).
    """
    with Path(path).open(encoding="utf-8") as fh:
        org_extension = json.load(fh)
    framework = load_framework_schema()
    return merge_schemas(framework, org_extension)


def _load_override_allow(settings_path: Path) -> set:
    """Return the allow entries declared in sibling
    ``settings.local.override.json`` (the closed-world escape hatch).
    """
    ov = settings_path.with_name("settings.local.override.json")
    if not ov.is_file():
        return set()
    try:
        data = json.loads(ov.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return set()
    if not isinstance(data, dict):
        return set()
    perms = data.get("permissions")
    if not isinstance(perms, dict):
        return set()
    allow = perms.get("allow")
    if not isinstance(allow, list):
        return set()
    return {x for x in allow if isinstance(x, str)}


def check_docs(schema: dict, permissions_md: Path) -> list:
    if not Path(permissions_md).is_file():
        return [
            Finding(
                str(permissions_md),
                "<docs>",
                "ERROR",
                "permissions.md not found",
            )
        ]
    text = Path(permissions_md).read_text(encoding="utf-8")
    blocks = extract_role_blocks(text, schema["roles"])
    findings: list = []
    for role_name, role_schema in schema["roles"].items():
        if not role_schema.get("docs_section"):
            continue
        config = blocks.get(role_name)
        findings.extend(
            validate_config(
                f"permissions.md[{role_schema['docs_section']}]",
                role_name,
                config,
                role_schema,
                schema.get("global", {}),
            )
        )
    return findings


_HOOKS_DIR_MARKER = ".hooks/"
_SHELL_WORD_BREAKS = frozenset(" \t\r\n;|&<>()")
_WINDOWS_ABS_RE = re.compile(r"^[A-Za-z]:/")


def _shell_words(command: str) -> list:
    """Split ``command`` into shell words, honouring quote concatenation.

    Quoted segments must NOT be validated on their own: the shell joins
    a quote to whatever abuts it, so ``bash "<root>/.hooks/x.sh".bak``
    is the single word ``<root>/.hooks/x.sh.bak`` and actually executes
    a file that does not exist -- leaving the guard dead, which is the
    very failure mode Issue #768 is about. Treating the quoted part
    alone would report that command as correctly anchored.
    """
    words: list = []
    current: list = []
    started = False
    quote = None
    for ch in command:
        if quote is not None:
            if ch == quote:
                quote = None
            else:
                current.append(ch)
            continue
        if ch in ('"', "'"):
            quote = ch
            started = True
            continue
        if ch in _SHELL_WORD_BREAKS:
            if started:
                words.append("".join(current))
                current = []
                started = False
            continue
        current.append(ch)
        started = True
    if started:
        words.append("".join(current))
    return words


def _is_absolute_posixish(path: str) -> bool:
    """True for ``/unix/abs`` and ``C:/windows/abs`` alike."""
    return path.startswith("/") or bool(_WINDOWS_ABS_RE.match(path))


def _hook_script_refs(command: str, required_scripts: frozenset) -> list:
    """Shell words in ``command`` that should resolve to a hook script.

    The selection test deliberately does NOT key off the ``.hooks/``
    literal alone. Issue #768 is a *cwd-dependent hook command* defect,
    and the cheapest way to reintroduce it is to drop the directory from
    the path entirely (``bash block-workers-delete.sh``). A ``.hooks/``
    -only trigger would skip exactly those commands, so a word also
    qualifies when it names -- or ends with -- one of the schema's
    ``required_hook_scripts``. Words matching none of the three tests
    are operator-owned commands and are left alone.
    """
    selected: list = []
    for word in _shell_words(command):
        slashed = word.replace("\\", "/")
        if (
            _HOOKS_DIR_MARKER in slashed
            or posixpath.basename(slashed) in required_scripts
            or any(slashed.endswith(s) for s in required_scripts)
        ):
            selected.append(word)
    return selected


def _iter_hook_commands(config: dict):
    hooks = (config or {}).get("hooks") or {}
    if not isinstance(hooks, dict):
        return
    for event, entries in hooks.items():
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            for sub in entry.get("hooks") or []:
                if isinstance(sub, dict) and isinstance(sub.get("command"), str):
                    yield event, sub["command"]


def _anchored_at(normalized: str, script: str, anchor: Path) -> bool:
    """True iff ``normalized`` is ``<anchor>/.hooks/<script>``."""
    expected = posixpath.normpath(
        anchor.as_posix() + "/" + ".hooks" + "/" + script
    )
    if normalized == expected:
        return True
    if not _is_absolute_posixish(normalized):
        # Resolving a relative path would silently anchor it at the CWD --
        # precisely the defect being audited. Never accept one.
        return False
    # A checkout reached through a symlink is lexically different but names
    # the same script; compare canonical targets before rejecting.
    try:
        return Path(normalized).resolve() == Path(expected).resolve()
    except OSError:
        return False


def _check_hook_paths(
    source: str,
    role: str,
    config: dict,
    anchors: tuple,
    required_scripts: frozenset,
    placeholders: dict,
) -> list:
    """Report hook commands in ``config`` that are not root-anchored.

    ``anchors`` are the roots under whose ``.hooks/`` a command may
    legitimately live; a command is accepted when it resolves under any
    of them AND the script exists there. ``placeholders`` maps each
    substitutable token to the root it expands to -- they differ, so
    they cannot share one value: ``{claude_org_path}`` is a prune-time
    placeholder resolved to the org root, while ``${CLAUDE_PROJECT_DIR}``
    is expanded by Claude Code to the *project* directory holding the
    settings file. A generated settings file must not still contain
    ``{claude_org_path}``, so the on-disk caller omits it and the
    surviving literal is reported (the "pasted the sample without
    resolving it" case).
    """
    findings: list = []
    for event, command in _iter_hook_commands(config):
        for ref in _hook_script_refs(command, required_scripts):
            resolved = ref
            for token, value in placeholders.items():
                resolved = resolved.replace(token, value)
            normalized = posixpath.normpath(resolved.replace("\\", "/"))
            script = posixpath.basename(normalized)
            matched = next(
                (a for a in anchors if _anchored_at(normalized, script, a)),
                None,
            )
            if matched is None:
                expected = posixpath.normpath(
                    anchors[0].as_posix() + "/" + ".hooks" + "/" + script
                )
                findings.append(
                    Finding(
                        source,
                        role,
                        "ERROR",
                        (
                            f"{event} hook command is not anchored at the org "
                            f"root: {ref!r} normalizes to {normalized!r}, "
                            f"expected {expected!r}. A relative or otherwise "
                            "unanchored hook path silently no-ops for any role "
                            "whose cwd is not the org root; use: "
                            'bash "{claude_org_path}/.hooks/<script>"'
                        ),
                    )
                )
                continue
            if not (matched / ".hooks" / script).is_file():
                findings.append(
                    Finding(
                        source,
                        role,
                        "ERROR",
                        (
                            f"{event} hook command references a script that "
                            "does not exist: "
                            + posixpath.normpath(
                                matched.as_posix() + "/.hooks/" + script
                            )
                        ),
                    )
                )
    return findings


def check_hook_command_paths(
    schema: dict,
    permissions_md: Path,
    source_root: Path = REPO_ROOT,
    *,
    schema_path: Path = DEFAULT_SCHEMA,
) -> list:
    """Every org-role hook command must resolve to ``<root>/.hooks/<script>``.

    ``required_hooks.command_contains`` only asserts that the script
    *basename* appears somewhere in the command string, so the relative
    ``bash .hooks/x.sh`` and the absolute ``bash "<root>/.hooks/x.sh"``
    are indistinguishable to it -- yet the relative form is a silent
    no-op for every role whose cwd is not the org root (the dispatcher
    runs in ``.dispatcher/``). That is how Issue #768 passed CI.

    This check normalizes rather than matching substrings: placeholders
    are substituted with the org root, ``\\`` is folded to ``/`` for
    Windows-form commands, the result is ``normpath``-ed and compared
    for *equality* against the root-anchored path, and the script must
    exist on disk. Equality rejects the four cases a path-fragment
    substring test would still pass: a foreign absolute root, a ``..``
    escape, a nonexistent script, and a bare relative filename.

    ``source_root`` is the checkout that ships ``.hooks/`` -- NOT the
    audit ``--root``. The sources validated here are the SoT *templates*
    (permissions.md role blocks and the schema's ``worker_roles``), which
    always travel next to the hook scripts they name, so anchoring them
    at an unrelated audit root would emit one spurious finding per
    template command. Returns ``[]`` when ``source_root`` has no
    ``.hooks/`` directory, mirroring the prune tool's guard.
    """
    resolved_root = Path(source_root).resolve()
    if not (resolved_root / ".hooks").is_dir():
        return []
    required_scripts = frozenset(schema.get("required_hook_scripts") or ())
    findings: list = []
    if Path(permissions_md).is_file():
        text = Path(permissions_md).read_text(encoding="utf-8")
        blocks = extract_role_blocks(text, schema["roles"])
        for role_name, role_schema in schema["roles"].items():
            if not role_schema.get("docs_section"):
                continue
            config = blocks.get(role_name)
            if not isinstance(config, dict) or "__parse_error__" in config:
                # check_docs already reports missing / unparsable blocks.
                continue
            findings.extend(
                _check_hook_paths(
                    f"permissions.md[{role_schema['docs_section']}]",
                    role_name,
                    config,
                    (resolved_root,),
                    required_scripts,
                    {"{claude_org_path}": resolved_root.as_posix()},
                )
            )
    schema_name = Path(schema_path).name
    for wr_name, template in (schema.get("worker_roles") or {}).items():
        if not isinstance(template, dict):
            continue  # ``$comment*`` string entries
        findings.extend(
            _check_hook_paths(
                f"{schema_name}[worker_roles.{wr_name}]",
                wr_name,
                template,
                (resolved_root,),
                required_scripts,
                {"{claude_org_path}": resolved_root.as_posix()},
            )
        )
    return findings


_CLAUDE_PROJECT_DIR = "${CLAUDE_PROJECT_DIR}"


def check_on_disk_hook_paths(
    schema: dict,
    settings_path: Path,
    role: str,
    config: dict,
    root: Path,
) -> list:
    """Root-anchoring check for one *generated* settings file.

    Opt-in counterpart to ``check_hook_command_paths``: wired only into
    the ``--include-local`` / ``--role`` paths, which already read
    on-disk files. Without it a merged fix is unverifiable in the field
    -- real ``settings.local.json`` files are gitignored, so CI never
    sees whether an installed terminal still carries the relative form
    that Issue #768 shipped.

    Two roots are legitimate here and they are NOT interchangeable: the
    org root the file declares in ``env.CLAUDE_ORG_PATH`` (falling back
    to ``root``), and the project directory holding the settings file,
    which is what Claude Code expands ``${CLAUDE_PROJECT_DIR}`` to. A
    worker in a worktree has ``root`` set to that worktree while its
    hooks correctly point at the central checkout, so collapsing the two
    would either flag every valid hook or accept a dead
    ``${CLAUDE_PROJECT_DIR}`` path that only exists centrally.
    """
    env = (config or {}).get("env") or {}
    declared = env.get("CLAUDE_ORG_PATH") if isinstance(env, dict) else None
    declared = declared if isinstance(declared, str) and declared else None
    try:
        org_root = Path(declared).resolve() if declared else Path(root).resolve()
        project_dir = Path(settings_path).resolve().parent.parent
    except OSError:
        return []
    if not (org_root / ".hooks").is_dir():
        if declared is not None:
            # An explicitly declared root with no .hooks/ is a broken
            # installation (e.g. the checkout was moved or deleted): every
            # absolute hook command in this file is dead. Only the silent
            # ``root`` fallback may be skipped.
            return [
                Finding(
                    str(settings_path),
                    role,
                    "ERROR",
                    (
                        "env.CLAUDE_ORG_PATH points at "
                        f"{org_root.as_posix()!r}, which has no .hooks/ "
                        "directory; every hook command anchored there is "
                        "dead. Repoint it at the org checkout and "
                        "regenerate via tools/org_setup_prune.py."
                    ),
                )
            ]
        return []
    anchors = (org_root,)
    if project_dir != org_root and (project_dir / ".hooks").is_dir():
        anchors = (org_root, project_dir)
    return _check_hook_paths(
        str(settings_path),
        role,
        config,
        anchors,
        frozenset(schema.get("required_hook_scripts") or ()),
        {_CLAUDE_PROJECT_DIR: project_dir.as_posix()},
    )


class _GitTrackedError(Exception):
    """Raised when ``_is_git_tracked`` cannot reach a definite answer.

    Carries a short ``reason`` so the caller can surface it as an
    audit ``Finding``. Renamed-internal so callers must handle the
    fail-CLOSED case explicitly (see cross-review M1).
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _is_git_tracked(path: Path, root: Path) -> bool:
    """Return True when ``path`` is tracked by git (not gitignored).

    Raises ``_GitTrackedError`` when the answer cannot be determined —
    e.g. ``git`` is not on PATH, or ``path`` lives outside ``root``.
    The caller MUST treat this as an audit failure (Finding ERROR);
    silently skipping such paths previously hid real drift on
    machines where git happens to be missing (cross-review M1).
    """
    try:
        rel = path.resolve().relative_to(root.resolve())
    except ValueError:
        raise _GitTrackedError(
            f"path {str(path)!r} is not under repository root {str(root)!r}; "
            "cannot determine git-tracked status"
        )
    try:
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(rel).replace("\\", "/")],
            cwd=root,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError:
        raise _GitTrackedError(
            "git executable not found on PATH; cannot determine "
            "git-tracked status (audit fails closed)"
        )
    # ``git ls-files --error-unmatch`` exits 0 for tracked, 1 for not
    # tracked, and 128 for fatal errors (``safe.directory`` /
    # ``not a git repository`` / corrupt index / permission issues).
    # Treating 128 as "untracked" would silently skip the audit on
    # exactly the misconfigured machines that should fail loudest, so
    # we surface it as ``_GitTrackedError`` (cross-review M1 follow-up).
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    stderr_tail = (result.stderr or b"").decode("utf-8", errors="replace").strip()
    if len(stderr_tail) > 200:
        stderr_tail = stderr_tail[:200] + "..."
    raise _GitTrackedError(
        f"git ls-files exited {result.returncode}"
        + (f": {stderr_tail}" if stderr_tail else "")
    )


WORKER_LOCAL_SETTINGS = ".claude/settings.local.json"


def _transport_aware_role_schema(role_name: str, role_schema: dict) -> dict:
    """Return ``role_schema`` with ``required_allow`` projected onto the active
    transport's allowlist (§5.3, D から defer した broker consume).

    **既定 ``renga`` では同一オブジェクトをそのまま返す (恒等)** ので、CI の通常
    経路 (``ORG_TRANSPORT`` 無設定) は挙動・検証結果が完全に不変。
    ``ORG_TRANSPORT=broker`` のときだけ、schema の renga 期待
    (``mcp__renga-peers__*``) を当該ロールの broker tier (``mcp__org-broker__*``)
    へ rewrite した **浅いコピー** を返す。これは on-disk の broker 設定
    (``ORG_TRANSPORT=broker`` で生成したもの) を検証するための期待面の付け替えで、
    byte 比較される ``org_extension_schema.json`` 自体は touch しない (in-memory
    のみ)。permissions.md は renga のままなので ``check_docs`` 側は付け替えない。
    """
    required = role_schema.get("required_allow")
    if not isinstance(required, list):
        return role_schema
    # transport モジュールは同じ tools/ ディレクトリ。スクリプト実行時は script
    # dir が sys.path[0]、モジュール import 時は呼び元が path を通している前提だが、
    # 念のため lazy import 時に保険を入れる。
    import sys as _sys
    from pathlib import Path as _Path

    _here = str(_Path(__file__).resolve().parent)
    if _here not in _sys.path:
        _sys.path.insert(0, _here)
    import transport as _transport

    rewritten = _transport.rewrite_allow_entries(required, role_name)
    if rewritten == required:
        # renga (恒等) もしくは renga ブロックを持たないロール: 無変更。
        return role_schema
    return {**role_schema, "required_allow": rewritten}


def check_on_disk(
    schema: dict,
    root: Path,
    include_untracked: bool = False,
    role_override: str | None = None,
) -> list:
    findings: list = []
    if role_override is not None:
        role_schema = schema["roles"].get(role_override)
        if role_schema is None:
            findings.append(
                Finding(
                    "<cli>",
                    role_override,
                    "ERROR",
                    f"unknown --role: {role_override!r}",
                )
            )
            return findings
        candidate_paths = role_schema.get("settings_paths") or [WORKER_LOCAL_SETTINGS]
        checked_any = False
        for rel in candidate_paths:
            path = Path(root) / rel
            if not path.is_file():
                continue
            checked_any = True
            try:
                config = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                findings.append(
                    Finding(
                        str(path),
                        role_override,
                        "ERROR",
                        f"JSON parse error: {exc}",
                    )
                )
                continue
            findings.extend(
                validate_config(
                    str(path),
                    role_override,
                    config,
                    _transport_aware_role_schema(role_override, role_schema),
                    schema.get("global", {}),
                    extra_allowed=_load_override_allow(path),
                )
            )
            if include_untracked:
                findings.extend(
                    check_on_disk_hook_paths(
                        schema, path, role_override, config, Path(root)
                    )
                )
        if not checked_any:
            findings.append(
                Finding(
                    str(Path(root) / candidate_paths[0]),
                    role_override,
                    "ERROR",
                    (
                        "settings.local.json not found; tried: "
                        + ", ".join(str(Path(root) / p) for p in candidate_paths)
                    ),
                )
            )
        return findings

    for role_name, role_schema in schema["roles"].items():
        for rel in role_schema.get("settings_paths", []):
            path = Path(root) / rel
            if not path.is_file():
                continue
            if not include_untracked:
                try:
                    tracked = _is_git_tracked(path, Path(root))
                except _GitTrackedError as exc:
                    findings.append(
                        Finding(
                            str(path),
                            role_name,
                            "ERROR",
                            (
                                "could not determine git-tracked status "
                                f"({exc.reason}); pass --include-local to "
                                "audit this file regardless, or install git"
                            ),
                        )
                    )
                    continue
                if not tracked:
                    continue
            try:
                config = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                findings.append(
                    Finding(
                        str(path),
                        role_name,
                        "ERROR",
                        f"JSON parse error: {exc}",
                    )
                )
                continue
            findings.extend(
                validate_config(
                    str(path),
                    role_name,
                    config,
                    _transport_aware_role_schema(role_name, role_schema),
                    schema.get("global", {}),
                    extra_allowed=_load_override_allow(path),
                )
            )
            if include_untracked:
                findings.extend(
                    check_on_disk_hook_paths(
                        schema, path, role_name, config, Path(root)
                    )
                )
    return findings


def run(
    schema_path: Path = DEFAULT_SCHEMA,
    permissions_md: Path = DEFAULT_PERMISSIONS_MD,
    root: Path = REPO_ROOT,
    include_on_disk: bool = True,
    include_untracked: bool = False,
    role_override: str | None = None,
    worker_settings_base: Path | None = None,
) -> list:
    schema = load_schema(schema_path)
    findings: list = []
    findings.extend(validate_schema_integrity(schema))
    findings.extend(check_docs(schema, permissions_md))
    # Anchored at REPO_ROOT (the checkout shipping .hooks/), not ``root``:
    # this validates the SoT templates, which are not root-dependent.
    findings.extend(
        check_hook_command_paths(
            schema, permissions_md, REPO_ROOT, schema_path=schema_path
        )
    )
    if include_on_disk:
        findings.extend(
            check_on_disk(
                schema,
                root,
                include_untracked=include_untracked,
                role_override=role_override,
            )
        )
    if worker_settings_base is not None:
        # include_worktrees=True (core-harness 0.3.1+) descends into
        # ``<base>/.worktrees/<branch>/`` so worker checkouts living
        # under a `.worktrees/` parent are audited too. Refs M4.
        findings.extend(
            check_worker_settings(
                schema,
                worker_settings_base,
                include_worktrees=True,
            )
        )
    return findings


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate per-role settings.local.json against the schema."
    )
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--permissions-md", type=Path, default=DEFAULT_PERMISSIONS_MD)
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument(
        "--docs-only",
        action="store_true",
        help=(
            "Validate only permissions.md + schema integrity; skip every "
            "on-disk settings*.json. Default validates tracked settings files."
        ),
    )
    parser.add_argument(
        "--include-local",
        action="store_true",
        help=(
            "Also validate gitignored / untracked on-disk settings.local.json "
            "files at the schema-declared paths. Default checks only tracked "
            "files (e.g. .claude/settings.json) so CI and local runs agree."
        ),
    )
    parser.add_argument(
        "--role",
        default=None,
        help=(
            "Validate <root>/.claude/settings.local.json against the given "
            "role schema (e.g. 'worker' when invoked from inside a worker "
            "worktree). Resolves path ambiguity since .claude/settings.local.json "
            "hosts different role configs in different worktrees. Implies "
            "--include-local semantics."
        ),
    )
    parser.add_argument(
        "--include-worker-settings",
        type=Path,
        default=None,
        metavar="BASE_DIR",
        help=(
            "Also enumerate <BASE_DIR>/*/.claude/settings.local.json and "
            "report drift against the worker_roles templates in the schema. "
            "Opt-in; existing invocations are unaffected."
        ),
    )
    args = parser.parse_args(argv)

    findings = run(
        schema_path=args.schema,
        permissions_md=args.permissions_md,
        root=args.root,
        include_on_disk=not args.docs_only,
        include_untracked=args.include_local or args.role is not None,
        role_override=args.role,
        worker_settings_base=args.include_worker_settings,
    )

    if not findings:
        print("role_configs: OK")
        return 0

    for f in findings:
        try:
            print(f.format())
        except UnicodeEncodeError:
            print(f.format().encode("ascii", "replace").decode("ascii"))
    errors = sum(1 for f in findings if f.severity == "ERROR")
    print(f"role_configs: {errors} error(s)", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
