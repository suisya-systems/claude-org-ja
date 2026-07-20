"""Project dossier (``registry/projects/<slug>/``) resolution — Issue #744 Stage 1.

A *dossier* is the per-project home for things that used to have nowhere to
live: the project's charter, its operational notes, and its **execution
profiles** (the per-task-class settings the Secretary used to decide by hand
on every dispatch).

    registry/projects/<slug>/
      charter.md            # what "good" means for this project
      notes/<topic>.md      # operational knowledge, one phenomenon per file
      profiles/base.toml    # execution profile base
      profiles/<class>.toml # per task class, inherits/overrides base
      contracts/README.md   # REFERENCES ONLY - see below

Design SoT: ``docs/design/project-dossier.md``.

Three disciplines are enforced here rather than left to prose:

1. **Profiles configure execution, not approval.** Keys that would encode a
   merge pre-approval (or the org-wide ``permission_mode``) are rejected
   outright — see :data:`FORBIDDEN_KEYS`.
2. **No axis silently does nothing.** Every key is classified: wired
   (applied), deferred (accepted with a warning saying it is not wired in
   Stage 1), forbidden (error), or unknown (error).
3. **contracts/ holds references only.** A standing scope contract is a
   human-approved standalone document; copying its body into a dossier turns
   a session-scoped exception into durable project policy. Anything other
   than ``README.md`` under ``contracts/`` raises a warning.

Resolution order (weakest first), continuing the existing ``--from-toml``
sentinel merge in :mod:`tools.gen_delegate_payload`::

    profiles/base.toml  <  profiles/<class>.toml  <  --from-toml  <  CLI flags
"""
from __future__ import annotations

# Direct-script bootstrap (see tools/resolve_worker_layout.py for context).
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

try:  # Python 3.11+
    import tomllib  # type: ignore[import-not-found]
except ModuleNotFoundError:  # pragma: no cover - 3.10 fallback
    import tomli as tomllib  # type: ignore[no-redef]


class DossierError(ValueError):
    """Raised when a dossier / profile is missing or malformed.

    The CLI turns this into a ``SystemExit`` so a typo'd profile fails loudly
    instead of silently degrading to an unprofiled brief.
    """


# --- Layout -----------------------------------------------------------------

DOSSIER_SUBDIR = ("registry", "projects")
PROFILES_DIRNAME = "profiles"
NOTES_DIRNAME = "notes"
CONTRACTS_DIRNAME = "contracts"
CHARTER_FILENAME = "charter.md"
BASE_PROFILE_STEM = "base"

# --- Embedding budget (docs/design/project-dossier.md section 5) ------------

PER_FILE_CHAR_LIMIT = 4000
TOTAL_CHAR_LIMIT = 12000

# --- Key classification (docs/design/project-dossier.md section 4.3) --------

#: section -> keys that are actually wired to a downstream surface.
WIRED_KEYS: dict[str, frozenset[str]] = {
    "project": frozenset({"description"}),
    "task": frozenset({"verification_depth", "commit_prefix", "branch_style"}),
    "implementation": frozenset({"guidance", "target_files"}),
    "references": frozenset({"knowledge"}),
    "parallel": frozenset({"notes"}),
    "dossier": frozenset({"embed_charter", "embed_notes"}),
}

#: Known axes with no downstream receptacle in Stage 1. Accepted only inside
#: the dedicated ``[profile]`` table, and always warned about, so a dossier
#: author cannot believe they configured something that does nothing.
DEFERRED_KEYS = frozenset({"model", "codex_round_max", "pr_shape", "codex_review"})

#: Rejected anywhere. ``permission_mode`` is org-wide (registry/org-config.md,
#: whose value is additionally hardcoded as a literal across skills and
#: contracts, so a per-project override would desync invisibly). The rest are
#: approval-shaped: a profile configures execution, never approval.
FORBIDDEN_KEYS = frozenset(
    {
        "permission_mode",
        "merge_preapproved",
        "merge_pre_approved",
        "pre_approved_merge",
        "auto_merge",
        "merge_approved",
        "approval",
        "approved_by",
    }
)

DEFERRED_SECTION = "profile"

VALID_DEPTHS = frozenset({"full", "minimal"})

#: Placeholders ``branch_style`` may use. Anything else is a typo, not a
#: feature — rendering uses explicit substitution (never ``str.format``, which
#: would happily expand attribute/index expressions).
BRANCH_STYLE_FIELDS = ("task_id", "project_slug")
_PLACEHOLDER_RE = re.compile(r"\{([^{}]*)\}")


@dataclass(frozen=True)
class ProfileResolution:
    """Outcome of resolving ``--profile <slug>[/<class>]``.

    ``plan_kwargs`` is the profile layer of the
    :func:`tools.gen_delegate_payload.build_delegate_plan` kwargs merge — the
    weakest layer, overridden by ``--from-toml`` and then by CLI flags.
    ``branch_style`` is held out of that dict because it needs the *final*
    ``task_id`` / ``project_slug`` (which stronger layers may still change);
    the caller renders it after the merge and only when no stronger layer
    supplied a branch.
    """

    slug: str
    class_name: Optional[str]
    dossier_dir: Path
    plan_kwargs: dict[str, Any] = field(default_factory=dict)
    branch_style: Optional[str] = None
    dossier_block: Optional[str] = None
    warnings: list[str] = field(default_factory=list)
    sources: list[Path] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def dossier_root(claude_org_root: Path) -> Path:
    return Path(claude_org_root).joinpath(*DOSSIER_SUBDIR)


def dossier_dir(claude_org_root: Path, slug: str) -> Path:
    return dossier_root(claude_org_root) / slug


def _repo_rel_display(slug: str, *parts: str) -> str:
    """Repo-root-relative display path (forward slashes, no leading ./).

    Built from the canonical layout rather than ``os.path.relpath`` so the
    marker text is stable regardless of where the dossier physically sits
    (a temp sandbox in tests, the real repo in production).
    """
    return "/".join([*DOSSIER_SUBDIR, slug, *parts])


def available_classes(dossier_directory: Path) -> list[str]:
    """Sorted profile class names (``base`` excluded) for error messages."""
    profiles = dossier_directory / PROFILES_DIRNAME
    if not profiles.is_dir():
        return []
    return sorted(
        p.stem
        for p in profiles.glob("*.toml")
        if p.is_file() and p.stem != BASE_PROFILE_STEM
    )


# ---------------------------------------------------------------------------
# Profile parsing
# ---------------------------------------------------------------------------


def parse_profile_ref(ref: str) -> tuple[str, Optional[str]]:
    """Split ``<slug>`` or ``<slug>/<class>`` into its parts.

    A bare ``<slug>`` means "base profile only" and is an intentional,
    explicit request — it is the supported way to opt out of a class without
    getting a silent fallback (see ``--profile`` docs).
    """
    # Whitespace is stripped, slashes are NOT: ``en/`` must surface as an
    # empty class rather than quietly becoming a base-only request. Section
    # 4.2 of the design makes the base-only path explicit on purpose.
    raw = (ref or "").strip()
    if not raw:
        raise DossierError("--profile requires <slug> or <slug>/<class>")
    raw_parts = raw.split("/")
    if len(raw_parts) > 2:
        raise DossierError(
            f"--profile takes <slug> or <slug>/<class>, got {ref!r}"
        )
    # Validate the STRIPPED components, because the stripped values are what
    # get used to build the path. Checking the raw ones let `--profile ' .. '`
    # through: the guard saw " .. " (not in the reject set) while the lookup
    # used "..", escaping registry/projects/ into registry/ itself.
    parts = [p.strip() for p in raw_parts]
    slug = parts[0]
    if not slug:
        raise DossierError(f"--profile has an empty slug: {ref!r}")
    if len(parts) == 2 and not parts[1]:
        raise DossierError(f"--profile has an empty class: {ref!r}")
    for part in parts:
        if part in {".", ".."} or "\\" in part or "\0" in part:
            raise DossierError(f"--profile has an invalid path component: {ref!r}")
    class_name = parts[1] if len(parts) == 2 else None
    return slug, class_name


def _load_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise DossierError(f"{path}: invalid TOML ({exc})") from exc
    except OSError as exc:
        raise DossierError(f"{path}: cannot read ({exc})") from exc
    if not isinstance(data, dict):
        raise DossierError(f"{path}: profile root must be a TOML table")
    return data


def _classify_and_collect(
    data: dict[str, Any], path: Path, warnings: list[str]
) -> dict[str, dict[str, Any]]:
    """Validate one profile TOML and return its wired values by section.

    Classification (design section 4.3): wired -> applied; deferred -> accepted
    with a warning; forbidden -> error; unknown -> error.
    """
    collected: dict[str, dict[str, Any]] = {}
    for section, body in data.items():
        if not isinstance(body, dict):
            raise DossierError(
                f"{path}: [{section}] must be a TOML table "
                f"(top-level scalars are not part of the profile schema)"
            )

        if section == DEFERRED_SECTION:
            for key in body:
                if key in FORBIDDEN_KEYS:
                    raise DossierError(_forbidden_message(path, section, key))
                if key not in DEFERRED_KEYS:
                    raise DossierError(
                        f"{path}: unknown key [{section}].{key}. "
                        f"[{DEFERRED_SECTION}] only carries axes that are not "
                        f"wired yet: {', '.join(sorted(DEFERRED_KEYS))}"
                    )
                warnings.append(
                    f"dossier: axis '{key}' is declared in {path.name} but is "
                    f"not wired in Stage 1 (no downstream surface); it has no "
                    f"effect on the generated brief"
                )
            continue

        if section not in WIRED_KEYS:
            raise DossierError(
                f"{path}: unknown section [{section}]. Allowed: "
                f"{', '.join(sorted([*WIRED_KEYS, DEFERRED_SECTION]))}"
            )

        allowed = WIRED_KEYS[section]
        values: dict[str, Any] = {}
        for key, value in body.items():
            if key in FORBIDDEN_KEYS:
                raise DossierError(_forbidden_message(path, section, key))
            if key in allowed:
                values[key] = value
                continue
            if key in DEFERRED_KEYS:
                raise DossierError(
                    f"{path}: [{section}].{key} is not wired in Stage 1. "
                    f"Declare not-yet-wired axes under [{DEFERRED_SECTION}] "
                    f"so the warning is explicit."
                )
            raise DossierError(
                f"{path}: unknown key [{section}].{key}. "
                f"Allowed in [{section}]: {', '.join(sorted(allowed))}"
            )
        if values:
            collected[section] = values
    return collected


def _forbidden_message(path: Path, section: str, key: str) -> str:
    if key == "permission_mode":
        return (
            f"{path}: [{section}].permission_mode is not allowed in a profile. "
            f"Permission mode is org-wide (registry/org-config.md) and is also "
            f"hardcoded at spawn sites, so a per-project override would desync "
            f"silently."
        )
    return (
        f"{path}: [{section}].{key} is not allowed in a profile. Profiles "
        f"configure execution, never approval; a standing scope contract is a "
        f"separate human-approved document."
    )


def _merge_sections(
    into: dict[str, dict[str, Any]], layer: dict[str, dict[str, Any]]
) -> None:
    """Overlay ``layer`` on ``into`` key-by-key (class overrides base)."""
    for section, values in layer.items():
        into.setdefault(section, {}).update(values)


# ---------------------------------------------------------------------------
# Value validation
# ---------------------------------------------------------------------------


def _require_str(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DossierError(f"{label} must be a non-empty string")
    return value


def _require_str_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(x, str) and x.strip() for x in value
    ):
        raise DossierError(f"{label} must be a list of non-empty strings")
    return list(value)


def _validate_values(sections: dict[str, dict[str, Any]]) -> None:
    task = sections.get("task", {})
    if "verification_depth" in task:
        depth = task["verification_depth"]
        if depth not in VALID_DEPTHS:
            raise DossierError(
                f"[task].verification_depth must be one of "
                f"{sorted(VALID_DEPTHS)}, got {depth!r}"
            )
    if "commit_prefix" in task:
        _require_str(task["commit_prefix"], "[task].commit_prefix")
    if "branch_style" in task:
        validate_branch_style(_require_str(task["branch_style"], "[task].branch_style"))

    project = sections.get("project", {})
    if "description" in project:
        _require_str(project["description"], "[project].description")

    impl = sections.get("implementation", {})
    if "guidance" in impl:
        _require_str(impl["guidance"], "[implementation].guidance")
    if "target_files" in impl:
        _require_str_list(impl["target_files"], "[implementation].target_files")

    refs = sections.get("references", {})
    if "knowledge" in refs:
        _require_str_list(refs["knowledge"], "[references].knowledge")

    parallel = sections.get("parallel", {})
    if "notes" in parallel:
        _require_str(parallel["notes"], "[parallel].notes")

    dossier = sections.get("dossier", {})
    if "embed_charter" in dossier and not isinstance(dossier["embed_charter"], bool):
        raise DossierError("[dossier].embed_charter must be a boolean")
    if "embed_notes" in dossier:
        _require_str_list(dossier["embed_notes"], "[dossier].embed_notes")


def validate_branch_style(style: str) -> None:
    """Reject unknown ``{placeholders}`` — a typo must not ship as literal text."""
    for name in _PLACEHOLDER_RE.findall(style):
        if name not in BRANCH_STYLE_FIELDS:
            raise DossierError(
                f"[task].branch_style uses unknown placeholder {{{name}}}; "
                f"allowed: {', '.join('{%s}' % f for f in BRANCH_STYLE_FIELDS)}"
            )


def render_branch_style(style: str, *, task_id: str, project_slug: str) -> str:
    """Expand ``branch_style`` with explicit substitution (never ``str.format``)."""
    validate_branch_style(style)
    out = style.replace("{task_id}", task_id)
    out = out.replace("{project_slug}", project_slug)
    return out


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    """Cut at a line boundary at or below ``limit`` characters."""
    if len(text) <= limit:
        return text, False
    head = text[:limit]
    nl = head.rfind("\n")
    if nl > 0:
        head = head[:nl]
    return head.rstrip(), True


def _resolve_note_path(notes_dir: Path, name: str) -> Path:
    """Resolve an ``embed_notes`` entry, refusing escapes out of ``notes/``."""
    if name.startswith("/") or name.startswith("\\") or Path(name).is_absolute():
        raise DossierError(f"[dossier].embed_notes entry must be relative: {name!r}")
    candidate = notes_dir / name
    try:
        resolved = candidate.resolve()
        base = notes_dir.resolve()
    except OSError as exc:  # pragma: no cover - filesystem edge
        raise DossierError(f"cannot resolve note {name!r}: {exc}") from exc
    if resolved != base and base not in resolved.parents:
        raise DossierError(
            f"[dossier].embed_notes entry escapes the notes directory: {name!r}"
        )
    return candidate


def build_dossier_block(
    *,
    dossier_directory: Path,
    slug: str,
    embed_charter: bool,
    embed_notes: list[str],
    warnings: list[str],
) -> Optional[str]:
    """Render the charter/notes excerpt embedded into the worker brief.

    Selection is opt-in and never globbed: the charter (unless switched off)
    plus exactly the notes the resolved profile listed, in declared order.
    Globbing ``notes/`` would make every brief grow monotonically as the
    project accumulates knowledge — the warm-start benefit has to be paid for
    out of a bounded context budget, so the profile picks what this task class
    actually needs. Over-budget content is cut at a line boundary and always
    marked; nothing is dropped silently.
    """
    sections: list[str] = []
    total = 0

    def add(title: str, display_path: str, text: str) -> None:
        nonlocal total
        remaining = TOTAL_CHAR_LIMIT - total
        if remaining <= 0:
            warnings.append(
                f"dossier: '{display_path}' omitted from the brief (total embed "
                f"budget {TOTAL_CHAR_LIMIT} chars exhausted)"
            )
            return
        limit = min(PER_FILE_CHAR_LIMIT, remaining)
        body, truncated = _truncate(text.strip(), limit)
        if truncated:
            warnings.append(
                f"dossier: '{display_path}' truncated at {limit} chars in the brief"
            )
            body = f"{body}\n\n（以下省略。全文は `{display_path}` を参照）"
        total += len(body)
        sections.append(f"### {title}\n\n{body}\n")

    if embed_charter:
        charter = dossier_directory / CHARTER_FILENAME
        if charter.is_file():
            try:
                add(
                    "憲章（charter.md）",
                    _repo_rel_display(slug, CHARTER_FILENAME),
                    charter.read_text(encoding="utf-8"),
                )
            except OSError as exc:
                warnings.append(f"dossier: cannot read charter.md ({exc})")
        else:
            warnings.append(
                f"dossier: charter embedding requested but "
                f"'{_repo_rel_display(slug, CHARTER_FILENAME)}' does not exist"
            )

    notes_dir = dossier_directory / NOTES_DIRNAME
    for name in embed_notes:
        note = _resolve_note_path(notes_dir, name)
        display = _repo_rel_display(slug, NOTES_DIRNAME, name)
        if not note.is_file():
            warnings.append(
                f"dossier: [dossier].embed_notes lists '{display}' but it does "
                f"not exist"
            )
            continue
        try:
            add(f"ノート: {name}", display, note.read_text(encoding="utf-8"))
        except OSError as exc:
            warnings.append(f"dossier: cannot read '{display}' ({exc})")

    if not sections:
        return None
    return "\n".join(sections).rstrip() + "\n"


def check_contracts_are_references_only(
    dossier_directory: Path, slug: str, warnings: list[str]
) -> None:
    """Warn when ``contracts/`` holds anything but its README index.

    A standing scope contract is a human-approved standalone document. The
    seed source for the first dossier contained a session-scoped human
    merge-preapproval override; copying such a body into a permanent dossier
    is how "an exception for this session" becomes "this project's policy".
    """
    contracts = dossier_directory / CONTRACTS_DIRNAME
    if not contracts.is_dir():
        return
    try:
        entries = sorted(contracts.iterdir(), key=lambda p: p.name)
    except OSError as exc:  # pragma: no cover - filesystem edge
        warnings.append(f"dossier: cannot inspect contracts/ ({exc})")
        return
    offenders = [
        f"{p.name}/" if p.is_dir() else p.name
        for p in entries
        if p.is_dir() or p.name != "README.md"
    ]
    if offenders:
        warnings.append(
            f"dossier: '{_repo_rel_display(slug, CONTRACTS_DIRNAME)}' must hold "
            f"references only (a README index of links to human-approved "
            f"standalone contracts); found: {', '.join(offenders)}"
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def resolve_profile(
    *, claude_org_root: Path, ref: str
) -> ProfileResolution:
    """Resolve ``--profile <slug>[/<class>]`` into a plan-kwargs layer.

    Raises :class:`DossierError` for a missing dossier or an undefined class.
    An undefined class is deliberately NOT a warned fallback to ``base.toml``:
    a silent fallback yields an artifact that looks profiled but is not, and
    it cannot distinguish a deliberate base-only run from a typo. Use
    ``--profile <slug>`` to ask for base only.
    """
    slug, class_name = parse_profile_ref(ref)
    directory = dossier_dir(claude_org_root, slug)
    if not directory.is_dir():
        raise DossierError(
            f"no dossier for project '{slug}' "
            f"(expected {_repo_rel_display(slug)}/)"
        )

    warnings: list[str] = []
    sources: list[Path] = []
    merged: dict[str, dict[str, Any]] = {}

    profiles_dir = directory / PROFILES_DIRNAME
    base_path = profiles_dir / f"{BASE_PROFILE_STEM}.toml"
    if base_path.is_file():
        sources.append(base_path)
        _merge_sections(merged, _classify_and_collect(_load_toml(base_path), base_path, warnings))
    elif class_name is None:
        raise DossierError(
            f"dossier '{slug}' has no "
            f"{_repo_rel_display(slug, PROFILES_DIRNAME, 'base.toml')}; "
            f"pass --profile {slug}/<class> instead. Available classes: "
            f"{', '.join(available_classes(directory)) or '(none)'}"
        )

    if class_name is not None:
        class_path = profiles_dir / f"{class_name}.toml"
        if not class_path.is_file():
            classes = available_classes(directory)
            raise DossierError(
                f"dossier '{slug}' has no profile class '{class_name}'. "
                f"Available classes: {', '.join(classes) or '(none)'}. "
                f"Use --profile {slug} if you meant the base profile only."
            )
        sources.append(class_path)
        _merge_sections(merged, _classify_and_collect(_load_toml(class_path), class_path, warnings))

    _validate_values(merged)
    check_contracts_are_references_only(directory, slug, warnings)

    task = merged.get("task", {})
    project = merged.get("project", {})
    impl = merged.get("implementation", {})
    refs = merged.get("references", {})
    parallel = merged.get("parallel", {})
    dossier_cfg = merged.get("dossier", {})

    plan_kwargs: dict[str, Any] = {"project_slug": slug}
    if "verification_depth" in task:
        plan_kwargs["verification_depth"] = task["verification_depth"]
    if "commit_prefix" in task:
        plan_kwargs["commit_prefix"] = task["commit_prefix"]
    if "description" in project:
        plan_kwargs["project_description_override"] = project["description"]
    if "guidance" in impl:
        plan_kwargs["implementation_guidance"] = impl["guidance"]
    if "target_files" in impl:
        plan_kwargs["implementation_target_files"] = list(impl["target_files"])
    if "knowledge" in refs:
        plan_kwargs["references_knowledge"] = list(refs["knowledge"])
    if "notes" in parallel:
        plan_kwargs["parallel_notes"] = parallel["notes"]

    block = build_dossier_block(
        dossier_directory=directory,
        slug=slug,
        embed_charter=bool(dossier_cfg.get("embed_charter", True)),
        embed_notes=list(dossier_cfg.get("embed_notes", []) or []),
        warnings=warnings,
    )
    if block is not None:
        plan_kwargs["project_dossier"] = block

    # INV-4 at the profile level: an empty (or wholly deferred-axis) profile
    # resolves successfully and produces a brief that LOOKS profiled while
    # applying nothing. That is the same "silently does nothing" failure the
    # per-key classification exists to prevent, one level up. ``project_slug``
    # is the identity seed every resolution sets, so it does not count as a
    # contribution.
    contributed = set(plan_kwargs) - {"project_slug"}
    if not contributed:
        ref_label = f"{slug}/{class_name}" if class_name else slug
        warnings.append(
            f"dossier: profile '{ref_label}' resolved but set nothing "
            f"(no wired axis, no charter/notes embedded); the brief is "
            f"unchanged from an unprofiled dispatch"
        )

    return ProfileResolution(
        slug=slug,
        class_name=class_name,
        dossier_dir=directory,
        plan_kwargs=plan_kwargs,
        branch_style=task.get("branch_style"),
        dossier_block=block,
        warnings=warnings,
        sources=sources,
    )
