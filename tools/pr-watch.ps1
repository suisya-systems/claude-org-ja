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
#
# Issue #224: `--version` passing is necessary but not sufficient — pr_watch.py
# does `from core_harness.audit import Journal`, so a Python whose
# site-packages lacks core_harness will exit 1 with a confusing ImportError
# at runtime. Probe the import too so we fall through to a sibling
# interpreter that *does* have core_harness installed.
function Test-Interpreter {
    param([string]$Exe, [string[]]$Prefix)
    if (-not (Get-Command $Exe -ErrorAction SilentlyContinue)) { return $false }
    try {
        $null = & $Exe @Prefix '--version' 2>&1
        if ($LASTEXITCODE -ne 0) { return $false }
        # Combined probe: require Python 3 AND core_harness.audit importable.
        # Bare `python`/`python3` on some systems still aliases to Python 2,
        # which would pass `--version` but die on pr_watch.py's f-strings.
        $probe = 'import sys; assert sys.version_info[0] == 3; import core_harness.audit'
        $null = & $Exe @Prefix '-c' $probe 2>&1
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
    [Console]::Error.WriteLine('tools/pr-watch.ps1: no working Python interpreter found with core_harness.audit installed (tried py -3, python, python3).')
    exit 127
}

$forwardArgs = $pyArgsPrefix + @($ScriptPath, '--pr', $PR, '--interval', $Interval)
if ($PSBoundParameters.ContainsKey('Repo') -and $Repo) {
    $forwardArgs += @('--repo', $Repo)
}

& $pyExec @forwardArgs
exit $LASTEXITCODE
