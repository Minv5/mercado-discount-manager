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

function New-LinearBrush {
  param(
    [float]$X1,
    [float]$Y1,
    [float]$X2,
    [float]$Y2,
    [string]$Start,
    [string]$End
  )
  return [System.Drawing.Drawing2D.LinearGradientBrush]::new(
    (New-PointF $X1 $Y1),
    (New-PointF $X2 $Y2),
    [System.Drawing.ColorTranslator]::FromHtml($Start),
    [System.Drawing.ColorTranslator]::FromHtml($End))
}

function Draw-ShadowPath {
  param(
    [System.Drawing.Graphics]$Graphics,
    [System.Drawing.Drawing2D.GraphicsPath]$Path,
    [System.Drawing.Brush]$Brush,
    [float]$Dx,
    [float]$Dy,
    [int]$Alpha
  )
  $matrix = [System.Drawing.Drawing2D.Matrix]::new()
  $shadowPath = $Path.Clone()
  $matrix.Translate($Dx, $Dy)
  $shadowPath.Transform($matrix)
  $shadowBrush = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::FromArgb($Alpha, 0, 0, 0))
  $Graphics.FillPath($shadowBrush, $shadowPath)
  $Graphics.FillPath($Brush, $Path)
  $shadowBrush.Dispose()
  $shadowPath.Dispose()
  $matrix.Dispose()
}

function Draw-RoundedRect {
  param(
    [System.Drawing.Graphics]$Graphics,
    [System.Drawing.RectangleF]$Rect,
    [float]$Radius,
    [System.Drawing.Brush]$Brush,
    [System.Drawing.Pen]$Pen,
    [float]$ShadowX,
    [float]$ShadowY,
    [int]$ShadowAlpha
  )
  $path = New-RoundedRectPath $Rect $Radius
  if ($ShadowAlpha -gt 0) {
    Draw-ShadowPath $Graphics $path $Brush $ShadowX $ShadowY $ShadowAlpha
  }
  else {
    $Graphics.FillPath($Brush, $path)
  }
  if ($null -ne $Pen) {
    $Graphics.DrawPath($Pen, $path)
  }
  $path.Dispose()
}

function Draw-AppIconV2Png {
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

  $baseRect = [System.Drawing.RectangleF]::new((P 16), (P 16), (P 224), (P 224))
  $basePath = New-RoundedRectPath $baseRect (P 48)
  $baseBrush = New-LinearBrush (P 36) (P 18) (P 220) (P 238) '#FFE34A' '#E6A900'
  $baseShadow = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::FromArgb(60, 0, 0, 0))
  $shadowRect = [System.Drawing.RectangleF]::new((P 18), (P 22), (P 220), (P 218))
  $shadowPath = New-RoundedRectPath $shadowRect (P 46)
  $g.FillPath($baseShadow, $shadowPath)
  $g.FillPath($baseBrush, $basePath)
  $basePen = [System.Drawing.Pen]::new([System.Drawing.Color]::FromArgb(92, [System.Drawing.ColorTranslator]::FromHtml('#C58F00')), (P 5))
  $g.DrawPath($basePen, $basePath)
  if ($Size -ge 32) {
    $highlightRect = [System.Drawing.RectangleF]::new((P 23), (P 23), (P 210), (P 210))
    $highlightPath = New-RoundedRectPath $highlightRect (P 42)
    $highlightPen = [System.Drawing.Pen]::new([System.Drawing.Color]::FromArgb(130, [System.Drawing.ColorTranslator]::FromHtml('#FFF07A')), (P 3))
    $g.DrawPath($highlightPen, $highlightPath)
    $highlightPen.Dispose()
    $highlightPath.Dispose()
  }

  $darkBrush = New-LinearBrush (P 42) (P 70) (P 206) (P 184) '#444846' '#171B19'
  $darkPen = [System.Drawing.Pen]::new([System.Drawing.ColorTranslator]::FromHtml('#202420'), (P 2))
  $cardBrush = New-LinearBrush (P 72) (P 74) (P 186) (P 170) '#4A4F4C' '#2C302D'
  $cardPen = [System.Drawing.Pen]::new([System.Drawing.ColorTranslator]::FromHtml('#5B605D'), (P 2))

  Draw-RoundedRect $g ([System.Drawing.RectangleF]::new((P 44), (P 72), (P 20), (P 112))) (P 10) $darkBrush $darkPen (P 2) (P 4) 55
  Draw-RoundedRect $g ([System.Drawing.RectangleF]::new((P 194), (P 72), (P 20), (P 112))) (P 10) $darkBrush $darkPen (P 2) (P 4) 55
  Draw-RoundedRect $g ([System.Drawing.RectangleF]::new((P 58), (P 105), (P 148), (P 16))) (P 5) $darkBrush $null (P 2) (P 3) 50
  Draw-RoundedRect $g ([System.Drawing.RectangleF]::new((P 58), (P 164), (P 148), (P 16))) (P 5) $darkBrush $null (P 2) (P 3) 50

  if ($Size -ge 24) {
    Draw-RoundedRect $g ([System.Drawing.RectangleF]::new((P 85), (P 78), (P 46), (P 30))) (P 7) $cardBrush $cardPen (P 2) (P 3) 45
    Draw-RoundedRect $g ([System.Drawing.RectangleF]::new((P 145), (P 89), (P 27), (P 20))) (P 6) $cardBrush $cardPen (P 2) (P 3) 45
    Draw-RoundedRect $g ([System.Drawing.RectangleF]::new((P 75), (P 139), (P 34), (P 27))) (P 7) $cardBrush $cardPen (P 2) (P 3) 45
    Draw-RoundedRect $g ([System.Drawing.RectangleF]::new((P 150), (P 136), (P 43), (P 31))) (P 8) $cardBrush $cardPen (P 2) (P 3) 45
  }

  $tagPath = [System.Drawing.Drawing2D.GraphicsPath]::new()
  Add-PolygonPath $tagPath @(
    (New-PointF (P 57) (P 61)),
    (New-PointF (P 92) (P 49)),
    (New-PointF (P 126) (P 58)),
    (New-PointF (P 121) (P 89)),
    (New-PointF (P 86) (P 98)),
    (New-PointF (P 55) (P 81))
  )
  $tagBrush = [System.Drawing.SolidBrush]::new([System.Drawing.ColorTranslator]::FromHtml('#171B19'))
  $tagPen = [System.Drawing.Pen]::new([System.Drawing.ColorTranslator]::FromHtml('#3D413F'), (P 5))
  $tagPen.LineJoin = [System.Drawing.Drawing2D.LineJoin]::Round
  Draw-ShadowPath $g $tagPath $tagBrush (P 2) (P 4) 55
  $g.DrawPath($tagPen, $tagPath)
  if ($Size -ge 24) {
    $holeBrush = [System.Drawing.SolidBrush]::new([System.Drawing.ColorTranslator]::FromHtml('#FFD219'))
    $g.FillEllipse($holeBrush, (P 79), (P 61), (P 9), (P 9))
    $fontFamily = [System.Drawing.FontFamily]::new('Segoe UI')
    $percentPath = [System.Drawing.Drawing2D.GraphicsPath]::new()
    $percentPath.AddString('%', $fontFamily, [int][System.Drawing.FontStyle]::Bold, (P 31), (New-PointF (P 76) (P 60)), [System.Drawing.StringFormat]::GenericDefault)
    $percentBrush = [System.Drawing.SolidBrush]::new([System.Drawing.ColorTranslator]::FromHtml('#FFE34A'))
    $g.FillPath($percentBrush, $percentPath)
    $percentBrush.Dispose()
    $percentPath.Dispose()
    $fontFamily.Dispose()
    $holeBrush.Dispose()
  }

  $checkShadow = [System.Drawing.Pen]::new([System.Drawing.Color]::FromArgb(95, 43, 75, 23), (P 34))
  $checkShadow.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
  $checkShadow.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
  $checkShadow.LineJoin = [System.Drawing.Drawing2D.LineJoin]::Round
  $g.DrawLines($checkShadow, @(
    (New-PointF (P 77) (P 139)),
    (New-PointF (P 112) (P 174)),
    (New-PointF (P 184) (P 94))
  ))

  $checkPen = [System.Drawing.Pen]::new([System.Drawing.ColorTranslator]::FromHtml('#62BD22'), (P 30))
  $checkPen.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
  $checkPen.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
  $checkPen.LineJoin = [System.Drawing.Drawing2D.LineJoin]::Round
  $g.DrawLines($checkPen, @(
    (New-PointF (P 76) (P 135)),
    (New-PointF (P 112) (P 171)),
    (New-PointF (P 184) (P 90))
  ))

  $checkHighlight = [System.Drawing.Pen]::new([System.Drawing.Color]::FromArgb(150, [System.Drawing.ColorTranslator]::FromHtml('#B6EA4A')), (P 7))
  $checkHighlight.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
  $checkHighlight.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
  $checkHighlight.LineJoin = [System.Drawing.Drawing2D.LineJoin]::Round
  if ($Size -ge 32) {
    $g.DrawLines($checkHighlight, @(
      (New-PointF (P 76) (P 124)),
      (New-PointF (P 112) (P 160)),
      (New-PointF (P 184) (P 79))
    ))
  }

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
  $checkHighlight.Dispose()
  $checkPen.Dispose()
  $checkShadow.Dispose()
  $tagPen.Dispose()
  $tagBrush.Dispose()
  $tagPath.Dispose()
  $cardPen.Dispose()
  $cardBrush.Dispose()
  $darkPen.Dispose()
  $darkBrush.Dispose()
  $basePen.Dispose()
  $baseBrush.Dispose()
  $baseShadow.Dispose()
  $shadowPath.Dispose()
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
    $size = [int]($name -replace '^app-icon-v2-', '')
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
  $path = Join-Path $IconDir "app-icon-v2-$size.png"
  Draw-AppIconV2Png -Size $size -OutPath $path
  $path
}

$icoPath = Join-Path $IconDir 'app.ico'
Write-Ico -PngPaths $pngPaths -OutPath $icoPath

[pscustomobject]@{
  Ico = $icoPath
  PngSizes = ($Sizes -join ',')
  Source = (Join-Path $IconDir 'app-icon-v2.svg')
}
