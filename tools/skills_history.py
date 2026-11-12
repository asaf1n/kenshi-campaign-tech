"""Формирование исторических витрин skills_history.csv, campaign_history.csv и relations_history.csv.

Чистые функции без прямого обращения к глобальному состоянию: выборка цепочки дней через
campaign_chain, дельта-трекинг целочисленных уровней навыков и фиксация динамики отряда/дипломатии.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Sequence

from tools.index import campaign_chain
from tools.io_utils import read_json
from tools.tables import ATTRIBUTES, SKILL_LABELS, Table

TRACKED_SKILLS: tuple[str, ...] = tuple(sorted(set(ATTRIBUTES) | set(SKILL_LABELS.keys())))


def select_daily_snapshots(index_data: dict[str, Any], snapshot_id: str) -> list[tuple[int, str]]:
    """Отбирает строго последний снимок каждого игрового дня по хронологической цепочке.

    Обходит campaign_chain(index_data, snapshot_id), отсеивающей откаты перезагрузок.
    Для каждого снимка извлекает день из index_data["snapshots"][sid]["game_time"]["day"].
    Группирует по дню и сохраняет строго последний снимок каждого дня.
    Возвращает отсортированный по возрастанию дня список [(day, sid), ...].
    """
    chain = campaign_chain(index_data, snapshot_id)
    snapshots = index_data.get("snapshots") or {}
    daily: dict[int, str] = {}
    for sid in chain:
        entry = snapshots.get(sid)
        if not entry:
            continue
        gt = entry.get("game_time")
        if not isinstance(gt, dict):
            continue
        day = gt.get("day")
        if day is None:
            continue
        try:
            day_int = int(day)
        except (ValueError, TypeError):
            continue
        daily[day_int] = sid
    return sorted(daily.items(), key=lambda item: item[0])


def load_daily_snapshots(
    daily_pairs: Sequence[tuple[int, str]],
    snapshot_loader: Callable[[str], dict[str, Any]],
) -> list[tuple[int, dict[str, Any]]]:
    """Загружает данные снимков через переданный snapshot_loader."""
    return [(day, snapshot_loader(sid)) for day, sid in daily_pairs]


def load_daily_snapshots_from_dir(
    index_data: dict[str, Any],
    snapshot_id: str,
    snapshots_dir: Path,
) -> list[tuple[int, dict[str, Any]]]:
    """Читает snapshots_dir / sid / 'current.json' через tools.io_utils.read_json."""
    daily_pairs = select_daily_snapshots(index_data, snapshot_id)
    target_dir = Path(snapshots_dir)

    def _loader(sid: str) -> dict[str, Any]:
        path = target_dir / sid / "current.json"
        return read_json(path.read_bytes())

    return load_daily_snapshots(daily_pairs, _loader)


def build_skills_history(daily_snapshots: Sequence[tuple[int, dict[str, Any]]]) -> Table:
    """Формирует витрину skills_history.csv по цепочке дней.

    Колонки: day, character_id, character_name, skill, level.
    Отслеживает для каждого (character_id, skill) последнее зафиксированное значение int(level).
    Включает все 4 атрибута (ATTRIBUTES) и 33 навыка (SKILL_LABELS.keys()).
    При первом появлении персонажа записывает строки со всеми его непустыми атрибутами и навыками.
    На последующих днях записывает строку ТОЛЬКО если int(current_val) != int(previous_val).
    Сортировка: (int(r["day"]), str(r["character_id"]), str(r["skill"])).
    """
    columns = [
        ("day", "Игровой день"),
        ("character_id", "Идентификатор персонажа"),
        ("character_name", "Имя персонажа"),
        ("skill", "Название навыка или атрибута"),
        ("level", "Целочисленный уровень"),
    ]
    sorted_snapshots = sorted(daily_snapshots, key=lambda pair: int(pair[0]))
    last_seen_levels: dict[tuple[str, str], int] = {}
    seen_characters: set[str] = set()
    rows: list[dict[str, Any]] = []

    for day, cur in sorted_snapshots:
        characters = cur.get("characters") or []
        for c in characters:
            cid = c.get("id")
            if not cid:
                continue
            cid_str = str(cid)
            cname = c.get("name")
            is_new_char = cid_str not in seen_characters
            if is_new_char:
                seen_characters.add(cid_str)

            attrs = c.get("attributes_base") or {}
            skills = c.get("skills_base") or {}
            merged_stats = {**attrs, **skills}

            for skill_name in TRACKED_SKILLS:
                if skill_name not in merged_stats:
                    continue
                val = merged_stats[skill_name]
                if val is None or val == "":
                    continue
                try:
                    lvl = int(float(val))
                except (ValueError, TypeError):
                    continue

                if lvl < 0:
                    continue

                key = (cid_str, skill_name)
                if is_new_char or key not in last_seen_levels:
                    rows.append({
                        "day": day,
                        "character_id": cid,
                        "character_name": cname,
                        "skill": skill_name,
                        "level": lvl,
                    })
                    last_seen_levels[key] = lvl
                else:
                    prev_lvl = last_seen_levels[key]
                    if lvl != prev_lvl:
                        rows.append({
                            "day": day,
                            "character_id": cid,
                            "character_name": cname,
                            "skill": skill_name,
                            "level": lvl,
                        })
                        last_seen_levels[key] = lvl

    rows.sort(key=lambda r: (int(r["day"]), str(r["character_id"]), str(r["skill"])))
    return Table(
        "skills_history.csv",
        "day, character_id, skill",
        "История изменения уровней навыков и атрибутов",
        columns,
        rows,
        join_keys="character_id",
    )


def build_campaign_history(daily_snapshots: Sequence[tuple[int, dict[str, Any]]]) -> Table:
    """Формирует витрину campaign_history.csv по дням.

    Колонки: day, game_time, cats, squad_size.
    Сортировка: int(r["day"]).
    """
    columns = [
        ("day", "Игровой день"),
        ("game_time", "Игровое время снимка"),
        ("cats", "Баланс казны игрока"),
        ("squad_size", "Численность отряда"),
    ]
    sorted_snapshots = sorted(daily_snapshots, key=lambda pair: int(pair[0]))
    rows: list[dict[str, Any]] = []

    for day, cur in sorted_snapshots:
        world = cur.get("world") or {}
        time_str = world.get("time") or "00:00"
        game_time = f"Day {day} {time_str}"
        cats = world.get("money")
        characters = cur.get("characters") or []
        squad_size = len(characters)
        rows.append({
            "day": day,
            "game_time": game_time,
            "cats": cats,
            "squad_size": squad_size,
        })

    rows.sort(key=lambda r: int(r["day"]))
    return Table(
        "campaign_history.csv",
        "day",
        "Динамика казны и численности отряда по дням",
        columns,
        rows,
        join_keys="day",
    )


def build_relations_history(daily_snapshots: Sequence[tuple[int, dict[str, Any]]]) -> Table:
    """Формирует витрину relations_history.csv по дням.

    Колонки: day, faction_sid, faction_name, relation.
    Отслеживает для каждой faction_sid последнее значение relation.
    При первом появлении фракции или изменении relation пишет строку.
    Сортировка: (int(r["day"]), str(r["faction_sid"])).
    """
    columns = [
        ("day", "Игровой день"),
        ("faction_sid", "sid фракции"),
        ("faction_name", "Название фракции"),
        ("relation", "Значение отношений"),
    ]
    sorted_snapshots = sorted(daily_snapshots, key=lambda pair: int(pair[0]))
    last_relations: dict[str, Any] = {}
    rows: list[dict[str, Any]] = []

    for day, cur in sorted_snapshots:
        dip = cur.get("diplomacy") or {}
        relations = dip.get("relations") or []
        for r in relations:
            faction_sid = r.get("faction_sid")
            if not faction_sid:
                continue
            faction_sid_str = str(faction_sid)
            rel = r.get("relation")
            if rel is None:
                continue

            if faction_sid_str not in last_relations:
                last_relations[faction_sid_str] = rel
                rows.append({
                    "day": day,
                    "faction_sid": faction_sid,
                    "faction_name": r.get("faction_name"),
                    "relation": rel,
                })
            else:
                prev_rel = last_relations[faction_sid_str]
                if rel != prev_rel:
                    last_relations[faction_sid_str] = rel
                    rows.append({
                        "day": day,
                        "faction_sid": faction_sid,
                        "faction_name": r.get("faction_name"),
                        "relation": rel,
                    })

    rows.sort(key=lambda r: (int(r["day"]), str(r["faction_sid"])))
    return Table(
        "relations_history.csv",
        "day, faction_sid",
        "История дипломатических отношений с фракциями",
        columns,
        rows,
        join_keys="faction_sid",
    )
