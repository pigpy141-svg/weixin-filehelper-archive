$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$env:PYTHONIOENCODING = "utf-8"
$py = ".\.venv\Scripts\python.exe"
$cfg = Get-Content local\config.json -Raw | ConvertFrom-Json
$account = [Environment]::ExpandEnvironmentVariables($cfg.account_dir)
$wxid = [Environment]::ExpandEnvironmentVariables($cfg.wxid)
$key = [Environment]::ExpandEnvironmentVariables($cfg.key_json)
if (-not [IO.Path]::IsPathRooted($key)) { $key = Join-Path (Get-Location).Path $key }
$archive = [Environment]::ExpandEnvironmentVariables($cfg.archive_dir)
if (-not $archive) { $archive = "local\archive" }
if (-not [IO.Path]::IsPathRooted($archive)) { $archive = Join-Path (Get-Location).Path $archive }
& $py -u exporter\save_new_filehelper.py local\decrypted $archive (Join-Path $account "msg") --wxid $wxid --storage (Join-Path $account "db_storage") --key-json $key --fast
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
