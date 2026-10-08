$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

# Resolve paths from this script, including when the project path contains spaces.
$projectPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $projectPython)) {
    throw "Create .venv and install requirements.txt and requirements-dev.txt first (see README)."
}

$savedBuildPath = $env:PATH
Push-Location -LiteralPath $PSScriptRoot
try {
    # Avoid collecting incompatible Qt/ICU DLLs from unrelated tools on PATH.
    # Qt must resolve Windows system DLLs instead (not, for example, Poppler ICU).
    $env:PATH = (Split-Path -Parent $projectPython) + ";" + (Join-Path $env:SystemRoot "System32") + ";" + $env:SystemRoot
    # Standard PyInstaller hooks collect PySide6, Qt plugins and pyqtgraph.
    # No extra hidden imports or external fonts/data files are needed.
    # Keep the generated spec alongside the other temporary build files.
    & $projectPython -m PyInstaller --noconfirm --clean --onefile --windowed --name SerialMonitor --specpath build main.py
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE."
    }
    $outputExe = Join-Path $PSScriptRoot "dist\SerialMonitor.exe"
    if (-not (Test-Path -LiteralPath $outputExe)) {
        throw "Build completed without producing $outputExe."
    }
    Write-Host "Built: $outputExe"
}
finally {
    $env:PATH = $savedBuildPath
    Pop-Location
}
