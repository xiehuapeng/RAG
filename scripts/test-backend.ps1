param(
    [switch]$Install
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $repoRoot "knowledge_base_backend"
$venvPython = Join-Path $backendDir ".venv\\Scripts\\python.exe"

function Write-Step($message) {
    Write-Host "[backend-test] $message" -ForegroundColor Cyan
}

if (-not (Test-Path $venvPython)) {
    throw "Backend virtual environment not found. Run scripts/start-dev.ps1 first."
}

Push-Location $backendDir
try {
    if ($Install) {
        Write-Step "Installing backend dependencies"
        & $venvPython -m pip install -r requirements.txt
        if ($LASTEXITCODE -ne 0) {
            throw "Backend dependency installation failed (exit $LASTEXITCODE)."
        }
    }

    Write-Step "Running backend test suite"
    & $venvPython -m pytest tests -q
    $testExitCode = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $testExitCode
