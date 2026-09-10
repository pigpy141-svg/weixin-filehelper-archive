$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path .venv\Scripts\python.exe)) {
  py -3 -m venv .venv
}
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (-not (Test-Path tools\WeChatDataAnalysis)) {
  git clone https://github.com/LifeArchiveProject/WeChatDataAnalysis.git tools\WeChatDataAnalysis
}
if (-not (Test-Path local\config.json)) {
  Copy-Item config.example.json local\config.json
  Write-Host "Created local\config.json. Edit account_dir, wxid and dll_path, then run scripts\init_baseline.ps1"
}
