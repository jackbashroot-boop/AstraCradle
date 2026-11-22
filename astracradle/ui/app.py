"""Main native desktop application window for AstraCradle."""

from __future__ import annotations

from dataclasses import asdict
import json
import math
from pathlib import Path
import platform
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image, ImageTk

from .. import __app_name__, __tagline__, __version__
from ..core.adblock_shield import AstraShieldManager
from ..core.autopilot import AutopilotEngine
from ..core.bloat_analyzer import BloatAnalyzer, DuplicateGroup
from ..core.config import AppConfig, ConfigManager, SITUATION_PROFILES
from ..core.crash_analyzer import CrashAnalyzer
from ..core.diagnostics import DiagnosticReport, DiagnosticsEngine
from ..core.executor import GuardedExecutor
from ..core.pc_extras import (
    BaselineDiffEngine,
    BatteryAndPowerInspector,
    MemoryStandbyManager,
    NetworkAndDnsInspector,
    SafeBeginnerFixer,
    StartupDelayManager as StartupFileDelayManager,
)
from ..core.advanced_controls import (
    FullscreenFocusDetector,
    GpuPowerAndFanController,
    GpuThermalProfile,
    NetworkFirewallController,
    StandaloneBuildRunner,
    clamp_fan_curve_points,
)
from ..core.autoruns_audit import AutorunsAndGhostAuditor
from ..core.full_discovery import FullSystemBaseline, FullSystemDiscoveryEngine
from ..core.hardware_library import HardwareLibraryManager, PriorityPreset
from ..core.i18n import SUPPORTED_LANGUAGES, Translator
from ..core.journal import ForensicJournal
from ..core.latency_guard import InterruptAndDpcGuard
from ..core.lifecycle import LifecycleManager
from ..core.models import (
    CrashPostMortem,
    OptimizationScript,
    Playbook,
    PlaybookStep,
    ProcessRecommendation,
    SelfAuditFinding,
    SelfHealEvent,
    SystemSnapshot,
)
from ..core.os_adapters import get_os_adapter
from ..core.perf_guardian import (
    AppAutoDetector,
    DeviceGlitchAdvisor,
    MemoryLeakWatchdog,
    MicroBenchmark,
    NetworkAndDnsAdvisor,
)
from ..core.pipelines import (
    OptimizationPipeline,
    PipelineEngine,
    PipelineStage,
    ScriptAndPresetPackManager,
)
from ..core.playbook import PlaybookBuilder
from ..core.probalance import ProBalanceEngine
from ..core.rollback import RollbackManager
from ..core.safeguard_plus import (
    ConfigIntegrityShield,
    PrivacyTraceSweeper,
    ProcessTranslator,
    RecoveryBundleExporter,
    StartupDelayManager,
    WeeklyDigestGenerator,
)
from ..core.scheduler import BackgroundSchedulerManager
from ..core.scripts_engine import ScriptsEngine
from ..core.self_heal import SelfHealEngine
from ..core.storage_deep import (
    DevAndAIMoVaultScanner,
    DiskSunburstBuilder,
    EmptyAndDeadSweeper,
    SmartDiskMonitor,
)
from ..core.telemetry import TelemetryCollector, format_bytes, format_uptime
from ..core.telemetry_history import (
    GlitchTroubleshooter,
    GpuTelemetryCollector,
    OutdatedAppsChecker,
    PreFlightSnapshotHook,
    TelemetryHistoryDB,
)
from ..core.thermal_spc import ThermalSpcMonitor
from ..core.updater import ModuleAndUpdateManager
from ..theme import ThemePalette, get_palette, get_ui_fonts
from .modals import (
    AutomationPromptDialog,
    ConsentAutomationDialog,
    CrashRecoveryDialog,
    CustomScriptDialog,
    DryRunDiffDialog,
    EasyGuideDialog,
    FullDiscoveryResultDialog,
    HelpSearchDialog,
    PipelineEditorDialog,
    ProcessExplanationDialog,
    UninstallDialog,
)
from .tray import TrayController
from .widgets import (
    AnimatedGifLabel,
    DysonOrbitalBeacon,
    DysonSunburstWidget,
    EquilibriumGauge,
    FanCurveCanvas,
    TelemetryHistoryChart,
    TelemetrySparkline,
    attach_tooltip,
    make_badge,
)


class AstraCradleApp(ctk.CTk):
    """Cross-platform native desktop studio for AstraCradle."""

    def __init__(
        self,
        base_dir: Path | str | None = None,
        assets_dir: Path | str | None = None,
        show_startup_popups: bool = True,
        start_minimized: bool = False,
    ) -> None:
        super().__init__()

        self.cfg_mgr = ConfigManager(base_dir=base_dir)
        self.config_obj: AppConfig = self.cfg_mgr.load()
        self.journal = ForensicJournal(self.cfg_mgr)
        self.rollback_mgr = RollbackManager(self.cfg_mgr)
        self.executor = GuardedExecutor(self.cfg_mgr, self.rollback_mgr, self.journal)
        self.self_heal = SelfHealEngine(self.cfg_mgr, self.rollback_mgr, self.journal)
        self.crash_analyzer = CrashAnalyzer(self.cfg_mgr, self.journal)
        self.scripts_engine = ScriptsEngine(self.cfg_mgr, self.executor, self.journal)
        self.lifecycle = LifecycleManager(
            self.cfg_mgr, self.rollback_mgr, self.journal, self.scripts_engine
        )
        self.updater = ModuleAndUpdateManager(self.cfg_mgr, self.journal)
        self.autopilot = AutopilotEngine(self.cfg_mgr, self.executor, self.self_heal, self.journal)
        self.telemetry = TelemetryCollector(self.config_obj.excluded_processes)
        self.diagnostics = DiagnosticsEngine()
        self.playbook_builder = PlaybookBuilder()
        self.bloat_analyzer = BloatAnalyzer()
        self.scheduler = BackgroundSchedulerManager(self.cfg_mgr, self.journal)
        self.smart_monitor = SmartDiskMonitor()
        self.dev_vault = DevAndAIMoVaultScanner()
        self.dead_sweeper = EmptyAndDeadSweeper()
        self.leak_watchdog = MemoryLeakWatchdog()
        self.benchmark = MicroBenchmark(self.cfg_mgr)
        self.auto_detector = AppAutoDetector()
        self.net_advisor = NetworkAndDnsAdvisor()
        self.device_advisor = DeviceGlitchAdvisor()
        self.privacy_sweeper = PrivacyTraceSweeper()
        self.integrity_shield = ConfigIntegrityShield(self.cfg_mgr, self.journal)
        self.recovery_exporter = RecoveryBundleExporter(self.cfg_mgr, self.journal)
        self.digest_gen = WeeklyDigestGenerator(self.cfg_mgr, self.journal)
        self.hw_mgr = HardwareLibraryManager(self.cfg_mgr, self.journal)
        self.pipe_mgr = PipelineEngine(self.cfg_mgr, self.executor, self.scripts_engine, self.hw_mgr, self.journal)
        self.history_db = TelemetryHistoryDB(self.cfg_mgr)
        self.net_fw = NetworkFirewallController(self.cfg_mgr, self.journal)
        self.gpu_pwr = GpuPowerAndFanController(self.cfg_mgr, self.journal)
        self.latency_guard = InterruptAndDpcGuard(self.cfg_mgr, self.journal)
        self.probalance = ProBalanceEngine(self.cfg_mgr, self.journal)
        self.thermal_spc = ThermalSpcMonitor(self.cfg_mgr, self.journal)
        self.autoruns_auditor = AutorunsAndGhostAuditor(self.cfg_mgr, self.rollback_mgr, self.journal)
        self.full_discovery = FullSystemDiscoveryEngine(self.cfg_mgr, self.rollback_mgr, self.journal)
        self.adblock_shield = AstraShieldManager(self.cfg_mgr, self.rollback_mgr, self.journal)
        self.baseline_diff = BaselineDiffEngine(self.cfg_mgr, self.journal)
        self.startup_file_delay = StartupFileDelayManager(self.cfg_mgr, self.rollback_mgr, self.journal)
        self.net_dns_inspector = NetworkAndDnsInspector(self.journal)
        self.ram_standby_mgr = MemoryStandbyManager(self.journal)
        self.battery_inspector = BatteryAndPowerInspector()
        self.safe_fixer = SafeBeginnerFixer(self.cfg_mgr, self.executor, self.rollback_mgr, self.journal)
        self.i18n = Translator(self.config_obj.language)
        self._last_duplicate_groups: list[DuplicateGroup] = []
        self._last_dup_scan_dir: str = str(Path.home() / "Downloads")
        self._last_dead_items: list = []
        self._last_outdated_lines: list[str] = []
        self._last_latency_report = None
        self._last_autoruns: list = []
        self._last_ghosts: list = []
        self._last_baseline: FullSystemBaseline | None = self.full_discovery.load_saved_baseline()
        self._draft_fan_curve: list[list[int]] = self.gpu_pwr.get_applied_curve()[1]
        self._toast_after_id: str | None = None
        self._last_history_write: float = 0.0
        self._last_focus_state = None
        self._last_layout_w: int = 0
        self._resize_after_id: str | None = None
        self._show_startup_popups = show_startup_popups

        self.assets_dir = (
            Path(assets_dir)
            if assets_dir
            else Path(__file__).resolve().parents[2] / "assets"
        )

        self.palette: ThemePalette = get_palette(self.config_obj.theme)
        self.fonts = get_ui_fonts(self._scale_float())
        ctk.set_appearance_mode(self.palette.mode)
        self._apply_ui_scale()
        self.tray = TrayController(
            self.assets_dir / "astracradle_emblem_64.png",
            on_show=lambda: self._ui_queue.put(("show_window", None, None)),
            on_scan=lambda: self._ui_queue.put(("trigger_scan", None, None)),
            on_quit=lambda: self._ui_queue.put(("quit_app", None, None)),
            on_fix_safe=lambda: self._ui_queue.put(("fix_safe", None, None)),
            on_profile=lambda prof: self._ui_queue.put(("switch_profile", prof, None)),
            on_toggle_probalance=lambda: self._ui_queue.put(("toggle_probalance", None, None)),
            on_toggle_astrashield=lambda: self._ui_queue.put(("toggle_astrashield", None, None)),
        )
        if show_startup_popups and (self.config_obj.show_tray_icon or self.config_obj.minimize_to_tray or start_minimized):
            self.tray.start()
        if show_startup_popups and (start_minimized or self.config_obj.start_minimized):
            if not self.tray.running:
                self.tray.start()
            if self.tray.running:
                self.after(150, self.withdraw)

        self.title(f"{__app_name__} v{__version__} — {__tagline__}")
        scr_w = max(1280, int(self.winfo_screenwidth() or 1366))
        scr_h = max(800, int(self.winfo_screenheight() or 768))
        init_w, init_h, pos_x, pos_y = self._compute_responsive_geometry(scr_w, scr_h)
        self.geometry(f"{init_w}x{init_h}+{pos_x}+{pos_y}")
        self.minsize(1060, 700)
        self.maxsize(max(3840, scr_w), max(2160, scr_h))
        self.configure(fg_color=self.palette.bg_canvas)
        self._set_window_icon()

        self.current_nav = "dashboard"
        self.current_hw_tab = "Component Library & Presets"
        self.current_module_tab = "Storage"
        self.current_pb_tab = "ProBalance Engine"
        self.current_auto_tab = "Autoruns & Services"
        self.playbook_filter = "all"
        self.latest_snapshot: SystemSnapshot | None = None
        self.latest_report: DiagnosticReport | None = None
        self.latest_playbook: Playbook | None = None
        self.latest_self_heal: list[SelfHealEvent] = []
        self.latest_audit: list[SelfAuditFinding] = []
        self._cache_check_vars: dict[str, tk.BooleanVar] = {}
        self._poll_job: str | None = None
        self._drain_job: str | None = None
        self._latest_gpu = None
        self._is_busy = False
        self._ui_queue: queue.Queue = queue.Queue()
        self._bg_images: dict[str, ctk.CTkImage] = {}

        # Crash Post-Mortem detection before marking current session active
        self.startup_crash_report: CrashPostMortem | None = None
        if self.config_obj.consent_crash_diagnostics:
            self.startup_crash_report = self.crash_analyzer.inspect_on_startup()
        self.crash_analyzer.mark_running(self.config_obj.active_profile)

        self.journal.log_event(
            component="AstraCradleApp",
            action="startup",
            how=f"Initialized native studio on {platform.system()} ({platform.machine()})",
            why="User launched AstraCradle",
            outcome="ok",
        )

        adapter = self._active_adapter()
        if self.config_obj.auto_self_heal:
            self.latest_self_heal, self.latest_audit = self.self_heal.run_self_check(
                adapter, self.config_obj
            )
            self.config_obj = self.cfg_mgr.load()

        # Check seamless updates at startup
        if self.config_obj.auto_update_mode == "silent_auto":
            upd = self.updater.check_for_updates(self.config_obj)
            if upd.update_available:
                self.updater.stage_and_apply_local_update(self.config_obj)

        self._load_brand_images()
        self._build_layout()
        self._bind_shortcuts()
        self._bind_global_mousewheel()
        self.bind("<Configure>", self._on_root_configure)
        self.bind("<Unmap>", self._on_window_unmap)
        self._drain_ui_queue()

        # Respect explicit permission before first full-computer diagnostic pull on interactive first launch
        if show_startup_popups and not self.config_obj.consent_prompted:
            self.dash_headline.configure(text="Permission Required for First-Load Local PC Scan")
            self.dash_summary.configure(
                text=(
                    "AstraCradle only collects full computer diagnostics locally with your explicit consent. "
                    "Click 'Grant & Run Full PC Scan' in the permission prompt (or 'Permissions' above) to inspect "
                    "hardware, SMART disks, installed apps, autoruns, DPC latency, and system health."
                )
            )
            self.topbar_status.configure(text="Awaiting scan permission")
        else:
            self.refresh_telemetry_async(include_dns=True, also_scan=True)
            self._schedule_telemetry_poll()

        self.protocol("WM_DELETE_WINDOW", self.on_close)

        if show_startup_popups:
            if self.startup_crash_report is not None:
                self.after(350, lambda: CrashRecoveryDialog(self, self.palette, self.fonts, self.startup_crash_report))
            elif not self.config_obj.consent_prompted:
                self.after(300, lambda: self.open_consent_wizard(first_run=True))

    @staticmethod
    def _compute_responsive_geometry(screen_w: int, screen_h: int) -> tuple[int, int, int, int]:
        """Sizes the window proportionally to the active display from 1060x700 up to 4K (3840x2160)."""
        w = max(1060, min(3680, int(round(screen_w * 0.80))))
        h = max(700, min(2060, int(round(screen_h * 0.82))))
        if w > screen_w:
            w = max(1060, screen_w - 24)
        if h > screen_h:
            h = max(700, screen_h - 48)
        x = max(0, (screen_w - w) // 2)
        y = max(0, (screen_h - h) // 2)
        return w, h, x, y

    def _scale_float(self) -> float:
        raw = str(getattr(self.config_obj, "ui_scale", "100%")).strip()
        if raw.lower().startswith("auto"):
            try:
                sw = int(self.winfo_screenwidth() or 1920)
            except Exception:
                sw = 1920
            if sw >= 3400:
                return 1.35
            if sw >= 2400:
                return 1.15
            return 1.0
        try:
            return max(0.85, min(1.75, float(raw.replace("%", "").strip()) / 100.0))
        except Exception:
            return 1.0

    def _on_root_configure(self, event) -> None:
        if event.widget is not self:
            return
        w = max(1060, int(event.width or 1300))
        if abs(w - self._last_layout_w) < 28:
            return
        self._last_layout_w = w
        if self._resize_after_id is not None:
            try:
                self.after_cancel(self._resize_after_id)
            except Exception:
                pass
        self._resize_after_id = self.after(60, lambda ww=w: self._apply_responsive_layout(ww))

    def _apply_responsive_layout(self, win_w: int) -> None:
        """Dynamically scales sidebar, topbar buttons, and text wrapping for windows up to 4K."""
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        ratio = max(0.92, min(1.85, win_w / 1300.0))
        sb_w = int(max(224, min(360, round(248 * ratio))))
        self._current_sb_w = sb_w
        if hasattr(self, "sidebar") and self.sidebar.winfo_exists():
            self.sidebar.configure(width=sb_w)
        btn_h = int(max(30, min(46, round(32 * ratio))))
        for btn in getattr(self, "nav_buttons", {}).values():
            try:
                btn.configure(height=btn_h)
            except Exception:
                pass
        top_h = int(max(56, min(82, round(60 * ratio))))
        if hasattr(self, "topbar") and self.topbar.winfo_exists():
            self.topbar.configure(height=top_h)
        # Scale topbar CTA buttons proportionally so they fill monitor space nicely without clipping
        cta_h = int(max(28, min(44, round(31 * ratio))))
        for bname, base_w in (("scan_cta", 104), ("help_btn", 98), ("guide_btn", 88), ("perm_btn", 92)):
            btn = getattr(self, bname, None)
            if btn is not None:
                try:
                    btn.configure(height=cta_h, width=int(max(78, min(190, round(base_w * ratio)))))
                except Exception:
                    pass
        wrap_w = max(360, win_w - sb_w - 170)
        for lbl_name in ("dash_summary",):
            lbl = getattr(self, lbl_name, None)
            if lbl is not None:
                try:
                    lbl.configure(wraplength=max(280, win_w - sb_w - 490))
                except Exception:
                    pass
        # Dynamically reflow Command Deck buttons between 2 rows (compact) and 1 row (wide/4K)
        if hasattr(self, "_cmd_btns") and hasattr(self, "dash_btn_row") and self.dash_btn_row.winfo_exists():
            try:
                cols = 5 if win_w >= 1450 else 3
                for c in range(5):
                    self.dash_btn_row.grid_columnconfigure(c, weight=(1 if c < cols else 0))
                for idx, btn in enumerate(self._cmd_btns):
                    r, c = (0, idx) if cols == 5 else divmod(idx, 3)
                    btn.grid(row=r, column=c, sticky="ew", padx=3, pady=3)
            except Exception:
                pass
        # Dynamically adapt wrapped labels & prevent any CTkScrollableFrame horizontal overflow
        if hasattr(self, "page_container") and self.page_container.winfo_exists():
            self._sync_responsive_wraps(self.page_container, wrap_w)

    def _content_wrap(self, reserve: int = 120) -> int:
        try:
            win_w = int(self.winfo_width() or 1300)
            if win_w < 300:
                win_w = 1300
        except Exception:
            win_w = 1300
        sb_w = int(getattr(self, "_current_sb_w", 252))
        return max(440, win_w - sb_w - reserve)

    def _sync_responsive_wraps(self, root_widget, max_wrap: int) -> None:
        stack = [root_widget]
        while stack:
            w = stack.pop()
            try:
                if isinstance(w, ctk.CTkScrollableFrame):
                    canvas = getattr(w, "_parent_canvas", None)
                    if canvas is not None:
                        canvas.xview_moveto(0.0)
                elif isinstance(w, ctk.CTkLabel):
                    cur_wrap = int(w.cget("wraplength") or 0)
                    if cur_wrap > 0:
                        target = max(360, min(max_wrap, max_wrap - 40))
                        if abs(cur_wrap - target) > 40:
                            w.configure(wraplength=target)
                stack.extend(w.winfo_children())
            except Exception:
                continue

    def _bind_global_mousewheel(self) -> None:
        """Ensures vertical mouse-wheel scrolling works reliably over any child widget, with zero horizontal scroll."""
        def _find_scroll_frame(widget):
            cur = widget
            while cur is not None:
                if isinstance(cur, ctk.CTkScrollableFrame):
                    return cur
                cur = getattr(cur, "master", None)
            return None

        def _on_wheel(event) -> None:
            sf = _find_scroll_frame(event.widget)
            if sf is None:
                return
            canvas = getattr(sf, "_parent_canvas", None)
            if canvas is None:
                return
            # Only scroll vertically when content is taller than viewport
            try:
                first, last = canvas.yview()
                if first <= 0.0 and last >= 1.0:
                    return
            except Exception:
                pass
            if getattr(event, "num", None) == 4:
                delta = -2
            elif getattr(event, "num", None) == 5:
                delta = 2
            else:
                d = getattr(event, "delta", 0)
                if d == 0:
                    return
                delta = -1 if d > 0 else 1
                if abs(d) >= 120:
                    delta = int(-d / 120) * 2
            try:
                canvas.yview_scroll(delta, "units")
            except Exception:
                pass

        self.bind_all("<MouseWheel>", _on_wheel, add="+")
        self.bind_all("<Button-4>", _on_wheel, add="+")
        self.bind_all("<Button-5>", _on_wheel, add="+")

    def _bind_shortcuts(self) -> None:
        self.bind("<F1>", lambda _e: self.open_help_search())
        self.bind("<Control-f>", lambda _e: self.open_help_search())
        self.bind("<Control-r>", lambda _e: self.on_deep_scan())
        nav_keys = (
            "dashboard",
            "playbook",
            "hunter",
            "scripts",
            "hardware",
            "modules",
            "safeguards",
            "settings",
            "probalance",
            "autoruns",
        )
        for idx, k in enumerate(nav_keys, start=1):
            key_num = "0" if idx == 10 else str(idx)
            self.bind(f"<Control-Key-{key_num}>", lambda _e, key=k: self.switch_nav(key))

    def _on_window_unmap(self, event) -> None:
        if event.widget is not self:
            return
        try:
            if self.config_obj.minimize_to_tray and self.state() == "iconic":
                if not self.tray.running:
                    self.tray.start()
                if self.tray.running:
                    self.withdraw()
                    self.tray.notify("AstraCradle", "Minimized to the bottom-right system tray.")
        except Exception:
            pass

    def on_close(self, force_quit: bool = False) -> None:
        if not force_quit and self.config_obj.minimize_to_tray:
            if not self.tray.running:
                self.tray.start()
            if self.tray.running:
                self.withdraw()
                self.tray.notify("AstraCradle", "Still running in the bottom-right tray. Right-click icon to Quit.")
                return
        try:
            self.adblock_shield.stop_dns_sinkhole()
            self.tray.stop()
            self.journal.log_event(
                component="AstraCradleApp",
                action="shutdown",
                how="Clean window close protocol executed",
                why="Mark clean shutdown sentinel for CrashAnalyzer",
                outcome="ok",
            )
            self.crash_analyzer.mark_clean_shutdown()
        finally:
            self.destroy()

    def destroy(self) -> None:
        for attr in ("_poll_job", "_drain_job", "_resize_after_id", "_toast_after_id"):
            job = getattr(self, attr, None)
            if job is not None:
                try:
                    self.after_cancel(job)
                except Exception:
                    pass
                setattr(self, attr, None)
        super().destroy()

    def _apply_ui_scale(self) -> None:
        try:
            pct = self._scale_float()
            ctk.set_widget_scaling(max(0.85, min(1.75, pct)))
        except Exception:
            pass

    def _active_adapter(self):
        return get_os_adapter(
            override=self.config_obj.os_adapter_override,
            custom_protected_paths=self.config_obj.custom_protected_paths,
        )

    def _set_window_icon(self) -> None:
        icon_path = self.assets_dir / "astracradle_emblem_64.png"
        if icon_path.exists():
            try:
                img = Image.open(icon_path)
                self._tk_icon = ImageTk.PhotoImage(img)
                self.iconphoto(True, self._tk_icon)
            except Exception:
                pass

    def _load_brand_images(self) -> None:
        for key, fname, sz in (
            ("cmd", "bg_command.png", (1050, 740)),
            ("vault", "bg_vault.png", (1050, 740)),
        ):
            fpath = self.assets_dir / fname
            if fpath.exists():
                try:
                    im = Image.open(fpath).convert("RGBA")
                    self._bg_images[key] = ctk.CTkImage(light_image=im, dark_image=im, size=sz)
                except Exception:
                    pass

    def _build_layout(self) -> None:
        p = self.palette
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # Left Navigation Rail
        self.sidebar = ctk.CTkFrame(
            self, width=252, corner_radius=0, fg_color=p.bg_sidebar, border_width=0
        )
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_propagate(False)

        brand_box = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        brand_box.pack(fill="x", padx=14, pady=(16, 8))

        gif_badge = self.assets_dir / "astracradle_badge_animated.gif"
        if gif_badge.exists():
            self.sidebar_gif = AnimatedGifLabel(brand_box, gif_badge, bg=p.bg_sidebar, size=50)
            self.sidebar_gif.pack(side="left", padx=(0, 10))

        title_col = ctk.CTkFrame(brand_box, fg_color="transparent")
        title_col.pack(side="left", fill="x")
        ctk.CTkLabel(
            title_col, text=__app_name__, font=self.fonts["title"], text_color=p.text_primary
        ).pack(anchor="w")
        ctk.CTkLabel(
            title_col, text="Dyson-Core OS Studio", font=self.fonts["caption"], text_color=p.accent_cyan
        ).pack(anchor="w")

        os_pill = ctk.CTkFrame(
            self.sidebar, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=8
        )
        os_pill.pack(fill="x", padx=16, pady=(4, 12))
        self.os_pill_label = ctk.CTkLabel(
            os_pill,
            text=f"{platform.system()} · {platform.machine()}",
            font=self.fonts["small"],
            text_color=p.text_secondary,
        )
        self.os_pill_label.pack(padx=10, pady=6, anchor="w")

        self.nav_buttons: dict[str, ctk.CTkButton] = {}
        nav_items = [
            ("dashboard", self.i18n.t("nav.dashboard", "Command Deck"), "Live CPU, RAM, GPU, Disk, Network telemetry & Dyson Equilibrium Score (Ctrl+1)."),
            ("playbook", self.i18n.t("nav.playbook", "Diagnostics & Playbook"), "Actionable findings with Confidence %, Safe Auto-Fix & copyable DIY steps (Ctrl+2)."),
            ("hunter", "AI & Process Hunter", "Spot idle AI chatbots (Ollama, LM Studio) & dormant helpers holding RAM (Ctrl+3)."),
            ("scripts", self.i18n.t("nav.scripts", "Toggleable Scripts"), "One-click workload modes (Gaming, 3D Render, Compile) with OS hardware snapshots (Ctrl+4)."),
            ("hardware", self.i18n.t("nav.hardware", "Hardware & Pipelines"), "PC Component Library, Presets, Visual Pipeline Editor & Interactive Fan Curve (Ctrl+5)."),
            ("modules", self.i18n.t("nav.modules", "System Modules"), "Storage cleanup, Startup items, Processes & Firewall, Duplicates, Privacy & Glitch Fixes (Ctrl+6)."),
            ("probalance", self.i18n.t("nav.probalance", "ProBalance & Latency"), "ProBalance Foreground Boost, Auto-Reverted App Profiles & Interrupt/DPC Guard (Ctrl+9)."),
            ("autoruns", self.i18n.t("nav.autoruns", "Autoruns & Baselines"), "Autoruns Startup/Task/Service Audit, Ghost Driver Cleanup & Welford Thermal SPC (Ctrl+0)."),
            ("safeguards", self.i18n.t("nav.safeguards", "Forensic, Crash & Vault"), "7-invariant Self-Heal, hash-chained WHAT/HOW/WHY log, Crash Post-Mortem & Undo Vault (Ctrl+7)."),
            ("settings", self.i18n.t("nav.settings", "Settings & Updates"), "Theme, High Contrast, Language, Background Scheduler, Packaging & Uninstall (Ctrl+8)."),
        ]
        for key, label, tip_txt in nav_items:
            btn = ctk.CTkButton(
                self.sidebar,
                text=f"  {label}",
                anchor="w",
                height=31,
                corner_radius=8,
                font=self.fonts["body_bold"],
                fg_color="transparent",
                text_color=p.text_secondary,
                hover_color=p.bg_elevated,
                command=lambda k=key: self.switch_nav(k),
            )
            btn.pack(fill="x", padx=12, pady=1)
            attach_tooltip(btn, tip_txt, p)
            self.nav_buttons[key] = btn

        spacer = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        spacer.pack(fill="both", expand=True)

        self.footer_card = ctk.CTkFrame(
            self.sidebar, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=10
        )
        self.footer_card.pack(fill="x", padx=14, pady=12)
        self.footer_heal_lbl = ctk.CTkLabel(
            self.footer_card, text="Self-Heal: 7/7 OK", font=self.fonts["small"], text_color=p.accent_jade
        )
        self.footer_heal_lbl.pack(anchor="w", padx=12, pady=(8, 2))
        self.footer_prof_lbl = ctk.CTkLabel(
            self.footer_card,
            text=f"Profile: {self.config_obj.active_profile}",
            font=self.fonts["caption"],
            text_color=p.text_muted,
        )
        self.footer_prof_lbl.pack(anchor="w", padx=12, pady=(0, 8))

        # Main Workspace Area
        self.main_area = ctk.CTkFrame(self, fg_color=p.bg_canvas, corner_radius=0)
        self.main_area.grid(row=0, column=1, sticky="nsew")
        self.main_area.grid_rowconfigure(1, weight=1)
        self.main_area.grid_columnconfigure(0, weight=1)

        # Top Bar
        self.topbar = ctk.CTkFrame(self.main_area, height=60, fg_color=p.bg_sidebar, corner_radius=0)
        self.topbar.grid(row=0, column=0, sticky="ew")
        self.topbar.grid_propagate(False)

        self.topbar_title = ctk.CTkLabel(
            self.topbar, text="Command Deck", font=self.fonts["title"], text_color=p.text_primary
        )
        self.topbar_title.pack(side="left", padx=(20, 8))

        self.topbar_beacon = DysonOrbitalBeacon(self.topbar, p, size=22)
        self.topbar_beacon.pack(side="left", padx=(0, 4))
        attach_tooltip(self.topbar_beacon, "Live animated Dyson telemetry heartbeat.", p)

        self.topbar_status = ctk.CTkLabel(
            self.topbar, text="Live", font=self.fonts["small"], text_color=p.text_muted
        )
        self.topbar_status.pack(side="left", padx=4)

        self.scan_cta = ctk.CTkButton(
            self.topbar,
            text="Deep Scan",
            height=32,
            width=96,
            corner_radius=8,
            font=self.fonts["body_bold"],
            fg_color=p.accent_cyan,
            hover_color=p.accent_cyan_hover,
            text_color="#071217",
            command=self.on_deep_scan,
        )
        self.scan_cta.pack(side="right", padx=(6, 14))
        attach_tooltip(
            self.scan_cta,
            "Deep Scan & Playbook: scans caches, memory, DNS, and AI/idle processes locally (Ctrl+R).",
            p,
        )

        self.help_btn = ctk.CTkButton(
            self.topbar,
            text=self.i18n.t("btn.help_search", "Help / Search"),
            height=30,
            width=96,
            corner_radius=8,
            fg_color=p.bg_elevated,
            hover_color=p.border_strong,
            text_color=p.accent_cyan,
            font=self.fonts["small"],
            command=self.open_help_search,
        )
        self.help_btn.pack(side="right", padx=3)
        attach_tooltip(
            self.help_btn,
            "Search any AstraCradle feature, safety tier, script, or OS crash code (F1 or Ctrl+F).",
            p,
        )

        self.guide_btn = ctk.CTkButton(
            self.topbar,
            text=self.i18n.t("btn.easy_guide", "Easy Guide"),
            height=30,
            width=84,
            corner_radius=8,
            fg_color=p.bg_elevated,
            hover_color=p.border_strong,
            font=self.fonts["small"],
            command=self.open_easy_guide,
        )
        self.guide_btn.pack(side="right", padx=3)
        attach_tooltip(self.guide_btn, "Step-by-step beginner walkthrough for AstraCradle.", p)

        self.perm_btn = ctk.CTkButton(
            self.topbar,
            text="Permissions",
            height=30,
            width=88,
            corner_radius=8,
            fg_color=p.bg_elevated,
            hover_color=p.border_strong,
            font=self.fonts["small"],
            command=self.open_consent_wizard,
        )
        self.perm_btn.pack(side="right", padx=3)
        attach_tooltip(self.perm_btn, "Review local diagnostic consent gates & 1-click automation rules.", p)

        self.dry_run_var = tk.BooleanVar(value=self.config_obj.dry_run_default)
        self.dry_run_switch = ctk.CTkSwitch(
            self.topbar,
            text=self.i18n.t("btn.dry_run", "Dry-Run"),
            variable=self.dry_run_var,
            font=self.fonts["small"],
            text_color=p.text_secondary,
            progress_color=p.accent_brass,
            command=self._on_toggle_dry_run,
        )
        self.dry_run_switch.pack(side="right", padx=6)
        attach_tooltip(
            self.dry_run_switch,
            "When ON, AstraCradle only previews what it would do without modifying or moving any files.",
            p,
        )

        # Background artwork backdrop behind page frames
        self.bg_layer = ctk.CTkFrame(self.main_area, fg_color=p.bg_canvas, corner_radius=0)
        self.bg_layer.grid(row=1, column=0, sticky="nsew")
        self.bg_layer.grid_rowconfigure(0, weight=1)
        self.bg_layer.grid_columnconfigure(0, weight=1)

        self.bg_art_lbl = ctk.CTkLabel(self.bg_layer, text="")
        if "cmd" in self._bg_images:
            self.bg_art_lbl.configure(image=self._bg_images["cmd"])
        self.bg_art_lbl.place(x=0, y=0, relwidth=1, relheight=1)

        self.page_container = ctk.CTkFrame(self.bg_layer, fg_color="transparent")
        self.page_container.grid(row=0, column=0, sticky="nsew", padx=16, pady=12)
        self.page_container.grid_rowconfigure(0, weight=1)
        self.page_container.grid_columnconfigure(0, weight=1)

        self.pages: dict[str, ctk.CTkFrame] = {}
        for key in (
            "dashboard",
            "playbook",
            "hunter",
            "scripts",
            "hardware",
            "modules",
            "probalance",
            "autoruns",
            "safeguards",
            "settings",
        ):
            frame = ctk.CTkFrame(self.page_container, fg_color="transparent")
            frame.grid(row=0, column=0, sticky="nsew")
            self.pages[key] = frame

        self._build_dashboard_page()
        self._build_playbook_page()
        self._build_hunter_page()
        self._build_scripts_page()
        self._build_hardware_page()
        self._build_modules_page()
        self._build_probalance_page()
        self._build_autoruns_page()
        self._build_safeguards_page()
        self._build_settings_page()

        self.toast_frame = ctk.CTkFrame(
            self.main_area,
            fg_color=p.bg_elevated,
            border_width=1,
            border_color=p.accent_cyan,
            corner_radius=9,
        )
        self.toast_label = ctk.CTkLabel(
            self.toast_frame,
            text="",
            fg_color="transparent",
            text_color=p.text_primary,
            font=self.fonts["small"],
            wraplength=470,
            padx=10,
            pady=6,
        )
        self.toast_label.pack(side="left")
        self.toast_undo_btn = ctk.CTkButton(
            self.toast_frame,
            text="Undo",
            width=64,
            height=24,
            corner_radius=6,
            fg_color=p.accent_brass,
            hover_color="#B8934F",
            text_color="#120E06",
            font=self.fonts["caption"],
        )
        self.switch_nav("dashboard")

    def show_toast(self, msg: str, color: str | None = None, undo_entry_id: str | None = None) -> None:
        p = self.palette
        self.toast_label.configure(
            text=f"  {msg}  ",
            text_color=color or p.accent_cyan,
        )
        if undo_entry_id:
            self.toast_undo_btn.configure(
                command=lambda eid=undo_entry_id: (self.toast_frame.place_forget(), self.on_rollback_entry(eid))
            )
            self.toast_undo_btn.pack(side="right", padx=(2, 8), pady=4)
        else:
            self.toast_undo_btn.pack_forget()
        self.toast_frame.place(relx=0.98, rely=0.97, anchor="se")
        if self._toast_after_id is not None:
            try:
                self.after_cancel(self._toast_after_id)
            except Exception:
                pass
        self._toast_after_id = self.after(5500, lambda: self.toast_frame.place_forget())

    def hide_toast(self) -> None:
        self.toast_frame.place_forget()

    def switch_nav(self, key: str) -> None:
        p = self.palette
        self.current_nav = key
        titles = {
            "dashboard": "Command Deck",
            "playbook": "Diagnostics & Playbook",
            "hunter": "AI & Process Hunter",
            "scripts": "Toggleable Scripts",
            "hardware": "Hardware & Pipelines",
            "modules": "System Modules",
            "probalance": "ProBalance & Latency",
            "autoruns": "Autoruns & Baselines",
            "safeguards": "Forensic & Crash Vault",
            "settings": "Settings & Packaging",
        }
        self.topbar_title.configure(text=titles.get(key, __app_name__))
        bg_key = "vault" if key in ("safeguards", "scripts", "hardware", "probalance", "autoruns", "settings") else "cmd"
        if bg_key in self._bg_images and p.mode == "dark":
            self.bg_art_lbl.configure(image=self._bg_images[bg_key])
        else:
            self.bg_art_lbl.configure(image="")

        for k, btn in self.nav_buttons.items():
            if k == key:
                btn.configure(fg_color=p.bg_elevated, text_color=p.accent_cyan)
            else:
                btn.configure(fg_color="transparent", text_color=p.text_secondary)
        self.pages[key].tkraise()

        if key == "playbook":
            self._render_playbook()
        elif key == "hunter":
            self._render_hunter()
        elif key == "scripts":
            self._render_scripts()
        elif key == "hardware":
            self._render_hardware_page()
        elif key == "modules":
            self._render_modules()
        elif key == "probalance":
            self._render_probalance_page()
        elif key == "autoruns":
            self._render_autoruns_page()
        elif key == "safeguards":
            self._render_safeguards()

    def _on_toggle_dry_run(self) -> None:
        self.config_obj.dry_run_default = bool(self.dry_run_var.get())
        self.cfg_mgr.save(self.config_obj)
        self.journal.log_event(
            "Settings",
            "toggle_dry_run",
            f"Dry-Run Mode set to {self.config_obj.dry_run_default}",
            "User toggled global preview safety switch",
            "ok",
        )
        self.show_toast(
            f"Dry-Run Mode {'ON (Zero disk mutations)' if self.config_obj.dry_run_default else 'OFF'}"
        )

    def open_easy_guide(self) -> None:
        EasyGuideDialog(self, self.palette, self.fonts)

    def open_help_search(self, initial_query: str = "") -> None:
        HelpSearchDialog(
            self,
            self.palette,
            self.fonts,
            on_navigate=self.switch_nav,
            initial_query=initial_query,
        )

    def open_uninstall_wizard(self) -> None:
        _, q_count = self.rollback_mgr.quarantine_stats()

        def _do_uninstall(del_data: bool, restore_q: bool, uninst_pkg: bool) -> None:
            summary = self.lifecycle.uninstall_astracradle(
                self._active_adapter(),
                self.config_obj,
                delete_user_data=del_data,
                restore_quarantine_first=restore_q,
                uninstall_pip_package=uninst_pkg,
            )
            messagebox.showinfo(
                "AstraCradle Uninstalled",
                "\n".join(summary.notes) + "\n\nAstraCradle will now close cleanly.",
            )
            self.destroy()

        UninstallDialog(
            self,
            self.palette,
            self.fonts,
            str(self.cfg_mgr.base_dir),
            q_count,
            _do_uninstall,
        )

    def on_create_shortcuts(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        created = self.lifecycle.install_shortcuts(repo_root)
        self.show_toast(f"Created {len(created)} Desktop / App Menu shortcut(s)!")

    def open_consent_wizard(self, first_run: bool = False) -> None:
        def on_save(perms: dict[str, bool], scan_mode: str | None = None) -> None:
            trigger_full = bool(perms.pop("_trigger_full_discovery", False))
            for k, v in perms.items():
                if hasattr(self.config_obj, k):
                    setattr(self.config_obj, k, v)
            self.config_obj.consent_prompted = True
            self.cfg_mgr.save(self.config_obj)
            mode = scan_mode or ("full" if trigger_full else ("selected" if any(perms.values()) else "minimal"))
            self.journal.log_event(
                "ConsentGate",
                "update_permissions",
                f"Saved local permissions ({mode}): {perms}",
                "User configured explicit local telemetry, PC discovery, and automation consent",
                "ok",
            )
            if mode == "full" or (mode == "selected" and any(perms.values())):
                self.show_toast("Permission granted — running full local PC baseline discovery...")
                self.run_full_pc_discovery_async(show_dialog=True)
                self._schedule_telemetry_poll()
            else:
                self.show_toast("Minimal mode saved — no deep telemetry scan run.")
                self.dash_headline.configure(text="Minimal Privacy Mode Active")
                self.dash_summary.configure(
                    text="Telemetry scanning is currently minimized per your choice. Open Permissions anytime to grant local scan access."
                )

        ConsentAutomationDialog(
            self,
            self.palette,
            self.fonts,
            self.config_obj,
            on_save,
            first_run=first_run or not self.config_obj.consent_prompted,
        )

    def run_full_pc_discovery_async(self, show_dialog: bool = True) -> None:
        if not self.config_obj.consent_telemetry:
            self.open_consent_wizard(first_run=False)
            return
        self.topbar_status.configure(text="Discovering PC...")
        self.topbar_beacon.set_scanning(True)

        def worker() -> None:
            try:
                out = self.full_discovery.run_full_discovery(self._active_adapter(), self.config_obj)
                self.config_obj.first_run_scan_completed = True
                self.cfg_mgr.save(self.config_obj)
                self._ui_queue.put(("full_discovery_done", out, show_dialog))
            except Exception as exc:
                self._ui_queue.put(("full_discovery_err", str(exc), None))

        threading.Thread(target=worker, daemon=True).start()

    def open_automation_prompt(self, rec: ProcessRecommendation) -> None:
        def on_decision(choice: str) -> None:
            if choice == "automate":
                self.config_obj.consent_auto_remediate = True
                for r in self.config_obj.automation_rules:
                    if r.get("rule_id") == "rule_idle_ai_prompt":
                        r["enabled"] = True
                self.cfg_mgr.save(self.config_obj)
                if rec.pid:
                    ok, msg = self.executor.safe_terminate_process(
                        rec.pid, self.config_obj, why="User chose Run & Automate via Popup Assistant"
                    )
                    self.show_toast(msg)
                else:
                    self.show_toast("Automation enabled for idle AI chatbot detection.")
            elif choice == "once":
                if rec.pid:
                    ok, msg = self.executor.safe_terminate_process(
                        rec.pid, self.config_obj, why="User chose Run Once via Popup Assistant"
                    )
                    self.show_toast(msg)
            else:
                self.show_toast("Skipped — no process altered.")
            self.refresh_telemetry_async(include_dns=False, also_scan=True)

        AutomationPromptDialog(
            self,
            self.palette,
            self.fonts,
            f"Automate Handling for {rec.name}?",
            f"{rec.reason}\n\nWould you like AstraCradle to handle this once or automate it from now on?",
            on_decision,
        )

    # ------------------------------------------------------------------
    # Page 1: Command Deck
    # ------------------------------------------------------------------
    def _build_dashboard_page(self) -> None:
        p = self.palette
        page = ctk.CTkScrollableFrame(self.pages["dashboard"], fg_color="transparent")
        page.pack(fill="both", expand=True)
        page.grid_columnconfigure((0, 1), weight=1)

        hero = ctk.CTkFrame(
            page, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
        )
        hero.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        hero.grid_columnconfigure(1, weight=1)

        self.gauge = EquilibriumGauge(hero, p, size=185, animate=not self.config_obj.low_power_ui_mode)
        self.gauge.grid(row=0, column=0, padx=18, pady=14)
        attach_tooltip(
            self.gauge,
            "Dyson Equilibrium Score (0–100): combines CPU, RAM, Disk headroom, DNS speed & dormant AI bloat.",
            p,
        )

        info_col = ctk.CTkFrame(hero, fg_color="transparent")
        info_col.grid(row=0, column=1, sticky="nsew", padx=(4, 16), pady=14)

        self.dash_headline = ctk.CTkLabel(
            info_col, text="Initializing Dyson-Core Telemetry...", font=self.fonts["title"], text_color=p.text_primary, anchor="w"
        )
        self.dash_headline.pack(fill="x", pady=(4, 4))

        self.dash_summary = ctk.CTkLabel(
            info_col,
            text="Running silent 7-invariant self-check and collecting local diagnostics.",
            font=self.fonts["body"],
            text_color=p.text_secondary,
            justify="left",
            wraplength=480,
            anchor="w",
        )
        self.dash_summary.pack(fill="x", pady=(0, 8))

        self.dash_meta = ctk.CTkLabel(
            info_col, text="Host: -- | Uptime: --", font=self.fonts["mono_small"], text_color=p.text_muted, anchor="w"
        )
        self.dash_meta.pack(fill="x", pady=(0, 10))

        btn_row = ctk.CTkFrame(info_col, fg_color="transparent")
        btn_row.pack(fill="x")
        self.dash_btn_row = btn_row
        self._cmd_btns = [
            ctk.CTkButton(
                btn_row,
                text="Fix Everything Safe",
                height=34,
                corner_radius=8,
                fg_color=p.accent_jade,
                hover_color=p.accent_jade_hover,
                text_color="#071510",
                font=self.fonts["body_bold"],
                command=self.on_fix_everything_safe,
            ),
            ctk.CTkButton(
                btn_row,
                text="Full PC Discovery",
                height=34,
                corner_radius=8,
                fg_color=p.accent_cyan,
                hover_color=p.accent_cyan_hover,
                text_color="#071217",
                font=self.fonts["body_bold"],
                command=lambda: self.run_full_pc_discovery_async(show_dialog=True),
            ),
            ctk.CTkButton(
                btn_row,
                text="AstraShield AdBlocker",
                height=34,
                corner_radius=8,
                fg_color=p.bg_elevated,
                hover_color=p.border_strong,
                font=self.fonts["body_bold"],
                command=lambda: (self._on_module_tab("Privacy & Shield"), self.switch_nav("modules")),
            ),
            ctk.CTkButton(
                btn_row,
                text="Weekly Digest",
                height=34,
                corner_radius=8,
                fg_color=p.bg_elevated,
                hover_color=p.border_strong,
                font=self.fonts["body"],
                command=self.on_show_weekly_digest,
            ),
            ctk.CTkButton(
                btn_row,
                text="Self-Heal Check",
                height=34,
                corner_radius=8,
                fg_color="transparent",
                border_width=1,
                border_color=p.border_strong,
                text_color=p.text_secondary,
                font=self.fonts["body"],
                command=self.on_run_self_heal,
            ),
        ]
        init_cols = 5 if (self.winfo_screenwidth() or 1300) >= 1650 else 3
        for c in range(init_cols):
            btn_row.grid_columnconfigure(c, weight=1)
        for idx, b in enumerate(self._cmd_btns):
            r, c = (0, idx) if init_cols == 5 else divmod(idx, 3)
            b.grid(row=r, column=c, sticky="ew", padx=3, pady=3)

        self.cpu_card, self.cpu_val_lbl, self.cpu_sub_lbl, self.cpu_spark = self._make_metric_card(
            page, "CPU & Compute Load", p.accent_cyan
        )
        self.cpu_card.grid(row=1, column=0, sticky="nsew", padx=(0, 6), pady=5)

        self.ram_card, self.ram_val_lbl, self.ram_sub_lbl, self.ram_spark = self._make_metric_card(
            page, "Memory & Swap Pressure", p.accent_jade
        )
        self.ram_card.grid(row=1, column=1, sticky="nsew", padx=(6, 0), pady=5)

        self.disk_card, self.disk_val_lbl, self.disk_sub_lbl, self.disk_spark = self._make_metric_card(
            page, "Storage & Reclaimable Clutter", p.accent_brass
        )
        self.disk_card.grid(row=2, column=0, sticky="nsew", padx=(0, 6), pady=5)

        self.net_card, self.net_val_lbl, self.net_sub_lbl, self.net_spark = self._make_metric_card(
            page, "Network, DNS & Thermals", p.accent_cyan
        )
        self.net_card.grid(row=2, column=1, sticky="nsew", padx=(6, 0), pady=5)

        # Row 3: Live GPU / VRAM / Thermal Throttling + 30-Day SQLite History Graph
        self.gpu_card, self.gpu_val_lbl, self.gpu_sub_lbl, self.gpu_spark = self._make_metric_card(
            page, "GPU, VRAM & Thermals", p.accent_jade
        )
        self.gpu_card.grid(row=3, column=0, sticky="nsew", padx=(0, 6), pady=5)

        hist_card = ctk.CTkFrame(
            page, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
        )
        hist_card.grid(row=3, column=1, sticky="nsew", padx=(6, 0), pady=5)
        hist_hdr = ctk.CTkFrame(hist_card, fg_color="transparent")
        hist_hdr.pack(fill="x", padx=14, pady=(8, 2))
        ctk.CTkLabel(
            hist_hdr, text="Telemetry History", font=self.fonts["body_bold"], text_color=p.text_secondary
        ).pack(side="left")
        self.hist_res_seg = ctk.CTkSegmentedButton(
            hist_hdr,
            values=["60s", "5m", "30m", "1hr", "30d"],
            height=24,
            font=self.fonts["caption"],
            command=self._on_hist_resolution,
        )
        self.hist_res_seg.set(self.config_obj.telemetry_resolution)
        self.hist_res_seg.pack(side="right")
        attach_tooltip(self.hist_res_seg, "Switch history window between live 60s/5m/30m/1hr ring and 30-day SQLite store.", p)
        self.history_chart = TelemetryHistoryChart(hist_card, p, width=420, height=96)
        self.history_chart.resolution = self.config_obj.telemetry_resolution
        self.history_chart.pack(fill="both", expand=True, padx=12, pady=(0, 8))

        self.dash_findings_frame = ctk.CTkFrame(
            page, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
        )
        self.dash_findings_frame.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(8, 6))

        # Quick Power Tools Strip: Micro-Benchmark, App Auto-Detector, Recovery Bundle, Weekly Digest
        tools = ctk.CTkFrame(
            page, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
        )
        tools.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(4, 6))
        thdr = ctk.CTkFrame(tools, fg_color="transparent")
        thdr.pack(fill="x", padx=16, pady=(10, 4))
        ctk.CTkLabel(thdr, text="Benchmark, App Auto-Detector & Portable Reports", font=self.fonts["body_bold"]).pack(
            side="left"
        )
        self.bench_lbl = ctk.CTkLabel(
            thdr, text="No benchmark run yet", font=self.fonts["mono_small"], text_color=p.accent_cyan
        )
        self.bench_lbl.pack(side="right")

        tbtns = ctk.CTkFrame(tools, fg_color="transparent")
        tbtns.pack(fill="x", padx=16, pady=(2, 12))
        tbtns.grid_columnconfigure((0, 1, 2), weight=1)

        b_bench = ctk.CTkButton(
            tbtns,
            text="Run Before/After Benchmark",
            height=32,
            fg_color=p.accent_cyan,
            text_color="#071217",
            font=self.fonts["small"],
            command=self.on_run_benchmark,
        )
        b_bench.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        attach_tooltip(b_bench, "Runs a 1-second local CPU, memory, and temp disk test to compare Before vs After speed.", p)

        b_bundle = ctk.CTkButton(
            tbtns,
            text="Export Recovery Bundle (.zip)",
            height=32,
            fg_color=p.bg_elevated,
            hover_color=p.border_strong,
            font=self.fonts["small"],
            command=self.on_export_recovery_bundle,
        )
        b_bundle.grid(row=0, column=1, sticky="ew", padx=4)
        attach_tooltip(b_bundle, "Packs config, forensic journal, crash reports & rollback ledger into one portable zip.", p)

        b_digest = ctk.CTkButton(
            tbtns,
            text="Generate Weekly Digest (.html)",
            height=32,
            fg_color=p.bg_elevated,
            hover_color=p.border_strong,
            font=self.fonts["small"],
            command=self.on_generate_weekly_digest,
        )
        b_digest.grid(row=0, column=2, sticky="ew", padx=4)
        attach_tooltip(b_digest, "Creates an offline visual HTML health report inside ~/.astracradle/playbooks.", p)

        self.auto_det_var = tk.BooleanVar(value=self.config_obj.app_auto_detect_enabled)
        b_autodet = ctk.CTkSwitch(
            tbtns,
            text="Auto-Detect Games/3D Apps",
            variable=self.auto_det_var,
            font=self.fonts["small"],
            progress_color=p.accent_jade,
            command=self._on_toggle_auto_detect,
        )
        b_autodet.grid(row=0, column=3, sticky="e", padx=(8, 0))
        attach_tooltip(
            b_autodet,
            "Automatically activates matching Toggleable Scripts when Blender, Steam, Cargo, or Unreal launch.",
            p,
        )

    def _make_metric_card(self, parent, title: str, color: str):
        p = self.palette
        card = ctk.CTkFrame(
            parent, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
        )
        hdr = ctk.CTkFrame(card, fg_color="transparent")
        hdr.pack(fill="x", padx=16, pady=(10, 2))
        ctk.CTkLabel(hdr, text=title, font=self.fonts["body_bold"], text_color=p.text_secondary).pack(side="left")
        val_lbl = ctk.CTkLabel(hdr, text="--", font=self.fonts["section"], text_color=p.text_primary)
        val_lbl.pack(side="right")
        sub_lbl = ctk.CTkLabel(card, text="Collecting...", font=self.fonts["small"], text_color=p.text_muted, anchor="w")
        sub_lbl.pack(fill="x", padx=16, pady=(0, 4))
        spark = TelemetrySparkline(card, p, line_color=color, height=56)
        spark.pack(fill="x", padx=14, pady=(0, 10))
        return card, val_lbl, sub_lbl, spark

    # ------------------------------------------------------------------
    # Page 2: Diagnostics & Playbook
    # ------------------------------------------------------------------
    def _build_playbook_page(self) -> None:
        p = self.palette
        root = self.pages["playbook"]
        root.grid_rowconfigure(1, weight=1)
        root.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(
            root, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
        )
        header.grid(row=0, column=0, sticky="ew", pady=(0, 10))

        top_line = ctk.CTkFrame(header, fg_color="transparent")
        top_line.pack(fill="x", padx=16, pady=(12, 6))
        self.pb_headline = ctk.CTkLabel(
            top_line, text="Diagnosis Playbook", font=self.fonts["section"], text_color=p.text_primary
        )
        self.pb_headline.pack(side="left")

        btn_grp = ctk.CTkFrame(top_line, fg_color="transparent")
        btn_grp.pack(side="right")
        ctk.CTkButton(
            btn_grp,
            text="Execute All Safe Steps",
            height=30,
            corner_radius=7,
            fg_color=p.accent_jade,
            hover_color=p.accent_jade_hover,
            text_color="#071510",
            font=self.fonts["small"],
            command=self.on_safe_auto_optimize,
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            btn_grp,
            text="Export .MD",
            height=30,
            width=88,
            corner_radius=7,
            fg_color=p.bg_elevated,
            hover_color=p.border_strong,
            font=self.fonts["small"],
            command=lambda: self.on_export_playbook("md"),
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            btn_grp,
            text="Export .JSON",
            height=30,
            width=92,
            corner_radius=7,
            fg_color=p.bg_elevated,
            hover_color=p.border_strong,
            font=self.fonts["small"],
            command=lambda: self.on_export_playbook("json"),
        ).pack(side="left", padx=4)

        self.pb_summary_lbl = ctk.CTkLabel(
            header,
            text="Scanning...",
            font=self.fonts["body"],
            text_color=p.text_secondary,
            wraplength=self._content_wrap(140),
            justify="left",
            anchor="w",
        )
        self.pb_summary_lbl.pack(fill="x", padx=16, pady=(0, 8))

        filter_bar = ctk.CTkFrame(header, fg_color="transparent")
        filter_bar.pack(fill="x", padx=16, pady=(0, 12))
        self.pb_filter_seg = ctk.CTkSegmentedButton(
            filter_bar,
            values=["All Steps", "Auto-Fix Supported", "Manual DIY Only"],
            command=self._on_pb_filter_change,
        )
        self.pb_filter_seg.set("All Steps")
        self.pb_filter_seg.pack(side="left")

        self.pb_scroll = ctk.CTkScrollableFrame(root, fg_color="transparent")
        self.pb_scroll.grid(row=1, column=0, sticky="nsew")

    def _on_pb_filter_change(self, val: str) -> None:
        self.playbook_filter = {"All Steps": "all", "Auto-Fix Supported": "auto", "Manual DIY Only": "manual"}.get(
            val, "all"
        )
        self._render_playbook()

    def _render_playbook(self) -> None:
        p = self.palette
        for c in self.pb_scroll.winfo_children():
            c.destroy()
        pb = self.latest_playbook
        if pb is None:
            return
        self.pb_headline.configure(text=f"{pb.summary_headline}  (Score {pb.health_score}/100)")
        self.pb_summary_lbl.configure(text=pb.executive_summary)

        for step in pb.steps:
            if self.playbook_filter == "auto" and not step.can_auto_fix:
                continue
            if self.playbook_filter == "manual" and step.can_auto_fix:
                continue
            self._render_playbook_card(step)

    def _render_playbook_card(self, step: PlaybookStep) -> None:
        p = self.palette
        card = ctk.CTkFrame(
            self.pb_scroll, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
        )
        card.pack(fill="x", pady=5)

        hdr = ctk.CTkFrame(card, fg_color="transparent")
        hdr.pack(fill="x", padx=16, pady=(12, 4))
        ctk.CTkLabel(
            hdr, text=f"Step {step.step_number}: {step.title}", font=self.fonts["section"], text_color=p.text_primary
        ).pack(side="left")
        make_badge(hdr, step.severity.upper(), p.badge_info_bg, p.badge_info_fg, self.fonts["caption"]).pack(
            side="right", padx=3
        )
        conf_pct = int(getattr(step, "confidence_percent", 85))
        make_badge(
            hdr,
            f"CONF {conf_pct}%",
            p.badge_safe_bg if conf_pct >= 80 else p.badge_warn_bg,
            p.badge_safe_fg if conf_pct >= 80 else p.badge_warn_fg,
            self.fonts["caption"],
        ).pack(side="right", padx=3)
        make_badge(hdr, f"RISK: {step.risk_level.upper()}", p.bg_elevated, p.text_secondary, self.fonts["caption"]).pack(
            side="right", padx=3
        )
        make_badge(hdr, step.subsystem.upper(), p.bg_elevated, p.accent_cyan, self.fonts["caption"]).pack(
            side="right", padx=3
        )

        ctk.CTkLabel(
            card, text=step.summary, font=self.fonts["body"], text_color=p.text_secondary, wraplength=self._content_wrap(150), justify="left", anchor="w"
        ).pack(fill="x", padx=16, pady=(2, 4))
        ev_sigs = getattr(step, "evidence_signals", []) or []
        ev_extra = f" | Signals: {'; '.join(ev_sigs[:2])}" if ev_sigs else ""
        ctk.CTkLabel(
            card,
            text=f"Evidence: {step.evidence}{ev_extra}",
            font=self.fonts["mono_small"],
            text_color=p.text_muted,
            wraplength=self._content_wrap(150),
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=16, pady=(0, 8))

        diy = ctk.CTkFrame(card, fg_color=p.bg_elevated, corner_radius=8)
        diy.pack(fill="x", padx=16, pady=(0, 10))
        ctk.CTkLabel(
            diy,
            text="DIY Manual Instructions (Do it yourself or let AstraCradle handle it):",
            font=self.fonts["small"],
            text_color=p.accent_brass,
            anchor="w",
        ).pack(fill="x", padx=12, pady=(8, 4))
        for i, ms in enumerate(step.manual_steps, start=1):
            ctk.CTkLabel(
                diy, text=f"  {i}. {ms}", font=self.fonts["small"], text_color=p.text_primary, wraplength=self._content_wrap(180), justify="left", anchor="w"
            ).pack(fill="x", padx=12, pady=1)

        if step.manual_commands:
            cmd_txt = "\n".join(step.manual_commands)
            cmd_row = ctk.CTkFrame(diy, fg_color=p.bg_input, corner_radius=6)
            cmd_row.pack(fill="x", padx=12, pady=(6, 10))
            ctk.CTkLabel(
                cmd_row, text=cmd_txt, font=self.fonts["mono_small"], text_color=p.accent_cyan, justify="left", anchor="w"
            ).pack(side="left", padx=10, pady=8, fill="x", expand=True)
            ctk.CTkButton(
                cmd_row,
                text="Copy",
                width=64,
                height=26,
                corner_radius=6,
                fg_color=p.bg_elevated,
                hover_color=p.border_strong,
                font=self.fonts["caption"],
                command=lambda txt=cmd_txt: self._copy_text(txt),
            ).pack(side="right", padx=8, pady=6)
        else:
            ctk.CTkFrame(diy, height=6, fg_color="transparent").pack()

        ftr = ctk.CTkFrame(card, fg_color="transparent")
        ftr.pack(fill="x", padx=16, pady=(0, 10))
        st_txt = f"Status: {step.status.upper()}" + (f" — {step.result_note}" if step.result_note else "")
        ctk.CTkLabel(ftr, text=st_txt, font=self.fonts["small"], text_color=p.text_muted).pack(side="left")
        if step.can_auto_fix and step.action_id:
            ctk.CTkButton(
                ftr,
                text="Have AstraCradle Do It",
                height=30,
                corner_radius=7,
                fg_color=p.accent_cyan,
                hover_color=p.accent_cyan_hover,
                text_color="#071217",
                font=self.fonts["small"],
                command=lambda s=step: self.on_execute_playbook_step(s),
            ).pack(side="right")

    # ------------------------------------------------------------------
    # Page 3: AI Chatbot & Unused Process Hunter
    # ------------------------------------------------------------------
    def _build_hunter_page(self) -> None:
        self.hunter_scroll = ctk.CTkScrollableFrame(self.pages["hunter"], fg_color="transparent")
        self.hunter_scroll.pack(fill="both", expand=True)

    def _render_hunter(self) -> None:
        p = self.palette
        for c in self.hunter_scroll.winfo_children():
            c.destroy()

        hdr = ctk.CTkFrame(
            self.hunter_scroll, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
        )
        hdr.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(
            hdr,
            text="AI Chatbot, Idle Helper & Unused Program Hunter",
            font=self.fonts["section"],
            text_color=p.text_primary,
        ).pack(side="left", padx=16, pady=12)
        ctk.CTkButton(
            hdr,
            text="Permission Settings",
            height=30,
            corner_radius=7,
            fg_color=p.bg_elevated,
            hover_color=p.border_strong,
            font=self.fonts["small"],
            command=self.open_consent_wizard,
        ).pack(side="right", padx=(6, 16), pady=10)
        ctk.CTkButton(
            hdr,
            text="Rescan Processes",
            height=30,
            corner_radius=7,
            fg_color=p.accent_cyan,
            hover_color=p.accent_cyan_hover,
            text_color="#071217",
            font=self.fonts["small"],
            command=self.on_deep_scan,
        ).pack(side="right", padx=4, pady=10)

        if not self.config_obj.consent_process_scan:
            ctk.CTkLabel(
                self.hunter_scroll,
                text="Process & AI Chatbot scanning is currently disabled in your Permissions. Click 'Permission Settings' to enable local inspection.",
                font=self.fonts["body"],
                text_color=p.accent_brass,
            ).pack(pady=24)
            return

        recs = self.latest_report.recommendations if self.latest_report else []
        if not recs:
            card = ctk.CTkFrame(
                self.hunter_scroll, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
            )
            card.pack(fill="x", pady=6)
            ctk.CTkLabel(
                card,
                text="No idle AI chatbot daemons (Ollama, LM Studio, GPT4All, KoboldCpp, Copilot, etc.) or dormant helpers currently wasting RAM.",
                font=self.fonts["body"],
                text_color=p.accent_jade,
                wraplength=self._content_wrap(160),
                justify="left",
            ).pack(padx=16, pady=18, anchor="w")
            return

        for rec in recs:
            card = ctk.CTkFrame(
                self.hunter_scroll, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=11
            )
            card.pack(fill="x", pady=4)
            top = ctk.CTkFrame(card, fg_color="transparent")
            top.pack(fill="x", padx=14, pady=(10, 4))

            cat_lbl = "AI CHATBOT" if rec.category == "ai_chatbot" else rec.category.upper()
            bg, fg = (
                (p.badge_warn_bg, p.badge_warn_fg)
                if rec.category == "ai_chatbot"
                else (p.badge_info_bg, p.badge_info_fg)
            )
            make_badge(top, cat_lbl, bg, fg, self.fonts["caption"]).pack(side="left", padx=(0, 8))
            ctk.CTkLabel(top, text=rec.name, font=self.fonts["body_bold"], text_color=p.text_primary).pack(side="left")

            ctk.CTkButton(
                top,
                text="Ignore",
                width=70,
                height=26,
                corner_radius=6,
                fg_color=p.bg_elevated,
                hover_color=p.border_strong,
                font=self.fonts["caption"],
                command=lambda r=rec: self.on_ignore_recommendation(r),
            ).pack(side="right", padx=(4, 0))
            ctk.CTkButton(
                top,
                text="Automate Popup",
                width=112,
                height=26,
                corner_radius=6,
                fg_color=p.accent_cyan,
                hover_color=p.accent_cyan_hover,
                text_color="#071217",
                font=self.fonts["caption"],
                command=lambda r=rec: self.open_automation_prompt(r),
            ).pack(side="right", padx=4)
            if rec.pid:
                ctk.CTkButton(
                    top,
                    text="Turn Off Now",
                    width=96,
                    height=26,
                    corner_radius=6,
                    fg_color=p.badge_crit_bg,
                    hover_color=p.accent_coral,
                    text_color=p.badge_crit_fg,
                    font=self.fonts["caption"],
                    command=lambda pid=rec.pid, nm=rec.target_ref: self.on_terminate_proc(pid, nm),
                ).pack(side="right", padx=4)

            ctk.CTkLabel(
                card,
                text=rec.reason,
                font=self.fonts["small"],
                text_color=p.text_secondary,
                wraplength=self._content_wrap(160),
                justify="left",
                anchor="w",
            ).pack(fill="x", padx=14, pady=(0, 10))

    def on_ignore_recommendation(self, rec: ProcessRecommendation) -> None:
        key = f"proc:{rec.target_ref.lower()}" if rec.pid else rec.rec_id
        if key not in self.config_obj.ignored_recommendations:
            self.config_obj.ignored_recommendations.append(key)
            self.cfg_mgr.save(self.config_obj)
        self.show_toast(f"Ignored '{rec.name}' from future recommendations.")
        self.refresh_telemetry_async(include_dns=False, also_scan=True)

    # ------------------------------------------------------------------
    # Page 4: Toggleable Scripts
    # ------------------------------------------------------------------
    def _build_scripts_page(self) -> None:
        self.scripts_scroll = ctk.CTkScrollableFrame(self.pages["scripts"], fg_color="transparent")
        self.scripts_scroll.pack(fill="both", expand=True)

    def _render_scripts(self) -> None:
        p = self.palette
        for c in self.scripts_scroll.winfo_children():
            c.destroy()

        hdr = ctk.CTkFrame(
            self.scripts_scroll, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
        )
        hdr.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(
            hdr,
            text="Toggleable Workload Scripts (Add, Toggle ON/OFF, or Remove Anytime)",
            font=self.fonts["section"],
            text_color=p.text_primary,
        ).pack(side="left", padx=16, pady=12)
        ctk.CTkButton(
            hdr,
            text="+ Add Custom Script",
            height=30,
            corner_radius=7,
            fg_color=p.accent_cyan,
            hover_color=p.accent_cyan_hover,
            text_color="#071217",
            font=self.fonts["small"],
            command=self.open_add_script_modal,
        ).pack(side="right", padx=16, pady=10)

        for sc in self.scripts_engine.load_scripts():
            card = ctk.CTkFrame(
                self.scripts_scroll, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=11
            )
            card.pack(fill="x", pady=5)
            top = ctk.CTkFrame(card, fg_color="transparent")
            top.pack(fill="x", padx=16, pady=(12, 4))

            sw_var = tk.BooleanVar(value=sc.enabled)
            ctk.CTkSwitch(
                top,
                text=sc.name,
                variable=sw_var,
                font=self.fonts["section"],
                progress_color=p.accent_jade,
                command=lambda s=sc, v=sw_var: self.on_toggle_script(s, bool(v.get())),
            ).pack(side="left")

            ctk.CTkButton(
                top,
                text="Remove",
                width=72,
                height=26,
                corner_radius=6,
                fg_color="transparent",
                border_width=1,
                border_color=p.border_strong,
                text_color=p.text_muted,
                font=self.fonts["caption"],
                command=lambda sid=sc.script_id: self.on_remove_script(sid),
            ).pack(side="right", padx=(6, 0))
            make_badge(top, sc.category.upper(), p.bg_elevated, p.accent_brass, self.fonts["caption"]).pack(
                side="right", padx=4
            )

            ctk.CTkLabel(
                card,
                text=sc.description,
                font=self.fonts["body"],
                text_color=p.text_secondary,
                wraplength=self._content_wrap(160),
                justify="left",
                anchor="w",
            ).pack(fill="x", padx=16, pady=(2, 6))

            for st in sc.steps_summary:
                ctk.CTkLabel(
                    card, text=f"  • {st}", font=self.fonts["small"], text_color=p.text_muted, anchor="w"
                ).pack(fill="x", padx=18, pady=1)
            ctk.CTkFrame(card, height=8, fg_color="transparent").pack()

    def on_toggle_script(self, script: OptimizationScript, enable: bool) -> None:
        procs = self.latest_snapshot.top_processes if self.latest_snapshot else []
        ok, msg = self.scripts_engine.toggle_script(
            script.script_id,
            enable,
            self._active_adapter(),
            self.config_obj,
            procs,
            dry_run=bool(self.dry_run_var.get()),
        )
        self.footer_prof_lbl.configure(text=f"Profile: {self.config_obj.active_profile}")
        self.show_toast(msg)
        self._render_scripts()
        self.refresh_telemetry_async(include_dns=False, also_scan=True)

    def on_remove_script(self, script_id: str) -> None:
        ok, msg = self.scripts_engine.remove_script(script_id)
        self.show_toast(msg)
        self._render_scripts()

    def open_add_script_modal(self) -> None:
        def on_create(name: str, cat: str, desc: str, cmd: str, prof: str) -> None:
            sc = self.scripts_engine.add_custom_script(name, cat, desc, cmd, prof)
            self.show_toast(f"Added toggleable script '{sc.name}'.")
            self._render_scripts()

        CustomScriptDialog(self, self.palette, self.fonts, on_create)

    # ------------------------------------------------------------------
    # Page 5: Hardware, Visual Pipelines & GPU Thermal Studio
    # ------------------------------------------------------------------
    def _build_hardware_page(self) -> None:
        root = self.pages["hardware"]
        root.grid_rowconfigure(1, weight=1)
        root.grid_columnconfigure(0, weight=1)

        bar = ctk.CTkFrame(root, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        self.hw_seg = ctk.CTkSegmentedButton(
            bar,
            values=["Component Library & Presets", "Visual Pipelines & Packs", "GPU Power & Fan Curves"],
            font=self.fonts["small"],
            command=self._on_hw_tab,
        )
        self.hw_seg.set("Component Library & Presets")
        self.hw_seg.pack(fill="x", expand=True)
        attach_tooltip(
            self.hw_seg,
            "Manage hardware component profiles & CPU/I/O presets, visual conditional pipelines, or GPU power/fan envelopes.",
            self.palette,
        )

        self.hw_body = ctk.CTkScrollableFrame(root, fg_color="transparent")
        self.hw_body.grid(row=1, column=0, sticky="nsew")

    def _on_hw_tab(self, name: str) -> None:
        self.current_hw_tab = name
        if hasattr(self, "hw_seg") and self.hw_seg.get() != name:
            self.hw_seg.set(name)
        self._render_hardware_page()

    def _render_hardware_page(self) -> None:
        for c in self.hw_body.winfo_children():
            c.destroy()
        if self.current_hw_tab == "Component Library & Presets":
            self._render_hardware_presets_module(parent=self.hw_body)
        elif self.current_hw_tab == "Visual Pipelines & Packs":
            self._render_pipelines_module(parent=self.hw_body)
        else:
            self._render_gpu_power_fan_panel(parent=self.hw_body)

    # ------------------------------------------------------------------
    # Page 6: System Modules
    # ------------------------------------------------------------------
    def _build_modules_page(self) -> None:
        root = self.pages["modules"]
        root.grid_rowconfigure(1, weight=1)
        root.grid_columnconfigure(0, weight=1)

        bar = ctk.CTkFrame(root, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        self.mod_seg = ctk.CTkSegmentedButton(
            bar,
            values=[
                "Storage",
                "Startup",
                "Processes & Firewall",
                "Installed Apps",
                "Duplicates & Bloat",
                "Privacy & Shield",
                "Glitch & OS Tools",
            ],
            font=self.fonts["caption"],
            command=self._on_module_tab,
        )
        self.mod_seg.set("Storage")
        self.mod_seg.pack(fill="x", expand=True)
        attach_tooltip(
            self.mod_seg,
            "Switch between cache cleanup, startup apps, processes & network firewall, apps, SHA-256 duplicates, privacy shield, and glitch/OS tools.",
            self.palette,
        )

        self.mod_body = ctk.CTkScrollableFrame(root, fg_color="transparent")
        self.mod_body.grid(row=1, column=0, sticky="nsew")

    def _on_module_tab(self, name: str) -> None:
        self.current_module_tab = name
        if hasattr(self, "mod_seg") and self.mod_seg.get() != name:
            self.mod_seg.set(name)
        self._render_modules()

    def _render_modules(self) -> None:
        for c in self.mod_body.winfo_children():
            c.destroy()
        tab = self.current_module_tab
        if tab == "Storage":
            self._render_storage_module()
        elif tab == "Startup":
            self._render_startup_module()
        elif tab in ("Processes", "Processes & Firewall"):
            self._render_processes_module()
        elif tab == "Installed Apps":
            self._render_apps_module()
        elif tab == "Duplicates & Bloat":
            self._render_bloat_module()
        elif tab == "Privacy & Shield":
            self._render_privacy_shield_module()
        else:
            self._render_glitch_specs_module()
            self._render_toolkit_module()

    def _render_storage_module(self) -> None:
        p = self.palette
        if not self.latest_report:
            return
        hdr = ctk.CTkFrame(
            self.mod_body, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
        )
        hdr.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(
            hdr, text="Allow-Listed Cache & Temp Targets (Protected Paths Locked)", font=self.fonts["body_bold"], text_color=p.text_primary
        ).pack(side="left", padx=14, pady=10)
        ctk.CTkButton(
            hdr,
            text="Clean Selected (Quarantine)",
            height=30,
            corner_radius=7,
            fg_color=p.accent_jade,
            hover_color=p.accent_jade_hover,
            text_color="#071510",
            font=self.fonts["small"],
            command=self.on_clean_selected_caches,
        ).pack(side="right", padx=14, pady=8)

        for t in self.latest_report.cache_targets:
            var = self._cache_check_vars.get(t.target_id)
            if var is None:
                var = tk.BooleanVar(value=t.selected_by_default and t.exists)
                self._cache_check_vars[t.target_id] = var
            row = ctk.CTkFrame(
                self.mod_body, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=10
            )
            row.pack(fill="x", pady=4)
            ctk.CTkCheckBox(row, text=t.name, variable=var, font=self.fonts["body_bold"]).pack(
                side="left", padx=14, pady=10
            )
            ctk.CTkLabel(
                row,
                text=f"{format_bytes(t.size_bytes)} ({t.file_count} files)" if t.exists else "Not present",
                font=self.fonts["mono_small"],
                text_color=p.accent_cyan if t.size_bytes > 0 else p.text_muted,
            ).pack(side="right", padx=14)

    def _render_startup_module(self) -> None:
        p = self.palette
        if not self.latest_report:
            return
        items = self.latest_report.startup_items
        if not items:
            ctk.CTkLabel(
                self.mod_body, text="Zero third-party user startup items detected.", font=self.fonts["body"], text_color=p.accent_jade
            ).pack(pady=24)
            return
        for s in items:
            row = ctk.CTkFrame(
                self.mod_body, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=10
            )
            row.pack(fill="x", pady=3)
            ctk.CTkLabel(row, text=s.name, font=self.fonts["body_bold"], text_color=p.text_primary).pack(
                side="left", padx=14, pady=9
            )
            delay_btn = ctk.CTkButton(
                row,
                text="Delay +60s",
                width=92,
                height=26,
                corner_radius=6,
                fg_color=p.bg_elevated,
                hover_color=p.border_strong,
                font=self.fonts["caption"],
                command=lambda item=s: self._delay_startup_item(item),
            )
            delay_btn.pack(side="right", padx=(6, 12), pady=6)
            attach_tooltip(delay_btn, "Delays this startup app by 60s after login so your desktop boots faster.", p)
            ctk.CTkLabel(row, text=s.location, font=self.fonts["mono_small"], text_color=p.text_muted).pack(
                side="right", padx=8
            )

    def _render_processes_module(self) -> None:
        p = self.palette
        # Per-App Network Firewall & Bandwidth Shaper Card
        fw_card = ctk.CTkFrame(
            self.mod_body, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
        )
        fw_card.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(
            fw_card, text="Per-App Network Firewall & Bandwidth Shaper (Reversible Rules)", font=self.fonts["body_bold"]
        ).pack(anchor="w", padx=14, pady=(10, 4))
        fw_row = ctk.CTkFrame(fw_card, fg_color="transparent")
        fw_row.pack(fill="x", padx=14, pady=(0, 8))
        fw_app = ctk.CTkEntry(fw_row, placeholder_text="App / Process (e.g., steam, dropbox)", width=210)
        fw_app.pack(side="left", padx=(0, 6))
        fw_act = tk.StringVar(value="throttle")
        ctk.CTkOptionMenu(fw_row, values=["throttle", "block", "allow"], variable=fw_act, width=110).pack(side="left", padx=4)
        ctk.CTkLabel(fw_row, text="Limit KB/s:", font=self.fonts["caption"], text_color=p.text_muted).pack(side="left", padx=(6, 2))
        fw_kbps = ctk.CTkEntry(fw_row, placeholder_text="1024", width=92)
        fw_kbps.insert(0, "1024")
        fw_kbps.pack(side="left", padx=4)

        def _add_fw_rule() -> None:
            aname = fw_app.get().strip() or "background_updater"
            try:
                kbps = max(64, int(fw_kbps.get().strip() or "1024"))
            except ValueError:
                kbps = 1024
            _, msg, _ = self.net_fw.add_or_update_rule(
                aname, fw_act.get(), rate_kbps=kbps, dry_run=bool(self.dry_run_var.get())
            )
            self.show_toast(msg)
            self._render_modules()

        ctk.CTkButton(
            fw_row,
            text="Save / Preview Rule",
            height=28,
            fg_color=p.accent_cyan,
            text_color="#071217",
            font=self.fonts["caption"],
            command=_add_fw_rule,
        ).pack(side="right")

        for rule in self.net_fw.load_rules()[:6]:
            rrow = ctk.CTkFrame(fw_card, fg_color=p.bg_elevated, corner_radius=8)
            rrow.pack(fill="x", padx=14, pady=2)
            ctk.CTkLabel(
                rrow,
                text=f"[{rule.action.upper()}] {rule.app_name} ({rule.rate_limit_kbps} KB/s) -> {rule.command_preview}",
                font=self.fonts["mono_small"],
                text_color=p.text_secondary,
                wraplength=self._content_wrap(240),
                justify="left",
            ).pack(side="left", padx=10, pady=5)
            ctk.CTkButton(
                rrow,
                text="Remove",
                width=64,
                height=22,
                fg_color=p.badge_crit_bg,
                text_color=p.badge_crit_fg,
                font=self.fonts["caption"],
                command=lambda rid=rule.rule_id: (self.net_fw.remove_rule(rid), self._render_modules()),
            ).pack(side="right", padx=8, pady=4)
        ctk.CTkFrame(fw_card, height=6, fg_color="transparent").pack()

        snap = self.latest_snapshot
        if not snap:
            return
        leaks = {s.pid: s for s in self.leak_watchdog.detect_leaks()}
        for proc in snap.top_processes[:22]:
            row = ctk.CTkFrame(
                self.mod_body, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=9
            )
            row.pack(fill="x", pady=3)
            ctk.CTkLabel(
                row, text=f"{proc.name} (PID {proc.pid})", font=self.fonts["body_bold"], width=230, anchor="w"
            ).pack(side="left", padx=12, pady=8)
            ctk.CTkLabel(
                row,
                text=f"CPU {proc.cpu_percent:.1f}% | RAM {proc.memory_mb:.1f} MB | {proc.category}",
                font=self.fonts["mono_small"],
                text_color=p.text_secondary,
            ).pack(side="left", padx=6)
            if proc.pid in leaks:
                make_badge(
                    row, f"LEAK +{leaks[proc.pid].growth_mb:.0f}MB", p.badge_warn_bg, p.badge_warn_fg, self.fonts["caption"]
                ).pack(side="left", padx=4)

            exp_btn = ctk.CTkButton(
                row,
                text="Explain?",
                width=70,
                height=26,
                corner_radius=6,
                fg_color=p.bg_elevated,
                hover_color=p.border_strong,
                text_color=p.accent_cyan,
                font=self.fonts["caption"],
                command=lambda pr=proc: self._explain_process(pr),
            )
            exp_btn.pack(side="right", padx=(4, 10))
            attach_tooltip(exp_btn, "Plain-English explanation of what this process does and whether stopping it is safe.", p)

            if proc.protected:
                make_badge(row, "PROTECTED", p.badge_safe_bg, p.badge_safe_fg, self.fonts["caption"]).pack(
                    side="right", padx=4
                )
            else:
                ctk.CTkButton(
                    row,
                    text="Turn Off",
                    width=76,
                    height=26,
                    corner_radius=6,
                    fg_color=p.badge_crit_bg,
                    hover_color=p.accent_coral,
                    text_color=p.badge_crit_fg,
                    font=self.fonts["caption"],
                    command=lambda pid=proc.pid, nm=proc.name: self.on_terminate_proc(pid, nm),
                ).pack(side="right", padx=4)
                ctk.CTkButton(
                    row,
                    text="Renice +5",
                    width=76,
                    height=26,
                    corner_radius=6,
                    fg_color=p.bg_elevated,
                    hover_color=p.border_strong,
                    font=self.fonts["caption"],
                    command=lambda pid=proc.pid: self.on_renice_proc(pid),
                ).pack(side="right", padx=4)

    def _render_apps_module(self) -> None:
        p = self.palette
        if not self.config_obj.consent_installed_apps:
            ctk.CTkLabel(
                self.mod_body, text="Installed app discovery is disabled in Permissions.", font=self.fonts["body"], text_color=p.accent_brass
            ).pack(pady=24)
            return
        apps = self.bloat_analyzer.scan_installed_apps(self.config_obj)
        if not apps:
            ctk.CTkLabel(
                self.mod_body, text="No user-level desktop apps discovered in standard app directory.", font=self.fonts["body"], text_color=p.text_muted
            ).pack(pady=24)
            return
        for a in apps:
            row = ctk.CTkFrame(
                self.mod_body, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=9
            )
            row.pack(fill="x", pady=3)
            if a.unused_candidate:
                make_badge(row, "REVIEW", p.badge_warn_bg, p.badge_warn_fg, self.fonts["caption"]).pack(
                    side="left", padx=(10, 4), pady=8
                )
            ctk.CTkLabel(row, text=f"{a.name} ({a.version})", font=self.fonts["body_bold"]).pack(
                side="left", padx=10, pady=8
            )
            sz_txt = f"{a.estimated_mb:.1f} MB · " if a.estimated_mb > 0 else ""
            ctk.CTkLabel(
                row, text=f"{sz_txt}{a.last_used_hint} [{a.source}]", font=self.fonts["mono_small"], text_color=p.text_muted
            ).pack(side="right", padx=14)

    def _render_bloat_module(self) -> None:
        p = self.palette
        if not self.config_obj.consent_bloat_scan:
            ctk.CTkLabel(
                self.mod_body,
                text="Duplicate & folder bloat scanning is disabled in Permissions.",
                font=self.fonts["body"],
                text_color=p.accent_brass,
            ).pack(pady=24)
            return

        ctrl = ctk.CTkFrame(
            self.mod_body, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
        )
        ctrl.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(
            ctrl,
            text="SHA-256 Exact Duplicate Finder & Folder Bloat Analyzer (Quarantine-Backed)",
            font=self.fonts["body_bold"],
        ).pack(anchor="w", padx=14, pady=(10, 4))

        row = ctk.CTkFrame(ctrl, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(0, 10))
        self._dup_dir_var = tk.StringVar(value=self._last_dup_scan_dir)
        dup_entry = ctk.CTkEntry(row, textvariable=self._dup_dir_var, font=self.fonts["mono_small"])
        dup_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        attach_tooltip(dup_entry, "Folder path to scan for exact SHA-256 duplicate files.", p)

        browse_btn = ctk.CTkButton(
            row, text="Browse", width=76, height=30, fg_color=p.bg_elevated, command=self._pick_dup_folder
        )
        browse_btn.pack(side="left", padx=4)
        scan_btn = ctk.CTkButton(
            row,
            text="Scan SHA-256 Duplicates",
            height=30,
            fg_color=p.accent_cyan,
            text_color="#071217",
            font=self.fonts["body_bold"],
            command=self._run_duplicate_scan,
        )
        scan_btn.pack(side="left", padx=4)
        attach_tooltip(
            scan_btn,
            "Finds byte-for-byte duplicate files using size + 4KB pre-hash + full SHA-256 verification.",
            p,
        )
        clean_btn = ctk.CTkButton(
            row,
            text="Quarantine Extras",
            height=30,
            fg_color=p.accent_jade,
            text_color="#071510",
            font=self.fonts["body_bold"],
            command=self._quarantine_found_duplicates,
        )
        clean_btn.pack(side="left", padx=4)
        attach_tooltip(
            clean_btn,
            "Keeps the original file and moves duplicate copies safely to Quarantine (100% undoable).",
            p,
        )

        if self._last_duplicate_groups:
            total_dup = sum(g.reclaimable_bytes for g in self._last_duplicate_groups)
            ctk.CTkLabel(
                self.mod_body,
                text=f"Found {len(self._last_duplicate_groups)} duplicate group(s) — {format_bytes(total_dup)} reclaimable",
                font=self.fonts["small"],
                text_color=p.accent_jade,
            ).pack(anchor="w", padx=4, pady=(2, 6))
            for g in self._last_duplicate_groups[:15]:
                gcard = ctk.CTkFrame(
                    self.mod_body, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=9
                )
                gcard.pack(fill="x", pady=3)
                ctk.CTkLabel(
                    gcard,
                    text=f"Keep: {g.keep_path}  ({format_bytes(g.file_size_bytes)} each · sha256:{g.sha256[:12]})",
                    font=self.fonts["body_bold"],
                    anchor="w",
                ).pack(fill="x", padx=12, pady=(8, 2))
                for dp in g.duplicate_paths[:4]:
                    ctk.CTkLabel(
                        gcard,
                        text=f"  Duplicate -> {dp}",
                        font=self.fonts["mono_small"],
                        text_color=p.text_secondary,
                        anchor="w",
                    ).pack(fill="x", padx=12, pady=(0, 4))
        else:
            ctk.CTkLabel(
                self.mod_body,
                text="Click 'Scan SHA-256 Duplicates' to inspect a folder without modifying protected personal files.",
                font=self.fonts["small"],
                text_color=p.text_muted,
            ).pack(anchor="w", padx=4, pady=(2, 10))

        # Folder Bloat Breakdown + Animated Dyson Sunburst Visualizer
        folders = self.bloat_analyzer.analyze_folder_bloat(top_n=6)
        slices = DiskSunburstBuilder.build(folders)
        bloat_hdr = ctk.CTkFrame(
            self.mod_body, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
        )
        bloat_hdr.pack(fill="x", pady=(10, 6))
        ctk.CTkLabel(
            bloat_hdr, text="Dyson Disk Sunburst, SMART Health, AI/Dev Vault & Empty Sweeper", font=self.fonts["body_bold"]
        ).pack(anchor="w", padx=14, pady=(10, 4))

        vis_row = ctk.CTkFrame(bloat_hdr, fg_color="transparent")
        vis_row.pack(fill="x", padx=14, pady=(0, 12))
        sun = DysonSunburstWidget(
            vis_row,
            p,
            slices,
            size=200,
            on_select=lambda sl: self.show_toast(f"{sl.path}: {format_bytes(sl.size_bytes)}"),
        )
        sun.pack(side="left", padx=(0, 14))
        attach_tooltip(sun, "Hover or click any Dyson ring arc to inspect large directory space usage.", p)

        list_col = ctk.CTkFrame(vis_row, fg_color="transparent")
        list_col.pack(side="left", fill="both", expand=True)
        for item in folders:
            brow = ctk.CTkFrame(list_col, fg_color=p.bg_elevated, corner_radius=8)
            brow.pack(fill="x", pady=2)
            ctk.CTkLabel(brow, text=item.path, font=self.fonts["small"]).pack(side="left", padx=10, pady=5)
            ctk.CTkLabel(
                brow,
                text=f"{format_bytes(item.size_bytes)} ({item.file_count} files)",
                font=self.fonts["mono_small"],
                text_color=p.accent_cyan,
            ).pack(side="right", padx=10, pady=5)

        # SMART / NVMe Disk Health
        smart_card = ctk.CTkFrame(
            self.mod_body, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=10
        )
        smart_card.pack(fill="x", pady=(6, 4))
        ctk.CTkLabel(smart_card, text="SMART / NVMe Wear & Thermal Health", font=self.fonts["body_bold"]).pack(
            anchor="w", padx=14, pady=(8, 4)
        )
        for sh in self.smart_monitor.inspect_disks():
            srow = ctk.CTkFrame(smart_card, fg_color=p.bg_elevated, corner_radius=8)
            srow.pack(fill="x", padx=14, pady=(0, 6))
            make_badge(
                srow,
                sh.status.upper(),
                p.badge_safe_bg if sh.status == "Healthy" else p.badge_warn_bg,
                p.badge_safe_fg if sh.status == "Healthy" else p.badge_warn_fg,
                self.fonts["caption"],
            ).pack(side="left", padx=8, pady=6)
            ctk.CTkLabel(srow, text=sh.summary, font=self.fonts["small"]).pack(side="left", padx=6)

        # Dev & AI Model Vault
        vault_items = self.dev_vault.scan(limit=8)
        if vault_items:
            vcard = ctk.CTkFrame(
                self.mod_body, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=10
            )
            vcard.pack(fill="x", pady=(6, 4))
            ctk.CTkLabel(
                vcard, text="AI Model & Developer Artifact Vault (Ollama / HuggingFace / node_modules / Cargo)", font=self.fonts["body_bold"]
            ).pack(anchor="w", padx=14, pady=(8, 4))
            for va in vault_items:
                vrow = ctk.CTkFrame(vcard, fg_color=p.bg_elevated, corner_radius=8)
                vrow.pack(fill="x", padx=14, pady=(0, 5))
                ctk.CTkLabel(vrow, text=f"{va.name} ({format_bytes(va.size_bytes)} · {va.age_days}d)", font=self.fonts["small"]).pack(
                    side="left", padx=10, pady=6
                )
                ctk.CTkLabel(vrow, text=va.path, font=self.fonts["mono_small"], text_color=p.text_muted).pack(
                    side="right", padx=10
                )

        # Empty Folders & Dead Symlinks Sweeper
        dead_card = ctk.CTkFrame(
            self.mod_body, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=10
        )
        dead_card.pack(fill="x", pady=(6, 4))
        dhdr = ctk.CTkFrame(dead_card, fg_color="transparent")
        dhdr.pack(fill="x", padx=14, pady=8)
        ctk.CTkLabel(dhdr, text="Empty Folders & Dangling Symlink Sweeper", font=self.fonts["body_bold"]).pack(
            side="left"
        )
        ctk.CTkButton(
            dhdr,
            text="Scan Empty & Dead Links",
            height=28,
            fg_color=p.bg_elevated,
            hover_color=p.border_strong,
            font=self.fonts["caption"],
            command=self._run_dead_sweep,
        ).pack(side="right")
        if self._last_dead_items:
            ctk.CTkLabel(
                dead_card,
                text=f"Found {len(self._last_dead_items)} empty/dangling items (latest: {self._last_dead_items[0].path})",
                font=self.fonts["small"],
                text_color=p.accent_brass,
            ).pack(anchor="w", padx=14, pady=(0, 8))

    def _pick_dup_folder(self) -> None:
        chosen = filedialog.askdirectory(initialdir=self._last_dup_scan_dir or str(Path.home()))
        if chosen:
            self._last_dup_scan_dir = chosen
            self._dup_dir_var.set(chosen)

    def _run_duplicate_scan(self) -> None:
        target = self._dup_dir_var.get().strip() or str(Path.home() / "Downloads")
        self._last_dup_scan_dir = target
        adapter = self._active_adapter()
        # Allow scanning user-picked folder in preview; quarantine cleanup still enforces protected roots
        self._last_duplicate_groups = self.bloat_analyzer.find_duplicates(
            [target], adapter, min_bytes=1024, allow_protected_scan=True
        )
        self.show_toast(f"SHA-256 scan finished: {len(self._last_duplicate_groups)} duplicate group(s) found.")
        self._render_modules()

    def _quarantine_found_duplicates(self) -> None:
        if not self._last_duplicate_groups:
            self.show_toast("Run a SHA-256 duplicate scan first.")
            return
        dup_paths = [p for g in self._last_duplicate_groups for p in g.duplicate_paths]
        res = self.executor.execute_action(
            "clean_duplicates",
            self._active_adapter(),
            self.config_obj,
            params={"paths": dup_paths},
            dry_run=bool(self.dry_run_var.get()),
            why="User quarantined verified SHA-256 duplicate copies",
        )
        self.show_toast(res.message)
        if not res.dry_run and res.ok:
            self._last_duplicate_groups = []
        self._render_modules()

    def _render_privacy_shield_module(self) -> None:
        p = self.palette
        # 0. AstraShield: Pi-hole-Style Local Ad / Tracker / Telemetry Blocker
        shield_st = self.adblock_shield.status()
        ab_card = ctk.CTkFrame(
            self.mod_body, fg_color=p.bg_card, border_width=1, border_color=p.accent_cyan, corner_radius=12
        )
        ab_card.pack(fill="x", pady=(0, 8))
        abh = ctk.CTkFrame(ab_card, fg_color="transparent")
        abh.pack(fill="x", padx=14, pady=(10, 4))
        ctk.CTkLabel(
            abh,
            text="AstraShield — Local Pi-hole-Style Ad, Tracker & Telemetry Blocker",
            font=self.fonts["section"],
            text_color=p.accent_cyan,
        ).pack(side="left")

        hosts_sw_var = tk.BooleanVar(value=shield_st.hosts_active)
        ctk.CTkSwitch(
            abh,
            text="System Hosts Sinkhole",
            variable=hosts_sw_var,
            font=self.fonts["caption"],
            progress_color=p.accent_jade,
            command=lambda: self._on_toggle_astrashield_hosts(bool(hosts_sw_var.get())),
        ).pack(side="right", padx=(8, 0))

        dns_sw_var = tk.BooleanVar(value=shield_st.dns_server_running)
        ctk.CTkSwitch(
            abh,
            text=f"Local DNS ({self.config_obj.astrashield_dns_port})",
            variable=dns_sw_var,
            font=self.fonts["caption"],
            progress_color=p.accent_cyan,
            command=lambda: self._on_toggle_astrashield_dns(bool(dns_sw_var.get())),
        ).pack(side="right", padx=8)

        ctk.CTkLabel(
            ab_card,
            text=(
                f"Domains Blocked: {shield_st.blocked_domain_count} · Queries: {shield_st.total_queries} · "
                f"Blocked: {shield_st.blocked_queries} ({shield_st.blocked_percent:.1f}%) · "
                f"DNS: {shield_st.dns_listen_addr} · Hosts Active: {'YES' if shield_st.hosts_active else 'NO'}"
            ),
            font=self.fonts["mono_small"],
            text_color=p.accent_jade if (shield_st.hosts_active or shield_st.dns_server_running) else p.text_secondary,
        ).pack(anchor="w", padx=14, pady=(0, 6))

        cat_row = ctk.CTkFrame(ab_card, fg_color="transparent")
        cat_row.pack(fill="x", padx=14, pady=(0, 6))
        ctk.CTkLabel(cat_row, text="Filter Lists:", font=self.fonts["caption"], text_color=p.text_muted).pack(side="left", padx=(0, 8))
        rules_data = self.adblock_shield.load_rules()
        active_cats = set(rules_data.get("categories", []))
        for cname, clabel in [("ads", "Ads"), ("trackers", "Trackers"), ("telemetry", "Telemetry"), ("malware", "Malware")]:
            cvar = tk.BooleanVar(value=cname in active_cats)

            def _toggle_cat(cat_id=cname, var=cvar) -> None:
                rd = self.adblock_shield.load_rules()
                cats = set(rd.get("categories", []))
                if var.get():
                    cats.add(cat_id)
                else:
                    cats.discard(cat_id)
                rd["categories"] = sorted(cats)
                self.adblock_shield.save_rules(rd)
                self.config_obj.astrashield_categories = rd["categories"]
                self.cfg_mgr.save(self.config_obj)
                self._render_modules()

            ctk.CTkCheckBox(cat_row, text=clabel, variable=cvar, font=self.fonts["caption"], command=_toggle_cat).pack(
                side="left", padx=6
            )

        dom_row = ctk.CTkFrame(ab_card, fg_color="transparent")
        dom_row.pack(fill="x", padx=14, pady=(0, 10))
        dom_ent = ctk.CTkEntry(dom_row, placeholder_text="Domain (e.g. doubleclick.net or trusted-site.com)", width=300)
        dom_ent.pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            dom_row,
            text="Test Lookup",
            height=26,
            fg_color=p.bg_elevated,
            font=self.fonts["caption"],
            command=lambda: (
                self.show_toast(self.adblock_shield.test_query_domain(dom_ent.get() or "ads.doubleclick.net")[1]),
                self._render_modules(),
            ),
        ).pack(side="left", padx=3)
        ctk.CTkButton(
            dom_row,
            text="+ Block Domain",
            height=26,
            fg_color=p.badge_crit_bg,
            text_color=p.badge_crit_fg,
            font=self.fonts["caption"],
            command=lambda: (
                self.show_toast(self.adblock_shield.add_custom_domain(dom_ent.get(), allow=False)[1]),
                self._render_modules(),
            ),
        ).pack(side="left", padx=3)
        ctk.CTkButton(
            dom_row,
            text="+ Allowlist",
            height=26,
            fg_color=p.accent_jade,
            text_color="#071510",
            font=self.fonts["caption"],
            command=lambda: (
                self.show_toast(self.adblock_shield.add_custom_domain(dom_ent.get(), allow=True)[1]),
                self._render_modules(),
            ),
        ).pack(side="left", padx=3)

        # 1. Privacy Trace Sweeper (Consent-Gated)
        pcard = ctk.CTkFrame(
            self.mod_body, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
        )
        pcard.pack(fill="x", pady=(0, 8))
        phdr = ctk.CTkFrame(pcard, fg_color="transparent")
        phdr.pack(fill="x", padx=14, pady=10)
        ctk.CTkLabel(
            phdr,
            text="Privacy Cache Sweeper (Strictly Preserves Passwords, Bookmarks & Login Cookies)",
            font=self.fonts["body_bold"],
        ).pack(side="left")
        if self.config_obj.consent_privacy_scan:
            ctk.CTkButton(
                phdr,
                text="Quarantine Browser Caches",
                height=28,
                fg_color=p.accent_jade,
                text_color="#071510",
                font=self.fonts["caption"],
                command=self._quarantine_privacy_traces,
            ).pack(side="right")
            traces = self.privacy_sweeper.scan(self.config_obj)
            if traces:
                for tr in traces:
                    trow = ctk.CTkFrame(pcard, fg_color=p.bg_elevated, corner_radius=8)
                    trow.pack(fill="x", padx=14, pady=(0, 5))
                    ctk.CTkLabel(trow, text=f"{tr.name} — {tr.protected_data_excluded}", font=self.fonts["small"]).pack(
                        side="left", padx=10, pady=6
                    )
                    ctk.CTkLabel(trow, text=format_bytes(tr.size_bytes), font=self.fonts["mono_small"], text_color=p.accent_cyan).pack(
                        side="right", padx=10
                    )
            else:
                ctk.CTkLabel(
                    pcard, text="Zero browser/thumbnail privacy cache bloat detected.", font=self.fonts["small"], text_color=p.text_muted
                ).pack(anchor="w", padx=14, pady=(0, 10))
        else:
            ctk.CTkLabel(
                pcard,
                text="Privacy trace scanning is opt-in. Enable 'Consent: Privacy Cache Scan' in Settings to activate.",
                font=self.fonts["small"],
                text_color=p.accent_brass,
            ).pack(anchor="w", padx=14, pady=(0, 10))

        # 2. Critical Config Integrity Shield & Hosts Audit
        scard = ctk.CTkFrame(
            self.mod_body, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
        )
        scard.pack(fill="x", pady=(0, 8))
        shdr = ctk.CTkFrame(scard, fg_color="transparent")
        shdr.pack(fill="x", padx=14, pady=10)
        ctk.CTkLabel(shdr, text="Integrity Shield (hosts & SSH Watchdog) + Hosts Safety Audit", font=self.fonts["body_bold"]).pack(
            side="left"
        )
        ctk.CTkButton(
            shdr,
            text="Re-Baseline Shield",
            height=28,
            fg_color=p.bg_elevated,
            font=self.fonts["caption"],
            command=self._rebaseline_shield,
        ).pack(side="right")
        for f in self.integrity_shield.check_or_init():
            srow = ctk.CTkFrame(scard, fg_color=p.bg_elevated, corner_radius=8)
            srow.pack(fill="x", padx=14, pady=(0, 4))
            make_badge(
                srow,
                f.status.upper(),
                p.badge_warn_bg if f.status == "modified" else p.badge_safe_bg,
                p.badge_warn_fg if f.status == "modified" else p.badge_safe_fg,
                self.fonts["caption"],
            ).pack(side="left", padx=8, pady=5)
            ctk.CTkLabel(srow, text=f"{f.path} (sha256:{f.sha256[:12]})", font=self.fonts["mono_small"]).pack(side="left", padx=6)

        hosts_res = self.net_advisor.audit_hosts_file()
        ctk.CTkLabel(
            scard,
            text=f"Hosts File ({hosts_res.hosts_path}): {hosts_res.entry_count} entries · Status: {hosts_res.status.upper()}",
            font=self.fonts["small"],
            text_color=p.accent_jade if hosts_res.status == "clean" else p.accent_coral,
        ).pack(anchor="w", padx=14, pady=(4, 10))

        # 3. Network Activity, Per-App Hogs, Fast-DNS & Smart RAM Purge
        ncard = ctk.CTkFrame(
            self.mod_body, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
        )
        ncard.pack(fill="x", pady=(0, 8))
        nhdr = ctk.CTkFrame(ncard, fg_color="transparent")
        nhdr.pack(fill="x", padx=14, pady=(10, 4))
        ctk.CTkLabel(nhdr, text="Per-App Network/Ping Hogs, Reversible Fast-DNS & Standby RAM Trim", font=self.fonts["body_bold"]).pack(
            side="left"
        )
        ctk.CTkButton(
            nhdr,
            text="Free Standby RAM",
            height=26,
            fg_color=p.accent_jade,
            text_color="#071510",
            font=self.fonts["caption"],
            command=lambda: self.show_toast(self.ram_standby_mgr.trim_standby_ram(dry_run=bool(self.dry_run_var.get()))[1]),
        ).pack(side="right", padx=(6, 0))
        dns_preset_var = tk.StringVar(value="Cloudflare (1.1.1.1)")
        ctk.CTkButton(
            nhdr,
            text="Copy Fast-DNS Cmd",
            height=26,
            fg_color=p.bg_elevated,
            font=self.fonts["caption"],
            command=lambda: self._copy_text(self.net_dns_inspector.apply_dns_preset_command(dns_preset_var.get())),
        ).pack(side="right", padx=4)
        ctk.CTkOptionMenu(
            nhdr,
            values=list(self.net_dns_inspector.DNS_PRESETS.keys()),
            variable=dns_preset_var,
            width=190,
        ).pack(side="right", padx=4)

        for hog in self.net_dns_inspector.scan_network_hogs(limit=6):
            ctk.CTkLabel(
                ncard,
                text=f"  • {hog.name} (PID {hog.pid}): {hog.connection_count} socket(s), {hog.established_count} active -> {hog.remote_sample}",
                font=self.fonts["mono_small"],
                text_color=p.text_secondary,
            ).pack(anchor="w", padx=14, pady=1)
        for dc in self.device_advisor.inspect():
            ctk.CTkLabel(
                ncard,
                text=f"  • [{dc.status.upper()}] {dc.title}: {dc.details}",
                font=self.fonts["small"],
                text_color=p.accent_cyan if dc.status == "ok" else p.accent_brass,
            ).pack(anchor="w", padx=14, pady=3)
        ctk.CTkFrame(ncard, height=8, fg_color="transparent").pack()

    def _render_hardware_presets_module(self, parent=None) -> None:
        p = self.palette
        host = parent or self.mod_body
        passport = self.hw_mgr.detect_passport()
        lib = self.hw_mgr.load_component_library()

        # 1. Hardware Passport & Component Library Selector
        hcard = ctk.CTkFrame(
            host, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
        )
        hcard.pack(fill="x", pady=(0, 8))
        hhdr = ctk.CTkFrame(hcard, fg_color="transparent")
        hhdr.pack(fill="x", padx=14, pady=(10, 4))
        ctk.CTkLabel(
            hhdr, text="Know Your PC — Hardware Passport & Component Library", font=self.fonts["body_bold"]
        ).pack(side="left")
        ctk.CTkButton(
            hhdr,
            text="Copy Spec Passport",
            height=28,
            fg_color=p.bg_elevated,
            font=self.fonts["caption"],
            command=lambda txt=passport.to_clipboard_summary(): self._copy_text(txt),
        ).pack(side="right")

        ctk.CTkLabel(
            hcard,
            text=(
                f"CPU: {passport.cpu_model} ({passport.cpu_physical_cores}C/{passport.cpu_logical_cores}T · "
                f"Profile: {passport.detected_cpu_profile_id})\n"
                f"GPU: {passport.gpu_summary} · RAM: {passport.ram_total_gb:.1f} GB · Storage: {passport.storage_summary}\n"
                f"OS: {passport.os_platform}"
            ),
            font=self.fonts["mono_small"],
            text_color=p.accent_cyan,
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=14, pady=(2, 8))

        tier_row = ctk.CTkFrame(hcard, fg_color="transparent")
        tier_row.pack(fill="x", padx=14, pady=(0, 10))

        def _build_map(entries: list[dict], fallback_id: str) -> tuple[dict[str, str], dict[str, str]]:
            id_to_lbl = {str(x["id"]): str(x.get("name") or x["id"]) for x in entries if isinstance(x, dict) and x.get("id")}
            if not id_to_lbl:
                id_to_lbl = {fallback_id: fallback_id}
            lbl_to_id = {v: k for k, v in id_to_lbl.items()}
            return id_to_lbl, lbl_to_id

        cpu_i2l, cpu_l2i = _build_map(lib.get("cpus", []), passport.detected_cpu_profile_id)
        gpu_i2l, gpu_l2i = _build_map(lib.get("gpus", []), passport.detected_gpu_profile_id)
        stor_i2l, stor_l2i = _build_map(lib.get("storage", []), passport.detected_storage_id)
        ram_i2l, ram_l2i = _build_map(lib.get("ram_tiers", []), passport.detected_ram_tier_id)

        custom_t = self.config_obj.hardware_custom_tiers
        cur_cpu_id = custom_t.get("cpu_tier") if custom_t.get("cpu_tier") in cpu_i2l else passport.detected_cpu_profile_id
        cur_gpu_id = custom_t.get("gpu_tier") if custom_t.get("gpu_tier") in gpu_i2l else passport.detected_gpu_profile_id
        cur_stor_id = custom_t.get("storage_tier") if custom_t.get("storage_tier") in stor_i2l else passport.detected_storage_id
        cur_ram_id = custom_t.get("ram_tier") if custom_t.get("ram_tier") in ram_i2l else passport.detected_ram_tier_id

        cpu_v = tk.StringVar(value=cpu_i2l.get(cur_cpu_id, next(iter(cpu_i2l.values()))))
        gpu_v = tk.StringVar(value=gpu_i2l.get(cur_gpu_id, next(iter(gpu_i2l.values()))))
        stor_v = tk.StringVar(value=stor_i2l.get(cur_stor_id, next(iter(stor_i2l.values()))))
        ram_v = tk.StringVar(value=ram_i2l.get(cur_ram_id, next(iter(ram_i2l.values()))))

        for lbl_t, var, vals, w in (
            ("CPU:", cpu_v, list(cpu_i2l.values()), 205),
            ("GPU:", gpu_v, list(gpu_i2l.values()), 195),
            ("Disk:", stor_v, list(stor_i2l.values()), 155),
            ("RAM:", ram_v, list(ram_i2l.values()), 145),
        ):
            ctk.CTkLabel(tier_row, text=lbl_t, font=self.fonts["caption"]).pack(side="left", padx=(0, 3))
            ctk.CTkOptionMenu(tier_row, values=vals, variable=var, width=w).pack(side="left", padx=(0, 6))

        def _save_hw_tiers() -> None:
            self.config_obj.hardware_custom_tiers = {
                "cpu_tier": cpu_l2i.get(cpu_v.get(), cur_cpu_id),
                "gpu_tier": gpu_l2i.get(gpu_v.get(), cur_gpu_id),
                "storage_tier": stor_l2i.get(stor_v.get(), cur_stor_id),
                "ram_tier": ram_l2i.get(ram_v.get(), cur_ram_id),
            }
            self.cfg_mgr.save(self.config_obj)
            self.show_toast("Saved custom Hardware Component Library tiers.")
            self._refresh_hw_or_mod()

        ctk.CTkButton(
            tier_row,
            text="Save PC Profile",
            height=28,
            fg_color=p.accent_jade,
            text_color="#071510",
            font=self.fonts["caption"],
            command=_save_hw_tiers,
        ).pack(side="right")

        # 2. Savable Priority & Affinity Presets
        pcard = ctk.CTkFrame(
            host, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
        )
        pcard.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(
            pcard,
            text="Hardware Priority & Affinity Presets (P-Core/CCD0 Pinning + Background I/O Throttling)",
            font=self.fonts["body_bold"],
        ).pack(anchor="w", padx=14, pady=(10, 6))

        for pr in self.hw_mgr.load_presets():
            row = ctk.CTkFrame(pcard, fg_color=p.bg_elevated, corner_radius=9)
            row.pack(fill="x", padx=14, pady=3)
            active_tag = " [ACTIVE]" if pr.preset_id == self.config_obj.active_preset_id else ""
            ctk.CTkLabel(
                row,
                text=(
                    f"{pr.name}{active_tag}\n{pr.description} "
                    f"(E-Core Pin: {pr.pin_background_to_efficiency_cores} · BG I/O: {pr.background_io_class})"
                ),
                font=self.fonts["small"],
                justify="left",
                wraplength=610,
                anchor="w",
            ).pack(side="left", padx=10, pady=6)
            if not pr.built_in:
                ctk.CTkButton(
                    row,
                    text="Delete",
                    width=64,
                    height=26,
                    fg_color=p.badge_crit_bg,
                    text_color=p.badge_crit_fg,
                    font=self.fonts["caption"],
                    command=lambda pid=pr.preset_id: (self.hw_mgr.delete_preset(pid), self._refresh_hw_or_mod()),
                ).pack(side="right", padx=(2, 8))
            ctk.CTkButton(
                row,
                text="Apply Preset",
                width=102,
                height=26,
                fg_color=p.accent_cyan,
                text_color="#071217",
                font=self.fonts["caption"],
                command=lambda preset=pr: self._on_apply_preset(preset),
            ).pack(side="right", padx=6, pady=6)

        # Quick Custom Preset Saver
        creator = ctk.CTkFrame(pcard, fg_color="transparent")
        creator.pack(fill="x", padx=14, pady=(8, 12))
        name_ent = ctk.CTkEntry(creator, placeholder_text="New Custom Preset Name (e.g., My 9950X3D Flight Sim)", width=270)
        name_ent.pack(side="left", padx=(0, 6))
        io_mode = tk.StringVar(value="idle")
        ctk.CTkOptionMenu(creator, values=["idle", "best_effort", "normal"], variable=io_mode, width=120).pack(side="left", padx=4)
        pin_var = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(creator, text="Pin BG to E-Cores/CCD1", variable=pin_var, font=self.fonts["caption"]).pack(side="left", padx=6)

        def _save_new_preset() -> None:
            nm = name_ent.get().strip() or "Custom Hardware Preset"
            c_id = cpu_l2i.get(cpu_v.get(), cur_cpu_id)
            pr = PriorityPreset(
                preset_id=f"custom_{nm.lower().replace(' ', '_')[:20]}",
                name=nm,
                description=f"User-saved affinity & I/O ({io_mode.get()}) preset for {cpu_v.get()}.",
                cpu_profile_id=c_id,
                gpu_profile_id=gpu_l2i.get(gpu_v.get(), cur_gpu_id),
                storage_id=stor_l2i.get(stor_v.get(), cur_stor_id),
                ram_tier_id=ram_l2i.get(ram_v.get(), cur_ram_id),
                background_io_class=io_mode.get(),
                pin_background_to_efficiency_cores=bool(pin_var.get()),
                built_in=False,
            )
            self.hw_mgr.save_custom_preset(pr)
            self.show_toast(f"Saved custom preset '{pr.name}'.")
            self._refresh_hw_or_mod()

        ctk.CTkButton(
            creator,
            text="+ Save Preset",
            height=28,
            fg_color=p.accent_jade,
            text_color="#071510",
            font=self.fonts["caption"],
            command=_save_new_preset,
        ).pack(side="right")

    def _refresh_hw_or_mod(self) -> None:
        if self.current_nav == "hardware":
            self._render_hardware_page()
        else:
            self._render_modules()

    def _on_apply_preset(self, preset: PriorityPreset) -> None:
        ok, msg = self.hw_mgr.apply_preset(preset, self.config_obj, dry_run=bool(self.dry_run_var.get()))
        if not self.dry_run_var.get():
            self.config_obj.active_preset_id = preset.preset_id
            self.cfg_mgr.save(self.config_obj)
        self.show_toast(msg)
        self._refresh_hw_or_mod()

    def _render_pipelines_module(self, parent=None) -> None:
        p = self.palette
        host = parent or self.mod_body
        card = ctk.CTkFrame(
            host, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
        )
        card.pack(fill="x", pady=(0, 8))
        hdr = ctk.CTkFrame(card, fg_color="transparent")
        hdr.pack(fill="x", padx=14, pady=(10, 6))
        ctk.CTkLabel(
            hdr, text="Customizable Optimization Pipelines & Local File/CLI Connectors", font=self.fonts["body_bold"]
        ).pack(side="left")
        ctk.CTkButton(
            hdr,
            text="Import Pack",
            height=28,
            fg_color=p.bg_elevated,
            font=self.fonts["caption"],
            command=self._on_import_pack,
        ).pack(side="right", padx=(4, 0))
        ctk.CTkButton(
            hdr,
            text="Export Pack (.astracradle-pack.json)",
            height=28,
            fg_color=p.bg_elevated,
            font=self.fonts["caption"],
            command=self._on_export_pack,
        ).pack(side="right", padx=4)

        for pipe in self.pipe_mgr.load_pipelines():
            prow = ctk.CTkFrame(card, fg_color=p.bg_elevated, corner_radius=9)
            prow.pack(fill="x", padx=14, pady=4)
            stage_names = " -> ".join(
                f"{s.label}" + (f" [{s.condition}]" if getattr(s, "condition", "always") != "always" else "")
                for s in pipe.stages if s.enabled
            )
            ctk.CTkLabel(
                prow,
                text=f"{pipe.name} [{pipe.trigger_mode}]\n{pipe.description}\nStages: {stage_names}",
                font=self.fonts["small"],
                justify="left",
                wraplength=600,
                anchor="w",
            ).pack(side="left", padx=10, pady=8)
            ctk.CTkButton(
                prow,
                text="Run Pipeline",
                width=102,
                height=28,
                fg_color=p.accent_jade,
                text_color="#071510",
                font=self.fonts["caption"],
                command=lambda pid=pipe.pipeline_id: self._on_run_pipeline(pid),
            ).pack(side="right", padx=(4, 10), pady=8)
            if not pipe.built_in:
                ctk.CTkButton(
                    prow,
                    text="Delete",
                    width=64,
                    height=28,
                    fg_color=p.badge_crit_bg,
                    text_color=p.badge_crit_fg,
                    font=self.fonts["caption"],
                    command=lambda pid=pipe.pipeline_id: (self.pipe_mgr.delete_pipeline(pid), self._refresh_hw_or_mod()),
                ).pack(side="right", padx=2, pady=8)
            ctk.CTkButton(
                prow,
                text="Edit Stages",
                width=92,
                height=28,
                fg_color=p.bg_card,
                font=self.fonts["caption"],
                command=lambda pl=pipe: self._open_pipeline_editor(pl),
            ).pack(side="right", padx=4, pady=8)

        # Quick Custom Pipeline Builder + Local Hook Info
        b_row = ctk.CTkFrame(card, fg_color="transparent")
        b_row.pack(fill="x", padx=14, pady=(8, 6))
        pname_ent = ctk.CTkEntry(b_row, placeholder_text="Custom Pipeline Name (e.g., Nightly Hygiene)", width=260)
        pname_ent.pack(side="left", padx=(0, 6))
        preset_ids = [pr.preset_id for pr in self.hw_mgr.load_presets()]
        p_choice = tk.StringVar(value=preset_ids[0] if preset_ids else "preset_intel_hybrid_gaming")
        ctk.CTkOptionMenu(b_row, values=preset_ids or ["preset_intel_hybrid_gaming"], variable=p_choice, width=210).pack(
            side="left", padx=4
        )

        def _create_pipe() -> None:
            nm = pname_ent.get().strip() or "My Custom Pipeline"
            pl = OptimizationPipeline(
                pipeline_id=f"pipe_{nm.lower().replace(' ', '_')[:20]}",
                name=nm,
                description="User-defined multi-stage preset + cache + self-heal pipeline.",
                trigger_mode="local_hook",
                stages=[
                    PipelineStage("preflight_snapshot", "Pre-Flight OS Snapshot Check"),
                    PipelineStage("apply_preset", "Apply Hardware Preset", {"preset_id": p_choice.get()}),
                    PipelineStage("clean_caches", "Quarantine Stale Temp Caches", condition="if_ram_above", condition_value=75.0),
                    PipelineStage("self_heal", "7-Invariant Self-Heal Check"),
                ],
                built_in=False,
            )
            self.pipe_mgr.save_custom_pipeline(pl)
            self.show_toast(f"Saved custom pipeline '{pl.name}'.")
            self._refresh_hw_or_mod()

        ctk.CTkButton(
            b_row,
            text="+ Save Custom Pipeline",
            height=28,
            fg_color=p.accent_cyan,
            text_color="#071217",
            font=self.fonts["caption"],
            command=_create_pipe,
        ).pack(side="right")

        ctk.CTkLabel(
            card,
            text=(
                f"Local Connector Hook: Drop JSON {{\"pipeline_id\": \"pipe_smooth_gaming\"}} into {self.pipe_mgr.hook_trigger_path} "
                "or run `astracradle --run-pipeline pipe_smooth_gaming` from any external game launcher or hotkey."
            ),
            font=self.fonts["mono_small"],
            text_color=p.text_muted,
            wraplength=self._content_wrap(160),
            justify="left",
        ).pack(anchor="w", padx=14, pady=(4, 12))

    def _open_pipeline_editor(self, pipeline: OptimizationPipeline) -> None:
        def _on_save(updated) -> None:
            self.pipe_mgr.save_custom_pipeline(updated)
            self.show_toast(f"Saved stage order & conditional rules for '{updated.name}'.")
            self._refresh_hw_or_mod()

        PipelineEditorDialog(self, self.palette, self.fonts, pipeline, _on_save)

    def _render_gpu_power_fan_panel(self, parent=None) -> None:
        p = self.palette
        host = parent or self.hw_body
        live = self.gpu_pwr.probe_live_power_and_fan()

        card = ctk.CTkFrame(
            host, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
        )
        card.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(card, text="GPU Power-Limit & Thermal Fan-Curve Profiles", font=self.fonts["body_bold"]).pack(
            anchor="w", padx=14, pady=(10, 4)
        )
        ctk.CTkLabel(
            card,
            text=(
                f"Detected GPU Controller: {live['name']} · Power Draw: {live['power_draw_w']} W / Limit: {live['power_limit_w']} W · "
                f"Fan: {live['fan_percent']}% · Temp: {live['temp_c']} C"
            ),
            font=self.fonts["mono_small"],
            text_color=p.accent_cyan,
            anchor="w",
        ).pack(fill="x", padx=14, pady=(0, 8))

        # Interactive Draft-vs-Applied Fan Curve Editor
        _, applied_pts = self.gpu_pwr.get_applied_curve()
        try:
            live_t = float(live["temp_c"]) if live.get("temp_c") not in (None, "N/A", "") else None
        except Exception:
            live_t = None
        curve_box = ctk.CTkFrame(card, fg_color=p.bg_elevated, corner_radius=10)
        curve_box.pack(fill="x", padx=14, pady=(0, 10))
        curve_hdr = ctk.CTkFrame(curve_box, fg_color="transparent")
        curve_hdr.pack(fill="x", padx=12, pady=(8, 4))
        ctk.CTkLabel(
            curve_hdr,
            text="Interactive Draft-vs-Applied Fan Curve (Drag Nodes on Heat Gradient)",
            font=self.fonts["small"],
            text_color=p.text_primary,
        ).pack(side="left")
        curve_readout = ctk.CTkLabel(
            curve_hdr,
            text="Draft: " + ", ".join(f"{t}C:{f}%" for t, f in self._draft_fan_curve),
            font=self.fonts["mono_small"],
            text_color=p.accent_cyan,
        )
        curve_readout.pack(side="right")

        def _on_curve_drag(pts: list[list[int]]) -> None:
            self._draft_fan_curve = [list(x) for x in pts]
            curve_readout.configure(text="Draft: " + ", ".join(f"{t}C:{f}%" for t, f in pts))

        self.fan_curve_canvas = FanCurveCanvas(
            curve_box,
            p,
            applied_points=applied_pts,
            draft_points=self._draft_fan_curve,
            live_temp_c=live_t,
            width=640,
            height=175,
            on_change=_on_curve_drag,
        )
        self.fan_curve_canvas.pack(fill="x", padx=12, pady=(0, 6))

        c_btns = ctk.CTkFrame(curve_box, fg_color="transparent")
        c_btns.pack(fill="x", padx=12, pady=(0, 10))

        def _apply_draft() -> None:
            _, msg, pts = self.gpu_pwr.apply_draft_curve(
                self._draft_fan_curve, dry_run=bool(self.dry_run_var.get())
            )
            self._draft_fan_curve = pts
            self.show_toast(msg)
            self._refresh_hw_or_mod()

        def _reset_draft() -> None:
            _, ap = self.gpu_pwr.get_applied_curve()
            self._draft_fan_curve = [list(x) for x in ap]
            self.fan_curve_canvas.set_curves(ap, self._draft_fan_curve)
            curve_readout.configure(text="Draft: " + ", ".join(f"{t}C:{f}%" for t, f in self._draft_fan_curve))
            self.show_toast("Reset draft curve to applied curve.")

        ctk.CTkButton(
            c_btns,
            text="Apply Draft Curve",
            height=26,
            fg_color=p.accent_jade,
            text_color="#071510",
            font=self.fonts["caption"],
            command=_apply_draft,
        ).pack(side="right", padx=(6, 0))
        ctk.CTkButton(
            c_btns,
            text="Reset Draft to Applied",
            height=26,
            fg_color=p.bg_card,
            font=self.fonts["caption"],
            command=_reset_draft,
        ).pack(side="right")

        for gp in self.gpu_pwr.load_profiles():
            row = ctk.CTkFrame(card, fg_color=p.bg_elevated, corner_radius=9)
            row.pack(fill="x", padx=14, pady=3)
            ctk.CTkLabel(
                row,
                text=f"{gp.name}\n{gp.description} (Power {gp.power_limit_percent}% · Fan {gp.fan_target_percent}% · Target <={gp.target_temp_c}C)",
                font=self.fonts["small"],
                justify="left",
                wraplength=self._content_wrap(270),
                anchor="w",
            ).pack(side="left", padx=10, pady=6)
            if not gp.built_in:
                ctk.CTkButton(
                    row,
                    text="Delete",
                    width=64,
                    height=26,
                    fg_color=p.badge_crit_bg,
                    text_color=p.badge_crit_fg,
                    font=self.fonts["caption"],
                    command=lambda gpid=gp.profile_id: (self.gpu_pwr.delete_profile(gpid), self._refresh_hw_or_mod()),
                ).pack(side="right", padx=(2, 6), pady=6)
            ctk.CTkButton(
                row,
                text="Apply / Copy Cmd",
                width=126,
                height=26,
                fg_color=p.accent_jade,
                text_color="#071510",
                font=self.fonts["caption"],
                command=lambda prof=gp: self._on_apply_gpu_profile(prof),
            ).pack(side="right", padx=(4, 10), pady=6)

        # Custom GPU Thermal Profile Creator
        crow = ctk.CTkFrame(card, fg_color="transparent")
        crow.pack(fill="x", padx=14, pady=(8, 12))
        g_name = ctk.CTkEntry(crow, placeholder_text="Custom Thermal Name (e.g., Quiet Render)", width=230)
        g_name.pack(side="left", padx=(0, 6))
        ctk.CTkLabel(crow, text="Power %:", font=self.fonts["caption"], text_color=p.text_muted).pack(side="left", padx=(4, 2))
        g_pwr = ctk.CTkEntry(crow, placeholder_text="85", width=68)
        g_pwr.insert(0, "85")
        g_pwr.pack(side="left", padx=4)
        ctk.CTkLabel(crow, text="Fan %:", font=self.fonts["caption"], text_color=p.text_muted).pack(side="left", padx=(4, 2))
        g_fan = ctk.CTkEntry(crow, placeholder_text="55", width=68)
        g_fan.insert(0, "55")
        g_fan.pack(side="left", padx=4)

        def _save_gpu() -> None:
            nm = g_name.get().strip() or "Custom Thermal Curve"
            try:
                pwr = max(50, min(115, int(g_pwr.get().strip() or "85")))
                fan = max(25, min(100, int(g_fan.get().strip() or "55")))
            except ValueError:
                pwr, fan = 85, 55
            gp = GpuThermalProfile(
                profile_id=f"gpu_{nm.lower().replace(' ', '_')[:18]}",
                name=nm,
                power_limit_percent=pwr,
                fan_target_percent=fan,
                target_temp_c=76,
                description=f"User-defined GPU power ({pwr}%) and fan curve ({fan}%).",
                built_in=False,
            )
            self.gpu_pwr.save_custom_profile(gp)
            self.show_toast(f"Saved GPU thermal profile '{gp.name}'.")
            self._refresh_hw_or_mod()

        ctk.CTkButton(
            crow,
            text="+ Save Curve",
            height=28,
            fg_color=p.accent_cyan,
            text_color="#071217",
            font=self.fonts["caption"],
            command=_save_gpu,
        ).pack(side="right")

    def _on_apply_gpu_profile(self, prof: GpuThermalProfile) -> None:
        _, msg, cmd = self.gpu_pwr.apply_profile(prof, dry_run=bool(self.dry_run_var.get()))
        self._draft_fan_curve = clamp_fan_curve_points(prof.curve_points)
        self._copy_text(cmd)
        self.show_toast(f"{msg} (Command copied: {cmd})")
        self._refresh_hw_or_mod()

    # ------------------------------------------------------------------
    # Page 7 (Ctrl+9): ProBalance, Auto-Revert App Rules & Interrupt/DPC Guard
    # ------------------------------------------------------------------
    def _build_probalance_page(self) -> None:
        root = self.pages["probalance"]
        root.grid_rowconfigure(1, weight=1)
        root.grid_columnconfigure(0, weight=1)

        bar = ctk.CTkFrame(root, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        self.pb_seg = ctk.CTkSegmentedButton(
            bar,
            values=["ProBalance Engine", "Auto-Revert App Rules", "Interrupt & DPC Guard"],
            font=self.fonts["small"],
            command=self._on_pb_tab,
        )
        self.pb_seg.set(self.current_pb_tab)
        self.pb_seg.pack(fill="x", expand=True)
        attach_tooltip(
            self.pb_seg,
            "Switch between ProBalance CPU Hog Restraint, Auto-Reverted App Profiles, and Interrupt/DPC Latency Guard.",
            self.palette,
        )
        self.pb_body = ctk.CTkScrollableFrame(root, fg_color="transparent")
        self.pb_body.grid(row=1, column=0, sticky="nsew")

    def _on_pb_tab(self, name: str) -> None:
        self.current_pb_tab = name
        if hasattr(self, "pb_seg") and self.pb_seg.get() != name:
            self.pb_seg.set(name)
        self._render_probalance_page()

    def _render_probalance_page(self) -> None:
        for c in self.pb_body.winfo_children():
            c.destroy()
        if self.current_pb_tab == "ProBalance Engine":
            self._render_probalance_tab()
        elif self.current_pb_tab == "Auto-Revert App Rules":
            self._render_app_rules_tab()
        else:
            self._render_latency_guard_tab()

    def _render_probalance_tab(self) -> None:
        p = self.palette
        card = ctk.CTkFrame(
            self.pb_body, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
        )
        card.pack(fill="x", pady=(0, 8))
        hdr = ctk.CTkFrame(card, fg_color="transparent")
        hdr.pack(fill="x", padx=14, pady=(10, 6))
        ctk.CTkLabel(
            hdr,
            text="ProBalance Foreground Boost & Dynamic Background Restraint",
            font=self.fonts["body_bold"],
        ).pack(side="left")

        pb_on_var = tk.BooleanVar(value=self.config_obj.probalance_enabled)
        ctk.CTkSwitch(
            hdr,
            text="Enable ProBalance",
            variable=pb_on_var,
            progress_color=p.accent_jade,
            font=self.fonts["caption"],
            command=lambda: (
                setattr(self.config_obj, "probalance_enabled", bool(pb_on_var.get())),
                self.cfg_mgr.save(self.config_obj),
                self.show_toast(f"ProBalance {'ENABLED' if pb_on_var.get() else 'DISABLED'}."),
                self._render_probalance_page(),
            ),
        ).pack(side="right")

        ctrl = ctk.CTkFrame(card, fg_color="transparent")
        ctrl.pack(fill="x", padx=14, pady=(0, 10))
        ctk.CTkLabel(ctrl, text="Restrain non-protected background hogs above CPU %:", font=self.fonts["small"]).pack(
            side="left"
        )
        thresh_ent = ctk.CTkEntry(ctrl, width=68)
        thresh_ent.insert(0, str(int(self.config_obj.probalance_cpu_threshold)))
        thresh_ent.pack(side="left", padx=8)
        fg_var = tk.BooleanVar(value=self.config_obj.probalance_boost_foreground)
        ctk.CTkCheckBox(ctrl, text="Boost Foreground App Priority", variable=fg_var, font=self.fonts["caption"]).pack(
            side="left", padx=8
        )

        def _save_pb_cfg() -> None:
            try:
                val = max(10.0, min(95.0, float(thresh_ent.get().strip() or "35")))
            except ValueError:
                val = 35.0
            self.config_obj.probalance_cpu_threshold = val
            self.config_obj.probalance_boost_foreground = bool(fg_var.get())
            self.cfg_mgr.save(self.config_obj)
            self.show_toast(f"Saved ProBalance threshold ({val:.0f}% CPU).")

        ctk.CTkButton(
            ctrl, text="Save Policy", width=96, height=26, fg_color=p.accent_cyan, text_color="#071217", font=self.fonts["caption"], command=_save_pb_cfg
        ).pack(side="right", padx=4)
        ctk.CTkButton(
            ctrl,
            text="Revert All Restraints",
            width=136,
            height=26,
            fg_color=p.bg_elevated,
            font=self.fonts["caption"],
            command=lambda: (
                self.show_toast(self.probalance.revert_all(self.config_obj, dry_run=bool(self.dry_run_var.get()))[1]),
                self._render_probalance_page(),
            ),
        ).pack(side="right", padx=4)

        # Active Restraint Ledger
        rcard = ctk.CTkFrame(
            self.pb_body, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
        )
        rcard.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(
            rcard,
            text=f"Currently Restrained Background Processes ({len(self.probalance.restrained)})",
            font=self.fonts["body_bold"],
        ).pack(anchor="w", padx=14, pady=(10, 4))
        if not self.probalance.restrained:
            ctk.CTkLabel(
                rcard,
                text="Zero background processes currently restrained (protected OS processes are strictly immune).",
                font=self.fonts["small"],
                text_color=p.text_muted,
            ).pack(anchor="w", padx=14, pady=(0, 10))
        else:
            for rec in self.probalance.restrained.values():
                rrow = ctk.CTkFrame(rcard, fg_color=p.bg_elevated, corner_radius=8)
                rrow.pack(fill="x", padx=14, pady=2)
                ctk.CTkLabel(
                    rrow,
                    text=f"{rec.name} (PID {rec.pid}) · CPU {rec.cpu_percent:.1f}% · nice {rec.original_nice} -> {rec.applied_nice} · {rec.reason}",
                    font=self.fonts["mono_small"],
                ).pack(side="left", padx=10, pady=6)

        # Recent Events
        ecard = ctk.CTkFrame(
            self.pb_body, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
        )
        ecard.pack(fill="x")
        ctk.CTkLabel(ecard, text="Recent ProBalance & Auto-Revert Activity", font=self.fonts["body_bold"]).pack(
            anchor="w", padx=14, pady=(10, 4)
        )
        for ev in (self.probalance.recent_events or ["No ProBalance events recorded yet."])[:10]:
            ctk.CTkLabel(ecard, text=f"  • {ev}", font=self.fonts["mono_small"], text_color=p.text_secondary, wraplength=self._content_wrap(160), justify="left").pack(
                anchor="w", padx=14, pady=1
            )
        ctk.CTkFrame(ecard, height=8, fg_color="transparent").pack()

    def _render_app_rules_tab(self) -> None:
        p = self.palette
        card = ctk.CTkFrame(
            self.pb_body, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
        )
        card.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(
            card,
            text="Auto-Reverted Per-App Profiles (Escalate on Launch -> Cleanly Revert on Exit)",
            font=self.fonts["body_bold"],
        ).pack(anchor="w", padx=14, pady=(10, 4))

        esc = self.probalance.active_escalation
        esc_txt = (
            f"ACTIVE ESCALATION: '{esc.matched_app}' (will revert to '{esc.prior_power_profile}' on exit)"
            if esc
            else "No temporary app escalation active right now."
        )
        ctk.CTkLabel(
            card,
            text=esc_txt,
            font=self.fonts["mono_small"],
            text_color=p.accent_jade if esc else p.text_muted,
        ).pack(anchor="w", padx=14, pady=(0, 8))

        for rule in self.probalance.load_rules():
            row = ctk.CTkFrame(card, fg_color=p.bg_elevated, corner_radius=9)
            row.pack(fill="x", padx=14, pady=3)
            ctk.CTkLabel(
                row,
                text=f"App '{rule.app_pattern}' -> {rule.target_power_profile} (FG Boost: {rule.boost_foreground})\n{rule.notes}",
                font=self.fonts["small"],
                justify="left",
                wraplength=self._content_wrap(260),
                anchor="w",
            ).pack(side="left", padx=10, pady=6)
            if not rule.built_in:
                ctk.CTkButton(
                    row,
                    text="Delete",
                    width=64,
                    height=26,
                    fg_color=p.badge_crit_bg,
                    text_color=p.badge_crit_fg,
                    font=self.fonts["caption"],
                    command=lambda rid=rule.rule_id: (self.probalance.delete_rule(rid), self._render_probalance_page()),
                ).pack(side="right", padx=(4, 10), pady=6)

        # Custom App Rule Creator
        from ..core.config import SITUATION_PROFILES

        add_row = ctk.CTkFrame(card, fg_color="transparent")
        add_row.pack(fill="x", padx=14, pady=(8, 12))
        pat_ent = ctk.CTkEntry(add_row, placeholder_text="Process name/keyword (e.g., obs, cyberpunk)", width=240)
        pat_ent.pack(side="left", padx=(0, 6))
        prof_var = tk.StringVar(value="Gaming / Low-Latency")
        ctk.CTkOptionMenu(add_row, values=list(SITUATION_PROFILES.keys()), variable=prof_var, width=210).pack(
            side="left", padx=4
        )
        fg_b = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(add_row, text="Boost FG", variable=fg_b, font=self.fonts["caption"]).pack(side="left", padx=6)

        def _add_app_rule() -> None:
            pat = pat_ent.get().strip() or "custom_game"
            self.probalance.save_custom_rule(pat, prof_var.get(), boost_foreground=bool(fg_b.get()))
            self.show_toast(f"Saved auto-revert rule for '{pat}'.")
            self._render_probalance_page()

        ctk.CTkButton(
            add_row,
            text="+ Save App Rule",
            height=28,
            fg_color=p.accent_cyan,
            text_color="#071217",
            font=self.fonts["caption"],
            command=_add_app_rule,
        ).pack(side="right")

    def _render_latency_guard_tab(self) -> None:
        p = self.palette
        rep = self._last_latency_report or self.latency_guard.measure_dpc_latency(samples=16)
        self._last_latency_report = rep

        lcard = ctk.CTkFrame(
            self.pb_body, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
        )
        lcard.pack(fill="x", pady=(0, 8))
        lhdr = ctk.CTkFrame(lcard, fg_color="transparent")
        lhdr.pack(fill="x", padx=14, pady=(10, 4))
        ctk.CTkLabel(lhdr, text="DPC & Scheduler Timer Jitter Probe", font=self.fonts["body_bold"]).pack(side="left")
        ctk.CTkButton(
            lhdr,
            text="Run DPC Latency Probe",
            height=28,
            fg_color=p.accent_cyan,
            text_color="#071217",
            font=self.fonts["caption"],
            command=lambda: (
                setattr(self, "_last_latency_report", self.latency_guard.measure_dpc_latency(samples=24)),
                self.show_toast(f"Latency probe: p95={self._last_latency_report.p95_us} us ({self._last_latency_report.grade})"),
                self._render_probalance_page(),
            ),
        ).pack(side="right")

        ctk.CTkLabel(
            lcard,
            text=(
                f"Grade: {rep.grade} · p50: {rep.p50_us} us · p95: {rep.p95_us} us · "
                f"p99: {rep.p99_us} us · Max: {rep.max_us} us\n{rep.notes}"
            ),
            font=self.fonts["mono_small"],
            text_color=p.accent_jade if rep.grade == "Clean" else p.accent_brass,
            justify="left",
        ).pack(anchor="w", padx=14, pady=(0, 8))

        # Interrupt Affinity Policies
        rcard = ctk.CTkFrame(
            self.pb_body, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
        )
        rcard.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(
            rcard, text="Interrupt Affinity Policies (Move Noisy USB/NIC/Audio IRQs off Core 0)", font=self.fonts["body_bold"]
        ).pack(anchor="w", padx=14, pady=(10, 6))
        for rule in self.latency_guard.load_rules():
            row = ctk.CTkFrame(rcard, fg_color=p.bg_elevated, corner_radius=8)
            row.pack(fill="x", padx=14, pady=3)
            ctk.CTkLabel(
                row,
                text=f"{rule.device_label} [{rule.irq_or_bus.upper()} -> Cores {rule.target_core_mask}]\n{rule.reason}",
                font=self.fonts["small"],
                justify="left",
                wraplength=620,
                anchor="w",
            ).pack(side="left", padx=10, pady=6)
            if not rule.built_in:
                ctk.CTkButton(
                    row,
                    text="Delete",
                    width=64,
                    height=26,
                    fg_color=p.badge_crit_bg,
                    text_color=p.badge_crit_fg,
                    font=self.fonts["caption"],
                    command=lambda rid=rule.rule_id: (self.latency_guard.delete_rule(rid), self._render_probalance_page()),
                ).pack(side="right", padx=(2, 8), pady=6)
            ctk.CTkButton(
                row,
                text="Apply / Copy Cmd",
                width=126,
                height=26,
                fg_color=p.accent_jade,
                text_color="#071510",
                font=self.fonts["caption"],
                command=lambda rl=rule: self._on_apply_irq_rule(rl),
            ).pack(side="right", padx=6, pady=6)

        add_irq = ctk.CTkFrame(rcard, fg_color="transparent")
        add_irq.pack(fill="x", padx=14, pady=(8, 12))
        d_ent = ctk.CTkEntry(add_irq, placeholder_text="Device (e.g., USB DAC, Wi-Fi 7 NIC)", width=230)
        d_ent.pack(side="left", padx=(0, 6))
        bus_v = tk.StringVar(value="usb")
        ctk.CTkOptionMenu(add_irq, values=["usb", "nic", "audio", "gpu", "nvme"], variable=bus_v, width=92).pack(
            side="left", padx=4
        )
        mask_ent = ctk.CTkEntry(add_irq, placeholder_text="Cores (2-3)", width=90)
        mask_ent.insert(0, "2-3")
        mask_ent.pack(side="left", padx=4)

        def _save_irq() -> None:
            rl = self.latency_guard.save_custom_rule(d_ent.get() or "Custom Device", bus_v.get(), mask_ent.get() or "2-3")
            self.show_toast(f"Saved IRQ policy '{rl.device_label}'.")
            self._render_probalance_page()

        ctk.CTkButton(
            add_irq, text="+ Save IRQ Policy", height=28, fg_color=p.accent_cyan, text_color="#071217", font=self.fonts["caption"], command=_save_irq
        ).pack(side="right")

        # IRQ Table
        icard = ctk.CTkFrame(
            self.pb_body, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
        )
        icard.pack(fill="x")
        ctk.CTkLabel(icard, text="Detected Hardware Interrupt Sources", font=self.fonts["body_bold"]).pack(
            anchor="w", padx=14, pady=(10, 4)
        )
        for src in rep.irq_sources[:8]:
            ctk.CTkLabel(
                icard,
                text=f"  • IRQ {src.irq} [{src.bus_hint.upper()}] {src.device} — {src.total_count:,} interrupts",
                font=self.fonts["mono_small"],
                text_color=p.text_secondary,
            ).pack(anchor="w", padx=14, pady=1)
        ctk.CTkFrame(icard, height=8, fg_color="transparent").pack()

    def _on_apply_irq_rule(self, rule) -> None:
        _, msg, cmd = self.latency_guard.apply_rule(rule, dry_run=bool(self.dry_run_var.get()))
        self._copy_text(cmd)
        self.show_toast(f"{msg} (Command copied)")

    # ------------------------------------------------------------------
    # Page 8 (Ctrl+0): Autoruns Audit, Ghost Drivers & Thermal SPC Baselines
    # ------------------------------------------------------------------
    def _build_autoruns_page(self) -> None:
        root = self.pages["autoruns"]
        root.grid_rowconfigure(1, weight=1)
        root.grid_columnconfigure(0, weight=1)

        bar = ctk.CTkFrame(root, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        self.auto_seg = ctk.CTkSegmentedButton(
            bar,
            values=["Autoruns & Services", "Ghost Driver Cleanup", "Thermal SPC Baselines"],
            font=self.fonts["small"],
            command=self._on_auto_tab,
        )
        self.auto_seg.set(self.current_auto_tab)
        self.auto_seg.pack(fill="x", expand=True)
        attach_tooltip(
            self.auto_seg,
            "Audit Startup/Scheduled Tasks/Services, clean Disconnected Ghost Drivers, and inspect Welford Thermal SPC Baselines.",
            self.palette,
        )
        self.auto_body = ctk.CTkScrollableFrame(root, fg_color="transparent")
        self.auto_body.grid(row=1, column=0, sticky="nsew")

    def _on_auto_tab(self, name: str) -> None:
        self.current_auto_tab = name
        if hasattr(self, "auto_seg") and self.auto_seg.get() != name:
            self.auto_seg.set(name)
        self._render_autoruns_page()

    def _render_autoruns_page(self) -> None:
        for c in self.auto_body.winfo_children():
            c.destroy()
        if self.current_auto_tab == "Autoruns & Services":
            self._render_autoruns_tab()
        elif self.current_auto_tab == "Ghost Driver Cleanup":
            self._render_ghosts_tab()
        else:
            self._render_thermal_spc_tab()

    def _render_autoruns_tab(self) -> None:
        p = self.palette
        hdr = ctk.CTkFrame(
            self.auto_body, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
        )
        hdr.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(
            hdr, text="Autoruns Deep Audit (Startup, Scheduled Tasks, Services & Drivers)", font=self.fonts["body_bold"]
        ).pack(side="left", padx=14, pady=10)
        ctk.CTkButton(
            hdr,
            text="Refresh Audit",
            height=28,
            fg_color=p.accent_cyan,
            text_color="#071217",
            font=self.fonts["caption"],
            command=lambda: (
                setattr(self, "_last_autoruns", self.autoruns_auditor.scan_autoruns(self._active_adapter())),
                self._render_autoruns_page(),
            ),
        ).pack(side="right", padx=14, pady=8)

        if not self._last_autoruns:
            self._last_autoruns = self.autoruns_auditor.scan_autoruns(self._active_adapter())

        for ar in self._last_autoruns[:25]:
            row = ctk.CTkFrame(
                self.auto_body, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=9
            )
            row.pack(fill="x", pady=3)
            make_badge(row, ar.category.upper(), p.bg_elevated, p.accent_cyan, self.fonts["caption"]).pack(
                side="left", padx=(10, 6), pady=8
            )
            ctk.CTkLabel(
                row,
                text=f"{ar.name} ({ar.publisher_or_scope})\n{ar.plain_english}",
                font=self.fonts["small"],
                justify="left",
                wraplength=self._content_wrap(270),
                anchor="w",
            ).pack(side="left", padx=6, pady=6)
            if ar.can_toggle:
                ctk.CTkButton(
                    row,
                    text="Disable (Undoable)",
                    width=124,
                    height=26,
                    fg_color=p.badge_warn_bg,
                    text_color=p.badge_warn_fg,
                    font=self.fonts["caption"],
                    command=lambda e=ar: self._on_toggle_autorun(e, False),
                ).pack(side="right", padx=(4, 10))
                if getattr(ar, "command_or_path", "") and Path(str(ar.command_or_path)).exists():
                    ctk.CTkButton(
                        row,
                        text="Delay 45s",
                        width=86,
                        height=26,
                        fg_color=p.bg_elevated,
                        font=self.fonts["caption"],
                        command=lambda p_str=str(ar.command_or_path): self.show_toast(
                            self.startup_file_delay.delay_startup_entry(Path(p_str), 45)[1]
                        ),
                    ).pack(side="right", padx=2)
            else:
                make_badge(row, "READ-ONLY", p.bg_elevated, p.text_muted, self.fonts["caption"]).pack(side="right", padx=10)

    def _on_toggle_autorun(self, entry, enable: bool) -> None:
        _, msg = self.autoruns_auditor.toggle_autorun_entry(
            entry, enable, self._active_adapter(), dry_run=bool(self.dry_run_var.get())
        )
        self.show_toast(msg)
        self._last_autoruns = self.autoruns_auditor.scan_autoruns(self._active_adapter())
        self._render_autoruns_page()

    def _render_ghosts_tab(self) -> None:
        p = self.palette
        hdr = ctk.CTkFrame(
            self.auto_body, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
        )
        hdr.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(
            hdr, text="Ghost & Disconnected Device Driver Cleanup", font=self.fonts["body_bold"]
        ).pack(side="left", padx=14, pady=10)
        ctk.CTkButton(
            hdr,
            text="Scan Ghost Drivers",
            height=28,
            fg_color=p.accent_cyan,
            text_color="#071217",
            font=self.fonts["caption"],
            command=lambda: (
                setattr(self, "_last_ghosts", self.autoruns_auditor.scan_ghost_drivers()),
                self.show_toast(f"Ghost driver scan complete: {len(self._last_ghosts)} item(s)."),
                self._render_autoruns_page(),
            ),
        ).pack(side="right", padx=14, pady=8)

        if not self._last_ghosts:
            self._last_ghosts = self.autoruns_auditor.scan_ghost_drivers()

        if not self._last_ghosts:
            ctk.CTkLabel(
                self.auto_body,
                text="Zero disconnected or orphaned ghost drivers detected.",
                font=self.fonts["body"],
                text_color=p.accent_jade,
            ).pack(pady=24)
            return

        for gh in self._last_ghosts[:20]:
            row = ctk.CTkFrame(
                self.auto_body, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=9
            )
            row.pack(fill="x", pady=3)
            make_badge(row, gh.subsystem.upper(), p.badge_warn_bg, p.badge_warn_fg, self.fonts["caption"]).pack(
                side="left", padx=10, pady=8
            )
            ctk.CTkLabel(
                row,
                text=f"{gh.name} [{gh.status}]\n{gh.plain_english} ({gh.reclaimable_note})",
                font=self.fonts["small"],
                justify="left",
                wraplength=640,
                anchor="w",
            ).pack(side="left", padx=6, pady=6)
            ctk.CTkButton(
                row,
                text="Clean / Advise",
                width=110,
                height=26,
                fg_color=p.accent_jade,
                text_color="#071510",
                font=self.fonts["caption"],
                command=lambda g=gh: (
                    self.show_toast(
                        self.autoruns_auditor.clean_ghost_driver(
                            g, self._active_adapter(), dry_run=bool(self.dry_run_var.get())
                        )[1]
                    ),
                    self._render_autoruns_page(),
                ),
            ).pack(side="right", padx=10)

    def _render_thermal_spc_tab(self) -> None:
        p = self.palette
        card = ctk.CTkFrame(
            self.auto_body, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
        )
        card.pack(fill="x", pady=(0, 8))
        hdr = ctk.CTkFrame(card, fg_color="transparent")
        hdr.pack(fill="x", padx=14, pady=(10, 6))
        ctk.CTkLabel(
            hdr,
            text="Workload-Aware Thermal Baselines & Welford Online SPC (3-Sigma Drift)",
            font=self.fonts["body_bold"],
        ).pack(side="left")
        ctk.CTkButton(
            hdr,
            text="Reset Baselines",
            height=26,
            fg_color=p.badge_crit_bg,
            text_color=p.badge_crit_fg,
            font=self.fonts["caption"],
            command=lambda: (self.thermal_spc.reset_baselines(), self.show_toast("Reset thermal SPC baselines."), self._render_autoruns_page()),
        ).pack(side="right", padx=(4, 0))
        ctk.CTkButton(
            hdr,
            text="Sample Current Tier",
            height=26,
            fg_color=p.accent_cyan,
            text_color="#071217",
            font=self.fonts["caption"],
            command=self._on_sample_thermal_spc,
        ).pack(side="right", padx=4)

        baselines = self.thermal_spc.load_baselines()
        for tier, wb in baselines.items():
            row = ctk.CTkFrame(card, fg_color=p.bg_elevated, corner_radius=8)
            row.pack(fill="x", padx=14, pady=3)
            make_badge(row, tier.upper(), p.bg_card, p.accent_cyan, self.fonts["caption"]).pack(side="left", padx=10, pady=7)
            ctk.CTkLabel(
                row,
                text=(
                    f"Samples: {wb.count} · Mean Temp: {wb.mean_temp_c:.1f} C (±{wb.std_temp_c:.2f} C) · "
                    f"UCL: {wb.ucl_temp_c:.1f} C · Mean CPU: {wb.mean_cpu_pct:.1f}%"
                ),
                font=self.fonts["mono_small"],
            ).pack(side="left", padx=8)

        acard = ctk.CTkFrame(
            self.auto_body, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
        )
        acard.pack(fill="x")
        ctk.CTkLabel(acard, text="Detected Thermal / Power SPC Anomalies", font=self.fonts["body_bold"]).pack(
            anchor="w", padx=14, pady=(10, 4)
        )
        if not self.thermal_spc.anomalies:
            ctk.CTkLabel(
                acard, text="All workloads operating within normal Welford SPC control limits.", font=self.fonts["small"], text_color=p.accent_jade
            ).pack(anchor="w", padx=14, pady=(0, 10))
        else:
            for an in self.thermal_spc.anomalies[:8]:
                ctk.CTkLabel(
                    acard,
                    text=f"  • [{an.severity.upper()}] {an.summary}",
                    font=self.fonts["mono_small"],
                    text_color=p.accent_coral if an.severity == "critical" else p.accent_brass,
                ).pack(anchor="w", padx=14, pady=2)
            ctk.CTkFrame(acard, height=8, fg_color="transparent").pack()

    def _on_sample_thermal_spc(self) -> None:
        snap = self.latest_snapshot
        cpu = snap.cpu_percent if snap else 15.0
        temp = snap.temperature_c if (snap and snap.temperature_c is not None) else 48.0
        tier = self.thermal_spc.classify_workload(cpu)
        anoms = self.thermal_spc.update_sample(tier, temp, cpu)
        self.show_toast(
            anoms[0].summary if anoms else f"Recorded SPC baseline sample in '{tier}' ({temp:.1f}C)."
        )
        self._render_autoruns_page()

    def _on_hist_resolution(self, res: str) -> None:
        self.config_obj.telemetry_resolution = res
        self.cfg_mgr.save(self.config_obj)
        pts = self.history_db.query_resolution(res, limit=120)
        self.history_chart.set_points(pts, resolution=res)

    def _on_run_pipeline(self, pipeline_id: str) -> None:
        procs = self.latest_snapshot.top_processes if self.latest_snapshot else []
        ok, summary, _ = self.pipe_mgr.run_pipeline(
            pipeline_id,
            self._active_adapter(),
            self.config_obj,
            procs,
            dry_run=bool(self.dry_run_var.get()),
        )
        self.show_toast(summary)
        self._refresh_hw_or_mod()

    def _on_export_pack(self) -> None:
        dest = filedialog.asksaveasfilename(
            title="Export Shareable AstraCradle Pack",
            defaultextension=".astracradle-pack.json",
            initialfile="my_pack.astracradle-pack.json",
            filetypes=[("AstraCradle Pack", "*.json")],
        )
        if dest:
            out = ScriptAndPresetPackManager.export_pack(
                Path(dest), self.config_obj, self.hw_mgr.load_presets(), self.pipe_mgr.load_pipelines()
            )
            self.show_toast(f"Exported SHA-256 verified pack -> {out}")

    def _on_import_pack(self) -> None:
        src = filedialog.askopenfilename(title="Import AstraCradle Pack", filetypes=[("JSON files", "*.json")])
        if src:
            ok, msg = ScriptAndPresetPackManager.import_pack(Path(src), self.hw_mgr, self.pipe_mgr, self.config_obj)
            if ok:
                self.cfg_mgr.save(self.config_obj)
            self.show_toast(msg)
            self._refresh_hw_or_mod()

    def _render_glitch_specs_module(self) -> None:
        p = self.palette
        # 0. Active Window & Fullscreen Game/Presentation Focus Guard
        fs = FullscreenFocusDetector.detect()
        fcard = ctk.CTkFrame(
            self.mod_body, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
        )
        fcard.pack(fill="x", pady=(0, 8))
        fhdr = ctk.CTkFrame(fcard, fg_color="transparent")
        fhdr.pack(fill="x", padx=14, pady=10)
        ctk.CTkLabel(
            fhdr,
            text=f"Active Window & Fullscreen Focus Guard ({fs.detection_method})",
            font=self.fonts["body_bold"],
        ).pack(side="left")
        fs_var = tk.BooleanVar(value=self.config_obj.pause_during_fullscreen)
        ctk.CTkSwitch(
            fhdr,
            text="Pause Background Tweaks in Fullscreen",
            variable=fs_var,
            font=self.fonts["caption"],
            progress_color=p.accent_jade,
            command=lambda: (
                setattr(self.config_obj, "pause_during_fullscreen", bool(fs_var.get())),
                self.cfg_mgr.save(self.config_obj),
                self.show_toast(f"Fullscreen Focus Guard {'ENABLED' if fs_var.get() else 'DISABLED'}."),
            ),
        ).pack(side="right")
        ctk.CTkLabel(
            fcard,
            text=f"Foreground: {fs.window_title} ({fs.process_name}) · Fullscreen Active: {'YES' if fs.is_fullscreen else 'NO'}",
            font=self.fonts["mono_small"],
            text_color=p.accent_cyan,
        ).pack(anchor="w", padx=14, pady=(0, 10))

        # 1. 1-Click Everyday PC Glitch Troubleshooters
        gcard = ctk.CTkFrame(
            self.mod_body, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
        )
        gcard.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(
            gcard, text="1-Click 'Everyday PC Glitch' Troubleshooters (Safe & Reversible)", font=self.fonts["body_bold"]
        ).pack(anchor="w", padx=14, pady=(10, 6))

        for tr in GlitchTroubleshooter.list_troubleshooters():
            row = ctk.CTkFrame(gcard, fg_color=p.bg_elevated, corner_radius=8)
            row.pack(fill="x", padx=14, pady=3)
            ctk.CTkLabel(
                row, text=f"{tr['title']} — {tr['desc']}", font=self.fonts["small"], wraplength=self._content_wrap(260), justify="left"
            ).pack(side="left", padx=10, pady=7)
            ctk.CTkButton(
                row,
                text="Run Fix",
                width=86,
                height=26,
                fg_color=p.accent_jade,
                text_color="#071510",
                font=self.fonts["caption"],
                command=lambda tid=tr["id"]: self._on_run_glitch_fix(tid),
            ).pack(side="right", padx=10, pady=6)

        # 2. Battery Wear, Wattage, Sleep Wake-Locks & 'What Changed Since My PC Was Fast?' Snapshot Diff
        bat = self.battery_inspector.inspect()
        bcard = ctk.CTkFrame(
            self.mod_body, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
        )
        bcard.pack(fill="x", pady=(0, 8))
        bhdr = ctk.CTkFrame(bcard, fg_color="transparent")
        bhdr.pack(fill="x", padx=14, pady=(10, 4))
        ctk.CTkLabel(bhdr, text="Battery Health, Wake-Locks & Snapshot Change Diff", font=self.fonts["body_bold"]).pack(
            side="left"
        )
        ctk.CTkButton(
            bhdr,
            text="Compare Latest 2 Snapshots",
            height=26,
            fg_color=p.accent_cyan,
            text_color="#071217",
            font=self.fonts["caption"],
            command=self._on_compare_snapshots,
        ).pack(side="right", padx=(4, 0))
        ctk.CTkButton(
            bhdr,
            text="Save PC Snapshot",
            height=26,
            fg_color=p.bg_elevated,
            font=self.fonts["caption"],
            command=self._on_save_diff_snapshot,
        ).pack(side="right", padx=4)

        ctk.CTkLabel(
            bcard,
            text=f"Power / Battery: {bat.summary}",
            font=self.fonts["mono_small"],
            text_color=p.accent_jade if bat.present else p.text_secondary,
        ).pack(anchor="w", padx=14, pady=(0, 4))
        if bat.wake_locks:
            ctk.CTkLabel(
                bcard,
                text="Active Sleep Wake-Locks: " + " | ".join(bat.wake_locks[:3]),
                font=self.fonts["caption"],
                text_color=p.accent_brass,
            ).pack(anchor="w", padx=14, pady=(0, 8))
        else:
            ctk.CTkFrame(bcard, height=6, fg_color="transparent").pack()

        # 3. Pre-Flight Native OS Snapshot + Opt-In Outdated Apps Check
        ocard = ctk.CTkFrame(
            self.mod_body, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
        )
        ocard.pack(fill="x", pady=(0, 8))
        ohdr = ctk.CTkFrame(ocard, fg_color="transparent")
        ohdr.pack(fill="x", padx=14, pady=10)
        ctk.CTkLabel(ohdr, text="Pre-Flight OS Restore Hook & Native Outdated Runtimes Check", font=self.fonts["body_bold"]).pack(
            side="left"
        )
        ctk.CTkButton(
            ohdr,
            text="Check Outdated Runtimes (Opt-In)",
            height=28,
            fg_color=p.accent_cyan,
            text_color="#071217",
            font=self.fonts["caption"],
            command=self._on_check_outdated_apps,
        ).pack(side="right", padx=(4, 0))
        ctk.CTkButton(
            ohdr,
            text="Check OS Snapshot Hook",
            height=28,
            fg_color=p.bg_elevated,
            font=self.fonts["caption"],
            command=lambda: self.show_toast(PreFlightSnapshotHook.check_or_prepare(dry_run=True)[1]),
        ).pack(side="right", padx=4)

        for ln in (self._last_outdated_lines or ["Click 'Check Outdated Runtimes (Opt-In)' to inspect native winget / brew / apt updates."])[:10]:
            ctk.CTkLabel(ocard, text=f"  • {ln}", font=self.fonts["mono_small"], text_color=p.text_secondary).pack(
                anchor="w", padx=14, pady=1
            )
        ctk.CTkFrame(ocard, height=8, fg_color="transparent").pack()

    def _on_run_glitch_fix(self, trouble_id: str) -> None:
        _, msg = GlitchTroubleshooter.run(
            trouble_id,
            self._active_adapter(),
            self.executor,
            self.config_obj,
            self.journal,
            dry_run=bool(self.dry_run_var.get()),
        )
        self.show_toast(msg)

    def _on_check_outdated_apps(self) -> None:
        _, lines = OutdatedAppsChecker.check(consent=True)
        self._last_outdated_lines = lines
        self.show_toast(f"Outdated runtime scan complete ({len(lines)} line(s)).")
        self._render_modules()

    def _render_toolkit_module(self) -> None:
        p = self.palette
        adapter = self._active_adapter()
        for title, cmd_list in adapter.os_maintenance_commands().items():
            card = ctk.CTkFrame(
                self.mod_body, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=10
            )
            card.pack(fill="x", pady=4)
            hdr = ctk.CTkFrame(card, fg_color="transparent")
            hdr.pack(fill="x", padx=14, pady=(10, 4))
            ctk.CTkLabel(hdr, text=title, font=self.fonts["body_bold"]).pack(side="left")
            joined = "\n".join(cmd_list)
            ctk.CTkButton(
                hdr,
                text="Copy",
                width=64,
                height=24,
                corner_radius=6,
                fg_color=p.bg_elevated,
                font=self.fonts["caption"],
                command=lambda txt=joined: self._copy_text(txt),
            ).pack(side="right")
            ctk.CTkLabel(
                card, text=joined, font=self.fonts["mono_small"], text_color=p.accent_cyan, justify="left", anchor="w"
            ).pack(fill="x", padx=14, pady=(0, 10))

    # ------------------------------------------------------------------
    # Page 6: Forensic Journal, Crash Post-Mortem, Self-Heal & Rollback Vault
    # ------------------------------------------------------------------
    def _build_safeguards_page(self) -> None:
        self.safeguards_scroll = ctk.CTkScrollableFrame(self.pages["safeguards"], fg_color="transparent")
        self.safeguards_scroll.pack(fill="both", expand=True)

    def _render_safeguards(self) -> None:
        p = self.palette
        for c in self.safeguards_scroll.winfo_children():
            c.destroy()

        # 1. Self-Heal & Crash Post-Mortem Bar
        sh_hdr = ctk.CTkFrame(
            self.safeguards_scroll, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
        )
        sh_hdr.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(
            sh_hdr, text="7-Invariant Self-Heal & Crash Post-Mortem Analyzer", font=self.fonts["section"]
        ).pack(side="left", padx=14, pady=12)
        ctk.CTkButton(
            sh_hdr,
            text="Simulate Crash & Diagnose",
            height=30,
            corner_radius=7,
            fg_color=p.badge_crit_bg,
            hover_color=p.accent_coral,
            text_color=p.badge_crit_fg,
            font=self.fonts["small"],
            command=self.on_simulate_crash,
        ).pack(side="right", padx=(4, 14), pady=10)
        ctk.CTkButton(
            sh_hdr,
            text="Simulate Config Fault",
            height=30,
            corner_radius=7,
            fg_color=p.accent_brass,
            hover_color="#B8934F",
            text_color="#120E06",
            font=self.fonts["small"],
            command=self.on_simulate_self_heal,
        ).pack(side="right", padx=4, pady=10)
        ctk.CTkButton(
            sh_hdr,
            text="Verify & Self-Audit",
            height=30,
            corner_radius=7,
            fg_color=p.accent_jade,
            hover_color=p.accent_jade_hover,
            text_color="#071510",
            font=self.fonts["small"],
            command=self.on_run_self_heal,
        ).pack(side="right", padx=4, pady=10)

        for ev in (self.latest_self_heal or self.self_heal.load_ledger()[:7])[:7]:
            row = ctk.CTkFrame(
                self.safeguards_scroll, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=9
            )
            row.pack(fill="x", pady=2)
            bg, fg = (p.badge_warn_bg, p.badge_warn_fg) if ev.repaired_automatically else (p.badge_safe_bg, p.badge_safe_fg)
            make_badge(row, ev.status.upper(), bg, fg, self.fonts["caption"]).pack(side="left", padx=10, pady=7)
            ctk.CTkLabel(row, text=ev.check_name, font=self.fonts["body_bold"]).pack(side="left", padx=6)
            ctk.CTkLabel(
                row, text=ev.details, font=self.fonts["small"], text_color=p.text_secondary, wraplength=500, justify="left"
            ).pack(side="right", padx=12, pady=7)

        # 2. Forensic Audit Findings & Log Stream (WHAT / HOW / WHY)
        fj_hdr = ctk.CTkFrame(
            self.safeguards_scroll, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
        )
        fj_hdr.pack(fill="x", pady=(12, 6))
        ctk.CTkLabel(
            fj_hdr, text="Forensic Journal (Everything Logged: What, How & Why + Self-Audit)", font=self.fonts["section"]
        ).pack(side="left", padx=14, pady=10)

        for af in self.latest_audit[:3]:
            arow = ctk.CTkFrame(
                self.safeguards_scroll, fg_color=p.bg_elevated, border_width=1, border_color=p.border_subtle, corner_radius=9
            )
            arow.pack(fill="x", pady=3)
            make_badge(
                arow,
                f"AUDIT: {af.severity.upper()}",
                p.badge_safe_bg if af.severity == "ok" else p.badge_warn_bg,
                p.badge_safe_fg if af.severity == "ok" else p.badge_warn_fg,
                self.fonts["caption"],
            ).pack(side="left", padx=10, pady=8)
            ctk.CTkLabel(arow, text=f"{af.title} — {af.explanation}", font=self.fonts["small"], wraplength=self._content_wrap(240), justify="left").pack(
                side="left", padx=6, pady=6
            )

        for fe in self.journal.read_entries(limit=10, newest_first=True):
            frow = ctk.CTkFrame(
                self.safeguards_scroll, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=8
            )
            frow.pack(fill="x", pady=2)
            ctk.CTkLabel(
                frow,
                text=f"[{fe.timestamp}] {fe.component}.{fe.action} ({fe.outcome.upper()})\nHOW: {fe.how}  |  WHY: {fe.why}",
                font=self.fonts["mono_small"],
                text_color=p.text_secondary,
                justify="left",
                wraplength=self._content_wrap(150),
                anchor="w",
            ).pack(fill="x", padx=12, pady=6)

        # 3. Rollback & Quarantine Vault
        q_bytes, q_files = self.rollback_mgr.quarantine_stats()
        rb_hdr = ctk.CTkFrame(
            self.safeguards_scroll, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
        )
        rb_hdr.pack(fill="x", pady=(12, 6))
        ctk.CTkLabel(
            rb_hdr, text=f"Quarantine & Rollback Vault ({q_files} files, {format_bytes(q_bytes)})", font=self.fonts["section"]
        ).pack(side="left", padx=14, pady=10)
        ctk.CTkButton(
            rb_hdr,
            text="Prune Expired",
            height=28,
            corner_radius=6,
            fg_color=p.bg_elevated,
            font=self.fonts["small"],
            command=lambda: self._run_single_action("prune_quarantine"),
        ).pack(side="right", padx=14, pady=8)

        for entry in self.rollback_mgr.load_entries()[:12]:
            erow = ctk.CTkFrame(
                self.safeguards_scroll, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=9
            )
            erow.pack(fill="x", pady=2)
            make_badge(
                erow,
                "DRY RUN" if entry.dry_run else entry.status.upper(),
                p.badge_info_bg if entry.dry_run else p.badge_safe_bg,
                p.badge_info_fg if entry.dry_run else p.badge_safe_fg,
                self.fonts["caption"],
            ).pack(side="left", padx=10, pady=7)
            ctk.CTkLabel(erow, text=f"{entry.title} — {entry.summary}", font=self.fonts["small"]).pack(side="left", padx=6)
            if not entry.dry_run and entry.status == "applied" and entry.quarantine_paths:
                ctk.CTkButton(
                    erow,
                    text="Shred",
                    width=64,
                    height=26,
                    corner_radius=6,
                    fg_color=p.badge_crit_bg,
                    hover_color=p.accent_coral,
                    text_color=p.badge_crit_fg,
                    font=self.fonts["caption"],
                    command=lambda eid=entry.entry_id: self.on_shred_entry(eid),
                ).pack(side="right", padx=(2, 10), pady=6)
                ctk.CTkButton(
                    erow,
                    text="Restore / Undo",
                    width=106,
                    height=26,
                    corner_radius=6,
                    fg_color=p.accent_cyan,
                    text_color="#071217",
                    font=self.fonts["caption"],
                    command=lambda eid=entry.entry_id: self.on_rollback_entry(eid),
                ).pack(side="right", padx=(6, 2), pady=6)

    # ------------------------------------------------------------------
    # Page 7: Universal Settings, Plugins & Seamless Updater
    # ------------------------------------------------------------------
    def _build_settings_page(self) -> None:
        p = self.palette
        scroll = ctk.CTkScrollableFrame(self.pages["settings"], fg_color="transparent")
        scroll.pack(fill="both", expand=True)

        # Hero Brand Showcase with Animated Dyson Sphere GIF
        about = ctk.CTkFrame(
            scroll, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
        )
        about.pack(fill="x", pady=(0, 10))
        full_gif = self.assets_dir / "astracradle_animated.gif"
        if full_gif.exists():
            self.about_gif = AnimatedGifLabel(about, full_gif, bg=p.bg_card, size=92)
            self.about_gif.pack(side="left", padx=16, pady=12)
        ab_txt = ctk.CTkFrame(about, fg_color="transparent")
        ab_txt.pack(side="left", fill="x", expand=True, pady=12)
        ctk.CTkLabel(ab_txt, text=f"{__app_name__} v{__version__}", font=self.fonts["section"]).pack(anchor="w")
        ctk.CTkLabel(
            ab_txt,
            text=f"{__tagline__} · Local Workspace: {self.cfg_mgr.base_dir}",
            font=self.fonts["small"],
            text_color=p.text_secondary,
        ).pack(anchor="w")

        # Seamless Auto-Updater & Modular Plugin Registry Card
        upd_card = ctk.CTkFrame(
            scroll, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
        )
        upd_card.pack(fill="x", pady=(0, 10))
        uhdr = ctk.CTkFrame(upd_card, fg_color="transparent")
        uhdr.pack(fill="x", padx=16, pady=(12, 6))
        ctk.CTkLabel(uhdr, text="Seamless Auto-Updater & Modular Plugin Registry", font=self.fonts["section"]).pack(
            side="left"
        )
        ctk.CTkButton(
            uhdr,
            text="Install Update Bundle (.zip)...",
            height=28,
            corner_radius=7,
            fg_color=p.bg_elevated,
            hover_color=p.border_strong,
            font=self.fonts["caption"],
            command=self.on_install_update_bundle,
        ).pack(side="right", padx=(6, 0))
        ctk.CTkButton(
            uhdr,
            text="Check Updates Now",
            height=28,
            corner_radius=7,
            fg_color=p.accent_cyan,
            text_color="#071217",
            font=self.fonts["caption"],
            command=self.on_check_updates,
        ).pack(side="right")

        self.upd_status_lbl = ctk.CTkLabel(
            upd_card,
            text=self.updater.check_for_updates(self.config_obj, check_remote=False).status_message,
            font=self.fonts["small"],
            text_color=p.accent_jade,
            anchor="w",
        )
        self.upd_status_lbl.pack(fill="x", padx=16, pady=(0, 6))

        repo_row = ctk.CTkFrame(upd_card, fg_color="transparent")
        repo_row.pack(fill="x", padx=16, pady=(0, 8))
        ctk.CTkLabel(
            repo_row,
            text="Default GitHub Repo (Checked First, Local Fallback):",
            font=self.fonts["caption"],
            text_color=p.text_secondary,
        ).pack(side="left", padx=(0, 8))
        self.set_update_repo_var = ctk.StringVar(value=self.config_obj.update_repo_url)
        ctk.CTkEntry(
            repo_row,
            textvariable=self.set_update_repo_var,
            height=28,
            font=self.fonts["caption"],
        ).pack(side="left", fill="x", expand=True)

        for mod in self.updater.list_modules():
            mrow = ctk.CTkFrame(upd_card, fg_color=p.bg_elevated, corner_radius=8)
            mrow.pack(fill="x", padx=16, pady=2)
            make_badge(mrow, mod.kind.upper(), p.bg_card, p.accent_cyan, self.fonts["caption"]).pack(
                side="left", padx=8, pady=5
            )
            ctk.CTkLabel(mrow, text=f"{mod.name} (v{mod.version})", font=self.fonts["small"]).pack(side="left", padx=6)
            ctk.CTkLabel(mrow, text=mod.description, font=self.fonts["caption"], text_color=p.text_muted).pack(
                side="right", padx=10
            )
        ctk.CTkFrame(upd_card, height=10, fg_color="transparent").pack()

        # Packaging & Standalone Installer Pipeline Card
        bld_st = StandaloneBuildRunner.status()
        bld_card = ctk.CTkFrame(
            scroll, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
        )
        bld_card.pack(fill="x", pady=(0, 10))
        bhdr = ctk.CTkFrame(bld_card, fg_color="transparent")
        bhdr.pack(fill="x", padx=16, pady=(12, 6))
        ctk.CTkLabel(bhdr, text="Standalone Binary & OS Installer Pipeline (.exe / .dmg / .AppImage)", font=self.fonts["section"]).pack(
            side="left"
        )
        ctk.CTkButton(
            bhdr,
            text="Regenerate Installer Recipes",
            height=28,
            fg_color=p.accent_jade,
            text_color="#071510",
            font=self.fonts["caption"],
            command=lambda: self.show_toast(StandaloneBuildRunner.generate_installer_scripts()[1]),
        ).pack(side="right")
        ctk.CTkLabel(
            bld_card,
            text=(
                f"Host OS: {bld_st['os']} · PyInstaller Ready: {'YES' if bld_st['pyinstaller_installed'] else 'Optional (pip install pyinstaller)'} · "
                f"Spec Ready: {'YES' if bld_st['spec_exists'] else 'NO'}\n"
                f"Installer Scripts: {', '.join(bld_st['installer_scripts']) or 'None yet'}"
            ),
            font=self.fonts["mono_small"],
            text_color=p.text_secondary,
            justify="left",
        ).pack(anchor="w", padx=16, pady=(0, 12))

        # Universal Settings Form
        form = ctk.CTkFrame(
            scroll, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
        )
        form.pack(fill="x", pady=(0, 10))
        form.grid_columnconfigure(1, weight=1)

        self.set_theme_var = tk.StringVar(value=self.config_obj.theme)
        self.set_scale_var = tk.StringVar(value=self.config_obj.ui_scale)
        self.set_lang_var = tk.StringVar(value=self.config_obj.language)
        self.set_os_var = tk.StringVar(value=self.config_obj.os_adapter_override)
        self.set_upd_var = tk.StringVar(value=self.config_obj.auto_update_mode)
        self.set_q_enabled = tk.BooleanVar(value=self.config_obj.quarantine_enabled)
        self.set_self_heal = tk.BooleanVar(value=self.config_obj.auto_self_heal)
        self.set_low_power = tk.BooleanVar(value=self.config_obj.low_power_ui_mode)
        self.set_tray_icon_var = tk.BooleanVar(value=self.config_obj.show_tray_icon)
        self.set_tray_var = tk.BooleanVar(value=self.config_obj.minimize_to_tray)
        self.set_start_min_var = tk.BooleanVar(value=self.config_obj.start_minimized)
        self.set_login_var = tk.BooleanVar(value=self.config_obj.launch_at_login)
        self.set_ram_guard_var = tk.BooleanVar(value=self.config_obj.ram_guard_enabled)
        self.set_sched_var = tk.BooleanVar(value=self.config_obj.background_schedule_enabled)
        self.set_privacy_var = tk.BooleanVar(value=self.config_obj.consent_privacy_scan)
        self.set_quiet = tk.BooleanVar(value=self.config_obj.quiet_hours_enabled)

        self.set_q_days = ctk.CTkEntry(form, width=100)
        self.set_q_days.insert(0, str(self.config_obj.quarantine_retention_days))
        self.set_q_max = ctk.CTkEntry(form, width=100)
        self.set_q_max.insert(0, str(self.config_obj.quarantine_max_mb))
        self.set_disk_floor = ctk.CTkEntry(form, width=100)
        self.set_disk_floor.insert(0, str(self.config_obj.disk_safety_floor_percent))

        sched_status = self.scheduler.schedule_status()
        sched_btn = ctk.CTkButton(
            form,
            text=("Remove Background Schedule" if sched_status.installed else "Install Background Schedule"),
            height=28,
            fg_color=p.bg_elevated,
            hover_color=p.border_strong,
            command=self.on_toggle_background_schedule,
        )
        self.sched_btn = sched_btn

        rows = [
            (
                "Visual Theme Scheme",
                ctk.CTkOptionMenu(form, values=["Obsidian Horology", "Atelier Alabaster", "High Contrast"], variable=self.set_theme_var),
                "Choose Dark Titanium, Light Studio, or WCAG High Contrast mode.",
            ),
            (
                "UI Scale Factor",
                ctk.CTkOptionMenu(
                    form,
                    values=["Auto (Monitor)", "90%", "100%", "110%", "125%", "150%", "175%"],
                    variable=self.set_scale_var,
                ),
                "Scale interface fonts & widget spacing from laptop screens up to 4K monitors.",
            ),
            (
                "Interface Language",
                ctk.CTkOptionMenu(form, values=list(SUPPORTED_LANGUAGES.keys()), variable=self.set_lang_var),
                "Switch localized interface labels (en = English, es = Español, fr = Français).",
            ),
            (
                "OS Adapter Mode",
                ctk.CTkOptionMenu(form, values=["auto", "Linux", "Darwin", "Windows"], variable=self.set_os_var),
                "Auto-detect host OS or simulate another OS playbook adapter.",
            ),
            (
                "Auto-Update Policy",
                ctk.CTkOptionMenu(form, values=["silent_auto", "notify", "manual"], variable=self.set_upd_var),
                "Controls whether SHA-256 + Ed25519 verified updates apply automatically.",
            ),
            (
                "Enable Reversible Quarantine Vault",
                ctk.CTkSwitch(form, text="Soft-move instead of delete", variable=self.set_q_enabled),
                "Keep cleaned items recoverable inside ~/.astracradle/quarantine.",
            ),
            (
                "Silent Self-Heal & Forensic Audit",
                ctk.CTkSwitch(form, text="Automatically verify & repair state", variable=self.set_self_heal),
                "Self-check 7 state/journal invariants on launch and periodically.",
            ),
            (
                "Low-Power UI Mode",
                ctk.CTkSwitch(form, text="Reduce telemetry polling cadence", variable=self.set_low_power),
                "Slows sensor refresh rate to save battery on laptops.",
            ),
            (
                "Bottom-Right System Tray Icon",
                ctk.CTkSwitch(form, text="Show live icon in bottom-right tray / menu bar", variable=self.set_tray_icon_var),
                "Displays AstraCradle's icon in the bottom-right notification tray (Windows/Linux) or menu bar (macOS).",
            ),
            (
                "Minimize to System Tray (Hide from Taskbar)",
                ctk.CTkSwitch(form, text="Minimize & close into tray instead of taskbar", variable=self.set_tray_var),
                "When ON, minimizing or closing hides AstraCradle into the bottom-right tray icon instead of keeping it in the taskbar.",
            ),
            (
                "Start Minimized to Tray",
                ctk.CTkSwitch(form, text="Open straight into bottom-right tray", variable=self.set_start_min_var),
                "Starts AstraCradle quietly in the bottom-right system tray without popping up the main window.",
            ),
            (
                "Launch AstraCradle at OS Login",
                ctk.CTkSwitch(form, text="Auto-start when computer turns on", variable=self.set_login_var),
                "Registers (or cleanly removes) AstraCradle in your OS login autostart folder.",
            ),
            (
                "Smart RAM Pressure Guard (>85%)",
                ctk.CTkSwitch(form, text="Auto-trim standby RAM under pressure", variable=self.set_ram_guard_var),
                "Automatically performs a safe working-set/standby RAM trim whenever RAM usage exceeds 85%.",
            ),
            (
                f"Native Background Schedule ({sched_status.mechanism})",
                sched_btn,
                f"Current status: {sched_status.details} ({sched_status.target_path}).",
            ),
            (
                "Quarantine Retention (Days)",
                self.set_q_days,
                "Days to retain quarantined files before automatic expiration sweep.",
            ),
            (
                "Quarantine Size Cap (MB)",
                self.set_q_max,
                "Maximum disk space allowed for Quarantine Vault backups.",
            ),
            (
                "System Disk Safety Floor (% Free)",
                self.set_disk_floor,
                "Alert threshold when free disk space drops below this percentage.",
            ),
            (
                "Quiet Hours Guard",
                ctk.CTkSwitch(form, text="Suppress non-critical auto tasks", variable=self.set_quiet),
                "Pause background automation during your Quiet Hours window.",
            ),
            (
                "Consent: Privacy Cache Scan",
                ctk.CTkSwitch(form, text="Allow non-credential browser cache scan", variable=self.set_privacy_var),
                "Scans browser caches while strictly preserving passwords, bookmarks, and login cookies.",
            ),
        ]
        for r_idx, (label, widget, tip_text) in enumerate(rows):
            lbl_w = ctk.CTkLabel(form, text=label, font=self.fonts["body"], anchor="w")
            lbl_w.grid(row=r_idx, column=0, sticky="w", padx=16, pady=6)
            widget.grid(row=r_idx, column=1, sticky="w", padx=16, pady=6)
            attach_tooltip(widget, tip_text, p)
            attach_tooltip(lbl_w, tip_text, p)

        prot_card = ctk.CTkFrame(
            scroll, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
        )
        prot_card.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(prot_card, text="Protected Process Allow-List (comma-separated)", font=self.fonts["body_bold"]).pack(
            anchor="w", padx=16, pady=(10, 4)
        )
        self.set_proc_box = ctk.CTkTextbox(prot_card, height=58, font=self.fonts["mono_small"])
        self.set_proc_box.insert("1.0", ", ".join(self.config_obj.excluded_processes))
        self.set_proc_box.pack(fill="x", padx=16, pady=(0, 8))

        ctk.CTkLabel(prot_card, text="Custom Protected Folders (one per line)", font=self.fonts["body_bold"]).pack(
            anchor="w", padx=16, pady=(4, 4)
        )
        self.set_paths_box = ctk.CTkTextbox(prot_card, height=54, font=self.fonts["mono_small"])
        self.set_paths_box.insert("1.0", "\n".join(self.config_obj.custom_protected_paths))
        self.set_paths_box.pack(fill="x", padx=16, pady=(0, 12))

        act_bar = ctk.CTkFrame(scroll, fg_color="transparent")
        act_bar.pack(fill="x", pady=(0, 14))
        ctk.CTkButton(
            act_bar,
            text="Save & Apply Settings",
            height=34,
            corner_radius=8,
            fg_color=p.accent_cyan,
            text_color="#071217",
            font=self.fonts["body_bold"],
            command=self.on_save_settings,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            act_bar,
            text="Export Config JSON",
            height=34,
            corner_radius=8,
            fg_color=p.bg_elevated,
            font=self.fonts["body"],
            command=self.on_export_config,
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            act_bar,
            text="Create Desktop Shortcut",
            height=34,
            corner_radius=8,
            fg_color=p.bg_elevated,
            font=self.fonts["body"],
            command=self.on_create_shortcuts,
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            act_bar,
            text="Uninstall AstraCradle...",
            height=34,
            corner_radius=8,
            fg_color=p.badge_crit_bg,
            hover_color=p.accent_coral,
            text_color=p.badge_crit_fg,
            font=self.fonts["body_bold"],
            command=self.open_uninstall_wizard,
        ).pack(side="right", padx=4)

    # ------------------------------------------------------------------
    # Async Telemetry, Actions & Events
    # ------------------------------------------------------------------
    def _schedule_telemetry_poll(self) -> None:
        ms = 5000 if self.config_obj.low_power_ui_mode else int(max(1.0, self.config_obj.telemetry_interval_sec) * 1000)
        self._poll_job = self.after(ms, self._poll_tick)

    def _poll_tick(self) -> None:
        if not self._is_busy:
            self.refresh_telemetry_async(include_dns=False, also_scan=False)
        self._schedule_telemetry_poll()

    def _drain_ui_queue(self) -> None:
        try:
            while True:
                item = self._ui_queue.get_nowait()
                first = item[0]
                if first == "show_window":
                    self.deiconify()
                    try:
                        self.state("normal")
                    except Exception:
                        pass
                    self.lift()
                    self.focus_force()
                    continue
                if first == "trigger_scan":
                    self.on_deep_scan()
                    continue
                if first == "fix_safe":
                    self.on_fix_everything_safe()
                    continue
                if first == "switch_profile":
                    prof = str(item[1] or "Balanced Daily")
                    if prof in SITUATION_PROFILES:
                        self.config_obj.apply_profile(prof)
                        self.cfg_mgr.save(self.config_obj)
                        if hasattr(self, "profile_menu"):
                            self.profile_menu.set(prof)
                        self.show_toast(f"Switched profile to {prof}.")
                        self.tray.notify("AstraCradle", f"Active profile: {prof}")
                    continue
                if first == "toggle_probalance":
                    self.config_obj.probalance_enabled = not self.config_obj.probalance_enabled
                    self.cfg_mgr.save(self.config_obj)
                    state = "ON" if self.config_obj.probalance_enabled else "OFF"
                    self.show_toast(f"ProBalance {state}")
                    self.tray.notify("AstraCradle", f"ProBalance {state}")
                    continue
                if first == "toggle_astrashield":
                    if self.adblock_shield.dns_running:
                        _, msg = self.adblock_shield.stop_dns_sinkhole()
                    else:
                        _, msg = self.adblock_shield.start_dns_sinkhole(
                            port=self.config_obj.astrashield_dns_port,
                            upstream=self.config_obj.astrashield_upstream_dns,
                        )
                    self.show_toast(msg)
                    self.tray.notify("AstraShield", msg)
                    continue
                if first == "quit_app":
                    self.on_close(force_quit=True)
                    return
                if first == "full_discovery_done":
                    out, show_dlg = item[1], bool(item[2])
                    self._last_baseline = out.baseline
                    self.latest_snapshot = out.snapshot
                    self.latest_report = out.report
                    self.latest_playbook = out.playbook
                    self._last_latency_report = out.latency_report
                    self._last_autoruns = out.autoruns
                    self._last_ghosts = out.ghost_drivers
                    self.topbar_beacon.set_scanning(False)
                    self._update_telemetry_cards(out.snapshot)
                    self._update_diagnosis_overview(out.report, out.playbook)
                    self.topbar_status.configure(text=f"Full PC Baseline · {len(out.baseline.subsystems_checked)} subsystems")
                    self.show_toast(
                        f"Full PC Discovery complete ({len(out.baseline.subsystems_checked)} local subsystems baselined)!"
                    )
                    if show_dlg:
                        FullDiscoveryResultDialog(self, self.palette, self.fonts, out.baseline)
                    continue
                if first == "full_discovery_err":
                    self.topbar_beacon.set_scanning(False)
                    self.topbar_status.configure(text="Discovery warning")
                    self.show_toast(f"Full PC Discovery note: {item[1]}", color=self.palette.accent_coral)
                    continue

                if len(item) == 4 and isinstance(item[0], SystemSnapshot):
                    snap, report, pb, gpu = item
                    self._latest_gpu = gpu
                else:
                    snap, report, pb = item
                self.latest_snapshot = snap
                self.leak_watchdog.record_snapshot(snap.top_processes)
                ad_act, ad_sid, ad_app = self.auto_detector.evaluate(snap.top_processes, self.config_obj)
                if ad_act and ad_sid:
                    _, ad_msg = self.scripts_engine.toggle_script(
                        ad_sid,
                        ad_act == "enable",
                        self._active_adapter(),
                        self.config_obj,
                        snap.top_processes,
                        dry_run=bool(self.dry_run_var.get()),
                    )
                    self.show_toast(f"Auto-Detector ({ad_app}): {ad_msg}")
                hook_msg = self.pipe_mgr.poll_local_hook_trigger(
                    self._active_adapter(), self.config_obj, snap.top_processes
                )
                if hook_msg:
                    self.show_toast(hook_msg)
                self._update_telemetry_cards(snap, self._latest_gpu)
                focus = FullscreenFocusDetector.detect()
                self._last_focus_state = focus
                if self.config_obj.thermal_spc_enabled:
                    tier = self.thermal_spc.classify_workload(snap.cpu_percent, is_fullscreen=focus.is_fullscreen)
                    anoms = self.thermal_spc.update_sample(tier, snap.temperature_c, snap.cpu_percent)
                    if anoms:
                        self.show_toast(anoms[0].summary)
                pb_notes = self.probalance.evaluate_tick(
                    snap.top_processes,
                    focus.process_name,
                    self.config_obj,
                    dry_run=bool(self.dry_run_var.get()),
                )
                if pb_notes:
                    self.show_toast(pb_notes[0])
                if report and pb:
                    self.latest_report = report
                    self.latest_playbook = pb
                    self._update_diagnosis_overview(report, pb)
                    import time as _t
                    persist_now = (_t.time() - self._last_history_write) > 15.0
                    self.history_db.record_tick(snap, pb.health_score, persist_db=persist_now)
                    if persist_now:
                        self._last_history_write = _t.time()
                    res_key = self.config_obj.telemetry_resolution
                    self.history_chart.set_points(
                        self.history_db.query_resolution(res_key, limit=120), resolution=res_key
                    )
                    alerts = self.scheduler.evaluate_threshold_alerts(snap, report, self.config_obj)
                    if alerts:
                        self.tray.notify(alerts[0].title, alerts[0].message)
                    if self.current_nav == "playbook":
                        self._render_playbook()
                    elif self.current_nav == "hunter":
                        self._render_hunter()
                    elif self.current_nav == "modules":
                        self._render_modules()
                elif self.current_nav == "modules" and self.current_module_tab in ("Processes", "Processes & Firewall"):
                    self._render_processes_module()
                fs_tag = f" · Focus Guard ({focus.process_name})" if (self.config_obj.pause_during_fullscreen and focus.is_fullscreen) else ""
                self.topbar_status.configure(text=f"Live · {snap.timestamp.split('T')[-1]}{fs_tag}")
        except queue.Empty:
            pass
        if self.winfo_exists():
            self._drain_job = self.after(120, self._drain_ui_queue)

    def refresh_telemetry_async(self, include_dns: bool = False, also_scan: bool = False) -> None:
        def worker():
            adapter = self._active_adapter()
            snap = self.telemetry.collect(
                adapter,
                include_dns=include_dns,
                consent_telemetry=self.config_obj.consent_telemetry,
                consent_processes=self.config_obj.consent_process_scan,
            )
            gpu = GpuTelemetryCollector.collect()
            report = self.diagnostics.run_scan(snap, adapter, self.config_obj) if also_scan else None
            pb = self.playbook_builder.build(report, self.config_obj) if report else None
            self._ui_queue.put((snap, report, pb, gpu))

        threading.Thread(target=worker, daemon=True).start()

    def _update_telemetry_cards(self, snap: SystemSnapshot, gpu=None) -> None:
        self.cpu_val_lbl.configure(text=f"{snap.cpu_percent:.1f}%")
        freq_txt = f"{snap.cpu_freq_mhz:.0f} MHz" if snap.cpu_freq_mhz else "Adaptive"
        self.cpu_sub_lbl.configure(text=f"{snap.cpu_count_logical} logical cores · {freq_txt}")
        self.cpu_spark.push(snap.cpu_percent)

        self.ram_val_lbl.configure(text=f"{snap.mem_percent:.1f}%")
        self.ram_sub_lbl.configure(
            text=f"{format_bytes(snap.mem_used_bytes)} / {format_bytes(snap.mem_total_bytes)} (Swap {snap.swap_percent:.0f}%)"
        )
        self.ram_spark.push(snap.mem_percent)

        if snap.partitions:
            rp = snap.partitions[0]
            self.disk_val_lbl.configure(text=f"{rp.percent:.1f}%")
            self.disk_sub_lbl.configure(text=f"{rp.mountpoint}: {format_bytes(rp.free_bytes)} free of {format_bytes(rp.total_bytes)}")
            self.disk_spark.push(rp.percent)

        dns_txt = f"{snap.dns_latency_ms:.0f} ms" if snap.dns_latency_ms is not None else "Probed on Scan"
        self.net_val_lbl.configure(text=dns_txt)
        self.net_sub_lbl.configure(text=f"RX {format_bytes(snap.net_bytes_recv)} / TX {format_bytes(snap.net_bytes_sent)}")
        self.net_spark.push(min(100.0, (snap.dns_latency_ms or 35.0) / 3.0))

        gpu = gpu if gpu is not None else GpuTelemetryCollector.collect()
        if gpu.available:
            util = gpu.utilization_percent or 0.0
            vram_txt = (
                f"VRAM {gpu.vram_used_mb:.0f}/{gpu.vram_total_mb:.0f} MB"
                if gpu.vram_used_mb is not None and gpu.vram_total_mb is not None
                else "VRAM N/A"
            )
            throt = f" · {gpu.throttling_reason}" if gpu.throttling_reason else " · No Thermal Throttling"
            self.gpu_val_lbl.configure(text=f"{util:.0f}%")
            self.gpu_sub_lbl.configure(text=f"{gpu.name[:24]} · {vram_txt}{throt}")
            self.gpu_spark.push(util)
        else:
            self.gpu_val_lbl.configure(text="Integrated")
            self.gpu_sub_lbl.configure(text=gpu.name)
            self.gpu_spark.push(snap.cpu_percent * 0.35)

    def _update_diagnosis_overview(self, report: DiagnosticReport, pb: Playbook) -> None:
        p = self.palette
        snap = report.snapshot
        subtitle = "Optimal" if pb.health_score >= 88 else ("Balanced" if pb.health_score >= 70 else "Needs Care")
        self.gauge.set_score(pb.health_score, subtitle)
        self.dash_headline.configure(text=pb.summary_headline)
        self.dash_summary.configure(text=pb.executive_summary)
        self.dash_meta.configure(
            text=(
                f"Host: {snap.hostname} | Uptime: {format_uptime(snap.uptime_seconds)} | "
                f"OS Adapter: {self._active_adapter().os_family} | Profile: {self.config_obj.active_profile}"
            )
        )

        for c in self.dash_findings_frame.winfo_children():
            c.destroy()
        hdr = ctk.CTkFrame(self.dash_findings_frame, fg_color="transparent")
        hdr.pack(fill="x", padx=16, pady=(10, 4))
        ctk.CTkLabel(hdr, text="Active Diagnosis, AI Bloat & Safeguard Highlights", font=self.fonts["body_bold"]).pack(
            side="left"
        )
        repaired_cnt = sum(1 for e in self.latest_self_heal if e.repaired_automatically)
        self.footer_heal_lbl.configure(text=f"Self-Heal: {len(self.latest_self_heal)}/7 OK ({repaired_cnt} repaired)")

        for f in report.findings[:4]:
            line = ctk.CTkFrame(self.dash_findings_frame, fg_color="transparent")
            line.pack(fill="x", padx=16, pady=2)
            make_badge(line, f.subsystem.upper(), p.bg_elevated, p.accent_cyan, self.fonts["caption"]).pack(
                side="left", padx=(0, 8)
            )
            ctk.CTkLabel(line, text=f.title, font=self.fonts["small"], text_color=p.text_secondary).pack(side="left")
        ctk.CTkFrame(self.dash_findings_frame, height=8, fg_color="transparent").pack()

    def on_deep_scan(self) -> None:
        self.topbar_status.configure(text="Deep scanning 8 subsystems...")
        self.refresh_telemetry_async(include_dns=True, also_scan=True)
        self.show_toast("Deep Scan complete — Playbook & AI Hunter updated!")

    def on_execute_playbook_step(self, step: PlaybookStep) -> None:
        if not step.action_id:
            return
        res = self.executor.execute_action(
            step.action_id,
            self._active_adapter(),
            self.config_obj,
            params=step.action_params,
            dry_run=bool(self.dry_run_var.get()),
            why=f"Playbook Step {step.step_number}: {step.title}",
        )
        step.status = "simulated" if res.dry_run else ("applied" if res.ok else "failed")
        step.result_note = res.message
        if res.dry_run and res.preview_items:
            DryRunDiffDialog(
                self,
                self.palette,
                self.fonts,
                res.title,
                res.message,
                res.preview_items,
                on_execute_real=lambda s=step: self._execute_step_real(s),
            )
        self.show_toast(res.message, undo_entry_id=(None if res.dry_run else res.entry_id))
        self._render_playbook()
        self.refresh_telemetry_async(include_dns=False, also_scan=True)

    def _execute_step_real(self, step: PlaybookStep) -> None:
        if not step.action_id:
            return
        res = self.executor.execute_action(
            step.action_id,
            self._active_adapter(),
            self.config_obj,
            params=step.action_params,
            dry_run=False,
            why=f"Confirmed after Dry-Run diff preview: {step.title}",
        )
        step.status = "applied" if res.ok else "failed"
        step.result_note = res.message
        self.show_toast(res.message, undo_entry_id=res.entry_id)
        self._render_playbook()
        self.refresh_telemetry_async(include_dns=False, also_scan=True)

    def on_safe_auto_optimize(self) -> None:
        if not self.latest_playbook:
            self.on_deep_scan()
            return
        adapter = self._active_adapter()
        dry = bool(self.dry_run_var.get())
        ran = 0
        for step in self.latest_playbook.steps:
            if step.can_auto_fix and step.action_id and step.safety_tier in ("safe_auto", "guarded"):
                res = self.executor.execute_action(
                    step.action_id,
                    adapter,
                    self.config_obj,
                    params=step.action_params,
                    dry_run=dry,
                    why="Safe Auto-Optimize batch run",
                )
                step.status = "simulated" if res.dry_run else ("applied" if res.ok else "failed")
                step.result_note = res.message
                ran += 1
        self.show_toast(f"{'Simulated' if dry else 'Safely executed'} {ran} guarded optimization step(s).")
        self.refresh_telemetry_async(include_dns=True, also_scan=True)

    def on_clean_selected_caches(self) -> None:
        if not self.latest_report:
            return
        paths = [
            t.path
            for t in self.latest_report.cache_targets
            if t.exists and self._cache_check_vars.get(t.target_id, tk.BooleanVar(value=False)).get()
        ]
        if not paths:
            self.show_toast("Select at least one existing cache target first.")
            return
        res = self.executor.execute_action(
            "clean_paths",
            self._active_adapter(),
            self.config_obj,
            params={"paths": paths},
            dry_run=bool(self.dry_run_var.get()),
            why="User selected storage targets in System Modules",
        )
        if res.dry_run and res.preview_items:
            DryRunDiffDialog(
                self,
                self.palette,
                self.fonts,
                res.title,
                res.message,
                res.preview_items,
                on_execute_real=lambda ps=paths: self._clean_paths_confirmed(ps),
            )
        self.show_toast(res.message, undo_entry_id=(None if res.dry_run else res.entry_id))
        self.refresh_telemetry_async(include_dns=False, also_scan=True)

    def _clean_paths_confirmed(self, paths: list[str]) -> None:
        res = self.executor.execute_action(
            "clean_paths",
            self._active_adapter(),
            self.config_obj,
            params={"paths": paths},
            dry_run=False,
            why="User confirmed Dry-Run diff preview",
        )
        self.show_toast(res.message, undo_entry_id=res.entry_id)
        self.refresh_telemetry_async(include_dns=False, also_scan=True)

    def _run_single_action(self, action_id: str) -> None:
        res = self.executor.execute_action(
            action_id, self._active_adapter(), self.config_obj, dry_run=bool(self.dry_run_var.get())
        )
        self.show_toast(res.message)
        if self.current_nav == "safeguards":
            self._render_safeguards()

    def on_renice_proc(self, pid: int) -> None:
        _, msg = self.executor.safe_renice_process(pid, self.config_obj)
        self.show_toast(msg)
        self.refresh_telemetry_async(include_dns=False, also_scan=True)

    def on_terminate_proc(self, pid: int, name: str) -> None:
        if self.config_obj.require_confirm_moderate:
            if not messagebox.askyesno(
                "Confirm Turn Off",
                f"Send graceful terminate signal (SIGTERM) to '{name}' (PID {pid})?",
            ):
                return
        ok, msg = self.executor.safe_terminate_process(pid, self.config_obj)
        self.show_toast(msg)
        if ok:
            self.refresh_telemetry_async(include_dns=False, also_scan=True)

    def on_run_self_heal(self) -> None:
        self.latest_self_heal, self.latest_audit = self.self_heal.run_self_check(
            self._active_adapter(), self.config_obj
        )
        self.config_obj = self.cfg_mgr.load()
        repaired = sum(1 for e in self.latest_self_heal if e.repaired_automatically)
        self.show_toast(f"Self-Heal verified 7 invariants ({repaired} auto-repaired).")
        if self.current_nav == "safeguards":
            self._render_safeguards()

    def on_simulate_self_heal(self) -> None:
        self.latest_self_heal, self.latest_audit = self.self_heal.simulate_fault_and_heal(
            self._active_adapter(), self.config_obj
        )
        self.config_obj = self.cfg_mgr.load()
        self.show_toast("Fault injected into config.json -> Self-Heal automatically restored from backup!")
        self._render_safeguards()

    def on_simulate_crash(self) -> None:
        report = self.crash_analyzer.simulate_crash_and_diagnose(self.config_obj.active_profile)
        self._render_safeguards()
        CrashRecoveryDialog(self, self.palette, self.fonts, report)

    def on_rollback_entry(self, entry_id: str) -> None:
        ok, msg = self.rollback_mgr.rollback_entry(entry_id)
        self.journal.log_event(
            "RollbackManager",
            "rollback_entry",
            msg,
            f"User restored quarantined files from entry {entry_id}",
            "ok" if ok else "warning",
        )
        self.show_toast(msg)
        if self.current_nav == "safeguards":
            self._render_safeguards()
        self.refresh_telemetry_async(include_dns=False, also_scan=True)

    def on_shred_entry(self, entry_id: str) -> None:
        if not messagebox.askyesno(
            "Confirm Permanent Zero-Pass Shred",
            "Permanently zero-overwrite and delete quarantined files for this entry? This CANNOT be undone.",
        ):
            return
        ok, msg = self.rollback_mgr.shred_quarantine_entry(entry_id, confirm_irreversible=True)
        self.journal.log_event(
            "RollbackManager",
            "shred_quarantine_entry",
            msg,
            f"User confirmed irreversible zero-pass shred for {entry_id}",
            "ok" if ok else "warning",
        )
        self.show_toast(msg)
        self._render_safeguards()

    def on_export_playbook(self, fmt: str) -> None:
        if self.latest_playbook:
            out = self.playbook_builder.export(self.latest_playbook, self.cfg_mgr.playbooks_dir, fmt=fmt)
            self.show_toast(f"Exported Playbook -> {out}")

    def on_check_updates(self) -> None:
        if hasattr(self, "set_update_repo_var"):
            repo_txt = self.set_update_repo_var.get().strip()
            if repo_txt and repo_txt != self.config_obj.update_repo_url:
                self.config_obj.update_repo_url = repo_txt
                self.cfg_mgr.save(self.config_obj)

        repo_root = Path(__file__).resolve().parents[2]
        res = self.updater.check_for_updates(self.config_obj, check_remote=True, repo_dir=repo_root)
        if res.update_available:
            if res.release_notes.startswith("https://"):
                if messagebox.askyesno(
                    f"AstraCradle GitHub Update Ready (v{res.latest_version})",
                    f"Found newer release on GitHub:\n{res.release_notes}\n\n"
                    "Pull and install this update now (your settings, profiles & rollback vault stay intact)?",
                ):
                    ok, msg = self.updater.pull_from_github_and_update(self.config_obj, repo_dir=repo_root)
                    if ok:
                        self.lifecycle.install_shortcuts(repo_root)
                    self.upd_status_lbl.configure(text=msg)
                    self.show_toast(msg)
                    return
            bundle_cand = Path(res.release_notes) if res.release_notes and Path(res.release_notes).exists() else None
            if bundle_cand is not None:
                if messagebox.askyesno(
                    f"AstraCradle Update Ready (v{res.latest_version})",
                    f"Found update package:\n{bundle_cand}\n\n"
                    f"SHA-256: {res.package_hash[:24]}...\n\n"
                    "Apply this update now (your settings, profiles & rollback vault are preserved)?",
                ):
                    ok, msg = self.updater.install_update_bundle(bundle_cand, self.config_obj)
                    if ok:
                        self.lifecycle.install_shortcuts(repo_root)
                    self.upd_status_lbl.configure(text=msg)
                    self.show_toast(msg)
                    return
            elif self.config_obj.auto_update_mode == "silent_auto" or messagebox.askyesno(
                f"AstraCradle Update Ready (v{res.latest_version})",
                f"{res.status_message}\n\nApply staged update now?",
            ):
                ok, msg = self.updater.stage_and_apply_local_update(self.config_obj)
                self.upd_status_lbl.configure(text=msg)
                self.show_toast(msg)
                return
        self.upd_status_lbl.configure(text=res.status_message)
        self.show_toast(res.status_message)

    def on_install_update_bundle(self) -> None:
        picked = filedialog.askopenfilename(
            title="Select AstraCradle Update Bundle (.zip or .tar.gz)",
            filetypes=[
                ("AstraCradle Update Bundles", "*.zip *.tar.gz *.tgz"),
                ("All Files", "*.*"),
            ],
        )
        if not picked:
            return
        ok, msg = self.updater.install_update_bundle(Path(picked), self.config_obj)
        if ok:
            self.lifecycle.install_shortcuts(Path(__file__).resolve().parents[2])
        self.upd_status_lbl.configure(text=msg)
        self.show_toast(msg)

    def on_toggle_background_schedule(self) -> None:
        st = self.scheduler.schedule_status()
        if st.installed:
            ok, msg = self.scheduler.remove_schedule()
            self.config_obj.background_schedule_enabled = False
        else:
            ok, msg = self.scheduler.install_schedule(self.config_obj.schedule_interval_minutes)
            self.config_obj.background_schedule_enabled = ok
        self.cfg_mgr.save(self.config_obj)
        new_st = self.scheduler.schedule_status()
        self.sched_btn.configure(
            text="Remove Background Schedule" if new_st.installed else "Install Background Schedule"
        )
        self.show_toast(msg)

    def on_run_benchmark(self) -> None:
        prev = self.benchmark.load_history()
        res = self.benchmark.run(label=self.config_obj.active_profile)
        delta_txt = ""
        if prev:
            delta = res.composite_score - prev[0].composite_score
            delta_txt = f" ({'+' if delta >= 0 else ''}{delta} vs prior)"
        msg = (
            f"Score {res.composite_score}/100{delta_txt} · CPU {res.cpu_mops_per_sec} Mops/s · "
            f"RAM {res.mem_copy_mb_per_sec} MB/s · Disk W/R {res.disk_write_mb_per_sec}/{res.disk_read_mb_per_sec} MB/s"
        )
        self.bench_lbl.configure(text=msg)
        self.show_toast(f"Micro-Benchmark complete: {res.composite_score}/100{delta_txt}")

    def on_export_recovery_bundle(self) -> None:
        out = self.recovery_exporter.export_zip()
        self.show_toast(f"Recovery Bundle exported -> {out}")

    def on_generate_weekly_digest(self) -> None:
        score = self.latest_playbook.health_score if self.latest_playbook else 90
        recl = self.latest_playbook.total_reclaimable_bytes if self.latest_playbook else 0
        crashes = len(self.crash_analyzer.load_history())
        entries = len(self.journal.read_entries(limit=500))
        out = self.digest_gen.generate(score, recl, crashes, entries, self.config_obj.active_profile)
        self.show_toast(f"Weekly HTML Digest saved -> {out}")

    def _on_toggle_auto_detect(self) -> None:
        self.config_obj.app_auto_detect_enabled = bool(self.auto_det_var.get())
        self.cfg_mgr.save(self.config_obj)
        self.show_toast(
            f"App Auto-Detector {'ENABLED' if self.config_obj.app_auto_detect_enabled else 'DISABLED'}."
        )

    def _explain_process(self, proc) -> None:
        info = ProcessTranslator.explain(
            proc.name, is_protected=proc.protected, is_ai=(proc.category == "ai_chatbot")
        )
        ProcessExplanationDialog(self, self.palette, self.fonts, info)

    def _delay_startup_item(self, item) -> None:
        ok, msg = StartupDelayManager.apply_delay_to_item(item, delay_seconds=60)
        self.journal.log_event(
            "StartupDelay",
            "delay_startup_item",
            msg,
            "User chose to delay heavy startup item by 60s for faster login",
            "ok" if ok else "warning",
        )
        self.show_toast(msg)

    def _run_dead_sweep(self) -> None:
        roots = [self._last_dup_scan_dir, str(Path.home() / ".cache")]
        self._last_dead_items = self.dead_sweeper.scan(roots, self._active_adapter(), max_items=60)
        self.show_toast(f"Found {len(self._last_dead_items)} empty folders / broken shortcuts / dead symlinks.")
        self._render_modules()

    def _quarantine_privacy_traces(self) -> None:
        traces = self.privacy_sweeper.scan(self.config_obj)
        if not traces:
            self.show_toast("No privacy caches to clean.")
            return
        res = self.executor.execute_action(
            "clean_paths",
            self._active_adapter(),
            self.config_obj,
            params={"paths": [t.path for t in traces]},
            dry_run=bool(self.dry_run_var.get()),
            why="User cleaned non-credential browser/thumbnail caches",
        )
        self.show_toast(res.message)
        self._render_modules()

    def _rebaseline_shield(self) -> None:
        self.integrity_shield.accept_new_baseline()
        self.show_toast("Accepted new SHA-256 baseline for Integrity Shield.")
        self._render_modules()

    def on_save_settings(self) -> None:
        theme_changed = self.set_theme_var.get() != self.config_obj.theme
        lang_changed = self.set_lang_var.get() != self.config_obj.language
        self.config_obj.theme = self.set_theme_var.get()
        self.config_obj.ui_scale = self.set_scale_var.get()
        self.config_obj.language = self.set_lang_var.get()
        self.i18n.set_language(self.config_obj.language)
        self.config_obj.os_adapter_override = self.set_os_var.get()
        self.config_obj.auto_update_mode = self.set_upd_var.get()
        if hasattr(self, "set_update_repo_var"):
            repo_val = self.set_update_repo_var.get().strip()
            if repo_val:
                self.config_obj.update_repo_url = repo_val
        self.config_obj.quarantine_enabled = bool(self.set_q_enabled.get())
        self.config_obj.auto_self_heal = bool(self.set_self_heal.get())
        self.config_obj.low_power_ui_mode = bool(self.set_low_power.get())
        self.config_obj.show_tray_icon = bool(self.set_tray_icon_var.get())
        self.config_obj.minimize_to_tray = bool(self.set_tray_var.get())
        self.config_obj.start_minimized = bool(self.set_start_min_var.get())
        self.config_obj.launch_at_login = bool(self.set_login_var.get())
        self.config_obj.ram_guard_enabled = bool(self.set_ram_guard_var.get())
        self.config_obj.consent_privacy_scan = bool(self.set_privacy_var.get())
        self.config_obj.quiet_hours_enabled = bool(self.set_quiet.get())
        self.lifecycle.set_launch_at_login(
            self.config_obj.launch_at_login,
            Path(__file__).resolve().parents[2],
            start_minimized=self.config_obj.start_minimized,
        )
        if self.config_obj.show_tray_icon or self.config_obj.minimize_to_tray:
            self.tray.start()
        else:
            self.tray.stop()
        try:
            self.config_obj.quarantine_retention_days = max(1, min(365, int(self.set_q_days.get())))
            self.config_obj.quarantine_max_mb = max(64, min(102400, int(self.set_q_max.get())))
            floor = float(self.set_disk_floor.get())
            if math.isfinite(floor):
                self.config_obj.disk_safety_floor_percent = max(2.0, min(40.0, floor))
        except ValueError:
            pass
        raw_procs = self.set_proc_box.get("1.0", "end").strip()
        self.config_obj.excluded_processes = [x.strip() for x in raw_procs.split(",") if x.strip()]
        raw_paths = self.set_paths_box.get("1.0", "end").strip()
        self.config_obj.custom_protected_paths = [x.strip() for x in raw_paths.splitlines() if x.strip()]

        self.telemetry.update_exclusions(self.config_obj.excluded_processes)
        self.cfg_mgr.save(self.config_obj)
        self.journal.log_event(
            "Settings",
            "save_settings",
            "Saved Universal Settings & Protected Allow-Lists",
            "User updated system configuration",
            "ok",
        )
        self._apply_ui_scale()
        if theme_changed or lang_changed or self.set_scale_var.get() != getattr(self, "_applied_scale", self.config_obj.ui_scale):
            self._hot_reload_ui()
            self.refresh_telemetry_async(include_dns=True, also_scan=True)
            self.show_toast("Theme, scale & language applied live!")
        else:
            self.refresh_telemetry_async(include_dns=True, also_scan=True)
            self.show_toast("Universal Settings saved & applied.")

    def _hot_reload_ui(self) -> None:
        """Rebuilds sidebar & workspace live when theme, contrast, scale, or language changes."""
        active_nav = self.current_nav
        active_hw = self.current_hw_tab
        active_mod = self.current_module_tab
        active_pb = self.current_pb_tab
        active_auto = self.current_auto_tab
        self.palette = get_palette(self.config_obj.theme)
        self.fonts = get_ui_fonts(self._scale_float())
        self._applied_scale = self.config_obj.ui_scale
        ctk.set_appearance_mode(self.palette.mode)
        self.configure(fg_color=self.palette.bg_canvas)
        for w in (getattr(self, "sidebar", None), getattr(self, "main_area", None)):
            if w is not None:
                try:
                    w.destroy()
                except Exception:
                    pass
        self._build_layout()
        self.current_hw_tab = active_hw
        self.current_module_tab = active_mod
        self.current_pb_tab = active_pb
        self.current_auto_tab = active_auto
        self.switch_nav(active_nav)
        if self.latest_snapshot:
            self._update_telemetry_cards(self.latest_snapshot)
        if self.latest_report and self.latest_playbook:
            self._update_diagnosis_overview(self.latest_report, self.latest_playbook)

    def on_export_config(self) -> None:
        dest = filedialog.asksaveasfilename(
            title="Export AstraCradle Config",
            defaultextension=".json",
            initialfile="astracradle_profile.json",
            filetypes=[("JSON files", "*.json")],
        )
        if dest:
            Path(dest).write_text(json.dumps(asdict(self.config_obj), indent=2), encoding="utf-8")
            self.show_toast(f"Exported configuration to {dest}")

    def on_fix_everything_safe(self) -> None:
        _, summary, notes = self.safe_fixer.run_fix_everything_safe(
            self._active_adapter(),
            self.config_obj,
            dry_run=bool(self.dry_run_var.get()),
        )
        self.show_toast(summary)
        if self._show_startup_popups:
            messagebox.showinfo(summary, "\n".join(f"• {n}" for n in notes))
        self.refresh_telemetry_async(include_dns=False, also_scan=True)

    def on_show_weekly_digest(self) -> None:
        blk = self.adblock_shield.status().blocked_queries
        digest = self.safe_fixer.build_weekly_digest(adblock_blocked_count=blk)
        self.show_toast("Generated 7-Day AstraCradle Weekly Digest.")
        if self._show_startup_popups:
            messagebox.showinfo("AstraCradle 7-Day Weekly Digest", digest)

    def _on_toggle_astrashield_hosts(self, enable: bool) -> None:
        if enable:
            ok, msg = self.adblock_shield.enable_hosts_sinkhole(
                self.config_obj.astrashield_categories,
                dry_run=bool(self.dry_run_var.get()),
            )
        else:
            ok, msg = self.adblock_shield.disable_hosts_sinkhole()
        self.config_obj.astrashield_enabled = bool(enable and ok)
        self.cfg_mgr.save(self.config_obj)
        self.show_toast(msg)
        self._render_modules()

    def _on_toggle_astrashield_dns(self, enable: bool) -> None:
        if enable:
            _, msg = self.adblock_shield.start_dns_sinkhole(
                port=self.config_obj.astrashield_dns_port,
                upstream=self.config_obj.astrashield_upstream_dns,
            )
        else:
            _, msg = self.adblock_shield.stop_dns_sinkhole()
        self.show_toast(msg)
        self._render_modules()

    def _on_save_diff_snapshot(self) -> None:
        out = self.baseline_diff.capture_snapshot(
            self._active_adapter(),
            self.config_obj,
            autoruns=self.autoruns_auditor,
        )
        self.show_toast(f"Saved PC snapshot -> {out.name}")
        self._render_modules()

    def _on_compare_snapshots(self) -> None:
        snaps = self.baseline_diff.list_snapshots()
        if len(snaps) < 2:
            self.baseline_diff.capture_snapshot(self._active_adapter(), self.config_obj, autoruns=self.autoruns_auditor)
            snaps = self.baseline_diff.list_snapshots()
        if len(snaps) < 2:
            self.show_toast("Saved initial baseline snapshot. Save one more later to compare changes!")
            return
        diff = self.baseline_diff.diff_snapshots(snaps[-2], snaps[-1])
        self.show_toast(diff.summary_lines[1])
        if self._show_startup_popups:
            messagebox.showinfo("What Changed Since My PC Was Fast?", "\n".join(diff.summary_lines))

    def _copy_text(self, text: str) -> None:
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.update()
            self.show_toast("Copied commands to clipboard!")
        except Exception:
            self.show_toast("Clipboard unavailable in headless display.")


def launch_gui(base_dir: Path | str | None = None, start_minimized: bool = False) -> None:
    app = AstraCradleApp(base_dir=base_dir, start_minimized=start_minimized)
    app.mainloop()
