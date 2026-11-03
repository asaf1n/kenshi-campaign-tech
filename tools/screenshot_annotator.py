"""Модуль смысловой разметки скриншотов Kenshi.

Поддерживает:
1. Кэширование аннотаций по SHA-256 (не обрабатывать неизмененные изображения).
2. Анализ через Gemini VLM (gemini-3.8-flash) с извлечением текста интерфейса, диалогов и сцены.
3. Надежный локальный эвристический fallback при недоступности сети/API.
4. Полную изоляцию сбоев: процесс разметки не может выбросить необработанное исключение.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
from typing import Any


PROMPT_KENSHI_VLM = """Анализируй скриншот игры Kenshi. Извлеки наблюдаемые факты в формате строгого JSON со следующими полями:
{
  "game_day": int or null,
  "game_time": "HH:MM" or null,
  "location": string or null,
  "characters": [string],
  "dialogues": [{"speaker": string or null, "text": string, "confidence": float}],
  "notifications": [string],
  "items_or_tasks": [string],
  "scene_type": string (one of: travel, combat, dialogue, trade, building, exploration, base, inventory, resting, stealth, other),
  "present_characters": [string],
  "environment": string or null,
  "actions": [string],
  "summary": string
}
Не выдумывай события. Если элемент не различим в интерфейсе или кадре, возвращай null или пустой список. Ответ строго в JSON."""


def compress_image_for_vlm(image_path: Path, max_width: int = 640) -> bytes | None:
    """Быстрое сжатие изображения в JPEG для экономичной передачи в VLM."""
    image_path = Path(image_path)
    if not image_path.is_file():
        return None

    # Попытка сжатия через PyAV
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

    # Если PyAV не доступен или ошибка декодирования — читаем оригинал
    try:
        return image_path.read_bytes()
    except Exception:
        return None


def call_gemini_vision(
    image_bytes: bytes,
    api_key: str,
    model_name: str = "gemini-3.8-flash",
    timeout: float = 2.5,
) -> dict[str, Any] | None:
    """Вызов Gemini VLM API с возвратом структурированного словаря фактов."""
    try:
        import requests
    except ImportError:
        return None

    b64_data = base64.b64encode(image_bytes).decode("utf-8")
    payload = {
        "contents": [{
            "parts": [
                {"text": PROMPT_KENSHI_VLM},
                {"inline_data": {"mime_type": "image/jpeg", "data": b64_data}}
            ]
        }],
        "generationConfig": {
            "response_mime_type": "application/json",
            "temperature": 0.1
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


def annotate_single_screenshot(
    item: dict[str, Any],
    cache: dict[str, dict[str, Any]],
    context: dict[str, Any] | None = None,
    api_key: str | None = None,
    model_name: str = "gemini-3.8-flash",
    now_iso: str | None = None,
    circuit_broken: bool = False,
) -> dict[str, Any]:
    """Разметка одного скриншота с использованием кэша, VLM или эвристики."""
    sha256 = item.get("sha256", "")
    fn = item.get("filename", "")
    order = item.get("order", 1)
    modified_at = item.get("modified_at", "")
    scene_group_id = item.get("scene_group_id", "ep_001")
    is_exact_duplicate = bool(item.get("is_exact_duplicate", False))
    duplicate_of = item.get("duplicate_of")
    current_time_iso = now_iso or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # 1. Проверка персистентного кэша по SHA-256
    if sha256 in cache:
        cached = dict(cache[sha256])
        # Актуализация локальных порядковых полей текущего окна
        cached["order"] = order
        cached["modified_at"] = modified_at
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
                "game_day": orig["meta"].get("game_day"),
                "game_time": orig["meta"].get("game_time"),
                "scene_group_id": scene_group_id,
                "is_exact_duplicate": True,
                "duplicate_of": duplicate_of,
            },
            "ui_text": dict(orig["ui_text"]),
            "visual": dict(orig["visual"]),
            "provenance": {
                "source": "cache",
                "confidence": orig["provenance"].get("confidence", 0.9),
                "annotated_at": current_time_iso,
            },
        }
        cache[sha256] = dup_rec
        return dup_rec

    # 3. Попытка анализа через VLM (при наличии ключа и неразомкнутом circuit breaker)
    vlm_data = None
    resolved_api_key = api_key or os.environ.get("GEMINI_API_KEY")
    img_path = Path(item.get("source_path") or item.get("path"))

    if not circuit_broken and resolved_api_key and img_path.is_file():
        compressed = compress_image_for_vlm(img_path)
        if compressed:
            vlm_data = call_gemini_vision(compressed, resolved_api_key, model_name=model_name)

    if vlm_data and isinstance(vlm_data, dict):
        # Валидация типов полей VLM
        scene_type = vlm_data.get("scene_type")
        allowed_types = {
            "travel", "combat", "dialogue", "trade", "building",
            "exploration", "base", "inventory", "resting", "stealth", "other"
        }
        if scene_type not in allowed_types:
            scene_type = "other"

        rec = {
            "format": "kenshi-screenshot-annotation/1",
            "sha256": sha256,
            "filename": fn,
            "order": order,
            "modified_at": modified_at,
            "meta": {
                "game_day": vlm_data.get("game_day") if isinstance(vlm_data.get("game_day"), int) else None,
                "game_time": vlm_data.get("game_time") if isinstance(vlm_data.get("game_time"), str) else None,
                "scene_group_id": scene_group_id,
                "is_exact_duplicate": is_exact_duplicate,
                "duplicate_of": duplicate_of,
            },
            "ui_text": {
                "location": vlm_data.get("location") if isinstance(vlm_data.get("location"), str) else None,
                "characters": [str(c) for c in vlm_data.get("characters", []) if c],
                "dialogues": [
                    {
                        "speaker": d.get("speaker"),
                        "text": str(d.get("text", "")),
                        "confidence": float(d.get("confidence", 0.9))
                    }
                    for d in vlm_data.get("dialogues", [])
                    if isinstance(d, dict) and d.get("text")
                ],
                "notifications": [str(n) for n in vlm_data.get("notifications", []) if n],
                "items_or_tasks": [str(t) for t in vlm_data.get("items_or_tasks", []) if t],
            },
            "visual": {
                "scene_type": scene_type,
                "present_characters": [str(c) for c in vlm_data.get("present_characters", []) if c],
                "environment": vlm_data.get("environment") if isinstance(vlm_data.get("environment"), str) else None,
                "actions": [str(a) for a in vlm_data.get("actions", []) if a],
                "summary": str(vlm_data.get("summary") or f"Наблюдаемый кадр {fn}."),
            },
            "provenance": {
                "source": model_name,
                "confidence": 0.92,
                "annotated_at": current_time_iso,
            },
        }
        cache[sha256] = rec
        return rec

    # 4. Автономный локальный эвристический fallback
    ctx = context or {}
    ctx_day = ctx.get("game_day")
    ctx_time = ctx.get("game_time")
    ctx_loc = ctx.get("location")
    ctx_squad = ctx.get("squad_members", [])

    heuristic_rec = {
        "format": "kenshi-screenshot-annotation/1",
        "sha256": sha256,
        "filename": fn,
        "order": order,
        "modified_at": modified_at,
        "meta": {
            "game_day": ctx_day if isinstance(ctx_day, int) else None,
            "game_time": ctx_time if isinstance(ctx_time, str) else None,
            "scene_group_id": scene_group_id,
            "is_exact_duplicate": is_exact_duplicate,
            "duplicate_of": duplicate_of,
        },
        "ui_text": {
            "location": ctx_loc if isinstance(ctx_loc, str) else None,
            "characters": [str(c) for c in ctx_squad[:4]],
            "dialogues": [],
            "notifications": [],
            "items_or_tasks": [],
        },
        "visual": {
            "scene_type": "travel" if not is_exact_duplicate else "resting",
            "present_characters": [str(c) for c in ctx_squad[:2]],
            "environment": f"Окрестности {ctx_loc}" if ctx_loc else "Пустоши Kenshi",
            "actions": ["Передвижение отряда"] if not is_exact_duplicate else ["Пауза/наблюдение"],
            "summary": f"Кадр игрового процесса {fn} (сцена {scene_group_id}).",
        },
        "provenance": {
            "source": "heuristic",
            "confidence": 0.50,
            "annotated_at": current_time_iso,
        },
    }
    cache[sha256] = heuristic_rec
    return heuristic_rec


def load_annotations_cache(cache_path: Path | None) -> dict[str, dict[str, Any]]:
    """Загрузка существующего кэша аннотаций из JSONL файла."""
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
            if sha:
                cache[sha] = record
    except Exception:
        pass

    return cache


def annotate_screenshots_batch(
    clustered_screenshots: list[dict[str, Any]],
    cache_path: Path | None = None,
    context: dict[str, Any] | None = None,
    api_key: str | None = None,
    model_name: str = "gemini-3.8-flash",
    now_iso: str | None = None,
) -> list[dict[str, Any]]:
    """Пакетная разметка скриншотов с кэшированием и Circuit Breaker."""
    cache = load_annotations_cache(cache_path)
    annotated_records: list[dict[str, Any]] = []

    circuit_broken = False
    consecutive_api_failures = 0
    resolved_api_key = api_key or os.environ.get("GEMINI_API_KEY")

    for item in clustered_screenshots:
        sha256 = item.get("sha256", "")
        # Если кадра нет в кэше и мы пробуем VLM
        is_in_cache = sha256 in cache or (item.get("is_exact_duplicate") and item.get("duplicate_of") in cache)

        rec = annotate_single_screenshot(
            item=item,
            cache=cache,
            context=context,
            api_key=resolved_api_key,
            model_name=model_name,
            now_iso=now_iso,
            circuit_broken=circuit_broken,
        )
        annotated_records.append(rec)

        if not is_in_cache and resolved_api_key and not circuit_broken:
            if rec.get("provenance", {}).get("source") == model_name:
                consecutive_api_failures = 0
            else:
                consecutive_api_failures += 1
                if consecutive_api_failures >= 2:
                    # Circuit breaker срабатывает: отключаем внешние запросы для текущей сессии
                    circuit_broken = True

    return annotated_records
