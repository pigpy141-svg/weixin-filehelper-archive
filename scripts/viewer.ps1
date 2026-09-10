$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$env:PYTHONIOENCODING = "utf-8"
$archive = "local\archive"
$port = 8765
if (Test-Path local\config.json) {
  $cfg = Get-Content local\config.json -Raw | ConvertFrom-Json
  if ($cfg.port) { $port = [int]$cfg.port }
  if ($cfg.archive_dir) {
    $archive = [Environment]::ExpandEnvironmentVariables($cfg.archive_dir)
    if (-not [IO.Path]::IsPathRooted($archive)) { $archive = Join-Path (Get-Location).Path $archive }
  }
}
.\.venv\Scripts\python.exe viewer\server.py $archive $port
