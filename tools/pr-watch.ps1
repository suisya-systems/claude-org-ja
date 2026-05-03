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

# Probe interpreters by actually running `--version`, not just by checking
# Get-Command. Some Windows boxes have a stale `py.exe` whose default 3.x
# target points at a moved python.exe — Get-Command says "yes, py is there"
# but invoking it fails with "Unable to create process". Verifying with a
# real exec lets us fall through to plain `python` / `python3` in that case.
function Test-Interpreter {
    param([string]$Exe, [string[]]$Prefix)
    if (-not (Get-Command $Exe -ErrorAction SilentlyContinue)) { return $false }
    try {
        $null = & $Exe @Prefix '--version' 2>&1
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

$candidates = @(
    @{ Exe = 'py';      Prefix = @('-3') },
    @{ Exe = 'python';  Prefix = @() },
    @{ Exe = 'python3'; Prefix = @() }
)

$pyExec = $null
$pyArgsPrefix = @()
foreach ($cand in $candidates) {
    if (Test-Interpreter -Exe $cand.Exe -Prefix $cand.Prefix) {
        $pyExec = $cand.Exe
        $pyArgsPrefix = $cand.Prefix
        break
    }
}

if (-not $pyExec) {
    [Console]::Error.WriteLine('tools/pr-watch.ps1: no working Python interpreter found (tried py -3, python, python3).')
    exit 127
}

$forwardArgs = $pyArgsPrefix + @($ScriptPath, '--pr', $PR, '--interval', $Interval)
if ($PSBoundParameters.ContainsKey('Repo') -and $Repo) {
    $forwardArgs += @('--repo', $Repo)
}

& $pyExec @forwardArgs
exit $LASTEXITCODE
