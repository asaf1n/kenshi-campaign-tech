<#
.SYNOPSIS
    Запуск конвейера kenshi-campaign-tech одной командой.

.DESCRIPTION
    Задаёт переменные окружения Kenshi, берёт самое свежее сохранение, выполняет pipeline
    (разбор сохранения и скриншотов -> published/) и доставляет результат в репозиторий
    Kenshi Compain (KENSHI_COMPANION_DIR или соседний каталог "Kenshi Compain"): commit и push в GitHub.
    campaign_id и branch_id берутся из последнего published/manifest.json.
    Сохранения и скриншоты игры открываются только на чтение; git вызывает только доставка в Kenshi Compain.
#>
$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

function Find-KenshiDir {
    $candidates = @($env:KENSHI_DIR)
    foreach ($key in 'HKLM:\SOFTWARE\WOW6432Node\Valve\Steam', 'HKCU:\SOFTWARE\Valve\Steam') {
        $props = $null
        try { $props = Get-ItemProperty -Path $key -ErrorAction Stop } catch { }
        $steam = if ($props.InstallPath) { $props.InstallPath } else { $props.SteamPath }
        if ($steam) { $candidates += (Join-Path $steam 'steamapps\common\Kenshi') }
    }
    $candidates += 'C:\Program Files (x86)\Steam\steamapps\common\Kenshi'
    $candidates | Where-Object { $_ -and (Test-Path -LiteralPath (Join-Path $_ 'kenshi_x64.exe')) } | Select-Object -First 1
}

$Root = $PSScriptRoot
Push-Location $Root
try {
    $kenshi = Find-KenshiDir
    if (-not $kenshi) { throw 'Каталог игры не найден: задайте $env:KENSHI_DIR.' }
    $env:KENSHI_DIR = $kenshi
    if (-not $env:KENSHI_FACTION_SID) { $env:KENSHI_FACTION_SID = '204-gamedata.base' }
    if (-not $env:KENSHI_WORKSHOP_DIR) {
        $workshop = Join-Path (Split-Path -Parent (Split-Path -Parent $kenshi)) 'workshop\content\233860'
        if (Test-Path -LiteralPath $workshop) { $env:KENSHI_WORKSHOP_DIR = $workshop }
    }
    if (-not $env:KENSHI_SAVE_DIRS) {
        $env:KENSHI_SAVE_DIRS = (@((Join-Path $env:LOCALAPPDATA 'kenshi\save'), (Join-Path $kenshi 'save')) |
            Where-Object { Test-Path -LiteralPath $_ }) -join ';'
    }

    if ((& python -c 'import sys; print(sys.version_info >= (3, 12))') -ne 'True') { throw 'Нужен Python 3.12+.' }
    & python -c 'import jsonschema' 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'Не установлен jsonschema: pip install jsonschema' }
    & python -c 'import reportlab' 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'Не установлен reportlab: pip install reportlab' }

    $save = $env:KENSHI_SAVE_DIRS -split ';' | Where-Object { $_ } | ForEach-Object { Get-ChildItem -LiteralPath $_ -Directory } |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $save) { throw 'Сохранения не найдены в KENSHI_SAVE_DIRS.' }

    $manifestPath = Join-Path $Root 'published\manifest.json'
    $previous = $null
    if (Test-Path -LiteralPath $manifestPath) { $previous = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json }
    $campaignId = if ($previous.campaign_id) { $previous.campaign_id } else { 'default' }
    $branchId = if ($previous.branch_id) { $previous.branch_id } else { 'main' }

    # Родитель снимка задаётся явно (он входит в идентичность снимка): берём голову ветки из индекса.
    $parentArgs = @()
    $indexPath = Join-Path $Root 'data\index.json'
    if (Test-Path -LiteralPath $indexPath) {
        $head = (Get-Content -LiteralPath $indexPath -Raw -Encoding UTF8 | ConvertFrom-Json).branches.$branchId.head_snapshot_id
        if ($head) { $parentArgs = @('--parent-snapshot-id', $head) }
    }

    $companion = if ($env:KENSHI_COMPANION_DIR) { $env:KENSHI_COMPANION_DIR } else { Join-Path (Split-Path -Parent $Root) 'Kenshi Compain' }
    if (-not (Test-Path -LiteralPath $companion)) { throw "Каталог Kenshi Compain не найден: $companion (задайте KENSHI_COMPANION_DIR)." }

    Write-Host "Сохранение: $($save.FullName) ($($save.LastWriteTime.ToString('yyyy-MM-dd HH:mm')))"
    Write-Host "campaign_id=$campaignId, branch_id=$branchId; доставка: $companion"
    & python -m tools.campaign pipeline --source-kind save --source $save.FullName --campaign-id $campaignId --branch-id $branchId @parentArgs --deliver-to $companion --deliver-push --sync-drive
    if ($LASTEXITCODE -ne 0) { throw "pipeline завершился с кодом $LASTEXITCODE" }

    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($manifest.validation.status -ne 'passed') { throw "validation.status=$($manifest.validation.status)" }
    Write-Host "Готово: $($manifest.game_time), snapshot_id=$($manifest.snapshot_id), validation.status=passed"
}
finally {
    Pop-Location
}
