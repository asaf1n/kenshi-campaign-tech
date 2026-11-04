"""Технический экстрактор визуальных данных и текста интерфейса скриншотов Kenshi.

Принципы:
1. Только объективные факты: файловые метаданные (SHA-256, mtime, dHash), перцептивная группировка дублей и OCR интерфейса.
   Основной источник OCR — машинный индекс `tools.screenshot_hud`; Gemini включается только явно (`KENSHI_SCREENSHOT_VLM=gemini`).
2. Исключены любые генерации и домыслы описаний сцен, действий или сюжета.
3. Сохранение неопределенности: если текст не распознан, поля остаются null / [], а не заполняются значениями по умолчанию.
4. Кэширование по SHA-256: неизмененные кадры не обрабатываются повторно.
5. Интерпретация игрового процесса полностью делегирована чат-модели / LLM-компаньону.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
from typing import Any

from tools.screenshot_hud import to_ocr


PROMPT_KENSHI_OCR = """Ты — технический инструмент извлечения текста (OCR) из интерфейса игры Kenshi на скриншоте.
Извлеки ТОЛЬКО фактически видимый и различимый текст интерфейса в формате строгого JSON со следующими полями:
{
  "game_day": int или null (число игрового дня рядом со спидометром/часами в правом нижнем углу интерфейса, например "День 45"),
  "game_time": "HH:MM" или null (игровое время на часах, например "14:30"),
  "location": string или null (название зоны, биома или поселения, если явно подписано в UI),
  "characters": [string] (имена персонажей игрока и NPC, написанные в плашках/панели отряда/цели),
  "dialogues": [{"speaker": string или null, "text": string, "confidence": float}] (реплики в облачках диалогов speech bubbles над головами),
  "notifications": [string] (системные сообщения и уведомления интерфейса),
  "items_or_tasks": [string] (названия предметов, задач или текущих действий из панели состояния)
}
КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО:
- Описывать визуальное окружение, погоду, графику или композицию.
- Придумывать действия персонажей, сюжет или трактовать события.
- Домысливать текст, если он не читается или отсутствует.
Если элемент отсутствует или не различим, возвращай null для одиночных полей и пустой массив [] для списков. Ответ СТРОГО в JSON без пояснений."""


def resolve_api_key(api_key: str | None) -> str | None:
    """Ключ из окружения действует только при явном включении: наличие ключа не должно отправлять кадры наружу."""
    if api_key:
        return api_key
    return os.environ.get("GEMINI_API_KEY") if os.environ.get("KENSHI_SCREENSHOT_VLM") == "gemini" else None


def compress_image_for_ocr(image_path: Path, max_width: int = 800) -> bytes | None:
    """Сжатие изображения в JPEG для передачи в OCR-экстрактор."""
    image_path = Path(image_path)
    if not image_path.is_file():
        return None

    container = None
    try:
        import av
        container = av.open(str(image_path))
        for frame in container.decode(video=0):
            w, h = frame.width, frame.height
            if w > max_width:
                new_w = max_width
                new_h = int(h * (max_width / w))
            else:
                new_w, new_h = w, h
            small = frame.reformat(width=new_w, height=new_h)
            out_buf = io.BytesIO()
            out_cont = av.open(out_buf, mode="w", format="mjpeg")
            stream = out_cont.add_stream("mjpeg", rate=1)
            stream.width = new_w
            stream.height = new_h
            stream.pix_fmt = "yuvj420p"
            packet = stream.encode(small)
            out_cont.mux(packet)
            out_cont.close()
            return out_buf.getvalue()
    except Exception:
        pass
    finally:
        if container is not None:
            try:
                container.close()
            except Exception:
                pass

    try:
        return image_path.read_bytes()
    except Exception:
        return None


def call_ocr_vision(
    image_bytes: bytes,
    api_key: str,
    model_name: str = "gemini-3.8-flash",
    timeout: float = 2.5,
) -> dict[str, Any] | None:
    """Вызов технического OCR-распознавания текста интерфейса Kenshi."""
    try:
        import requests
    except ImportError:
        return None

    b64_data = base64.b64encode(image_bytes).decode("utf-8")
    payload = {
        "contents": [{
            "parts": [
                {"text": PROMPT_KENSHI_OCR},
                {"inline_data": {"mime_type": "image/jpeg", "data": b64_data}}
            ]
        }],
        "generationConfig": {
            "response_mime_type": "application/json",
            "temperature": 0.0
        }
    }

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
    try:
        resp = requests.post(url, json=payload, timeout=timeout)
        if resp.status_code == 200:
            res_json = resp.json()
            candidates = res_json.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                if parts:
                    raw_text = parts[0].get("text", "")
                    return json.loads(raw_text)
    except Exception:
        pass

    return None


def extract_single_screenshot(
    item: dict[str, Any],
    cache: dict[str, dict[str, Any]],
    api_key: str | None = None,
    model_name: str = "gemini-3.8-flash",
    now_iso: str | None = None,
    circuit_broken: bool = False,
    hud_rows: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Техническое извлечение данных кадра: метаданные, хэши и OCR без генерации описаний."""
    sha256 = item.get("sha256", "")
    fn = item.get("filename", "")
    order = item.get("order", 1)
    modified_at = item.get("modified_at", "")
    dhash = item.get("dhash", "0000000000000000")
    scene_group_id = item.get("scene_group_id", "seq_001")
    is_exact_duplicate = bool(item.get("is_exact_duplicate", False))
    duplicate_of = item.get("duplicate_of")
    current_time_iso = now_iso or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # 1. Проверка персистентного кэша по SHA-256
    if sha256 in cache:
        cached = dict(cache[sha256])
        cached["order"] = order
        cached["modified_at"] = modified_at
        cached["meta"]["dhash"] = dhash
        cached["meta"]["scene_group_id"] = scene_group_id
        cached["meta"]["is_exact_duplicate"] = is_exact_duplicate
        cached["meta"]["duplicate_of"] = duplicate_of
        return cached

    # 2. Если точный дубликат и оригинал уже в кэше
    if is_exact_duplicate and duplicate_of and duplicate_of in cache:
        orig = cache[duplicate_of]
        dup_rec = {
            "format": "kenshi-screenshot-annotation/1",
            "sha256": sha256,
            "filename": fn,
            "order": order,
            "modified_at": modified_at,
            "meta": {
                "dhash": dhash,
                "scene_group_id": scene_group_id,
                "is_exact_duplicate": True,
                "duplicate_of": duplicate_of,
            },
            "ocr": dict(orig.get("ocr", {
                "game_day": None, "game_time": None, "location": None,
                "characters": [], "dialogues": [], "notifications": [], "items_or_tasks": []
            })),
            "provenance": {
                "source": "cache",
                "confidence": orig.get("provenance", {}).get("confidence"),
                "extracted_at": current_time_iso,
            },
        }
        cache[sha256] = dup_rec
        return dup_rec

    # 3. Машинный индекс интерфейса: ключ — sha256 оригинала, публикуемый кадр уменьшен и хэшируется иначе.
    hud_row = (hud_rows or {}).get(item.get("source_sha256") or sha256)
    hud_ocr = to_ocr(hud_row)
    if hud_ocr:
        rec = {
            "format": "kenshi-screenshot-annotation/1",
            "sha256": sha256,
            "filename": fn,
            "order": order,
            "modified_at": modified_at,
            "meta": {
                "dhash": dhash,
                "scene_group_id": scene_group_id,
                "is_exact_duplicate": is_exact_duplicate,
                "duplicate_of": duplicate_of,
            },
            "ocr": hud_ocr,
            "provenance": {"source": "ocr", "confidence": None, "extracted_at": hud_row["extracted_at"]},
        }
        cache[sha256] = rec
        return rec

    # 4. Попытка OCR интерфейса через VLM (только при явном включении и неразомкнутом Circuit Breaker)
    ocr_data = None
    resolved_api_key = resolve_api_key(api_key)
    raw_path = item.get("source_path") or item.get("path")
    img_path = Path(raw_path) if raw_path else None

    if not circuit_broken and resolved_api_key and img_path and img_path.is_file():
        compressed = compress_image_for_ocr(img_path)
        if compressed:
            ocr_data = call_ocr_vision(compressed, resolved_api_key, model_name=model_name)

    if ocr_data and isinstance(ocr_data, dict):
        rec = {
            "format": "kenshi-screenshot-annotation/1",
            "sha256": sha256,
            "filename": fn,
            "order": order,
            "modified_at": modified_at,
            "meta": {
                "dhash": dhash,
                "scene_group_id": scene_group_id,
                "is_exact_duplicate": is_exact_duplicate,
                "duplicate_of": duplicate_of,
            },
            "ocr": {
                "game_day": ocr_data.get("game_day") if isinstance(ocr_data.get("game_day"), int) else None,
                "game_time": ocr_data.get("game_time") if isinstance(ocr_data.get("game_time"), str) else None,
                "location": ocr_data.get("location") if isinstance(ocr_data.get("location"), str) else None,
                "characters": [str(c) for c in ocr_data.get("characters", []) if c],
                "dialogues": [
                    {
                        "speaker": d.get("speaker") if isinstance(d.get("speaker"), str) else None,
                        "text": str(d.get("text", "")),
                        "confidence": float(d.get("confidence", 0.9)),
                    }
                    for d in ocr_data.get("dialogues", [])
                    if isinstance(d, dict) and d.get("text")
                ],
                "notifications": [str(n) for n in ocr_data.get("notifications", []) if n],
                "items_or_tasks": [str(t) for t in ocr_data.get("items_or_tasks", []) if t],
            },
            "provenance": {
                "source": model_name,
                "confidence": 0.90,
                "extracted_at": current_time_iso,
            },
        }
        cache[sha256] = rec
        return rec

    # 5. Технический экстракт без OCR (сохранение полной неопределенности)
    # Никаких домыслов, описаний сцен или действий по умолчанию!
    metadata_rec = {
        "format": "kenshi-screenshot-annotation/1",
        "sha256": sha256,
        "filename": fn,
        "order": order,
        "modified_at": modified_at,
        "meta": {
            "dhash": dhash,
            "scene_group_id": scene_group_id,
            "is_exact_duplicate": is_exact_duplicate,
            "duplicate_of": duplicate_of,
        },
        "ocr": {
            "game_day": None,
            "game_time": None,
            "location": None,
            "characters": [],
            "dialogues": [],
            "notifications": [],
            "items_or_tasks": [],
        },
        "provenance": {
            "source": "metadata",
            "confidence": None,
            "extracted_at": current_time_iso,
        },
    }
    cache[sha256] = metadata_rec
    return metadata_rec


def load_extractor_cache(cache_path: Path | None) -> dict[str, dict[str, Any]]:
    """Загрузка существующего кэша экстракта из JSONL файла."""
    cache: dict[str, dict[str, Any]] = {}
    if not cache_path or not cache_path.is_file():
        return cache

    try:
        lines = cache_path.read_text(encoding="utf-8").splitlines()
        for line in lines:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            sha = record.get("sha256")
            # Запись без OCR не кэшируется: иначе кадр навсегда останется пустым, когда OCR станет доступен.
            source = record.get("provenance", {}).get("source")
            if sha and "ocr" in record and "visual" not in record and source != "metadata":
                cache[sha] = record
    except Exception:
        pass

    return cache


def extract_screenshots_batch(
    clustered_screenshots: list[dict[str, Any]],
    cache_path: Path | None = None,
    api_key: str | None = None,
    model_name: str = "gemini-3.8-flash",
    now_iso: str | None = None,
    hud_rows: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Пакетное техническое извлечение данных кадров с кэшированием и Circuit Breaker."""
    cache = load_extractor_cache(cache_path)
    extracted_records: list[dict[str, Any]] = []

    circuit_broken = False
    consecutive_api_failures = 0
    resolved_api_key = resolve_api_key(api_key)

    for item in clustered_screenshots:
        sha256 = item.get("sha256", "")
        is_in_cache = sha256 in cache or (item.get("is_exact_duplicate") and item.get("duplicate_of") in cache)

        rec = extract_single_screenshot(
            item=item,
            cache=cache,
            api_key=resolved_api_key,
            model_name=model_name,
            now_iso=now_iso,
            circuit_broken=circuit_broken,
            hud_rows=hud_rows,
        )
        extracted_records.append(rec)

        if not is_in_cache and resolved_api_key and not circuit_broken:
            if rec.get("provenance", {}).get("source") == model_name:
                consecutive_api_failures = 0
            else:
                consecutive_api_failures += 1
                if consecutive_api_failures >= 2:
                    circuit_broken = True

    return extracted_records
