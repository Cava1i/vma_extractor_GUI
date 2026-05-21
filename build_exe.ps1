param(
    [switch]$Clean,
    [switch]$KeepBuild
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPath = Join-Path $ProjectRoot ".venv"
$PythonPath = Join-Path $VenvPath "Scripts\python.exe"
$BuildPath = Join-Path $ProjectRoot "build"
$DistPath = Join-Path $ProjectRoot "dist"

function Remove-ProjectDirectory {
    param([string]$Path)

    $resolvedRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
    $absolutePath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Path)
    if ($absolutePath.StartsWith($resolvedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $absolutePath -Recurse -Force -ErrorAction SilentlyContinue
    }
}

if ($Clean) {
    Remove-ProjectDirectory $BuildPath
    Remove-ProjectDirectory $DistPath
}

if (-not (Test-Path -LiteralPath $PythonPath)) {
    python -m venv $VenvPath
}

& $PythonPath -m pip install --disable-pip-version-check --no-cache-dir -r (Join-Path $ProjectRoot "requirements-build.txt")
if ($LASTEXITCODE -ne 0) {
    throw "pip install failed with exit code $LASTEXITCODE"
}

& $PythonPath -m PyInstaller `
    --clean `
    --noconfirm `
    --onefile `
    --windowed `
    --name VMAExtractor `
    --exclude-module pytest `
    --exclude-module unittest `
    --exclude-module doctest `
    --exclude-module pydoc `
    --exclude-module email `
    --exclude-module html `
    --exclude-module http `
    --exclude-module sqlite3 `
    (Join-Path $ProjectRoot "vma_gui.py")
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

$ExePath = Join-Path $ProjectRoot "dist\VMAExtractor.exe"
if (Test-Path -LiteralPath $ExePath) {
    $size = (Get-Item -LiteralPath $ExePath).Length / 1MB
    "Built $ExePath ({0:N2} MB)" -f $size
}

if (-not $KeepBuild) {
    Remove-ProjectDirectory $BuildPath
}
