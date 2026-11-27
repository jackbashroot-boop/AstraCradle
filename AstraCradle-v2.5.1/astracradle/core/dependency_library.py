"""AstraCradle Companion Dependency Library: Detection, Full Bash/Fish Installers & Permission-Gated Auto-Setup."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import platform
import shutil
import subprocess

from .astrabeam import SunshineMoonlightBridge, TailscaleDiscovery
from .elevation import ElevationRunner
from .journal import ForensicJournal


@dataclass(frozen=True)
class DependencySpec:
    dep_id: str
    name: str
    role: str
    binary_names: tuple[str, ...]
    flatpak_id: str = ""
    required_for: str = ""
    linux_bash_cmd: str = ""
    linux_fish_cmd: str = ""
    macos_cmd: str = ""
    windows_cmd: str = ""
    post_setup_note: str = ""
    needs_root: bool = True


@dataclass
class DependencyStatus:
    dep_id: str
    name: str
    role: str
    installed: bool
    binary_path: str
    install_source: str  # native_bin | flatpak | missing
    required_for: str
    recommended_cmd: str
    bash_cmd: str
    fish_cmd: str
    post_setup_note: str


@dataclass
class InstallOutcome:
    dep_id: str
    name: str
    ok: bool
    commands_used: str
    message: str
    post_setup_applied: list[str] = field(default_factory=list)


DEPENDENCY_CATALOG: tuple[DependencySpec, ...] = (
    DependencySpec(
        dep_id="tailscale",
        name="Tailscale Zero-Trust Mesh",
        role="Private WireGuard mesh for PC, iPhone, Apple Watch & Android with zero open ports",
        binary_names=("tailscale",),
        required_for="Remote Stream & Devices (AstraBeam + Moonlight over Tailscale)",
        linux_bash_cmd="curl -fsSL https://tailscale.com/install.sh | sh && sudo systemctl enable --now tailscaled && sudo tailscale up",
        linux_fish_cmd="curl -fsSL https://tailscale.com/install.sh | sh; and sudo systemctl enable --now tailscaled; and sudo tailscale up",
        macos_cmd="brew install --cask tailscale && open -a Tailscale",
        windows_cmd="winget install --id Tailscale.Tailscale -e && tailscale up",
        post_setup_note="Enables tailscaled systemd unit and authenticates node on your private tailnet.",
        needs_root=True,
    ),
    DependencySpec(
        dep_id="sunshine",
        name="Sunshine Streaming Host",
        role="Hardware-accelerated 120fps desktop & game streaming server for Moonlight (iOS / Android / PC)",
        binary_names=("sunshine",),
        flatpak_id="dev.lizardbyte.app.Sunshine",
        required_for="Native 120fps Moonlight streaming on iPhone, iPad, Android & secondary PCs",
        linux_bash_cmd=(
            "if command -v pacman >/dev/null; then sudo pacman -S --needed --noconfirm flatpak || true; fi && "
            "if command -v apt-get >/dev/null; then sudo apt-get update && sudo apt-get install -y flatpak; fi && "
            "flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo && "
            "flatpak install -y flathub dev.lizardbyte.app.Sunshine"
        ),
        linux_fish_cmd=(
            "if command -q pacman; sudo pacman -S --needed --noconfirm flatpak; else if command -q apt-get; sudo apt-get update; and sudo apt-get install -y flatpak; end; "
            "and flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo; "
            "and flatpak install -y flathub dev.lizardbyte.app.Sunshine"
        ),
        macos_cmd="brew install lizardbyte/homebrew/sunshine",
        windows_cmd="winget install --id LizardByte.Sunshine -e",
        post_setup_note="Applies AstraCradle hardened Tailscale config (upnp=disabled, mandatory TLS/LAN/WAN encryption).",
        needs_root=False,
    ),
    DependencySpec(
        dep_id="moonlight",
        name="Moonlight Game/Desktop Client",
        role="Ultra-low-latency streaming client connecting to Sunshine hosts",
        binary_names=("moonlight", "moonlight-qt"),
        flatpak_id="com.moonlight_stream.Moonlight",
        required_for="Launching remote desktop/game sessions from this machine",
        linux_bash_cmd=(
            "if command -v pacman >/dev/null; then sudo pacman -S --needed --noconfirm moonlight-qt || flatpak install -y flathub com.moonlight_stream.Moonlight; "
            "elif command -v flatpak >/dev/null; then flatpak install -y flathub com.moonlight_stream.Moonlight; fi"
        ),
        linux_fish_cmd=(
            "if command -q pacman; sudo pacman -S --needed --noconfirm moonlight-qt; or flatpak install -y flathub com.moonlight_stream.Moonlight; "
            "else if command -q flatpak; flatpak install -y flathub com.moonlight_stream.Moonlight; end"
        ),
        macos_cmd="brew install --cask moonlight",
        windows_cmd="winget install --id MoonlightGameStreamingProject.Moonlight -e",
        post_setup_note="Ready to launch against your Tailscale Sunshine host IP.",
        needs_root=False,
    ),
    DependencySpec(
        dep_id="adb",
        name="Android ADB (Platform Tools)",
        role="USB & Wireless Android debugging + zero-Wi-Fi reverse tunneling (adb reverse)",
        binary_names=("adb",),
        required_for="Android auto-discovery & USB zero-Wi-Fi AstraBeam streaming",
        linux_bash_cmd=(
            "if command -v pacman >/dev/null; then sudo pacman -S --needed --noconfirm android-tools; "
            "elif command -v apt-get >/dev/null; then sudo apt-get update && sudo apt-get install -y android-tools-adb; "
            "elif command -v dnf >/dev/null; then sudo dnf install -y android-tools; fi"
        ),
        linux_fish_cmd=(
            "if command -q pacman; sudo pacman -S --needed --noconfirm android-tools; "
            "else if command -q apt-get; sudo apt-get update; and sudo apt-get install -y android-tools-adb; "
            "else if command -q dnf; sudo dnf install -y android-tools; end"
        ),
        macos_cmd="brew install android-platform-tools",
        windows_cmd="winget install --id Google.PlatformTools -e",
        post_setup_note="Enables `adb reverse tcp:47990 tcp:47990` USB streaming.",
        needs_root=True,
    ),
    DependencySpec(
        dep_id="scrcpy",
        name="Android Scrcpy Bridge",
        role="High-FPS low-latency Android display & control over USB/Wi-Fi",
        binary_names=("scrcpy",),
        required_for="Designated Android control & mirroring",
        linux_bash_cmd=(
            "if command -v pacman >/dev/null; then sudo pacman -S --needed --noconfirm scrcpy; "
            "elif command -v apt-get >/dev/null; then sudo apt-get update && sudo apt-get install -y scrcpy; "
            "elif command -v dnf >/dev/null; then sudo dnf install -y scrcpy; fi"
        ),
        linux_fish_cmd=(
            "if command -q pacman; sudo pacman -S --needed --noconfirm scrcpy; "
            "else if command -q apt-get; sudo apt-get update; and sudo apt-get install -y scrcpy; "
            "else if command -q dnf; sudo dnf install -y scrcpy; end"
        ),
        macos_cmd="brew install scrcpy",
        windows_cmd="winget install --id Genymobile.scrcpy -e",
        post_setup_note="Launch with `scrcpy -s <serial>` from Designated Devices.",
        needs_root=True,
    ),
    DependencySpec(
        dep_id="openssl",
        name="OpenSSL (TLS 1.2+ Cert Engine)",
        role="Generates 2048-bit RSA SAN certificates for zero-hole HTTPS AstraBeam",
        binary_names=("openssl",),
        required_for="AstraBeam encrypted TLS hosting & iOS/Android/Watch PWA",
        linux_bash_cmd=(
            "if command -v pacman >/dev/null; then sudo pacman -S --needed --noconfirm openssl; "
            "elif command -v apt-get >/dev/null; then sudo apt-get update && sudo apt-get install -y openssl; "
            "elif command -v dnf >/dev/null; then sudo dnf install -y openssl; fi"
        ),
        linux_fish_cmd=(
            "if command -q pacman; sudo pacman -S --needed --noconfirm openssl; "
            "else if command -q apt-get; sudo apt-get update; and sudo apt-get install -y openssl; "
            "else if command -q dnf; sudo dnf install -y openssl; end"
        ),
        macos_cmd="brew install openssl@3",
        windows_cmd="winget install --id ShiningLight.OpenSSL.Light -e",
        post_setup_note="Verified automatically by AstraBeam TLS Certificate Self-Heal.",
        needs_root=True,
    ),
    DependencySpec(
        dep_id="bluetoothctl",
        name="BlueZ Bluetooth & Handshake Stack",
        role="Bluetooth pairing, proximity scan & weak-Wi-Fi handshake fallback",
        binary_names=("bluetoothctl",),
        required_for="iPhone/Apple Watch/Android Bluetooth proximity & fallback",
        linux_bash_cmd=(
            "if command -v pacman >/dev/null; then sudo pacman -S --needed --noconfirm bluez bluez-utils; "
            "elif command -v apt-get >/dev/null; then sudo apt-get update && sudo apt-get install -y bluez rfkill; "
            "elif command -v dnf >/dev/null; then sudo dnf install -y bluez; fi && "
            "sudo systemctl enable --now bluetooth"
        ),
        linux_fish_cmd=(
            "if command -q pacman; sudo pacman -S --needed --noconfirm bluez bluez-utils; "
            "else if command -q apt-get; sudo apt-get update; and sudo apt-get install -y bluez rfkill; "
            "else if command -q dnf; sudo dnf install -y bluez; end; "
            "and sudo systemctl enable --now bluetooth"
        ),
        macos_cmd="# macOS uses built-in CoreBluetooth / system_profiler SPBluetoothDataType",
        windows_cmd="# Windows uses built-in Bluetooth stack (PowerShell Get-PnpDevice)",
        post_setup_note="Enables `bluetooth.service` and unblocks soft-blocked radio via rfkill.",
        needs_root=True,
    ),
    DependencySpec(
        dep_id="nmcli",
        name="NetworkManager & Wi-Fi Probe Tools",
        role="Measures Wi-Fi RSSI/channel congestion and triggers automatic channel & BT optimization",
        binary_names=("nmcli", "iw"),
        required_for="Wi-Fi Channel Optimizer, RF Waves & weak-Wi-Fi Bluetooth fallback detection",
        linux_bash_cmd=(
            "if command -v pacman >/dev/null; then sudo pacman -S --needed --noconfirm networkmanager iw; "
            "elif command -v apt-get >/dev/null; then sudo apt-get update && sudo apt-get install -y network-manager iw; "
            "elif command -v dnf >/dev/null; then sudo dnf install -y NetworkManager iw; fi"
        ),
        linux_fish_cmd=(
            "if command -q pacman; sudo pacman -S --needed --noconfirm networkmanager iw; "
            "else if command -q apt-get; sudo apt-get update; and sudo apt-get install -y network-manager iw; "
            "else if command -q dnf; sudo dnf install -y NetworkManager iw; end"
        ),
        macos_cmd="# macOS uses built-in Wi-Fi framework (networksetup / airport)",
        windows_cmd="# Windows uses built-in netsh wlan",
        post_setup_note="Provides real-time RSSI dBm & channel telemetry.",
        needs_root=True,
    ),
    DependencySpec(
        dep_id="flatpak",
        name="Flatpak Universal App Runtime",
        role="Cross-distro Linux sandbox installer used by Sunshine & Moonlight",
        binary_names=("flatpak",),
        required_for="Installing sandboxed Linux Sunshine & Moonlight builds",
        linux_bash_cmd=(
            "if command -v pacman >/dev/null; then sudo pacman -S --needed --noconfirm flatpak; "
            "elif command -v apt-get >/dev/null; then sudo apt-get update && sudo apt-get install -y flatpak; "
            "elif command -v dnf >/dev/null; then sudo dnf install -y flatpak; fi && "
            "flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo"
        ),
        linux_fish_cmd=(
            "if command -q pacman; sudo pacman -S --needed --noconfirm flatpak; "
            "else if command -q apt-get; sudo apt-get update; and sudo apt-get install -y flatpak; "
            "else if command -q dnf; sudo dnf install -y flatpak; end; "
            "and flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo"
        ),
        macos_cmd="# Not needed on macOS (use Homebrew)",
        windows_cmd="# Not needed on Windows (use winget)",
        post_setup_note="Configures Flathub repository automatically.",
        needs_root=True,
    ),
)


class DependencyLibraryManager:
    """Manages AstraCradle companion dependencies, Linux Bash/Fish installers & permission-gated auto-setup."""

    def __init__(
        self,
        journal: ForensicJournal | None = None,
        elevation: ElevationRunner | None = None,
    ) -> None:
        self.journal = journal
        self.elevation = elevation

    @staticmethod
    def detect_user_shell() -> str:
        sh = Path(os.environ.get("SHELL", "")).name.lower()
        return "fish" if "fish" in sh else "bash"

    @staticmethod
    def _has_flatpak_app(app_id: str) -> bool:
        if not app_id or not shutil.which("flatpak"):
            return False
        try:
            res = subprocess.run(["flatpak", "info", app_id], capture_output=True, text=True, timeout=2.5)
            return res.returncode == 0
        except Exception:
            return False

    def inspect_dependency(self, spec: DependencySpec, shell_pref: str | None = None) -> DependencyStatus:
        sh_kind = shell_pref or self.detect_user_shell()
        found_bin = ""
        src = "missing"
        for b in spec.binary_names:
            p = shutil.which(b)
            if p:
                found_bin = p
                src = "native_bin"
                break
        if not found_bin and spec.flatpak_id and self._has_flatpak_app(spec.flatpak_id):
            found_bin = f"flatpak:{spec.flatpak_id}"
            src = "flatpak"

        sys_name = platform.system().lower()
        if sys_name == "darwin":
            rec_cmd = spec.macos_cmd
        elif sys_name == "windows":
            rec_cmd = spec.windows_cmd
        else:
            rec_cmd = spec.linux_fish_cmd if sh_kind == "fish" else spec.linux_bash_cmd

        return DependencyStatus(
            dep_id=spec.dep_id,
            name=spec.name,
            role=spec.role,
            installed=bool(found_bin),
            binary_path=found_bin,
            install_source=src,
            required_for=spec.required_for,
            recommended_cmd=rec_cmd,
            bash_cmd=spec.linux_bash_cmd,
            fish_cmd=spec.linux_fish_cmd,
            post_setup_note=spec.post_setup_note,
        )

    def inspect_all(self, shell_pref: str | None = None) -> list[DependencyStatus]:
        return [self.inspect_dependency(spec, shell_pref=shell_pref) for spec in DEPENDENCY_CATALOG]

    @staticmethod
    def full_stack_script(shell_kind: str = "bash") -> str:
        """Returns a complete copyable Linux Bash or Fish script installing all AstraCradle companion tools."""
        if shell_kind == "fish":
            return (
                "# AstraCradle Full Dependency Stack Installer (Linux Fish Shell)\n"
                "if command -q pacman\n"
                "    sudo pacman -Syu --needed --noconfirm python python-pip tk openssl bluez bluez-utils networkmanager iw android-tools scrcpy flatpak\n"
                "else if command -q apt-get\n"
                "    sudo apt-get update; and sudo apt-get install -y python3 python3-venv python3-tk openssl bluez rfkill network-manager iw android-tools-adb scrcpy flatpak curl\n"
                "else if command -q dnf\n"
                "    sudo dnf install -y python3 python3-tkinter openssl bluez NetworkManager iw android-tools flatpak curl\n"
                "end\n"
                "sudo systemctl enable --now bluetooth\n"
                "curl -fsSL https://tailscale.com/install.sh | sh; and sudo systemctl enable --now tailscaled\n"
                "flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo\n"
                "flatpak install -y flathub dev.lizardbyte.app.Sunshine com.moonlight_stream.Moonlight"
            )
        return (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "# AstraCradle Full Dependency Stack Installer (Linux Bash)\n"
            "if command -v pacman >/dev/null; then\n"
            "  sudo pacman -Syu --needed --noconfirm python python-pip tk openssl bluez bluez-utils networkmanager iw android-tools scrcpy flatpak\n"
            "elif command -v apt-get >/dev/null; then\n"
            "  sudo apt-get update && sudo apt-get install -y python3 python3-venv python3-tk openssl bluez rfkill network-manager iw android-tools-adb scrcpy flatpak curl\n"
            "elif command -v dnf >/dev/null; then\n"
            "  sudo dnf install -y python3 python3-tkinter openssl bluez NetworkManager iw android-tools flatpak curl\n"
            "fi\n"
            "sudo systemctl enable --now bluetooth\n"
            "curl -fsSL https://tailscale.com/install.sh | sh && sudo systemctl enable --now tailscaled\n"
            "flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo\n"
            "flatpak install -y flathub dev.lizardbyte.app.Sunshine com.moonlight_stream.Moonlight"
        )

    def install_and_setup(
        self,
        dep_id: str,
        *,
        permitted: bool,
        shell_kind: str = "bash",
        dry_run: bool = False,
        tailscale_ip: str = "",
        sunshine_conf_path: Path | None = None,
    ) -> InstallOutcome:
        spec = next((s for s in DEPENDENCY_CATALOG if s.dep_id == dep_id), None)
        if spec is None:
            return InstallOutcome(dep_id, dep_id, False, "", f"Unknown dependency '{dep_id}'.")

        status = self.inspect_dependency(spec, shell_pref=shell_kind)
        cmd_str = status.recommended_cmd or status.bash_cmd

        if not permitted:
            return InstallOutcome(
                dep_id=dep_id,
                name=spec.name,
                ok=False,
                commands_used=cmd_str,
                message="Permission required: enable auto-install permission before installing external programs.",
            )

        if dry_run:
            return InstallOutcome(
                dep_id=dep_id,
                name=spec.name,
                ok=True,
                commands_used=cmd_str,
                message=f"[Dry-Run] Would execute: {cmd_str}",
            )

        post_notes: list[str] = []
        if status.installed:
            post_notes = self._apply_post_setup(spec.dep_id, tailscale_ip=tailscale_ip, sunshine_conf_path=sunshine_conf_path)
            return InstallOutcome(
                dep_id=dep_id,
                name=spec.name,
                ok=True,
                commands_used=cmd_str,
                message=f"{spec.name} is already installed ({status.binary_path}); verified setup.",
                post_setup_applied=post_notes,
            )

        # Execute install using the exact shell script (with ElevationRunner if root needed on Linux)
        sys_name = platform.system().lower()
        if sys_name == "linux":
            shell_bin = shutil.which("fish") if (shell_kind == "fish" and shutil.which("fish")) else (shutil.which("bash") or "/bin/sh")
            argv = [shell_bin, "-c", cmd_str.replace("sudo ", "") if (os.geteuid() == 0) else cmd_str]
        elif sys_name == "darwin":
            argv = ["zsh", "-c", cmd_str]
        else:
            argv = ["powershell", "-NoProfile", "-Command", cmd_str]

        ok_exec = False
        exec_msg = ""
        try:
            if sys_name == "linux" and spec.needs_root and self.elevation is not None and os.geteuid() != 0:
                res = self.elevation.run_privileged(argv, title=f"Install {spec.name}", detail=cmd_str, dry_run=False)
                ok_exec = res.ok
                exec_msg = res.stderr or res.stdout or res.message
            else:
                proc = subprocess.run(argv, capture_output=True, text=True, timeout=180.0)
                ok_exec = proc.returncode == 0
                exec_msg = (proc.stderr or proc.stdout or "").strip()
        except Exception as exc:
            exec_msg = str(exc)

        if ok_exec:
            post_notes = self._apply_post_setup(spec.dep_id, tailscale_ip=tailscale_ip, sunshine_conf_path=sunshine_conf_path)
        if self.journal:
            self.journal.log_event(
                component="DependencyLibrary",
                action=f"install_{dep_id}",
                how=f"Ran `{cmd_str[:160]}` -> {'OK' if ok_exec else 'FAILED'}",
                why=f"Permission-granted installation & auto-setup for {spec.name}",
                outcome="ok" if ok_exec else "error",
            )
        return InstallOutcome(
            dep_id=dep_id,
            name=spec.name,
            ok=ok_exec,
            commands_used=cmd_str,
            message=(f"Installed & configured {spec.name}." if ok_exec else f"Install did not complete: {exec_msg[:240]}"),
            post_setup_applied=post_notes,
        )

    def _apply_post_setup(
        self,
        dep_id: str,
        *,
        tailscale_ip: str = "",
        sunshine_conf_path: Path | None = None,
    ) -> list[str]:
        notes: list[str] = []
        if dep_id == "sunshine":
            ts_ip = tailscale_ip or TailscaleDiscovery.get_status().self_ip
            ok_s, msg_s = SunshineMoonlightBridge.apply_hardened_tailscale_config(ts_ip, conf_path=sunshine_conf_path)
            notes.append(msg_s if ok_s else f"Sunshine config note: {msg_s}")
        elif dep_id == "bluetoothctl":
            from .mobile_continuity import BluetoothHandshakeManager

            _, msg_b = BluetoothHandshakeManager.ensure_adapter_healthy()
            notes.append(msg_b)
        return notes

    def install_all_missing(
        self,
        *,
        permitted: bool,
        shell_kind: str = "bash",
        dry_run: bool = False,
        tailscale_ip: str = "",
    ) -> list[InstallOutcome]:
        outcomes: list[InstallOutcome] = []
        for st in self.inspect_all(shell_pref=shell_kind):
            if not st.installed:
                outcomes.append(
                    self.install_and_setup(
                        st.dep_id,
                        permitted=permitted,
                        shell_kind=shell_kind,
                        dry_run=dry_run,
                        tailscale_ip=tailscale_ip,
                    )
                )
        return outcomes
