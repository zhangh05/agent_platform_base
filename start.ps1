param(
    [switch]$NoBrowser,
    [switch]$SkipInstall,
    [switch]$ForceBuild,
    [switch]$ValidateOnly
)

$ErrorActionPreference = "Stop"
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$FrontendDir = Join-Path $Root "frontend"
$LogDir = if ($env:LOG_DIR) { $env:LOG_DIR } else { Join-Path $Root "logs" }
$BackendPort = if ($env:BACKEND_PORT) { [int]$env:BACKEND_PORT } else { 8011 }
$FrontendPort = if ($env:FRONTEND_PORT) { [int]$env:FRONTEND_PORT } else { 5273 }
$BackendHost = if ($env:BACKEND_HOST) { $env:BACKEND_HOST } else { "0.0.0.0" }
$FrontendHost = if ($env:FRONTEND_HOST) { $env:FRONTEND_HOST } else { "0.0.0.0" }
$BackendPidFile = Join-Path $Root ".backend.pid"
$FrontendPidFile = Join-Path $Root ".frontend.pid"
$StateDir = Join-Path $Root ".runtime"
$BundledPython = Join-Path $Root "runtime\python\python.exe"
$BundledNode = Join-Path $Root "runtime\node\node.exe"

function Write-Step([string]$Message) {
    Write-Host "[lzcore] $Message" -ForegroundColor Cyan
}

function Fail([string]$Message) {
    throw $Message
}

function Invoke-Native([string]$File, [string[]]$Arguments = @(), [switch]$Quiet) {
    # Windows PowerShell can promote a native program's stderr into a terminating
    # ErrorRecord when ErrorActionPreference is Stop. Capture the complete output
    # and use the real process exit code instead of losing everything after the
    # first "Traceback" line.
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $output = [System.Collections.Generic.List[string]]::new()
    try {
        & $File @Arguments 2>&1 | ForEach-Object {
            $line = [string]$_
            $output.Add($line)
            if (-not $Quiet) { Write-Host $line }
        }
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousPreference
    }
    return @{ ExitCode = $exitCode; Output = @($output) }
}

function Fail-Native([string]$Message, [hashtable]$Result) {
    $details = (@($Result.Output) | Select-Object -Last 30) -join "`n"
    if ($details) { Fail "$Message`n$details" }
    Fail "$Message (exit code $($Result.ExitCode))"
}

function Test-Url([string]$Url) {
    try {
        Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 -Uri $Url | Out-Null
        return $true
    } catch {
        return $false
    }
}

function Wait-Url([string]$Role, [string]$Url, [int]$Attempts = 60) {
    for ($i = 0; $i -lt $Attempts; $i++) {
        if (Test-Url $Url) {
            Write-Host "[$Role] Ready."
            return
        }
        Start-Sleep -Seconds 1
    }
    Fail "$Role failed to start. Check logs in $LogDir"
}

function Get-ListeningPid([int]$Port) {
    $connection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($connection) { return [int]$connection.OwningProcess }
    return 0
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

function Assert-Port([string]$Role, [int]$Port, [string]$HealthUrl, [string]$PidFile) {
    $pidValue = Get-ListeningPid $Port
    if ($pidValue -eq 0) { return $false }
    if ((Test-ProjectProcess $pidValue $Role) -and (Test-Url $HealthUrl)) {
        Set-Content -Path $PidFile -Value $pidValue -Encoding ascii
        Write-Host "[$Role] Already running on port $Port (PID $pidValue)."
        return $true
    }
    Fail "Port $Port is occupied by another process (PID $pidValue)."
}

function Find-BasePython {
    # Prefer the Python launcher. It avoids accidentally selecting the Microsoft
    # Store app execution alias when a real python.org installation is present.
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) {
        foreach ($minor in @("3.12", "3.13")) {
            $probe = Invoke-Native $launcher.Source @("-$minor", "-c", "import sys; raise SystemExit(0 if sys.version_info[:2] == tuple(map(int, '$minor'.split('.'))) and sys.maxsize > 2**32 else 1)") -Quiet
            if ($probe.ExitCode -eq 0) {
                return @{ File = $launcher.Source; Args = @("-$minor") }
            }
        }
    }
    foreach ($commandName in @("python3.13.exe", "python3.12.exe", "python.exe")) {
        $python = Get-Command $commandName -ErrorAction SilentlyContinue
        if ($python) {
            $probe = Invoke-Native $python.Source @("-c", "import sys; raise SystemExit(0 if sys.version_info[:2] in ((3,12),(3,13)) and sys.maxsize > 2**32 else 1)") -Quiet
            if ($probe.ExitCode -eq 0) {
                return @{ File = $python.Source; Args = @() }
            }
        }
    }
    Fail "64-bit CPython 3.12 or 3.13 was not found. Official Windows releases include Python; re-download the release archive. Source checkouts require Python from python.org."
}

function Ensure-Python {
    if (Test-Path $BundledPython) {
        $bundledVersion = Invoke-Native $BundledPython @("-c", "import sys; raise SystemExit(0 if sys.version_info[:2] in ((3,12),(3,13)) and sys.maxsize > 2**32 else 1)") -Quiet
        if ($bundledVersion.ExitCode -ne 0) {
            Fail-Native "The bundled Python runtime is damaged; re-download and fully extract the Windows release" $bundledVersion
        }
        Write-Step "Using bundled Python runtime."
        return $BundledPython
    }
    $venvPython = Join-Path (Join-Path $Root ".venv") "Scripts\python.exe"
    if (Test-Path $venvPython) {
        $existingVersion = Invoke-Native $venvPython @("-c", "import sys; raise SystemExit(0 if sys.version_info[:2] in ((3,12),(3,13)) and sys.maxsize > 2**32 else 1)") -Quiet
        if ($existingVersion.ExitCode -ne 0) {
            Write-Warning "The existing .venv is incomplete or uses an unsupported Python; rebuilding it."
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $Root ".venv")
        }
    }
    if (-not (Test-Path $venvPython)) {
        $base = Find-BasePython
        Write-Step "Creating isolated Python environment (.venv)..."
        $create = Invoke-Native $base.File (@($base.Args) + @("-m", "venv", (Join-Path $Root ".venv")))
        if ($create.ExitCode -ne 0) {
            # Some Windows Python distributions fail while venv invokes
            # ensurepip even though the interpreter itself is valid. Build the
            # environment without pip, then bootstrap pip explicitly below.
            Write-Warning "Standard venv creation failed; retrying with explicit pip bootstrap."
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $Root ".venv")
            $createWithoutPip = Invoke-Native $base.File (@($base.Args) + @("-m", "venv", "--without-pip", (Join-Path $Root ".venv")))
            if ($createWithoutPip.ExitCode -ne 0) {
                Fail-Native "Failed to create .venv" $create
            }
        }
    }
    $version = Invoke-Native $venvPython @("-c", "import sys; raise SystemExit(0 if sys.version_info[:2] in ((3,12),(3,13)) and sys.maxsize > 2**32 else 1)") -Quiet
    if ($version.ExitCode -ne 0) { Fail-Native "Project .venv does not use CPython 3.12 or 3.13" $version }
    $pip = Invoke-Native $venvPython @("-m", "pip", "--version") -Quiet
    if ($pip.ExitCode -ne 0) {
        $bootstrap = Invoke-Native $venvPython @("-m", "ensurepip", "--upgrade")
        if ($bootstrap.ExitCode -ne 0) { Fail-Native "Failed to install pip in .venv" $bootstrap }
    }
    return $venvPython
}

function Ensure-PythonDependencies([string]$Python) {
    if ($SkipInstall -or $env:INSTALL_DEPS -in @("0", "false")) { return }
    $requirements = Join-Path $Root "requirements.txt"
    $stamp = Join-Path $StateDir "requirements.sha256"
    $hash = (Get-FileHash -Algorithm SHA256 $requirements).Hash
    $installedHash = if (Test-Path $stamp) { (Get-Content $stamp -Raw).Trim() } else { "" }
    $probe = Invoke-Native $Python @("-c", "import flask, flask_sock, yaml, bs4, lxml, pdfplumber, scapy, paramiko") -Quiet
    if ([IO.Path]::GetFullPath($Python) -eq [IO.Path]::GetFullPath($BundledPython)) {
        if ($probe.ExitCode -ne 0) {
            Fail-Native "The bundled Python dependencies are damaged; re-download and fully extract the Windows release" $probe
        }
        $check = Invoke-Native $Python @("-m", "pip", "check")
        if ($check.ExitCode -ne 0) { Fail-Native "Bundled Python dependency check failed" $check }
        Set-Content -Path $stamp -Value $hash -Encoding ascii
        return
    }
    if ($probe.ExitCode -ne 0 -or $hash -ne $installedHash) {
        Write-Step "Installing Python dependencies..."
        $wheelhouse = Join-Path $Root "wheelhouse"
        $pipArguments = @("-m", "pip", "install", "--disable-pip-version-check")
        if (Test-Path $wheelhouse) {
            Write-Step "Using bundled Windows dependency cache."
            $offlineArguments = $pipArguments + @("--no-index", "--find-links", $wheelhouse, "-r", $requirements)
            $install = Invoke-Native $Python $offlineArguments
            if ($install.ExitCode -ne 0) {
                Write-Warning "Bundled dependency cache is incomplete for this Python runtime; retrying from the configured Python package index."
                $install = Invoke-Native $Python ($pipArguments + @("-r", $requirements))
            }
        } else {
            $install = Invoke-Native $Python ($pipArguments + @("-r", $requirements))
        }
        if ($install.ExitCode -ne 0) { Fail-Native "Python dependency installation failed" $install }
        Set-Content -Path $stamp -Value $hash -Encoding ascii
    }
    $check = Invoke-Native $Python @("-m", "pip", "check")
    if ($check.ExitCode -ne 0) { Fail-Native "Python dependency check failed" $check }
}

function Ensure-Frontend {
    $usingBundledNode = Test-Path $BundledNode
    $node = if ($usingBundledNode) {
        @{ Source = $BundledNode }
    } else {
        Get-Command node.exe -ErrorAction SilentlyContinue
    }
    # A release archive is immutable and ships its Vite dependencies, so it
    # must not accidentally depend on npm from the host machine.
    $npm = if ($usingBundledNode) { $null } else { Get-Command npm.cmd -ErrorAction SilentlyContinue }
    if (-not $node) {
        Fail "Node.js 24 LTS was not found. Official Windows releases include Node.js; re-download the release archive."
    }
    $nodeCheck = Invoke-Native $node.Source @("-e", "const major=Number(process.versions.node.split('.')[0]); process.exit(major===24?0:1)") -Quiet
    if ($nodeCheck.ExitCode -ne 0) { Fail-Native "Node.js 24 LTS is required" $nodeCheck }

    $lockFile = Join-Path $FrontendDir "package-lock.json"
    $lockHash = (Get-FileHash -Algorithm SHA256 $lockFile).Hash
    $npmStamp = Join-Path $StateDir "frontend-lock.sha256"
    $installedHash = if (Test-Path $npmStamp) { (Get-Content $npmStamp -Raw).Trim() } else { "" }
    $viteScript = Join-Path $FrontendDir "node_modules\vite\bin\vite.js"
    if (-not $SkipInstall -and $env:INSTALL_DEPS -notin @("0", "false")) {
        $needsInstall = -not (Test-Path $viteScript) -or ($installedHash -and $lockHash -ne $installedHash)
        if ($needsInstall) {
            if (-not $npm) {
                Fail "Bundled frontend dependencies are missing or stale; re-download and fully extract the Windows release."
            }
            Write-Step "Installing frontend dependencies with npm ci..."
            Push-Location $FrontendDir
            try { $npmInstall = Invoke-Native $npm.Source @("ci") } finally { Pop-Location }
            if ($npmInstall.ExitCode -ne 0) { Fail-Native "Frontend dependency installation failed" $npmInstall }
            Set-Content -Path $npmStamp -Value $lockHash -Encoding ascii
        } elseif (-not $installedHash) {
            # A release archive contains Windows node_modules but deliberately
            # excludes runtime stamps. Adopt the bundled lock state without an
            # unnecessary online npm install.
            Set-Content -Path $npmStamp -Value $lockHash -Encoding ascii
        }
    }
    if (-not (Test-Path $viteScript)) { Fail "Vite is not installed; re-download the Windows release or rerun the source checkout without -SkipInstall" }

    $distIndex = Join-Path $FrontendDir "dist\index.html"
    if ($ForceBuild -or -not (Test-Path $distIndex)) {
        if (-not $npm) {
            Fail "The prebuilt frontend is missing and npm is unavailable; re-download and fully extract the Windows release."
        }
        Write-Step "Building the frontend..."
        Push-Location $FrontendDir
        try { $frontendBuild = Invoke-Native $npm.Source @("run", "build") } finally { Pop-Location }
        if ($frontendBuild.ExitCode -ne 0) { Fail-Native "Frontend build failed" $frontendBuild }
    }
    return @{ Node = $node.Source; Vite = $viteScript }
}

function Get-AllowedOrigins {
    $origins = [System.Collections.Generic.List[string]]::new()
    $origins.Add("http://localhost:$FrontendPort")
    $origins.Add("http://127.0.0.1:$FrontendPort")
    $addresses = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" }
    foreach ($address in $addresses) {
        $origins.Add("http://$($address.IPAddress):$FrontendPort")
    }
    return ($origins | Select-Object -Unique) -join ","
}

New-Item -ItemType Directory -Force -Path $LogDir, $StateDir | Out-Null
Set-Location $Root
$startedProcessIds = [System.Collections.Generic.List[int]]::new()

try {
    $Python = Ensure-Python
    Ensure-PythonDependencies $Python
    $Frontend = Ensure-Frontend
    if ($ValidateOnly) {
        Write-Host "Windows runtime validation completed successfully." -ForegroundColor Green
        exit 0
    }

    $backendHealth = "http://127.0.0.1:$BackendPort/api/health"
    $frontendHealth = "http://127.0.0.1:$FrontendPort"
    $backendRunning = Assert-Port "backend" $BackendPort $backendHealth $BackendPidFile
    if (-not $backendRunning) {
        Write-Step "Starting backend on ${BackendHost}:$BackendPort..."
        $env:LZCORE_ALLOWED_ORIGINS = Get-AllowedOrigins
        $backend = Start-Process -FilePath $Python `
            -ArgumentList @("backend\main.py", "--host", $BackendHost, "--port", "$BackendPort") `
            -WorkingDirectory $Root -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $LogDir "backend-$BackendPort.log") `
            -RedirectStandardError (Join-Path $LogDir "backend-$BackendPort.err.log")
        $startedProcessIds.Add($backend.Id)
        Set-Content -Path $BackendPidFile -Value $backend.Id -Encoding ascii
        Wait-Url "backend" $backendHealth
    }

    $frontendRunning = Assert-Port "frontend" $FrontendPort $frontendHealth $FrontendPidFile
    if (-not $frontendRunning) {
        Write-Step "Starting frontend on ${FrontendHost}:$FrontendPort..."
        $env:VITE_DEV_API_TARGET = "http://127.0.0.1:$BackendPort"
        # Start-Process joins ArgumentList into one command line on Windows.
        # Quote the script path explicitly so release folders containing spaces work.
        $quotedViteScript = '"' + $Frontend.Vite.Replace('"', '\"') + '"'
        $frontendProcess = Start-Process -FilePath $Frontend.Node `
            -ArgumentList @($quotedViteScript, "preview", "--host", $FrontendHost, "--port", "$FrontendPort", "--strictPort") `
            -WorkingDirectory $FrontendDir -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $LogDir "frontend-$FrontendPort.log") `
            -RedirectStandardError (Join-Path $LogDir "frontend-$FrontendPort.err.log")
        $startedProcessIds.Add($frontendProcess.Id)
        Set-Content -Path $FrontendPidFile -Value $frontendProcess.Id -Encoding ascii
        Wait-Url "frontend" $frontendHealth
    }

    Write-Host ""
    Write-Host "LZCore is ready:" -ForegroundColor Green
    Write-Host "  UI:      $frontendHealth"
    Write-Host "  API:     $backendHealth"
    Write-Host "  Logs:    $LogDir"
    Write-Host "  Stop:    stop.bat"
    if (-not $NoBrowser) { Start-Process $frontendHealth }
} catch {
    foreach ($startedId in $startedProcessIds) {
        & taskkill.exe /PID $startedId /T /F 2>$null | Out-Null
    }
    $errorText = "[$(Get-Date -Format o)] $($_.Exception.Message)`n$($_.ScriptStackTrace)"
    Add-Content -Path (Join-Path $LogDir "startup-error.log") -Value $errorText -Encoding UTF8
    Write-Host ""
    Write-Host "[ERROR] $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "See logs in: $LogDir"
    exit 1
}
