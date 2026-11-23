"""Custom vector-drawn and animated GIF native widgets for AstraCradle."""

from __future__ import annotations

import math
from pathlib import Path
import tkinter as tk

import customtkinter as ctk
from PIL import Image, ImageSequence, ImageTk

from ..theme import ThemePalette


class AnimatedGifLabel(tk.Label):
    """Plays a multi-frame animated GIF smoothly inside a native Tk/CTk frame."""

    def __init__(
        self,
        master,
        gif_path: Path,
        bg: str,
        size: int = 52,
        **kwargs,
    ) -> None:
        super().__init__(master, bg=bg, bd=0, highlightthickness=0, **kwargs)
        self._frames: list[ImageTk.PhotoImage] = []
        self._delays: list[int] = []
        self._idx = 0
        self._after_id: str | None = None

        if gif_path.exists():
            try:
                im = Image.open(gif_path)
                for frame in ImageSequence.Iterator(im):
                    fr = frame.convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
                    self._frames.append(ImageTk.PhotoImage(fr, master=self))
                    self._delays.append(max(35, int(frame.info.get("duration", 55))))
            except Exception:
                self._frames = []

        if self._frames:
            self.configure(image=self._frames[0])
            if len(self._frames) > 1:
                self._schedule_next()
        self.bind("<Destroy>", self._on_destroy, add="+")

    def _on_destroy(self, _event=None) -> None:
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _schedule_next(self) -> None:
        if not self.winfo_exists() or not self._frames:
            return
        delay = self._delays[self._idx % len(self._delays)]
        self._after_id = self.after(delay, self._advance)

    def _advance(self) -> None:
        self._after_id = None
        if not self.winfo_exists() or not self._frames:
            return
        self._idx = (self._idx + 1) % len(self._frames)
        self.configure(image=self._frames[self._idx])
        self._schedule_next()


class EquilibriumGauge(tk.Canvas):
    """Animated Dyson-sphere radial gyroscope gauge displaying 0-100 Equilibrium Score."""

    def __init__(self, master, palette: ThemePalette, size: int = 190, animate: bool = True, **kwargs) -> None:
        super().__init__(
            master,
            width=size,
            height=size,
            bg=palette.bg_card,
            highlightthickness=0,
            bd=0,
            **kwargs,
        )
        self.palette = palette
        self.size = size
        self.score = 100
        self._display_score: float = 0.0
        self._orbit_deg: float = 0.0
        self._pulse_phase: float = 0.0
        self._after_id: str | None = None
        self.animate_enabled = animate
        self._paused = False
        self.subtitle = "Scanning..."
        self.bind("<Destroy>", self._on_destroy, add="+")
        self.draw()
        if self.animate_enabled:
            self._tick_anim()

    def set_paused(self, paused: bool) -> None:
        self._paused = bool(paused)
        if not self._paused and self.animate_enabled and self._after_id is None:
            self._tick_anim()

    def _on_destroy(self, _event=None) -> None:
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def update_palette(self, palette: ThemePalette) -> None:
        self.palette = palette
        self.configure(bg=palette.bg_card)
        self.draw()

    def set_score(self, score: int, subtitle: str = "Equilibrium") -> None:
        try:
            s_val = float(score)
            self.score = 0 if not math.isfinite(s_val) else max(0, min(100, int(s_val)))
        except Exception:
            self.score = 0
        self.subtitle = subtitle
        if not self.animate_enabled or self._paused or self._after_id is None:
            self._display_score = float(self.score)
            self.draw()

    def _tick_anim(self) -> None:
        self._after_id = None
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        if self.animate_enabled and not self._paused:
            diff = self.score - self._display_score
            if abs(diff) > 0.15:
                self._display_score += diff * 0.18
            else:
                self._display_score = float(self.score)
            self._orbit_deg = (self._orbit_deg + 1.35) % 360.0
            self._pulse_phase = (self._pulse_phase + 0.095) % (2.0 * math.pi)
            self.draw()
            self._after_id = self.after(30, self._tick_anim)

    def draw(self) -> None:
        self.delete("all")
        p = self.palette
        s = self.size
        cx = s / 2
        cy = s / 2
        outer_r = s * 0.44
        inner_r = s * 0.34
        shown = int(round(self._display_score)) if self.animate_enabled else self.score

        # Rotating Dyson outer containment lattice
        self.create_oval(
            cx - outer_r,
            cy - outer_r,
            cx + outer_r,
            cy + outer_r,
            outline=p.border_subtle,
            width=1.5,
        )

        # Two counter-rotating orbital nodes on outer Dyson ring
        for idx, offset in enumerate((0.0, 180.0)):
            ang = math.radians(self._orbit_deg + offset)
            nx = cx + outer_r * math.cos(ang)
            ny = cy + outer_r * math.sin(ang)
            col = p.accent_cyan if idx == 0 else p.accent_brass
            self.create_oval(nx - 3, ny - 3, nx + 3, ny + 3, fill=col, outline=p.bg_card, width=1)

        # Dyson calibration ticks (slow drift)
        for deg in range(0, 360, 15):
            rad = math.radians(deg + self._orbit_deg * 0.25)
            tick_len = 6 if deg % 45 == 0 else 3
            x1 = cx + (outer_r - 2) * math.cos(rad)
            y1 = cy + (outer_r - 2) * math.sin(rad)
            x2 = cx + (outer_r - 2 - tick_len) * math.cos(rad)
            y2 = cy + (outer_r - 2 - tick_len) * math.sin(rad)
            color = p.accent_brass if deg % 90 == 0 else p.border_strong
            self.create_line(x1, y1, x2, y2, fill=color, width=1.5 if deg % 90 == 0 else 1)

        # Subtle pulsing dim-star inner ring
        pulse_r = inner_r * (0.72 + 0.025 * math.sin(self._pulse_phase))
        self.create_oval(
            cx - pulse_r, cy - pulse_r, cx + pulse_r, cy + pulse_r, outline=p.border_subtle, width=1
        )

        self.create_arc(
            cx - inner_r,
            cy - inner_r,
            cx + inner_r,
            cy + inner_r,
            start=-225,
            extent=270,
            style=tk.ARC,
            outline=p.bg_elevated,
            width=12,
        )

        if self.score >= 85:
            arc_color = p.accent_jade
        elif self.score >= 65:
            arc_color = p.accent_cyan
        elif self.score >= 45:
            arc_color = p.accent_brass
        else:
            arc_color = p.accent_coral

        extent = -2.7 * max(0.0, min(100.0, self._display_score if self.animate_enabled else float(self.score)))
        if abs(extent) > 0.5:
            self.create_arc(
                cx - inner_r,
                cy - inner_r,
                cx + inner_r,
                cy + inner_r,
                start=225,
                extent=extent,
                style=tk.ARC,
                outline=arc_color,
                width=12,
            )

        self.create_text(
            cx,
            cy - 14,
            text=str(shown),
            fill=p.text_primary,
            font=("DejaVu Sans", int(s * 0.17), "bold"),
        )
        self.create_text(
            cx,
            cy + 22,
            text=self.subtitle,
            fill=p.text_secondary,
            font=("DejaVu Sans", 10, "bold"),
        )
        self.create_text(
            cx,
            cy + 39,
            text="DYSON CORE",
            fill=p.text_muted,
            font=("DejaVu Sans", 8),
        )


class DysonOrbitalBeacon(tk.Canvas):
    """Compact animated rotating Dyson beacon + breathing telemetry pulse for top/side bars."""

    def __init__(self, master, palette: ThemePalette, size: int = 22, **kwargs) -> None:
        super().__init__(
            master, width=size, height=size, bg=palette.bg_sidebar, highlightthickness=0, bd=0, **kwargs
        )
        self.palette = palette
        self.size = size
        self._deg = 0.0
        self._scanning = False
        self._after_id: str | None = None
        self.bind("<Destroy>", self._on_destroy, add="+")
        self._tick()

    def _on_destroy(self, _event=None) -> None:
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def set_scanning(self, active: bool) -> None:
        self._scanning = bool(active)

    def _tick(self) -> None:
        self._after_id = None
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        self.delete("all")
        p = self.palette
        s = self.size
        c = s / 2.0
        r = s * 0.36
        ring_col = p.accent_jade if self._scanning else p.accent_cyan
        self.create_oval(c - r, c - r, c + r, c + r, outline=ring_col, width=1.6 if self._scanning else 1.4)
        rad = math.radians(self._deg)
        nx = c + r * math.cos(rad)
        ny = c + r * math.sin(rad)
        self.create_oval(nx - 2.3, ny - 2.3, nx + 2.3, ny + 2.3, fill=p.accent_brass, outline="")
        core_r = 2.6 + 0.9 * math.sin(rad * 2.0)
        self.create_oval(c - core_r, c - core_r, c + core_r, c + core_r, fill=p.accent_jade, outline="")
        self._deg = (self._deg + (9.0 if self._scanning else 3.8)) % 360.0
        self._after_id = self.after(30 if self._scanning else 42, self._tick)


class DysonSunburstWidget(tk.Canvas):
    """Animated interactive radial Sunburst ring visualizing folder disk bloat."""

    def __init__(
        self,
        master,
        palette: ThemePalette,
        slices: list,
        size: int = 230,
        on_select=None,
        **kwargs,
    ) -> None:
        super().__init__(
            master, width=size, height=size, bg=palette.bg_card, highlightthickness=0, bd=0, **kwargs
        )
        self.palette = palette
        self.slices = slices
        self.size = size
        self.on_select = on_select
        self._sweep_progress = 0.0
        self._hover_idx: int | None = None
        self._arc_Bounds: list[tuple[float, float, object]] = []
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)
        self._animate_sweep()

    def _animate_sweep(self) -> None:
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        self._sweep_progress = min(1.0, self._sweep_progress + 0.11)
        self.draw()
        if self._sweep_progress < 1.0:
            self.after(30, self._animate_sweep)

    def _angle_to_slice(self, event) -> int | None:
        c = self.size / 2.0
        dx = event.x - c
        dy = c - event.y
        dist = math.hypot(dx, dy)
        if dist < self.size * 0.18 or dist > self.size * 0.46:
            return None
        ang = math.degrees(math.atan2(dy, dx)) % 360.0
        for idx, (start, extent, _) in enumerate(self._arc_Bounds):
            end = start + extent
            if start <= ang <= end:
                return idx
        return None

    def _on_motion(self, event) -> None:
        idx = self._angle_to_slice(event)
        if idx != self._hover_idx:
            self._hover_idx = idx
            self.draw()

    def _on_leave(self, _event=None) -> None:
        if self._hover_idx is not None:
            self._hover_idx = None
            self.draw()

    def _on_click(self, event) -> None:
        idx = self._angle_to_slice(event)
        if idx is not None and self.on_select and idx < len(self.slices):
            self.on_select(self.slices[idx])

    def draw(self) -> None:
        self.delete("all")
        p = self.palette
        s = self.size
        c = s / 2.0
        r = s * 0.35
        self._arc_Bounds.clear()

        if not self.slices:
            self.create_oval(c - r, c - r, c + r, c + r, outline=p.border_subtle, width=14)
            self.create_text(c, c, text="No Data", fill=p.text_muted, font=("DejaVu Sans", 10))
            return

        cur_angle = 0.0
        for idx, sl in enumerate(self.slices):
            full_ext = max(3.0, sl.fraction * 360.0 * self._sweep_progress)
            w = 28 if idx == self._hover_idx else 22
            self.create_arc(
                c - r,
                c - r,
                c + r,
                c + r,
                start=cur_angle,
                extent=max(1.0, full_ext - 1.5),
                style=tk.ARC,
                outline=sl.color,
                width=w,
            )
            self._arc_Bounds.append((cur_angle, full_ext, sl))
            cur_angle += full_ext

        active = self.slices[self._hover_idx] if self._hover_idx is not None else self.slices[0]
        mb = active.size_bytes / (1024 * 1024)
        sz_txt = f"{mb / 1024:.1f} GB" if mb >= 1024 else f"{mb:.0f} MB"
        self.create_text(c, c - 10, text=active.label[:14], fill=p.text_primary, font=("DejaVu Sans", 10, "bold"))
        self.create_text(c, c + 10, text=sz_txt, fill=p.accent_cyan, font=("DejaVu Sans", 11, "bold"))
        self.create_text(c, c + 27, text="DISK RING", fill=p.text_muted, font=("DejaVu Sans", 7))


class TelemetrySparkline(tk.Canvas):
    """Live hardware sparkline chart for CPU, RAM, or Disk/Net trends."""

    def __init__(
        self,
        master,
        palette: ThemePalette,
        line_color: str,
        width: int = 240,
        height: int = 58,
        **kwargs,
    ) -> None:
        super().__init__(
            master,
            width=width,
            height=height,
            bg=palette.bg_card,
            highlightthickness=0,
            bd=0,
            **kwargs,
        )
        self.palette = palette
        self.line_color = line_color
        self.w = width
        self.h = height
        self.values: list[float] = [0.0] * 30
        self._grid_ids: list[int] = []
        self._line_id: int | None = None
        self._dot_id: int | None = None
        self.bind("<Configure>", self._on_resize)
        self.draw()

    def _on_resize(self, event) -> None:
        if event.width > 20 and event.height > 20 and (abs(event.width - self.w) > 2 or abs(event.height - self.h) > 2):
            self.w = event.width
            self.h = event.height
            self.draw(force_rebuild=True)

    def update_palette(self, palette: ThemePalette, line_color: str | None = None) -> None:
        self.palette = palette
        if line_color:
            self.line_color = line_color
        self.configure(bg=palette.bg_card)
        self.draw(force_rebuild=True)

    def push(self, val: float) -> None:
        try:
            f = float(val)
            safe_val = 0.0 if not math.isfinite(f) else max(0.0, min(100.0, f))
        except Exception:
            safe_val = 0.0
        self.values.append(safe_val)
        if len(self.values) > 36:
            self.values = self.values[-36:]
        self.draw(force_rebuild=False)

    def draw(self, force_rebuild: bool = False) -> None:
        p = self.palette
        w = max(40, self.w)
        h = max(30, self.h)

        n = len(self.values)
        if n < 2:
            return
        pad_y = 6
        usable_h = max(10, h - pad_y * 2)
        step_x = w / max(1, n - 1)
        pts: list[float] = []
        for idx, v in enumerate(self.values):
            x = idx * step_x
            y = h - pad_y - (v / 100.0) * usable_h
            pts.extend([x, y])
        lx, ly = pts[-2], pts[-1]

        if not force_rebuild and self._line_id is not None and self._dot_id is not None and len(self._grid_ids) == 3:
            try:
                for gid, frac in zip(self._grid_ids, (0.25, 0.5, 0.75)):
                    y = h * frac
                    self.coords(gid, 0, y, w, y)
                self.coords(self._line_id, *pts)
                self.coords(self._dot_id, lx - 3.5, ly - 3.5, lx + 3.5, ly + 3.5)
                return
            except Exception:
                pass

        self.delete("all")
        self._grid_ids = []
        for frac in (0.25, 0.5, 0.75):
            y = h * frac
            self._grid_ids.append(self.create_line(0, y, w, y, fill=p.border_subtle, dash=(2, 4)))
        self._line_id = self.create_line(*pts, fill=self.line_color, width=2.2, smooth=True)
        self._dot_id = self.create_oval(
            lx - 3.5, ly - 3.5, lx + 3.5, ly + 3.5, fill=self.line_color, outline=p.bg_card, width=1.5
        )


def make_badge(
    master,
    text: str,
    bg: str,
    fg: str,
    font: tuple = ("DejaVu Sans", 10, "bold"),
) -> ctk.CTkLabel:
    return ctk.CTkLabel(
        master,
        text=f"  {text}  ",
        fg_color=bg,
        text_color=fg,
        corner_radius=6,
        font=font,
        height=22,
    )


class HoverTooltip:
    """Smooth, non-flickering hover tooltip for explaining AstraCradle controls on mouseover."""

    def __init__(self, widget, text: str, palette: ThemePalette, delay_ms: int = 380) -> None:
        self.widget = widget
        self.text = text
        self.palette = palette
        self.delay_ms = delay_ms
        self._after_id: str | None = None
        self._tip_win: tk.Toplevel | None = None
        self._bind_recursive(widget)

    def _bind_recursive(self, w) -> None:
        try:
            w.bind("<Enter>", self._on_enter, add="+")
            w.bind("<Leave>", self._on_leave, add="+")
            w.bind("<ButtonPress>", self._on_leave, add="+")
        except Exception:
            pass

    def _on_enter(self, _event=None) -> None:
        self._cancel()
        try:
            self._after_id = self.widget.after(self.delay_ms, self.show)
        except Exception:
            pass

    def _on_leave(self, _event=None) -> None:
        self._cancel()
        self.hide()

    def _cancel(self) -> None:
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def show(self) -> None:
        if self._tip_win is not None or not self.text:
            return
        try:
            if not self.widget.winfo_exists():
                return
            x = self.widget.winfo_rootx() + 12
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
            scr_w = self.widget.winfo_screenwidth()
            scr_h = self.widget.winfo_screenheight()
            if x + 320 > scr_w:
                x = max(8, scr_w - 330)
            if y + 110 > scr_h:
                y = max(8, self.widget.winfo_rooty() - 90)

            win = tk.Toplevel(self.widget)
            win.wm_overrideredirect(True)
            win.wm_geometry(f"+{x}+{y}")
            try:
                win.attributes("-topmost", True)
            except Exception:
                pass

            frame = tk.Frame(
                win,
                bg=self.palette.bg_elevated,
                highlightbackground=self.palette.accent_cyan,
                highlightthickness=1,
                bd=0,
            )
            frame.pack(fill="both", expand=True)
            lbl = tk.Label(
                frame,
                text=self.text,
                bg=self.palette.bg_elevated,
                fg=self.palette.text_primary,
                justify="left",
                wraplength=300,
                font=("DejaVu Sans", 9),
                padx=10,
                pady=7,
            )
            lbl.pack()
            self._tip_win = win
        except Exception:
            self._tip_win = None

    def hide(self) -> None:
        if self._tip_win is not None:
            try:
                self._tip_win.destroy()
            except Exception:
                pass
            self._tip_win = None


def attach_tooltip(widget, text: str, palette: ThemePalette, delay_ms: int = 380) -> HoverTooltip:
    tip = HoverTooltip(widget, text, palette, delay_ms=delay_ms)
    try:
        setattr(widget, "_astracradle_tooltip", tip)
    except Exception:
        pass
    return tip


class TelemetryHistoryChart(tk.Canvas):
    """Multi-resolution (60s / 5m / 30m / 1hr / 30d) trend canvas plotting Health Score, CPU %, and RAM %."""

    def __init__(self, master, palette: ThemePalette, width: int = 560, height: int = 150, **kwargs) -> None:
        super().__init__(
            master, width=width, height=height, bg=palette.bg_card, highlightthickness=0, bd=0, **kwargs
        )
        self.palette = palette
        self.w = width
        self.h = height
        self.points: list = []
        self.resolution: str = "60s"
        self._grid_ids: list[int] = []
        self._line_ids: dict[str, int] = {}
        self._legend_id: int | None = None
        self._empty_id: int | None = None
        self.bind("<Configure>", self._on_resize)
        self.draw(force_rebuild=True)

    def _on_resize(self, event) -> None:
        if event.width > 40 and event.height > 40:
            if abs(event.width - self.w) > 2 or abs(event.height - self.h) > 2:
                self.w = event.width
                self.h = event.height
                self.draw(force_rebuild=True)

    def set_points(self, points: list, resolution: str | None = None) -> None:
        if resolution:
            self.resolution = resolution
        self.points = points[-120:]
        self.draw(force_rebuild=False)

    def draw(self, force_rebuild: bool = False) -> None:
        p = self.palette
        w = max(80, self.w)
        h = max(60, self.h)
        pad_x, pad_y = 14, 18

        if len(self.points) < 2:
            self.delete("all")
            self._grid_ids = []
            self._line_ids = {}
            self._legend_id = None
            for frac in (0.25, 0.5, 0.75):
                y = pad_y + (h - pad_y * 2) * frac
                self._grid_ids.append(self.create_line(pad_x, y, w - pad_x, y, fill=p.border_subtle, dash=(2, 4)))
            self._empty_id = self.create_text(
                w / 2,
                h / 2,
                text=f"Collecting {self.resolution} telemetry samples...",
                fill=p.text_muted,
                font=("DejaVu Sans", 10),
            )
            return

        uw = max(20, w - pad_x * 2)
        uh = max(20, h - pad_y * 2)
        step = uw / max(1, len(self.points) - 1)

        def _pts_for(attr: str) -> list[float]:
            pts: list[float] = []
            for idx, pt in enumerate(self.points):
                val = max(0.0, min(100.0, float(getattr(pt, attr, 0.0))))
                pts.extend([pad_x + idx * step, h - pad_y - (val / 100.0) * uh])
            return pts

        if (
            not force_rebuild
            and self._empty_id is None
            and len(self._grid_ids) == 3
            and len(self._line_ids) == 3
            and self._legend_id is not None
        ):
            try:
                for attr in ("mem_percent", "cpu_percent", "health_score"):
                    pts = _pts_for(attr)
                    if len(pts) >= 4:
                        self.coords(self._line_ids[attr], *pts)
                self.itemconfigure(
                    self._legend_id,
                    text=f"[{self.resolution}]  Score   CPU %   RAM %  ({len(self.points)} pts)",
                )
                return
            except Exception:
                pass

        self.delete("all")
        self._empty_id = None
        self._grid_ids = []
        self._line_ids = {}
        for frac in (0.25, 0.5, 0.75):
            y = pad_y + (h - pad_y * 2) * frac
            self._grid_ids.append(self.create_line(pad_x, y, w - pad_x, y, fill=p.border_subtle, dash=(2, 4)))

        for attr, color, width_px in (
            ("mem_percent", p.accent_brass, 1.6),
            ("cpu_percent", p.accent_cyan, 1.6),
            ("health_score", p.accent_jade, 2.4),
        ):
            pts = _pts_for(attr)
            if len(pts) >= 4:
                self._line_ids[attr] = self.create_line(*pts, fill=color, width=width_px, smooth=True)

        self._legend_id = self.create_text(
            pad_x + 4,
            8,
            anchor="w",
            text=f"[{self.resolution}]  Score   CPU %   RAM %  ({len(self.points)} pts)",
            fill=p.text_muted,
            font=("DejaVu Sans", 8),
        )


class FanCurveCanvas(tk.Canvas):
    """Interactive Draft-vs-Applied GPU Temperature-to-Fan% Curve Editor with heat gradient."""

    TEMP_MIN, TEMP_MAX = 35, 95
    FAN_MIN, FAN_MAX = 20, 100

    def __init__(
        self,
        master,
        palette: ThemePalette,
        applied_points: list[list[int]] | None = None,
        draft_points: list[list[int]] | None = None,
        live_temp_c: float | None = None,
        width: int = 560,
        height: int = 190,
        on_change=None,
        **kwargs,
    ) -> None:
        super().__init__(
            master, width=width, height=height, bg=palette.bg_elevated, highlightthickness=0, bd=0, **kwargs
        )
        self.palette = palette
        self.w = width
        self.h = height
        self.applied_points: list[list[int]] = [list(pt) for pt in (applied_points or [[40, 30], [55, 45], [70, 65], [80, 82], [90, 100]])]
        self.draft_points: list[list[int]] = [list(pt) for pt in (draft_points or self.applied_points)]
        self.live_temp_c = live_temp_c
        self.on_change = on_change
        self._drag_idx: int | None = None

        self.bind("<Configure>", self._on_resize)
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.draw()

    def _on_resize(self, event) -> None:
        if event.width > 80 and event.height > 60:
            self.w = event.width
            self.h = event.height
            self.draw()

    def set_curves(self, applied: list[list[int]], draft: list[list[int]] | None = None) -> None:
        self.applied_points = [list(pt) for pt in applied]
        if draft is not None:
            self.draft_points = [list(pt) for pt in draft]
        self.draw()

    def _bounds(self) -> tuple[float, float, float, float]:
        return 42.0, 18.0, max(120.0, self.w - 18.0), max(80.0, self.h - 26.0)

    def _pt_to_xy(self, temp: float, fan: float) -> tuple[float, float]:
        x0, y0, x1, y1 = self._bounds()
        tx = (max(self.TEMP_MIN, min(self.TEMP_MAX, temp)) - self.TEMP_MIN) / float(self.TEMP_MAX - self.TEMP_MIN)
        fy = (max(self.FAN_MIN, min(self.FAN_MAX, fan)) - self.FAN_MIN) / float(self.FAN_MAX - self.FAN_MIN)
        return x0 + tx * (x1 - x0), y1 - fy * (y1 - y0)

    def _xy_to_pt(self, x: float, y: float) -> tuple[int, int]:
        x0, y0, x1, y1 = self._bounds()
        tx = max(0.0, min(1.0, (x - x0) / max(1.0, x1 - x0)))
        fy = max(0.0, min(1.0, (y1 - y) / max(1.0, y1 - y0)))
        temp = int(round(self.TEMP_MIN + tx * (self.TEMP_MAX - self.TEMP_MIN)))
        fan = int(round(self.FAN_MIN + fy * (self.FAN_MAX - self.FAN_MIN)))
        return temp, fan

    def _on_press(self, event) -> None:
        best_idx = None
        best_d = 18.0
        for idx, (t, f) in enumerate(self.draft_points):
            px, py = self._pt_to_xy(t, f)
            d = math.hypot(event.x - px, event.y - py)
            if d < best_d:
                best_d = d
                best_idx = idx
        if best_idx is None and self.draft_points:
            # Click anywhere moves nearest temperature node to clicked fan %
            t_click, f_click = self._xy_to_pt(event.x, event.y)
            best_idx = min(range(len(self.draft_points)), key=lambda i: abs(self.draft_points[i][0] - t_click))
            self.draft_points[best_idx][1] = f_click
        self._drag_idx = best_idx
        self.draw()
        if self.on_change:
            self.on_change(self.draft_points)

    def _on_drag(self, event) -> None:
        if self._drag_idx is None or not (0 <= self._drag_idx < len(self.draft_points)):
            return
        t_new, f_new = self._xy_to_pt(event.x, event.y)
        # Keep x-order monotonic with small 3C gap
        lo_t = self.TEMP_MIN if self._drag_idx == 0 else self.draft_points[self._drag_idx - 1][0] + 3
        hi_t = self.TEMP_MAX if self._drag_idx == len(self.draft_points) - 1 else self.draft_points[self._drag_idx + 1][0] - 3
        self.draft_points[self._drag_idx] = [max(lo_t, min(hi_t, t_new)), f_new]
        self.draw()
        if self.on_change:
            self.on_change(self.draft_points)

    def _on_release(self, _event) -> None:
        self._drag_idx = None

    def draw(self) -> None:
        self.delete("all")
        p = self.palette
        x0, y0, x1, y1 = self._bounds()
        uw = x1 - x0
        uh = y1 - y0

        # Subtle thermal heat zones across X (cool -> warm -> hot)
        zones = (
            (0.0, 0.45, "#0E222A"),
            (0.45, 0.72, "#242014"),
            (0.72, 1.0, "#2A1519"),
        )
        for f0, f1, col in zones:
            self.create_rectangle(x0 + uw * f0, y0, x0 + uw * f1, y1, fill=col, outline="")

        # Grid lines
        for t in (40, 55, 70, 80, 90):
            gx, _ = self._pt_to_xy(t, self.FAN_MIN)
            self.create_line(gx, y0, gx, y1, fill=p.border_subtle, dash=(2, 4))
            self.create_text(gx, y1 + 11, text=f"{t}°", fill=p.text_muted, font=("DejaVu Sans", 8))
        for fan in (25, 50, 75, 100):
            _, gy = self._pt_to_xy(self.TEMP_MIN, fan)
            self.create_line(x0, gy, x1, gy, fill=p.border_subtle, dash=(2, 4))
            self.create_text(x0 - 6, gy, anchor="e", text=f"{fan}%", fill=p.text_muted, font=("DejaVu Sans", 8))

        self.create_rectangle(x0, y0, x1, y1, outline=p.border_strong, width=1)

        # Live GPU temperature marker if available
        if self.live_temp_c is not None and math.isfinite(self.live_temp_c):
            lx, _ = self._pt_to_xy(self.live_temp_c, self.FAN_MIN)
            self.create_line(lx, y0, lx, y1, fill=p.accent_brass, dash=(3, 3), width=1.5)
            self.create_text(lx + 4, y0 + 8, anchor="w", text=f"Live {self.live_temp_c:.0f}C", fill=p.accent_brass, font=("DejaVu Sans", 8, "bold"))

        # Applied curve (dashed jade)
        app_coords: list[float] = []
        for t, f in self.applied_points:
            px, py = self._pt_to_xy(t, f)
            app_coords.extend([px, py])
        if len(app_coords) >= 4:
            self.create_line(*app_coords, fill=p.accent_jade, width=2.0, dash=(5, 4))

        # Draft curve (solid cyan + draggable nodes)
        dr_coords: list[float] = []
        for t, f in self.draft_points:
            px, py = self._pt_to_xy(t, f)
            dr_coords.extend([px, py])
        if len(dr_coords) >= 4:
            self.create_line(*dr_coords, fill=p.accent_cyan, width=2.6)
        for idx, (t, f) in enumerate(self.draft_points):
            px, py = self._pt_to_xy(t, f)
            r = 6 if idx == self._drag_idx else 5
            self.create_oval(px - r, py - r, px + r, py + r, fill=p.accent_cyan, outline=p.bg_card, width=1.6)
            self.create_text(px, py - 11, text=f"{f}%", fill=p.text_primary, font=("DejaVu Sans", 7, "bold"))

        self.create_text(
            x0 + 6, y0 + 8, anchor="w", text="--- Applied Curve    — Draft Curve (drag nodes)", fill=p.text_secondary, font=("DejaVu Sans", 8)
        )


class SmoothPulseBar(tk.Canvas):
    """Sleek animated topbar activity shimmer bar shown cleanly when scans/updates are busy."""

    def __init__(self, master, palette: ThemePalette, height: int = 3, **kwargs) -> None:
        super().__init__(
            master,
            height=height,
            bg=palette.bg_sidebar,
            highlightthickness=0,
            bd=0,
            **kwargs,
        )
        self.palette = palette
        self._active = False
        self._phase = 0.0
        self._after_id: str | None = None
        self.bind("<Destroy>", self._on_destroy, add="+")

    def _on_destroy(self, _event=None) -> None:
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def set_active(self, active: bool) -> None:
        want = bool(active)
        if want == self._active:
            return
        self._active = want
        if want and self._after_id is None:
            self._tick()
        elif not want:
            self.delete("all")

    def _tick(self) -> None:
        self._after_id = None
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        self.delete("all")
        if not self._active:
            return
        w = max(120, int(self.winfo_width() or 400))
        h = max(2, int(self.winfo_height() or 3))
        pos = 0.5 - 0.5 * math.cos(self._phase)
        seg_w = max(48.0, w * 0.24)
        x0 = pos * (w - seg_w)
        self.create_rectangle(0, 0, w, h, fill=self.palette.border_subtle, outline="")
        self.create_rectangle(x0, 0, x0 + seg_w, h, fill=self.palette.accent_cyan, outline="")
        self._phase = (self._phase + 0.14) % (2.0 * math.pi)
        self._after_id = self.after(28, self._tick)


class CategoryMotionEmblem(tk.Canvas):
    """Per-category bespoke smooth vector animation displayed beside the active view title."""

    CATEGORIES = (
        "dashboard",
        "playbook",
        "hunter",
        "scripts",
        "hardware",
        "modules",
        "wifi",
        "rf_waves",
        "scanner",
        "probalance",
        "autoruns",
        "safeguards",
        "settings",
    )

    def __init__(self, master, palette: ThemePalette, size: int = 34, animate: bool = True, **kwargs) -> None:
        super().__init__(
            master,
            width=size,
            height=size,
            bg=palette.bg_sidebar,
            highlightthickness=0,
            bd=0,
            **kwargs,
        )
        self.palette = palette
        self.size = size
        self.category = "dashboard"
        self.animate_enabled = bool(animate)
        self._paused = False
        self._hover_boost = False
        self._phase = 0.0
        self._after_id: str | None = None
        self.bind("<Enter>", lambda _e: setattr(self, "_hover_boost", True))
        self.bind("<Leave>", lambda _e: setattr(self, "_hover_boost", False))
        self.bind("<Destroy>", self._on_destroy, add="+")
        self.draw()
        if self.animate_enabled:
            self._tick()

    def set_paused(self, paused: bool) -> None:
        self._paused = bool(paused)
        if not self._paused and self.animate_enabled and self._after_id is None:
            self._tick()

    def _on_destroy(self, _event=None) -> None:
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def set_category(self, category: str) -> None:
        self.category = category if category in self.CATEGORIES else "dashboard"
        self.draw()

    def set_animate(self, enabled: bool) -> None:
        want = bool(enabled)
        if want == self.animate_enabled:
            return
        self.animate_enabled = want
        if want and self._after_id is None:
            self._tick()
        self.draw()

    def update_palette(self, palette: ThemePalette) -> None:
        self.palette = palette
        self.configure(bg=palette.bg_sidebar)
        self.draw()

    def _tick(self) -> None:
        self._after_id = None
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        if self.animate_enabled and not self._paused:
            step = 0.22 if getattr(self, "_hover_boost", False) else 0.11
            self._phase = (self._phase + step) % (2.0 * math.pi)
            self.draw()
            self._after_id = self.after(33, self._tick)

    def draw(self) -> None:
        self.delete("all")
        p = self.palette
        s = self.size
        cx = cy = s / 2.0
        r = s * 0.38
        ph = self._phase
        cat = self.category

        if cat == "dashboard":
            # Dyson triple-ring gyroscope core
            self.create_oval(cx - r, cy - r, cx + r, cy + r, outline=p.accent_cyan, width=1.6)
            rx = r * (0.45 + 0.45 * abs(math.cos(ph)))
            ry = r * (0.45 + 0.45 * abs(math.sin(ph)))
            self.create_oval(cx - rx, cy - r * 0.72, cx + rx, cy + r * 0.72, outline=p.accent_jade, width=1.4)
            self.create_oval(cx - r * 0.72, cy - ry, cx + r * 0.72, cy + ry, outline=p.accent_brass, width=1.2)
            self.create_oval(cx - 3, cy - 3, cx + 3, cy + 3, fill=p.accent_cyan, outline="")
        elif cat == "playbook":
            # Flowing step-timeline pulse nodes
            self.create_line(4, cy, s - 4, cy, fill=p.border_strong, width=1.5)
            for idx in range(3):
                nx = 7 + idx * ((s - 14) / 2.0)
                pulse = 3.0 + 1.8 * max(0.0, math.sin(ph - idx * 1.1))
                col = p.accent_jade if idx == int((ph / (2 * math.pi)) * 3) % 3 else p.accent_cyan
                self.create_oval(nx - pulse, cy - pulse, nx + pulse, cy + pulse, fill=col, outline="")
        elif cat == "hunter":
            # 360 radar sweep + blip
            self.create_oval(cx - r, cy - r, cx + r, cy + r, outline=p.accent_jade, width=1.4)
            self.create_oval(cx - r * 0.5, cy - r * 0.5, cx + r * 0.5, cy + r * 0.5, outline=p.border_strong, width=1)
            sx = cx + r * math.cos(ph)
            sy = cy + r * math.sin(ph)
            self.create_line(cx, cy, sx, sy, fill=p.accent_cyan, width=2.0)
            bx = cx + r * 0.55 * math.cos(1.1)
            by = cy + r * 0.55 * math.sin(1.1)
            self.create_oval(bx - 2.2, by - 2.2, bx + 2.2, by + 2.2, fill=p.accent_brass, outline="")
        elif cat == "scripts":
            # Cascading equalizer / terminal data bars
            for i in range(4):
                bh = 5 + 16 * (0.5 + 0.5 * math.sin(ph * 1.6 + i * 0.9))
                x = 6 + i * 6.5
                self.create_rectangle(x, s - 6 - bh, x + 4, s - 6, fill=p.accent_cyan if i % 2 == 0 else p.accent_jade, outline="")
        elif cat == "hardware":
            # Multi-blade thermal fan turbine
            self.create_oval(cx - r, cy - r, cx + r, cy + r, outline=p.border_strong, width=1.3)
            for b in range(4):
                ang = ph * 1.8 + b * (math.pi / 2.0)
                x1 = cx + r * 0.85 * math.cos(ang)
                y1 = cy + r * 0.85 * math.sin(ang)
                self.create_line(cx, cy, x1, y1, fill=p.accent_brass if b % 2 else p.accent_cyan, width=2.4)
            self.create_oval(cx - 2.5, cy - 2.5, cx + 2.5, cy + 2.5, fill=p.text_primary, outline="")
        elif cat == "modules":
            # Morphing modular triad nodes
            for i in range(3):
                ang = ph * 0.8 + i * (2.0 * math.pi / 3.0)
                nx = cx + r * 0.65 * math.cos(ang)
                ny = cy + r * 0.65 * math.sin(ang)
                self.create_rectangle(nx - 3.5, ny - 3.5, nx + 3.5, ny + 3.5, fill=p.accent_cyan, outline=p.accent_jade, width=1)
        elif cat == "wifi":
            # Concentric expanding Wi-Fi RF ripples
            for i in range(3):
                prog = ((ph / (2.0 * math.pi)) + i / 3.0) % 1.0
                rr = 4.0 + prog * (r * 1.15)
                col = p.accent_jade if prog < 0.5 else p.accent_cyan
                self.create_arc(cx - rr, cy + 4 - rr, cx + rr, cy + 4 + rr, start=35, extent=110, style="arc", outline=col, width=1.8)
            self.create_oval(cx - 2.5, cy + 3, cx + 2.5, cy + 8, fill=p.accent_jade, outline="")
        elif cat == "rf_waves":
            # Oscillating dual-frequency sine radio waves
            pts1: list[float] = []
            pts2: list[float] = []
            for px in range(3, s - 2, 2):
                t = (px / max(1.0, float(s))) * 2.0 * math.pi
                pts1.extend([px, cy + math.sin(t * 2.0 + ph * 2.0) * 6.5])
                pts2.extend([px, cy + math.cos(t * 3.0 - ph * 1.5) * 4.5])
            if len(pts1) >= 4:
                self.create_line(*pts1, fill=p.accent_cyan, width=1.8)
            if len(pts2) >= 4:
                self.create_line(*pts2, fill=p.accent_jade, width=1.5)
        elif cat == "scanner":
            # Sweeping drive platter + optical laser arc
            self.create_oval(cx - r, cy - r, cx + r, cy + r, outline=p.accent_cyan, width=1.5)
            deg = (ph * 180.0 / math.pi) % 360.0
            self.create_arc(cx - r * 0.72, cy - r * 0.72, cx + r * 0.72, cy + r * 0.72, start=deg, extent=95, style="arc", outline=p.accent_jade, width=2.5)
            self.create_oval(cx - 2.5, cy - 2.5, cx + 2.5, cy + 2.5, fill=p.accent_brass, outline="")
        elif cat == "probalance":
            # Dynamic equilibrium balance pendulum
            tilt = math.sin(ph * 1.4) * 0.45
            dx = r * math.cos(tilt)
            dy = r * math.sin(tilt)
            self.create_line(cx - dx, cy - dy, cx + dx, cy + dy, fill=p.accent_cyan, width=2.2)
            self.create_oval(cx - dx - 3, cy - dy - 3, cx - dx + 3, cy - dy + 3, fill=p.accent_jade, outline="")
            self.create_oval(cx + dx - 3, cy + dy - 3, cx + dx + 3, cy + dy + 3, fill=p.accent_brass, outline="")
            self.create_polygon(cx, cy + 1, cx - 4, cy + 9, cx + 4, cy + 9, fill=p.border_strong)
        elif cat == "autoruns":
            # Interlocked precision gear
            for i in range(6):
                a = ph + i * (math.pi / 3.0)
                self.create_line(cx + 4 * math.cos(a), cy + 4 * math.sin(a), cx + r * math.cos(a), cy + r * math.sin(a), fill=p.accent_brass, width=2.2)
            self.create_oval(cx - 5, cy - 5, cx + 5, cy + 5, fill=p.bg_card, outline=p.accent_cyan, width=1.6)
        elif cat == "safeguards":
            # Breathing hexagon shield barrier
            scale = 0.85 + 0.15 * math.sin(ph * 1.5)
            rr = r * scale
            pts = []
            for i in range(6):
                a = i * (math.pi / 3.0) - math.pi / 2.0
                pts.extend([cx + rr * math.cos(a), cy + rr * math.sin(a)])
            self.create_polygon(*pts, outline=p.accent_jade, fill="", width=1.8)
            self.create_oval(cx - 3, cy - 3, cx + 3, cy + 3, fill=p.accent_cyan, outline="")
        else:
            # Horology balance wheel (settings)
            self.create_oval(cx - r, cy - r, cx + r, cy + r, outline=p.accent_brass, width=1.6)
            osc = math.sin(ph * 1.6) * 1.2
            self.create_line(cx - r * math.cos(osc), cy - r * math.sin(osc), cx + r * math.cos(osc), cy + r * math.sin(osc), fill=p.accent_cyan, width=1.8)
            self.create_oval(cx - 2.5, cy - 2.5, cx + 2.5, cy + 2.5, fill=p.accent_brass, outline="")


_RF_DX_RANGE = tuple(range(-36, 37, 4))
_RF_GAUSS_16 = tuple(math.exp(-((dx * dx) / (2.0 * 16.0 * 16.0))) for dx in _RF_DX_RANGE)
_RF_GAUSS_13 = tuple(math.exp(-((dx * dx) / (2.0 * 13.0 * 13.0))) for dx in _RF_DX_RANGE)


class RfBandwidthWaveChart(tk.Canvas):
    """Detailed live Bandwidth RX/TX Oscilloscope + Multi-Band RF Radio-Wave Spectrum Chart."""

    def __init__(
        self,
        master,
        palette: ThemePalette,
        width: int = 820,
        height: int = 320,
        animate: bool = True,
        **kwargs,
    ) -> None:
        super().__init__(
            master,
            width=width,
            height=height,
            bg=palette.bg_elevated,
            highlightthickness=1,
            highlightbackground=palette.border_subtle,
            bd=0,
            **kwargs,
        )
        self.palette = palette
        self.w = width
        self.h = height
        self.animate_enabled = bool(animate)
        self._paused = False
        self.band_filter = "All Bands"
        self.snapshot = None
        self._phase = 0.0
        self._hover_x: float | None = None
        self._after_id: str | None = None

        self.bind("<Configure>", self._on_resize)
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Destroy>", self._on_destroy, add="+")
        self.draw()
        if self.animate_enabled:
            self._tick()

    def _on_destroy(self, _event=None) -> None:
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _on_resize(self, event) -> None:
        if event.width > 120 and event.height > 120:
            self.w = event.width
            self.h = event.height
            self.draw()

    def _on_motion(self, event) -> None:
        self._hover_x = float(event.x)
        if not self.animate_enabled:
            self.draw()

    def _on_leave(self, _event=None) -> None:
        self._hover_x = None
        if not self.animate_enabled:
            self.draw()

    def set_snapshot(self, snap, band_filter: str | None = None) -> None:
        self.snapshot = snap
        if band_filter is not None:
            self.band_filter = band_filter
        self.draw()

    def set_band_filter(self, band_filter: str) -> None:
        self.band_filter = band_filter or "All Bands"
        self.draw()

    def set_paused(self, paused: bool) -> None:
        self._paused = bool(paused)
        if not self._paused and self.animate_enabled and self._after_id is None:
            self._tick()

    def _tick(self) -> None:
        self._after_id = None
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        if self.animate_enabled and not self._paused:
            self._phase = (self._phase + 0.14) % (2.0 * math.pi)
            self.draw()
            self._after_id = self.after(35, self._tick)

    def _map_freq_to_x(self, freq_mhz: int, band: str, x0: float, x1: float) -> float:
        uw = max(40.0, x1 - x0)
        if self.band_filter == "2.4 GHz":
            t = max(0.0, min(1.0, (freq_mhz - 2400) / 88.0))
            return x0 + t * uw
        if self.band_filter == "5 GHz":
            t = max(0.0, min(1.0, (freq_mhz - 5150) / 720.0))
            return x0 + t * uw
        if self.band_filter == "6 GHz":
            t = max(0.0, min(1.0, (freq_mhz - 5925) / 1200.0))
            return x0 + t * uw
        # "All Bands": 3 cleanly partitioned spectral panes (2.4 GHz | 5 GHz | 6 GHz)
        if band in ("2.4GHz", "Bluetooth"):
            t = max(0.0, min(1.0, (freq_mhz - 2400) / 88.0))
            return x0 + (t * 0.30) * uw
        if band == "5GHz":
            t = max(0.0, min(1.0, (freq_mhz - 5150) / 720.0))
            return x0 + (0.34 + t * 0.33) * uw
        t = max(0.0, min(1.0, (freq_mhz - 5925) / 1200.0))
        return x0 + (0.71 + t * 0.29) * uw

    def draw(self) -> None:
        self.delete("all")
        p = self.palette
        w = max(240, self.w)
        h = max(180, self.h)
        snap = self.snapshot
        ph = self._phase

        # Top pane: RF Radio Spectrum Wave Chart (60% height)
        x0, x1 = 46.0, float(w - 18)
        rf_y0, rf_y1 = 22.0, h * 0.56
        bw_y0, bw_y1 = h * 0.65, float(h - 22)

        # Background grid
        self.create_rectangle(x0, rf_y0, x1, rf_y1, fill=p.bg_card, outline=p.border_subtle)
        for frac in (0.25, 0.5, 0.75):
            gy = rf_y0 + (rf_y1 - rf_y0) * frac
            self.create_line(x0, gy, x1, gy, fill=p.border_subtle, dash=(2, 4))

        if self.band_filter == "All Bands":
            uw = x1 - x0
            for label, sx in (("2.4 GHz + BT", x0 + uw * 0.02), ("5 GHz UNII", x0 + uw * 0.35), ("6 GHz Wi-Fi 6E", x0 + uw * 0.72)):
                self.create_text(sx, rf_y0 + 10, anchor="w", text=label, fill=p.text_muted, font=("DejaVu Sans", 8, "bold"))
            self.create_line(x0 + uw * 0.32, rf_y0, x0 + uw * 0.32, rf_y1, fill=p.border_strong, dash=(3, 3))
            self.create_line(x0 + uw * 0.69, rf_y0, x0 + uw * 0.69, rf_y1, fill=p.border_strong, dash=(3, 3))
        else:
            self.create_text(x0 + 8, rf_y0 + 10, anchor="w", text=f"RF Spectrum Wave ({self.band_filter})", fill=p.text_muted, font=("DejaVu Sans", 8, "bold"))

        # Animated RF Noise Floor Wave
        nf_pts: list[float] = []
        steps = 90
        for i in range(steps + 1):
            px = x0 + (i / steps) * (x1 - x0)
            ripple = 3.0 * math.sin(i * 0.45 + ph * 2.0) + 1.8 * math.cos(i * 0.9 - ph * 1.4)
            nf_pts.extend([px, rf_y1 - 8.0 - abs(ripple)])
        if len(nf_pts) >= 4:
            self.create_line(*nf_pts, fill=p.border_strong, width=1.2)

        rf_pts = list(snap.rf_channels)[:24] if snap else []
        bt_pts = list(snap.bt_emitters)[:24] if snap else []

        # Draw Gaussian RF channel lobes + modulated carrier sine ripples
        band_colors = {"2.4GHz": p.accent_brass, "5GHz": p.accent_cyan, "6GHz": p.accent_jade}
        visible_rf: list[tuple[float, object]] = []
        for ap in rf_pts:
            if self.band_filter == "2.4 GHz" and ap.band != "2.4GHz":
                continue
            if self.band_filter == "5 GHz" and ap.band != "5GHz":
                continue
            if self.band_filter == "6 GHz" and ap.band != "6GHz":
                continue
            if self.band_filter == "Bandwidth Only":
                continue

            cx = self._map_freq_to_x(ap.freq_mhz, ap.band, x0, x1)
            visible_rf.append((cx, ap))
            col = band_colors.get(ap.band, p.accent_cyan)
            amp = max(12.0, (ap.signal_pct / 100.0) * (rf_y1 - rf_y0 - 24.0))
            gauss = _RF_GAUSS_16 if ap.band == "2.4GHz" else _RF_GAUSS_13
            lobe_pts: list[float] = []
            for dx, g in zip(_RF_DX_RANGE, gauss):
                px = x0 if cx + dx < x0 else (x1 if cx + dx > x1 else cx + dx)
                wave_mod = 1.0 + 0.08 * math.sin(ph * 2.5 + dx * 0.25 + ap.channel)
                py = rf_y1 - 6.0 - amp * g * wave_mod
                lobe_pts.extend([px, py])
            if len(lobe_pts) >= 4:
                self.create_line(*lobe_pts, fill=col, width=2.4 if ap.in_use else 1.5)
            if ap.in_use:
                self.create_oval(cx - 4, rf_y1 - 6 - amp - 4, cx + 4, rf_y1 - 6 - amp + 4, fill=p.accent_jade, outline=p.bg_card)
                self.create_text(cx, max(rf_y0 + 18, rf_y1 - 14 - amp), text=f"Ch {ap.channel} ({ap.signal_pct}%)", fill=p.accent_jade, font=("DejaVu Sans", 7, "bold"))

        # Bluetooth 2.4 GHz emitters as purple/coral frequency spikes
        if self.band_filter in ("All Bands", "2.4 GHz"):
            for bt in bt_pts:
                bx = self._map_freq_to_x(bt.band_mhz, "Bluetooth", x0, x1)
                bh = 14.0 + 6.0 * abs(math.sin(ph * 2.0 + bt.band_mhz))
                self.create_line(bx, rf_y1 - 4, bx, rf_y1 - 4 - bh, fill=p.accent_coral, width=1.6, dash=(2, 2))

        # Hover inspection crosshair
        if self._hover_x is not None and x0 <= self._hover_x <= x1 and visible_rf:
            nearest_cx, nearest_ap = min(visible_rf, key=lambda item: abs(item[0] - self._hover_x))
            self.create_line(nearest_cx, rf_y0, nearest_cx, rf_y1, fill=p.text_muted, dash=(2, 2))
            self.create_text(
                min(x1 - 8, max(x0 + 8, nearest_cx)),
                rf_y0 + 22,
                text=f"{nearest_ap.ssid} · {nearest_ap.band} Ch {nearest_ap.channel} ({nearest_ap.freq_mhz} MHz) · {nearest_ap.signal_pct}%",
                fill=p.text_primary,
                font=("DejaVu Sans", 8, "bold"),
            )

        # Bottom pane: Live Network Bandwidth RX / TX Wave Oscilloscope
        self.create_rectangle(x0, bw_y0, x1, bw_y1, fill=p.bg_card, outline=p.border_subtle)
        rx_hist = list(snap.wave_history_rx) if snap and snap.wave_history_rx else [0.2, 0.3, 0.25, 0.4]
        tx_hist = list(snap.wave_history_tx) if snap and snap.wave_history_tx else [0.1, 0.15, 0.12, 0.2]
        peak_mbps = max(1.0, max(rx_hist + tx_hist))

        self.create_text(
            x0 + 8,
            bw_y0 + 10,
            anchor="w",
            text=f"Bandwidth Oscilloscope · RX {rx_hist[-1]:.2f} Mbps (jade) / TX {tx_hist[-1]:.2f} Mbps (cyan) · Peak {peak_mbps:.2f} Mbps",
            fill=p.text_secondary,
            font=("DejaVu Sans", 8, "bold"),
        )
        for series, col in ((rx_hist, p.accent_jade), (tx_hist, p.accent_cyan)):
            pts: list[float] = []
            n = max(2, len(series))
            for idx, val in enumerate(series):
                px = x0 + (idx / float(n - 1)) * (x1 - x0)
                norm = max(0.0, min(1.0, val / peak_mbps))
                shimmer = 1.5 * math.sin(ph * 1.8 + idx * 0.6) if val > 0.01 else 0.0
                py = bw_y1 - 6.0 - norm * max(12.0, (bw_y1 - bw_y0 - 24.0)) + shimmer
                pts.extend([px, py])
            if len(pts) >= 4:
                self.create_line(*pts, fill=col, width=2.2, smooth=True)


class InteractiveFluidSolarCanvas(tk.Canvas):
    """Interactive Canvas featuring Black & White-inspired mouse fluid particle physics & moving Solar System Orrery."""

    MODES = (
        "Hybrid Solar + Fluid",
        "Fluid Crest (Black & White Style)",
        "Solar System Orrery",
        "Reduced Motion",
    )

    def __init__(
        self,
        master,
        palette: ThemePalette,
        width: int = 240,
        height: int = 112,
        mode: str = "Hybrid Solar + Fluid",
        **kwargs,
    ) -> None:
        super().__init__(
            master,
            width=width,
            height=height,
            bg=palette.bg_elevated,
            highlightthickness=1,
            highlightbackground=palette.border_subtle,
            bd=0,
            **kwargs,
        )
        self.palette = palette
        self.w = width
        self.h = height
        self.mode = mode if mode in self.MODES else "Hybrid Solar + Fluid"
        self._phase = 0.0
        self._mx = width / 2.0
        self._my = height / 2.0
        self._mvx = 0.0
        self._mvy = 0.0
        self._dragging = False
        self._paused = False
        self._after_id: str | None = None

        # 36 fluid particles initialized in a crest/ellipse ring (Black & White particle physics feel)
        self._particles: list[list[float]] = []
        for i in range(36):
            ang = (i / 36.0) * 2.0 * math.pi
            rad = 24.0 + (i % 3) * 8.0
            hx = width * 0.5 + rad * math.cos(ang)
            hy = height * 0.5 + rad * 0.65 * math.sin(ang)
            self._particles.append([hx, hy, 0.0, 0.0, hx, hy, ang])

        self.bind("<Configure>", self._on_resize)
        self.bind("<Motion>", self._on_mouse_move)
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Destroy>", self._on_destroy, add="+")
        self.draw()
        if self.mode != "Reduced Motion":
            self._tick()

    def _on_destroy(self, _event=None) -> None:
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _on_resize(self, event) -> None:
        if event.width >= 10 and event.height >= 10:
            self.w = max(10, int(event.width))
            self.h = max(10, int(event.height))
            for i, pt in enumerate(self._particles):
                ang = pt[6]
                rad = min(self.w, self.h) * (0.24 + (i % 3) * 0.07)
                pt[4] = self.w * 0.5 + rad * math.cos(ang)
                pt[5] = self.h * 0.5 + rad * 0.7 * math.sin(ang)
            self.draw()

    def apply_window_inertia(self, dx: float, dy: float) -> None:
        """Black & White 2 style fluid slosh when dragging/moving the window across screen."""
        if self.mode == "Reduced Motion" or self._paused:
            return
        clamped_x = max(-18.0, min(18.0, -dx * 0.22))
        clamped_y = max(-18.0, min(18.0, -dy * 0.22))
        for pt in self._particles:
            pt[2] += clamped_x
            pt[3] += clamped_y

    def set_paused(self, paused: bool) -> None:
        self._paused = bool(paused)
        if not self._paused and self.mode != "Reduced Motion" and self._after_id is None:
            self._tick()

    def _apply_mouse_impulse(self, x: float, y: float, strength: float) -> None:
        self._mvx = x - self._mx
        self._mvy = y - self._my
        self._mx = x
        self._my = y
        for pt in self._particles:
            dx = pt[0] - x
            dy = pt[1] - y
            dist = math.hypot(dx, dy) + 1.0
            if dist < 68.0:
                force = strength * (1.0 - dist / 68.0)
                # Fluid swirl + radial splash
                pt[2] += (dx / dist) * force * 0.9 - (dy / dist) * force * 0.65 + self._mvx * 0.14
                pt[3] += (dy / dist) * force * 0.9 + (dx / dist) * force * 0.65 + self._mvy * 0.14

    def _on_mouse_move(self, event) -> None:
        self._apply_mouse_impulse(float(event.x), float(event.y), 2.2)

    def _on_press(self, event) -> None:
        self._dragging = True
        self._apply_mouse_impulse(float(event.x), float(event.y), 5.0)

    def _on_drag(self, event) -> None:
        self._apply_mouse_impulse(float(event.x), float(event.y), 4.2)

    def _on_release(self, _event=None) -> None:
        self._dragging = False

    def set_mode(self, mode: str) -> None:
        self.mode = mode if mode in self.MODES else "Hybrid Solar + Fluid"
        if self.mode != "Reduced Motion" and self._after_id is None:
            self._tick()
        self.draw()

    def update_palette(self, palette: ThemePalette) -> None:
        self.palette = palette
        self.configure(bg=palette.bg_elevated)
        self.draw()

    def _tick(self) -> None:
        self._after_id = None
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        if self.mode == "Reduced Motion" or self._paused:
            return

        self._phase = (self._phase + 0.045) % (2.0 * math.pi)
        # Spring + fluid damping step on particles
        for i, pt in enumerate(self._particles):
            ang = pt[6] + self._phase * (0.45 if i % 2 == 0 else -0.3)
            rad = min(self.w, self.h) * (0.24 + (i % 3) * 0.07)
            tx = self.w * 0.5 + rad * math.cos(ang)
            ty = self.h * 0.5 + rad * 0.72 * math.sin(ang)
            pt[4], pt[5] = tx, ty
            pt[2] = (pt[2] + (tx - pt[0]) * 0.085) * 0.84
            pt[3] = (pt[3] + (ty - pt[1]) * 0.085) * 0.84
            pt[0] += pt[2]
            pt[1] += pt[3]

        self.draw()
        self._after_id = self.after(32, self._tick)

    def draw(self) -> None:
        self.delete("all")
        p = self.palette
        w, h = self.w, self.h
        cx = w * 0.5 + ((self._mx - w * 0.5) * 0.03 if self.mode != "Reduced Motion" else 0.0)
        cy = h * 0.5 + ((self._my - h * 0.5) * 0.03 if self.mode != "Reduced Motion" else 0.0)
        ph = self._phase

        # Background subtle starfield
        for i in range(14):
            sx = ((i * 53 + 17) % max(10, int(w - 8))) + 4
            sy = ((i * 31 + 11) % max(10, int(h - 8))) + 4
            self.create_oval(sx, sy, sx + 1.5, sy + 1.5, fill=p.border_strong, outline="")

        # Solar System Orrery layer
        if self.mode in ("Solar System Orrery", "Hybrid Solar + Fluid", "Reduced Motion"):
            # Sun glow + core
            glow = 11.0 + 1.8 * math.sin(ph * 2.0)
            self.create_oval(cx - glow, cy - glow, cx + glow, cy + glow, fill="", outline=p.accent_brass, width=1.2)
            self.create_oval(cx - 7, cy - 7, cx + 7, cy + 7, fill=p.accent_brass, outline="")

            orbits = (
                (0.20, 0.55, 1.9, 2.5, p.accent_cyan),
                (0.32, 0.58, 1.15, 3.6, p.accent_jade),
                (0.43, 0.60, 0.72, 3.0, p.text_primary),
            )
            for rx_f, tilt, speed, pr, pcol in orbits:
                rx = w * rx_f
                ry = h * rx_f * tilt * 1.45
                self.create_oval(cx - rx, cy - ry, cx + rx, cy + ry, outline=p.border_subtle, width=1.0)
                ang = ph * speed + rx_f * 9.0
                px = cx + rx * math.cos(ang)
                py = cy + ry * math.sin(ang)
                self.create_oval(px - pr, py - pr, px + pr, py + pr, fill=pcol, outline="")
                if pr >= 3.5:
                    mx = px + 7.5 * math.cos(ph * 4.2)
                    my = py + 5.5 * math.sin(ph * 4.2)
                    self.create_oval(mx - 1.3, my - 1.3, mx + 1.3, my + 1.3, fill=p.text_muted, outline="")

        # Interactive Black & White-inspired Fluid Particle Crest layer
        if self.mode in ("Fluid Crest (Black & White Style)", "Hybrid Solar + Fluid"):
            for i, pt in enumerate(self._particles):
                px, py = pt[0], pt[1]
                sz = 2.6 if i % 3 == 0 else 1.9
                col = p.accent_cyan if i % 2 == 0 else (p.accent_jade if i % 3 == 0 else p.text_primary)
                self.create_oval(px - sz, py - sz, px + sz, py + sz, fill=col, outline="")
                if i % 4 == 0:
                    nx, ny = self._particles[(i + 1) % len(self._particles)][:2]
                    if math.hypot(nx - px, ny - py) < 28.0:
                        self.create_line(px, py, nx, ny, fill=p.border_strong, width=1.0)

        tag = "Drag/Hover Fluid + Solar" if self.mode == "Hybrid Solar + Fluid" else self.mode
        if w >= 60 and h >= 28:
            self.create_text(8, h - 9, anchor="w", text=tag, fill=p.text_muted, font=("DejaVu Sans", 7))


class SolarBackdropCanvas(tk.Canvas):
    """Subtle full-workspace Solar System starfield & drifting orbital background with window parallax."""

    def __init__(self, master, palette: ThemePalette, enabled: bool = True, **kwargs) -> None:
        super().__init__(
            master,
            bg=palette.bg_canvas,
            highlightthickness=0,
            bd=0,
            **kwargs,
        )
        self.palette = palette
        self.enabled = bool(enabled)
        self._paused = False
        self._phase = 0.0
        self._px_off = 0.0
        self._py_off = 0.0
        self.w = 960
        self.h = 640
        self._after_id: str | None = None
        self.bind("<Configure>", self._on_resize)
        self.bind("<Destroy>", self._on_destroy, add="+")
        self.draw()
        if self.enabled:
            self._tick()

    def _on_destroy(self, _event=None) -> None:
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _on_resize(self, event) -> None:
        if event.width >= 10 and event.height >= 10:
            self.w = max(10, int(event.width))
            self.h = max(10, int(event.height))
            self.draw()

    def apply_parallax(self, dx: float, dy: float) -> None:
        if not self.enabled or self._paused:
            return
        self._px_off = max(-48.0, min(48.0, self._px_off - dx * 0.12))
        self._py_off = max(-48.0, min(48.0, self._py_off - dy * 0.12))

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
        if self.enabled and not self._paused and self._after_id is None:
            self._tick()
        self.draw()

    def set_paused(self, paused: bool) -> None:
        self._paused = bool(paused)
        if not self._paused and self.enabled and self._after_id is None:
            self._tick()

    def update_palette(self, palette: ThemePalette) -> None:
        self.palette = palette
        self.configure(bg=palette.bg_canvas)
        self.draw()

    def _tick(self) -> None:
        self._after_id = None
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        if self.enabled and not self._paused:
            self._phase = (self._phase + 0.018) % (2.0 * math.pi)
            self._px_off *= 0.90
            self._py_off *= 0.90
            self.draw()
            self._after_id = self.after(45, self._tick)

    def draw(self) -> None:
        self.delete("all")
        p = self.palette
        if not self.enabled or p.mode != "dark":
            return
        w = max(10, self.w)
        h = max(10, self.h)
        if w < 40 or h < 40:
            return
        cx = w * 0.72 + self._px_off
        cy = h * 0.34 + self._py_off
        ph = self._phase

        # Drifting low-contrast starfield
        for i in range(28):
            sx = ((i * 137 + int(ph * 18) + int(self._px_off * 0.5)) % max(10, w - 6)) + 3
            sy = ((i * 89 + 29 + int(self._py_off * 0.5)) % max(10, h - 6)) + 3
            sz = 1.6 if i % 5 == 0 else 1.0
            self.create_oval(sx, sy, sx + sz, sy + sz, fill=p.border_subtle, outline="")

        # Wide subtle Solar Orrery rings & distant planets
        for frac, speed, r_p, col in (
            (0.18, 1.3, 2.5, p.border_strong),
            (0.30, 0.75, 3.2, p.accent_cyan),
            (0.44, 0.42, 2.8, p.accent_jade),
        ):
            rx = max(12.0, min(w, h) * frac)
            ry = max(8.0, rx * 0.62)
            self.create_oval(cx - rx, cy - ry, cx + rx, cy + ry, outline=p.border_subtle, width=1.0)
            ang = ph * speed + frac * 7.0
            px = cx + rx * math.cos(ang)
            py = cy + ry * math.sin(ang)
            self.create_oval(px - r_p, py - r_p, px + r_p, py + r_p, fill=col, outline="")





