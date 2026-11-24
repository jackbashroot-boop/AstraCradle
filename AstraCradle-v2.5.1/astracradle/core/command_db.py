"""SQLite Install Commands & Reusable Script Vault with Automatic Self-Healing Backup."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import sqlite3
import uuid

from .config import ConfigManager
from .journal import ForensicJournal
from .models import utc_now_iso


@dataclass
class CommandRecord:
    cmd_id: str
    title: str
    category: str
    platform: str
    script: str
    description: str = ""
    is_builtin: bool = False
    favorite: bool = False
    use_count: int = 0
    created_at: str = ""
    updated_at: str = ""


BUILTIN_COMMANDS: list[dict[str, str]] = [
    {
        "cmd_id": "install_astracradle_unix",
        "title": "Install AstraCradle (macOS / Linux One-Step)",
        "category": "Install",
        "platform": "macOS / Linux",
        "description": "Installs AstraCradle into an isolated environment with desktop launcher and CLI entrypoint.",
        "script": "bash install_astracradle.sh",
    },
    {
        "cmd_id": "install_astracradle_windows",
        "title": "Install AstraCradle (Windows Batch / PowerShell)",
        "category": "Install",
        "platform": "Windows",
        "description": "Installs AstraCradle on Windows 10/11 and registers the Start Menu & Desktop shortcut.",
        "script": "Install_AstraCradle.bat",
    },
    {
        "cmd_id": "install_astracradle_pip",
        "title": "Install AstraCradle via Python venv (Developer / Editable)",
        "category": "Install",
        "platform": "All",
        "description": "Creates an isolated .venv and installs AstraCradle from source for immediate CLI/GUI launch.",
        "script": "python3 -m venv ~/.astracradle-venv && source ~/.astracradle-venv/bin/activate && pip install -e . && python3 -m astracradle",
    },
    {
        "cmd_id": "install_tailscale_linux",
        "title": "Install Tailscale Zero-Trust Mesh (Linux)",
        "category": "Tailscale",
        "platform": "Linux",
        "description": "Official Tailscale installer for Ubuntu, Debian, Fedora, Arch/CachyOS, and Raspberry Pi.",
        "script": "curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up",
    },
    {
        "cmd_id": "install_tailscale_macos",
        "title": "Install Tailscale Zero-Trust Mesh (macOS Homebrew)",
        "category": "Tailscale",
        "platform": "macOS",
        "description": "Installs Tailscale via Homebrew Cask and launches the menu bar app.",
        "script": "brew install --cask tailscale && open -a Tailscale",
    },
    {
        "cmd_id": "install_tailscale_windows",
        "title": "Install Tailscale Zero-Trust Mesh (Windows Winget)",
        "category": "Tailscale",
        "platform": "Windows",
        "description": "Installs Tailscale on Windows and connects the device to your private tailnet.",
        "script": "winget install --id Tailscale.Tailscale -e && tailscale up",
    },
    {
        "cmd_id": "install_sunshine_windows",
        "title": "Install Sunshine Host (Windows 120fps Moonlight Stream)",
        "category": "Sunshine & Moonlight",
        "platform": "Windows",
        "description": "Installs LizardByte Sunshine streaming host for hardware-accelerated iOS/Android Moonlight.",
        "script": "winget install --id LizardByte.Sunshine -e",
    },
    {
        "cmd_id": "install_sunshine_linux",
        "title": "Install Sunshine Host (Linux Flatpak)",
        "category": "Sunshine & Moonlight",
        "platform": "Linux",
        "description": "Installs LizardByte Sunshine on Linux via Flathub.",
        "script": "flatpak install -y flathub dev.lizardbyte.app.Sunshine && flatpak run dev.lizardbyte.app.Sunshine",
    },
    {
        "cmd_id": "install_moonlight_desktop",
        "title": "Install Moonlight Client (Windows / macOS)",
        "category": "Sunshine & Moonlight",
        "platform": "Windows / macOS",
        "description": "Installs the native Moonlight client on secondary PCs or Macs.",
        "script": "# Windows:\nwinget install --id MoonlightGameStreamingProject.Moonlight -e\n# macOS:\nbrew install --cask moonlight",
    },
    {
        "cmd_id": "ios_watch_shortcut_setup",
        "title": "iPhone & Apple Watch Shortcuts Remote Health & Fix Safe",
        "category": "iOS & Apple Watch",
        "platform": "iOS / watchOS",
        "description": "Example curl/Shortcuts request to read PC health or trigger 1-Click Fix Safe from iPhone or Apple Watch over Tailscale TLS.",
        "script": (
            "# Replace <TAILSCALE_IP> and <TOKEN> from AstraBeam Host Live status:\n"
            "curl -k -H \"Authorization: Bearer <TOKEN>\" https://<TAILSCALE_IP>:47990/api/watch/summary\n"
            "curl -k -X POST -H \"Authorization: Bearer <TOKEN>\" -H \"Content-Type: application/json\" "
            "-d '{\"action\":\"fix_safe\"}' https://<TAILSCALE_IP>:47990/api/watch/action"
        ),
    },
    {
        "cmd_id": "ios_pwa_home_screen",
        "title": "iOS Safari / Android Chrome Install-Free PWA Stream URL",
        "category": "iOS & Apple Watch",
        "platform": "iOS / Android",
        "description": "Open in iPhone Safari or Android Chrome over Tailscale and tap Share -> Add to Home Screen.",
        "script": "https://<TAILSCALE_IP>:47990/   # Watch Companion: https://<TAILSCALE_IP>:47990/watch",
    },
    {
        "cmd_id": "install_android_adb_scrcpy",
        "title": "Install Android ADB & Scrcpy (USB / Wireless Bridge)",
        "category": "Android",
        "platform": "All",
        "description": "Installs Android Platform Tools (adb) and scrcpy for USB/Wi-Fi Android pairing and reverse tunneling.",
        "script": (
            "# macOS:\nbrew install android-platform-tools scrcpy\n"
            "# Ubuntu/Debian/CachyOS:\nsudo apt install -y adb scrcpy || sudo pacman -S --noconfirm android-tools scrcpy\n"
            "# Windows:\nwinget install --id Google.PlatformTools -e"
        ),
    },
    {
        "cmd_id": "android_adb_reverse_beam",
        "title": "Android USB Zero-Wi-Fi AstraBeam Reverse Tunnel",
        "category": "Android",
        "platform": "Android",
        "description": "Tunnels AstraBeam TLS port directly over USB ADB when Wi-Fi or Tailscale is unavailable.",
        "script": "adb reverse tcp:47990 tcp:47990 && adb shell am start -a android.intent.action.VIEW -d \"https://127.0.0.1:47990/\"",
    },
    {
        "cmd_id": "cli_beam_host_start",
        "title": "Start AstraBeam Secure TLS Host (Headless CLI)",
        "category": "Diagnostics & Self-Heal",
        "platform": "All",
        "description": "Starts the zero-trust TLS AstraBeam server bound to your Tailscale IP from a terminal.",
        "script": "python3 -m astracradle --beam-host",
    },
    {
        "cmd_id": "cli_tailscale_apple_status",
        "title": "Check Tailscale, iPhone, Apple Watch & Android Mesh",
        "category": "Diagnostics & Self-Heal",
        "platform": "All",
        "description": "Prints Tailscale mesh peers, discovered Apple & Android devices, and Sunshine/Moonlight readiness.",
        "script": "python3 -m astracradle --tailscale-status",
    },
    {
        "cmd_id": "cli_self_heal_and_bench",
        "title": "Run Full Self-Heal, Benchmark & Bufferbloat Audit",
        "category": "Diagnostics & Self-Heal",
        "platform": "All",
        "description": "Executes AstraCradle self-integrity healing, system benchmark, and latency bufferbloat check.",
        "script": "python3 -m astracradle --self-check && python3 -m astracradle --benchmark-report && python3 -m astracradle --bufferbloat-test",
    },
    {
        "cmd_id": "linux_bash_full_deps",
        "title": "Full Linux Bash Installer (All AstraCradle Companion Programs)",
        "category": "Linux Bash",
        "platform": "Linux",
        "description": "Complete Bash script for Arch/CachyOS, Ubuntu/Debian, and Fedora installing Python/Tk, OpenSSL, BlueZ, NetworkManager/iw, ADB, Scrcpy, Tailscale, Flatpak, Sunshine & Moonlight.",
        "script": (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "if command -v pacman >/dev/null; then\n"
            "  sudo pacman -Syu --needed --noconfirm python python-pip tk openssl bluez bluez-utils networkmanager iw android-tools scrcpy flatpak\n"
            "elif command -v apt-get >/dev/null; then\n"
            "  sudo apt-get update && sudo apt-get install -y python3 python3-venv python3-tk openssl bluez rfkill network-manager iw android-tools-adb scrcpy flatpak curl\n"
            "elif command -v dnf >/dev/null; then\n"
            "  sudo dnf install -y python3 python3-tkinter openssl bluez NetworkManager iw android-tools flatpak curl\n"
            "fi\n"
            "sudo systemctl enable --now bluetooth\n"
            "curl -fsSL https://tailscale.com/install.sh | sh && sudo systemctl enable --now tailscaled && sudo tailscale up\n"
            "flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo\n"
            "flatpak install -y flathub dev.lizardbyte.app.Sunshine com.moonlight_stream.Moonlight"
        ),
    },
    {
        "cmd_id": "linux_fish_full_deps",
        "title": "Full Linux Fish Installer (All AstraCradle Companion Programs)",
        "category": "Linux Fish",
        "platform": "Linux",
        "description": "Complete Fish shell script (CachyOS/Arch/Ubuntu/Fedora) installing Python/Tk, OpenSSL, BlueZ, NetworkManager/iw, ADB, Scrcpy, Tailscale, Flatpak, Sunshine & Moonlight.",
        "script": (
            "# Fish Shell Full Installer for AstraCradle Dependencies\n"
            "if command -q pacman\n"
            "    sudo pacman -Syu --needed --noconfirm python python-pip tk openssl bluez bluez-utils networkmanager iw android-tools scrcpy flatpak\n"
            "else if command -q apt-get\n"
            "    sudo apt-get update; and sudo apt-get install -y python3 python3-venv python3-tk openssl bluez rfkill network-manager iw android-tools-adb scrcpy flatpak curl\n"
            "else if command -q dnf\n"
            "    sudo dnf install -y python3 python3-tkinter openssl bluez NetworkManager iw android-tools flatpak curl\n"
            "end\n"
            "sudo systemctl enable --now bluetooth\n"
            "curl -fsSL https://tailscale.com/install.sh | sh; and sudo systemctl enable --now tailscaled; and sudo tailscale up\n"
            "flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo\n"
            "flatpak install -y flathub dev.lizardbyte.app.Sunshine com.moonlight_stream.Moonlight"
        ),
    },
    {
        "cmd_id": "linux_bash_sunshine_moonlight",
        "title": "Linux Bash: Install & Harden Sunshine + Moonlight + Uinput",
        "category": "Linux Bash",
        "platform": "Linux",
        "description": "Installs Flatpak Sunshine & Moonlight and adds your user to the input group for virtual gamepad support.",
        "script": (
            "flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo && "
            "flatpak install -y flathub dev.lizardbyte.app.Sunshine com.moonlight_stream.Moonlight && "
            "sudo usermod -aG input \"$USER\" && "
            "echo 'Run AstraCradle -> Apply Hardened Tailscale Sunshine Config'"
        ),
    },
    {
        "cmd_id": "linux_fish_sunshine_moonlight",
        "title": "Linux Fish: Install & Harden Sunshine + Moonlight + Uinput",
        "category": "Linux Fish",
        "platform": "Linux",
        "description": "Fish shell equivalent for installing Flatpak Sunshine & Moonlight and enabling controller input permissions.",
        "script": (
            "flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo; "
            "and flatpak install -y flathub dev.lizardbyte.app.Sunshine com.moonlight_stream.Moonlight; "
            "and sudo usermod -aG input $USER; "
            "and echo 'Run AstraCradle -> Apply Hardened Tailscale Sunshine Config'"
        ),
    },
    {
        "cmd_id": "linux_fish_tailscale_bt",
        "title": "Linux Fish: Tailscale + BlueZ Bluetooth + Wi-Fi Tools Setup",
        "category": "Linux Fish",
        "platform": "Linux",
        "description": "Installs and starts Tailscale, BlueZ Bluetooth handshake daemon, rfkill unblock, and iw/nmcli Wi-Fi tools in Fish.",
        "script": (
            "if command -q pacman; sudo pacman -S --needed --noconfirm bluez bluez-utils networkmanager iw openssl; "
            "else if command -q apt-get; sudo apt-get update; and sudo apt-get install -y bluez rfkill network-manager iw openssl; end; "
            "and sudo rfkill unblock bluetooth; and sudo systemctl enable --now bluetooth; "
            "and curl -fsSL https://tailscale.com/install.sh | sh; and sudo systemctl enable --now tailscaled; and sudo tailscale up"
        ),
    },
    {
        "cmd_id": "linux_bash_tailscale_bt",
        "title": "Linux Bash: Tailscale + BlueZ Bluetooth + Wi-Fi Tools Setup",
        "category": "Linux Bash",
        "platform": "Linux",
        "description": "Installs and starts Tailscale, BlueZ Bluetooth handshake daemon, rfkill unblock, and iw/nmcli Wi-Fi tools in Bash.",
        "script": (
            "if command -v pacman >/dev/null; then sudo pacman -S --needed --noconfirm bluez bluez-utils networkmanager iw openssl; "
            "elif command -v apt-get >/dev/null; then sudo apt-get update && sudo apt-get install -y bluez rfkill network-manager iw openssl; fi && "
            "sudo rfkill unblock bluetooth && sudo systemctl enable --now bluetooth && "
            "curl -fsSL https://tailscale.com/install.sh | sh && sudo systemctl enable --now tailscaled && sudo tailscale up"
        ),
    },
    {
        "cmd_id": "linux_fish_adb_scrcpy",
        "title": "Linux Fish: Install Android ADB & Scrcpy + Reverse Tunnel",
        "category": "Linux Fish",
        "platform": "Linux",
        "description": "Fish shell installer for Android Platform Tools + scrcpy and immediate USB reverse tunnel.",
        "script": (
            "if command -q pacman; sudo pacman -S --needed --noconfirm android-tools scrcpy; "
            "else if command -q apt-get; sudo apt-get update; and sudo apt-get install -y android-tools-adb scrcpy; end; "
            "and adb start-server; and adb devices -l"
        ),
    },
    {
        "cmd_id": "linux_fish_install_astracradle",
        "title": "Linux Fish: Full Install AstraCradle + Dependencies",
        "category": "Linux Fish",
        "platform": "Linux",
        "description": "Native Fish command to install AstraCradle, create .venv, install all companion tools, and run full self-check.",
        "script": "fish install_astracradle.fish --no-launch; and ./.venv/bin/python -m astracradle --install-deps --shell-kind fish --yes; and ./.venv/bin/python -m astracradle --self-check",
    },
    {
        "cmd_id": "linux_fish_failover_watch",
        "title": "Linux Fish: Start Ethernet <-> Wi-Fi Failover Monitor",
        "category": "Linux Fish",
        "platform": "Linux",
        "description": "Continuously pings primary Ethernet and fails over seamlessly to Wi-Fi, recovering after 5m stable pings.",
        "script": "python3 -m astracradle --failover-status; and python3 -m astracradle --failover-watch",
    },
]


class CommandDatabaseManager:
    """SQLite-persisted install commands & reusable script database with JSON shadow backup."""

    def __init__(self, cfg_mgr: ConfigManager, journal: ForensicJournal | None = None) -> None:
        self.cfg_mgr = cfg_mgr
        self.journal = journal
        self.db_path: Path = cfg_mgr.base_dir / "commands.db"
        self.backup_path: Path = cfg_mgr.state_dir / "commands_backup.json"
        self._init_and_seed()

    def _connect(self) -> sqlite3.Connection:
        self.cfg_mgr.ensure_directories()
        conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS commands (
                cmd_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                category TEXT NOT NULL,
                platform TEXT NOT NULL,
                script TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                is_builtin INTEGER NOT NULL DEFAULT 0,
                favorite INTEGER NOT NULL DEFAULT 0,
                use_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()

    def _init_and_seed(self) -> None:
        try:
            with self._connect() as conn:
                self._create_schema(conn)
                self._seed_missing_builtins(conn)
            self._write_backup()
        except Exception:
            self.verify_and_repair()

    def _seed_missing_builtins(self, conn: sqlite3.Connection) -> int:
        now = utc_now_iso()
        added = 0
        for item in BUILTIN_COMMANDS:
            row = conn.execute("SELECT cmd_id FROM commands WHERE cmd_id = ?", (item["cmd_id"],)).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO commands (
                        cmd_id, title, category, platform, script, description,
                        is_builtin, favorite, use_count, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 1, 0, 0, ?, ?)
                    """,
                    (
                        item["cmd_id"],
                        item["title"],
                        item["category"],
                        item["platform"],
                        item["script"],
                        item["description"],
                        now,
                        now,
                    ),
                )
                added += 1
        conn.commit()
        return added

    def _write_backup(self) -> None:
        try:
            items = self.list_commands()
            tmp = self.backup_path.with_suffix(".tmp")
            tmp.write_text(json.dumps([asdict(c) for c in items], indent=2), encoding="utf-8")
            tmp.replace(self.backup_path)
        except Exception:
            pass

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> CommandRecord:
        return CommandRecord(
            cmd_id=str(row["cmd_id"]),
            title=str(row["title"]),
            category=str(row["category"]),
            platform=str(row["platform"]),
            script=str(row["script"]),
            description=str(row["description"] or ""),
            is_builtin=bool(row["is_builtin"]),
            favorite=bool(row["favorite"]),
            use_count=int(row["use_count"] or 0),
            created_at=str(row["created_at"] or ""),
            updated_at=str(row["updated_at"] or ""),
        )

    def list_commands(
        self,
        category: str = "All",
        search: str = "",
        platform: str = "All",
    ) -> list[CommandRecord]:
        clauses: list[str] = []
        params: list[str] = []
        if category and category != "All":
            clauses.append("category = ?")
            params.append(category)
        if platform and platform != "All":
            clauses.append("(platform LIKE ? OR platform = 'All')")
            params.append(f"%{platform}%")
        if search.strip():
            q = f"%{search.strip()}%"
            clauses.append("(title LIKE ? OR script LIKE ? OR description LIKE ? OR category LIKE ?)")
            params.extend([q, q, q, q])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM commands {where} ORDER BY favorite DESC, is_builtin ASC, use_count DESC, title ASC"
        try:
            with self._connect() as conn:
                rows = conn.execute(sql, params).fetchall()
            return [self._row_to_record(r) for r in rows]
        except Exception:
            self.verify_and_repair()
            with self._connect() as conn:
                rows = conn.execute(sql, params).fetchall()
            return [self._row_to_record(r) for r in rows]

    def get_categories(self) -> list[str]:
        base = [
            "All",
            "Install",
            "Linux Bash",
            "Linux Fish",
            "Tailscale",
            "Sunshine & Moonlight",
            "iOS & Apple Watch",
            "Android",
            "Diagnostics & Self-Heal",
            "Custom",
        ]
        try:
            with self._connect() as conn:
                rows = conn.execute("SELECT DISTINCT category FROM commands ORDER BY category ASC").fetchall()
            for r in rows:
                cat = str(r["category"])
                if cat and cat not in base:
                    base.append(cat)
        except Exception:
            pass
        return base

    def get_command(self, cmd_id: str) -> CommandRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM commands WHERE cmd_id = ?", (cmd_id,)).fetchone()
        return self._row_to_record(row) if row is not None else None

    def add_command(
        self,
        title: str,
        category: str,
        platform: str,
        script: str,
        description: str = "",
        cmd_id: str | None = None,
        favorite: bool = False,
    ) -> CommandRecord:
        clean_title = title.strip()
        clean_script = script.strip()
        if not clean_title or not clean_script:
            raise ValueError("Command title and script text are required.")
        cid = (cmd_id or f"custom_{uuid.uuid4().hex[:8]}").strip()
        now = utc_now_iso()
        cat = (category or "Custom").strip()
        plat = (platform or "All").strip()
        desc = description.strip()
        with self._connect() as conn:
            self._create_schema(conn)
            existing = conn.execute("SELECT created_at, is_builtin, use_count FROM commands WHERE cmd_id = ?", (cid,)).fetchone()
            if existing is not None:
                conn.execute(
                    """
                    UPDATE commands
                    SET title = ?, category = ?, platform = ?, script = ?, description = ?, favorite = ?, updated_at = ?
                    WHERE cmd_id = ?
                    """,
                    (clean_title, cat, plat, clean_script, desc, int(favorite), now, cid),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO commands (
                        cmd_id, title, category, platform, script, description,
                        is_builtin, favorite, use_count, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, 0, ?, ?)
                    """,
                    (cid, clean_title, cat, plat, clean_script, desc, int(favorite), now, now),
                )
            conn.commit()
        self._write_backup()
        if self.journal:
            self.journal.log_event(
                component="CommandDatabase",
                action="save_command",
                how=f"Saved command '{clean_title}' [{cat}/{plat}] into commands.db",
                why="Persist user or install command script for quick dropdown reuse",
                outcome="ok",
            )
        rec = self.get_command(cid)
        assert rec is not None
        return rec

    def delete_command(self, cmd_id: str) -> tuple[bool, str]:
        rec = self.get_command(cmd_id)
        if rec is None:
            return False, "Command not found."
        if rec.is_builtin:
            return False, "Built-in install commands are protected; create or delete custom commands."
        with self._connect() as conn:
            conn.execute("DELETE FROM commands WHERE cmd_id = ?", (cmd_id,))
            conn.commit()
        self._write_backup()
        if self.journal:
            self.journal.log_event(
                component="CommandDatabase",
                action="delete_command",
                how=f"Removed custom command '{rec.title}' ({cmd_id})",
                why="User deleted stored command from vault",
                outcome="ok",
            )
        return True, f"Deleted '{rec.title}'."

    def mark_copied(self, cmd_id: str) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE commands SET use_count = use_count + 1, updated_at = ? WHERE cmd_id = ?",
                    (utc_now_iso(), cmd_id),
                )
                conn.commit()
            self._write_backup()
        except Exception:
            pass

    def verify_and_repair(self) -> tuple[bool, str]:
        """Verifies SQLite commands.db integrity; rebuilds from shadow backup + seeds built-ins on corruption."""
        repaired = False
        notes: list[str] = []
        healthy = False
        if self.db_path.exists():
            try:
                with self._connect() as conn:
                    chk = conn.execute("PRAGMA integrity_check").fetchone()
                    tbl = conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name='commands'"
                    ).fetchone()
                    healthy = bool(chk and str(chk[0]).lower() == "ok" and tbl is not None)
            except Exception:
                healthy = False

        if not healthy:
            repaired = True
            if self.db_path.exists():
                try:
                    self.db_path.unlink()
                except Exception:
                    pass
            with self._connect() as conn:
                self._create_schema(conn)
                restored = 0
                if self.backup_path.exists():
                    try:
                        raw = json.loads(self.backup_path.read_text(encoding="utf-8"))
                        if isinstance(raw, list):
                            for item in raw:
                                if isinstance(item, dict) and item.get("cmd_id") and item.get("script"):
                                    conn.execute(
                                        """
                                        INSERT OR IGNORE INTO commands (
                                            cmd_id, title, category, platform, script, description,
                                            is_builtin, favorite, use_count, created_at, updated_at
                                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                        """,
                                        (
                                            str(item["cmd_id"]),
                                            str(item.get("title", "Restored Command")),
                                            str(item.get("category", "Custom")),
                                            str(item.get("platform", "All")),
                                            str(item["script"]),
                                            str(item.get("description", "")),
                                            int(bool(item.get("is_builtin", False))),
                                            int(bool(item.get("favorite", False))),
                                            int(item.get("use_count", 0) or 0),
                                            str(item.get("created_at") or utc_now_iso()),
                                            str(item.get("updated_at") or utc_now_iso()),
                                        ),
                                    )
                                    restored += 1
                    except Exception:
                        pass
                seeded = self._seed_missing_builtins(conn)
                conn.commit()
            notes.append(f"Rebuilt commands.db ({restored} restored from shadow backup, {seeded} built-ins seeded)")
        else:
            with self._connect() as conn:
                seeded = self._seed_missing_builtins(conn)
            if seeded:
                repaired = True
                notes.append(f"Re-seeded {seeded} missing built-in command(s)")

        self._write_backup()
        if not notes:
            notes.append("commands.db SQLite integrity OK")
        return repaired, "; ".join(notes)
