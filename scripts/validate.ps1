[CmdletBinding(DefaultParameterSetName = 'Run')]
param(
  [Parameter(ParameterSetName = 'Run')]
  [ValidateSet('Quick', 'Release', 'RealWrite')]
  [string]$Mode = 'Quick',

  [Parameter(ParameterSetName = 'Run')]
  [switch]$AllowPackageMutation,

  [Parameter(ParameterSetName = 'Run')]
  [ValidateSet('PySide', 'Legacy', 'Both')]
  [string]$PackageTarget = 'PySide',

  [Parameter(Mandatory = $true, ParameterSetName = 'Show')]
  [string]$ShowFailure,

  [Parameter(Mandatory = $true, ParameterSetName = 'Show')]
  [string]$Check,

  [Parameter(ParameterSetName = 'Show')]
  [ValidateRange(1, 10000)]
  [int]$Tail = 120,

  [Parameter(ParameterSetName = 'Show')]
  [switch]$Full
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$EvidenceBase = Join-Path $ProjectRoot 'data\validation-evidence'

function Show-FailureEvidence {
  $runRoot = Join-Path $EvidenceBase $ShowFailure
  $summaryPath = Join-Path $runRoot 'summary.json'
  if (-not (Test-Path -LiteralPath $summaryPath)) {
    throw "Validation run not found: $ShowFailure"
  }
  $summary = Get-Content -LiteralPath $summaryPath -Raw -Encoding UTF8 | ConvertFrom-Json
  $result = @($summary.checks | Where-Object id -eq $Check)
  if ($result.Count -ne 1) {
    throw "Check '$Check' was not found in run '$ShowFailure'."
  }
  foreach ($name in @('stderr.log', 'stdout.log')) {
    $path = Join-Path (Join-Path $runRoot $Check) $name
    if (-not (Test-Path -LiteralPath $path)) { continue }
    "--- $name ($path) ---"
    if ($Full) { Get-Content -LiteralPath $path -Encoding UTF8 }
    else { Get-Content -LiteralPath $path -Encoding UTF8 -Tail $Tail }
  }
  exit $(if ($result[0].status -eq 'FAIL') { 1 } else { 0 })
}

if ($PSCmdlet.ParameterSetName -eq 'Show') {
  Show-FailureEvidence
}

if ($Mode -eq 'Release' -and -not $AllowPackageMutation) {
  [Console]::Error.WriteLine('BLOCKED Release requires explicit -AllowPackageMutation. No checks or packaging were started.')
  exit 2
}
if ($Mode -ne 'Release' -and $AllowPackageMutation) {
  [Console]::Error.WriteLine('-AllowPackageMutation is valid only with -Mode Release.')
  exit 2
}

New-Item -ItemType Directory -Force -Path $EvidenceBase | Out-Null
$RunId = '{0}-{1}-{2}' -f (Get-Date -Format 'yyyyMMdd-HHmmss'), $Mode.ToLowerInvariant(), ([guid]::NewGuid().ToString('N').Substring(0, 8))
$RunRoot = Join-Path $EvidenceBase $RunId
New-Item -ItemType Directory -Force -Path $RunRoot | Out-Null

function Get-ToolOutput([string]$File, [string[]]$Arguments) {
  try {
    $text = & $File @Arguments 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) { return "ERROR($LASTEXITCODE): $text" }
    return $text.Trim()
  }
  catch { return "MISSING: $($_.Exception.Message)" }
}

function Get-StringHash([string]$Value) {
  $algorithm = [Security.Cryptography.SHA256]::Create()
  try { ([BitConverter]::ToString($algorithm.ComputeHash([Text.Encoding]::UTF8.GetBytes($Value)))).Replace('-', '') }
  finally { $algorithm.Dispose() }
}

$Python = 'C:\Users\dztf6\AppData\Local\Python\bin\python.exe'
$ExpectedServiceName = [string]::Concat([char[]](0x7F8E,0x5BA2,0x591A,0x6D3B,0x52A8,0x52A9,0x624B))
$ExpectedProduct = 'mercado-discount-manager'
$ExpectedProtocolVersion = '3'
$Environment = [ordered]@{
  os = [Environment]::OSVersion.VersionString
  architecture = [Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString()
  powershell = $PSVersionTable.PSVersion.ToString()
  node = Get-ToolOutput 'node' @('--version')
  dotnet = Get-ToolOutput 'dotnet' @('--version')
  python_executable = $Python
  python = Get-ToolOutput $Python @('--version')
  pyside6 = Get-ToolOutput $Python @('-c', 'import PySide6; print(PySide6.__version__)')
  pyinstaller = Get-ToolOutput $Python @('-m', 'PyInstaller', '--version')
}
$EnvironmentJson = $Environment | ConvertTo-Json -Compress
$EnvironmentFingerprint = Get-StringHash $EnvironmentJson

function Get-InputFingerprint([string[]]$Patterns) {
  $Patterns = @($Patterns) + 'scripts\validate.ps1'
  $files = foreach ($pattern in $Patterns) {
    Get-ChildItem -Path (Join-Path $ProjectRoot $pattern) -File -Recurse -ErrorAction SilentlyContinue
  }
  $rows = foreach ($file in @($files | Sort-Object FullName -Unique)) {
    $relative = $file.FullName.Substring($ProjectRoot.Length).TrimStart('\').Replace('\', '/')
    "$relative`t$((Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash)"
  }
  $input = (@($rows) -join "`n") + "`nenvironment`t$EnvironmentFingerprint"
  Get-StringHash $input
}

$jsSyntaxCommand = "`$files=Get-ChildItem -LiteralPath 'src','public','tests' -Recurse -File -Filter *.js; foreach(`$f in `$files){ node --check `$f.FullName; if(`$LASTEXITCODE -ne 0){exit `$LASTEXITCODE}}"
$jsSyntaxEncoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($jsSyntaxCommand))
$Checks = @(
  @{ id='js-syntax'; required=$true; cacheable=$true; patterns=@('src\*.js','public\*.js','tests\*.js','package.json'); file='powershell'; args=@('-NoProfile','-EncodedCommand',$jsSyntaxEncoded) },
  @{ id='npm-test'; required=$true; cacheable=$true; patterns=@('src\*.js','public\*.js','tests\*.js','package.json'); file='npm.cmd'; args=@('test') },
  @{ id='pyside-tests'; required=$true; cacheable=$true; patterns=@('desktop-pyside\*.py','desktop-pyside\*.spec','desktop-pyside\*.ps1','desktop-pyside\*.json','desktop-pyside\assets\*'); file=$Python; args=@('-m','unittest','discover','-s','desktop-pyside\tests','-v'); environment=@{QT_QPA_PLATFORM='offscreen'} },
  @{ id='health'; required=($Mode -eq 'RealWrite'); cacheable=$false; patterns=@('src\*.js','package.json'); health=$true }
)

if ($PackageTarget -in @('Legacy','Both')) {
  $Checks += @{ id='dotnet-legacy-release-build'; required=$true; cacheable=$true; patterns=@('standalone\*.cs','standalone\*.csproj','standalone\app.manifest','standalone\assets\icon\app.ico','src\*.js','public\*','package.json','README.md'); file='dotnet'; args=@('build','standalone\MercadoDiscountManager.Standalone.csproj','-c','Release','--nologo') }
}

if ($Mode -eq 'Release') {
  if ($PackageTarget -in @('Legacy','Both')) {
    $Checks += @{ id='package-legacy'; required=$true; cacheable=$false; package=$true; patterns=@('src\*.js','public\*','standalone\*','package.json','README.md'); file='powershell'; args=@('-NoProfile','-ExecutionPolicy','Bypass','-File','standalone\build-full-exe.ps1') }
  }
  if ($PackageTarget -in @('PySide','Both')) {
    $Checks += @{ id='package-pyside'; required=$true; cacheable=$false; package=$true; patterns=@('src\*.js','public\*','desktop-pyside\*','package.json','README.md'); file='powershell'; args=@('-NoProfile','-ExecutionPolicy','Bypass','-File','desktop-pyside\build-release.ps1') }
    $Checks += @{ id='install-pyside-isolated'; required=$true; cacheable=$false; package=$true; patterns=@('desktop-pyside\install-release.ps1','desktop-pyside\test-install-release.ps1','desktop-pyside\build-release.ps1'); file='powershell'; args=@('-NoProfile','-ExecutionPolicy','Bypass','-File','desktop-pyside\test-install-release.ps1') }
  }
}

function Find-CachedPass([string]$CheckId, [string]$Fingerprint) {
  if ($Mode -ne 'Quick') { return $null }
  $summaries = Get-ChildItem -LiteralPath $EvidenceBase -Directory -ErrorAction SilentlyContinue |
    Where-Object Name -ne $RunId | Sort-Object Name -Descending
  foreach ($dir in $summaries) {
    $path = Join-Path $dir.FullName 'summary.json'
    if (-not (Test-Path -LiteralPath $path)) { continue }
    try {
      $old = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
      if ($old.mode -ne 'Quick' -or $old.overall -ne 'PASS') { continue }
      $hit = @($old.checks | Where-Object { $_.id -eq $CheckId -and $_.status -eq 'PASS' -and $_.input_fingerprint -eq $Fingerprint })
      if ($hit.Count -eq 1) { return $dir.Name }
    } catch { continue }
  }
  return $null
}

$Started = Get-Date
$Results = @()
foreach ($definition in $Checks) {
  $checkRoot = Join-Path $RunRoot $definition['id']
  New-Item -ItemType Directory -Force -Path $checkRoot | Out-Null
  $stdoutPath = Join-Path $checkRoot 'stdout.log'
  $stderrPath = Join-Path $checkRoot 'stderr.log'
  $fingerprint = Get-InputFingerprint $definition['patterns']
  $watch = [Diagnostics.Stopwatch]::StartNew()
  $status = 'PASS'; $reason = 'completed'; $exitCode = 0

  if ($definition['package'] -and @($Results | Where-Object { $_.required -and $_.status -ne 'PASS' }).Count -gt 0) {
    $status = 'SKIP'; $reason = 'blocked_by_failed_required_precheck'
    '' | Set-Content -LiteralPath $stdoutPath -Encoding UTF8
    '' | Set-Content -LiteralPath $stderrPath -Encoding UTF8
  }

  if ($status -eq 'PASS' -and $definition['health'] -and $Mode -ne 'RealWrite') {
    $listening = Get-NetTCPConnection -LocalPort 28758 -State Listen -ErrorAction SilentlyContinue
    if (-not $listening) {
      $status = 'SKIP'; $reason = 'service_not_running_quick_does_not_start_it'
      '' | Set-Content -LiteralPath $stdoutPath -Encoding UTF8
      '' | Set-Content -LiteralPath $stderrPath -Encoding UTF8
    }
  }
  if ($status -eq 'PASS' -and $definition['cacheable']) {
    $cachedRun = Find-CachedPass $definition['id'] $fingerprint
    if ($cachedRun) {
      $status = 'SKIP'; $reason = "unchanged_from_successful_run:$cachedRun"
      '' | Set-Content -LiteralPath $stdoutPath -Encoding UTF8
      '' | Set-Content -LiteralPath $stderrPath -Encoding UTF8
    }
  }
  if ($status -eq 'PASS' -and $reason -eq 'completed') {
    if ($definition['health']) {
      try {
        $response = Invoke-RestMethod -Uri 'http://127.0.0.1:28758/api/health' -TimeoutSec 10
        $response | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $stdoutPath -Encoding UTF8
        '' | Set-Content -LiteralPath $stderrPath -Encoding UTF8
        if (-not $response.ok -or $response.service -ne $ExpectedServiceName -or $response.product -ne $ExpectedProduct -or [string]$response.protocol_version -ne $ExpectedProtocolVersion -or [string]::IsNullOrWhiteSpace([string]$response.build_fingerprint)) { throw 'Health response did not match the required product protocol contract.' }
      } catch {
        $_ | Out-String | Set-Content -LiteralPath $stderrPath -Encoding UTF8
        $status='FAIL'; $reason='health_required_but_unavailable_or_invalid'; $exitCode=1
      }
    } else {
      $saved = @{}
      if ($definition['environment']) {
        foreach ($entry in $definition['environment'].GetEnumerator()) { $saved[$entry.Key] = [Environment]::GetEnvironmentVariable($entry.Key); [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value) }
      }
      try {
        # Invoke directly: some managed hosts expose Path/PATH aliases that
        # Windows PowerShell Start-Process rejects before the child can start.
        Push-Location $ProjectRoot
        $savedErrorActionPreference = $ErrorActionPreference
        try {
          # unittest writes normal progress to stderr; do not turn that stream
          # into a PowerShell terminating NativeCommandError.
          $ErrorActionPreference = 'Continue'
          & $definition['file'] @($definition['args']) 1> $stdoutPath 2> $stderrPath
          $exitCode = $LASTEXITCODE
        } finally {
          $ErrorActionPreference = $savedErrorActionPreference
          Pop-Location
        }
        if ($exitCode -ne 0) { $status='FAIL'; $reason='command_failed' }
      } catch {
        $_ | Out-String | Set-Content -LiteralPath $stderrPath -Encoding UTF8
        $status='FAIL'; $reason='command_start_failed'; $exitCode=1
      } finally {
        foreach ($key in $saved.Keys) { [Environment]::SetEnvironmentVariable($key, $saved[$key]) }
      }
    }
  }
  $watch.Stop()
  $result = [ordered]@{ id=$definition['id']; status=$status; required=[bool]$definition['required']; duration_ms=$watch.ElapsedMilliseconds; reason=$reason; input_fingerprint=$fingerprint; evidence_path=$checkRoot; exit_code=$exitCode }
  $result | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $checkRoot 'result.json') -Encoding UTF8
  $Results += [pscustomobject]$result
  '{0,-22} {1,-4} {2,8}ms {3}' -f $definition['id'],$status,$watch.ElapsedMilliseconds,$reason
}

$requiredFailures = @($Results | Where-Object { $_.required -and ($_.status -eq 'FAIL' -or ($Mode -ne 'Quick' -and $_.status -eq 'SKIP')) })
$Overall = if ($requiredFailures.Count) { 'FAIL' } else { 'PASS' }
$Summary = [ordered]@{
  run_id=$RunId; mode=$Mode; started_at=$Started.ToUniversalTime().ToString('o'); duration_ms=[int]((Get-Date)-$Started).TotalMilliseconds
  overall=$Overall; checks=$Results
  counts=[ordered]@{ pass=@($Results|Where-Object status -eq 'PASS').Count; fail=@($Results|Where-Object status -eq 'FAIL').Count; skip=@($Results|Where-Object status -eq 'SKIP').Count }
  environment_fingerprint=$EnvironmentFingerprint; environment=$Environment; evidence_root=$RunRoot
  safety=[ordered]@{ starts_or_stops_service=$false; calls_mercado_write_api=$false; package_mutation=($Mode -eq 'Release' -and [bool]$AllowPackageMutation) }
}
$Summary | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $RunRoot 'summary.json') -Encoding UTF8
[ordered]@{ environment=$Environment; checks=@($Results | Select-Object id,input_fingerprint) } | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $RunRoot 'manifest.json') -Encoding UTF8
'OVERALL {0} pass={1} fail={2} skip={3} evidence={4}' -f $Overall,$Summary.counts.pass,$Summary.counts.fail,$Summary.counts.skip,$RunRoot
exit $(if ($Overall -eq 'PASS') { 0 } else { 1 })
