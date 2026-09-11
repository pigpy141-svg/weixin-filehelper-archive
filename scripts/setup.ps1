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
git checkout --detach --force $wdaCommit
Pop-Location

$pyTag = & .\.venv\Scripts\python.exe -c "import sys; print(f'cp{sys.version_info.major}{sys.version_info.minor}')"
$wheel = Get-ChildItem "tools\WeChatDataAnalysis\tools\key_wheels\wx_key-*-win_amd64.whl" -ErrorAction SilentlyContinue |
  Where-Object { $_.Name -match $pyTag } |
  Select-Object -First 1
if (-not $wheel) {
  throw "No wx_key wheel for Python $pyTag. Use Windows x64 Python 3.10-3.14."
}
.\.venv\Scripts\python.exe -m pip install $wheel.FullName

if (-not (Test-Path local\config.json)) {
  Copy-Item config.example.json local\config.json
  Write-Host "Created local\config.json. Run scripts\init_baseline.ps1 next."
}