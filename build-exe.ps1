$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$uv = Join-Path $env:USERPROFILE ".local\bin\uv.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Project Python was not found at $python. Create the .venv first."
}
if (-not (Test-Path -LiteralPath $uv)) {
    $uvCommand = Get-Command uv -ErrorAction SilentlyContinue
    if ($uvCommand) {
        $uv = $uvCommand.Source
    }
    else {
        throw "uv was not found. Install uv, then run this build script again."
    }
}

& $uv pip install --python $python -r (Join-Path $root "requirements-build.txt")
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
