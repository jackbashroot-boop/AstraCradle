"""UI Section for PC Wi-Fi Mesh Array & Interactive Mesh Assistant."""

from __future__ import annotations

import tkinter as tk
from typing import TYPE_CHECKING

import customtkinter as ctk

if TYPE_CHECKING:
    from ..app import AstraCradleApp


def render_wifi_mesh_section(app: AstraCradleApp, parent: ctk.CTkScrollableFrame) -> None:
    """Renders the interactive PC Wi-Fi Mesh Assistant inside the Wi-Fi, Failover & Mesh page."""
    p = app.palette
    cfg = app.config_obj

    card = ctk.CTkFrame(
        parent, fg_color=p.bg_card, border_width=1, border_color=p.accent_cyan, corner_radius=12
    )
    card.pack(fill="x", pady=6)

    hdr = ctk.CTkFrame(card, fg_color="transparent")
    hdr.pack(fill="x", padx=16, pady=(12, 4))
    ctk.CTkLabel(
        hdr,
        text="PC Wi-Fi Mesh Array & Interactive Mesh Assistant",
        font=app.fonts["section"],
        text_color=p.accent_cyan,
    ).pack(side="left")

    adapters = app.wifi_mesh.probe_adapters()
    adapter_summary = (
        " | ".join(
            f"{a.iface} (AP={'YES' if a.supports_ap else 'NO'}, AP+STA={'YES' if a.supports_ap_sta_concurrent else 'NO'}, 802.11s={'YES' if a.supports_mesh_point else 'NO'}"
            + (f", SSID='{a.current_ssid}'" if a.current_ssid else "")
            + ")"
            for a in adapters
        )
        if adapters
        else "No Wi-Fi adapters detected (connect USB/PCIe Wi-Fi NIC or use Ethernet -> Wi-Fi root)."
    )
    ctk.CTkLabel(
        card,
        text=f"Detected Radios: {adapter_summary}",
        font=app.fonts["mono_small"],
        text_color=p.accent_jade if adapters else p.accent_brass,
        wraplength=app._content_wrap(160),
        justify="left",
        anchor="w",
    ).pack(fill="x", padx=16, pady=(0, 8))

    form = ctk.CTkFrame(card, fg_color=p.bg_elevated, corner_radius=10)
    form.pack(fill="x", padx=16, pady=(0, 8))
    form.grid_columnconfigure((1, 3), weight=1)

    default_uplink = cfg.wifi_mesh_uplink or (adapters[0].current_ssid or adapters[0].iface if adapters else "eth0")
    app.mesh_uplink_var = tk.StringVar(value=default_uplink)
    app.mesh_ssid_var = tk.StringVar(value=cfg.wifi_mesh_ssid or "AstraMesh-Array")
    app.mesh_pass_var = tk.StringVar(value="")
    app.mesh_pass_hidden_var = tk.BooleanVar(value=True)
    app.mesh_band_var = tk.StringVar(value=cfg.wifi_mesh_band or "5 GHz")
    app.mesh_role_var = tk.StringVar(value=cfg.wifi_mesh_role or "Auto (Best Available)")

    ctk.CTkLabel(form, text="Source Wi-Fi / Uplink:", font=app.fonts["small"]).grid(row=0, column=0, padx=10, pady=(10, 4), sticky="w")
    ctk.CTkEntry(
        form, textvariable=app.mesh_uplink_var, placeholder_text="Wi-Fi SSID or interface (e.g. HomeWiFi, wlan0, eth0)"
    ).grid(row=0, column=1, padx=6, pady=(10, 4), sticky="ew")

    ctk.CTkLabel(form, text="Mesh Array SSID:", font=app.fonts["small"]).grid(row=0, column=2, padx=10, pady=(10, 4), sticky="w")
    ctk.CTkEntry(
        form, textvariable=app.mesh_ssid_var, placeholder_text="AstraMesh-Array"
    ).grid(row=0, column=3, padx=(6, 10), pady=(10, 4), sticky="ew")

    ctk.CTkLabel(form, text="Mesh Passphrase:", font=app.fonts["small"]).grid(row=1, column=0, padx=10, pady=4, sticky="w")
    pass_box = ctk.CTkFrame(form, fg_color="transparent")
    pass_box.grid(row=1, column=1, padx=6, pady=4, sticky="ew")
    app.mesh_pass_entry = ctk.CTkEntry(
        pass_box,
        textvariable=app.mesh_pass_var,
        show="•",
        placeholder_text="Leave blank to auto-generate WPA2/SAE key",
    )
    app.mesh_pass_entry.pack(side="left", fill="x", expand=True, padx=(0, 4))

    def _format_plan_text(plan) -> str:
        hidden = bool(app.mesh_pass_hidden_var.get())
        pw_disp = ("•••••••••••• (hidden — click Show)" if hidden else plan.passphrase)
        lines = [
            f"Mode: {plan.mode} | Ready: {'YES' if plan.ok else 'GUIDANCE'} | Uplink: {plan.uplink_ssid} ({plan.uplink_iface}) -> Mesh: '{plan.mesh_ssid}' on {plan.mesh_iface} (Ch {plan.recommended_channel}, {plan.band})",
            f"Passphrase: {pw_disp}",
        ]
        lines.extend(f"  • {n}" for n in plan.notes)
        if plan.fish_commands:
            lines.append("Fish Commands:\n  " + "\n  ".join(plan.fish_commands))
        return "\n".join(lines)

    def _toggle_pass_visibility() -> None:
        new_hidden = not bool(app.mesh_pass_hidden_var.get())
        app.mesh_pass_hidden_var.set(new_hidden)
        app.mesh_pass_entry.configure(show=("•" if new_hidden else ""))
        app.mesh_pass_toggle_btn.configure(text=("Show" if new_hidden else "Hide"))
        plan = getattr(app, "_latest_mesh_plan", None)
        if plan is not None and hasattr(app, "mesh_plan_lbl") and app.mesh_plan_lbl.winfo_exists():
            app.mesh_plan_lbl.configure(text=_format_plan_text(plan))

    def _copy_mesh_pass() -> None:
        plan = getattr(app, "_latest_mesh_plan", None)
        pw = app.mesh_pass_var.get().strip() or (plan.passphrase if plan else "")
        if not pw:
            plan = _build_plan_obj()
            pw = plan.passphrase
        app._copy_text(pw)
        app.show_toast("Copied Wi-Fi Mesh passphrase to clipboard!")

    app.mesh_pass_toggle_btn = ctk.CTkButton(
        pass_box,
        text="Show",
        width=56,
        height=28,
        fg_color=p.bg_card,
        hover_color=p.border_strong,
        font=app.fonts["caption"],
        command=_toggle_pass_visibility,
    )
    app.mesh_pass_toggle_btn.pack(side="left", padx=(0, 4))
    ctk.CTkButton(
        pass_box,
        text="Copy",
        width=52,
        height=28,
        fg_color=p.bg_card,
        hover_color=p.border_strong,
        font=app.fonts["caption"],
        command=_copy_mesh_pass,
    ).pack(side="left")

    ctk.CTkLabel(form, text="Band & Role:", font=app.fonts["small"]).grid(row=1, column=2, padx=10, pady=4, sticky="w")
    band_role_box = ctk.CTkFrame(form, fg_color="transparent")
    band_role_box.grid(row=1, column=3, padx=(6, 10), pady=4, sticky="ew")
    ctk.CTkOptionMenu(
        band_role_box, variable=app.mesh_band_var, values=["5 GHz", "2.4 GHz", "6 GHz"], width=95
    ).pack(side="left", padx=(0, 6))
    ctk.CTkOptionMenu(
        band_role_box,
        variable=app.mesh_role_var,
        values=[
            "Auto (Best Available)",
            "Dual-Radio AP Relay",
            "802.11s Mesh Point",
            "Ethernet -> Wi-Fi Mesh Root",
        ],
    ).pack(side="left", fill="x", expand=True)

    btn_row = ctk.CTkFrame(form, fg_color="transparent")
    btn_row.grid(row=2, column=0, columnspan=4, sticky="ew", padx=10, pady=(6, 10))

    app.mesh_plan_lbl = ctk.CTkLabel(
        card,
        text="Enter your source Wi-Fi SSID/uplink above and click 'Build Mesh Plan' to check hardware concurrency & generate your mesh setup.",
        font=app.fonts["mono_small"],
        text_color=p.text_secondary,
        wraplength=app._content_wrap(160),
        justify="left",
        anchor="w",
    )
    app.mesh_plan_lbl.pack(fill="x", padx=16, pady=(2, 12))

    def _build_plan_obj():
        cfg.wifi_mesh_uplink = app.mesh_uplink_var.get().strip()
        cfg.wifi_mesh_ssid = app.mesh_ssid_var.get().strip() or "AstraMesh-Array"
        cfg.wifi_mesh_band = app.mesh_band_var.get().strip() or "5 GHz"
        cfg.wifi_mesh_role = app.mesh_role_var.get().strip() or "Auto (Best Available)"
        app.cfg_mgr.save(cfg)
        plan = app.wifi_mesh.build_mesh_plan(
            uplink_input=cfg.wifi_mesh_uplink,
            mesh_ssid=cfg.wifi_mesh_ssid,
            passphrase=app.mesh_pass_var.get().strip(),
            band=cfg.wifi_mesh_band,
            role=cfg.wifi_mesh_role,
            wifi_scan_rec=getattr(app, "_latest_wifi_rec", None),
        )
        app._latest_mesh_plan = plan
        return plan

    def _on_build_plan() -> None:
        plan = _build_plan_obj()
        app.mesh_plan_lbl.configure(
            text=_format_plan_text(plan),
            text_color=p.accent_jade if plan.ok else p.accent_brass,
        )
        app.show_toast(f"Mesh plan built ({plan.mode}) on Channel {plan.recommended_channel}.")

    def _on_apply_mesh() -> None:
        plan = getattr(app, "_latest_mesh_plan", None) or _build_plan_obj()
        dry = bool(app.dry_run_var.get()) if hasattr(app, "dry_run_var") else bool(cfg.dry_run_default)
        ok, msg = app.wifi_mesh.apply_mesh_plan(plan, dry_run=dry)
        app.show_toast(msg, color=p.accent_jade if ok else p.accent_brass)

    def _on_copy_fish() -> None:
        plan = getattr(app, "_latest_mesh_plan", None) or _build_plan_obj()
        cmd_txt = "\n".join(plan.fish_commands or plan.bash_commands)
        app._copy_text(cmd_txt)
        app.show_toast("Copied Fish Mesh commands to clipboard!")

    def _on_stop_mesh() -> None:
        dry = bool(app.dry_run_var.get()) if hasattr(app, "dry_run_var") else bool(cfg.dry_run_default)
        _, msg = app.wifi_mesh.stop_mesh_node(dry_run=dry)
        app.show_toast(msg)

    ctk.CTkButton(
        btn_row, text="Build Mesh Plan", width=135, fg_color=p.accent_cyan, text_color="#071217", font=app.fonts["body_bold"], command=_on_build_plan
    ).pack(side="left", padx=(0, 6))
    ctk.CTkButton(
        btn_row, text="Activate Mesh Node", width=150, fg_color=p.accent_jade, text_color="#071510", font=app.fonts["body_bold"], command=_on_apply_mesh
    ).pack(side="left", padx=4)
    ctk.CTkButton(
        btn_row, text="Copy Fish Commands", width=155, fg_color=p.bg_card, font=app.fonts["small"], command=_on_copy_fish
    ).pack(side="left", padx=4)
    ctk.CTkButton(
        btn_row, text="Stop Mesh Node", width=125, fg_color=p.badge_crit_bg, text_color=p.badge_crit_fg, font=app.fonts["small"], command=_on_stop_mesh
    ).pack(side="right")
