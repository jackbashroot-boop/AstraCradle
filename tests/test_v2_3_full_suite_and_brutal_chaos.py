from __future__ import annotations

from pathlib import Path
import random
import zipfile

from astracradle import __version__
from astracradle.core.bloat_analyzer import FolderBloatItem
from astracradle.core.config import ConfigManager
from astracradle.core.journal import ForensicJournal
from astracradle.core.models import ProcessInfo, StartupItem
from astracradle.core.os_adapters import get_os_adapter
from astracradle.core.perf_guardian import (
    AppAutoDetector,
    DeviceGlitchAdvisor,
    MemoryLeakWatchdog,
    MicroBenchmark,
    NetworkAndDnsAdvisor,
)
from astracradle.core.safeguard_plus import (
    ConfigIntegrityShield,
    PrivacyTraceSweeper,
    ProcessTranslator,
    RecoveryBundleExporter,
    StartupDelayManager,
    WeeklyDigestGenerator,
)
from astracradle.core.storage_deep import (
    DevAndAIMoVaultScanner,
    DiskSunburstBuilder,
    EmptyAndDeadSweeper,
    SmartDiskMonitor,
)
from astracradle.ui.app import AstraCradleApp
from astracradle.ui.modals import ProcessExplanationDialog
from astracradle.ui.widgets import DysonOrbitalBeacon, DysonSunburstWidget, EquilibriumGauge


def test_storage_deep_smart_vault_sunburst_and_dead_sweeper(tmp_path: Path) -> None:
    assert __version__ == "2.5.1"
    adapter = get_os_adapter(custom_protected_paths=[str(tmp_path / "protected")])

    # 1. SMART / NVMe monitor never crashes
    disks = SmartDiskMonitor().inspect_disks()
    assert len(disks) >= 1 and disks[0].status in {"Healthy", "Caution", "Unknown"}

    # 2. Dev & AI Model Vault scanner finds ollama/hf/node_modules
    fake_home = tmp_path / "home"
    (fake_home / ".ollama" / "models").mkdir(parents=True)
    (fake_home / ".ollama" / "models" / "llama.gguf").write_bytes(b"0" * 4096)
    (fake_home / "Projects" / "my_app" / "node_modules").mkdir(parents=True)
    (fake_home / "Projects" / "my_app" / "node_modules" / "bundle.bin").write_bytes(b"X" * (6 * 1024 * 1024))
    vault = DevAndAIMoVaultScanner().scan(home=fake_home)
    assert any(v.kind == "ai_model" for v in vault)
    assert any(v.kind == "node_modules" for v in vault)

    # 3. Empty dirs, dead symlinks, broken .desktop shortcuts — respecting protected roots
    work = tmp_path / "sweep_root"
    (work / "empty_sub").mkdir(parents=True)
    (work / "dead_link").symlink_to(tmp_path / "nonexistent_target")
    (work / "broken.desktop").write_text("[Desktop Entry]\nName=Dead\nExec=/no/such/binary\n", encoding="utf-8")
    prot = tmp_path / "protected" / "empty_protected"
    prot.mkdir(parents=True)

    dead = EmptyAndDeadSweeper().scan([work, tmp_path / "protected"], adapter)
    kinds = {d.kind for d in dead}
    assert "empty_dir" in kinds and "dead_symlink" in kinds and "broken_shortcut" in kinds
    assert not any("protected" in d.path for d in dead)

    # 4. Sunburst builder sums fractions to ~1.0
    slices = DiskSunburstBuilder.build(
        [
            FolderBloatItem(str(tmp_path / "a"), 600, 10),
            FolderBloatItem(str(tmp_path / "b"), 400, 5),
        ]
    )
    assert len(slices) == 2 and abs(sum(s.fraction for s in slices) - 1.0) < 1e-6


def test_perf_guardian_leak_benchmark_autodetect_net_and_device(tmp_path: Path) -> None:
    mgr = ConfigManager(base_dir=tmp_path / ".astracradle")
    cfg = mgr.load()

    # 1. RAM Leak Watchdog detects monotonic RSS climb
    wd = MemoryLeakWatchdog()
    for step_mb in (100.0, 125.0, 152.0, 185.0):
        wd.record_snapshot([ProcessInfo(4242, "leaky_browser", "user", 2.0, step_mb, 1.0, "running")])
    suspects = wd.detect_leaks(min_samples=4, min_growth_mb=40.0)
    assert len(suspects) == 1 and suspects[0].growth_mb >= 80.0

    # 2. Micro-Benchmark runs locally and stores Before/After history
    bm = MicroBenchmark(mgr)
    r1 = bm.run("Before")
    r2 = bm.run("After")
    assert 10 <= r1.composite_score <= 100
    assert len(bm.load_history()) == 2 and bm.load_history()[0].label == "After"

    # 3. App Auto-Detector enables on match and reverts on exit
    cfg.app_auto_detect_enabled = True
    ad = AppAutoDetector()
    act, sid, app = ad.evaluate([ProcessInfo(101, "blender", "user", 50.0, 500.0, 4.0, "running")], cfg)
    assert act == "enable" and sid == "script_3d_render" and app == "blender"
    act_off, sid_off, _ = ad.evaluate([ProcessInfo(102, "bash", "user", 0.1, 5.0, 0.1, "sleeping")], cfg)
    assert act_off == "revert" and sid_off == "script_3d_render"

    # 4. Network, Hosts & USB/Power Advisors
    assert isinstance(NetworkAndDnsAdvisor.sample_network_apps(limit=3), list)
    hosts_res = NetworkAndDnsAdvisor.audit_hosts_file()
    assert hosts_res.status in {"clean", "review"}
    assert len(DeviceGlitchAdvisor.inspect()) >= 1


def test_safeguard_plus_privacy_delay_shield_bundle_translator_digest(tmp_path: Path) -> None:
    mgr = ConfigManager(base_dir=tmp_path / ".astracradle")
    cfg = mgr.load()
    journal = ForensicJournal(mgr)

    # 1. Privacy Sweeper strictly excludes Login Data / Cookies
    fake_home = tmp_path / "phome"
    cdir = fake_home / ".cache" / "google-chrome" / "Default" / "Cache"
    cdir.mkdir(parents=True)
    (cdir / "data_0").write_bytes(b"C" * 2048)
    (cdir / "Cookies").write_bytes(b"SECRET_COOKIE" * 50)
    cfg.consent_privacy_scan = False
    assert PrivacyTraceSweeper().scan(cfg, home=fake_home) == []
    cfg.consent_privacy_scan = True
    traces = PrivacyTraceSweeper().scan(cfg, home=fake_home)
    assert len(traces) == 1 and traces[0].size_bytes == 2048

    # 2. Startup Delay +60s rewrites user .desktop Exec cleanly
    desktop = tmp_path / "discord.desktop"
    desktop.write_text("[Desktop Entry]\nName=Discord\nExec=/usr/bin/discord\n", encoding="utf-8")
    st_item = StartupItem("xdg:discord", "Discord", str(desktop), "/usr/bin/discord", "user", True, "high", "Linux")
    ok_d, _ = StartupDelayManager.apply_delay_to_item(st_item, 60)
    assert ok_d is True and "sleep 60 && exec /usr/bin/discord" in desktop.read_text(encoding="utf-8")

    # 3. Config Integrity Shield detects tampering
    watched = tmp_path / "authorized_keys"
    watched.write_text("ssh-ed25519 AAAA_ORIGINAL", encoding="utf-8")
    shield = ConfigIntegrityShield(mgr, journal)
    f1 = shield.check_or_init([watched])
    assert f1[0].status == "baseline_created"
    watched.write_text("ssh-ed25519 AAAA_ROGUE_KEY", encoding="utf-8")
    f2 = shield.check_or_init([watched])
    assert f2[0].status == "modified"

    # 4. Recovery Bundle Zip & Weekly HTML Digest
    journal.log_event("Test", "seed", "Seed entry", "Verification", "ok")
    bundle_zip = RecoveryBundleExporter(mgr, journal).export_zip()
    assert bundle_zip.exists()
    with zipfile.ZipFile(bundle_zip) as zf:
        assert "bundle_info.json" in zf.namelist()

    digest_html = WeeklyDigestGenerator(mgr, journal).generate(94, 50_000_000, 0, 1, "Balanced Daily")
    assert digest_html.exists() and "<svg" in digest_html.read_text(encoding="utf-8")

    # 5. Plain-English Process Translator
    exp = ProcessTranslator.explain("svchost.exe")
    assert exp.essential is True and "Windows" in exp.plain_english
    exp_ai = ProcessTranslator.explain("custom_llm_runner", is_ai=True)
    assert exp_ai.essential is False and "AI" in exp_ai.role


def test_full_animations_and_brutal_fuzz_v2_3(tmp_path: Path) -> None:
    # Animated widgets + ProcessExplanationDialog + Privacy/Bloat GUI pages under Xvfb
    assets_dir = Path(__file__).resolve().parents[1] / "assets"
    app = AstraCradleApp(base_dir=tmp_path / ".astracradle_anim", assets_dir=assets_dir, show_startup_popups=False)
    app.update()

    # Verify animated gauge, beacon, sunburst & ProcessExplanationDialog render and animate ticks cleanly
    assert isinstance(app.gauge, EquilibriumGauge)
    assert isinstance(app.topbar_beacon, DysonOrbitalBeacon)
    app.gauge.set_score(88, "Animated")
    app.gauge._tick_anim()
    app.topbar_beacon._tick()

    app.switch_nav("modules")
    app._on_module_tab("Duplicates & Bloat")
    app.update()
    app._on_module_tab("Privacy & Shield")
    app.update()

    sun = DysonSunburstWidget(
        app, app.palette, DiskSunburstBuilder.build([FolderBloatItem("/tmp/a", 500, 2)]), size=160
    )
    sun._animate_sweep()
    sun.destroy()

    dlg = ProcessExplanationDialog(app, app.palette, app.fonts, ProcessTranslator.explain("ollama", is_ai=True))
    app.update()
    dlg.destroy()

    app.on_run_benchmark()
    app.on_export_recovery_bundle()
    app.on_generate_weekly_digest()
    app.on_close()

    # Randomized brutal fuzzing across new modules
    rng = random.Random(23000)
    wd = MemoryLeakWatchdog()
    for tick in range(25):
        procs = [
            ProcessInfo(
                pid=rng.randint(1, 5000),
                name=rng.choice(["svchost.exe", "ollama", "mystery_proc", "\x00bad", ""]),
                username="u",
                cpu_percent=rng.uniform(-5, 200),
                memory_mb=rng.uniform(0, 16000),
                memory_percent=rng.uniform(0, 100),
                status="running",
            )
            for _ in range(rng.randint(0, 10))
        ]
        wd.record_snapshot(procs)
        assert isinstance(wd.detect_leaks(), list)
        for p in procs:
            assert len(ProcessTranslator.explain(p.name, is_protected=rng.random() < 0.3).plain_english) > 0
