# Markdown Conventions

Conventions for in-repo markdown so paths read consistently regardless of where
a reader opens the file (in an editor, on GitHub, or via a relative-path follow
in Claude Code).

## Link path notation (Issue #322)

For any markdown link whose **display label is a path mention** (the visible
text wrapped in backticks), use this rule:

```text
[`<repo-root path>`](<document-relative path>)
```

- The **display** (inside the backticks) is the path **from the repository
  root**, with forward slashes and no leading `./`. This is what readers see,
  so it should be a stable, portable identifier.
- The **target** (inside the parentheses) is the path **relative to the file
  the link lives in**, so markdown renderers can resolve it directly.

Examples (link from `.claude/skills/org-delegate/SKILL.md`):

```markdown
[`.claude/skills/org-pull-request/SKILL.md`](../org-pull-request/SKILL.md)
[`.dispatcher/references/spawn-flow.md`](../../../.dispatcher/references/spawn-flow.md)
[`docs/journal-events.md`](../../../docs/journal-events.md)
```

Plain-text path mentions in prose (outside link syntax) also use the
repo-root form, e.g. `tools/pr-watch.ps1`, not `../../tools/pr-watch.ps1`.

External URLs (`http://`, `https://`, `mailto:`) and pure in-document
anchors (`#section`) are out of scope.

### In-scope files

The audit script (`tools/audit_link_paths.py`) checks:

- `CLAUDE.md`
- `.curator/CLAUDE.md`
- `.dispatcher/CLAUDE.md`, `.dispatcher/references/**/*.md`
- `.claude/skills/**/SKILL.md`, `.claude/skills/**/references/**/*.md`
- `docs/contracts/**/*.md`, `docs/journal-events.md`

`notes/`, `docs/legacy/` (museum copies), third-party docs, and generated
dashboards are intentionally excluded.

### Verification

Run from the repo root:

```bash
py -3 tools/audit_link_paths.py
```

The script verifies, for each `[`...`](...)` link in the in-scope set, that
the target resolves to an existing file and that the display equals the
repo-root form of that file. Exit code `0` means clean; `1` lists every
violation.
