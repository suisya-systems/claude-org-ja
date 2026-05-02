#!/usr/bin/env pwsh
# Thin PowerShell wrapper around tools/pr_watch.py.
# Usage: tools/pr-watch.ps1 <PR> [--repo OWNER/REPO] [--interval SEC]
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
& py -3 (Join-Path $ScriptDir 'pr_watch.py') @args
exit $LASTEXITCODE
