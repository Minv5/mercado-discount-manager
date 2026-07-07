$ErrorActionPreference = 'Stop'

$IconDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Sizes = @(16, 24, 32, 48, 64, 128, 256)

Add-Type -AssemblyName System.Drawing

function New-RoundedRectPath {
  param(
    [System.Drawing.RectangleF]$Rect,
    [float]$Radius
  )
  $path = [System.Drawing.Drawing2D.GraphicsPath]::new()
  $d = $Radius * 2
  $path.AddArc($Rect.X, $Rect.Y, $d, $d, 180, 90)
  $path.AddArc($Rect.Right - $d, $Rect.Y, $d, $d, 270, 90)
  $path.AddArc($Rect.Right - $d, $Rect.Bottom - $d, $d, $d, 0, 90)
  $path.AddArc($Rect.X, $Rect.Bottom - $d, $d, $d, 90, 90)
  $path.CloseFigure()
  return $path
}

function New-PointF {
  param([float]$X, [float]$Y)
  return [System.Drawing.PointF]::new($X, $Y)
}

function Add-PolygonPath {
  param([System.Drawing.Drawing2D.GraphicsPath]$Path, [System.Drawing.PointF[]]$Points)
  $Path.AddPolygon($Points)
}

function Draw-AppIconPng {
  param(
    [int]$Size,
    [string]$OutPath
  )

  $scale = if ($Size -lt 64) { 8 } elseif ($Size -lt 128) { 4 } else { 3 }
  $canvas = $Size * $scale
  $u = [float]($canvas / 256.0)
  function P([float]$v) { return [float]($v * $u) }

  $bitmap = [System.Drawing.Bitmap]::new($canvas, $canvas, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
  $g = [System.Drawing.Graphics]::FromImage($bitmap)
  $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
  $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
  $g.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
  $g.Clear([System.Drawing.Color]::Transparent)

  $baseRect = [System.Drawing.RectangleF]::new((P 18), (P 18), (P 220), (P 220))
  $basePath = New-RoundedRectPath $baseRect (P 44)
  $baseBrush = [System.Drawing.Drawing2D.LinearGradientBrush]::new(
    (New-PointF (P 36) (P 18)),
    (New-PointF (P 220) (P 238)),
    [System.Drawing.ColorTranslator]::FromHtml('#202620'),
    [System.Drawing.ColorTranslator]::FromHtml('#101311'))
  $g.FillPath($baseBrush, $basePath)
  $basePen = [System.Drawing.Pen]::new([System.Drawing.ColorTranslator]::FromHtml('#4E472F'), (P 8))
  $g.DrawPath($basePen, $basePath)

  $innerRect = [System.Drawing.RectangleF]::new((P 26), (P 26), (P 204), (P 204))
  $innerPath = New-RoundedRectPath $innerRect (P 36)
  $innerColor = [System.Drawing.Color]::FromArgb(140, [System.Drawing.ColorTranslator]::FromHtml('#8A7432'))
  $innerPen = [System.Drawing.Pen]::new($innerColor, (P 2))
  $g.DrawPath($innerPen, $innerPath)

  $shieldPath = [System.Drawing.Drawing2D.GraphicsPath]::new()
  Add-PolygonPath $shieldPath @(
    (New-PointF (P 128) (P 50)),
    (New-PointF (P 190) (P 74)),
    (New-PointF (P 178) (P 156)),
    (New-PointF (P 128) (P 204)),
    (New-PointF (P 78) (P 156)),
    (New-PointF (P 66) (P 74))
  )
  $shieldBrush = [System.Drawing.Drawing2D.LinearGradientBrush]::new(
    (New-PointF (P 86) (P 56)),
    (New-PointF (P 172) (P 204)),
    [System.Drawing.ColorTranslator]::FromHtml('#367747'),
    [System.Drawing.ColorTranslator]::FromHtml('#235332'))
  $g.FillPath($shieldBrush, $shieldPath)
  $shieldPen = [System.Drawing.Pen]::new([System.Drawing.ColorTranslator]::FromHtml('#8A7432'), (P 7))
  $shieldPen.LineJoin = [System.Drawing.Drawing2D.LineJoin]::Round
  $g.DrawPath($shieldPen, $shieldPath)

  if ($Size -ge 32) {
    $innerShield = [System.Drawing.Drawing2D.GraphicsPath]::new()
    Add-PolygonPath $innerShield @(
      (New-PointF (P 128) (P 64)),
      (New-PointF (P 176) (P 82)),
      (New-PointF (P 166) (P 149)),
      (New-PointF (P 128) (P 186)),
      (New-PointF (P 90) (P 149)),
      (New-PointF (P 80) (P 82))
    )
    $innerShieldPen = [System.Drawing.Pen]::new([System.Drawing.Color]::FromArgb(46, [System.Drawing.ColorTranslator]::FromHtml('#E6E2D8')), (P 4))
    $innerShieldPen.LineJoin = [System.Drawing.Drawing2D.LineJoin]::Round
    $g.DrawPath($innerShieldPen, $innerShield)
    $innerShield.Dispose()
    $innerShieldPen.Dispose()
  }

  $tagPath = [System.Drawing.Drawing2D.GraphicsPath]::new()
  Add-PolygonPath $tagPath @(
    (New-PointF (P 70) (P 105)),
    (New-PointF (P 116) (P 80)),
    (New-PointF (P 180) (P 94)),
    (New-PointF (P 169) (P 151)),
    (New-PointF (P 106) (P 162)),
    (New-PointF (P 67) (P 132))
  )
  $tagBrush = [System.Drawing.SolidBrush]::new([System.Drawing.ColorTranslator]::FromHtml('#171B19'))
  $tagPen = [System.Drawing.Pen]::new([System.Drawing.ColorTranslator]::FromHtml('#8A7432'), (P 8))
  $tagPen.LineJoin = [System.Drawing.Drawing2D.LineJoin]::Round
  $g.FillPath($tagBrush, $tagPath)
  $g.DrawPath($tagPen, $tagPath)

  $holeBrush = [System.Drawing.SolidBrush]::new([System.Drawing.ColorTranslator]::FromHtml('#8A7432'))
  $g.FillEllipse($holeBrush, (P 97), (P 101), (P 14), (P 14))

  $fontFamily = [System.Drawing.FontFamily]::new('Segoe UI')
  $percentPath = [System.Drawing.Drawing2D.GraphicsPath]::new()
  $fontSize = P 62
  $percentPath.AddString('%', $fontFamily, [int][System.Drawing.FontStyle]::Bold, $fontSize, (New-PointF (P 92) (P 88)), [System.Drawing.StringFormat]::GenericDefault)
  $percentBrush = [System.Drawing.SolidBrush]::new([System.Drawing.ColorTranslator]::FromHtml('#E6E2D8'))
  $g.FillPath($percentBrush, $percentPath)

  $cardRect = [System.Drawing.RectangleF]::new((P 143), (P 134), (P 56), (P 54))
  $cardPath = New-RoundedRectPath $cardRect (P 11)
  $cardBrush = [System.Drawing.SolidBrush]::new([System.Drawing.ColorTranslator]::FromHtml('#202620'))
  $cardPen = [System.Drawing.Pen]::new([System.Drawing.ColorTranslator]::FromHtml('#4E472F'), (P 5))
  $g.FillPath($cardBrush, $cardPath)
  $g.DrawPath($cardPen, $cardPath)

  $checkPen = [System.Drawing.Pen]::new([System.Drawing.ColorTranslator]::FromHtml('#8A7432'), (P 6))
  $checkPen.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
  $checkPen.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
  $checkPen.LineJoin = [System.Drawing.Drawing2D.LineJoin]::Round
  $g.DrawLines($checkPen, @(
    (New-PointF (P 154) (P 150)),
    (New-PointF (P 159) (P 155)),
    (New-PointF (P 168) (P 144))
  ))

  $linePen = [System.Drawing.Pen]::new([System.Drawing.ColorTranslator]::FromHtml('#E6E2D8'), (P 6))
  $linePen.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
  $linePen.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
  $g.DrawLine($linePen, (P 174), (P 150), (P 188), (P 150))
  $g.DrawLine($linePen, (P 154), (P 169), (P 188), (P 169))

  $final = [System.Drawing.Bitmap]::new($Size, $Size, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
  $fg = [System.Drawing.Graphics]::FromImage($final)
  $fg.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
  $fg.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
  $fg.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
  $fg.Clear([System.Drawing.Color]::Transparent)
  $fg.DrawImage($bitmap, 0, 0, $Size, $Size)
  $final.Save($OutPath, [System.Drawing.Imaging.ImageFormat]::Png)

  $fg.Dispose()
  $final.Dispose()
  $linePen.Dispose()
  $checkPen.Dispose()
  $cardPen.Dispose()
  $cardBrush.Dispose()
  $cardPath.Dispose()
  $percentBrush.Dispose()
  $percentPath.Dispose()
  $fontFamily.Dispose()
  $holeBrush.Dispose()
  $tagPen.Dispose()
  $tagBrush.Dispose()
  $tagPath.Dispose()
  $shieldPen.Dispose()
  $shieldBrush.Dispose()
  $shieldPath.Dispose()
  $innerPen.Dispose()
  $innerPath.Dispose()
  $basePen.Dispose()
  $baseBrush.Dispose()
  $basePath.Dispose()
  $g.Dispose()
  $bitmap.Dispose()
}

function Write-Ico {
  param(
    [string[]]$PngPaths,
    [string]$OutPath
  )

  $images = foreach ($path in $PngPaths) {
    $bytes = [System.IO.File]::ReadAllBytes($path)
    $name = [System.IO.Path]::GetFileNameWithoutExtension($path)
    $size = [int]($name -replace '^app-icon-', '')
    [pscustomobject]@{ Size = $size; Bytes = $bytes }
  }

  $fs = [System.IO.File]::Open($OutPath, [System.IO.FileMode]::Create, [System.IO.FileAccess]::Write)
  $bw = [System.IO.BinaryWriter]::new($fs)
  try {
    $bw.Write([UInt16]0)
    $bw.Write([UInt16]1)
    $bw.Write([UInt16]$images.Count)
    $offset = 6 + (16 * $images.Count)
    foreach ($image in $images) {
      $dimension = if ($image.Size -eq 256) { 0 } else { $image.Size }
      $bw.Write([byte]$dimension)
      $bw.Write([byte]$dimension)
      $bw.Write([byte]0)
      $bw.Write([byte]0)
      $bw.Write([UInt16]1)
      $bw.Write([UInt16]32)
      $bw.Write([UInt32]$image.Bytes.Length)
      $bw.Write([UInt32]$offset)
      $offset += $image.Bytes.Length
    }
    foreach ($image in $images) {
      $bw.Write($image.Bytes)
    }
  }
  finally {
    $bw.Dispose()
    $fs.Dispose()
  }
}

New-Item -ItemType Directory -Force -Path $IconDir | Out-Null
$pngPaths = foreach ($size in $Sizes) {
  $path = Join-Path $IconDir "app-icon-$size.png"
  Draw-AppIconPng -Size $size -OutPath $path
  $path
}

$icoPath = Join-Path $IconDir 'app.ico'
Write-Ico -PngPaths $pngPaths -OutPath $icoPath

[pscustomobject]@{
  Ico = $icoPath
  PngSizes = ($Sizes -join ',')
  Source = (Join-Path $IconDir 'app-icon.svg')
}
