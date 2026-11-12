"""Нативные файлы Google на Диске: один Google Doc на `.md`, один Google Sheet на `.csv`.

Запись идёт через Docs/Sheets/Drive API (`tools/gdrive_api.py`), а не через смонтированный Диск: файл создаётся один раз,
затем обновляется на месте с сохранением `file_id`. Docs - способ (б): текст `.md` вставляется как обычный текст, без
разметки Docs; Sheets - `valueInputOption=RAW` (значения остаются строками). Экспорт Google (`text/plain`, `text/csv`)
не совпадает с исходником побайтно, но различия детерминированы; `normalize_*_export` возвращают исходный текст.
"""
from __future__ import annotations

import csv
import hashlib
import io
import time
from pathlib import Path, PurePosixPath
from typing import Any, Callable
import os

DOC_MIME = "application/vnd.google-apps.document"
SHEET_MIME = "application/vnd.google-apps.spreadsheet"
FOLDER_MIME = "application/vnd.google-apps.folder"
MIME_BY_KIND = {"doc": DOC_MIME, "sheet": SHEET_MIME}
EXPORT_MIME = {"doc": "text/plain", "sheet": "text/csv"}
SUFFIX_KIND = {".md": "doc", ".csv": "sheet"}
URL_BY_KIND = {"doc": "https://docs.google.com/document/d/{}/edit", "sheet": "https://docs.google.com/spreadsheets/d/{}/edit"}

ROOT_NAME = "Kenshi Compain. Docs"
ENV_ROOT_ID = "KENSHI_GDRIVE_ROOT_ID"
ENV_ROOT_NAME = "KENSHI_GDRIVE_ROOT_NAME"
# Проверено опытами 2026-11-07: insertText принял 1,5 млн символов, 2 млн - отказ; ячейка Sheets - 50 000 символов;
# запрос values.update до 12 МБ принят. Экспорт Sheet ограничен 10 МБ (на запись не влияет).
DOC_CHAR_LIMIT = 1_500_000
CELL_CHAR_LIMIT = 50_000
SHEET_REQUEST_LIMIT = 12 * 1024 * 1024
BOM = "﻿"
CRLF, LF = b"\r\n", b"\n"
META_FIELDS = "id,name,mimeType,modifiedTime,headRevisionId,trashed,appProperties"
SETTLE_FIELDS = "id,modifiedTime,headRevisionId,appProperties"
# Drive может сдвинуть modifiedTime (~0,3 с назад) уже после ответа на запись: значение перечитывается, пока два чтения подряд
# не совпадут; не более SETTLE_ATTEMPTS чтений с паузой SETTLE_PAUSE (с), иначе остаётся последнее прочитанное.
SETTLE_ATTEMPTS = 3
SETTLE_PAUSE = 0.5
SOURCE_PROPERTY = "kenshi_source"
# Drive: ключ + значение одного appProperties не длиннее 124 байт UTF-8; длинный (кириллица) путь заменяется хешем.
PROPERTY_LIMIT_BYTES = 124


class NativeUnavailable(RuntimeError):
    """Google API или токен недоступны: синхронизация нативных файлов невозможна."""


class NativeError(OSError):
    """Сбой вызова API; `status` - HTTP-код, если известен."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class RefusedError(ValueError):
    """Файл не записан из-за лимита Google; остальные файлы синхронизируются."""


def kind_of(path: str) -> str | None:
    """`doc` | `sheet` для `.md` | `.csv`; остальное (PDF) остаётся обычным файлом."""
    return SUFFIX_KIND.get(PurePosixPath(path).suffix.lower())


def native_name(path: str) -> str:
    return PurePosixPath(path).stem


def source_tag(key: str) -> str:
    """Значение `kenshi_source`: путь источника с расширением, либо `sha256:<32 hex>` пути, если путь не помещается в лимит свойства."""
    if len((SOURCE_PROPERTY + key).encode("utf-8")) <= PROPERTY_LIMIT_BYTES:
        return key
    return "sha256:" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


def source_digest(data: bytes) -> str:
    """sha256 источника после CRLF -> LF: Git и Windows по-разному хранят окончания строк."""
    return hashlib.sha256(data.replace(CRLF, LF)).hexdigest()


def source_text(data: bytes) -> str:
    return data.decode("utf-8").replace("\r\n", "\n").removeprefix(BOM)


def trailing_lf(text: str) -> int:
    return len(text) - len(text.rstrip("\n"))


def _utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def sheet_rows(text: str, label: str) -> list[list[str]]:
    """Строки CSV как строковые значения; ячейка длиннее лимита Sheets - отказ."""
    try:
        rows = list(csv.reader(io.StringIO(text, newline="")))
    except csv.Error as error:
        raise RefusedError(f"{label}: CSV не разобран ({error}); поле длиннее лимита ячейки Google Sheets "
                           f"({CELL_CHAR_LIMIT} символов) либо файл повреждён; нативная копия не записана") from error
    for number, row in enumerate(rows, 1):
        for column, cell in enumerate(row, 1):
            if len(cell) > CELL_CHAR_LIMIT:
                raise RefusedError(f"{label}: ячейка {number}:{column} содержит {len(cell)} символов, лимит Google Sheets "
                                   f"{CELL_CHAR_LIMIT}; нативная копия не записана")
    size = len(text.encode("utf-8"))
    if size > SHEET_REQUEST_LIMIT:
        raise RefusedError(f"{label}: {size} байт, запрос values.update проверен до {SHEET_REQUEST_LIMIT}; нативная копия не записана")
    return rows


def prepare(key: str, kind: str, data: bytes) -> tuple[str, list[list[str]]]:
    """Текст для записи и строки таблицы; нарушение лимита - `RefusedError`."""
    try:
        text = source_text(data)
    except UnicodeDecodeError as error:
        raise RefusedError(f"{key}: файл не в UTF-8; нативная копия не записана") from error
    if kind == "doc":
        if _utf16_len(text) > DOC_CHAR_LIMIT:
            raise RefusedError(f"{key}: {_utf16_len(text)} символов, лимит вставки в Google Doc {DOC_CHAR_LIMIT}; нативная копия не записана")
        return text, []
    return text, sheet_rows(text, key)


def normalize_doc_export(raw: bytes, trailing: int) -> bytes:
    """Обратная нормализация `text/plain`-экспорта Google Doc в текст исходника (LF).

    Экспорт: BOM в начале, CRLF, каждая пустая строка исходника - две пустые, финальный LF не выводится. Серии пустых
    строк делятся пополам, хвостовые переводы строк снимаются и восстанавливаются числом `trailing` (`trailing_lf`
    источника из sync-state): сам экспорт не различает исходник с финальным LF и без него.
    """
    text = raw.decode("utf-8").removeprefix(BOM).replace("\r\n", "\n")
    out: list[str] = []
    blanks = 0
    for line in text.split("\n"):
        if line == "":
            blanks += 1
            continue
        out.extend([""] * (blanks // 2))
        blanks = 0
        out.append(line)
    return ("\n".join(out) + "\n" * trailing).encode("utf-8")


def normalize_csv_export(raw: bytes, trailing: int) -> bytes:
    """Экспорт Sheet (`text/csv`): CRLF между строками и без финального перевода строки -> LF и `trailing` переводов."""
    text = raw.decode("utf-8").removeprefix(BOM).replace("\r\n", "\n")
    return (text.rstrip("\n") + "\n" * trailing).encode("utf-8")


def normalize_export(kind: str, raw: bytes, trailing: int) -> bytes:
    return (normalize_doc_export if kind == "doc" else normalize_csv_export)(raw, trailing)


def _quote(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


class GoogleApi:
    """Тонкая обёртка над сервисами Drive v3 / Docs v1 / Sheets v4; сбои приводятся к `NativeError`."""

    def __init__(self, drive: Any, docs: Any, sheets: Any) -> None:
        self.drive, self.docs, self.sheets = drive, docs, sheets

    @classmethod
    def open(cls) -> "GoogleApi":
        try:
            from tools.gdrive_api import docs_service, drive_service, sheets_service
            return cls(drive_service(), docs_service(), sheets_service())
        except ImportError as error:
            raise NativeUnavailable(f"не установлены пакеты Google API ({error}): pip install google-api-python-client "
                                    "google-auth-oauthlib google-auth-httplib2") from error
        except Exception as error:  # GdriveAuthError, сбой сети при обновлении токена
            raise NativeUnavailable(f"Google API недоступен: {error}") from error

    @staticmethod
    def _run(request: Any, what: str) -> Any:
        try:
            return request.execute(num_retries=3)
        except Exception as error:
            status = getattr(getattr(error, "resp", None), "status", None)
            reason = getattr(error, "reason", None) or str(error)
            raise NativeError(f"{what}: {str(reason)[:300]}", int(status) if status else None) from error

    def get_meta(self, file_id: str) -> dict[str, Any] | None:
        try:
            return self._run(self.drive.files().get(fileId=file_id, fields=META_FIELDS), "files.get")
        except NativeError as error:
            if error.status == 404:
                return None
            raise

    def get_state(self, file_id: str) -> dict[str, Any] | None:
        """Метаданные для сверки после записи (`id,modifiedTime,headRevisionId,appProperties`)."""
        try:
            return self._run(self.drive.files().get(fileId=file_id, fields=SETTLE_FIELDS), "files.get")
        except NativeError as error:
            if error.status == 404:
                return None
            raise

    def find_files(self, parent_id: str, name: str, mime: str) -> list[dict[str, Any]]:
        query = f"'{parent_id}' in parents and name = '{_quote(name)}' and mimeType = '{mime}' and trashed = false"
        result = self._run(self.drive.files().list(q=query, spaces="drive", pageSize=10, orderBy="createdTime",
                                                   fields=f"files({META_FIELDS})"), "files.list")
        return list(result.get("files", []))

    def find_owned(self, parent_id: str, tag: str, mime: str) -> list[dict[str, Any]]:
        """Наши файлы в каталоге: по `appProperties.kenshi_source`, а не по имени."""
        query = (f"'{parent_id}' in parents and mimeType = '{mime}' and trashed = false and "
                 f"appProperties has {{ key='{SOURCE_PROPERTY}' and value='{_quote(tag)}' }}")
        result = self._run(self.drive.files().list(q=query, spaces="drive", pageSize=10, orderBy="createdTime",
                                                   fields=f"files({META_FIELDS})"), "files.list")
        return list(result.get("files", []))

    def create_folder(self, parent_id: str, name: str) -> str:
        body = {"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]}
        return str(self._run(self.drive.files().create(body=body, fields="id"), "files.create")["id"])

    def create_file(self, parent_id: str, name: str, mime: str) -> dict[str, Any]:
        body = {"name": name, "mimeType": mime, "parents": [parent_id]}
        return self._run(self.drive.files().create(body=body, fields=META_FIELDS), "files.create")

    def write_doc(self, file_id: str, text: str) -> None:
        document = self._run(self.docs.documents().get(documentId=file_id, fields="body(content(endIndex))"), "documents.get")
        end = document["body"]["content"][-1]["endIndex"]
        requests: list[dict[str, Any]] = []
        if end > 2:
            requests.append({"deleteContentRange": {"range": {"startIndex": 1, "endIndex": end - 1}}})
        if text:
            requests.append({"insertText": {"location": {"index": 1}, "text": text}})
        if requests:
            self._run(self.docs.documents().batchUpdate(documentId=file_id, body={"requests": requests}), "documents.batchUpdate")

    def write_sheet(self, file_id: str, rows: list[list[str]]) -> None:
        # Имя листа локализовано («Лист1»): берётся из метаданных, а не «Sheet1».
        info = self._run(self.sheets.spreadsheets().get(spreadsheetId=file_id, fields="sheets(properties(title))"), "spreadsheets.get")
        title = info["sheets"][0]["properties"]["title"]
        sheet = "'" + title.replace("'", "''") + "'"
        values = self.sheets.spreadsheets().values()
        self._run(values.clear(spreadsheetId=file_id, range=sheet, body={}), "values.clear")
        if rows:
            self._run(values.update(spreadsheetId=file_id, range=f"{sheet}!A1", valueInputOption="RAW", body={"values": rows}),
                      "values.update")

    def set_properties(self, file_id: str, properties: dict[str, str]) -> dict[str, Any]:
        return self._run(self.drive.files().update(fileId=file_id, body={"appProperties": properties}, fields=META_FIELDS),
                         "files.update")

    def export(self, file_id: str, mime: str) -> bytes:
        data = self._run(self.drive.files().export(fileId=file_id, mimeType=mime), "files.export")
        return data if isinstance(data, bytes) else str(data).encode("utf-8")


def resolve_root(api: GoogleApi, state_id: str | None, environ: dict[str, str] | None = None) -> str:
    """Корень «Kenshi Compain. Docs»: id из окружения, затем из sync-state, затем поиск по имени в «Мой диск»."""
    env = os.environ if environ is None else environ
    for source, candidate in (("переменная окружения", env.get(ENV_ROOT_ID)), ("sync-state", state_id)):
        if not candidate:
            continue
        meta = api.get_meta(candidate)
        if meta and meta.get("mimeType") == FOLDER_MIME and not meta.get("trashed"):
            return str(meta["id"])
        if source == "переменная окружения":
            raise NativeUnavailable(f"{ENV_ROOT_ID}={candidate}: каталог не найден на Диске")
    name = env.get(ENV_ROOT_NAME) or ROOT_NAME
    found = api.find_files("root", name, FOLDER_MIME)
    if not found:
        raise NativeUnavailable(f"Каталог «{name}» не найден в «Мой диск» (задайте {ENV_ROOT_ID} или {ENV_ROOT_NAME})")
    if len(found) > 1:
        raise NativeUnavailable(f"В «Мой диск» несколько каталогов «{name}»: задайте {ENV_ROOT_ID}")
    return str(found[0]["id"])


class NativeSync:
    """Запись нативных файлов по ключам-путям (`docs/x.md`) с учётом sync-state (секция `native`).

    `entries[key]`: `file_id`, `kind` (doc|sheet), `source_sha256` (после CRLF -> LF), `drive_modified_time` (после нашей
    записи), `eol_final`, `trailing_lf` (хвостовые LF источника для обратной нормализации), `chars`.
    """

    def __init__(self, api: GoogleApi, entries: dict[str, dict[str, Any]], root_id: str, root: Path,
                 write_file: Callable[[bytes, Path], bool], dry_run: bool = False, stamp: str = "") -> None:
        self.api, self.entries, self.root_id = api, entries, root_id
        self.root, self.write_file, self.dry_run, self.stamp = root, write_file, dry_run, stamp
        self.verified: set[str] = set()
        # Отчёт запуска: по каждому нативному файлу путь, действие и drive_modified_time до/после; чужие одноимённые файлы.
        self.files: dict[str, dict[str, Any]] = {}
        self.foreign: list[dict[str, str]] = []
        self._folders: dict[str, str | None] = {}

    def folder_id(self, parts: list[str], create: bool) -> str | None:
        parent: str | None = self.root_id
        for index, part in enumerate(parts):
            path = "/".join(parts[: index + 1])
            if path not in self._folders:
                found = self.api.find_files(str(parent), part, FOLDER_MIME)
                if len(found) > 1:
                    raise NativeError(f"{path}: на Диске несколько каталогов с этим именем; оставьте один")
                if found:
                    self._folders[path] = str(found[0]["id"])
                elif create:
                    self._folders[path] = self.api.create_folder(str(parent), part)
                else:
                    self._folders[path] = None
            parent = self._folders[path]
            if parent is None:
                return None
        return parent

    def _write(self, kind: str, file_id: str, text: str, rows: list[list[str]]) -> None:
        if kind == "doc":
            self.api.write_doc(file_id, text)
        else:
            self.api.write_sheet(file_id, rows)

    def _store(self, key: str, kind: str, file_id: str, sha: str, trailing: int, chars: int,
               meta: dict[str, Any] | None = None) -> str | None:
        """Запись в sync-state и свойств файла; возвращает `modifiedTime` после. Если `meta` уже содержит те же свойства,
        `files.update` не вызывается (не плодим правки Диска и не двигаем modifiedTime)."""
        wanted = {"sha256": sha, "trailing_lf": str(trailing), SOURCE_PROPERTY: source_tag(key)}
        if meta is not None and all((meta.get("appProperties") or {}).get(name) == value for name, value in wanted.items()):
            after = meta
        else:
            after = self._settle(file_id, self.api.set_properties(file_id, wanted))
        entry = {"file_id": file_id, "kind": kind, "source_sha256": sha, "drive_modified_time": after.get("modifiedTime"),
                 "eol_final": trailing > 0, "trailing_lf": trailing, "chars": chars}
        if after.get("headRevisionId"):
            entry["head_revision_id"] = after["headRevisionId"]
        self.entries[key] = entry
        return after.get("modifiedTime")

    def _settle(self, file_id: str, written: dict[str, Any]) -> dict[str, Any]:
        """Метаданные после записи, перечитанные до совпадения двух чтений подряд (ответ на запись - первое чтение)."""
        latest = written
        for _ in range(SETTLE_ATTEMPTS):
            time.sleep(SETTLE_PAUSE)
            current = self.api.get_state(file_id)
            if current is None:
                break
            previous, latest = latest, {**latest, **current}
            if current.get("modifiedTime") == previous.get("modifiedTime"):
                break
        return latest

    def _journal(self, key: str, action: str, before: str | None, after: str | None, **extra: Any) -> None:
        self.files[key] = {"path": key, "action": action, "drive_modified_before": before, "drive_modified_after": after, **extra}

    def totals(self) -> dict[str, int]:
        """Сводка запуска по нативным файлам; в `--dry-run` `written` - число запланированных записей."""
        actions = [item["action"] for item in self.files.values()]
        count = actions.count
        return {"written": count("created") + count("updated") + count("conflict"),
                "unchanged": count("unchanged") + count("content-equal-modified"),
                "content_equal_modified": count("content-equal-modified"), "conflicts": count("conflict"),
                "foreign": len(self.foreign)}

    def _lookup(self, key: str, kind: str, parent: str | None) -> dict[str, Any] | None:
        """Наш файл по `kenshi_source` в каталоге; одноимённые без метки - чужие, не трогаются и попадают в `foreign`."""
        if not parent:
            return None
        owned = self.api.find_owned(parent, source_tag(key), MIME_BY_KIND[kind])
        if len(owned) > 1:
            raise NativeError(f"{key}: на Диске несколько нативных файлов с kenshi_source этого пути; оставьте один")
        taken = {str(item["id"]) for item in owned}
        for other in self.api.find_files(parent, native_name(key), MIME_BY_KIND[kind]):
            if str(other["id"]) not in taken:
                self.foreign.append({"path": key, "name": str(other.get("name", native_name(key))), "file_id": str(other["id"])})
        return owned[0] if owned else None

    def put(self, key: str, data: bytes, report: dict[str, Any], stamp: str | None = None) -> str | None:
        """`create` | `write` | None (записи нет). В dry-run ничего не меняется (только чтение).

        Правка на Диске определяется по содержимому: изменённый `modifiedTime` - лишь повод экспортировать файл и сравнить
        нормализованный текст с источником. Совпало - конфликта нет, обновляется только `drive_modified_time` в sync-state.
        """
        kind = kind_of(key)
        if kind is None:
            raise ValueError(f"{key}: не нативный тип")
        text, rows = prepare(key, kind, data)
        encoded, sha, trailing = text.encode("utf-8"), source_digest(data), trailing_lf(text)
        entry = self.entries.get(key)
        meta: dict[str, Any] | None = None
        adopted = False
        if entry and entry.get("kind") == kind:
            meta = self.api.get_meta(entry["file_id"])
            if meta is None or meta.get("trashed"):
                report.setdefault("recreated", []).append(key)
                meta, entry = None, None
        else:
            entry = None
        recorded = entry.get("drive_modified_time") if entry else None
        parent: str | None = None
        if meta is None:
            parent = self.folder_id(key.split("/")[:-1], create=not self.dry_run)
            meta = self._lookup(key, kind, parent)
            adopted = meta is not None
        if meta is None:
            if self.dry_run:
                self._journal(key, "created", None, None)
                return "create"
            created = self.api.create_file(str(parent), native_name(key), MIME_BY_KIND[kind])
            file_id = str(created["id"])
            try:
                self._write(kind, file_id, text, rows)
            except BaseException:
                # Пустая заготовка не должна выглядеть записанной: sha пуст, следующий запуск допишет.
                self.entries[key] = {"file_id": file_id, "kind": kind, "source_sha256": "", "drive_modified_time": None,
                                     "eol_final": trailing > 0, "trailing_lf": trailing, "chars": len(text)}
                raise
            after = self._store(key, kind, file_id, sha, trailing, len(text))
            self._journal(key, "created", None, after)
            self.verified.add(key)
            return "create"

        self.verified.add(key)
        file_id = str(meta["id"])
        modified = meta.get("modifiedTime")
        tagged = (meta.get("appProperties") or {}).get(SOURCE_PROPERTY) == source_tag(key)
        extra = {"adopted": True} if adopted else {}
        changed_on_drive = adopted or entry is None or modified != recorded
        if not changed_on_drive and entry and entry.get("source_sha256") == sha:
            after = recorded
            if not tagged and not self.dry_run:  # файл из sync-state без метки (прежний запуск): метка ставится один раз
                after = self._store(key, kind, file_id, sha, trailing, len(text), meta)
            self._journal(key, "unchanged", recorded, after)
            return None
        conflict = False
        if changed_on_drive:
            raw = self.api.export(file_id, EXPORT_MIME[kind])
            if normalize_export(kind, raw, trailing) == encoded:
                # Содержимое равно источнику (правка отменена либо служебное обновление времени): конфликта нет, файл не пишется.
                after = modified if self.dry_run else self._store(key, kind, file_id, sha, trailing, len(text), meta)
                self._journal(key, "content-equal-modified" if entry else "unchanged", recorded, after, **extra)
                return None
            previous = int(entry.get("trailing_lf", trailing)) if entry else trailing
            theirs = normalize_export(kind, raw, previous)
            if theirs.strip():  # пустая заготовка прерванного создания не считается правкой
                suffix = PurePosixPath(key).suffix
                relative = f"tmp/conflicts/{key[: -len(suffix)]}-{stamp or self.stamp}{suffix}"
                if not self.dry_run:
                    self.write_file(theirs, self.root / relative)
                report.setdefault("conflicts", []).append({"path": key, "copy": relative})
                conflict = True
        if self.dry_run:
            self._journal(key, "conflict" if conflict else "updated", recorded, recorded, **extra)
            return "write"
        try:
            self._write(kind, file_id, text, rows)
        except BaseException:
            if entry:  # содержимое могло измениться частично: запись не считается актуальной
                entry["source_sha256"] = ""
            raise
        after = self._store(key, kind, file_id, sha, trailing, len(text))
        self._journal(key, "conflict" if conflict else "updated", recorded, after, **extra)
        return "write"

    def reconnect(self, keys: list[str]) -> list[dict[str, str]]:
        """Нативные файлы набора для подключения источников блокнота: имя, путь, file_id, ссылка."""
        items = []
        for key in keys:
            entry = self.entries.get(key)
            if entry and entry.get("file_id"):
                suffix = PurePosixPath(key).suffix
                items.append({"path": key[: -len(suffix)], "name": native_name(key), "kind": entry["kind"],
                              "file_id": entry["file_id"], "url": URL_BY_KIND[entry["kind"]].format(entry["file_id"])})
        return items


def locate(api: GoogleApi, root_id: str, key: str) -> dict[str, Any] | None:
    """Наш нативный файл по пути (без sync-state): каталоги Диска по именам, файл - по `appProperties.kenshi_source`."""
    parent = root_id
    for part in key.split("/")[:-1]:
        found = api.find_files(parent, part, FOLDER_MIME)
        if not found:
            return None
        parent = str(found[0]["id"])
    kind = kind_of(key)
    found = api.find_owned(parent, source_tag(key), MIME_BY_KIND[str(kind)])
    return found[0] if found else None
