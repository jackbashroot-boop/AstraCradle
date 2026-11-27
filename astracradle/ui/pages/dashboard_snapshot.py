"""Zero-Flicker Command Deck Snapshot: Detailed Thermals, Expandable Compact Diagnostics & Direct Menu Links."""

from __future__ import annotations

from typing import TYPE_CHECKING

import customtkinter as ctk

from ...core.telemetry import format_bytes

if TYPE_CHECKING:
    from ..app import AstraCradleApp


def _set_lbl_if_changed(lbl: ctk.CTkLabel, text: str, color: str | None = None) -> None:
    if getattr(lbl, "_cached_txt", None) != text:
        lbl._cached_txt = text  # type: ignore[attr-defined]
        if color is not None:
            lbl._cached_col = color  # type: ignore[attr-defined]
            lbl.configure(text=text, text_color=color)
            return
        lbl.configure(text=text)
    elif color is not None and getattr(lbl, "_cached_col", None) != color:
        lbl._cached_col = color  # type: ignore[attr-defined]
        lbl.configure(text_color=color)


def _bind_click_recursive(widget, callback) -> None:
    try:
        widget.configure(cursor="hand2")
    except Exception:
        pass
    widget.bind("<Button-1>", lambda _e: callback())
    for child in widget.winfo_children():
        _bind_click_recursive(child, callback)


def build_command_deck_snapshot(app: AstraCradleApp, page: ctk.CTkScrollableFrame) -> None:
    """Builds persistent in-place Command Deck Snapshot widgets once (eliminating all periodic flicker)."""
    p = app.palette
    app._dash_expanded_keys = set()
    app._dash_thermals_expanded = False

    # Row 4: Detailed Thermals & Expandable Sensors Card (persistent widgets!)
    tcard = ctk.CTkFrame(page, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12)
    tcard.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(4, 6))
    app.dash_diag_panel = tcard

    thdr = ctk.CTkFrame(tcard, fg_color="transparent")
    thdr.pack(fill="x", padx=14, pady=(10, 4))
    ctk.CTkLabel(
        thdr,
        text="Detailed Thermals, Fans & Power Snapshot",
        font=app.fonts["body_bold"],
        text_color=p.accent_cyan,
    ).pack(side="left")

    ctk.CTkButton(
        thdr,
        text="Open Monitoring ->",
        width=130,
        height=24,
        fg_color=p.bg_elevated,
        font=app.fonts["caption"],
        command=lambda: app.switch_nav("monitoring"),
    ).pack(side="right", padx=(4, 0))

    app._dash_therm_exp_btn = ctk.CTkButton(
        thdr,
        text="▸ Expand All Sensors",
        width=148,
        height=24,
        fg_color=p.bg_elevated,
        font=app.fonts["caption"],
        command=lambda: toggle_dash_thermals_expand(app),
    )
    app._dash_therm_exp_btn.pack(side="right", padx=4)

    strip = ctk.CTkFrame(tcard, fg_color="transparent")
    strip.pack(fill="x", padx=14, pady=(2, 8))
    strip.grid_columnconfigure((0, 1, 2, 3), weight=1)
    app._dash_therm_pills = {}
    for idx, (k, lbl_t, nav_tgt) in enumerate(
        [
            ("cpu", "CPU PACKAGE TEMP", "monitoring"),
            ("gpu", "GPU TEMP & POWER", "hardware"),
            ("nvme", "NVME / SSD TEMP", "monitoring"),
            ("fan", "FAN RPM & CLOCK", "hardware"),
        ]
    ):
        pill = ctk.CTkFrame(strip, fg_color=p.bg_elevated, corner_radius=8)
        pill.grid(row=0, column=idx, sticky="ew", padx=3)
        ctk.CTkLabel(pill, text=lbl_t, font=app.fonts["caption"], text_color=p.text_muted).pack(
            anchor="w", padx=10, pady=(4, 0)
        )
        vlbl = ctk.CTkLabel(pill, text="--", font=app.fonts["body_bold"], text_color=p.accent_jade)
        vlbl.pack(anchor="w", padx=10, pady=(0, 5))
        app._dash_therm_pills[k] = vlbl
        _bind_click_recursive(pill, lambda tgt=nav_tgt: app.switch_nav(tgt))

    app._dash_therm_detail_frame = ctk.CTkFrame(tcard, fg_color=p.bg_elevated, corner_radius=8)
    app._dash_therm_detail_lbl = ctk.CTkLabel(
        app._dash_therm_detail_frame,
        text="Collecting detailed thermal & fan probes...",
        font=app.fonts["mono_small"],
        text_color=p.text_secondary,
        justify="left",
        anchor="w",
    )
    app._dash_therm_detail_lbl.pack(fill="x", padx=12, pady=8)

    # Row 5: Compact Individual Expandable Diagnostics Snapshot (persistent rows!)
    dcard = ctk.CTkFrame(page, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12)
    dcard.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(2, 6))
    app.dash_findings_frame = dcard

    dhdr = ctk.CTkFrame(dcard, fg_color="transparent")
    dhdr.pack(fill="x", padx=14, pady=(10, 4))
    ctk.CTkLabel(
        dhdr,
        text="Compact Diagnostics Snapshot (Expand Any Item or Click to Open Menu)",
        font=app.fonts["body_bold"],
        text_color=p.text_primary,
    ).pack(side="left")
    ctk.CTkButton(
        dhdr,
        text="Full Playbook ->",
        width=118,
        height=24,
        fg_color=p.bg_elevated,
        font=app.fonts["caption"],
        command=lambda: app.switch_nav("playbook"),
    ).pack(side="right")

    diag_specs = [
        ("compute", "COMPUTE & THERMALS", "hardware"),
        ("memory_ai", "MEMORY & AI BLOAT", "hunter"),
        ("storage", "STORAGE & CLEANUP", "modules"),
        ("network_fo", "NETWORK, WI-FI & FAILOVER", "wifi"),
        ("security", "ASTRASHIELD & BADUSB", "modules"),
        ("self_heal", "SELF-HEAL & INTEGRITY", "safeguards"),
    ]
    app._dash_diag_rows = {}
    for key, label, target_nav in diag_specs:
        row_box = ctk.CTkFrame(dcard, fg_color=p.bg_elevated, corner_radius=8)
        row_box.pack(fill="x", padx=14, pady=2)
        top_l = ctk.CTkFrame(row_box, fg_color="transparent")
        top_l.pack(fill="x", padx=10, pady=4)

        badge = ctk.CTkLabel(
            top_l, text=label, font=app.fonts["caption"], fg_color=p.bg_card, text_color=p.accent_cyan, corner_radius=5, height=20
        )
        badge.pack(side="left", padx=(0, 8))
        summary_lbl = ctk.CTkLabel(top_l, text="Scanning...", font=app.fonts["small"], text_color=p.text_primary, anchor="w")
        summary_lbl.pack(side="left", fill="x", expand=True)

        open_btn = ctk.CTkButton(
            top_l,
            text="Open ->",
            width=66,
            height=22,
            fg_color=p.bg_card,
            font=app.fonts["caption"],
            command=lambda tgt=target_nav: app.switch_nav(tgt),
        )
        open_btn.pack(side="right", padx=(4, 0))
        exp_btn = ctk.CTkButton(
            top_l,
            text="▸ Expand",
            width=74,
            height=22,
            fg_color=p.bg_card,
            font=app.fonts["caption"],
            command=lambda k=key: toggle_dash_diag_item(app, k),
        )
        exp_btn.pack(side="right", padx=2)
        _bind_click_recursive(badge, lambda tgt=target_nav: app.switch_nav(tgt))
        _bind_click_recursive(summary_lbl, lambda tgt=target_nav: app.switch_nav(tgt))

        detail_wrap = ctk.CTkFrame(row_box, fg_color=p.bg_card, corner_radius=6)
        detail_lbl = ctk.CTkLabel(
            detail_wrap,
            text="",
            font=app.fonts["mono_small"],
            text_color=p.text_secondary,
            justify="left",
            anchor="w",
        )
        detail_lbl.pack(fill="x", padx=10, pady=6)
        app._dash_diag_rows[key] = {
            "summary": summary_lbl,
            "detail_wrap": detail_wrap,
            "detail_lbl": detail_lbl,
            "exp_btn": exp_btn,
            "badge": badge,
        }
    ctk.CTkFrame(dcard, height=6, fg_color="transparent").pack()

    # Row 6: Clickable System Options Snapshot Grid (Direct Menu Links!)
    scard = ctk.CTkFrame(page, fg_color=p.bg_card, border_width=1, border_color=p.accent_cyan, corner_radius=12)
    scard.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(2, 8))
    app.dash_conflicts_frame = scard

    shdr = ctk.CTkFrame(scard, fg_color="transparent")
    shdr.pack(fill="x", padx=14, pady=(10, 4))
    ctk.CTkLabel(
        shdr,
        text="System Snapshot & Quick Menu Links (Click Any Tile to Jump Directly)",
        font=app.fonts["body_bold"],
        text_color=p.accent_cyan,
    ).pack(side="left")

    sgrid = ctk.CTkFrame(scard, fg_color="transparent")
    sgrid.pack(fill="x", padx=12, pady=(2, 10))
    sgrid.grid_columnconfigure((0, 1, 2), weight=1, uniform="snap_tile")

    tiles = [
        ("playbook", "Fix Problems", "1-click safe fixes"),
        ("wifi", "Wi-Fi & Failover", "Ping & auto-recovery"),
        ("remote_beam", "Phone & Remote", "Tailscale, iOS/Watch/BT"),
        ("hermes", "Hermes OS AI", "Local GPU & Free Cloud"),
        ("hunter", "Speed Up Apps", "Idle apps & AI hunter"),
        ("modules", "Clean & Protect", "AstraShield & Cleanup"),
        ("monitoring", "Live Monitoring", "Full sensors & history"),
        ("cmd_vault", "Command Vault", "Bash/Fish, Deps & Help"),
        ("safeguards", "Forensic & Heal", "14-Invariant Vault"),
    ]
    app._dash_snapshot_tiles = {}
    for idx, (nav_key, title, sub) in enumerate(tiles):
        r, c = divmod(idx, 3)
        tile = ctk.CTkFrame(sgrid, fg_color=p.bg_elevated, border_width=1, border_color=p.border_subtle, corner_radius=9)
        tile.grid(row=r, column=c, sticky="nsew", padx=3, pady=3)
        ctk.CTkLabel(tile, text=title, font=app.fonts["body_bold"], text_color=p.text_primary).pack(
            anchor="w", padx=10, pady=(6, 0)
        )
        sub_lbl = ctk.CTkLabel(tile, text=sub, font=app.fonts["caption"], text_color=p.accent_jade)
        sub_lbl.pack(anchor="w", padx=10, pady=(0, 6))
        app._dash_snapshot_tiles[nav_key] = sub_lbl
        _bind_click_recursive(tile, lambda k=nav_key: app.switch_nav(k))


def toggle_dash_thermals_expand(app: AstraCradleApp) -> None:
    app._dash_thermals_expanded = not bool(getattr(app, "_dash_thermals_expanded", False))
    if app._dash_thermals_expanded:
        app._dash_therm_detail_frame.pack(fill="x", padx=14, pady=(0, 10))
        app._dash_therm_exp_btn.configure(text="▾ Collapse Sensors")
    else:
        app._dash_therm_detail_frame.pack_forget()
        app._dash_therm_exp_btn.configure(text="▸ Expand All Sensors")


def toggle_dash_diag_item(app: AstraCradleApp, key: str) -> None:
    expanded: set[str] = getattr(app, "_dash_expanded_keys", set())
    row = getattr(app, "_dash_diag_rows", {}).get(key)
    if row is None:
        return
    if key in expanded:
        expanded.remove(key)
        row["detail_wrap"].pack_forget()
        row["exp_btn"].configure(text="▸ Expand")
    else:
        expanded.add(key)
        row["detail_wrap"].pack(fill="x", padx=10, pady=(0, 6))
        row["exp_btn"].configure(text="▾ Collapse")


def update_command_deck_snapshot_inplace(app: AstraCradleApp, snap, gpu=None) -> None:
    """Updates all Command Deck labels smoothly in-place with zero widget destruction or flashing."""
    if not hasattr(app, "_dash_therm_pills"):
        return
    p = app.palette
    sensors = app._latest_sensors or {}
    temps = sensors.get("temperatures") or []
    fans = sensors.get("fans") or []
    freq = sensors.get("cpu_freq") or {}
    gpu_live = sensors.get("gpu_live") or {}

    cpu_t = app._num_or_none(sensors.get("cpu_temp_c")) or app._num_or_none(snap.temperature_c)
    gpu_t = app._num_or_none(sensors.get("gpu_temp_c")) or app._num_or_none(gpu_live.get("temp_c"))
    nvme_t = app._num_or_none(sensors.get("nvme_temp_c"))
    pwr_w = app._num_or_none(gpu_live.get("power_draw_w"))
    fan_pct = app._num_or_none(gpu_live.get("fan_percent"))
    fan_rpm = f"{fans[0].get('rpm')} RPM" if fans else (f"{fan_pct:.0f}%" if fan_pct is not None else "Auto")

    _set_lbl_if_changed(
        app._dash_therm_pills["cpu"],
        f"{cpu_t:.1f}°C" if cpu_t is not None else "N/A",
        p.accent_coral if (cpu_t and cpu_t >= 85) else p.accent_jade,
    )
    _set_lbl_if_changed(
        app._dash_therm_pills["gpu"],
        (f"{gpu_t:.1f}°C" if gpu_t is not None else "N/A") + (f" · {pwr_w:.0f}W" if pwr_w is not None else ""),
        p.accent_coral if (gpu_t and gpu_t >= 85) else p.accent_cyan,
    )
    _set_lbl_if_changed(
        app._dash_therm_pills["nvme"],
        f"{nvme_t:.1f}°C" if nvme_t is not None else "N/A",
        p.accent_brass if (nvme_t and nvme_t >= 70) else p.accent_jade,
    )
    _set_lbl_if_changed(
        app._dash_therm_pills["fan"],
        f"{fan_rpm} · {int(freq.get('current_mhz') or snap.cpu_freq_mhz or 0)} MHz",
        p.text_primary,
    )

    detail_lines: list[str] = []
    for t in temps[:10]:
        detail_lines.append(
            f"[{str(t.get('category', 'board')).upper()}] {t.get('chip')}:{t.get('label')} = {t.get('current_c')}°C ({str(t.get('status', 'ok')).upper()})"
        )
    for f in fans[:4]:
        detail_lines.append(f"[FAN] {f.get('chip')}:{f.get('label')} = {f.get('rpm')} RPM")
    if not detail_lines:
        detail_lines.append(
            f"CPU Sensor: {f'{cpu_t:.1f}°C' if cpu_t is not None else 'N/A'} | GPU: {gpu_live.get('name', 'Integrated')} | Power: {pwr_w or 'Auto'} W"
        )
    _set_lbl_if_changed(app._dash_therm_detail_lbl, "\n".join(detail_lines))

    # Update compact diagnostics rows in-place
    rows = getattr(app, "_dash_diag_rows", {})
    if "compute" in rows:
        _set_lbl_if_changed(
            rows["compute"]["summary"],
            f"CPU {snap.cpu_percent:.1f}% · {int(freq.get('current_mhz') or snap.cpu_freq_mhz or 0)} MHz · Temp {f'{cpu_t:.1f}°C' if cpu_t is not None else 'N/A'}",
        )
        _set_lbl_if_changed(
            rows["compute"]["detail_lbl"],
            f"Load Avg: {snap.load_avg or 'N/A'} | Processes: {snap.process_count} | GPU: {gpu_live.get('name', 'Integrated')} ({f'{gpu_t:.1f}°C' if gpu_t is not None else 'N/A'})",
        )

    if "memory_ai" in rows:
        rep = app.latest_report
        ai_cnt = len([r for r in (rep.recommendations if rep else []) if r.category == "ai_chatbot"])
        _set_lbl_if_changed(
            rows["memory_ai"]["summary"],
            f"RAM {snap.mem_percent:.1f}% ({format_bytes(snap.mem_used_bytes)}/{format_bytes(snap.mem_total_bytes)}) · {ai_cnt} idle AI helper(s)",
        )
        _set_lbl_if_changed(
            rows["memory_ai"]["detail_lbl"],
            f"Swap: {snap.swap_percent:.0f}% | Active Recommendations: {len(rep.recommendations) if rep else 0} | Click 'Open ->' for PID web lookup & AI Hunter.",
        )

    if "storage" in rows and snap.partitions:
        rp = snap.partitions[0]
        _set_lbl_if_changed(
            rows["storage"]["summary"],
            f"{rp.mountpoint}: {rp.percent:.1f}% used ({format_bytes(rp.free_bytes)} free)"
            + (f" · NVMe {nvme_t:.0f}°C" if nvme_t is not None else ""),
        )
        _set_lbl_if_changed(
            rows["storage"]["detail_lbl"],
            " | ".join(f"{pt.mountpoint} ({pt.fstype}): {pt.percent:.1f}% used" for pt in snap.partitions[:4]),
        )

    # Update Snapshot quick tiles smoothly
    tiles = getattr(app, "_dash_snapshot_tiles", {})
    fo_snap = app.network_failover.current_snapshot(app.config_obj) if ("network_fo" in rows or "wifi" in tiles) else None
    if "network_fo" in rows and fo_snap is not None:
        dns_txt = f"{snap.dns_latency_ms:.0f} ms" if snap.dns_latency_ms is not None else "Online"
        _set_lbl_if_changed(
            rows["network_fo"]["summary"],
            f"Failover: {fo_snap.state.upper()} ({fo_snap.active_iface}) · DNS {dns_txt}",
        )
        _set_lbl_if_changed(
            rows["network_fo"]["detail_lbl"],
            f"Primary: {fo_snap.primary_iface} -> Backup: {fo_snap.backup_iface} | Ping every {app.config_obj.failover_ping_interval_sec:.0f}s | Recovery: {app.config_obj.failover_recovery_stable_sec:.0f}s",
        )

    if "security" in rows:
        sh = app.adblock_shield.status()
        periphs = app._latest_peripherals or []
        _set_lbl_if_changed(
            rows["security"]["summary"],
            f"AstraShield: {sh.blocked_domain_count:,} blocked domains · BadUSB: {'ON' if app.config_obj.badusb_guard_enabled else 'OFF'} ({len(periphs)} USB/periph)",
        )
        _set_lbl_if_changed(
            rows["security"]["detail_lbl"],
            f"DNS Sinkhole: {'RUNNING' if sh.dns_server_running else 'STANDBY'} | Blocked Queries: {sh.blocked_queries} | Attached Peripherals: {len(periphs)}",
        )

    if "self_heal" in rows:
        ev = app.latest_self_heal or []
        rep_cnt = sum(1 for e in ev if e.repaired_automatically)
        _set_lbl_if_changed(
            rows["self_heal"]["summary"],
            f"{len(ev)}/14 invariants checked · {rep_cnt} auto-repaired · Crash Shield Active",
        )
        _set_lbl_if_changed(
            rows["self_heal"]["detail_lbl"],
            " | ".join(f"{e.check_name}: {e.status.upper()}" for e in ev[:5]) or "All checks verified.",
        )

    if "wifi" in tiles and fo_snap is not None:
        _set_lbl_if_changed(tiles["wifi"], f"{fo_snap.active_iface} ({fo_snap.state})")
    if "remote_beam" in tiles:
        srv = getattr(app, "_astrabeam_server", None)
        _set_lbl_if_changed(tiles["remote_beam"], "Host TLS LIVE" if (srv and srv.running) else "Tailscale / BT Ready")
    if "cmd_vault" in tiles:
        cmd_cnt = getattr(app, "_cached_cmd_count", None)
        if cmd_cnt is None:
            try:
                cmd_cnt = len(app.command_db.list_commands())
            except Exception:
                cmd_cnt = 0
            app._cached_cmd_count = cmd_cnt  # type: ignore[attr-defined]
        _set_lbl_if_changed(tiles["cmd_vault"], f"{cmd_cnt} scripts · Fish/Bash")
    if "hunter" in tiles:
        _set_lbl_if_changed(tiles["hunter"], f"{snap.process_count} PIDs + Web Lookup")
    if "monitoring" in tiles:
        _set_lbl_if_changed(tiles["monitoring"], f"CPU {snap.cpu_percent:.0f}% · RAM {snap.mem_percent:.0f}%")


def update_snapshot_findings_inplace(app: AstraCradleApp, report, pb) -> None:
    """Updates headline, gauge & footer safely in-place without destroying rows."""
    subtitle = "Optimal" if pb.health_score >= 88 else ("Balanced" if pb.health_score >= 70 else "Needs Care")
    app.gauge.set_score(pb.health_score, subtitle)
    _set_lbl_if_changed(app.dash_headline, pb.summary_headline)
    _set_lbl_if_changed(app.dash_summary, pb.executive_summary)
    from ...core.telemetry import format_uptime

    snap = report.snapshot
    _set_lbl_if_changed(
        app.dash_meta,
        f"Host: {snap.hostname} | Uptime: {format_uptime(snap.uptime_seconds)} | OS Adapter: {app._active_adapter().os_family} | Profile: {app.config_obj.active_profile}",
    )
    repaired_cnt = sum(1 for e in app.latest_self_heal if e.repaired_automatically)
    _set_lbl_if_changed(app.footer_heal_lbl, f"Self-Heal: {len(app.latest_self_heal)}/14 OK ({repaired_cnt} repaired)")
