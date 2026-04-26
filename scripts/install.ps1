# One-liner installer for claude-org-ja (Windows / PowerShell 7+).
# Usage:
#   iwr -useb https://raw.githubusercontent.com/suisya-systems/claude-org-ja/main/scripts/install.ps1 | iex
#   pwsh -NoProfile -File scripts/install.ps1 [-Dir <path>] [-DryRun] [-SkipMcp]
#
# This script:
#   1. Checks for required commands (git, claude, renga, gh) and prints
#      installation hints when something is missing.
#   2. Clones suisya-systems/claude-org-ja (asks before reusing an
#      existing directory).
#   3. Runs `renga mcp install` (user-scope) so the renga-peers MCP
#      server is registered with Claude Code.
#   4. Prints next steps.
#
# It never auto-installs missing tools and never bypasses Claude Code's
# permission prompts.

[CmdletBinding()]
param(
    [string]$Dir = 'claude-org-ja',
    [switch]$DryRun,
    [switch]$SkipMcp,
    [switch]$Help
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ($Help) {
    @'
Usage: install.ps1 [-Dir <path>] [-DryRun] [-SkipMcp] [-Help]

Options:
  -Dir <path>   Target directory for the clone (default: .\claude-org-ja).
  -DryRun       Print commands that would run without executing them.
  -SkipMcp      Skip `renga mcp install` (use when already registered).
  -Help         Show this help and exit.
'@ | Write-Host
    return
}

$RepoUrl = 'https://github.com/suisya-systems/claude-org-ja.git'

function Invoke-Step {
    param([string[]]$Cmd)
    Write-Host "+ $($Cmd -join ' ')"
    if (-not $DryRun) {
        & $Cmd[0] @($Cmd[1..($Cmd.Length - 1)])
        if ($LASTEXITCODE -ne 0) {
            throw "Command failed (exit $LASTEXITCODE): $($Cmd -join ' ')"
        }
    }
}

function Test-Prerequisite {
    param([string]$Name, [string]$Hint)
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -ne $cmd) {
        Write-Host "  [ok]   $Name`: $($cmd.Source)"
        return $true
    }
    Write-Host "  [miss] $Name not found. Install hint: $Hint"
    return $false
}

function Read-YesNo {
    param([string]$Prompt, [string]$Default = 'Y')
    $hint = if ($Default -eq 'Y') { '[Y/n]' } else { '[y/N]' }
    # When piped via `iwr | iex`, $Host.UI.RawUI may still allow ReadLine via
    # the host; fall back to the default if no interactive console is present.
    try {
        $reply = Read-Host "$Prompt $hint"
    } catch {
        Write-Host "install.ps1: non-interactive shell; assuming '$Default' for: $Prompt"
        return ($Default -eq 'Y')
    }
    if ([string]::IsNullOrWhiteSpace($reply)) { $reply = $Default }
    return ($reply -match '^(y|yes)$')
}

Write-Host '== claude-org-ja installer =='
Write-Host ''

Write-Host 'Checking prerequisites...'
$missing = $false
if (-not (Test-Prerequisite 'git'    'https://git-scm.com/downloads'))                          { $missing = $true }
if (-not (Test-Prerequisite 'claude' 'https://claude.ai/code (Claude Code CLI)'))               { $missing = $true }
if (-not (Test-Prerequisite 'renga'  'npm install -g @suisya-systems/renga@0.18.0'))            { $missing = $true }
if (-not (Test-Prerequisite 'gh'     'https://cli.github.com/'))                                { $missing = $true }
Write-Host ''

if ($missing) {
    Write-Error @'
install.ps1: one or more prerequisites are missing.
Install the listed tools, then re-run this installer.
(This script intentionally does not auto-install dependencies.)
'@
    exit 1
}

# --- Clone ---------------------------------------------------------------

if (Test-Path -LiteralPath $Dir) {
    # Confirm it is actually a git workspace by asking git, not by looking
    # for a `.git` path (which can be a stray file in a non-repo directory).
    $isGitRepo = $false
    Push-Location -LiteralPath $Dir
    try {
        $null = & git rev-parse --is-inside-work-tree 2>$null
        if ($LASTEXITCODE -eq 0) { $isGitRepo = $true }
    } finally {
        Pop-Location
    }
    if ($isGitRepo) {
        Write-Host "install.ps1: '$Dir' already exists and looks like a git repo."
        if (Read-YesNo 'Skip clone and reuse existing directory?' 'Y') {
            Write-Host "Reusing existing $Dir (no clone)."
        } else {
            Write-Error "install.ps1: aborting so you can move or rename '$Dir' first."
            exit 1
        }
    } else {
        Write-Error "install.ps1: '$Dir' exists but is not a git repository. Move or rename it and re-run."
        exit 1
    }
} else {
    Invoke-Step @('git', 'clone', $RepoUrl, $Dir)
}

# --- renga mcp install ---------------------------------------------------

if ($SkipMcp) {
    Write-Host 'Skipping `renga mcp install` (-SkipMcp).'
} else {
    Write-Host ''
    Write-Host 'Registering renga-peers MCP with Claude Code (user-scope)...'
    Write-Host 'Note: Claude Code may show a permission prompt; approve it to continue.'
    Invoke-Step @('renga', 'mcp', 'install')
}

# --- Done ----------------------------------------------------------------

@"

Done. Next steps:

  cd $Dir
  bash scripts/install-hooks.sh   # enable pre-commit secret scanner (run from Git Bash / WSL)
  renga --layout ops              # launch the Secretary pane

Inside the Secretary's Claude Code pane, run:

  /org-setup    # first time only: place per-role permissions and hooks
  /org-start    # bring foreman + curator online

For details see docs/getting-started.md.
"@ | Write-Host
