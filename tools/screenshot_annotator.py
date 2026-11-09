"""Фасадный модуль обратной совместимости, перенаправляющий на tools.screenshot_extractor."""
from __future__ import annotations

from tools.screenshot_extractor import (
    compress_image_for_ocr as compress_image_for_vlm,
    call_ocr_vision as call_gemini_vision,
    extract_single_screenshot as annotate_single_screenshot,
    extract_screenshots_batch as annotate_screenshots_batch,
    load_extractor_cache as load_annotations_cache,
)

__all__ = [
    "compress_image_for_vlm",
    "call_gemini_vision",
    "annotate_single_screenshot",
    "annotate_screenshots_batch",
    "load_annotations_cache",
]
