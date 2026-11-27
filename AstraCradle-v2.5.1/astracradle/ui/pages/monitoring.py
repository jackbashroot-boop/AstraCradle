"""Dedicated Live Monitoring & Diagnostics category page (large charts, full sensors, peripherals & diagnostics)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import customtkinter as ctk

from ...core.advanced_controls import GpuPowerAndFanController
from ...core.telemetry import format_bytes, format_uptime
from ..widgets import TelemetryHistoryChart

if TYPE_CHECKING:
    from ..app import AstraCradleApp


def _make_mon_card(parent, p, title: str, fonts: dict) -> ctk.CTkFrame:
    card = ctk.CTkFrame(parent, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12)
    ctk.CTkLabel(card, text=title, font=fonts["body_bold"], text_color=p.text_primary).pack(
        anchor="w", padx=16, pady=(12, 4)
    )
    return card


def build_monitoring_page(app: AstraCradleApp, page: ctk.CTkScrollableFrame) -> None:
    """Initializes the Live Monitoring & Diagnostics page with large history graph + full diagnostic cards."""
    p = app.palette
    page.grid_columnconfigure(0, weight=1)

    hdr_card = ctk.CTkFrame(page, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12)
    hdr_card.grid(row=0, column=0, sticky="ew", pady=(0, 8))
    ctk.CTkLabel(
        hdr_card,
        text="Live Monitoring & Deep Hardware Diagnostics",
        font=app.fonts["section"],
        text_color=p.text_primary,
    ).pack(anchor="w", padx=16, pady=(12, 2))
    ctk.CTkLabel(
        hdr_card,
        text="Full-resolution Health/CPU/RAM history, all hwmon thermal & fan probes, USB/BadUSB audit, conflicts & benchmark tools",
        font=app.fonts["small"],
        text_color=p.text_secondary,
    ).pack(anchor="w", padx=16, pady=(0, 12))

    # Row 1: Large Telemetry History Graph (60s -> 30d)
    hcard = _make_mon_card(page, p, "Telemetry History Graph (Zero-Flicker Rolling Stream)", app.fonts)
    hcard.grid(row=1, column=0, sticky="ew", pady=4)
    tb = ctk.CTkFrame(hcard, fg_color="transparent")
    tb.pack(fill="x", padx=16, pady=(0, 6))
    app.mon_window_var = ctk.StringVar(value=app.config_obj.telemetry_resolution or "5m")
    for w_key, w_lbl in [("60s", "60 sec"), ("5m", "5 min"), ("30m", "30 min"), ("1hr", "1 hour"), ("30d", "30 days")]:
        ctk.CTkButton(
            tb,
            text=w_lbl,
            width=70,
            height=26,
            fg_color=p.bg_elevated,
            font=app.fonts["caption"],
            command=lambda k=w_key: _set_mon_window(app, k),
        ).pack(side="left", padx=2)
    ctk.CTkButton(
        tb,
        text="<- Back to Command Deck",
        width=165,
        height=26,
        fg_color=p.bg_elevated,
        font=app.fonts["caption"],
        command=lambda: app.switch_nav("dashboard"),
    ).pack(side="right")

    app.mon_history_chart = TelemetryHistoryChart(hcard, p, width=820, height=195)
    app.mon_history_chart.pack(fill="x", padx=16, pady=(0, 12))

    # Row 2: Live Sensor & Hardware Matrix
    scard = _make_mon_card(page, p, "Full Thermal, Fan, GPU, Power & Disk Matrix", app.fonts)
    scard.grid(row=2, column=0, sticky="ew", pady=4)
    app.mon_sensors_lbl = ctk.CTkLabel(
        scard,
        text="Collecting full hardware sensor telemetry...",
        font=app.fonts["small"],
        text_color=p.text_secondary,
        justify="left",
        anchor="w",
        wraplength=app._content_wrap(100),
    )
    app.mon_sensors_lbl.pack(fill="x", padx=16, pady=(2, 12))

    # Row 3: Active Diagnostic Findings + Conflicting Software
    fcard = _make_mon_card(page, p, "Full Diagnostic Findings & Conflicting Software", app.fonts)
    fcard.grid(row=3, column=0, sticky="ew", pady=4)
    app.mon_findings_lbl = ctk.CTkLabel(
        fcard,
        text="Running diagnostics scan...",
        font=app.fonts["small"],
        text_color=p.text_primary,
        justify="left",
        anchor="w",
        wraplength=app._content_wrap(100),
    )
    app.mon_findings_lbl.pack(fill="x", padx=16, pady=(2, 10))
    act_row = ctk.CTkFrame(fcard, fg_color="transparent")
    act_row.pack(fill="x", padx=16, pady=(0, 12))
    ctk.CTkButton(
        act_row, text="Run Deep Diagnostic Scan", width=185, fg_color=p.accent_cyan, text_color="#071019", command=app.on_deep_scan
    ).pack(side="left", padx=(0, 6))
    ctk.CTkButton(act_row, text="Run Benchmark", width=140, fg_color=p.bg_elevated, command=app.on_run_benchmark).pack(
        side="left", padx=4
    )
    ctk.CTkButton(
        act_row, text="Export Recovery Bundle", width=165, fg_color=p.bg_elevated, command=app.on_export_recovery_bundle
    ).pack(side="left", padx=4)
    ctk.CTkButton(
        act_row, text="Export AI Diagnostic Log", width=185, fg_color=p.bg_elevated, command=app.on_export_ai_diagnostic_bundle
    ).pack(side="left", padx=4)

    # Row 4: Attached Peripherals & BadUSB Audit + Conflicting Services
    pcard = _make_mon_card(page, p, "Attached USB / Hardware Peripherals, BadUSB Guard & Conflicts", app.fonts)
    pcard.grid(row=4, column=0, sticky="ew", pady=4)
    app.mon_periph_frame = ctk.CTkFrame(pcard, fg_color="transparent")
    app.mon_periph_frame.pack(fill="x", padx=16, pady=(0, 12))


def _set_mon_window(app: AstraCradleApp, win_key: str) -> None:
    if hasattr(app, "mon_window_var"):
        app.mon_window_var.set(win_key)
    update_monitoring_live(app)


def update_monitoring_live(app: AstraCradleApp) -> None:
    """In-place live refresh for the Live Monitoring & Diagnostics page (no page flicker)."""
    if not hasattr(app, "mon_history_chart"):
        return
    snap = app.latest_snapshot
    if snap is None:
        return

    win = app.mon_window_var.get() if hasattr(app, "mon_window_var") else "5m"
    pts = app.history_db.query_resolution(win, limit=120)
    app.mon_history_chart.set_points(pts, resolution=win)

    sensors = app._latest_sensors or {}
    gpu_live = sensors.get("gpu_live") or {}
    temps = sensors.get("temperatures") or []
    fans = sensors.get("fans") or []
    freq = sensors.get("cpu_freq") or {}
    bat = sensors.get("battery")
    lines = [
        f"System: {snap.hostname}  ·  Uptime: {format_uptime(snap.uptime_seconds)}  ·  CPU: {snap.cpu_percent:.1f}% @ {int(freq.get('current_mhz') or snap.cpu_freq_mhz or 0)} MHz  ·  RAM: {format_bytes(snap.mem_used_bytes)} / {format_bytes(snap.mem_total_bytes)} ({snap.mem_percent:.1f}%)",
        f"GPU: {gpu_live.get('name', 'Integrated')}  ·  Temp: {gpu_live.get('temp_c') or 'N/A'}°C  ·  Load: {gpu_live.get('util_percent') or 'N/A'}%  ·  Power: {gpu_live.get('power_draw_w') or 'N/A'} W  ·  Fan: {gpu_live.get('fan_percent') or 'Auto'}%",
    ]
    if temps:
        lines.append("Temperatures: " + ", ".join(f"{t.get('label')} {t.get('current_c')}°C" for t in temps[:8]))
    if fans:
        lines.append("Fans: " + ", ".join(f"{f.get('label')} {f.get('rpm')} RPM" for f in fans[:6]))
    if bat:
        lines.append(f"Battery: {bat.get('percent')}% ({'Plugged In' if bat.get('plugged') else 'On Battery'})")
    if snap.partitions:
        seen_mounts = []
        disk_parts = []
        for pt in snap.partitions[:6]:
            if pt.mountpoint not in seen_mounts:
                seen_mounts.append(pt.mountpoint)
                disk_parts.append(f"{pt.mountpoint} {pt.percent:.1f}% used ({format_bytes(pt.free_bytes)} free)")
        lines.append("Storage: " + "  ·  ".join(disk_parts))
    sensor_txt = "\n".join(lines)
    if getattr(app.mon_sensors_lbl, "_cached_txt", None) != sensor_txt:
        app.mon_sensors_lbl._cached_txt = sensor_txt  # type: ignore[attr-defined]
        app.mon_sensors_lbl.configure(text=sensor_txt)

    rep = app.latest_report
    flines = []
    if rep:
        for r in rep.findings[:6]:
            flines.append(f"[{r.severity.upper()} · {r.subsystem.upper()}] {r.title} — {r.summary}")
    for c in (app._latest_conflicts or [])[:5]:
        flines.append(f"[CONFLICT · {c.severity.upper()}] {c.name} ({c.service_unit or f'PID {c.pid}'}): {c.reason}")
    if not flines:
        flines.append("No active bottlenecks or conflicting background tools detected.")
    ftxt = "\n".join(flines)
    if getattr(app.mon_findings_lbl, "_cached_txt", None) != ftxt:
        app.mon_findings_lbl._cached_txt = ftxt  # type: ignore[attr-defined]
        app.mon_findings_lbl.configure(text=ftxt)


def refresh_monitoring_peripherals(app: AstraCradleApp) -> None:
    """Renders peripheral + BadUSB + conflict rows when opening Monitoring or clicking Deep Scan."""
    if not hasattr(app, "mon_periph_frame"):
        return
    p = app.palette
    for ch in app.mon_periph_frame.winfo_children():
        ch.destroy()
    periphs = app._latest_peripherals or []
    conflicts = app._latest_conflicts or []
    if not periphs and not conflicts:
        ctk.CTkLabel(
            app.mon_periph_frame,
            text="No external USB/HID peripherals or conflicting daemons reported on this scan.",
            font=app.fonts["small"],
            text_color=p.text_muted,
        ).pack(anchor="w")
        return
    for dev in periphs[:10]:
        row = ctk.CTkFrame(app.mon_periph_frame, fg_color=p.bg_elevated, corner_radius=7)
        row.pack(fill="x", pady=2)
        badge_col = p.accent_coral if dev.status == "blocked" else p.text_primary
        ctk.CTkLabel(
            row,
            text=f"{dev.vendor} {dev.name} ({dev.bus.upper()} · {dev.status.upper()}) — {dev.plain_description}",
            font=app.fonts["small"],
            text_color=badge_col,
        ).pack(side="left", padx=10, pady=5)
        if dev.bus == "usb" and dev.status == "blocked":
            ctk.CTkButton(
                row,
                text="Trust & Allow",
                width=105,
                height=24,
                fg_color=p.accent_jade,
                text_color="#071510",
                font=app.fonts["caption"],
                command=lambda d=dev: app._on_trust_usb(d),
            ).pack(side="right", padx=8, pady=4)
    for cf in conflicts[:6]:
        crow = ctk.CTkFrame(app.mon_periph_frame, fg_color=p.bg_elevated, corner_radius=7)
        crow.pack(fill="x", pady=2)
        ctk.CTkLabel(
            crow,
            text=f"[CONFLICT · {cf.severity.upper()}] {cf.name} ({cf.service_unit or f'PID {cf.pid}'}) — {cf.reason}",
            font=app.fonts["small"],
            text_color=p.accent_brass,
        ).pack(side="left", padx=10, pady=5)
        ctk.CTkButton(
            crow,
            text="Turn Off Safely",
            width=116,
            height=24,
            fg_color=p.badge_crit_bg,
            text_color=p.badge_crit_fg,
            font=app.fonts["caption"],
            command=lambda item=cf: app._on_stop_conflict(item),
        ).pack(side="right", padx=8, pady=4)
