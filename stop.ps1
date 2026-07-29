param()

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$BackendPort = if ($env:BACKEND_PORT) { [int]$env:BACKEND_PORT } else { 8011 }
$FrontendPort = if ($env:FRONTEND_PORT) { [int]$env:FRONTEND_PORT } else { 5273 }
$BackendPidFile = Join-Path $Root ".backend.pid"
$FrontendPidFile = Join-Path $Root ".frontend.pid"

function Get-ListeningPids([int]$Port) {
    return @(
        Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique
    )
}

function Test-ProjectProcess([int]$ProcessId, [string]$Role) {
    if ($ProcessId -le 0) { return $false }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
    if (-not $process) { return $false }
    $commandLine = [string]$process.CommandLine
    $rootPattern = [regex]::Escape($Root)
    if ($Role -eq "backend") {
        return $commandLine -match $rootPattern -and $commandLine -match "backend[\\/]main\.py"
    }
    return $commandLine -match $rootPattern -and $commandLine -match "vite"
}

function Stop-VerifiedProcess([string]$Role, [int]$ProcessId) {
    if (-not (Test-ProjectProcess $ProcessId $Role)) {
        Write-Warning "[$Role] Refusing to stop unverified PID $ProcessId."
        return $false
    }
    & taskkill.exe /PID $ProcessId /T /F 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0 -and (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)) {
        Write-Warning "[$Role] Failed to stop PID $ProcessId."
        return $false
    }
    Write-Host "[$Role] Stopped PID $ProcessId."
    return $true
}

function Stop-Service([string]$Role, [int]$Port, [string]$PidFile) {
    $candidateIds = [System.Collections.Generic.HashSet[int]]::new()
    if (Test-Path $PidFile) {
        $stored = 0
        [void][int]::TryParse((Get-Content $PidFile -Raw).Trim(), [ref]$stored)
        if ($stored -gt 0) { [void]$candidateIds.Add($stored) }
    }
    foreach ($listenerId in (Get-ListeningPids $Port)) {
        if ($listenerId) { [void]$candidateIds.Add([int]$listenerId) }
    }

    $stopped = $false
    foreach ($candidateId in $candidateIds) {
        if (Stop-VerifiedProcess $Role $candidateId) { $stopped = $true }
    }
    Remove-Item -Force -ErrorAction SilentlyContinue $PidFile
    if (-not $stopped) { Write-Host "[$Role] Not running." }
}

try {
    Stop-Service "frontend" $FrontendPort $FrontendPidFile
    Stop-Service "backend" $BackendPort $BackendPidFile
} catch {
    Write-Host "[ERROR] $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
