"""Bluetooth & Peripherals Page — Unified Bluetooth Battery Monitor (<20% Alert), USB/BadUSB Gate, Audio, Displays & Input Devices."""

from __future__ import annotations

import shutil
import subprocess
import tkinter as tk
from pathlib import Path
from typing import TYPE_CHECKING, Any

import customtkinter as ctk

from ...core.lan_tv_shield import LanTvAdShield

if TYPE_CHECKING:
    from ..app import AstraCradleApp


def _collect_audio_and_display_summary() -> tuple[list[str], list[str], list[str]]:
    """Reads live audio sinks/sources, active displays, and USB autosuspend nodes safely."""
    audio_lines: list[str] = []
    for cmd in (["wpctl", "status"], ["pactl", "list", "short", "sinks"]):
        if shutil.which(cmd[0]):
            try:
                pr = subprocess.run(cmd, capture_output=True, text=True, timeout=4)
                for ln in (pr.stdout or "").splitlines():
                    st = ln.strip()
                    if st and not st.startswith("├─") and len(audio_lines) < 10:
                        audio_lines.append(st)
                    elif st and len(audio_lines) < 10:
                        audio_lines.append(st)
                if audio_lines:
                    break
            except Exception:
                pass

    display_lines: list[str] = []
    for cmd in (["kscreen-doctor", "-o"], ["wlr-randr"], ["xrandr", "--query"]):
        if shutil.which(cmd[0]):
            try:
                pr = subprocess.run(cmd, capture_output=True, text=True, timeout=4)
                for ln in (pr.stdout or "").splitlines():
                    st = ln.strip()
                    if st and ("connected" in st.lower() or "output" in st.lower() or "mode" in st.lower() or "current" in st.lower()):
                        display_lines.append(st)
                    if len(display_lines) >= 10:
                        break
                if display_lines:
                    break
            except Exception:
                pass

    usb_auto: list[str] = []
    try:
        for ctl in sorted(Path("/sys/bus/usb/devices").glob("*/power/control"))[:20]:
            if ctl.read_text(encoding="utf-8", errors="ignore").strip() == "auto":
                usb_auto.append(ctl.parent.parent.name)
    except Exception:
        pass
    return audio_lines, display_lines, usb_auto


def render_peripherals_page(app: AstraCradleApp) -> None:
    parent = getattr(app, "peripherals_scroll", None)
    if parent is None:
        return
    try:
        if not parent.winfo_exists():
            return
    except Exception:
        return
    for child in list(parent.winfo_children()):
        try:
            child.destroy()
        except Exception:
            pass

    p = app.palette
    f = app.fonts
    cfg = app.config_obj
    wrap_w = app._content_wrap(120)

    # ── Top Header Card ───────────────────────────────────────────────────
    hdr_card = ctk.CTkFrame(
        parent, fg_color=p.bg_card, border_width=1, border_color=p.accent_cyan, corner_radius=12
    )
    hdr_card.pack(fill="x", pady=(0, 8))
    hdr_row = ctk.CTkFrame(hdr_card, fg_color="transparent")
    hdr_row.pack(fill="x", padx=14, pady=(10, 4))
    ctk.CTkLabel(
        hdr_row,
        text="Bluetooth & Peripherals — All Connected Devices",
        font=f["section"],
        text_color=p.accent_cyan,
    ).pack(side="left")

    def _refresh_all() -> None:
        try:
            app._latest_bt_devices = LanTvAdShield.scan_bluetooth_devices_and_batteries()
        except Exception:
            pass
        try:
            app._latest_peripherals = app.usb_guard.scan_all_peripherals()
        except Exception:
            pass
        render_peripherals_page(app)
        app.show_toast("Refreshed Bluetooth devices, battery levels, and all PC peripherals.")

    ctk.CTkButton(
        hdr_row,
        text="↻ Scan All Devices & Batteries",
        width=210,
        height=30,
        fg_color=p.accent_cyan,
        text_color="#071216",
        font=f["small_bold"],
        command=_refresh_all,
    ).pack(side="right", padx=(6, 0))

    ctk.CTkButton(
        hdr_row,
        text="Ask Stephen AI",
        width=130,
        height=30,
        fg_color=p.bg_elevated,
        hover_color=p.accent_cyan,
        text_color=p.text_primary,
        font=f["small_bold"],
        command=lambda: app.switch_nav("hermes"),
    ).pack(side="right", padx=(6, 0))

    ctk.CTkLabel(
        hdr_card,
        text=(
            "Unified hub for all Bluetooth devices (with live battery % & <20% low-battery warnings), "
            "USB/HID/BadUSB protection, keyboards, mice, audio outputs/mics, displays, and controllers."
        ),
        font=f["small"],
        text_color=p.text_secondary,
        wraplength=wrap_w,
        justify="left",
    ).pack(anchor="w", padx=14, pady=(0, 10))

    # ── 1. Bluetooth Devices & Battery Tracker (<20% Alert) ───────────────
    bt_devs: list[Any] = list(getattr(app, "_latest_bt_devices", None) or [])
    if not bt_devs:
        try:
            bt_devs = LanTvAdShield.scan_bluetooth_devices_and_batteries()
            app._latest_bt_devices = bt_devs
        except Exception:
            bt_devs = []

    bt_thresh = int(getattr(cfg, "bluetooth_low_battery_threshold", 20) or 20)
    low_devs = [d for d in bt_devs if getattr(d, "battery_pct", None) is not None and int(d.battery_pct) < bt_thresh]

    bt_card = ctk.CTkFrame(
        parent,
        fg_color=p.bg_card,
        border_width=1,
        border_color=(p.accent_coral if low_devs else p.border_subtle),
        corner_radius=12,
    )
    bt_card.pack(fill="x", pady=(0, 8))
    bt_hdr = ctk.CTkFrame(bt_card, fg_color="transparent")
    bt_hdr.pack(fill="x", padx=14, pady=(10, 4))
    ctk.CTkLabel(
        bt_hdr,
        text=f"Bluetooth Devices & Battery Monitor ({len(bt_devs)} found · Alert < {bt_thresh}%)",
        font=f["body_bold"],
        text_color=(p.accent_coral if low_devs else p.accent_jade),
    ).pack(side="left")

    bt_mon_var = tk.BooleanVar(value=bool(getattr(cfg, "bluetooth_battery_monitor_enabled", True)))

    def _toggle_bt_alert() -> None:
        val = bool(bt_mon_var.get())
        cfg.bluetooth_battery_monitor_enabled = val
        if hasattr(app, "set_bt_battery_var"):
            try:
                app.set_bt_battery_var.set(val)
            except Exception:
                pass
        app.cfg_mgr.save(cfg)
        app.show_toast(f"Bluetooth <{bt_thresh}% battery alerts {'ENABLED' if val else 'PAUSED'}.")

    ctk.CTkSwitch(
        bt_hdr,
        text=f"<{bt_thresh}% Low-Battery Alerts",
        variable=bt_mon_var,
        font=f["caption"],
        progress_color=p.accent_jade,
        command=_toggle_bt_alert,
    ).pack(side="right")

    if not bt_devs:
        ctk.CTkLabel(
            bt_card,
            text="No paired or connected Bluetooth peripherals detected right now. Pair a headset, controller, mouse, or keyboard to track battery % automatically.",
            font=f["small"],
            text_color=p.text_secondary,
            wraplength=wrap_w,
            justify="left",
        ).pack(anchor="w", padx=14, pady=(2, 10))
    else:
        for d in bt_devs:
            row = ctk.CTkFrame(bt_card, fg_color=p.bg_elevated, corner_radius=8)
            row.pack(fill="x", padx=14, pady=(0, 4))
            batt = getattr(d, "battery_pct", None)
            is_low = batt is not None and int(batt) < bt_thresh
            batt_txt = f"{batt}%{' LOW!' if is_low else ''}" if batt is not None else "Battery N/A"
            conn_txt = "Connected" if getattr(d, "connected", False) else "Paired"
            kind_txt = str(getattr(d, "device_kind", "") or "bluetooth")
            ctk.CTkLabel(
                row,
                text=f"[{conn_txt} · {kind_txt}] {d.name} ({d.mac})",
                font=f["small_bold"],
                text_color=(p.accent_coral if is_low else p.text_primary),
            ).pack(side="left", padx=10, pady=6)
            ctk.CTkLabel(
                row,
                text=batt_txt,
                font=f["mono_small"],
                text_color=(p.accent_coral if is_low else (p.accent_jade if batt is not None else p.text_muted)),
            ).pack(side="right", padx=10, pady=6)
        ctk.CTkFrame(bt_card, height=4, fg_color="transparent").pack()

    # ── 2. All USB, HID, PCIe & Input Peripherals (BadUSB Guard) ──────────
    periphs = list(getattr(app, "_latest_peripherals", None) or [])
    if not periphs:
        try:
            periphs = app.usb_guard.scan_all_peripherals()
            app._latest_peripherals = periphs
        except Exception:
            periphs = []

    usb_card = ctk.CTkFrame(
        parent, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
    )
    usb_card.pack(fill="x", pady=(0, 8))
    uhdr = ctk.CTkFrame(usb_card, fg_color="transparent")
    uhdr.pack(fill="x", padx=14, pady=(10, 4))
    ctk.CTkLabel(
        uhdr,
        text=f"All Hardware, USB & Input Peripherals ({len(periphs)} detected)",
        font=f["body_bold"],
        text_color=p.accent_cyan,
    ).pack(side="left")

    strict_var = tk.BooleanVar(value=bool(getattr(cfg, "usb_strict_kernel_gate", False)))
    ctk.CTkSwitch(
        uhdr,
        text="Strict USB Kernel Gate",
        variable=strict_var,
        font=f["caption"],
        progress_color=p.accent_jade,
        command=lambda: (app._on_toggle_usb_strict_gate(bool(strict_var.get())), render_peripherals_page(app)),
    ).pack(side="right")

    if not periphs:
        ctk.CTkLabel(
            usb_card,
            text="No USB/HID hardware peripherals reported in this environment.",
            font=f["small"],
            text_color=p.text_secondary,
        ).pack(anchor="w", padx=14, pady=(2, 10))
    else:
        for dev in periphs[:30]:
            drow = ctk.CTkFrame(usb_card, fg_color=p.bg_elevated, corner_radius=8)
            drow.pack(fill="x", padx=14, pady=(0, 4))
            ctk.CTkLabel(
                drow,
                text=f"[{dev.bus.upper()} · {dev.status.upper()}] {dev.vendor} {dev.name} — {dev.plain_description}",
                font=f["mono_small"],
                text_color=(p.accent_coral if dev.status == "blocked" else p.text_secondary),
                wraplength=wrap_w - 130,
                justify="left",
            ).pack(side="left", padx=10, pady=5)
            if dev.bus == "usb" and dev.status == "blocked":
                ctk.CTkButton(
                    drow,
                    text="Trust & Allow",
                    width=102,
                    height=24,
                    fg_color=p.accent_jade,
                    text_color="#071510",
                    font=f["caption"],
                    command=lambda d=dev: (app._on_trust_usb(d), render_peripherals_page(app)),
                ).pack(side="right", padx=8, pady=4)
        ctk.CTkFrame(usb_card, height=4, fg_color="transparent").pack()

    # ── 3. Audio Sinks, Connected Displays & USB Power Control ────────────
    audio_lines, display_lines, usb_auto = _collect_audio_and_display_summary()
    av_card = ctk.CTkFrame(
        parent, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
    )
    av_card.pack(fill="x", pady=(0, 8))
    ctk.CTkLabel(
        av_card,
        text="Audio Outputs / Microphones, Displays & USB Power",
        font=f["body_bold"],
        text_color=p.text_primary,
    ).pack(anchor="w", padx=14, pady=(10, 4))
    av_text = (
        f"Audio Devices:\n  " + ("\n  ".join(audio_lines[:6]) if audio_lines else "None detected / headless")
        + "\n\nConnected Displays:\n  " + ("\n  ".join(display_lines[:6]) if display_lines else "None detected / headless")
        + f"\n\nUSB Autosuspend Nodes ('auto'): {', '.join(usb_auto[:10]) if usb_auto else 'None'}"
    )
    ctk.CTkLabel(
        av_card,
        text=av_text,
        font=f["mono_small"],
        text_color=p.text_secondary,
        wraplength=wrap_w,
        justify="left",
    ).pack(anchor="w", padx=14, pady=(0, 10))
