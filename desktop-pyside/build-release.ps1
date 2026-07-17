$ErrorActionPreference = 'Stop'

$DesktopDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $DesktopDir
$Python = 'C:\Users\dztf6\AppData\Local\Python\bin\python.exe'
$Spec = Join-Path $DesktopDir 'mercado_discount_manager_pyside.spec'
$Dist = Join-Path $ProjectRoot 'dist-pyside'
$Work = Join-Path $DesktopDir 'build-release'
$Staging = Join-Path $DesktopDir 'runtime-staging'
$AppStaging = Join-Path $Staging 'app'
$NodeStaging = Join-Path $Staging 'node\node.exe'
$NodeLockPath = Join-Path $DesktopDir 'node-runtime.lock.json'
$Product = 'mercado-discount-manager'
$DisplayName = [string]::Concat([char[]](0x7F8E,0x5BA2,0x591A,0x6D3B,0x52A8,0x52A9,0x624B))
$BuildStarted = Get-Date
$Utf8NoBom = [System.Text.UTF8Encoding]::new($false)

function Write-Utf8NoBom([string]$Path, [string]$Text) {
  [IO.File]::WriteAllText($Path, $Text, $script:Utf8NoBom)
}

function Get-ProductProtocolVersion([string]$Root) {
  $contractPath = Join-Path $Root 'src\productContract.js'
  $contract = Get-Content -LiteralPath $contractPath -Raw -Encoding UTF8
  $match = [regex]::Match($contract, 'PROTOCOL_VERSION\s*=\s*[''"](?<version>[^''"]+)[''"]')
  if (-not $match.Success) { throw 'Unable to read protocol version from src/productContract.js.' }
  return $match.Groups['version'].Value
}

$ProtocolVersion = Get-ProductProtocolVersion $ProjectRoot

function Get-TreeFingerprint([string]$Root) {
  $rows = foreach ($file in Get-ChildItem -LiteralPath $Root -Recurse -File | Sort-Object FullName) {
    $relative = $file.FullName.Substring($Root.Length).TrimStart('\').Replace('\', '/')
    if ($relative -eq 'build-info.json') { continue }
    '{0}|{1}' -f $relative, (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash
  }
  $bytes = [Text.Encoding]::UTF8.GetBytes(($rows -join "`n"))
  $sha = [Security.Cryptography.SHA256]::Create()
  try { return ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-', '') }
  finally { $sha.Dispose() }
}

function Write-ReleaseManifest([string]$ReleaseRoot, [System.IO.FileInfo]$Exe, [System.Collections.IDictionary]$BuildInfo) {
  $manifestPath = Join-Path $ReleaseRoot 'release-manifest.json'
  for ($attempt = 0; $attempt -lt 4; $attempt++) {
    $files = @(Get-ChildItem -LiteralPath $ReleaseRoot -Recurse -File)
    $manifest = [ordered]@{
      schema_version = 1
      product = $BuildInfo.product
      display_name = $BuildInfo.display_name
      protocol_version = $BuildInfo.protocol_version
      build_fingerprint = $BuildInfo.build_fingerprint
      built_at = $BuildInfo.built_at
      file_count = $files.Count
      total_bytes = [long](($files | Measure-Object Length -Sum).Sum)
      executable = $Exe.Name
      exe_length = [long]$Exe.Length
      exe_sha256 = (Get-FileHash -LiteralPath $Exe.FullName -Algorithm SHA256).Hash
      node_version = $BuildInfo.node_version
      node_sha256 = $BuildInfo.node_sha256
    }
    $json = $manifest | ConvertTo-Json -Depth 5
    $old = if (Test-Path -LiteralPath $manifestPath) { Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 } else { '' }
    if ($old -eq $json) { break }
    Write-Utf8NoBom $manifestPath $json
  }
  $files = @(Get-ChildItem -LiteralPath $ReleaseRoot -Recurse -File)
  $saved = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
  if ($saved.file_count -ne $files.Count -or $saved.total_bytes -ne [long](($files | Measure-Object Length -Sum).Sum)) {
    throw 'Generated release manifest does not match the final directory.'
  }
  return $saved
}

if (-not (Test-Path -LiteralPath $Python)) { throw 'The verified project Python runtime is missing.' }
if (-not (Test-Path -LiteralPath $NodeLockPath)) { throw 'The verified Node runtime lock is missing.' }

$nodeLock = Get-Content -LiteralPath $NodeLockPath -Raw -Encoding UTF8 | ConvertFrom-Json
$nodeCommand = Get-Command node.exe -ErrorAction Stop
$nodeSource = $nodeCommand.Source
$nodeVersion = (& $nodeSource --version).Trim()
$nodeHash = (Get-FileHash -LiteralPath $nodeSource -Algorithm SHA256).Hash
if ($nodeVersion -ne $nodeLock.version -or $nodeHash -ne $nodeLock.sha256) {
  throw "The available Node runtime does not match node-runtime.lock.json. version=$nodeVersion SHA256=$nodeHash"
}

if (Test-Path -LiteralPath $Staging) { Remove-Item -LiteralPath $Staging -Recurse -Force }
New-Item -ItemType Directory -Force -Path $AppStaging,(Split-Path -Parent $NodeStaging) | Out-Null
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'src') -Destination (Join-Path $AppStaging 'src') -Recurse
$PublicReleaseFiles = @('index.html', 'styles.css')
$PublicStaging = Join-Path $AppStaging 'public'
New-Item -ItemType Directory -Force -Path $PublicStaging | Out-Null
foreach ($name in $PublicReleaseFiles) {
  Copy-Item -LiteralPath (Join-Path $ProjectRoot "public\$name") -Destination (Join-Path $PublicStaging $name)
}
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'package.json') -Destination $AppStaging
Copy-Item -LiteralPath $nodeSource -Destination $NodeStaging
if ((Get-FileHash -LiteralPath $NodeStaging -Algorithm SHA256).Hash -ne $nodeLock.sha256) {
  throw 'Staged Node runtime hash changed during copy.'
}

$buildInfo = [ordered]@{
  product = $Product
  display_name = $DisplayName
  protocol_version = $ProtocolVersion
  build_fingerprint = Get-TreeFingerprint $AppStaging
  built_at = $BuildStarted.ToUniversalTime().ToString('o')
  node_version = $nodeVersion
  node_sha256 = $nodeHash
}
Write-Utf8NoBom (Join-Path $AppStaging 'build-info.json') ($buildInfo | ConvertTo-Json -Depth 4)

& $Python -m PyInstaller --noconfirm --clean --distpath $Dist --workpath $Work $Spec
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller release build failed.' }

$NewExecutables = @(Get-ChildItem -LiteralPath $Dist -Recurse -File -Filter '*.exe' |
  Where-Object { $_.Name -ne 'node.exe' -and $_.LastWriteTime -ge $BuildStarted.AddMinutes(-1) })
if ($NewExecutables.Count -ne 1) { throw "Expected exactly one newly built release executable, found $($NewExecutables.Count)." }
$Exe = $NewExecutables[0]
$ReleaseRoot = $Exe.Directory
$Required = @(
  (Join-Path $ReleaseRoot.FullName '_internal\node\node.exe'),
  (Join-Path $ReleaseRoot.FullName '_internal\app\src\server.js'),
  (Join-Path $ReleaseRoot.FullName '_internal\app\build-info.json'),
  (Join-Path $ReleaseRoot.FullName '_internal\app\public\index.html'),
  (Join-Path $ReleaseRoot.FullName '_internal\app\public\styles.css'),
  (Join-Path $ReleaseRoot.FullName '_internal\PySide6\Qt6Core.dll'),
  (Join-Path $ReleaseRoot.FullName '_internal\PySide6\Qt6Gui.dll'),
  (Join-Path $ReleaseRoot.FullName '_internal\PySide6\Qt6Widgets.dll')
)
$Missing = @($Required | Where-Object { -not (Test-Path -LiteralPath $_) })
if ($Missing.Count -gt 0) { throw "Release package is incomplete. Missing $($Missing.Count) required files." }

$manifest = Write-ReleaseManifest $ReleaseRoot.FullName $Exe $buildInfo
$result = [pscustomobject]@{
  Root = $ReleaseRoot.FullName
  Exe = $Exe.FullName
  FileCount = $manifest.file_count
  TotalBytes = $manifest.total_bytes
  ExeLength = $manifest.exe_length
  LastWriteTime = $Exe.LastWriteTime
  SHA256 = $manifest.exe_sha256
  ProtocolVersion = $manifest.protocol_version
  BuildFingerprint = $manifest.build_fingerprint
  NodeSHA256 = $manifest.node_sha256
  Manifest = (Join-Path $ReleaseRoot.FullName 'release-manifest.json')
}
if (Test-Path -LiteralPath $Staging) { Remove-Item -LiteralPath $Staging -Recurse -Force }
if (Test-Path -LiteralPath $Work) { Remove-Item -LiteralPath $Work -Recurse -Force }
$result
