"""Витрина гарнизонов и постоянных обитателей объектов мира `location_garrisons.csv`.

Связывает физические объекты карты мира (`instance_id` из `world_reference.json`) с их постоянными
отрядами-резидентами (`residents` из шаблонов `TOWN`), боевыми характеристиками лидера и рядовых,
оружием, броней и условиями World State.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Sequence

from tools.encounters import (
    ARMOUR_GRADES,
    _aggregate_stats,
    _num,
    _parse_armour_grade,
    _percent,
    _range,
    _squad_composition,
    _squad_size_range,
    _unit_stat_attack,
    _unit_stat_defence,
    _unit_stat_strength,
    _unit_stat_toughness,
    _unit_stats_randomise,
    neutral_gender,
)
if TYPE_CHECKING:
    from tools.tables import Table

LOCATION_GARRISONS_COLUMNS: list[tuple[str, str]] = [
    ("instance_id", "ID физического объекта на карте мира"),
    ("location_name_en", "Название объекта (en)"),
    ("location_name_ru", "Название объекта (ru)"),
    ("template_sid", "sid шаблона объекта (TOWN)"),
    ("kind", "Тип объекта"),
    ("region_name_en", "Регион расположения (en)"),
    ("region_name_ru", "Регион расположения (ru)"),
    ("squad_sid", "sid шаблона отряда"),
    ("squad_name", "Название отряда"),
    ("faction_sid", "sid фракции отряда"),
    ("faction_name", "Название фракции"),
    ("spawn_share", "Доля среди гарнизонов объекта, %"),
    ("squad_size_min", "Минимальный размер отряда"),
    ("squad_size_max", "Максимальный размер отряда"),
    ("members_summary", "Краткий состав отряда"),
    ("leader_template_name", "Имя шаблона лидера"),
    ("leader_attack", "Атака лидера"),
    ("leader_defence", "Защита лидера"),
    ("leader_strength", "Сила лидера"),
    ("leader_toughness", "Стойкость лидера"),
    ("leader_stats_randomise", "Разброс характеристик лидера (+/-)"),
    ("leader_weapon", "Оружие лидера"),
    ("leader_armour", "Броня лидера"),
    ("members_attack_min", "Мин. атака рядовых"),
    ("members_attack_median", "Медианная атака рядовых"),
    ("members_attack_max", "Макс. атака рядовых"),
    ("members_defence_min", "Мин. защита рядовых"),
    ("members_defence_median", "Медианная защита рядовых"),
    ("members_defence_max", "Макс. защита рядовых"),
    ("members_strength_min", "Мин. сила рядовых"),
    ("members_strength_median", "Медианная сила рядовых"),
    ("members_strength_max", "Макс. сила рядовых"),
    ("members_toughness_min", "Мин. стойкость рядовых"),
    ("members_toughness_median", "Медианная стойкость рядовых"),
    ("members_toughness_max", "Макс. стойкость рядовых"),
    ("members_stats_randomise", "Разброс характеристик рядовых (+/-)"),
    ("weapons_typical", "Типичное оружие рядовых"),
    ("armour_grade_max", "Макс. грейд брони в отряде"),
    ("world_state_conditions", "Условия World State"),
]


def build_location_garrisons(
    world_ref: dict[str, Any] | None,
    game_records: dict[str, Any] | None,
    game_index: dict[str, list[Any]] | None = None,
    enc_tables: dict[str, list[dict[str, str]]] | None = None,
) -> list[dict[str, Any]]:
    """Собирает строки аналитической витрины гарнизонов объектов мира.

    Для объектов без резидентов в данных игры строки не создаются.
    """
    if not world_ref or not world_ref.get("locations") or not game_records:
        return []

    from tools.tables import (
        _label,
        _names,
        _refs,
        _units,
        faction_hostility_table,
        squad_members_table,
        squad_templates_table,
        unit_template_gear_table,
        unit_templates_table,
    )

    # Подготовка справочных строк таблиц
    if enc_tables and all(k in enc_tables for k in ("squad_templates", "squad_members", "unit_templates", "unit_template_gear")):
        sq_templates_rows = enc_tables.get("squad_templates") or []
        sq_members_rows = enc_tables.get("squad_members") or []
        unit_templates_rows = enc_tables.get("unit_templates") or []
        unit_gear_rows = enc_tables.get("unit_template_gear") or []
        f_host_rows = enc_tables.get("faction_hostility") or []
    else:
        sq_templates_rows = squad_templates_table(game_records, game_index).rows
        sq_members_rows = squad_members_table(game_records, game_index).rows
        unit_templates_rows = unit_templates_table(game_records, game_index).rows
        unit_gear_rows = unit_template_gear_table(game_records, game_index).rows
        f_host_rows = faction_hostility_table(game_records, game_index).rows

    templates: dict[str, dict[str, Any]] = {row["sid"]: row for row in sq_templates_rows}
    members_by_squad: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in sq_members_rows:
        members_by_squad[row["squad_sid"]].append(row)

    units: dict[str, dict[str, Any]] = {row["sid"]: row for row in unit_templates_rows}
    gear_by_unit: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in unit_gear_rows:
        gear_by_unit[row["template_sid"]].append(row)

    faction_names: dict[str, str] = {}
    for r in f_host_rows:
        if r.get("faction_sid"):
            faction_names[r["faction_sid"]] = r.get("faction_name_ru") or r.get("faction_name_en") or ""

    units_data = _units(game_records)
    towns_by_sid: dict[str, dict[str, Any]] = {t["sid"]: t for t in units_data.get("towns") or []}

    # Словарь русских названий регионов
    region_ru_by_en: dict[str, str] = {}
    for b in units_data.get("biomes") or []:
        b_name = b.get("name")
        if b_name:
            ru = _names(game_index, b["sid"])[1]
            if ru:
                region_ru_by_en[b_name] = ru
    if enc_tables and "squad_spawns" in enc_tables:
        for r in enc_tables["squad_spawns"]:
            if r.get("source") == "biome_homeless" and r.get("place_name_en") and r.get("place_name_ru"):
                region_ru_by_en.setdefault(r["place_name_en"], r["place_name_ru"])

    rows: list[dict[str, Any]] = []

    for loc in world_ref.get("locations") or []:
        instance_id = loc.get("instance_id") or ""
        template_sid = loc.get("template_sid") or ""
        if not template_sid:
            continue
        town = towns_by_sid.get(template_sid)
        if not town:
            continue
        residents = _refs(town, "residents")
        if not residents:
            continue

        reg_en = loc.get("region") or ""
        reg_ru = region_ru_by_en.get(reg_en) or reg_en

        total_weight = sum((_num(v[0]) if len(v) > 0 else Decimal(0) for _sid, v in residents), Decimal(0))

        for sq_sid, v in residents:
            v0 = v[0] if len(v) > 0 else 0
            if total_weight:
                share = _num(str(v0)) * 100 / total_weight
            elif len(residents):
                share = Decimal(100) / Decimal(len(residents))
            else:
                share = Decimal(0)
            spawn_share = _percent(share)

            t = templates.get(sq_sid)
            mems = members_by_squad.get(sq_sid, [])
            size_min, size_max = _squad_size_range(mems, t)

            # Параметры лидера
            leader = next((m for m in sorted(mems, key=lambda m: (m["role"], m["member_sid"])) if m["role"] == "leader"), None)
            if leader:
                l_sid = leader["member_sid"]
                l_u = units.get(l_sid)
                l_name = (l_u.get("name_ru") or l_u.get("name_en") or leader.get("member_name") or l_sid) if l_u else (leader.get("member_name") or l_sid)
                leader_template_name = neutral_gender(l_name)
                leader_attack = _unit_stat_attack(l_u)
                leader_defence = _unit_stat_defence(l_u)
                leader_strength = _unit_stat_strength(l_u)
                leader_toughness = _unit_stat_toughness(l_u)
                leader_stats_randomise = _unit_stats_randomise(l_u)

                l_gear = gear_by_unit.get(l_sid, [])
                l_weapons = [neutral_gender(g["ref_name"]) for g in l_gear if g.get("category") in ("weapons", "crossbows", "weapon") and g.get("ref_name")]
                l_mfgs = [neutral_gender(g["ref_name"]) for g in l_gear if g.get("category") == "weapon level" and g.get("ref_name")]
                if l_weapons:
                    w_str = ", ".join(dict.fromkeys(l_weapons))
                    leader_weapon = f"{w_str} ({l_mfgs[0]})" if l_mfgs else w_str
                elif l_mfgs:
                    leader_weapon = f"({l_mfgs[0]})"
                else:
                    leader_weapon = ""

                l_clothing = [neutral_gender(g["ref_name"]) for g in l_gear if g.get("category") == "clothing" and g.get("ref_name")]
                l_ag = l_u.get("armour_grade") if l_u else None
                _l_code, grade_str = _parse_armour_grade(l_ag)
                if l_clothing:
                    c_str = ", ".join(dict.fromkeys(l_clothing))
                    leader_armour = f"{c_str} ({grade_str})" if grade_str else c_str
                elif grade_str:
                    leader_armour = grade_str
                else:
                    leader_armour = ""
            else:
                leader_template_name = ""
                leader_attack = None
                leader_defence = None
                leader_strength = None
                leader_toughness = None
                leader_stats_randomise = None
                leader_weapon = ""
                leader_armour = ""

            # Боевые метрики рядовых участников (без лидера)
            regular = [m for m in mems if m["role"] != "leader" and not (_num(m.get("v0")) == 0 and _num(m.get("v1")) == 0)]
            reg_attacks: list[int] = []
            reg_defences: list[int] = []
            reg_strengths: list[int] = []
            reg_toughnesses: list[int] = []
            reg_randomises: list[int] = []

            for m in regular:
                u = units.get(m["member_sid"])
                atk = _unit_stat_attack(u)
                if atk is not None:
                    reg_attacks.append(atk)
                df = _unit_stat_defence(u)
                if df is not None:
                    reg_defences.append(df)
                st_val = _unit_stat_strength(u)
                if st_val is not None:
                    reg_strengths.append(st_val)
                tgh = _unit_stat_toughness(u)
                if tgh is not None:
                    reg_toughnesses.append(tgh)
                rnd = _unit_stats_randomise(u)
                if rnd is not None:
                    reg_randomises.append(rnd)

            members_attack_min, members_attack_median, members_attack_max = _aggregate_stats(reg_attacks, leader_attack)
            members_defence_min, members_defence_median, members_defence_max = _aggregate_stats(reg_defences, leader_defence)
            members_strength_min, members_strength_median, members_strength_max = _aggregate_stats(reg_strengths, leader_strength)
            members_toughness_min, members_toughness_median, members_toughness_max = _aggregate_stats(reg_toughnesses, leader_toughness)

            if reg_randomises:
                members_stats_randomise: str = _range(str(min(reg_randomises)), str(max(reg_randomises)))
            elif leader_stats_randomise is not None:
                members_stats_randomise = str(leader_stats_randomise)
            else:
                members_stats_randomise = ""

            # Типичное оружие рядовых отряда (с учетом производителя/грейда)
            typical_weapons: list[str] = []
            for m in regular:
                u_gear = gear_by_unit.get(m["member_sid"], [])
                u_mfgs = [neutral_gender(g["ref_name"]) for g in u_gear if g.get("category") == "weapon level" and g.get("ref_name")]
                mfg_suffix = f" ({u_mfgs[0]})" if u_mfgs else ""
                for g in u_gear:
                    if g.get("category") in ("weapons", "crossbows", "weapon") and g.get("ref_name"):
                        wname = neutral_gender(g["ref_name"])
                        w_formatted = f"{wname}{mfg_suffix}"
                        if w_formatted not in typical_weapons:
                            typical_weapons.append(w_formatted)
            weapons_typical = "; ".join(sorted(typical_weapons))

            # Максимальный грейд брони в отряде
            squad_grade_codes: list[int] = []
            for m in mems:
                if _num(m.get("v0")) == 0 and _num(m.get("v1")) == 0 and m.get("role") != "leader":
                    continue
                u = units.get(m["member_sid"])
                if u and u.get("armour_grade") not in ("", None):
                    code, _ = _parse_armour_grade(u.get("armour_grade"))
                    if code is not None:
                        squad_grade_codes.append(code)
            if squad_grade_codes:
                max_code = max(squad_grade_codes)
                armour_grade_max = f"{max_code} ({ARMOUR_GRADES[max_code]})" if max_code in ARMOUR_GRADES else str(max_code)
            else:
                armour_grade_max = ""

            # Фракция отряда (если не задана в шаблоне, наследуется от объекта TOWN)
            faction_sid = (t or {}).get("faction_sids") or ""
            faction_name = neutral_gender((t or {}).get("faction_names") or "")
            if not faction_sid:
                town_factions = _refs(town, "faction")
                if town_factions:
                    faction_sid = town_factions[0][0]
            if not faction_name:
                if faction_sid in faction_names:
                    faction_name = neutral_gender(faction_names[faction_sid])
                elif game_index and faction_sid in game_index:
                    faction_name = neutral_gender(_label(game_index, faction_sid) or "")

            # Название отряда
            squad_name = neutral_gender(
                (t or {}).get("name_ru")
                or (t or {}).get("name_en")
                or (game_index.get(sq_sid)[1] if game_index and game_index.get(sq_sid) else None)
                or (game_index.get(sq_sid)[0] if game_index and game_index.get(sq_sid) else None)
                or sq_sid
            )

            rows.append({
                "instance_id": instance_id,
                "location_name_en": loc.get("name_en") or "",
                "location_name_ru": loc.get("name_ru") or loc.get("name_en") or "",
                "template_sid": template_sid,
                "kind": loc.get("kind") or "",
                "region_name_en": reg_en,
                "region_name_ru": reg_ru,
                "squad_sid": sq_sid,
                "squad_name": squad_name,
                "faction_sid": faction_sid,
                "faction_name": faction_name,
                "spawn_share": spawn_share,
                "squad_size_min": size_min,
                "squad_size_max": size_max,
                "members_summary": _squad_composition(mems, t),
                "leader_template_name": leader_template_name,
                "leader_attack": leader_attack if leader_attack is not None else "",
                "leader_defence": leader_defence if leader_defence is not None else "",
                "leader_strength": leader_strength if leader_strength is not None else "",
                "leader_toughness": leader_toughness if leader_toughness is not None else "",
                "leader_stats_randomise": leader_stats_randomise if leader_stats_randomise is not None else "",
                "leader_weapon": leader_weapon,
                "leader_armour": leader_armour,
                "members_attack_min": members_attack_min,
                "members_attack_median": members_attack_median,
                "members_attack_max": members_attack_max,
                "members_defence_min": members_defence_min,
                "members_defence_median": members_defence_median,
                "members_defence_max": members_defence_max,
                "members_strength_min": members_strength_min,
                "members_strength_median": members_strength_median,
                "members_strength_max": members_strength_max,
                "members_toughness_min": members_toughness_min,
                "members_toughness_median": members_toughness_median,
                "members_toughness_max": members_toughness_max,
                "members_stats_randomise": members_stats_randomise,
                "weapons_typical": weapons_typical,
                "armour_grade_max": armour_grade_max,
                "world_state_conditions": (t or {}).get("world_state_sids") or "",
            })

    rows.sort(key=lambda r: (r["instance_id"], r["squad_name"], r["squad_sid"]))
    return rows


def location_garrisons_table(
    world_ref: dict[str, Any] | None,
    game_records: dict[str, Any] | None,
    game_index: dict[str, list[Any]] | None = None,
    enc_tables: dict[str, list[dict[str, str]]] | None = None,
) -> Table:
    """Оборачивает строки витрины гарнизонов объектов в объект Table."""
    from tools.tables import Table

    rows = build_location_garrisons(world_ref, game_records, game_index, enc_tables=enc_tables)
    return Table(
        name="location_garrisons.csv",
        sort_key="instance_id, squad_name, squad_sid",
        purpose="Гарнизоны и постоянные обитатели объектов мира",
        columns=LOCATION_GARRISONS_COLUMNS,
        rows=rows,
        join_keys="instance_id, template_sid, squad_sid, faction_sid",
    )
