param(
    [Parameter(Mandatory = $true)][string]$Name,
    [Parameter(Mandatory = $true)][string]$Tag,
    [string]$Region = "ap",
    [string]$Act = "current",
    [string]$Mode,
    [ValidateRange(1, 500)][int]$Limit = 100,
    [switch]$Csv,
    [switch]$Jsonl,
    [switch]$Digest,
    [switch]$Manifest,
    [switch]$Rounds
)

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$fetchScript = Join-Path $PSScriptRoot "fetch.py"
if (-not (Test-Path -LiteralPath $python)) {
    Write-Error "Project Python was not found at $python. Create the .venv first."
    exit 1
}

$secureKey = Read-Host "HenrikDev API key" -AsSecureString
$keyPointer = [IntPtr]::Zero
$priorKey = $env:HENRIK_API_KEY
$exitCode = 1

try {
    $keyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
    $env:HENRIK_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPointer)

    $fetchArgs = @(
        $fetchScript,
        "--name", $Name,
        "--tag", $Tag,
        "--region", $Region,
        "--act", $Act,
        "--limit", [string]$Limit,
        "--data-dir", (Join-Path $PSScriptRoot "data")
    )
    if (-not [string]::IsNullOrWhiteSpace($Mode)) {
        $fetchArgs += @("--mode", $Mode)
    }
    if ($Csv) {
        $fetchArgs += "--csv"
    }
    if ($Jsonl) {
        $fetchArgs += "--jsonl"
    }
    if ($Digest) {
        $fetchArgs += "--digest"
    }
    if ($Manifest) {
        $fetchArgs += "--manifest"
    }
    if ($Rounds) {
        $fetchArgs += "--rounds"
    }

    & $python @fetchArgs
    $exitCode = $LASTEXITCODE
}
finally {
    if ($keyPointer -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPointer)
    }
    if ($null -ne $secureKey) {
        $secureKey.Dispose()
    }
    if ($null -eq $priorKey) {
        Remove-Item Env:HENRIK_API_KEY -ErrorAction SilentlyContinue
    }
    else {
        $env:HENRIK_API_KEY = $priorKey
    }
}

exit $exitCode
