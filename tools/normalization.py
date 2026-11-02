"""Профиль kenshi-session/10 без истории и локальных диагностических данных."""
from __future__ import annotations

import copy
import re
from typing import Any

from tools.io_utils import ExtractionError

_PATH = re.compile(r"(?i)(?:(?<![a-z0-9])[a-z]:[\\/]|\\\\[^\s\\/]+[\\/]|/(?:home|Users|root|tmp)/)[^\r\n\"<>|;,)]*")
_SECRET_KEY = re.compile(r"(?i)(?:password|passwd|secret|token|api[_-]?key|authorization|credential|private[_-]?key)")
_SECRET_TEXT = re.compile(r"(?i)\b(password|passwd|secret|token|api[_-]?key|authorization)\b\s*[:=]\s*(?:Bearer\s+)?(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)")
_TOKEN = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|sk-[A-Za-z0-9_-]{12,})\b")
_AUTH = re.compile(r"(?i)(?:Bearer\s+[A-Za-z0-9._~+/-]+=*|(?<=://)[^/@\s]+:[^/@\s]+@)")
_VOLATILE = {"exported_at", "timings_ms", "cache", "cache_file", "cache_path", "session_file"}


def scrub(value: Any, metadata: bool = False) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if metadata and key in _VOLATILE:
                continue
            safe_key = scrub(key)
            if safe_key in result:
                raise ExtractionError("Коллизия ключей после очистки")
            result[safe_key] = "[REDACTED]" if _SECRET_KEY.search(key) else scrub(item, metadata)
        return result
    if isinstance(value, list):
        return [scrub(item, metadata) for item in value]
    if isinstance(value, str):
        return _PATH.sub("[LOCAL_PATH]", _TOKEN.sub("[REDACTED]", _AUTH.sub("[REDACTED]", _SECRET_TEXT.sub(r"\1=[REDACTED]", value))))
    return value


def validate_session(data: Any) -> None:
    if not isinstance(data, dict) or data.get("format") not in {"kenshi-session/10", "kenshi-session/11"}:
        raise ExtractionError("Поддерживаются только kenshi-session/10 и kenshi-session/11")
    for key, kind in {"save": dict, "world": dict, "meta": dict, "characters": list, "squads": list, "warnings": list}.items():
        if not isinstance(data.get(key), kind):
            raise ExtractionError(f"Повреждена обязательная структура: {key}")
    if not all(isinstance(w, str) for w in data["warnings"]):
        raise ExtractionError("warnings должен содержать строки")
    ids: set[str] = set()
    for character in data["characters"]:
        if not isinstance(character, dict):
            raise ExtractionError("Повреждена запись characters")
        identity = character.get("id")
        if identity is not None:
            if not isinstance(identity, str) or not identity:
                raise ExtractionError("Некорректный characters[].id")
            if identity in ids:
                raise ExtractionError("Повтор characters[].id")
            ids.add(identity)
    squads: set[str] = set()
    for squad in data["squads"]:
        if not isinstance(squad, dict) or not isinstance(squad.get("squad_id"), str) or not squad["squad_id"] or not isinstance(squad.get("members"), list):
            raise ExtractionError("Повреждена запись squads")
        if squad["squad_id"] in squads:
            raise ExtractionError("Повтор squads[].squad_id")
        squads.add(squad["squad_id"])


def normalize(data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    validate_session(data)
    result = copy.deepcopy(data)
    result.pop("changes", None)
    result["save"].pop("path", None)
    # Эти поля зависят от имени папки и mtime, а не от байтов сейва.
    for key in ("saved_at", "name", "kind"):
        result["save"].pop(key, None)
    result["meta"] = scrub(result["meta"], metadata=True)
    result = scrub(result)
    result["characters"].sort(key=lambda c: (c.get("id") or "", str(c.get("name", ""))))
    result["squads"].sort(key=lambda s: s["squad_id"])
    result["warnings"] = sorted(set(result["warnings"]))
    limitations = []
    if any(not str(c.get("id") or "").startswith("ps:") for c in result["characters"]):
        limitations.append("identity: h:*, имя или отсутствие ID не подтверждают устойчивую идентичность")
    categories = {}
    for field in ("research", "property", "supplies"):
        categories[field] = "available" if field in result and result[field] is not None else "unavailable"
    for warning in result["warnings"]:
        low = warning.lower()
        if re.search(r"quick\.save:\s*(?:members|squads)=", low):
            categories["quick_save_counts"] = "inconsistent"
        if any(word in low for word in ("zone", "player_towns", "город", "здани")):
            categories["property"] = "incomplete"
        if "собственность недоступна" in low:
            categories["property"] = "unavailable"
        if "research" in low and any(word in low for word in ("недоступ", "не найден")):
            categories["research"] = "unavailable"
    if result["meta"].get("unresolved_sids") or result["meta"].get("game_data", {}).get("missing"):
        limitations.append("mods: имеются неразрешённые sid или отсутствующие моды")
    return result, {"categories": categories, "limitations": limitations, "warnings": result["warnings"]}
