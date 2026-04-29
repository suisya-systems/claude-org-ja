# Security Policy

## Reporting a Vulnerability

We take the security of claude-org-ja seriously. If you discover a security issue, please report it privately so we can address it before public disclosure.

### Preferred channel: GitHub Private Vulnerability Reporting

Use [GitHub's private vulnerability reporting](https://github.com/suisya-systems/claude-org-ja/security/advisories/new) feature. This is the fastest channel and ensures the report is encrypted and only visible to maintainers.

### Alternative

If you cannot use GitHub PVR, open a regular Issue **without** including reproduction details and we will reach out via a private channel.

## Disclosure Policy

- We aim to acknowledge reports within 72 hours.
- We aim to provide an initial assessment and remediation plan within 14 days.
- We will coordinate disclosure timing with the reporter; default is public disclosure once a fix is available.

## Supported Versions

Only the latest tagged release on `main` is actively supported. Earlier development history was squashed at the v0.1.0 public release; pre-v0.1.0 references are not in scope.

## Scope

Security reports for the following are in scope:
- Permission / hook bypass in worker / dispatcher / curator settings
- Path traversal or arbitrary write through Skill / hook execution
- Secret leakage in default skills, workflows, or scripts
- Privilege escalation via MCP tool misuse

Out of scope: third-party tools (Claude Code CLI, renga, gh CLI itself); please report to those projects directly.
