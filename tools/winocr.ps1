<#
.SYNOPSIS
    Распознавание текста интерфейса Kenshi штатным Windows OCR (WinRT), без внешних пакетов; нужны языки Windows ru и en-US.

.DESCRIPTION
    Читает пути кадров из ListFile (UTF-8, по одному в строке), пишет в OutFile JSONL:
    {"path","width","height","regions":{"name":[{"text","x","y","w","h"}]}}.
    Координаты строк приведены к кадру. Кадры только читаются, кропы существуют лишь в памяти.
    Русская модель читает цифры дня как буквы («130» как «ВО»), поэтому без прочитанного дня полоса подписи
    перечитывается английской моделью: она не знает кириллицы и выдаёт цифры (регион day_strip).
#>
param(
    [Parameter(Mandatory = $true)][string]$ListFile,
    [Parameter(Mandatory = $true)][string]$OutFile
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing, System.Runtime.WindowsRuntime
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Foundation, ContentType = WindowsRuntime]

$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    })[0]
$FrameTimeoutMs = 30000
function Wait-Async($operation, [type]$type) {
    $task = $asTask.MakeGenericMethod($type).Invoke($null, @($operation))
    # Без предела один зависший кадр остановил бы весь пакет.
    if (-not $task.Wait($FrameTimeoutMs)) { throw 'Windows OCR: превышено время ожидания кадра.' }
    $task.Result
}

$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new('ru'))
if (-not $engine) { throw 'Windows OCR: русский язык не установлен.' }
$digitEngine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new('en-US'))

# Доли кадра: панели интерфейса в разных оформлениях стоят по-разному, но не выходят за эти границы.
$Regions = [ordered]@{
    hud  = @(0.55, 0.66, 0.45, 0.34)
    left = @(0.00, 0.53, 0.22, 0.18)
}
# Высота шрифта после масштабирования одинакова для всех разрешений: ширина кадра приводится к этому значению.
$TargetWidth = 4320
$DayValue = '[\u0414\u0434]\u0435\u043d\S*\s*\d'
# Отрицание отсекает соседнюю подпись «Деньги».
$DayLabel = '^[Дд]?ен(?!ьг)'
# Полоса подписи в долях кадра: подпись и до четырёх цифр.
$StripWidth = 0.06; $StripHeight = 0.0245

function Read-Pixels([Drawing.Bitmap]$bitmap, [int]$x, [int]$y, [int]$w, [int]$h, [double]$scale, $reader) {
    if ($x + $w -gt $bitmap.Width) { $w = $bitmap.Width - $x }
    if ($y + $h -gt $bitmap.Height) { $h = $bitmap.Height - $y }
    $crop = New-Object Drawing.Bitmap ([int]($w * $scale)), ([int]($h * $scale))
    $graphics = [Drawing.Graphics]::FromImage($crop)
    $graphics.InterpolationMode = 'HighQualityBicubic'
    $graphics.DrawImage($bitmap, (New-Object Drawing.Rectangle 0, 0, $crop.Width, $crop.Height), (New-Object Drawing.Rectangle $x, $y, $w, $h), 'Pixel')
    $graphics.Dispose()
    $stream = New-Object IO.MemoryStream
    $crop.Save($stream, [Drawing.Imaging.ImageFormat]::Png)
    $crop.Dispose()
    $stream.Position = 0
    $random = [System.IO.WindowsRuntimeStreamExtensions]::AsRandomAccessStream($stream)
    $decoder = Wait-Async ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($random)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $soft = Wait-Async ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $result = Wait-Async ($reader.RecognizeAsync($soft)) ([Windows.Media.Ocr.OcrResult])
    $lines = @()
    foreach ($line in $result.Lines) {
        $rects = @($line.Words | ForEach-Object { $_.BoundingRect })
        $left = ($rects | ForEach-Object { $_.X } | Measure-Object -Minimum).Minimum
        $top = ($rects | ForEach-Object { $_.Y } | Measure-Object -Minimum).Minimum
        $right = ($rects | ForEach-Object { $_.X + $_.Width } | Measure-Object -Maximum).Maximum
        $bottom = ($rects | ForEach-Object { $_.Y + $_.Height } | Measure-Object -Maximum).Maximum
        $lines += [ordered]@{
            text = $line.Text
            x = [int]($x + $left / $scale); y = [int]($y + $top / $scale)
            w = [int](($right - $left) / $scale); h = [int](($bottom - $top) / $scale)
        }
    }
    , $lines
}

$encoding = New-Object Text.UTF8Encoding($false)
$writer = New-Object IO.StreamWriter($OutFile, $false, $encoding)
# Готовые строки должны пережить падение процесса: вызывающий код принимает частичный результат.
$writer.AutoFlush = $true
try {
    foreach ($path in [IO.File]::ReadAllLines($ListFile, $encoding)) {
        if (-not $path) { continue }
        $record = [ordered]@{ path = $path }
        try {
            $bitmap = [Drawing.Bitmap]::FromFile($path)
            try {
                $record.width = $bitmap.Width; $record.height = $bitmap.Height
                $found = [ordered]@{}
                $scale = $TargetWidth / $bitmap.Width
                foreach ($name in $Regions.Keys) {
                    $box = $Regions[$name]
                    $found[$name] = Read-Pixels $bitmap ($bitmap.Width * $box[0]) ($bitmap.Height * $box[1]) ($bitmap.Width * $box[2]) ($bitmap.Height * $box[3]) $scale $engine
                }
                $label = @($found.hud | Where-Object { $_.text -match $DayLabel }) | Select-Object -First 1
                if ($digitEngine -and $label -and $label.text -notmatch $DayValue) {
                    $top = $label.y - [int]($bitmap.Height * 0.0045)
                    foreach ($zoom in 2, 4) {
                        $strip = Read-Pixels $bitmap $label.x $top ($bitmap.Width * $StripWidth) ($bitmap.Height * $StripHeight) $zoom $digitEngine
                        if (@($strip | ForEach-Object { $_.text }) -join ' ' -match '\d') { $found.day_strip = $strip; break }
                    }
                }
                $record.regions = $found
            }
            finally { $bitmap.Dispose() }
        }
        catch { $record.error = $_.Exception.Message }
        $writer.WriteLine(($record | ConvertTo-Json -Depth 8 -Compress))
    }
}
finally { $writer.Dispose() }
