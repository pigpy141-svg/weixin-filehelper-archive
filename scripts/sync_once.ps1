$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$env:PYTHONIOENCODING = "utf-8"
$py = ".\.venv\Scripts\python.exe"
New-Item -ItemType Directory -Force local\keys, local\decrypted, local\archive, local\progress | Out-Null
& $py -u exporter\sync_accounts.py --config local\config.json --active-only --json-result local\progress\last_sync.json
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }