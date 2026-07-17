[CmdletBinding()]
param(
  [string]$CandidateRoot = $null,
  [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'Programs\MercadoDiscountManagerPySide'),
  [string]$ShortcutPath = $null,
  [switch]$SkipShortcut,
  [switch]$SkipSmoke,
  [switch]$IsolatedValidation,
  [switch]$SimulateFailureAfterCopy
)

$ErrorActionPreference = 'Stop'
$Product = 'mercado-discount-manager'
$DisplayName = [string]::Concat([char[]](0x7F8E,0x5BA2,0x591A,0x6D3B,0x52A8,0x52A9,0x624B))
$LegacyDisplayName = [string]::Concat([char[]](0x7F8E,0x5BA2,0x591A,0x6298,0x6263,0x7BA1,0x5BB6))
$ProjectRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($CandidateRoot)) {
  $CandidateRoot = Join-Path $ProjectRoot (Join-Path 'dist-pyside' $DisplayName)
}
if ([string]::IsNullOrWhiteSpace($ShortcutPath)) {
  $ShortcutPath = Join-Path ([Environment]::GetFolderPath('Programs')) ($DisplayName + '.lnk')
}
$ContractPath = Join-Path $ProjectRoot 'src\productContract.js'
$Contract = Get-Content -LiteralPath $ContractPath -Raw -Encoding UTF8
$ProtocolMatch = [regex]::Match($Contract, 'PROTOCOL_VERSION\s*=\s*[''"](?<version>[^''"]+)[''"]')
if (-not $ProtocolMatch.Success) { throw 'Unable to read protocol version from src/productContract.js.' }
$ProtocolVersion = $ProtocolMatch.Groups['version'].Value
$ExeName = $DisplayName + '.exe'
$LegacyExeName = $LegacyDisplayName + '.exe'
$HealthUrl = 'http://127.0.0.1:28758/api/health'
$ActiveJobsUrl = 'http://127.0.0.1:28758/api/execution/jobs/active'
$ActiveSubmissionUrl = 'http://127.0.0.1:28758/api/execution/submissions/active'

function Read-ReleaseManifest([string]$Root) {
  $path = Join-Path $Root 'release-manifest.json'
  if (-not (Test-Path -LiteralPath $path)) { throw 'Candidate package is missing release-manifest.json.' }
  $manifest = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
  if ($manifest.product -ne $Product -or [string]$manifest.protocol_version -ne $ProtocolVersion) {
    throw 'Candidate product or protocol does not match.'
  }
  if ([string]$manifest.display_name -ne $DisplayName -or [string]$manifest.executable -ne $ExeName) {
    throw 'Candidate display name or executable name does not match the current product release.'
  }
  $exe = Join-Path $Root $manifest.executable
  if (-not (Test-Path -LiteralPath $exe)) { throw 'Candidate executable is missing.' }
  if ((Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash -ne $manifest.exe_sha256) {
    throw 'Candidate executable hash does not match the manifest.'
  }
  $files = @(Get-ChildItem -LiteralPath $Root -Recurse -File)
  $bytes = [long](($files | Measure-Object Length -Sum).Sum)
  if ($manifest.file_count -ne $files.Count -or $manifest.total_bytes -ne $bytes) {
    throw 'Candidate file count or total size does not match the manifest.'
  }
  foreach ($required in @('_internal\node\node.exe','_internal\app\src\server.js','_internal\app\build-info.json','_internal\PySide6\Qt6Core.dll','_internal\PySide6\Qt6Gui.dll','_internal\PySide6\Qt6Widgets.dll')) {
    if (-not (Test-Path -LiteralPath (Join-Path $Root $required))) { throw "Candidate dependency is missing: $required" }
  }
  return $manifest
}

function Get-PortOwnerPid {
  $line = Get-NetTCPConnection -LocalPort 28758 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($line) { return [int]$line.OwningProcess }
  return $null
}

function Get-CompatibleHealth {
  try { $health = Invoke-RestMethod -Uri $HealthUrl -TimeoutSec 2 }
  catch { return $null }
  if ($health.product -ne $Product -or [string]$health.protocol_version -ne $ProtocolVersion) {
    throw 'Port 28758 hosts an incompatible product. Installation stopped without terminating it.'
  }
  return $health
}

function Assert-NoActiveExecution {
  $health = Get-CompatibleHealth
  if (-not $health) {
    if (Get-PortOwnerPid) { throw 'Port 28758 is occupied by an unknown service; active jobs cannot be verified.' }
    return
  }
  $active = Invoke-RestMethod -Uri $ActiveJobsUrl -TimeoutSec 5
  if ($active.active -or @($active.execution_jobs).Count -gt 0 -or @($active.benchmark_jobs).Count -gt 0 -or @($active.execution_groups).Count -gt 0) {
    throw 'An execution job or group is active. Installation stopped.'
  }
  $activeSubmission = Invoke-RestMethod -Uri $ActiveSubmissionUrl -TimeoutSec 5
  if ($activeSubmission.active) {
    throw 'An execution submission is preparing or awaiting confirmation. Installation stopped.'
  }
}

function Get-ProductProcesses([string]$Root) {
  $rootFull = [IO.Path]::GetFullPath($Root).TrimEnd('\') + '\'
  return @(Get-CimInstance Win32_Process | Where-Object {
    $_.ExecutablePath -and ([IO.Path]::GetFullPath($_.ExecutablePath).StartsWith($rootFull, [StringComparison]::OrdinalIgnoreCase))
  })
}

function Stop-ProductProcesses([string]$Root) {
  $processes = @(Get-ProductProcesses $Root)
  foreach ($entry in $processes | Where-Object { $_.Name -ieq $ExeName -or $_.Name -ieq $LegacyExeName }) {
    $process = Get-Process -Id $entry.ProcessId -ErrorAction SilentlyContinue
    if ($process) { $null = $process.CloseMainWindow() }
  }
  $deadline = (Get-Date).AddSeconds(15)
  do {
    if (@(Get-ProductProcesses $Root).Count -eq 0) { return }
    Start-Sleep -Milliseconds 250
  } while ((Get-Date) -lt $deadline)
  throw 'The product is still running; installation stopped without terminating unrelated processes.'
}

function Set-AndVerifyShortcut([string]$Path, [string]$Target) {
  $shortcutDirectory = Split-Path -Parent $Path
  if (-not (Test-Path -LiteralPath $shortcutDirectory)) {
    New-Item -ItemType Directory -Path $shortcutDirectory -Force | Out-Null
  }
  $shell = New-Object -ComObject WScript.Shell
  $shortcut = $shell.CreateShortcut($Path)
  $shortcut.TargetPath = $Target
  $shortcut.WorkingDirectory = Split-Path -Parent $Target
  $shortcut.IconLocation = "$Target,0"
  $shortcut.Save()
  $saved = $shell.CreateShortcut($Path)
  if ([IO.Path]::GetFullPath($saved.TargetPath) -ne [IO.Path]::GetFullPath($Target) -or
      [IO.Path]::GetFullPath($saved.WorkingDirectory) -ne [IO.Path]::GetFullPath((Split-Path -Parent $Target))) {
    throw 'Shortcut verification failed.'
  }
}

function Remove-LegacyStartMenuShortcut([string]$CurrentShortcutPath) {
  $legacy = Join-Path (Split-Path -Parent $CurrentShortcutPath) ($LegacyDisplayName + '.lnk')
  if ([IO.Path]::GetFullPath($legacy) -ne [IO.Path]::GetFullPath($CurrentShortcutPath) -and (Test-Path -LiteralPath $legacy)) {
    Remove-Item -LiteralPath $legacy -Force
  }
}

function Invoke-HiddenSmoke([string]$Exe, [string]$DataRoot) {
  $savedData = $env:MDM_DATA_DIR
  try {
    $env:MDM_DATA_DIR = $DataRoot
    foreach ($argument in @('--keyboard-smoke','--smoke-service')) {
      $process = Start-Process -FilePath $Exe -ArgumentList $argument -WindowStyle Hidden -Wait -PassThru
      if ($process.ExitCode -ne 0) { throw "Installed $argument smoke failed." }
    }
    if (Get-PortOwnerPid) { throw 'Port 28758 remains open after hidden smoke.' }
  } finally {
    $env:MDM_DATA_DIR = $savedData
    if (Test-Path -LiteralPath $DataRoot) { Remove-Item -LiteralPath $DataRoot -Recurse -Force }
  }
}

$candidate = [IO.Path]::GetFullPath($CandidateRoot)
$install = [IO.Path]::GetFullPath($InstallRoot)
if (-not $IsolatedValidation -and $install -notlike ((Join-Path $env:LOCALAPPDATA 'Programs\*'))) {
  throw 'Formal installation is restricted to the current user AppData Programs directory.'
}
$manifest = Read-ReleaseManifest $candidate
Assert-NoActiveExecution
if (Test-Path -LiteralPath $install) { Stop-ProductProcesses $install }

$parent = Split-Path -Parent $install
$leaf = Split-Path -Leaf $install
$transaction = Join-Path $parent ('.{0}.installing-{1}' -f $leaf, [guid]::NewGuid().ToString('N'))
$backup = Join-Path $parent ('{0}.backup-{1}' -f $leaf, (Get-Date -Format 'yyyyMMdd-HHmmssfff'))
$movedOld = $false
try {
  New-Item -ItemType Directory -Force -Path $parent | Out-Null
  Copy-Item -LiteralPath $candidate -Destination $transaction -Recurse
  $null = Read-ReleaseManifest $transaction
  if (Test-Path -LiteralPath $install) {
    Move-Item -LiteralPath $install -Destination $backup
    'mercado-discount-manager-installer-backup' | Set-Content -LiteralPath (Join-Path $backup '.mdm-product-backup') -Encoding ASCII
    $movedOld = $true
  }
  Move-Item -LiteralPath $transaction -Destination $install
  if ($SimulateFailureAfterCopy) { throw 'isolated validation rollback simulation' }
  $installedManifest = Read-ReleaseManifest $install
  $installedExe = Join-Path $install $ExeName
  if (-not $SkipShortcut) {
    Set-AndVerifyShortcut $ShortcutPath $installedExe
    Remove-LegacyStartMenuShortcut $ShortcutPath
  }
  if (-not $SkipSmoke) { Invoke-HiddenSmoke $installedExe (Join-Path $parent ('.{0}.smoke-data' -f $leaf)) }
  if (@(Get-ProductProcesses $install).Count -gt 0) { throw 'Product processes remain after hidden smoke.' }

  $backups = @(Get-ChildItem -LiteralPath $parent -Directory -Filter "$leaf.backup-*" |
    Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName '.mdm-product-backup') })
  foreach ($old in $backups | Where-Object { $_.FullName -ne $backup }) {
    Remove-Item -LiteralPath $old.FullName -Recurse -Force
  }
  if ($movedOld -and -not (Test-Path -LiteralPath $backup)) { throw 'The current installation backup was not retained.' }
  [pscustomobject]@{ ok=$true; install_root=$install; backup_root=if($movedOld){$backup}else{$null}; exe_sha256=$installedManifest.exe_sha256 }
} catch {
  if (Test-Path -LiteralPath $transaction) { Remove-Item -LiteralPath $transaction -Recurse -Force }
  if (Test-Path -LiteralPath $install) { Remove-Item -LiteralPath $install -Recurse -Force }
  if ($movedOld -and (Test-Path -LiteralPath $backup)) {
    Copy-Item -LiteralPath $backup -Destination $install -Recurse
    $restoredMarker = Join-Path $install '.mdm-product-backup'
    if (Test-Path -LiteralPath $restoredMarker) { Remove-Item -LiteralPath $restoredMarker -Force }
  }
  throw
}
