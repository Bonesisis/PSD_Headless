$ErrorActionPreference = 'Stop'
$testRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('pshl-' + [guid]::NewGuid().ToString('N'))
$shareName = 'pshl-' + [guid]::NewGuid().ToString('N')
New-Item -ItemType Directory -Path $testRoot | Out-Null
try {
    New-SmbShare -Name $shareName -Path $testRoot -FullAccess ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) | Out-Null
    $env:PSHL_TEST_UNC = "\\localhost\$shareName"
    & "$PSScriptRoot\..\.venv\Scripts\python.exe" -m pytest -q tests/test_unc.py
    if ($LASTEXITCODE -ne 0) { throw 'UNC integration failed' }
} finally {
    Remove-Item Env:PSHL_TEST_UNC -ErrorAction SilentlyContinue
    Remove-SmbShare -Name $shareName -Force -ErrorAction SilentlyContinue
    # Only delete the exact random test directory created by this script.
    Remove-Item -LiteralPath $testRoot -Recurse -Force
}
