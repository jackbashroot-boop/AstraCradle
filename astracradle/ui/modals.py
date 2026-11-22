"""Interactive popup modals for Consent, Automation Prompts, Custom Scripts, and Crash Recovery."""

from __future__ import annotations

import tkinter as tk
from typing import Callable

import customtkinter as ctk

from ..core.config import AppConfig, SITUATION_PROFILES
from ..core.i18n import HelpTopic, search_help_topics
from ..core.models import CrashPostMortem, ProcessRecommendation
from ..core.safeguard_plus import ProcessExplanation
from ..theme import ThemePalette


class ConsentAutomationDialog(ctk.CTkToplevel):
    """First-load permission & full PC discovery onboarding wizard."""

    def __init__(
        self,
        master,
        palette: ThemePalette,
        fonts: dict,
        config: AppConfig,
        on_save: Callable[[dict[str, bool]], None],
        first_run: bool = False,
    ) -> None:
        super().__init__(master)
        self.palette = palette
        self.on_save = on_save
        self.first_run = first_run
        self.title("AstraCradle — Permission to Scan Your Computer")
        dw, dh = 690, 580
        try:
            master.update_idletasks()
            mx = master.winfo_rootx()
            my = master.winfo_rooty()
            mw = max(690, master.winfo_width() or 1200)
            mh = max(580, master.winfo_height() or 800)
            px = max(20, mx + (mw - dw) // 2)
            py = max(20, my + (mh - dh) // 2)
            self.geometry(f"{dw}x{dh}+{px}+{py}")
        except Exception:
            self.geometry(f"{dw}x{dh}")
        self.minsize(560, 460)
        self.configure(fg_color=palette.bg_canvas)
        self.transient(master)

        scroll = ctk.CTkScrollableFrame(
            self, fg_color=palette.bg_card, border_width=1, border_color=palette.accent_cyan, corner_radius=12
        )
        scroll.pack(fill="both", expand=True, padx=16, pady=16)

        heading = (
            "Welcome to AstraCradle — Allow Full Local PC Scan?"
            if first_run
            else "Local System Permissions & Full PC Discovery"
        )
        ctk.CTkLabel(
            scroll,
            text=heading,
            font=fonts["section"],
            text_color=palette.accent_cyan,
            wraplength=590,
            justify="left",
        ).pack(anchor="w", padx=18, pady=(16, 4))
        ctk.CTkLabel(
            scroll,
            text=(
                "AstraCradle pulls diagnostic data strictly locally on your computer (zero cloud uploads). "
                "When granted permission, AstraCradle checks everything within its bounds — CPU/GPU/RAM/Storage/SMART, "
                "installed apps, startup/autoruns, ghost drivers, DPC latency, network sockets/DNS, and crash clues — "
                "to build your live PC baseline."
            ),
            font=fonts["small"],
            text_color=palette.text_secondary,
            wraplength=590,
            justify="left",
        ).pack(anchor="w", padx=18, pady=(0, 12))

        self.v_tel = tk.BooleanVar(value=config.consent_telemetry)
        self.v_proc = tk.BooleanVar(value=config.consent_process_scan)
        self.v_apps = tk.BooleanVar(value=config.consent_installed_apps)
        self.v_bloat = tk.BooleanVar(value=config.consent_bloat_scan)
        self.v_crash = tk.BooleanVar(value=config.consent_crash_diagnostics)
        self.v_auto = tk.BooleanVar(value=config.consent_auto_remediate)

        opts = [
            (self.v_tel, "Hardware, Telemetry & Latency (CPU, GPU/VRAM, RAM, SMART Disk, DNS, DPC/IRQs)"),
            (self.v_proc, "Running Processes & AI Hunter (Spot idle AI chatbots & background CPU hogs)"),
            (self.v_apps, "Installed Apps, Autoruns & Ghost Drivers (Startup items, tasks & orphan drivers)"),
            (self.v_bloat, "Storage Bloat & AI/Dev Model Vault (Reclaimable caches & large dev caches)"),
            (self.v_crash, "Crash & 48h Driver Post-Mortem (Diagnose hangs/reboots locally)"),
            (self.v_auto, "Automate Safe Optimizations (Auto-quarantine stale caches & trim logs silently)"),
        ]
        for var, txt in opts:
            ctk.CTkCheckBox(
                scroll, text=txt, variable=var, font=fonts["small"], text_color=palette.text_primary
            ).pack(anchor="w", padx=18, pady=6)

        btn_row = ctk.CTkFrame(scroll, fg_color="transparent")
        btn_row.pack(fill="x", padx=18, pady=(16, 14))
        btn_row.grid_columnconfigure((0, 1, 2), weight=1)
        ctk.CTkButton(
            btn_row,
            text="Grant & Run Full PC Scan",
            height=36,
            fg_color=palette.accent_jade,
            hover_color=palette.accent_jade_hover,
            text_color="#071510",
            font=fonts["body_bold"],
            command=self._accept_all,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ctk.CTkButton(
            btn_row,
            text="Scan Selected Only",
            height=36,
            fg_color=palette.accent_cyan,
            hover_color=palette.accent_cyan_hover,
            text_color="#071217",
            font=fonts["body_bold"],
            command=self._save_selected,
        ).grid(row=0, column=1, sticky="ew", padx=3)
        ctk.CTkButton(
            btn_row,
            text="Decline (Minimal)",
            height=36,
            fg_color=palette.bg_elevated,
            hover_color=palette.border_strong,
            font=fonts["small"],
            command=self._decline_minimal,
        ).grid(row=0, column=2, sticky="ew", padx=(6, 0))

    def _accept_all(self) -> None:
        self.v_tel.set(True)
        self.v_proc.set(True)
        self.v_apps.set(True)
        self.v_bloat.set(True)
        self.v_crash.set(True)
        self.v_auto.set(True)
        self._save_selected(run_full_scan=True)

    def _decline_minimal(self) -> None:
        self.v_tel.set(False)
        self.v_proc.set(False)
        self.v_apps.set(False)
        self.v_bloat.set(False)
        self.v_crash.set(False)
        self.v_auto.set(False)
        self._save_selected(run_full_scan=False)

    def _save_selected(self, run_full_scan: bool | None = None) -> None:
        should_scan = run_full_scan if run_full_scan is not None else any(
            (self.v_tel.get(), self.v_proc.get(), self.v_apps.get(), self.v_bloat.get())
        )
        self.on_save(
            {
                "consent_telemetry": bool(self.v_tel.get()),
                "consent_process_scan": bool(self.v_proc.get()),
                "consent_installed_apps": bool(self.v_apps.get()),
                "consent_bloat_scan": bool(self.v_bloat.get()),
                "consent_crash_diagnostics": bool(self.v_crash.get()),
                "consent_auto_remediate": bool(self.v_auto.get()),
                "_trigger_full_discovery": bool(should_scan),
            }
        )
        self.destroy()


class FullDiscoveryResultDialog(ctk.CTkToplevel):
    """Displays the full local PC baseline discovered after user permission."""

    def __init__(self, master, palette: ThemePalette, fonts: dict, baseline) -> None:
        super().__init__(master)
        self.title("AstraCradle — Full Local PC Baseline Complete")
        dw, dh = 700, 540
        try:
            master.update_idletasks()
            mx = master.winfo_rootx()
            my = master.winfo_rooty()
            mw = max(700, master.winfo_width() or 1200)
            mh = max(540, master.winfo_height() or 800)
            px = max(20, mx + (mw - dw) // 2)
            py = max(20, my + (mh - dh) // 2)
            self.geometry(f"{dw}x{dh}+{px}+{py}")
        except Exception:
            self.geometry(f"{dw}x{dh}")
        self.minsize(540, 420)
        self.configure(fg_color=palette.bg_canvas)
        self.transient(master)

        scroll = ctk.CTkScrollableFrame(
            self, fg_color=palette.bg_card, border_width=1, border_color=palette.accent_jade, corner_radius=12
        )
        scroll.pack(fill="both", expand=True, padx=16, pady=16)

        ctk.CTkLabel(
            scroll,
            text=f"Full Local PC Scan Complete — {baseline.hostname} ({baseline.health_score}/100)",
            font=fonts["section"],
            text_color=palette.accent_jade,
            wraplength=600,
            justify="left",
        ).pack(anchor="w", padx=16, pady=(14, 4))
        ctk.CTkLabel(
            scroll,
            text=f"Checked {len(baseline.subsystems_checked)} local subsystems at {baseline.discovered_at} (100% offline/local).",
            font=fonts["mono_small"],
            text_color=palette.text_muted,
            wraplength=600,
            justify="left",
        ).pack(anchor="w", padx=16, pady=(0, 10))

        for ln in baseline.summary_lines:
            box = ctk.CTkFrame(scroll, fg_color=palette.bg_elevated, corner_radius=8)
            box.pack(fill="x", padx=16, pady=3)
            ctk.CTkLabel(
                box, text=ln, font=fonts["small"], text_color=palette.text_primary, wraplength=580, justify="left", anchor="w"
            ).pack(fill="x", padx=12, pady=6)

        if baseline.smart_disks:
            ctk.CTkLabel(scroll, text="Disk & SMART Health:", font=fonts["body_bold"], text_color=palette.accent_cyan).pack(
                anchor="w", padx=16, pady=(10, 4)
            )
            for sd in baseline.smart_disks[:4]:
                ctk.CTkLabel(
                    scroll,
                    text=f"  • [{sd.get('status', 'OK').upper()}] {sd.get('summary', '')}",
                    font=fonts["mono_small"],
                    text_color=palette.text_secondary,
                    wraplength=580,
                    justify="left",
                ).pack(anchor="w", padx=16, pady=1)

        ctk.CTkButton(
            scroll,
            text="Explore Command Deck & Playbook",
            height=36,
            fg_color=palette.accent_cyan,
            hover_color=palette.accent_cyan_hover,
            text_color="#071217",
            font=fonts["body_bold"],
            command=self.destroy,
        ).pack(fill="x", padx=16, pady=(14, 14))


class AutomationPromptDialog(ctk.CTkToplevel):
    """Popup window asking the user if they want AstraCradle to automate a detected maintenance task."""

    def __init__(
        self,
        master,
        palette: ThemePalette,
        fonts: dict,
        title_txt: str,
        summary_txt: str,
        on_decision: Callable[[str], None],
    ) -> None:
        super().__init__(master)
        self.on_decision = on_decision
        self.title("AstraCradle — Smart Automation Assistant")
        self.geometry("540x290")
        self.configure(fg_color=palette.bg_canvas)
        self.transient(master)

        card = ctk.CTkFrame(
            self, fg_color=palette.bg_card, border_width=1, border_color=palette.accent_cyan, corner_radius=12
        )
        card.pack(fill="both", expand=True, padx=16, pady=16)

        ctk.CTkLabel(card, text=title_txt, font=fonts["section"], text_color=palette.accent_cyan).pack(
            anchor="w", padx=16, pady=(14, 6)
        )
        ctk.CTkLabel(
            card,
            text=summary_txt,
            font=fonts["body"],
            text_color=palette.text_secondary,
            wraplength=470,
            justify="left",
        ).pack(anchor="w", padx=16, pady=(0, 18))

        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(0, 14))
        ctk.CTkButton(
            row,
            text="Run & Automate Always",
            height=34,
            fg_color=palette.accent_jade,
            hover_color=palette.accent_jade_hover,
            text_color="#071510",
            font=fonts["body_bold"],
            command=lambda: self._pick("automate"),
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            row,
            text="Run Once",
            height=34,
            fg_color=palette.accent_cyan,
            hover_color=palette.accent_cyan_hover,
            text_color="#071217",
            font=fonts["body_bold"],
            command=lambda: self._pick("once"),
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            row,
            text="Not Now",
            height=34,
            fg_color=palette.bg_elevated,
            hover_color=palette.border_strong,
            font=fonts["body"],
            command=lambda: self._pick("skip"),
        ).pack(side="right")

    def _pick(self, choice: str) -> None:
        self.on_decision(choice)
        self.destroy()


class CustomScriptDialog(ctk.CTkToplevel):
    """Modal for creating a new toggleable optimization script."""

    def __init__(
        self,
        master,
        palette: ThemePalette,
        fonts: dict,
        on_create: Callable[[str, str, str, str, str], None],
    ) -> None:
        super().__init__(master)
        self.on_create = on_create
        self.title("Create Toggleable Optimization Script")
        self.geometry("540x430")
        self.configure(fg_color=palette.bg_canvas)
        self.transient(master)

        card = ctk.CTkFrame(
            self, fg_color=palette.bg_card, border_width=1, border_color=palette.border_subtle, corner_radius=12
        )
        card.pack(fill="both", expand=True, padx=16, pady=16)

        ctk.CTkLabel(card, text="New Toggleable Script", font=fonts["section"], text_color=palette.text_primary).pack(
            anchor="w", padx=16, pady=(14, 8)
        )

        self.name_ent = ctk.CTkEntry(card, placeholder_text="Script Name (e.g., Unreal Engine Build Mode)")
        self.name_ent.pack(fill="x", padx=16, pady=5)

        self.cat_var = tk.StringVar(value="custom")
        ctk.CTkOptionMenu(
            card, values=["custom", "gaming", "3d_render", "compile_data", "ai_hibernate"], variable=self.cat_var
        ).pack(fill="x", padx=16, pady=5)

        self.prof_var = tk.StringVar(value="Balanced Daily")
        ctk.CTkOptionMenu(
            card, values=list(SITUATION_PROFILES.keys()), variable=self.prof_var
        ).pack(fill="x", padx=16, pady=5)

        self.desc_ent = ctk.CTkEntry(card, placeholder_text="Short description of what this toggle does")
        self.desc_ent.pack(fill="x", padx=16, pady=5)

        self.cmd_ent = ctk.CTkEntry(card, placeholder_text="Optional shell command (leave blank for profile-only toggle)")
        self.cmd_ent.pack(fill="x", padx=16, pady=5)

        ctk.CTkButton(
            card,
            text="Add Toggleable Script",
            height=36,
            fg_color=palette.accent_cyan,
            hover_color=palette.accent_cyan_hover,
            text_color="#071217",
            font=fonts["body_bold"],
            command=self._submit,
        ).pack(fill="x", padx=16, pady=(18, 12))

    def _submit(self) -> None:
        self.on_create(
            self.name_ent.get(),
            self.cat_var.get(),
            self.desc_ent.get(),
            self.cmd_ent.get(),
            self.prof_var.get(),
        )
        self.destroy()


class CrashRecoveryDialog(ctk.CTkToplevel):
    """Post-mortem crash & failure diagnosis modal."""

    def __init__(
        self,
        master,
        palette: ThemePalette,
        fonts: dict,
        report: CrashPostMortem,
    ) -> None:
        super().__init__(master)
        self.title("AstraCradle — Crash & Failure Post-Mortem Diagnosis")
        self.geometry("680x520")
        self.configure(fg_color=palette.bg_canvas)
        self.transient(master)

        scroll = ctk.CTkScrollableFrame(self, fg_color=palette.bg_card, corner_radius=12)
        scroll.pack(fill="both", expand=True, padx=16, pady=16)

        ctk.CTkLabel(
            scroll,
            text=f"Post-Mortem Incident Report ({report.incident_id})",
            font=fonts["section"],
            text_color=palette.accent_coral,
        ).pack(anchor="w", padx=14, pady=(12, 4))
        ctk.CTkLabel(
            scroll,
            text=f"Type: {report.crash_type} · Detected: {report.detected_at} · Diagnostic Confidence: {getattr(report, 'confidence_percent', 85)}%",
            font=fonts["mono_small"],
            text_color=palette.text_muted,
        ).pack(anchor="w", padx=14, pady=(0, 6))
        ev_scores = getattr(report, "evidence_scores", []) or []
        if ev_scores:
            score_txt = " · ".join(f"{e.get('signal')} (+{e.get('weight')}%)" for e in ev_scores[:4])
            ctk.CTkLabel(
                scroll,
                text=f"Weighted Signals: {score_txt}",
                font=fonts["caption"],
                text_color=palette.accent_cyan,
                wraplength=590,
                justify="left",
            ).pack(anchor="w", padx=14, pady=(0, 8))

        ctk.CTkLabel(scroll, text="Probable Cause:", font=fonts["body_bold"], text_color=palette.text_primary).pack(
            anchor="w", padx=14
        )
        ctk.CTkLabel(
            scroll,
            text=report.probable_cause,
            font=fonts["body"],
            text_color=palette.accent_brass,
            wraplength=590,
            justify="left",
        ).pack(anchor="w", padx=14, pady=(2, 10))

        ctk.CTkLabel(scroll, text="How to Fix:", font=fonts["body_bold"], text_color=palette.accent_jade).pack(
            anchor="w", padx=14
        )
        for i, step in enumerate(report.fix_steps, start=1):
            ctk.CTkLabel(
                scroll,
                text=f"  {i}. {step}",
                font=fonts["small"],
                text_color=palette.text_primary,
                wraplength=580,
                justify="left",
            ).pack(anchor="w", padx=14, pady=2)

        if report.evidence:
            ctk.CTkLabel(
                scroll, text="Evidence & Clues:", font=fonts["body_bold"], text_color=palette.text_secondary
            ).pack(anchor="w", padx=14, pady=(10, 2))
            for ev in report.evidence[:5]:
                ctk.CTkLabel(
                    scroll,
                    text=f"• {ev}",
                    font=fonts["mono_small"],
                    text_color=palette.text_muted,
                    wraplength=580,
                    justify="left",
                ).pack(anchor="w", padx=14, pady=2)

        if report.last_known_actions:
            ctk.CTkLabel(
                scroll,
                text="Last Forensic Actions Before Incident:",
                font=fonts["body_bold"],
                text_color=palette.text_secondary,
            ).pack(anchor="w", padx=14, pady=(10, 2))
            for act in report.last_known_actions[:5]:
                ctk.CTkLabel(
                    scroll,
                    text=act,
                    font=fonts["mono_small"],
                    text_color=palette.accent_cyan,
                    wraplength=580,
                    justify="left",
                ).pack(anchor="w", padx=14, pady=1)

        ctk.CTkButton(
            scroll,
            text="Acknowledge & Continue Safely",
            height=34,
            fg_color=palette.accent_cyan,
            hover_color=palette.accent_cyan_hover,
            text_color="#071217",
            font=fonts["body_bold"],
            command=self.destroy,
        ).pack(fill="x", padx=14, pady=16)


class EasyGuideDialog(ctk.CTkToplevel):
    """Zero-jargon 3-step quick guide for first-time or non-technical users."""

    def __init__(self, master, palette: ThemePalette, fonts: dict) -> None:
        super().__init__(master)
        self.title("AstraCradle — Easy 3-Step Guide")
        self.geometry("620x470")
        self.configure(fg_color=palette.bg_canvas)
        self.transient(master)

        card = ctk.CTkFrame(
            self, fg_color=palette.bg_card, border_width=1, border_color=palette.border_subtle, corner_radius=12
        )
        card.pack(fill="both", expand=True, padx=16, pady=16)

        ctk.CTkLabel(
            card,
            text="Welcome to AstraCradle — Simple & Safe Computer Care",
            font=fonts["section"],
            text_color=palette.accent_cyan,
        ).pack(anchor="w", padx=18, pady=(16, 4))
        ctk.CTkLabel(
            card,
            text="You cannot break your computer here. Everything AstraCradle cleans goes into a safe Quarantine Vault so you can undo any step with one click.",
            font=fonts["small"],
            text_color=palette.text_secondary,
            wraplength=540,
            justify="left",
        ).pack(anchor="w", padx=18, pady=(0, 12))

        steps = [
            (
                "1. Check Your Computer (Command Deck)",
                "Click 'Deep Scan & Playbook' at the top right. AstraCradle gives you a simple 0–100 score and explains anything slowing you down in plain English.",
            ),
            (
                "2. Turn Off Unused AI Chatbots & Clutter",
                "Open 'AI & Process Hunter' to spot background AI chatbots or idle helpers wasting memory, and click 'Turn Off Now' or let AstraCradle automate it.",
            ),
            (
                "3. Flip One Switch for Games, 3D Work, or Coding",
                "Open 'Toggleable Scripts' and flip a switch ON when gaming, rendering 3D scenes, or compiling code — flip it OFF when done to return to normal.",
            ),
        ]
        for heading, desc in steps:
            box = ctk.CTkFrame(card, fg_color=palette.bg_elevated, corner_radius=9)
            box.pack(fill="x", padx=18, pady=5)
            ctk.CTkLabel(box, text=heading, font=fonts["body_bold"], text_color=palette.text_primary, anchor="w").pack(
                fill="x", padx=12, pady=(8, 2)
            )
            ctk.CTkLabel(
                box, text=desc, font=fonts["small"], text_color=palette.text_secondary, wraplength=520, justify="left", anchor="w"
            ).pack(fill="x", padx=12, pady=(0, 8))

        ctk.CTkButton(
            card,
            text="Got It — Let's Start!",
            height=36,
            fg_color=palette.accent_jade,
            hover_color=palette.accent_jade_hover,
            text_color="#071510",
            font=fonts["body_bold"],
            command=self.destroy,
        ).pack(fill="x", padx=18, pady=(14, 14))


class UninstallDialog(ctk.CTkToplevel):
    """Idiot-proof Uninstall Wizard asking explicitly whether to delete all profiles & saved data."""

    def __init__(
        self,
        master,
        palette: ThemePalette,
        fonts: dict,
        data_dir: str,
        quarantine_count: int,
        on_confirm: Callable[[bool, bool, bool], None],
    ) -> None:
        super().__init__(master)
        self.on_confirm = on_confirm
        self.title("Uninstall AstraCradle Safely")
        self.geometry("600x430")
        self.configure(fg_color=palette.bg_canvas)
        self.transient(master)

        card = ctk.CTkFrame(
            self, fg_color=palette.bg_card, border_width=1, border_color=palette.border_subtle, corner_radius=12
        )
        card.pack(fill="both", expand=True, padx=16, pady=16)

        ctk.CTkLabel(
            card,
            text="Clean Uninstall — Zero Damage to Your Computer",
            font=fonts["section"],
            text_color=palette.accent_coral,
        ).pack(anchor="w", padx=18, pady=(16, 6))
        ctk.CTkLabel(
            card,
            text=(
                "AstraCradle will safely revert any active toggle scripts back to Balanced Daily, remove its Desktop/Menu "
                f"shortcuts, and clean up without touching any of your personal files or OS.\n\nData folder: {data_dir}"
            ),
            font=fonts["small"],
            text_color=palette.text_secondary,
            wraplength=530,
            justify="left",
        ).pack(anchor="w", padx=18, pady=(0, 12))

        self.v_restore_q = tk.BooleanVar(value=True)
        self.v_delete_data = tk.BooleanVar(value=True)
        self.v_uninstall_pkg = tk.BooleanVar(value=True)

        ctk.CTkCheckBox(
            card,
            text=f"Restore {quarantine_count} quarantined item(s) to original paths first (Recommended)",
            variable=self.v_restore_q,
            font=fonts["body"],
        ).pack(anchor="w", padx=20, pady=6)
        ctk.CTkCheckBox(
            card,
            text="Delete ALL AstraCradle profiles, settings, logs, scripts & saved data",
            variable=self.v_delete_data,
            font=fonts["body_bold"],
            text_color=palette.accent_brass,
        ).pack(anchor="w", padx=20, pady=6)
        ctk.CTkCheckBox(
            card,
            text="Uninstall the 'astracradle' Python package from this environment",
            variable=self.v_uninstall_pkg,
            font=fonts["body"],
        ).pack(anchor="w", padx=20, pady=6)

        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.pack(fill="x", padx=18, pady=(20, 14))
        ctk.CTkButton(
            btn_row,
            text="Uninstall AstraCradle Now",
            height=36,
            fg_color=palette.accent_coral,
            hover_color=palette.accent_coral_hover,
            text_color="#FFFFFF",
            font=fonts["body_bold"],
            command=self._run,
        ).pack(side="left", padx=(0, 10))
        ctk.CTkButton(
            btn_row,
            text="Cancel (Keep AstraCradle)",
            height=36,
            fg_color=palette.bg_elevated,
            hover_color=palette.border_strong,
            font=fonts["body"],
            command=self.destroy,
        ).pack(side="right")

    def _run(self) -> None:
        self.on_confirm(
            bool(self.v_delete_data.get()),
            bool(self.v_restore_q.get()),
            bool(self.v_uninstall_pkg.get()),
        )
        self.destroy()


class HelpSearchDialog(ctk.CTkToplevel):
    """Searchable Help & Knowledge Base modal answering questions in plain English."""

    def __init__(
        self,
        master,
        palette: ThemePalette,
        fonts: dict,
        on_navigate: Callable[[str], None] | None = None,
        initial_query: str = "",
    ) -> None:
        super().__init__(master)
        self.palette = palette
        self.fonts = fonts
        self.on_navigate = on_navigate
        self.title("AstraCradle Help & Search Center")
        self.geometry("760x580")
        self.configure(fg_color=palette.bg_canvas)
        self.transient(master)

        card = ctk.CTkFrame(
            self,
            fg_color=palette.bg_card,
            corner_radius=12,
            border_width=1,
            border_color=palette.accent_cyan,
        )
        card.pack(fill="both", expand=True, padx=16, pady=16)

        hdr = ctk.CTkFrame(card, fg_color="transparent")
        hdr.pack(fill="x", padx=18, pady=(14, 6))
        ctk.CTkLabel(
            hdr,
            text="Help & Feature Search (F1 / Ctrl+F)",
            font=fonts["title"],
            text_color=palette.accent_cyan,
        ).pack(side="left")
        ctk.CTkLabel(
            hdr,
            text="Type any feature, button, error code (e.g. Event 41, OOM), or question",
            font=fonts["small"],
            text_color=palette.text_secondary,
        ).pack(side="right")

        search_row = ctk.CTkFrame(card, fg_color="transparent")
        search_row.pack(fill="x", padx=18, pady=(4, 10))
        self.query_var = tk.StringVar(value=initial_query)
        self.search_entry = ctk.CTkEntry(
            search_row,
            textvariable=self.query_var,
            placeholder_text="Search e.g. 'quarantine', 'duplicate', 'AI', 'Event 41', 'gaming', 'uninstall'...",
            height=36,
            fg_color=palette.bg_input,
            border_color=palette.border_strong,
            font=fonts["body"],
        )
        self.search_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.search_entry.bind("<KeyRelease>", lambda _e: self._refresh_results())
        ctk.CTkButton(
            search_row,
            text="Clear",
            width=70,
            height=36,
            fg_color=palette.bg_elevated,
            hover_color=palette.border_strong,
            command=self._clear_query,
        ).pack(side="right")

        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=18, pady=(0, 12))
        body.grid_columnconfigure(0, weight=2)
        body.grid_columnconfigure(1, weight=3)
        body.grid_rowconfigure(0, weight=1)

        self.list_scroll = ctk.CTkScrollableFrame(
            body, fg_color=palette.bg_elevated, corner_radius=8
        )
        self.list_scroll.grid(row=0, column=0, sticky="nsew", padx=(0, 10))

        self.detail_box = ctk.CTkFrame(
            body, fg_color=palette.bg_elevated, corner_radius=8, border_width=1, border_color=palette.border_subtle
        )
        self.detail_box.grid(row=0, column=1, sticky="nsew")

        self.detail_cat = ctk.CTkLabel(
            self.detail_box, text="", font=fonts["caption"], text_color=palette.accent_brass, anchor="w"
        )
        self.detail_cat.pack(fill="x", padx=14, pady=(12, 2))
        self.detail_title = ctk.CTkLabel(
            self.detail_box, text="", font=fonts["section"], text_color=palette.text_primary, anchor="w", wraplength=380, justify="left"
        )
        self.detail_title.pack(fill="x", padx=14, pady=(0, 6))
        self.detail_summary = ctk.CTkLabel(
            self.detail_box, text="", font=fonts["body_bold"], text_color=palette.accent_cyan, anchor="w", wraplength=380, justify="left"
        )
        self.detail_summary.pack(fill="x", padx=14, pady=(0, 8))
        self.detail_body = ctk.CTkLabel(
            self.detail_box, text="", font=fonts["body"], text_color=palette.text_secondary, anchor="w", wraplength=380, justify="left"
        )
        self.detail_body.pack(fill="x", padx=14, pady=(0, 14))

        self.jump_btn = ctk.CTkButton(
            self.detail_box,
            text="Open Related Section",
            height=32,
            fg_color=palette.accent_jade,
            hover_color=palette.accent_jade_hover,
            text_color="#071510",
            font=fonts["body_bold"],
        )
        self.jump_btn.pack(anchor="w", padx=14, pady=(0, 12))

        self._selected_topic: HelpTopic | None = None
        self._refresh_results()
        self.after(60, self.search_entry.focus_set)

    def _clear_query(self) -> None:
        self.query_var.set("")
        self._refresh_results()

    def _refresh_results(self) -> None:
        for child in self.list_scroll.winfo_children():
            child.destroy()
        topics = search_help_topics(self.query_var.get())
        if not topics:
            ctk.CTkLabel(
                self.list_scroll,
                text="No matching help topics.\nTry 'quarantine', 'ai', 'duplicate', or 'crash'.",
                font=self.fonts["small"],
                text_color=self.palette.text_muted,
                wraplength=220,
                justify="left",
            ).pack(padx=10, pady=12)
            self._show_topic(None)
            return

        for idx, topic in enumerate(topics):
            btn = ctk.CTkButton(
                self.list_scroll,
                text=f"[{topic.category}] {topic.title}",
                anchor="w",
                height=34,
                fg_color=self.palette.bg_card if idx == 0 else "transparent",
                hover_color=self.palette.border_strong,
                text_color=self.palette.text_primary,
                font=self.fonts["small"],
                command=lambda t=topic: self._show_topic(t),
            )
            btn.pack(fill="x", padx=4, pady=2)
        self._show_topic(topics[0])

    def _show_topic(self, topic: HelpTopic | None) -> None:
        self._selected_topic = topic
        if topic is None:
            self.detail_cat.configure(text="")
            self.detail_title.configure(text="No Topic Selected")
            self.detail_summary.configure(text="")
            self.detail_body.configure(text="")
            self.jump_btn.pack_forget()
            return
        self.detail_cat.configure(text=topic.category.upper())
        self.detail_title.configure(text=topic.title)
        self.detail_summary.configure(text=topic.summary)
        self.detail_body.configure(text=topic.details)
        if topic.nav_target and self.on_navigate:
            self.jump_btn.configure(
                command=lambda target=topic.nav_target: self._jump(target)
            )
            self.jump_btn.pack(anchor="w", padx=14, pady=(0, 12))
        else:
            self.jump_btn.pack_forget()

    def _jump(self, nav_target: str) -> None:
        if self.on_navigate:
            self.on_navigate(nav_target)
        self.destroy()


class ProcessExplanationDialog(ctk.CTkToplevel):
    """Plain-English 'What is this running process?' explanation card."""

    def __init__(self, master, palette: ThemePalette, fonts: dict, info: ProcessExplanation) -> None:
        super().__init__(master)
        self.title(f"Process Translator — {info.name}")
        self.geometry("520x320")
        self.configure(fg_color=palette.bg_canvas)
        self.transient(master)

        card = ctk.CTkFrame(
            self,
            fg_color=palette.bg_card,
            corner_radius=12,
            border_width=1,
            border_color=palette.accent_jade if info.essential else palette.accent_cyan,
        )
        card.pack(fill="both", expand=True, padx=16, pady=16)
        ctk.CTkLabel(
            card, text=f"{info.name} — {info.role}", font=fonts["section"], text_color=palette.accent_cyan
        ).pack(anchor="w", padx=18, pady=(16, 6))
        status_txt = "ESSENTIAL / OS-PROTECTED" if info.essential else "USER / BACKGROUND PROCESS"
        ctk.CTkLabel(
            card,
            text=status_txt,
            font=fonts["caption"],
            text_color=palette.accent_jade if info.essential else palette.accent_brass,
        ).pack(anchor="w", padx=18, pady=(0, 8))
        ctk.CTkLabel(
            card, text=info.plain_english, font=fonts["body"], text_color=palette.text_primary, wraplength=460, justify="left"
        ).pack(anchor="w", padx=18, pady=(0, 10))
        ctk.CTkLabel(
            card,
            text=f"AstraCradle Advice: {info.safe_advice}",
            font=fonts["body_bold"],
            text_color=palette.text_secondary,
            wraplength=460,
            justify="left",
        ).pack(anchor="w", padx=18, pady=(0, 14))
        ctk.CTkButton(
            card,
            text="Close",
            height=32,
            fg_color=palette.bg_elevated,
            hover_color=palette.border_strong,
            command=self.destroy,
        ).pack(anchor="e", padx=18, pady=(0, 12))


class DryRunDiffDialog(ctk.CTkToplevel):
    """Interactive Dry-Run file preview showing exact files before applying cleanup."""

    def __init__(
        self,
        master,
        palette: ThemePalette,
        fonts: dict,
        title: str,
        summary: str,
        preview_items: list[dict[str, str]],
        on_execute_real: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(master)
        self.title(f"Dry-Run Diff Preview — {title}")
        self.geometry("680x480")
        self.configure(fg_color=palette.bg_canvas)
        self.transient(master)

        card = ctk.CTkFrame(
            self, fg_color=palette.bg_card, corner_radius=12, border_width=1, border_color=palette.border_subtle
        )
        card.pack(fill="both", expand=True, padx=16, pady=16)
        ctk.CTkLabel(card, text=title, font=fonts["section"], text_color=palette.accent_cyan).pack(
            anchor="w", padx=16, pady=(14, 4)
        )
        ctk.CTkLabel(
            card, text=summary, font=fonts["body"], text_color=palette.text_secondary, wraplength=620, justify="left"
        ).pack(anchor="w", padx=16, pady=(0, 8))

        scroll = ctk.CTkScrollableFrame(card, fg_color=palette.bg_elevated, corner_radius=8)
        scroll.pack(fill="both", expand=True, padx=16, pady=6)
        if not preview_items:
            ctk.CTkLabel(
                scroll, text="Zero files matched cleanup criteria — nothing would change on disk.", font=fonts["small"], text_color=palette.text_muted
            ).pack(anchor="w", padx=10, pady=12)
        else:
            for item in preview_items[:60]:
                ctk.CTkLabel(
                    scroll,
                    text=f"[{item.get('size', '?')}]  {item.get('path', '')}",
                    font=fonts["mono_small"],
                    text_color=palette.text_primary,
                    anchor="w",
                ).pack(fill="x", padx=8, pady=2)

        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=(8, 12))
        if on_execute_real and preview_items:
            ctk.CTkButton(
                btn_row,
                text="Quarantine These Files Now",
                height=34,
                fg_color=palette.accent_jade,
                hover_color=palette.accent_jade_hover,
                text_color="#071510",
                font=fonts["body_bold"],
                command=lambda: (on_execute_real(), self.destroy()),
            ).pack(side="left")
        ctk.CTkButton(
            btn_row,
            text="Close Preview",
            height=34,
            fg_color=palette.bg_elevated,
            hover_color=palette.border_strong,
            command=self.destroy,
        ).pack(side="right")


class PipelineEditorDialog(ctk.CTkToplevel):
    """Visual drag-order (Up/Down) & conditional branching editor for Optimization Pipelines."""

    def __init__(
        self,
        master,
        palette: ThemePalette,
        fonts: dict,
        pipeline,
        on_save: Callable[[object], None],
    ) -> None:
        super().__init__(master)
        self.palette = palette
        self.fonts = fonts
        self.pipeline = pipeline
        self.on_save = on_save
        self.title(f"Visual Pipeline Editor — {pipeline.name}")
        self.geometry("760x560")
        self.configure(fg_color=palette.bg_canvas)
        self.transient(master)

        card = ctk.CTkFrame(
            self, fg_color=palette.bg_card, corner_radius=12, border_width=1, border_color=palette.border_subtle
        )
        card.pack(fill="both", expand=True, padx=16, pady=16)

        ctk.CTkLabel(card, text=f"Edit Stages & Conditions: {pipeline.name}", font=fonts["section"], text_color=palette.accent_cyan).pack(
            anchor="w", padx=16, pady=(14, 6)
        )

        self.stage_scroll = ctk.CTkScrollableFrame(card, fg_color=palette.bg_elevated, corner_radius=8)
        self.stage_scroll.pack(fill="both", expand=True, padx=16, pady=6)

        add_bar = ctk.CTkFrame(card, fg_color="transparent")
        add_bar.pack(fill="x", padx=16, pady=(6, 4))
        self.new_stage_type = tk.StringVar(value="clean_caches")
        ctk.CTkOptionMenu(
            add_bar,
            values=["preflight_snapshot", "apply_preset", "run_script", "apply_gpu_profile", "clean_caches", "self_heal"],
            variable=self.new_stage_type,
            width=190,
        ).pack(side="left", padx=(0, 8))
        self.new_stage_cond = tk.StringVar(value="always")
        ctk.CTkOptionMenu(
            add_bar,
            values=["always", "if_ram_above", "if_temp_above", "if_on_battery", "if_not_fullscreen"],
            variable=self.new_stage_cond,
            width=170,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            add_bar,
            text="+ Add Stage",
            height=30,
            fg_color=palette.accent_cyan,
            text_color="#071217",
            font=fonts["caption"],
            command=self._add_stage,
        ).pack(side="left")

        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=(8, 12))
        ctk.CTkButton(
            btn_row,
            text="Save Pipeline Order & Conditions",
            height=34,
            fg_color=palette.accent_jade,
            text_color="#071510",
            font=fonts["body_bold"],
            command=self._save_and_close,
        ).pack(side="left")
        ctk.CTkButton(
            btn_row,
            text="Cancel",
            height=34,
            fg_color=palette.bg_elevated,
            hover_color=palette.border_strong,
            command=self.destroy,
        ).pack(side="right")

        self._render_stages()

    def _render_stages(self) -> None:
        p = self.palette
        for c in self.stage_scroll.winfo_children():
            c.destroy()
        for idx, st in enumerate(self.pipeline.stages):
            row = ctk.CTkFrame(self.stage_scroll, fg_color=p.bg_card, corner_radius=8)
            row.pack(fill="x", padx=8, pady=4)

            en_v = tk.BooleanVar(value=st.enabled)
            ctk.CTkCheckBox(
                row,
                text=f"{idx + 1}. {st.label} ({st.stage_type})",
                variable=en_v,
                font=self.fonts["small"],
                command=lambda s=st, v=en_v: setattr(s, "enabled", bool(v.get())),
            ).pack(side="left", padx=10, pady=6)

            ctk.CTkButton(
                row, text="Del", width=44, height=24, fg_color=p.badge_crit_bg, text_color=p.badge_crit_fg,
                font=self.fonts["caption"], command=lambda i=idx: self._remove(i),
            ).pack(side="right", padx=(2, 8), pady=5)
            ctk.CTkButton(
                row, text="Down", width=52, height=24, fg_color=p.bg_elevated, font=self.fonts["caption"],
                command=lambda i=idx: self._move(i, 1),
            ).pack(side="right", padx=2, pady=5)
            ctk.CTkButton(
                row, text="Up", width=44, height=24, fg_color=p.bg_elevated, font=self.fonts["caption"],
                command=lambda i=idx: self._move(i, -1),
            ).pack(side="right", padx=2, pady=5)

            cond_v = tk.StringVar(value=getattr(st, "condition", "always"))
            ctk.CTkOptionMenu(
                row,
                values=["always", "if_ram_above", "if_temp_above", "if_on_battery", "if_not_fullscreen"],
                variable=cond_v,
                width=145,
                command=lambda val, s=st: setattr(s, "condition", val),
            ).pack(side="right", padx=6, pady=5)

    def _move(self, idx: int, delta: int) -> None:
        j = idx + delta
        if 0 <= j < len(self.pipeline.stages):
            self.pipeline.stages[idx], self.pipeline.stages[j] = self.pipeline.stages[j], self.pipeline.stages[idx]
            self._render_stages()

    def _remove(self, idx: int) -> None:
        if len(self.pipeline.stages) > 1:
            self.pipeline.stages.pop(idx)
            self._render_stages()

    def _add_stage(self) -> None:
        from ..core.pipelines import PipelineStage
        st_type = self.new_stage_type.get()
        labels = {
            "preflight_snapshot": "Pre-Flight OS Snapshot",
            "apply_preset": "Apply Hardware Preset",
            "run_script": "Toggle Workload Script",
            "apply_gpu_profile": "Apply GPU Thermal Curve",
            "clean_caches": "Quarantine Stale Temp Caches",
            "self_heal": "7-Invariant Self-Heal Check",
        }
        params = {}
        if st_type == "apply_preset":
            params = {"preset_id": "preset_intel_hybrid_gaming"}
        elif st_type == "run_script":
            params = {"script_id": "script_gaming_max", "enable": True}
        elif st_type == "apply_gpu_profile":
            params = {"gpu_profile_id": "gpu_quiet_eco"}
        self.pipeline.stages.append(
            PipelineStage(st_type, labels.get(st_type, st_type), params=params, enabled=True, condition=self.new_stage_cond.get())
        )
        self._render_stages()

    def _save_and_close(self) -> None:
        self.on_save(self.pipeline)
        self.destroy()


class SudoPasswordDialog(ctk.CTkToplevel):
    """Modal prompt asking for sudo password (or optional desktop Polkit) when a toggle needs root."""

    def __init__(
        self,
        master,
        palette: ThemePalette,
        fonts: dict,
        title_text: str,
        reason_text: str,
        pkexec_available: bool = False,
    ) -> None:
        super().__init__(master)
        self.result: str | None = None
        self.title(title_text or "Administrator / Sudo Password Required")
        dw, dh = 520, 320
        try:
            master.update_idletasks()
            mx, my = master.winfo_rootx(), master.winfo_rooty()
            mw, mh = max(520, master.winfo_width() or 1000), max(320, master.winfo_height() or 700)
            self.geometry(f"{dw}x{dh}+{max(20, mx + (mw - dw) // 2)}+{max(20, my + (mh - dh) // 2)}")
        except Exception:
            self.geometry(f"{dw}x{dh}")
        self.minsize(440, 280)
        self.configure(fg_color=palette.bg_canvas)
        self.transient(master)

        card = ctk.CTkFrame(
            self, fg_color=palette.bg_card, border_width=1, border_color=palette.accent_brass, corner_radius=12
        )
        card.pack(fill="both", expand=True, padx=16, pady=16)

        ctk.CTkLabel(
            card,
            text=title_text or "Administrator / Sudo Password Required",
            font=fonts["section"],
            text_color=palette.accent_brass,
            wraplength=460,
            justify="left",
        ).pack(anchor="w", padx=18, pady=(16, 6))

        ctk.CTkLabel(
            card,
            text=reason_text,
            font=fonts["small"],
            text_color=palette.text_secondary,
            wraplength=460,
            justify="left",
        ).pack(anchor="w", padx=18, pady=(0, 12))

        ctk.CTkLabel(
            card,
            text="Enter sudo password (never saved or logged):",
            font=fonts["caption"],
            text_color=palette.text_muted,
        ).pack(anchor="w", padx=18, pady=(0, 4))

        self.pwd_entry = ctk.CTkEntry(card, show="•", height=34, font=fonts["body"])
        self.pwd_entry.pack(fill="x", padx=18, pady=(0, 14))
        self.pwd_entry.bind("<Return>", lambda _e: self._submit())

        btns = ctk.CTkFrame(card, fg_color="transparent")
        btns.pack(fill="x", padx=18, pady=(0, 14))

        ctk.CTkButton(
            btns,
            text="Run with Sudo",
            height=32,
            fg_color=palette.accent_jade,
            text_color="#071510",
            font=fonts["body_bold"],
            command=self._submit,
        ).pack(side="right", padx=(6, 0))

        if pkexec_available:
            ctk.CTkButton(
                btns,
                text="Use System Prompt",
                height=32,
                fg_color=palette.bg_elevated,
                font=fonts["small"],
                command=self._use_pkexec,
            ).pack(side="right", padx=6)

        ctk.CTkButton(
            btns,
            text="Cancel",
            height=32,
            fg_color="transparent",
            border_width=1,
            border_color=palette.border_strong,
            text_color=palette.text_secondary,
            font=fonts["small"],
            command=self._cancel,
        ).pack(side="left")

        self.protocol("WM_DELETE_WINDOW", self._cancel)
        try:
            self.grab_set()
            self.pwd_entry.focus_set()
        except Exception:
            pass

    def _submit(self) -> None:
        val = self.pwd_entry.get()
        self.pwd_entry.delete(0, "end")
        self.result = val if val else None
        self.destroy()

    def _use_pkexec(self) -> None:
        self.pwd_entry.delete(0, "end")
        self.result = "__PKEXEC__"
        self.destroy()

    def _cancel(self) -> None:
        self.pwd_entry.delete(0, "end")
        self.result = None
        self.destroy()




