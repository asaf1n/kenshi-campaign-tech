"""Генерация небольших настоящих изображений для тестов публикации скриншотов."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path


def write_test_image(path: Path, seed: bytes = b"x", size: tuple[int, int] = (64, 48), mtime: float | None = None) -> None:
    """Пишет декодируемый JPEG или PNG (по расширению); содержимое детерминированно зависит от seed."""
    import av
    import numpy as np

    digest = hashlib.sha256(seed).digest()
    width, height = size
    ys, xs = np.mgrid[0:height, 0:width]
    pixels = np.stack(
        [(digest[0] + xs * 3) % 256, (digest[1] + ys * 5) % 256, (digest[2] + xs + ys) % 256], axis=-1
    ).astype("uint8")
    is_png = path.suffix.lower() == ".png"
    pix_fmt = "rgb24" if is_png else "yuvj420p"
    frame = av.VideoFrame.from_ndarray(pixels, format="rgb24").reformat(format=pix_fmt)
    out = av.open(str(path), "w", format="image2")
    try:
        stream = out.add_stream("png" if is_png else "mjpeg", rate=1)
        stream.width, stream.height, stream.pix_fmt = width, height, pix_fmt
        for packet in stream.encode(frame):
            out.mux(packet)
        for packet in stream.encode(None):
            out.mux(packet)
    finally:
        out.close()
    if mtime is not None:
        os.utime(path, (mtime, mtime))
