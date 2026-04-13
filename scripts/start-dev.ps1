param(
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $repoRoot "knowledge_base_backend"
$frontendDir = Join-Path $repoRoot "knowledge_base_frontend"
$venvPython = Join-Path $backendDir ".venv\\Scripts\\python.exe"
$backendEnv = Join-Path $backendDir ".env"
$backendEnvExample = Join-Path $backendDir ".env.example"

function Write-Step($message) {
    Write-Host "[dev-start] $message" -ForegroundColor Cyan
}

function Get-PythonCommand() {
    $commands = @(
        @{ File = "py"; Args = @("-3.12") },
        @{ File = "python"; Args = @() }
    )

    foreach ($command in $commands) {
        try {
            $versionOutput = & $command.File @($command.Args + @("--version")) 2>&1
            if ($LASTEXITCODE -eq 0 -and ($versionOutput -join " ") -match "Python 3\.12") {
                return $command
            }
        } catch {
        }
    }

    throw "Python 3.12 is required. Please install it first."
}

function Get-NpmCommand() {
    $commands = @("npm.cmd", "npm")

    foreach ($command in $commands) {
        try {
            $resolved = (Get-Command $command -ErrorAction Stop).Source
            if ($resolved) {
                return $resolved
            }
        } catch {
        }
    }

    throw "npm was not found in PATH. Please install Node.js 20+ first."
}

function Ensure-BackendEnv() {
    if (-not (Test-Path $backendEnv) -and (Test-Path $backendEnvExample)) {
        Copy-Item $backendEnvExample $backendEnv
        Write-Warning "Created knowledge_base_backend\\.env from .env.example. Please fill in a real OPENAI_API_KEY before using chat features."
    }
}

function Ensure-BackendVenv() {
    if (Test-Path $venvPython) {
        return
    }

    $pythonCommand = Get-PythonCommand
    Write-Step "Creating backend virtual environment with Python 3.12"
    Push-Location $backendDir
    try {
        & $pythonCommand.File @($pythonCommand.Args + @("-m", "venv", ".venv"))
    } finally {
        Pop-Location
    }
}

function Ensure-BackendDeps() {
    if ($SkipInstall) {
        return
    }

    Write-Step "Installing backend dependencies"
    Push-Location $backendDir
    try {
        & $venvPython -m pip install --upgrade pip
        & $venvPython -m pip install -r requirements.txt
    } finally {
        Pop-Location
    }
}

function Ensure-FrontendDeps() {
    if ($SkipInstall -or (Test-Path (Join-Path $frontendDir "node_modules"))) {
        return
    }

    $npmCmd = Get-NpmCommand

    Write-Step "Installing frontend dependencies"
    Push-Location $frontendDir
    try {
        & $npmCmd install
    } finally {
        Pop-Location
    }
}

Ensure-BackendEnv
Ensure-BackendVenv
Ensure-BackendDeps
Ensure-FrontendDeps

$npmCmd = Get-NpmCommand

$backendCommand = "Set-Location '$backendDir'; & '$venvPython' -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload"
$frontendCommand = "Set-Location '$frontendDir'; & '$npmCmd' run dev -- --host 127.0.0.1 --port 5173"

Write-Step "Starting backend in a new PowerShell window"
Start-Process powershell.exe -ArgumentList "-NoExit", "-Command", $backendCommand | Out-Null

Write-Step "Starting frontend in a new PowerShell window"
Start-Process powershell.exe -ArgumentList "-NoExit", "-Command", $frontendCommand | Out-Null

Write-Host ""
Write-Host "Backend:  http://127.0.0.1:8000" -ForegroundColor Green
Write-Host "Frontend: http://127.0.0.1:5173" -ForegroundColor Green
Write-Host "Swagger:  http://127.0.0.1:8000/docs" -ForegroundColor Green
