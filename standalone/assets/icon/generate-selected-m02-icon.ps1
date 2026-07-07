$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing

$source = Join-Path $PSScriptRoot 'candidates\imagegen-10-flat-discount-20260703\flattened\flat-candidate-m02-discount-token.png'
$outDir = Join-Path $PSScriptRoot 'selected-m02-20260703'
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

function Resize-Png {
    param(
        [string] $SourcePath,
        [string] $TargetPath,
        [int] $Size
    )

    $src = [System.Drawing.Image]::FromFile($SourcePath)
    try {
        $dst = [System.Drawing.Bitmap]::new($Size, $Size, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
        $g = [System.Drawing.Graphics]::FromImage($dst)
        try {
            $g.Clear([System.Drawing.Color]::Transparent)
            $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
            $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
            $g.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
            $g.CompositingQuality = [System.Drawing.Drawing2D.CompositingQuality]::HighQuality
            $g.DrawImage($src, 0, 0, $Size, $Size)
            $dst.Save($TargetPath, [System.Drawing.Imaging.ImageFormat]::Png)
        } finally {
            $g.Dispose()
            $dst.Dispose()
        }
    } finally {
        $src.Dispose()
    }
}

function Write-Ico {
    param(
        [string[]] $PngPaths,
        [int[]] $Sizes,
        [string] $TargetPath
    )

    $streams = [System.Collections.Generic.List[byte[]]]::new()
    foreach ($path in $PngPaths) {
        $streams.Add([System.IO.File]::ReadAllBytes($path))
    }

    $fs = [System.IO.File]::Create($TargetPath)
    $bw = [System.IO.BinaryWriter]::new($fs)
    try {
        $count = [UInt16]$PngPaths.Count
        $bw.Write([UInt16]0)
        $bw.Write([UInt16]1)
        $bw.Write($count)

        $offset = 6 + (16 * $PngPaths.Count)
        for ($i = 0; $i -lt $PngPaths.Count; $i++) {
            $size = $Sizes[$i]
            $data = $streams[$i]
            $icoSize = if ($size -eq 256) { 0 } else { $size }
            $bw.Write([byte]$icoSize)
            $bw.Write([byte]$icoSize)
            $bw.Write([byte]0)
            $bw.Write([byte]0)
            $bw.Write([UInt16]1)
            $bw.Write([UInt16]32)
            $bw.Write([BitConverter]::GetBytes([UInt32]$data.Length))
            $bw.Write([BitConverter]::GetBytes([UInt32]$offset))
            $offset += $data.Length
        }

        foreach ($data in $streams) {
            $bw.Write($data)
        }
    } finally {
        $bw.Dispose()
        $fs.Dispose()
    }
}

function New-Preview {
    param(
        [string] $OutPath,
        [string] $Png256,
        [string] $Png64,
        [string] $Png48,
        [string] $Png32,
        [string] $Png16
    )

    $canvas = [System.Drawing.Bitmap]::new(760, 420, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $g = [System.Drawing.Graphics]::FromImage($canvas)
    try {
        $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
        $g.Clear([System.Drawing.Color]::FromArgb(255, 240, 238, 231))
        $titleFont = [System.Drawing.Font]::new('Segoe UI', 22, [System.Drawing.FontStyle]::Bold, [System.Drawing.GraphicsUnit]::Pixel)
        $bodyFont = [System.Drawing.Font]::new('Segoe UI', 15, [System.Drawing.FontStyle]::Regular, [System.Drawing.GraphicsUnit]::Pixel)
        $brush = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::FromArgb(255, 36, 39, 36))
        $g.DrawString('Selected M02 discount token icon', $titleFont, [System.Drawing.Brushes]::Black, 46, 30)

        $img256 = [System.Drawing.Image]::FromFile($Png256)
        $img64 = [System.Drawing.Image]::FromFile($Png64)
        $img48 = [System.Drawing.Image]::FromFile($Png48)
        $img32 = [System.Drawing.Image]::FromFile($Png32)
        $img16 = [System.Drawing.Image]::FromFile($Png16)
        try {
            $g.DrawImage($img256, 54, 78, 224, 224)
            $g.DrawImage($img64, 340, 112, 64, 64)
            $g.DrawImage($img48, 438, 120, 48, 48)
            $g.DrawImage($img32, 520, 128, 32, 32)
            $g.DrawImage($img16, 584, 136, 16, 16)
            $g.DrawString('64px', $bodyFont, $brush, 344, 190)
            $g.DrawString('48px', $bodyFont, $brush, 439, 190)
            $g.DrawString('32px', $bodyFont, $brush, 515, 190)
            $g.DrawString('16px', $bodyFont, $brush, 574, 190)
            $g.DrawString('Flat M02 source, no app.ico replacement yet', $bodyFont, $brush, 54, 335)
        } finally {
            $img256.Dispose()
            $img64.Dispose()
            $img48.Dispose()
            $img32.Dispose()
            $img16.Dispose()
        }

        $canvas.Save($OutPath, [System.Drawing.Imaging.ImageFormat]::Png)
    } finally {
        $g.Dispose()
        $canvas.Dispose()
    }
}

if (-not (Test-Path -LiteralPath $source)) {
    throw "Source image not found: $source"
}

Copy-Item -LiteralPath $source -Destination (Join-Path $outDir 'selected-m02-source.png') -Force

$sizes = @(16, 24, 32, 48, 64, 128, 256)
$pngPaths = foreach ($size in $sizes) {
    $path = Join-Path $outDir ("selected-m02-{0}.png" -f $size)
    Resize-Png $source $path $size
    $path
}

$icoPath = Join-Path $outDir 'selected-m02.ico'
Write-Ico $pngPaths $sizes $icoPath

$previewPath = Join-Path $outDir 'preview-selected-m02.png'
New-Preview `
    -OutPath $previewPath `
    -Png256 (Join-Path $outDir 'selected-m02-256.png') `
    -Png64 (Join-Path $outDir 'selected-m02-64.png') `
    -Png48 (Join-Path $outDir 'selected-m02-48.png') `
    -Png32 (Join-Path $outDir 'selected-m02-32.png') `
    -Png16 (Join-Path $outDir 'selected-m02-16.png')

[pscustomobject]@{
    Source = $source
    OutDir = $outDir
    Ico = $icoPath
    Preview = $previewPath
}
