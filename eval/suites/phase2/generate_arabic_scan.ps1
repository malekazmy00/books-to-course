param([Parameter(Mandatory=$true)][string]$OutputPath)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$linesB64 = @('2KfZhNin2K3YqtmF2KfZhCDZh9mIINmF2YLZitin2LMg2YTZgdix2LXYqSDZiNmC2YjYuSDYrdiv2Ksg2YXYudmK2YYu','2KrZg9mI2YYg2YLZitmF2Kkg2KfZhNin2K3YqtmF2KfZhCDYqNmK2YYg2LXZgdixINmI2YjYp9it2K8u','2KXYsNinINmD2KfZhiDYp9mE2K3Yr9irINmF2KTZg9iv2KfZiyDZgdil2YYg2KfYrdiq2YXYp9mE2Ycg2YrYs9in2YjZiiDZiNin2K3Yr9in2Ysu')
$lines = $linesB64 | ForEach-Object { [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($_)) }
$bitmap = [System.Drawing.Bitmap]::new(2550,3300)
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$graphics.Clear([System.Drawing.Color]::White)
$graphics.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit
$font = [System.Drawing.Font]::new('Arial', 62, [System.Drawing.FontStyle]::Regular, [System.Drawing.GraphicsUnit]::Pixel)
$brush = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::Black)
$format = [System.Drawing.StringFormat]::new()
$format.FormatFlags = [System.Drawing.StringFormatFlags]::DirectionRightToLeft
$y = 520
foreach ($line in $lines) {
    $rect = [System.Drawing.RectangleF]::new(170, $y, 2150, 150)
    $graphics.DrawString($line, $font, $brush, $rect, $format)
    $y += 220
}
$bitmap.Save($OutputPath, [System.Drawing.Imaging.ImageFormat]::Png)
$graphics.Dispose()
$font.Dispose()
$brush.Dispose()
$format.Dispose()
$bitmap.Dispose()
Write-Output $OutputPath

