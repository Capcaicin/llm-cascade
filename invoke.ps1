# AI Router — AI STACK Invoke Script
# Usage: .\invoke.ps1 [-Project <name>] [-NoRag] [-Dashboard] [-Check] [-Serve]

param(
    [string]$Project  = "",
    [switch]$NoRag,
    [switch]$Dashboard,
    [switch]$Check,
    [switch]$Serve
)

$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$SrcDir      = Join-Path $ScriptDir "src"
$BinDir      = Join-Path $ScriptDir "bin"
$RouterExe   = Join-Path $BinDir "AI Router.exe"
$CliExe      = Join-Path $BinDir "AI Router CLI.exe"
$CliScript   = Join-Path $SrcDir "ai_router_v2.py"
$ServerScript= Join-Path $SrcDir "router_server.py"
$DashScript  = Join-Path $SrcDir "dashboard.py"

# Ensure project root is on PYTHONPATH for Python child processes
if (-not [string]::IsNullOrEmpty($Env:PYTHONPATH)) { $Env:PYTHONPATH = $ScriptDir + ';' + $Env:PYTHONPATH } else { $Env:PYTHONPATH = $ScriptDir }

# Load .env into environment (simple KEY=VALUE parser)
$EnvFile = Join-Path $ScriptDir ".env"
if (Test-Path $EnvFile) {
    Get-Content $EnvFile | ForEach-Object {
        if ($_ -match '^[\s#]') { return }
        if ($_ -match '^[\s]*$') { return }
        $parts = $_ -split '=', 2
        if ($parts.Count -ne 2) { return }
        $k = $parts[0].Trim()
        $v = $parts[1].Trim(' "''')
        if ($k) { Set-Item -Path Env:$k -Value $v }
    }
}

if ($Serve) {
    Write-Host "`n[*] Starting two-tier router API on http://localhost:3839" -ForegroundColor Cyan
    if (Test-Path $RouterExe) { & $RouterExe }
    else                      { python $ServerScript }
    exit
}

if ($Check) {
    Write-Host "`n[*] Checking services..." -ForegroundColor Cyan
    python $CliScript --check
    exit
}

if ($Dashboard) {
    Write-Host "`n[*] Starting Streamlit dashboard at http://localhost:8501" -ForegroundColor Cyan
    python -m streamlit run $DashScript --server.headless=false --server.port=8501 --browser.gatherUsageStats=false
    exit
}

$Args = @()
if ($Project) { $Args += "--project", $Project }
if ($NoRag)   { $Args += "--no-rag" }

Write-Host "`n[*] Starting AI Router CLI..." -ForegroundColor Cyan
if ($Project) { Write-Host "    Project: $Project" -ForegroundColor Gray }
Write-Host ""

if ((Test-Path $CliExe) -and -not $Project -and -not $NoRag) { & $CliExe }
else                                                         { python $CliScript @Args }
