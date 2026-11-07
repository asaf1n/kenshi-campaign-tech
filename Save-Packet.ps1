<#
.SYNOPSIS
    Буфер обмена -> пакет в tmp/inbox Google Диска -> "ingest-inbox" -> отчёт (принят с SHA либо отклонён с причиной).

.DESCRIPTION
    Берёт текст из буфера обмена (Get-Clipboard -Raw), снимает внешнее обрамление кода (первая строка ```, ```markdown
    или ```md и последняя ```), приводит переводы строк к LF и пишет пакет в tmp/inbox каталога Диска в UTF-8 без BOM.
    Каталог Диска определяется как в ingest-inbox: KENSHI_GDRIVE_DIR либо смонтированный G:
    (tools.config.find_gdrive_root). Затем запускается "python -m tools.campaign ingest-inbox".

    Формат буфера и имя файла пакета:
      - в тексте есть маркеры пакета (=== ФАЙЛ: ... ===, === ВЫГРУЗКА ... ===): записывается как есть;
      - маркеров нет, но в первых 40 строках (frontmatter или заголовок) есть идентификатор сессии S-YYYYMMDD-N:
        текст оборачивается в блок === ВЫГРУЗКА S-YYYYMMDD-N === и принимается как Campaign/<campaign>/archive/sessions/S-YYYYMMDD-N.md
        (кампания - поле campaign: во frontmatter; без него приём отклоняется);
      - маркеров и идентификатора нет: ошибка, файл не пишется (путь назначения неизвестен).
    Имя файла во входящих: S-YYYYMMDD-N.md, если идентификатор найден, иначе packet-<UTC>.md. Если такое имя уже занято,
    добавляется суффикс -<UTC>.

    Защиты ingest-inbox (файл короче версии в main более чем на 20 %, потеря заголовков "##", ссылки-обёртки
    google.com/search|url и абсолютные URL на файлы Kenshi-Compain, S-* без frontmatter с campaign) отклоняют пакет;
    отклонённый защитой пакет удаляется из tmp/inbox (буфер обмена остаётся источником), повтор - с -Accept <файл>.
    Пакет, отклонённый из-за сбоя git (например, push), остаётся во входящих: повторный запуск ingest-inbox доведёт его.
    После принятия ingest-inbox синхронизирует Campaign/ на Диск; недоступный Диск или Google API - предупреждение.

    Код выхода: 0 - пакет принят (или с -NoPush закоммичен без отправки), 1 - отклонён, пуст или ошибка.

.PARAMETER Accept
    Файл(ы) пакета, для которых снимаются защиты (путь, окончание пути или имя файла); передаётся как --accept.

.PARAMETER NoPush
    Закоммитить в клон Kenshi-Compain без push (--no-push): пакет остаётся во входящих, Диск не синхронизируется.
#>
param(
    [string[]]$Accept = @(),
    [switch]$NoPush
)
$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$env:PYTHONWARNINGS = 'ignore::FutureWarning'

# stderr native-команды в PowerShell 5.1 при 'Stop' становится исключением (NativeCommandError).
# Вызов идёт с 'Continue' и 2>&1; stdout и stderr разделяются по типу записи, код берётся из $LASTEXITCODE.
function Invoke-Python([string[]]$PyArgs) {
    $ErrorActionPreference = 'Continue'
    $all = @(& python @PyArgs 2>&1)
    $code = $LASTEXITCODE
    $out = @($all | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] } | ForEach-Object { "$_" })
    $err = @($all | Where-Object { $_ -is [System.Management.Automation.ErrorRecord] } | ForEach-Object { "$_".TrimEnd() } |
        Where-Object { $_ -and $_ -notmatch 'FutureWarning|^\s*warnings\.warn\(' })
    [pscustomobject]@{ Code = $code; Out = $out; Err = $err }
}

$Root = $PSScriptRoot
Push-Location $Root
try {
    $text = Get-Clipboard -Raw
    if ([string]::IsNullOrWhiteSpace($text)) { throw 'Буфер обмена пуст.' }
    $text = ($text -replace "`r`n", "`n" -replace "`r", "`n").Trim("`n") + "`n"

    # Внешнее обрамление: открывающая ```, ```markdown или ```md и закрывающая ``` (внутренние блоки кода не трогаются).
    $lines = [System.Collections.Generic.List[string]]($text.TrimEnd("`n") -split "`n")
    if ($lines.Count -ge 2 -and $lines[0].Trim() -match '^```(markdown|md)?$' -and $lines[$lines.Count - 1].Trim() -eq '```') {
        $lines.RemoveAt($lines.Count - 1)
        $lines.RemoveAt(0)
        $text = ($lines -join "`n").Trim("`n") + "`n"
    }

    $sessionId = $null
    $head = (($text -split "`n") | Select-Object -First 40) -join "`n"
    if ($head -match '(S-\d{8}-\d+)') { $sessionId = $Matches[1] }
    $hasMarkers = $text -match '(?m)^=== (ФАЙЛ: .+|ВЫГРУЗКА S-\d{8}-\d+) ===\s*$'
    if (-not $hasMarkers) {
        if (-not $sessionId) { throw 'В буфере нет маркеров пакета (=== ФАЙЛ: ... ===, === ВЫГРУЗКА ... ===) и идентификатора сессии S-YYYYMMDD-N: путь назначения неизвестен.' }
        $text = "=== ВЫГРУЗКА $sessionId ===`n" + $text.TrimEnd("`n") + "`n=== КОНЕЦ ===`n"
    }

    $driveRun = Invoke-Python @('-c', "from tools.config import find_gdrive_root; print(find_gdrive_root() or '')")
    $driveOut = @($driveRun.Out)
    $driveExit = $driveRun.Code
    $driveRoot = if ($driveOut.Count -gt 0) { "$($driveOut[0])".Trim() } else { '' }
    if ($driveExit -ne 0) { throw 'Не удалось определить каталог Диска (python -c ...).' }
    if (-not $driveRoot -or -not (Test-Path -LiteralPath $driveRoot)) { throw 'Каталог Google Диска не найден: задайте KENSHI_GDRIVE_DIR или смонтируйте G:.' }
    $inbox = Join-Path $driveRoot 'tmp\inbox'
    New-Item -ItemType Directory -Force -Path $inbox | Out-Null

    $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
    $base = if ($sessionId) { $sessionId } else { "packet-$stamp" }
    $name = "$base.md"
    if (Test-Path -LiteralPath (Join-Path $inbox $name)) { $name = "$base-$stamp.md" }
    $packetPath = Join-Path $inbox $name
    [System.IO.File]::WriteAllText($packetPath, $text, (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "Пакет записан: $packetPath"

    $ingestArgs = @()
    foreach ($file in $Accept) { if ($file) { $ingestArgs += @('--accept', $file) } }
    if ($NoPush) { $ingestArgs += '--no-push' }
    # stderr (WARNING) выводится предупреждениями; stdout - JSON отчёта.
    $run = Invoke-Python (@('-m', 'tools.campaign', 'ingest-inbox', '--gdrive-dir', $driveRoot) + $ingestArgs)
    $raw = $run.Out
    $code = $run.Code
    $run.Err | ForEach-Object { Write-Warning ($_ -replace '^WARNING:\s*', '') }
    $report = $null
    try { $report = ($raw -join "`n") | ConvertFrom-Json } catch { }
    if (-not $report) { throw "ingest-inbox завершился с кодом $code без отчёта." }

    $mine = @($report.processed | Where-Object { $_.packet -eq $name })
    $rejected = @($report.rejected | Where-Object { $_.packet -eq $name })
    $pending = @($report.committed_not_pushed | Where-Object { $_.packet -eq $name })
    $others = @($report.processed).Count - $mine.Count
    $otherRejected = @($report.rejected).Count - $rejected.Count
    if ($others -gt 0 -or $otherRejected -gt 0) { Write-Host "Другие пакеты во входящих: принято $others, отклонено $otherRejected (см. отчёт ingest-inbox)." }

    $exit = 1
    if ($mine.Count -gt 0) {
        Write-Host "принят, SHA $($mine[0].commit)"
        Write-Host ("  файлы: " + (@($mine[0].applied) -join ', '))
        foreach ($g in @($mine[0].guards)) { Write-Host ("  защита снята --accept: {0}: {1}" -f $g.path, (@($g.reasons) -join '; ')) }
        $drive = $report.drive_sync
        if ($drive -and $drive.status -ne 'synced') { Write-Warning "Диск: $($drive.status) ($($drive.error)); приём выполнен." }
        elseif ($drive) { Write-Host 'Диск: Campaign/ синхронизирован.' }
        $exit = 0
    }
    elseif ($pending.Count -gt 0) {
        Write-Host "закоммичен без push, SHA $($pending[0].commit); пакет остаётся в tmp/inbox ($name)"
        $exit = 0
    }
    elseif ($rejected.Count -gt 0) {
        $item = $rejected[0]
        if ($item.files) {
            foreach ($f in @($item.files)) {
                $mark = if ($f.accepted) { 'защита снята' } else { 'отклонён' }
                Write-Host ("отклонён: {0} [{1}]: {2}" -f $f.path, $mark, (@($f.reasons) -join '; '))
            }
            Remove-Item -LiteralPath $packetPath -Force
            Write-Host 'Пакет удалён из tmp/inbox. Повтор: Save-Packet.ps1 -Accept <файл>.'
        }
        else {
            Write-Host "отклонён: $($item.reason)"
            Write-Host "Пакет остаётся в tmp/inbox ($name); повторный запуск ingest-inbox доведёт его."
        }
    }
    elseif ($report.error) { Write-Host "ошибка приёма: $($report.error)" }
    elseif (@($report.ignored) -contains $name) {
        Remove-Item -LiteralPath $packetPath -Force
        Write-Host 'отклонён: в пакете нет применимых блоков; файл удалён из tmp/inbox.'
    }
    else { Write-Host "отклонён: пакет $name не обработан (код ingest-inbox $code)." }
    exit $exit
}
finally {
    Pop-Location
}
