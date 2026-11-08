"""Проверки картографического слоя и доставки его артефактов в Kenshi-Compain.

Фикстурный режим (CI): малый выпуск, собранный в памяти, малые растры, синтетический просмотрщик.
Локальный режим: полный data/world/<world_ref_id>.json и настоящий published/ (если есть);
без них соответствующие проверки пропускаются.
"""

from copy import deepcopy
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
import warnings
import xml.etree.ElementTree as ET

from tools import cartography as c
from tools.deliver_companion import deliver_to_companion


ROOT = Path(__file__).resolve().parents[1]
WORLD_SOURCE = c.default_world_source()
PUBLISHED = ROOT / "published"
COMPANION = Path(os.environ.get("KENSHI_COMPANION_DIR", r"C:\Users\artem\Kenshi Compain"))
FORBIDDEN = ("проходим", "проверен", "безопас", "фанатик", "исследован", "подтверждённый переход")
NS = {"s": "http://www.w3.org/2000/svg"}
PALETTE = ["Floodlands", "Rebirth", "Deadlands", "Arm of Okran", "Fog Islands", "Hidden Forest",
           "Skinner's Roam", "Border Zone", "The Iron Trail"]


def make_release() -> dict:
    """Малый выпуск для CI: регионы из графа, одноимённые персонажи, секретная и неизвестная записи."""
    world_id = c.GEOMETRY_WORLD_REF_ID
    manifest = {"format": "kenshi-publication/1", "validation": {"status": "passed"}, "snapshot_id": "sha256-a",
                "world_ref_id": world_id, "branch_id": "branch-a", "campaign_id": "camp", "game_time": "Day 1 00:00",
                "generated_at": "2026-01-01T00:00:00Z"}
    regions = [{"index": 1000 + i, "name_en": name, "name_ru": name + "-ru", "sid": f"s{i}"} for i, name in enumerate(PALETTE)]
    regions.append({"index": 5, "name_en": "NONE", "name_ru": "NONE", "sid": "none"})
    regions[4]["name_ru"] = "Острова тумана"
    regions[8]["name_ru"] = "Железный шлейф"

    def place(identity, name, kind, region, x, z, secret=False):
        return {"instance_id": identity, "name_en": name, "name_ru": name, "kind": kind, "region": region,
                "x": x, "z": z, "is_secret": secret, "is_public": True}
    world = {"format": "kenshi-world-reference/1", "world_ref_id": world_id, "regions": regions, "locations": [
        place("l1", "Край Мира", "poi", "Arm of Okran", -24740.5, -86186.2),
        place("l2", "Хаб-1", "town", "Border Zone", -10000.0, 20000.0),
        place("l3", "Тайник", "poi", "Border Zone", -9000.0, 21000.0, secret=True)]}

    def person(identity, name, status, region, x, z):
        return {"id": identity, "name": name, "resolution_status": status,
                "coordinates": [x, 0.0, z], "region": {"name_en": region, "name_ru": region}}
    characters = [person("ps:1", "Бык", "in_region", "Border Zone", -10000.0, 20000.0),
                  person("ps:2", "Бык", "in_region", "Border Zone", -10100.0, 20100.0),
                  person("ps:3", "Хот Лон", "in_location", "Arm of Okran", -24740.5, -86186.2),
                  person("ps:4", "Тень", "unknown", "Floodlands", 0.0, 0.0)]
    characters[3]["region"] = None
    locations = {"format": "kenshi-locations/1", "world_ref_id": world_id, "snapshot_id": "sha256-a",
                 "branch_id": "branch-a", "characters": characters,
                 "spatial_groups": [{"group_id": "g1", "resolution_status": "in_region",
                                     "characters": [{"id": "ps:1"}, {"id": "ps:2"}]}],
                 "movement": {"valid": False}}
    return {"manifest": manifest, "world": world, "locations": locations}


def make_viewer() -> str:
    """Синтетический просмотрщик, совпадающий с доставляемыми артефактами (CI не требует Kenshi-Compain)."""
    svg = ET.fromstring(c.GEOMETRY_SVG_PATH.read_text(encoding="utf-8"))
    geometry = []
    for path in svg.iter("{http://www.w3.org/2000/svg}path"):
        title = path.find("{http://www.w3.org/2000/svg}title").text
        geometry.append({"name_en": re.search(r"\(([^()]*)\)$", title)[1], "d": path.get("d")})
    derived = {"world_ref_id": c.GEOMETRY_WORLD_REF_ID, "graph": c.ADJACENCY_GRAPH, "geometry": geometry}
    chains = json.dumps(c.CHAPTER_XI_CHAINS, ensure_ascii=False).replace('"', "'")
    return ('<script id="derived" type="application/json">' + json.dumps(derived, ensure_ascii=False)
            + "</script><script>const CHAINS = " + chains + ";\nfetch('../../published/manifest.json');"
            + "confirm('Заменить выпуск?'); 'устаревший снимок';</script>")


class FixtureTests(unittest.TestCase):
    """Фикстурный режим: без полного справочника мира."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.release = c.load_release(**dict(zip(("manifest", "world", "locations"), (make_release()[k] for k in ("manifest", "world", "locations")))))
        cls.viewer = make_viewer()

    def test_adjacency_algorithm(self) -> None:
        # Диагональ не граница; NONE исключён; два индекса одного имени сливаются; изолированный регион остаётся.
        #  1 1 2 0
        #  3 1 2 2
        #  0 4 4 5
        #  6 0 0 0
        cells = [1, 1, 2, 0, 3, 1, 2, 2, 0, 4, 4, 5, 6, 0, 0, 0]
        names = {0: "NONE", 1: "A", 2: "B", 3: "C", 4: "D", 5: "D", 6: "E"}
        graph = c.build_adjacency(cells, names, size=4)
        self.assertEqual(graph, {"A": ["B", "C", "D"], "B": ["A", "D"], "C": ["A"], "D": ["A", "B"], "E": []})
        with self.assertRaises(ValueError):
            c.decode_raster([[1, 5]], size=2)
        with self.assertRaises(ValueError):
            c.decode_raster([[1, 0], [1, 4]], size=2)

    def test_corridor_steps_are_adjacent_pairs_or_gaps(self) -> None:
        # Несмежные пары (Fog Islands — Floodlands, Hidden Forest — Skinner's Roam) дают gap, а не соединение.
        chains = {"gap": ["Fog Islands", "Floodlands"], "gap2": ["Hidden Forest", "Skinner's Roam"],
                  "border": ["Floodlands", "Hidden Forest"]}
        result = {row["name"]: row for row in c.compare_corridors(chains)}
        self.assertEqual({k: v["steps"][0]["status"] for k, v in result.items()},
                         {"gap": "gap", "gap2": "gap", "border": "border"})
        self.assertEqual({k: v["complete"] for k, v in result.items()}, {"gap": False, "gap2": False, "border": True})
        with self.assertRaises(ValueError):
            c.compare_corridors({"x": ["Floodlands", "Нет такого"]})
        with self.assertRaises(ValueError):
            c.compare_corridors({"x": ["Floodlands"]})

    def test_chapter_xi_chains(self) -> None:
        corridors = {row["name"]: row for row in c.compare_corridors()}
        a, b = (corridors[k] for k in c.CHAPTER_XI_CHAINS)
        self.assertEqual(a["regions"], ["Floodlands", "Rebirth", "Deadlands"])
        self.assertEqual(b["regions"], ["Floodlands", "Arm of Okran", "Deadlands"])
        for corridor in corridors.values():
            for step in corridor["steps"]:
                adjacent = step["to_region"] in c.ADJACENCY_GRAPH[step["from_region"]]
                self.assertEqual(step["status"], "border" if adjacent else "gap")
                self.assertEqual(adjacent, step["from_region"] in c.ADJACENCY_GRAPH[step["to_region"]])
        self.assertEqual([s["status"] for s in a["steps"]], ["border", "gap"])
        self.assertEqual([s["status"] for s in b["steps"]], ["gap", "gap"])
        self.assertEqual(c._region_names(self.release["world"])["Fog Islands"], "Острова тумана")

    def test_no_route_claims_and_no_connecting_lines(self) -> None:
        corridors = c.compare_corridors()
        summary = c.generate_summary(self.release, corridors)
        svg = c.export_svg(self.release, corridors)
        root = ET.fromstring(svg)
        self.assertEqual(root.findall(".//s:polyline", NS), [])
        self.assertEqual(root.findall(".//s:line", NS), [])
        self.assertIn("разрыв: общей границы нет", summary)
        self.assertIn("разрыв: общей границы нет", svg)
        texts = [summary, svg, (ROOT / "tools/cartography.py").read_text(encoding="utf-8").split("FORBIDDEN_VIEWER_WORDS")[0],
                 " ".join(c.STATUS_LABELS.values()), c.DISCLAIMER]
        for text in texts:
            for word in FORBIDDEN:
                self.assertNotIn(word, text.casefold())

    def test_characters_distinct_by_id(self) -> None:
        characters = self.release["locations"]["characters"]
        self.assertEqual(len({row["id"] for row in characters}), len(characters))
        self.assertEqual([r["name"] for r in characters].count("Бык"), 2)
        summary = c.generate_summary(self.release)
        for row in characters:
            self.assertIn(f"({row['id']})", summary)
        duplicate = deepcopy(self.release)
        duplicate["locations"]["characters"].append(deepcopy(characters[0]))
        self.assertTrue(c.validate_release(duplicate["manifest"], duplicate["world"], duplicate["locations"]))

    def test_snapshot_and_world_mismatch_warn(self) -> None:
        for document, key in (("world", "world_ref_id"), ("locations", "world_ref_id"),
                              ("locations", "snapshot_id"), ("locations", "branch_id")):
            candidate = deepcopy(self.release)
            candidate[document][key] = "different"
            with self.subTest(document=document, key=key), warnings.catch_warnings(record=True) as captured:
                warnings.simplefilter("always")
                with self.assertRaisesRegex(ValueError, key):
                    c.load_release(candidate["manifest"], candidate["world"], candidate["locations"])
                self.assertEqual(len(captured), 1)
                self.assertIn(key, str(captured[0].message))
        candidate = deepcopy(self.release)
        candidate["manifest"]["world_ref_id"] = candidate["world"]["world_ref_id"] = candidate["locations"]["world_ref_id"] = "other"
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            with self.assertRaisesRegex(ValueError, "world_ref_id"):
                c.load_release(candidate["manifest"], candidate["world"], candidate["locations"])
            self.assertEqual(len(captured), 1)

    def test_incomparable_releases_are_not_merged(self) -> None:
        other = deepcopy(self.release)
        other["manifest"]["snapshot_id"] = other["locations"]["snapshot_id"] = "sha256-other"
        other["manifest"]["branch_id"] = other["locations"]["branch_id"] = "alt-branch"
        self.assertIs(c.select_release([self.release]), self.release)
        with self.assertRaises(c.AmbiguousReleaseError):
            c.select_release([self.release, other])
        with self.assertRaises(c.AmbiguousReleaseError):
            c.select_release([self.release, other], snapshot_id="missing")
        chosen = c.select_release([self.release, other], snapshot_id="sha256-other")
        self.assertIs(chosen, other)
        self.assertEqual(chosen["locations"]["snapshot_id"], chosen["manifest"]["snapshot_id"])

    def test_release_validation_failures(self) -> None:
        for mutation in ("invalid_validation", "duplicate_id", "missing_list", "invalid_format", "invalid_name",
                         "null_validation", "invalid_coords", "unknown_region", "empty_palette", "character_outside_palette"):
            candidate = deepcopy(self.release)
            if mutation == "invalid_validation":
                candidate["manifest"]["validation"]["status"] = "failed"
            elif mutation == "duplicate_id":
                candidate["locations"]["characters"].append(candidate["locations"]["characters"][0])
            elif mutation == "missing_list":
                del candidate["locations"]["characters"]
            elif mutation == "invalid_name":
                candidate["world"]["regions"][0]["name_ru"] = 123
            elif mutation == "null_validation":
                candidate["manifest"]["validation"] = None
            elif mutation == "invalid_coords":
                candidate["locations"]["characters"][0]["coordinates"] = [0, 0, math.inf]
            elif mutation == "unknown_region":
                candidate["world"]["locations"][0]["region"] = "Нет такого"
            elif mutation == "empty_palette":
                candidate["world"]["regions"] = []
            elif mutation == "character_outside_palette":
                candidate["locations"]["characters"][0]["region"] = {"name_en": "Hidden Forest"}
                candidate["world"]["regions"] = [r for r in candidate["world"]["regions"] if r["name_en"] != "Hidden Forest"]
            else:
                candidate["world"]["format"] = "other"
            with self.subTest(mutation=mutation):
                self.assertTrue(c.validate_release(candidate["manifest"], candidate["world"], candidate["locations"]))

    def test_coordinates_and_semantics(self) -> None:
        self.assertEqual(c.world_to_grid(c.MIN_COORD, c.MIN_COORD), (0, 255))
        last = math.nextafter(c.MAX_COORD, -math.inf)
        self.assertEqual(c.world_to_grid(last, last), (255, 0))
        for value in (c.MAX_COORD, c.MIN_COORD - 1, math.nan, math.inf, None, "0", True):
            with self.subTest(invalid=value):
                self.assertIsNone(c.world_to_svg(value, 0))
                self.assertIsNone(c.world_to_grid(0, value))
        for px, py in ((0, 0), (255, 255), (17, 200), (200, 17)):
            center = c.grid_to_world_center(px, py)
            self.assertEqual(c.world_to_grid(*center), (px, py))
            self.assertEqual(c.svg_to_world(*c.world_to_svg(*center)), center)
        with self.assertRaises(ValueError):
            c.grid_to_world_center(256, 0)
        # Эталон из справочника мира: «Край Мира» (22-rebirth-INGAME).
        svg = c.world_to_svg(-24740.51, -86186.23)
        self.assertAlmostEqual(svg[0], 106.524, delta=0.001)
        self.assertAlmostEqual(svg[1], 53.186, delta=0.001)
        world = self.release["world"]
        self.assertFalse(any(row["is_secret"] for row in c.visible_locations(world)))
        self.assertEqual(len(c.visible_locations(world, show_secrets=True)), len(world["locations"]))
        self.assertNotIn("Тайник", c.export_svg(self.release))
        self.assertIn("Тайник", c.export_svg(self.release, show_secrets=True))
        self.assertIn("Тень", c.generate_summary(self.release))
        self.assertNotIn("Тень: ", c.export_svg(self.release))

    def test_export_and_iron_trail(self) -> None:
        svg = c.export_svg(self.release, c.compare_corridors())
        root = ET.fromstring(svg)
        for key in ("world_ref_id", "snapshot_id", "branch_id", "game_time"):
            self.assertIn(key + ": " + self.release["manifest"][key], svg)
        self.assertIn(c.DISCLAIMER, svg)
        self.assertEqual(len(root.findall("s:g/s:path", NS)), 69 + 6)
        filtered = ET.fromstring(c.export_svg(self.release, kind="town", region="Border Zone", query="Хаб"))
        self.assertEqual(json.loads(filtered.find("s:metadata", NS).text)["query"], "Хаб")
        self.assertEqual(len(filtered.findall("s:circle", NS)), 1 + 3)  # локация + позиции трёх известных персонажей
        escaped = deepcopy(self.release)
        escaped["locations"]["characters"][0]["name"] = '<script>alert("x")</script>&'
        self.assertEqual(ET.fromstring(c.export_svg(escaped)).findall(".//s:script", NS), [])
        # Сопоставление по name_en; ru-название игры («Железный шлейф») не выводится.
        self.assertEqual(c.get_region_display_name("The Iron Trail", "Железный шлейф"), "Железный шельф")
        release = deepcopy(self.release)
        release["locations"]["characters"][0]["region"] = {"name_en": "The Iron Trail", "name_ru": "Железный шлейф"}
        text = c.generate_summary(release)
        self.assertIn("Железный шельф", text)
        self.assertNotIn("Железный шлейф", text)

    def test_geometry_fixture_mode_does_not_claim_source_check(self) -> None:
        report = c.verify_geometry(c.GEOMETRY_WORLD_REF_ID)
        self.assertIs(report["world_source_verified"], False)
        self.assertEqual(report["svg"].tag, "{http://www.w3.org/2000/svg}svg")
        with self.assertRaisesRegex(ValueError, "world_ref_id"):
            c.verify_geometry("different-world")
        with tempfile.TemporaryDirectory() as directory:
            original = c.GEOMETRY_SVG_PATH.read_text(encoding="utf-8")
            path = Path(directory) / "regions_map.svg"
            path.write_bytes(original.replace("\n", "\r\n").encode("utf-8"))
            c.verify_geometry(c.GEOMETRY_WORLD_REF_ID, path)
            path.write_text(original.replace('<path d="M', '<path d="M0,0 L', 1), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "SHA-256 SVG"):
                c.verify_geometry(c.GEOMETRY_WORLD_REF_ID, path)
            table = json.loads(c.ADJACENCY_PATH.read_text(encoding="utf-8"))
            table["adjacency"]["Floodlands"].append("Deadlands")
            tampered = Path(directory) / "adjacency.json"
            tampered.write_text(json.dumps(table), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "таблицы соседства"):
                c.load_adjacency(tampered)

    def test_raster_not_in_code_or_assets(self) -> None:
        source = "".join(line for line in (ROOT / "tools/cartography.py").read_text(encoding="utf-8").splitlines(True)
                         if not line.startswith("RASTER_MARKERS"))
        for marker in ("_RASTER_DATA", "RASTER_RLE", "b85decode", "zlib"):
            self.assertNotIn(marker, source)
        self.assertLess(sum(p.stat().st_size for p in c.ASSET_DIR.iterdir()), 500_000)
        self.assertEqual(sorted(p.name for p in c.ASSET_DIR.iterdir()), ["regions_adjacency.json", "regions_map.svg"])


class ViewerCompatTests(unittest.TestCase):
    """Совместимость просмотрщика Kenshi-Compain с доставляемыми артефактами."""

    def test_synthetic_viewer_compatible_and_mutations_rejected(self) -> None:
        viewer = make_viewer()
        self.assertEqual(c.check_viewer_compat(viewer), [])
        graph = json.loads(re.search(r'application/json">(.*?)</script>', viewer, re.S)[1])
        graph["graph"]["Floodlands"].append("Deadlands")
        shape = json.loads(re.search(r'application/json">(.*?)</script>', viewer, re.S)[1])
        shape["geometry"][0]["d"] = "M0,0Z"
        block = lambda doc: '<script id="derived" type="application/json">' + json.dumps(doc, ensure_ascii=False) + "</script>"  # noqa: E731
        original = re.search(r'<script id="derived".*?</script>', viewer, re.S)[0]
        mutations = {
            "graph": viewer.replace(original, block(graph)),
            "geometry": viewer.replace(original, block(shape)),
            "chains": viewer.replace("'Rebirth'", "'Wend'"),
            "raster": viewer + "<!-- raster_rle -->",
            "word": viewer + "<!-- проходимый маршрут -->",
            "no_published": viewer.replace("../../published/", "./"),
            "no_confirm": viewer.replace("confirm(", "ask("),
            "no_stale_warning": viewer.replace("устаревший снимок", "снимок"),
            "no_derived": "<html></html>",
            "derived_list": viewer.replace(original, '<script id="derived" type="application/json">[]</script>'),
            "derived_null": viewer.replace(original, '<script id="derived" type="application/json">null</script>'),
            "geometry_null": viewer.replace(original, '<script id="derived" type="application/json">{"geometry": null}</script>'),
            "geometry_scalar": viewer.replace(original, '<script id="derived" type="application/json">{"geometry": [1]}</script>'),
        }
        for name, html in mutations.items():
            with self.subTest(mutation=name):
                self.assertTrue(c.check_viewer_compat(html))

    @unittest.skipUnless((COMPANION / c.VIEWER_RELATIVE).is_file(), "нет sites/map/index.html в Kenshi-Compain")
    def test_delivered_viewer_is_compatible(self) -> None:
        self.assertEqual(c.check_viewer_compat((COMPANION / c.VIEWER_RELATIVE).read_text(encoding="utf-8")), [])


class DeliveryTests(unittest.TestCase):
    def git(self, *args: str, cwd: Path) -> str:
        return subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True, encoding="utf-8").stdout

    def make_clone(self, tmp: Path, viewer: str | None) -> tuple[Path, Path]:
        clone, pub = tmp / "clone", tmp / "published"
        clone.mkdir()
        self.git("init", "-b", "main", cwd=clone)
        self.git("config", "user.name", "t", cwd=clone)
        self.git("config", "user.email", "t@example.invalid", cwd=clone)
        (clone / "README.md").write_text("x\n", encoding="utf-8")
        if viewer is not None:
            (clone / c.VIEWER_RELATIVE).parent.mkdir(parents=True)
            (clone / c.VIEWER_RELATIVE).write_text(viewer, encoding="utf-8")
        self.git("add", ".", cwd=clone)
        self.git("commit", "-m", "init", cwd=clone)
        pub.mkdir()
        (pub / "manifest.json").write_text(json.dumps({"validation": {"status": "passed"}, "game_time": "Day 1 00:00"}), encoding="utf-8")
        return clone, pub

    def test_artifacts_in_same_commit_idempotent(self) -> None:
        """Ловит артефакты вне коммита выгрузки, лишний коммит при повторе и доставку без viewer."""
        with tempfile.TemporaryDirectory() as holder:
            clone, pub = self.make_clone(Path(holder), make_viewer())
            first = deliver_to_companion(pub, clone, auto_commit=True)
            self.assertEqual((first["cartography"], first["cartography_changed"], first["committed"], first["status"]),
                             ("ok", True, True, "delivered"))
            files = self.git("show", "--name-only", "--format=", "HEAD", cwd=clone)
            for name in ("docs/reference/regions_map.svg", "docs/reference/regions_adjacency.json", "published/manifest.json"):
                self.assertIn(name, files)
            self.assertEqual(c.load_adjacency(clone / "docs/reference/regions_adjacency.json"), c.ADJACENCY_GRAPH)
            second = deliver_to_companion(pub, clone, auto_commit=True)
            self.assertEqual((second["cartography"], second["cartography_changed"], second["committed"]), ("ok", False, False))

    def test_incompatible_viewer_blocks_artifacts_but_published_is_delivered(self) -> None:
        """Ловит смешение статусов: сбой карты не должен помечать доставку published/ как неуспешную."""
        with tempfile.TemporaryDirectory() as holder:
            clone, pub = self.make_clone(Path(holder), make_viewer().replace("'Rebirth'", "'Wend'"))
            result = deliver_to_companion(pub, clone, auto_commit=True)
            self.assertEqual((result["cartography"], result["status"], result["committed"]), ("incompatible", "delivered", True))
            self.assertIn("Цепочки просмотрщика", result["cartography_reason"])
            self.assertIn("published/manifest.json", self.git("show", "--name-only", "--format=", "HEAD", cwd=clone))
            self.assertFalse((clone / "docs/reference/regions_map.svg").exists())
            self.assertTrue((clone / "published/manifest.json").is_file())

    def test_unreadable_viewer_is_incompatible_not_a_crash(self) -> None:
        with tempfile.TemporaryDirectory() as holder:
            clone, pub = self.make_clone(Path(holder), "x")
            (clone / c.VIEWER_RELATIVE).write_bytes(b"\xff\xfe\x00bad")
            result = deliver_to_companion(pub, clone, auto_commit=False)
            self.assertEqual((result["cartography"], result["status"]), ("incompatible", "delivered"))
            self.assertTrue(result["cartography_reason"])

    def test_published_failure_is_failed_while_cartography_reported_separately(self) -> None:
        """Сбой git при доставке published/ даёт status=failed; поле cartography остаётся самостоятельным."""
        with tempfile.TemporaryDirectory() as holder:
            clone, pub = self.make_clone(Path(holder), make_viewer())
            result = deliver_to_companion(pub, clone, auto_commit=True, auto_push=True)  # remote не настроен
            self.assertEqual(result["status"], "failed")
            self.assertIn("git_error", result)
            self.assertEqual(result["cartography"], "ok")

    def test_absent_viewer_is_not_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as holder:
            clone, pub = self.make_clone(Path(holder), None)
            result = deliver_to_companion(pub, clone, auto_commit=True)
            self.assertEqual((result["cartography"], result["status"]), ("ok", "delivered"))


@unittest.skipUnless(WORLD_SOURCE, "локальный режим: нет data/world/<world_ref_id>.json")
class LocalWorldSourceTests(unittest.TestCase):
    """Полная проверка геометрии по справочнику мира (world_source_verified=true)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.source = c.load_world_source(WORLD_SOURCE)

    def test_full_geometry_verification(self) -> None:
        report = c.verify_geometry(c.GEOMETRY_WORLD_REF_ID, world_source=WORLD_SOURCE)
        self.assertIs(report["world_source_verified"], True)
        self.assertEqual(c.build_adjacency(self.source["cells"], self.source["names"]), c.ADJACENCY_GRAPH)
        self.assertEqual(len(c.ADJACENCY_GRAPH), 69)
        for a, neighbors in c.ADJACENCY_GRAPH.items():
            self.assertNotIn(a, neighbors)
            for b in neighbors:
                self.assertIn(a, c.ADJACENCY_GRAPH[b])

    def test_chain_steps_match_raster_borders(self) -> None:
        cells, names, size = self.source["cells"], self.source["names"], c.RASTER_GRID_SIZE
        borders = set()
        for offset, cell in enumerate(cells):
            for other in ([offset + 1] if offset % size < size - 1 else []) + ([offset + size] if offset + size < len(cells) else []):
                if cell != cells[other]:
                    borders.add(frozenset((names[cell], names[cells[other]])))
        for corridor in c.compare_corridors():
            for step in corridor["steps"]:
                with self.subTest(step=step):
                    self.assertEqual(frozenset((step["from_region"], step["to_region"])) in borders,
                                     step["status"] == "border")

    @unittest.skipUnless((PUBLISHED / "manifest.json").is_file(), "нет локального published/")
    def test_current_published_matches_raster(self) -> None:
        release = c.load_published(PUBLISHED)
        c.verify_release_against_source(release, WORLD_SOURCE)
        characters = release["locations"]["characters"]
        self.assertEqual(len({row["id"] for row in characters}), len(characters))

    def test_release_tampering_rejected_in_local_mode(self) -> None:
        # Идентичность палитры и привязка регионов к растру входят в заявленную полную проверку.
        release = c.load_published(PUBLISHED) if (PUBLISHED / "manifest.json").is_file() else None
        if release is None:
            self.skipTest("нет локального published/")
        sid = deepcopy(release)
        next(r for r in sid["world"]["regions"] if r["name_en"] == "Floodlands")["sid"] = "other"
        swapped = deepcopy(release)
        member = next(r for r in swapped["locations"]["characters"] if r["resolution_status"] != "unknown")
        member["region"] = {"name_en": "Deadlands", "name_ru": "Гиблые земли"}
        for candidate in (sid, swapped):
            with self.assertRaisesRegex(ValueError, "Несовместимая геометрия"):
                c.verify_release_against_source(candidate, WORLD_SOURCE)

    def test_cli_local_mode(self) -> None:
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        done = subprocess.run([sys.executable, "-m", "tools.cartography", "--verify-geometry", "--local"],
                              capture_output=True, text=True, encoding="utf-8", env=env, cwd=ROOT)
        if not (PUBLISHED / "manifest.json").is_file():
            self.skipTest("нет локального published/")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIs(json.loads(done.stdout)["world_source_verified"], True)

    def test_tampered_source_rejected(self) -> None:
        original = json.loads(WORLD_SOURCE.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "world.json"
            for key in ("world_ref_id", "source_hashes", "regions_map", "regions"):
                candidate = deepcopy(original)
                if key == "world_ref_id":
                    candidate[key] = "different-world"
                elif key == "source_hashes":
                    candidate[key][c.RASTER_SOURCE_KEY] = "0" * 64
                elif key == "regions_map":
                    rle = candidate[key]["rle"]
                    rle[0]["length"] -= 1
                    rle.insert(1, {"index": 255, "length": 1, "border": False})
                else:
                    candidate[key] = [r for r in candidate[key] if r["name_en"] != "Floodlands"]
                path.write_text(json.dumps(candidate), encoding="utf-8")
                with self.subTest(source_field=key), self.assertRaisesRegex(ValueError, "Несовместимая геометрия"):
                    c.verify_geometry(c.GEOMETRY_WORLD_REF_ID, world_source=path)


if __name__ == "__main__":
    unittest.main()
