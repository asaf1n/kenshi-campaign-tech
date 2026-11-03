"""Модуль генерации хронологической ленты feed.md и сериализации annotations.jsonl."""
from __future__ import annotations

import json
from typing import Any

from tools.screenshots import BLOB_DEFAULT_URL, RAW_DEFAULT_URL, DEFAULT_WINDOW_DAYS


def serialize_annotations_jsonl(records: list[dict[str, Any]]) -> bytes:
    """Сериализация списка аннотаций в UTF-8 JSONL байты."""
    lines: list[str] = []
    for rec in records:
        # json.dumps с сортировкой ключей и compact представлением
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
    """Генерация Markdown-хроники feed.md для контекста игрового компаньона."""
    lines: list[str] = [
        "# Хронологическая визуальная лента эпизодов Kenshi",
        "",
        f"- **Обновлено**: `{generated_at}`",
        f"- **Окно выборки**: последние {window_days} суток (с `{window_start}` по `{window_end}`)",
        f"- **Всего скриншотов в окне**: {len(annotations)}",
        f"- **Сгруппировано эпизодов/сцен**: {len(episodes)}",
        "",
        "Хронологическая лента сцен игрового процесса для ориентирования игрового компаньона. "
        "Похожие последовательные кадры объединены в эпизоды. Описания фиксируют наблюдаемое.",
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

    # Индексируем аннотации по имени файла для быстрого доступа
    ann_by_fn = {rec.get("filename"): rec for rec in annotations}

    for ep in episodes:
        ep_id = ep.get("episode_id", "ep_000")
        ep_idx = ep.get("episode_index", 1)
        count = ep.get("count", 1)
        start_time = ep.get("start_time", "")
        end_time = ep.get("end_time", "")
        key_fn = ep.get("key_filename", "")
        filenames = ep.get("filenames", [])

        key_ann = ann_by_fn.get(key_fn, {})
        meta = key_ann.get("meta", {})
        ui = key_ann.get("ui_text", {})
        visual = key_ann.get("visual", {})

        # Агрегация данных по всем кадрам эпизода
        all_locations = set()
        all_characters = set()
        all_actions = set()
        all_dialogues: list[dict[str, Any]] = []

        day_val = meta.get("game_day")
        time_val = meta.get("game_time")

        for fn in filenames:
            frame_ann = ann_by_fn.get(fn, {})
            f_meta = frame_ann.get("meta", {})
            f_ui = frame_ann.get("ui_text", {})
            f_vis = frame_ann.get("visual", {})

            if not day_val and f_meta.get("game_day"):
                day_val = f_meta.get("game_day")
            if not time_val and f_meta.get("game_time"):
                time_val = f_meta.get("game_time")

            if f_ui.get("location"):
                all_locations.add(f_ui["location"])
            for c in f_ui.get("characters", []):
                all_characters.add(c)
            for c in f_vis.get("present_characters", []):
                all_characters.add(c)
            for a in f_vis.get("actions", []):
                all_actions.add(a)
            for d in f_ui.get("dialogues", []):
                if d and d not in all_dialogues:
                    all_dialogues.append(d)

        loc_str = ", ".join(sorted(all_locations)) if all_locations else (visual.get("environment") or "Пустоши Kenshi")
        time_str = f"День {day_val}, {time_val}" if (day_val and time_val) else (f"День {day_val}" if day_val else "Время не определено")
        chars_str = ", ".join(sorted(all_characters)) if all_characters else "Не различимы"
        scene_type_ru = {
            "travel": "Путешествие / переход",
            "combat": "Сражение / боевой контакт",
            "dialogue": "Диалог / переговоры",
            "trade": "Торговля",
            "building": "Строительство / база",
            "exploration": "Исследование руин / локации",
            "base": "Управление поселением",
            "inventory": "Инвентарь / менеджмент",
            "resting": "Лагерь / отдых",
            "stealth": "Скрытность / разведка",
            "other": "Игровой процесс",
        }.get(visual.get("scene_type", "other"), visual.get("scene_type", "other"))

        lines.extend([
            f"### Эпизод {ep_idx}: {loc_str} ({count} кадр{'а' if 1 < count < 5 else 'ов' if count >= 5 else ''})",
            "",
            f"- **Ориентир времени**: `{time_str}` (файлы: `{start_time}` – `{end_time}`)",
            f"- **Локация**: {loc_str}",
            f"- **Участники**: {chars_str}",
            f"- **Тип сцены**: {scene_type_ru}",
            f"- **Краткое описание**: {visual.get('summary', 'Наблюдение игровой сцены.')}",
        ])

        if all_actions:
            lines.append(f"- **Действия**: {', '.join(sorted(all_actions))}")

        if all_dialogues:
            lines.extend([
                "",
                "**Диалоги и реплики:**",
            ])
            for d in all_dialogues:
                spk = d.get("speaker") or "Реплика"
                txt = d.get("text", "")
                lines.append(f"> **{spk}**: «{txt}»")

        lines.extend([
            "",
            "**Кадры эпизода:**",
        ])
        for fn in filenames:
            blob_url = f"{BLOB_DEFAULT_URL}/published/screenshots/{fn}"
            raw_url = f"{RAW_DEFAULT_URL}/published/screenshots/{fn}"
            lines.append(f"- [{fn}]({fn}) ([GitHub]({blob_url}) | [Raw]({raw_url}))")

        # Встраивание первого кадра эпизода в качестве превью
        lines.extend([
            "",
            f"![{key_fn}]({key_fn})",
            "",
            "---",
            "",
        ])

    return "\n".join(lines)
