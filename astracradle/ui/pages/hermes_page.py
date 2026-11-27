"""Hermes OS AI Agent UI Page — GPU-Matched Local Models, Free Cloud APIs, 1-Click Auto-Setup, Chat, Skills, Memory & Cron."""

from __future__ import annotations

import json
import threading
import tkinter as tk
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

import customtkinter as ctk

from ...core.hermes_agent import (
    CUSTOM_API_PROVIDERS,
    FREE_CLOUD_PROVIDERS,
    HERMES_GPU_MODES,
    HERMES_PERMISSION_LEVELS,
    HERMES_PERSONAS,
    HermesHardwareRecommendation,
    HermesSetupStatus,
    recommend_hermes_models,
)

if TYPE_CHECKING:
    from ..app import AstraCradleApp


_QUICK_ACTION_CHIPS: tuple[tuple[str, str], ...] = (
    ("Diagnose PC", "Run a deep system scan and check live CPU/GPU/RAM status, then summarize clearly."),
    ("Fix Slow PC (Safe)", "Diagnose bottlenecks, safely clean caches with astra_fix_safe(dry_run=false), and restrain background CPU hogs if needed."),
    ("Optimize Wi-Fi", "Check my Wi-Fi channels and optimize if a cleaner channel is available."),
    ("Startup & Hogs", "Audit my startup entries and high-CPU processes, and explain or trim safe bloat."),
    ("Remote Tasks", "Check paired mobile/remote devices, Tailscale/ADB/AstraBeam status, and recent remote tasks."),
    ("Self-Heal & Undo", "Run AstraCradle 14-invariant self-heal repair and list any recent rollback/undo items."),
    ("What AI Learned", "Summarize what you have learned about my whole-PC usage, top apps, games, and remote tasks."),
    ("Research & Improve Astra", "Search the web or inspect AstraCradle's local build to propose safe improvements, tests, or new skills."),
)


def _persona_options() -> tuple[list[str], dict[str, str]]:
    labels = [spec["label"] for spec in HERMES_PERSONAS.values()]
    by_lbl = {spec["label"]: k for k, spec in HERMES_PERSONAS.items()}
    return labels, by_lbl


def _clean_bubble_markdown(raw: str) -> str:
    """Turns raw Markdown headings/bold markers into crisp plain-text hierarchy for Tk chat bubbles."""
    import re as _re

    txt = str(raw or "")
    txt = _re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", txt)
    txt = _re.sub(r"\*\*(.+?)\*\*", r"\1", txt)
    txt = _re.sub(r"\*{2,}", "", txt)
    txt = _re.sub(r"__(.+?)__", r"\1", txt)
    txt = _re.sub(r"`([^`\n]+)`", r"\1", txt)
    return txt


def _capture_clipboard_attachments(hm, tk_widget=None) -> tuple[list[str], str]:
    """Reads pasted images or file paths/URIs from the system clipboard (Wayland, X11, Pillow, or Tk)."""
    import shutil
    import subprocess
    import time as _t
    import urllib.parse

    found: list[str] = []
    hm.captures_dir.mkdir(parents=True, exist_ok=True)

    # 1. Try PIL ImageGrab (supports images AND copied file lists on some platforms)
    try:
        from PIL import ImageGrab  # type: ignore

        grabbed = ImageGrab.grabclipboard()
        if isinstance(grabbed, list):
            for item in grabbed:
                p_item = Path(str(item)).expanduser()
                if p_item.exists() and p_item.is_file():
                    found.append(str(p_item))
            if found:
                return found[:8], f"Pasted {len(found)} file(s) from clipboard!"
        elif grabbed is not None and hasattr(grabbed, "save"):
            out = hm.captures_dir / f"paste_{int(_t.time() * 1000)}.png"
            grabbed.save(str(out), format="PNG")
            return [str(out)], f"Pasted image attached ({out.name})!"
    except Exception:
        pass

    # 2. Wayland / X11 binary image paste (wl-paste / xclip)
    img_cmds = []
    if shutil.which("wl-paste"):
        img_cmds.append(["wl-paste", "--no-newline", "--type", "image/png"])
    if shutil.which("xclip"):
        img_cmds.append(["xclip", "-selection", "clipboard", "-t", "image/png", "-o"])
    for cmd in img_cmds:
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=3)
            if r.returncode == 0 and r.stdout and r.stdout[:4] == b"\x89PNG":
                out = hm.captures_dir / f"paste_{int(_t.time() * 1000)}.png"
                out.write_bytes(r.stdout)
                return [str(out)], f"Pasted screenshot attached ({out.name})!"
        except Exception:
            continue

    # 3. Wayland / X11 file URI list (files copied in Dolphin / Nautilus / Thunar)
    uri_cmds = []
    if shutil.which("wl-paste"):
        uri_cmds.append(["wl-paste", "--no-newline", "--type", "text/uri-list"])
    if shutil.which("xclip"):
        uri_cmds.append(["xclip", "-selection", "clipboard", "-t", "text/uri-list", "-o"])
    for cmd in uri_cmds:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
            if r.returncode == 0 and r.stdout.strip():
                for line in r.stdout.splitlines():
                    ln = line.strip()
                    if ln.startswith("file://"):
                        local = Path(urllib.parse.unquote(urllib.parse.urlparse(ln).path))
                        if local.exists() and local.is_file():
                            found.append(str(local))
                if found:
                    return found[:8], f"Pasted {len(found)} file(s) attached!"
        except Exception:
            continue

    # 4. Plain text clipboard containing file:// URI or existing file path(s)
    clip_txt = ""
    if tk_widget is not None:
        try:
            clip_txt = str(tk_widget.clipboard_get() or "").strip()
        except Exception:
            clip_txt = ""
    if not clip_txt:
        _ok_c, clip_txt = hm.clipboard_read()
        clip_txt = str(clip_txt or "").strip()
    if clip_txt:
        for line in clip_txt.splitlines():
            ln = line.strip().strip('"').strip("'")
            if not ln:
                continue
            if ln.startswith("file://"):
                ln = urllib.parse.unquote(urllib.parse.urlparse(ln).path)
            if ln.startswith("/") or ln.startswith("~/"):
                cand = Path(ln).expanduser()
                if cand.exists() and cand.is_file():
                    found.append(str(cand))
        if found:
            return found[:8], f"Pasted {len(found)} file path(s) attached!"

    return [], ""


def _refresh_attachment_strip(
    strip: ctk.CTkFrame,
    att_list: list[str],
    p,
    f,
    *,
    before_widget=None,
) -> None:
    for ch in strip.winfo_children():
        ch.destroy()
    if not att_list:
        strip.pack_forget()
        return
    if not strip.winfo_manager():
        if before_widget is not None and before_widget.winfo_manager():
            strip.pack(fill="x", padx=8, pady=(0, 4), before=before_widget)
        else:
            strip.pack(fill="x", padx=8, pady=(0, 4))
    ctk.CTkLabel(strip, text="Attached:", font=f["small_bold"], text_color=p.accent_cyan).pack(side="left", padx=(2, 6))
    for idx, ap in enumerate(list(att_list)):
        name = Path(ap).name
        is_img = Path(ap).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
        ico = "🖼" if is_img else "📄"

        def _rm(i: int = idx) -> None:
            if 0 <= i < len(att_list):
                att_list.pop(i)
                _refresh_attachment_strip(strip, att_list, p, f, before_widget=before_widget)

        ctk.CTkButton(
            strip,
            text=f"{ico} {name[:22]}  ✕",
            height=24,
            fg_color=p.bg_card,
            border_width=1,
            border_color=p.border_subtle,
            font=f["caption"],
            command=_rm,
        ).pack(side="left", padx=3, pady=2)


def _format_tool_trace(raw_trace: str) -> str:
    if not raw_trace:
        return ""
    try:
        items = json.loads(raw_trace)
        if isinstance(items, list) and items:
            parts = []
            for ev in items:
                name = str(ev.get("tool") or "tool")
                ok_mark = "OK" if ev.get("ok") else "BLOCKED/ERR"
                parts.append(f"{name} [{ok_mark}]")
            return "  ->  ".join(parts)
    except Exception:
        pass
    return raw_trace[:220]


def _render_chat_bubbles(
    container: ctk.CTkScrollableFrame,
    msgs: list[dict[str, str]],
    persona: dict[str, str],
    p,
    f,
    *,
    wrap_px: int = 720,
    thinking_text: str = "",
    error_text: str = "",
) -> None:
    msg_sig = (
        len(msgs),
        tuple((str(m.get("ts") or ""), str(m.get("role") or ""), len(str(m.get("content") or ""))) for m in msgs),
        str(persona.get("name") or ""),
        int(chat_font_pt),
        str(error_text or ""),
    )
    prev_sig = getattr(container, "_hermes_render_sig", None)
    prev_th_lbl = getattr(container, "_hermes_thinking_lbl", None)
    if (
        prev_sig == msg_sig
        and thinking_text
        and not error_text
        and prev_th_lbl is not None
    ):
        try:
            if prev_th_lbl.winfo_exists():
                prev_th_lbl.configure(text=thinking_text)
                try:
                    container._parent_canvas.yview_moveto(1.0)
                except Exception:
                    pass
                return
        except Exception:
            pass

    container._hermes_render_sig = msg_sig  # type: ignore[attr-defined]
    container._hermes_thinking_lbl = None  # type: ignore[attr-defined]
    for child in container.winfo_children():
        child.destroy()

    if not msgs and not thinking_text and not error_text:
        welcome = ctk.CTkFrame(
            container,
            fg_color=p.bg_card,
            border_width=1,
            border_color=p.border_subtle,
            corner_radius=12,
        )
        welcome.pack(fill="x", padx=6, pady=6)
        ctk.CTkLabel(
            welcome,
            text=f"{persona['name']} · {persona['badge']}",
            font=f["body_bold"],
            text_color=p.accent_cyan,
        ).pack(anchor="w", padx=14, pady=(10, 2))
        ctk.CTkLabel(
            welcome,
            text=(
                f"{persona['greeting']}\n\n"
                f"Style: {persona['tagline']}\n"
                "Try one of the quick action buttons below, or ask anything about your PC, Wi-Fi, games, "
                "remote devices, online research, or updating AstraCradle's local build."
            ),
            font=f["body"],
            text_color=p.text_primary,
            justify="left",
            wraplength=wrap_px,
        ).pack(anchor="w", padx=14, pady=(0, 12))
    else:
        for m in msgs:
            is_user = m.get("role") == "user"
            card_bg = "#132934" if is_user else p.bg_card
            border_col = p.accent_cyan if is_user else p.border_subtle
            hdr_txt = "You" if is_user else f"{persona['name']} · {persona['badge']}"
            hdr_col = p.accent_cyan if is_user else p.accent_jade

            bubble = ctk.CTkFrame(
                container,
                fg_color=card_bg,
                border_width=1,
                border_color=border_col,
                corner_radius=11,
            )
            bubble.pack(
                fill="x",
                padx=(46, 6) if is_user else (6, 32),
                pady=4,
            )
            top = ctk.CTkFrame(bubble, fg_color="transparent")
            top.pack(fill="x", padx=12, pady=(8, 2))
            ctk.CTkLabel(top, text=hdr_txt, font=f["caption_bold"], text_color=hdr_col).pack(side="left")
            ts = str(m.get("ts") or "")[:19].replace("T", " ")
            if ts:
                ctk.CTkLabel(top, text=ts, font=f["caption"], text_color=p.text_muted).pack(side="right")

            ctk.CTkLabel(
                bubble,
                text=str(m.get("content") or ""),
                font=f["body"],
                text_color=p.text_primary,
                justify="left",
                wraplength=max(360, wrap_px - 80),
            ).pack(anchor="w", padx=12, pady=(0, 8))

            trace_str = _format_tool_trace(str(m.get("tool_trace") or ""))
            if trace_str:
                tbar = ctk.CTkFrame(bubble, fg_color=p.bg_elevated, corner_radius=7)
                tbar.pack(fill="x", padx=10, pady=(0, 8))
                ctk.CTkLabel(
                    tbar,
                    text=f"Tools Run: {trace_str}",
                    font=f["caption"],
                    text_color=p.text_secondary,
                    justify="left",
                    wraplength=max(340, wrap_px - 100),
                ).pack(anchor="w", padx=8, pady=4)

    if thinking_text:
        tcard = ctk.CTkFrame(
            container,
            fg_color=p.bg_card,
            border_width=1,
            border_color=p.accent_brass,
            corner_radius=11,
        )
        tcard.pack(fill="x", padx=(6, 32), pady=4)
        ctk.CTkLabel(
            tcard,
            text=f"{persona['name']} · Working...",
            font=f["caption_bold"],
            text_color=p.accent_brass,
        ).pack(anchor="w", padx=12, pady=(8, 2))
        ctk.CTkLabel(
            tcard,
            text=thinking_text,
            font=f["body"],
            text_color=p.text_secondary,
            justify="left",
            wraplength=max(360, wrap_px - 80),
        ).pack(anchor="w", padx=12, pady=(0, 8))

    if error_text:
        ecard = ctk.CTkFrame(
            container,
            fg_color=p.bg_card,
            border_width=1,
            border_color=p.accent_coral,
            corner_radius=11,
        )
        ecard.pack(fill="x", padx=(6, 32), pady=4)
        ctk.CTkLabel(
            ecard,
            text=f"{persona['name']} · Notice / Error",
            font=f["caption_bold"],
            text_color=p.accent_coral,
        ).pack(anchor="w", padx=12, pady=(8, 2))
        ctk.CTkLabel(
            ecard,
            text=error_text,
            font=f["body"],
            text_color=p.text_primary,
            justify="left",
            wraplength=max(360, wrap_px - 80),
        ).pack(anchor="w", padx=12, pady=(0, 8))

    try:
        container.update_idletasks()
        container._parent_canvas.yview_moveto(1.0)
    except Exception:
        pass


def _build_quick_chips(
    parent: ctk.CTkFrame,
    send_cb: Callable[[str], None],
    p,
    f,
    *,
    cols: int = 4,
) -> None:
    for c in range(cols):
        parent.grid_columnconfigure(c, weight=1, uniform="qchips")
    for idx, (lbl_txt, q_txt) in enumerate(_QUICK_ACTION_CHIPS):
        r, c = divmod(idx, cols)
        ctk.CTkButton(
            parent,
            text=lbl_txt,
            height=28,
            corner_radius=7,
            fg_color=p.bg_elevated,
            hover_color=p.accent_cyan,
            text_color=p.text_primary,
            font=f["caption"],
            command=lambda qt=q_txt: send_cb(qt),
        ).grid(row=r, column=c, sticky="ew", padx=3, pady=3)


def _build_provider_options() -> tuple[list[str], dict[str, tuple[str, str]]]:
    labels: list[str] = []
    mapping: dict[str, tuple[str, str]] = {}
    lbl_local = "100% Local-Only AI (Offline · Auto-Matched GPU Model via Ollama)"
    labels.append(lbl_local)
    mapping[lbl_local] = ("local_gpu", "ollama")
    for k, spec in FREE_CLOUD_PROVIDERS.items():
        lbl = f"Free Cloud · {spec['label']}"
        labels.append(lbl)
        mapping[lbl] = ("free_cloud", k)
    for k, spec in CUSTOM_API_PROVIDERS.items():
        lbl = f"Own Key · {spec['label']}"
        labels.append(lbl)
        mapping[lbl] = ("custom_api", k)
    return labels, mapping


def open_quick_assistant_modal(app: AstraCradleApp) -> None:
    """Opens a persistent-session AI Action Assistant popup from any screen in AstraCradle (Ctrl+K)."""
    existing = getattr(app, "_quick_ai_win", None)
    if existing is not None:
        try:
            if existing.winfo_exists():
                existing.lift()
                existing.focus_force()
                return
        except Exception:
            pass

    p = app.palette
    f = app.fonts
    cfg = app.config_obj
    hm = app.hermes_mgr

    win = ctk.CTkToplevel(app)
    app._quick_ai_win = win
    win.title("AstraCradle AI Action Assistant")
    win.geometry("860x660")
    win.minsize(740, 540)
    win.configure(fg_color=p.bg_canvas)
    try:
        win.transient(app)
    except Exception:
        pass

    hdr = ctk.CTkFrame(win, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12)
    hdr.pack(fill="x", padx=12, pady=(12, 6))
    ctk.CTkLabel(
        hdr,
        text="Ask AI Assistant · Diagnose, Fix, Research & Automate",
        font=f["section"],
        text_color=p.accent_cyan,
    ).pack(side="left", padx=14, pady=10)

    def _open_full_hub() -> None:
        app._hermes_fullscreen_chat = True
        try:
            win.destroy()
        except Exception:
            pass
        app.switch_nav("hermes")

    ctk.CTkButton(
        hdr,
        text="Open Full-Screen Chat ⛶",
        height=30,
        fg_color=p.accent_jade,
        text_color="#071510",
        font=f["small_bold"],
        command=_open_full_hub,
    ).pack(side="right", padx=12, pady=8)

    # Identity + Hardware/Budget status strip
    meta_card = ctk.CTkFrame(win, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=10)
    meta_card.pack(fill="x", padx=12, pady=(0, 6))

    id_row = ctk.CTkFrame(meta_card, fg_color="transparent")
    id_row.pack(fill="x", padx=12, pady=(8, 4))
    p_labels, p_by_lbl = _persona_options()
    cur_persona = hm.get_active_persona(cfg)
    persona_var = tk.StringVar(value=cur_persona["label"])

    ctk.CTkLabel(id_row, text="Assistant Identity:", font=f["small_bold"], text_color=p.text_primary).pack(side="left", padx=(0, 8))
    trait_lbl = ctk.CTkLabel(
        meta_card,
        text=f"{cur_persona['tagline']}  ·  Traits: {cur_persona['traits']}",
        font=f["caption"],
        text_color=p.accent_jade,
        justify="left",
        wraplength=800,
    )

    spec = hm.resolve_provider_spec(cfg)
    gpu_plan = hm.compute_gpu_throttle_plan(cfg)
    ctk.CTkLabel(
        id_row,
        text=(
            f"Model: {spec['model']} ({spec['provider']}) · "
            f"GPU: {gpu_plan['target_gpu_pct']}% · Reserve: {gpu_plan['vram_reserve_mb']} MB"
        ),
        font=f["caption"],
        text_color=p.text_secondary,
    ).pack(side="right")

    session_id = str(getattr(app, "_hermes_session_id", "default"))
    chat_scroll = ctk.CTkScrollableFrame(
        win,
        fg_color=p.bg_elevated,
        border_width=1,
        border_color=p.border_subtle,
        corner_radius=12,
    )
    chat_scroll.pack(fill="both", expand=True, padx=12, pady=4)

    stream_msgs_cache: list[list[dict[str, Any]] | None] = [None]

    def _reload_transcript(thinking: str = "", err: str = "", *, use_cache: bool = False) -> None:
        persona_now = hm.get_active_persona(cfg)
        msgs = hm.get_session_messages(session_id, limit=30)
        _render_chat_bubbles(
            chat_scroll,
            msgs,
            persona_now,
            p,
            f,
            wrap_px=740,
            thinking_text=thinking,
            error_text=err,
        )

    def _on_persona_change(choice: str) -> None:
        pkey = p_by_lbl.get(choice, "astra_standard")
        cfg.hermes_persona = pkey
        app.cfg_mgr.save(cfg)
        hm.write_config_yaml(cfg)
        np = hm.get_active_persona(cfg)
        trait_lbl.configure(text=f"{np['tagline']}  ·  Traits: {np['traits']}")
        _reload_transcript()
        app.show_toast(f"Switched AI identity to {np['name']} ({np['badge']})")
        if app.current_nav == "hermes":
            render_hermes_page(app)

    ctk.CTkOptionMenu(id_row, values=p_labels, variable=persona_var, width=360, command=_on_persona_change).pack(side="left")
    trait_lbl.pack(anchor="w", padx=12, pady=(0, 8))
    _reload_transcript()

    chips = ctk.CTkFrame(win, fg_color="transparent")
    chips.pack(fill="x", padx=10, pady=(4, 4))

    popup_att_strip = ctk.CTkFrame(win, fg_color="transparent")
    in_row = ctk.CTkFrame(win, fg_color="transparent")
    in_row.pack(fill="x", padx=12, pady=(2, 12))
    entry = ctk.CTkEntry(in_row, height=36, placeholder_text="Ask or tell AstraCradle AI to inspect, fix, research, or update...")
    entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
    entry.bind("<Control-v>", _paste_popup_item)
    entry.bind("<Control-V>", _paste_popup_item)

    def _send(override: str | None = None) -> None:
        q = (override if override is not None else entry.get()).strip()
        if not q and popup_attachments:
            q = "Please inspect the attached image/file(s) and help me with what I am working on."
        if not q:
            return
        if override is None:
            entry.delete(0, "end")
        att_now = list(popup_attachments)
        popup_attachments.clear()
        _refresh_attachment_strip(popup_att_strip, popup_attachments, p, f, before_widget=in_row)
        _reload_transcript(thinking=f"Prompt: \"{q[:140]}\"\nInspecting & streaming response via {spec['model']}...")

        last_stream_ts = [0.0]

        def _on_stream(partial: str) -> None:
            import time as _t

            txt = _clean_bubble_markdown(partial)
            if not txt.strip():
                return
            now_t = _t.monotonic()
            if (now_t - last_stream_ts[0]) < 0.14:
                return
            last_stream_ts[0] = now_t

            def _upd() -> None:
                if win.winfo_exists():
                    _reload_transcript(thinking=txt, use_cache=True)

            app.after(0, _upd)

        cancel_ev = threading.Event()
        win._active_cancel = cancel_ev  # type: ignore[attr-defined]
        steps_seen: list[str] = []

        def _on_step(step_txt: str) -> None:
            steps_seen.append(step_txt)
            joined = "  |  ".join(steps_seen[-5:])
            app.after(0, lambda j=joined: _reload_transcript(thinking=f"Steps: {j}", use_cache=True) if win.winfo_exists() else None)

        def _worker() -> None:
            res = hm.run_prompt(
                q,
                cfg,
                session_id=session_id,
                attachments=att_now or None,
                stream_cb=_on_stream,
                step_cb=_on_step,
                cancel_event=cancel_ev,
            )

            def _done() -> None:
                app._hermes_status_cache = hm.get_status(cfg)
                if win.winfo_exists():
                    _reload_transcript(err=(res.error if not res.ok else ""))
                if not res.ok:
                    app.show_toast(f"Hermes AI: {res.error}")
                elif app.current_nav == "hermes":
                    render_hermes_page(app)

            app.after(0, _done)

        threading.Thread(target=_worker, daemon=True).start()

    _build_quick_chips(chips, _send, p, f, cols=4)
    entry.bind("<Return>", lambda _e: _send())
    ctk.CTkButton(
        in_row,
        text="Send & Act",
        width=130,
        height=36,
        fg_color=p.accent_cyan,
        text_color="#071216",
        font=f["body_bold"],
        command=_send,
    ).pack(side="right")
    entry.focus_set()


def _build_chat_workspace(
    container: ctk.CTkFrame | ctk.CTkScrollableFrame,
    app: AstraCradleApp,
    *,
    is_fullscreen: bool = False,
) -> None:
    """Renders the fleshed-out Hermes AI Action Chat (used in Tab 1 AND Full-Screen Chat mode)."""
    p = app.palette
    f = app.fonts
    cfg = app.config_obj
    hm = app.hermes_mgr

    session_id = str(getattr(app, "_hermes_session_id", "default"))
    chat_top = ctk.CTkFrame(container, fg_color=p.bg_elevated, corner_radius=10)
    chat_top.pack(fill="x", padx=8, pady=(6, 4))

    row_a = ctk.CTkFrame(chat_top, fg_color="transparent")
    row_a.pack(fill="x", padx=10, pady=(8, 4))

    sessions = hm.list_sessions(limit=20)
    sess_ids = [s["session_id"] for s in sessions] or ["default"]
    if session_id not in sess_ids:
        sess_ids.insert(0, session_id)
    sess_var = tk.StringVar(value=session_id)

    ctk.CTkLabel(row_a, text="Session:", font=f["small_bold"]).pack(side="left", padx=(0, 6))

    def _switch_sess(val: str) -> None:
        app._hermes_session_id = val
        render_hermes_page(app)

    ctk.CTkOptionMenu(row_a, values=sess_ids, variable=sess_var, width=180, command=_switch_sess).pack(side="left", padx=(0, 6))

    def _new_session() -> None:
        import time as _t

        app._hermes_session_id = f"session_{int(_t.time())}"
        render_hermes_page(app)

    ctk.CTkButton(row_a, text="+ New Chat", width=96, height=28, fg_color=p.bg_card, command=_new_session).pack(side="left", padx=(0, 12))

    # Active Identity / Persona selector right in the chat header
    p_labels, p_by_lbl = _persona_options()
    cur_persona = hm.get_active_persona(cfg)
    persona_var = tk.StringVar(value=cur_persona["label"])

    ctk.CTkLabel(row_a, text="Identity:", font=f["small_bold"]).pack(side="left", padx=(0, 6))
    trait_banner = ctk.CTkLabel(
        chat_top,
        text=f"{cur_persona['name']} ({cur_persona['badge']}) — {cur_persona['tagline']}  ·  Mannerisms: {cur_persona['mannerisms']}",
        font=f["caption"],
        text_color=p.accent_jade,
        justify="left",
        wraplength=app._content_wrap(90),
    )

    def _toggle_fs() -> None:
        app._hermes_fullscreen_chat = not bool(getattr(app, "_hermes_fullscreen_chat", False))
        render_hermes_page(app)

    ctk.CTkButton(
        row_a,
        text=("⤢ Exit Full-Screen" if is_fullscreen else "⛶ Full-Screen Chat"),
        width=145,
        height=28,
        fg_color=(p.accent_brass if is_fullscreen else p.accent_cyan),
        text_color="#071216",
        font=f["small_bold"],
        command=_toggle_fs,
    ).pack(side="right")

    try:
        win_h = int(app.winfo_height() or 900)
    except Exception:
        win_h = 900
    if win_h < 400:
        win_h = 900
    chat_h = max(320, min(640, win_h - 330)) if is_fullscreen else max(210, min(380, win_h - 490))

    chat_scroll = ctk.CTkScrollableFrame(
        container,
        height=chat_h,
        fg_color=p.bg_elevated,
        border_width=1,
        border_color=p.border_subtle,
        corner_radius=11,
    )
    chat_scroll.pack(fill="both", expand=True, padx=8, pady=4)

    stream_msgs_cache: list[list[dict[str, Any]] | None] = [None]

    def _reload_chat(thinking: str = "", err: str = "", *, use_cache: bool = False) -> None:
        persona_now = hm.get_active_persona(cfg)
        msgs = hm.get_session_messages(session_id, limit=40)
        _render_chat_bubbles(
            chat_scroll,
            msgs,
            persona_now,
            p,
            f,
            wrap_px=max(560, app._content_wrap(120)),
            thinking_text=thinking,
            error_text=err,
        )

    def _on_persona_changed(choice: str) -> None:
        pkey = p_by_lbl.get(choice, "astra_standard")
        cfg.hermes_persona = pkey
        app.cfg_mgr.save(cfg)
        hm.write_config_yaml(cfg)
        np = hm.get_active_persona(cfg)
        trait_banner.configure(
            text=f"{np['name']} ({np['badge']}) — {np['tagline']}  ·  Mannerisms: {np['mannerisms']}"
        )
        _reload_chat()
        app.show_toast(f"AI identity switched to {np['name']} ({np['badge']})")

    ctk.CTkOptionMenu(row_a, values=p_labels, variable=persona_var, width=320, command=_on_persona_changed).pack(side="left")
    trait_banner.pack(anchor="w", padx=10, pady=(0, 8))
    _reload_chat()

    chips = ctk.CTkFrame(container, fg_color="transparent")
    chips.pack(fill="x", padx=6, pady=(4, 4))

    page_att_strip = ctk.CTkFrame(container, fg_color="transparent")
    in_row = ctk.CTkFrame(container, fg_color="transparent")
    in_row.pack(fill="x", padx=8, pady=(2, 8))
    prompt_entry = ctk.CTkEntry(
        in_row,
        height=38,
        placeholder_text="Ask Hermes OS AI to inspect, fix, research online, or help build AstraCradle updates...",
    )
    prompt_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
    prompt_entry.bind("<Control-v>", _paste_page_item)
    prompt_entry.bind("<Control-V>", _paste_page_item)

    active_cancel: list[threading.Event | None] = [None]

    def _stop_generation() -> None:
        if active_cancel[0] is not None:
            active_cancel[0].set()
            app.show_toast("Stopping AI generation...")

    stop_btn = ctk.CTkButton(
        in_row,
        text="⏹ Stop",
        width=76,
        height=38,
        fg_color=p.bg_card,
        font=f["small_bold"],
        command=_stop_generation,
    )

    def _send_prompt(override: str | None = None) -> None:
        q = (override if override is not None else prompt_entry.get()).strip()
        if not q and page_attachments:
            q = "Please inspect the attached image/file(s) and help me with what I am working on."
        if not q:
            return
        if override is None:
            prompt_entry.delete(0, "end")
        att_now = list(page_attachments)
        page_attachments.clear()
        _refresh_attachment_strip(page_att_strip, page_attachments, p, f, before_widget=in_row)
        spec = hm.resolve_provider_spec(cfg)
        _reload_chat(thinking=f"Prompt: \"{q[:160]}\"\nInspecting & streaming response via {spec['model']}...")

        cancel_ev = threading.Event()
        active_cancel[0] = cancel_ev
        last_stream_ts = [0.0]
        steps_seen: list[str] = []

        def _on_stream(partial: str) -> None:
            import time as _t

            txt = _clean_bubble_markdown(partial)
            if not txt.strip():
                return
            now_t = _t.monotonic()
            if (now_t - last_stream_ts[0]) < 0.14:
                return
            last_stream_ts[0] = now_t
            app.after(0, lambda t=txt: _reload_chat(thinking=t, use_cache=True))

        def _on_step(step_txt: str) -> None:
            steps_seen.append(step_txt)
            joined = "  |  ".join(steps_seen[-5:])
            app.after(0, lambda j=joined: _reload_chat(thinking=f"Steps: {j}", use_cache=True))

        def _worker() -> None:
            res = hm.run_prompt(
                q,
                cfg,
                session_id=session_id,
                attachments=att_now or None,
                stream_cb=_on_stream,
                step_cb=_on_step,
                cancel_event=cancel_ev,
            )

            def _ui() -> None:
                active_cancel[0] = None
                app._hermes_status_cache = hm.get_status(cfg)
                if res.ok:
                    render_hermes_page(app)
                    if res.skill_created:
                        app.show_toast("Hermes auto-learned a new reusable SKILL.md!")
                else:
                    _reload_chat(err=res.error)
                    app.show_toast(f"Hermes error: {res.error}")

            app.after(0, _ui)

        threading.Thread(target=_worker, daemon=True).start()

    _build_quick_chips(chips, _send_prompt, p, f, cols=4)
    prompt_entry.bind("<Return>", lambda _e: _send_prompt())
    ctk.CTkButton(
        in_row,
        text="Send & Act",
        width=130,
        height=38,
        fg_color=p.accent_cyan,
        text_color="#071216",
        font=f["body_bold"],
        command=_send_prompt,
    ).pack(side="right")
    stop_btn.pack(side="right", padx=(0, 6))


def render_hermes_page(app: AstraCradleApp) -> None:
    parent = app.hermes_scroll
    for child in parent.winfo_children():
        child.destroy()

    p = app.palette
    f = app.fonts
    cfg = app.config_obj
    hm = app.hermes_mgr

    # Use cached recommendation/status immediately so UI never freezes; refresh async in background
    rec: HermesHardwareRecommendation | None = getattr(app, "_hermes_rec_cache", None)
    status: HermesSetupStatus | None = getattr(app, "_hermes_status_cache", None)
    if rec is None or status is None:
        def _bg_probe() -> None:
            r = recommend_hermes_models()
            hm.auto_integrate_downloaded_gpu_model(cfg, start_runtime=True)
            s = hm.get_status(cfg)
            app._hermes_rec_cache = r
            app._hermes_status_cache = s
            app.after(0, lambda: render_hermes_page(app) if app.current_nav == "hermes" else None)

        threading.Thread(target=_bg_probe, daemon=True).start()

    # Full-Screen Chat Focus Mode — fills the entire Hermes OS AI canvas
    if bool(getattr(app, "_hermes_fullscreen_chat", False)):
        fs_bar = ctk.CTkFrame(parent, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12)
        fs_bar.pack(fill="x", padx=4, pady=(2, 8))
        spec_fs = hm.resolve_provider_spec(cfg)
        gpu_fs = hm.compute_gpu_throttle_plan(cfg)
        ctk.CTkLabel(
            fs_bar,
            text=(
                f"Hermes OS AI · Full-Screen Action Chat   |   Model: {spec_fs['model']}   ·   "
                f"GPU: {gpu_fs['target_gpu_pct']}% (Reserve {gpu_fs['vram_reserve_mb']} MB VRAM)"
            ),
            font=f["small_bold"],
            text_color=p.accent_cyan,
        ).pack(side="left", padx=14, pady=10)
        ctk.CTkButton(
            fs_bar,
            text="Show Setup & All Tabs",
            height=28,
            fg_color=p.bg_elevated,
            hover_color=p.border_strong,
            font=f["small"],
            command=lambda: (setattr(app, "_hermes_fullscreen_chat", False), render_hermes_page(app)),
        ).pack(side="right", padx=12, pady=8)

        fs_card = ctk.CTkFrame(parent, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12)
        fs_card.pack(fill="both", expand=True, padx=4, pady=(0, 6))
        _build_chat_workspace(fs_card, app, is_fullscreen=True)
        return

    # ── Top Banner: Hermes OS AI Status & Hardware Match ─────────────────────
    hdr = ctk.CTkFrame(parent, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12)
    hdr.pack(fill="x", padx=4, pady=(2, 10))

    top_row = ctk.CTkFrame(hdr, fg_color="transparent")
    top_row.pack(fill="x", padx=14, pady=(12, 6))
    ctk.CTkLabel(
        top_row,
        text="Hermes OS AI Agent · Autonomous Assistant, Skills, Memory & Cron",
        font=f["section"],
        text_color=p.accent_cyan,
    ).pack(side="left")

    def _refresh_async() -> None:
        app._hermes_rec_cache = None
        app._hermes_status_cache = None
        render_hermes_page(app)

    ctk.CTkButton(
        top_row,
        text="Refresh Hardware & Status",
        height=28,
        corner_radius=7,
        fg_color=p.bg_elevated,
        hover_color=p.border_strong,
        font=f["small"],
        command=_refresh_async,
    ).pack(side="right")

    if rec is not None and status is not None:
        status_col = p.accent_jade if status.ready else p.accent_brass
        pc_prof = hm.get_pc_usage_profile()
        ctk.CTkLabel(
            hdr,
            text=(
                f"Status: {status.summary}   ·   Hardware: {rec.gpu_name} ({rec.gpu_vram_gb:.1f} GB VRAM, {rec.system_ram_gb:.0f} GB RAM)\n"
                f"Learned PC Profile: {pc_prof.get('samples', 0)} ticks · {len(pc_prof.get('apps') or {})} apps · "
                f"{len(hm.list_learned_games())} learned games · {len(pc_prof.get('remote_tasks') or [])} remote task(s)"
            ),
            font=f["small"],
            text_color=status_col,
            justify="left",
            wraplength=app._content_wrap(80),
        ).pack(anchor="w", padx=14, pady=(0, 6))

        # Collapsible setup toggle so ready users land directly on the AI Action Assistant chat!
        setup_open = getattr(app, "_hermes_setup_expanded", None)
        if setup_open is None:
            setup_open = not bool(status.ready)
            app._hermes_setup_expanded = setup_open

        def _toggle_setup_card() -> None:
            app._hermes_setup_expanded = not bool(getattr(app, "_hermes_setup_expanded", False))
            render_hermes_page(app)

        ctk.CTkButton(
            top_row,
            text=("Hide Setup & Models ▲" if setup_open else "Model, GPU & Setup ▾"),
            height=28,
            corner_radius=7,
            fg_color=p.bg_elevated,
            hover_color=p.border_strong,
            font=f["small"],
            command=_toggle_setup_card,
        ).pack(side="right", padx=(0, 6))
    else:
        ctk.CTkLabel(
            hdr,
            text="Probing GPU VRAM, Ollama runtime, and Hermes workspace...",
            font=f["small"],
            text_color=p.text_muted,
        ).pack(anchor="w", padx=14, pady=(0, 10))

    # ── Automated 1-Click Setup & Provider Configuration (collapsible when ready) ─
    setup_expanded = bool(getattr(app, "_hermes_setup_expanded", True if status is None else (not status.ready)))
    setup_card = ctk.CTkFrame(parent, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12)
    if setup_expanded:
        setup_card.pack(fill="x", padx=4, pady=(0, 10))

    ctk.CTkLabel(
        setup_card,
        text="1-Click Automated Setup & Model Selection",
        font=f["body_bold"],
        text_color=p.text_primary,
    ).pack(anchor="w", padx=14, pady=(10, 6))

    prov_labels, prov_map = _build_provider_options()
    cur_local_only = bool(getattr(cfg, "hermes_local_only", False))
    cur_mode = "local_gpu" if cur_local_only else str(getattr(cfg, "hermes_mode", "free_cloud"))
    cur_prov = "ollama" if cur_local_only else str(getattr(cfg, "hermes_provider", "openrouter_free"))
    default_lbl = next(
        (lbl for lbl, (m, pr) in prov_map.items() if m == cur_mode and pr == cur_prov),
        prov_labels[1] if len(prov_labels) > 1 else prov_labels[0],
    )
    prov_var = tk.StringVar(value=default_lbl)
    local_only_var = tk.BooleanVar(value=cur_local_only)
    ctx_var = tk.StringVar(value=str(int(getattr(cfg, "hermes_context_tokens", 32768) or 32768)))

    row1 = ctk.CTkFrame(setup_card, fg_color="transparent")
    row1.pack(fill="x", padx=14, pady=4)
    ctk.CTkLabel(row1, text="Backend / Provider:", font=f["small"], width=130, anchor="w").pack(side="left")

    row2 = ctk.CTkFrame(setup_card, fg_color="transparent")
    row2.pack(fill="x", padx=14, pady=4)
    ctk.CTkLabel(row2, text="Model Name:", font=f["small"], width=130, anchor="w").pack(side="left")
    model_ent = ctk.CTkEntry(
        row2,
        width=220,
        placeholder_text="Leave blank for auto-picked optimal",
    )
    if getattr(cfg, "hermes_model", ""):
        model_ent.insert(0, str(cfg.hermes_model))
    model_ent.pack(side="left", padx=(4, 10))

    ctk.CTkLabel(row2, text="API Key (if Cloud):", font=f["small"]).pack(side="left", padx=(4, 4))
    key_ent = ctk.CTkEntry(
        row2,
        width=240,
        show="*",
        placeholder_text="Paste key (not needed for Local-Only)",
    )
    key_ent.pack(side="left", fill="x", expand=True)

    row3 = ctk.CTkFrame(setup_card, fg_color="transparent")
    row3.pack(fill="x", padx=14, pady=4)
    ctk.CTkLabel(row3, text="Endpoint URL:", font=f["small"], width=130, anchor="w").pack(side="left")
    url_ent = ctk.CTkEntry(
        row3,
        placeholder_text="Optional override (http://127.0.0.1:11434/v1 for Ollama)",
    )
    if getattr(cfg, "hermes_base_url", ""):
        url_ent.insert(0, str(cfg.hermes_base_url))
    url_ent.pack(side="left", fill="x", expand=True, padx=(4, 0))

    def _on_prov_changed(choice: str) -> None:
        mode, prov = prov_map.get(choice, ("free_cloud", "openrouter_free"))
        model_ent.delete(0, "end")
        url_ent.delete(0, "end")
        if mode == "local_gpu":
            local_only_var.set(True)
            if rec is not None:
                model_ent.insert(0, rec.recommended_local_model)
                ctx_var.set(str(rec.recommended_context_tokens))
            url_ent.insert(0, "http://127.0.0.1:11434/v1")
        elif prov in FREE_CLOUD_PROVIDERS:
            local_only_var.set(False)
            spec = FREE_CLOUD_PROVIDERS[prov]
            model_ent.insert(0, spec["model"])
            url_ent.insert(0, spec["base_url"])
        elif prov in CUSTOM_API_PROVIDERS:
            local_only_var.set(False)
            spec = CUSTOM_API_PROVIDERS[prov]
            model_ent.insert(0, spec["model"])
            url_ent.insert(0, spec["base_url"])

    ctk.CTkOptionMenu(
        row1,
        values=prov_labels,
        variable=prov_var,
        width=390,
        command=_on_prov_changed,
    ).pack(side="left", padx=(4, 10))
    ctk.CTkLabel(row1, text="Context:", font=f["small"]).pack(side="left", padx=(8, 4))
    ctk.CTkOptionMenu(row1, values=["16384", "32768", "65536"], variable=ctx_var, width=100).pack(side="left")

    ctk.CTkSwitch(
        setup_card,
        text="Strict 100% Local-Only Mode (Offline Ollama only — never connect to cloud APIs)",
        variable=local_only_var,
    ).pack(anchor="w", padx=14, pady=(4, 2))

    perm_labels = [spec["label"] for spec in HERMES_PERMISSION_LEVELS.values()]
    perm_key_by_lbl = {spec["label"]: k for k, spec in HERMES_PERMISSION_LEVELS.items()}
    cur_perm_key = str(getattr(cfg, "hermes_permission_level", "safe_user") or "safe_user")
    if cur_perm_key not in HERMES_PERMISSION_LEVELS:
        cur_perm_key = "safe_user"
    perm_var = tk.StringVar(value=HERMES_PERMISSION_LEVELS[cur_perm_key]["label"])

    perm_row = ctk.CTkFrame(setup_card, fg_color="transparent")
    perm_row.pack(fill="x", padx=14, pady=(4, 2))
    ctk.CTkLabel(perm_row, text="AI Access Level:", font=f["small"], width=130, anchor="w").pack(side="left")
    ctk.CTkOptionMenu(perm_row, values=perm_labels, variable=perm_var, width=480).pack(side="left", padx=(4, 0))

    gpu_labels = [spec["label"] for spec in HERMES_GPU_MODES.values()]
    gpu_key_by_lbl = {spec["label"]: k for k, spec in HERMES_GPU_MODES.items()}
    cur_gpu_key = str(getattr(cfg, "hermes_gpu_limit_mode", "game_adaptive") or "game_adaptive")
    if cur_gpu_key not in HERMES_GPU_MODES:
        cur_gpu_key = "game_adaptive"
    gpu_var = tk.StringVar(value=HERMES_GPU_MODES[cur_gpu_key]["label"])

    gpu_row = ctk.CTkFrame(setup_card, fg_color="transparent")
    gpu_row.pack(fill="x", padx=14, pady=(4, 2))
    ctk.CTkLabel(gpu_row, text="AI GPU Budget:", font=f["small"], width=130, anchor="w").pack(side="left")
    ctk.CTkOptionMenu(gpu_row, values=gpu_labels, variable=gpu_var, width=480).pack(side="left", padx=(4, 0))

    gpu_plan = hm.compute_gpu_throttle_plan(cfg)
    ctk.CTkLabel(
        setup_card,
        text=(
            f"GPU Throttle State: {gpu_plan['reason']}  "
            f"(Learned games: {gpu_plan['learned_games_count']} · VRAM reserve: {gpu_plan['vram_reserve_mb']} MB)"
        ),
        font=f["caption"],
        text_color=p.accent_cyan,
        wraplength=app._content_wrap(80),
        justify="left",
    ).pack(anchor="w", padx=14, pady=(2, 2))

    prog_lbl = ctk.CTkLabel(setup_card, text="", font=f["caption"], text_color=p.text_muted)
    prog_lbl.pack(anchor="w", padx=14, pady=(4, 0))
    prog_bar = ctk.CTkProgressBar(setup_card, height=8)
    prog_bar.set(0.0)
    prog_bar.pack(fill="x", padx=14, pady=(4, 8))

    btn_row = ctk.CTkFrame(setup_card, fg_color="transparent")
    btn_row.pack(fill="x", padx=14, pady=(0, 12))

    def _apply_form_to_config() -> None:
        is_local_only = bool(local_only_var.get())
        mode, prov = prov_map.get(prov_var.get(), ("free_cloud", "openrouter_free"))
        if is_local_only:
            mode, prov = "local_gpu", "ollama"
        cfg.hermes_local_only = is_local_only
        cfg.hermes_mode = mode
        cfg.hermes_provider = prov
        cfg.hermes_model = model_ent.get().strip()
        cfg.hermes_base_url = url_ent.get().strip()
        sel_perm = perm_key_by_lbl.get(perm_var.get(), "safe_user")
        cfg.hermes_permission_level = sel_perm
        cfg.hermes_allow_shell = sel_perm in ("standard_user", "sudo_with_prompt")
        cfg.hermes_gpu_limit_mode = gpu_key_by_lbl.get(gpu_var.get(), "game_adaptive")
        cfg.hermes_vram_reserve_mb = max(512, int(getattr(cfg, "hermes_vram_reserve_mb", 1024) or 1024))
        try:
            cfg.hermes_context_tokens = int(ctx_var.get())
        except Exception:
            cfg.hermes_context_tokens = 32768
        app.cfg_mgr.save(cfg)

    def _run_auto_setup(pull_model: bool = True) -> None:
        _apply_form_to_config()
        api_key = key_ent.get().strip()
        prog_lbl.configure(text="Starting automated Hermes OS AI setup...", text_color=p.accent_cyan)
        prog_bar.set(0.08)

        def _worker() -> None:
            def _on_prog(step: str, pct: float, detail: str) -> None:
                app.after(
                    0,
                    lambda s=step, x=pct, d=detail: (
                        prog_bar.set(max(0.0, min(1.0, x))),
                        prog_lbl.configure(text=f"{s}: {d}", text_color=p.accent_cyan),
                    ),
                )

            ok, summary, st = hm.install_and_setup_hermes(
                cfg,
                api_key=api_key,
                install_upstream_pkg=True,
                pull_local_model=pull_model,
                progress_cb=_on_prog,
            )
            app._hermes_status_cache = st

            def _done() -> None:
                prog_bar.set(1.0 if ok else 0.7)
                prog_lbl.configure(text=summary, text_color=(p.accent_jade if ok else p.accent_brass))
                app.show_toast("Hermes OS AI setup complete!" if ok else "Hermes setup finished with notes.")
                render_hermes_page(app)

            app.after(0, _done)

        threading.Thread(target=_worker, daemon=True).start()

    ctk.CTkButton(
        btn_row,
        text="Auto-Install & Set Up Hermes AI Now",
        height=32,
        fg_color=p.accent_jade,
        text_color="#071510",
        font=f["body_bold"],
        command=lambda: _run_auto_setup(pull_model=True),
    ).pack(side="left", padx=(0, 8))

    def _pick_optimal_local() -> None:
        local_only_var.set(True)
        prov_var.set(prov_labels[0])
        _on_prov_changed(prov_labels[0])
        _run_auto_setup(pull_model=True)

    ctk.CTkButton(
        btn_row,
        text="1-Click Local-Only GPU Setup (Offline)",
        height=32,
        fg_color=p.accent_cyan,
        text_color="#071216",
        font=f["body_bold"],
        command=_pick_optimal_local,
    ).pack(side="left", padx=(0, 8))

    def _save_only() -> None:
        _apply_form_to_config()
        if key_ent.get().strip():
            spec = hm.resolve_provider_spec(cfg)
            hm.save_env_vars({spec["env_var"]: key_ent.get().strip()})
        hm.ensure_hermes_home(cfg)
        app._hermes_status_cache = hm.get_status(cfg)
        app.show_toast("Saved Hermes AI configuration & credentials.")
        render_hermes_page(app)

    ctk.CTkButton(
        btn_row,
        text="Save Settings Only",
        height=32,
        fg_color=p.bg_elevated,
        hover_color=p.border_strong,
        font=f["small"],
        command=_save_only,
    ).pack(side="left")

    # ── Native Workspace Tabs: Chat, Memory & Skills, Cron, Safety & Gateway ─
    tabs = ctk.CTkTabview(parent, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12)
    tabs.pack(fill="both", expand=True, padx=4, pady=(0, 6))
    t_chat = tabs.add("Action Assistant Chat")
    t_mem = tabs.add("Learned PC Usage & Memory")
    t_cron = tabs.add("Cron & Subagents")
    t_safe = tabs.add("Safety, Identity & GPU Budget")

    # ── Tab 1: Fleshed-Out Chat, Identity Selector & Tool Execution ─────────
    _build_chat_workspace(t_chat, app, is_fullscreen=False)

    # ── Tab 2: Learned Whole-PC Usage, Memory & Skills ───────────────────────
    hm.ensure_hermes_home(cfg)
    prof = hm.get_pc_usage_profile()
    top_apps = sorted((prof.get("apps") or {}).items(), key=lambda kv: int(kv[1].get("hits", 0)), reverse=True)[:8]
    top_apps_str = ", ".join(f"{k} ({v.get('category', 'app')}, {v.get('hits', 0)}x)" for k, v in top_apps) or "Still observing..."
    rem_str = ", ".join(f"{k} ({v}x)" for k, v in (prof.get("remote_services_seen") or {}).items()) or "None active yet"
    learned_summary_txt = (
        f"Whole-PC Usage Learned ({prof.get('samples', 0)} telemetry ticks):\n"
        f"  • CPU avg/peak: {prof.get('avg_cpu_pct', 0)}% / {prof.get('peak_cpu_pct', 0)}%  ·  "
        f"RAM avg/peak: {prof.get('avg_ram_pct', 0)}% / {prof.get('peak_ram_pct', 0)}%  ·  "
        f"Peak GPU/VRAM: {prof.get('peak_gpu_pct', 0)}% / {prof.get('peak_vram_mb', 0)} MB\n"
        f"  • Top Frequent Apps: {top_apps_str}\n"
        f"  • Remote Continuity & Tasks: {rem_str}  ·  {len(prof.get('paired_devices_snapshot') or [])} paired device(s), {len(prof.get('remote_tasks') or [])} remote task(s)"
    )
    ctk.CTkLabel(
        t_mem,
        text=learned_summary_txt,
        font=f["caption"],
        text_color=p.accent_cyan,
        justify="left",
        wraplength=760,
    ).pack(anchor="w", padx=8, pady=(6, 6))

    mem_box = ctk.CTkTextbox(t_mem, height=110, font=f["small"], fg_color=p.bg_elevated)
    ctk.CTkLabel(t_mem, text="Persistent Memory (MEMORY.md):", font=f["body_bold"]).pack(anchor="w", padx=8, pady=(6, 2))
    mem_box.pack(fill="x", padx=8, pady=(0, 6))
    mem_box.insert("1.0", hm.memory_path.read_text(encoding="utf-8", errors="ignore"))

    def _save_mem_md() -> None:
        hm.memory_path.write_text(mem_box.get("1.0", "end").strip() + "\n", encoding="utf-8")
        app.show_toast("Saved MEMORY.md")

    ctk.CTkButton(t_mem, text="Save MEMORY.md", width=130, height=26, fg_color=p.accent_jade, text_color="#071510", command=_save_mem_md).pack(anchor="w", padx=8, pady=(0, 8))

    ctk.CTkLabel(t_mem, text="Learned Skills (skills/*/SKILL.md):", font=f["body_bold"]).pack(anchor="w", padx=8, pady=(2, 2))
    skills_box = ctk.CTkTextbox(t_mem, height=130, font=f["small"], fg_color=p.bg_elevated)
    skills_box.pack(fill="both", expand=True, padx=8, pady=(0, 6))
    for sk in hm.list_skills():
        skills_box.insert("end", f"=== {sk['slug']} ({sk['path']}) ===\n{sk['content']}\n\n")
    skills_box.configure(state="disabled")

    # ── Tab 3: Cron & Subagents ──────────────────────────────────────────────
    cron_form = ctk.CTkFrame(t_cron, fg_color="transparent")
    cron_form.pack(fill="x", padx=8, pady=6)
    cname_var = tk.StringVar(value="Hourly Health Check")
    cprompt_var = tk.StringVar(value="Check system status and note any high thermals or RAM bloat in MEMORY.md.")
    cint_var = tk.StringVar(value="60")

    ctk.CTkEntry(cron_form, textvariable=cname_var, width=160, placeholder_text="Job name").pack(side="left", padx=(0, 6))
    ctk.CTkEntry(cron_form, textvariable=cprompt_var, placeholder_text="Prompt to run on schedule").pack(side="left", fill="x", expand=True, padx=(0, 6))
    ctk.CTkEntry(cron_form, textvariable=cint_var, width=60).pack(side="left", padx=(0, 6))

    def _add_cron() -> None:
        try:
            mins = int(cint_var.get())
        except Exception:
            mins = 60
        hm.add_cron_job(cname_var.get(), cprompt_var.get(), mins)
        cfg.hermes_cron_enabled = True
        app.cfg_mgr.save(cfg)
        render_hermes_page(app)

    ctk.CTkButton(cron_form, text="Add Cron Task", width=110, fg_color=p.accent_cyan, text_color="#071216", command=_add_cron).pack(side="right")

    for job in hm.list_cron_jobs():
        jrow = ctk.CTkFrame(t_cron, fg_color=p.bg_elevated, corner_radius=8)
        jrow.pack(fill="x", padx=8, pady=3)
        ctk.CTkLabel(
            jrow,
            text=f"{job.get('name')} · every {job.get('interval_minutes')} min · {job.get('last_status', 'pending')}",
            font=f["small"],
        ).pack(side="left", padx=10, pady=6)
        ctk.CTkButton(
            jrow,
            text="Delete",
            width=70,
            height=24,
            fg_color=p.accent_coral,
            command=lambda jid=job.get("job_id", ""): (hm.delete_cron_job(jid), render_hermes_page(app)),
        ).pack(side="right", padx=8, pady=4)

    # ── Tab 4: Safety, Identity & Messaging Gateways ────────────────────────
    id_card = ctk.CTkFrame(t_safe, fg_color=p.bg_elevated, corner_radius=9)
    id_card.pack(fill="x", padx=10, pady=(8, 4))
    ctk.CTkLabel(
        id_card,
        text="Assistant Personality & Selectable Identities (Standard + 10 Unique Personas)",
        font=f["body_bold"],
        text_color=p.accent_cyan,
    ).pack(anchor="w", padx=10, pady=(8, 4))

    safe_p_labels, safe_p_by_lbl = _persona_options()
    cur_safe_p = hm.get_active_persona(cfg)
    safe_p_var = tk.StringVar(value=cur_safe_p["label"])
    safe_p_desc = ctk.CTkLabel(
        id_card,
        text=(
            f"Traits: {cur_safe_p['traits']}\n"
            f"Mannerisms: {cur_safe_p['mannerisms']}\n"
            f"Greeting: \"{cur_safe_p['greeting']}\""
        ),
        font=f["caption"],
        text_color=p.text_secondary,
        wraplength=700,
        justify="left",
    )

    def _save_persona_tab(_choice=None) -> None:
        pk = safe_p_by_lbl.get(safe_p_var.get(), "astra_standard")
        cfg.hermes_persona = pk
        app.cfg_mgr.save(cfg)
        hm.write_config_yaml(cfg)
        np = hm.get_active_persona(cfg)
        safe_p_desc.configure(
            text=(
                f"Traits: {np['traits']}\n"
                f"Mannerisms: {np['mannerisms']}\n"
                f"Greeting: \"{np['greeting']}\""
            )
        )
        app.show_toast(f"Switched AI identity to {np['name']} ({np['badge']})")
        render_hermes_page(app)

    ctk.CTkOptionMenu(id_card, values=safe_p_labels, variable=safe_p_var, width=520, command=_save_persona_tab).pack(anchor="w", padx=10, pady=(0, 4))
    safe_p_desc.pack(anchor="w", padx=12, pady=(0, 8))

    req_app_var = tk.BooleanVar(value=bool(getattr(cfg, "hermes_require_approval", True)))
    cron_en_var = tk.BooleanVar(value=bool(getattr(cfg, "hermes_cron_enabled", False)))
    web_search_var = tk.BooleanVar(value=bool(getattr(cfg, "hermes_allow_web_search", True)))
    safe_perm_var = tk.StringVar(value=HERMES_PERMISSION_LEVELS[cur_perm_key]["label"])
    perm_desc_lbl = ctk.CTkLabel(
        t_safe,
        text=HERMES_PERMISSION_LEVELS[cur_perm_key]["summary"],
        font=f["caption"],
        text_color=p.text_secondary,
        wraplength=680,
        justify="left",
    )

    def _save_safety(_choice=None) -> None:
        sel_p = perm_key_by_lbl.get(safe_perm_var.get(), "safe_user")
        cfg.hermes_permission_level = sel_p
        cfg.hermes_allow_shell = sel_p in ("standard_user", "sudo_with_prompt")
        cfg.hermes_require_approval = bool(req_app_var.get())
        cfg.hermes_cron_enabled = bool(cron_en_var.get())
        cfg.hermes_allow_web_search = bool(web_search_var.get())
        perm_desc_lbl.configure(text=HERMES_PERMISSION_LEVELS[sel_p]["summary"])
        perm_var.set(safe_perm_var.get())
        app.cfg_mgr.save(cfg)
        hm.write_config_yaml(cfg)
        app.show_toast("Updated Hermes OS permission level & safety settings.")

    ctk.CTkLabel(t_safe, text="How Much System Control Should Hermes OS AI Have?", font=f["body_bold"], text_color=p.accent_cyan).pack(anchor="w", padx=10, pady=(10, 4))
    ctk.CTkOptionMenu(t_safe, values=perm_labels, variable=safe_perm_var, width=520, command=_save_safety).pack(anchor="w", padx=10, pady=(0, 4))
    perm_desc_lbl.pack(anchor="w", padx=12, pady=(0, 8))

    ctk.CTkSwitch(t_safe, text="Allow AI Web Search, Online Docs Research & Skill URL Fetch (keeps local GPU model offline)", variable=web_search_var, command=_save_safety).pack(anchor="w", padx=10, pady=4)
    ctk.CTkSwitch(t_safe, text="Require explicit approval / dry-run before risky changes (Recommended)", variable=req_app_var, command=_save_safety).pack(anchor="w", padx=10, pady=4)
    ctk.CTkSwitch(t_safe, text="Enable scheduled Hermes Cron tasks while AstraCradle runs", variable=cron_en_var, command=_save_safety).pack(anchor="w", padx=10, pady=4)
    ctk.CTkSwitch(t_safe, text="Speak Hermes AI replies aloud offline (TTS via espeak-ng / spd-say)", variable=voice_tts_var, command=_save_safety).pack(anchor="w", padx=10, pady=4)

    # GPU Usage Limit & Learned Games Manager
    gpu_card = ctk.CTkFrame(t_safe, fg_color=p.bg_elevated, corner_radius=9)
    gpu_card.pack(fill="x", padx=10, pady=8)
    ctk.CTkLabel(
        gpu_card,
        text="AI GPU Usage Cap & Learned Game Mode (~1 GB VRAM Reserve)",
        font=f["body_bold"],
        text_color=p.accent_jade,
    ).pack(anchor="w", padx=10, pady=(8, 4))

    safe_gpu_var = tk.StringVar(value=HERMES_GPU_MODES[cur_gpu_key]["label"])
    gpu_desc_lbl = ctk.CTkLabel(
        gpu_card,
        text=HERMES_GPU_MODES[cur_gpu_key]["summary"],
        font=f["caption"],
        text_color=p.text_secondary,
        wraplength=680,
        justify="left",
    )

    def _save_gpu_mode(_choice=None) -> None:
        sel_g = gpu_key_by_lbl.get(safe_gpu_var.get(), "game_adaptive")
        cfg.hermes_gpu_limit_mode = sel_g
        gpu_var.set(safe_gpu_var.get())
        gpu_desc_lbl.configure(text=HERMES_GPU_MODES[sel_g]["summary"])
        app.cfg_mgr.save(cfg)
        hm.write_config_yaml(cfg)
        app.show_toast(f"AI GPU budget updated: {sel_g}")
        render_hermes_page(app)

    ctk.CTkOptionMenu(gpu_card, values=gpu_labels, variable=safe_gpu_var, width=540, command=_save_gpu_mode).pack(anchor="w", padx=10, pady=(0, 4))
    gpu_desc_lbl.pack(anchor="w", padx=12, pady=(0, 6))

    learned_list = hm.list_learned_games()
    learned_summary = (
        "Learned Games & Peak VRAM: "
        + (", ".join(f"{g.get('display_name')} ({g.get('peak_vram_mb', 0):.0f} MB, {g.get('launch_count', 1)}x)" for g in learned_list[:8]))
        if learned_list
        else "Learned Games: None recorded yet — Optimized Game Mode learns your games automatically whenever you launch them, or you can add a process below."
    )
    ctk.CTkLabel(
        gpu_card,
        text=learned_summary,
        font=f["caption"],
        text_color=p.text_primary,
        wraplength=680,
        justify="left",
    ).pack(anchor="w", padx=12, pady=(0, 6))

    add_g_row = ctk.CTkFrame(gpu_card, fg_color="transparent")
    add_g_row.pack(fill="x", padx=10, pady=(0, 8))
    add_g_ent = ctk.CTkEntry(add_g_row, width=260, placeholder_text="Optional: add game process (e.g. cs2, cyberpunk, javaw)")
    add_g_ent.pack(side="left", padx=(0, 8))

    def _add_learned_game_ui() -> None:
        val = add_g_ent.get().strip()
        if not val:
            return
        hm.add_learned_game(val, user_pinned=True)
        app.show_toast(f"Added '{val}' to Hermes learned games.")
        render_hermes_page(app)

    ctk.CTkButton(
        add_g_row,
        text="+ Learn Game",
        width=110,
        height=28,
        fg_color=p.accent_cyan,
        text_color="#071216",
        command=_add_learned_game_ui,
    ).pack(side="left", padx=(0, 8))
    if learned_list:
        def _clear_last_game() -> None:
            hm.remove_learned_game(str(learned_list[0].get("key", "")))
            render_hermes_page(app)

        ctk.CTkButton(
            add_g_row,
            text=f"Remove '{learned_list[0].get('display_name', '')[:16]}'",
            width=140,
            height=28,
            fg_color=p.bg_card,
            command=_clear_last_game,
        ).pack(side="left")

    gw_row = ctk.CTkFrame(t_safe, fg_color=p.bg_elevated, corner_radius=9)
    gw_row.pack(fill="x", padx=10, pady=10)
    ctk.CTkLabel(
        gw_row,
        text="Messaging Gateways (Telegram / Discord / Slack / WhatsApp / Email via upstream `hermes gateway`):",
        font=f["body_bold"],
    ).pack(anchor="w", padx=10, pady=(8, 4))

    gw_inputs = ctk.CTkFrame(gw_row, fg_color="transparent")
    gw_inputs.pack(fill="x", padx=10, pady=4)
    tg_ent = ctk.CTkEntry(gw_inputs, show="*", placeholder_text="TELEGRAM_BOT_TOKEN (optional)")
    tg_ent.pack(side="left", fill="x", expand=True, padx=(0, 6))
    dc_ent = ctk.CTkEntry(gw_inputs, show="*", placeholder_text="DISCORD_BOT_TOKEN (optional)")
    dc_ent.pack(side="left", fill="x", expand=True)

    gw_btns = ctk.CTkFrame(gw_row, fg_color="transparent")
    gw_btns.pack(fill="x", padx=10, pady=(4, 10))

    def _start_gw() -> None:
        ups = {}
        if tg_ent.get().strip():
            ups["TELEGRAM_BOT_TOKEN"] = tg_ent.get().strip()
        if dc_ent.get().strip():
            ups["DISCORD_BOT_TOKEN"] = dc_ent.get().strip()
        if ups:
            hm.save_env_vars(ups)
        ok, msg = hm.start_gateway(cfg)
        app.show_toast(msg)
        app._hermes_status_cache = hm.get_status(cfg)
        if ok:
            render_hermes_page(app)

    ctk.CTkButton(gw_btns, text="Save Tokens & Start Gateway", fg_color=p.accent_jade, text_color="#071510", command=_start_gw).pack(side="left", padx=(0, 8))
    ctk.CTkButton(gw_btns, text="Stop Gateway", fg_color=p.accent_coral, command=lambda: (hm.stop_gateway(), app.show_toast("Stopped Hermes gateway."))).pack(side="left")
