"""Opción «Iniciar con Windows» (sección 10 de SPEC.md).

Se crea o se borra un acceso directo en la carpeta de Inicio del usuario:
``%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup``.

Crear un ``.lnk`` de verdad necesita COM, que no está en la biblioteca
estándar; se genera con un pequeño script de VBScript y ``cscript``, que
viene con Windows. Si eso falla, se recurre a un ``.cmd`` equivalente, que
funciona igual aunque sea menos elegante.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from .logger import get_logger

log = get_logger("startup")

SHORTCUT_NAME = "Vaiven"


def is_windows() -> bool:
    return os.name == "nt"


def startup_dir() -> Path | None:
    """Carpeta de Inicio de Windows, o ``None`` fuera de Windows."""
    override = os.environ.get("VAIVEN_STARTUP_DIR")
    if override:
        return Path(override)
    if not is_windows():
        return None
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def executable_path() -> Path:
    """Ruta que debe lanzar el acceso directo.

    Empaquetado con PyInstaller es el propio ``Vaiven.exe``; en desarrollo,
    el intérprete de Python con ``src/main.py``.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable)
    return Path(sys.executable)


def _launch_command() -> tuple[str, str]:
    """Programa y argumentos que lanzan la aplicación."""
    if getattr(sys, "frozen", False):
        return str(Path(sys.executable)), ""
    raiz = Path(__file__).resolve().parents[1]
    return str(Path(sys.executable)), f'"{raiz / "src" / "main.py"}"'


def shortcut_path() -> Path | None:
    carpeta = startup_dir()
    return carpeta / f"{SHORTCUT_NAME}.lnk" if carpeta else None


def _cmd_path() -> Path | None:
    carpeta = startup_dir()
    return carpeta / f"{SHORTCUT_NAME}.cmd" if carpeta else None


def is_enabled() -> bool:
    """¿Está configurado el arranque automático?"""
    for ruta in (shortcut_path(), _cmd_path()):
        if ruta and ruta.exists():
            return True
    return False


def _write_shortcut_with_vbscript(destino: Path, programa: str, argumentos: str) -> bool:
    from .config import icon_path

    icono = icon_path()
    guion = f'''Set oWS = WScript.CreateObject("WScript.Shell")
Set oLink = oWS.CreateShortcut("{destino}")
oLink.TargetPath = "{programa}"
oLink.Arguments = "{argumentos}"
oLink.WorkingDirectory = "{Path(programa).parent}"
oLink.Description = "Vaiven"
{f'oLink.IconLocation = "{icono}"' if icono.exists() else ""}
oLink.Save
'''
    handle, nombre = tempfile.mkstemp(suffix=".vbs", text=True)
    try:
        with os.fdopen(handle, "w", encoding="mbcs" if is_windows() else "utf-8") as archivo:
            archivo.write(guion)
        subprocess.run(
            ["cscript", "//nologo", nombre],
            check=True, capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return destino.exists()
    except Exception as exc:
        log.warning("no se pudo crear el acceso directo con VBScript: %s", exc)
        return False
    finally:
        Path(nombre).unlink(missing_ok=True)


def enable() -> bool:
    """Crea el acceso directo de inicio. Devuelve si lo consiguió."""
    carpeta = startup_dir()
    if carpeta is None:
        log.info("«Iniciar con Windows» solo está disponible en Windows")
        return False
    carpeta.mkdir(parents=True, exist_ok=True)
    programa, argumentos = _launch_command()

    destino = shortcut_path()
    if destino and is_windows() and _write_shortcut_with_vbscript(destino, programa, argumentos):
        log.info("arranque automático activado: %s", destino)
        return True

    # Plan B: un .cmd hace exactamente lo mismo.
    alternativa = _cmd_path()
    if alternativa is None:
        return False
    alternativa.write_text(
        f'@echo off\r\nstart "" "{programa}" {argumentos}\r\n', encoding="utf-8"
    )
    log.info("arranque automático activado (cmd): %s", alternativa)
    return True


def disable() -> bool:
    """Borra el acceso directo de inicio."""
    borrado = False
    for ruta in (shortcut_path(), _cmd_path()):
        if ruta and ruta.exists():
            ruta.unlink()
            borrado = True
    if borrado:
        log.info("arranque automático desactivado")
    return borrado


def apply(enabled: bool) -> bool:
    """Sincroniza el estado real con la preferencia del usuario."""
    return enable() if enabled else disable()
