"""Modular Custom Automation Rules Builder & Weekly Regression Benchmarks UI sections."""

from __future__ import annotations

import tkinter as tk
from typing import TYPE_CHECKING

import customtkinter as ctk

if TYPE_CHECKING:
    from ..app import AstraCradleApp


def render_custom_rules_section(app: AstraCradleApp, parent: ctk.CTkFrame) -> None:
    p = app.palette
    card = ctk.CTkFrame(parent, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12)
    card.pack(fill="x", pady=6)
    ctk.CTkLabel(
        card,
        text="5. Custom If-X-Then-Y Rule Builder",
        font=app.fonts["section"],
        text_color=p.accent_jade,
    ).pack(anchor="w", padx=14, pady=(12, 2))
    ctk.CTkLabel(
        card,
        text="Create custom triggers (e.g., if process 'blender' is running -> switch profile to '3D Render / Studio').",
        font=app.fonts["small"],
        text_color=p.text_muted,
        wraplength=app._content_wrap(80),
        justify="left",
    ).pack(anchor="w", padx=14, pady=(0, 8))

    form = ctk.CTkFrame(card, fg_color="transparent")
    form.pack(fill="x", padx=14, pady=4)
    cond_var = tk.StringVar(value="process_running")
    cval_var = tk.StringVar(value="blender")
    act_var = tk.StringVar(value="switch_profile")
    aval_var = tk.StringVar(value="Gaming / Low-Latency")

    ctk.CTkOptionMenu(form, values=["process_running", "cpu_above", "on_battery_and_temp"], variable=cond_var, width=165).pack(side="left", padx=(0, 6))
    ctk.CTkEntry(form, textvariable=cval_var, width=110).pack(side="left", padx=(0, 6))
    ctk.CTkOptionMenu(form, values=["switch_profile", "low_power_ui", "toast_alert"], variable=act_var, width=140).pack(side="left", padx=(0, 6))
    ctk.CTkEntry(form, textvariable=aval_var, width=165).pack(side="left", padx=(0, 6))
    ctk.CTkButton(
        form,
        text="+ Add Rule",
        width=92,
        height=28,
        fg_color=p.accent_jade,
        text_color="#071510",
        font=app.fonts["body_bold"],
        command=lambda: app._on_add_custom_rule(cond_var.get(), cval_var.get(), act_var.get(), aval_var.get()),
    ).pack(side="left")

    rules = getattr(app.config_obj, "custom_automation_rules", []) or []
    for r in rules[:10]:
        rrow = ctk.CTkFrame(card, fg_color=p.bg_elevated, corner_radius=7)
        rrow.pack(fill="x", padx=14, pady=2)
        st = "ON" if r.get("enabled", True) else "OFF"
        ctk.CTkLabel(
            rrow,
            text=f"[{st}] If {r.get('condition_type')} == '{r.get('condition_value')}' -> {r.get('action_type')} ({r.get('action_value')})",
            font=app.fonts["mono_small"],
            text_color=p.text_primary,
        ).pack(side="left", padx=10, pady=4)
        rid = str(r.get("rule_id") or "")
        ctk.CTkButton(
            rrow,
            text="Delete",
            width=64,
            height=22,
            fg_color=p.bg_card,
            hover_color=p.accent_coral,
            command=lambda x=rid: app._on_delete_custom_rule(x),
        ).pack(side="right", padx=6, pady=3)
    ctk.CTkFrame(card, height=8, fg_color="transparent").pack()


def render_benchmarks_section(app: AstraCradleApp, parent: ctk.CTkFrame) -> None:
    p = app.palette
    card = ctk.CTkFrame(parent, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12)
    card.pack(fill="x", pady=6)
    top = ctk.CTkFrame(card, fg_color="transparent")
    top.pack(fill="x", padx=14, pady=(12, 4))
    ctk.CTkLabel(
        top,
        text="6. Scheduled Benchmark & Regression Monitor",
        font=app.fonts["section"],
        text_color=p.accent_cyan,
    ).pack(side="left")
    ctk.CTkButton(
        top,
        text="Capture Benchmark Now",
        height=28,
        fg_color=p.bg_elevated,
        hover_color=p.border_strong,
        command=app._on_capture_benchmark,
    ).pack(side="right")

    regs = app.history_db.detect_regressions()
    benches = app.history_db.list_benchmarks(limit=5)
    summary = (
        "Regressions: " + " | ".join(regs)
        if regs
        else (f"Latest benchmark: Health {benches[0]['health_score']}/100 · RAM {benches[0]['idle_ram_mb']:.0f} MB · Bloat {benches[0]['bufferbloat_grade']}" if benches else "No benchmark baseline captured yet.")
    )
    ctk.CTkLabel(
        card,
        text=summary,
        font=app.fonts["mono_small"],
        text_color=p.accent_coral if regs else p.text_secondary,
        wraplength=app._content_wrap(80),
        justify="left",
    ).pack(anchor="w", padx=14, pady=(0, 12))
