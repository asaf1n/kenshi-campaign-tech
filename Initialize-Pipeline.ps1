<#
.SYNOPSIS
    Запуск конвейера kenshi-campaign-tech одной командой.

.DESCRIPTION
    Задаёт переменные окружения Kenshi, берёт сохранение кампании, выполняет pipeline
    (разбор сохранения и скриншотов -> published/) и доставляет результат в репозиторий
    Kenshi Compain (KENSHI_COMPANION_DIR или соседний каталог "Kenshi Compain"): commit и push в GitHub.
    campaign_id и branch_id берутся из последнего published/manifest.json.
    Сохранения и скриншоты игры открываются только на чтение; git вызывает только доставка в Kenshi Compain.

    Папка сохранений общая для всех прохождений, поэтому выбор по папке кампанию не определяет.
    Явный выбор: параметр -SaveFolder, иначе KENSHI_SAVE_PATTERN (шаблон имени, например 'Hot*'), иначе
    KENSHI_SAVE_NAME (точное имя); при нескольких совпадениях берётся свежайшая из совпавших.
    Без явного выбора папки перебираются от свежих к старым (до -MaxCandidates), для каждой выполняется
    "python -m tools.campaign check-save" (состав персонажей против головы ветки); берётся первая папка,
    относящаяся к кампании. Если ни одна не подходит, ошибка со списком проверенных папок, код 1.
    Перед публикацией pipeline в любом случае повторно сверяет состав персонажей с родительским снимком;
    при несовпадении публикации, доставки и синхронизации Диска нет, код возврата 1.
    Синхронизация Диска (--sync-drive) выполняется по умолчанию после публикации и доставки; недоступный Диск или Google API
    (нет токена, сети, G:, ошибка Drive API) - предупреждение "drive: skipped|failed (причина)", код возврата остаётся 0.

.PARAMETER SaveFolder
    Имя или шаблон (wildcard) папки сохранения кампании.

.PARAMETER AllowNewCampaign
    Осознанный новый старт или смена кампании/ветки: пропускает проверку принадлежности сейва
    (перебор по составу невозможен, папку нужно указать явно).

.PARAMETER CampaignId
    campaign_id вместо значения из published/manifest.json (новая кампания; обычно вместе с -AllowNewCampaign).

.PARAMETER BranchId
    branch_id вместо значения из published/manifest.json.

.PARAMETER CheckOnly
    Только проверка: выбор/перебор папок и "check-save" для каждой (имя, время, общих id, доля, вердикт),
    итог "выбран бы: <папка>" либо "ни одна не подходит"; код 0 (подходящая найдена) или 1 (нет).
    pipeline, доставка и синхронизация Диска не запускаются, каталог Kenshi Compain не нужен, data/ и published/ не пишутся.
    При явном выборе (-SaveFolder, KENSHI_SAVE_PATTERN, KENSHI_SAVE_NAME) проверяется только выбранная папка.

.PARAMETER MaxCandidates
    Сколько последних папок сохранений проверять при автоматическом выборе (по умолчанию 6).
#>
param(
    [string]$SaveFolder,
    [switch]$AllowNewCampaign,
    [string]$CampaignId,
    [string]$BranchId,
    [int]$MaxCandidates = 6,
    [switch]$CheckOnly
)
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

function Test-SaveFolder([System.IO.DirectoryInfo]$Folder, [string]$Campaign, [string]$Branch) {
    $inv = [System.Globalization.CultureInfo]::InvariantCulture
    $raw = & python -m tools.campaign check-save --source $Folder.FullName --campaign-id $Campaign --branch-id $Branch
    $code = $LASTEXITCODE
    $when = $Folder.LastWriteTime.ToString('yyyy-MM-dd HH:mm')
    $verdict = 'ошибка проверки (код {0})' -f $code
    $belongs = $false
    if ($code -eq 0 -or $code -eq 1) {
        $r = $null
        try { $r = ($raw -join "`n") | ConvertFrom-Json } catch { }
        if ($r) {
            $belongs = ($code -eq 0)
            $what = if ($r.status -eq 'skipped') { "проверка пропущена: $($r.reason)" }
                    else { 'общих id {0} из {1}, доля {2}' -f $r.shared, $r.parent_size, ([double]$r.share).ToString('0.00', $inv) }
            $verdict = '{0} -> {1}' -f $what, $(if ($belongs) { 'относится' } else { 'не относится' })
        }
    }
    [pscustomobject]@{ Belongs = $belongs; Line = ('  {0}  {1}  {2}' -f $Folder.Name, $when, $verdict) }
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
    if (-not $CheckOnly) {
        & python -c 'import reportlab' 2>$null
        if ($LASTEXITCODE -ne 0) { throw 'Не установлен reportlab: pip install reportlab' }
    }

    $folders = @($env:KENSHI_SAVE_DIRS -split ';' | Where-Object { $_ } | ForEach-Object { Get-ChildItem -LiteralPath $_ -Directory })

    $manifestPath = Join-Path $Root 'published\manifest.json'
    $previous = $null
    if (Test-Path -LiteralPath $manifestPath) { $previous = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json }
    $campaignId = if ($CampaignId) { $CampaignId } elseif ($previous.campaign_id) { $previous.campaign_id } else { 'default' }
    $branchId = if ($BranchId) { $BranchId } elseif ($previous.branch_id) { $previous.branch_id } else { 'main' }

    # Родитель снимка задаётся явно (он входит в идентичность снимка): берём голову ветки из индекса.
    $parentArgs = @()
    $indexPath = Join-Path $Root 'data\index.json'
    if (Test-Path -LiteralPath $indexPath) {
        $head = (Get-Content -LiteralPath $indexPath -Raw -Encoding UTF8 | ConvertFrom-Json).branches.$branchId.head_snapshot_id
        if ($head) { $parentArgs = @('--parent-snapshot-id', $head) }
    }

    $pattern = $SaveFolder
    if (-not $pattern) { $pattern = $env:KENSHI_SAVE_PATTERN }
    $exact = $null
    if (-not $pattern) { $exact = $env:KENSHI_SAVE_NAME }
    if ($pattern -or $exact) {
        $matched = if ($pattern) { @($folders | Where-Object { $_.Name -like $pattern }) } else { @($folders | Where-Object { $_.Name -eq $exact }) }
        if (-not $matched) { throw "Папка сохранения не найдена по условию '$(if ($pattern) { $pattern } else { $exact })' в KENSHI_SAVE_DIRS." }
        $save = $matched | Sort-Object LastWriteTime -Descending | Select-Object -First 1
        if ($matched.Count -gt 1) { Write-Host "Шаблон совпал с $($matched.Count) папками; берётся свежайшая." }
        if ($CheckOnly) {
            $res = Test-SaveFolder $save $campaignId $branchId
            Write-Host $res.Line
            if ($res.Belongs) { Write-Host "выбран бы: $($save.Name)"; exit 0 }
            Write-Host 'ни одна не подходит'; exit 1
        }
    }
    else {
        # Папка сохранений общая для всех прохождений: единственная защита - состав персонажей (check-save).
        if ($AllowNewCampaign) { throw 'С -AllowNewCampaign проверка состава не выполняется: укажите папку явно (-SaveFolder, KENSHI_SAVE_PATTERN или KENSHI_SAVE_NAME).' }
        $save = $null
        $report = @()
        foreach ($folder in ($folders | Sort-Object LastWriteTime -Descending | Select-Object -First $MaxCandidates)) {
            $res = Test-SaveFolder $folder $campaignId $branchId
            $report += $res.Line
            Write-Host $res.Line
            # -CheckOnly показывает все кандидаты; выбор - первая подходящая.
            if ($res.Belongs) {
                if (-not $save) { $save = $folder }
                if (-not $CheckOnly) { break }
            }
        }
        if (-not $save -and $CheckOnly) { Write-Host 'ни одна не подходит'; exit 1 }
        if (-not $save) {
            throw ("Ни одна из последних $MaxCandidates папок сохранений не относится к кампании $campaignId (ветка $branchId); " +
                "публикация, доставка и синхронизация Диска не выполнялись. Проверено (свежие сверху):`n" + ($report -join "`n") +
                "`nУкажите папку явно (-SaveFolder), увеличьте -MaxCandidates либо используйте -AllowNewCampaign для нового старта.")
        }
    }

    if ($CheckOnly) { Write-Host "выбран бы: $($save.Name)"; exit 0 }

    $companion = if ($env:KENSHI_COMPANION_DIR) { $env:KENSHI_COMPANION_DIR } else { Join-Path (Split-Path -Parent $Root) 'Kenshi Compain' }
    if (-not (Test-Path -LiteralPath $companion)) { throw "Каталог Kenshi Compain не найден: $companion (задайте KENSHI_COMPANION_DIR)." }

    Write-Host "Сохранение: $($save.FullName) ($($save.LastWriteTime.ToString('yyyy-MM-dd HH:mm')))"
    Write-Host "campaign_id=$campaignId, branch_id=$branchId; доставка: $companion"
    $allowArgs = if ($AllowNewCampaign) { @('--allow-new-campaign') } else { @() }
    $pipelineOut = & python -m tools.campaign pipeline --source-kind save --source $save.FullName --campaign-id $campaignId --branch-id $branchId @parentArgs @allowArgs --deliver-to $companion --deliver-push --sync-drive
    $pipelineCode = $LASTEXITCODE
    $pipelineOut | ForEach-Object { Write-Host $_ }
    if ($pipelineCode -ne 0) { throw "pipeline завершился с кодом $pipelineCode (при отказе проверки кампании published/, доставка и Диск не затронуты)" }
    # Недоступный Диск или Google API - предупреждение: публикация и доставка уже выполнены.
    $driveLine = $null
    try { $driveLine = (($pipelineOut -join "`n") | ConvertFrom-Json).drive } catch { }
    if ($driveLine -and $driveLine -ne 'synced') { Write-Warning "drive: $driveLine" }
    elseif ($driveLine) { Write-Host 'drive: synced' }

    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($manifest.validation.status -ne 'passed') { throw "validation.status=$($manifest.validation.status)" }
    Write-Host "Готово: $($manifest.game_time), snapshot_id=$($manifest.snapshot_id), validation.status=passed"
}
finally {
    Pop-Location
}
