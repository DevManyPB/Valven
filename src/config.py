"""Lectura y escritura de la configuración de Vaivén.

La configuración vive en la carpeta de datos de la aplicación:

* Windows: ``%APPDATA%\\Vaiven``
* Otros sistemas (solo desarrollo): ``$XDG_CONFIG_HOME/vaiven`` o ``~/.config/vaiven``

El token de GitHub **nunca** se guarda aquí; va al Administrador de
credenciales de Windows a través de ``keyring`` (ver ``auth.py``).
"""

from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
from dataclasses import dataclass, field, asdict
from pathlib import Path

APP_NAME = "Vaiven"
APP_DISPLAY_NAME = "Vaivén"

CONFIG_FILENAME = "config.json"
BACKUPS_FILENAME = "backups.json"
LOGS_DIRNAME = "logs"

VALID_THEMES = ("system", "light", "dark")


def resource_path(*partes: str) -> Path:
    """Ruta a un recurso empaquetado (el icono, por ejemplo).

    PyInstaller en modo ``--onefile`` extrae los archivos añadidos a una
    carpeta temporal que anuncia en ``sys._MEIPASS``; en desarrollo están
    junto al código.
    """
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return base.joinpath(*partes)


def icon_path() -> Path:
    return resource_path("assets", "icon.ico")


def data_dir() -> Path:
    """Carpeta de datos de la aplicación, creada si no existe."""
    override = os.environ.get("VAIVEN_DATA_DIR")
    if override:
        base = Path(override)
    elif os.name == "nt":
        appdata = os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")
        base = Path(appdata) / APP_NAME
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        base = Path(xdg) / APP_NAME.lower() if xdg else Path.home() / ".config" / APP_NAME.lower()
    base.mkdir(parents=True, exist_ok=True)
    return base


def config_path() -> Path:
    return data_dir() / CONFIG_FILENAME


def backups_index_path() -> Path:
    return data_dir() / BACKUPS_FILENAME


def logs_dir() -> Path:
    path = data_dir() / LOGS_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_team_name() -> str:
    """Nombre del equipo por defecto: el nombre del ordenador."""
    name = (
        os.environ.get("VAIVEN_TEAM_NAME")
        or os.environ.get("COMPUTERNAME")
        or socket.gethostname()
        or "EQUIPO"
    )
    return name.strip().upper()[:40] or "EQUIPO"


@dataclass
class Config:
    """Preferencias del usuario (sección 4 de SPEC.md)."""

    root_folder: str | None = None
    team_name: str = field(default_factory=default_team_name)
    start_with_windows: bool = False
    check_on_open: bool = True
    excluded_repos: list[str] = field(default_factory=list)
    theme: str = "system"
    setup_done: bool = False

    # --- validación -----------------------------------------------------

    def __post_init__(self) -> None:
        if self.theme not in VALID_THEMES:
            self.theme = "system"
        if not self.team_name or not self.team_name.strip():
            self.team_name = default_team_name()
        self.team_name = self.team_name.strip()
        self.excluded_repos = [str(r) for r in self.excluded_repos]

    @property
    def root_path(self) -> Path | None:
        return Path(self.root_folder) if self.root_folder else None

    def is_excluded(self, repo_name: str) -> bool:
        return repo_name in self.excluded_repos

    # --- persistencia ---------------------------------------------------

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        """Carga la configuración; si no existe o está corrupta, usa valores por defecto."""
        path = path or config_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError, UnicodeDecodeError):
            return cls()
        if not isinstance(data, dict):
            return cls()
        try:
            return cls.from_dict(data)
        except TypeError:
            return cls()

    def save(self, path: Path | None = None) -> Path:
        """Guarda de forma atómica: escribe a un temporal y lo reemplaza."""
        path = path or config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".config-", suffix=".tmp")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self.to_dict(), handle, indent=2, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return path
