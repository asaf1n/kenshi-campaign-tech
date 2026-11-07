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
from typing import Mapping, Sequence

Row = Mapping[str, str]
Tables = Mapping[str, Sequence[Row]]

# Таблицы, без которых справочник не строится (старые выпуски без справочника юнитов их не содержат).
REQUIRED_TABLES: tuple[str, ...] = (
    "squad_spawns", "squad_templates", "squad_members", "faction_relations", "faction_hostility",
)
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


def load_tables(tables_dir: Path) -> dict[str, list[dict[str, str]]]:
    """Читает CSV справочника юнитов; отсутствующий файл пропускается (проверяет `missing_tables`)."""
    tables: dict[str, list[dict[str, str]]] = {}
    for name in REQUIRED_TABLES:
        path = tables_dir / f"{name}.csv"
        if path.is_file():
            with path.open(encoding="utf-8", newline="") as handle:
                tables[name] = list(csv.DictReader(handle))
    return tables


def missing_tables(tables: Tables) -> list[str]:
    return [name for name in REQUIRED_TABLES if name not in tables]


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
