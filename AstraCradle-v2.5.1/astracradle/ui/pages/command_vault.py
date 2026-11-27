"""Command Vault (SQLite), Dependency Library (Bash/Fish + Permission Auto-Setup) & Offline Help/Troubleshooter UI."""

from __future__ import annotations

import tkinter as tk
from typing import TYPE_CHECKING

import customtkinter as ctk

from ...core.i18n import get_help_categories, search_help_topics

if TYPE_CHECKING:
    from ..app import AstraCradleApp


def render_command_vault_page(app: AstraCradleApp) -> None:
    p = app.palette
    for w in app.cmd_vault_scroll.winfo_children():
        w.destroy()

    db = app.command_db
    cat_filter = str(getattr(app, "_cmd_vault_cat", "All"))
    search_q = str(getattr(app, "_cmd_vault_search", ""))
    cmds = db.list_commands(category=cat_filter, search=search_q)
    categories = db.get_categories()

    # 1. Header Card
    hdr = ctk.CTkFrame(
        app.cmd_vault_scroll, fg_color=p.bg_card, border_width=1, border_color=p.accent_cyan, corner_radius=12
    )
    hdr.pack(fill="x", pady=(0, 8))
    top_r = ctk.CTkFrame(hdr, fg_color="transparent")
    top_r.pack(fill="x", padx=14, pady=(10, 4))
    ctk.CTkLabel(
        top_r,
        text="Command Vault, Dependency Library & Offline Troubleshooter",
        font=app.fonts["section"],
        text_color=p.accent_cyan,
    ).pack(side="left")
    ctk.CTkLabel(
        top_r,
        text=f"{len(db.list_commands())} saved command(s) · Bash & Fish ready",
        font=app.fonts["mono_small"],
        text_color=p.accent_jade,
    ).pack(side="right")
    ctk.CTkLabel(
        hdr,
        text="Find and copy full Linux Bash, Linux Fish, macOS & Windows scripts from SQLite, auto-install & configure companion dependencies (with your permission), or search offline solutions.",
        font=app.fonts["small"],
        text_color=p.text_secondary,
        wraplength=app._content_wrap(80),
        justify="left",
    ).pack(anchor="w", padx=14, pady=(0, 10))

    # 2. Filter + Dropdown Picker Card
    pick_card = ctk.CTkFrame(
        app.cmd_vault_scroll, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
    )
    pick_card.pack(fill="x", pady=(0, 8))

    filt_r = ctk.CTkFrame(pick_card, fg_color="transparent")
    filt_r.pack(fill="x", padx=14, pady=(10, 4))
    ctk.CTkLabel(filt_r, text="Category:", font=app.fonts["small"], text_color=p.text_muted).pack(side="left", padx=(0, 6))
    cat_var = tk.StringVar(value=cat_filter if cat_filter in categories else "All")
    ctk.CTkOptionMenu(
        filt_r,
        values=categories,
        variable=cat_var,
        width=185,
        command=lambda v: app._on_cmd_vault_filter(v, search_var.get()),
    ).pack(side="left", padx=(0, 10))

    ctk.CTkLabel(filt_r, text="Search:", font=app.fonts["small"], text_color=p.text_muted).pack(side="left", padx=(0, 6))
    search_ent = ctk.CTkEntry(filt_r, width=210, placeholder_text="fish, bash, sunshine, adb, tailscale...")
    if search_q:
        search_ent.insert(0, search_q)
    search_ent.pack(side="left", padx=(0, 6))
    ctk.CTkButton(
        filt_r,
        text="Filter",
        width=68,
        height=28,
        fg_color=p.bg_elevated,
        command=lambda: app._on_cmd_vault_filter(cat_var.get(), search_ent.get()),
    ).pack(side="left")

    # Command Dropdown Row
    drop_r = ctk.CTkFrame(pick_card, fg_color="transparent")
    drop_r.pack(fill="x", padx=14, pady=(4, 6))
    ctk.CTkLabel(drop_r, text="Select Command:", font=app.fonts["body_bold"], text_color=p.text_primary).pack(side="left", padx=(0, 8))

    titles = [f"{c.title} [{c.platform}]" for c in cmds] or ["(No matching commands)"]
    title_to_rec = {f"{c.title} [{c.platform}]": c for c in cmds}

    sel_id = getattr(app, "_cmd_vault_selected_id", "")
    active_rec = next((c for c in cmds if c.cmd_id == sel_id), cmds[0] if cmds else None)
    default_label = f"{active_rec.title} [{active_rec.platform}]" if active_rec else titles[0]
    cmd_menu_var = tk.StringVar(value=default_label)
    app._cmd_vault_dropdown_var = cmd_menu_var

    def _on_pick(label: str) -> None:
        rec = title_to_rec.get(label)
        if rec is not None:
            app._cmd_vault_selected_id = rec.cmd_id
            render_command_vault_page(app)

    ctk.CTkOptionMenu(
        drop_r,
        values=titles,
        variable=cmd_menu_var,
        width=480,
        dynamic_resizing=False,
        command=_on_pick,
    ).pack(side="left", fill="x", expand=True, padx=(0, 8))

    if active_rec is not None:
        meta_txt = f"Category: {active_rec.category}  ·  Platform: {active_rec.platform}  ·  {'Built-in' if active_rec.is_builtin else 'Custom'}  ·  Copied {active_rec.use_count}x"
        ctk.CTkLabel(
            pick_card,
            text=meta_txt,
            font=app.fonts["mono_small"],
            text_color=p.accent_brass,
        ).pack(anchor="w", padx=14, pady=(2, 2))
        if active_rec.description:
            ctk.CTkLabel(
                pick_card,
                text=active_rec.description,
                font=app.fonts["small"],
                text_color=p.text_secondary,
                wraplength=app._content_wrap(80),
                justify="left",
            ).pack(anchor="w", padx=14, pady=(0, 4))

        box = ctk.CTkTextbox(pick_card, height=96, font=app.fonts["mono_small"], fg_color=p.bg_elevated)
        box.pack(fill="x", padx=14, pady=(2, 6))
        box.insert("1.0", active_rec.script)
        box.configure(state="disabled")

        act_r = ctk.CTkFrame(pick_card, fg_color="transparent")
        act_r.pack(fill="x", padx=14, pady=(0, 10))
        ctk.CTkButton(
            act_r,
            text="Copy Command Script",
            height=30,
            fg_color=p.accent_jade,
            text_color="#071510",
            font=app.fonts["body_bold"],
            command=lambda rec=active_rec: app._on_copy_vault_command(rec.cmd_id, rec.script, rec.title),
        ).pack(side="left")
        if not active_rec.is_builtin:
            ctk.CTkButton(
                act_r,
                text="Delete Custom Command",
                height=30,
                fg_color=p.bg_elevated,
                hover_color=p.accent_coral,
                font=app.fonts["small"],
                command=lambda cid=active_rec.cmd_id: app._on_delete_vault_command(cid),
            ).pack(side="right")

    # 3. Dependency Library & Permission-Gated Auto-Installer
    dcard = ctk.CTkFrame(
        app.cmd_vault_scroll, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
    )
    dcard.pack(fill="x", pady=(0, 8))
    dtop = ctk.CTkFrame(dcard, fg_color="transparent")
    dtop.pack(fill="x", padx=14, pady=(10, 4))
    ctk.CTkLabel(
        dtop,
        text="Dependency Library (Sunshine, Moonlight, Tailscale, ADB, BlueZ & Full Bash/Fish Setup)",
        font=app.fonts["body_bold"],
        text_color=p.accent_jade,
    ).pack(side="left")

    shell_pref = str(getattr(app, "_dep_shell_pref", app.dep_lib.detect_user_shell()))
    shell_var = tk.StringVar(value=shell_pref)

    def _on_shell_chg(val: str) -> None:
        app._dep_shell_pref = val
        render_command_vault_page(app)

    ctk.CTkOptionMenu(dtop, values=["bash", "fish"], variable=shell_var, width=90, command=_on_shell_chg).pack(
        side="right", padx=(6, 0)
    )
    ctk.CTkLabel(dtop, text="Linux Shell:", font=app.fonts["small"], text_color=p.text_muted).pack(side="right")

    perm_r = ctk.CTkFrame(dcard, fg_color="transparent")
    perm_r.pack(fill="x", padx=14, pady=(2, 6))
    perm_var = tk.BooleanVar(value=bool(getattr(app.config_obj, "consent_auto_install_deps", False)))
    ctk.CTkCheckBox(
        perm_r,
        text="Grant permission to auto-install & configure missing companion programs",
        variable=perm_var,
        command=lambda: app._on_toggle_dep_install_consent(perm_var.get()),
    ).pack(side="left")
    ctk.CTkButton(
        perm_r,
        text=f"Copy Full {shell_pref.upper()} Stack",
        height=26,
        fg_color=p.bg_elevated,
        font=app.fonts["small"],
        command=lambda s=shell_pref: app._on_copy_text_only(
            app.dep_lib.full_stack_script(s), f"Full {s.upper()} dependency stack script"
        ),
    ).pack(side="right", padx=(6, 0))
    ctk.CTkButton(
        perm_r,
        text="Auto-Install All Missing",
        height=26,
        fg_color=p.accent_jade,
        text_color="#071510",
        font=app.fonts["body_bold"],
        command=lambda s=shell_pref: app._on_install_all_missing_deps(s),
    ).pack(side="right")

    for st in app.dep_lib.inspect_all(shell_pref=shell_pref):
        drow = ctk.CTkFrame(dcard, fg_color=p.bg_elevated, corner_radius=7)
        drow.pack(fill="x", padx=14, pady=2)
        badge = "INSTALLED" if st.installed else "MISSING"
        cmd_preview = st.fish_cmd if shell_pref == "fish" else st.bash_cmd
        ctk.CTkLabel(
            drow,
            text=f"[{badge}] {st.name} — {st.role}\n  Cmd ({shell_pref}): {cmd_preview[:130]}{'...' if len(cmd_preview) > 130 else ''}",
            font=app.fonts["mono_small"],
            text_color=p.accent_jade if st.installed else p.accent_brass,
            justify="left",
            wraplength=app._content_wrap(260),
        ).pack(side="left", padx=10, pady=4)
        ctk.CTkButton(
            drow,
            text="Copy Cmd",
            width=76,
            height=24,
            fg_color=p.bg_card,
            command=lambda c=cmd_preview, n=st.name: app._on_copy_text_only(c, f"{n} ({shell_pref}) command"),
        ).pack(side="right", padx=4, pady=4)
        ctk.CTkButton(
            drow,
            text="Verify Setup" if st.installed else "Install & Setup",
            width=102,
            height=24,
            fg_color=p.accent_cyan,
            text_color="#071216",
            command=lambda did=st.dep_id, s=shell_pref: app._on_install_single_dep(did, s),
        ).pack(side="right", padx=4, pady=4)
    ctk.CTkFrame(dcard, height=6, fg_color="transparent").pack()

    # 4. Offline Problem Troubleshooter & Help Menu (No Web Search Needed)
    hcard = ctk.CTkFrame(
        app.cmd_vault_scroll, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
    )
    hcard.pack(fill="x", pady=(0, 8))
    ctk.CTkLabel(
        hcard,
        text="Offline Problem Troubleshooter & Quick Help Menu (No Web Search Required)",
        font=app.fonts["body_bold"],
        text_color=p.accent_brass,
    ).pack(anchor="w", padx=14, pady=(10, 4))

    help_cats = get_help_categories()
    h_cat = str(getattr(app, "_vault_help_cat", "All"))
    h_q = str(getattr(app, "_vault_help_q", ""))
    topics = search_help_topics(h_q, category=h_cat)

    hf_r = ctk.CTkFrame(hcard, fg_color="transparent")
    hf_r.pack(fill="x", padx=14, pady=3)
    hcat_var = tk.StringVar(value=h_cat if h_cat in help_cats else "All")
    hq_var = tk.StringVar(value=h_q)
    ctk.CTkOptionMenu(
        hf_r,
        values=help_cats,
        variable=hcat_var,
        width=225,
        command=lambda v: app._on_vault_help_filter(v, hq_var.get()),
    ).pack(side="left", padx=(0, 8))
    ctk.CTkEntry(
        hf_r,
        textvariable=hq_var,
        width=230,
        placeholder_text="Keyword search: sunshine, fish, bluetooth, 429, adb...",
    ).pack(side="left", padx=(0, 8), fill="x", expand=True)
    ctk.CTkButton(
        hf_r,
        text="Search Offline KB",
        width=125,
        height=28,
        fg_color=p.bg_elevated,
        command=lambda: app._on_vault_help_filter(hcat_var.get(), hq_var.get()),
    ).pack(side="left")

    topic_titles = [t.title for t in topics] or ["(No matching help topic)"]
    t_map = {t.title: t for t in topics}
    sel_tid = getattr(app, "_vault_help_tid", "")
    active_topic = next((t for t in topics if t.topic_id == sel_tid), topics[0] if topics else None)
    t_var = tk.StringVar(value=active_topic.title if active_topic else topic_titles[0])

    def _on_pick_topic(lbl: str) -> None:
        top = t_map.get(lbl)
        if top is not None:
            app._vault_help_tid = top.topic_id
            render_command_vault_page(app)

    td_r = ctk.CTkFrame(hcard, fg_color="transparent")
    td_r.pack(fill="x", padx=14, pady=4)
    ctk.CTkLabel(td_r, text="Problem / Topic:", font=app.fonts["small"], text_color=p.text_muted).pack(side="left", padx=(0, 8))
    ctk.CTkOptionMenu(td_r, values=topic_titles, variable=t_var, width=480, dynamic_resizing=False, command=_on_pick_topic).pack(
        side="left", fill="x", expand=True
    )

    if active_topic is not None:
        ctk.CTkLabel(
            hcard,
            text=f"{active_topic.summary}\n\n{active_topic.details}",
            font=app.fonts["small"],
            text_color=p.text_secondary,
            justify="left",
            wraplength=app._content_wrap(80),
        ).pack(anchor="w", padx=14, pady=(4, 10))

    # 5. Add & Save New Command Card
    add_card = ctk.CTkFrame(
        app.cmd_vault_scroll, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
    )
    add_card.pack(fill="x", pady=(0, 8))
    ctk.CTkLabel(
        add_card,
        text="Save a New Command or Install Script to Database",
        font=app.fonts["body_bold"],
        text_color=p.accent_cyan,
    ).pack(anchor="w", padx=14, pady=(10, 4))

    row1 = ctk.CTkFrame(add_card, fg_color="transparent")
    row1.pack(fill="x", padx=14, pady=3)
    new_cat = tk.StringVar(value="Custom")
    new_plat = tk.StringVar(value="All")
    new_title_ent = ctk.CTkEntry(row1, width=260, placeholder_text="Command Title (e.g. Pair My New Tablet)")
    new_title_ent.pack(side="left", padx=(0, 8), fill="x", expand=True)
    ctk.CTkOptionMenu(
        row1,
        values=categories,
        variable=new_cat,
        width=165,
    ).pack(side="left", padx=(0, 8))
    ctk.CTkOptionMenu(
        row1,
        values=["All", "Linux", "macOS", "Windows", "iOS / watchOS", "Android"],
        variable=new_plat,
        width=130,
    ).pack(side="left")

    new_desc_ent = ctk.CTkEntry(
        add_card,
        placeholder_text="Short description / usage note (optional)",
    )
    new_desc_ent.pack(fill="x", padx=14, pady=3)

    script_txt = ctk.CTkTextbox(add_card, height=74, font=app.fonts["mono_small"], fg_color=p.bg_elevated)
    script_txt.pack(fill="x", padx=14, pady=3)

    def _save_new() -> None:
        raw_script = script_txt.get("1.0", "end").strip()
        app._on_add_vault_command(new_title_ent.get(), new_cat.get(), new_plat.get(), raw_script, new_desc_ent.get())

    ctk.CTkButton(
        add_card,
        text="Save Command to Database",
        height=30,
        fg_color=p.accent_cyan,
        text_color="#071216",
        font=app.fonts["body_bold"],
        command=_save_new,
    ).pack(anchor="e", padx=14, pady=(4, 10))
