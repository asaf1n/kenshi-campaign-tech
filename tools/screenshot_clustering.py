"""Модуль двухуровневой дедупликации и кластеризации скриншотов Kenshi.

Уровни обработки:
1. Точные дубликаты: выявление одинаковых файлов по SHA-256.
2. Похожие кадры / сцены: перцептивный разностный хэш (dHash) и временная близость
   для группировки последовательных кадров в смысловые эпизоды.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def compute_dhash(image_path: Path) -> str:
    """Вычисление 64-битного разностного хэша (dHash) изображения в формате hex-строки."""
    image_path = Path(image_path)
    # Попытка быстрого вычисления через PyAV (av)
    container = None
    try:
        import av
        container = av.open(str(image_path))
        for frame in container.decode(video=0):
            small = frame.reformat(width=9, height=8, format="gray")
            arr = small.to_ndarray()
            diff = arr[:, 1:] > arr[:, :-1]
            bits = 0
            for i, b in enumerate(diff.flatten()):
                if b:
                    bits |= (1 << i)
            return f"{bits:016x}"
    except Exception:
        pass
    finally:
        if container is not None:
            try:
                container.close()
            except Exception:
                pass

    # Автономный fallback без сторонних библиотек:
    # Чтение контрольных блоков и сигнатурных байт для стабильного хэширования
    try:
        data = image_path.read_bytes()
        if len(data) < 64:
            return "0000000000000000"
        # Семплирование 64 байт из середины файла
        step = max(1, len(data) // 64)
        sample = [data[i * step] for i in range(64)]
        bits = 0
        for i in range(63):
            if sample[i] > sample[i + 1]:
                bits |= (1 << i)
        return f"{bits:016x}"
    except Exception:
        return "0000000000000000"


def hamming_distance(hash1: str, hash2: str) -> int:
    """Вычисление расстояния Хэмминга между двумя hex-хэшами одинаковой длины."""
    try:
        n1 = int(hash1, 16)
        n2 = int(hash2, 16)
        return bin(n1 ^ n2).count("1")
    except ValueError:
        return 64


def cluster_screenshots(
    screenshots: list[dict[str, Any]],
    time_threshold_seconds: float = 180.0,
    hash_threshold_distance: int = 14,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Двухуровневая дедупликация и объединение последовательных кадров в эпизоды.

    Параметры:
      screenshots: хронологически отсортированный список словарей скриншотов
                   (каждый содержит filename, source_path/path, mtime, sha256).
      time_threshold_seconds: максимальный разрыв времени между кадрами одной сцены (по умолчанию 3 минуты).
      hash_threshold_distance: максимальное расстояние Хэмминга dHash для схожих кадров сцены.

    Возвращает:
      (annotated_items, episodes_summary)
      - annotated_items: дополненные словари с полями scene_group_id, is_exact_duplicate, duplicate_of, dhash.
      - episodes_summary: структурированный список эпизодов с диапазоном времени и участниками.
    """
    if not screenshots:
        return [], []

    # 1. Поиск точных дубликатов по SHA-256
    first_seen_sha: dict[str, str] = {}
    annotated: list[dict[str, Any]] = []

    for item in screenshots:
        item_copy = dict(item)
        file_sha = item_copy.get("sha256", "")
        img_p = Path(item_copy.get("source_path") or item_copy.get("path"))

        # Вычисление dhash
        dhash = item_copy.get("dhash")
        if not dhash and img_p.is_file():
            dhash = compute_dhash(img_p)
        item_copy["dhash"] = dhash or "0000000000000000"

        if file_sha in first_seen_sha:
            item_copy["is_exact_duplicate"] = True
            item_copy["duplicate_of"] = first_seen_sha[file_sha]
        else:
            item_copy["is_exact_duplicate"] = False
            item_copy["duplicate_of"] = None
            first_seen_sha[file_sha] = file_sha

        annotated.append(item_copy)

    # 2. Группировка похожих кадров одной сцены в эпизоды
    episodes: list[dict[str, Any]] = []
    current_episode_idx = 1
    current_episode_frames: list[dict[str, Any]] = []

    for idx, item in enumerate(annotated):
        if not current_episode_frames:
            # Первый кадр первого эпизода
            scene_id = f"ep_{current_episode_idx:03d}"
            item["scene_group_id"] = scene_id
            current_episode_frames.append(item)
            continue

        prev_item = current_episode_frames[-1]
        dt = item.get("mtime", 0.0) - prev_item.get("mtime", 0.0)
        h_dist = hamming_distance(item["dhash"], prev_item["dhash"])

        # Проверка границы сцены:
        # Резкий скачок во времени ИЛИ визуальный разрыв между кадрами
        # (если кадр — точный дубль, он безусловно остается в текущей сцене)
        is_exact_dup = item.get("is_exact_duplicate", False)
        is_same_scene = is_exact_dup or (dt <= time_threshold_seconds and h_dist <= hash_threshold_distance)

        if is_same_scene:
            scene_id = f"ep_{current_episode_idx:03d}"
            item["scene_group_id"] = scene_id
            current_episode_frames.append(item)
        else:
            # Завершение текущего эпизода
            episodes.append(_build_episode_summary(current_episode_frames, current_episode_idx))
            current_episode_idx += 1
            scene_id = f"ep_{current_episode_idx:03d}"
            item["scene_group_id"] = scene_id
            current_episode_frames = [item]

    if current_episode_frames:
        episodes.append(_build_episode_summary(current_episode_frames, current_episode_idx))

    return annotated, episodes


def _build_episode_summary(frames: list[dict[str, Any]], ep_index: int) -> dict[str, Any]:
    """Сборка агрегированных сведений об эпизоде/сцене."""
    first = frames[0]
    last = frames[-1]
    scene_id = f"ep_{ep_index:03d}"
    key_frame = next((f for f in frames if not f.get("is_exact_duplicate")), first)

    return {
        "episode_id": scene_id,
        "episode_index": ep_index,
        "count": len(frames),
        "key_filename": key_frame.get("filename"),
        "key_sha256": key_frame.get("sha256"),
        "filenames": [f.get("filename") for f in frames],
        "start_time": first.get("modified_at"),
        "end_time": last.get("modified_at"),
        "exact_duplicates_count": sum(1 for f in frames if f.get("is_exact_duplicate")),
    }
