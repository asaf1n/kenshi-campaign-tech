"""Нативные Google Docs/Sheets на Диске: фейковые сервисы Drive/Docs/Sheets, без сети и без реального Диска."""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock

from tools import gdrive_native
from tools.gdrive_native import (BOM, CELL_CHAR_LIMIT, DOC_CHAR_LIMIT, DOC_MIME, FOLDER_MIME, SHEET_MIME, GoogleApi, NativeSync,
                                 NativeUnavailable, RefusedError, normalize_csv_export, normalize_doc_export, prepare,
                                 resolve_root, source_text, trailing_lf)
from tools.gdrive_sync import gdrive_export, sync_drive, write_in_place

gdrive_native.SETTLE_PAUSE = 0.0  # пауза перечитывания метаданных нужна только реальному Диску
KENSHI_COMPAIN = Path(r"C:\Users\artem\Kenshi Compain")
PROJECT_PUBLISHED = Path(__file__).resolve().parents[1] / "published"


def google_plain_export(source: bytes) -> bytes:
    """Модель экспорта `text/plain` Google Doc (опыты 2026-11-07): BOM, CRLF, каждая пустая строка - две, финальный LF не выводится."""
    text = source.decode("utf-8").replace("\r\n", "\n").removeprefix(BOM)
    if text.endswith("\n"):
        text = text[:-1]
    lines: list[str] = []
    run = 0
    for line in text.split("\n") if text else []:
        if line == "":
            run += 1
            continue
        lines.extend([""] * (2 * run))
        run = 0
        lines.append(line)
    lines.extend([""] * (2 * run))
    return (BOM + "\r\n".join(lines)).encode("utf-8")


def google_csv_export(rows: list[list[str]]) -> bytes:
    """Модель экспорта `text/csv` листа: CRLF между строками, без финального перевода строки."""
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\r\n").writerows(rows)
    return buffer.getvalue().removesuffix("\r\n").encode("utf-8")


class FakeHttpError(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(f"http {status}")
        self.resp = types.SimpleNamespace(status=status)
        self.reason = f"http {status}"


class _Request:
    def __init__(self, action) -> None:  # noqa: ANN001
        self.action = action

    def execute(self, num_retries: int = 0):  # noqa: ANN201
        return self.action()


class FakeGoogle:
    """Drive v3 / Docs v1 / Sheets v4 в памяти: те же цепочки вызовов, что у googleapiclient; `mutations` - журнал записей."""

    def __init__(self) -> None:
        self.items: dict[str, dict] = {"root": {"name": "My Drive", "mime": FOLDER_MIME, "parents": []}}
        self.mutations: list[str] = []
        self.clock = 0
        self.drift = False  # после ответа на files.update Диск сдвигает modifiedTime на 0,29 с назад
        self.gets = 0
        self.counter = 0
        self.drive = types.SimpleNamespace(files=lambda: _Files(self))
        self.docs = types.SimpleNamespace(documents=lambda: _Documents(self))
        self.sheets = types.SimpleNamespace(spreadsheets=lambda: _Spreadsheets(self))
        self.root_folder = self.add("Kenshi Compain. Docs", FOLDER_MIME, ["root"])

    def api(self) -> GoogleApi:
        return GoogleApi(self.drive, self.docs, self.sheets)

    def touch(self, file_id: str) -> None:
        self.clock += 1
        self.items[file_id]["modified"] = f"2026-11-07T10:00:{self.clock:02d}.000Z"

    def add(self, name: str, mime: str, parents: list[str]) -> str:
        self.counter += 1
        file_id = f"id{self.counter}"
        self.items[file_id] = {"name": name, "mime": mime, "parents": parents, "props": {}, "text": "", "rows": [], "trashed": False}
        self.touch(file_id)
        return file_id

    def meta(self, file_id: str) -> dict:
        if file_id not in self.items:
            raise FakeHttpError(404)
        item = self.items[file_id]
        return {"id": file_id, "name": item["name"], "mimeType": item["mime"], "modifiedTime": item["modified"],
                "trashed": item.get("trashed", False), "appProperties": dict(item.get("props", {}))}

    def user_edit(self, file_id: str, text: str) -> None:
        """Правка человека в Google: меняет содержимое и время изменения."""
        item = self.items[file_id]
        if item["mime"] == DOC_MIME:
            item["text"] = text
        else:
            item["rows"] = list(csv.reader(io.StringIO(text, newline="")))
        self.touch(file_id)

    def find(self, path: str, mime: str | None = None) -> str:
        """id по пути от корня проекта (`docs/x`)."""
        parent = self.root_folder
        parts = path.split("/")
        for index, part in enumerate(parts):
            wanted = mime if index == len(parts) - 1 and mime else FOLDER_MIME
            matches = [key for key, item in self.items.items() if item["name"] == part and parent in item["parents"]
                       and item["mime"] == wanted and not item.get("trashed")]
            if not matches:
                raise KeyError(path)
            parent = matches[0]
        return parent

    def tree(self) -> dict[str, str]:
        """Пути (от корня проекта) всех файлов -> mime."""
        def path_of(file_id: str) -> str:
            item = self.items[file_id]
            parent = item["parents"][0]
            return item["name"] if parent == self.root_folder else f"{path_of(parent)}/{item['name']}"
        return {path_of(key): item["mime"] for key, item in self.items.items()
                if item["mime"] in (DOC_MIME, SHEET_MIME) and not item.get("trashed")}


class _Files:
    def __init__(self, fake: FakeGoogle) -> None:
        self.fake = fake

    def list(self, q: str, spaces: str, pageSize: int, orderBy: str, fields: str) -> _Request:  # noqa: N803
        import re
        unquote = lambda text: text.replace("\\'", "'").replace("\\\\", "\\")  # noqa: E731
        by_name = re.fullmatch(r"'(.+?)' in parents and name = '(.*)' and mimeType = '(.+?)' and trashed = false", q)
        by_source = re.fullmatch(r"'(.+?)' in parents and mimeType = '(.+?)' and trashed = false and "
                                 r"appProperties has \{ key='kenshi_source' and value='(.*)' \}", q)
        assert by_name or by_source, q
        if by_name:
            parent, name, mime = by_name.group(1), unquote(by_name.group(2)), by_name.group(3)
            found = [self.fake.meta(key) for key, item in self.fake.items.items()
                     if parent in item["parents"] and item["name"] == name and item["mime"] == mime and not item.get("trashed")]
        else:
            parent, mime, tag = by_source.group(1), by_source.group(2), unquote(by_source.group(3))
            found = [self.fake.meta(key) for key, item in self.fake.items.items()
                     if parent in item["parents"] and item["mime"] == mime and not item.get("trashed")
                     and item["props"].get("kenshi_source") == tag]
        return _Request(lambda: {"files": found})

    def get(self, fileId: str, fields: str) -> _Request:  # noqa: N803
        self.fake.gets += 1
        return _Request(lambda: self.fake.meta(fileId))

    def create(self, body: dict, fields: str) -> _Request:
        def run() -> dict:
            self.fake.mutations.append("files.create")
            file_id = self.fake.add(body["name"], body["mimeType"], list(body["parents"]))
            return self.fake.meta(file_id)
        return _Request(run)

    def update(self, fileId: str, body: dict, fields: str) -> _Request:  # noqa: N803
        def run() -> dict:
            self.fake.mutations.append("files.update")
            self.fake.items[fileId]["props"].update(body["appProperties"])
            self.fake.touch(fileId)
            answer = self.fake.meta(fileId)
            if self.fake.drift:
                self.fake.items[fileId]["modified"] = answer["modifiedTime"][:-7] + "59.710Z"
            return answer
        return _Request(run)

    def export(self, fileId: str, mimeType: str) -> _Request:  # noqa: N803
        def run() -> bytes:
            item = self.fake.items[fileId]
            if item["mime"] == DOC_MIME:
                assert mimeType == "text/plain"
                return google_plain_export(item["text"].encode("utf-8"))
            assert mimeType == "text/csv"
            return google_csv_export(item["rows"])
        return _Request(run)


class _Documents:
    def __init__(self, fake: FakeGoogle) -> None:
        self.fake = fake

    def get(self, documentId: str, fields: str) -> _Request:  # noqa: N803
        end = len(self.fake.items[documentId]["text"]) + 2
        return _Request(lambda: {"body": {"content": [{"endIndex": 1}, {"startIndex": 1, "endIndex": end}]}})

    def batchUpdate(self, documentId: str, body: dict) -> _Request:  # noqa: N802,N803
        def run() -> dict:
            self.fake.mutations.append("documents.batchUpdate")
            item = self.fake.items[documentId]
            for request in body["requests"]:
                if "deleteContentRange" in request:
                    span = request["deleteContentRange"]["range"]
                    assert span["startIndex"] == 1 and span["endIndex"] == len(item["text"]) + 1, span
                    item["text"] = ""
                else:
                    insert = request["insertText"]
                    assert insert["location"]["index"] == 1 and insert["text"]
                    item["text"] = insert["text"] + item["text"]
            self.fake.touch(documentId)
            return {}
        return _Request(run)


class _Spreadsheets:
    def __init__(self, fake: FakeGoogle) -> None:
        self.fake = fake

    def get(self, spreadsheetId: str, fields: str) -> _Request:  # noqa: N803
        return _Request(lambda: {"sheets": [{"properties": {"title": "Лист1"}}]})

    def values(self) -> "_Values":
        return _Values(self.fake)


class _Values:
    def __init__(self, fake: FakeGoogle) -> None:
        self.fake = fake

    def clear(self, spreadsheetId: str, range: str, body: dict) -> _Request:  # noqa: A002,N803
        def run() -> dict:
            assert range == "'Лист1'"
            self.fake.mutations.append("values.clear")
            self.fake.items[spreadsheetId]["rows"] = []
            self.fake.touch(spreadsheetId)
            return {}
        return _Request(run)

    def update(self, spreadsheetId: str, range: str, valueInputOption: str, body: dict) -> _Request:  # noqa: A002,N803
        def run() -> dict:
            assert range == "'Лист1'!A1" and valueInputOption == "RAW"
            assert all(isinstance(cell, str) for row in body["values"] for cell in row)
            self.fake.mutations.append("values.update")
            self.fake.items[spreadsheetId]["rows"] = [list(row) for row in body["values"]]
            self.fake.touch(spreadsheetId)
            return {}
        return _Request(run)


def make_native(fake: FakeGoogle, root: Path, entries: dict | None = None, dry_run: bool = False) -> NativeSync:
    api = fake.api()
    return NativeSync(api, {} if entries is None else entries, resolve_root(api, None, {}), root, write_in_place, dry_run, "STAMP")


def load_state(root: Path) -> dict:
    return json.loads((root / "tmp/sync-state.json").read_text(encoding="utf-8"))


class NativeFilesTests(unittest.TestCase):
    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.tmp = Path(holder.name)
        self.root = self.tmp / "drive"
        self.root.mkdir()
        self.published = self.tmp / "published"
        (self.published / "tables").mkdir(parents=True)
        (self.published / "screenshots").mkdir()
        self.fake = FakeGoogle()

    def run_sync(self, **kwargs):  # noqa: ANN003, ANN201
        return sync_drive(self.root, self.published, None, scope="session", api=self.fake.api(), **kwargs)

    def test_update_in_place_keeps_file_id_and_repeat_writes_nothing(self) -> None:
        """Ловит смену file_id при обновлении (разрыв источников блокнота), запись неизменённых файлов при повторе,
        запись PDF в Google и потерю eol_final/структуры sync-state."""
        (self.published / "brief.md").write_bytes(b"# Brief\r\n\r\ntext\r\n")
        (self.published / "tables/a.csv").write_text("id,name\n001,\"a\nb\"\ntrue,\u041a\n", encoding="utf-8")
        (self.published / "screenshots/index.md").write_text("no final lf", encoding="utf-8")
        (self.published / "screenshots/screenshots_latest.pdf").write_bytes(b"%PDF")

        first = self.run_sync()
        self.assertEqual((first["status"], first["summary"]["written"]), ("synced", 4))
        self.assertEqual(self.fake.tree(), {"published/brief": DOC_MIME, "published/tables/a": SHEET_MIME,
                                            "published/screenshots/index": DOC_MIME})
        self.assertEqual((self.root / "published/screenshots/screenshots_latest.pdf").read_bytes(), b"%PDF")
        self.assertFalse((self.root / "published/brief.md").exists())
        native = load_state(self.root)["native"]
        brief_id = self.fake.find("published/brief", DOC_MIME)
        self.assertEqual(native["published/brief.md"]["file_id"], brief_id)
        self.assertEqual({key: native["published/screenshots/index.md"][key] for key in ("kind", "eol_final", "trailing_lf")},
                         {"kind": "doc", "eol_final": False, "trailing_lf": 0})
        self.assertEqual(self.fake.items[brief_id]["props"]["sha256"], native["published/brief.md"]["source_sha256"])
        self.assertEqual(self.fake.items[self.fake.find("published/tables/a", SHEET_MIME)]["rows"],
                         [["id", "name"], ["001", "a\nb"], ["true", "\u041a"]])
        self.assertTrue(first["reconnect"]["game"][0]["url"].startswith("https://docs.google.com/"))

        # Повтор: ни одной записи (ни в Google, ни в sync-state по смыслу).
        before = list(self.fake.mutations)
        again = self.run_sync()
        self.assertEqual((again["summary"]["written"], again["summary"]["unchanged"]), (0, 4))
        self.assertEqual(self.fake.mutations, before)

        # Изменение источника: тот же file_id, обновляется только изменённый файл.
        (self.published / "brief.md").write_bytes(b"# Brief\n\nchanged\n")
        changed = self.run_sync()
        self.assertEqual(changed["summary"]["written"], 1)
        self.assertEqual(self.fake.find("published/brief", DOC_MIME), brief_id)
        self.assertEqual(self.fake.items[brief_id]["text"], "# Brief\n\nchanged\n")
        self.assertEqual(load_state(self.root)["native"]["published/brief.md"]["file_id"], brief_id)
        self.assertEqual(len([name for name in self.fake.mutations[len(before):] if name == "files.create"]), 0)

        # Недоступный Google API: ошибка с причиной, код возврата CLI равен 1 (status != synced), Диск не тронут.
        with mock.patch.object(GoogleApi, "open", side_effect=NativeUnavailable("токен не найден")):
            failed = sync_drive(self.root, self.published, None, scope="session")
        self.assertEqual((failed["status"], failed["reason"]), ("failed", "gdrive_api_unavailable"))

    def test_drive_edit_of_native_csv_is_saved_to_conflicts_and_overwritten(self) -> None:
        """Ловит потерю правки на Диске (нет копии в tmp/conflicts), отсутствие перезаписи версией из main и ложный
        конфликт, когда содержимое Диска уже равно источнику."""
        (self.published / "tables/a.csv").write_text("a,b\n1,2\n", encoding="utf-8")
        self.run_sync()
        sheet_id = self.fake.find("published/tables/a", SHEET_MIME)

        self.fake.user_edit(sheet_id, "a,b\n1,2\n")  # служебное обновление: содержимое то же
        self.assertEqual(self.run_sync()["summary"]["conflicts"], [])
        self.assertEqual(self.fake.items[sheet_id]["rows"], [["a", "b"], ["1", "2"]])

        self.fake.user_edit(sheet_id, "a,b\n1,\u043f\u0440\u0430\u0432\u043a\u0430\n")
        (self.published / "tables/a.csv").write_text("a,b\n1,3\n", encoding="utf-8")
        report = self.run_sync(now=datetime(2026, 11, 7, 12, 0, 0, tzinfo=timezone.utc))
        self.assertEqual(report["summary"]["conflicts"], [{"path": "published/tables/a.csv",
                                                           "copy": "tmp/conflicts/published/tables/a-20261107T120000Z.csv"}])
        self.assertEqual((self.root / "tmp/conflicts/published/tables/a-20261107T120000Z.csv").read_text(encoding="utf-8"),
                         "a,b\n1,\u043f\u0440\u0430\u0432\u043a\u0430\n")
        self.assertEqual(self.fake.find("published/tables/a", SHEET_MIME), sheet_id)
        self.assertEqual(self.fake.items[sheet_id]["rows"], [["a", "b"], ["1", "3"]])

    def test_changed_modified_time_with_equal_content_is_not_a_conflict(self) -> None:
        """Ловит ложный конфликт и запись при изменённом modifiedTime и равном содержимом (в том числе служебная запись
        свойств, двигающая время), а также пропуск реального конфликта при изменённом содержимом; отчёт до/после."""
        (self.published / "brief.md").write_text("# A\n\ntext\n", encoding="utf-8")
        self.run_sync()
        doc_id = self.fake.find("published/brief", DOC_MIME)
        recorded = load_state(self.root)["native"]["published/brief.md"]["drive_modified_time"]

        self.fake.touch(doc_id)  # время изменилось, содержимое то же
        moved = self.fake.items[doc_id]["modified"]
        before = list(self.fake.mutations)
        report = self.run_sync()
        item = report["summary"]["native_files"][0]
        self.assertEqual((item["path"], item["action"], item["drive_modified_before"], item["drive_modified_after"]),
                         ("published/brief.md", "content-equal-modified", recorded, moved))
        self.assertEqual((report["summary"]["written"], report["summary"]["conflicts"], self.fake.mutations), (0, [], before))
        self.assertFalse((self.root / "tmp/conflicts").exists())
        self.assertEqual(load_state(self.root)["native"]["published/brief.md"]["drive_modified_time"], moved)

        again = self.run_sync()  # повтор: время не менялось - экспорт не нужен
        self.assertEqual(again["summary"]["native_files"][0]["action"], "unchanged")
        self.assertEqual(again["summary"]["native_summary"], {"written": 0, "unchanged": 1, "content_equal_modified": 0,
                                                              "conflicts": 0, "foreign": 0})

        self.fake.user_edit(doc_id, "# A\n\nправка\n")
        edited = self.run_sync()
        self.assertEqual(edited["summary"]["native_files"][0]["action"], "conflict")
        self.assertEqual(len(edited["summary"]["conflicts"]), 1)
        self.assertEqual(self.fake.items[doc_id]["text"], "# A\n\ntext\n")

    def test_modified_time_is_reread_after_write_and_drift_is_stabilized(self) -> None:
        """Ловит сохранение в sync-state modifiedTime из ответа на запись, когда Диск позже сдвигает значение: следующий
        прогон видел бы правку, экспортировал файл и записывал content-equal-modified."""
        self.fake.drift = True
        (self.published / "brief.md").write_text("# A\n", encoding="utf-8")
        self.run_sync()
        doc_id = self.fake.find("published/brief", DOC_MIME)
        settled = self.fake.items[doc_id]["modified"]
        self.assertTrue(settled.endswith("59.710Z"))
        self.assertEqual(load_state(self.root)["native"]["published/brief.md"]["drive_modified_time"], settled)
        again = self.run_sync()
        self.assertEqual(again["summary"]["native_files"][0]["action"], "unchanged")

        # Значение не устанавливается: чтений не больше SETTLE_ATTEMPTS, остаётся последнее прочитанное.
        counter = iter(range(100))
        native = make_native(self.fake, self.root)
        with mock.patch.object(GoogleApi, "get_state", side_effect=lambda _id: {"modifiedTime": f"t{next(counter)}"}) as reread:
            result = native._settle(doc_id, {"modifiedTime": "t-start"})
        self.assertEqual((reread.call_count, result["modifiedTime"]), (gdrive_native.SETTLE_ATTEMPTS, "t2"))

    def test_own_file_is_found_by_kenshi_source_and_foreign_same_name_is_untouched(self) -> None:
        """Ловит поиск своего файла по имени (захват чужого одноимённого документа), потерю метки kenshi_source и
        отсутствие чужого файла в отчёте."""
        (self.published / "brief.md").write_text("mine\n", encoding="utf-8")
        foreign_id = self.fake.add("brief", DOC_MIME, [self.fake.add("published", FOLDER_MIME, [self.fake.root_folder])])
        self.fake.items[foreign_id]["text"] = "чужой текст\n"
        self.fake.touch(foreign_id)
        foreign_state = dict(self.fake.items[foreign_id])

        first = self.run_sync()
        own_id = load_state(self.root)["native"]["published/brief.md"]["file_id"]
        self.assertNotEqual(own_id, foreign_id)
        self.assertEqual(self.fake.items[own_id]["props"]["kenshi_source"], "published/brief.md")
        self.assertEqual(self.fake.items[foreign_id], foreign_state)
        self.assertEqual(first["summary"]["foreign_same_name"],
                         [{"path": "published/brief.md", "name": "brief", "file_id": foreign_id}])
        self.assertEqual(first["summary"]["native_summary"]["foreign"], 1)

        (self.root / "tmp/sync-state.json").unlink()  # без sync-state: тот же файл по kenshi_source, а не новый
        mutations = list(self.fake.mutations)
        second = self.run_sync()
        self.assertEqual(load_state(self.root)["native"]["published/brief.md"]["file_id"], own_id)
        self.assertEqual((second["summary"]["written"], self.fake.mutations, second["summary"]["conflicts"]), (0, mutations, []))
        self.assertEqual(self.fake.items[foreign_id], foreign_state)

    def test_limits_are_refused_without_breaking_other_files(self) -> None:
        """Ловит запись сверх лимитов Google (Doc 1,5 млн символов, ячейка 50 000) и остановку остальных файлов."""
        (self.published / "brief.md").write_text("x" * (DOC_CHAR_LIMIT + 1), encoding="utf-8")
        (self.published / "tables/wide.csv").write_text("a,b\n1," + "y" * (CELL_CHAR_LIMIT + 1) + "\n", encoding="utf-8")
        (self.published / "squad.md").write_text("ok\n", encoding="utf-8")
        report = self.run_sync()
        self.assertEqual(report["status"], "partial")
        self.assertEqual(len(report["summary"]["refused"]), 2)
        self.assertIn("1500000", report["summary"]["refused"][0] + report["summary"]["refused"][1])
        self.assertIn("2:2", "".join(report["summary"]["refused"]))
        self.assertEqual(self.fake.tree(), {"published/squad": DOC_MIME})
        # Граница: ровно лимит проходит.
        prepare("a.md", "doc", ("x" * DOC_CHAR_LIMIT).encode())
        prepare("a.csv", "sheet", ("y" * CELL_CHAR_LIMIT).encode())
        with self.assertRaises(RefusedError):
            prepare("a.csv", "sheet", ("y" * (CELL_CHAR_LIMIT + 1)).encode())

    def test_export_normalization_returns_source_byte_for_byte(self) -> None:
        """Ловит расхождение обратной нормализации с исходником: BOM, CRLF, удвоение пустых строк, финальный LF."""
        sources: dict[str, bytes] = {
            "synthetic: ends without LF": b"# a\n\nb\n\n\nc",
            "synthetic: two trailing LF": b"a\n\nb\n\n",
            "synthetic: leading blanks and CRLF": b"\r\n\r\n# x\r\n\r\n\r\ny  \r\ntail\r\n",
            "synthetic: single line": b"one\n",
            "synthetic: empty": b"",
        }
        real = 0
        if KENSHI_COMPAIN.is_dir():
            for area in ("Campaign", "docs", "templates"):
                for path in sorted((KENSHI_COMPAIN / area).rglob("*.md")):
                    sources[f"clone:{path.relative_to(KENSHI_COMPAIN).as_posix()}"] = path.read_bytes()
                    real += 1
        for path in sorted(PROJECT_PUBLISHED.glob("*.md")) + sorted(PROJECT_PUBLISHED.glob("*/*.md")):
            sources[f"published:{path.relative_to(PROJECT_PUBLISHED).as_posix()}"] = path.read_bytes()
            real += 1
        if real < 27:
            print(f"ПРОПУСК части фикстур: реальных .md {real} (< 27), клон/published неполны")
        for label, data in sources.items():
            with self.subTest(label):
                expected = source_text(data).encode("utf-8")
                exported = google_plain_export(data)
                self.assertTrue(exported.startswith(BOM.encode()) or not data.strip())
                self.assertEqual(normalize_doc_export(exported, trailing_lf(source_text(data))), expected)
        for rows in ([["a", "b"], ["1", "x\ny"]], [["\u043a"]], [["only"]]):
            buffer = io.StringIO()
            csv.writer(buffer, lineterminator="\n").writerows(rows)
            text = buffer.getvalue()
            self.assertEqual(normalize_csv_export(google_csv_export(rows), trailing_lf(text)), text.encode("utf-8"))

    def test_gdrive_export_restores_synced_sources(self) -> None:
        """Ловит потерю trailing_lf в sync-state/appProperties и выбор не того файла при восстановлении из Google."""
        (self.published / "brief.md").write_bytes(b"# A\r\n\r\n\r\ntext")
        (self.published / "squad.md").write_bytes(b"a\n\nb\n\n")
        (self.published / "tables/a.csv").write_bytes(b"a,b\n\"x\ny\",2\n")
        self.run_sync()
        for name, expected in (("brief.md", b"# A\n\n\ntext"), ("squad.md", b"a\n\nb\n\n"), ("tables/a.csv", b"a,b\n\"x\ny\",2\n")):
            data, info = gdrive_export(f"published/{name}", self.root, self.fake.api())
            self.assertEqual(data, expected)
            self.assertEqual(info["bytes"], len(expected))
        # Без sync-state: по именам каталогов и appProperties.
        (self.root / "tmp/sync-state.json").unlink()
        self.assertEqual(gdrive_export("published/squad.md", self.root, self.fake.api())[0], b"a\n\nb\n\n")
        with self.assertRaises(OSError):
            gdrive_export("published/absent.md", self.root, self.fake.api())


if __name__ == "__main__":
    unittest.main()
