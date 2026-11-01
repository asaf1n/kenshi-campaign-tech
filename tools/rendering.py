"""Детерминированная генерация русскоязычных Markdown-артефактов (brief.md, squad.md).

Генерация ведётся строго из проверенных JSON без обращения к часам или внешним данным.
Повторный рендер на одинаковых входных данных даёт побайтово идентичный результат.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any


def render_brief(current: dict[str, Any], changes: dict[str, Any], snapshot_id: str) -> str:
    world = current.get("world", {})
    day = world.get("day")
    time = world.get("time")
    money = world.get("money")

    day_str = f"День {day}" if day is not None else "День: не получено"
    time_str = f", время {time}" if time is not None else ""
    money_str = f"{money:,} кат.".replace(",", " ") if isinstance(money, (int, float)) else "не получено"

    characters = current.get("characters", [])
    squads = current.get("squads", [])

    lines: list[str] = [
        f"# Краткая сводка: {snapshot_id}",
        "",
        "## Игровой мир",
        f"- **Время:** {day_str}{time_str}",
        f"- **Баланс:** {money_str}",
        "",
        "## Состав отряда",
        f"- **Всего персонажей:** {len(characters)}",
        f"- **Всего отрядов:** {len(squads)}",
    ]

    for sq in sorted(squads, key=lambda s: s.get("squad_id", "")):
        sq_id = sq.get("squad_id", "")
        sq_name = sq.get("squad_name") or sq_id
        members = [c for c in characters if c.get("squad_id") == sq_id]
        lines.append(f"- **Отряд `{sq_id}`** ({sq_name}): {len(members)} участников")

    lines.extend([
        "",
        "### Список участников",
        "| ID | Имя | Отряд | Раса |",
        "|---|---|---|---|",
    ])
    for c in sorted(characters, key=lambda ch: (str(ch.get("name") or ""), str(ch.get("id") or ""))):
        cid = c.get("id") or "не указан"
        name = c.get("name") or "Без имени"
        sq = c.get("squad_id") or "—"
        race = c.get("race") or "—"
        lines.append(f"| `{cid}` | {name} | `{sq}` | {race} |")

    lines.extend([
        "",
        "## Доступные местоположения",
    ])
    locations: list[str] = []
    prop = current.get("property")
    if isinstance(prop, dict):
        towns = prop.get("towns") or prop.get("player_towns")
        if isinstance(towns, list) and towns:
            locations.append(f"Подтверждённые города построек: {', '.join(str(t) for t in sorted(towns))}")
    # Проверка сырых координат
    positions: list[str] = []
    for c in characters:
        pos = c.get("position")
        if isinstance(pos, (list, tuple)) and len(pos) >= 2:
            positions.append(f"{c.get('name')}: ({pos[0]:.1f}, {pos[1]:.1f})")
    if positions:
        locations.append(f"Сырые координаты: {'; '.join(sorted(positions)[:5])}")

    if locations:
        for loc in locations:
            lines.append(f"- {loc}")
    else:
        lines.append("- не получено (географическое разрешение подключается профильным артефактом в задаче 02)")

    lines.extend([
        "",
        "## Ключевые изменения",
    ])

    valid = changes.get("valid", False)
    if not valid:
        reason = changes.get("reason") or "; ".join(changes.get("problems", [])) or "нет baseline для сравнения"
        lines.extend([
            f"- **Статус сравнения:** базовое сравнение отсутствует (`valid: false`).",
            f"- **Причина:** {reason}.",
            "- **Ключевые изменения:** отсутствуют (первая публикация либо несравнимые снимки).",
        ])
    else:
        roster = changes.get("roster", {})
        joined = roster.get("joined", [])
        left = roster.get("left", [])
        squad_changed = roster.get("squad_changed", [])
        status_changed = roster.get("status_changed", [])
        transfers = changes.get("transfers", [])
        skills = changes.get("skills", [])
        equipment = changes.get("equipment", [])
        health = changes.get("health", [])

        if joined:
            j_str = ", ".join(f"{c.get('name')} (`{c.get('id')}`)" for c in joined)
            lines.append(f"- **Прибыли ({len(joined)}):** {j_str}")
        if left:
            l_str = ", ".join(f"{c.get('name')} (`{c.get('id')}`)" for c in left)
            lines.append(f"- **Покинули ({len(left)}):** {l_str}")
        if squad_changed:
            sq_str = ", ".join(f"{c.get('name')} (`{c.get('id')}`: {c.get('from')} -> {c.get('to')})" for c in squad_changed)
            lines.append(f"- **Смена отряда ({len(squad_changed)}):** {sq_str}")
        if status_changed:
            st_str = ", ".join(f"{c.get('name')} (`{c.get('id')}`: {c.get('field')}={c.get('to')})" for c in status_changed)
            lines.append(f"- **Смена статуса ({len(status_changed)}):** {st_str}")
        if transfers:
            lines.append(f"- **Перемещения предметов ({len(transfers)}):**")
            for t in transfers:
                item = t.get("item", t.get("sid", "предмет"))
                from_str = ", ".join(f"{k} ({v})" for k, v in sorted(t.get("from", {}).items()))
                to_str = ", ".join(f"{k} (+{v})" for k, v in sorted(t.get("to", {}).items()))
                lines.append(f"  - `{item}`: от [{from_str}] к [{to_str}]")
        if skills:
            lines.append(f"- **Изменения навыков:** у {len(skills)} участников")
            for sk in sorted(skills, key=lambda s: (str(s.get("name") or ""), str(s.get("id") or ""))):
                ch_str = ", ".join(f"{k}: {v.get('from')} -> {v.get('to')}" for k, v in sorted(sk.get("changes", {}).items()))
                lines.append(f"  - {sk.get('name')} (`{sk.get('id')}`): {ch_str}")
        if equipment:
            lines.append(f"- **Смена экипировки:** у {len(equipment)} участников")
        if health:
            lines.append(f"- **Изменения здоровья:** у {len(health)} участников")

        if not any([joined, left, squad_changed, status_changed, transfers, skills, equipment, health]):
            lines.append("- Изменений в отслеживаемых категориях не зафиксировано.")

    not_compared = changes.get("not_compared", [])
    if not_compared:
        lines.extend([
            "",
            "## Несравниваемые категории",
        ])
        for nc in sorted(not_compared):
            lines.append(f"- {nc}")

    warnings = current.get("warnings", [])
    if warnings:
        lines.extend([
            "",
            "## Предупреждения и неполнота данных",
        ])
        for w in sorted(set(warnings)):
            lines.append(f"- [Неполнота данных] {w}")

    lines.extend([
        "",
        "## Связанные артефакты",
        "- [Детальный снимок состояния (current.json)](current.json)",
        "- [Детальные изменения (changes.json)](changes.json)",
        "- [Полный состав отряда (squad.md)](squad.md)",
        "",
    ])

    return "\n".join(lines)


def render_squad(current: dict[str, Any], snapshot_id: str) -> str:
    characters = current.get("characters", [])
    squads = current.get("squads", [])

    lines: list[str] = [
        f"# Состав отряда: {snapshot_id}",
        "",
    ]

    squad_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for c in characters:
        squad_map[c.get("squad_id") or "default"].append(c)

    for sq in sorted(squads, key=lambda s: s.get("squad_id", "")):
        sq_id = sq.get("squad_id", "")
        sq_name = sq.get("squad_name") or sq_id
        members = squad_map.get(sq_id, [])
        lines.extend([
            f"## Отряд `{sq_id}`: {sq_name}",
            f"Количество участников: {len(members)}",
            "",
        ])

        sorted_members = sorted(
            members,
            key=lambda ch: (str(ch.get("name") or ""), str(ch.get("id") or "")),
        )

        for c in sorted_members:
            cid = c.get("id") or "не указан"
            name = c.get("name") or "Без имени"
            race = c.get("race") or "не указана"

            lines.extend([
                f"### {name} (`{cid}`)",
                f"- **Раса:** {race}",
            ])

            cond = c.get("condition", {})
            if cond:
                c_parts = []
                if "blood" in cond:
                    c_parts.append(f"кровь {cond['blood']}")
                if "hunger_raw" in cond:
                    c_parts.append(f"сытость {cond['hunger_raw']}")
                if c_parts:
                    lines.append(f"- **Состояние:** {', '.join(c_parts)}")

            status = c.get("status", {})
            if status:
                st_active = [k for k, v in sorted(status.items()) if v]
                if st_active:
                    lines.append(f"- **Статусы:** {', '.join(st_active)}")

            skills = c.get("skills_base", {})
            attrs = c.get("attributes_base", {})
            if attrs:
                attr_str = ", ".join(f"{k}: {v}" for k, v in sorted(attrs.items()))
                lines.append(f"- **Атрибуты:** {attr_str}")
            if skills:
                sk_items = [f"{k}: {v}" for k, v in sorted(skills.items()) if isinstance(v, (int, float)) and v > 0]
                if sk_items:
                    lines.append(f"- **Навыки:** {', '.join(sk_items)}")

            weapons = c.get("weapons", [])
            if weapons:
                w_str = ", ".join(
                    f"{w.get('slot', 'weapon')}: {w.get('name') or w.get('sid')} ({w.get('grade', 'уровень')} {w.get('level', '')})"
                    for w in sorted(weapons, key=lambda w: w.get("slot", ""))
                )
                lines.append(f"- **Оружие:** {w_str}")

            armour = c.get("armour", {})
            if armour:
                a_str = ", ".join(
                    f"{slot}: {item.get('name') or item.get('sid')} (ур. {item.get('level', '')})"
                    for slot, item in sorted(armour.items())
                    if isinstance(item, dict)
                )
                lines.append(f"- **Броня:** {a_str}")

            prosthetics = c.get("prosthetics", [])
            if prosthetics:
                p_str = ", ".join(
                    f"{p.get('limb')}: {p.get('name') or p.get('sid')}"
                    for p in sorted(prosthetics, key=lambda p: p.get("limb", ""))
                )
                lines.append(f"- **Протезы:** {p_str}")

            inv = c.get("inventory", [])
            bp = (c.get("backpack") or {}).get("contents", [])
            all_items: Counter[str] = Counter()
            for it in inv:
                all_items[it.get("name") or it.get("sid", "предмет")] += it.get("quantity", 1)
            for it in bp:
                all_items[f"{it.get('name') or it.get('sid', 'предмет')} (рюкзак)"] += it.get("quantity", 1)

            if all_items:
                inv_str = ", ".join(f"{k} x{v}" for k, v in sorted(all_items.items()))
                lines.append(f"- **Инвентарь:** {inv_str}")

            lines.append("")

    return "\n".join(lines)
