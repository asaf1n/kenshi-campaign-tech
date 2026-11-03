<#
.SYNOPSIS
    Быстрая инициализация и запуск конвейера kenshi-campaign-tech.

.DESCRIPTION
    Без -Run: задаёт переменные окружения Kenshi, проверяет Python, jsonschema, схемы и
    справочник мира, выбирает сохранение и печатает готовую команду pipeline.
    С -Run: после проверок выполняет pipeline и сверяет manifest.json
    (validation.status == passed). Git скрипт не вызывает; commit и push published/ выполняются отдельно.
    Сохранения и скриншоты открываются только на чтение.

    Переменные окружения живут в процессе PowerShell. Чтобы они остались в текущей сессии
    для ручных вызовов python -m tools.campaign, запускайте через dot-sourcing:
        . .\scripts\Initialize-Pipeline.ps1

.PARAMETER Source
    Путь к сохранению либо имя папки в каталогах сохранений (например autosave0).
    По умолчанию берётся сохранение с самой поздней датой изменения.

.PARAMETER CampaignId
    По умолчанию campaign_id из published/manifest.json, иначе default.

.PARAMETER BranchId
    По умолчанию branch_id из published/manifest.json, иначе main.

.PARAMETER DeliverTo
    Каталог репозитория Kenshi-Compain. По умолчанию KENSHI_COMPANION_DIR либо соседний
    каталог "Kenshi Compain", если он существует. Используется только с -Deliver.

.PARAMETER Deliver
    После успешной публикации доставить published/ в Kenshi-Compain (--deliver-to у pipeline:
    commit в Kenshi-Compain, без push). С -DryRun доставка пропускается.

.PARAMETER Run
    Выполнить pipeline (по умолчанию только подготовка и проверки).

.PARAMETER DryRun
    Передать --dry-run в pipeline (используется вместе с -Run).

.PARAMETER BuildWorld
    Принудительно пересобрать справочник мира (по умолчанию строится, только если его нет).

.EXAMPLE
    .\scripts\Initialize-Pipeline.ps1
    .\scripts\Initialize-Pipeline.ps1 -Source autosave0 -Run
    .\scripts\Initialize-Pipeline.ps1 -Run -DryRun
#>
[CmdletBinding()]
param(
    [string]$Source,
    [string]$CampaignId,
    [string]$BranchId,
    [string]$DeliverTo,
    [switch]$Run,
    [switch]$Deliver,
    [switch]$DryRun,
    [switch]$BuildWorld
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

function Write-Step([string]$Text) { Write-Host "==> $Text" -ForegroundColor Cyan }
function Write-Info([string]$Text) { Write-Host "    $Text" }
function Write-Warn([string]$Text) { Write-Host "    ! $Text" -ForegroundColor Yellow }

function Invoke-Campaign([string[]]$CampaignArgs) {
    & python -m tools.campaign @CampaignArgs
    if ($LASTEXITCODE -ne 0) {
        throw "tools.campaign $($CampaignArgs -join ' ') завершился с кодом $LASTEXITCODE"
    }
}

function Find-KenshiDir {
    $candidates = New-Object System.Collections.Generic.List[string]
    if ($env:KENSHI_DIR) { $candidates.Add($env:KENSHI_DIR) }
    foreach ($key in 'HKLM:\SOFTWARE\WOW6432Node\Valve\Steam', 'HKCU:\SOFTWARE\Valve\Steam') {
        $steam = $null
        try { $steam = (Get-ItemProperty -Path $key -ErrorAction Stop).InstallPath } catch { }
        if (-not $steam) {
            try { $steam = (Get-ItemProperty -Path $key -ErrorAction Stop).SteamPath } catch { }
        }
        if ($steam) { $candidates.Add((Join-Path $steam 'steamapps\common\Kenshi')) }
    }
    $candidates.Add('C:\Program Files (x86)\Steam\steamapps\common\Kenshi')
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath (Join-Path $candidate 'kenshi_x64.exe')) { return $candidate }
    }
    return $null
}

$Root = Split-Path -Parent $PSScriptRoot
Push-Location $Root
try {
    Write-Step 'Окружение Kenshi'
    $kenshi = Find-KenshiDir
    if ($kenshi) {
        $env:KENSHI_DIR = $kenshi
    } else {
        Write-Warn 'Каталог игры не найден: build-world и скриншоты недоступны. Задайте $env:KENSHI_DIR.'
    }
    if (-not $env:KENSHI_FACTION_SID) { $env:KENSHI_FACTION_SID = '204-gamedata.base' }
    if (-not $env:KENSHI_WORKSHOP_DIR -and $env:KENSHI_DIR) {
        $workshop = Join-Path (Split-Path -Parent (Split-Path -Parent $env:KENSHI_DIR)) 'workshop\content\233860'
        if (Test-Path -LiteralPath $workshop) { $env:KENSHI_WORKSHOP_DIR = $workshop }
    }
    if (-not $env:KENSHI_SAVE_DIRS) {
        $roots = @(Join-Path $env:LOCALAPPDATA 'kenshi\save')
        if ($env:KENSHI_DIR) { $roots += (Join-Path $env:KENSHI_DIR 'save') }
        $env:KENSHI_SAVE_DIRS = (@($roots | Where-Object { Test-Path -LiteralPath $_ }) -join ';')
    }
    Write-Info "KENSHI_DIR=$env:KENSHI_DIR"
    Write-Info "KENSHI_SAVE_DIRS=$env:KENSHI_SAVE_DIRS"
    Write-Info "KENSHI_FACTION_SID=$env:KENSHI_FACTION_SID"

    Write-Step 'Python и зависимости'
    $pyOk = (& python -c 'import sys; print(sys.version_info >= (3, 12))') 2>$null
    if ($pyOk -ne 'True') { throw 'Нужен Python 3.12+ в PATH (python --version).' }
    & python -c 'import jsonschema' 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'Не установлен jsonschema: pip install jsonschema' }
    Write-Info ((& python --version) + ', jsonschema доступен')

    Write-Step 'Контрактные схемы'
    Invoke-Campaign @('check-schemas')

    Write-Step 'Справочник мира'
    $worldDir = Join-Path $Root 'data\world'
    $hasWorld = (Test-Path -LiteralPath $worldDir) -and (@(Get-ChildItem -LiteralPath $worldDir -Filter '*.json').Count -gt 0)
    if ($BuildWorld -or -not $hasWorld) {
        if (-not $env:KENSHI_DIR) { throw 'Справочник мира отсутствует, а каталог игры не найден.' }
        Invoke-Campaign @('build-world')
    } else {
        Write-Info 'Справочник мира найден (data/world); пересборка: -BuildWorld'
    }

    Write-Step 'Сохранение'
    $saveRoots = @($env:KENSHI_SAVE_DIRS -split ';' | Where-Object { $_ -and (Test-Path -LiteralPath $_) })
    if (-not $Source) {
        $latest = $saveRoots | ForEach-Object { Get-ChildItem -LiteralPath $_ -Directory } |
            Sort-Object LastWriteTime -Descending | Select-Object -First 5
        if (-not $latest) { throw 'Сохранения не найдены в KENSHI_SAVE_DIRS.' }
        $latest | ForEach-Object { Write-Info ('{0:yyyy-MM-dd HH:mm}  {1}' -f $_.LastWriteTime, $_.FullName) }
        $Source = $latest[0].FullName
    } elseif (-not (Test-Path -LiteralPath $Source)) {
        $resolved = $saveRoots | ForEach-Object { Join-Path $_ $Source } | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
        if (-not $resolved) { throw "Сохранение не найдено: $Source" }
        $Source = $resolved
    }
    $Source = (Resolve-Path -LiteralPath $Source).Path
    Write-Info "Источник: $Source"

    $manifestPath = Join-Path $Root 'published\manifest.json'
    $previous = $null
    if (Test-Path -LiteralPath $manifestPath) {
        $previous = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    }
    if (-not $CampaignId) { if ($previous -and $previous.campaign_id) { $CampaignId = $previous.campaign_id } else { $CampaignId = 'default' } }
    if (-not $BranchId) { if ($previous -and $previous.branch_id) { $BranchId = $previous.branch_id } else { $BranchId = 'main' } }
    if (-not $DeliverTo) {
        if ($env:KENSHI_COMPANION_DIR) { $DeliverTo = $env:KENSHI_COMPANION_DIR }
        else {
            $sibling = Join-Path (Split-Path -Parent $Root) 'Kenshi Compain'
            if (Test-Path -LiteralPath $sibling) { $DeliverTo = $sibling }
        }
    }
    Write-Info "campaign_id=$CampaignId, branch_id=$BranchId"
    if ($previous) { Write-Info "Последняя публикация: $($previous.game_time), $($previous.generated_at)" }

    $pipelineArgs = @('pipeline', '--source-kind', 'save', '--source', $Source,
        '--campaign-id', $CampaignId, '--branch-id', $BranchId)
    if ($Deliver) {
        if (-not $DeliverTo) { throw 'Каталог Kenshi-Compain не найден: задайте -DeliverTo или KENSHI_COMPANION_DIR.' }
        $pipelineArgs += @('--deliver-to', $DeliverTo)
    }
    if ($DryRun) { $pipelineArgs += '--dry-run' }

    $quoted = ($pipelineArgs | ForEach-Object { if ($_ -match '\s') { '"' + $_ + '"' } else { $_ } }) -join ' '
    if (-not $Run) {
        Write-Step 'Готово. Команда запуска:'
        Write-Host "python -m tools.campaign $quoted"
        return
    }

    Write-Step 'Конвейер'
    Write-Info "python -m tools.campaign $quoted"
    Invoke-Campaign $pipelineArgs

    if (-not $DryRun) {
        Write-Step 'Проверка публикации'
        $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($manifest.validation.status -ne 'passed') { throw "validation.status=$($manifest.validation.status)" }
        Write-Info "snapshot_id=$($manifest.snapshot_id)"
        Write-Info "game_time=$($manifest.game_time), generated_at=$($manifest.generated_at)"
        if ($manifest.validation.warnings) { $manifest.validation.warnings | ForEach-Object { Write-Warn $_ } }
        Write-Info 'validation.status=passed.'
        if (-not $Deliver) {
            Write-Info 'Дальше: проверить git diff published/, commit и push; доставка в Kenshi-Compain: -Deliver.'
        }
    }
}
finally {
    Pop-Location
}
