$ErrorActionPreference = "Stop"

$projectRoot = $PSScriptRoot
$workspaceRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot "..\..\..\.."))
$buildEnvironment = Join-Path $workspaceRoot "DorkLinkExtractor-build-env"
$python = Join-Path $buildEnvironment "Scripts\python.exe"
$desktop = [Environment]::GetFolderPath("Desktop")
$output = Join-Path $projectRoot "dist\DorkLinkExtractor.exe"
$destination = Join-Path $desktop "DorkLinkExtractor.exe"

if (-not (Test-Path -LiteralPath $python)) {
    & py -3 -m venv $buildEnvironment
    if ($LASTEXITCODE -ne 0) {
        throw "Could not create the packaging environment. Install Python 3.12 or newer with the Windows py launcher."
    }
}

& $python -c "import PySide6, httpx, bs4, lxml, playwright, PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    & $python -m pip install -r (Join-Path $projectRoot "requirements.txt") pyinstaller
    if ($LASTEXITCODE -ne 0) {
        throw "Could not install the application build dependencies."
    }
}

Push-Location $projectRoot
try {
    & $python -m PyInstaller --noconfirm --clean "DorkLinkExtractor.spec"
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE."
    }
} finally {
    Pop-Location
}

if (-not (Test-Path -LiteralPath $output)) {
    throw "Build completed without producing $output."
}

Copy-Item -LiteralPath $output -Destination $destination -Force
Write-Host "Updated desktop app: $destination"
