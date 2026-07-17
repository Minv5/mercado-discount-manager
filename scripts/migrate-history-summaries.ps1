[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [string]$DataDir,

  [Parameter(Mandatory = $true)]
  [string]$BackupRoot,

  [switch]$TestCopy
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$DataDir = [IO.Path]::GetFullPath($DataDir)
$BackupRoot = [IO.Path]::GetFullPath($BackupRoot)
$DbPath = Join-Path $DataDir 'discount-manager.sqlite'
$FormalDataDir = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'MercadoDiscountManagerStandalone\data'))
if (-not (Test-Path -LiteralPath $DbPath)) { throw 'SQLite database not found.' }
if ($TestCopy -and $DataDir.TrimEnd('\') -eq $FormalDataDir.TrimEnd('\')) {
  throw 'TestCopy cannot bypass guards for the formal data directory.'
}
if (-not $TestCopy -and (Get-NetTCPConnection -LocalPort 28758 -State Listen -ErrorAction SilentlyContinue)) {
  throw 'Local service is still listening on port 28758.'
}

$unfinished = @('queued', 'running', 'stopping', 'legacy_unknown')
$StateDir = Join-Path $DataDir 'execution-job-states'
if (-not $TestCopy -and (Test-Path -LiteralPath $StateDir)) {
  foreach ($file in Get-ChildItem -LiteralPath $StateDir -Filter '*.json' -File) {
    try {
      $state = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
      throw "Cannot parse execution job state: $($file.Name)"
    }
    if ($unfinished -contains [string]$state.status) {
      throw "Unfinished execution job blocks migration: $($file.Name)"
    }
  }
}

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$BackupDir = Join-Path $BackupRoot "history-summary-migration-$stamp"
New-Item -ItemType Directory -Path $BackupDir | Out-Null
$Names = @('discount-manager.sqlite', 'discount-manager.sqlite-wal', 'discount-manager.sqlite-shm')
$Copied = @()
foreach ($name in $Names) {
  $source = Join-Path $DataDir $name
  if (-not (Test-Path -LiteralPath $source)) { continue }
  $target = Join-Path $BackupDir $name
  Copy-Item -LiteralPath $source -Destination $target
  $Copied += [pscustomobject]@{
    name = $name
    bytes = (Get-Item -LiteralPath $target).Length
    sha256 = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash
  }
}
if (-not ($Copied.name -contains 'discount-manager.sqlite')) { throw 'SQLite main-file backup failed.' }

$snapshotRaw = & node (Join-Path $PSScriptRoot 'check-sqlite-snapshot.mjs') "--data-dir=$BackupDir"
if ($LASTEXITCODE -ne 0) { throw 'SQLite backup integrity check failed.' }
$snapshot = $snapshotRaw | ConvertFrom-Json
if ($snapshot.integrity -ne 'ok') { throw 'SQLite backup is not internally consistent.' }

try {
  $raw = & node (Join-Path $PSScriptRoot 'migrate-history-summaries.mjs') "--data-dir=$DataDir"
  if ($LASTEXITCODE -ne 0) { throw 'History-summary migration failed.' }
  $migration = $raw | ConvertFrom-Json
  if (-not $migration.ok -or -not $migration.equal20 -or -not $migration.equal300 -or $migration.integrity -ne 'ok') {
    throw 'History-summary migration verification failed.'
  }
  $manifest = [ordered]@{
    created_at = (Get-Date).ToString('o')
    data_dir = $DataDir
    backup_dir = $BackupDir
    files = $Copied
    backup_snapshot = $snapshot
    migration = $migration
  }
  $manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $BackupDir 'migration-manifest.json') -Encoding UTF8
  $manifest | ConvertTo-Json -Depth 8
} catch {
  foreach ($file in $Copied) {
    Copy-Item -LiteralPath (Join-Path $BackupDir $file.name) -Destination (Join-Path $DataDir $file.name) -Force
  }
  foreach ($name in $Names) {
    if (($Copied.name -notcontains $name) -and (Test-Path -LiteralPath (Join-Path $DataDir $name))) {
      Remove-Item -LiteralPath (Join-Path $DataDir $name) -Force
    }
  }
  throw
}
