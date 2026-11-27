"""Hermes OS AI Agent UI Page — GPU-Matched Local Models, Free Cloud APIs, 1-Click Auto-Setup, Chat, Skills, Memory & Cron."""

from __future__ import annotations

import threading
import tkinter as tk
from typing import TYPE_CHECKING

import customtkinter as ctk

from ...core.hermes_agent import (
    CUSTOM_API_PROVIDERS,
    FREE_CLOUD_PROVIDERS,
    HERMES_GPU_MODES,
    HERMES_PERMISSION_LEVELS,
    HermesHardwareRecommendation,
    HermesSetupStatus,
    recommend_hermes_models,
)

if TYPE_CHECKING:
    from ..app import AstraCradleApp


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
    win.geometry("760x560")
    win.configure(fg_color=p.bg_canvas)
    try:
        win.transient(app)
    except Exception:
        pass

    hdr = ctk.CTkFrame(win, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=10)
    hdr.pack(fill="x", padx=12, pady=(12, 6))
    ctk.CTkLabel(
        hdr,
        text="Ask AI Assistant · Diagnose, Fix & Automate Safely",
        font=f["section"],
        text_color=p.accent_cyan,
    ).pack(side="left", padx=12, pady=10)

    def _open_full_hub() -> None:
        try:
            win.destroy()
        except Exception:
            pass
        app.switch_nav("hermes")

    ctk.CTkButton(
        hdr,
        text="Open Full AI Hub ->",
        height=28,
        fg_color=p.bg_elevated,
        hover_color=p.border_strong,
        font=f["small"],
        command=_open_full_hub,
    ).pack(side="right", padx=10, pady=8)

    spec = hm.resolve_provider_spec(cfg)
    gpu_plan = hm.compute_gpu_throttle_plan(cfg)
    ctk.CTkLabel(
        win,
        text=(
            f"Model: {spec['model']} ({spec['provider']})  ·  "
            f"GPU Budget: {gpu_plan['target_gpu_pct']}% (Reserve {gpu_plan['vram_reserve_mb']} MB VRAM)  ·  "
            f"Learned Games: {gpu_plan['learned_games_count']}"
        ),
        font=f["caption"],
        text_color=p.text_secondary,
    ).pack(anchor="w", padx=16, pady=(0, 4))

    session_id = str(getattr(app, "_hermes_session_id", "default"))
    transcript = ctk.CTkTextbox(win, font=f["body"], fg_color=p.bg_elevated, wrap="word")
    transcript.pack(fill="both", expand=True, padx=12, pady=4)

    def _reload_transcript() -> None:
        transcript.configure(state="normal")
        transcript.delete("1.0", "end")
        msgs = hm.get_session_messages(session_id, limit=30)
        if not msgs:
            transcript.insert(
                "end",
                "Tell me what you need fixed or checked on your PC!\n"
                "Examples:\n"
                "  • \"My PC feels sluggish — scan and safely clean caches & restrain background CPU hogs.\"\n"
                "  • \"Check my Wi-Fi channels and optimize if congested.\"\n"
                "  • \"Check my paired mobile/remote devices and AstraBeam status.\"\n"
                "  • \"What have you learned about how I use my PC?\"\n",
            )
        else:
            for m in msgs:
                who = "You" if m["role"] == "user" else "Hermes AI"
                transcript.insert("end", f"[{who}] {m['content']}\n")
                if m.get("tool_trace"):
                    transcript.insert("end", f"  └─ Actions/Tools: {m['tool_trace']}\n")
                transcript.insert("end", "\n")
        transcript.configure(state="disabled")
        transcript.see("end")

    _reload_transcript()

    chips = ctk.CTkFrame(win, fg_color="transparent")
    chips.pack(fill="x", padx=12, pady=(2, 4))
    entry = ctk.CTkEntry(win, placeholder_text="Ask or tell AstraCradle AI to fix, check, or optimize something...")

    def _send(override: str | None = None) -> None:
        q = (override if override is not None else entry.get()).strip()
        if not q:
            return
        if override is None:
            entry.delete(0, "end")
        transcript.configure(state="normal")
        transcript.insert("end", f"[You] {q}\n[Hermes AI] Inspecting & running safe AstraCradle tools...\n")
        transcript.configure(state="disabled")
        transcript.see("end")

        def _worker() -> None:
            res = hm.run_prompt(q, cfg, session_id=session_id)

            def _done() -> None:
                app._hermes_status_cache = hm.get_status(cfg)
                if win.winfo_exists():
                    _reload_transcript()
                if not res.ok:
                    app.show_toast(f"Hermes AI: {res.error}")
                elif app.current_nav == "hermes":
                    render_hermes_page(app)

            app.after(0, _done)

        threading.Thread(target=_worker, daemon=True).start()

    for lbl_txt, q_txt in (
        ("Fix Slow PC (Safe)", "Diagnose my PC, safely clean junk/caches with astra_fix_safe(dry_run=false), and restrain background CPU hogs if needed."),
        ("Optimize Wi-Fi", "Check my Wi-Fi channels and apply optimization if a cleaner channel is available."),
        ("Startup & Hogs", "Audit my startup entries and high-CPU processes, and suggest or safely trim unneeded bloat."),
        ("Remote Tasks", "Check my paired mobile/remote devices, Tailscale/ADB/AstraBeam status, and recent remote tasks."),
        ("What AI Learned", "Summarize what you have learned about my whole-PC usage, top apps, games, and remote tasks."),
    ):
        ctk.CTkButton(
            chips,
            text=lbl_txt,
            height=26,
            fg_color=p.bg_elevated,
            hover_color=p.accent_cyan,
            font=f["caption"],
            command=lambda qt=q_txt: _send(qt),
        ).pack(side="left", padx=(0, 6))

    in_row = ctk.CTkFrame(win, fg_color="transparent")
    in_row.pack(fill="x", padx=12, pady=(2, 12))
    entry.pack_forget()
    entry = ctk.CTkEntry(in_row, height=34, placeholder_text="Tell AstraCradle AI what to check, fix, or automate...")
    entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
    entry.bind("<Return>", lambda _e: _send())
    ctk.CTkButton(
        in_row,
        text="Send & Act",
        width=120,
        height=34,
        fg_color=p.accent_cyan,
        text_color="#071216",
        font=f["body_bold"],
        command=_send,
    ).pack(side="right")
    entry.focus_set()


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
        status_col = p.accent_jade if status.ready else p.accent_amber
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
    row3 = ctk.CTkFrame(setup_card, fg_color="transparent")

    ctk.CTkLabel(row2, text="Model Name:", font=f["small"], width=130, anchor="w").pack(side="left")
    model_ent = ctk.CTkEntry(
        row2,
        width=220,
        placeholder_text="Leave blank for auto-picked optimal",
    )
    if getattr(cfg, "hermes_model", ""):
        model_ent.insert(0, str(cfg.hermes_model))

    ctk.CTkLabel(row2, text="API Key (if Cloud):", font=f["small"]).pack(side="left", padx=(4, 4))
    key_ent = ctk.CTkEntry(
        row2,
        width=240,
        show="*",
        placeholder_text="Paste key (not needed for Local-Only)",
    )

    ctk.CTkLabel(row3, text="Endpoint URL:", font=f["small"], width=130, anchor="w").pack(side="left")
    url_ent = ctk.CTkEntry(
        row3,
        placeholder_text="Optional override (http://127.0.0.1:11434/v1 for Ollama)",
    )
    if getattr(cfg, "hermes_base_url", ""):
        url_ent.insert(0, str(cfg.hermes_base_url))

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

    row2.pack(fill="x", padx=14, pady=4)
    model_ent.pack(side="left", padx=(4, 10), before=key_ent)
    # Re-pack cleanly so order is Model Label -> model_ent -> Key Label -> key_ent
    for w_child in row2.winfo_children():
        w_child.pack_forget()
    row2.winfo_children()[0].pack(side="left")
    model_ent.pack(side="left", padx=(4, 10))
    row2.winfo_children()[2].pack(side="left", padx=(4, 4))
    key_ent.pack(side="left", fill="x", expand=True)

    row3.pack(fill="x", padx=14, pady=4)
    url_ent.pack(side="left", fill="x", expand=True, padx=(4, 0))

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
                prog_lbl.configure(text=summary, text_color=(p.accent_jade if ok else p.accent_amber))
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
    t_safe = tabs.add("Safety, GPU Budget & Gateways")

    # ── Tab 1: Chat & Tool Execution ─────────────────────────────────────────
    session_id = str(getattr(app, "_hermes_session_id", "default"))
    chat_top = ctk.CTkFrame(t_chat, fg_color="transparent")
    chat_top.pack(fill="x", padx=8, pady=(4, 4))

    sessions = hm.list_sessions(limit=20)
    sess_ids = [s["session_id"] for s in sessions] or ["default"]
    if session_id not in sess_ids:
        sess_ids.insert(0, session_id)
    sess_var = tk.StringVar(value=session_id)

    ctk.CTkLabel(chat_top, text="Session:", font=f["small"]).pack(side="left", padx=(0, 6))

    def _switch_sess(val: str) -> None:
        app._hermes_session_id = val
        render_hermes_page(app)

    ctk.CTkOptionMenu(chat_top, values=sess_ids, variable=sess_var, width=200, command=_switch_sess).pack(side="left", padx=(0, 8))

    def _new_session() -> None:
        import time as _t

        app._hermes_session_id = f"session_{int(_t.time())}"
        render_hermes_page(app)

    ctk.CTkButton(chat_top, text="+ New Session", width=110, height=26, fg_color=p.bg_elevated, command=_new_session).pack(side="left")

    transcript = ctk.CTkTextbox(t_chat, height=260, font=f["body"], fg_color=p.bg_elevated, wrap="word")
    transcript.pack(fill="both", expand=True, padx=8, pady=4)
    msgs = hm.get_session_messages(session_id, limit=40)
    if not msgs:
        transcript.insert(
            "end",
            "Hermes OS AI is ready.\n"
            "Try asking:\n"
            "  • Diagnose my PC right now and explain any bottlenecks.\n"
            "  • Check my Wi-Fi channels and GPU temperature.\n"
            "  • Preview safe cache cleanup without deleting personal files.\n"
            "  • Remember my preference and turn this workflow into a reusable skill.\n",
        )
    else:
        for m in msgs:
            who = "You" if m["role"] == "user" else "Hermes AI"
            transcript.insert("end", f"[{who}] {m['content']}\n")
            if m.get("tool_trace"):
                transcript.insert("end", f"  └─ Tools: {m['tool_trace']}\n")
            transcript.insert("end", "\n")
    transcript.configure(state="disabled")
    transcript.see("end")

    # Quick-action prompt chips
    chips = ctk.CTkFrame(t_chat, fg_color="transparent")
    chips.pack(fill="x", padx=8, pady=(2, 4))
    prompt_entry = ctk.CTkEntry(t_chat, placeholder_text="Ask Hermes OS AI to inspect, diagnose, automate, or build a skill...")

    def _send_prompt(override: str | None = None) -> None:
        q = (override if override is not None else prompt_entry.get()).strip()
        if not q:
            return
        if override is None:
            prompt_entry.delete(0, "end")
        transcript.configure(state="normal")
        transcript.insert("end", f"[You] {q}\n[Hermes AI] Thinking & calling OS tools...\n")
        transcript.configure(state="disabled")
        transcript.see("end")

        def _worker() -> None:
            res = hm.run_prompt(q, cfg, session_id=session_id)

            def _ui() -> None:
                app._hermes_status_cache = hm.get_status(cfg)
                render_hermes_page(app)
                if not res.ok:
                    app.show_toast(f"Hermes error: {res.error}")
                elif res.skill_created:
                    app.show_toast("Hermes auto-learned a new reusable SKILL.md!")

            app.after(0, _ui)

        threading.Thread(target=_worker, daemon=True).start()

    for label_txt, q_txt in (
        ("Diagnose PC", "Run a deep system scan and check live CPU/GPU/RAM status, then summarize in plain English."),
        ("Fix Slow PC (Safe)", "Diagnose bottlenecks, safely clean caches with astra_fix_safe(dry_run=false), and restrain background CPU hogs if needed."),
        ("Optimize Wi-Fi", "Check my Wi-Fi channels and optimize if a cleaner channel is available."),
        ("Startup & Hogs", "Audit my startup entries and high-CPU processes, and explain or trim safe bloat."),
        ("Remote Tasks", "Check paired mobile/remote devices, Tailscale/ADB/AstraBeam status, and recent remote tasks."),
        ("Self-Heal & Undo", "Run AstraCradle 14-invariant self-heal repair and list any recent rollback/undo items."),
    ):
        ctk.CTkButton(
            chips,
            text=label_txt,
            height=24,
            fg_color=p.bg_elevated,
            hover_color=p.accent_cyan,
            font=f["caption"],
            command=lambda qt=q_txt: _send_prompt(qt),
        ).pack(side="left", padx=(0, 6))

    in_row = ctk.CTkFrame(t_chat, fg_color="transparent")
    in_row.pack(fill="x", padx=8, pady=(2, 6))
    prompt_entry.pack_forget()
    prompt_entry = ctk.CTkEntry(in_row, placeholder_text="Ask Hermes OS AI anything about your PC or tasks...")
    prompt_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
    prompt_entry.bind("<Return>", lambda _e: _send_prompt())
    ctk.CTkButton(
        in_row,
        text="Send to Hermes",
        width=130,
        height=32,
        fg_color=p.accent_cyan,
        text_color="#071216",
        font=f["body_bold"],
        command=_send_prompt,
    ).pack(side="right")

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
            fg_color=p.accent_rose,
            command=lambda jid=job.get("job_id", ""): (hm.delete_cron_job(jid), render_hermes_page(app)),
        ).pack(side="right", padx=8, pady=4)

    # ── Tab 4: Safety & Messaging Gateways ───────────────────────────────────
    req_app_var = tk.BooleanVar(value=bool(getattr(cfg, "hermes_require_approval", True)))
    cron_en_var = tk.BooleanVar(value=bool(getattr(cfg, "hermes_cron_enabled", False)))
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
        perm_desc_lbl.configure(text=HERMES_PERMISSION_LEVELS[sel_p]["summary"])
        perm_var.set(safe_perm_var.get())
        app.cfg_mgr.save(cfg)
        hm.write_config_yaml(cfg)
        app.show_toast("Updated Hermes OS permission level & safety settings.")

    ctk.CTkLabel(t_safe, text="How Much System Control Should Hermes OS AI Have?", font=f["body_bold"], text_color=p.accent_cyan).pack(anchor="w", padx=10, pady=(10, 4))
    ctk.CTkOptionMenu(t_safe, values=perm_labels, variable=safe_perm_var, width=520, command=_save_safety).pack(anchor="w", padx=10, pady=(0, 4))
    perm_desc_lbl.pack(anchor="w", padx=12, pady=(0, 8))

    ctk.CTkSwitch(t_safe, text="Require explicit approval / dry-run before risky changes (Recommended)", variable=req_app_var, command=_save_safety).pack(anchor="w", padx=10, pady=6)
    ctk.CTkSwitch(t_safe, text="Enable scheduled Hermes Cron tasks while AstraCradle runs", variable=cron_en_var, command=_save_safety).pack(anchor="w", padx=10, pady=6)

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
    ctk.CTkButton(gw_btns, text="Stop Gateway", fg_color=p.accent_rose, command=lambda: (hm.stop_gateway(), app.show_toast("Stopped Hermes gateway."))).pack(side="left")
