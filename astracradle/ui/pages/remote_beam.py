"""AstraBeam v2.1 (Zero-Trust TLS, iOS/Android/Apple Watch Auto-Connect, Bluetooth Fallback & Custom Device Registry) UI."""

from __future__ import annotations

import io
import threading
import tkinter as tk
from typing import TYPE_CHECKING

import customtkinter as ctk

from ...core.astrabeam import (
    AppleDeviceScanner,
    AstraBeamClient,
    AstraBeamServer,
    SunshineMoonlightBridge,
    TailscaleDiscovery,
)
from ...core.mobile_continuity import DEVICE_PRESETS, MobileCompanionEngine, WifiQualityMonitor

if TYPE_CHECKING:
    from ..app import AstraCradleApp


class AstraBeamViewerWindow(ctk.CTkToplevel):
    """Built-in TLS Moonlight-style live remote desktop viewer over Tailscale."""

    def __init__(self, app: AstraCradleApp, client: AstraBeamClient, peer_label: str) -> None:
        super().__init__(app)
        self.app = app
        self.client = client
        self._stop = False
        self._photo = None
        self.title(f"AstraBeam Remote Viewer · {peer_label}")
        self.geometry("980x620")
        self.configure(fg_color=app.palette.bg_base)

        bar = ctk.CTkFrame(self, fg_color=app.palette.bg_card, corner_radius=0, height=40)
        bar.pack(fill="x")
        self.status_lbl = ctk.CTkLabel(
            bar,
            text=f"Connected to {client.base_url}",
            font=app.fonts["body_bold"],
            text_color=app.palette.accent_jade,
        )
        self.status_lbl.pack(side="left", padx=12, pady=6)
        self.text_entry = ctk.CTkEntry(bar, width=200, placeholder_text="Send remote text...")
        self.text_entry.pack(side="right", padx=6, pady=6)
        ctk.CTkButton(
            bar,
            text="Send Text",
            width=86,
            height=26,
            fg_color=app.palette.accent_cyan,
            text_color="#071216",
            command=self._send_text,
        ).pack(side="right", padx=4, pady=6)

        self.canvas = tk.Canvas(self, bg="#05080f", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Button-1>", lambda e: self._pointer_evt("click", "left", e))
        self.canvas.bind("<Button-3>", lambda e: self._pointer_evt("click", "right", e))
        self.canvas.bind("<Motion>", lambda e: self._pointer_evt("move", "", e))
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        threading.Thread(target=self._poll_frames, daemon=True).start()

    def _pointer_evt(self, kind: str, button: str, event: tk.Event) -> None:
        w = max(1, self.canvas.winfo_width())
        h = max(1, self.canvas.winfo_height())
        payload = {"kind": kind, "x": round(event.x / w, 4), "y": round(event.y / h, 4)}
        if button:
            payload["button"] = button
        threading.Thread(target=self.client.send_input, args=(payload,), daemon=True).start()

    def _send_text(self) -> None:
        txt = self.text_entry.get()
        if not txt:
            return
        self.text_entry.delete(0, "end")
        threading.Thread(target=self.client.send_input, args=({"kind": "text", "text": txt},), daemon=True).start()

    def _poll_frames(self) -> None:
        from PIL import Image, ImageTk

        while not self._stop:
            raw = self.client.fetch_frame(timeout=3.0)
            if raw:
                try:
                    im = Image.open(io.BytesIO(raw))
                    cw = max(320, self.canvas.winfo_width())
                    ch = max(200, self.canvas.winfo_height())
                    im.thumbnail((cw, ch))

                    def _paint(frame_img=im, rtt=self.client.last_rtt_ms) -> None:
                        if self._stop or not self.winfo_exists():
                            return
                        self._photo = ImageTk.PhotoImage(frame_img, master=self.canvas)
                        self.canvas.delete("all")
                        self.canvas.create_image(cw // 2, ch // 2, image=self._photo, anchor="center")
                        self.status_lbl.configure(text=f"Encrypted Live · {self.client.base_url} · {rtt:.1f} ms RTT")

                    self.after(0, _paint)
                except Exception:
                    pass
            else:
                import time

                time.sleep(0.25)

    def _on_close(self) -> None:
        self._stop = True
        try:
            self.destroy()
        except Exception:
            pass


def render_remote_beam_page(app: AstraCradleApp) -> None:
    """Renders AstraBeam TLS streaming, iOS/Android/Watch auto-connect, BT fallback & custom device registry."""
    p = app.palette
    for w in app.remote_beam_scroll.winfo_children():
        w.destroy()

    ts = getattr(app, "_latest_tailscale", None) or TailscaleDiscovery.get_status()
    app._latest_tailscale = ts
    sm = SunshineMoonlightBridge.inspect(ts)
    apple_devs = AppleDeviceScanner.scan(ts)
    adb_devs = MobileCompanionEngine.scan_android_adb()
    wifi_q = WifiQualityMonitor.probe(int(getattr(app.config_obj, "wifi_weak_rssi_threshold_dbm", -72)))
    srv: AstraBeamServer | None = getattr(app, "_astrabeam_server", None)

    # 1. Header & Proximity Auto-Connect Card
    hdr = ctk.CTkFrame(
        app.remote_beam_scroll, fg_color=p.bg_card, border_width=1, border_color=p.accent_cyan, corner_radius=12
    )
    hdr.pack(fill="x", pady=(0, 8))
    top_r = ctk.CTkFrame(hdr, fg_color="transparent")
    top_r.pack(fill="x", padx=14, pady=(10, 4))
    ctk.CTkLabel(
        top_r,
        text="AstraBeam · Zero-Trust Streaming, Proximity Auto-Connect & Device Hub",
        font=app.fonts["section"],
        text_color=p.accent_cyan,
    ).pack(side="left")
    ctk.CTkButton(
        top_r,
        text="Command Vault",
        height=28,
        corner_radius=7,
        fg_color=p.bg_elevated,
        hover_color=p.accent_cyan,
        font=app.fonts["small"],
        command=lambda: app.switch_nav("cmd_vault"),
    ).pack(side="right", padx=(6, 0))
    ctk.CTkButton(
        top_r,
        text="Refresh Mesh & Devices",
        height=28,
        corner_radius=7,
        fg_color=p.bg_elevated,
        hover_color=p.border_strong,
        font=app.fonts["small"],
        command=app._on_refresh_tailscale,
    ).pack(side="right")

    ctk.CTkLabel(
        hdr,
        text=(
            f"Tailscale: {ts.summary}\n"
            f"Wi-Fi Link: {wifi_q.reason}  ·  "
            f"Native Sunshine: {'Running' if sm.sunshine_running else ('Installed' if sm.sunshine_installed else 'Built-in TLS Host')}"
        ),
        font=app.fonts["small"],
        text_color=p.text_secondary,
        justify="left",
        wraplength=app._content_wrap(80),
    ).pack(anchor="w", padx=14, pady=(0, 6))

    prox_r = ctk.CTkFrame(hdr, fg_color="transparent")
    prox_r.pack(fill="x", padx=14, pady=(0, 10))
    auto_prox_var = tk.BooleanVar(value=bool(getattr(app.config_obj, "auto_connect_proximity_devices", True)))
    bt_fb_var = tk.BooleanVar(value=bool(getattr(app.config_obj, "bluetooth_handshake_fallback", True)))
    ctk.CTkSwitch(
        prox_r,
        text="Auto-Connect Paired Devices (Proximity / Same Network)",
        variable=auto_prox_var,
        command=lambda: app._on_toggle_proximity_flags(auto_prox_var.get(), bt_fb_var.get()),
    ).pack(side="left", padx=(0, 12))
    ctk.CTkSwitch(
        prox_r,
        text="Bluetooth Handshake Fallback (Weak Wi-Fi)",
        variable=bt_fb_var,
        command=lambda: app._on_toggle_proximity_flags(auto_prox_var.get(), bt_fb_var.get()),
    ).pack(side="left", padx=(0, 12))
    ctk.CTkButton(
        prox_r,
        text="Auto-Connect Now",
        height=28,
        fg_color=p.accent_jade,
        text_color="#071510",
        font=app.fonts["body_bold"],
        command=app._on_auto_connect_proximity_now,
    ).pack(side="right")

    outcomes = getattr(app, "_last_proximity_outcomes", [])
    if outcomes:
        for oc in outcomes[:6]:
            orow = ctk.CTkFrame(hdr, fg_color=p.bg_elevated, corner_radius=7)
            orow.pack(fill="x", padx=14, pady=(0, 4))
            tag = "BT FALLBACK" if oc.wifi_weak_fallback and oc.connected else ("CONNECTED" if oc.connected else "STANDBY")
            ctk.CTkLabel(
                orow,
                text=f"[{tag}] {oc.device_name} ({oc.device_kind}) via {oc.transport_used} — {oc.message}",
                font=app.fonts["mono_small"],
                text_color=p.accent_jade if oc.connected else p.text_muted,
                wraplength=app._content_wrap(120),
                justify="left",
            ).pack(anchor="w", padx=10, pady=4)
        ctk.CTkFrame(hdr, height=6, fg_color="transparent").pack()

    # 2. Host Controls Card (iOS / Android PWA + Native Viewer + TLS)
    hcard = ctk.CTkFrame(
        app.remote_beam_scroll, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
    )
    hcard.pack(fill="x", pady=(0, 8))
    ctk.CTkLabel(
        hcard,
        text="Host This PC for iPhone, Android, Apple Watch & Moonlight (TLS 1.2+)",
        font=app.fonts["body_bold"],
        text_color=p.accent_jade,
    ).pack(anchor="w", padx=14, pady=(10, 4))

    cfg_row = ctk.CTkFrame(hcard, fg_color="transparent")
    cfg_row.pack(fill="x", padx=14, pady=4)
    port_var = tk.StringVar(value=str(getattr(app.config_obj, "astrabeam_port", 47990)))
    fps_var = tk.StringVar(value=str(getattr(app.config_obj, "astrabeam_fps", 30)))
    pin_var = tk.StringVar(value=str(getattr(app.config_obj, "astrabeam_pin", "") or (srv.pin if srv else "")))
    ts_only_var = tk.BooleanVar(value=bool(getattr(app.config_obj, "astrabeam_tailscale_only", True)))
    tls_var = tk.BooleanVar(value=bool(getattr(app.config_obj, "astrabeam_tls_enabled", True)))

    for lbl, var, w in [("Port", port_var, 70), ("FPS", fps_var, 52), ("PIN", pin_var, 96)]:
        ctk.CTkLabel(cfg_row, text=lbl, font=app.fonts["small"], text_color=p.text_muted).pack(side="left", padx=(0, 4))
        ctk.CTkEntry(cfg_row, textvariable=var, width=w).pack(side="left", padx=(0, 8))
    ctk.CTkSwitch(cfg_row, text="Tailscale ACL", variable=ts_only_var).pack(side="left", padx=4)
    ctk.CTkSwitch(cfg_row, text="TLS 1.2+", variable=tls_var).pack(side="left", padx=4)

    is_live = srv is not None and srv.running
    if is_live:
        ctk.CTkButton(
            cfg_row,
            text="Revoke Sessions",
            height=28,
            fg_color=p.bg_elevated,
            hover_color=p.accent_coral,
            font=app.fonts["small"],
            command=app._on_revoke_astrabeam_sessions,
        ).pack(side="right", padx=(4, 0))
    ctk.CTkButton(
        cfg_row,
        text="Stop Host" if is_live else "Start Secure Host",
        height=30,
        corner_radius=7,
        fg_color=p.accent_coral if is_live else p.accent_jade,
        text_color="#071510",
        font=app.fonts["body_bold"],
        command=lambda: app._on_toggle_astrabeam_host(
            port_var.get(), fps_var.get(), pin_var.get(), ts_only_var.get(), tls_var.get()
        ),
    ).pack(side="right")

    if is_live and srv is not None:
        share_host = ts.self_ip or srv.bind_host
        base_u = f"{srv.scheme}://{share_host}:{srv.port}"
        fp_txt = f"  ·  Cert SHA-256: {srv.cert_fingerprint_sha256[:23]}..." if srv.cert_fingerprint_sha256 else ""
        ctk.CTkLabel(
            hcard,
            text=(
                f"HOST LIVE ({srv.scheme.upper()})  ·  PIN: {srv.pin}  ·  Frames: {srv.frames_served}  ·  Inputs: {srv.inputs_handled}{fp_txt}\n"
                f"iOS/Android PWA: {base_u}/   |   Apple Watch Companion: {base_u}/watch   |   BT Handshake API: {base_u}/api/bt/handshake"
            ),
            font=app.fonts["mono_small"],
            text_color=p.accent_jade,
            justify="left",
            wraplength=app._content_wrap(80),
        ).pack(anchor="w", padx=14, pady=(2, 10))
    else:
        ctk.CTkFrame(hcard, height=6, fg_color="transparent").pack()

    # 3. iPhone, Apple Watch & Android Continuity Card
    acard = ctk.CTkFrame(
        app.remote_beam_scroll, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
    )
    acard.pack(fill="x", pady=(0, 8))
    atop = ctk.CTkFrame(acard, fg_color="transparent")
    atop.pack(fill="x", padx=14, pady=(10, 4))
    ctk.CTkLabel(
        atop,
        text=f"iPhone, Apple Watch & Android Continuity ({len(apple_devs)} Apple · {len(adb_devs)} Android ADB)",
        font=app.fonts["body_bold"],
        text_color=p.accent_cyan,
    ).pack(side="left")
    ctk.CTkButton(
        atop,
        text="Copy iOS/Watch Shortcut",
        height=26,
        fg_color=p.bg_elevated,
        font=app.fonts["small"],
        command=app._on_copy_ios_watch_shortcut,
    ).pack(side="right", padx=(4, 0))
    ctk.CTkButton(
        atop,
        text="Android USB ADB Bridge",
        height=26,
        fg_color=p.bg_elevated,
        font=app.fonts["small"],
        command=lambda: app._on_setup_android_adb_bridge(adb_devs[0].serial if adb_devs else ""),
    ).pack(side="right")

    for ad in apple_devs:
        arow = ctk.CTkFrame(acard, fg_color=p.bg_elevated, corner_radius=7)
        arow.pack(fill="x", padx=14, pady=2)
        st = "ONLINE" if ad.connected else "PAIRED"
        ctk.CTkLabel(
            arow,
            text=f"[{st}] {ad.device_kind.upper()} · {ad.name} ({ad.transport}: {ad.address}) — {ad.notes}",
            font=app.fonts["mono_small"],
            text_color=p.accent_jade if ad.connected else p.text_primary,
            wraplength=app._content_wrap(120),
            justify="left",
        ).pack(anchor="w", padx=10, pady=4)
    for adb in adb_devs:
        arow = ctk.CTkFrame(acard, fg_color=p.bg_elevated, corner_radius=7)
        arow.pack(fill="x", padx=14, pady=2)
        ctk.CTkLabel(
            arow,
            text=f"[ADB:{adb.state.upper()}] ANDROID · {adb.model} ({adb.usb_or_wifi}: {adb.serial})",
            font=app.fonts["mono_small"],
            text_color=p.accent_jade if adb.state == "device" else p.accent_brass,
        ).pack(side="left", padx=10, pady=4)
        ctk.CTkButton(
            arow,
            text="Reverse Tunnel",
            width=105,
            height=22,
            fg_color=p.bg_card,
            command=lambda s=adb.serial: app._on_setup_android_adb_bridge(s),
        ).pack(side="right", padx=6, pady=3)
    if not apple_devs and not adb_devs:
        ctk.CTkLabel(
            acard,
            text="No live Tailscale iOS, paired Bluetooth Watch/iPhone, or ADB Android devices detected right now — register any device below for instant proximity/BT handshake.",
            font=app.fonts["mono_small"],
            text_color=p.text_muted,
            wraplength=app._content_wrap(90),
            justify="left",
        ).pack(anchor="w", padx=14, pady=(0, 8))
    else:
        ctk.CTkFrame(acard, height=6, fg_color="transparent").pack()

    # 4. User-Designated New Devices & Custom Programs Card
    dcard = ctk.CTkFrame(
        app.remote_beam_scroll, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
    )
    dcard.pack(fill="x", pady=(0, 8))
    ctk.CTkLabel(
        dcard,
        text="Designate New Released Devices & Custom Companion Programs",
        font=app.fonts["body_bold"],
        text_color=p.accent_brass,
    ).pack(anchor="w", padx=14, pady=(10, 4))

    preset_names = list(DEVICE_PRESETS.keys())
    preset_var = tk.StringVar(value=preset_names[0])
    dev_name_var = tk.StringVar()
    dev_host_var = tk.StringVar()
    dev_mac_var = tk.StringVar()
    dev_adb_var = tk.StringVar()
    dev_port_var = tk.StringVar(value="47990")
    dev_cmd_var = tk.StringVar()
    dev_auto_var = tk.BooleanVar(value=True)

    def _apply_preset(pname: str) -> None:
        spec = DEVICE_PRESETS.get(pname) or {}
        dev_port_var.set(str(spec.get("port", 47990)))
        dev_cmd_var.set(str(spec.get("program_command", "")))

    dr1 = ctk.CTkFrame(dcard, fg_color="transparent")
    dr1.pack(fill="x", padx=14, pady=3)
    ctk.CTkOptionMenu(dr1, values=preset_names, variable=preset_var, width=250, command=_apply_preset).pack(
        side="left", padx=(0, 8)
    )
    ctk.CTkEntry(dr1, textvariable=dev_name_var, width=210, placeholder_text="Device / Program Name (e.g. iPhone 18 Pro)").pack(
        side="left", padx=(0, 8), fill="x", expand=True
    )
    ctk.CTkEntry(dr1, textvariable=dev_host_var, width=140, placeholder_text="IP / Tailscale Host").pack(
        side="left", padx=(0, 6)
    )
    ctk.CTkEntry(dr1, textvariable=dev_port_var, width=64, placeholder_text="Port").pack(side="left")

    dr2 = ctk.CTkFrame(dcard, fg_color="transparent")
    dr2.pack(fill="x", padx=14, pady=3)
    ctk.CTkEntry(dr2, textvariable=dev_mac_var, width=165, placeholder_text="Bluetooth MAC AA:BB:CC:DD:EE:FF").pack(
        side="left", padx=(0, 8)
    )
    ctk.CTkEntry(dr2, textvariable=dev_adb_var, width=130, placeholder_text="ADB Serial (opt)").pack(
        side="left", padx=(0, 8)
    )
    ctk.CTkEntry(dr2, textvariable=dev_cmd_var, width=220, placeholder_text="Custom Program Cmd (e.g. moonlight stream {host})").pack(
        side="left", padx=(0, 8), fill="x", expand=True
    )
    ctk.CTkSwitch(dr2, text="Auto-Connect", variable=dev_auto_var).pack(side="left", padx=(0, 8))
    ctk.CTkButton(
        dr2,
        text="Save Device / App",
        height=28,
        fg_color=p.accent_cyan,
        text_color="#071216",
        font=app.fonts["body_bold"],
        command=lambda: app._on_register_designated_device(
            preset_var.get(),
            dev_name_var.get(),
            dev_host_var.get(),
            dev_mac_var.get(),
            dev_adb_var.get(),
            dev_port_var.get(),
            dev_cmd_var.get(),
            dev_auto_var.get(),
        ),
    ).pack(side="right")

    saved_devs = app.device_registry.load_devices()
    for sd in saved_devs:
        srow = ctk.CTkFrame(dcard, fg_color=p.bg_elevated, corner_radius=7)
        srow.pack(fill="x", padx=14, pady=2)
        info_txt = f"{sd.name} [{sd.device_type} · {sd.os_family}] · Status: {sd.last_status}"
        if sd.last_transport:
            info_txt += f" ({sd.last_transport})"
        ctk.CTkLabel(srow, text=info_txt, font=app.fonts["mono_small"], text_color=p.text_primary).pack(
            side="left", padx=10, pady=4
        )
        ctk.CTkButton(
            srow,
            text="Remove",
            width=66,
            height=22,
            fg_color=p.bg_card,
            hover_color=p.accent_coral,
            command=lambda did=sd.device_id: app._on_remove_designated_device(did),
        ).pack(side="right", padx=4, pady=3)
        if sd.program_command:
            ctk.CTkButton(
                srow,
                text="Launch App",
                width=88,
                height=22,
                fg_color=p.accent_jade,
                text_color="#071510",
                command=lambda did=sd.device_id: app._on_launch_designated_program(did),
            ).pack(side="right", padx=4, pady=3)
    ctk.CTkFrame(dcard, height=6, fg_color="transparent").pack()

    # 5. Native Sunshine/Moonlight iOS & Android Hardening + Connect Card
    ccard = ctk.CTkFrame(
        app.remote_beam_scroll, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
    )
    ccard.pack(fill="x", pady=(0, 8))
    top_s = ctk.CTkFrame(ccard, fg_color="transparent")
    top_s.pack(fill="x", padx=14, pady=(10, 4))
    ctk.CTkLabel(
        top_s,
        text="Native Sunshine + Moonlight (iOS / Android 120fps) & Remote Viewer",
        font=app.fonts["body_bold"],
        text_color=p.accent_brass,
    ).pack(side="left")
    ctk.CTkButton(
        top_s,
        text="Apply Hardened Tailscale Sunshine Config",
        height=26,
        fg_color=p.bg_elevated,
        hover_color=p.accent_jade,
        font=app.fonts["small"],
        command=app._on_apply_sunshine_tailscale_conf,
    ).pack(side="right")

    conn_r = ctk.CTkFrame(ccard, fg_color="transparent")
    conn_r.pack(fill="x", padx=14, pady=4)
    rm_host = tk.StringVar(value=(ts.peers[0].tailscale_ip if ts.peers else "100.64.0.1"))
    rm_port = tk.StringVar(value=str(getattr(app.config_obj, "astrabeam_port", 47990)))
    rm_pin = tk.StringVar(value="")
    for lbl, var, w in [("Host / Tailscale IP", rm_host, 150), ("Port", rm_port, 68), ("PIN", rm_pin, 88)]:
        ctk.CTkLabel(conn_r, text=lbl, font=app.fonts["small"], text_color=p.text_muted).pack(side="left", padx=(0, 4))
        ctk.CTkEntry(conn_r, textvariable=var, width=w).pack(side="left", padx=(0, 10))
    ctk.CTkButton(
        conn_r,
        text="Connect Viewer",
        height=28,
        fg_color=p.accent_cyan,
        text_color="#071216",
        font=app.fonts["body_bold"],
        command=lambda: app._on_connect_astrabeam(rm_host.get(), rm_port.get(), rm_pin.get()),
    ).pack(side="left", padx=4)
    if sm.moonlight_installed:
        ctk.CTkButton(
            conn_r,
            text="Launch Moonlight",
            height=28,
            fg_color=p.bg_elevated,
            command=lambda: app._on_launch_moonlight(rm_host.get()),
        ).pack(side="left", padx=4)

    wol_r = ctk.CTkFrame(ccard, fg_color="transparent")
    wol_r.pack(fill="x", padx=14, pady=(2, 10))
    mac_var = tk.StringVar(value="")
    ctk.CTkLabel(wol_r, text="Wake-on-LAN MAC:", font=app.fonts["small"], text_color=p.text_muted).pack(side="left", padx=(0, 6))
    ctk.CTkEntry(wol_r, textvariable=mac_var, width=165, placeholder_text="AA:BB:CC:DD:EE:FF").pack(side="left", padx=(0, 8))
    ctk.CTkButton(
        wol_r,
        text="Send WoL Packet",
        height=26,
        fg_color=p.bg_elevated,
        command=lambda: app._on_send_wol(mac_var.get()),
    ).pack(side="left")
