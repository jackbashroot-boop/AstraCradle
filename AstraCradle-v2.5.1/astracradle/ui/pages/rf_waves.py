"""Modular Bandwidth & RF Spectrum Wave page renderer."""

from __future__ import annotations

import tkinter as tk
from typing import TYPE_CHECKING

import customtkinter as ctk

from ..widgets import RfBandwidthWaveChart

if TYPE_CHECKING:
    from ..app import AstraCradleApp


def render_rf_waves_page(app: AstraCradleApp) -> None:
    p = app.palette
    for w in app.rf_waves_scroll.winfo_children():
        w.destroy()

    snap = getattr(app, "_latest_rf_snap", None) or app.rf_scanner.scan_all(rescan_rf=False, include_ping=False)
    app._latest_rf_snap = snap

    hdr = ctk.CTkFrame(
        app.rf_waves_scroll, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
    )
    hdr.pack(fill="x", pady=(0, 8))
    top_r = ctk.CTkFrame(hdr, fg_color="transparent")
    top_r.pack(fill="x", padx=14, pady=(10, 4))
    ctk.CTkLabel(
        top_r,
        text="Bandwidth, Packet & 2.4/5/6 GHz Wi-Fi / Bluetooth RF Spectrum",
        font=app.fonts["section"],
        text_color=p.accent_cyan,
    ).pack(side="left")
    ctk.CTkButton(
        top_r,
        text="Full RF & Ping Sweep",
        height=28,
        corner_radius=7,
        fg_color=p.accent_jade,
        hover_color=p.accent_jade_hover,
        text_color="#071510",
        font=app.fonts["body_bold"],
        command=lambda: app._on_scan_rf_waves(include_ping=True),
    ).pack(side="right", padx=(4, 0))
    ctk.CTkButton(
        top_r,
        text="Bufferbloat Test",
        height=28,
        corner_radius=7,
        fg_color=p.bg_elevated,
        hover_color=p.border_strong,
        text_color=p.accent_brass,
        font=app.fonts["small"],
        command=app._on_run_bufferbloat_check,
    ).pack(side="right", padx=(4, 0))

    band_var = tk.StringVar(value=getattr(app.config_obj, "rf_wave_band_filter", "All Bands"))
    band_menu = ctk.CTkOptionMenu(
        top_r,
        values=["All Bands", "2.4 GHz", "5 GHz", "6 GHz", "Bandwidth Only"],
        variable=band_var,
        height=28,
        width=138,
        fg_color=p.bg_elevated,
        command=app._on_rf_band_filter_change,
    )
    band_menu.pack(side="right", padx=(0, 6))

    ctk.CTkLabel(
        hdr,
        text=(
            f"Live Throughput: RX {snap.total_rx_mbps:.2f} Mbps · TX {snap.total_tx_mbps:.2f} Mbps · "
            f"Max Link: {snap.max_link_mbps or '--'} Mbps · Latency: {snap.latency_ms:.1f} ms (Jitter {snap.jitter_ms:.1f} ms)\n"
            f"RF Congestion Density -> 2.4 GHz: {snap.rf_24_density:.0f}% · 5 GHz: {snap.rf_5_density:.0f}% · "
            f"6 GHz: {snap.rf_6_density:.0f}% · Bluetooth Devices: {snap.bt_count}"
        ),
        font=app.fonts["mono_small"],
        text_color=p.text_secondary,
        justify="left",
        wraplength=app._content_wrap(80),
    ).pack(anchor="w", padx=14, pady=(0, 10))

    chart_card = ctk.CTkFrame(
        app.rf_waves_scroll, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
    )
    chart_card.pack(fill="x", pady=(0, 8))
    app.rf_wave_chart = RfBandwidthWaveChart(
        chart_card,
        p,
        width=820,
        height=330,
        animate=bool(getattr(app.config_obj, "animations_enabled", True)) and not app.config_obj.low_power_ui_mode,
    )
    app.rf_wave_chart.pack(fill="x", padx=12, pady=12)
    app.rf_wave_chart.set_snapshot(snap, band_filter=band_var.get())

    icard = ctk.CTkFrame(
        app.rf_waves_scroll, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
    )
    icard.pack(fill="x", pady=(0, 8))
    ctk.CTkLabel(icard, text="Network Adapters & Packet Waves", font=app.fonts["body_bold"], text_color=p.accent_jade).pack(
        anchor="w", padx=14, pady=(10, 4)
    )
    for iface in snap.interfaces[:10]:
        row = ctk.CTkFrame(icard, fg_color=p.bg_elevated, corner_radius=7)
        row.pack(fill="x", padx=14, pady=2)
        st_lbl = "UP" if iface.is_up else "DOWN"
        ctk.CTkLabel(
            row,
            text=(
                f"[{st_lbl}] {iface.name:<12} · Link {iface.link_mbps or '--'} Mbps · "
                f"RX {iface.rx_mbps:.3f} Mbps ({iface.packets_rx_sec:.0f} pkt/s) · "
                f"TX {iface.tx_mbps:.3f} Mbps ({iface.packets_tx_sec:.0f} pkt/s) · Err/Drop {iface.drop_err_count}"
            ),
            font=app.fonts["mono_small"],
            text_color=p.accent_cyan if iface.is_up else p.text_muted,
            anchor="w",
        ).pack(side="left", padx=10, pady=4)
    ctk.CTkFrame(icard, height=6, fg_color="transparent").pack()

    if getattr(app, "_last_bufferbloat", None) is not None:
        bb = app._last_bufferbloat
        bcard = ctk.CTkFrame(
            app.rf_waves_scroll, fg_color=p.bg_card, border_width=1, border_color=p.accent_brass, corner_radius=12
        )
        bcard.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(
            bcard,
            text=(
                f"Bufferbloat Grade: {bb.grade}  ·  Idle RTT {bb.idle_rtt_ms:.1f} ms  ·  "
                f"Loaded RTT {bb.loaded_rtt_ms:.1f} ms (+{bb.bloat_delta_ms:.1f} ms bloat, Jitter {bb.jitter_ms:.1f} ms)\n"
                f"{bb.recommendation}"
            ),
            font=app.fonts["mono_small"],
            text_color=p.accent_jade if bb.grade in ("A+", "A") else p.accent_brass,
            justify="left",
            wraplength=app._content_wrap(80),
        ).pack(anchor="w", padx=14, pady=10)

    scard = ctk.CTkFrame(
        app.rf_waves_scroll, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
    )
    scard.pack(fill="x", pady=(0, 8))
    ctk.CTkLabel(
        scard,
        text="Per-Process Network, Live I/O (KB/s) & Socket Attribution",
        font=app.fonts["body_bold"],
        text_color=p.accent_cyan,
    ).pack(anchor="w", padx=14, pady=(10, 4))
    socks = app.rf_scanner.scan_process_sockets(limit=8)
    if not socks:
        ctk.CTkLabel(
            scard,
            text="No non-system active TCP/UDP user sockets detected right now.",
            font=app.fonts["small"],
            text_color=p.text_muted,
        ).pack(anchor="w", padx=14, pady=(0, 8))
    else:
        for ps in socks:
            srow = ctk.CTkFrame(scard, fg_color=p.bg_elevated, corner_radius=7)
            srow.pack(fill="x", padx=14, pady=2)
            rem = f" -> {ps.sample_remote}" if ps.sample_remote else ""
            ctk.CTkLabel(
                srow,
                text=(
                    f"[{ps.category}] {ps.name} (PID {ps.pid}) · {ps.io_kbps:.1f} KB/s · "
                    f"{ps.socket_count} sock ({ps.established_count} est){rem}"
                ),
                font=app.fonts["mono_small"],
                text_color=p.text_primary,
            ).pack(side="left", padx=10, pady=4)
            ctk.CTkButton(
                srow,
                text="Deprioritize",
                width=94,
                height=22,
                fg_color=p.bg_card,
                hover_color=p.accent_brass,
                font=app.fonts["caption"],
                command=lambda pid=ps.pid, pname=ps.name: app._throttle_net_hog(pid, pname),
            ).pack(side="right", padx=6, pady=3)
        ctk.CTkFrame(scard, height=6, fg_color="transparent").pack()
