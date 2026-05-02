# renga Pane Conventions

## Decoration is cosmetic; `client_kind` is authoritative

In renga, pane decoration — border color, pane label, and tab title markers —
is **cosmetic only**. It is derived from a best-effort live signal (historically
an OSC 0/2 window-title substring match for `"claude"` / `"codex"`) and can flip
off at any moment, for example when Claude Code rewrites its window title to the
current task name via `\x1b]0;✶ <task name>\x07`. A missing border or a tab
label that has reverted to `shell` does **not** mean the Claude process has died
or hung.

The authoritative source of truth for "is this pane a Claude / Codex agent?" is
the peer registry exposed by `mcp__renga-peers__list_peers`. When an agent
registers over the MCP peer channel its entry carries a `client_kind` field
(`claude`, `codex`, …) that is **not** affected by terminal title rewrites.
Orchestration code, dashboards, and human operators should treat a present
`client_kind` as ground truth and treat decoration purely as a UI hint.

Note that `client_kind` is set "when known" — it can be absent or missing for
panes that have not yet completed peer registration (e.g. a freshly spawned
Claude pane during the brief window before its MCP channel is up). An absent
`client_kind` means "unknown", not "not an agent"; resolve it by waiting for
the peer to appear in `list_peers` rather than by falling back to decoration.

> Rule of thumb: if a pane is registered with `client_kind=claude` but has no
> decoration, trust the kind and not the decoration.

### Background

This convention was hardened in response to renga issues
[#208](https://github.com/suisya-systems/renga/issues/208) and
[#209](https://github.com/suisya-systems/renga/issues/209), where the
title-substring detection used for decoration was found to flip false during
normal Claude Code operation (and to misclassify Claude panes whose task name
contained the string `"codex"`). Both were fixed upstream by
[renga PR #210](https://github.com/suisya-systems/renga/pull/210) on
2026-05-02, but the underlying lesson remains: decoration is a derived,
best-effort signal, while `client_kind` from the peer registry is the contract.
New tooling and runbooks in this repo should follow that split.
