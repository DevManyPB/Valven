"""Fase 2 — aceptación: un respaldo devuelve el contenido original exacto,
incluidos los archivos nuevos (sección 6.4 de SPEC.md)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src import safety
from src.analyzer import analyze_repo
from src.safety import Backup, create_backup, list_backups, restore_backup, undo_last
from tests.conftest import TEAM_B, commit, git, write


@pytest.fixture(autouse=True)
def _indice_aislado(tmp_path, monkeypatch):
    """Cada prueba usa su propio backups.json."""
    destino = tmp_path / "backups.json"
    monkeypatch.setattr(safety, "_index_path", lambda: destino)
    return destino


# --- criterio de aceptación de la fase 2 -----------------------------------

def test_restaurar_devuelve_el_contenido_original_incluidos_archivos_nuevos(sandbox):
    repo = sandbox.b
    write(repo, "README.md", "contenido original modificado\n")
    write(repo, "nuevo.txt", "archivo nuevo original\n")
    write(repo, "carpeta/hondo.txt", "anidado original\n")
    original = {
        "README.md": (repo / "README.md").read_text(encoding="utf-8"),
        "nuevo.txt": (repo / "nuevo.txt").read_text(encoding="utf-8"),
        "carpeta/hondo.txt": (repo / "carpeta/hondo.txt").read_text(encoding="utf-8"),
    }

    respaldo = create_backup(repo, "antes de sincronizar", TEAM_B)

    # Se estropea todo lo que se pueda estropear.
    write(repo, "README.md", "DESTROZADO\n")
    write(repo, "nuevo.txt", "DESTROZADO\n")
    write(repo, "carpeta/hondo.txt", "DESTROZADO\n")
    commit(repo, "intruso.txt", "commit no deseado\n", "Commit que hay que deshacer", TEAM_B)

    restore_backup(respaldo)

    for ruta, esperado in original.items():
        assert (repo / ruta).read_text(encoding="utf-8") == esperado, ruta
    assert git(repo, "rev-parse", "HEAD") == respaldo.head_sha


def test_restaurar_recupera_un_archivo_borrado(sandbox):
    repo = sandbox.b
    commit(repo, "importante.txt", "no me borres\n", "Archivo importante", TEAM_B)
    respaldo = create_backup(repo, "antes de subir", TEAM_B)

    (repo / "importante.txt").unlink()
    restore_backup(respaldo)

    assert (repo / "importante.txt").read_text(encoding="utf-8") == "no me borres\n"


def test_restaurar_conserva_los_permisos_de_ejecucion(sandbox):
    import os
    repo = sandbox.b
    guion = write(repo, "script.sh", "#!/bin/sh\necho hola\n")
    guion.chmod(0o755)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "Añade guion")

    respaldo = create_backup(repo, "antes de sincronizar", TEAM_B)
    guion.write_text("ESTROPEADO\n", encoding="utf-8")
    restore_backup(respaldo)

    assert guion.read_text(encoding="utf-8") == "#!/bin/sh\necho hola\n"
    assert os.access(guion, os.X_OK)


def test_restaurar_recupera_contenido_binario_intacto(sandbox):
    repo = sandbox.b
    datos = bytes(range(256)) * 20
    (repo / "imagen.bin").write_bytes(datos)
    respaldo = create_backup(repo, "antes de subir", TEAM_B)

    (repo / "imagen.bin").write_bytes(b"roto")
    restore_backup(respaldo)

    assert (repo / "imagen.bin").read_bytes() == datos


# --- el respaldo no modifica el repositorio --------------------------------

def test_crear_un_respaldo_no_altera_la_carpeta_de_trabajo(sandbox):
    repo = sandbox.b
    write(repo, "README.md", "editado\n")
    write(repo, "nuevo.txt", "nuevo\n")
    antes_head = git(repo, "rev-parse", "HEAD")
    antes_estado = git(repo, "status", "--porcelain=v2")

    create_backup(repo, "antes de sincronizar", TEAM_B)

    assert git(repo, "rev-parse", "HEAD") == antes_head
    assert git(repo, "status", "--porcelain=v2") == antes_estado
    assert (repo / "README.md").read_text(encoding="utf-8") == "editado\n"
    assert (repo / "nuevo.txt").read_text(encoding="utf-8") == "nuevo\n"


def test_el_respaldo_no_deja_entradas_en_el_escondite(sandbox):
    """'stash create' no debe añadir nada a la lista de stashes del usuario."""
    repo = sandbox.b
    write(repo, "README.md", "editado\n")
    create_backup(repo, "antes de subir", TEAM_B)
    assert git(repo, "stash", "list") == ""


def test_las_referencias_de_respaldo_viven_fuera_de_las_ramas(sandbox):
    repo = sandbox.b
    write(repo, "nuevo.txt", "x\n")
    respaldo = create_backup(repo, "antes de subir", TEAM_B)

    refs = safety.backup_refs(repo)
    assert respaldo.head_ref in refs
    assert respaldo.untracked_ref in refs
    assert all(ref.startswith("refs/vaiven-backup/") for ref in refs)
    assert "vaiven-backup" not in git(repo, "branch", "--list", "--all")


def test_los_respaldos_no_se_suben_a_github(sandbox):
    repo = sandbox.b
    write(repo, "nuevo.txt", "x\n")
    create_backup(repo, "antes de subir", TEAM_B)
    commit(repo, "otro.txt", "y\n", "Trabajo normal", TEAM_B)
    git(repo, "push", "origin", "main")

    remotas = git(sandbox.remote, "for-each-ref", "--format=%(refname)")
    assert "vaiven-backup" not in remotas


# --- deshacer es reversible ------------------------------------------------

def test_deshacer_tambien_es_reversible(sandbox):
    repo = sandbox.b
    write(repo, "borrador.txt", "estado 1\n")
    primero = create_backup(repo, "antes de sincronizar", TEAM_B)

    write(repo, "borrador.txt", "estado 2\n")
    seguridad = restore_backup(primero)
    assert (repo / "borrador.txt").read_text(encoding="utf-8") == "estado 1\n"

    restore_backup(seguridad)
    assert (repo / "borrador.txt").read_text(encoding="utf-8") == "estado 2\n"


def test_deshacer_la_ultima_operacion(sandbox):
    repo = sandbox.b
    write(repo, "trabajo.txt", "antes\n")
    create_backup(repo, "antes de subir", TEAM_B)

    commit(repo, "trabajo.txt", "después\n", "Cambio que se deshará", TEAM_B)
    previo = undo_last(repo)

    assert previo is not None
    assert (repo / "trabajo.txt").read_text(encoding="utf-8") == "antes\n"


def test_deshacer_sin_respaldos_no_hace_nada(sandbox):
    assert undo_last(sandbox.b) is None


def test_deshacer_ignora_las_copias_de_seguridad_de_otras_restauraciones(sandbox):
    repo = sandbox.b
    write(repo, "a.txt", "uno\n")
    primero = create_backup(repo, "antes de subir", TEAM_B)
    write(repo, "a.txt", "dos\n")
    restore_backup(primero)  # crea un respaldo "antes de restaurar..."

    siguiente = undo_last(repo)
    assert siguiente is not None
    assert (repo / "a.txt").read_text(encoding="utf-8") == "uno\n"


# --- índice de respaldos ---------------------------------------------------

def test_el_indice_registra_repo_fecha_motivo_y_referencia(sandbox):
    repo = sandbox.b
    respaldo = create_backup(repo, "antes de sincronizar", TEAM_B)

    guardados = list_backups(repo)
    assert len(guardados) == 1
    registro = guardados[0]
    assert registro.repo_name == repo.name
    assert registro.reason == "antes de sincronizar"
    assert registro.team == TEAM_B
    assert registro.head_ref == f"refs/vaiven-backup/{respaldo.id}"
    assert registro.branch == "main"
    assert "/" in registro.describe()


def test_el_indice_sobrevive_a_un_archivo_corrupto(_indice_aislado):
    _indice_aislado.write_text("{no es json", encoding="utf-8")
    assert safety.load_index() == []


def test_los_respaldos_se_listan_del_mas_nuevo_al_mas_viejo(sandbox):
    repo = sandbox.b
    base = datetime.now(timezone.utc)
    for i in range(3):
        create_backup(repo, f"operación {i}", TEAM_B, when=base + timedelta(seconds=i))
    motivos = [b.reason for b in list_backups(repo)]
    assert motivos == ["operación 2", "operación 1", "operación 0"]


def test_los_respaldos_se_filtran_por_repositorio(sandbox):
    create_backup(sandbox.a, "op en A", "PORTATIL")
    create_backup(sandbox.b, "op en B", TEAM_B)
    assert [b.reason for b in list_backups(sandbox.a)] == ["op en A"]
    assert [b.reason for b in list_backups(sandbox.b)] == ["op en B"]


# --- limpieza por antigüedad -----------------------------------------------

def _falso(repo_path: str, dias_de_antiguedad: int, indice: int) -> Backup:
    creado = datetime.now(timezone.utc) - timedelta(days=dias_de_antiguedad, seconds=indice)
    return Backup(
        id=f"b{indice}", repo_name="r", repo_path=repo_path,
        created_at=creado.isoformat(timespec="seconds"), reason="prueba", team="X",
    )


def test_se_conservan_los_recientes():
    entradas = [_falso("/r", 1, i) for i in range(5)]
    assert safety.expired_backups(entradas) == []


def test_caducan_los_de_mas_de_treinta_dias_si_sobran_veinte_mas_nuevos():
    entradas = [_falso("/r", 1, i) for i in range(20)] + [_falso("/r", 40, 100)]
    caducados = safety.expired_backups(entradas)
    assert [b.id for b in caducados] == ["b100"]


def test_los_ultimos_veinte_se_conservan_aunque_sean_antiguos():
    """«30 días o los últimos 20, lo que sea mayor»."""
    entradas = [_falso("/r", 365, i) for i in range(20)]
    assert safety.expired_backups(entradas) == []


def test_la_limpieza_borra_las_referencias_del_repositorio(sandbox):
    repo = sandbox.b
    write(repo, "nuevo.txt", "x\n")
    viejo = create_backup(
        repo, "operación antigua", TEAM_B,
        when=datetime.now(timezone.utc) - timedelta(days=60),
    )
    assert viejo.head_ref in safety.backup_refs(repo)

    borrados = safety.cleanup(keep=0)

    assert [b.id for b in borrados] == [viejo.id]
    assert safety.backup_refs(repo) == []
    assert list_backups(repo) == []


def test_la_limpieza_no_toca_las_ramas_del_usuario(sandbox):
    repo = sandbox.b
    create_backup(repo, "op", TEAM_B, when=datetime.now(timezone.utc) - timedelta(days=60))
    safety.cleanup(keep=0)
    assert git(repo, "rev-parse", "--verify", "main")


# --- interacción con el analizador -----------------------------------------

def test_el_estado_del_repo_no_cambia_tras_respaldar(sandbox):
    repo = sandbox.b
    write(repo, "README.md", "editado\n")
    antes = analyze_repo(repo, fetch=False).state
    create_backup(repo, "antes de subir", TEAM_B)
    assert analyze_repo(repo, fetch=False).state is antes
