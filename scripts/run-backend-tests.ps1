# Purpose: run the backend unittest suite on Windows.
# Flow: enter project root -> set PYTHONPATH -> run tests with venv Python.
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

# The backend package lives under backend, so add it to Python module search path.
$env:PYTHONPATH = "backend"
.\.venv\Scripts\python.exe -m unittest discover backend\tests -v
if ($LASTEXITCODE -ne 0) {
    throw "Backend tests failed with exit code $LASTEXITCODE."
}
