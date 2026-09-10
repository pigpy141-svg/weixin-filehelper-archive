$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path .venv\Scripts\python.exe)) {
  py -3 -m venv .venv
}
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$wdaCommit = "36d1e548172e9fb87f26d729403a058d13d92bca"
if (-not (Test-Path tools\WeChatDataAnalysis\.git)) {
  git clone https://github.com/LifeArchiveProject/WeChatDataAnalysis.git tools\WeChatDataAnalysis
}
Push-Location tools\WeChatDataAnalysis
git fetch --depth 1 origin $wdaCommit
git checkout --detach $wdaCommit
Pop-Location
if (-not (Test-Path local\config.json)) {
  Copy-Item config.example.json local\config.json
  Write-Host "Created local\config.json. Edit account_dir, wxid and dll_path, then run scripts\init_baseline.ps1"
}
