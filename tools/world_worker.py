"""Изолированный сборщик данных мира из установленной Kenshi и модов через python -I -B."""
from __future__ import annotations

import contextlib
from datetime import datetime, timezone
import glob
import io
import math
import os
from pathlib import Path
import struct
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.io_utils import ExtractionError, canonical, digest, file_hash, install_write_guard, read_json
from tools.normalization import scrub

TOWN_TYPES = {
    1: "outpost",
    2: "town",
    3: "village",
    4: "ruins",
    5: "slave_camp",
    6: "military",
    7: "prison",
    9: "poi",
}


def _find_mod_path(stem: str, game: Path, workshop: Path) -> Path | None:
    mod_name = stem if stem.endswith(".mod") else f"{stem}.mod"
    raw_stem = stem[:-4] if stem.endswith(".mod") else stem
    candidates = [
        game / f"data/{mod_name}",
        game / f"mods/{raw_stem}/{mod_name}",
    ]
    for c in candidates:
        if c.is_file():
            return c
    workshop_hits = sorted(glob.glob(str(workshop / f"*/{mod_name}")))
    if workshop_hits:
        return Path(workshop_hits[0])
    return None


def run(request: dict[str, Any]) -> dict[str, Any]:
    workspace = Path(request["workspace"]).resolve()
    game = Path(request["game"]).resolve()
    workshop = Path(request["workshop"]).resolve()

    os.environ["KENSHI_DIR"] = str(game)
    os.environ["KENSHI_WORKSHOP_DIR"] = str(workshop)
    sys.pycache_prefix = str(workspace / "pycache")
    denied = install_write_guard(workspace)

    from tools import kenshi_gd
    from tools import gamedb
    from tools.save_reader import town_handle

    cache_dir = workspace / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    base_stems = ["base", "Newwworld", "Dialogue", "rebirth"]
    base_file_map = {
        "base": game / "data/gamedata.base",
        "Newwworld": game / "data/Newwworld.mod",
        "Dialogue": game / "data/Dialogue.mod",
        "rebirth": game / "data/rebirth.mod",
    }

    for stem, p in base_file_map.items():
        if not p.is_file():
            raise ExtractionError(f"Отсутствует обязательный базовый файл {stem}: {p}")

    mods_override = request.get("mods_override")
    extra_mod_names: list[str] = []
    if mods_override is not None:
        extra_mod_names = [m for m in mods_override if m not in base_stems and m != "base"]
    else:
        cfg_path = game / "data/mods.cfg"
        if cfg_path.is_file():
            for line in cfg_path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if line and line not in base_stems and line != "base":
                    extra_mod_names.append(line)

    active_mods = list(base_stems)
    active_mod_files: list[Path] = [base_file_map[s] for s in base_stems]

    for em in extra_mod_names:
        mp = _find_mod_path(em, game, workshop)
        if mp is not None and mp not in active_mod_files:
            active_mod_files.append(mp)
            active_mods.append(em[:-4] if em.endswith(".mod") else em)

    source_files: list[Path] = []
    source_hashes: dict[str, str] = {}

    def add_source_file(fpath: Path) -> None:
        p = fpath.resolve()
        if p in source_files or not p.is_file():
            return
        h = file_hash(p)
        try:
            rel = p.relative_to(game).as_posix()
        except ValueError:
            try:
                rel = f"workshop/{p.relative_to(workshop).as_posix()}"
            except ValueError:
                rel = p.name
        source_hashes[rel] = h
        source_files.append(p)

    for mf in active_mod_files:
        add_source_file(mf)

    leveldata_dirs: list[tuple[str, Path]] = []
    for mod_stem in active_mods:
        d_game = game / f"data/newland/leveldata/{mod_stem}"
        if d_game.is_dir() and (d_game / "leveldata.level").is_file():
            leveldata_dirs.append((mod_stem, d_game))
        else:
            mp = _find_mod_path(mod_stem, game, workshop)
            if mp is not None:
                d_mod = mp.parent / "leveldata"
                if d_mod.is_dir() and (d_mod / "leveldata.level").is_file():
                    leveldata_dirs.append((mod_stem, d_mod))

    if not leveldata_dirs:
        raise ExtractionError("Не найдено ни одного каталога leveldata в активных модулях")

    for _, ldir in leveldata_dirs:
        add_source_file(ldir / "leveldata.level")
        for zf in sorted(ldir.glob("zone.*.zone")):
            add_source_file(zf)

    areasmap_path = game / "data/newland/land/areasmap.tga"
    if not areasmap_path.is_file():
        raise ExtractionError(f"Отсутствует areasmap.tga: {areasmap_path}")
    add_source_file(areasmap_path)

    po_path = game / "locale/ru_RU/gamedata.po"
    if not po_path.is_file():
        raise ExtractionError(f"Отсутствует gamedata.po: {po_path}")
    add_source_file(po_path)

    version_path = game / "currentVersion.txt"
    if version_path.is_file():
        game_version = version_path.read_text(encoding="utf-8", errors="replace").strip()
    else:
        game_version = "Kenshi 1.0.68 - x64 (Newland)"

    max_mtime = max(p.stat().st_mtime for p in source_files)
    created_at = datetime.fromtimestamp(max_mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    sorted_hashes = {k: source_hashes[k] for k in sorted(source_hashes.keys())}
    world_ref_id = f"sha256-{digest(canonical(sorted_hashes))}"

    ru, types, by_msgid = gamedb.load_po(str(po_path))
    db = gamedb.build([str(p) for p in active_mod_files])

    active_bg: dict[int, dict[str, Any]] = {}
    for mf in active_mod_files:
        res = kenshi_gd.read_mod(mf.read_bytes())
        for r in res["records"]:
            if r["type"] == 95:
                idx = r["int"].get("index")
                if idx is not None:
                    name_en = r["name"]
                    active_bg[idx] = {
                        "sid": r["sid"],
                        "name_en": name_en,
                        "name_ru": by_msgid.get(name_en),
                        "index": idx,
                    }

    regions = sorted(active_bg.values(), key=lambda r: (r["index"], r["sid"]))

    town_instances: dict[str, dict[str, Any]] = {}
    hand_to_instance: dict[tuple, str] = {}
    for _, ldir in leveldata_dirs:
        ld_path = ldir / "leveldata.level"
        res = kenshi_gd.read_file(ld_path.read_bytes())
        for r in res["records"]:
            if r["type"] == 38:
                for inst in r["instances"]:
                    town_instances[inst["id"]] = inst
            elif r["type"] == 94:
                h = town_handle(r["int"], "hand")
                inst_id = r["string"].get("instance")
                if inst_id and h:
                    hand_to_instance[tuple(h)] = inst_id

    filtered_nest_markers = 0
    real_locations: dict[str, dict[str, Any]] = {}
    for iid, inst in town_instances.items():
        tpl = db["records"].get(inst["target"])
        t = tpl.get("data", {}).get("int", {}).get("type") if tpl else None
        if t == 8:
            filtered_nest_markers += 1
            continue
        real_locations[iid] = {
            "inst": inst,
            "tpl": tpl,
            "type": t,
        }

    all_zone_names: set[str] = set()
    for _, ldir in leveldata_dirs:
        for zf in ldir.glob("zone.*.zone"):
            all_zone_names.add(zf.name)

    loc_buildings: dict[str, list[tuple[str, int, list[float] | None]]] = {}
    unresolved_static_buildings_count = 0

    for zn in sorted(all_zone_names):
        col_row = zn[len("zone."):-len(".zone")]
        zone_b35: dict[str, dict[str, Any]] = {}
        zone_inst: dict[str, dict[str, Any]] = {}
        for _, ldir in leveldata_dirs:
            zp = ldir / zn
            if zp.is_file():
                res = kenshi_gd.read_file(zp.read_bytes())
                for r in res["records"]:
                    if r["type"] == 30:
                        for x in r["instances"]:
                            for s in x["states"]:
                                zone_inst[s] = x
                    elif r["type"] == 35:
                        zone_b35[r["sid"]] = r
        for sid, r in zone_b35.items():
            h = town_handle(r["int"], "town")
            target_iid = hand_to_instance.get(tuple(h)) if h else None
            b_id = r["id"]
            pos = zone_inst[sid]["pos"] if sid in zone_inst else None
            if target_iid and target_iid in real_locations:
                loc_buildings.setdefault(target_iid, []).append((col_row, b_id, pos))
            else:
                unresolved_static_buildings_count += 1

    tga_raw = areasmap_path.read_bytes()
    if len(tga_raw) < 18 + 256 * 256 * 3:
        raise ExtractionError(f"Повреждён areasmap.tga: размер {len(tga_raw)} байт")
    hdr = tga_raw[:18]
    w, h, bpp, desc = struct.unpack("<HHBB", hdr[12:18])
    if w != 256 or h != 256 or bpp != 24:
        raise ExtractionError(f"Невалидный формат areasmap.tga: {w}x{h} bpp={bpp}")

    pixel_data = tga_raw[18:18 + 256 * 256 * 3]
    grid = [[0] * 256 for _ in range(256)]
    for py in range(256):
        for px in range(256):
            off = (py * 256 + px) * 3
            b, g, r = pixel_data[off], pixel_data[off + 1], pixel_data[off + 2]
            grid[py][px] = (r << 16) | (g << 8) | b

    border_grid = [[False] * 256 for _ in range(256)]
    for py in range(256):
        for px in range(256):
            c = grid[py][px]
            for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                nx, ny = px + dx, py + dy
                if 0 <= nx < 256 and 0 <= ny < 256:
                    if grid[ny][nx] != c:
                        border_grid[py][px] = True
                        break

    rle: list[dict[str, Any]] = []
    cur_idx = None
    cur_border = None
    cur_len = 0
    for py in range(256):
        for px in range(256):
            c = grid[py][px]
            b_flag = border_grid[py][px]
            if cur_idx == c and cur_border == b_flag:
                cur_len += 1
            else:
                if cur_len > 0:
                    rle.append({"index": cur_idx, "border": cur_border, "length": cur_len})
                cur_idx = c
                cur_border = b_flag
                cur_len = 1
    if cur_len > 0:
        rle.append({"index": cur_idx, "border": cur_border, "length": cur_len})

    if sum(seg["length"] for seg in rle) != 65536:
        raise ExtractionError("Сумма длин RLE-сегментов не равна 65536")

    r_raw: dict[str, float] = {}
    radius_source: dict[str, str] = {}
    for iid, loc in real_locations.items():
        inst = loc["inst"]
        tpl = loc["tpl"]
        loc["pos"] = [round(float(v), 2) for v in inst["pos"]]
        if iid in loc_buildings:
            b_list = loc_buildings[iid]
            t_pos = loc["pos"]
            dists = [math.hypot(b[2][0] - t_pos[0], b[2][2] - t_pos[2]) for b in b_list if b[2]]
            r_raw[iid] = max(dists) if dists else 0.0
            radius_source[iid] = "game_buildings"
        else:
            tpl_float = tpl.get("data", {}).get("float", {}) if tpl else {}
            sr = tpl_float.get("size radius", 0.0)
            trm = tpl_float.get("town radius mult", 1.0)
            r_raw[iid] = sr * trm
            radius_source[iid] = "template_parameter"

    loc_ids = sorted(real_locations.keys())
    confirmed_r: dict[str, float] = {}
    for iid in loc_ids:
        ri_raw = r_raw[iid]
        pos_i = real_locations[iid]["pos"]
        min_share = ri_raw
        for jid in loc_ids:
            if iid == jid:
                continue
            rj_raw = r_raw[jid]
            pos_j = real_locations[jid]["pos"]
            d = math.hypot(pos_i[0] - pos_j[0], pos_i[2] - pos_j[2])
            if ri_raw + rj_raw > 0:
                share = 0.95 * d * ri_raw / (ri_raw + rj_raw)
            else:
                share = 0.0
            if share < min_share:
                min_share = share
        confirmed_r[iid] = round(min_share, 2)

    locations_list: list[dict[str, Any]] = []
    for iid in loc_ids:
        loc = real_locations[iid]
        inst = loc["inst"]
        tpl = loc["tpl"] or {}
        x, y, z = loc["pos"]
        col = math.floor((x + 147456.0) / 4608.0)
        row = math.floor((z + 147456.0) / 4608.0)
        zone_str = f"{col}.{row}"

        px = min(255, max(0, math.floor((x + 147456.0) / 1152.0)))
        py = 255 - min(255, max(0, math.floor((z + 147456.0) / 1152.0)))
        c = grid[py][px]
        reg_obj = active_bg.get(c)
        reg_name = (
            reg_obj["name_en"]
            if reg_obj and reg_obj["name_en"] != "NONE" and c not in (0, 0x329999)
            else None
        )

        b_list = loc_buildings.get(iid, [])
        static_buildings = sorted(
            [{"zone": b[0], "building_id": b[1]} for b in b_list],
            key=lambda b: (b["zone"], b["building_id"]),
        )

        name_en = tpl.get("name") or ""
        name_ru = by_msgid.get(name_en)

        locations_list.append(
            {
                "instance_id": iid,
                "template_sid": inst["target"],
                "name_en": name_en,
                "name_ru": name_ru,
                "kind": TOWN_TYPES.get(loc["type"], "poi"),
                "x": x,
                "y": y,
                "z": z,
                "zone": zone_str,
                "region": reg_name,
                "coordinate_source": "game_leveldata",
                "radius_source": radius_source[iid],
                "confirmed_radius": confirmed_r[iid],
                "static_buildings": static_buildings,
                "defined_in": tpl.get("defined_in"),
                "modified_in": tpl.get("modified_in", []),
                "is_public": tpl.get("data", {}).get("bool", {}).get("is public"),
                "is_secret": tpl.get("data", {}).get("bool", {}).get("is secret"),
                "unexplored_name": tpl.get("data", {}).get("string", {}).get("unexplored name") or "",
            }
        )

    if denied:
        raise ExtractionError("Сборщик пытался записать защищённые данные игры/конвертера")

    for p in source_files:
        try:
            rel = p.relative_to(game).as_posix()
        except ValueError:
            try:
                rel = f"workshop/{p.relative_to(workshop).as_posix()}"
            except ValueError:
                rel = p.name
        if file_hash(p) != sorted_hashes[rel]:
            raise ExtractionError(f"Файл {rel} изменился во время сборки справочника мира")

    meta = {
        "filtered_nest_markers": filtered_nest_markers,
        "total_locations": len(locations_list),
        "locations_with_buildings": len(loc_buildings),
        "static_buildings_count": sum(len(b) for b in loc_buildings.values()),
        "unresolved_static_buildings_count": unresolved_static_buildings_count,
    }

    world_ref = {
        "format": "kenshi-world-reference/1",
        "world_ref_id": world_ref_id,
        "game_version": game_version,
        "mods": active_mods,
        "source_hashes": sorted_hashes,
        "created_at": created_at,
        "meta": meta,
        "locations": locations_list,
        "regions": regions,
        "regions_map": {
            "pixel_size": 1152,
            "width": 256,
            "height": 256,
            "rle": rle,
        },
    }

    return world_ref


def main() -> int:
    try:
        request = read_json(sys.stdin.buffer.read())
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = run(request)
        sys.stdout.buffer.write(canonical(result))
        return 0
    except Exception as error:
        sys.stderr.buffer.write((str(scrub(f"{type(error).__name__}: {error}")) + "\n").encode("utf-8"))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
