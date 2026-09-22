"""Configuración local (sección 4 de SPEC.md)."""

from __future__ import annotations

import json
from pathlib import Path

from src.config import Config, default_team_name


def test_valores_por_defecto():
    config = Config()
    assert config.root_folder is None
    assert config.team_name == default_team_name()
    assert config.check_on_open is True
    assert config.start_with_windows is False
    assert config.theme == "system"


def test_guardar_y_recuperar(tmp_path: Path):
    destino = tmp_path / "config.json"
    Config(root_folder="/proyectos", team_name="PC-MESA", excluded_repos=["viejo"]).save(destino)

    recuperada = Config.load(destino)
    assert recuperada.root_folder == "/proyectos"
    assert recuperada.team_name == "PC-MESA"
    assert recuperada.is_excluded("viejo")
    assert not recuperada.is_excluded("otro")
    assert recuperada.root_path == Path("/proyectos")


def test_un_archivo_corrupto_no_rompe_la_aplicacion(tmp_path: Path):
    destino = tmp_path / "config.json"
    destino.write_text("{esto no es json", encoding="utf-8")
    assert Config.load(destino).team_name == default_team_name()


def test_se_ignoran_las_claves_desconocidas(tmp_path: Path):
    destino = tmp_path / "config.json"
    destino.write_text(json.dumps({"team_name": "PORTATIL", "inventada": 1}), encoding="utf-8")
    assert Config.load(destino).team_name == "PORTATIL"


def test_un_tema_invalido_vuelve_al_predeterminado():
    assert Config(theme="fucsia").theme == "system"


def test_el_token_nunca_se_guarda_en_el_archivo(tmp_path: Path):
    destino = tmp_path / "config.json"
    Config(root_folder="/proyectos").save(destino)
    guardado = json.loads(destino.read_text(encoding="utf-8"))
    assert not any("token" in clave.lower() for clave in guardado)
