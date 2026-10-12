$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    if (Get-Command py -ErrorAction SilentlyContinue) { & py -3 -m venv .venv }
    else { & python -m venv .venv }
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ is required.' }
    & "$PSScriptRoot\.venv\Scripts\python.exe" -m pip install -e '.[dev]'
    if ($LASTEXITCODE -ne 0) { throw 'Installation failed.' }
} finally { Pop-Location }
