"""Модуль генерации технической хронологической ленты feed.md и сериализации annotations.jsonl.

Исключены любые вымышленные описания сцен, действий или сюжета.
Лента фиксирует только технические данные, группировку кадров и OCR интерфейса.
"""
from __future__ import annotations

import json
from typing import Any

from tools.screenshots import BLOB_DEFAULT_URL, RAW_DEFAULT_URL, DEFAULT_WINDOW_DAYS


def serialize_annotations_jsonl(records: list[dict[str, Any]]) -> bytes:
    """Сериализация списка аннотаций в UTF-8 JSONL байты."""
    lines: list[str] = []
    for rec in records:
        line = json.dumps(rec, ensure_ascii=False, sort_keys=True)
        lines.append(line)
    text = "\n".join(lines) + ("\n" if lines else "")
    return text.encode("utf-8")


def render_screenshot_feed(
    annotations: list[dict[str, Any]],
    episodes: list[dict[str, Any]],
    generated_at: str,
    window_days: int = DEFAULT_WINDOW_DAYS,
    window_start: str = "",
    window_end: str = "",
) -> str:
    """Генерация технической Markdown-хроники feed.md для анализа LLM-компаньоном."""
    lines: list[str] = [
        "# Техническая лента скриншотов Kenshi",
        "",
        f"- **Сформировано**: `{generated_at}`",
        f"- **Окно выборки**: последние {window_days} суток (с `{window_start}` по `{window_end}`)",
        f"- **Всего скриншотов в окне**: {len(annotations)}",
        f"- **Сгруппировано серий/кадров**: {len(episodes)}",
        "",
        "Техническая лента сгруппированных по сходству кадров с извлеченным текстом интерфейса (OCR).",
        "",
    ]

    if not annotations or not episodes:
        lines.extend([
            "> [!NOTE]",
            f"> В течение последних {window_days} суток скриншоты не зафиксированы.",
            "",
        ])
        return "\n".join(lines)

    lines.extend([
        "---",
        "",
    ])

    # Индексируем записи по имени файла
    ann_by_fn = {rec.get("filename"): rec for rec in annotations}

    for ep in episodes:
        ep_id = ep.get("episode_id", "seq_000")
        ep_idx = ep.get("episode_index", 1)
        count = ep.get("count", 1)
        start_time = ep.get("start_time", "")
        end_time = ep.get("end_time", "")
        key_fn = ep.get("key_filename", "")
        filenames = ep.get("filenames", [])

        # Агрегация данных OCR без домыслов
        all_locations = set()
        all_characters = set()
        all_notifications: list[str] = []
        all_dialogues: list[dict[str, Any]] = []
        all_items_tasks = set()
        day_val = None
        time_val = None

        for fn in filenames:
            frame_ann = ann_by_fn.get(fn, {})
            f_ocr = frame_ann.get("ocr", {})
            if not day_val and f_ocr.get("game_day"):
                day_val = f_ocr.get("game_day")
            if not time_val and f_ocr.get("game_time"):
                time_val = f_ocr.get("game_time")
            if f_ocr.get("location"):
                all_locations.add(f_ocr["location"])
            for c in f_ocr.get("characters", []):
                all_characters.add(c)
            for d in f_ocr.get("dialogues", []):
                if d and d not in all_dialogues:
                    all_dialogues.append(d)
            for n in f_ocr.get("notifications", []):
                if n and n not in all_notifications:
                    all_notifications.append(n)
            for t in f_ocr.get("items_or_tasks", []):
                all_items_tasks.add(t)

        loc_str = ", ".join(sorted(all_locations)) if all_locations else "Не зафиксирована в UI"
        time_str = f"День {day_val}, {time_val}" if (day_val and time_val) else (f"День {day_val}" if day_val else "Не зафиксировано в UI")
        chars_str = ", ".join(sorted(all_characters)) if all_characters else "Не зафиксированы в UI"

        lines.extend([
            f"### Серия {ep_idx} ({count} кадр{'а' if 1 < count < 5 else 'ов' if count >= 5 else ''})",
            "",
            f"- **Файловое время**: с `{start_time}` по `{end_time}`",
            f"- **Игровое время (OCR)**: `{time_str}`",
            f"- **Локация (OCR)**: {loc_str}",
            f"- **Персонажи в UI (OCR)**: {chars_str}",
        ])

        if all_items_tasks:
            lines.append(f"- **Задачи/предметы в UI**: {', '.join(sorted(all_items_tasks))}")

        if all_notifications:
            lines.extend([
                "",
                "**Уведомления интерфейса:**",
            ])
            for n in all_notifications:
                lines.append(f"- {n}")

        if all_dialogues:
            lines.extend([
                "",
                "**Зафиксированные реплики (диалоги):**",
            ])
            for d in all_dialogues:
                spk = d.get("speaker") or "Персонаж"
                txt = d.get("text", "")
                lines.append(f"> **{spk}**: «{txt}»")

        lines.extend([
            "",
            "**Кадры серии:**",
        ])
        for fn in filenames:
            blob_url = f"{BLOB_DEFAULT_URL}/published/screenshots/{fn}"
            raw_url = f"{RAW_DEFAULT_URL}/published/screenshots/{fn}"
            lines.append(f"- [{fn}]({fn}) ([GitHub]({blob_url}) | [Raw]({raw_url}))")

        # Встраивание первого кадра серии в качестве превью
        lines.extend([
            "",
            f"![{key_fn}]({key_fn})",
            "",
            "---",
            "",
        ])

    return "\n".join(lines)
