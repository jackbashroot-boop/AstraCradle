"""Main native desktop application window for AstraCradle."""

from __future__ import annotations

from dataclasses import asdict
import json
import math
from dataclasses import asdict
from pathlib import Path
import platform
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image, ImageTk

from .. import __app_name__, __tagline__, __version__
from ..core.adblock_shield import AstraShieldManager
from ..core.astrabeam import AstraBeamClient, AstraBeamServer, SunshineMoonlightBridge, TailscaleDiscovery
from ..core.autopilot import AutopilotEngine
from ..core.bloat_analyzer import BloatAnalyzer, DuplicateGroup
from ..core.config import AppConfig, ConfigManager, SITUATION_PROFILES
from ..core.crash_analyzer import CrashAnalyzer
from ..core.diagnostics import DiagnosticReport, DiagnosticsEngine
from ..core.elevation import ElevationRunner
from ..core.executor import GuardedExecutor
from ..core.hotkeys import GlobalHotkeyManager
from ..core.pc_extras import (
    BaselineDiffEngine,
    BatteryAndPowerInspector,
    ConflictProgramDetector,
    MemoryStandbyManager,
    NetworkAndDnsInspector,
    SafeBeginnerFixer,
    StartupDelayManager as StartupFileDelayManager,
)
from ..core.advanced_controls import (
    ActiveWindowState,
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
from ..core.modules import (
    ASTRA_MODULES,
    INSTALL_BUNDLE_PRESETS,
    MODULE_BY_ID,
    NAV_KEY_TO_MODULE_ID,
    allowed_nav_keys,
    normalize_enabled_modules,
)
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
    AIDiagnosticArchiveExporter,
    ConfigIntegrityShield,
    PortableProfileBackup,
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
from ..core.telemetry import TelemetryCollector, format_bytes
from ..core.telemetry_history import (
    GlitchTroubleshooter,
    GpuTelemetryCollector,
    OutdatedAppsChecker,
    PreFlightSnapshotHook,
    TelemetryHistoryDB,
)
from ..core.thermal_spc import ThermalSpcMonitor
from ..core.lan_tv_shield import LanTvAdShield
from ..core.usb_guard import UsbAndPeripheralGuard
from ..core.updater import ModuleAndUpdateManager
from ..core.wifi_channel_optimizer import WifiChannelOptimizer
from ..core.rf_bandwidth_scanner import BandwidthAndRfScanner
from ..core.system_catalog_scanner import KNOWN_ISSUES_DB, SystemCatalogAndHealer
from ..theme import ThemePalette, get_palette, get_ui_fonts
from .modals import (
    AutomationPromptDialog,
    ComprehensiveSetupDialog,
    ConsentAutomationDialog,
    CrashRecoveryDialog,
    CustomScriptDialog,
    DesktopTelemetryWidget,
    DryRunDiffDialog,
    DysonStarGatherSplash,
    EasyGuideDialog,
    FloatingMiniOrbWindow,
    FullDiscoveryResultDialog,
    HelpSearchDialog,
    PipelineEditorDialog,
    ProcessExplanationDialog,
    SetupWizardDialog,
    SudoPasswordDialog,
    UninstallDialog,
    YesNoActionDialog,
)
from .pages.command_vault import render_command_vault_page
from .pages.custom_rules_benchmarks import render_benchmarks_section, render_custom_rules_section
from .pages.dashboard_snapshot import (
    build_command_deck_snapshot,
    update_command_deck_snapshot_inplace,
    update_snapshot_findings_inplace,
)
from .pages.hermes_page import render_hermes_page
from .pages.monitoring import build_monitoring_page, refresh_monitoring_peripherals, update_monitoring_live
from .pages.network_failover import render_failover_section
from .pages.remote_beam import AstraBeamViewerWindow, render_remote_beam_page
from .pages.rf_waves import render_rf_waves_page
from .pages.wifi_mesh import render_wifi_mesh_section
from .tray import TrayController
from .widgets import (
    AnimatedGifLabel,
    CategoryMotionEmblem,
    DysonOrbitalBeacon,
    DysonSunburstWidget,
    EquilibriumGauge,
    FanCurveCanvas,
    InteractiveFluidSolarCanvas,
    SmoothPulseBar,
    SolarBackdropCanvas,
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
        start_widget: bool = False,
        open_setup_wizard: bool = False,
    ) -> None:
        super().__init__(className="AstraCradle")

        self.cfg_mgr = ConfigManager(base_dir=base_dir)
        self.config_obj: AppConfig = self.cfg_mgr.load()
        self.journal = ForensicJournal(self.cfg_mgr)
        self.rollback_mgr = RollbackManager(self.cfg_mgr)
        self.executor = GuardedExecutor(self.cfg_mgr, self.rollback_mgr, self.journal)
        self.elevation = ElevationRunner(password_prompt_cb=self._prompt_sudo_password)
        self.self_heal = SelfHealEngine(self.cfg_mgr, self.rollback_mgr, self.journal)
        from ..core.command_db import CommandDatabaseManager
        from ..core.dependency_library import DependencyLibraryManager
        from ..core.mobile_continuity import DeviceRegistryManager, MobileCompanionEngine
        from ..core.network_failover import NetworkFailoverEngine
        from ..core.process_lookup import ProcessIntelligenceLookup
        from ..core.single_instance import SingleInstanceManager
        from ..core.wifi_mesh import WifiMeshAssistantEngine

        self.single_instance = SingleInstanceManager(self.cfg_mgr)
        if show_startup_popups:
            self.single_instance.terminate_existing_instances(timeout_sec=3.0)
            self.single_instance.acquire()
            self._install_signal_handlers()

        self.command_db = CommandDatabaseManager(self.cfg_mgr, self.journal)
        self.dep_lib = DependencyLibraryManager(self.journal, elevation=self.elevation)
        self.device_registry = DeviceRegistryManager(self.cfg_mgr, self.journal)
        self.mobile_companion = MobileCompanionEngine(self.cfg_mgr, self.device_registry, self.journal)
        self.network_failover = NetworkFailoverEngine(self.cfg_mgr, self.journal, elevation=self.elevation)
        self.wifi_mesh = WifiMeshAssistantEngine(self.cfg_mgr, self.journal, elevation=self.elevation)
        self.proc_lookup = ProcessIntelligenceLookup(self.cfg_mgr)
        self._proc_lookup_cache_ui: dict[str, str] = {}
        self._proc_lookup_in_flight: set[str] = set()
        self._last_proximity_outcomes = []
        self.crash_analyzer = CrashAnalyzer(self.cfg_mgr, self.journal)
        self.scripts_engine = ScriptsEngine(
            self.cfg_mgr, self.executor, self.journal, elevation=self.elevation
        )
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
        self.ai_exporter = AIDiagnosticArchiveExporter(self.cfg_mgr, self.journal)
        self.digest_gen = WeeklyDigestGenerator(self.cfg_mgr, self.journal)
        self.hw_mgr = HardwareLibraryManager(self.cfg_mgr, self.journal)
        self.pipe_mgr = PipelineEngine(self.cfg_mgr, self.executor, self.scripts_engine, self.hw_mgr, self.journal)
        self.history_db = TelemetryHistoryDB(self.cfg_mgr)
        self.net_fw = NetworkFirewallController(self.cfg_mgr, self.journal, elevation=self.elevation)
        self.gpu_pwr = GpuPowerAndFanController(self.cfg_mgr, self.journal, elevation=self.elevation)
        self.latency_guard = InterruptAndDpcGuard(self.cfg_mgr, self.journal, elevation=self.elevation)
        self.probalance = ProBalanceEngine(self.cfg_mgr, self.journal)
        self.thermal_spc = ThermalSpcMonitor(self.cfg_mgr, self.journal)
        self.autoruns_auditor = AutorunsAndGhostAuditor(
            self.cfg_mgr, self.rollback_mgr, self.journal, elevation=self.elevation
        )
        self.full_discovery = FullSystemDiscoveryEngine(self.cfg_mgr, self.rollback_mgr, self.journal)
        self.adblock_shield = AstraShieldManager(
            self.cfg_mgr, self.rollback_mgr, self.journal, elevation=self.elevation
        )
        self.conflict_detector = ConflictProgramDetector(self.journal, elevation=self.elevation)
        self.usb_guard = UsbAndPeripheralGuard(self.cfg_mgr, self.journal, elevation=self.elevation)
        self.lan_tv_shield = LanTvAdShield(self.journal)
        self.wifi_optimizer = WifiChannelOptimizer(self.journal)
        self.rf_scanner = BandwidthAndRfScanner(self.journal)
        self.catalog_scanner = SystemCatalogAndHealer(self.cfg_mgr, self.rollback_mgr, self.journal)
        self._latest_lan_devices: list = []
        self._latest_bt_devices: list = []
        self._latest_wifi_res = None
        self._latest_wifi_rec: dict | None = None
        self._latest_rf_snap = None
        self._latest_catalog_summary = None
        self._last_wifi_check_ts: float = 0.0
        self._last_passive_scan_ts: float = 0.0
        self.baseline_diff = BaselineDiffEngine(self.cfg_mgr, self.journal)
        self.startup_file_delay = StartupFileDelayManager(self.cfg_mgr, self.rollback_mgr, self.journal)
        self.net_dns_inspector = NetworkAndDnsInspector(self.journal)
        self.ram_standby_mgr = MemoryStandbyManager(self.journal)
        self.battery_inspector = BatteryAndPowerInspector()
        self.safe_fixer = SafeBeginnerFixer(self.cfg_mgr, self.executor, self.rollback_mgr, self.journal)
        from ..core.hermes_agent import HermesAgentManager

        self._hermes_elevation = ElevationRunner(password_prompt_cb=self._prompt_hermes_sudo_from_worker)
        self.hermes_mgr = HermesAgentManager(
            self.cfg_mgr,
            self.journal,
            executor=self.executor,
            diagnostics=self.diagnostics,
            telemetry=self.telemetry,
            safe_fixer=self.safe_fixer,
            wifi_optimizer=self.wifi_optimizer,
            proc_lookup=self.proc_lookup,
            elevation=self._hermes_elevation,
            rollback_mgr=self.rollback_mgr,
            self_heal=self.self_heal,
            probalance=self.probalance,
            autoruns_auditor=self.autoruns_auditor,
            device_registry=self.device_registry,
            mobile_companion=self.mobile_companion,
            playbook_builder=self.playbook_builder,
            ram_standby_mgr=self.ram_standby_mgr,
            net_dns_inspector=self.net_dns_inspector,
        )
        self.i18n = Translator(self.config_obj.language)
        self._last_duplicate_groups: list[DuplicateGroup] = []
        self._last_dup_scan_dir: str = str(Path.home() / "Downloads")
        self._last_dead_items: list = []
        self._last_outdated_lines: list[str] = []
        self._last_latency_report = None
        self._last_autoruns: list = []
        self._last_ghosts: list = []
        self._latest_sensors: dict = {}
        self._latest_conflicts: list = []
        self._latest_peripherals: list = []
        self._last_baseline: FullSystemBaseline | None = self.full_discovery.load_saved_baseline()
        self._draft_fan_curve: list[list[int]] = self.gpu_pwr.get_applied_curve()[1]
        self._toast_after_id: str | None = None
        self._last_history_write: float = 0.0
        self._last_focus_state = None
        self._last_layout_w: int = 0
        self._last_win_x: int | None = None
        self._last_win_y: int | None = None
        self._hover_icon_frames: list[ImageTk.PhotoImage] = []
        self._hover_icon_idx: int = 0
        self._hover_icon_after_id: str | None = None
        self._hover_icon_boost: bool = False
        self._mini_orb = None
        self._desktop_widget = None
        self._resize_after_id: str | None = None
        self._show_startup_popups = show_startup_popups

        if assets_dir:
            self.assets_dir = Path(assets_dir)
        else:
            pkg_assets = Path(__file__).resolve().parents[1] / "assets"
            repo_assets = Path(__file__).resolve().parents[2] / "assets"
            self.assets_dir = (
                pkg_assets
                if pkg_assets.exists()
                else (repo_assets if repo_assets.exists() else Path.cwd() / "assets")
            )

        self.palette: ThemePalette = get_palette(self.config_obj.theme)
        # CustomTkinter's set_widget_scaling() already scales fonts & widget sizes; keep font scale=1.0 to avoid double-scaling
        self.fonts = get_ui_fonts(1.0)
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
            on_pause_shield_5m=lambda: self._ui_queue.put(("pause_shield_5m", None, None)),
            on_toggle_widget=lambda: self._ui_queue.put(("toggle_widget", None, None)),
            on_context_menu=lambda rx, ry: self._ui_queue.put(("show_tray_menu", (rx, ry), None)),
        )
        self._astrabeam_server: AstraBeamServer | None = None
        self._global_hotkeys = GlobalHotkeyManager()
        if show_startup_popups and getattr(self.config_obj, "global_hotkeys_enabled", True):
            self._global_hotkeys.start(
                on_shield=lambda: self._ui_queue.put(("toggle_astrashield", None, None)),
                on_fix_safe=lambda: self._ui_queue.put(("fix_safe", None, None)),
                on_widget=lambda: self._ui_queue.put(("toggle_widget", None, None)),
                on_beam=lambda: self._ui_queue.put(("open_beam", None, None)),
            )
        if show_startup_popups and (self.config_obj.show_tray_icon or self.config_obj.minimize_to_tray or start_minimized):
            tray_ok = self.tray.start()
            if not tray_ok and self.tray.last_error:
                self.journal.log_event(
                    "TrayController",
                    "tray_start_warn",
                    f"System tray unavailable ({self.tray.last_error})",
                    "On Linux KDE/Wayland, install libayatana-appindicator & python-gobject if tray icon is hidden",
                    "warning",
                )
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
        self.minsize(10, 10)
        self.maxsize(max(3840, scr_w), max(2160, scr_h))
        self.configure(fg_color=self.palette.bg_canvas)
        self._set_window_icon()
        self.after(80, self._set_window_icon)
        if show_startup_popups and not (start_minimized or self.config_obj.start_minimized):
            self.after(450, lambda: self._start_hover_icon_anim(boost=False))

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
            self.latest_self_heal, self.latest_audit = self.self_heal.run_full_self_heal(
                adapter,
                self.config_obj,
                command_db=self.command_db,
                device_registry=self.device_registry,
                companion=self.mobile_companion,
            )
            self.config_obj = self.cfg_mgr.load()

        # Check seamless updates at startup
        if self.config_obj.auto_update_mode == "silent_auto":
            upd = self.updater.check_for_updates(self.config_obj)
            if upd.update_available:
                self.updater.stage_and_apply_local_update(self.config_obj)

        self._load_brand_images()
        self._active_tasks: dict[str, str] = {}
        self._toast_seq: int = 0
        self._build_layout()
        self._bind_shortcuts()
        self._bind_global_mousewheel()
        self._install_button_progress_hook()
        self.bind("<Configure>", self._on_root_configure)
        self.bind("<Unmap>", self._on_window_unmap)
        self._drain_ui_queue()

        # Respect explicit permission before first full-computer diagnostic pull on interactive first launch
        if show_startup_popups and not self.config_obj.setup_wizard_completed:
            self.dash_headline.configure(text="Setup Wizard Ready — Choose Your Modules")
            self.dash_summary.configure(
                text=(
                    "Welcome to AstraCradle! Pick which modular features you want active, "
                    "configure local scan permissions, and optionally install menu/desktop shortcuts."
                )
            )
            self.topbar_status.configure(text="Awaiting Setup Wizard")
        elif show_startup_popups and not self.config_obj.consent_prompted:
            self.dash_headline.configure(text="Permission Required for First-Load Local PC Scan")
            self.dash_summary.configure(
                text=(
                    "AstraCradle only collects full computer diagnostics locally with your explicit consent. "
                    "Click 'Grant & Run Full PC Scan' in the permission prompt (or 'Permissions' above) to inspect "
                    "hardware, temperatures, SMART disks, installed apps, autoruns, USB peripherals, DPC latency, and system health."
                )
            )
            self.topbar_status.configure(text="Awaiting scan permission")
        else:
            if show_startup_popups and self.config_obj.consent_telemetry and not self.config_obj.first_run_scan_completed:
                self.after(200, lambda: self.run_full_pc_discovery_async(show_dialog=False))
            else:
                self.refresh_telemetry_async(include_dns=True, also_scan=True)
            self._schedule_telemetry_poll()

        self.protocol("WM_DELETE_WINDOW", self.on_close)

        if show_startup_popups:
            if (
                not start_minimized
                and getattr(self.config_obj, "boot_star_gather_splash", True)
                and getattr(self.config_obj, "animations_enabled", True)
                and not self.config_obj.low_power_ui_mode
            ):
                self.after(20, self._launch_boot_star_gather_splash)
            if self.startup_crash_report is not None:
                self.after(350, lambda: CrashRecoveryDialog(self, self.palette, self.fonts, self.startup_crash_report))
                if open_setup_wizard or not self.config_obj.setup_wizard_completed:
                    self.after(550, lambda: self.open_setup_wizard(first_run=not self.config_obj.setup_wizard_completed))
            elif open_setup_wizard or not self.config_obj.setup_wizard_completed:
                self.after(300, lambda: self.open_setup_wizard(first_run=not self.config_obj.setup_wizard_completed))
            elif not self.config_obj.consent_prompted:
                self.after(300, lambda: self.open_consent_wizard(first_run=True))
            if self.config_obj.is_module_enabled("privacy_shield") and self.config_obj.astrashield_auto_start and (
                self.config_obj.astrashield_enabled
                or self.config_obj.astrashield_dns_enabled
                or self.config_obj.astrashield_lan_tv_mode
            ):
                self.after(450, self._auto_start_astrashield_async)
            elif self.config_obj.is_module_enabled("privacy_shield"):
                fs_msg = self.lan_tv_shield.check_and_run_startup_failsafe(self.adblock_shield)
                if fs_msg:
                    self.after(600, lambda m=fs_msg: self.show_toast(m))
            if self.config_obj.is_module_enabled("hermes_ai"):
                self.after(700, self._auto_integrate_hermes_gpu_async)
            if self.config_obj.is_module_enabled("wifi_failover_mesh") and getattr(self.config_obj, "wifi_auto_optimize", True):
                self.after(900, self._auto_wifi_optimize_async)
            if start_widget or getattr(self.config_obj, "desktop_widget_enabled", False):
                self.after(140, self.show_desktop_widget)
            self.after(700, self._reapply_saved_scripts_on_boot)
            boot_rb_msg = self.crash_analyzer.check_boot_crash_auto_rollback(
                self.rollback_mgr,
                enabled=bool(getattr(self.config_obj, "crash_auto_rollback_on_boot", True)),
            )
            if boot_rb_msg:
                self.after(420, lambda m=boot_rb_msg: self.show_toast(m, color=self.palette.accent_coral))
        elif start_widget:
            self.after(80, self.show_desktop_widget)

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
            try:
                dpi_scale = max(1.0, float(self.winfo_fpixels("1i") or 96.0) / 96.0)
            except Exception:
                dpi_scale = 1.0
            target = 1.18 if sw >= 3400 else (1.08 if sw >= 2400 else 1.0)
            # Avoid compounding on top of desktop environment DPI scaling (e.g. KDE 125%-150%)
            return max(0.80, min(1.30, target / dpi_scale))
        try:
            return max(0.80, min(1.75, float(raw.replace("%", "").strip()) / 100.0))
        except Exception:
            return 1.0

    def _launch_boot_star_gather_splash(self) -> None:
        try:
            DysonStarGatherSplash(self, self.palette, self.fonts, duration_ms=820)
        except Exception:
            pass

    def _on_root_configure(self, event) -> None:
        if event.widget is not self:
            return
        # Track window position deltas for Black & White 2 fluid inertia + solar parallax
        try:
            ex = int(getattr(event, "x", 0) or 0)
            ey = int(getattr(event, "y", 0) or 0)
            if self._last_win_x is not None and self._last_win_y is not None:
                dx = ex - self._last_win_x
                dy = ey - self._last_win_y
                if abs(dx) > 1 or abs(dy) > 1:
                    if hasattr(self, "hero_motion_canvas") and self.hero_motion_canvas.winfo_exists():
                        self.hero_motion_canvas.apply_window_inertia(dx, dy)
                    if hasattr(self, "solar_backdrop") and self.solar_backdrop.winfo_exists():
                        self.solar_backdrop.apply_parallax(dx, dy)
            self._last_win_x = ex
            self._last_win_y = ey
        except Exception:
            pass

        w = max(10, int(event.width or 1300))
        if abs(w - self._last_layout_w) < 18:
            return
        self._last_layout_w = w
        if self._resize_after_id is not None:
            try:
                self.after_cancel(self._resize_after_id)
            except Exception:
                pass
        self._resize_after_id = self.after(55, lambda ww=w: self._apply_responsive_layout(ww))

    def _apply_responsive_layout(self, win_w: int) -> None:
        """Dynamically scales sidebar, topbar buttons, and text wrapping from 10x10 up to 4K (3840x2160)."""
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        win_w = max(10, int(win_w))
        if win_w < 340:
            # Ultra-compact mode (down to 10x10) hides left sidebar so content never clips
            if hasattr(self, "sidebar") and self.sidebar.winfo_exists():
                try:
                    self.sidebar.grid_remove()
                except Exception:
                    pass
            self._current_sb_w = 0
            return
        if hasattr(self, "sidebar") and self.sidebar.winfo_exists():
            try:
                self.sidebar.grid(row=0, column=0, sticky="nsew")
            except Exception:
                pass
        ratio = max(0.68, min(1.85, win_w / 1300.0))
        sb_w = int(max(136, min(360, round(248 * ratio))))
        self._current_sb_w = sb_w
        if hasattr(self, "sidebar") and self.sidebar.winfo_exists():
            self.sidebar.configure(width=sb_w)
        btn_h = int(max(26, min(46, round(32 * ratio))))
        for btn in getattr(self, "nav_buttons", {}).values():
            try:
                btn.configure(height=btn_h)
            except Exception:
                pass
        top_h = int(max(44, min(82, round(60 * ratio))))
        if hasattr(self, "topbar") and self.topbar.winfo_exists():
            self.topbar.configure(height=top_h)
        cta_h = int(max(24, min(44, round(31 * ratio))))
        for bname, base_w in (("scan_cta", 104), ("help_btn", 98), ("guide_btn", 88), ("perm_btn", 92), ("widget_btn", 76)):
            btn = getattr(self, bname, None)
            if btn is not None:
                try:
                    btn.configure(height=cta_h, width=int(max(52, min(190, round(base_w * ratio)))))
                except Exception:
                    pass
        wrap_w = max(120, win_w - sb_w - 170)
        for lbl_name in ("dash_summary",):
            lbl = getattr(self, lbl_name, None)
            if lbl is not None:
                try:
                    lbl.configure(wraplength=max(80, win_w - sb_w - 490))
                except Exception:
                    pass
        if hasattr(self, "_cmd_btns") and hasattr(self, "dash_btn_row") and self.dash_btn_row.winfo_exists():
            try:
                cols = 6 if win_w >= 1750 else (2 if win_w < 720 else 3)
                for c in range(6):
                    self.dash_btn_row.grid_columnconfigure(c, weight=(1 if c < cols else 0))
                for idx, btn in enumerate(self._cmd_btns):
                    r, c = (0, idx) if cols == 6 else divmod(idx, cols)
                    btn.grid(row=r, column=c, sticky="ew", padx=3, pady=3)
            except Exception:
                pass
        if hasattr(self, "page_container") and self.page_container.winfo_exists():
            self._sync_responsive_wraps(self.page_container, wrap_w)

    def _content_wrap(self, reserve: int = 120) -> int:
        try:
            win_w = int(self.winfo_width() or 1300)
            if win_w < 10:
                win_w = 1300
        except Exception:
            win_w = 1300
        sb_w = int(getattr(self, "_current_sb_w", 252))
        return max(80, win_w - sb_w - reserve)

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
        self.bind("<Control-k>", lambda _e: self.open_ai_assistant_drawer())
        self.bind("<Control-Alt-a>", lambda _e: self.toggle_desktop_widget())
        self.bind("<Control-Alt-s>", lambda _e: self.on_fix_everything_safe())
        self.bind("<Control-Alt-w>", lambda _e: self._on_quick_wifi_optimize_from_widget())
        self.bind("<Control-Shift-B>", lambda _e: self.switch_nav("remote_beam"))
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

    def _set_animations_paused(self, paused: bool) -> None:
        for attr in ("gauge", "category_emblem", "hero_motion_canvas", "rf_wave_chart", "solar_backdrop", "sidebar_gif", "about_gif"):
            w = getattr(self, attr, None)
            if w is not None and hasattr(w, "set_paused"):
                try:
                    if w.winfo_exists():
                        w.set_paused(paused)
                except Exception:
                    pass
        if paused:
            self._stop_hover_icon_anim()
        elif self._show_startup_popups:
            self._start_hover_icon_anim(boost=False)

    def _show_mini_orb(self) -> None:
        if not getattr(self.config_obj, "floating_orb_on_minimize", False):
            return
        if not self._show_startup_popups:
            return
        if self._mini_orb is not None:
            try:
                if self._mini_orb.winfo_exists():
                    return
            except Exception:
                self._mini_orb = None
        try:
            self._mini_orb = FloatingMiniOrbWindow(
                self,
                self.palette,
                on_restore=lambda: self._ui_queue.put(("show_window", None, None)),
                gif_path=self.assets_dir / "astracradle_hover.gif",
                on_context_menu=lambda rx, ry: self._ui_queue.put(("show_tray_menu", (rx, ry), None)),
            )
            if self.latest_report:
                self._mini_orb.set_score(self.latest_report.health_score)
        except Exception:
            self._mini_orb = None

    def _hide_mini_orb(self) -> None:
        if self._mini_orb is not None:
            try:
                if self._mini_orb.winfo_exists():
                    self._mini_orb.destroy()
            except Exception:
                pass
            self._mini_orb = None

    def show_desktop_widget(self) -> None:
        if self._desktop_widget is not None:
            try:
                if self._desktop_widget.winfo_exists():
                    self._desktop_widget.lift()
                    return
            except Exception:
                self._desktop_widget = None
        try:
            self.config_obj.desktop_widget_enabled = True
            self.cfg_mgr.save(self.config_obj)
            self._desktop_widget = DesktopTelemetryWidget(
                self,
                self.palette,
                self.fonts,
                gif_path=self.assets_dir / "astracradle_hover.gif",
                x=int(getattr(self.config_obj, "desktop_widget_x", 28)),
                y=int(getattr(self.config_obj, "desktop_widget_y", 64)),
                expanded=bool(getattr(self.config_obj, "desktop_widget_expanded", True)),
                always_on_top=bool(getattr(self.config_obj, "desktop_widget_always_on_top", True)),
                mode=str(getattr(self.config_obj, "desktop_widget_mode", "Balanced")),
                on_open_studio=lambda: self._ui_queue.put(("show_window", None, None)),
                on_fix_safe=lambda: self._ui_queue.put(("fix_safe", None, None)),
                on_toggle_shield=lambda: self._ui_queue.put(("toggle_astrashield", None, None)),
                on_optimize_wifi=self._on_quick_wifi_optimize_from_widget,
                on_save_state=self._save_desktop_widget_state,
                on_close_widget=lambda: self.hide_desktop_widget(persist_disabled=True),
            )
            if self.latest_snapshot is not None:
                self._push_telemetry_to_widget(self.latest_snapshot)
        except Exception:
            self._desktop_widget = None

    def hide_desktop_widget(self, persist_disabled: bool = False) -> None:
        if persist_disabled:
            self.config_obj.desktop_widget_enabled = False
            self.cfg_mgr.save(self.config_obj)
            if hasattr(self, "set_desktop_widget_var"):
                try:
                    self.set_desktop_widget_var.set(False)
                except Exception:
                    pass
        if self._desktop_widget is not None:
            try:
                if self._desktop_widget.winfo_exists():
                    self._desktop_widget.destroy()
            except Exception:
                pass
            self._desktop_widget = None

    def toggle_desktop_widget(self) -> None:
        if self._desktop_widget is not None:
            try:
                if self._desktop_widget.winfo_exists():
                    self.hide_desktop_widget(persist_disabled=True)
                    self.show_toast("Desktop Widget hidden.")
                    return
            except Exception:
                pass
        self.show_desktop_widget()
        if hasattr(self, "set_desktop_widget_var"):
            try:
                self.set_desktop_widget_var.set(True)
            except Exception:
                pass
        self.show_toast("Desktop Widget active across all workspaces.")

    def _save_desktop_widget_state(self, state: dict[str, object]) -> None:
        for k in (
            "desktop_widget_enabled",
            "desktop_widget_always_on_top",
            "desktop_widget_expanded",
            "desktop_widget_mode",
            "desktop_widget_x",
            "desktop_widget_y",
        ):
            if k in state:
                setattr(self.config_obj, k, state[k])
        self.cfg_mgr.save(self.config_obj)
        if hasattr(self, "set_desktop_widget_var"):
            try:
                self.set_desktop_widget_var.set(bool(self.config_obj.desktop_widget_enabled))
            except Exception:
                pass
        if hasattr(self, "set_widget_top_var"):
            try:
                self.set_widget_top_var.set(bool(self.config_obj.desktop_widget_always_on_top))
            except Exception:
                pass

    def _on_quick_wifi_optimize_from_widget(self) -> None:
        self.switch_nav("wifi")
        self.begin_task_progress("wifi", "Optimizing Wi-Fi channels...")
        self._auto_wifi_optimize_async()

    def _reapply_saved_scripts_on_boot(self) -> None:
        adapter = self._active_adapter()
        enabled_scripts = [sc for sc in self.scripts_engine.load_scripts() if sc.enabled]
        if not enabled_scripts:
            return

        def _worker() -> None:
            for sc in enabled_scripts:
                try:
                    self.scripts_engine.toggle_script(
                        sc.script_id,
                        True,
                        adapter,
                        self.config_obj,
                        [],
                        dry_run=bool(self.config_obj.dry_run_default),
                    )
                except Exception:
                    continue

        threading.Thread(target=_worker, daemon=True).start()

    def _push_telemetry_to_widget(self, snap: SystemSnapshot) -> None:
        if self._desktop_widget is None:
            return
        try:
            if not self._desktop_widget.winfo_exists():
                return
            disk_pct = snap.partitions[0].percent if snap.partitions else 0.0
            rx_mbps = 0.0
            tx_mbps = 0.0
            if hasattr(self, "rf_scanner") and getattr(self, "_last_rf_snapshot", None):
                rx_mbps = float(self._last_rf_snapshot.rx_mbps)
                tx_mbps = float(self._last_rf_snapshot.tx_mbps)
            score = self.latest_report.health_score if self.latest_report else 96
            shield_st = self.adblock_shield.status()
            shield_on = bool(self.adblock_shield.dns_running or self.config_obj.astrashield_enabled)
            gpu_t = self._num_or_none((self._latest_sensors or {}).get("gpu_temp_c"))
            self._desktop_widget.update_telemetry(
                cpu_pct=snap.cpu_percent,
                ram_pct=snap.mem_percent,
                disk_pct=disk_pct,
                rx_mbps=rx_mbps,
                tx_mbps=tx_mbps,
                health_score=score,
                shield_on=shield_on,
                wifi_label=("Wi-Fi Auto" if self.config_obj.wifi_auto_optimize else ""),
                gpu_temp_c=gpu_t,
                shield_blocked=shield_st.blocked_queries,
            )
        except Exception:
            pass

    def _on_window_unmap(self, event) -> None:
        if event.widget is not self:
            return
        if time.monotonic() < float(getattr(self, "_restoring_until", 0.0)):
            return
        try:
            if self.state() == "iconic":
                self._set_animations_paused(True)
            if self.config_obj.minimize_to_tray and self.state() == "iconic":
                if not self.tray.running:
                    self.tray.start()
                if self.tray.running:
                    self.withdraw()
                    self._show_mini_orb()
                    self.tray.notify("AstraCradle", "Minimized to the bottom-right system tray.")
        except Exception:
            pass

    def _restore_main_window(self) -> None:
        self._restoring_until = time.monotonic() + 0.9
        self._hide_mini_orb()
        try:
            self.deiconify()
        except Exception:
            pass
        try:
            self.state("normal")
        except Exception:
            pass
        try:
            self.update_idletasks()
            self.lift()
            self.attributes("-topmost", True)
            self.after(90, lambda: self._clear_topmost_safe())
        except Exception:
            pass
        try:
            self.focus_force()
        except Exception:
            pass
        self._set_animations_paused(False)

    def _clear_topmost_safe(self) -> None:
        try:
            if self.winfo_exists():
                self.attributes("-topmost", False)
        except Exception:
            pass

    def _show_tray_context_menu(self, rx: int = 0, ry: int = 0) -> None:
        """Displays a sleek native dark context menu on right-click of the tray icon or floating orb."""
        p = self.palette
        try:
            old = getattr(self, "_tray_ctx_menu", None)
            if old is not None:
                try:
                    old.destroy()
                except Exception:
                    pass
            menu = tk.Menu(
                self,
                tearoff=0,
                bg=p.bg_card,
                fg=p.text_primary,
                activebackground=p.accent_cyan,
                activeforeground="#0B1018",
                font=self.fonts["small"],
                relief="flat",
                borderwidth=1,
            )
            menu.add_command(
                label="Open AstraCradle",
                command=lambda: self._ui_queue.put(("show_window", None, None)),
            )
            menu.add_command(
                label="Run Equilibrium Scan",
                command=lambda: self._ui_queue.put(("trigger_scan", None, None)),
            )
            menu.add_command(
                label="1-Click Fix Everything Safe",
                command=lambda: self._ui_queue.put(("fix_safe", None, None)),
            )
            prof_menu = tk.Menu(
                menu,
                tearoff=0,
                bg=p.bg_card,
                fg=p.text_primary,
                activebackground=p.accent_cyan,
                activeforeground="#0B1018",
            )
            for prof_name in ("Balanced Daily", "Gaming / Low-Latency", "Battery Endurance"):
                prof_menu.add_command(
                    label=prof_name,
                    command=lambda pn=prof_name: self._ui_queue.put(("switch_profile", pn, None)),
                )
            menu.add_cascade(label="Switch Profile", menu=prof_menu)
            menu.add_separator()
            menu.add_command(
                label="Toggle ProBalance",
                command=lambda: self._ui_queue.put(("toggle_probalance", None, None)),
            )
            menu.add_command(
                label="Toggle AstraShield",
                command=lambda: self._ui_queue.put(("toggle_astrashield", None, None)),
            )
            menu.add_command(
                label="Toggle Desktop Widget",
                command=lambda: self._ui_queue.put(("toggle_widget", None, None)),
            )
            menu.add_separator()
            menu.add_command(
                label="Quit AstraCradle",
                command=lambda: self.on_close(force_quit=True),
            )
            self._tray_ctx_menu = menu
            px = int(rx) if int(rx) > 0 else int(self.winfo_pointerx())
            py = int(ry) if int(ry) > 0 else int(self.winfo_pointery())
            self.update_idletasks()
            menu.tk_popup(px, py)
        except Exception:
            pass
        finally:
            try:
                if getattr(self, "_tray_ctx_menu", None) is not None:
                    self._tray_ctx_menu.grab_release()
            except Exception:
                pass

    def _install_signal_handlers(self) -> None:
        if threading.current_thread() is not threading.main_thread():
            return
        import signal

        def _handle_exit(_signum, _frame) -> None:
            try:
                self._ui_queue.put(("quit_app", None, None))
            except Exception:
                pass

        for sig_name in ("SIGTERM", "SIGINT"):
            sig = getattr(signal, sig_name, None)
            if sig is not None:
                try:
                    signal.signal(sig, _handle_exit)
                except Exception:
                    pass

    def on_close(self, force_quit: bool = False) -> None:
        if not force_quit and self.config_obj.minimize_to_tray:
            if not self.tray.running:
                self.tray.start()
            if self.tray.running:
                self._set_animations_paused(True)
                self.withdraw()
                self._show_mini_orb()
                self.tray.notify("AstraCradle", "Still running in the bottom-right tray. Right-click icon to Quit.")
                return
        try:
            self._hide_mini_orb()
            self.hide_desktop_widget(persist_disabled=False)
            if getattr(self.config_obj, "astrashield_tv_auto_failsafe", True):
                tv_st = self.lan_tv_shield.load_failsafe_state()
                if tv_st and tv_st.get("active"):
                    self.lan_tv_shield.restore_tv_settings(reason="app_shutdown", shield=self.adblock_shield)
            self.adblock_shield.stop_dns_sinkhole()
            self.network_failover.stop_monitor()
            if hasattr(self, "hermes_mgr"):
                self.hermes_mgr.stop_gateway()
            self.tray.stop()
            self.journal.log_event(
                component="AstraCradleApp",
                action="shutdown",
                how="Clean window close protocol executed",
                why="Mark clean shutdown sentinel for CrashAnalyzer",
                outcome="ok",
            )
            self.crash_analyzer.mark_clean_shutdown()
            if hasattr(self, "single_instance"):
                self.single_instance.release()
        finally:
            self.destroy()

    def destroy(self) -> None:
        self._hide_mini_orb()
        self.hide_desktop_widget(persist_disabled=False)
        for attr in ("_poll_job", "_drain_job", "_resize_after_id", "_toast_after_id", "_hover_icon_after_id"):
            job = getattr(self, attr, None)
            if job is not None:
                try:
                    self.after_cancel(job)
                except Exception:
                    pass
                setattr(self, attr, None)
        super().destroy()

    def _capture_window_state(self) -> dict[str, object]:
        is_fs = False
        is_zoomed = False
        geom = ""
        try:
            is_fs = bool(self.attributes("-fullscreen"))
        except Exception:
            pass
        try:
            is_zoomed = str(self.state()) == "zoomed"
        except Exception:
            pass
        if not is_zoomed:
            try:
                is_zoomed = bool(self.attributes("-zoomed"))
            except Exception:
                pass
        try:
            geom = str(self.geometry())
        except Exception:
            pass
        return {"fullscreen": is_fs, "zoomed": is_zoomed, "geometry": geom}

    def _restore_window_state(self, st: dict[str, object]) -> None:
        if not self.winfo_exists():
            return
        try:
            sw = max(1920, int(self.winfo_screenwidth() or 1920))
            sh = max(1080, int(self.winfo_screenheight() or 1080))
            self.minsize(10, 10)
            self.maxsize(sw * 2, sh * 2)
        except Exception:
            pass
        if st.get("fullscreen"):
            try:
                self.attributes("-fullscreen", True)
                return
            except Exception:
                pass
        if st.get("zoomed"):
            for fn in (
                lambda: self.state("zoomed"),
                lambda: self.attributes("-zoomed", True),
            ):
                try:
                    fn()
                    return
                except Exception:
                    pass
        geom = str(st.get("geometry") or "")
        if geom:
            try:
                self.geometry(geom)
            except Exception:
                pass

    def _apply_ui_scale(self, preserve_window: bool = False) -> None:
        st = self._capture_window_state() if preserve_window else None
        try:
            pct = self._scale_float()
            ctk.set_widget_scaling(max(0.85, min(1.75, pct)))
        except Exception:
            pass
        if st is not None:
            self._restore_window_state(st)
            try:
                self.after(50, lambda s=st: self._restore_window_state(s))
                self.after(1060, lambda s=st: self._restore_window_state(s))
            except Exception:
                pass

    def _active_adapter(self):
        return get_os_adapter(
            override=self.config_obj.os_adapter_override,
            custom_protected_paths=self.config_obj.custom_protected_paths,
        )

    def _set_window_icon(self) -> None:
        self._tk_icons: list[ImageTk.PhotoImage] = []
        if platform.system() == "Windows":
            ico_path = self.assets_dir / "astracradle.ico"
            if ico_path.exists():
                try:
                    self.iconbitmap(str(ico_path))
                except Exception:
                    pass
        for fname in (
            "astracradle_emblem_256.png",
            "astracradle_emblem_128.png",
            "astracradle_emblem_64.png",
            "astracradle_emblem_32.png",
        ):
            icon_path = self.assets_dir / fname
            if icon_path.exists():
                try:
                    img = Image.open(icon_path).convert("RGBA")
                    self._tk_icons.append(ImageTk.PhotoImage(img, master=self))
                except Exception:
                    pass
        if self._tk_icons:
            try:
                self.wm_iconphoto(True, *self._tk_icons)
                self._tk_icon = self._tk_icons[0]
            except Exception:
                pass
        # Preload animated hover taskbar icon frames
        self._hover_icon_frames = []
        hover_gif = self.assets_dir / "astracradle_hover.gif"
        if hover_gif.exists():
            try:
                from PIL import ImageSequence

                with Image.open(hover_gif) as im:
                    for fr in ImageSequence.Iterator(im):
                        self._hover_icon_frames.append(
                            ImageTk.PhotoImage(fr.convert("RGBA").resize((48, 48)), master=self)
                        )
            except Exception:
                self._hover_icon_frames = []

    def _start_hover_icon_anim(self, boost: bool = True) -> None:
        if (
            not getattr(self.config_obj, "hover_icon_animation", True)
            or not getattr(self.config_obj, "animations_enabled", True)
            or getattr(self.config_obj, "low_power_ui_mode", False)
        ):
            return
        self._hover_icon_boost = bool(boost)
        if self._hover_icon_after_id is not None or not self._hover_icon_frames:
            return
        self._step_hover_icon_anim()

    def _step_hover_icon_anim(self) -> None:
        self._hover_icon_after_id = None
        try:
            if not self.winfo_exists() or not self._hover_icon_frames:
                return
            n = len(self._hover_icon_frames)
            # Align to 35ms (hover boost) or 90ms (idle) monotonic phase so taskbar 3D rotation never stutters
            interval_ms = 35 if self._hover_icon_boost else 90
            now = time.monotonic()
            last_t = getattr(self, "_hover_icon_last_ts", now)
            self._hover_icon_last_ts = now
            dt_ms = max(16.0, min(180.0, (now - last_t) * 1000.0))
            step = max(1, int(round(dt_ms / float(interval_ms)))) if hasattr(self, "_hover_icon_started") else 1
            self._hover_icon_started = True
            self._hover_icon_idx = (self._hover_icon_idx + step) % n
            fr = self._hover_icon_frames[self._hover_icon_idx]
            self.wm_iconphoto(True, fr)
            self.tray.pulse_icon_frame(self._hover_icon_idx)
            self._hover_icon_after_id = self.after(interval_ms, self._step_hover_icon_anim)
        except Exception:
            self._hover_icon_after_id = None

    def _stop_hover_icon_anim(self) -> None:
        self._hover_icon_boost = False
        if self._hover_icon_after_id is not None:
            try:
                self.after_cancel(self._hover_icon_after_id)
            except Exception:
                pass
            self._hover_icon_after_id = None
        if getattr(self, "_tk_icons", None):
            try:
                self.wm_iconphoto(True, *self._tk_icons)
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
            ("dashboard", self.i18n.t("nav.dashboard", "Command Deck"), "Interactive snapshot of AstraCradle options, compact expandable diagnostics & thermals (Ctrl+1)."),
            ("monitoring", "Live Monitoring", "Dedicated Live Monitoring category: full charts, detailed sensors, USB/BadUSB audit & conflicts."),
            ("playbook", self.i18n.t("nav.playbook", "Diagnostics & Playbook"), "Actionable findings with Confidence %, Safe Auto-Fix & copyable DIY steps (Ctrl+2)."),
            ("hunter", "AI & Process Hunter", "Spot idle AI chatbots (Ollama, LM Studio) & dormant helpers holding RAM (Ctrl+3)."),
            ("scripts", self.i18n.t("nav.scripts", "Toggleable Scripts"), "One-click workload modes (Gaming, 3D Render, Compile) with OS hardware snapshots (Ctrl+4)."),
            ("hardware", self.i18n.t("nav.hardware", "Hardware & Pipelines"), "PC Component Library, Presets, Visual Pipeline Editor & Interactive Fan Curve (Ctrl+5)."),
            ("modules", self.i18n.t("nav.modules", "System Modules"), "Storage cleanup, Startup items, Privacy & AdBlocker Shield, YouTube Safe Ad-Prune & BadUSB Guard (Ctrl+6)."),
            ("wifi", "Wi-Fi, Failover & Mesh", "Wi-Fi channel optimizer, Ethernet <-> Wi-Fi ping failover & PC Wi-Fi Mesh Array Assistant."),
            ("rf_waves", "Bandwidth & RF Waves", "Special Category: Live network RX/TX wave oscilloscope & 2.4/5/6 GHz + Bluetooth radio-wave spectrum chart."),
            ("remote_beam", "Remote Stream & Devices", "AstraBeam TLS streaming, iOS/Android/Apple Watch proximity auto-connect, Bluetooth fallback & custom device registry."),
            ("hermes", "Hermes OS AI", "Native Hermes AI agent: auto-matched GPU local model (Ollama), free cloud APIs, persistent memory, self-learned SKILL.md & cron."),
            ("cmd_vault", "Command & Install Vault", "SQLite install commands & reusable script database with quick dropdown picker, copy & custom save."),
            ("scanner", "System Scanner & DB", "Special Category: Multi-drive file & program catalog, passive scanner, new-app watcher, Known Issues DB & quiet auto-repair."),
            ("probalance", self.i18n.t("nav.probalance", "ProBalance & Latency"), "ProBalance Foreground Boost, Auto-Reverted App Profiles & Interrupt/DPC Guard (Ctrl+9)."),
            ("autoruns", self.i18n.t("nav.autoruns", "Autoruns & Baselines"), "Autoruns Startup/Task/Service Audit, Ghost Driver Cleanup & Welford Thermal SPC (Ctrl+0)."),
            ("safeguards", self.i18n.t("nav.safeguards", "Forensic, Crash & Vault"), "7-invariant Self-Heal, hash-chained WHAT/HOW/WHY log, Crash Post-Mortem & Undo Vault (Ctrl+7)."),
            ("settings", self.i18n.t("nav.settings", "Settings & Updates"), "Theme, Simple Mode, GitHub Auto-Updater, Packaging & Uninstall (Ctrl+8)."),
        ]
        self._nav_order: list[str] = [k for k, _, _ in nav_items]
        self._full_nav_labels: dict[str, str] = {k: lbl for k, lbl, _ in nav_items}
        self._simple_nav_labels: dict[str, str] = {
            "dashboard": "Home Overview",
            "playbook": "Fix Problems",
            "hunter": "Speed Up Apps",
            "modules": "Clean & Protect",
            "wifi": "Wi-Fi & Internet",
            "remote_beam": "Phone & Remote",
            "hermes": "Hermes OS AI",
            "settings": "Settings & Updates",
        }
        self._advanced_nav_keys: tuple[str, ...] = (
            "monitoring",
            "rf_waves",
            "cmd_vault",
            "scanner",
            "scripts",
            "hardware",
            "probalance",
            "autoruns",
            "safeguards",
        )
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
        self._sidebar_spacer = spacer

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

        self.category_emblem = CategoryMotionEmblem(
            self.topbar,
            p,
            size=32,
            animate=bool(getattr(self.config_obj, "animations_enabled", True)) and not self.config_obj.low_power_ui_mode,
        )
        self.category_emblem.pack(side="left", padx=(0, 6))
        attach_tooltip(self.category_emblem, "Custom category motion emblem (smoothly adapts to active tab).", p)

        self.topbar_beacon = DysonOrbitalBeacon(self.topbar, p, size=22)
        self.topbar_beacon.pack(side="left", padx=(0, 4))
        attach_tooltip(self.topbar_beacon, "Live animated Dyson telemetry heartbeat.", p)

        self.topbar_pulse = SmoothPulseBar(self.topbar, p, height=3)
        self.topbar_pulse.place(relx=0.0, rely=1.0, relwidth=1.0, anchor="sw")

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

        self.ask_ai_btn = ctk.CTkButton(
            self.topbar,
            text="Ask AI Assistant",
            height=30,
            width=118,
            corner_radius=8,
            fg_color=p.accent_cyan,
            hover_color=p.accent_cyan_hover,
            text_color="#071216",
            font=self.fonts["body_bold"],
            command=self.open_ai_assistant_drawer,
        )
        self.ask_ai_btn.pack(side="right", padx=4)
        attach_tooltip(
            self.ask_ai_btn,
            "Open the AstraCradle AI Action Assistant anywhere (Ctrl+K) — ask questions or tell it to safely fix PC & remote tasks.",
            p,
        )

        self.widget_btn = ctk.CTkButton(
            self.topbar,
            text="Widget",
            height=30,
            width=74,
            corner_radius=8,
            fg_color=p.bg_elevated,
            hover_color=p.border_strong,
            text_color=p.accent_jade,
            font=self.fonts["small"],
            command=self.toggle_desktop_widget,
        )
        self.widget_btn.pack(side="right", padx=3)
        attach_tooltip(
            self.widget_btn,
            "Open or hide the floating cross-OS Desktop Widget (Linux / Windows / macOS).",
            p,
        )

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

        self.simple_mode_var = tk.BooleanVar(value=bool(self.config_obj.simple_mode))
        self.simple_mode_switch = ctk.CTkSwitch(
            self.topbar,
            text="Simple Mode",
            variable=self.simple_mode_var,
            font=self.fonts["small"],
            text_color=p.accent_jade,
            progress_color=p.accent_jade,
            command=self._on_toggle_simple_mode,
        )
        self.simple_mode_switch.pack(side="right", padx=6)
        attach_tooltip(
            self.simple_mode_switch,
            "Simple Mode hides advanced power-user studios & technical switches. Turn OFF anytime for all 10 tabs.",
            p,
        )

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

        self.solar_backdrop = SolarBackdropCanvas(
            self.bg_layer,
            p,
            enabled=bool(getattr(self.config_obj, "solar_backdrop_enabled", True))
            and bool(getattr(self.config_obj, "animations_enabled", True))
            and not self.config_obj.low_power_ui_mode
            and getattr(self.config_obj, "motion_style", "Hybrid Solar + Fluid") != "Reduced Motion",
        )
        self.solar_backdrop.place(x=0, y=0, relwidth=1, relheight=1)
        self.topbar.bind("<Enter>", lambda _e: self._start_hover_icon_anim(boost=True), add="+")
        self.topbar.bind("<Leave>", lambda _e: setattr(self, "_hover_icon_boost", False), add="+")
        self.bind("<Enter>", lambda _e: self._start_hover_icon_anim(boost=True), add="+")
        self.bind("<Leave>", lambda _e: setattr(self, "_hover_icon_boost", False), add="+")
        if hasattr(self, "sidebar_gif"):
            self.sidebar_gif.bind("<Enter>", lambda _e: self._start_hover_icon_anim(boost=True), add="+")
            self.sidebar_gif.bind("<Leave>", lambda _e: setattr(self, "_hover_icon_boost", False), add="+")

        self.page_container = ctk.CTkFrame(self.bg_layer, fg_color="transparent")
        self.page_container.grid(row=0, column=0, sticky="nsew", padx=16, pady=12)
        self.page_container.grid_rowconfigure(0, weight=1)
        self.page_container.grid_columnconfigure(0, weight=1)

        self.pages: dict[str, ctk.CTkFrame] = {}
        for key in (
            "dashboard",
            "monitoring",
            "playbook",
            "hunter",
            "scripts",
            "hardware",
            "modules",
            "wifi",
            "rf_waves",
            "remote_beam",
            "hermes",
            "cmd_vault",
            "scanner",
            "probalance",
            "autoruns",
            "safeguards",
            "settings",
        ):
            frame = ctk.CTkFrame(self.page_container, fg_color="transparent")
            frame.grid(row=0, column=0, sticky="nsew")
            self.pages[key] = frame

        self._build_dashboard_page()
        self._build_monitoring_page()
        self._build_playbook_page()
        self._build_hunter_page()
        self._build_scripts_page()
        self._build_hardware_page()
        self._build_modules_page()
        self._build_wifi_page()
        self._build_rf_waves_page()
        self._build_remote_beam_page()
        self._build_hermes_page()
        self._build_cmd_vault_page()
        self._build_scanner_page()
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
        self._apply_simple_mode(initial=True)
        self.switch_nav("dashboard")
        self._refresh_github_version_badge_async()

    def show_toast(self, msg: str, color: str | None = None, undo_entry_id: str | None = None) -> None:
        self._toast_seq = getattr(self, "_toast_seq", 0) + 1
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
        self.toast_frame.place(relx=0.985, rely=0.985, anchor="se")
        self._animate_toast_in(0)
        if self._toast_after_id is not None:
            try:
                self.after_cancel(self._toast_after_id)
            except Exception:
                pass
        self._toast_after_id = self.after(5500, lambda: self.toast_frame.place_forget())

    def begin_task_progress(self, task_id: str, in_progress_msg: str, toast: bool = True) -> None:
        """Shows immediate in-progress feedback in topbar + toast whenever a button/background task starts."""
        if not hasattr(self, "_active_tasks"):
            self._active_tasks = {}
        self._active_tasks[str(task_id)] = str(in_progress_msg)
        p = self.palette
        try:
            if hasattr(self, "topbar_beacon"):
                self.topbar_beacon.set_scanning(True)
            if hasattr(self, "topbar_pulse"):
                self.topbar_pulse.set_active(True)
            if hasattr(self, "topbar_status"):
                self.topbar_status.configure(text=f"⏳ {in_progress_msg}", text_color=p.accent_brass)
        except Exception:
            pass
        if toast:
            self.show_toast(f"In progress: {in_progress_msg}", color=p.accent_brass)
        try:
            self.update_idletasks()
        except Exception:
            pass

    def finish_task_progress(
        self,
        task_id: str,
        done_msg: str,
        color: str | None = None,
        undo_entry_id: str | None = None,
        notify_title: str | None = None,
    ) -> None:
        """Clears active task state, paints completion toast, and optionally emits a tray/desktop notification."""
        if not hasattr(self, "_active_tasks"):
            self._active_tasks = {}
        self._active_tasks.pop(str(task_id), None)
        p = self.palette
        col = color or p.accent_jade
        try:
            if self._active_tasks:
                nxt = next(reversed(self._active_tasks.values()))
                if hasattr(self, "topbar_status"):
                    self.topbar_status.configure(text=f"⏳ {nxt}", text_color=p.accent_brass)
            else:
                if hasattr(self, "topbar_beacon"):
                    self.topbar_beacon.set_scanning(False)
                if hasattr(self, "topbar_pulse"):
                    self.topbar_pulse.set_active(False)
                if hasattr(self, "topbar_status"):
                    short_msg = done_msg if len(done_msg) <= 68 else done_msg[:65] + "..."
                    self.topbar_status.configure(text=f"✓ {short_msg}", text_color=col)
        except Exception:
            pass
        clean_done = done_msg if done_msg.startswith("✓") else f"✓ {done_msg}"
        self.show_toast(clean_done, color=col, undo_entry_id=undo_entry_id)
        if notify_title and hasattr(self, "tray"):
            try:
                is_bg = str(self.state()) in ("withdrawn", "iconic") or self.focus_displayof() is None
            except Exception:
                is_bg = False
            if is_bg:
                try:
                    self.tray.notify(notify_title, done_msg)
                except Exception:
                    pass

    def _install_button_progress_hook(self) -> None:
        """Ensures every interactive button click across the UI gives immediate in-progress feedback."""
        try:
            self.bind_all("<ButtonPress-1>", self._on_any_button_press_progress, add="+")
        except Exception:
            pass

    def _on_any_button_press_progress(self, event=None) -> None:
        w = getattr(event, "widget", None)
        btn = None
        cur = w
        for _ in range(4):
            if cur is None:
                break
            if isinstance(cur, ctk.CTkButton):
                btn = cur
                break
            cur = getattr(cur, "master", None)
        if btn is None or btn is getattr(self, "toast_undo_btn", None):
            return
        try:
            if str(btn.cget("state")) == "disabled":
                return
            lbl = str(btn.cget("text") or "").strip()
        except Exception:
            return
        if not lbl or lbl in ("×", "−", "Undo", "No", "Cancel", "Close") or btn in getattr(self, "nav_buttons", {}).values():
            return
        seq_before = getattr(self, "_toast_seq", 0)
        p = self.palette
        try:
            if hasattr(self, "topbar_pulse"):
                self.topbar_pulse.set_active(True)
            if hasattr(self, "topbar_status"):
                self.topbar_status.configure(text=f"⏳ In progress: {lbl}...", text_color=p.accent_brass)
        except Exception:
            pass

        def _post_click_check() -> None:
            if not self.winfo_exists():
                return
            # If the button's handler didn't start an async task or emit a custom toast, show a crisp completion toast
            if getattr(self, "_toast_seq", 0) == seq_before and not getattr(self, "_active_tasks", None):
                try:
                    if hasattr(self, "topbar_pulse"):
                        self.topbar_pulse.set_active(False)
                    if hasattr(self, "topbar_status"):
                        self.topbar_status.configure(text=f"✓ {lbl} complete", text_color=p.accent_jade)
                except Exception:
                    pass
                self.show_toast(f"✓ {lbl} complete.", color=p.accent_jade)

        try:
            self.after(35, _post_click_check)
        except Exception:
            pass

    def _animate_toast_in(self, step: int) -> None:
        if not self.winfo_exists():
            return
        steps = (0.995, 0.985, 0.976, 0.97)
        if step < len(steps):
            self.toast_frame.place(relx=0.98, rely=steps[step], anchor="se")
            self.after(18, lambda s=step + 1: self._animate_toast_in(s))

    def hide_toast(self) -> None:
        self.toast_frame.place_forget()

    def confirm_yes_no(
        self,
        title: str,
        summary: str,
        details: list[str] | None = None,
        yes_label: str = "Yes, Apply Now",
        no_label: str = "No, Keep Current",
        force_prompt: bool = False,
    ) -> bool:
        """Unified 1-button Yes / No confirmation for Wi-Fi channel switches and important changes."""
        if not self._show_startup_popups:
            return True
        if not force_prompt and not getattr(self.config_obj, "confirm_important_prompts", True):
            return True
        try:
            is_hidden = str(self.state()) in ("withdrawn", "iconic")
        except Exception:
            is_hidden = False
        if is_hidden:
            if not force_prompt:
                # Never block the event loop with a hidden transient grab dialog while minimized to tray
                return False
            self._restore_main_window()
        dlg = YesNoActionDialog(
            self,
            self.palette,
            self.fonts,
            title_text=title,
            summary=summary,
            detail_lines=details,
            yes_label=yes_label,
            no_label=no_label,
        )
        self.wait_window(dlg)
        return bool(dlg.approved)

    def switch_nav(self, key: str) -> None:
        allowed_keys = allowed_nav_keys(getattr(self.config_obj, "enabled_modules", None))
        if key not in allowed_keys:
            mod_id = NAV_KEY_TO_MODULE_ID.get(key, "")
            mod_name = MODULE_BY_ID[mod_id].name if mod_id in MODULE_BY_ID else key
            self.show_toast(
                f"Module '{mod_name}' is disabled. Enable it in Settings -> Modular Features.",
                color=self.palette.accent_brass,
            )
            key = "dashboard"
        p = self.palette
        self.current_nav = key
        titles = {
            "dashboard": "Command Deck",
            "monitoring": "Live Monitoring & Deep Diagnostics",
            "playbook": "Diagnostics & Playbook",
            "hunter": "AI & Process Hunter",
            "scripts": "Toggleable Scripts",
            "hardware": "Hardware & Pipelines",
            "modules": "System Modules",
            "wifi": "Wi-Fi Optimizer, Failover & Mesh Assistant",
            "rf_waves": "Bandwidth & RF Radio-Wave Spectrum",
            "remote_beam": "AstraBeam · Tailscale, iOS/Android/Watch & Devices",
            "hermes": "Hermes OS AI · Autonomous Agent, GPU Local & Free Cloud",
            "cmd_vault": "Install Commands & Script Vault (SQLite)",
            "scanner": "Full System Scanner & Known-Issues DB",
            "probalance": "ProBalance & Latency",
            "autoruns": "Autoruns & Baselines",
            "safeguards": "Forensic & Crash Vault",
            "settings": "Settings & Packaging",
        }
        self.topbar_title.configure(text=titles.get(key, __app_name__))
        if hasattr(self, "category_emblem"):
            self.category_emblem.set_category(key)
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

        if key == "monitoring":
            update_monitoring_live(self)
            refresh_monitoring_peripherals(self)
        elif key == "playbook":
            self._render_playbook()
        elif key == "hunter":
            self._render_hunter()
        elif key == "scripts":
            self._render_scripts()
        elif key == "hardware":
            self._render_hardware_page()
        elif key == "modules":
            self._render_modules()
        elif key == "wifi":
            self._render_wifi_page()
        elif key == "rf_waves":
            self._render_rf_waves_page()
        elif key == "remote_beam":
            self._render_remote_beam_page()
        elif key == "hermes":
            self._render_hermes_page()
        elif key == "cmd_vault":
            self._render_cmd_vault_page()
        elif key == "scanner":
            self._render_scanner_page()
        elif key == "probalance":
            self._render_probalance_page()
        elif key == "autoruns":
            self._render_autoruns_page()
        elif key == "safeguards":
            self._render_safeguards()

    def _on_toggle_simple_mode(self) -> None:
        val = bool(self.simple_mode_var.get())
        self.config_obj.simple_mode = val
        if hasattr(self, "set_simple_mode_var"):
            self.set_simple_mode_var.set(val)
        self.cfg_mgr.save(self.config_obj)
        self._apply_simple_mode(initial=False)
        self.show_toast(
            "Simple Mode ON — showing beginner-friendly essentials."
            if val
            else "Advanced Mode ON — all 10 studio tools & technical switches visible."
        )

    def _apply_simple_mode(self, initial: bool = False) -> None:
        simple = bool(getattr(self.config_obj, "simple_mode", True))
        allowed_keys = allowed_nav_keys(getattr(self.config_obj, "enabled_modules", None))
        if hasattr(self, "simple_mode_var") and bool(self.simple_mode_var.get()) != simple:
            self.simple_mode_var.set(simple)
        if hasattr(self, "nav_buttons") and hasattr(self, "_nav_order") and hasattr(self, "_sidebar_spacer"):
            for k in self._nav_order:
                btn = self.nav_buttons.get(k)
                if btn is not None:
                    btn.pack_forget()
            for k in self._nav_order:
                btn = self.nav_buttons.get(k)
                if btn is None:
                    continue
                if k not in allowed_keys:
                    continue
                if simple and k in getattr(self, "_advanced_nav_keys", ()):
                    continue
                if simple and hasattr(self, "_simple_nav_labels") and k in self._simple_nav_labels:
                    btn.configure(text=f"  {self._simple_nav_labels[k]}")
                elif hasattr(self, "_full_nav_labels") and k in self._full_nav_labels:
                    btn.configure(text=f"  {self._full_nav_labels[k]}")
                btn.pack(fill="x", padx=12, pady=1, before=self._sidebar_spacer)

        if hasattr(self, "dry_run_switch"):
            if simple:
                self.dry_run_switch.pack_forget()
            elif not self.dry_run_switch.winfo_ismapped():
                self.dry_run_switch.pack(side="right", padx=6)

        if hasattr(self, "dash_tools_card"):
            if simple:
                self.dash_tools_card.grid_remove()
            else:
                self.dash_tools_card.grid()

        if hasattr(self, "dash_diag_panel"):
            if simple:
                self.dash_diag_panel.grid_remove()
            else:
                self.dash_diag_panel.grid()

        if hasattr(self, "settings_advanced_cards"):
            for adv_c in self.settings_advanced_cards:
                try:
                    if simple:
                        adv_c.pack_forget()
                    else:
                        adv_c.pack(fill="x", pady=(0, 10))
                except Exception:
                    pass

        if not initial and (
            self.current_nav not in allowed_keys
            or (simple and self.current_nav in getattr(self, "_advanced_nav_keys", ()))
        ):
            self.switch_nav("dashboard")

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
            try:
                if getattr(self, "_astrabeam_server", None) is not None:
                    self._astrabeam_server.stop()
                    self._astrabeam_server = None
            except Exception:
                pass
            try:
                if self.adblock_shield.dns_running:
                    self.adblock_shield.stop_dns_sinkhole()
            except Exception:
                pass
            try:
                self.tray.stop()
            except Exception:
                pass
            try:
                self._global_hotkeys.stop()
            except Exception:
                pass
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
        mem = self.updater.load_install_memory()
        want_desktop = bool(mem.get("desktop_icon", False)) if mem.get("desktop_icon") is not None else False
        if self._show_startup_popups:
            want_desktop = self.confirm_yes_no(
                "Add Desktop Icon Shortcut?",
                "AstraCradle always installs your App Menu & Taskbar icons. Would you also like an icon placed on your Desktop?",
                yes_label="Yes, Add Desktop Icon",
                no_label="No, Menu Only",
                force_prompt=True,
            )
        created = self.lifecycle.install_shortcuts(repo_root, include_desktop_icon=want_desktop)
        self.updater.record_install_memory(
            install_dir=repo_root,
            install_method=str(mem.get("install_method") or "gui_shortcuts"),
            desktop_icon=want_desktop,
            shortcuts_installed=True,
            step_note="Created App Menu / Launcher shortcuts",
            config=self.config_obj,
        )
        self._refresh_install_memory_label()
        self.show_toast(f"Created {len(created)} Application Menu / Launcher shortcut(s)!")

    def open_setup_wizard(self, first_run: bool = False) -> None:
        def on_finish(payload: dict) -> None:
            skipped = bool(payload.get("skipped", False))
            old_theme = str(self.config_obj.theme)
            old_lang = str(self.config_obj.language)
            setup_actions: list[str] = []

            if not skipped:
                self.config_obj.apply_wizard_choices(payload)
                setup_actions.append(f"{len(self.config_obj.enabled_modules)} modules")
                setup_actions.append(f"profile: {self.config_obj.active_profile}")
                try:
                    self.lifecycle.set_launch_at_login(bool(self.config_obj.launch_at_login))
                    if self.config_obj.launch_at_login:
                        setup_actions.append("launch at login")
                except Exception:
                    pass

                if payload.get("install_shortcuts"):
                    try:
                        repo_root = Path(__file__).resolve().parents[2]
                        want_desk = bool(payload.get("desktop_icon", False))
                        self.lifecycle.install_shortcuts(repo_root, include_desktop_icon=want_desk)
                        self.updater.record_install_memory(
                            install_dir=repo_root,
                            install_method="setup_wizard",
                            desktop_icon=want_desk,
                            shortcuts_installed=True,
                            step_note=f"Setup Wizard configured {len(self.config_obj.enabled_modules)} modules ({self.config_obj.install_bundle_preset})",
                            config=self.config_obj,
                        )
                        setup_actions.append("shortcuts" + (" + desktop icon" if want_desk else ""))
                    except Exception:
                        pass
            else:
                self.config_obj.consent_prompted = True
                self.config_obj.setup_wizard_completed = True

            self.cfg_mgr.save(self.config_obj)
            self.updater.record_install_memory(
                install_method="setup_wizard",
                step_note=f"Saved setup: {self.config_obj.install_bundle_preset} ({len(self.config_obj.enabled_modules)} modules)",
                config=self.config_obj,
            )

            # Apply live UI changes immediately (theme, language, sidebar modules, tray, desktop widget)
            if str(self.config_obj.theme) != old_theme or str(self.config_obj.language) != old_lang:
                try:
                    self.rebuild_ui_for_theme()
                except Exception:
                    self._sync_module_settings_vars()
                    self._apply_simple_mode(initial=False)
            else:
                self._sync_module_settings_vars()
                self._apply_simple_mode(initial=False)

            if hasattr(self, "profile_menu") and self.profile_menu.winfo_exists():
                self.profile_menu.set(self.config_obj.active_profile)

            try:
                if self.config_obj.show_tray_icon:
                    self.tray.ensure_running()
                else:
                    self.tray.stop()
            except Exception:
                pass

            try:
                if self.config_obj.desktop_widget_enabled:
                    self.show_desktop_widget()
                else:
                    self.hide_desktop_widget(persist_disabled=False)
            except Exception:
                pass

            # Execute enabled module services as requested by the user
            if not skipped:
                if (
                    self.config_obj.is_module_enabled("privacy_shield")
                    and self.config_obj.astrashield_auto_start
                ):
                    self.after(200, self._auto_start_astrashield_async)
                    setup_actions.append("AstraShield armed")

                if (
                    self.config_obj.is_module_enabled("wifi_failover_mesh")
                    and self.config_obj.consent_wifi_auto_channel
                ):
                    self.after(350, lambda: self._auto_wifi_optimize_async(periodic=False))
                    setup_actions.append("Wi-Fi optimizer")

                if (
                    self.config_obj.is_module_enabled("remote_beam")
                    and payload.get("start_beam_now")
                    and getattr(self, "_astrabeam_server", None) is None
                ):
                    self.after(
                        450,
                        lambda: self._on_toggle_astrabeam_host(
                            str(getattr(self.config_obj, "astrabeam_port", 47992) or 47992),
                            str(getattr(self.config_obj, "astrabeam_fps", 30) or 30),
                            str(getattr(self.config_obj, "astrabeam_pin", "") or ""),
                            bool(getattr(self.config_obj, "astrabeam_tailscale_only", True)),
                            bool(getattr(self.config_obj, "astrabeam_tls_enabled", True)),
                        ),
                    )
                    setup_actions.append("iPhone/Watch host started")

                if payload.get("run_initial_fix"):
                    self.after(700, self.on_fix_everything_safe)
                    setup_actions.append("initial safe tune-up")

                if self.config_obj.is_module_enabled("hermes_ai") and payload.get("hermes_auto_setup_now"):
                    h_key = str(payload.get("hermes_api_key") or "").strip()

                    def _bg_hermes_wizard_setup() -> None:
                        ok_h, msg_h, st_h = self.hermes_mgr.install_and_setup_hermes(
                            self.config_obj,
                            api_key=h_key,
                            install_upstream_pkg=True,
                            pull_local_model=True,
                        )
                        self._hermes_status_cache = st_h
                        self.after(
                            0,
                            lambda: self.show_toast(
                                "Hermes OS AI setup complete!" if ok_h else f"Hermes OS AI: {msg_h[:80]}"
                            ),
                        )

                    threading.Thread(target=_bg_hermes_wizard_setup, daemon=True).start()
                    setup_actions.append("Hermes OS AI installing")
                elif self.config_obj.is_module_enabled("hermes_ai") and str(payload.get("hermes_api_key") or "").strip():
                    spec_h = self.hermes_mgr.resolve_provider_spec(self.config_obj)
                    self.hermes_mgr.save_env_vars({spec_h["env_var"]: str(payload["hermes_api_key"]).strip()})
                    self.hermes_mgr.ensure_hermes_home(self.config_obj)

            self.journal.log_event(
                "SetupWizard",
                "complete_setup_wizard",
                f"Preset={self.config_obj.install_bundle_preset}, Modules={self.config_obj.enabled_modules}, Actions={setup_actions}",
                "User completed plain-English modular Setup Wizard and executed chosen setup actions",
                "ok",
            )
            detail_msg = ", ".join(setup_actions) if setup_actions else self.config_obj.install_bundle_preset
            self.show_toast(f"All user choices set up ({detail_msg}).")

            if self.config_obj.consent_telemetry:
                self.run_full_pc_discovery_async(show_dialog=False)
                self._schedule_telemetry_poll()
            else:
                self.dash_headline.configure(text="Setup Complete (Minimal Scan Mode)")
                self.dash_summary.configure(
                    text="Your chosen modules and permissions are active. Open Permissions or Setup Wizard anytime to adjust."
                )

        SetupWizardDialog(
            self,
            self.palette,
            self.fonts,
            self.config_obj,
            on_finish,
            first_run=first_run or not self.config_obj.setup_wizard_completed,
        )

    def _sync_module_settings_vars(self) -> None:
        if hasattr(self, "set_module_vars"):
            active = set(normalize_enabled_modules(getattr(self.config_obj, "enabled_modules", None)))
            for mid, var in self.set_module_vars.items():
                spec = MODULE_BY_ID.get(mid)
                var.set(True if (spec and spec.locked) else (mid in active))
        if hasattr(self, "set_bundle_preset_var"):
            self.set_bundle_preset_var.set(str(getattr(self.config_obj, "install_bundle_preset", "Custom")))
        if hasattr(self, "set_simple_mode_var"):
            self.set_simple_mode_var.set(bool(self.config_obj.simple_mode))

    def _on_apply_settings_modules(self) -> None:
        if not hasattr(self, "set_module_vars"):
            return
        picked = [mid for mid, var in self.set_module_vars.items() if bool(var.get())]
        self.config_obj.apply_module_selection(picked, preset_name="Custom", sync_simple_mode=False)
        self.config_obj.setup_wizard_completed = True
        self.cfg_mgr.save(self.config_obj)
        self.updater.record_install_memory(
            install_method="settings_modules",
            step_note=f"Updated enabled modules ({len(self.config_obj.enabled_modules)})",
            config=self.config_obj,
        )
        self._sync_module_settings_vars()
        self._apply_simple_mode(initial=False)
        self.journal.log_event(
            "Modules",
            "apply_module_selection",
            f"Active modules: {self.config_obj.enabled_modules}",
            "User customized active AstraCradle modules in Settings",
            "ok",
        )
        self.show_toast(
            f"Updated modules ({len(self.config_obj.enabled_modules)}/{len(ASTRA_MODULES)} active) — sidebar & background engines updated."
        )

    def _on_apply_bundle_preset_from_settings(self, preset_name: str) -> None:
        spec = INSTALL_BUNDLE_PRESETS.get(preset_name)
        if not spec:
            return
        self.config_obj.apply_module_selection(spec["modules"], preset_name=preset_name, sync_simple_mode=True)
        self.config_obj.setup_wizard_completed = True
        self.cfg_mgr.save(self.config_obj)
        self._sync_module_settings_vars()
        self._apply_simple_mode(initial=False)
        self.show_toast(f"Applied bundle preset: {preset_name} ({len(self.config_obj.enabled_modules)} modules).")

    def open_consent_wizard(self, first_run: bool = False) -> None:
        def on_save(perms: dict[str, bool], scan_mode: str | None = None) -> None:
            trigger_full = bool(perms.pop("_trigger_full_discovery", False))
            for k, v in perms.items():
                if hasattr(self.config_obj, k):
                    setattr(self.config_obj, k, v)
            if "consent_wifi_auto_channel" in perms:
                if perms["consent_wifi_auto_channel"]:
                    self.config_obj.wifi_auto_optimize = True
                    self.config_obj.wifi_prompt_before_apply = False
                    self.config_obj.wifi_min_channel_hold_min = max(
                        10, int(getattr(self.config_obj, "wifi_min_channel_hold_min", 10))
                    )
                else:
                    self.config_obj.wifi_prompt_before_apply = True
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
            if perms.get("consent_wifi_auto_channel") and getattr(self.config_obj, "wifi_auto_optimize", True):
                self._auto_wifi_optimize_async(periodic=False)
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
        self.begin_task_progress("full_discovery", "Running Full Local PC Baseline Discovery...")

        def worker() -> None:
            try:
                adapter = self._active_adapter()
                out = self.full_discovery.run_full_discovery(adapter, self.config_obj)
                sensors = TelemetryCollector.collect_detailed_sensors() if self.config_obj.consent_telemetry else {}
                conflicts = self.conflict_detector.scan_conflicts() if self.config_obj.consent_process_scan else []
                peripherals = self.usb_guard.scan_all_peripherals() if self.config_obj.consent_telemetry else []
                self.config_obj.first_run_scan_completed = True
                self.cfg_mgr.save(self.config_obj)
                self._ui_queue.put(("full_discovery_done", out, show_dialog, sensors, conflicts, peripherals))
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
        info_col.grid(row=0, column=1, sticky="nsew", padx=(4, 10), pady=14)

        self.hero_motion_canvas = InteractiveFluidSolarCanvas(
            hero,
            p,
            width=210,
            height=138,
            mode=(
                "Reduced Motion"
                if (self.config_obj.low_power_ui_mode or not getattr(self.config_obj, "animations_enabled", True))
                else str(getattr(self.config_obj, "motion_style", "Hybrid Solar + Fluid"))
            ),
        )
        self.hero_motion_canvas.grid(row=0, column=2, padx=(0, 16), pady=14, sticky="e")
        attach_tooltip(
            self.hero_motion_canvas,
            "Interactive Solar Orrery & Black-and-White-style Fluid Particle Crest — hover or drag your mouse across it!",
            p,
        )

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
                text="Comprehensive Setup",
                height=34,
                corner_radius=8,
                fg_color=p.accent_brass,
                hover_color="#B8934F",
                text_color="#120E06",
                font=self.fonts["body_bold"],
                command=self.open_comprehensive_setup,
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
                text="Export Crash Log",
                height=34,
                corner_radius=8,
                fg_color=p.bg_elevated,
                hover_color=p.border_strong,
                font=self.fonts["body"],
                command=lambda: self.on_export_forensic_crash_log(save_as=False),
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
        init_cols = 6 if (self.winfo_screenwidth() or 1300) >= 1750 else 3
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

        # Rows 4-6: Zero-Flicker Detailed Thermals, Individual Expandable Compact Diagnostics & Clickable Snapshot Grid
        build_command_deck_snapshot(self, page)

        # Row 7: Quick Power Tools Strip (Hidden in Simple Mode for beginner clarity)
        tools = ctk.CTkFrame(
            page, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
        )
        tools.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(4, 6))
        self.dash_tools_card = tools
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

        b_ai_log = ctk.CTkButton(
            tbtns,
            text="Consolidate & Export AI Diagnostic Log",
            height=32,
            fg_color=p.accent_jade,
            hover_color=p.accent_jade_hover,
            text_color="#071510",
            font=self.fonts["body_bold"],
            command=self.on_export_ai_diagnostic_bundle,
        )
        b_ai_log.grid(row=1, column=0, columnspan=2, sticky="ew", padx=(0, 6), pady=(6, 0))
        attach_tooltip(
            b_ai_log,
            "Archives old snapshots/logs and generates AI_HANDOFF_REPORT.md + JSON + zip for any AI assistant.",
            p,
        )

        self.auto_det_var = tk.BooleanVar(value=self.config_obj.app_auto_detect_enabled)
        b_autodet = ctk.CTkSwitch(
            tbtns,
            text="Auto-Detect Games/3D Apps",
            variable=self.auto_det_var,
            font=self.fonts["small"],
            progress_color=p.accent_jade,
            command=self._on_toggle_auto_detect,
        )
        b_autodet.grid(row=1, column=2, sticky="e", padx=(8, 0), pady=(6, 0))
        attach_tooltip(
            b_autodet,
            "Automatically activates matching Toggleable Scripts when Blender, Steam, Cargo, or Unreal launch.",
            p,
        )

        self.dash_deploy_frame = ctk.CTkFrame(
            page, fg_color=p.bg_card, border_width=1, border_color=p.accent_cyan, corner_radius=12
        )
        self.dash_deploy_frame.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(4, 8))
        self._build_command_deck_deploy_hub()

    def _build_monitoring_page(self) -> None:
        mon_scroll = ctk.CTkScrollableFrame(self.pages["monitoring"], fg_color="transparent")
        mon_scroll.pack(fill="both", expand=True)
        build_monitoring_page(self, mon_scroll)

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
        is_simple = bool(getattr(self.config_obj, "simple_mode", True))
        if not is_simple:
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

        if is_simple:
            def _toggle_diy(frame=diy, parent_ftr=ftr) -> None:
                if frame.winfo_ismapped():
                    frame.pack_forget()
                else:
                    frame.pack(fill="x", padx=16, pady=(0, 10), before=parent_ftr)

            ctk.CTkButton(
                ftr,
                text="Show Technical Steps",
                height=28,
                width=142,
                corner_radius=7,
                fg_color=p.bg_elevated,
                hover_color=p.border_strong,
                font=self.fonts["caption"],
                command=_toggle_diy,
            ).pack(side="right", padx=(6, 0))
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
    # Page 3: AI Chatbot & Unused Process Hunter + PID Web Intelligence
    # ------------------------------------------------------------------
    def _build_hunter_page(self) -> None:
        p = self.palette
        root = self.pages["hunter"]
        root.grid_rowconfigure(1, weight=1)
        root.grid_columnconfigure(0, weight=1)

        # Persistent PID# & Process Name Direct Web Search Card (never flashes!)
        search_card = ctk.CTkFrame(
            root, fg_color=p.bg_card, border_width=1, border_color=p.accent_cyan, corner_radius=12
        )
        search_card.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        shdr = ctk.CTkFrame(search_card, fg_color="transparent")
        shdr.pack(fill="x", padx=16, pady=(10, 4))
        ctk.CTkLabel(
            shdr,
            text="PID# & Process Web Intelligence Lookup (Plain-English Descriptions)",
            font=self.fonts["body_bold"],
            text_color=p.accent_cyan,
        ).pack(side="left")
        ctk.CTkButton(
            shdr,
            text="Rescan Processes",
            height=26,
            corner_radius=7,
            fg_color=p.accent_cyan,
            text_color="#071217",
            font=self.fonts["caption"],
            command=self.on_deep_scan,
        ).pack(side="right", padx=(6, 0))
        ctk.CTkButton(
            shdr,
            text="Permission Settings",
            height=26,
            corner_radius=7,
            fg_color=p.bg_elevated,
            font=self.fonts["caption"],
            command=self.open_consent_wizard,
        ).pack(side="right")

        srow = ctk.CTkFrame(search_card, fg_color="transparent")
        srow.pack(fill="x", padx=16, pady=(2, 6))
        self.hunter_pid_var = tk.StringVar(value="")
        self.hunter_pid_entry = ctk.CTkEntry(
            srow,
            textvariable=self.hunter_pid_var,
            placeholder_text="Enter PID# (e.g. 1234) or process name (e.g. ollama, dockerd, pipewire, chrome)...",
            height=32,
            font=self.fonts["body"],
        )
        self.hunter_pid_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.hunter_pid_entry.bind("<Return>", lambda _e: self._on_lookup_pid_or_process())
        ctk.CTkButton(
            srow,
            text="Web Lookup PID / Process",
            width=185,
            height=32,
            fg_color=p.accent_jade,
            hover_color=p.accent_jade_hover,
            text_color="#071510",
            font=self.fonts["body_bold"],
            command=self._on_lookup_pid_or_process,
        ).pack(side="right")

        self.hunter_lookup_result_lbl = ctk.CTkLabel(
            search_card,
            text="Tip: Enter any numeric PID# or click 'Web Explain' on any running process below for a brief plain-English summary.",
            font=self.fonts["small"],
            text_color=p.text_secondary,
            wraplength=self._content_wrap(150),
            justify="left",
            anchor="w",
        )
        self.hunter_lookup_result_lbl.pack(fill="x", padx=16, pady=(0, 10))

        self.hunter_scroll = ctk.CTkScrollableFrame(root, fg_color="transparent")
        self.hunter_scroll.grid(row=1, column=0, sticky="nsew")

    def _on_lookup_pid_or_process(self, target_override: str | int | None = None) -> None:
        raw = str(target_override if target_override is not None else self.hunter_pid_var.get()).strip()
        if not raw:
            self.show_toast("Enter a PID# or process name to look up.")
            return
        if target_override is not None:
            self.hunter_pid_var.set(str(target_override))
        self.begin_task_progress("proc_lookup", f"Looking up process '{raw}'...")
        self.hunter_lookup_result_lbl.configure(
            text=f"Searching local knowledge + web for '{raw}'...",
            text_color=self.palette.accent_cyan,
        )

        def _worker() -> None:
            res = self.proc_lookup.lookup(int(raw) if raw.isdigit() else raw, allow_web=True)
            self._ui_queue.put(("proc_lookup_done", res, True))

        threading.Thread(target=_worker, daemon=True).start()

    def _queue_bg_process_lookup(self, pid: int | None, name: str) -> None:
        key = name.lower().strip()
        if not key or key in self._proc_lookup_cache_ui or key in self._proc_lookup_in_flight:
            return
        self._proc_lookup_in_flight.add(key)

        def _worker() -> None:
            res = self.proc_lookup.lookup(pid if pid is not None else name, allow_web=True)
            self._ui_queue.put(("proc_lookup_done", res, False))

        threading.Thread(target=_worker, daemon=True).start()

    def _render_hunter(self) -> None:
        p = self.palette
        for c in self.hunter_scroll.winfo_children():
            c.destroy()

        if not self.config_obj.consent_process_scan:
            ctk.CTkLabel(
                self.hunter_scroll,
                text="Process & AI Chatbot scanning is currently disabled in your Permissions. Click 'Permission Settings' above to enable local inspection.",
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
            ).pack(padx=16, pady=14, anchor="w")
        else:
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
                title_txt = f"{rec.name}" + (f" (PID {rec.pid})" if rec.pid else "")
                ctk.CTkLabel(top, text=title_txt, font=self.fonts["body_bold"], text_color=p.text_primary).pack(side="left")

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
                    text="Web Explain",
                    width=92,
                    height=26,
                    corner_radius=6,
                    fg_color=p.bg_elevated,
                    hover_color=p.border_strong,
                    text_color=p.accent_cyan,
                    font=self.fonts["caption"],
                    command=lambda r=rec: self._on_lookup_pid_or_process(r.pid or r.target_ref or r.name),
                ).pack(side="right", padx=4)
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
                ).pack(fill="x", padx=14, pady=(0, 8))

        # Running Processes Snapshot with brief plain-English descriptions (via ProcessIntelligenceLookup)
        procs = (self.latest_snapshot.top_processes if self.latest_snapshot else []) or []
        if procs:
            pcard = ctk.CTkFrame(
                self.hunter_scroll, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
            )
            pcard.pack(fill="x", pady=(8, 4))
            ctk.CTkLabel(
                pcard,
                text=f"Active Running Processes ({len(procs)} top PIDs — Plain-English Summaries)",
                font=self.fonts["body_bold"],
                text_color=p.accent_cyan,
            ).pack(anchor="w", padx=14, pady=(10, 6))

            for proc in procs[:18]:
                pname = str(getattr(proc, "name", "") or "")
                pid = getattr(proc, "pid", None)
                cpu_p = float(getattr(proc, "cpu_percent", 0.0) or 0.0)
                mem_mb = float(getattr(proc, "memory_mb", 0.0) or 0.0)
                cached_desc = self._proc_lookup_cache_ui.get(pname.lower().strip())
                if cached_desc is None:
                    quick = self.proc_lookup.lookup(pid if pid is not None else pname, allow_web=False)
                    cached_desc = quick.brief_description
                    if quick.source_name == "OS Process Inspection":
                        self._queue_bg_process_lookup(pid, pname)

                prow = ctk.CTkFrame(pcard, fg_color=p.bg_elevated, corner_radius=8)
                prow.pack(fill="x", padx=14, pady=2)
                top_r = ctk.CTkFrame(prow, fg_color="transparent")
                top_r.pack(fill="x", padx=10, pady=(5, 2))
                ctk.CTkLabel(
                    top_r,
                    text=f"PID {pid} · {pname}",
                    font=self.fonts["body_bold"],
                    text_color=p.text_primary,
                ).pack(side="left")
                ctk.CTkLabel(
                    top_r,
                    text=f"CPU {cpu_p:.1f}% · RAM {mem_mb:.0f} MB",
                    font=self.fonts["mono_small"],
                    text_color=p.accent_jade,
                ).pack(side="left", padx=12)
                ctk.CTkButton(
                    top_r,
                    text="Web Lookup",
                    width=88,
                    height=22,
                    fg_color=p.bg_card,
                    text_color=p.accent_cyan,
                    font=self.fonts["caption"],
                    command=lambda p_id=pid, p_nm=pname: self._on_lookup_pid_or_process(p_id or p_nm),
                ).pack(side="right")
                ctk.CTkLabel(
                    prow,
                    text=cached_desc,
                    font=self.fonts["small"],
                    text_color=p.text_secondary,
                    wraplength=self._content_wrap(180),
                    justify="left",
                    anchor="w",
                ).pack(fill="x", padx=10, pady=(0, 6))
            ctk.CTkFrame(pcard, height=6, fg_color="transparent").pack()

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
        ).pack(side="right", padx=(4, 16), pady=10)
        ctk.CTkButton(
            hdr,
            text="Import Script Pack",
            height=30,
            corner_radius=7,
            fg_color=p.bg_elevated,
            hover_color=p.border_strong,
            font=self.fonts["small"],
            command=self._on_import_script_pack,
        ).pack(side="right", padx=4, pady=10)
        ctk.CTkButton(
            hdr,
            text="Export Script Pack",
            height=30,
            corner_radius=7,
            fg_color=p.bg_elevated,
            hover_color=p.border_strong,
            font=self.fonts["small"],
            command=self._on_export_script_pack,
        ).pack(side="right", padx=4, pady=10)

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
                aname,
                fw_act.get(),
                rate_kbps=kbps,
                dry_run=bool(self.dry_run_var.get()),
                enforce_os=not bool(self.dry_run_var.get()),
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

        if yt_row := getattr(self, "_last_yt_row_ref", None):
            pass
        ctrl_row = ctk.CTkFrame(ab_card, fg_color="transparent")
        ctrl_row.pack(fill="x", padx=14, pady=(0, 8))
        auto_sw_var = tk.BooleanVar(value=bool(getattr(self.config_obj, "astrashield_auto_start", True)))
        ctk.CTkSwitch(
            ctrl_row,
            text="Auto-Start Sinkhole on Startup",
            variable=auto_sw_var,
            font=self.fonts["caption"],
            progress_color=p.accent_jade,
            command=lambda: self._on_toggle_astrashield_autostart(bool(auto_sw_var.get())),
        ).pack(side="left", padx=(0, 10))
        fs_sw_var = tk.BooleanVar(value=bool(getattr(self.config_obj, "astrashield_tv_auto_failsafe", True)))
        ctk.CTkSwitch(
            ctrl_row,
            text="TV Failsafe Auto-Reset",
            variable=fs_sw_var,
            font=self.fonts["caption"],
            progress_color=p.accent_brass,
            command=lambda: self._on_toggle_tv_failsafe(bool(fs_sw_var.get())),
        ).pack(side="left")
        ctk.CTkButton(
            ctrl_row,
            text="Setup Sinkhole & Restart Program",
            height=26,
            fg_color=p.accent_jade,
            text_color="#071510",
            font=self.fonts["caption"],
            command=self.on_setup_and_restart_astrashield,
        ).pack(side="right")

        ctk.CTkLabel(
            ab_card,
            text=(
                f"Domains Blocked: {shield_st.blocked_domain_count:,} (Pi-hole Feed Cache: {shield_st.pihole_cached_count:,}) · "
                f"Queries: {shield_st.total_queries} · Blocked: {shield_st.blocked_queries} ({shield_st.blocked_percent:.1f}%) · "
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
        for cname, clabel in [
            ("ads", "Ads"),
            ("youtube_ads", "YouTube Ad Endpoints"),
            ("trackers", "Trackers"),
            ("telemetry", "Telemetry"),
            ("malware", "Malware"),
        ]:
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

        yt_row = ctk.CTkFrame(ab_card, fg_color="transparent")
        yt_row.pack(fill="x", padx=14, pady=(0, 6))
        yt_safe_var = tk.BooleanVar(value=bool(rules_data.get("youtube_safe_prune", True)))
        ctk.CTkSwitch(
            yt_row,
            text="YouTube Safe Ad-Prune (Never Breaks googlevideo Playback)",
            variable=yt_safe_var,
            font=self.fonts["caption"],
            progress_color=p.accent_jade,
            command=lambda: self._on_toggle_youtube_safe_prune(bool(yt_safe_var.get())),
        ).pack(side="left")
        ctk.CTkButton(
            yt_row,
            text="Sync Standard Pi-hole Lists",
            height=26,
            fg_color=p.accent_cyan,
            text_color="#071217",
            font=self.fonts["caption"],
            command=self._on_sync_pihole_lists,
        ).pack(side="right", padx=(6, 0))
        ctk.CTkButton(
            yt_row,
            text="Export YouTube Browser Ad-Pruner (.user.js)",
            height=26,
            fg_color=p.bg_elevated,
            font=self.fonts["caption"],
            command=self._on_export_youtube_adprune,
        ).pack(side="right", padx=4)

        browsers = self.adblock_shield.detect_installed_browsers()
        if browsers:
            b_summary = ", ".join(
                f"{b['name']} ({'uBlock OK' if b['has_ublock'] else 'no uBlock detected'})" for b in browsers[:5]
            )
            ctk.CTkLabel(
                ab_card,
                text=f"Detected Browsers: {b_summary}",
                font=self.fonts["caption"],
                text_color=p.text_muted,
            ).pack(anchor="w", padx=14, pady=(0, 6))

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

        # Live AstraShield Query Feed with 1-Click Allowlist
        qfeed = self.adblock_shield.recent_query_feed(limit=8)
        qbox = ctk.CTkFrame(ab_card, fg_color=p.bg_elevated, corner_radius=8)
        qbox.pack(fill="x", padx=14, pady=(0, 10))
        ctk.CTkLabel(
            qbox,
            text=f"Live DNS Query Feed ({len(qfeed)} recent)",
            font=self.fonts["caption"],
            text_color=p.accent_cyan,
        ).pack(anchor="w", padx=10, pady=(6, 2))
        if not qfeed:
            ctk.CTkLabel(
                qbox,
                text="No DNS queries captured yet — start Local DNS Sinkhole or test a domain above.",
                font=self.fonts["small"],
                text_color=p.text_muted,
            ).pack(anchor="w", padx=10, pady=(0, 6))
        else:
            for qev in qfeed[:6]:
                qrow = ctk.CTkFrame(qbox, fg_color="transparent")
                qrow.pack(fill="x", padx=10, pady=1)
                col_q = p.accent_coral if qev.action == "blocked" else p.accent_jade
                rtt_tag = f" · {qev.rtt_ms:.1f}ms" if getattr(qev, "rtt_ms", None) is not None else ""
                ctk.CTkLabel(
                    qrow,
                    text=f"[{qev.action.upper()}] {qev.domain} ({qev.reason}{rtt_tag})",
                    font=self.fonts["mono_small"],
                    text_color=col_q,
                ).pack(side="left")
                if qev.action == "blocked":
                    ctk.CTkButton(
                        qrow,
                        text="Allow Domain",
                        width=96,
                        height=22,
                        fg_color=p.accent_jade,
                        text_color="#071510",
                        font=self.fonts["caption"],
                        command=lambda d=qev.domain: (
                            self.show_toast(self.adblock_shield.allow_domain_live(d, dry_run=bool(self.dry_run_var.get()))[1]),
                            self._render_modules(),
                        ),
                    ).pack(side="right", padx=2)
            ctk.CTkFrame(qbox, height=4, fg_color="transparent").pack()

        # 0.5 BadUSB Guard (ON by Default) & Attached Hardware Monitor
        ucard = ctk.CTkFrame(
            self.mod_body, fg_color=p.bg_card, border_width=1, border_color=p.accent_jade, corner_radius=12
        )
        ucard.pack(fill="x", pady=(0, 8))
        uhdr = ctk.CTkFrame(ucard, fg_color="transparent")
        uhdr.pack(fill="x", padx=14, pady=(10, 4))
        ctk.CTkLabel(
            uhdr,
            text="BadUSB Pre-Connection Guard & Live Peripheral Ping (ON by Default)",
            font=self.fonts["body_bold"],
            text_color=p.accent_jade,
        ).pack(side="left")

        badusb_var = tk.BooleanVar(value=bool(self.config_obj.badusb_guard_enabled))
        ctk.CTkSwitch(
            uhdr,
            text="BadUSB Guard",
            variable=badusb_var,
            font=self.fonts["caption"],
            progress_color=p.accent_jade,
            command=lambda: self._on_toggle_badusb_guard(bool(badusb_var.get())),
        ).pack(side="right", padx=(8, 0))

        ctk.CTkButton(
            uhdr,
            text="Enforce Kernel USB Gate",
            height=26,
            fg_color=p.accent_cyan,
            text_color="#071217",
            font=self.fonts["caption"],
            command=lambda: self._on_toggle_usb_strict_gate(True),
        ).pack(side="right", padx=4)

        periphs = self._latest_peripherals or self.usb_guard.scan_all_peripherals()
        ctk.CTkLabel(
            ucard,
            text=(
                f"Detected {len(periphs)} hardware/peripheral device(s). Newly plugged USB devices are pinged, "
                "inspected for composite HID/Storage/Microcontroller BadUSB signatures, and allowed ONLY after clearing."
            ),
            font=self.fonts["small"],
            text_color=p.text_secondary,
            wraplength=self._content_wrap(120),
            justify="left",
        ).pack(anchor="w", padx=14, pady=(0, 6))
        for dev in periphs[:8]:
            drow = ctk.CTkFrame(ucard, fg_color=p.bg_elevated, corner_radius=8)
            drow.pack(fill="x", padx=14, pady=(0, 4))
            ctk.CTkLabel(
                drow,
                text=f"[{dev.bus.upper()} · {dev.status.upper()}] {dev.vendor} {dev.name} ({dev.plain_description})",
                font=self.fonts["mono_small"],
                text_color=p.accent_coral if dev.status == "blocked" else p.text_secondary,
            ).pack(side="left", padx=10, pady=5)
            if dev.bus == "usb" and dev.status == "blocked":
                ctk.CTkButton(
                    drow,
                    text="Trust & Allow",
                    width=96,
                    height=22,
                    fg_color=p.accent_jade,
                    text_color="#071510",
                    font=self.fonts["caption"],
                    command=lambda d=dev: self._on_trust_usb(d),
                ).pack(side="right", padx=8, pady=4)
        ctk.CTkFrame(ucard, height=4, fg_color="transparent").pack()

        # 0c. Smart TV & Network Devices YouTube Ad-Shield (LAN DNS + TV Setup)
        tvcard = ctk.CTkFrame(
            self.mod_body, fg_color=p.bg_card, border_width=1, border_color=p.accent_cyan, corner_radius=12
        )
        tvcard.pack(fill="x", pady=(0, 8))
        tvhdr = ctk.CTkFrame(tvcard, fg_color="transparent")
        tvhdr.pack(fill="x", padx=14, pady=(10, 4))
        ctk.CTkLabel(
            tvhdr,
            text="Smart TV, Bluetooth & Network Devices YouTube Ad-Shield (Failsafe Enabled)",
            font=self.fonts["body_bold"],
            text_color=p.accent_cyan,
        ).pack(side="left")

        lan_tv_var = tk.BooleanVar(value=bool(getattr(self.config_obj, "astrashield_lan_tv_mode", False)))
        ctk.CTkSwitch(
            tvhdr,
            text="LAN TV DNS",
            variable=lan_tv_var,
            font=self.fonts["caption"],
            progress_color=p.accent_jade,
            command=lambda: self._on_toggle_lan_tv_dns(bool(lan_tv_var.get())),
        ).pack(side="right", padx=(8, 0))
        ctk.CTkButton(
            tvhdr,
            text="Reset TV DNS (Failsafe)",
            height=26,
            fg_color=p.badge_crit_bg,
            text_color=p.badge_crit_fg,
            font=self.fonts["caption"],
            command=self._on_restore_tv_failsafe,
        ).pack(side="right", padx=4)
        ctk.CTkButton(
            tvhdr,
            text="Scan Network, TVs & Bluetooth",
            height=26,
            fg_color=p.accent_cyan,
            text_color="#071217",
            font=self.fonts["caption"],
            command=self._on_scan_lan_tvs,
        ).pack(side="right", padx=4)

        lan_ip = self.adblock_shield.get_lan_ipv4()
        router_ip = self.lan_tv_shield.get_default_gateway_ip()
        dns_listen = self.adblock_shield.dns_listen_addr or "stopped"
        tv_fs = self.lan_tv_shield.load_failsafe_state()
        fs_note = (
            f" · Active TV Failsafe: {tv_fs.get('tv_brand')} ({tv_fs.get('tv_ip')}) -> Reset to {tv_fs.get('router_failsafe_dns', router_ip)} on exit/failure"
            if tv_fs and tv_fs.get("active")
            else ""
        )
        ctk.CTkLabel(
            tvcard,
            text=(
                f"PC LAN IP: {lan_ip} · Router Failsafe Secondary DNS: {router_ip} · Sinkhole: {dns_listen}{fs_note}\n"
                f"Set TV Primary DNS = {lan_ip} and Secondary DNS = {router_ip}. If AstraCradle stops or fails, TV resets cleanly."
            ),
            font=self.fonts["small"],
            text_color=p.text_secondary,
            justify="left",
            wraplength=820,
        ).pack(anchor="w", padx=14, pady=(0, 6))

        tv_devs = self._latest_lan_devices
        bt_devs = self._latest_bt_devices
        if not tv_devs and not bt_devs:
            ctk.CTkLabel(
                tvcard,
                text="Click 'Scan Network, TVs & Bluetooth' to discover Smart TVs, Wi-Fi devices & Bluetooth TVs/audio to connect.",
                font=self.fonts["small"],
                text_color=p.text_muted,
            ).pack(anchor="w", padx=14, pady=(0, 10))
        else:
            for dev in tv_devs[:10]:
                trow = ctk.CTkFrame(tvcard, fg_color=p.bg_elevated, corner_radius=8)
                trow.pack(fill="x", padx=14, pady=2)
                tcol = p.accent_jade if dev.dns_queries > 0 else (p.accent_cyan if dev.device_type in ("smart_tv", "streaming_box") else p.text_secondary)
                ctk.CTkLabel(
                    trow,
                    text=f"[{dev.brand}] {dev.ip} ({dev.mac}) — {dev.protection_status}",
                    font=self.fonts["mono_small"],
                    text_color=tcol,
                    anchor="w",
                ).pack(side="left", padx=10, pady=5)
                ctk.CTkButton(
                    trow,
                    text="Connect & Protect TV",
                    width=136,
                    height=22,
                    fg_color=p.accent_jade,
                    text_color="#071510",
                    font=self.fonts["caption"],
                    command=lambda d=dev: self._on_connect_protect_tv(d),
                ).pack(side="right", padx=8, pady=4)
            if bt_devs:
                ctk.CTkLabel(
                    tvcard,
                    text=f"Bluetooth Devices Discovered ({len(bt_devs)}):",
                    font=self.fonts["body_bold"],
                    text_color=p.accent_cyan,
                ).pack(anchor="w", padx=14, pady=(6, 2))
                for bt in bt_devs[:8]:
                    brow = ctk.CTkFrame(tvcard, fg_color=p.bg_elevated, corner_radius=8)
                    brow.pack(fill="x", padx=14, pady=2)
                    bcol = p.accent_jade if bt.connected else p.text_secondary
                    st_txt = "Connected" if bt.connected else ("Paired" if bt.paired else "Available")
                    ctk.CTkLabel(
                        brow,
                        text=f"[BT · {bt.device_kind.upper()}] {bt.name} ({bt.mac}) — {st_txt}",
                        font=self.fonts["mono_small"],
                        text_color=bcol,
                        anchor="w",
                    ).pack(side="left", padx=10, pady=5)
                    ctk.CTkButton(
                        brow,
                        text=("Disconnect" if bt.connected else "Connect"),
                        width=96,
                        height=22,
                        fg_color=p.bg_card if bt.connected else p.accent_cyan,
                        text_color=p.text_primary if bt.connected else "#071217",
                        font=self.fonts["caption"],
                        command=lambda b=bt: self._on_connect_bluetooth(b),
                    ).pack(side="right", padx=8, pady=4)
        ctk.CTkFrame(tvcard, height=4, fg_color="transparent").pack()

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
            command=lambda: self.show_toast(
                self.ram_standby_mgr.trim_standby_ram(
                    dry_run=bool(self.dry_run_var.get()),
                    elevation=self.elevation,
                )[1]
            ),
        ).pack(side="right", padx=(6, 0))
        dns_preset_var = tk.StringVar(value="Cloudflare (1.1.1.1)")
        ctk.CTkButton(
            nhdr,
            text="Apply Fast-DNS",
            height=26,
            fg_color=p.accent_cyan,
            text_color="#071217",
            font=self.fonts["caption"],
            command=lambda: self.show_toast(
                self.net_dns_inspector.apply_dns_preset(
                    dns_preset_var.get(),
                    dry_run=bool(self.dry_run_var.get()),
                    elevation=self.elevation,
                )[1]
            ),
        ).pack(side="right", padx=4)
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
        _, msg, cmd = self.gpu_pwr.apply_profile(
            prof,
            dry_run=bool(self.dry_run_var.get()),
            enforce_os=not bool(self.dry_run_var.get()),
        )
        self._draft_fan_curve = clamp_fan_curve_points(prof.curve_points)
        self._copy_text(cmd)
        self.show_toast(f"{msg} (Command copied: {cmd})")
        self._refresh_hw_or_mod()

    # ------------------------------------------------------------------
    # Special Category: Wi-Fi Channel Optimizer (CachyOS + Fish Ready)
    # ------------------------------------------------------------------
    def _build_wifi_page(self) -> None:
        self.wifi_scroll = ctk.CTkScrollableFrame(self.pages["wifi"], fg_color="transparent")
        self.wifi_scroll.pack(fill="both", expand=True)

    def _render_wifi_page(self) -> None:
        p = self.palette
        for c in self.wifi_scroll.winfo_children():
            c.destroy()

        res = self._latest_wifi_res
        ist = self.wifi_optimizer.integration_status()

        # Header & 1-Click Actions
        hdr = ctk.CTkFrame(
            self.wifi_scroll, fg_color=p.bg_card, border_width=1, border_color=p.accent_cyan, corner_radius=12
        )
        hdr.pack(fill="x", pady=(0, 8))
        top_r = ctk.CTkFrame(hdr, fg_color="transparent")
        top_r.pack(fill="x", padx=14, pady=(10, 4))
        ctk.CTkLabel(
            top_r,
            text="Wi-Fi Channel Optimizer — Best Channel Auto-Selector (CachyOS + Fish)",
            font=self.fonts["section"],
            text_color=p.accent_cyan,
        ).pack(side="left")

        ctk.CTkButton(
            top_r,
            text="Restore Defaults",
            height=28,
            corner_radius=7,
            fg_color=p.bg_elevated,
            hover_color=p.border_strong,
            font=self.fonts["caption"],
            command=self._on_restore_wifi,
        ).pack(side="right", padx=(4, 0))
        ctk.CTkButton(
            top_r,
            text="Install CachyOS + Fish Auto-Hooks",
            height=28,
            corner_radius=7,
            fg_color=p.accent_brass,
            hover_color="#B8934F",
            text_color="#120E06",
            font=self.fonts["caption"],
            command=self._on_install_cachyos_fish_wifi,
        ).pack(side="right", padx=4)
        ctk.CTkButton(
            top_r,
            text="Optimize Now",
            height=28,
            corner_radius=7,
            fg_color=p.accent_jade,
            hover_color=p.accent_jade_hover,
            text_color="#071510",
            font=self.fonts["body_bold"],
            command=self._on_optimize_wifi_now,
        ).pack(side="right", padx=4)
        ctk.CTkButton(
            top_r,
            text="Scan Spectrum",
            height=28,
            corner_radius=7,
            fg_color=p.accent_cyan,
            text_color="#071217",
            font=self.fonts["body_bold"],
            command=self._on_scan_wifi_channels,
        ).pack(side="right", padx=4)

        sw_r = ctk.CTkFrame(hdr, fg_color="transparent")
        sw_r.pack(fill="x", padx=14, pady=(2, 8))
        w_auto_var = tk.BooleanVar(value=bool(getattr(self.config_obj, "wifi_auto_optimize", True)))
        ctk.CTkSwitch(
            sw_r,
            text="Auto-Optimize on Connect & Periodic Traffic Checks",
            variable=w_auto_var,
            font=self.fonts["caption"],
            progress_color=p.accent_jade,
            command=lambda: self._on_toggle_wifi_auto(bool(w_auto_var.get())),
        ).pack(side="left", padx=(0, 12))
        w_5g_var = tk.BooleanVar(value=bool(getattr(self.config_obj, "wifi_prefer_5ghz", True)))
        ctk.CTkSwitch(
            sw_r,
            text="Prefer 5 GHz / 6 GHz",
            variable=w_5g_var,
            font=self.fonts["caption"],
            progress_color=p.accent_cyan,
            command=lambda: self._on_toggle_wifi_5ghz(bool(w_5g_var.get())),
        ).pack(side="left", padx=(0, 12))
        w_ps_var = tk.BooleanVar(value=bool(getattr(self.config_obj, "wifi_disable_powersave", True)))
        ctk.CTkSwitch(
            sw_r,
            text="Disable Wi-Fi Power-Save (Low Jitter)",
            variable=w_ps_var,
            font=self.fonts["caption"],
            progress_color=p.accent_brass,
            command=lambda: self._on_toggle_wifi_ps(bool(w_ps_var.get())),
        ).pack(side="left", padx=(0, 12))
        w_ask_var = tk.BooleanVar(value=bool(getattr(self.config_obj, "wifi_prompt_before_apply", True)))
        ctk.CTkSwitch(
            sw_r,
            text="Yes/No Prompt Before Switching",
            variable=w_ask_var,
            font=self.fonts["caption"],
            progress_color=p.accent_cyan,
            command=lambda: self._on_toggle_wifi_prompt(bool(w_ask_var.get())),
        ).pack(side="left")

        rec = self._latest_wifi_rec
        if rec and rec.get("has_recommendation"):
            rec_card = ctk.CTkFrame(
                self.wifi_scroll, fg_color=p.bg_elevated, border_width=1, border_color=p.accent_jade, corner_radius=10
            )
            rec_card.pack(fill="x", pady=(0, 8))
            ctk.CTkLabel(
                rec_card,
                text=f"Recommended Low-Traffic Wi-Fi Channel: {rec['summary']}",
                font=self.fonts["body_bold"],
                text_color=p.accent_jade,
                wraplength=self._content_wrap(260),
                justify="left",
            ).pack(side="left", padx=14, pady=10)
            ctk.CTkButton(
                rec_card,
                text="No",
                width=60,
                height=28,
                fg_color=p.bg_card,
                font=self.fonts["caption"],
                command=self._on_dismiss_wifi_rec,
            ).pack(side="right", padx=(4, 12), pady=8)
            ctk.CTkButton(
                rec_card,
                text="Yes — Apply Channel",
                height=28,
                fg_color=p.accent_jade,
                text_color="#071510",
                font=self.fonts["body_bold"],
                command=self._on_apply_wifi_rec_now,
            ).pack(side="right", padx=4, pady=8)

        if res is None:
            ctk.CTkLabel(
                hdr,
                text=(
                    f"CachyOS / Fish Integration: Fish={'YES' if ist['fish_installed'] else 'Not yet'} · "
                    f"Systemd Timer={'YES' if ist['systemd_timer_installed'] else 'Not yet'} · "
                    f"NM On-Connect Hook={'YES' if ist['nm_dispatcher_installed'] else 'Not yet'}\n"
                    "Click 'Scan Spectrum' or 'Optimize Now' to evaluate 2.4 GHz / 5 GHz / 6 GHz channel congestion."
                ),
                font=self.fonts["small"],
                text_color=p.text_secondary,
                justify="left",
            ).pack(anchor="w", padx=14, pady=(0, 10))
            render_failover_section(self, self.wifi_scroll)
            render_wifi_mesh_section(self, self.wifi_scroll)
            return

        # Live Link Summary + 10m Minimum Channel Hold Status
        min_hold_sec = max(600.0, float(getattr(self.config_obj, "wifi_min_channel_hold_min", 10)) * 60.0)
        hold_st = self.wifi_optimizer.get_channel_hold_status(
            ssid=res.connected_ssid,
            current_channel=res.connected_channel,
            min_hold_sec=min_hold_sec,
        )
        hold_txt = (
            f"Channel Hold Lock: Active ({hold_st['remaining_min_str']} remaining of {int(hold_st['min_hold_sec'] // 60)}m min hold)"
            if hold_st.get("active")
            else f"Channel Hold Lock: Ready (keeps auto-selected channel for min {int(min_hold_sec // 60)}m)"
        )
        perm_txt = (
            "Auto-Select Permitted (switches automatically & holds >= 10m)"
            if getattr(self.config_obj, "consent_wifi_auto_channel", False)
            else "Prompt Mode (grant Wi-Fi permission in Permissions to auto-select)"
        )
        link_txt = (
            f"Interface: {res.interface} · Connected: {res.connected_ssid or 'Not associated'} "
            f"({res.connected_band} Ch {res.connected_channel or '--'} · {res.connected_signal}% signal)\n"
            f"Cleanest Channels -> 2.4 GHz: Ch {res.best_24_channel} | 5 GHz: Ch {res.best_5_channel} | 6 GHz: Ch {res.best_6_channel}\n"
            f"{hold_txt} · {perm_txt}\n"
            f"Status: {res.action_taken}"
        )
        ctk.CTkLabel(
            hdr,
            text=link_txt,
            font=self.fonts["mono_small"],
            text_color=p.accent_jade if res.switched else p.text_secondary,
            justify="left",
            wraplength=self._content_wrap(80),
        ).pack(anchor="w", padx=14, pady=(0, 10))

        # CachyOS + Fish Status Card
        ccard = ctk.CTkFrame(
            self.wifi_scroll, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
        )
        ccard.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(
            ccard,
            text="CachyOS + Fish Shell + Systemd + NetworkManager Integration",
            font=self.fonts["body_bold"],
            text_color=p.accent_brass,
        ).pack(anchor="w", padx=14, pady=(10, 4))
        ctk.CTkLabel(
            ccard,
            text=(
                f"• Fish functions (wifi-best, wifi-scan, wifi-watch, wifi-restore): "
                f"{'Installed (~/.config/fish/conf.d/astracradle_wifi.fish)' if ist['fish_installed'] else 'Click Install CachyOS + Fish Auto-Hooks'}\n"
                f"• Periodic Traffic Watchdog Timer (every {self.config_obj.wifi_check_interval_min}m): "
                f"{'Installed (~/.config/systemd/user/astracradle-wifi-opt.timer)' if ist['systemd_timer_installed'] else 'Pending'}\n"
                f"• NetworkManager On-Connect Auto-Hook: "
                f"{'Active (/etc/NetworkManager/dispatcher.d/90-astracradle-wifi-channel)' if ist['nm_dispatcher_installed'] else 'Generated script ready (click Install CachyOS + Fish Auto-Hooks)'}"
            ),
            font=self.fonts["mono_small"],
            text_color=p.text_secondary,
            justify="left",
        ).pack(anchor="w", padx=14, pady=(0, 10))

        # Channel Scores Card
        scard = ctk.CTkFrame(
            self.wifi_scroll, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
        )
        scard.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(
            scard,
            text="Channel Interference & Congestion Scores (Lower Score = Cleaner Spectrum)",
            font=self.fonts["body_bold"],
            text_color=p.accent_cyan,
        ).pack(anchor="w", padx=14, pady=(10, 4))
        for band_lbl in ("2.4GHz", "5GHz", "6GHz"):
            b_scores = [s for s in res.channel_scores if s.band == band_lbl]
            if band_lbl == "2.4GHz":
                shown = [s for s in b_scores if s.channel in (1, 6, 11) or s.recommended]
            elif band_lbl == "5GHz":
                shown = sorted(b_scores, key=lambda x: x.interference_score)[:8]
            else:
                shown = sorted(b_scores, key=lambda x: x.interference_score)[:4]
            if not shown:
                continue
            row = ctk.CTkFrame(scard, fg_color=p.bg_elevated, corner_radius=8)
            row.pack(fill="x", padx=14, pady=3)
            chips = "  |  ".join(
                f"{'★ ' if s.recommended else ''}Ch {s.channel}: {s.grade} ({s.interference_score:.0f}, {s.ap_count} AP)"
                for s in shown
            )
            ctk.CTkLabel(
                row,
                text=f"[{band_lbl}]  {chips}",
                font=self.fonts["mono_small"],
                text_color=p.text_primary,
                wraplength=self._content_wrap(95),
                justify="left",
                anchor="w",
            ).pack(fill="x", padx=10, pady=6)
        ctk.CTkFrame(scard, height=6, fg_color="transparent").pack()

        # Nearby Access Points Card
        acard = ctk.CTkFrame(
            self.wifi_scroll, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
        )
        acard.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(
            acard,
            text=f"Nearby Wi-Fi Access Points ({len(res.aps)} detected)",
            font=self.fonts["body_bold"],
        ).pack(anchor="w", padx=14, pady=(10, 4))
        if not res.aps:
            ctk.CTkLabel(
                acard,
                text="No wireless interface / nearby APs reported in this environment.",
                font=self.fonts["small"],
                text_color=p.text_muted,
            ).pack(anchor="w", padx=14, pady=(0, 10))
        else:
            for ap in res.aps[:18]:
                ar = ctk.CTkFrame(acard, fg_color=p.bg_elevated, corner_radius=7)
                ar.pack(fill="x", padx=14, pady=2)
                col = p.accent_jade if ap.in_use else (p.accent_cyan if ap.bssid == res.best_bssid_for_ssid else p.text_secondary)
                tag = "ACTIVE" if ap.in_use else ("BEST ALT" if ap.bssid == res.best_bssid_for_ssid else ap.band)
                ctk.CTkLabel(
                    ar,
                    text=f"[{tag}] {ap.ssid} · Ch {ap.channel} ({ap.freq_mhz} MHz) · {ap.signal_pct}% · {ap.bssid}",
                    font=self.fonts["mono_small"],
                    text_color=col,
                    anchor="w",
                ).pack(side="left", padx=10, pady=4)
        ctk.CTkFrame(acard, height=6, fg_color="transparent").pack()
        render_failover_section(self, self.wifi_scroll)
        render_wifi_mesh_section(self, self.wifi_scroll)

    def _is_remote_stream_active(self) -> tuple[bool, str]:
        srv = getattr(self, "_astrabeam_server", None)
        if srv is not None and getattr(srv, "running", False):
            # Active AstraBeam tokens or recent streaming frames/inputs count as live remote stream
            if getattr(srv, "tokens", None) or getattr(srv, "frames_sent", 0) > 0:
                return True, "AstraBeam stream active"
        return self.wifi_optimizer.detect_active_remote_desktop_session()

    def _auto_wifi_optimize_async(self, periodic: bool = False) -> None:
        self._last_wifi_check_ts = time.monotonic()
        stream_active, stream_reason = self._is_remote_stream_active()
        if periodic and stream_active:
            # Never perform off-channel Wi-Fi scans or channel switches during active Remote Desktop / Sunshine streaming
            return
        min_hold_sec = max(600.0, float(getattr(self.config_obj, "wifi_min_channel_hold_min", 10)) * 60.0)
        can_auto_select = bool(
            getattr(self.config_obj, "consent_wifi_auto_channel", False)
            and not getattr(self.config_obj, "wifi_prompt_before_apply", True)
        )

        def _worker() -> None:
            if not can_auto_select:
                rec = self.wifi_optimizer.recommend_channels(
                    prefer_5ghz=bool(getattr(self.config_obj, "wifi_prefer_5ghz", True)),
                    disable_powersave=bool(getattr(self.config_obj, "wifi_disable_powersave", True)),
                    rescan=(not periodic) and (not stream_active),
                    min_hold_sec=min_hold_sec,
                    remote_stream_active=stream_active,
                )
                self._ui_queue.put(("wifi_recommended", rec, periodic))
            else:
                res = self.wifi_optimizer.optimize_connection(
                    dry_run=bool(self.dry_run_var.get()),
                    prefer_5ghz=bool(getattr(self.config_obj, "wifi_prefer_5ghz", True)),
                    disable_powersave=bool(getattr(self.config_obj, "wifi_disable_powersave", True)),
                    rescan=(not periodic) and (not stream_active),
                    min_hold_sec=min_hold_sec,
                    remote_stream_active=stream_active,
                )
                self._ui_queue.put(("wifi_scanned", res, periodic))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_scan_wifi_channels(self) -> None:
        self.begin_task_progress("wifi", "Scanning 2.4 / 5 / 6 GHz Wi-Fi channels for congestion...")
        min_hold_sec = max(600.0, float(getattr(self.config_obj, "wifi_min_channel_hold_min", 10)) * 60.0)
        stream_active, _ = self._is_remote_stream_active()

        def _worker() -> None:
            rec = self.wifi_optimizer.recommend_channels(
                prefer_5ghz=bool(getattr(self.config_obj, "wifi_prefer_5ghz", True)),
                disable_powersave=bool(getattr(self.config_obj, "wifi_disable_powersave", True)),
                rescan=(not stream_active),
                min_hold_sec=min_hold_sec,
                remote_stream_active=stream_active,
            )
            self._ui_queue.put(("wifi_recommended", rec, False))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_optimize_wifi_now(self) -> None:
        self.begin_task_progress("wifi", "Checking Wi-Fi spectrum for less-trafficked channels...")
        self._auto_wifi_optimize_async(periodic=False)

    def _on_apply_wifi_rec_now(self) -> None:
        self._latest_wifi_rec = None
        min_hold_sec = max(600.0, float(getattr(self.config_obj, "wifi_min_channel_hold_min", 10)) * 60.0)
        res = self.wifi_optimizer.optimize_connection(
            dry_run=bool(self.dry_run_var.get()),
            prefer_5ghz=bool(getattr(self.config_obj, "wifi_prefer_5ghz", True)),
            disable_powersave=bool(getattr(self.config_obj, "wifi_disable_powersave", True)),
            rescan=False,
            min_hold_sec=min_hold_sec,
        )
        self._latest_wifi_res = res
        self.show_toast(res.action_taken)
        self._render_wifi_page()

    def _on_dismiss_wifi_rec(self) -> None:
        self._latest_wifi_rec = None
        self.show_toast("Kept current Wi-Fi channel/BSSID.")
        self._render_wifi_page()

    def _on_toggle_wifi_prompt(self, enable: bool) -> None:
        self.config_obj.wifi_prompt_before_apply = bool(enable)
        if not enable:
            self.config_obj.consent_wifi_auto_channel = True
            self.config_obj.wifi_min_channel_hold_min = max(10, int(getattr(self.config_obj, "wifi_min_channel_hold_min", 10)))
        self.cfg_mgr.save(self.config_obj)
        self.show_toast(
            "Wi-Fi Yes/No Prompt ENABLED."
            if enable
            else "Wi-Fi Auto-Select ENABLED (keeps chosen channel for at least 10 minutes)."
        )

    def _on_install_cachyos_fish_wifi(self) -> None:
        ok, items = self.wifi_optimizer.install_cachyos_fish_integration(
            install_dispatcher=True,
            enable_timer=True,
            interval_min=int(getattr(self.config_obj, "wifi_check_interval_min", 10)),
            elevation=self.elevation if self._show_startup_popups else None,
        )
        if ok:
            self.updater.record_install_memory(
                cachyos_fish_wifi_hooks=True,
                step_note="Installed CachyOS + Fish Wi-Fi auto-hooks",
                config=self.config_obj,
            )
            self._refresh_install_memory_label()
        self.show_toast(
            f"Installed {len(items)} CachyOS + Fish Wi-Fi optimizer component(s)!" if ok else "Wi-Fi hook install finished with notes."
        )
        self._render_wifi_page()

    def _on_restore_wifi(self) -> None:
        _, msg = self.wifi_optimizer.restore_wifi_settings()
        self.show_toast(msg)
        self._render_wifi_page()

    def _on_toggle_wifi_auto(self, enable: bool) -> None:
        self.config_obj.wifi_auto_optimize = bool(enable)
        self.cfg_mgr.save(self.config_obj)
        self.show_toast(f"Wi-Fi Auto-Optimize {'ENABLED' if enable else 'DISABLED'}.")

    def _on_toggle_wifi_5ghz(self, enable: bool) -> None:
        self.config_obj.wifi_prefer_5ghz = bool(enable)
        self.cfg_mgr.save(self.config_obj)

    def _on_save_failover_settings(
        self,
        enabled: bool,
        seamless: bool,
        primary: str,
        backup: str,
        targets_str: str,
        interval_str: str,
        timeout_str: str,
        max_lat_str: str,
        fail_thresh_str: str,
        min_hold_str: str,
        rec_stable_str: str,
    ) -> None:
        self.config_obj.failover_enabled = bool(enabled)
        self.config_obj.failover_seamless_switch = bool(seamless)
        self.config_obj.failover_primary_interface = primary.strip() or "auto"
        self.config_obj.failover_backup_interface = backup.strip() or "auto"
        targets = [t.strip() for t in targets_str.split(",") if t.strip()]
        self.config_obj.failover_ping_targets = targets or ["1.1.1.1", "8.8.8.8"]
        try:
            self.config_obj.failover_ping_interval_sec = max(2.0, float(interval_str.strip()))
        except Exception:
            pass
        try:
            self.config_obj.failover_ping_timeout_sec = max(0.5, float(timeout_str.strip()))
        except Exception:
            pass
        try:
            self.config_obj.failover_max_latency_ms = max(10.0, float(max_lat_str.strip()))
        except Exception:
            pass
        try:
            self.config_obj.failover_failure_threshold = max(1, int(fail_thresh_str.strip()))
        except Exception:
            pass
        try:
            self.config_obj.failover_min_hold_sec = max(0.0, float(min_hold_str.strip()))
        except Exception:
            pass
        try:
            self.config_obj.failover_recovery_stable_sec = max(5.0, float(rec_stable_str.strip()))
        except Exception:
            pass
        self.cfg_mgr.save(self.config_obj)
        self.show_toast("Saved custom network failover & recovery settings.", color=self.palette.accent_jade)
        self._render_wifi_page()

    def _on_probe_failover_now(self) -> None:
        snap = self.network_failover.evaluate_step(self.config_obj)
        self.show_toast(
            f"Failover Probe: {snap.state} ({snap.active_iface}) — {snap.last_probe_reason}",
            color=self.palette.accent_jade if snap.last_probe_ok else self.palette.accent_brass,
        )
        self._render_wifi_page()

    def _on_force_failover_test(self) -> None:
        snap = self.network_failover.force_failover(self.config_obj)
        self.show_toast(f"Failover active -> {snap.active_iface}", color=self.palette.accent_brass)
        self._render_wifi_page()

    def _on_force_recover_primary(self) -> None:
        snap = self.network_failover.force_recover(self.config_obj)
        self.show_toast(f"Primary restored -> {snap.active_iface}", color=self.palette.accent_jade)
        self._render_wifi_page()

    def _on_add_custom_failover_pair(
        self, name: str, primary: str, backup: str, interval_str: str, recovery_str: str
    ) -> None:
        try:
            intv = float(interval_str.strip() or 5)
        except Exception:
            intv = 5.0
        try:
            rec = float(recovery_str.strip() or 300)
        except Exception:
            rec = 300.0
        prof = self.network_failover.add_custom_profile(
            self.config_obj, name=name, primary=primary, backup=backup, interval_sec=intv, recovery_sec=rec
        )
        self.show_toast(f"Added custom failover pair '{prof['name']}'.", color=self.palette.accent_jade)
        self._render_wifi_page()

    def _on_remove_custom_failover_pair(self, name: str) -> None:
        self.network_failover.remove_custom_profile(self.config_obj, name)
        self.show_toast(f"Removed failover pair '{name}'.")
        self._render_wifi_page()
        self.show_toast(f"Prefer 5/6 GHz {'ENABLED' if enable else 'DISABLED'}.")

    def _on_toggle_wifi_ps(self, enable: bool) -> None:
        self.config_obj.wifi_disable_powersave = bool(enable)
        self.cfg_mgr.save(self.config_obj)
        self.show_toast(f"Disable Wi-Fi Power-Save {'ENABLED' if enable else 'DISABLED'}.")

    # ------------------------------------------------------------------
    # Special Category: Full Multi-Drive File & Program Scanner + Known-Issues DB
    # ------------------------------------------------------------------
    def _build_scanner_page(self) -> None:
        self.scanner_scroll = ctk.CTkScrollableFrame(self.pages["scanner"], fg_color="transparent")
        self.scanner_scroll.pack(fill="both", expand=True)

    def _render_scanner_page(self) -> None:
        p = self.palette
        for c in self.scanner_scroll.winfo_children():
            c.destroy()

        stats = self.catalog_scanner.get_catalog_stats()
        drives = self.catalog_scanner.list_all_drives()
        summ = self._latest_catalog_summary

        # Header & 1-Click Actions
        hdr = ctk.CTkFrame(
            self.scanner_scroll, fg_color=p.bg_card, border_width=1, border_color=p.accent_cyan, corner_radius=12
        )
        hdr.pack(fill="x", pady=(0, 8))
        top_r = ctk.CTkFrame(hdr, fg_color="transparent")
        top_r.pack(fill="x", padx=14, pady=(10, 4))
        ctk.CTkLabel(
            top_r,
            text="System Scanner, Multi-Drive File Catalog & Known-Issues Intelligence DB",
            font=self.fonts["section"],
            text_color=p.accent_cyan,
        ).pack(side="left")

        ctk.CTkButton(
            top_r,
            text="Quiet Auto-Fix Safe",
            height=28,
            corner_radius=7,
            fg_color=p.accent_jade,
            hover_color=p.accent_jade_hover,
            text_color="#071510",
            font=self.fonts["body_bold"],
            command=self._on_autofix_catalog_safe,
        ).pack(side="right", padx=(4, 0))
        ctk.CTkButton(
            top_r,
            text="Passive Check Now",
            height=28,
            corner_radius=7,
            fg_color=p.bg_elevated,
            hover_color=p.border_strong,
            font=self.fonts["caption"],
            command=self._on_run_passive_catalog_batch,
        ).pack(side="right", padx=4)
        ctk.CTkButton(
            top_r,
            text="Scan All Drives & Programs",
            height=28,
            corner_radius=7,
            fg_color=p.accent_cyan,
            text_color="#071217",
            font=self.fonts["body_bold"],
            command=self._on_run_full_catalog_scan,
        ).pack(side="right", padx=4)

        # Native Built-In Settings inside Scanner Category
        s_row = ctk.CTkFrame(hdr, fg_color="transparent")
        s_row.pack(fill="x", padx=14, pady=(4, 8))
        cat_passive_var = tk.BooleanVar(value=bool(getattr(self.config_obj, "catalog_passive_scan", True)))
        ctk.CTkSwitch(
            s_row,
            text="Passive Background File Scanner",
            variable=cat_passive_var,
            font=self.fonts["caption"],
            progress_color=p.accent_jade,
            command=lambda: self._on_toggle_scanner_setting("catalog_passive_scan", bool(cat_passive_var.get()), "Passive File Scanner"),
        ).pack(side="left", padx=(0, 10))
        cat_autofix_var = tk.BooleanVar(value=bool(getattr(self.config_obj, "catalog_quiet_autofix", True)))
        ctk.CTkSwitch(
            s_row,
            text="Quietly Auto-Fix Safe Bugs",
            variable=cat_autofix_var,
            font=self.fonts["caption"],
            progress_color=p.accent_cyan,
            command=lambda: self._on_toggle_scanner_setting("catalog_quiet_autofix", bool(cat_autofix_var.get()), "Quiet Auto-Fix"),
        ).pack(side="left", padx=(0, 10))
        cat_newapp_var = tk.BooleanVar(value=bool(getattr(self.config_obj, "catalog_watch_new_apps", True)))
        ctk.CTkSwitch(
            s_row,
            text="Auto-Catalog Newly Installed Apps",
            variable=cat_newapp_var,
            font=self.fonts["caption"],
            progress_color=p.accent_brass,
            command=lambda: self._on_toggle_scanner_setting("catalog_watch_new_apps", bool(cat_newapp_var.get()), "New-App Watcher"),
        ).pack(side="left", padx=(0, 10))

        throt_var = tk.StringVar(value=str(getattr(self.config_obj, "catalog_throttle_mode", "Balanced")))
        ctk.CTkOptionMenu(
            s_row,
            values=["Low", "Balanced", "Fast"],
            variable=throt_var,
            width=100,
            height=24,
            font=self.fonts["caption"],
            command=lambda val: self._on_toggle_scanner_setting("catalog_throttle_mode", val, "Passive Scan Cadence"),
        ).pack(side="right")
        ctk.CTkLabel(s_row, text="Pace:", font=self.fonts["caption"], text_color=p.text_muted).pack(side="right", padx=(4, 4))

        ctk.CTkLabel(
            hdr,
            text=(
                f"Drives Detected ({len(drives)}): {', '.join(str(d) for d in drives[:6])}\n"
                f"Catalog DB: {stats['files_cataloged']:,} files · {stats['programs_cataloged']:,} programs · "
                f"{stats['known_rules_count']} Known Issue Rules · {stats['total_fixes_applied']} quiet fixes applied · "
                f"Open Issues: {stats['open_file_issues']}"
            ),
            font=self.fonts["mono_small"],
            text_color=p.text_secondary,
            justify="left",
            wraplength=self._content_wrap(80),
        ).pack(anchor="w", padx=14, pady=(0, 10))

        # Newly Installed Programs Banner
        new_apps = (summ.newly_installed_programs if summ else None) or stats.get("new_programs") or []
        if new_apps:
            ncard = ctk.CTkFrame(
                self.scanner_scroll, fg_color=p.bg_elevated, border_width=1, border_color=p.accent_brass, corner_radius=10
            )
            ncard.pack(fill="x", pady=(0, 8))
            ctk.CTkLabel(
                ncard,
                text=f"Newly Installed Programs Cataloged ({len(new_apps)}): {', '.join(new_apps[:8])}",
                font=self.fonts["body_bold"],
                text_color=p.accent_brass,
                wraplength=self._content_wrap(90),
                justify="left",
            ).pack(anchor="w", padx=14, pady=8)

        # Detected Issues & 1-Click Yes/No Fix
        icard = ctk.CTkFrame(
            self.scanner_scroll, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
        )
        icard.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(
            icard,
            text="Detected File, Config & Program Issues",
            font=self.fonts["body_bold"],
            text_color=p.accent_cyan,
        ).pack(anchor="w", padx=14, pady=(10, 4))
        issues = summ.issues_found if summ else []
        if not issues:
            ctk.CTkLabel(
                icard,
                text="Zero open issues in latest pass. Click 'Scan All Drives & Programs' to refresh full multi-drive catalog.",
                font=self.fonts["small"],
                text_color=p.text_muted,
            ).pack(anchor="w", padx=14, pady=(0, 10))
        else:
            for item in issues[:16]:
                row = ctk.CTkFrame(icard, fg_color=p.bg_elevated, corner_radius=8)
                row.pack(fill="x", padx=14, pady=2)
                col = p.accent_jade if item.status in ("fixed", "auto_fixed") else p.accent_brass
                ctk.CTkLabel(
                    row,
                    text=f"[{item.status.upper()} · {int(item.confidence * 100)}%] {item.title}: {item.target_path}",
                    font=self.fonts["mono_small"],
                    text_color=col,
                    wraplength=self._content_wrap(240),
                    justify="left",
                    anchor="w",
                ).pack(side="left", padx=10, pady=5)
                if item.status == "open":
                    ctk.CTkButton(
                        row,
                        text="Fix (Yes/No)",
                        width=96,
                        height=22,
                        fg_color=p.accent_jade,
                        text_color="#071510",
                        font=self.fonts["caption"],
                        command=lambda it=item: self._on_fix_single_catalog_issue(it),
                    ).pack(side="right", padx=8, pady=4)
        ctk.CTkFrame(icard, height=6, fg_color="transparent").pack()

        # Built-In Known Bugs & Issues Intelligence Database Card
        db_card = ctk.CTkFrame(
            self.scanner_scroll, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
        )
        db_card.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(
            db_card,
            text=f"Built-In Known Bugs & Issues Database ({len(KNOWN_ISSUES_DB)} Intelligence Signatures)",
            font=self.fonts["body_bold"],
            text_color=p.accent_jade,
        ).pack(anchor="w", padx=14, pady=(10, 4))
        for rule in KNOWN_ISSUES_DB:
            rrow = ctk.CTkFrame(db_card, fg_color=p.bg_elevated, corner_radius=7)
            rrow.pack(fill="x", padx=14, pady=2)
            ctk.CTkLabel(
                rrow,
                text=f"[{rule.category.upper()} · {rule.safety_tier}] {rule.title} — {rule.explanation}",
                font=self.fonts["small"],
                text_color=p.text_secondary,
                wraplength=self._content_wrap(110),
                justify="left",
                anchor="w",
            ).pack(fill="x", padx=10, pady=4)
        ctk.CTkFrame(db_card, height=6, fg_color="transparent").pack()

    def _on_toggle_scanner_setting(self, attr: str, value, label: str) -> None:
        setattr(self.config_obj, attr, value)
        self.cfg_mgr.save(self.config_obj)
        self.show_toast(f"{label} -> {value}")

    def _on_run_full_catalog_scan(self) -> None:
        self.begin_task_progress("catalog", "Scanning all mounted drives & programs into SQLite catalog...")
        dry = bool(self.dry_run_var.get())
        quiet_fix = bool(getattr(self.config_obj, "catalog_quiet_autofix", True))
        adapter = self._active_adapter()

        def _worker() -> None:
            summ = self.catalog_scanner.run_full_scan(
                adapter,
                self.config_obj,
                quiet_autofix=quiet_fix,
                dry_run=dry,
            )
            self._ui_queue.put(("catalog_scanned", summ, False))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_run_passive_catalog_batch(self) -> None:
        self._last_passive_scan_ts = time.monotonic()
        dry = bool(self.dry_run_var.get())
        quiet_fix = bool(getattr(self.config_obj, "catalog_quiet_autofix", True))
        watch_new = bool(getattr(self.config_obj, "catalog_watch_new_apps", True))
        adapter = self._active_adapter()

        def _worker() -> None:
            summ = self.catalog_scanner.run_passive_batch(
                adapter,
                self.config_obj,
                batch_size=140,
                quiet_autofix=quiet_fix,
                check_new_programs=watch_new,
                dry_run=dry,
            )
            self._ui_queue.put(("catalog_scanned", summ, True))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_autofix_catalog_safe(self) -> None:
        if not self.confirm_yes_no(
            "Quiet Auto-Fix Safe Catalog Issues",
            "Automatically quarantine or repair all safe known file/script issues found?",
            [
                "Broken symlinks, malformed JSON caches, stale lockfiles, and script permission hygiene.",
                "Every modified file is safely preserved inside Quarantine Vault for instant undo.",
            ],
            yes_label="Yes, Fix All Safe Issues",
            no_label="No, Cancel",
        ):
            self.show_toast("Cancelled auto-fix.")
            return
        adapter = self._active_adapter()
        summ = self._latest_catalog_summary
        fixed = 0
        if summ and summ.issues_found:
            for it in summ.issues_found:
                if it.status == "open" and it.safety_tier == "safe_auto":
                    ok, _ = self.catalog_scanner.apply_issue_fix(
                        it, adapter, self.config_obj, dry_run=bool(self.dry_run_var.get())
                    )
                    if ok:
                        fixed += 1
        self.show_toast(f"Applied {fixed} reversible catalog fix(es).")
        self._render_scanner_page()

    def _on_fix_single_catalog_issue(self, item) -> None:
        if not self.confirm_yes_no(
            f"Fix Issue: {item.title}",
            f"Apply '{item.fix_action}' to {item.target_path}?",
            [item.explanation, f"Safety tier: {item.safety_tier} · Confidence: {int(item.confidence * 100)}%"],
            yes_label="Yes, Fix Issue",
            no_label="No, Leave Untouched",
        ):
            return
        ok, msg = self.catalog_scanner.apply_issue_fix(
            item, self._active_adapter(), self.config_obj, dry_run=bool(self.dry_run_var.get())
        )
        self.show_toast(msg, color=self.palette.accent_jade if ok else self.palette.accent_brass)
        self._render_scanner_page()

    # ------------------------------------------------------------------
    # Special Category: Bandwidth & RF Radio-Wave Spectrum Visualizer
    # ------------------------------------------------------------------
    def _build_rf_waves_page(self) -> None:
        self.rf_waves_scroll = ctk.CTkScrollableFrame(self.pages["rf_waves"], fg_color="transparent")
        self.rf_waves_scroll.pack(fill="both", expand=True)

    def _render_rf_waves_page(self) -> None:
        render_rf_waves_page(self)

    def _build_remote_beam_page(self) -> None:
        self.remote_beam_scroll = ctk.CTkScrollableFrame(self.pages["remote_beam"], fg_color="transparent")
        self.remote_beam_scroll.pack(fill="both", expand=True)

    def _prompt_hermes_sudo_from_worker(self, title: str, reason: str) -> str | None:
        """Thread-safe prompt that opens SudoPasswordDialog on the main UI thread when Hermes needs sudo."""
        if threading.current_thread() is threading.main_thread():
            return self._ask_sudo_password(title, reason)
        done = threading.Event()
        holder: dict[str, str | None] = {"pw": None}

        def _ask_on_ui() -> None:
            try:
                holder["pw"] = self._ask_sudo_password(title, reason)
            except Exception:
                holder["pw"] = None
            finally:
                done.set()

        self.after(0, _ask_on_ui)
        done.wait(timeout=180.0)
        return holder["pw"]

    def _render_remote_beam_page(self) -> None:
        render_remote_beam_page(self)

    def _build_hermes_page(self) -> None:
        self.hermes_scroll = ctk.CTkScrollableFrame(self.pages["hermes"], fg_color="transparent")
        self.hermes_scroll.pack(fill="both", expand=True)

    def _auto_integrate_hermes_gpu_async(self) -> None:
        def _worker() -> None:
            try:
                res = self.hermes_mgr.auto_integrate_downloaded_gpu_model(self.config_obj, start_runtime=True)
                self._hermes_status_cache = self.hermes_mgr.get_status(self.config_obj)
                if res.get("integrated"):
                    self.after(
                        0,
                        lambda m=res.get("model", ""): (
                            self.show_toast(f"Hermes AI auto-integrated local GPU model ({m}) — ready offline with zero re-download."),
                            render_hermes_page(self) if self.current_nav == "hermes" else None,
                        ),
                    )
            except Exception:
                pass

        threading.Thread(target=_worker, daemon=True).start()

    def _render_hermes_page(self) -> None:
        render_hermes_page(self)

    def open_ai_assistant_drawer(self) -> None:
        """Opens a quick chatbot-style Action Assistant popup from any page in AstraCradle."""
        from .pages.hermes_page import open_quick_assistant_modal

        open_quick_assistant_modal(self)

    def _build_cmd_vault_page(self) -> None:
        self.cmd_vault_scroll = ctk.CTkScrollableFrame(self.pages["cmd_vault"], fg_color="transparent")
        self.cmd_vault_scroll.pack(fill="both", expand=True)

    def _render_cmd_vault_page(self) -> None:
        render_command_vault_page(self)

    def _on_cmd_vault_filter(self, category: str, search: str) -> None:
        self._cmd_vault_cat = category
        self._cmd_vault_search = search
        self._render_cmd_vault_page()

    def _on_copy_vault_command(self, cmd_id: str, script: str, title: str) -> None:
        try:
            self.clipboard_clear()
            self.clipboard_append(script)
            self.update()
        except Exception:
            pass
        self.command_db.mark_copied(cmd_id)
        self.show_toast(f"Copied '{title}' to clipboard.", color=self.palette.accent_jade)
        if self.current_nav == "cmd_vault":
            self._render_cmd_vault_page()

    def _on_add_vault_command(self, title: str, category: str, platform_name: str, script: str, desc: str) -> None:
        try:
            rec = self.command_db.add_command(title, category, platform_name, script, desc)
            self._cmd_vault_selected_id = rec.cmd_id
            self._cmd_vault_cat = "All"
            self._cmd_vault_search = ""
            self.show_toast(f"Saved command '{rec.title}' to SQLite database.", color=self.palette.accent_jade)
            self._render_cmd_vault_page()
        except Exception as exc:
            self.show_toast(str(exc), color=self.palette.accent_coral)

    def _on_delete_vault_command(self, cmd_id: str) -> None:
        ok, msg = self.command_db.delete_command(cmd_id)
        self.show_toast(msg, color=self.palette.accent_jade if ok else self.palette.accent_coral)
        self._render_cmd_vault_page()

    def _on_copy_text_only(self, text: str, label: str) -> None:
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.update()
        except Exception:
            pass
        self.show_toast(f"Copied {label} to clipboard.", color=self.palette.accent_jade)

    def _on_toggle_dep_install_consent(self, allowed: bool) -> None:
        self.config_obj.consent_auto_install_deps = bool(allowed)
        self.cfg_mgr.save(self.config_obj)
        self.show_toast(
            "Auto-install permission GRANTED for companion programs." if allowed else "Auto-install permission disabled.",
            color=self.palette.accent_jade if allowed else self.palette.accent_brass,
        )

    def _on_install_single_dep(self, dep_id: str, shell_kind: str = "bash") -> None:
        permitted = bool(getattr(self.config_obj, "consent_auto_install_deps", False))
        if not permitted:
            self.show_toast("Enable the permission checkbox first to install external programs.", color=self.palette.accent_coral)
            return
        self.begin_task_progress("dep_install", f"Installing companion tool '{dep_id}'...")

        def _worker() -> None:
            ts = getattr(self, "_latest_tailscale", None) or TailscaleDiscovery.get_status()
            res = self.dep_lib.install_and_setup(
                dep_id, permitted=True, shell_kind=shell_kind, dry_run=bool(self.config_obj.dry_run_default), tailscale_ip=ts.self_ip
            )
            self._ui_queue.put(("dep_install_done", res.message, self.palette.accent_jade if res.ok else self.palette.accent_coral))
            self._ui_queue.put(("refresh_cmd_vault", None, None))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_install_all_missing_deps(self, shell_kind: str = "bash") -> None:
        permitted = bool(getattr(self.config_obj, "consent_auto_install_deps", False))
        if not permitted:
            self.show_toast("Enable the permission checkbox first to auto-install missing dependencies.", color=self.palette.accent_coral)
            return
        self.begin_task_progress("dep_install", "Installing missing companion dependencies...")

        def _worker() -> None:
            ts = getattr(self, "_latest_tailscale", None) or TailscaleDiscovery.get_status()
            outs = self.dep_lib.install_all_missing(
                permitted=True, shell_kind=shell_kind, dry_run=bool(self.config_obj.dry_run_default), tailscale_ip=ts.self_ip
            )
            ok_cnt = sum(1 for o in outs if o.ok)
            msg = f"Dependency auto-setup finished: {ok_cnt}/{len(outs)} processed." if outs else "All companion dependencies are already installed!"
            self._ui_queue.put(("dep_install_done", msg, self.palette.accent_jade))
            self._ui_queue.put(("refresh_cmd_vault", None, None))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_vault_help_filter(self, category: str, query: str) -> None:
        self._vault_help_cat = category
        self._vault_help_q = query
        self._render_cmd_vault_page()

    def _on_toggle_proximity_flags(self, auto_prox: bool, bt_fb: bool) -> None:
        self.config_obj.auto_connect_proximity_devices = bool(auto_prox)
        self.config_obj.bluetooth_handshake_fallback = bool(bt_fb)
        self.cfg_mgr.save(self.config_obj)
        self.show_toast("Updated proximity auto-connect & Bluetooth fallback preferences.")

    def _on_auto_connect_proximity_now(self) -> None:
        ts = getattr(self, "_latest_tailscale", None) or TailscaleDiscovery.get_status()
        outcomes = self.mobile_companion.auto_connect_proximity_devices(self.config_obj, ts_status=ts)
        self._last_proximity_outcomes = outcomes
        conn_cnt = sum(1 for o in outcomes if o.connected)
        self.show_toast(
            f"Proximity scan complete: {conn_cnt}/{len(outcomes)} paired/designated device(s) connected.",
            color=self.palette.accent_jade if conn_cnt else self.palette.accent_brass,
        )
        if self.current_nav == "remote_beam":
            self._render_remote_beam_page()

    def _on_copy_ios_watch_shortcut(self) -> None:
        ts = getattr(self, "_latest_tailscale", None) or TailscaleDiscovery.get_status()
        srv = getattr(self, "_astrabeam_server", None)
        scheme = srv.scheme if srv else ("https" if self.config_obj.astrabeam_tls_enabled else "http")
        host = ts.self_ip or "100.64.0.1"
        port = srv.port if srv else self.config_obj.astrabeam_port
        bundle = self.mobile_companion.build_ios_watch_shortcut_bundle(f"{scheme}://{host}:{port}")
        txt = f"Watch: {bundle['watch_dashboard_url']}\nPWA: {bundle['ios_pwa_url']}\n{bundle['curl_fix_safe']}"
        try:
            self.clipboard_clear()
            self.clipboard_append(txt)
            self.update()
        except Exception:
            pass
        self.show_toast("Copied iPhone & Apple Watch URL + Shortcut curl commands!", color=self.palette.accent_jade)

    def _on_setup_android_adb_bridge(self, serial: str = "") -> None:
        ok, msg = self.mobile_companion.setup_android_adb_bridge(serial, int(self.config_obj.astrabeam_port))
        self.show_toast(msg, color=self.palette.accent_jade if ok else self.palette.accent_coral)

    def _on_register_designated_device(
        self,
        preset_name: str,
        name: str,
        host: str,
        mac: str,
        adb_serial: str,
        port_str: str,
        prog_cmd: str,
        auto_conn: bool,
    ) -> None:
        from ..core.mobile_continuity import DEVICE_PRESETS

        spec = DEVICE_PRESETS.get(preset_name) or {}
        try:
            port = int(port_str.strip()) if port_str.strip() else int(spec.get("port", 47990))
        except Exception:
            port = 47990
        try:
            dev = self.device_registry.register_device(
                name=name,
                device_type=str(spec.get("device_type", "Handheld / VR / New Device")),
                os_family=str(spec.get("os_family", "Other")),
                network_host=host,
                bluetooth_mac=mac,
                adb_serial=adb_serial,
                port=port,
                program_command=prog_cmd or str(spec.get("program_command", "")),
                preferred_transport=str(spec.get("preferred_transport", "auto")),
                auto_connect=bool(auto_conn),
                notes=str(spec.get("notes", "")),
            )
            self.show_toast(f"Registered '{dev.name}' ({dev.device_type}).", color=self.palette.accent_jade)
            self._render_remote_beam_page()
        except Exception as exc:
            self.show_toast(str(exc), color=self.palette.accent_coral)

    def _on_remove_designated_device(self, device_id: str) -> None:
        ok, msg = self.device_registry.remove_device(device_id)
        self.show_toast(msg, color=self.palette.accent_jade if ok else self.palette.accent_coral)
        self._render_remote_beam_page()

    def _on_launch_designated_program(self, device_id: str) -> None:
        ok, msg = self.device_registry.launch_designated_program(device_id)
        self.show_toast(msg, color=self.palette.accent_jade if ok else self.palette.accent_coral)

    def _on_refresh_tailscale(self) -> None:
        self.begin_task_progress("tailscale", "Scanning Tailscale mesh peers & Sunshine/Moonlight status...")

        def _worker() -> None:
            ts = TailscaleDiscovery.get_status()
            self._ui_queue.put(("tailscale_refreshed", ts))

        threading.Thread(target=_worker, daemon=True).start()

    def _watch_summary_payload(self) -> dict[str, object]:
        snap = self.latest_snapshot
        rep = self.latest_report
        sensors = self._latest_sensors or {}
        ts = getattr(self, "_latest_tailscale", None)
        wres = getattr(self, "_latest_wifi_res", None)
        sm = SunshineMoonlightBridge.inspect(ts)
        return {
            "hostname": snap.hostname if snap else platform.node(),
            "health_score": rep.health_score if rep else 94,
            "cpu_percent": round(snap.cpu_percent, 1) if snap else 0.0,
            "mem_percent": round(snap.mem_percent, 1) if snap else 0.0,
            "cpu_temp_c": self._num_or_none(sensors.get("cpu_temp_c")) or self._num_or_none(snap.temperature_c if snap else None),
            "gpu_temp_c": self._num_or_none(sensors.get("gpu_temp_c")),
            "active_profile": self.config_obj.active_profile,
            "astrashield_running": bool(self.adblock_shield.dns_running),
            "sunshine_running": bool(sm.sunshine_running),
            "tailscale_ip": ts.self_ip if ts else "",
            "wifi_ssid": wres.connected_ssid if wres else "",
            "wifi_channel": wres.connected_channel if wres else 0,
        }

    def _watch_action_dispatch(self, action: str, value: str) -> tuple[bool, str]:
        if not getattr(self.config_obj, "astrabeam_watch_actions_enabled", True):
            return False, "Watch actions disabled in config."
        if action == "fix_safe":
            self._ui_queue.put(("fix_safe", None, None))
            return True, "Queued 1-Click Fix Everything Safe on PC."
        if action == "deep_scan":
            self._ui_queue.put(("trigger_scan", None, None))
            return True, "Queued Deep Scan on PC."
        if action == "optimize_wifi":
            self._auto_wifi_optimize_async(periodic=False)
            return True, "Triggered Wi-Fi Channel Optimizer."
        if action == "restart_sunshine":
            _, msg_s = SunshineMoonlightBridge.start_or_restart_sunshine()
            return True, msg_s
        if action == "switch_profile" and value in SITUATION_PROFILES:
            self._ui_queue.put(("switch_profile", value, None))
            return True, f"Switched PC profile -> {value}."
        if action == "pause_shield_5m":
            self._ui_queue.put(("pause_shield_5m", None, None))
            return True, "Paused AstraShield for 5 minutes."
        if action == "toggle_shield":
            self._ui_queue.put(("toggle_astrashield", None, None))
            return True, "Toggled AstraShield."
        return False, f"Unknown watch action '{action}'."

    def _on_toggle_astrabeam_host(
        self, port_str: str, fps_str: str, pin_str: str, ts_only: bool, tls_enabled: bool = True
    ) -> None:
        srv = getattr(self, "_astrabeam_server", None)
        if srv is not None and srv.running:
            _, msg = srv.stop()
            self._astrabeam_server = None
            self.show_toast(msg)
            self._render_remote_beam_page()
            return
        try:
            port = max(1024, min(65535, int(port_str.strip())))
        except Exception:
            port = 47992
        shifted_for_sunshine = False
        if port == 47990 and SunshineMoonlightBridge.inspect().sunshine_installed:
            port = 47992
            shifted_for_sunshine = True
        try:
            fps = max(5, min(120, int(fps_str.strip())))
        except Exception:
            fps = 30
        ts = getattr(self, "_latest_tailscale", None) or TailscaleDiscovery.get_status()
        bind_ip = ts.self_ip if (ts_only and ts.running and ts.self_ip) else "0.0.0.0"
        self.config_obj.astrabeam_port = port
        self.config_obj.astrabeam_fps = fps
        self.config_obj.astrabeam_tailscale_only = bool(ts_only)
        self.config_obj.astrabeam_tls_enabled = bool(tls_enabled)
        self.config_obj.astrabeam_pin = pin_str.strip()
        self.cfg_mgr.save(self.config_obj)
        srv = AstraBeamServer(
            self.journal,
            host=bind_ip,
            port=port,
            pin=pin_str.strip(),
            fps=fps,
            quality=int(getattr(self.config_obj, "astrabeam_quality", 72)),
            scale_pct=int(getattr(self.config_obj, "astrabeam_scale_pct", 85)),
            tailscale_only=bool(ts_only),
            tls_enabled=bool(tls_enabled),
            cert_dir=self.cfg_mgr.base_dir / "certs",
            lockout_seconds=int(getattr(self.config_obj, "astrabeam_lockout_seconds", 300)),
            watch_summary_provider=self._watch_summary_payload,
            watch_action_handler=self._watch_action_dispatch,
        )
        ok, msg = srv.start()
        if ok:
            self._astrabeam_server = srv
            if shifted_for_sunshine:
                msg = f"{msg} (Auto-shifted to port 47992 so native Sunshine keeps :47990)"
        self.show_toast(msg, color=self.palette.accent_jade if ok else self.palette.accent_coral)
        self._render_remote_beam_page()

    def _on_revoke_astrabeam_sessions(self) -> None:
        srv = getattr(self, "_astrabeam_server", None)
        if srv is not None:
            cnt = srv.revoke_all_tokens()
            self.show_toast(f"Revoked {cnt} active AstraBeam mobile/desktop session token(s).")

    def _on_apply_sunshine_tailscale_conf(self) -> None:
        ts = getattr(self, "_latest_tailscale", None) or TailscaleDiscovery.get_status()
        ok, msg = SunshineMoonlightBridge.apply_hardened_tailscale_config(ts.self_ip)
        self.show_toast(msg, color=self.palette.accent_jade if ok else self.palette.accent_coral)

    def _on_optimize_sunshine_ipad(self) -> None:
        ts = getattr(self, "_latest_tailscale", None) or TailscaleDiscovery.get_status()
        ok, msg = SunshineMoonlightBridge.optimize_sunshine_for_tablet(ts.self_ip)
        if ok:
            ok_r, msg_r = SunshineMoonlightBridge.start_or_restart_sunshine()
            if ok_r:
                msg = f"{msg} — Sunshine restarted."
            else:
                msg = f"{msg} ({msg_r})"
        self.show_toast(msg, color=self.palette.accent_jade if ok else self.palette.accent_coral)
        self._render_remote_beam_page()

    def _on_restart_sunshine_service(self) -> None:
        ok, msg = SunshineMoonlightBridge.start_or_restart_sunshine()
        self.show_toast(msg, color=self.palette.accent_jade if ok else self.palette.accent_coral)
        self._render_remote_beam_page()

    def _on_connect_astrabeam(self, host: str, port_str: str, pin: str) -> None:
        try:
            port = int(port_str.strip())
        except Exception:
            port = 47990
        client = AstraBeamClient(host=host, port=port, pin=pin)
        ok, msg = client.pair()
        self.show_toast(msg, color=self.palette.accent_jade if ok else self.palette.accent_coral)
        if ok:
            AstraBeamViewerWindow(self, client, f"{host}:{port}")

    def _on_launch_moonlight(self, host: str) -> None:
        _, msg = SunshineMoonlightBridge.launch_moonlight(host)
        self.show_toast(msg)

    def _on_send_wol(self, mac: str) -> None:
        ok, msg = SunshineMoonlightBridge.send_wake_on_lan(mac)
        self.show_toast(msg, color=self.palette.accent_jade if ok else self.palette.accent_coral)

    def _on_pause_shield_5m(self) -> None:
        msg = self.adblock_shield.pause_temporarily(minutes=5)
        self.show_toast(msg)
        if self.current_nav == "modules":
            self._render_modules()

    def _on_add_custom_rule(self, ctype: str, cval: str, atype: str, aval: str) -> None:
        rule = self.autopilot.add_custom_rule(
            self.config_obj,
            name=f"If {ctype}={cval}",
            condition_type=ctype,
            condition_value=cval,
            action_type=atype,
            action_value=aval,
        )
        self.show_toast(f"Added custom rule: {rule['name']}")
        if self.current_nav == "settings":
            self._build_settings_page()

    def _on_delete_custom_rule(self, rule_id: str) -> None:
        if self.autopilot.delete_custom_rule(self.config_obj, rule_id):
            self.show_toast("Removed custom rule.")
            if self.current_nav == "settings":
                self._build_settings_page()

    def _on_capture_benchmark(self) -> None:
        snap = self.latest_snapshot or self.telemetry.collect(self._active_adapter(), include_dns=False)
        score = self.latest_report.health_score if self.latest_report else 90
        bb_grade = self._last_bufferbloat.grade if getattr(self, "_last_bufferbloat", None) else "A"
        self.history_db.record_weekly_benchmark(snap, score, bufferbloat_grade=bb_grade)
        regs = self.history_db.detect_regressions()
        self.show_toast(regs[0] if regs else "Captured weekly benchmark baseline!", color=self.palette.accent_coral if regs else self.palette.accent_jade)
        if self.current_nav == "settings":
            self._build_settings_page()

    def _on_run_bufferbloat_check(self) -> None:
        self.begin_task_progress("bufferbloat", "Running gateway latency & bufferbloat check...")

        def _worker() -> None:
            rep = self.rf_scanner.run_bufferbloat_check()
            self._ui_queue.put(("bufferbloat_done", rep))

        threading.Thread(target=_worker, daemon=True).start()

    def _throttle_net_hog(self, pid: int, name: str) -> None:
        try:
            proc = psutil.Process(pid)
            proc.nice(10 if os.name != "nt" else psutil.BELOW_NORMAL_PRIORITY_CLASS)
            self.journal.log_event(
                "NetworkAttribution",
                "deprioritize_net_hog",
                f"Lowered priority of {name} ({pid})",
                "User clicked Deprioritize in Bandwidth & RF Waves",
                "ok",
            )
            self.show_toast(f"Deprioritized background network hog '{name}' (PID {pid}).")
        except Exception as exc:
            self.show_toast(f"Could not deprioritize {name}: {exc}", color=self.palette.accent_coral)

    def _on_rf_band_filter_change(self, band_val: str) -> None:
        self.config_obj.rf_wave_band_filter = band_val
        self.cfg_mgr.save(self.config_obj)
        if hasattr(self, "rf_wave_chart") and self.rf_wave_chart.winfo_exists():
            self.rf_wave_chart.set_band_filter(band_val)

    def _on_scan_rf_waves(self, include_ping: bool = False) -> None:
        self.begin_task_progress("rf_waves", "Sweeping bandwidth interfaces, 2.4/5/6 GHz RF channels & Bluetooth waves...")

        def _worker() -> None:
            snap = self.rf_scanner.scan_all(rescan_rf=True, include_ping=include_ping)
            self._ui_queue.put(("rf_waves_scanned", snap))

        threading.Thread(target=_worker, daemon=True).start()

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
        _, msg, cmd = self.latency_guard.apply_rule(
            rule,
            dry_run=bool(self.dry_run_var.get()),
            enforce_os=not bool(self.dry_run_var.get()),
        )
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

        # 0. Paste-Ready Forensic & Crash Log Export Card
        exp_card = ctk.CTkFrame(
            self.safeguards_scroll, fg_color=p.bg_card, border_width=1, border_color=p.accent_cyan, corner_radius=12
        )
        exp_card.pack(fill="x", pady=(0, 10))
        ehdr = ctk.CTkFrame(exp_card, fg_color="transparent")
        ehdr.pack(fill="x", padx=14, pady=(10, 4))
        ctk.CTkLabel(
            ehdr,
            text="Forensic & Crash Vault Log Export (Paste-Ready for Fixes)",
            font=self.fonts["section"],
            text_color=p.accent_cyan,
        ).pack(side="left")
        ctk.CTkButton(
            ehdr,
            text="Export Full AI Zip Bundle",
            height=28,
            corner_radius=7,
            fg_color=p.bg_elevated,
            hover_color=p.border_strong,
            font=self.fonts["caption"],
            command=self.on_export_ai_diagnostic_bundle,
        ).pack(side="right", padx=(4, 0))
        ctk.CTkButton(
            ehdr,
            text="Save Log File As...",
            height=28,
            corner_radius=7,
            fg_color=p.bg_elevated,
            hover_color=p.border_strong,
            font=self.fonts["caption"],
            command=lambda: self.on_export_forensic_crash_log(save_as=True),
        ).pack(side="right", padx=4)
        ctk.CTkButton(
            ehdr,
            text="Export & Copy Paste-Ready Log",
            height=28,
            corner_radius=7,
            fg_color=p.accent_jade,
            hover_color=p.accent_jade_hover,
            text_color="#071510",
            font=self.fonts["body_bold"],
            command=lambda: self.on_export_forensic_crash_log(save_as=False),
        ).pack(side="right", padx=4)

        latest_export_p = self.cfg_mgr.base_dir / "exports" / "AstraCradle_Crash_Forensic_Log_latest.txt"
        ctk.CTkLabel(
            exp_card,
            text=(
                f"Creates a clean, self-contained crash + forensic log at {latest_export_p} "
                "and copies it straight to your clipboard so you can paste it directly into chat for instant troubleshooting."
            ),
            font=self.fonts["small"],
            text_color=p.text_secondary,
            wraplength=self._content_wrap(90),
            justify="left",
        ).pack(anchor="w", padx=14, pady=(0, 8))

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
        for child in self.pages["settings"].winfo_children():
            try:
                child.destroy()
            except Exception:
                pass
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
        build_info = self.updater.get_local_build_info()
        self.settings_ver_lbl = ctk.CTkLabel(
            ab_txt,
            text=f"{__app_name__} · {build_info['display_version']}",
            font=self.fonts["section"],
        )
        self.settings_ver_lbl.pack(anchor="w")
        cached_gh = getattr(self, "_last_gh_sync_state", None)
        gh_txt = cached_gh[0] if cached_gh else "GitHub Sync: Checking latest pushed version automatically..."
        gh_col = cached_gh[1] if cached_gh else p.accent_cyan
        self.settings_gh_ver_lbl = ctk.CTkLabel(
            ab_txt,
            text=gh_txt,
            font=self.fonts["mono_small"],
            text_color=gh_col,
        )
        self.settings_gh_ver_lbl.pack(anchor="w", pady=(2, 2))
        ctk.CTkLabel(
            ab_txt,
            text=f"{__tagline__} · Workspace: {self.cfg_mgr.base_dir}",
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
        ctk.CTkLabel(uhdr, text="Seamless Auto-Updater & GitHub Version Sync", font=self.fonts["section"]).pack(
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
        self.upd_btn = ctk.CTkButton(
            uhdr,
            text="Check & Update Now",
            height=28,
            corner_radius=7,
            fg_color=p.accent_cyan,
            text_color="#071217",
            font=self.fonts["body_bold"],
            command=self.on_check_updates,
        )
        self.upd_btn.pack(side="right")

        cached_upd_txt = getattr(self, "_last_upd_status_text", None)
        self.upd_status_lbl = ctk.CTkLabel(
            upd_card,
            text=cached_upd_txt or self.updater.check_for_updates(self.config_obj, check_remote=False).status_message,
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

        mem_row = ctk.CTkFrame(upd_card, fg_color=p.bg_elevated, corner_radius=8)
        mem_row.pack(fill="x", padx=16, pady=(0, 8))
        self.machine_mem_lbl = ctk.CTkLabel(
            mem_row,
            text=self._format_install_memory_summary(),
            font=self.fonts["mono_small"],
            text_color=p.text_secondary,
            justify="left",
            wraplength=self._content_wrap(280),
            anchor="w",
        )
        self.machine_mem_lbl.pack(side="left", fill="x", expand=True, padx=10, pady=8)
        ctk.CTkButton(
            mem_row,
            text="Clear Memory",
            width=98,
            height=26,
            fg_color=p.bg_card,
            hover_color=p.border_strong,
            font=self.fonts["caption"],
            command=self._on_clear_install_memory,
        ).pack(side="right", padx=(4, 10), pady=6)
        ctk.CTkButton(
            mem_row,
            text="Re-Apply Remembered Steps",
            width=180,
            height=26,
            fg_color=p.accent_jade,
            text_color="#071510",
            font=self.fonts["caption"],
            command=self._on_reapply_install_memory,
        ).pack(side="right", padx=4, pady=6)

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
        self.settings_advanced_cards = [bld_card]
        if not bool(getattr(self.config_obj, "simple_mode", True)):
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

        # Organized Studio Settings Cards + Comprehensive Setup Bar
        setup_bar = ctk.CTkFrame(
            scroll, fg_color=p.bg_card, border_width=1, border_color=p.accent_cyan, corner_radius=12
        )
        setup_bar.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(
            setup_bar,
            text="Quick Studio Setup & Forensic Diagnostics",
            font=self.fonts["section"],
            text_color=p.accent_cyan,
        ).pack(side="left", padx=16, pady=12)
        ctk.CTkButton(
            setup_bar,
            text="Export Crash & Forensic Log (Paste-Ready)",
            height=30,
            corner_radius=7,
            fg_color=p.bg_elevated,
            hover_color=p.border_strong,
            font=self.fonts["caption"],
            command=lambda: self.on_export_forensic_crash_log(save_as=False),
        ).pack(side="right", padx=(4, 14), pady=10)
        ctk.CTkButton(
            setup_bar,
            text="Run Modular Setup Wizard",
            height=30,
            corner_radius=7,
            fg_color=p.accent_cyan,
            hover_color=p.accent_cyan_hover,
            text_color="#071217",
            font=self.fonts["body_bold"],
            command=lambda: self.open_setup_wizard(first_run=False),
        ).pack(side="right", padx=4, pady=10)
        ctk.CTkButton(
            setup_bar,
            text="Comprehensive Presets",
            height=30,
            corner_radius=7,
            fg_color=p.accent_jade,
            hover_color=p.accent_jade_hover,
            text_color="#071510",
            font=self.fonts["body_bold"],
            command=self.open_comprehensive_setup,
        ).pack(side="right", padx=4, pady=10)

        self.set_theme_var = tk.StringVar(value=self.config_obj.theme)
        self.set_scale_var = tk.StringVar(value=self.config_obj.ui_scale)
        self.set_lang_var = tk.StringVar(value=self.config_obj.language)
        self.set_motion_style_var = tk.StringVar(
            value=str(getattr(self.config_obj, "motion_style", "Hybrid Solar + Fluid"))
        )
        self.set_animations_var = tk.BooleanVar(
            value=bool(getattr(self.config_obj, "animations_enabled", True))
        )
        self.set_boot_splash_var = tk.BooleanVar(
            value=bool(getattr(self.config_obj, "boot_star_gather_splash", True))
        )
        self.set_floating_orb_var = tk.BooleanVar(
            value=bool(getattr(self.config_obj, "floating_orb_on_minimize", False))
        )
        self.set_hover_icon_var = tk.BooleanVar(
            value=bool(getattr(self.config_obj, "hover_icon_animation", True))
        )
        self.set_solar_backdrop_var = tk.BooleanVar(
            value=bool(getattr(self.config_obj, "solar_backdrop_enabled", True))
        )
        self.set_desktop_widget_var = tk.BooleanVar(
            value=bool(getattr(self.config_obj, "desktop_widget_enabled", False))
        )
        self.set_widget_top_var = tk.BooleanVar(
            value=bool(getattr(self.config_obj, "desktop_widget_always_on_top", True))
        )
        self.set_widget_mode_var = tk.StringVar(
            value=str(getattr(self.config_obj, "desktop_widget_mode", "Balanced"))
        )
        self.set_reboot_persist_var = tk.BooleanVar(
            value=bool(getattr(self.config_obj, "reboot_persistence_enabled", False))
        )
        self.set_auto_triggers_var = tk.BooleanVar(
            value=bool(getattr(self.config_obj, "auto_trigger_rules_enabled", True))
        )
        self.set_crash_rb_var = tk.BooleanVar(
            value=bool(getattr(self.config_obj, "crash_auto_rollback_on_boot", True))
        )
        self.set_global_hotkeys_var = tk.BooleanVar(
            value=bool(getattr(self.config_obj, "global_hotkeys_enabled", True))
        )
        self.set_doh_enabled_var = tk.BooleanVar(
            value=bool(getattr(self.config_obj, "upstream_doh_enabled", True))
        )
        self.set_doh_provider_var = tk.StringVar(
            value=str(getattr(self.config_obj, "upstream_doh_provider", "cloudflare"))
        )
        self.set_simple_mode_var = tk.BooleanVar(value=bool(self.config_obj.simple_mode))
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
        self.set_auto_shield_var = tk.BooleanVar(value=bool(getattr(self.config_obj, "astrashield_auto_start", True)))
        self.set_tv_failsafe_var = tk.BooleanVar(value=bool(getattr(self.config_obj, "astrashield_tv_auto_failsafe", True)))
        self.set_wifi_auto_var = tk.BooleanVar(value=bool(getattr(self.config_obj, "wifi_auto_optimize", True)))
        self.set_confirm_prompts_var = tk.BooleanVar(value=bool(getattr(self.config_obj, "confirm_important_prompts", True)))
        self.set_badusb_var = tk.BooleanVar(value=bool(getattr(self.config_obj, "badusb_guard_enabled", True)))
        self.set_probalance_var = tk.BooleanVar(value=bool(getattr(self.config_obj, "probalance_enabled", False)))

        def _make_section_card(title: str, accent: str, subtitle: str) -> ctk.CTkFrame:
            card = ctk.CTkFrame(
                scroll, fg_color=p.bg_card, border_width=1, border_color=p.border_subtle, corner_radius=12
            )
            card.pack(fill="x", pady=(0, 10))
            ctk.CTkLabel(card, text=title, font=self.fonts["section"], text_color=accent).pack(
                anchor="w", padx=16, pady=(12, 2)
            )
            ctk.CTkLabel(card, text=subtitle, font=self.fonts["small"], text_color=p.text_muted).pack(
                anchor="w", padx=16, pady=(0, 8)
            )
            grid_f = ctk.CTkFrame(card, fg_color="transparent")
            grid_f.pack(fill="x", padx=8, pady=(0, 10))
            grid_f.grid_columnconfigure(1, weight=1)
            return grid_f

        def _populate_rows(grid_f: ctk.CTkFrame, rows_spec: list[tuple[str, object, str]]) -> None:
            for r_idx, (label, widget, tip_text) in enumerate(rows_spec):
                lbl_w = ctk.CTkLabel(grid_f, text=label, font=self.fonts["body"], anchor="w")
                lbl_w.grid(row=r_idx, column=0, sticky="w", padx=10, pady=5)
                widget.grid(row=r_idx, column=1, sticky="w", padx=12, pady=5)
                attach_tooltip(widget, tip_text, p)
                attach_tooltip(lbl_w, tip_text, p)

        # Card 0: Modular Features & Setup Wizard
        mod_card = ctk.CTkFrame(
            scroll, fg_color=p.bg_card, border_width=1, border_color=p.accent_cyan, corner_radius=12
        )
        mod_card.pack(fill="x", pady=(0, 10))
        mod_hdr = ctk.CTkFrame(mod_card, fg_color="transparent")
        mod_hdr.pack(fill="x", padx=16, pady=(12, 4))
        ctk.CTkLabel(
            mod_hdr,
            text="0. Modular Features & Install Bundle",
            font=self.fonts["section"],
            text_color=p.accent_cyan,
        ).pack(side="left")
        self.set_bundle_preset_var = tk.StringVar(
            value=str(getattr(self.config_obj, "install_bundle_preset", "Full Suite (Everything)"))
        )
        ctk.CTkOptionMenu(
            mod_hdr,
            values=list(INSTALL_BUNDLE_PRESETS.keys()),
            variable=self.set_bundle_preset_var,
            command=self._on_apply_bundle_preset_from_settings,
            width=230,
        ).pack(side="right")
        ctk.CTkLabel(
            mod_card,
            text=(
                "Enable or disable individual AstraCradle modules. Disabled modules are hidden from the sidebar "
                "and their background watchers stay completely off."
            ),
            font=self.fonts["small"],
            text_color=p.text_muted,
            wraplength=760,
            justify="left",
        ).pack(anchor="w", padx=16, pady=(0, 8))

        active_mod_set = set(normalize_enabled_modules(getattr(self.config_obj, "enabled_modules", None)))
        self.set_module_vars: dict[str, tk.BooleanVar] = {}
        mod_grid = ctk.CTkFrame(mod_card, fg_color="transparent")
        mod_grid.pack(fill="x", padx=12, pady=(0, 8))
        mod_grid.grid_columnconfigure((0, 1), weight=1)
        for idx, spec in enumerate(ASTRA_MODULES):
            var = tk.BooleanVar(value=(True if spec.locked else (spec.module_id in active_mod_set)))
            self.set_module_vars[spec.module_id] = var
            cb = ctk.CTkCheckBox(
                mod_grid,
                text=f"{spec.name} ({spec.footprint_label})",
                variable=var,
                font=self.fonts["small"],
                text_color=p.text_primary,
            )
            if spec.locked:
                cb.configure(state="disabled")
            cb.grid(row=idx // 2, column=idx % 2, sticky="w", padx=8, pady=4)
            attach_tooltip(cb, spec.summary, p)

        mod_btns = ctk.CTkFrame(mod_card, fg_color="transparent")
        mod_btns.pack(fill="x", padx=16, pady=(2, 12))
        ctk.CTkButton(
            mod_btns,
            text="Apply Module Selection",
            height=30,
            fg_color=p.accent_jade,
            hover_color=p.accent_jade_hover,
            text_color="#071510",
            font=self.fonts["body_bold"],
            command=self._on_apply_settings_modules,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            mod_btns,
            text="Launch Step-by-Step Setup Wizard...",
            height=30,
            fg_color=p.bg_elevated,
            hover_color=p.border_strong,
            font=self.fonts["small"],
            command=lambda: self.open_setup_wizard(first_run=False),
        ).pack(side="left")

        # Card 1: Interface & Accessibility
        sec_ui = _make_section_card(
            "1. Interface, Theme & Accessibility",
            p.accent_cyan,
            "Customize visual clarity, monitor scaling from laptop to 4K, language, and Simple/Advanced workspace.",
        )
        _populate_rows(
            sec_ui,
            [
                (
                    "Simple Mode (Beginner-Friendly)",
                    ctk.CTkSwitch(sec_ui, text="Hide advanced power-user studios & knobs", variable=self.set_simple_mode_var),
                    "Recommended for everyday users: hides complex expert tabs and keeps 1-click safe fixes front and center.",
                ),
                (
                    "Visual Theme Scheme",
                    ctk.CTkOptionMenu(sec_ui, values=["Obsidian Horology", "Atelier Alabaster", "High Contrast"], variable=self.set_theme_var),
                    "Choose Dark Titanium, Light Studio, or WCAG High Contrast mode.",
                ),
                (
                    "UI Scale Factor",
                    ctk.CTkOptionMenu(
                        sec_ui,
                        values=["Auto (Monitor)", "90%", "100%", "110%", "125%", "150%", "175%"],
                        variable=self.set_scale_var,
                    ),
                    "Scale interface fonts & widget spacing from laptop screens up to 4K monitors.",
                ),
                (
                    "Interface Language",
                    ctk.CTkOptionMenu(sec_ui, values=list(SUPPORTED_LANGUAGES.keys()), variable=self.set_lang_var),
                    "Switch localized interface labels (en = English, es = Español, fr = Français).",
                ),
                (
                    "Interactive Motion & Hero Design",
                    ctk.CTkOptionMenu(sec_ui, values=list(InteractiveFluidSolarCanvas.MODES), variable=self.set_motion_style_var),
                    "Choose Solar System Orrery, Black & White-inspired mouse Fluid Particle Crest, Hybrid, or Reduced Motion.",
                ),
                (
                    "Smooth Category & Chart Animations",
                    ctk.CTkSwitch(sec_ui, text="Enable smooth 30 FPS category emblems & wave motion", variable=self.set_animations_var),
                    "Toggles per-category topbar animations, wave chart ripples, and dashboard fluid physics.",
                ),
                (
                    "Boot-Up Star-Gather Splash (Option A)",
                    ctk.CTkSwitch(sec_ui, text="Swirl luminous stars into Dyson Crest on launch", variable=self.set_boot_splash_var),
                    "Plays the Black & White-inspired Dyson Star-Gather particle animation at boot.",
                ),
                (
                    "Floating Mini-Orb When Minimized (Off by Default)",
                    ctk.CTkSwitch(sec_ui, text="Show optional floating Mini-Orb shortcut when minimized to tray", variable=self.set_floating_orb_var),
                    "Optional circular desktop shortcut that floats when AstraCradle is minimized (default is OFF).",
                ),
                (
                    "Taskbar / Tray Hover GIF Animation",
                    ctk.CTkSwitch(sec_ui, text="Animate live window & tray icon smoothly on hover", variable=self.set_hover_icon_var),
                    "Cycles animated Solar-Orrery frames in the taskbar & tray icon while hovering or active.",
                ),
                (
                    "Moving Solar System Backdrop",
                    ctk.CTkSwitch(sec_ui, text="Drift subtle planetary orbits & parallax stars behind workspace", variable=self.set_solar_backdrop_var),
                    "Adds a calm dark-mode Solar System starfield that responds with glass parallax when moving the window.",
                ),
                (
                    "Cross-OS Desktop Telemetry Widget",
                    ctk.CTkSwitch(
                        sec_ui,
                        text="Show floating desktop widget (Linux, Windows & macOS)",
                        variable=self.set_desktop_widget_var,
                        command=self._on_toggle_desktop_widget_setting,
                    ),
                    "Toggle the floating desktop telemetry widget on or off (off by default). Remembers position across reboots.",
                ),
                (
                    "Desktop Widget Always on Top",
                    ctk.CTkSwitch(sec_ui, text="Pin desktop widget above other windows", variable=self.set_widget_top_var),
                    "Keeps the floating desktop widget pinned above other apps across Linux, Windows, and macOS.",
                ),
                (
                    "Desktop Widget Skin / Mode",
                    ctk.CTkOptionMenu(sec_ui, values=["Balanced", "Gaming", "Network", "Minimal"], variable=self.set_widget_mode_var),
                    "Choose Balanced telemetry, Gaming (GPU Temp + CPU), Network (RX/TX + Shield Blocked), or Minimal Equilibrium.",
                ),
            ],
        )

        # Card 2: AstraShield, Smart TV Failsafe & Hardware Security
        sec_shield = _make_section_card(
            "2. AstraShield, Smart TV Failsafe & Hardware Guard",
            p.accent_jade,
            "Keep host/TV ad-blocking active automatically, protect TVs with automatic DNS reset, and guard against BadUSB.",
        )
        _populate_rows(
            sec_shield,
            [
                (
                    "Auto-Start AstraShield Sinkhole",
                    ctk.CTkSwitch(sec_shield, text="Start host/LAN sinkhole automatically at launch", variable=self.set_auto_shield_var),
                    "Automatically activates your saved AstraShield sinkhole & watchdog whenever AstraCradle starts.",
                ),
                (
                    "Encrypted Upstream DNS (DoH)",
                    ctk.CTkSwitch(sec_shield, text="Encrypt upstream DNS lookups over HTTPS (RFC 8484)", variable=self.set_doh_enabled_var),
                    "Protects DNS queries from ISP snooping via Cloudflare/Quad9/Google DoH with automatic UDP fallback.",
                ),
                (
                    "DoH Upstream Provider",
                    ctk.CTkOptionMenu(sec_shield, values=["cloudflare", "quad9", "google"], variable=self.set_doh_provider_var),
                    "Choose encrypted DNS-over-HTTPS resolver when AstraShield forwards allowed domains.",
                ),
                (
                    "TV Failsafe Auto-Reset",
                    ctk.CTkSwitch(sec_shield, text="Reset TV DNS/rules if AstraCradle fails or closes", variable=self.set_tv_failsafe_var),
                    "Automatically reverts connected Smart TV overrides back to stock router/DHCP DNS if sinkhole stops.",
                ),
                (
                    "Wi-Fi Best-Channel Auto-Optimizer",
                    ctk.CTkSwitch(sec_shield, text="Auto-pick cleanest Wi-Fi channel/BSSID on connect & periodically", variable=self.set_wifi_auto_var),
                    "Monitors 2.4/5/6 GHz congestion on connect and periodically for CachyOS/Fish & Linux Wi-Fi.",
                ),
                (
                    "BadUSB Hardware Guard",
                    ctk.CTkSwitch(sec_shield, text="Block/quarantine spoofed HID keyboard USB devices", variable=self.set_badusb_var),
                    "Inspects every plugged-in USB device for hidden BadUSB / HID injection risks.",
                ),
                (
                    "OS Adapter Mode",
                    ctk.CTkOptionMenu(sec_shield, values=["auto", "Linux", "Darwin", "Windows"], variable=self.set_os_var),
                    "Auto-detect host OS or simulate another OS playbook adapter.",
                ),
                (
                    "Auto-Update Policy",
                    ctk.CTkOptionMenu(sec_shield, values=["silent_auto", "notify", "manual"], variable=self.set_upd_var),
                    "Controls whether SHA-256 + Ed25519 verified updates apply automatically.",
                ),
            ],
        )

        # Card 3: Automation, System Tray & Power Efficiency
        sec_auto = _make_section_card(
            "3. Automation, Tray & Power Tuning",
            p.accent_brass,
            "Configure background RAM protection, ProBalance CPU smoothing, login autostart, and bottom-right tray behavior.",
        )
        sched_status = self.scheduler.schedule_status()
        sched_btn = ctk.CTkButton(
            sec_auto,
            text=("Remove Background Schedule" if sched_status.installed else "Install Background Schedule"),
            height=28,
            fg_color=p.bg_elevated,
            hover_color=p.border_strong,
            command=self.on_toggle_background_schedule,
        )
        self.sched_btn = sched_btn
        rb_installed, rb_target = self.lifecycle.reboot_persistence_status()
        self.reboot_persist_btn = ctk.CTkButton(
            sec_auto,
            text=("Remove Root Reboot Service" if rb_installed else "Setup Root Reboot Persistence (Sudo / Admin)"),
            height=28,
            fg_color=p.accent_cyan,
            text_color="#071217",
            hover_color=p.accent_cyan_hover,
            font=self.fonts["body_bold"],
            command=self.on_setup_reboot_persistence,
        )
        _populate_rows(
            sec_auto,
            [
                (
                    "Smart RAM Pressure Guard (>85%)",
                    ctk.CTkSwitch(sec_auto, text="Auto-trim standby RAM under pressure", variable=self.set_ram_guard_var),
                    "Automatically performs a safe working-set/standby RAM trim whenever RAM usage exceeds 85%.",
                ),
                (
                    "ProBalance CPU Background Guard",
                    ctk.CTkSwitch(sec_auto, text="Gently deprioritize runaway background hogs", variable=self.set_probalance_var),
                    "Keeps foreground apps and games responsive by restraining CPU-heavy background spikes.",
                ),
                (
                    "Bottom-Right System Tray Icon",
                    ctk.CTkSwitch(sec_auto, text="Show live icon in bottom-right tray / menu bar", variable=self.set_tray_icon_var),
                    "Displays AstraCradle's icon in the bottom-right notification tray (Windows/Linux) or menu bar (macOS).",
                ),
                (
                    "Minimize to System Tray (Hide from Taskbar)",
                    ctk.CTkSwitch(sec_auto, text="Minimize & close into tray instead of taskbar", variable=self.set_tray_var),
                    "When ON, minimizing or closing hides AstraCradle into the bottom-right tray icon instead of keeping it in the taskbar.",
                ),
                (
                    "Start Minimized to Tray",
                    ctk.CTkSwitch(sec_auto, text="Open straight into bottom-right tray", variable=self.set_start_min_var),
                    "Starts AstraCradle quietly in the bottom-right system tray without popping up the main window.",
                ),
                (
                    "Launch AstraCradle at OS Login",
                    ctk.CTkSwitch(sec_auto, text="Auto-start when computer turns on", variable=self.set_login_var),
                    "Registers (or cleanly removes) AstraCradle in your OS login autostart folder.",
                ),
                (
                    "Low-Power UI Mode",
                    ctk.CTkSwitch(sec_auto, text="Reduce telemetry polling cadence", variable=self.set_low_power),
                    "Slows sensor refresh rate to save battery on laptops.",
                ),
                (
                    "Quiet Hours Guard",
                    ctk.CTkSwitch(sec_auto, text="Suppress non-critical auto tasks", variable=self.set_quiet),
                    "Pause background automation during your Quiet Hours window.",
                ),
                (
                    "Background Equilibrium Schedule Toggle",
                    ctk.CTkSwitch(sec_auto, text="Persist periodic background self-checks across reboots", variable=self.set_sched_var),
                    "Keeps periodic systemd --user / launchd / Task Scheduler equilibrium checks active across reboots.",
                ),
                (
                    f"Native Background Schedule ({sched_status.mechanism})",
                    sched_btn,
                    f"Current status: {sched_status.details} ({sched_status.target_path}).",
                ),
                (
                    "Survive Reboot (Root / Sudo Boot Persistence)",
                    ctk.CTkSwitch(sec_auto, text="Re-apply AstraShield, Wi-Fi & active Scripts on every boot", variable=self.set_reboot_persist_var),
                    f"Installs OS boot unit ({rb_target}) using your sudo/admin password so all enabled toggles survive rebooting.",
                ),
                (
                    "Automated Reboot Service Setup",
                    self.reboot_persist_btn,
                    "Prompts for your root/sudo password (or Windows UAC) to install/remove the automated boot persistence service.",
                ),
                (
                    "Smart Rule-Based Auto-Triggers",
                    ctk.CTkSwitch(sec_auto, text="Auto-react to high temps, Wi-Fi congestion & low battery", variable=self.set_auto_triggers_var),
                    "Automatically switches to Battery Endurance below 25% battery and alerts/mitigates high GPU temps or congested Wi-Fi.",
                ),
                (
                    "Crash-Safe Boot Auto-Rollback",
                    ctk.CTkSwitch(sec_auto, text="Auto-revert last boot tweak if crash detected", variable=self.set_crash_rb_var),
                    "If a boot-applied tweak causes an unclean shutdown, automatically rolls back the latest RollbackVault entry on startup.",
                ),
                (
                    "System-Wide Global Hotkeys",
                    ctk.CTkSwitch(sec_auto, text="Enable Ctrl+Alt+A/S/W & Ctrl+Shift+B globally", variable=self.set_global_hotkeys_var),
                    "Summon the Desktop Widget, toggle AstraShield, or run Fix Everything Safe even while inside full-screen apps.",
                ),
            ],
        )

        # Card 4: Safety Vault, Self-Heal & Privacy Consent
        sec_safe = _make_section_card(
            "4. Quarantine Vault, Self-Heal & Privacy Consent",
            p.accent_cyan,
            "Control reversible soft-delete storage caps, automatic 7-invariant self-healing, and browser cache consent.",
        )
        self.set_q_days = ctk.CTkEntry(sec_safe, width=100)
        self.set_q_days.insert(0, str(self.config_obj.quarantine_retention_days))
        self.set_q_max = ctk.CTkEntry(sec_safe, width=100)
        self.set_q_max.insert(0, str(self.config_obj.quarantine_max_mb))
        self.set_disk_floor = ctk.CTkEntry(sec_safe, width=100)
        self.set_disk_floor.insert(0, str(self.config_obj.disk_safety_floor_percent))
        _populate_rows(
            sec_safe,
            [
                (
                    "Enable Reversible Quarantine Vault",
                    ctk.CTkSwitch(sec_safe, text="Soft-move instead of delete", variable=self.set_q_enabled),
                    "Keep cleaned items recoverable inside ~/.astracradle/quarantine.",
                ),
                (
                    "Silent Self-Heal & Forensic Audit",
                    ctk.CTkSwitch(sec_safe, text="Automatically verify & repair state", variable=self.set_self_heal),
                    "Self-check 7 state/journal invariants on launch and periodically.",
                ),
                (
                    "Consent: Privacy Cache Scan",
                    ctk.CTkSwitch(sec_safe, text="Allow non-credential browser cache scan", variable=self.set_privacy_var),
                    "Scans browser caches while strictly preserving passwords, bookmarks, and login cookies.",
                ),
                (
                    "Yes/No Prompts for Important Changes",
                    ctk.CTkSwitch(sec_safe, text="Ask 1-click Yes/No before applying major changes", variable=self.set_confirm_prompts_var),
                    "Shows a clear Yes/No prompt for recommended Wi-Fi channels, hosts edits, and system fixes.",
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
            ],
        )

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

        render_custom_rules_section(self, scroll)
        render_benchmarks_section(self, scroll)

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
            text="Export Portable Profile",
            height=34,
            corner_radius=8,
            fg_color=p.bg_elevated,
            font=self.fonts["body"],
            command=self._on_export_portable_profile,
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            act_bar,
            text="Import Portable Profile",
            height=34,
            corner_radius=8,
            fg_color=p.bg_elevated,
            font=self.fonts["body"],
            command=self._on_import_portable_profile,
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
        if self._show_startup_popups and self.config_obj.show_tray_icon:
            self.tray.ensure_running()
        # Watchdog: keep sinkhole alive if enabled, or trigger TV failsafe restore if it fails
        if (
            self._show_startup_popups
            and self.config_obj.is_module_enabled("privacy_shield")
            and self.config_obj.astrashield_auto_start
            and (self.config_obj.astrashield_lan_tv_mode or self.config_obj.astrashield_dns_enabled)
        ):
            if not self.adblock_shield.dns_running:
                if self.config_obj.astrashield_lan_tv_mode:
                    ok_w, _ = self.adblock_shield.start_lan_dns_sinkhole(
                        port=53,
                        fallback_port=self.config_obj.astrashield_dns_port,
                        upstream=self.config_obj.astrashield_upstream_dns,
                        allow_elevation=False,
                    )
                else:
                    ok_w, _ = self.adblock_shield.start_dns_sinkhole(
                        port=self.config_obj.astrashield_dns_port,
                        upstream=self.config_obj.astrashield_upstream_dns,
                    )
                if not ok_w and self.config_obj.astrashield_tv_auto_failsafe:
                    tv_st = self.lan_tv_shield.load_failsafe_state()
                    if tv_st and tv_st.get("active"):
                        _, rmsg = self.lan_tv_shield.restore_tv_settings(
                            reason="sinkhole_watchdog_failed", shield=self.adblock_shield
                        )
                        self.show_toast(rmsg, color=self.palette.accent_coral)
        if (
            self._show_startup_popups
            and self.config_obj.is_module_enabled("wifi_failover_mesh")
            and getattr(self.config_obj, "wifi_auto_optimize", True)
        ):
            interval_s = max(60, int(getattr(self.config_obj, "wifi_check_interval_min", 10)) * 60)
            if (time.monotonic() - getattr(self, "_last_wifi_check_ts", 0.0)) >= interval_s:
                self._auto_wifi_optimize_async(periodic=True)
        if (
            self._show_startup_popups
            and self.config_obj.is_module_enabled("system_scanner")
            and getattr(self.config_obj, "catalog_passive_scan", True)
        ):
            pace = str(getattr(self.config_obj, "catalog_throttle_mode", "Balanced")).lower()
            pace_s = 30.0 if pace == "fast" else (120.0 if pace == "low" else 60.0)
            if (time.monotonic() - getattr(self, "_last_passive_scan_ts", 0.0)) >= pace_s:
                self._on_run_passive_catalog_batch()
        if self.current_nav == "rf_waves":
            snap_rf = self.rf_scanner.scan_all(rescan_rf=False, include_ping=False)
            self._latest_rf_snap = snap_rf
            if hasattr(self, "rf_wave_chart") and self.rf_wave_chart.winfo_exists():
                self.rf_wave_chart.set_snapshot(snap_rf)
        if not self._is_busy:
            self.refresh_telemetry_async(include_dns=False, also_scan=False)
        self._schedule_telemetry_poll()

    def _on_toggle_desktop_widget_setting(self) -> None:
        want = bool(self.set_desktop_widget_var.get()) if hasattr(self, "set_desktop_widget_var") else False
        if want:
            self.show_desktop_widget()
            self.show_toast("Desktop Widget enabled.")
        else:
            self.hide_desktop_widget(persist_disabled=True)
            self.show_toast("Desktop Widget disabled.")

    def _drain_ui_queue(self) -> None:
        try:
            processed = 0
            while processed < 8:
                item = self._ui_queue.get_nowait()
                processed += 1
                first = item[0]
                if first == "show_window":
                    self._restore_main_window()
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
                    self.adblock_shield.doh_enabled = bool(getattr(self.config_obj, "upstream_doh_enabled", True))
                    self.adblock_shield.doh_provider = str(getattr(self.config_obj, "upstream_doh_provider", "cloudflare"))
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
                if first == "pause_shield_5m":
                    self._on_pause_shield_5m()
                    continue
                if first == "open_beam":
                    self.switch_nav("remote_beam")
                    continue
                if first == "tailscale_refreshed":
                    self._latest_tailscale = item[1]
                    ts = item[1]
                    peers_cnt = len(getattr(ts, "peers", []) or []) if ts else 0
                    self.finish_task_progress(
                        "tailscale",
                        f"Tailscale & AstraBeam scan complete ({peers_cnt} peer(s) found).",
                        notify_title="Tailscale Scan Complete",
                    )
                    if self.current_nav == "remote_beam":
                        self._render_remote_beam_page()
                    continue
                if first == "dep_install_done":
                    self.finish_task_progress(
                        "dep_install",
                        str(item[1] or "Dependency task finished."),
                        color=item[2],
                        notify_title="Dependency Setup Complete",
                    )
                    continue
                if first == "refresh_cmd_vault":
                    if self.current_nav == "cmd_vault":
                        self._render_cmd_vault_page()
                    continue
                if first == "toast":
                    self.show_toast(str(item[1] or ""), color=item[2])
                    continue
                if first == "toggle_widget":
                    self.toggle_desktop_widget()
                    continue
                if first == "show_tray_menu":
                    coords = item[1] if isinstance(item[1], (tuple, list)) and len(item[1]) >= 2 else (0, 0)
                    self._show_tray_context_menu(int(coords[0] or 0), int(coords[1] or 0))
                    continue
                if first == "quit_app":
                    self.on_close(force_quit=True)
                    return
                if first == "full_discovery_done":
                    out, show_dlg = item[1], bool(item[2])
                    if len(item) >= 6:
                        self._latest_sensors = item[3] or {}
                        self._latest_conflicts = item[4] or []
                        self._latest_peripherals = item[5] or []
                    self._last_baseline = out.baseline
                    self.latest_snapshot = out.snapshot
                    self.latest_report = out.report
                    self.latest_playbook = out.playbook
                    self._last_latency_report = out.latency_report
                    self._last_autoruns = out.autoruns
                    self._last_ghosts = out.ghost_drivers
                    self._update_telemetry_cards(out.snapshot, getattr(self, "_latest_gpu", None))
                    self._update_diagnosis_overview(out.report, out.playbook)
                    self.finish_task_progress(
                        "full_discovery",
                        f"Full PC Discovery complete ({len(out.baseline.subsystems_checked)} local subsystems baselined)!",
                        notify_title="Full PC Discovery Complete",
                    )
                    if show_dlg:
                        FullDiscoveryResultDialog(self, self.palette, self.fonts, out.baseline)
                    continue
                if first == "fix_safe_done":
                    summary, proof_summary, notes = item[1], item[2], item[3]
                    self.finish_task_progress("fix_safe", f"{summary} ({proof_summary})")
                    if self._show_startup_popups:
                        messagebox.showinfo(summary, f"{proof_summary}\n\n" + "\n".join(f"• {n}" for n in notes))
                    self.refresh_telemetry_async(include_dns=False, also_scan=True)
                    continue
                if first == "fix_safe_err":
                    self.finish_task_progress(
                        "fix_safe",
                        f"Fix Everything Safe note: {item[1]}",
                        color=self.palette.accent_coral,
                    )
                    continue
                if first == "full_discovery_err":
                    self.finish_task_progress(
                        "full_discovery",
                        f"Full PC Discovery note: {item[1]}",
                        color=self.palette.accent_coral,
                    )
                    continue

                if first == "pihole_synced":
                    msg = item[1]
                    self.finish_task_progress(
                        "pihole",
                        str(msg),
                        notify_title="AstraShield Blocklist Sync Complete",
                    )
                    if self.current_nav == "modules":
                        self._render_modules()
                    continue

                if first == "lan_tv_scanned":
                    devs = item[1] or []
                    bdevs = item[2] or []
                    self._latest_lan_devices = devs
                    self._latest_bt_devices = bdevs
                    self.finish_task_progress(
                        "lan_tv",
                        f"TV & Bluetooth scan complete: found {len(devs)} Smart TV/LAN device(s) and {len(bdevs)} Bluetooth device(s).",
                        notify_title="Smart TV & Bluetooth Scan Complete",
                    )
                    if self.current_nav == "modules":
                        self._render_modules()
                    continue

                if first == "wifi_scanned":
                    wres = item[1]
                    periodic = bool(item[2])
                    self._latest_wifi_res = wres
                    if not periodic or (wres and wres.switched):
                        self.finish_task_progress(
                            "wifi",
                            wres.action_taken if wres else "Wi-Fi scan complete.",
                            notify_title="Wi-Fi Channel Scan Complete",
                        )
                    else:
                        self._active_tasks.pop("wifi", None)
                    if self.current_nav == "wifi":
                        self._render_wifi_page()
                    continue

                if first == "wifi_recommended":
                    rec = item[1] or {}
                    periodic = bool(item[2])
                    self._latest_wifi_rec = rec
                    self._latest_wifi_res = rec.get("result")
                    if self.current_nav == "wifi":
                        self._render_wifi_page()
                    if rec.get("has_recommendation"):
                        if self._show_startup_popups and self.confirm_yes_no(
                            "Less-Traffic Wi-Fi Channel Recommended",
                            str(rec.get("summary") or ""),
                            list(rec.get("details") or []),
                            yes_label="Yes — Switch Channel",
                            no_label="No — Keep Current",
                        ):
                            self._on_apply_wifi_rec_now()
                        else:
                            self.finish_task_progress(
                                "wifi",
                                str(rec.get("summary") or "Wi-Fi channel scan complete."),
                                notify_title="Wi-Fi Channel Scan Complete",
                            )
                    elif not periodic:
                        self.finish_task_progress(
                            "wifi",
                            str(rec.get("summary") or "Wi-Fi channels verified clean."),
                            notify_title="Wi-Fi Channel Scan Complete",
                        )
                    else:
                        self._active_tasks.pop("wifi", None)
                    continue

                if first == "catalog_scanned":
                    summ = item[1]
                    passive = bool(item[2])
                    self._latest_catalog_summary = summ
                    if not passive or (summ and (summ.newly_installed_programs or summ.quiet_fixes_applied > 0)):
                        self.finish_task_progress(
                            "catalog",
                            f"System Catalog ({summ.mode}) complete: {summ.files_checked_this_pass} checked, "
                            f"{summ.programs_cataloged} apps, {summ.quiet_fixes_applied} quiet fix(es).",
                            notify_title="System Catalog Scan Complete",
                        )
                    else:
                        self._active_tasks.pop("catalog", None)
                    if self.current_nav == "scanner":
                        self._render_scanner_page()
                    continue

                if first == "rf_waves_scanned":
                    snap_rf = item[1]
                    self._latest_rf_snap = snap_rf
                    self.finish_task_progress(
                        "rf_waves",
                        f"RF Sweep complete: RX {snap_rf.total_rx_mbps:.2f} Mbps | TX {snap_rf.total_tx_mbps:.2f} Mbps | "
                        f"{len(snap_rf.rf_channels)} Wi-Fi APs | {snap_rf.bt_count} BT",
                        notify_title="RF & Bluetooth Sweep Complete",
                    )
                    if self.current_nav == "rf_waves":
                        self._render_rf_waves_page()
                    continue

                if first == "bufferbloat_done":
                    bb = item[1]
                    self._last_bufferbloat = bb
                    self.finish_task_progress(
                        "bufferbloat",
                        f"Bufferbloat check complete — Grade {bb.grade}: +{bb.bloat_delta_ms:.1f} ms bloat",
                        notify_title="Bufferbloat Check Complete",
                    )
                    if self.current_nav == "rf_waves":
                        self._render_rf_waves_page()
                    continue

                if first == "proc_lookup_done":
                    res = item[1]
                    explicit = bool(item[2]) if len(item) > 2 else False
                    key_n = (res.name or "").lower().strip()
                    if key_n:
                        self._proc_lookup_in_flight.discard(key_n)
                        self._proc_lookup_cache_ui[key_n] = res.brief_description
                    if explicit:
                        self.finish_task_progress(
                            "proc_lookup",
                            f"Process lookup complete: {res.name} ({res.status})",
                        )
                    if explicit and hasattr(self, "hunter_lookup_result_lbl"):
                        pid_str = f"PID {res.pid} · " if res.pid is not None else ""
                        res_txt = (
                            f"[{res.status.upper()} · {res.source_name}] {pid_str}{res.name}: {res.brief_description}"
                            + (f"\nCmdline: {res.cmdline[:140]}" if res.cmdline else "")
                            + (f"\nNote: {res.safe_note}" if res.safe_note else "")
                        )
                        self.hunter_lookup_result_lbl.configure(text=res_txt, text_color=self.palette.text_primary)
                    elif self.current_nav == "hunter":
                        self._render_hunter()
                    continue

                if len(item) >= 4 and isinstance(item[0], SystemSnapshot):
                    snap, report, pb, gpu = item[0], item[1], item[2], item[3]
                    if len(item) >= 6:
                        self._latest_sensors = item[4] or {}
                        self._latest_conflicts = item[5] or []
                    if len(item) >= 8:
                        self._latest_peripherals = item[6] or []
                        for uev in (item[7] or []):
                            if uev.event_type == "new_device_blocked":
                                self.show_toast(uev.message, color=self.palette.accent_coral)
                                self.tray.notify("BadUSB BLOCKED", uev.message)
                            else:
                                self.show_toast(uev.message, color=self.palette.accent_jade)
                                self.tray.notify("New Hardware Ping", uev.message)
                    self._latest_gpu = gpu
                elif len(item) == 3 and isinstance(item[0], SystemSnapshot):
                    snap, report, pb = item
                else:
                    continue
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
                focus = getattr(self, "_bg_focus_state", None) or ActiveWindowState(
                    "Desktop / Headless Session", "desktop", False, "cached fallback"
                )
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
                    if "deep_scan" in getattr(self, "_active_tasks", {}):
                        self.finish_task_progress(
                            "deep_scan",
                            f"Deep Scan complete — Equilibrium {report.health_score}/100 ({len(report.findings)} findings).",
                            notify_title="Deep Scan Complete",
                        )
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
                    gpu_t = self._num_or_none((self._latest_sensors or {}).get("gpu_temp_c"))
                    rf_cong = float(self._latest_rf_snap.rf_24_density) if getattr(self, "_latest_rf_snap", None) else None
                    bat = None
                    try:
                        bat = psutil.sensors_battery()
                    except Exception:
                        bat = None
                    trig_events = self.autopilot.evaluate_smart_triggers(
                        snap,
                        self.config_obj,
                        gpu_temp_c=gpu_t,
                        wifi_congestion_pct=rf_cong,
                        battery_pct=(float(bat.percent) if bat else None),
                        on_battery=(bool(not bat.power_plugged) if bat else False),
                    )
                    custom_events = self.autopilot.evaluate_custom_rules(
                        snap,
                        self.config_obj,
                        on_battery=(bool(not bat.power_plugged) if bat else False),
                    )
                    if trig_events:
                        self.show_toast(trig_events[0]["message"], color=self.palette.accent_brass)
                    elif custom_events:
                        self.show_toast(custom_events[0]["message"], color=self.palette.accent_jade)
                    if self.current_nav == "monitoring":
                        update_monitoring_live(self)
                    elif self.current_nav == "playbook":
                        self._render_playbook()
                    elif self.current_nav == "hunter":
                        self._render_hunter()
                    elif self.current_nav == "modules":
                        self._render_modules()
                elif self.current_nav == "monitoring":
                    update_monitoring_live(self)
                elif self.current_nav == "modules" and self.current_module_tab in ("Processes", "Processes & Firewall"):
                    self._render_processes_module()
                fs_tag = f" · Focus Guard ({focus.process_name})" if (self.config_obj.pause_during_fullscreen and focus.is_fullscreen) else ""
                if not getattr(self, "_active_tasks", None):
                    self.topbar_status.configure(
                        text=f"Live · {snap.timestamp.split('T')[-1]}{fs_tag}",
                        text_color=self.palette.text_muted,
                    )
        except queue.Empty:
            pass
        except Exception:
            pass
        finally:
            try:
                if self.winfo_exists():
                    self._drain_job = self.after(100, self._drain_ui_queue)
            except Exception:
                pass

    def refresh_telemetry_async(self, include_dns: bool = False, also_scan: bool = False) -> None:
        if getattr(self, "_telemetry_in_flight", False) and not also_scan:
            return
        self._telemetry_in_flight = True
        dry_now = bool(self.dry_run_var.get()) if hasattr(self, "dry_run_var") else bool(self.config_obj.dry_run_default)
        probe_dns = bool(include_dns or not getattr(self, "_initial_dns_probed", False))
        self._initial_dns_probed = True
        now_m = time.monotonic()
        last_heavy = float(getattr(self, "_last_heavy_probe_ts", 0.0) or 0.0)
        run_heavy = bool(also_scan or (now_m - last_heavy) >= 10.0)
        if run_heavy:
            self._last_heavy_probe_ts = now_m

        def worker():
            try:
                adapter = self._active_adapter()
                snap = self.telemetry.collect(
                    adapter,
                    include_dns=probe_dns,
                    consent_telemetry=self.config_obj.consent_telemetry,
                    consent_processes=self.config_obj.consent_process_scan,
                    include_process_details=bool(also_scan),
                )
                try:
                    self._bg_focus_state = FullscreenFocusDetector.detect()
                except Exception:
                    pass
                gpu = (
                    GpuTelemetryCollector.collect()
                    if run_heavy or getattr(self, "_latest_gpu", None) is None
                    else self._latest_gpu
                )
                if run_heavy and self.config_obj.is_module_enabled("hermes_ai"):
                    try:
                        focus_obj = getattr(self, "_bg_focus_state", None)
                        self.hermes_mgr.observe_running_workload(
                            snap.top_processes,
                            focus_process=(focus_obj.process_name if focus_obj else ""),
                            is_fullscreen=bool(focus_obj.is_fullscreen if focus_obj else False),
                            gpu=gpu,
                            cpu_percent=snap.cpu_percent,
                            ram_percent=snap.mem_percent,
                        )
                    except Exception:
                        pass
                sensors = (
                    TelemetryCollector.collect_detailed_sensors()
                    if (self.config_obj.consent_telemetry and (run_heavy or not self._latest_sensors))
                    else (self._latest_sensors or {})
                )
                conflicts = (
                    self.conflict_detector.scan_conflicts()
                    if (self.config_obj.consent_process_scan and run_heavy)
                    else (self._latest_conflicts or [])
                )
                usb_events = (
                    self.usb_guard.poll_and_guard(self.config_obj, dry_run=dry_now)
                    if (self.config_obj.consent_telemetry and run_heavy)
                    else []
                )
                peripherals = (
                    list(self.usb_guard._known_keys.values())
                    if (self.config_obj.consent_telemetry and run_heavy and self.usb_guard._known_keys)
                    else (
                        self.usb_guard.scan_all_peripherals()
                        if (self.config_obj.consent_telemetry and run_heavy)
                        else (self._latest_peripherals or [])
                    )
                )
                report = self.diagnostics.run_scan(snap, adapter, self.config_obj) if also_scan else None
                pb = self.playbook_builder.build(report, self.config_obj) if report else None
                self._ui_queue.put((snap, report, pb, gpu, sensors, conflicts, peripherals, usb_events))
            finally:
                self._telemetry_in_flight = False

        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def _num_or_none(val: object) -> float | None:
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return float(val)
        return None

    @staticmethod
    def _set_text_if_changed(lbl: ctk.CTkLabel, text: str) -> None:
        if getattr(lbl, "_cached_txt", None) != text:
            lbl._cached_txt = text  # type: ignore[attr-defined]
            lbl.configure(text=text)

    def _update_telemetry_cards(self, snap: SystemSnapshot, gpu=None) -> None:
        sensors = self._latest_sensors or {}
        cpu_temp = self._num_or_none(sensors.get("cpu_temp_c")) or self._num_or_none(snap.temperature_c)
        gpu_live = sensors.get("gpu_live") or {}
        gpu_temp = self._num_or_none(sensors.get("gpu_temp_c")) or self._num_or_none(gpu_live.get("temp_c"))
        nvme_temp = self._num_or_none(sensors.get("nvme_temp_c"))

        cpu_temp_tag = f" · {cpu_temp:.0f}°C" if cpu_temp is not None else ""
        self._set_text_if_changed(self.cpu_val_lbl, f"{snap.cpu_percent:.1f}%{cpu_temp_tag}")
        freq_txt = f"{snap.cpu_freq_mhz:.0f} MHz" if snap.cpu_freq_mhz else "Adaptive"
        self._set_text_if_changed(
            self.cpu_sub_lbl,
            f"{snap.cpu_count_logical} logical cores · {freq_txt}"
            + (f" · Temp {cpu_temp:.1f}°C" if cpu_temp is not None else ""),
        )
        self.cpu_spark.push(snap.cpu_percent)

        self._set_text_if_changed(self.ram_val_lbl, f"{snap.mem_percent:.1f}%")
        self._set_text_if_changed(
            self.ram_sub_lbl,
            f"{format_bytes(snap.mem_used_bytes)} / {format_bytes(snap.mem_total_bytes)} (Swap {snap.swap_percent:.0f}%)",
        )
        self.ram_spark.push(snap.mem_percent)

        if snap.partitions:
            rp = snap.partitions[0]
            nvme_tag = f" · {nvme_temp:.0f}°C" if nvme_temp is not None else ""
            self._set_text_if_changed(self.disk_val_lbl, f"{rp.percent:.1f}%{nvme_tag}")
            self._set_text_if_changed(
                self.disk_sub_lbl,
                f"{rp.mountpoint}: {format_bytes(rp.free_bytes)} free of {format_bytes(rp.total_bytes)}"
                + (f" · NVMe {nvme_temp:.0f}°C" if nvme_temp is not None else ""),
            )
            self.disk_spark.push(rp.percent)

        dns_txt = f"{snap.dns_latency_ms:.0f} ms" if snap.dns_latency_ms is not None else "Online"
        self._set_text_if_changed(self.net_val_lbl, dns_txt)
        fans_list = sensors.get("fans") or []
        fan_txt = f" · Fan {fans_list[0].get('rpm')} RPM" if fans_list else ""
        self._set_text_if_changed(
            self.net_sub_lbl,
            f"RX {format_bytes(snap.net_bytes_recv)} / TX {format_bytes(snap.net_bytes_sent)}{fan_txt}",
        )
        self.net_spark.push(min(100.0, (snap.dns_latency_ms or 35.0) / 3.0))

        gpu = gpu if gpu is not None else getattr(self, "_latest_gpu", None)
        if gpu is not None and gpu.available:
            util = gpu.utilization_percent or 0.0
            vram_txt = (
                f"VRAM {gpu.vram_used_mb:.0f}/{gpu.vram_total_mb:.0f} MB"
                if gpu.vram_used_mb is not None and gpu.vram_total_mb is not None
                else "VRAM N/A"
            )
            pwr_txt = f" · {gpu_live.get('power_draw_w')}W" if gpu_live.get("power_draw_w") is not None else ""
            fan_g = f" · Fan {gpu_live.get('fan_percent')}%" if gpu_live.get("fan_percent") is not None else ""
            gtemp_tag = f" · {gpu_temp:.0f}°C" if gpu_temp is not None else ""
            throt = f" · {gpu.throttling_reason}" if gpu.throttling_reason else " · Optimal"
            self._set_text_if_changed(self.gpu_val_lbl, f"{util:.0f}%{gtemp_tag}")
            self._set_text_if_changed(self.gpu_sub_lbl, f"{gpu.name[:22]} · {vram_txt}{pwr_txt}{fan_g}{throt}")
            self.gpu_spark.push(util)
        else:
            self._set_text_if_changed(
                self.gpu_val_lbl, "Integrated" + (f" · {gpu_temp:.0f}°C" if gpu_temp is not None else "")
            )
            self._set_text_if_changed(self.gpu_sub_lbl, gpu.name if gpu is not None else str(gpu_live.get("name", "Probing GPU...")))
            self.gpu_spark.push(snap.cpu_percent * 0.35)

        self._update_command_deck_diagnostics(snap, gpu)
        self._push_telemetry_to_widget(snap)

    def _update_diagnosis_overview(self, report: DiagnosticReport, pb: Playbook) -> None:
        update_snapshot_findings_inplace(self, report, pb)

    def on_deep_scan(self) -> None:
        self.begin_task_progress("deep_scan", "Deep scanning 8 subsystems...")
        self.refresh_telemetry_async(include_dns=True, also_scan=True)

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
        self.latest_self_heal, self.latest_audit = self.self_heal.run_full_self_heal(
            self._active_adapter(),
            self.config_obj,
            command_db=self.command_db,
            device_registry=self.device_registry,
            companion=self.mobile_companion,
        )
        self.config_obj = self.cfg_mgr.load()
        repaired = sum(1 for e in self.latest_self_heal if e.repaired_automatically)
        self.show_toast(f"Self-Heal verified {len(self.latest_self_heal)} invariants ({repaired} auto-repaired).")
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
        if not self.confirm_yes_no(
            "Permanent Zero-Pass Shred",
            "Permanently zero-overwrite and delete quarantined files for this entry?",
            ["This permanently erases the quarantined backup and CANNOT be undone."],
            yes_label="Yes, Shred Permanently",
            no_label="No, Keep Backup",
            force_prompt=True,
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

    def _refresh_github_version_badge_async(self) -> None:
        if not self._show_startup_popups:
            return

        def _worker() -> None:
            repo_root = Path(__file__).resolve().parents[2]
            res = self.updater.check_for_updates(self.config_obj, check_remote=True, repo_dir=repo_root)
            build_info = self.updater.get_local_build_info(repo_dir=repo_root)

            def _apply() -> None:
                if not self.winfo_exists():
                    return
                if res.update_available:
                    gh_text = f"GitHub Sync: Update available ({res.latest_version}) — click 'Check & Update Now'"
                    gh_col = self.palette.accent_brass
                else:
                    gh_text = f"GitHub Sync: Latest version installed ({build_info['display_version']})"
                    gh_col = self.palette.accent_jade
                self._last_gh_sync_state = (gh_text, gh_col)
                self._last_upd_status_text = res.status_message
                if hasattr(self, "settings_ver_lbl") and self.settings_ver_lbl.winfo_exists():
                    self.settings_ver_lbl.configure(text=f"{__app_name__} · {build_info['display_version']}")
                if hasattr(self, "settings_gh_ver_lbl") and self.settings_gh_ver_lbl.winfo_exists():
                    self.settings_gh_ver_lbl.configure(text=gh_text, text_color=gh_col)
                if hasattr(self, "upd_status_lbl") and self.upd_status_lbl.winfo_exists():
                    self.upd_status_lbl.configure(text=res.status_message)

            try:
                self.after(0, _apply)
            except Exception:
                pass

        threading.Thread(target=_worker, daemon=True).start()

    def on_check_updates(self) -> None:
        if hasattr(self, "set_update_repo_var"):
            repo_txt = self.set_update_repo_var.get().strip()
            if repo_txt and repo_txt != self.config_obj.update_repo_url:
                self.config_obj.update_repo_url = repo_txt
                self.cfg_mgr.save(self.config_obj)

        repo_root = Path(__file__).resolve().parents[2]
        res = self.updater.check_for_updates(self.config_obj, check_remote=True, repo_dir=repo_root)
        build_info = self.updater.get_local_build_info(repo_dir=repo_root)
        if res.update_available:
            if res.release_notes.startswith("https://"):
                if not self._show_startup_popups or messagebox.askyesno(
                    f"AstraCradle Update Ready ({res.latest_version})",
                    f"A newer version is live on GitHub:\n{res.status_message}\n\n"
                    "Download and install it automatically now? (Your settings & rollback vault stay untouched.)",
                ):
                    ok, msg = self.updater.pull_from_github_and_update(self.config_obj, repo_dir=repo_root)
                    if ok:
                        reapplied, _ = self.updater.reapply_remembered_setup(
                            self.config_obj, self.lifecycle, repo_dir=repo_root, wifi_optimizer=self.wifi_optimizer
                        )
                        if reapplied:
                            msg = f"{msg} ({'; '.join(reapplied)})"
                        self._refresh_install_memory_label()
                        new_info = self.updater.get_local_build_info(repo_dir=repo_root)
                        if hasattr(self, "settings_ver_lbl") and self.settings_ver_lbl.winfo_exists():
                            self.settings_ver_lbl.configure(text=f"{__app_name__} · {new_info['display_version']}")
                        if hasattr(self, "settings_gh_ver_lbl") and self.settings_gh_ver_lbl.winfo_exists():
                            self.settings_gh_ver_lbl.configure(
                                text=f"GitHub Sync: Updated to {res.latest_version} — restart to apply",
                                text_color=self.palette.accent_jade,
                            )
                    self.upd_status_lbl.configure(text=msg)
                    self.show_toast(msg)
                    if ok and self._show_startup_popups:
                        messagebox.showinfo("AstraCradle Updated!", msg)
                    return
            bundle_cand = Path(res.release_notes) if res.release_notes and Path(res.release_notes).exists() else None
            if bundle_cand is not None:
                if not self._show_startup_popups or messagebox.askyesno(
                    f"AstraCradle Update Ready (v{res.latest_version})",
                    f"Found update package:\n{bundle_cand}\n\n"
                    "Apply this update now (your settings & rollback vault stay intact)?",
                ):
                    ok, msg = self.updater.install_update_bundle(bundle_cand, self.config_obj)
                    if ok:
                        reapplied, _ = self.updater.reapply_remembered_setup(
                            self.config_obj, self.lifecycle, repo_dir=repo_root, wifi_optimizer=self.wifi_optimizer
                        )
                        if reapplied:
                            msg = f"{msg} ({'; '.join(reapplied)})"
                        self._refresh_install_memory_label()
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

        up_msg = f"You are fully updated with the latest version! ({build_info['display_version']})"
        self.upd_status_lbl.configure(text=res.status_message or up_msg)
        if hasattr(self, "settings_gh_ver_lbl") and self.settings_gh_ver_lbl.winfo_exists():
            self.settings_gh_ver_lbl.configure(
                text=f"GitHub Sync: Fully updated ({build_info['display_version']})",
                text_color=self.palette.accent_jade,
            )
        self.show_toast(up_msg)
        if self._show_startup_popups:
            messagebox.showinfo("AstraCradle is Up to Date", f"{up_msg}\n\n{res.status_message}")

    def _format_install_memory_summary(self) -> str:
        mem = self.updater.load_install_memory()
        steps = ", ".join(mem.get("remembered_steps") or []) or "none yet (auto-recorded on install/update)"
        desk = mem.get("desktop_icon")
        desk_txt = "Yes" if desk is True else ("No (Menu Only)" if desk is False else "Not set")
        hist_cnt = len(mem.get("update_history") or [])
        inst_dir = str(mem.get("install_dir") or "(not recorded)")
        return (
            f"Machine Update Memory: Install Dir: {inst_dir} · Desktop Icon: {desk_txt}\n"
            f"Remembered Steps: {steps} · Recorded Updates: {hist_cnt}"
        )

    def _refresh_install_memory_label(self) -> None:
        if hasattr(self, "machine_mem_lbl") and self.machine_mem_lbl.winfo_exists():
            self.machine_mem_lbl.configure(text=self._format_install_memory_summary())

    def _on_reapply_install_memory(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        reapplied, _ = self.updater.reapply_remembered_setup(
            self.config_obj, self.lifecycle, repo_dir=repo_root, wifi_optimizer=self.wifi_optimizer
        )
        self._refresh_install_memory_label()
        msg = "; ".join(reapplied) if reapplied else "Remembered machine setup steps verified & refreshed."
        self.show_toast(msg, color=self.palette.accent_jade)

    def _on_clear_install_memory(self) -> None:
        self.updater.clear_install_memory()
        self._refresh_install_memory_label()
        self.show_toast("Cleared remembered machine install history.")

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
            repo_root = Path(__file__).resolve().parents[2]
            reapplied, _ = self.updater.reapply_remembered_setup(
                self.config_obj, self.lifecycle, repo_dir=repo_root, wifi_optimizer=self.wifi_optimizer
            )
            if reapplied:
                msg = f"{msg} ({'; '.join(reapplied)})"
            self._refresh_install_memory_label()
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
        if hasattr(self, "set_sched_var"):
            try:
                self.set_sched_var.set(bool(self.config_obj.background_schedule_enabled))
            except Exception:
                pass
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
        win_state = self._capture_window_state()
        theme_changed = self.set_theme_var.get() != self.config_obj.theme
        lang_changed = self.set_lang_var.get() != self.config_obj.language
        scale_changed = self.set_scale_var.get() != getattr(self, "_applied_scale", self.config_obj.ui_scale)
        self.config_obj.theme = self.set_theme_var.get()
        self.config_obj.ui_scale = self.set_scale_var.get()
        self.config_obj.language = self.set_lang_var.get()
        self.i18n.set_language(self.config_obj.language)
        if hasattr(self, "set_motion_style_var"):
            self.config_obj.motion_style = self.set_motion_style_var.get()
        if hasattr(self, "set_animations_var"):
            self.config_obj.animations_enabled = bool(self.set_animations_var.get())
        if hasattr(self, "set_boot_splash_var"):
            self.config_obj.boot_star_gather_splash = bool(self.set_boot_splash_var.get())
        if hasattr(self, "set_floating_orb_var"):
            self.config_obj.floating_orb_on_minimize = bool(self.set_floating_orb_var.get())
        if hasattr(self, "set_hover_icon_var"):
            self.config_obj.hover_icon_animation = bool(self.set_hover_icon_var.get())
        if hasattr(self, "set_solar_backdrop_var"):
            self.config_obj.solar_backdrop_enabled = bool(self.set_solar_backdrop_var.get())
        if hasattr(self, "set_desktop_widget_var"):
            self.config_obj.desktop_widget_enabled = bool(self.set_desktop_widget_var.get())
        if hasattr(self, "set_widget_top_var"):
            self.config_obj.desktop_widget_always_on_top = bool(self.set_widget_top_var.get())
            if self._desktop_widget is not None:
                try:
                    if self._desktop_widget.winfo_exists() and self._desktop_widget.always_on_top != self.config_obj.desktop_widget_always_on_top:
                        self._desktop_widget.toggle_pin()
                except Exception:
                    pass
        if hasattr(self, "set_widget_mode_var"):
            self.config_obj.desktop_widget_mode = self.set_widget_mode_var.get()
            if self._desktop_widget is not None:
                try:
                    if self._desktop_widget.winfo_exists():
                        self._desktop_widget.mode = self.config_obj.desktop_widget_mode
                        self._desktop_widget.mode_btn.configure(text=self._desktop_widget.mode[:3].upper())
                except Exception:
                    pass
        if hasattr(self, "set_auto_triggers_var"):
            self.config_obj.auto_trigger_rules_enabled = bool(self.set_auto_triggers_var.get())
        if hasattr(self, "set_crash_rb_var"):
            self.config_obj.crash_auto_rollback_on_boot = bool(self.set_crash_rb_var.get())
        if hasattr(self, "set_global_hotkeys_var"):
            self.config_obj.global_hotkeys_enabled = bool(self.set_global_hotkeys_var.get())
        if hasattr(self, "set_doh_enabled_var"):
            self.config_obj.upstream_doh_enabled = bool(self.set_doh_enabled_var.get())
            self.adblock_shield.doh_enabled = self.config_obj.upstream_doh_enabled
        if hasattr(self, "set_doh_provider_var"):
            self.config_obj.upstream_doh_provider = self.set_doh_provider_var.get()
            self.adblock_shield.doh_provider = self.config_obj.upstream_doh_provider
        if self.config_obj.desktop_widget_enabled:
            self.show_desktop_widget()
        else:
            self.hide_desktop_widget(persist_disabled=False)
        if hasattr(self, "hero_motion_canvas"):
            eff_mode = (
                "Reduced Motion"
                if (bool(self.set_low_power.get()) or not self.config_obj.animations_enabled)
                else self.config_obj.motion_style
            )
            self.hero_motion_canvas.set_mode(eff_mode)
        if hasattr(self, "category_emblem"):
            self.category_emblem.set_animate(self.config_obj.animations_enabled and not bool(self.set_low_power.get()))
        if hasattr(self, "solar_backdrop"):
            self.solar_backdrop.set_enabled(
                bool(self.config_obj.solar_backdrop_enabled)
                and bool(self.config_obj.animations_enabled)
                and not bool(self.set_low_power.get())
                and self.config_obj.motion_style != "Reduced Motion"
            )
        if hasattr(self, "set_simple_mode_var"):
            self.config_obj.simple_mode = bool(self.set_simple_mode_var.get())
            self._apply_simple_mode(initial=False)
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
        if hasattr(self, "set_auto_shield_var"):
            self.config_obj.astrashield_auto_start = bool(self.set_auto_shield_var.get())
        if hasattr(self, "set_tv_failsafe_var"):
            self.config_obj.astrashield_tv_auto_failsafe = bool(self.set_tv_failsafe_var.get())
        if hasattr(self, "set_wifi_auto_var"):
            self.config_obj.wifi_auto_optimize = bool(self.set_wifi_auto_var.get())
        if hasattr(self, "set_confirm_prompts_var"):
            self.config_obj.confirm_important_prompts = bool(self.set_confirm_prompts_var.get())
        if hasattr(self, "set_badusb_var"):
            self.config_obj.badusb_guard_enabled = bool(self.set_badusb_var.get())
        if hasattr(self, "set_probalance_var"):
            self.config_obj.probalance_enabled = bool(self.set_probalance_var.get())
        if hasattr(self, "set_sched_var"):
            want_sched = bool(self.set_sched_var.get())
            if want_sched != self.config_obj.background_schedule_enabled:
                if want_sched:
                    sok, _ = self.scheduler.install_schedule(self.config_obj.schedule_interval_minutes)
                    self.config_obj.background_schedule_enabled = sok
                else:
                    self.scheduler.remove_schedule()
                    self.config_obj.background_schedule_enabled = False
                if hasattr(self, "sched_btn") and self.sched_btn.winfo_exists():
                    new_st = self.scheduler.schedule_status()
                    self.sched_btn.configure(
                        text="Remove Background Schedule" if new_st.installed else "Install Background Schedule"
                    )
        if hasattr(self, "set_reboot_persist_var"):
            want_rb = bool(self.set_reboot_persist_var.get())
            if want_rb != self.config_obj.reboot_persistence_enabled:
                if want_rb:
                    ok_rb, msg_rb = self.lifecycle.install_reboot_persistence(
                        self.config_obj, elevation=self.elevation
                    )
                    self.config_obj.reboot_persistence_enabled = ok_rb
                    if not ok_rb:
                        self.set_reboot_persist_var.set(False)
                    self.show_toast(msg_rb)
                else:
                    self.lifecycle.remove_reboot_persistence(elevation=self.elevation)
                    self.config_obj.reboot_persistence_enabled = False
        self.lifecycle.set_launch_at_login(
            self.config_obj.launch_at_login,
            Path(__file__).resolve().parents[2],
            start_minimized=self.config_obj.start_minimized,
            start_widget=self.config_obj.desktop_widget_enabled,
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
        if scale_changed:
            self._apply_ui_scale(preserve_window=True)
        if theme_changed or lang_changed or scale_changed:
            self._hot_reload_ui(win_state=win_state)
            self.refresh_telemetry_async(include_dns=True, also_scan=True)
            self.show_toast("Theme, scale & language applied live!")
        else:
            self._restore_window_state(win_state)
            self.refresh_telemetry_async(include_dns=True, also_scan=True)
            self.show_toast("Universal Settings saved & applied.")

    def _hot_reload_ui(self, win_state: dict[str, object] | None = None) -> None:
        """Rebuilds sidebar & workspace live when theme, contrast, scale, or language changes."""
        st = win_state or self._capture_window_state()
        active_nav = self.current_nav
        active_hw = self.current_hw_tab
        active_mod = self.current_module_tab
        active_pb = self.current_pb_tab
        active_auto = self.current_auto_tab
        self.palette = get_palette(self.config_obj.theme)
        self.fonts = get_ui_fonts(1.0)
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
        self._apply_simple_mode(initial=True)
        self.current_hw_tab = active_hw
        self.current_module_tab = active_mod
        self.current_pb_tab = active_pb
        self.current_auto_tab = active_auto
        self.switch_nav(active_nav)
        if self.latest_snapshot:
            self._update_telemetry_cards(self.latest_snapshot)
        if self.latest_report and self.latest_playbook:
            self._update_diagnosis_overview(self.latest_report, self.latest_playbook)
        self._restore_window_state(st)
        try:
            self.after(40, lambda s=st: self._restore_window_state(s))
        except Exception:
            pass

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
        dry_now = bool(self.dry_run_var.get()) if hasattr(self, "dry_run_var") else bool(self.config_obj.dry_run_default)
        pre_snap_cached = self.latest_snapshot
        pre_score = self.latest_report.health_score if self.latest_report else 90
        self.begin_task_progress("fix_safe", "Running 1-Click Fix Everything Safe...")

        def _worker() -> None:
            try:
                adapter = self._active_adapter()
                pre_snap = pre_snap_cached or self.telemetry.collect(adapter, include_dns=False, include_process_details=False)
                baseline = self.autopilot.capture_proof_baseline(pre_snap, pre_score)
                _, summary, notes = self.safe_fixer.run_fix_everything_safe(
                    adapter,
                    self.config_obj,
                    dry_run=dry_now,
                )
                post_snap = self.telemetry.collect(adapter, include_dns=False, include_process_details=False)
                post_rep = self.diagnostics.run_scan(post_snap, adapter, self.config_obj)
                proof = self.autopilot.compare_proof_scorecard("Fix Everything Safe", baseline, post_snap, post_rep.health_score)
                self._ui_queue.put(("fix_safe_done", summary, proof.summary, notes))
            except Exception as exc:
                self._ui_queue.put(("fix_safe_err", str(exc)))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_export_script_pack(self) -> None:
        dest = filedialog.asksaveasfilename(
            title="Export Verified Script Pack",
            defaultextension=".json",
            initialfile="astracradle-script-pack.json",
            filetypes=[("JSON Script Pack", "*.json")],
        )
        if not dest:
            return
        _, msg, _ = self.scripts_engine.export_script_pack(Path(dest))
        self.show_toast(msg)

    def _on_import_script_pack(self) -> None:
        src = filedialog.askopenfilename(
            title="Import Verified Script Pack",
            filetypes=[("JSON Script Pack", "*.json"), ("All Files", "*.*")],
        )
        if not src:
            return
        ok, msg, _ = self.scripts_engine.import_script_pack(Path(src))
        self.show_toast(msg, color=self.palette.accent_jade if ok else self.palette.accent_coral)
        self._render_scripts()

    def _on_export_portable_profile(self) -> None:
        dest = filedialog.asksaveasfilename(
            title="Export Portable AstraCradle Profile",
            defaultextension=".astracradle-profile",
            initialfile="astracradle-backup.astracradle-profile",
            filetypes=[("AstraCradle Profile", "*.astracradle-profile *.zip")],
        )
        if not dest:
            return
        _, msg, _ = PortableProfileBackup.export_profile_archive(self.cfg_mgr, Path(dest), self.journal)
        self.show_toast(msg)

    def _on_import_portable_profile(self) -> None:
        src = filedialog.askopenfilename(
            title="Import Portable AstraCradle Profile",
            filetypes=[("AstraCradle Profile", "*.astracradle-profile *.zip"), ("All Files", "*.*")],
        )
        if not src:
            return
        ok, msg = PortableProfileBackup.import_profile_archive(Path(src), self.cfg_mgr, self.journal)
        if ok:
            self.config_obj = self.cfg_mgr.load()
            self._hot_reload_ui()
        self.show_toast(msg, color=self.palette.accent_jade if ok else self.palette.accent_coral)

    def on_show_weekly_digest(self) -> None:
        blk = self.adblock_shield.status().blocked_queries
        digest = self.safe_fixer.build_weekly_digest(adblock_blocked_count=blk)
        self.show_toast("Generated 7-Day AstraCradle Weekly Digest.")
        if self._show_startup_popups:
            messagebox.showinfo("AstraCradle 7-Day Weekly Digest", digest)

    def _on_toggle_astrashield_hosts(self, enable: bool) -> None:
        if enable:
            if not self.confirm_yes_no(
                "Enable AstraShield System Hosts Sinkhole",
                "Write AstraShield ad/tracker/telemetry sinkhole rules into the system hosts file?",
                [
                    "Reversible anytime by toggling OFF or restoring via Quarantine/Rollback Vault.",
                    "Keeps YouTube playback streams (*.googlevideo.com) untouched when Safe Prune is ON.",
                ],
                yes_label="Yes, Enable Sinkhole",
                no_label="No, Cancel",
            ):
                self._render_modules()
                return
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
            ok, msg = self.adblock_shield.start_dns_sinkhole(
                port=self.config_obj.astrashield_dns_port,
                upstream=self.config_obj.astrashield_upstream_dns,
            )
            self.config_obj.astrashield_dns_enabled = bool(ok)
        else:
            _, msg = self.adblock_shield.stop_dns_sinkhole()
            self.config_obj.astrashield_dns_enabled = False
        self.cfg_mgr.save(self.config_obj)
        self.show_toast(msg)
        self._render_modules()

    def _auto_start_astrashield_async(self) -> None:
        def _worker() -> None:
            msgs: list[str] = []
            if self.config_obj.astrashield_enabled and not self.adblock_shield.is_hosts_active():
                # Only auto-write hosts at startup when writable directly (never block startup with sudo prompt)
                try:
                    ok_h, msg_h = self.adblock_shield.enable_hosts_sinkhole(
                        self.config_obj.astrashield_categories, dry_run=False
                    )
                    if ok_h:
                        msgs.append(msg_h)
                except Exception:
                    pass
            dns_ok = True
            if self.config_obj.astrashield_lan_tv_mode:
                dns_ok, msg_d = self.adblock_shield.start_lan_dns_sinkhole(
                    port=53,
                    fallback_port=self.config_obj.astrashield_dns_port,
                    upstream=self.config_obj.astrashield_upstream_dns,
                    allow_elevation=False,
                )
                msgs.append(msg_d)
            elif self.config_obj.astrashield_dns_enabled:
                dns_ok, msg_d = self.adblock_shield.start_dns_sinkhole(
                    port=self.config_obj.astrashield_dns_port,
                    upstream=self.config_obj.astrashield_upstream_dns,
                )
                msgs.append(msg_d)
            if not dns_ok and self.config_obj.astrashield_tv_auto_failsafe:
                tv_st = self.lan_tv_shield.load_failsafe_state()
                if tv_st and tv_st.get("active"):
                    _, rmsg = self.lan_tv_shield.restore_tv_settings(
                        reason="startup_sinkhole_failed", shield=self.adblock_shield
                    )
                    msgs.append(rmsg)
            if msgs:
                self._ui_queue.put(("pihole_synced", msgs[-1], None))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_toggle_astrashield_autostart(self, enable: bool) -> None:
        self.config_obj.astrashield_auto_start = bool(enable)
        self.cfg_mgr.save(self.config_obj)
        self.show_toast(
            f"AstraShield Auto-Start on Startup {'ENABLED' if enable else 'DISABLED'}."
        )

    def _on_toggle_tv_failsafe(self, enable: bool) -> None:
        self.config_obj.astrashield_tv_auto_failsafe = bool(enable)
        self.cfg_mgr.save(self.config_obj)
        self.show_toast(
            f"TV Failsafe Auto-Reset {'ENABLED' if enable else 'DISABLED'}."
        )

    def restart_application(self) -> None:
        """Cleanly saves state, spawns a fresh AstraCradle instance, and exits this window."""
        import subprocess
        import sys

        self.cfg_mgr.save(self.config_obj)
        try:
            if getattr(self.config_obj, "astrashield_tv_auto_failsafe", True):
                tv_st = self.lan_tv_shield.load_failsafe_state()
                if tv_st and tv_st.get("active"):
                    self.lan_tv_shield.restore_tv_settings(reason="app_restart", shield=self.adblock_shield)
            self.adblock_shield.stop_dns_sinkhole()
            self.tray.stop()
            self.crash_analyzer.mark_clean_shutdown()
        except Exception:
            pass
        if self._show_startup_popups:
            subprocess.Popen([sys.executable, "-m", "astracradle"], start_new_session=True)
        self.destroy()

    def on_setup_and_restart_astrashield(self) -> None:
        """Configures AstraShield host + DNS sinkhole with auto-start on startup, then restarts AstraCradle."""
        dry = bool(self.dry_run_var.get())
        self.config_obj.astrashield_auto_start = True
        self.config_obj.astrashield_dns_enabled = True
        self.config_obj.astrashield_lan_tv_mode = True
        self.config_obj.astrashield_tv_auto_failsafe = True
        self.config_obj.astrashield_youtube_safe_prune = True
        ok_h, msg_h = self.adblock_shield.enable_hosts_sinkhole(
            self.config_obj.astrashield_categories, dry_run=dry
        )
        self.config_obj.astrashield_enabled = bool(ok_h and not dry)
        self.cfg_mgr.save(self.config_obj)
        if not self._show_startup_popups:
            self.show_toast(f"Setup complete ({msg_h}); ready for restart.")
            return
        if messagebox.askyesno(
            "AstraShield Configured — Restart Now?",
            f"Sinkhole setup complete ({msg_h}).\n"
            "Auto-Start Sinkhole on Startup & TV Failsafe Auto-Reset are now ON.\n\n"
            "Restart AstraCradle now so everything initializes fresh?",
        ):
            self.restart_application()

    def _prompt_sudo_password(self, title_text: str, reason_text: str) -> str | None:
        """Show the native sudo/admin password prompt cleanly on the Tk main thread."""
        if not self._show_startup_popups:
            return None

        def _open() -> str | None:
            dlg = SudoPasswordDialog(
                self,
                self.palette,
                self.fonts,
                title_text=title_text,
                reason_text=reason_text,
                pkexec_available=(platform.system() == "Linux" and bool(shutil.which("pkexec"))),
            )
            self.wait_window(dlg)
            return dlg.result

        if threading.current_thread() is threading.main_thread():
            return _open()
        done = threading.Event()
        holder: dict[str, str | None] = {"val": None}

        def _dispatch() -> None:
            try:
                holder["val"] = _open()
            finally:
                done.set()

        try:
            self.after(0, _dispatch)
            done.wait(timeout=180)
        except Exception:
            return None
        return holder["val"]

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

    # ------------------------------------------------------------------
    # Command Deck: 1-Button Setup & Deploy Hub + Full Live Diagnostics
    # ------------------------------------------------------------------
    def _build_command_deck_deploy_hub(self) -> None:
        p = self.palette
        for c in self.dash_deploy_frame.winfo_children():
            c.destroy()
        hdr = ctk.CTkFrame(self.dash_deploy_frame, fg_color="transparent")
        hdr.pack(fill="x", padx=16, pady=(10, 4))
        ctk.CTkLabel(
            hdr,
            text="1-Button Automatic Setup & Deploy Control Center",
            font=self.fonts["body_bold"],
            text_color=p.accent_cyan,
        ).pack(side="left")
        ctk.CTkButton(
            hdr,
            text="Deploy All Toggled Fixes Now",
            height=30,
            fg_color=p.accent_jade,
            hover_color=p.accent_jade_hover,
            text_color="#071510",
            font=self.fonts["body_bold"],
            command=self._deploy_selected_command_deck_items,
        ).pack(side="right")

        ctk.CTkLabel(
            self.dash_deploy_frame,
            text=(
                "Toggle the items you want and click 'Deploy All Toggled Fixes Now', or hit any single "
                "'Auto Setup & Deploy' button below to solve it immediately."
            ),
            font=self.fonts["small"],
            text_color=p.text_secondary,
        ).pack(anchor="w", padx=16, pady=(0, 6))

        self._deploy_vars: dict[str, tk.BooleanVar] = {}
        items = [
            ("astrashield_yt", "AstraShield Ad/Tracker Blocker + YouTube Safe Ad-Prune", True),
            ("lan_tv_shield", "LAN Smart TV & Network Device YouTube Ad-Shield DNS", bool(getattr(self.config_obj, "astrashield_lan_tv_mode", False))),
            ("pihole_sync", "Download & Sync Standard Pi-hole Blocklists", True),
            ("stop_conflicts", "Turn Off Conflicting Background Tools (DNS/Fan/Optimizer)", True),
            ("badusb_guard", "BadUSB Pre-Connection Gate + Active Peripheral Ping (Default ON)", bool(self.config_obj.badusb_guard_enabled)),
            ("fast_dns", "Apply Fast-DNS (Cloudflare 1.1.1.1)", False),
            ("safe_fix_ram", "Free Standby RAM + 1-Click Safe Storage/Ghost Sweep", True),
            ("probalance", "Enable ProBalance CPU Hog Restraint", bool(self.config_obj.probalance_enabled)),
            ("shortcuts_icon", "Install App Menu & Tray Shortcuts (Asks Before Desktop Icon)", False),
        ]
        for key, label, default_on in items:
            var = tk.BooleanVar(value=default_on)
            self._deploy_vars[key] = var
            row = ctk.CTkFrame(self.dash_deploy_frame, fg_color=p.bg_elevated, corner_radius=8)
            row.pack(fill="x", padx=16, pady=2)
            ctk.CTkCheckBox(row, text=label, variable=var, font=self.fonts["small"]).pack(
                side="left", padx=10, pady=5
            )
            ctk.CTkButton(
                row,
                text="Auto Setup & Deploy",
                width=142,
                height=24,
                fg_color=p.accent_cyan,
                text_color="#071217",
                font=self.fonts["caption"],
                command=lambda k=key: self._run_single_setup_deploy(k),
            ).pack(side="right", padx=8, pady=4)
        ctk.CTkFrame(self.dash_deploy_frame, height=8, fg_color="transparent").pack()

    def _run_single_setup_deploy(self, key: str, quiet: bool = False) -> tuple[bool, str]:
        dry = bool(self.dry_run_var.get())
        if key == "astrashield_yt":
            rd = self.adblock_shield.load_rules()
            rd["youtube_safe_prune"] = True
            cats = set(rd.get("categories", [])) | {"ads", "youtube_ads", "trackers", "telemetry", "malware"}
            rd["categories"] = sorted(cats)
            self.adblock_shield.save_rules(rd)
            js_p, _ = self.adblock_shield.export_youtube_ad_prune_assets()
            ok_h, msg_h = self.adblock_shield.enable_hosts_sinkhole(rd["categories"], dry_run=dry)
            ok_d, msg_d = self.adblock_shield.start_dns_sinkhole(
                port=self.config_obj.astrashield_dns_port,
                upstream=self.config_obj.astrashield_upstream_dns,
            )
            self.config_obj.astrashield_enabled = bool(ok_h)
            self.config_obj.astrashield_youtube_safe_prune = True
            self.cfg_mgr.save(self.config_obj)
            res_msg = f"AstraShield + YouTube Ad-Pruner ready ({js_p.name}): {msg_h} | {msg_d}"
            if not quiet:
                self.show_toast(res_msg)
            return ok_h or ok_d, res_msg

        if key == "lan_tv_shield":
            self.config_obj.astrashield_lan_tv_mode = True
            self.cfg_mgr.save(self.config_obj)
            ok_tv, msg_tv = self.adblock_shield.start_lan_dns_sinkhole(
                port=53,
                fallback_port=self.config_obj.astrashield_dns_port,
                upstream=self.config_obj.astrashield_upstream_dns,
            )
            self._latest_lan_devices = self.lan_tv_shield.scan_lan_devices(self.adblock_shield, ssdp_timeout=0.5)
            full_tv_msg = f"{msg_tv} (Found {len(self._latest_lan_devices)} LAN device(s))"
            if not quiet:
                self.show_toast(full_tv_msg)
            return ok_tv, full_tv_msg

        if key == "pihole_sync":
            ok_p, msg_p, _ = self.adblock_shield.download_pihole_blocklists(timeout=10.0)
            if ok_p and self.adblock_shield.is_hosts_active() and not dry:
                self.adblock_shield.enable_hosts_sinkhole(self.config_obj.astrashield_categories, dry_run=False)
            if not quiet:
                self.show_toast(msg_p)
            return ok_p, msg_p

        if key == "stop_conflicts":
            conflicts = self.conflict_detector.scan_conflicts()
            if not conflicts:
                msg_c = "Zero conflicting programs or services running."
                if not quiet:
                    self.show_toast(msg_c)
                return True, msg_c
            stopped = 0
            last_msg = ""
            for cf in conflicts:
                ok_c, last_msg = self.conflict_detector.stop_conflict(cf, dry_run=dry, elevation=self.elevation)
                if ok_c:
                    stopped += 1
            msg_c = f"Processed {stopped}/{len(conflicts)} conflicting tool(s). {last_msg}"
            if not quiet:
                self.show_toast(msg_c)
                self.refresh_telemetry_async(include_dns=False, also_scan=False)
            return stopped > 0, msg_c

        if key == "badusb_guard":
            self.config_obj.badusb_guard_enabled = True
            self.config_obj.usb_strict_gate = True
            self.cfg_mgr.save(self.config_obj)
            ok_u, msg_u = self.usb_guard.set_strict_precheck_gate(True, dry_run=dry)
            if not quiet:
                self.show_toast(msg_u)
            return ok_u, msg_u

        if key == "fast_dns":
            ok_n, msg_n = self.net_dns_inspector.apply_dns_preset(
                "Cloudflare (1.1.1.1)", dry_run=dry, elevation=self.elevation
            )
            if not quiet:
                self.show_toast(msg_n)
            return ok_n, msg_n

        if key == "safe_fix_ram":
            ok_r, msg_r, _ = self.ram_standby_mgr.trim_standby_ram(dry_run=dry, elevation=self.elevation)
            _, _, notes = self.safe_fixer.run_fix_everything_safe(
                self._active_adapter(), self.config_obj, dry_run=dry
            )
            msg_all = f"{msg_r} | {' ; '.join(notes[:2])}"
            if not quiet:
                self.show_toast(msg_all)
            return ok_r, msg_all

        if key == "probalance":
            self.config_obj.probalance_enabled = True
            self.cfg_mgr.save(self.config_obj)
            msg_pb = "ProBalance CPU Hog Restraint enabled."
            if not quiet:
                self.show_toast(msg_pb)
            return True, msg_pb

        if key == "shortcuts_icon":
            repo_root = Path(__file__).resolve().parents[2]
            want_desktop = False
            if not quiet and self._show_startup_popups:
                want_desktop = self.confirm_yes_no(
                    "Add Desktop Icon?",
                    "Install App Menu & System Tray icons now. Also add an icon on your Desktop?",
                    yes_label="Yes, Add Desktop Icon",
                    no_label="No, Menu & Tray Only",
                    force_prompt=True,
                )
            created = self.lifecycle.install_shortcuts(repo_root, include_desktop_icon=want_desktop)
            self.tray.ensure_running()
            msg_s = f"Installed {len(created)} AstraCradle launcher shortcut(s) and refreshed system tray."
            if not quiet:
                self.show_toast(msg_s)
            return True, msg_s

        return False, f"Unknown setup action '{key}'."

    def _deploy_selected_command_deck_items(self) -> None:
        selected = [k for k, v in getattr(self, "_deploy_vars", {}).items() if v.get()]
        if not selected:
            self.show_toast("Toggle at least one setup item first.")
            return
        results: list[str] = []
        for k in selected:
            ok, msg = self._run_single_setup_deploy(k, quiet=True)
            results.append(f"{'OK' if ok else 'WARN'} [{k}]: {msg}")
        self.show_toast(f"Deployed {len(selected)} toggled setup action(s)!")
        if self._show_startup_popups:
            messagebox.showinfo("AstraCradle Batch Setup & Deploy Complete", "\n\n".join(results))
        self.refresh_telemetry_async(include_dns=True, also_scan=True)

    def _update_command_deck_diagnostics(self, snap: SystemSnapshot, gpu=None) -> None:
        update_command_deck_snapshot_inplace(self, snap, gpu)
        if self.current_nav == "monitoring":
            update_monitoring_live(self)
            refresh_monitoring_peripherals(self)

    def on_export_forensic_crash_log(self, save_as: bool = False) -> dict:
        dest_file = None
        if save_as and self._show_startup_popups:
            chosen = filedialog.asksaveasfilename(
                title="Save Paste-Ready Forensic & Crash Log",
                defaultextension=".txt",
                initialfile="AstraCradle_Crash_Forensic_Log.txt",
                filetypes=[("Text / Markdown Log", "*.txt *.md"), ("All Files", "*.*")],
            )
            if not chosen:
                return {}
            dest_file = Path(chosen)
        res = self.ai_exporter.export_forensic_crash_log(
            config_obj=self.config_obj,
            self_heal_events=(self.latest_self_heal or self.self_heal.load_ledger()[:7]),
            self_audit=self.latest_audit,
            crash_report=asdict(self.startup_crash_report) if self.startup_crash_report else None,
            shield_status=asdict(self.adblock_shield.status()),
            tv_failsafe_state=self.lan_tv_shield.load_failsafe_state(),
            dest_file=dest_file,
        )
        self._copy_text(str(res["text"]))
        self.show_toast(
            f"Copied paste-ready Forensic & Crash Log ({res['line_count']} lines) -> {res['path']}"
        )
        if self._show_startup_popups:
            messagebox.showinfo(
                "Forensic & Crash Log Ready to Paste",
                f"Exported {res['line_count']} lines to:\n  {res['path']}\n"
                f"Latest mirror:\n  {res['latest_path']}\n\n"
                "The complete paste-ready log is already copied to your clipboard — just paste it here!",
            )
        return res

    def open_comprehensive_setup(self) -> None:
        ComprehensiveSetupDialog(
            self,
            self.palette,
            self.fonts,
            self.config_obj,
            on_apply=self._apply_comprehensive_setup,
        )

    def _apply_comprehensive_setup(
        self,
        preset_name: str,
        overrides: dict | None = None,
        create_shortcut: bool = False,
        sync_pihole: bool = False,
    ) -> list[str]:
        win_state = self._capture_window_state()
        applied = self.config_obj.apply_comprehensive_setup(preset_name, overrides=overrides)
        self.cfg_mgr.save(self.config_obj)
        self._apply_simple_mode(initial=False)
        if self.config_obj.show_tray_icon or self.config_obj.minimize_to_tray:
            self.tray.start()
        pkg_root = Path(__file__).resolve().parent
        repo_root = Path(__file__).resolve().parents[1]
        self.lifecycle.install_shortcuts(
            repo_root if (repo_root / "assets").exists() else pkg_root,
            include_desktop_icon=bool(create_shortcut),
        )
        self.lifecycle.set_launch_at_login(
            self.config_obj.launch_at_login,
            Path(__file__).resolve().parents[2],
            start_minimized=self.config_obj.start_minimized,
        )
        if sync_pihole:
            self._on_sync_pihole_lists()
        self.journal.log_event(
            "ComprehensiveSetup",
            "apply_setup",
            f"Applied Comprehensive Setup '{preset_name}' ({len(applied)} settings)",
            "User configured full AstraCradle suite in one pass",
            "ok",
        )
        self._hot_reload_ui(win_state=win_state)
        self.show_toast(f"Comprehensive Setup '{preset_name}' applied ({len(applied)} settings)!")
        return applied

    def on_export_ai_diagnostic_bundle(self) -> None:
        snap_dict = asdict(self.latest_snapshot) if self.latest_snapshot else {}
        gpu_live = GpuPowerAndFanController.probe_live_power_and_fan()
        conflicts_list = [asdict(c) for c in (self._latest_conflicts or self.conflict_detector.scan_conflicts())]
        shield_dict = asdict(self.adblock_shield.status())
        crash_dict = asdict(self.startup_crash_report) if self.startup_crash_report else {}
        audit_list = [asdict(a) for a in (self.latest_audit or [])]
        res = self.ai_exporter.export_ai_handoff_bundle(
            snapshot_data=snap_dict,
            sensors_data=self._latest_sensors,
            gpu_data=gpu_live,
            conflicts_data=conflicts_list,
            shield_status=shield_dict,
            crash_report=crash_dict,
            self_audit=audit_list,
        )
        msg = (
            f"Exported AI Diagnostic Log ({res['anomaly_count']} flag(s), {res['archived_count']} archived) -> {res['zip']}"
        )
        self._copy_text(str(res["markdown"]))
        self.show_toast(msg)
        if self._show_startup_popups:
            messagebox.showinfo(
                "AI Diagnostic & Forensic Archive Ready",
                f"Share either file below with Arena or any AI assistant to diagnose issues:\n\n"
                f"• Markdown Report: {res['markdown']}\n"
                f"• Full JSON Bundle: {res['json']}\n"
                f"• Complete Zip Archive: {res['zip']}\n\n"
                f"Report path copied to clipboard!",
            )

    def _on_stop_conflict(self, conflict) -> None:
        ok, msg = self.conflict_detector.stop_conflict(
            conflict, dry_run=bool(self.dry_run_var.get()), elevation=self.elevation
        )
        self.show_toast(msg, color=self.palette.accent_jade if ok else self.palette.accent_coral)
        self.refresh_telemetry_async(include_dns=False, also_scan=False)

    def _on_sync_pihole_lists(self) -> None:
        self.begin_task_progress("pihole", "Downloading standard Pi-hole blocklists (with YouTube playback safety)...")

        def worker() -> None:
            ok, msg, cnt = self.adblock_shield.download_pihole_blocklists(timeout=12.0)
            if ok and self.adblock_shield.is_hosts_active() and not bool(self.dry_run_var.get()):
                self.adblock_shield.enable_hosts_sinkhole(self.config_obj.astrashield_categories, dry_run=False)
            self._ui_queue.put(("pihole_synced", msg, cnt))

        threading.Thread(target=worker, daemon=True).start()

    def _on_toggle_youtube_safe_prune(self, enable: bool) -> None:
        rd = self.adblock_shield.load_rules()
        rd["youtube_safe_prune"] = bool(enable)
        self.adblock_shield.save_rules(rd)
        self.config_obj.astrashield_youtube_safe_prune = bool(enable)
        self.cfg_mgr.save(self.config_obj)
        self.show_toast(
            "YouTube Safe Ad-Prune ON (blocks ad beacons while protecting video playback)."
            if enable
            else "YouTube Safe Ad-Prune toggled OFF."
        )
        self._render_modules()

    def _on_export_youtube_adprune(self) -> None:
        js_path, flt_path = self.adblock_shield.export_youtube_ad_prune_assets()
        self._copy_text(str(js_path))
        self.show_toast(f"Generated YouTube Ad-Pruner: {js_path.name} & {flt_path.name}")
        if self._show_startup_popups:
            messagebox.showinfo(
                "YouTube Safe Ad-Pruner Ready",
                f"Generated browser-safe YouTube ad-pruning files:\n\n"
                f"1. Userscript (Tampermonkey / Violentmonkey):\n   {js_path}\n\n"
                f"2. uBlock Origin / AdGuard Filter List:\n   {flt_path}\n\n"
                f"Path copied to clipboard!",
            )

    def _on_trust_usb(self, dev) -> None:
        _, msg = self.usb_guard.trust_device(dev, self.config_obj, self.cfg_mgr)
        self.show_toast(msg)
        self.refresh_telemetry_async(include_dns=False, also_scan=False)

    def _on_toggle_badusb_guard(self, enable: bool) -> None:
        self.config_obj.badusb_guard_enabled = bool(enable)
        self.cfg_mgr.save(self.config_obj)
        self.show_toast(f"BadUSB Guard {'ENABLED (Default)' if enable else 'DISABLED'}.")
        self._render_modules()

    def _on_toggle_usb_strict_gate(self, enable: bool) -> None:
        self.config_obj.usb_strict_gate = bool(enable)
        self.cfg_mgr.save(self.config_obj)
        _, msg = self.usb_guard.set_strict_precheck_gate(enable, dry_run=bool(self.dry_run_var.get()))
        self.show_toast(msg)
        self._render_modules()

    def _on_toggle_lan_tv_dns(self, enable: bool) -> None:
        self.config_obj.astrashield_lan_tv_mode = bool(enable)
        self.cfg_mgr.save(self.config_obj)
        if enable:
            ok, msg = self.adblock_shield.start_lan_dns_sinkhole(
                port=53,
                fallback_port=self.config_obj.astrashield_dns_port,
                upstream=self.config_obj.astrashield_upstream_dns,
            )
            if not ok and getattr(self.config_obj, "astrashield_tv_auto_failsafe", True):
                tv_st = self.lan_tv_shield.load_failsafe_state()
                if tv_st and tv_st.get("active"):
                    _, rmsg = self.lan_tv_shield.restore_tv_settings(
                        reason="lan_tv_toggle_failed", shield=self.adblock_shield
                    )
                    msg = f"{msg} · {rmsg}"
            self.show_toast(msg)
        else:
            if getattr(self.config_obj, "astrashield_tv_auto_failsafe", True):
                tv_st = self.lan_tv_shield.load_failsafe_state()
                if tv_st and tv_st.get("active"):
                    self.lan_tv_shield.restore_tv_settings(
                        reason="lan_tv_dns_disabled", shield=self.adblock_shield
                    )
            _, msg = self.adblock_shield.stop_dns_sinkhole()
            self.show_toast(msg)
        self._render_modules()

    def _on_scan_lan_tvs(self) -> None:
        self.begin_task_progress("lan_tv", "Scanning Wi-Fi/LAN for Smart TVs and Bluetooth devices...")

        def _worker() -> None:
            devs = self.lan_tv_shield.scan_lan_devices(self.adblock_shield, ssdp_timeout=0.8)
            bdevs = self.lan_tv_shield.scan_bluetooth_devices()
            self._ui_queue.put(("lan_tv_scanned", devs, bdevs))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_connect_protect_tv(self, dev) -> None:
        lan_ip = self.adblock_shield.get_lan_ipv4()
        listen_addr = self.adblock_shield.dns_listen_addr or ""
        port = 53
        if ":" in listen_addr:
            try:
                port = int(listen_addr.rsplit(":", 1)[-1])
            except Exception:
                port = 53
        _, msg, plan = self.lan_tv_shield.connect_and_protect_tv(dev, lan_ip=lan_ip, dns_port=port)
        self._copy_text(plan["dns_instruction"])
        self.show_toast(msg)
        self._render_modules()
        if self._show_startup_popups:
            body = "\n\n".join(plan["steps"])
            messagebox.showinfo(
                f"Smart TV Protected (Failsafe Armed) — {dev.brand} ({dev.ip})",
                f"Mode: {plan['companion_mode']}\n\n{body}\n\n"
                f"Failsafe Active: If AstraCradle closes or fails, TV resets back to router DNS ({plan['router_failsafe']}).\n"
                f"(DNS pair '{plan['dns_instruction']}' copied to clipboard!)",
            )

    def _on_restore_tv_failsafe(self) -> None:
        _, msg = self.lan_tv_shield.restore_tv_settings(reason="user_manual_reset", shield=self.adblock_shield)
        self.show_toast(msg)
        self._render_modules()

    def _on_connect_bluetooth(self, bt) -> None:
        ok, msg = self.lan_tv_shield.connect_bluetooth_device(bt.mac, connect=(not bt.connected))
        if ok:
            bt.connected = not bt.connected
        self.show_toast(msg, color=self.palette.accent_jade if ok else self.palette.accent_brass)
        self._render_modules()

    def _on_show_tv_setup(self, dev) -> None:
        self._on_connect_protect_tv(dev)


    def on_setup_reboot_persistence(self) -> None:
        installed, _ = self.lifecycle.reboot_persistence_status()
        if installed:
            ok, msg = self.lifecycle.remove_reboot_persistence(elevation=self.elevation)
            if ok:
                self.config_obj.reboot_persistence_enabled = False
                if hasattr(self, "set_reboot_persist_var"):
                    self.set_reboot_persist_var.set(False)
        else:
            ok, msg = self.lifecycle.install_reboot_persistence(
                self.config_obj, elevation=self.elevation
            )
            if ok:
                self.config_obj.reboot_persistence_enabled = True
                if hasattr(self, "set_reboot_persist_var"):
                    self.set_reboot_persist_var.set(True)
        self.cfg_mgr.save(self.config_obj)
        if ok:
            self.updater.record_install_memory(
                reboot_persistence=bool(self.config_obj.reboot_persistence_enabled),
                step_note=("Enabled reboot persistence" if self.config_obj.reboot_persistence_enabled else "Removed reboot persistence"),
                config=self.config_obj,
            )
            self._refresh_install_memory_label()
        if hasattr(self, "reboot_persist_btn") and self.reboot_persist_btn.winfo_exists():
            now_inst, _ = self.lifecycle.reboot_persistence_status()
            self.reboot_persist_btn.configure(
                text=("Remove Root Reboot Service" if now_inst else "Setup Root Reboot Persistence (Sudo / Admin)")
            )
        self.show_toast(msg)


def launch_gui(
    base_dir: Path | str | None = None,
    start_minimized: bool = False,
    start_widget: bool = False,
    open_setup_wizard: bool = False,
) -> None:
    import signal as _signal
    from ..core.single_instance import SingleInstanceManager

    si = SingleInstanceManager(ConfigManager(base_dir=base_dir))
    si.terminate_existing_instances(timeout_sec=3.0)
    app = AstraCradleApp(
        base_dir=base_dir,
        start_minimized=start_minimized,
        start_widget=start_widget,
        open_setup_wizard=open_setup_wizard,
    )

    def _on_sigterm(_signum, _frame) -> None:
        try:
            app.after(0, lambda: app.on_close(force_quit=True))
        except Exception:
            pass

    for sig_name in ("SIGTERM", "SIGHUP"):
        sig = getattr(_signal, sig_name, None)
        if sig is not None:
            try:
                _signal.signal(sig, _on_sigterm)
            except Exception:
                pass
    app.mainloop()
