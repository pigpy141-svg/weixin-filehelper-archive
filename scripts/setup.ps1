$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not (Test-Path .venv\Scripts\python.exe)) {
  py -3 -m venv .venv
}
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

$wdaCommit = "36d1e548172e9fb87f26d729403a058d13d92bca"
$wdaRepo = "https://github.com/LifeArchiveProject/WeChatDataAnalysis.git"
$wdaDir = "tools\WeChatDataAnalysis"

if (-not (Test-Path "$wdaDir\.git")) {
  for ($attempt = 1; $attempt -le 3; $attempt++) {
    if (Test-Path $wdaDir) { Remove-Item -Recurse -Force $wdaDir }
    git clone $wdaRepo $wdaDir
    if ($LASTEXITCODE -eq 0) { break }
    Write-Host "git clone failed (attempt $attempt of 3)."
    if ($attempt -eq 3) {
      throw ("Could not download " + $wdaRepo + ". If GitHub is unreachable on your network, " +
        "set a proxy and re-run this script, for example: " + '$env:HTTPS_PROXY="http://127.0.0.1:7897"')
    }
    Start-Sleep -Seconds 5
  }
}

Push-Location $wdaDir

# A full clone normally already contains the pinned commit, so only hit the
# network when it is genuinely missing.
git rev-parse --verify --quiet "$wdaCommit^{commit}" | Out-Null
if ($LASTEXITCODE -ne 0) {
  for ($attempt = 1; $attempt -le 3; $attempt++) {
    git fetch --depth 1 origin $wdaCommit
    if ($LASTEXITCODE -eq 0) { break }
    Write-Host "git fetch failed (attempt $attempt of 3)."
    if ($attempt -eq 3) {
      Pop-Location
      throw ("Could not fetch WeChatDataAnalysis@" + $wdaCommit + ". Check your network or proxy, then re-run scripts\setup.ps1.")
    }
    Start-Sleep -Seconds 5
  }
}

git checkout --detach --force $wdaCommit
if ($LASTEXITCODE -ne 0) {
  Pop-Location
  throw ("git checkout of WeChatDataAnalysis@" + $wdaCommit + " failed.")
}

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
