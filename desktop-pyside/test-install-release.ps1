$ErrorActionPreference = 'Stop'

$DesktopDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $DesktopDir
$AppName = [string]::Concat([char[]](0x7F8E,0x5BA2,0x591A,0x6D3B,0x52A8,0x7BA1,0x5BB6))
$LegacyAppName = [string]::Concat([char[]](0x7F8E,0x5BA2,0x591A,0x6298,0x6263,0x7BA1,0x5BB6))
$Candidate = Join-Path $ProjectRoot (Join-Path 'dist-pyside' $AppName)
$Installer = Join-Path $DesktopDir 'install-release.ps1'
$Root = Join-Path $DesktopDir 'install-validation'
$Install = Join-Path $Root 'MercadoDiscountManagerPySide'
$StartMenuDir = Join-Path $Root 'Start Menu\Programs'
$IsolatedDesktopDir = Join-Path $Root 'Desktop'
$Shortcut = Join-Path $StartMenuDir ($AppName + '.lnk')
$DesktopShortcut = Join-Path $IsolatedDesktopDir ($AppName + '.lnk')
$LegacyShortcut = Join-Path $StartMenuDir ($LegacyAppName + '.lnk')
$Exe = Join-Path $Install ($AppName + '.exe')

if (-not (Test-Path -LiteralPath (Join-Path $Candidate 'release-manifest.json'))) {
  throw 'Build the PySide release before running isolated installer validation.'
}
if (Get-NetTCPConnection -LocalPort 28758 -State Listen -ErrorAction SilentlyContinue) {
  throw 'Port 28758 must be free before isolated installer validation.'
}
if (Test-Path -LiteralPath $Root) { Remove-Item -LiteralPath $Root -Recurse -Force }
New-Item -ItemType Directory -Force -Path $Root | Out-Null
New-Item -ItemType Directory -Force -Path $StartMenuDir | Out-Null
New-Item -ItemType Directory -Force -Path $IsolatedDesktopDir | Out-Null
Copy-Item -LiteralPath $Candidate -Destination $Install -Recurse
$null = New-Item -ItemType File -Path $LegacyShortcut -Force
$beforeHash = (Get-FileHash -LiteralPath $Exe -Algorithm SHA256).Hash

# A compatible test service reporting an active job must block installation.
$stubBase64 = 'aW1wb3J0IGpzb24KZnJvbSBodHRwLnNlcnZlciBpbXBvcnQgQmFzZUhUVFBSZXF1ZXN0SGFuZGxlciwgSFRUUFNlcnZlcgpjbGFzcyBIKEJhc2VIVFRQUmVxdWVzdEhhbmRsZXIpOgogICAgZGVmIGRvX0dFVChzZWxmKToKICAgICAgICBib2R5ID0gKHsib2siOiBUcnVlLCAicHJvZHVjdCI6ICJtZXJjYWRvLWRpc2NvdW50LW1hbmFnZXIiLCAicHJvdG9jb2xfdmVyc2lvbiI6ICIxIiwgImJ1aWxkX2ZpbmdlcnByaW50IjogIkEiICogNjR9CiAgICAgICAgICAgICAgICBpZiBzZWxmLnBhdGggPT0gIi9hcGkvaGVhbHRoIiBlbHNlCiAgICAgICAgICAgICAgICB7Im9rIjogVHJ1ZSwgImFjdGl2ZSI6IFRydWUsICJleGVjdXRpb25fam9icyI6IFt7InN0YXR1cyI6ICJydW5uaW5nIn1dLCAiYmVuY2htYXJrX2pvYnMiOiBbXSwgImV4ZWN1dGlvbl9ncm91cHMiOiBbXX0pCiAgICAgICAgZGF0YSA9IGpzb24uZHVtcHMoYm9keSkuZW5jb2RlKCkKICAgICAgICBzZWxmLnNlbmRfcmVzcG9uc2UoMjAwKTsgc2VsZi5zZW5kX2hlYWRlcigiQ29udGVudC1UeXBlIiwgImFwcGxpY2F0aW9uL2pzb24iKTsgc2VsZi5zZW5kX2hlYWRlcigiQ29udGVudC1MZW5ndGgiLCBzdHIobGVuKGRhdGEpKSk7IHNlbGYuZW5kX2hlYWRlcnMoKTsgc2VsZi53ZmlsZS53cml0ZShkYXRhKQogICAgZGVmIGxvZ19tZXNzYWdlKHNlbGYsICphcmdzKTogcGFzcwpIVFRQU2VydmVyKCgiMTI3LjAuMC4xIiwgMjg3NTgpLCBIKS5zZXJ2ZV9mb3JldmVyKCk='
$stub = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($stubBase64))
$stubFile = Join-Path $Root 'active_stub.py'
$stub | Set-Content -LiteralPath $stubFile -Encoding UTF8
$python = (Get-Command python -ErrorAction Stop).Source
$stubProcess = Start-Process -FilePath $python -ArgumentList $stubFile -WindowStyle Hidden -PassThru
try {
  $deadline = (Get-Date).AddSeconds(5)
  while (-not (Get-NetTCPConnection -LocalPort 28758 -State Listen -ErrorAction SilentlyContinue)) {
    if ((Get-Date) -ge $deadline) { throw 'Active-job stub did not start.' }
    Start-Sleep -Milliseconds 100
  }
  $blocked = $false
  try { & $Installer -CandidateRoot $Candidate -InstallRoot $Install -ShortcutPath $Shortcut -IsolatedValidation -SkipShortcut -SkipSmoke 2>$null }
  catch { $blocked = $true }
  if (-not $blocked) { throw 'Installer did not block an active execution job.' }
} finally {
  Stop-Process -Id $stubProcess.Id -Force -ErrorAction SilentlyContinue
  $stubProcess.WaitForExit(5000) | Out-Null
}
if ((Get-FileHash -LiteralPath $Exe -Algorithm SHA256).Hash -ne $beforeHash) { throw 'Active-job gate changed the installation.' }

& $Installer -CandidateRoot $Candidate -InstallRoot $Install -ShortcutPath $Shortcut -IsolatedValidation
if (-not (Test-Path -LiteralPath $Shortcut)) { throw 'Start Menu shortcut was not created.' }
if (Test-Path -LiteralPath $DesktopShortcut) { throw 'Desktop shortcut was created unexpectedly.' }
if (Test-Path -LiteralPath $LegacyShortcut) { throw 'Legacy Start Menu shortcut was not retired after successful installation.' }
$shell = New-Object -ComObject WScript.Shell
$savedShortcut = $shell.CreateShortcut($Shortcut)
if ([IO.Path]::GetFullPath($savedShortcut.TargetPath) -ne [IO.Path]::GetFullPath($Exe) -or
    [IO.Path]::GetFullPath($savedShortcut.WorkingDirectory) -ne [IO.Path]::GetFullPath($Install)) {
  throw 'Start Menu shortcut target or working directory is incorrect.'
}
& $Installer -CandidateRoot $Candidate -InstallRoot $Install -ShortcutPath $Shortcut -IsolatedValidation
$backups = @(Get-ChildItem -LiteralPath $Root -Directory -Filter 'MercadoDiscountManagerPySide.backup-*')
if ($backups.Count -ne 1) { throw "Installer retained $($backups.Count) rollback directories instead of one." }

# Touching the old backup must not make it outrank the backup created by this install.
$priorMarker = Join-Path $Install 'prior-install-marker.txt'
'current-install-before-retention-test' | Set-Content -LiteralPath $priorMarker -Encoding ASCII
$backups[0].LastWriteTime = (Get-Date).AddDays(1)
& $Installer -CandidateRoot $Candidate -InstallRoot $Install -ShortcutPath $Shortcut -IsolatedValidation
$backups = @(Get-ChildItem -LiteralPath $Root -Directory -Filter 'MercadoDiscountManagerPySide.backup-*')
if ($backups.Count -ne 1 -or -not (Test-Path -LiteralPath (Join-Path $backups[0].FullName 'prior-install-marker.txt'))) {
  throw 'Installer did not retain the backup created by the current install transaction.'
}

$stableHash = (Get-FileHash -LiteralPath $Exe -Algorithm SHA256).Hash
$failedInstallMarker = Join-Path $Install 'failed-install-source-marker.txt'
'restore-and-retain' | Set-Content -LiteralPath $failedInstallMarker -Encoding ASCII
$failed = $false
try {
  & $Installer -CandidateRoot $Candidate -InstallRoot $Install -ShortcutPath $Shortcut -IsolatedValidation -SkipShortcut -SkipSmoke -SimulateFailureAfterCopy
} catch { $failed = $true }
if (-not $failed) { throw 'Rollback simulation did not fail.' }
if ((Get-FileHash -LiteralPath $Exe -Algorithm SHA256).Hash -ne $stableHash) { throw 'Rollback did not restore the prior installation.' }
$failedBackups = @(Get-ChildItem -LiteralPath $Root -Directory -Filter 'MercadoDiscountManagerPySide.backup-*')
if (-not (Test-Path -LiteralPath $failedInstallMarker) -or
    -not ($failedBackups | Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName 'failed-install-source-marker.txt') })) {
  throw 'Failure rollback did not preserve both the restored installation and its backup evidence.'
}

$result = [pscustomobject]@{
  ok = $true
  active_job_gate = 'blocked'
  success_install = 'verified'
  failure_rollback = 'verified'
  retained_backups = 1
  installed_sha256 = $stableHash
}
if (Test-Path -LiteralPath $Root) { Remove-Item -LiteralPath $Root -Recurse -Force }
$result
