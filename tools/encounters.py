"""Справочник встреч `docs/reference/encounters.md` для игрового репозитория: сборка из таблиц справочника юнитов.

Источник - только CSV в `published/tables/` (`squad_spawns`, `squad_templates`, `squad_members`, `faction_relations`,
`faction_hostility`; `unit_templates` и `unit_template_gear` в текст не входят). Без LLM и без внешних данных: одни и те же таблицы
дают побайтно одинаковый результат (порядок задан сортировками ниже, окончания строк LF).
"""
from __future__ import annotations

import csv
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path
import re
import statistics
from typing import TYPE_CHECKING, Any, Mapping, Sequence

if TYPE_CHECKING:
    from tools.tables import Table

Row = Mapping[str, str]
Tables = Mapping[str, Sequence[Row]]

# Таблицы, без которых справочник не строится (старые выпуски без справочника юнитов их не содержат).
REQUIRED_TABLES: tuple[str, ...] = (
    "squad_spawns", "squad_templates", "squad_members", "faction_relations", "faction_hostility",
)
ENCOUNTERS_TABLE_REQUIRED: tuple[str, ...] = (
    "squad_spawns", "squad_templates", "squad_members", "unit_templates", "unit_template_gear",
)
ALL_TABLES: tuple[str, ...] = tuple(dict.fromkeys(REQUIRED_TABLES + ENCOUNTERS_TABLE_REQUIRED))
OUTPUT_RELATIVE = Path("docs") / "reference" / "encounters.md"

# Отряды региона с долей меньше порога сворачиваются в строку «Прочие (k)».
FOLD_SHARE_PERCENT = Decimal(3)
# В ячейке «Состав» - не больше стольких позиций; остальные заменяет «…».
MAX_COMPOSITION_ITEMS = 4
# В таблицу отношений входят фракции минимум с таким числом заданных отношений (kind = relation).
MIN_RELATIONS_LISTED = 3
# Служебная группа биомов без названия в данных игры (`NONE`): к региону карты не относится.
PLACEHOLDER_REGIONS = frozenset({"NONE"})
SUPERSCRIPT_CONDITION = "¹"

# Гендерная форма в локализации: «Мечни/кца1/» = мужской «к» + женский «ца». Выбирается мужской вариант.
_GENDER_MALE_ENDINGS: dict[str, str] = {"кца": "к", "каец": "ец", "аяый": "ый", "аяий": "ий"}
_GENDER_FORM = re.compile(r"/([^/\s]*?)\d*/")

_TEXT_INTRO = (
    "# Встречи по регионам и отряды\n\n"
    "Кто появляется в регионах и городах, из кого состоят отряды и как к ним относятся крупные фракции. Сведено из данных "
    "игры с набором модов кампании (`published/tables/`, выгрузка {game_time}). Для планирования маршрутов и этапов "
    "достаточно этого справочника; сырые таблицы (`squad_spawns.csv`, `squad_templates.csv`, `squad_members.csv`, "
    "`unit_templates.csv`, `faction_relations.csv`, `faction_hostility.csv`) нужны только для детальных вопросов механики.\n\n"
    "Как читать: «Доля» — относительный шанс отряда среди бездомных отрядов региона; «≈ на зону» — примерное число "
    "бездомных отрядов на зону карты; ¹ — отряд появляется только при условии World State. Названия отрядов и юнитов — "
    "как в данных игры; служебные имена на английском оставлены без перевода. Регион — группа биомов игры; её точная "
    "привязка к зонам карты в данных не задана.\n\n"
)
_TEXT_TOWNS = (
    "## Бродячие отряды городов\n\n"
    "Отряды, которые выходят из города в окрестности. Вес появления в данных не задан.\n\n"
    "| Город | Фракция города | Отряды |\n|---|---|---|\n"
)
_TEXT_RELATIONS = (
    "## Отношения фракций между собой\n\n"
    "Значения отношений из данных игры (−100…100); пары, которых нет в списке, равны исходному отношению фракции. "
    "«Нападает при встрече» — отношение не выше порога `enemy_classification` этой фракции. Отношения отряда игрока — "
    "в `published/tables/diplomacy.csv`.\n\n"
    "| Фракция | Исходное | Нападает при встрече | Прочие заданные отношения |\n|---|---|---|---|\n"
)


def load_tables(tables_dir: Path, names: Sequence[str] = ALL_TABLES) -> dict[str, list[dict[str, str]]]:
    """Читает CSV справочника юнитов; отсутствующий файл пропускается (проверяет `missing_tables`)."""
    tables: dict[str, list[dict[str, str]]] = {}
    for name in names:
        path = tables_dir / f"{name}.csv"
        if path.is_file():
            with path.open(encoding="utf-8", newline="") as handle:
                tables[name] = list(csv.DictReader(handle))
    return tables


def missing_tables(tables: Tables, required: Sequence[str] = REQUIRED_TABLES) -> list[str]:
    return [name for name in required if name not in tables]


def neutral_gender(text: str) -> str:
    """Заменяет гендерные формы локализации «/кца1/» на мужскую: «Мечни/кца1/» -> «Мечник»."""
    def pick(match: re.Match[str]) -> str:
        form = match.group(1)
        if form in _GENDER_MALE_ENDINGS:
            return _GENDER_MALE_ENDINGS[form]
        # Неизвестный шаблон «аб»: при чётной длине берётся вторая половина с мужским окончанием, иначе первая.
        half = len(form) // 2
        if len(form) % 2 == 0 and half:
            return form[half:] if form[half:].endswith(("ый", "ий", "ой", "ец")) else form[:half]
        return form
    return _GENDER_FORM.sub(pick, text)


def _cell(text: str) -> str:
    return neutral_gender(text).replace("|", "\\|")


def _percent(share: Decimal) -> str:
    return f"{share.quantize(Decimal(1), rounding=ROUND_HALF_EVEN)}%"


def _range(low: str, high: str) -> str:
    return f"{low}–{high}" if _num(high) > _num(low) else low


def _num(value: str) -> Decimal:
    return Decimal(value) if value not in ("", None) else Decimal(0)


def _squad_composition(members: Sequence[Row], template: Row | None) -> str:
    items: list[str] = []
    choose_count = sum(1 for m in members if m["role"] == "choosefrom list")
    choose_done = False
    for member in sorted(members, key=lambda m: (m["role"], m["member_sid"])):
        role, name = member["role"], _cell(member["member_name"] or member["member_sid"])
        if role == "leader":
            items.append(f"{name} ×{member['v0']}")
        elif role in ("squad", "squad2", "animals", "animals2"):
            if _num(member["v0"]) == 0 and _num(member["v1"]) == 0:
                continue  # запись-заглушка: число бойцов не задано, в отряд не попадает
            items.append(f"{name} {_range(member['v0'], member['v1'])}")
        elif role == "choosefrom list" and not choose_done:
            choose_done = True
            low = (template or {}).get("num_random_chars") or "0"
            high = (template or {}).get("num_random_chars_max") or "0"
            items.append(f"случайные {_range(low, high)} из {choose_count} вариантов")
    if not items:
        return "—"
    if len(items) > MAX_COMPOSITION_ITEMS:
        items = items[:MAX_COMPOSITION_ITEMS] + ["…"]
    return "; ".join(items)


def _regions(spawns: Sequence[Row]) -> list[tuple[str, str, list[Row]]]:
    """Группы биомов (name, spawn_amount, строки) по возрастанию названия; регионы с одним названием не склеиваются."""
    groups: dict[str, list[Row]] = defaultdict(list)
    for row in spawns:
        if row["source"] == "biome_homeless" and row["place_name_en"] not in PLACEHOLDER_REGIONS:
            groups[row["place_sid"]].append(row)
    result = []
    for sid, rows in groups.items():
        first = rows[0]
        result.append((first["place_name_ru"] or first["place_name_en"] or sid, first["spawn_amount"], rows))
    result.sort(key=lambda item: (item[0], item[2][0]["place_sid"]))
    return result


def _faction_name(template: Row | None) -> str:
    return _cell((template or {}).get("faction_names") or "")


def build_encounters(tables: Tables, game_time: str) -> str:
    """Собирает текст `encounters.md` из строк таблиц; `game_time` - игровое время выгрузки («Day 135 10:49»)."""
    absent = missing_tables(tables)
    if absent:
        raise ValueError("Нет таблиц справочника юнитов: " + ", ".join(absent))
    templates = {row["sid"]: row for row in tables["squad_templates"]}
    members: dict[str, list[Row]] = defaultdict(list)
    for row in tables["squad_members"]:
        members[row["squad_sid"]].append(row)
    spawns = tables["squad_spawns"]

    def squad_label(row: Row) -> str:
        template = templates.get(row["squad_sid"])
        mark = f" {SUPERSCRIPT_CONDITION}" if template and template.get("world_state_sids") else ""
        return _cell(row["squad_name"] or row["squad_sid"]) + mark

    def squad_members(row: Row) -> str:
        return _squad_composition(members.get(row["squad_sid"], []), templates.get(row["squad_sid"]))

    out: list[str] = [_TEXT_INTRO.format(game_time=game_time), "## Регионы\n\n"]
    for name, amount, rows in _regions(spawns):
        total = sum((_num(r["ref_v0"]) for r in rows), Decimal(0))
        ranked = sorted(rows, key=lambda r: (-_num(r["ref_v0"]), r["squad_sid"]))
        lines, folded = [], Decimal(0)
        folded_count = 0
        for row in ranked:
            share = _num(row["ref_v0"]) * 100 / total if total else Decimal(0)
            if share < FOLD_SHARE_PERCENT:
                folded += share
                folded_count += 1
                continue
            lines.append(f"| {squad_label(row)} | {_faction_name(templates.get(row['squad_sid']))} | "
                         f"{squad_members(row)} | {_percent(share)} |")
        if folded_count:
            lines.append(f"| Прочие ({folded_count}) | | | {_percent(folded)} |")
        header = f"≈ {amount} на зону." if amount else "Число бездомных отрядов на зону в данных не задано."
        out.append(f"### {_cell(name)}\n\n{header}\n\n| Отряд | Фракция | Состав | Доля |\n|---|---|---|---|\n")
        out.append("\n".join(lines) + "\n\n")

    out.append(_TEXT_TOWNS)
    # Города с одинаковыми названием и фракцией (дубли записей в модах) сводятся в одну строку без повторов отрядов.
    towns: dict[tuple[str, str], dict[str, Row]] = defaultdict(dict)
    for row in spawns:
        if row["source"] == "town_roaming":
            towns[(row["place_name_ru"] or row["place_name_en"], row["place_faction_sid"])][row["squad_sid"]] = row
    faction_names = {r["faction_sid"]: r["faction_name_ru"] or r["faction_name_en"] for r in tables["faction_hostility"]}
    for (city, faction_sid), by_squad in sorted(towns.items()):
        squads = "<br>".join(f"{squad_label(r)} ({squad_members(r)})" for _sid, r in sorted(by_squad.items()))
        out.append(f"| {_cell(city)} | {_cell(faction_names.get(faction_sid, ''))} | {squads} |\n")
    out.append("\n")

    out.append(_TEXT_RELATIONS)
    out.append(_relations_table(tables["faction_hostility"], tables["faction_relations"]))
    return "".join(out)


def _relations_table(hostility: Sequence[Row], relations: Sequence[Row]) -> str:
    by_faction: dict[str, list[Row]] = defaultdict(list)
    for row in relations:
        if row["kind"] == "relation" and row["value"] != "":
            by_faction[row["faction_sid"]].append(row)
    lines: list[tuple[str, str]] = []
    for faction in hostility:
        pairs = by_faction.get(faction["faction_sid"], [])
        if len(pairs) < MIN_RELATIONS_LISTED:
            continue
        threshold = _num(faction["enemy_classification"])
        ordered = sorted(pairs, key=lambda r: (_num(r["value"]), r["other_sid"]))
        attacks = [f"{_cell(r['other_name'])} {_signed(r['value'], False)}" for r in ordered if _num(r["value"]) <= threshold]
        others = [f"{_cell(r['other_name'])} {_signed(r['value'], True)}" for r in ordered if _num(r["value"]) > threshold]
        name = faction["faction_name_ru"] or faction["faction_name_en"]
        lines.append((name, f"| {_cell(name)} | {faction['default_relation'] or '0'} | {', '.join(attacks) or '—'} | "
                            f"{', '.join(others) or '—'} |\n"))
    lines.sort(key=lambda item: item[0])
    return "".join(text for _name, text in lines)


def _signed(value: str, plus: bool) -> str:
    number = int(_num(value))
    return f"+{number}" if plus and number >= 0 else str(number)


def build_encounters_from_dir(tables_dir: Path, game_time: str) -> str:
    return build_encounters(load_tables(tables_dir), game_time)


ARMOUR_GRADES: dict[int, str] = {
    0: "Прототип",
    1: "Низкий",
    2: "Стандартный",
    3: "Высокий",
    4: "Специалист",
    5: "Мастерский",
}

ARMOUR_GRADE_NAME_TO_INT: dict[str, int] = {
    "прототип": 0, "прото": 0, "prototype": 0,
    "низкий": 1, "низкое": 1, "shoddy": 1,
    "стандартный": 2, "стандартное": 2, "standard": 2,
    "высокий": 3, "высокое": 3, "high": 3,
    "специалист": 4, "специальный": 4, "specialist": 4,
    "мастерский": 5, "шедевр": 5, "masterwork": 5,
}

REGION_ENCOUNTERS_COLUMNS: list[tuple[str, str]] = [
    ("region_name_en", "Регион/биом (en)"),
    ("region_name_ru", "Регион/биом (ru)"),
    ("place_sid", "sid биом-группы или города"),
    ("source", "Источник появления (biome_homeless или town_roaming)"),
    ("town_name", "Название города (для town_roaming)"),
    ("squad_sid", "sid шаблона отряда"),
    ("squad_name", "Название отряда"),
    ("faction_sid", "sid фракции отряда"),
    ("faction_name", "Название фракции"),
    ("spawn_share", "Доля появления в регионе/городе, %"),
    ("squad_size_min", "Минимальный размер отряда"),
    ("squad_size_max", "Максимальный размер отряда"),
    ("members_summary", "Краткий состав отряда"),
    ("leader_template_name", "Имя шаблона лидера"),
    ("leader_combat_skill", "Боевой навык лидера"),
    ("leader_weapon", "Оружие лидера"),
    ("leader_armour", "Броня лидера"),
    ("members_combat_min", "Мин. боевой навык рядовых"),
    ("members_combat_median", "Медианный боевой навык рядовых"),
    ("members_combat_max", "Макс. боевой навык рядовых"),
    ("weapons_typical", "Типичное оружие рядовых"),
    ("armour_grade_max", "Макс. грейд брони в отряде"),
    ("world_state_conditions", "Условия World State"),
]


def _unit_combat_skill(unit: Row | None) -> int | None:
    """Извлекает боевой навык юнита (атака / сила животного / combat_stats)."""
    if not unit:
        return None
    kind = unit.get("kind")
    if kind == "animal":
        val = unit.get("animal_strength")
        if val not in ("", None):
            return int(_num(val))
        val = unit.get("stat_attack")
        if val not in ("", None):
            return int(_num(val))
        val = unit.get("combat_stats")
        if val not in ("", None):
            return int(_num(val))
        return None
    val = unit.get("stat_attack")
    if val not in ("", None, "0"):
        return int(_num(val))
    val = unit.get("combat_stats")
    if val not in ("", None, "0"):
        return int(_num(val))
    if val not in ("", None):
        return int(_num(val))
    val = unit.get("stat_attack")
    if val not in ("", None):
        return int(_num(val))
    return None


def _parse_armour_grade(val: Any) -> tuple[int | None, str]:
    """Разбирает грейд брони в (числовой код 0..5, строковое название)."""
    if val in ("", None):
        return None, ""
    s = str(val).strip()
    if s.lower() in ARMOUR_GRADE_NAME_TO_INT:
        code = ARMOUR_GRADE_NAME_TO_INT[s.lower()]
        return code, ARMOUR_GRADES.get(code, s)
    try:
        code = int(_num(s))
        return code, ARMOUR_GRADES.get(code, s)
    except Exception:
        return None, s


def _squad_size_range(members: Sequence[Row], template: Row | None) -> tuple[int, int]:
    """Рассчитывает минимальный и максимальный размер отряда по ролям участников."""
    min_size = 0
    max_size = 0
    has_choose = False
    for m in members:
        role = m.get("role")
        v0 = int(_num(m.get("v0")))
        v1 = int(_num(m.get("v1")))
        if role == "leader":
            min_size += v0
            max_size += v0
        elif role in ("squad", "squad2", "animals", "animals2"):
            if v0 == 0 and v1 == 0:
                continue
            low = v0
            high = v1 if v1 > v0 else v0
            min_size += low
            max_size += high
        elif role == "choosefrom list":
            has_choose = True
    if has_choose and template:
        low = int(_num(template.get("num_random_chars")))
        high = int(_num(template.get("num_random_chars_max")))
        if high < low:
            high = low
        min_size += low
        max_size += high
    return min_size, max_size


def build_region_encounters(tables: Tables) -> list[dict[str, Any]]:
    """Собирает плоскую аналитическую витрину встреч по регионам и городам."""
    absent = missing_tables(tables, ENCOUNTERS_TABLE_REQUIRED)
    if absent:
        raise ValueError("Нет таблиц для витрины встреч: " + ", ".join(absent))

    templates = {row["sid"]: row for row in tables["squad_templates"]}
    members_by_squad: dict[str, list[Row]] = defaultdict(list)
    for row in tables["squad_members"]:
        members_by_squad[row["squad_sid"]].append(row)

    units: dict[str, Row] = {row["sid"]: row for row in tables["unit_templates"]}
    gear_by_unit: dict[str, list[Row]] = defaultdict(list)
    for row in tables["unit_template_gear"]:
        gear_by_unit[row["template_sid"]].append(row)

    faction_names: dict[str, str] = {}
    if "faction_hostility" in tables:
        for r in tables["faction_hostility"]:
            if r.get("faction_sid"):
                faction_names[r["faction_sid"]] = r.get("faction_name_ru") or r.get("faction_name_en") or ""

    spawns_by_place: dict[tuple[str, str], list[Row]] = defaultdict(list)
    for r in tables["squad_spawns"]:
        if r.get("place_name_en") in PLACEHOLDER_REGIONS:
            continue
        spawns_by_place[(r.get("source", ""), r.get("place_sid", ""))].append(r)

    rows: list[dict[str, Any]] = []
    for (source, place_sid), place_spawns in spawns_by_place.items():
        total_weight = sum((_num(r.get("ref_v0")) for r in place_spawns), Decimal(0))
        for r in place_spawns:
            sq_sid = r.get("squad_sid", "")
            t = templates.get(sq_sid)
            mems = members_by_squad.get(sq_sid, [])
            share = _num(r.get("ref_v0")) * 100 / total_weight if total_weight else Decimal(0)
            spawn_share = _percent(share)
            size_min, size_max = _squad_size_range(mems, t)

            # Параметры лидера
            leader = next((m for m in sorted(mems, key=lambda m: (m["role"], m["member_sid"])) if m["role"] == "leader"), None)
            if leader:
                l_sid = leader["member_sid"]
                l_u = units.get(l_sid)
                l_name = (l_u.get("name_ru") or l_u.get("name_en") or leader.get("member_name") or l_sid) if l_u else (leader.get("member_name") or l_sid)
                leader_template_name = neutral_gender(l_name)
                l_skill = _unit_combat_skill(l_u)
                leader_combat_skill = l_skill if l_skill is not None else ""

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
                leader_combat_skill = ""
                leader_weapon = ""
                leader_armour = ""

            # Боевые метрики рядовых участников (без лидера)
            regular = [m for m in mems if m["role"] != "leader" and not (_num(m.get("v0")) == 0 and _num(m.get("v1")) == 0)]
            reg_skills = []
            for m in regular:
                sk = _unit_combat_skill(units.get(m["member_sid"]))
                if sk is not None:
                    reg_skills.append(sk)

            if reg_skills:
                members_combat_min = min(reg_skills)
                members_combat_max = max(reg_skills)
                members_combat_median = int(round(statistics.median(reg_skills)))
            elif leader_combat_skill != "":
                members_combat_min = leader_combat_skill
                members_combat_median = leader_combat_skill
                members_combat_max = leader_combat_skill
            else:
                members_combat_min = ""
                members_combat_median = ""
                members_combat_max = ""

            # Типичное оружие рядовых отряда
            typical_weapons = []
            for m in regular:
                for g in gear_by_unit.get(m["member_sid"], []):
                    if g.get("category") in ("weapons", "crossbows", "weapon") and g.get("ref_name"):
                        wname = neutral_gender(g["ref_name"])
                        if wname not in typical_weapons:
                            typical_weapons.append(wname)
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
            armour_grade_max = max(squad_grade_codes) if squad_grade_codes else ""

            # Фракция отряда
            faction_sid = (t or {}).get("faction_sids") or ""
            faction_name = neutral_gender((t or {}).get("faction_names") or "")
            if not faction_sid and r.get("place_faction_sid"):
                faction_sid = r.get("place_faction_sid")
            if not faction_name and faction_sid in faction_names:
                faction_name = neutral_gender(faction_names[faction_sid])

            rows.append({
                "region_name_en": r.get("place_name_en") or "",
                "region_name_ru": r.get("place_name_ru") or r.get("place_name_en") or "",
                "place_sid": r.get("place_sid") or "",
                "source": r.get("source") or "",
                "town_name": (r.get("place_name_ru") or r.get("place_name_en") or "") if r.get("source") == "town_roaming" else "",
                "squad_sid": sq_sid,
                "squad_name": neutral_gender(r.get("squad_name") or sq_sid),
                "faction_sid": faction_sid,
                "faction_name": faction_name,
                "spawn_share": spawn_share,
                "squad_size_min": size_min,
                "squad_size_max": size_max,
                "members_summary": _squad_composition(mems, t),
                "leader_template_name": leader_template_name,
                "leader_combat_skill": leader_combat_skill,
                "leader_weapon": leader_weapon,
                "leader_armour": leader_armour,
                "members_combat_min": members_combat_min,
                "members_combat_median": members_combat_median,
                "members_combat_max": members_combat_max,
                "weapons_typical": weapons_typical,
                "armour_grade_max": armour_grade_max,
                "world_state_conditions": (t or {}).get("world_state_sids") or "",
            })

    rows.sort(key=lambda r: (r["region_name_en"], r["source"], r["squad_name"], r["squad_sid"]))
    return rows


build_region_encounters_rows = build_region_encounters


def region_encounters_table(tables: Tables) -> Table:
    """Оборачивает строки витрины в объект Table (tools/tables.py)."""
    from tools.tables import Table
    rows = build_region_encounters(tables)
    return Table(
        name="region_encounters.csv",
        sort_key="region_name_en, source, squad_name, squad_sid",
        purpose="Плоская аналитическая витрина встреч и бродячих отрядов по регионам и городам",
        columns=REGION_ENCOUNTERS_COLUMNS,
        rows=rows,
        join_keys="region_name_en, squad_sid, faction_sid",
    )
