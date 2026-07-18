$ErrorActionPreference = 'Stop'

$DesktopDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $DesktopDir
$Python = (Get-Command python -ErrorAction Stop).Source
$Spec = Join-Path $DesktopDir 'mercado_discount_manager_pyside.spec'
$Dist = Join-Path $ProjectRoot 'dist-pyside'
$Work = Join-Path $DesktopDir 'build'

if (-not (Test-Path -LiteralPath $Python)) {
  throw "The verified project Python runtime is missing."
}

& $Python -m PyInstaller --noconfirm --clean --distpath $Dist --workpath $Work $Spec
if ($LASTEXITCODE -ne 0) {
  throw "PyInstaller candidate build failed."
}

$Candidates = @(Get-ChildItem -LiteralPath $Dist -Recurse -File -Filter '*.exe' |
  Where-Object { $_.Name -ne 'node.exe' })
if ($Candidates.Count -ne 1) {
  throw "Expected exactly one candidate executable, found $($Candidates.Count)."
}
$Exe = $Candidates[0].FullName

$Item = Get-Item -LiteralPath $Exe
$Hash = Get-FileHash -LiteralPath $Exe -Algorithm SHA256
[pscustomobject]@{
  Exe = $Item.FullName
  Length = $Item.Length
  LastWriteTime = $Item.LastWriteTime
  SHA256 = $Hash.Hash
}
