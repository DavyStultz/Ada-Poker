$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
Add-Type -AssemblyName System.Drawing
[Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime] > $null
[Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime] > $null
[Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime] > $null
$asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetGenericArguments().Count -eq 1 -and $_.GetParameters().Count -eq 1 } | Select-Object -First 1
function Await($operation, [Type]$type) {
    $task = $asTask.MakeGenericMethod($type).Invoke($null, @($operation))
    if (-not $task.Wait(30000)) { throw 'Windows OCR timed out.' }
    $task.Result
}
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime]::new('en-US'))
if ($null -eq $engine) { throw 'Windows English OCR is unavailable. Install the English language OCR feature in Windows Settings.' }
while ($null -ne ($inputLine = [Console]::ReadLine())) {
    $stream = $bitmap = $drawing = $null
    try {
        $path = $inputLine | ConvertFrom-Json
        $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($path)) ([Windows.Storage.StorageFile])
        $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
        $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
        $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
        $words = @($result.Lines | ForEach-Object { $_.Words | ForEach-Object {
            @{text=$_.Text; x=$_.BoundingRect.X; y=$_.BoundingRect.Y; width=$_.BoundingRect.Width; height=$_.BoundingRect.Height}
        } })
        $drawing = [System.Drawing.Bitmap]::new($path)
        $buttons = @(@(410,310),@(1008,310),@(1216,550),@(996,702),@(798,706),@(410,703),@(202,550))
        $gold = @(foreach ($point in $buttons) {
            $count = 0
            for ($dx=-18; $dx -le 18; $dx+=3) {
                for ($dy=-18; $dy -le 18; $dy+=3) {
                    $x = [int](($point[0]+$dx)*$drawing.Width/1418)
                    $y = [int](($point[1]+$dy)*$drawing.Height/1024)
                    $c = $drawing.GetPixel($x,$y)
                    if ($c.R -gt 140 -and $c.G -gt 90 -and $c.R -gt $c.G*1.08 -and $c.G -gt $c.B*1.35) { $count++ }
                }
            }
            $count
        })
        $green = 0
        foreach ($point in @(@(600,480),@(700,550),@(850,600),@(600,650),@(850,480))) {
            $c = $drawing.GetPixel([int]($point[0]*$drawing.Width/1418),[int]($point[1]*$drawing.Height/1024))
            if ($c.G -gt $c.R*1.25 -and $c.G -gt $c.B*1.1 -and $c.G -gt 35) { $green++ }
        }
        $response = @{path=$path; words=$words; width=$drawing.Width; height=$drawing.Height; dealer_scores=$gold; felt_points=$green}
        [Console]::WriteLine(($response | ConvertTo-Json -Compress -Depth 6))
    } catch {
        [Console]::WriteLine((@{path=$path;error=$_.Exception.Message} | ConvertTo-Json -Compress))
    } finally {
        if ($null -ne $drawing) { $drawing.Dispose() }
        if ($null -ne $bitmap) { $bitmap.Dispose() }
        if ($null -ne $stream) { $stream.Dispose() }
    }
}
