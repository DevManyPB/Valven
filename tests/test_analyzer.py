"""Fase 1 — aceptación: el analizador detecta todos los estados de la tabla 6.2."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.analyzer import (
    RepoState,
    STATE_COLORS,
    STATE_LABELS,
    analyze_all,
    analyze_repo,
    discover_repos,
    not_cloned_status,
)
from tests.conftest import TEAM_A, TEAM_B, commit, git, write


# --- la tabla de la sección 6.2, estado por estado -------------------------

def test_al_dia(sandbox):
    status = analyze_repo(sandbox.b)
    assert status.state is RepoState.UP_TO_DATE
    assert status.label == "Al día"
    assert status.color == "verde"
    assert (status.ahead, status.behind) == (0, 0)
    assert not status.dirty
    assert status.branch == "main"
    assert status.upstream == "origin/main"


def test_cambios_locales_por_archivo_modificado(sandbox):
    write(sandbox.b, "README.md", "texto cambiado\n")
    status = analyze_repo(sandbox.b)
    assert status.state is RepoState.LOCAL_CHANGES
    assert status.color == "azul"
    assert [f.path for f in status.modified] == ["README.md"]
    assert status.has_newer_work


def test_cambios_locales_por_archivo_nuevo(sandbox):
    write(sandbox.b, "nuevo.txt", "hola\n")
    status = analyze_repo(sandbox.b)
    assert status.state is RepoState.LOCAL_CHANGES
    assert [f.path for f in status.added] == ["nuevo.txt"]


def test_cambios_locales_por_archivo_borrado(sandbox):
    (sandbox.b / "README.md").unlink()
    status = analyze_repo(sandbox.b)
    assert status.state is RepoState.LOCAL_CHANGES
    assert [f.path for f in status.deleted] == ["README.md"]


def test_adelantado(sandbox):
    commit(sandbox.b, "nuevo.txt", "trabajo local\n", "Trabajo sin subir", TEAM_B)
    status = analyze_repo(sandbox.b)
    assert status.state is RepoState.AHEAD
    assert (status.ahead, status.behind) == (1, 0)
    assert [c.subject for c in status.outgoing] == ["Trabajo sin subir"]
    assert status.outgoing[0].team == TEAM_B
    assert "1 commit por subir" in status.summary()


def test_atrasado(sandbox):
    commit(sandbox.a, "desde-a.txt", "novedad\n", "Novedad del portátil", TEAM_A)
    sandbox.push(sandbox.a)

    status = analyze_repo(sandbox.b)
    assert status.state is RepoState.BEHIND
    assert status.color == "amarillo"
    assert (status.ahead, status.behind) == (0, 1)
    assert status.incoming[0].subject == "Novedad del portátil"
    assert status.incoming[0].team == TEAM_A
    assert status.can_sync
    assert not status.is_blocked
    assert f"del {TEAM_A}" in status.summary()


def test_divergido(sandbox):
    commit(sandbox.a, "desde-a.txt", "a\n", "Cambio en A", TEAM_A)
    sandbox.push(sandbox.a)
    commit(sandbox.b, "desde-b.txt", "b\n", "Cambio en B", TEAM_B)

    status = analyze_repo(sandbox.b)
    assert status.state is RepoState.DIVERGED
    assert status.color == "rojo"
    assert (status.ahead, status.behind) == (1, 1)
    assert status.is_blocked
    assert not status.can_sync
    assert not status.can_push


def test_atrasado_con_cambios_locales(sandbox):
    commit(sandbox.a, "desde-a.txt", "a\n", "Cambio en A", TEAM_A)
    sandbox.push(sandbox.a)
    write(sandbox.b, "README.md", "editado sin guardar\n")

    status = analyze_repo(sandbox.b)
    assert status.state is RepoState.BEHIND_WITH_LOCAL_CHANGES
    assert status.color == "rojo"
    assert status.behind == 1 and status.dirty
    assert status.is_blocked
    assert not status.can_sync


def test_en_conflicto(sandbox):
    commit(sandbox.a, "compartido.txt", "version de A\n", "A edita", TEAM_A)
    sandbox.push(sandbox.a)
    commit(sandbox.b, "compartido.txt", "version de B\n", "B edita", TEAM_B)
    sandbox.fetch(sandbox.b)
    git(sandbox.b, "merge", "origin/main", check=False)  # deja el merge a medias

    status = analyze_repo(sandbox.b)
    assert status.state is RepoState.CONFLICT
    assert status.has_conflicts
    assert status.is_blocked


def test_sin_rama_remota(sandbox):
    git(sandbox.b, "checkout", "-b", "experimento")
    status = analyze_repo(sandbox.b)
    assert status.state is RepoState.NO_UPSTREAM
    assert status.color == "gris"
    assert status.branch == "experimento"
    assert status.upstream is None


def test_head_suelto(sandbox):
    sha = git(sandbox.b, "rev-parse", "HEAD")
    git(sandbox.b, "checkout", "--detach", sha)
    status = analyze_repo(sandbox.b)
    assert status.state is RepoState.DETACHED_HEAD
    assert status.branch is None
    assert status.is_blocked


def test_no_clonado():
    status = not_cloned_status("mi-proyecto", "https://github.com/usuario/mi-proyecto.git", root="/tmp")
    assert status.state is RepoState.NOT_CLONED
    assert status.is_github
    assert "no en este equipo" in status.summary()


def test_carpeta_que_no_es_repositorio(tmp_path: Path):
    carpeta = tmp_path / "cualquiera"
    carpeta.mkdir()
    status = analyze_repo(carpeta)
    assert status.state is RepoState.ERROR
    assert status.error


def test_todos_los_estados_tienen_texto_y_color():
    for state in RepoState:
        assert STATE_LABELS[state]
        assert STATE_COLORS[state] in {"verde", "azul", "amarillo", "rojo", "gris"}


# --- reglas de la sección 6.3 ---------------------------------------------

@pytest.mark.parametrize("state", list(RepoState))
def test_solo_se_sincroniza_lo_atrasado_y_limpio(sandbox, state):
    """Ningún estado salvo 'Atrasado y limpio' puede sincronizarse en masa."""
    from src.analyzer import RepoStatus
    status = RepoStatus(name="x", path=sandbox.b, state=state, behind=1)
    assert status.can_sync is (state is RepoState.BEHIND)


def test_repo_adelantado_no_se_sincroniza_en_masa(sandbox):
    commit(sandbox.b, "mio.txt", "trabajo nuevo\n", "Trabajo del PC", TEAM_B)
    write(sandbox.b, "otro.txt", "sin guardar\n")
    status = analyze_repo(sandbox.b)
    assert status.has_newer_work
    assert not status.can_sync
    assert status.can_push


# --- otros datos del análisis ---------------------------------------------

def test_detecta_el_remoto_de_github(sandbox):
    git(sandbox.b, "remote", "set-url", "origin", "https://github.com/usuario/repo.git")
    status = analyze_repo(sandbox.b, fetch=False)
    assert status.is_github
    assert status.remote_url == "https://github.com/usuario/repo.git"


def test_remoto_que_no_es_de_github(sandbox):
    status = analyze_repo(sandbox.b, fetch=False)
    assert not status.is_github


def test_sin_red_el_analisis_sigue_con_el_estado_local(sandbox):
    """Sección 11: si el fetch falla, se analiza igualmente lo local."""
    git(sandbox.b, "remote", "set-url", "origin", "https://github.com/no/existe-jamas.git")
    commit(sandbox.b, "mio.txt", "x\n", "Trabajo local", TEAM_B)
    status = analyze_repo(sandbox.b, timeout=15)
    assert not status.fetch_ok
    assert status.fetch_error
    assert status.ahead == 1


def test_fecha_de_ultimo_cambio_local(sandbox):
    status = analyze_repo(sandbox.b)
    assert status.last_local_change is not None


def test_analisis_no_modifica_el_repositorio(sandbox):
    commit(sandbox.a, "desde-a.txt", "a\n", "Cambio en A", TEAM_A)
    sandbox.push(sandbox.a)
    write(sandbox.b, "borrador.txt", "no guardado\n")
    antes = git(sandbox.b, "rev-parse", "HEAD")

    analyze_repo(sandbox.b)

    assert git(sandbox.b, "rev-parse", "HEAD") == antes
    assert (sandbox.b / "borrador.txt").read_text(encoding="utf-8") == "no guardado\n"
    assert (sandbox.b / "README.md").exists()


# --- descubrimiento y análisis masivo -------------------------------------

def test_descubre_repositorios_hasta_dos_niveles(sandbox, tmp_path: Path):
    raiz = tmp_path / "proyectos"
    (raiz / "grupo" / "hondo").mkdir(parents=True)
    git(raiz / "grupo" / "hondo", "init", "-b", "main", ".")
    suelto = raiz / "suelto"
    suelto.mkdir()
    git(suelto, "init", "-b", "main", ".")
    (raiz / "sin-git").mkdir()
    muy_hondo = raiz / "a" / "b" / "c"
    muy_hondo.mkdir(parents=True)
    git(muy_hondo, "init", "-b", "main", ".")

    encontrados = {p.name for p in discover_repos(raiz, max_depth=2)}
    assert encontrados == {"hondo", "suelto"}


def test_analisis_masivo_informa_del_avance(sandbox):
    avance = []
    resultados = analyze_all(
        [sandbox.a, sandbox.b],
        on_progress=lambda hechos, total, estado: avance.append((hechos, total)),
    )
    assert len(resultados) == 2
    assert [r.name for r in resultados] == ["equipo-a", "equipo-b"]
    assert avance[-1] == (2, 2)


def test_detecta_renombrados_y_preparados(sandbox):
    """El parser de 'status --porcelain=v2 -z' consume bien el campo extra
    que Git añade en los renombrados."""
    commit(sandbox.b, "viejo.txt", "contenido\n", "Archivo inicial", TEAM_B)
    git(sandbox.b, "mv", "viejo.txt", "nuevo.txt")
    write(sandbox.b, "suelto.txt", "sin seguir\n")

    status = analyze_repo(sandbox.b, fetch=False)
    por_ruta = {f.path: f for f in status.files}
    assert por_ruta["nuevo.txt"].change == "renamed"
    assert por_ruta["nuevo.txt"].old_path == "viejo.txt"
    assert por_ruta["nuevo.txt"].staged
    assert por_ruta["suelto.txt"].change == "untracked"
    assert not por_ruta["suelto.txt"].staged


def test_detecta_rutas_con_espacios_y_tildes(sandbox):
    write(sandbox.b, "mis documentos/año 2026.txt", "hola\n")
    status = analyze_repo(sandbox.b, fetch=False)
    assert [f.path for f in status.files] == ["mis documentos/año 2026.txt"]
