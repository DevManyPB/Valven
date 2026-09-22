"""El log nunca debe contener el token de GitHub (secciones 4 y 11)."""

from __future__ import annotations

import logging
from pathlib import Path

from src import logger as logger_module


def test_censura_la_cabecera_de_autenticacion():
    sucio = "git -c http.https://github.com/.extraheader=AUTHORIZATION: basic eHh4OnRva2Vu fetch"
    limpio = logger_module.redact(sucio)
    assert "eHh4OnRva2Vu" not in limpio
    assert "fetch" in limpio


def test_censura_tokens_de_github_en_cualquier_formato():
    for secreto in (
        "ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "gho_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "github_pat_11ABCDEFG0aaaaaaaaaaaaaaaaaaaa",
    ):
        assert secreto not in logger_module.redact(f"fallo con {secreto} al final")


def test_censura_credenciales_incrustadas_en_la_url():
    limpio = logger_module.redact("clone https://x-access-token:supersecreto@github.com/u/r.git")
    assert "supersecreto" not in limpio
    assert "github.com/u/r.git" in limpio


def test_el_archivo_de_log_no_guarda_el_token(tmp_path: Path):
    destino = tmp_path / "logs"
    propio = logging.getLogger("vaiven_prueba_censura")
    propio.setLevel(logging.INFO)
    handler = logging.FileHandler(destino.parent / "prueba.log", encoding="utf-8")
    handler.addFilter(logger_module._RedactingFilter())
    propio.addHandler(handler)

    propio.info("ejecutando git push con ghp_zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz")
    handler.close()

    contenido = (destino.parent / "prueba.log").read_text(encoding="utf-8")
    assert "ghp_zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz" not in contenido
    assert "ejecutando git push" in contenido


def test_setup_crea_el_log_rotativo(tmp_path: Path):
    logger_module._configured = False
    try:
        log = logger_module.setup(log_dir=tmp_path)
        log.info("hola")
        for handler in log.handlers:
            handler.flush()
        assert (tmp_path / logger_module.LOG_FILENAME).exists()
        rotativo = next(
            h for h in log.handlers if isinstance(h, logger_module.RotatingFileHandler)
        )
        assert rotativo.maxBytes == 1024 * 1024
        assert rotativo.backupCount + 1 == 5  # 5 archivos en total
    finally:
        for handler in list(log.handlers):
            handler.close()
            log.removeHandler(handler)
        logger_module._configured = False
