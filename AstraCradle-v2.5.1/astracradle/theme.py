"""Design system & color schemes for AstraCradle.

Primary Theme: 'Obsidian Horology' — brushed dark titanium rings, warm brass
Dyson-lattice calibration ticks, and dim-star pale amber/cyan telemetry.
Secondary Theme: 'Atelier Alabaster' — crisp daytime studio mode.
Accessibility Theme: 'High Contrast' — maximum legibility WCAG AAA contrast mode.
"""

from __future__ import annotations

from dataclasses import dataclass
import platform


@dataclass(frozen=True)
class ThemePalette:
    name: str
    mode: str  # "dark" or "light"
    bg_canvas: str
    bg_sidebar: str
    bg_card: str
    bg_elevated: str
    bg_input: str
    border_subtle: str
    border_strong: str
    text_primary: str
    text_secondary: str
    text_muted: str
    accent_cyan: str
    accent_cyan_hover: str
    accent_jade: str
    accent_jade_hover: str
    accent_brass: str
    accent_coral: str
    accent_coral_hover: str
    badge_safe_bg: str
    badge_safe_fg: str
    badge_warn_bg: str
    badge_warn_fg: str
    badge_crit_bg: str
    badge_crit_fg: str
    badge_info_bg: str
    badge_info_fg: str


OBSIDIAN_HOROLOGY = ThemePalette(
    name="Obsidian Horology",
    mode="dark",
    bg_canvas="#0E1217",
    bg_sidebar="#15191F",
    bg_card="#151B23",
    bg_elevated="#1D2530",
    bg_input="#10151C",
    border_subtle="#26313F",
    border_strong="#364659",
    text_primary="#F1F5F9",
    text_secondary="#9FB0C3",
    text_muted="#687B91",
    accent_cyan="#48D8E0",
    accent_cyan_hover="#2BBBC4",
    accent_jade="#2EC994",
    accent_jade_hover="#23A679",
    accent_brass="#D4AE6A",
    accent_coral="#F45B69",
    accent_coral_hover="#D94250",
    badge_safe_bg="#122C26",
    badge_safe_fg="#4BE2AE",
    badge_warn_bg="#2F2616",
    badge_warn_fg="#F4C16D",
    badge_crit_bg="#331820",
    badge_crit_fg="#FF7B87",
    badge_info_bg="#132934",
    badge_info_fg="#6CE8EF",
)

ATELIER_ALABASTER = ThemePalette(
    name="Atelier Alabaster",
    mode="light",
    bg_canvas="#F3F5F8",
    bg_sidebar="#E8ECF2",
    bg_card="#FFFFFF",
    bg_elevated="#F7F9FC",
    bg_input="#EDF1F7",
    border_subtle="#D6DEE8",
    border_strong="#B8C4D4",
    text_primary="#111827",
    text_secondary="#475569",
    text_muted="#64748B",
    accent_cyan="#0891B2",
    accent_cyan_hover="#0E7490",
    accent_jade="#059669",
    accent_jade_hover="#047857",
    accent_brass="#B45309",
    accent_coral="#DC2626",
    accent_coral_hover="#B91C1C",
    badge_safe_bg="#DCFCE7",
    badge_safe_fg="#166534",
    badge_warn_bg="#FEF3C7",
    badge_warn_fg="#92400E",
    badge_crit_bg="#FEE2E2",
    badge_crit_fg="#991B1B",
    badge_info_bg="#CFFAFE",
    badge_info_fg="#155E75",
)

HIGH_CONTRAST = ThemePalette(
    name="High Contrast",
    mode="dark",
    bg_canvas="#000000",
    bg_sidebar="#0A0A0A",
    bg_card="#0D0D0D",
    bg_elevated="#1A1A1A",
    bg_input="#000000",
    border_subtle="#FFFFFF",
    border_strong="#FFE600",
    text_primary="#FFFFFF",
    text_secondary="#E5E7EB",
    text_muted="#D1D5DB",
    accent_cyan="#00F0FF",
    accent_cyan_hover="#00C4D4",
    accent_jade="#00FF88",
    accent_jade_hover="#00CC6A",
    accent_brass="#FFE600",
    accent_coral="#FF4D6D",
    accent_coral_hover="#E03152",
    badge_safe_bg="#00331A",
    badge_safe_fg="#00FF88",
    badge_warn_bg="#332B00",
    badge_warn_fg="#FFE600",
    badge_crit_bg="#3D0011",
    badge_crit_fg="#FF7A95",
    badge_info_bg="#002A33",
    badge_info_fg="#00F0FF",
)


def get_palette(theme_name: str) -> ThemePalette:
    low = (theme_name or "").lower()
    if low in ("high contrast", "high_contrast", "contrast", "hc"):
        return HIGH_CONTRAST
    if low in ("light", "atelier", "atelier alabaster"):
        return ATELIER_ALABASTER
    return OBSIDIAN_HOROLOGY


def get_ui_fonts(scale: float = 1.0) -> dict[str, tuple[str, int, str] | tuple[str, int]]:
    system = platform.system()
    if system == "Darwin":
        sans = "SF Pro Display"
        mono = "Menlo"
        linux_adj = 1.0
    elif system == "Windows":
        sans = "Segoe UI"
        mono = "Consolas"
        linux_adj = 1.0
    else:
        sans = "DejaVu Sans"
        mono = "DejaVu Sans Mono"
        # DejaVu Sans renders noticeably wider/taller per point on Linux/KDE than Segoe UI / SF Pro
        linux_adj = 0.90

    def sz(base: int) -> int:
        return max(8, int(round(base * scale * linux_adj)))

    return {
        "hero": (sans, sz(22), "bold"),
        "title": (sans, sz(16), "bold"),
        "section": (sans, sz(13), "bold"),
        "body_bold": (sans, sz(11), "bold"),
        "body": (sans, sz(11)),
        "small": (sans, sz(10)),
        "caption": (sans, sz(9)),
        "mono": (mono, sz(10)),
        "mono_small": (mono, sz(9)),
    }
