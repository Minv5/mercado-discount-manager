$ErrorActionPreference = 'Stop'

$StandaloneDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $StandaloneDir
$PayloadWork = Join-Path $StandaloneDir 'PayloadWork'
$PayloadDir = Join-Path $StandaloneDir 'Payload'
$PayloadZip = Join-Path $PayloadDir 'payload.zip'
$DistDir = Join-Path $ProjectRoot 'dist-full'
$ProjectFile = Join-Path $StandaloneDir 'MercadoDiscountManager.Standalone.csproj'
$Desktop = [Environment]::GetFolderPath('Desktop')
$AppName = [string]::Concat([char[]](0x7F8E,0x5BA2,0x591A,0x6298,0x6263,0x7BA1,0x5BB6))
$FullExeName = "$AppName-$([string]::Concat([char[]](0x5B8C,0x6574,0x7248))).exe"
$DesktopExeName = "$AppName.exe"

function Invoke-FileOperationWithRetry {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Description,
    [Parameter(Mandatory = $true)]
    [scriptblock]$Operation
  )

  for ($attempt = 1; $attempt -le 6; $attempt++) {
    try {
      & $Operation
      return
    }
    catch {
      if ($attempt -eq 6) {
        throw
      }
      Start-Sleep -Milliseconds (500 * $attempt)
    }
  }
}

function Test-SameFileContent {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Source,
    [Parameter(Mandatory = $true)]
    [string]$Destination
  )

  if (-not (Test-Path -LiteralPath $Destination)) {
    return $false
  }

  $sourceItem = Get-Item -LiteralPath $Source
  $destinationItem = Get-Item -LiteralPath $Destination
  if ($sourceItem.Length -ne $destinationItem.Length) {
    return $false
  }

  $sourceHash = Get-FileHash -Algorithm SHA256 -LiteralPath $Source
  $destinationHash = Get-FileHash -Algorithm SHA256 -LiteralPath $Destination
  return $sourceHash.Hash -eq $destinationHash.Hash
}

$nodeCommand = Get-Command node -ErrorAction Stop
$nodeExe = $nodeCommand.Source
if (-not (Test-Path -LiteralPath $nodeExe)) {
  throw "Node.exe not found: $nodeExe"
}

if (Test-Path -LiteralPath $PayloadWork) {
  Invoke-FileOperationWithRetry "Remove payload work" {
    Remove-Item -LiteralPath $PayloadWork -Recurse -Force
  }
}
if (Test-Path -LiteralPath $PayloadDir) {
  Invoke-FileOperationWithRetry "Remove payload dir" {
    Remove-Item -LiteralPath $PayloadDir -Recurse -Force
  }
}
New-Item -ItemType Directory -Force -Path (Join-Path $PayloadWork 'app') | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $PayloadWork 'node') | Out-Null
New-Item -ItemType Directory -Force -Path $PayloadDir | Out-Null

Copy-Item -LiteralPath (Join-Path $ProjectRoot 'src') -Destination (Join-Path $PayloadWork 'app\src') -Recurse
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'public') -Destination (Join-Path $PayloadWork 'app\public') -Recurse
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'package.json') -Destination (Join-Path $PayloadWork 'app\package.json')
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'README.md') -Destination (Join-Path $PayloadWork 'app\README.md')
Copy-Item -LiteralPath $nodeExe -Destination (Join-Path $PayloadWork 'node\node.exe')

Compress-Archive -Path (Join-Path $PayloadWork '*') -DestinationPath $PayloadZip -Force

dotnet publish $ProjectFile `
  -c Release `
  -r win-x64 `
  --self-contained true `
  /p:PublishSingleFile=true `
  /p:IncludeNativeLibrariesForSelfExtract=true `
  -o $DistDir

$fullExe = Join-Path $DistDir $FullExeName
if (-not (Test-Path -LiteralPath $fullExe)) {
  throw "Publish completed but exe not found: $fullExe"
}

Get-ChildItem -LiteralPath $DistDir -Filter '*.pdb' -File -ErrorAction SilentlyContinue |
  Remove-Item -Force
Get-ChildItem -LiteralPath $DistDir -Filter '*.xml' -File -ErrorAction SilentlyContinue |
  Remove-Item -Force

$desktopExe = Join-Path $Desktop $DesktopExeName
if (-not (Test-SameFileContent -Source $fullExe -Destination $desktopExe)) {
  Invoke-FileOperationWithRetry "Copy desktop exe" {
    Copy-Item -LiteralPath $fullExe -Destination $desktopExe -Force
  }
}
$desktopFullExe = Join-Path $Desktop $FullExeName
if (Test-Path -LiteralPath $desktopFullExe) {
  Invoke-FileOperationWithRetry "Remove old desktop full exe" {
    Remove-Item -LiteralPath $desktopFullExe -Force
  }
}

[pscustomobject]@{
  Exe = $fullExe
  Length = (Get-Item -LiteralPath $fullExe).Length
  DesktopExe = $desktopExe
  PayloadZip = $PayloadZip
}
