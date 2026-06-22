#!/usr/bin/env pwsh
# Thin PowerShell wrapper around tools/pr_watch.py.
# Usage: tools/pr-watch.ps1 -PR <PR> [-Repo OWNER/REPO] [-Interval SEC] [-MergeWatch] [-NoMergeWatch]
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [int]$PR,

    [Parameter(Mandatory = $false)]
    [string]$Repo,

    [Parameter(Mandatory = $false)]
    [int]$Interval = 30,

    [Parameter(Mandatory = $false)]
    [switch]$MergeWatch,

    [Parameter(Mandatory = $false)]
    [switch]$NoMergeWatch,

    # Wrapper-only flag (Issue #641): run in the foreground instead of
    # self-re-execing into a detached process. Used for tests / debugging.
    [Parameter(Mandatory = $false)]
    [switch]$NoDetach
)

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ScriptPath = Join-Path $ScriptDir 'pr_watch.py'

# Detach behavior (Issue #641) — mirror of tools/pr-watch.sh.
# ----------------------------------------------------------------------
# By default, self-re-exec into an independent, hidden process and return
# immediately so the watcher survives the parent session closing (the
# Windows analogue of the POSIX SIGHUP trap: a closing console sends
# CTRL_CLOSE to attached children). The child re-runs this script with
# PR_WATCH_DETACHED=1 set, which routes it down the foreground branch. The
# re-exec is idempotent, so an outer background launch becomes a no-op.
#
#   -NoDetach           Run in the foreground (tests / debugging).
#   $env:PR_WATCH_LOG   Override the log path. Default:
#                       <repo-root>\.state\pr-watch-<PR>.log
#   $env:PR_WATCH_DETACHED  Internal re-entry guard set by the re-exec.
if (-not $env:PR_WATCH_DETACHED -and -not $NoDetach) {
    $RepoRoot = Split-Path -Parent $ScriptDir
    if ($env:PR_WATCH_LOG) {
        $LogPath = $env:PR_WATCH_LOG
    }
    else {
        $LogPath = Join-Path (Join-Path $RepoRoot '.state') "pr-watch-$PR.log"
    }
    $LogDir = Split-Path -Parent $LogPath
    if ($LogDir -and -not (Test-Path -LiteralPath $LogDir)) {
        New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    }

    # Rebuild the child argument list, dropping -NoDetach (never re-passed).
    $childArgs = @('-NoProfile', '-File', $PSCommandPath, '-PR', $PR)
    if ($PSBoundParameters.ContainsKey('Repo') -and $Repo) {
        $childArgs += @('-Repo', $Repo)
    }
    if ($PSBoundParameters.ContainsKey('Interval')) {
        $childArgs += @('-Interval', $Interval)
    }
    if ($MergeWatch) { $childArgs += '-MergeWatch' }
    if ($NoMergeWatch) { $childArgs += '-NoMergeWatch' }

    # Resolve the running PowerShell host so the child uses the same engine.
    $pwshExe = (Get-Process -Id $PID).Path
    if (-not $pwshExe) { $pwshExe = 'pwsh' }

    # The child inherits this env var and so takes the foreground branch.
    $env:PR_WATCH_DETACHED = '1'
    # Start-Process spawns an independent process that outlives this one;
    # a hidden window detaches it from the parent console. stdout goes to
    # the log; stderr goes to "<log>.err" because Start-Process refuses to
    # point both streams at the same file.
    $errPath = "$LogPath.err"
    # Build one explicitly double-quoted command line: passing a string[]
    # to -ArgumentList joins the elements with spaces WITHOUT re-quoting
    # them, so a -File path containing spaces (e.g. C:\Users\First Last\..)
    # would be split and the child would fail to launch while the parent
    # still reports "detached".
    $argLine = ($childArgs | ForEach-Object {
        '"' + ([string]$_ -replace '"', '""') + '"'
    }) -join ' '
    $proc = Start-Process -FilePath $pwshExe -ArgumentList $argLine `
        -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $LogPath -RedirectStandardError $errPath
    Write-Output "pr-watch detached: pid=$($proc.Id) log=$LogPath"
    exit 0
}

# Probe interpreters by actually running `--version`, not just by checking
# Get-Command. Some Windows boxes have a stale `py.exe` whose default 3.x
# target points at a moved python.exe — Get-Command says "yes, py is there"
# but invoking it fails with "Unable to create process". Verifying with a
# real exec lets us fall through to plain `python` / `python3` in that case.
#
# Issue #224 / pr-watch-race-fix: `--version` passing is necessary but
# not sufficient — pr_watch.py uses the local `tools.state_db` package
# which depends on the stdlib `sqlite3` module (and on Python 3, where
# the f-strings in pr_watch.py parse). The earlier probe required
# `core_harness.audit`, but that dependency was retired when M4 cut
# pr_watch over to the SQLite events table; leaving the stale import
# in the probe would unnecessarily reject a working interpreter and
# fall through to "no working Python interpreter found". Probe
# `sqlite3` instead — it's the actual external dependency now and is
# part of the stdlib, so a stripped Python build (the only realistic
# failure mode) is correctly rejected.
function Test-Interpreter {
    param([string]$Exe, [string[]]$Prefix)
    if (-not (Get-Command $Exe -ErrorAction SilentlyContinue)) { return $false }
    try {
        $null = & $Exe @Prefix '--version' 2>&1
        if ($LASTEXITCODE -ne 0) { return $false }
        # Combined probe: require Python 3 AND stdlib sqlite3 importable.
        # Bare `python`/`python3` on some systems still aliases to Python 2,
        # which would pass `--version` but die on pr_watch.py's f-strings.
        $probe = 'import sys; assert sys.version_info[0] == 3; import sqlite3'
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
    [Console]::Error.WriteLine('tools/pr-watch.ps1: no working Python 3 interpreter found with stdlib sqlite3 (tried py -3, python, python3).')
    exit 127
}

$forwardArgs = $pyArgsPrefix + @($ScriptPath, '--pr', $PR, '--interval', $Interval)
if ($PSBoundParameters.ContainsKey('Repo') -and $Repo) {
    $forwardArgs += @('--repo', $Repo)
}
if ($MergeWatch) {
    $forwardArgs += @('--merge-watch')
}
if ($NoMergeWatch) {
    $forwardArgs += @('--no-merge-watch')
}

& $pyExec @forwardArgs
exit $LASTEXITCODE
