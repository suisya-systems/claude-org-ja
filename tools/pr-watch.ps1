#!/usr/bin/env pwsh
# Thin PowerShell wrapper around tools/pr_watch.py.
# Usage: tools/pr-watch.ps1 -PR <PR> [-Repo OWNER/REPO] [-Interval SEC]
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [int]$PR,

    [Parameter(Mandatory = $false)]
    [string]$Repo,

    [Parameter(Mandatory = $false)]
    [int]$Interval = 30
)

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ScriptPath = Join-Path $ScriptDir 'pr_watch.py'

# Probe the available Python interpreter. Mirrors scripts/install.ps1: try
# `py -3` (Python Launcher) first, then `python` and `python3`. The launcher
# is preferred because PEP 397 makes its argv handling well-defined on
# Windows, but it isn't always installed and on some systems its default
# Python target is broken — fall back rather than fail outright.
$pyExec = $null
$pyArgsPrefix = @()
if (Get-Command 'py' -ErrorAction SilentlyContinue) {
    $pyExec = 'py'
    $pyArgsPrefix = @('-3')
} elseif (Get-Command 'python' -ErrorAction SilentlyContinue) {
    $pyExec = 'python'
} elseif (Get-Command 'python3' -ErrorAction SilentlyContinue) {
    $pyExec = 'python3'
} else {
    Write-Error 'tools/pr-watch.ps1: no Python interpreter found (tried py, python, python3).'
    exit 127
}

$forwardArgs = $pyArgsPrefix + @($ScriptPath, '--pr', $PR, '--interval', $Interval)
if ($PSBoundParameters.ContainsKey('Repo') -and $Repo) {
    $forwardArgs += @('--repo', $Repo)
}

& $pyExec @forwardArgs
exit $LASTEXITCODE
