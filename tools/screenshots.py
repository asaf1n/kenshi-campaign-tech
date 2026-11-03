"""Модуль работы с визуальной лентой скриншотов Kenshi.

Обеспечивает:
1. Чтение каталога скриншотов (_screens) в режиме read-only.
2. Фильтрацию изображений по 7-дневному скользящему окну (now - 7 суток) с поддержкой JPG, JPEG, PNG.
3. Защиту от частично записанных файлов и модификаций в процессе копирования.
4. Хронологическую сортировку и сборку kenshi-screenshots-index/1.
5. Генерацию русскоязычной Markdown-галереи index.md для визуального контекста чат-моделей.
6. Безопасное копирование в staging с проверкой целостности хеша SHA-256.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import hashlib
import os
from pathlib import Path
import shutil
from typing import Any

from tools.io_utils import linked

REPO_DEFAULT_URL = "https://github.com/asaf1n/kenshi-campaign-tech"
RAW_DEFAULT_URL = f"https://raw.githubusercontent.com/asaf1n/kenshi-campaign-tech/main"
BLOB_DEFAULT_URL = f"https://github.com/asaf1n/kenshi-campaign-tech/blob/main"
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png"}
DEFAULT_WINDOW_DAYS = 7


def is_valid_image_header(data: bytes, ext: str) -> bool:
    """Проверка минимальной сигнатуры (magic bytes) формата изображения."""
    if len(data) < 8:
        return False
    low_ext = ext.lower()
    if low_ext in {".jpg", ".jpeg"}:
        return data.startswith(b"\xff\xd8")
    if low_ext == ".png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    return False


def compute_file_sha256(path: Path) -> str:
    """Вычисление SHA-256 файла блоками."""
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def collect_recent_screenshots(
    source_dir: Path,
    cutoff_time: datetime,
    now_time: datetime | None = None,
    repo_url: str = REPO_DEFAULT_URL,
) -> list[dict[str, Any]]:
    """Сбор и валидация скриншотов из исходного каталога за последние 7 суток."""
    source_dir = Path(source_dir).resolve()
    if not source_dir.is_dir() or linked(source_dir):
        from tools.publisher import PublisherError
        raise PublisherError(f"Каталог скриншотов недоступен или не является директорией: {source_dir}")

    try:
        entries = list(source_dir.iterdir())
    except (OSError, PermissionError) as err:
        from tools.publisher import PublisherError
        raise PublisherError(f"Ошибка чтения каталога скриншотов {source_dir}: {err}") from err

    cutoff_ts = cutoff_time.timestamp()
    now_ts = now_time.timestamp() if now_time is not None else datetime.now(timezone.utc).timestamp()
    # Допуск 5 минут на расхождение системных часов
    future_limit_ts = now_ts + 300.0

    collected: list[dict[str, Any]] = []

    for entry in entries:
        if not entry.is_file() or linked(entry):
            continue
        ext = entry.suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            continue

        try:
            st_before = entry.stat()
        except OSError:
            continue

        # Проверка размера
        if st_before.st_size <= 0:
            continue

        # Проверка возраста: возраст не превышает 7 суток <=> st_mtime >= cutoff_ts
        mtime = st_before.st_mtime
        if mtime < cutoff_ts:
            continue
        if mtime > future_limit_ts:
            continue

        # Защита от частично записанных файлов (чтение сигнатуры заголовка)
        try:
            with open(entry, "rb") as f:
                header = f.read(16)
        except OSError:
            continue

        if not is_valid_image_header(header, ext):
            continue

        # Вычисление SHA-256 с контролем неизменности во время чтения
        try:
            file_hash = compute_file_sha256(entry)
            st_after = entry.stat()
        except OSError:
            continue

        # Если файл изменялся игрой прямо во время чтения
        if st_before.st_size != st_after.st_size or st_before.st_mtime != st_after.st_mtime:
            continue

        mtime_utc = datetime.fromtimestamp(st_after.st_mtime, timezone.utc)
        mtime_iso = mtime_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

        collected.append({
            "filename": entry.name,
            "source_path": entry,
            "mtime": st_after.st_mtime,
            "modified_at": mtime_iso,
            "size_bytes": st_after.st_size,
            "sha256": file_hash,
        })

    # Хронологическая сортировка (по возрастанию mtime, при равенстве — по имени)
    collected.sort(key=lambda x: (x["mtime"], x["filename"]))

    # Присвоение порядковых номеров и URL
    for idx, item in enumerate(collected, 1):
        fn = item["filename"]
        item["order"] = idx
        item["path"] = f"screenshots/{fn}"
        item["url_raw"] = f"{RAW_DEFAULT_URL}/published/screenshots/{fn}"
        item["url_blob"] = f"{BLOB_DEFAULT_URL}/published/screenshots/{fn}"

    return collected


def build_screenshots_index(
    items: list[dict[str, Any]],
    generated_at: str,
    window_days: int = DEFAULT_WINDOW_DAYS,
    window_start: str = "",
    window_end: str = "",
) -> dict[str, Any]:
    """Формирование контракта kenshi-screenshots-index/1."""
    clean_screenshots = []
    for item in items:
        clean_screenshots.append({
            "order": item["order"],
            "filename": item["filename"],
            "path": item["path"],
            "modified_at": item["modified_at"],
            "size_bytes": item["size_bytes"],
            "sha256": item["sha256"],
            "url_raw": item["url_raw"],
            "url_blob": item["url_blob"],
        })

    return {
        "format": "kenshi-screenshots-index/1",
        "generated_at": generated_at,
        "count": len(clean_screenshots),
        "window_days": window_days,
        "window_start": window_start,
        "window_end": window_end,
        "screenshots": clean_screenshots,
    }


def render_screenshots_gallery(index_data: dict[str, Any]) -> str:
    """Генерация Markdown-галереи index.md со ссылками на оригиналы."""
    count = index_data.get("count", 0)
    w_days = index_data.get("window_days", DEFAULT_WINDOW_DAYS)
    w_start = index_data.get("window_start", "")
    w_end = index_data.get("window_end", "")
    gen_at = index_data.get("generated_at", "")

    lines: list[str] = [
        "# Визуальная лента скриншотов Kenshi",
        "",
        f"- **Обновлено**: `{gen_at}`",
        f"- **Окно выборки**: последние {w_days} суток (с `{w_start}` по `{w_end}`)",
        f"- **Количество скриншотов**: {count}",
        "",
        "Визуальная лента игрового процесса в хронологическом порядке для контекста чат-моделей (ChatGPT, Claude, Gemini). "
        "Изображения сохраняются в оригинальном разрешении и качестве.",
        "",
    ]

    screenshots = index_data.get("screenshots", [])
    if not screenshots:
        lines.extend([
            "> [!NOTE]",
            f"> В течение последних {w_days} суток новые скриншоты не зафиксированы.",
            "",
        ])
        return "\n".join(lines)

    lines.extend([
        "---",
        "",
    ])

    for item in screenshots:
        order = item["order"]
        fn = item["filename"]
        mod_at = item["modified_at"]
        size_kb = item["size_bytes"] / 1024.0
        file_sha = item["sha256"]
        url_raw = item["url_raw"]
        url_blob = item["url_blob"]

        lines.extend([
            f"### {order}. {fn}",
            "",
            f"- **Время записи (UTC)**: `{mod_at}`",
            f"- **Размер**: {size_kb:.1f} КБ",
            f"- **SHA-256**: `{file_sha}`",
            f"- **Ссылки**: [Просмотр в GitHub]({url_blob}) | [Прямой Raw URL]({url_raw})",
            "",
            f"![{fn}]({fn})",
            "",
            "---",
            "",
        ])

    return "\n".join(lines)


def copy_screenshot_safe(src: Path, dst: Path, expected_sha256: str | None = None) -> None:
    """Безопасное копирование одного файла скриншота с проверкой размера и SHA-256."""
    src = src.resolve()
    dst = dst.resolve()

    dst.parent.mkdir(parents=True, exist_ok=True)
    # Исходный каталог является строго read-only
    shutil.copy2(src, dst)

    if not dst.is_file():
        from tools.publisher import PublisherError
        raise PublisherError(f"Ошибка копирования: целевой файл {dst} не создан")

    if expected_sha256 is not None:
        copied_hash = compute_file_sha256(dst)
        if copied_hash != expected_sha256:
            try:
                dst.unlink(missing_ok=True)
            except OSError:
                pass
            from tools.publisher import PublisherError
            raise PublisherError(
                f"Несовпадение хеша SHA-256 при копировании {src.name}: ожидался {expected_sha256}, получен {copied_hash}"
            )
