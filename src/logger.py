"""Log rotativo de Vaivén, con censura obligatoria de credenciales.

Sección 4 de SPEC.md: 5 archivos de 1 MB en ``%APPDATA%\\Vaiven\\logs``.
Sección 11: todo error se registra con el comando ejecutado, **sin** la
cabecera de autenticación.

La censura se aplica como filtro de ``logging``, de modo que ningún módulo
pueda escribir un token en el log ni por descuido.
"""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import config

LOGGER_NAME = "vaiven"
LOG_FILENAME = "vaiven.log"
MAX_BYTES = 1024 * 1024
BACKUP_COUNT = 4  # el archivo activo + 4 rotados = 5 archivos

REDACTED = "***"

#: Patrones de cosas que jamás deben aparecer en el log.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Cabecera de autenticación que se pasa a git con -c http.<url>.extraheader.
    # El orden importa: primero el valor base64, luego el resto del argumento.
    re.compile(r"(AUTHORIZATION:\s*basic\s+)[^\s'\"]+", re.IGNORECASE),
    re.compile(r"(Authorization:\s*(?:token|bearer)\s+)[^\s'\"]+", re.IGNORECASE),
    re.compile(r"(extraheader\s*=\s*)(?:'[^']*'|\"[^\"]*\"|\S+)", re.IGNORECASE),
    # Tokens de GitHub en cualquiera de sus formatos.
    re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    # Credenciales incrustadas en una URL: https://user:token@github.com/...
    re.compile(r"(https?://)[^/\s:@]+:[^/\s@]+(@)"),
)


def redact(text: str) -> str:
    """Devuelve ``text`` con cualquier credencial reconocible sustituida."""
    if not text:
        return text
    for pattern in _SECRET_PATTERNS:
        if pattern.groups:
            text = pattern.sub(lambda m: "".join(m.groups()[:1]) + REDACTED + "".join(m.groups()[1:]), text)
        else:
            text = pattern.sub(REDACTED, text)
    return text


class _RedactingFilter(logging.Filter):
    """Censura el mensaje ya formateado antes de que llegue a cualquier handler."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - formateo defectuoso del emisor
            return True
        clean = redact(message)
        if clean != message:
            record.msg = clean
            record.args = ()
        return True


_configured = False


def setup(level: int = logging.INFO, log_dir: Path | None = None, console: bool = False) -> logging.Logger:
    """Configura el logger de la aplicación. Es idempotente."""
    global _configured
    logger = logging.getLogger(LOGGER_NAME)
    if _configured:
        return logger

    logger.setLevel(level)
    logger.propagate = False

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    redacting = _RedactingFilter()

    directory = log_dir or config.logs_dir()
    directory.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        directory / LOG_FILENAME,
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(formatter)
    handler.addFilter(redacting)
    logger.addHandler(handler)

    if console:
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        stream.addFilter(redacting)
        logger.addHandler(stream)

    logger.addFilter(redacting)
    _configured = True
    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    """Logger hijo del de la aplicación (``vaiven.git_ops``, etc.)."""
    base = logging.getLogger(LOGGER_NAME)
    if not base.handlers and not _configured:
        # Sin configurar todavía: evita el warning de "no handlers".
        base.addHandler(logging.NullHandler())
        base.addFilter(_RedactingFilter())
    return base.getChild(name) if name else base


def log_file_path() -> Path:
    return config.logs_dir() / LOG_FILENAME
