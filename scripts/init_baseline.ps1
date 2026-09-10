$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$env:PYTHONIOENCODING = "utf-8"
$py = ".\.venv\Scripts\python.exe"
& $py tools\extract_key.py --config local\config.json
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$cfg = Get-Content local\config.json -Raw | ConvertFrom-Json
$account = [Environment]::ExpandEnvironmentVariables($cfg.account_dir)
$wxid = [Environment]::ExpandEnvironmentVariables($cfg.wxid)
$key = [Environment]::ExpandEnvironmentVariables($cfg.key_json)
if (-not [IO.Path]::IsPathRooted($key)) { $key = Join-Path (Get-Location).Path $key }
$archive = [Environment]::ExpandEnvironmentVariables($cfg.archive_dir)
if (-not $archive) { $archive = "local\archive" }
if (-not [IO.Path]::IsPathRooted($archive)) { $archive = Join-Path (Get-Location).Path $archive }
New-Item -ItemType Directory -Force local\decrypted, $archive | Out-Null
& $py -u exporter\save_new_filehelper.py local\decrypted $archive (Join-Path $account "msg") --wxid $wxid --storage (Join-Path $account "db_storage") --key-json $key --init-baseline
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "Baseline created. Only later filehelper messages will be archived."
