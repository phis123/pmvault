"""现代极简主题色板、响应式三断点与通用 UI 构件。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import flet as ft

BREAKPOINT_WIDE = 960
BREAKPOINT_MEDIUM = 600

MONO_FONT = "Consolas"


@dataclass(frozen=True)
class Palette:
    bg: str
    surface: str
    surface_alt: str
    border: str
    text: str
    text_muted: str
    text_faint: str
    primary: str
    primary_soft: str
    danger: str
    danger_soft: str
    warning: str
    warning_soft: str
    success: str
    success_soft: str
    selected_bg: str


LIGHT = Palette(
    bg="#F5F7FA",
    surface="#FFFFFF",
    surface_alt="#F8FAFC",
    border="#E5E9F0",
    text="#0F172A",
    text_muted="#475569",
    text_faint="#94A3B8",
    primary="#2563EB",
    primary_soft="#EFF6FF",
    danger="#DC2626",
    danger_soft="#FEF2F2",
    warning="#D97706",
    warning_soft="#FFFBEB",
    success="#059669",
    success_soft="#ECFDF5",
    selected_bg="#EFF6FF",
)

DARK = Palette(
    bg="#0B1120",
    surface="#111827",
    surface_alt="#1E293B",
    border="#1F2937",
    text="#F1F5F9",
    text_muted="#94A3B8",
    text_faint="#64748B",
    primary="#3B82F6",
    primary_soft="#172554",
    danger="#F87171",
    danger_soft="#450A0A",
    warning="#FBBF24",
    warning_soft="#451A03",
    success="#34D399",
    success_soft="#064E3B",
    selected_bg="#1E3A5F",
)


def get_palette(page: Optional[ft.Page], mode: str = "system") -> Palette:
    if mode == "dark":
        return DARK
    if mode == "light":
        return LIGHT
    if page is not None:
        pb = getattr(page, "platform_brightness", None)
        if pb == ft.Brightness.DARK:
            return DARK
    return LIGHT


def layout_mode(width: Optional[float]) -> str:
    """返回 'wide' (三栏桌面/平板横屏) | 'medium' (双栏平板竖屏) | 'narrow' (单栏手机)。"""
    w = float(width or 1080)
    if w >= BREAKPOINT_WIDE:
        return "wide"
    if w >= BREAKPOINT_MEDIUM:
        return "medium"
    return "narrow"


def card(
    content: ft.Control,
    pal: Palette,
    *,
    padding: int = 16,
    radius: int = 12,
    bgcolor: Optional[str] = None,
    border_color: Optional[str] = None,
) -> ft.Container:
    return ft.Container(
        content=content,
        padding=padding,
        border_radius=radius,
        bgcolor=bgcolor or pal.surface,
        border=ft.Border.all(1, border_color or pal.border),
    )


def pill(text: str, color: str, bgcolor: str, size: int = 11) -> ft.Container:
    return ft.Container(
        content=ft.Text(text, size=size, color=color, weight=ft.FontWeight.W_600),
        padding=ft.Padding.symmetric(horizontal=8, vertical=2),
        border_radius=999,
        bgcolor=bgcolor,
    )


def section_title(text: str, pal: Palette, icon: Optional[str] = None, trailing: Optional[ft.Control] = None) -> ft.Row:
    left = [ft.Icon(icon, size=16, color=pal.primary)] if icon else []
    left.append(ft.Text(text, size=13, weight=ft.FontWeight.W_600, color=pal.text_muted))
    controls = [ft.Row(left, spacing=6, tight=True)]
    if trailing is not None:
        controls.append(trailing)
    return ft.Row(controls, alignment=ft.MainAxisAlignment.SPACE_BETWEEN, vertical_alignment=ft.CrossAxisAlignment.CENTER)
