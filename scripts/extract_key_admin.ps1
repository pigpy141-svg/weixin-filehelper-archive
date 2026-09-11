$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$env:PYTHONIOENCODING = "utf-8"

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  Start-Process powershell.exe -Verb RunAs -ArgumentList @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", "`"$PSCommandPath`""
  )
  exit
}

Write-Host "Extracting keys for the currently logged-in WeChat account..."
Write-Host "Keep WeChat unlocked and open File Transfer Helper."
.\.venv\Scripts\python.exe tools\extract_key.py --config local\config.json --active-only
Write-Host ""
Read-Host "Press Enter to close"