"""Сборка `screenshots_latest.pdf`: страница на кадр с текстовой шапкой над изображением.

Шапка выводится текстом, а не растром: модели и поиск читают её без OCR. Кадры берутся из оригиналов, потому что
1600 px выпуска для мелкого текста HUD недостаточно; исходный каталог только читается.
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

PDF_MAX_WIDTH = 1920
JPEG_QUALITY = 85
FONT_NAME = "KenshiHeader"
FONT_SIZE = 13
LINE_HEIGHT = 17
MARGIN = 12
POINTS_PER_PIXEL = 0.75
FONT_CANDIDATES = (
    Path("C:/Windows/Fonts/arial.ttf"),
    Path("C:/Windows/Fonts/segoeui.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
)


def _encode_frame(source: Path) -> tuple[bytes, int, int]:
    from PIL import Image

    with Image.open(source) as image:
        frame = image.convert("RGB")
    if frame.width > PDF_MAX_WIDTH:
        frame = frame.resize((PDF_MAX_WIDTH, round(frame.height * PDF_MAX_WIDTH / frame.width)), Image.Resampling.LANCZOS)
    buffer = BytesIO()
    frame.save(buffer, format="JPEG", quality=JPEG_QUALITY)
    return buffer.getvalue(), frame.width, frame.height


def build_screenshots_pdf(frames: list[dict[str, Any]], target: Path) -> list[str]:
    """Записывает PDF детерминированно и возвращает имена пропущенных кадров.

    Сигнатура файла не гарантирует целостность: игра может ещё дописывать кадр, поэтому нечитаемый кадр пропускается.
    """
    try:
        from reportlab.lib.utils import ImageReader, simpleSplit
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.pdfgen.canvas import Canvas
    except ImportError as error:
        from tools.publisher import PublisherError
        raise PublisherError("Для сборки PDF нужен пакет reportlab (pip install reportlab)") from error

    font = next((path for path in FONT_CANDIDATES if path.is_file()), None)
    if font is None:
        from tools.publisher import PublisherError
        raise PublisherError("Для шапки PDF не найден шрифт с кириллицей")
    pdfmetrics.registerFont(TTFont(FONT_NAME, str(font)))

    canvas = Canvas(str(target), invariant=1, pageCompression=1)
    canvas.setTitle("Kenshi: новые кадры снимка")
    skipped: list[str] = []
    for frame in frames:
        try:
            data, width_px, height_px = _encode_frame(Path(frame["source_path"]))
        except OSError:
            skipped.append(frame["filename"])
            continue
        width, height = width_px * POINTS_PER_PIXEL, height_px * POINTS_PER_PIXEL
        header = simpleSplit(frame["pdf_header"], FONT_NAME, FONT_SIZE, width - 2 * MARGIN)
        band = len(header) * LINE_HEIGHT + 2 * MARGIN
        canvas.setPageSize((width, height + band))
        canvas.setFont(FONT_NAME, FONT_SIZE)
        for number, line in enumerate(header):
            canvas.drawString(MARGIN, height + band - MARGIN - (number + 1) * LINE_HEIGHT + 4, line)
        canvas.drawImage(ImageReader(BytesIO(data)), 0, 0, width, height)
        canvas.showPage()
    if len(skipped) < len(frames):
        canvas.save()
    return skipped
