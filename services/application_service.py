"""Launching an allow-list of desktop applications. Nothing outside the list is ever executed."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

logger = logging.getLogger("ultron.apps")

APP_ALIASES: dict[str, str] = {
    "notepad": "notepad",
    "note pad": "notepad",
    "calculator": "calculator",
    "calc": "calculator",
    "file explorer": "file explorer",
    "explorer": "file explorer",
    "files": "file explorer",
    "file manager": "file explorer",
    "my files": "file explorer",
    "command prompt": "command prompt",
    "cmd": "command prompt",
    "terminal": "command prompt",
    "vs code": "vs code",
    "vscode": "vs code",
    "visual studio code": "vs code",
    "code editor": "vs code",
}

_ALLOWED_CUSTOM_EXTENSIONS = {"win32": {".exe", ".lnk"}, "darwin": {".app"}}


class ApplicationError(Exception):
    pass


@dataclass(frozen=True)
class LaunchSpec:
    args: tuple[str, ...]
    new_console: bool = False
    use_startfile: bool = False


def _windows_candidates(app: str) -> list[LaunchSpec]:
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    local = os.environ.get("LOCALAPPDATA", "")
    program_files = [os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", "")]

    def paths(*items: str | None) -> list[LaunchSpec]:
        return [LaunchSpec((p,)) for p in items if p]

    if app == "notepad":
        return paths(os.path.join(system_root, "System32", "notepad.exe"), shutil.which("notepad.exe"))
    if app == "calculator":
        return paths(os.path.join(system_root, "System32", "calc.exe"), shutil.which("calc.exe"))
    if app == "file explorer":
        return paths(os.path.join(system_root, "explorer.exe"), shutil.which("explorer.exe"))
    if app == "command prompt":
        cmd = os.path.join(system_root, "System32", "cmd.exe")
        return [LaunchSpec((cmd,), new_console=True)]
    if app == "vs code":
        candidates = [os.path.join(local, "Programs", "Microsoft VS Code", "Code.exe")] if local else []
        candidates += [os.path.join(pf, "Microsoft VS Code", "Code.exe") for pf in program_files if pf]
        return paths(*candidates, shutil.which("code.exe"))
    return []


_MAC_APPS = {
    "notepad": "TextEdit",
    "calculator": "Calculator",
    "file explorer": "Finder",
    "command prompt": "Terminal",
    "vs code": "Visual Studio Code",
}


def _mac_candidates(app: str) -> list[LaunchSpec]:
    name = _MAC_APPS.get(app)
    if not name:
        return []
    roots = ["/System/Applications", "/System/Applications/Utilities", "/Applications", "/System/Library/CoreServices"]
    bundles = [os.path.join(root, f"{name}.app") for root in roots]
    return [LaunchSpec(("open", "-a", bundle)) for bundle in bundles]


def _spec_exists(spec: LaunchSpec) -> bool:
    target = spec.args[-1]
    return os.path.exists(target)


def _default_launcher(spec: LaunchSpec) -> None:
    if spec.use_startfile and hasattr(os, "startfile"):
        os.startfile(spec.args[0])  # type: ignore[attr-defined]
        return
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NEW_CONSOLE if spec.new_console else subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        list(spec.args),
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=flags,
    )


class ApplicationService:
    def __init__(
        self,
        custom_apps: dict[str, str] | None = None,
        platform: str | None = None,
        launcher: Callable[[LaunchSpec], None] | None = None,
        exists: Callable[[LaunchSpec], bool] | None = None,
    ) -> None:
        self.platform = platform or sys.platform
        self.custom_apps = dict(custom_apps or {})
        self._launch = launcher or _default_launcher
        self._exists = exists or _spec_exists

    def canonical_name(self, spoken: str) -> str | None:
        key = " ".join(spoken.lower().replace(".", " ").split())
        for suffix in (" app", " application"):
            if key.endswith(suffix):
                key = key[: -len(suffix)].strip()
        if key in self.custom_apps:
            return key
        return APP_ALIASES.get(key)

    def supported_apps(self) -> list[str]:
        builtin = sorted(set(APP_ALIASES.values()))
        return builtin + sorted(k for k in self.custom_apps if k not in builtin)

    def _custom_spec(self, name: str) -> LaunchSpec | None:
        configured = self.custom_apps[name]
        resolved = configured if os.path.isabs(configured) else shutil.which(configured)
        if not resolved:
            return None
        ext = Path(resolved).suffix.lower()
        allowed = _ALLOWED_CUSTOM_EXTENSIONS.get(self.platform, set())
        if allowed and ext not in allowed:
            logger.warning("Refusing to launch configured app %r: extension %r not allowed", name, ext)
            return None
        if self.platform == "darwin" and ext == ".app":
            return LaunchSpec(("open", "-a", resolved))
        return LaunchSpec((resolved,), use_startfile=ext == ".lnk")

    def find(self, name: str) -> LaunchSpec | None:
        if name in self.custom_apps:
            spec = self._custom_spec(name)
            return spec if spec and self._exists(spec) else None
        if self.platform == "win32":
            candidates = _windows_candidates(name)
        elif self.platform == "darwin":
            candidates = _mac_candidates(name)
        else:
            candidates = []
        return next((c for c in candidates if self._exists(c)), None)

    def open(self, spoken_name: str) -> str:
        """Launch an allow-listed application and return its display name."""
        name = self.canonical_name(spoken_name)
        if not name:
            raise ApplicationError(
                f"'{spoken_name}' isn't an application I'm allowed to open. "
                "You can add it to ULTRON_APPS in the .env file."
            )
        spec = self.find(name)
        if not spec and name in self.custom_apps:
            raise ApplicationError(
                f"The path configured for {name.title()} in ULTRON_APPS doesn't point to an existing "
                "application (.exe or .lnk on Windows)."
            )
        if not spec:
            raise ApplicationError(f"{name.title()} doesn't appear to be installed on this computer.")
        try:
            self._launch(spec)
        except OSError as exc:
            logger.error("Failed to launch %s: %s", name, exc)
            raise ApplicationError(f"I couldn't start {name.title()}.") from exc
        logger.info("Launched application: %s", name)
        return name


def describe(apps: Sequence[str]) -> str:
    return ", ".join(a.title() if a != "vs code" else "VS Code" for a in apps)
