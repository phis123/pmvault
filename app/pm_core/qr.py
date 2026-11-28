"""轻量二维码生成：将文本编码为 SVG 字节或 Base64（Flet 1.0.4 Image(src=bytes) 直接渲染，零原生依赖）。"""

from __future__ import annotations

import base64
import io
from typing import Optional


def make_qr_svg_bytes(text: str, scale: int = 5, border: int = 2) -> Optional[bytes]:
    """返回 SVG UTF-8 字节（可直接传给 Flet ft.Image(src=...)）。"""
    if not text:
        return None
    try:
        import segno  # type: ignore
    except Exception:
        return None
    qr = segno.make(text, error="m")
    buf = io.BytesIO()
    qr.save(buf, kind="svg", scale=scale, border=border, xmldecl=False, dark="#0f172a", light="#ffffff")
    return buf.getvalue()


def make_qr_svg_b64(text: str, scale: int = 5, border: int = 2) -> Optional[str]:
    raw = make_qr_svg_bytes(text, scale=scale, border=border)
    if raw is None:
        return None
    return base64.b64encode(raw).decode("ascii")
