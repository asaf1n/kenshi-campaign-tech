"""Детерминированная генерация русскоязычных Markdown-артефактов (brief.md, squad.md).

Генерация ведётся строго из проверенных JSON без обращения к часам или внешним данным.
Повторный рендер на одинаковых входных данных даёт побайтово идентичный результат.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import html
from typing import Any



def game_time_title(day: Any, time: Any) -> str:
    """Заголовок документа по игровому времени: `День 131, 15:06`; хеши снимков в заголовки не попадают."""
    if day is None:
        return "Срез без даты"
    return f"День {day}, {time}" if time is not None else f"День {day}"


def _publication_header(publication: dict[str, Any] | None, day: Any, time: Any) -> list[str]:
    """Шапка манифеста в начале brief.md: читатель без доступа к .json получает идентичность снимка из Markdown."""
    if publication is None:
        return []
    return [
        f"- **snapshot_id:** `{publication.get('snapshot_id')}`",
        f"- **previous_snapshot_id:** `{publication.get('parent_snapshot_id')}`",
        f"- **campaign_id:** `{publication.get('campaign_id')}`",
        *([f"- **reload:** перезагрузка: игровое время откатилось с `{publication['reload'].get('from_game_time')}` "
           f"до `Day {day}, {time}`; снимки позже `Day {day}, {time}` исключены из цепочки"]
          if publication.get("reload") else []),
        f"- **game_time:** `Day {day}, {time}`",
        f"- **generated_at:** `{publication.get('generated_at')}`",
        "",
    ]


def limb_summary(prosthetics: list[dict[str, Any]]) -> str:
    """Смена конечностей и протезов по участникам: `Имя (`id`): left_leg: None -> lost`."""
    parts = []
    for entry in prosthetics:
        details = ", ".join(f"{slot}: {value.get('from')} -> {value.get('to')}"
                            for slot, value in sorted((entry.get("changes") or {}).items()))
        parts.append(f"{entry.get('name')} (`{entry.get('id')}`): {details}")
    return "; ".join(parts)


def render_change_lines(changes: dict[str, Any], *, compact: bool = False) -> list[str]:
    """Маркированный список ключевых изменений дельты; общий для brief.md и changes.md.

    `compact` (brief.md): навыки, перемещения предметов и отношения — счётчиками; построчно они остаются в changes.md."""
    lines: list[str] = []
    valid = changes.get("valid", False)
    if not valid:
        reason = changes.get("reason") or "; ".join(changes.get("problems", [])) or "нет baseline для сравнения"
        lines.extend([
            f"- **Статус сравнения:** базовое сравнение отсутствует (`valid: false`).",
            f"- **Причина:** {reason}.",
            "- **Ключевые изменения:** отсутствуют (первая публикация либо несравнимые снимки).",
        ])
    else:
        money = (changes.get("world") or {}).get("money") or {}
        prosthetics = changes.get("prosthetics", [])
        if money.get("delta"):
            lines.append(f"- **Казна:** {money.get('from')} -> {money.get('to')} ({money['delta']:+})")
        if prosthetics:
            lines.append(f"- **Конечности и протезы ({len(prosthetics)}):** {limb_summary(prosthetics)}")
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
        if transfers and compact:
            lines.append(f"- **Перемещения предметов:** {len(transfers)}")
        elif transfers:
            lines.append(f"- **Перемещения предметов ({len(transfers)}):**")
            for t in transfers:
                item = t.get("item", t.get("sid", "предмет"))
                from_str = ", ".join(f"{k} ({v})" for k, v in sorted(t.get("from", {}).items()))
                to_str = ", ".join(f"{k} (+{v})" for k, v in sorted(t.get("to", {}).items()))
                lines.append(f"  - `{item}`: от [{from_str}] к [{to_str}]")
        if skills:
            lines.append(f"- **Изменения навыков:** у {len(skills)} участников")
            for sk in [] if compact else sorted(skills, key=lambda s: (str(s.get("name") or ""), str(s.get("id") or ""))):
                ch_str = ", ".join(f"{k}: {v.get('from')} -> {v.get('to')}" for k, v in sorted(sk.get("changes", {}).items()))
                lines.append(f"  - {sk.get('name')} (`{sk.get('id')}`): {ch_str}")
        if equipment:
            lines.append(f"- **Смена экипировки:** у {len(equipment)} участников")
        if health:
            lines.append(f"- **Изменения здоровья:** у {len(health)} участников")

        dip_ch = changes.get("diplomacy", [])
        if dip_ch and compact:
            lines.append(f"- **Изменения отношений с фракциями:** {len(dip_ch)}")
        elif dip_ch:
            lines.append(f"- **Изменения отношений с фракциями ({len(dip_ch)}):**")
            for d in dip_ch:
                d_name = d.get("faction_name") or d.get("faction_sid")
                relation = d.get("relation")
                rel_info = ""
                if relation:
                    rel_info = f"отношение: {relation.get('from')} -> {relation.get('to')}"
                    if isinstance(relation.get("delta"), (int, float)):
                        rel_info += f" ({relation['delta']:+})"
                lines.append(f"  - `{d_name}`: {rel_info}")

        ws_ch = changes.get("world_state", {})
        if ws_ch:
            u_ch = ws_ch.get("unique_npcs", [])
            t_ch = ws_ch.get("town_overrides", [])
            if u_ch or t_ch:
                lines.append(f"- **События мира (World States):**")
                for u in u_ch:
                    u_nm = u.get("name_ru") or u.get("name") or u.get("sid")
                    lines.append(f"  - Лидер `{u_nm}`: код состояния {u.get('state_code', {}).get('from')} -> {u.get('state_code', {}).get('to')}")
                for t in t_ch:
                    t_nm = t.get("town_name") or t.get("replacement_sid")
                    lines.append(f"  - Город `{t_nm}`: {t.get('status')} ({t.get('faction') or 'смена контроля'})")

        jobs_ch = changes.get("jobs", [])
        if jobs_ch:
            lines.append(f"- **Изменения очереди работ:** у {len(jobs_ch)} участников")
            for j in jobs_ch:
                j_nm = j.get("name") or j.get("id")
                lines.append(f"  - `{j_nm}`: обновлены назначенные работы")

        bld_ch = changes.get("base_storage", [])
        if bld_ch:
            lines.append(f"- **Изменения складских запасов на базах:** {len(bld_ch)} позиций")
            for b in bld_ch[:5]:
                lines.append(f"  - `{b.get('building_name')}`: {b.get('item_name')} {b.get('delta'):+}")
            if len(bld_ch) > 5:
                lines.append(f"  - ... и ещё {len(bld_ch) - 5} складских изменений")

        if not any([money.get("delta"), prosthetics, joined, left, squad_changed, status_changed, transfers, skills, equipment, health,
                    dip_ch, ws_ch, jobs_ch, bld_ch]):
            lines.append("- Изменений в отслеживаемых категориях не зафиксировано.")
    return lines


def render_timeline_events(
    timeline: list[dict[str, Any]],
    *,
    projection: bool = False,
) -> str:
    """Рендеринг событий хроники (используется для тестов и специализированных витрин)."""
    from tools.timeline import resolve_timeline
    from tools.validation import validate_timeline

    validate_timeline(timeline, projection=projection)
    facts = timeline if projection else resolve_timeline(timeline)
    lines: list[str] = ["## Хроника событий", ""]

    def event_line(event: dict[str, Any]) -> str:
        def safe(value: Any) -> str:
            return (
                html.escape(str(value))
                .replace("\n", " ")
                .replace("\r", " ")
                .replace("[", "&#91;")
                .replace("]", "&#93;")
                .replace("`", "&#96;")
            )

        sources = "; ".join(
            f"{safe(e['kind'])}: {safe(e['ref'])}" + (f" ({safe(e['note'])})" if e.get("note") else "")
            for e in event["evidence"]
        )
        corrected = f"; исправляет {safe(event['corrects_event_id'])}" if event.get("corrects_event_id") else ""
        day = f"день {event['game_day']}" if event.get("game_day") is not None else "игровой день неизвестен"
        return f"- [{event['status']}] {safe(event['summary'])} ({day}; ID: {safe(event['event_id'])}{corrected}). Источники: {sources}"

    lines.extend(event_line(e) for e in facts)
    if not facts:
        lines.append("Подтверждённые события отсутствуют.")
    hypotheses = (
        []
        if projection
        else [e for e in resolve_timeline(timeline, include_inferred=True) if e["status"] == "inferred"]
    )
    if hypotheses:
        lines.extend(["", "### Неподтверждённые гипотезы (inferred)", ""])
        lines.extend(event_line(e) for e in hypotheses)
    return "\n".join(lines)


def render_brief(
    current: dict[str, Any],
    changes: dict[str, Any],
    snapshot_id: str,
    locations: dict[str, Any] | None = None,
    timeline: list[dict[str, Any]] | None = None,
    *,
    timeline_projection: bool = False,
    publication: dict[str, Any] | None = None,
    screenshots_section: str | None = None,
) -> str:
    world = current.get("world", {})
    day = world.get("day")
    time = world.get("time")
    money = world.get("money")

    money_str = f"{money:,} кат.".replace(",", " ") if isinstance(money, (int, float)) else "не получено"

    characters = current.get("characters", [])
    squads = current.get("squads", [])
    humanoids = [c for c in characters if not c.get("is_animal")]
    animals = [c for c in characters if c.get("is_animal")]

    lines: list[str] = [
        f"# Краткая сводка: {game_time_title(day, time)}",
        "",
        *_publication_header(publication, day, time),
        f"- **Казна:** {money_str}",
        "",
        "## Состав и группы отряда",
        f"- **Всего участников:** {len(characters)} (гуманоидов: {len(humanoids)}, животных: {len(animals)})",
        f"- **Всего отрядов:** {len(squads)}",
    ]

    for sq in sorted(squads, key=lambda s: s.get("squad_id", "")):
        sq_id = sq.get("squad_id", "")
        sq_name = sq.get("squad_name") or sq_id
        members = [c for c in characters if c.get("squad_id") == sq_id]
        m_hum = [c for c in members if not c.get("is_animal")]
        m_anim = [c for c in members if c.get("is_animal")]
        anim_suffix = f" (гуманоидов: {len(m_hum)}, животных: {len(m_anim)})" if m_anim else ""
        lines.append(f"- **Отряд `{sq_id}`** ({sq_name}): {len(members)} участников{anim_suffix}")

    if locations is not None:
        spatial_groups = locations.get("spatial_groups", [])
        if spatial_groups:
            lines.extend(["", "### Расположение групп отряда"])
            for g in spatial_groups:
                g_chars = g.get("characters", [])
                lines.append(f"- **{g['label']}** (`{g['resolution_status']}`): {len(g_chars)} участников")
        else:
            lines.append("- Сведения о группах отряда отсутствуют в locations.json")
    else:
        lines.append("- не получено (географические координаты не разрешены)")

    if screenshots_section:
        lines.extend(["", screenshots_section.rstrip()])

    lines.extend([
        "",
        "## Ключевые изменения",
    ])
    lines.extend(render_change_lines(changes, compact=True))
    lines.extend([
        "",
        "Построчные изменения: [changes.md](changes.md)",
    ])

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
        "## Таблицы",
        "- [tables/index.md](tables/index.md) — оглавление; словари колонок — `tables/dict/`",
        "- [tables/squad.csv](tables/squad.csv) — отряд: навыки, состояние, расположение",
        "- [tables/region_encounters.csv](tables/region_encounters.csv) — отряды по регионам",
        "- [tables/location_garrisons.csv](tables/location_garrisons.csv) — обитатели объектов",
        "- [tables/unique_entities.csv](tables/unique_entities.csv) — уникальные NPC и Мейто",
        "- [tables/skills_history.csv](tables/skills_history.csv), [campaign_history.csv](tables/campaign_history.csv), [relations_history.csv](tables/relations_history.csv) — история по дням",
        "- [tables/storage.csv](tables/storage.csv) — склады; снаряжение и инвентарь — `equipment.csv`, `inventory.csv`",
        "- [tables/diplomacy.csv](tables/diplomacy.csv) — отношения с фракциями",
        "- [tables/town_overrides.csv](tables/town_overrides.csv) — переопределения городов",
        "- [changes.md](changes.md) — изменения между снимками",
        "- [screenshots/index.md](screenshots/index.md) — скриншоты",
        "- [locations.json](locations.json) — координаты и группы",
    ])
    if timeline is not None:
        lines.append("- [timeline.jsonl](timeline.jsonl) — журнал событий")
    lines.append("")

    return "\n".join(lines)


def render_squad(
    current: dict[str, Any],
    snapshot_id: str,
    locations: dict[str, Any] | None = None,
) -> str:
    characters = current.get("characters", [])
    squads = current.get("squads", [])

    loc_char_map = {c["id"]: c for c in locations.get("characters", [])} if locations else {}

    lines: list[str] = [
        f"# Состав отряда: {game_time_title(current.get('world', {}).get('day'), current.get('world', {}).get('time'))}",
        "",
    ]

    squad_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for c in characters:
        squad_map[c.get("squad_id") or "default"].append(c)

    for sq in sorted(squads, key=lambda s: s.get("squad_id", "")):
        sq_id = sq.get("squad_id", "")
        sq_name = sq.get("squad_name") or sq_id
        members = squad_map.get(sq_id, [])
        m_hum = [c for c in members if not c.get("is_animal")]
        m_anim = [c for c in members if c.get("is_animal")]
        anim_suffix = f" (гуманоидов: {len(m_hum)}, животных: {len(m_anim)})" if m_anim else ""
        lines.extend([
            f"## Отряд `{sq_id}`: {sq_name}",
            f"Количество участников: {len(members)}{anim_suffix}",
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
            animal_tag = " [Животное]" if c.get("is_animal") else ""

            lines.extend([
                f"### {name} (`{cid}`){animal_tag}",
                f"- **Раса:** {race}",
            ])

            # Добавляем географическую привязку
            if cid in loc_char_map:
                linfo = loc_char_map[cid]
                st = linfo["resolution_status"]
                if st == "in_location" and linfo.get("location"):
                    lname = linfo["location"].get("name_ru") or linfo["location"].get("name_en")
                    dist = linfo["location"].get("distance", 0.0)
                    lines.append(f"- **Местоположение:** {lname} (`in_location`, {dist:.1f} м от центра)")
                elif st == "near_location" and linfo.get("nearby_locations"):
                    top = linfo["nearby_locations"][0]
                    nname = top.get("name_ru") or top.get("name_en")
                    dist = top.get("distance", 0.0)
                    lines.append(f"- **Местоположение:** окрестности {nname} (`near_location`, {dist:.1f} м)")
                elif st == "in_region" and linfo.get("region"):
                    rname = linfo["region"].get("name_ru") or linfo["region"].get("name_en")
                    b_flag = " (граница)" if linfo["region"].get("border") else ""
                    lines.append(f"- **Местоположение:** регион {rname}{b_flag} (`in_region`)")
                else:
                    lines.append("- **Местоположение:** неизвестно (`unknown`)")

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

            combat = c.get("combat_modes", {})
            if combat:
                c_active = [k for k, v in sorted(combat.items()) if v]
                if c_active:
                    lines.append(f"- **Боевые режимы:** {', '.join(c_active)}")

            jobs = c.get("jobs", {})
            if isinstance(jobs, dict) and jobs.get("primary"):
                p_tasks = []
                for t in jobs["primary"]:
                    if isinstance(t, dict):
                        tn = t.get("task_name") or str(t.get("task_code"))
                        if t.get("target_name"):
                            tn += f" [{t['target_name']}]"
                        p_tasks.append(tn)
                if p_tasks:
                    en_str = "" if jobs.get("enabled", True) else " (отключены)"
                    lines.append(f"- **Работы{en_str}:** {', '.join(p_tasks)}")

            carrying = c.get("carrying", {})
            if isinstance(carrying, dict) and carrying.get("is_carrying"):
                target_desc = carrying.get("target_name") or (f"цель {carrying['target_handle']}" if carrying.get("target_handle") else "персонажа")
                lines.append(f"- **Переноска:** несёт {target_desc}")
            elif isinstance(carrying, dict) and carrying.get("carried_by"):
                carrier_desc = carrying.get("carried_by_name") or (f"персонаж {carrying['carried_by']}" if carrying.get("carried_by") else "кто-то")
                lines.append(f"- **Переноска:** на плече у {carrier_desc}")

            bounties = c.get("bounties", {})
            if isinstance(bounties, dict) and bounties.get("has_bounty"):
                total_b = bounties.get("total_amount", 0)
                lines.append(f"- **Награда за голову:** {total_b:,} кат.".replace(",", " "))

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
