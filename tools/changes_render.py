"""Детерминированный рендер changes.md: читаемое зеркало changes.json и разделы промежуточных снимков."""
from __future__ import annotations

from typing import Any

from tools.rendering import render_change_lines
from tools.timeline import snapshot_anchor

DETAIL_SECTIONS = (
    ("health", "Здоровье и состояние"),
    ("prosthetics", "Конечности и протезы"),
    ("skills", "Навыки"),
    ("equipment", "Экипировка"),
    ("inventory", "Инвентарь"),
    ("jobs", "Очередь работ"),
    ("base_storage", "Складские запасы баз"),
    ("diplomacy", "Отношения с фракциями"),
    ("transfers", "Перемещения предметов"),
)
IDENTITY_KEYS = ("id", "name")


def _compact(value: Any) -> str:
    """Списки записей считаются, а не печатаются: подробности остаются в changes.json."""
    if isinstance(value, list) and any(isinstance(item, (dict, list)) for item in value):
        return f"{len(value)} записей"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)


def _flatten(value: Any, prefix: str = "") -> list[str]:
    """Значения записи дельты в строках `поле: значение`; пара from/to сворачивается в `from -> to`."""
    if isinstance(value, dict):
        if "from" in value and "to" in value and not isinstance(value["from"], dict):
            delta = f" ({value['delta']:+})" if isinstance(value.get("delta"), (int, float)) else ""
            return [f"{prefix}: {_compact(value['from'])} -> {_compact(value['to'])}{delta}"]
        return [line for key, item in sorted(value.items())
                for line in _flatten(item, f"{prefix}.{key}" if prefix else str(key))]
    if isinstance(value, list):
        return [f"{prefix}: {_compact(value)}"] if value else []
    return [f"{prefix}: {value}"]


def _entry_line(entry: dict[str, Any]) -> str:
    who = f"{entry.get('name')} (`{entry.get('id')}`): " if entry.get("id") else ""
    details = {key: value for key, value in entry.items() if key not in IDENTITY_KEYS}
    return f"- {who}{'; '.join(_flatten(details))}"


def _side(side: dict[str, Any]) -> str:
    return f"Day {side.get('day')}, {side.get('time')} (`{side.get('snapshot_id')}`)"


def render_changes_md(changes: dict[str, Any], chain_steps: list[dict[str, Any]]) -> str:
    """Сводка дельты выпуска; `chain_steps` — переходы окна цепочки до текущего, с полями child_id, label, summaries."""
    lines = ["# Изменения снимка", ""]
    if changes.get("valid"):
        lines += [f"- **Предыдущий снимок:** {_side(changes.get('from') or {})}",
                  f"- **Текущий снимок:** {_side(changes.get('to') or {})}"]
    lines += ["", "## Ключевые изменения", "", *render_change_lines(changes)]

    summary = changes.get("summary") or {}
    if summary:
        lines += ["", "## Счётчики", "", "| Показатель | Значение |", "|---|---|"]
        lines += [f"| `{key}` | {value} |" for key, value in sorted(summary.items())]

    if changes.get("supplies"):
        lines += ["", "## Запасы", "", *[f"- {line}" for line in _flatten(changes["supplies"])]]
    for key, title in DETAIL_SECTIONS:
        if changes.get(key):
            lines += ["", f"## {title}", "", *[_entry_line(entry) for entry in changes[key]]]
    if changes.get("world_state"):
        lines += ["", "## World States", "", *[f"- {line}" for line in _flatten(changes["world_state"])]]

    lines += ["", "## Промежуточные снимки", ""]
    if chain_steps:
        lines.append("События предыдущих переходов цепочки; файлы дельт остаются в конвертере, их содержание приведено здесь.")
        for step in chain_steps:
            lines += ["", f'<a id="{snapshot_anchor(step["child_id"])}"></a>', f"### {step['label']}", ""]
            lines += [f"- {summary_line}" for summary_line in step["summaries"]]
    else:
        lines.append("Значимых изменений в предыдущих переходах окна цепочки нет.")

    if changes.get("not_compared"):
        lines += ["", "## Несравниваемые категории", "", *[f"- {item}" for item in sorted(changes["not_compared"])]]
    return "\n".join(lines) + "\n"
