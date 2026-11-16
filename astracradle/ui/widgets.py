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
                    self._frames.append(ImageTk.PhotoImage(fr))
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
        self.subtitle = "Scanning..."
        self.bind("<Destroy>", self._on_destroy, add="+")
        self.draw()
        if self.animate_enabled:
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
        if not self.animate_enabled:
            self._display_score = float(self.score)
        self.draw()

    def _tick_anim(self) -> None:
        self._after_id = None
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        if self.animate_enabled:
            diff = self.score - self._display_score
            if abs(diff) > 0.25:
                self._display_score += diff * 0.22
            else:
                self._display_score = float(self.score)
            self._orbit_deg = (self._orbit_deg + 1.8) % 360.0
            self._pulse_phase = (self._pulse_phase + 0.14) % (2.0 * math.pi)
            self.draw()
            self._after_id = self.after(45, self._tick_anim)

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
        self._deg = (self._deg + (14.0 if self._scanning else 6.0)) % 360.0
        self._after_id = self.after(45 if self._scanning else 65, self._tick)


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
        self.bind("<Configure>", self._on_resize)
        self.draw()

    def _on_resize(self, event) -> None:
        if event.width > 20 and event.height > 20:
            self.w = event.width
            self.h = event.height
            self.draw()

    def update_palette(self, palette: ThemePalette, line_color: str | None = None) -> None:
        self.palette = palette
        if line_color:
            self.line_color = line_color
        self.configure(bg=palette.bg_card)
        self.draw()

    def push(self, val: float) -> None:
        try:
            f = float(val)
            safe_val = 0.0 if not math.isfinite(f) else max(0.0, min(100.0, f))
        except Exception:
            safe_val = 0.0
        self.values.append(safe_val)
        if len(self.values) > 36:
            self.values = self.values[-36:]
        self.draw()

    def draw(self) -> None:
        self.delete("all")
        p = self.palette
        w = max(40, self.w)
        h = max(30, self.h)

        for frac in (0.25, 0.5, 0.75):
            y = h * frac
            self.create_line(0, y, w, y, fill=p.border_subtle, dash=(2, 4))

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

        self.create_line(*pts, fill=self.line_color, width=2.2, smooth=True)
        lx, ly = pts[-2], pts[-1]
        self.create_oval(
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
        self.bind("<Configure>", self._on_resize)
        self.draw()

    def _on_resize(self, event) -> None:
        if event.width > 40 and event.height > 40:
            self.w = event.width
            self.h = event.height
            self.draw()

    def set_points(self, points: list, resolution: str | None = None) -> None:
        if resolution:
            self.resolution = resolution
        self.points = points[-120:]
        self.draw()

    def draw(self) -> None:
        self.delete("all")
        p = self.palette
        w = max(80, self.w)
        h = max(60, self.h)
        pad_x, pad_y = 14, 18
        for frac in (0.25, 0.5, 0.75):
            y = pad_y + (h - pad_y * 2) * frac
            self.create_line(pad_x, y, w - pad_x, y, fill=p.border_subtle, dash=(2, 4))

        if len(self.points) < 2:
            self.create_text(
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

        def _plot(attr: str, color: str, width_px: float) -> None:
            pts: list[float] = []
            for idx, pt in enumerate(self.points):
                val = max(0.0, min(100.0, float(getattr(pt, attr, 0.0))))
                pts.extend([pad_x + idx * step, h - pad_y - (val / 100.0) * uh])
            if len(pts) >= 4:
                self.create_line(*pts, fill=color, width=width_px, smooth=True)

        _plot("mem_percent", p.accent_brass, 1.6)
        _plot("cpu_percent", p.accent_cyan, 1.6)
        _plot("health_score", p.accent_jade, 2.4)
        self.create_text(
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


