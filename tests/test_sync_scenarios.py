"""Fase 3 — los ocho escenarios obligatorios de la sección 13 de SPEC.md.

Los equipos A (portátil) y B (PC de mesa) son dos clones del mismo remoto.
La regla que atraviesa todas las pruebas: **no se pierde ni un archivo ni un
commit**, y se comprueba comparando hashes y contenido.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src import safety, sync_engine
from src.analyzer import RepoState, analyze_repo
from src.sync_engine import (
    MissingRepo, Plan, execute, plan_push, plan_sync, push_repo,
    stash_and_sync, sync_repo, try_merge, undo,
)
from tests.conftest import TEAM_A, TEAM_B, commit, git, write


@pytest.fixture(autouse=True)
def _indice_aislado(tmp_path, monkeypatch):
    monkeypatch.setattr(safety, "_index_path", lambda: tmp_path / "backups.json")


def estado(repo: Path):
    return analyze_repo(repo)


def archivos(repo: Path) -> dict[str, str]:
    """Contenido de todos los archivos del repo, salvo la carpeta .git."""
    return {
        str(p.relative_to(repo)): p.read_text(encoding="utf-8", errors="replace")
        for p in sorted(repo.rglob("*"))
        if p.is_file() and ".git" not in p.relative_to(repo).parts
    }


def historial(repo: Path) -> list[str]:
    return git(repo, "log", "--format=%H").splitlines()


# --- escenario 1 -----------------------------------------------------------

def test_escenario_1_a_sube_y_b_sincroniza(sandbox):
    """A sube cambios → B sincroniza → B queda igual que A."""
    commit(sandbox.a, "trabajo.txt", "hecho en el portátil\n", "Trabajo de A", TEAM_A)
    subida = push_repo(estado(sandbox.a), TEAM_A, "Sync desde PORTATIL")
    assert subida.ok, subida.message

    plan = plan_sync([estado(sandbox.b)])
    assert [a.name for a in plan.to_do] == ["equipo-b"]

    informe = execute(plan, TEAM_B)
    assert informe.failed == []
    assert len(informe.done) == 1

    assert archivos(sandbox.b) == archivos(sandbox.a)
    assert historial(sandbox.b) == historial(sandbox.a)
    assert estado(sandbox.b).state is RepoState.UP_TO_DATE


# --- escenario 2 -----------------------------------------------------------

def test_escenario_2_b_tiene_trabajo_mas_nuevo_y_no_se_toca(sandbox):
    """B tiene 2 commits sin subir y pulsa «Sincronizar todo»:
    B no se modifica y aparece el aviso de trabajo más nuevo."""
    commit(sandbox.b, "uno.txt", "1\n", "Primero", TEAM_B)
    commit(sandbox.b, "dos.txt", "2\n", "Segundo", TEAM_B)
    antes_archivos, antes_historial = archivos(sandbox.b), historial(sandbox.b)

    plan = plan_sync([estado(sandbox.b)])

    assert plan.to_do == []
    assert len(plan.skipped) == 1
    motivo = plan.skipped[0].reason
    assert "trabajo más nuevo" in motivo
    assert "2 commits" in motivo
    assert "subir_este" in plan.skipped[0].offers

    execute(plan, TEAM_B)
    assert archivos(sandbox.b) == antes_archivos
    assert historial(sandbox.b) == antes_historial


def test_escenario_2_el_aviso_cuenta_commits_y_archivos(sandbox):
    commit(sandbox.b, "uno.txt", "1\n", "Primero", TEAM_B)
    commit(sandbox.b, "dos.txt", "2\n", "Segundo", TEAM_B)
    commit(sandbox.b, "tres.txt", "3\n", "Tercero", TEAM_B)
    for i in range(5):
        write(sandbox.b, f"suelto{i}.txt", "x\n")

    motivo = plan_sync([estado(sandbox.b)]).skipped[0].reason
    assert "3 commits y 5 archivos" in motivo


# --- escenario 3 -----------------------------------------------------------

def test_escenario_3_cambios_sin_commit_y_remoto_adelantado(sandbox):
    """B tiene archivos modificados sin commit y el remoto tiene commits
    nuevos → bloqueado; «Guardar mis cambios aparte y sincronizar» funciona
    y los cambios siguen presentes al final."""
    commit(sandbox.a, "desde-a.txt", "novedad de A\n", "Cambio en A", TEAM_A)
    sandbox.push(sandbox.a)
    write(sandbox.b, "borrador.txt", "trabajo sin guardar\n")
    write(sandbox.b, "README.md", "readme editado\n")

    actual = estado(sandbox.b)
    assert actual.state is RepoState.BEHIND_WITH_LOCAL_CHANGES

    plan = plan_sync([actual])
    assert plan.to_do == []
    assert "guardar_aparte" in plan.skipped[0].offers

    resultado = stash_and_sync(actual, TEAM_B)

    assert resultado.ok, resultado.message
    assert (sandbox.b / "borrador.txt").read_text(encoding="utf-8") == "trabajo sin guardar\n"
    assert (sandbox.b / "README.md").read_text(encoding="utf-8") == "readme editado\n"
    assert (sandbox.b / "desde-a.txt").read_text(encoding="utf-8") == "novedad de A\n"


def test_escenario_3_si_el_pop_choca_los_cambios_quedan_a_salvo(sandbox):
    """Si al recuperar los cambios hay conflicto, el escondite se deja intacto."""
    commit(sandbox.a, "compartido.txt", "version de A\n", "A edita", TEAM_A)
    sandbox.push(sandbox.a)
    write(sandbox.b, "compartido.txt", "mi version sin guardar\n")

    resultado = stash_and_sync(estado(sandbox.b), TEAM_B)

    assert not resultado.ok
    assert "a salvo" in resultado.message
    assert git(sandbox.b, "stash", "list") != ""   # sigue guardado
    respaldo = safety.get_backup(resultado.backup_id)
    assert respaldo is not None and respaldo.has_uncommitted


# --- escenario 4 -----------------------------------------------------------

def test_escenario_4_divergido_se_omite_en_ambas_acciones(sandbox):
    """A y B hacen commits distintos → ambas acciones masivas lo omiten."""
    commit(sandbox.a, "desde-a.txt", "a\n", "Cambio en A", TEAM_A)
    sandbox.push(sandbox.a)
    commit(sandbox.b, "desde-b.txt", "b\n", "Cambio en B", TEAM_B)

    actual = estado(sandbox.b)
    assert actual.state is RepoState.DIVERGED

    for plan in (plan_sync([actual]), plan_push([actual], TEAM_B)):
        assert plan.to_do == []
        assert len(plan.skipped) == 1
        assert "caminos distintos" in plan.skipped[0].reason
        assert "combinar" in plan.skipped[0].offers


def test_escenario_4_combinar_funciona_si_no_hay_conflicto(sandbox):
    commit(sandbox.a, "desde-a.txt", "a\n", "Cambio en A", TEAM_A)
    sandbox.push(sandbox.a)
    commit(sandbox.b, "desde-b.txt", "b\n", "Cambio en B", TEAM_B)

    resultado = try_merge(estado(sandbox.b), TEAM_B)

    assert resultado.ok, resultado.message
    assert (sandbox.b / "desde-a.txt").exists()
    assert (sandbox.b / "desde-b.txt").exists()
    assert estado(sandbox.b).state is RepoState.AHEAD


def test_escenario_4_si_hay_conflicto_aborta_y_deja_el_repo_igual(sandbox):
    commit(sandbox.a, "compartido.txt", "version de A\n", "A edita", TEAM_A)
    sandbox.push(sandbox.a)
    commit(sandbox.b, "compartido.txt", "version de B\n", "B edita", TEAM_B)

    antes_archivos, antes_historial = archivos(sandbox.b), historial(sandbox.b)
    resultado = try_merge(estado(sandbox.b), TEAM_B)

    assert not resultado.ok
    assert "resolverlo a mano" in resultado.message
    assert archivos(sandbox.b) == antes_archivos
    assert historial(sandbox.b) == antes_historial
    assert estado(sandbox.b).state is RepoState.DIVERGED  # ni rastro del rebase


# --- escenario 5 -----------------------------------------------------------

def test_escenario_5_push_rechazado_no_fuerza_nada(sandbox):
    """El remoto avanza durante la operación → no se fuerza, pasa a Divergido."""
    commit(sandbox.b, "mio.txt", "trabajo de B\n", "Trabajo de B", TEAM_B)
    actual = estado(sandbox.b)

    # Mientras tanto, A sube algo (el remoto avanza).
    commit(sandbox.a, "suyo.txt", "trabajo de A\n", "Trabajo de A", TEAM_A)
    sandbox.push(sandbox.a)

    resultado = push_repo(actual, TEAM_B, "Sync desde PC-MESA")

    assert not resultado.ok and resultado.skipped
    assert resultado.new_state is RepoState.DIVERGED
    assert "no se ha subido nada" in resultado.message

    # El remoto conserva el trabajo de A intacto.
    assert git(sandbox.remote, "log", "--format=%s", "-1", "main") == "Trabajo de A"
    assert estado(sandbox.b).state is RepoState.DIVERGED


# --- escenario 6 -----------------------------------------------------------

def test_escenario_6_archivo_env_avisa_y_bloquea_la_confirmacion(sandbox):
    """Archivo .env nuevo sin ignorar → advertencia y confirmación bloqueada."""
    write(sandbox.b, ".env", "API_KEY=secreto\n")

    plan = plan_push([estado(sandbox.b)], TEAM_B)

    assert len(plan.to_do) == 1
    avisos = plan.to_do[0].warnings
    assert [w.path for w in avisos] == [".env"]
    assert avisos[0].is_secret
    assert plan.has_secret_warnings   # la interfaz deshabilita «Sí, continuar»


def test_escenario_6_un_env_ignorado_no_avisa(sandbox):
    write(sandbox.b, ".gitignore", ".env\n")
    write(sandbox.b, ".env", "API_KEY=secreto\n")

    plan = plan_push([estado(sandbox.b)], TEAM_B)
    assert not plan.has_secret_warnings


def test_escenario_6_excluir_el_repo_quita_el_aviso(sandbox):
    """Casillas de la vista previa (6.5.4): excluir el repo desbloquea."""
    write(sandbox.b, ".env", "API_KEY=secreto\n")
    plan = plan_push([estado(sandbox.b)], TEAM_B)
    plan.to_do[0].selected = False
    assert not plan.has_secret_warnings
    assert plan.is_empty


@pytest.mark.parametrize(
    "nombre", [".env", ".env.local", "clave.pem", "servidor.key", "id_rsa", "credentials.json"]
)
def test_escenario_6_todos_los_patrones_de_secreto(sandbox, nombre):
    write(sandbox.b, nombre, "secreto\n")
    plan = plan_push([estado(sandbox.b)], TEAM_B)
    assert plan.has_secret_warnings, nombre


def test_avisa_de_archivos_demasiado_grandes(sandbox, monkeypatch):
    monkeypatch.setattr(sync_engine, "BIG_FILE_BYTES", 1024)
    (sandbox.b / "grande.bin").write_bytes(b"0" * 5000)

    avisos = sync_engine.find_warnings(estado(sandbox.b))
    assert [w.kind for w in avisos] == ["archivo_grande"]
    assert "MB" in avisos[0].detail


# --- escenario 7 -----------------------------------------------------------

def test_escenario_7_deshacer_devuelve_el_estado_previo_exacto(sandbox):
    """Tras cualquier operación, «Deshacer» devuelve el repo al estado previo."""
    write(sandbox.b, "borrador.txt", "trabajo a medias\n")
    write(sandbox.b, "README.md", "readme editado\n")
    antes_archivos, antes_historial = archivos(sandbox.b), historial(sandbox.b)

    subida = push_repo(estado(sandbox.b), TEAM_B, "Sync desde PC-MESA")
    assert subida.ok, subida.message
    assert historial(sandbox.b) != antes_historial

    deshecho = undo(estado(sandbox.b))

    assert deshecho.ok, deshecho.message
    assert archivos(sandbox.b) == antes_archivos
    assert historial(sandbox.b) == antes_historial


def test_escenario_7_deshacer_tras_sincronizar(sandbox):
    commit(sandbox.a, "desde-a.txt", "a\n", "Cambio en A", TEAM_A)
    sandbox.push(sandbox.a)
    antes_archivos, antes_historial = archivos(sandbox.b), historial(sandbox.b)

    assert sync_repo(estado(sandbox.b), TEAM_B).ok
    assert undo(estado(sandbox.b)).ok

    assert archivos(sandbox.b) == antes_archivos
    assert historial(sandbox.b) == antes_historial


# --- escenario 8 -----------------------------------------------------------

def test_escenario_8_ninguna_operacion_pierde_commits(sandbox):
    """Recorrido completo comprobando que todo commit creado sigue accesible."""
    creados: set[str] = set()
    creados.add(commit(sandbox.a, "a1.txt", "a1\n", "A uno", TEAM_A))
    push_repo(estado(sandbox.a), TEAM_A, "Sync desde PORTATIL")
    creados.add(commit(sandbox.b, "b1.txt", "b1\n", "B uno", TEAM_B))

    # B está divergido: se combina, se sube y se sincroniza A.
    assert try_merge(estado(sandbox.b), TEAM_B).ok
    creados.update(historial(sandbox.b))
    assert push_repo(estado(sandbox.b), TEAM_B, "Sync desde PC-MESA").ok
    assert sync_repo(estado(sandbox.a), TEAM_A).ok

    todos_a = set(historial(sandbox.a))
    todos_b = set(historial(sandbox.b))
    assert todos_a == todos_b
    assert archivos(sandbox.a) == archivos(sandbox.b)

    # Los commits originales siguen alcanzables (en la historia o en un respaldo).
    for sha in creados:
        alcanzable = (
            sha in todos_b
            or git(sandbox.b, "cat-file", "-t", sha, check=False) == "commit"
        )
        assert alcanzable, sha


def test_escenario_8_un_error_no_detiene_a_los_demas(sandbox, tmp_path):
    """Sección 11: el error de un repo no impide que los otros se completen."""
    roto = tmp_path / "no-es-repo"
    roto.mkdir()
    commit(sandbox.b, "bueno.txt", "x\n", "Trabajo bueno", TEAM_B)

    plan = plan_push([estado(sandbox.b)], TEAM_B)
    plan.to_do.append(
        sync_engine.PlannedAction(analyze_repo(roto), "push", commit_message="m")
    )

    informe = execute(plan, TEAM_B)

    assert len(informe.done) == 1
    assert len(informe.failed) == 1
    assert "1 completado, 0 omitidos, 1 con error" in informe.summary()


# --- la vista previa (sección 6.5) -----------------------------------------

def test_la_vista_previa_agrupa_en_tres_bloques(sandbox, tmp_path):
    al_dia = estado(sandbox.a)
    commit(sandbox.b, "mio.txt", "x\n", "Trabajo", TEAM_B)
    con_cambios = estado(sandbox.b)

    otro = tmp_path / "divergido"
    git(tmp_path, "clone", str(sandbox.remote), str(otro))
    commit(sandbox.a, "desde-a.txt", "a\n", "A", TEAM_A)
    sandbox.push(sandbox.a)
    commit(otro, "desde-otro.txt", "o\n", "Otro", "OTRO")
    divergido = estado(otro)

    plan = plan_push([al_dia, con_cambios, divergido], TEAM_B)

    assert [a.name for a in plan.to_do] == ["equipo-b"]
    assert [a.name for a in plan.skipped] == ["divergido"]
    assert [a.name for a in plan.unchanged] == ["equipo-a"]
    assert plan.question() == "¿Seguro que quieres subir 1 proyecto?"


def test_el_plan_vacio_se_detecta(sandbox):
    plan = plan_sync([estado(sandbox.b)])
    assert plan.is_empty
    assert plan.question() == "¿Seguro que quieres sincronizar 0 proyectos?"


def test_los_repos_no_clonados_vienen_desmarcados(sandbox):
    """Sección 8: por defecto desmarcados."""
    plan = plan_sync([estado(sandbox.b)], missing=[MissingRepo("otro", "https://github.com/u/otro.git")])
    assert plan.missing[0].selected is False
    assert plan.is_empty


def test_clonar_lo_que_falta(sandbox, tmp_path):
    destino = tmp_path / "raiz"
    destino.mkdir()
    plan = Plan(kind="sync", missing=[MissingRepo("clonado", str(sandbox.remote), selected=True)])

    informe = execute(plan, TEAM_B, root=destino)

    assert informe.failed == []
    assert (destino / "clonado" / "README.md").exists()


def test_clonar_no_pisa_una_carpeta_existente(sandbox, tmp_path):
    destino = tmp_path / "raiz"
    (destino / "clonado").mkdir(parents=True)
    (destino / "clonado" / "mio.txt").write_text("no me toques\n", encoding="utf-8")
    plan = Plan(kind="sync", missing=[MissingRepo("clonado", str(sandbox.remote), selected=True)])

    informe = execute(plan, TEAM_B, root=destino)

    assert informe.skipped and not informe.done
    assert (destino / "clonado" / "mio.txt").read_text(encoding="utf-8") == "no me toques\n"


# --- detalles de las secciones 7 y 8 ---------------------------------------

def test_el_commit_lleva_el_trailer_del_equipo(sandbox):
    write(sandbox.b, "nuevo.txt", "x\n")
    push_repo(estado(sandbox.b), TEAM_B, "Sync desde PC-MESA")
    cuerpo = git(sandbox.b, "log", "-1", "--format=%B")
    assert f"Synced-From: {TEAM_B}" in cuerpo
    assert estado(sandbox.b).state is RepoState.UP_TO_DATE


def test_el_mensaje_por_defecto_sigue_el_formato_del_spec():
    from datetime import datetime
    mensaje = sync_engine.default_commit_message("PORTATIL", datetime(2026, 9, 22, 15, 30))
    assert mensaje == "Sync desde PORTATIL — 2026-09-22 15:30"


def test_una_rama_sin_upstream_no_se_sube_sin_confirmacion(sandbox):
    git(sandbox.b, "checkout", "-b", "experimento")
    commit(sandbox.b, "nuevo.txt", "x\n", "Trabajo en rama nueva", TEAM_B)

    sin_permiso = push_repo(estado(sandbox.b), TEAM_B, "m")
    assert sin_permiso.skipped
    assert "confirmación" in sin_permiso.message

    con_permiso = push_repo(estado(sandbox.b), TEAM_B, "m", allow_set_upstream=True)
    assert con_permiso.ok, con_permiso.message
    assert "experimento" in git(sandbox.remote, "for-each-ref", "--format=%(refname)")


def test_sincronizar_verifica_que_head_coincide_con_github(sandbox):
    commit(sandbox.a, "desde-a.txt", "a\n", "Cambio en A", TEAM_A)
    sandbox.push(sandbox.a)

    resultado = sync_repo(estado(sandbox.b), TEAM_B)

    assert resultado.ok
    assert git(sandbox.b, "rev-parse", "HEAD") == git(sandbox.b, "rev-parse", "@{u}")


def test_sincronizar_reevalua_antes_de_actuar(sandbox):
    """Aunque el plan diga que se puede, se vuelve a comprobar al ejecutar."""
    commit(sandbox.a, "desde-a.txt", "a\n", "Cambio en A", TEAM_A)
    sandbox.push(sandbox.a)
    viejo = estado(sandbox.b)
    assert viejo.can_sync

    write(sandbox.b, "aparece-después.txt", "cambio de última hora\n")
    resultado = sync_repo(viejo, TEAM_B)

    assert resultado.skipped
    assert (sandbox.b / "aparece-después.txt").exists()


def test_cada_operacion_deja_un_respaldo(sandbox):
    write(sandbox.b, "nuevo.txt", "x\n")
    resultado = push_repo(estado(sandbox.b), TEAM_B, "m")
    assert resultado.backup_id
    assert safety.get_backup(resultado.backup_id) is not None


def test_el_progreso_se_informa_repo_a_repo(sandbox):
    commit(sandbox.b, "mio.txt", "x\n", "Trabajo", TEAM_B)
    avance = []
    execute(plan_push([estado(sandbox.b)], TEAM_B), TEAM_B,
            on_progress=lambda hechos, total, nombre: avance.append((hechos, total, nombre)))
    assert avance[0] == (0, 1, "equipo-b")
    assert avance[-1] == (1, 1, None)


def test_los_errores_de_git_se_traducen():
    assert "internet" in sync_engine._readable_error("fatal: unable to access 'https://...'")
    assert "caducado" in sync_engine._readable_error("remote: Invalid credentials\nHTTP 401")
    assert "combinar" in sync_engine._readable_error("! [rejected] main -> main (non-fast-forward)")


# --- regresiones de la revisión --------------------------------------------

def test_guardar_aparte_no_saca_un_escondite_antiguo_del_usuario(sandbox):
    """Si no hay nada que guardar, el «stash pop» no debe sacar el del usuario."""
    commit(sandbox.a, "desde-a.txt", "novedad de A\n", "Cambio en A", TEAM_A)
    sandbox.push(sandbox.a)
    write(sandbox.b, "README.md", "idea que aparqué\n")
    git(sandbox.b, "stash", "push", "-m", "escondite del usuario")
    # El análisis queda viejo: cree que hay cambios, pero el usuario los
    # esconde antes de pulsar el botón.
    write(sandbox.b, "README.md", "otra cosa\n")
    viejo = estado(sandbox.b)
    git(sandbox.b, "stash", "push", "-m", "segundo escondite del usuario")
    antes = git(sandbox.b, "stash", "list")

    resultado = stash_and_sync(viejo, TEAM_B)

    assert resultado.ok, resultado.message
    assert git(sandbox.b, "stash", "list") == antes
    assert (sandbox.b / "desde-a.txt").exists()
    assert (sandbox.b / "README.md").read_text(encoding="utf-8") == "proyecto de prueba\n"


def test_combinar_con_cambios_sin_guardar_no_crea_respaldo(sandbox):
    write(sandbox.b, "borrador.txt", "a medias\n")
    resultado = try_merge(estado(sandbox.b), TEAM_B)
    assert resultado.skipped and resultado.backup_id is None
    assert safety.list_backups(sandbox.b) == []


def test_deshacer_una_subida_avisa_de_que_sigue_en_github(sandbox):
    write(sandbox.b, "README.md", "readme editado\n")
    assert push_repo(estado(sandbox.b), TEAM_B, "Sync desde PC-MESA").ok

    deshecho = undo(estado(sandbox.b))

    assert deshecho.ok
    assert "sigue en GitHub" in deshecho.message


def test_dos_operaciones_no_coinciden_en_el_mismo_repo(sandbox):
    """Otra ventana trabajando en el repo: la segunda operación se omite."""
    import threading
    commit(sandbox.a, "desde-a.txt", "a\n", "Cambio en A", TEAM_A)
    sandbox.push(sandbox.a)
    dentro, salir = threading.Event(), threading.Event()

    def ocupar():
        with safety.repo_lock(sandbox.b):
            dentro.set()
            salir.wait(10)

    hilo = threading.Thread(target=ocupar)
    hilo.start()
    dentro.wait(10)
    try:
        resultado = sync_repo(estado(sandbox.b), TEAM_B)
        assert resultado.skipped and "otra operación" in resultado.message
        with pytest.raises(safety.RepoBusy):
            safety.restore_backup(safety.create_backup(sandbox.b, "x", TEAM_B))
    finally:
        salir.set()
        hilo.join()

    assert sync_repo(estado(sandbox.b), TEAM_B).ok
    assert undo(estado(sandbox.b)).ok   # deshacer restaura sin bloquearse a sí mismo
