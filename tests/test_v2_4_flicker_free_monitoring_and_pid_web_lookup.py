"""Tests for v2.4.0: Zero-flicker Command Deck, Dedicated Live Monitoring, Clickable Snapshot, Expandable Diagnostics & PID Web Lookup."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile

from astracradle import __version__
from astracradle.__main__ import main
from astracradle.core.config import ConfigManager
from astracradle.core.diagnostics import DiagnosticsEngine
from astracradle.core.models import ProcessInfo, SystemSnapshot
from astracradle.core.os_adapters import get_os_adapter
from astracradle.core.playbook import PlaybookBuilder
from astracradle.core.process_lookup import ProcessIntelligenceLookup
from astracradle.core.telemetry_history import HistoryPoint
from astracradle.ui.app import AstraCradleApp
from astracradle.ui.pages.dashboard_snapshot import toggle_dash_diag_item, toggle_dash_thermals_expand


def test_process_intelligence_web_lookup_pid_and_cache() -> None:
    assert __version__ == "2.5.0"
    with tempfile.TemporaryDirectory() as td:
        cfg_mgr = ConfigManager(base_dir=Path(td))
        pil = ProcessIntelligenceLookup(cfg_mgr)

        # 1. Inspect current live PID offline
        res_pid = pil.lookup(os.getpid(), allow_web=False)
        assert res_pid.pid == os.getpid()
        assert res_pid.name
        assert res_pid.brief_description

        # 2. Direct web lookup via DuckDuckGo mock + persistent 7-day cache
        calls: list[str] = []

        def fake_fetch(url: str):
            calls.append(url)
            if "duckduckgo.com" in url:
                return {
                    "AbstractText": "Nginx is a high-performance HTTP web server and reverse proxy. It powers millions of websites.",
                    "AbstractURL": "https://nginx.org",
                }
            return None

        res_web = pil.lookup("nginx", allow_web=True, fetch_fn=fake_fetch)
        assert res_web.source_name == "Web Search (DuckDuckGo)"
        assert "HTTP web server" in res_web.brief_description
        assert len(calls) == 1

        # Second call hits persistent cache without hitting fetch_fn again
        pil2 = ProcessIntelligenceLookup(cfg_mgr)
        res_cached = pil2.lookup("nginx", allow_web=True, fetch_fn=fake_fetch)
        assert "HTTP web server" in res_cached.brief_description
        assert len(calls) == 1

        # 3. Wikipedia fallback when DuckDuckGo returns empty
        def fake_wiki_fetch(url: str):
            if "wikipedia.org" in url:
                return {
                    "extract": "Redis is an open-source in-memory data structure store used as a database and cache.",
                    "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Redis"}},
                }
            return {}

        res_wiki = pil.lookup("redis-server", allow_web=True, fetch_fn=fake_wiki_fetch)
        assert res_wiki.source_name == "Web Search (Wikipedia)"
        assert "in-memory data structure" in res_wiki.brief_description


def test_flicker_free_command_deck_monitoring_and_hunter_ui() -> None:
    with tempfile.TemporaryDirectory() as td:
        app = AstraCradleApp(base_dir=Path(td), show_startup_popups=False)
        try:
            app.update()

            # 1. Sparkline reuses exact same canvas item IDs across pushes (zero delete("all") flicker)
            for v in (12.0, 25.0, 40.0, 62.0):
                app.cpu_spark.push(v)
            line_id_before = app.cpu_spark._line_id
            dot_id_before = app.cpu_spark._dot_id
            grid_ids_before = list(app.cpu_spark._grid_ids)
            app.cpu_spark.push(78.0)
            app.cpu_spark.push(55.0)
            assert app.cpu_spark._line_id == line_id_before
            assert app.cpu_spark._dot_id == dot_id_before
            assert app.cpu_spark._grid_ids == grid_ids_before

            # 2. TelemetryHistoryChart reuses exact same canvas item IDs across updates
            pts = [
                HistoryPoint(ts=float(i), cpu_percent=20.0 + i, mem_percent=40.0 + i, health_score=90 - i, temp_c=45.0)
                for i in range(6)
            ]
            app.history_chart.set_points(pts, resolution="5m")
            h_lines_before = dict(app.history_chart._line_ids)
            h_legend_before = app.history_chart._legend_id
            pts.append(HistoryPoint(ts=7.0, cpu_percent=35.0, mem_percent=48.0, health_score=84, temp_c=47.0))
            app.history_chart.set_points(pts, resolution="5m")
            assert app.history_chart._line_ids == h_lines_before
            assert app.history_chart._legend_id == h_legend_before

            # 3. Command Deck persistent widgets survive telemetry + diagnosis refreshes without destruction
            diag_children_before = [id(w) for w in app.dash_diag_panel.winfo_children()]
            findings_children_before = [id(w) for w in app.dash_findings_frame.winfo_children()]
            snap = SystemSnapshot(
                timestamp="2026-11-16T12:00:00",
                os_family="Linux",
                os_release="6.12",
                hostname="astra-test",
                architecture="x86_64",
                uptime_seconds=3600,
                is_elevated=False,
                cpu_percent=32.5,
                cpu_per_core=[32.5] * 8,
                cpu_count_logical=8,
                cpu_freq_mhz=3800.0,
                load_avg=(0.5, 0.4, 0.3),
                mem_total_bytes=16 * 1024**3,
                mem_used_bytes=6 * 1024**3,
                mem_available_bytes=10 * 1024**3,
                mem_percent=37.5,
                swap_total_bytes=4 * 1024**3,
                swap_used_bytes=160 * 1024**2,
                swap_percent=4.0,
                partitions=[],
                net_bytes_sent=1024 * 1024,
                net_bytes_recv=4 * 1024 * 1024,
                dns_latency_ms=14.0,
                battery_percent=None,
                battery_plugged=None,
                temperature_c=52.0,
                process_count=42,
                top_processes=[
                    ProcessInfo(pid=os.getpid(), name="python3", username="user", cpu_percent=1.2, memory_mb=42.0, memory_percent=0.3, status="running"),
                    ProcessInfo(pid=999991, name="ollama", username="user", cpu_percent=0.0, memory_mb=512.0, memory_percent=3.1, status="sleeping"),
                ],
            )
            app.latest_snapshot = snap
            adapter = get_os_adapter()
            rep = DiagnosticsEngine().run_scan(snap, adapter, app.config_obj)
            pb = PlaybookBuilder().build(rep, app.config_obj)
            app.latest_report = rep
            app.latest_playbook = pb
            app._update_telemetry_cards(snap)
            app._update_diagnosis_overview(rep, pb)
            assert [id(w) for w in app.dash_diag_panel.winfo_children()] == diag_children_before
            assert [id(w) for w in app.dash_findings_frame.winfo_children()] == findings_children_before

            # 4. Individual expandable Thermals & Compact Diagnostics toggles work cleanly
            assert app._dash_thermals_expanded is False
            toggle_dash_thermals_expand(app)
            assert app._dash_thermals_expanded is True
            toggle_dash_thermals_expand(app)
            assert app._dash_thermals_expanded is False

            toggle_dash_diag_item(app, "compute")
            assert "compute" in app._dash_expanded_keys
            assert "network_fo" not in app._dash_expanded_keys
            toggle_dash_diag_item(app, "compute")
            assert "compute" not in app._dash_expanded_keys

            # 5. Clickable Snapshot & dedicated "monitoring" category navigation
            assert "monitoring" in app.nav_buttons
            assert "monitoring" in app._dash_snapshot_tiles
            app.switch_nav("monitoring")
            app.update()
            assert app.current_nav == "monitoring"
            assert hasattr(app, "mon_history_chart")
            assert hasattr(app, "mon_sensors_lbl")

            # 6. AI & Process Hunter PID# lookup UI
            app.switch_nav("hunter")
            app.update()
            assert hasattr(app, "hunter_pid_entry")
            assert hasattr(app, "hunter_lookup_result_lbl")
            app._on_lookup_pid_or_process("ollama")
        finally:
            if app.winfo_exists():
                app.destroy()


def test_cli_lookup_pid_flag(capsys) -> None:
    with tempfile.TemporaryDirectory() as td:
        rc = main(["--config-dir", td, "--lookup-pid", str(os.getpid()), "--no-web"])
        assert rc == 0
        out = capsys.readouterr().out
        assert str(os.getpid()) in out

        rc2 = main(["--config-dir", td, "--lookup-pid", "ollama", "--no-web"])
        assert rc2 == 0
        out2 = capsys.readouterr().out
        assert "Local LLM" in out2
