$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$env:PYTHONIOENCODING = "utf-8"
$py = ".\.venv\Scripts\python.exe"
New-Item -ItemType Directory -Force local\keys, local\decrypted, local\archive, local\progress | Out-Null
& $py tools\extract_key.py --config local\config.json --active-only
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $py -u exporter\sync_accounts.py --config local\config.json --active-only --init-baseline
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "Baseline created. Only filehelper messages received after this point will be archived."