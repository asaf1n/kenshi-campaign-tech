"""Проверка принадлежности сейва кампании: сравнение состава персонажей с родительским снимком."""
from __future__ import annotations

from typing import Any

# Порог и обоснование: docs/data-contract.md, раздел «Проверка принадлежности сейва кампании».
MIN_SHARED_SHARE = 0.5


class CampaignMismatchError(ValueError):
    """Сейв не относится к кампании родительского снимка; публикация не выполняется."""


def roster(current: dict[str, Any]) -> frozenset[str]:
    """Устойчивые идентификаторы персонажей снимка (ps:...); имена не используются."""
    return frozenset(
        str(item["id"]) for item in current.get("characters", [])
        if isinstance(item, dict) and item.get("id")
    )


def compare_with_parent(parent: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """Доля общего состава считается от меньшего из двух составов: потеря или рост состава
    при продолжении не должны давать отказ, поэтому знаменатель — меньший состав."""
    parent_ids, candidate_ids = roster(parent), roster(candidate)
    shared = len(parent_ids & candidate_ids)
    base = min(len(parent_ids), len(candidate_ids))
    share = shared / base if base else 0.0
    return {
        "shared": shared,
        "parent_size": len(parent_ids),
        "candidate_size": len(candidate_ids),
        "share": round(share, 4),
        "belongs": shared > 0 and share >= MIN_SHARED_SHARE,
    }


def require_same_campaign(parent: dict[str, Any], candidate: dict[str, Any], campaign_id: str) -> dict[str, Any]:
    if not roster(parent):
        return {"status": "skipped", "reason": "в родительском снимке нет персонажей с устойчивым id"}
    result = compare_with_parent(parent, candidate)
    if not result["belongs"]:
        raise CampaignMismatchError(
            f"сейв не относится к кампании {campaign_id}: общих персонажей с родительским снимком "
            f"{result['shared']} (доля {result['share']:.0%}, порог {MIN_SHARED_SHARE:.0%}); "
            "публикация, доставка и синхронизация Диска не выполнялись. "
            "Для осознанного нового старта или смены ветки используйте --allow-new-campaign."
        )
    return result
