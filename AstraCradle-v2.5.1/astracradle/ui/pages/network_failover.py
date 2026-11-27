"""Network Failover & Seamless Recovery UI Section (Ethernet <-> Wi-Fi / Custom Pairs)."""

from __future__ import annotations

import tkinter as tk
from typing import TYPE_CHECKING

import customtkinter as ctk

if TYPE_CHECKING:
    from ..app import AstraCradleApp


def render_failover_section(app: AstraCradleApp, parent: ctk.CTkScrollableFrame) -> None:
    p = app.palette
    cfg = app.config_obj
    eng = app.network_failover
    snap = eng.current_snapshot(cfg)
    ifaces = eng.discover_interfaces()
    iface_names = ["auto"] + [i.name for i in ifaces]

    card = ctk.CTkFrame(parent, fg_color=p.bg_card, border_width=1, border_color=p.accent_cyan, corner_radius=12)
    card.pack(fill="x", pady=(8, 8))

    top_r = ctk.CTkFrame(card, fg_color="transparent")
    top_r.pack(fill="x", padx=14, pady=(10, 4))
    ctk.CTkLabel(
        top_r,
        text="Network Failover & Seamless Auto-Recovery (Ethernet <-> Wi-Fi / Custom)",
        font=app.fonts["section"],
        text_color=p.accent_cyan,
    ).pack(side="left")

    state_color = (
        p.accent_jade
        if snap.state == "primary_active"
        else (p.accent_brass if snap.state in ("degraded_probing", "primary_stabilizing") else p.accent_coral)
    )
    ctk.CTkLabel(
        top_r,
        text=f"STATE: {snap.state.upper()}  ·  Active: {snap.active_iface}",
        font=app.fonts["mono_small"],
        text_color=state_color,
    ).pack(side="right")

    status_txt = (
        f"Primary: {snap.primary_iface}  |  Backup: {snap.backup_iface}  |  "
        f"Last Probe: {snap.last_probe_reason}  |  Failures: {snap.consecutive_failures}/{snap.failure_threshold}"
    )
    if snap.state in ("failover_active", "primary_stabilizing"):
        status_txt += (
            f"\nFailover Hold: {snap.failover_elapsed_sec:.0f}s / {snap.min_hold_sec:.0f}s  |  "
            f"Primary Stable Recovery: {snap.stable_elapsed_sec:.0f}s / {snap.recovery_stable_sec:.0f}s"
        )
    if snap.last_transition_note:
        status_txt += f"\nLast Transition ({snap.last_transition_at}): {snap.last_transition_note}"

    ctk.CTkLabel(
        card,
        text=status_txt,
        font=app.fonts["small"],
        text_color=p.text_secondary,
        justify="left",
        wraplength=app._content_wrap(90),
    ).pack(anchor="w", padx=14, pady=(0, 6))

    # Row 1: Toggles & Interface Selection
    en_var = tk.BooleanVar(value=bool(getattr(cfg, "failover_enabled", True)))
    seam_var = tk.BooleanVar(value=bool(getattr(cfg, "failover_seamless_switch", True)))
    prim_var = tk.StringVar(value=str(getattr(cfg, "failover_primary_interface", "auto")))
    back_var = tk.StringVar(value=str(getattr(cfg, "failover_backup_interface", "auto")))
    if prim_var.get() not in iface_names:
        iface_names.append(prim_var.get())
    if back_var.get() not in iface_names:
        iface_names.append(back_var.get())

    r1 = ctk.CTkFrame(card, fg_color="transparent")
    r1.pack(fill="x", padx=14, pady=3)
    ctk.CTkSwitch(r1, text="Enable Auto-Failover", variable=en_var).pack(side="left", padx=(0, 12))
    ctk.CTkSwitch(r1, text="Seamless Metric Switch", variable=seam_var).pack(side="left", padx=(0, 14))
    ctk.CTkLabel(r1, text="Primary:", font=app.fonts["small"], text_color=p.text_muted).pack(side="left", padx=(0, 4))
    ctk.CTkOptionMenu(r1, values=iface_names, variable=prim_var, width=125).pack(side="left", padx=(0, 10))
    ctk.CTkLabel(r1, text="Backup:", font=app.fonts["small"], text_color=p.text_muted).pack(side="left", padx=(0, 4))
    ctk.CTkOptionMenu(r1, values=iface_names, variable=back_var, width=125).pack(side="left")

    # Row 2: Individual Numeric & Target Settings
    targets_var = tk.StringVar(value=", ".join(getattr(cfg, "failover_ping_targets", ["1.1.1.1", "8.8.8.8", "9.9.9.9"])))
    intv_var = tk.StringVar(value=str(getattr(cfg, "failover_ping_interval_sec", 5.0)))
    to_var = tk.StringVar(value=str(getattr(cfg, "failover_ping_timeout_sec", 2.0)))
    lat_var = tk.StringVar(value=str(getattr(cfg, "failover_max_latency_ms", 250.0)))
    fth_var = tk.StringVar(value=str(getattr(cfg, "failover_failure_threshold", 3)))
    hold_var = tk.StringVar(value=str(getattr(cfg, "failover_min_hold_sec", 60.0)))
    rec_var = tk.StringVar(value=str(getattr(cfg, "failover_recovery_stable_sec", 300.0)))

    r2 = ctk.CTkFrame(card, fg_color="transparent")
    r2.pack(fill="x", padx=14, pady=3)
    ctk.CTkLabel(r2, text="Ping Targets:", font=app.fonts["small"], text_color=p.text_muted).pack(side="left", padx=(0, 4))
    ctk.CTkEntry(r2, textvariable=targets_var, width=190).pack(side="left", padx=(0, 10))
    for lbl, var, w in [
        ("Ping Interval (s):", intv_var, 52),
        ("Timeout (s):", to_var, 48),
        ("Max RTT (ms):", lat_var, 58),
    ]:
        ctk.CTkLabel(r2, text=lbl, font=app.fonts["small"], text_color=p.text_muted).pack(side="left", padx=(0, 4))
        ctk.CTkEntry(r2, textvariable=var, width=w).pack(side="left", padx=(0, 8))

    r3 = ctk.CTkFrame(card, fg_color="transparent")
    r3.pack(fill="x", padx=14, pady=3)
    for lbl, var, w in [
        ("Fail After (pings):", fth_var, 46),
        ("Min Hold Failover (s):", hold_var, 56),
        ("Primary Stable Recovery (s):", rec_var, 62),
    ]:
        ctk.CTkLabel(r3, text=lbl, font=app.fonts["small"], text_color=p.text_muted).pack(side="left", padx=(0, 4))
        ctk.CTkEntry(r3, textvariable=var, width=w).pack(side="left", padx=(0, 10))

    # Row 4: Action Buttons
    r4 = ctk.CTkFrame(card, fg_color="transparent")
    r4.pack(fill="x", padx=14, pady=(4, 8))
    ctk.CTkButton(
        r4,
        text="Save Failover Settings",
        height=28,
        fg_color=p.accent_jade,
        text_color="#071510",
        font=app.fonts["body_bold"],
        command=lambda: app._on_save_failover_settings(
            en_var.get(),
            seam_var.get(),
            prim_var.get(),
            back_var.get(),
            targets_var.get(),
            intv_var.get(),
            to_var.get(),
            lat_var.get(),
            fth_var.get(),
            hold_var.get(),
            rec_var.get(),
        ),
    ).pack(side="left", padx=(0, 6))
    ctk.CTkButton(
        r4,
        text="Probe Ping Now",
        height=28,
        fg_color=p.accent_cyan,
        text_color="#071216",
        font=app.fonts["body_bold"],
        command=app._on_probe_failover_now,
    ).pack(side="left", padx=4)
    ctk.CTkButton(
        r4,
        text="Test / Switch Failover",
        height=28,
        fg_color=p.bg_elevated,
        command=app._on_force_failover_test,
    ).pack(side="left", padx=4)
    ctk.CTkButton(
        r4,
        text="Recover Primary Now",
        height=28,
        fg_color=p.bg_elevated,
        command=app._on_force_recover_primary,
    ).pack(side="left", padx=4)

    # Row 5: Additional Custom Failover Pair (labeled cleanly)
    cf_intv = tk.StringVar(value="5")
    cf_rec = tk.StringVar(value="300")
    r5 = ctk.CTkFrame(card, fg_color="transparent")
    r5.pack(fill="x", padx=14, pady=(4, 6))
    cf_name_ent = ctk.CTkEntry(r5, width=190, placeholder_text="Custom Pair Name (e.g. Home Eth->WiFi)")
    cf_name_ent.pack(side="left", padx=(0, 8))
    ctk.CTkLabel(r5, text="Primary:", font=app.fonts["caption"], text_color=p.text_muted).pack(side="left", padx=(0, 3))
    cf_prim_ent = ctk.CTkEntry(r5, width=88, placeholder_text="Primary NIC")
    cf_prim_ent.insert(0, snap.primary_iface)
    cf_prim_ent.pack(side="left", padx=(0, 8))
    ctk.CTkLabel(r5, text="Backup:", font=app.fonts["caption"], text_color=p.text_muted).pack(side="left", padx=(0, 3))
    cf_back_ent = ctk.CTkEntry(r5, width=88, placeholder_text="Backup NIC")
    cf_back_ent.insert(0, snap.backup_iface)
    cf_back_ent.pack(side="left", padx=(0, 8))
    ctk.CTkEntry(r5, textvariable=cf_intv, width=48).pack(side="left", padx=(0, 4))
    ctk.CTkLabel(r5, text="s ping", font=app.fonts["caption"], text_color=p.text_muted).pack(side="left", padx=(0, 6))
    ctk.CTkEntry(r5, textvariable=cf_rec, width=54).pack(side="left", padx=(0, 4))
    ctk.CTkLabel(r5, text="s rec", font=app.fonts["caption"], text_color=p.text_muted).pack(side="left", padx=(0, 8))
    ctk.CTkButton(
        r5,
        text="Add Custom Failover Pair",
        height=26,
        fg_color=p.bg_elevated,
        command=lambda: app._on_add_custom_failover_pair(
            cf_name_ent.get(), cf_prim_ent.get(), cf_back_ent.get(), cf_intv.get(), cf_rec.get()
        ),
    ).pack(side="left")

    for cp in getattr(cfg, "failover_custom_profiles", []) or []:
        crow = ctk.CTkFrame(card, fg_color=p.bg_elevated, corner_radius=7)
        crow.pack(fill="x", padx=14, pady=2)
        ctk.CTkLabel(
            crow,
            text=(
                f"{cp.get('name')}  ·  {cp.get('primary')} -> {cp.get('backup')}  ·  "
                f"Ping {cp.get('ping_interval_sec')}s  ·  Recovery {cp.get('recovery_stable_sec')}s"
            ),
            font=app.fonts["mono_small"],
            text_color=p.text_primary,
        ).pack(side="left", padx=10, pady=4)
        ctk.CTkButton(
            crow,
            text="Remove",
            width=66,
            height=22,
            fg_color=p.bg_card,
            hover_color=p.accent_coral,
            command=lambda n=str(cp.get("name")): app._on_remove_custom_failover_pair(n),
        ).pack(side="right", padx=6, pady=3)
    ctk.CTkFrame(card, height=6, fg_color="transparent").pack()
