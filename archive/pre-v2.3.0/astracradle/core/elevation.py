"""Cross-OS Interactive Privilege Elevation (sudo password / pkexec / macOS sudo / Windows UAC)."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
from typing import Callable


@dataclass
class ElevationResult:
    ok: bool
    returncode: int
    stdout: str
    stderr: str
    cancelled: bool = False
    message: str = ""
    method: str = ""


def is_elevated() -> bool:
    if platform.system() == "Windows":
        try:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
        except Exception:
            return False
    try:
        return os.geteuid() == 0
    except AttributeError:
        return False


class ElevationRunner:
    """Runs privileged OS toggles safely via user-entered sudo password, pkexec, or Windows UAC."""

    def __init__(
        self,
        password_prompt_cb: Callable[[str, str], str | None] | None = None,
    ) -> None:
        self.password_prompt_cb = password_prompt_cb

    def run_privileged(
        self,
        argv: list[str],
        reason: str,
        title: str = "Administrator / Sudo Permission Required",
        stdin_text: str = "",
        timeout: int = 20,
    ) -> ElevationResult:
        if not argv:
            return ElevationResult(False, 1, "", "Empty command", message="Empty elevated command.")

        if is_elevated():
            try:
                proc = subprocess.run(
                    argv,
                    input=stdin_text or None,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    check=False,
                )
                return ElevationResult(
                    proc.returncode == 0,
                    proc.returncode,
                    proc.stdout or "",
                    proc.stderr or "",
                    message="Executed with existing administrator privileges."
                    if proc.returncode == 0
                    else (proc.stderr or proc.stdout or f"Exit code {proc.returncode}").strip(),
                    method="direct",
                )
            except Exception as exc:
                return ElevationResult(False, 1, "", str(exc), message=str(exc))

        sys_name = platform.system()
        if sys_name == "Windows":
            try:
                import ctypes
                rc = ctypes.windll.shell32.ShellExecuteW(  # type: ignore[attr-defined]
                    None,
                    "runas",
                    argv[0],
                    subprocess.list2cmdline(argv[1:]) if len(argv) > 1 else None,
                    None,
                    1,
                )
                ok = int(rc) > 32
                return ElevationResult(
                    ok,
                    0 if ok else 1,
                    "",
                    "" if ok else f"UAC rejected/failed ({rc})",
                    cancelled=(not ok),
                    message="Approved via Windows UAC prompt." if ok else "Windows UAC prompt cancelled or denied.",
                    method="uac",
                )
            except Exception as exc:
                return ElevationResult(False, 1, "", str(exc), message=str(exc), method="uac")

        # POSIX (Linux / macOS): prompt user via GUI Sudo dialog (with retry) or pkexec
        cmd_preview = " ".join(argv)
        retry_prefix = ""
        for _attempt in range(3):
            secret: str | None = None
            if self.password_prompt_cb is not None:
                detail = f"{retry_prefix}{reason}\n\nCommand: {cmd_preview}"
                secret = self.password_prompt_cb(title, detail)
                if secret is None:
                    return ElevationResult(
                        False,
                        1,
                        "",
                        "User cancelled elevation prompt.",
                        cancelled=True,
                        message="Cancelled — administrator password not entered.",
                    )

            if secret == "__PKEXEC__" or (secret is None and sys_name == "Linux" and shutil.which("pkexec") and (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))):
                if not shutil.which("pkexec"):
                    return ElevationResult(False, 1, "", "pkexec not found", message="Desktop Polkit (pkexec) unavailable.", method="pkexec")
                try:
                    proc = subprocess.run(
                        ["pkexec", *argv],
                        input=stdin_text or None,
                        capture_output=True,
                        text=True,
                        timeout=timeout,
                        check=False,
                    )
                    cancelled = proc.returncode in (126, 127)
                    return ElevationResult(
                        proc.returncode == 0,
                        proc.returncode,
                        proc.stdout or "",
                        proc.stderr or "",
                        cancelled=cancelled,
                        message="Executed via desktop authentication prompt."
                        if proc.returncode == 0
                        else (proc.stderr or f"pkexec exited {proc.returncode}").strip(),
                        method="pkexec",
                    )
                except Exception as exc:
                    return ElevationResult(False, 1, "", str(exc), message=str(exc), method="pkexec")

            if not secret or not shutil.which("sudo"):
                return ElevationResult(
                    False,
                    1,
                    "",
                    "No sudo password provided or sudo unavailable.",
                    cancelled=True,
                    message="Administrator authentication required.",
                )

            try:
                payload = f"{secret}\n{stdin_text}"
                secret = ""  # wipe local reference promptly
                proc = subprocess.run(
                    ["sudo", "-S", "-k", "-p", "", *argv],
                    input=payload,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    check=False,
                )
                payload = ""
                err_txt = (proc.stderr or "").strip()
                if proc.returncode != 0 and ("incorrect password" in err_txt.lower() or "sorry, try again" in err_txt.lower()):
                    if self.password_prompt_cb is not None and _attempt < 2:
                        retry_prefix = "Incorrect sudo password — please try again.\n\n"
                        continue
                    return ElevationResult(
                        False,
                        proc.returncode,
                        proc.stdout or "",
                        err_txt,
                        message="Incorrect sudo password — please try again.",
                        method="sudo",
                    )
                return ElevationResult(
                    proc.returncode == 0,
                    proc.returncode,
                    proc.stdout or "",
                    err_txt,
                    message="Applied with sudo privileges."
                    if proc.returncode == 0
                    else (err_txt or proc.stdout or f"sudo exited {proc.returncode}").strip(),
                    method="sudo",
                )
            except Exception as exc:
                return ElevationResult(False, 1, "", str(exc), message=str(exc), method="sudo")
        return ElevationResult(False, 1, "", "Authentication failed", message="Incorrect sudo password.", method="sudo")

    def write_privileged_file(
        self,
        target_path: Path,
        content: str,
        reason: str,
        mode: str = "644",
    ) -> ElevationResult:
        """Atomically writes a protected system file (e.g. /etc/hosts) using elevated install/tee."""
        if is_elevated():
            try:
                target_path.write_text(content, encoding="utf-8")
                return ElevationResult(True, 0, "", "", message=f"Updated {target_path}.")
            except Exception as exc:
                return ElevationResult(False, 1, "", str(exc), message=str(exc))

        if platform.system() in ("Linux", "Darwin") and shutil.which("install"):
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as tf:
                tf.write(content)
                tmp_path = Path(tf.name)
            try:
                res = self.run_privileged(
                    ["install", "-m", mode, str(tmp_path), str(target_path)],
                    reason=reason,
                    title=f"Sudo Required to Update {target_path.name}",
                )
                return res
            finally:
                tmp_path.unlink(missing_ok=True)

        return self.run_privileged(
            ["tee", str(target_path)],
            reason=reason,
            title=f"Sudo Required to Update {target_path.name}",
            stdin_text=content,
        )
