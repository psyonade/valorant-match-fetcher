param(
    [string]$PythonPath
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$python = if ([string]::IsNullOrWhiteSpace($PythonPath)) {
    Join-Path $root ".venv\Scripts\python.exe"
}
elseif ([System.IO.Path]::IsPathRooted($PythonPath)) {
    $PythonPath
}
else {
    Join-Path $root $PythonPath
}

if (-not (Test-Path -LiteralPath $python)) {
    throw "Project Python was not found at $python. Create the .venv first."
}

$buildRequirements = Join-Path $root "requirements-build.txt"
$pipLauncher = Join-Path (Split-Path -Parent $python) "pip.exe"
if (Test-Path -LiteralPath $pipLauncher) {
    & $python -m pip install -r $buildRequirements
}
else {
    $uvCommand = Get-Command uv -ErrorAction SilentlyContinue
    if (-not $uvCommand) {
        throw "pip is unavailable in $python and uv was not found. Recreate .venv with Python's venv module or install uv."
    }
    $env:UV_CACHE_DIR = Join-Path $root ".uv-cache"
    & $uvCommand.Source pip install --python $python -r $buildRequirements
}
if ($LASTEXITCODE -ne 0) {
    throw "Could not install PyInstaller into the project environment."
}

$buildDir = Join-Path $root "build"
$distDir = Join-Path $root "dist"
& $python -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name ValorantMatchFetcher `
    --specpath $buildDir `
    --workpath (Join-Path $buildDir "pyinstaller") `
    --distpath $distDir `
    (Join-Path $root "gui.py")
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed to build the executable."
}

Write-Host "Built: $(Join-Path $distDir 'ValorantMatchFetcher.exe')"
