"""Fase 1 — aceptación: los comandos destructivos se rechazan (sección 6.1)."""

from __future__ import annotations

import pytest

from src import git_ops
from src.git_ops import (
    ALLOW_BACKUP_REF_DELETE,
    ALLOW_REBASE_PULL,
    ForbiddenGitCommand,
    GitCommandError,
    validate_args,
)
from tests.conftest import commit, write


# --- la lista negra de la sección 6.1 --------------------------------------

PROHIBIDOS = [
    # push forzado, en todas sus formas
    ["push", "--force"],
    ["push", "-f"],
    ["push", "--force-with-lease"],
    ["push", "--force-with-lease=main"],
    ["push", "--force-if-includes"],
    ["push", "origin", "main", "--force"],
    ["push", "-fu", "origin", "main"],
    ["push", "--delete", "origin", "main"],
    ["push", "origin", ":main"],
    ["push", "origin", "+main:main"],
    ["push", "--mirror"],
    # reset destructivo
    ["reset", "--hard"],
    ["reset", "--hard", "HEAD~1"],
    ["reset", "--merge", "origin/main"],
    # borrado de archivos del usuario
    ["clean", "-f"],
    ["clean", "-fd"],
    ["clean", "-ffdx"],
    ["clean", "--force"],
    # descartar cambios sin guardar
    ["checkout", "--", "."],
    ["checkout", "--", "src/main.py"],
    ["checkout", "-f", "main"],
    ["checkout", "--force", "main"],
    ["switch", "--force", "main"],
    ["switch", "--discard-changes", "main"],
    ["restore", "."],
    ["restore", "--staged", "--worktree", "."],
    # pull que no sea avance rápido
    ["pull"],
    ["pull", "origin", "main"],
    ["pull", "--rebase"],
    ["pull", "--no-ff"],
    ["pull", "--ff-only", "--rebase"],
    ["pull", "--force"],
    # borrado de ramas y referencias ajenas a los respaldos
    ["branch", "-D", "main"],
    ["branch", "--delete", "--force", "main"],
    ["update-ref", "-d", "refs/heads/main"],
    # la red de seguridad de Git y los respaldos
    ["stash", "drop"],
    ["stash", "clear"],
    ["reflog", "expire", "--expire=now", "--all"],
    ["gc", "--prune=now"],
    ["filter-branch", "--tree-filter", "rm -rf x"],
    ["rebase", "origin/main"],
    # opciones globales por delante: no sirven para esquivar la validación
    ["-c", "core.editor=true", "push", "--force"],
    ["-C", "/otro/repo", "reset", "--hard"],
    ["-c", "http.https://github.com/.extraheader=AUTHORIZATION: basic x", "push", "-f"],
]

PERMITIDOS = [
    ["status", "--porcelain=v2"],
    ["fetch", "--prune", "origin"],
    ["pull", "--ff-only"],
    ["pull", "--ff-only", "origin", "main"],
    ["push"],
    ["push", "origin", "main"],
    ["push", "-u", "origin", "main"],
    ["add", "-A"],
    ["commit", "-m", "Sync desde PORTATIL"],
    ["stash", "create", "--include-untracked"],
    ["stash", "push", "-u"],
    ["stash", "pop"],
    ["rebase", "--abort"],
    ["rebase", "--continue"],
    ["checkout", "-b", "experimento"],
    ["checkout", "main"],
    ["branch", "-d", "rama-ya-fusionada"],
    ["reset", "HEAD~1"],
    ["reset", "--soft", "HEAD~1"],
    ["log", "-1", "--format=%H"],
    ["rev-list", "--left-right", "--count", "HEAD...@{u}"],
    ["clone", "https://github.com/usuario/repo.git", "destino"],
    ["update-ref", "refs/vaiven-backup/2026-09-22_120000_PORTATIL", "HEAD"],
]


@pytest.mark.parametrize("args", PROHIBIDOS, ids=lambda a: " ".join(a)[:60])
def test_los_comandos_destructivos_se_rechazan(args):
    with pytest.raises(ForbiddenGitCommand):
        validate_args(args)


@pytest.mark.parametrize("args", PERMITIDOS, ids=lambda a: " ".join(a)[:60])
def test_los_comandos_seguros_se_permiten(args):
    validate_args(args)


def test_run_tambien_valida_no_solo_validate_args(sandbox):
    """La validación no se puede esquivar llamando directamente a run()."""
    with pytest.raises(ForbiddenGitCommand):
        git_ops.run(["reset", "--hard"], cwd=sandbox.b)
    with pytest.raises(ForbiddenGitCommand):
        git_ops.run(["push", "--force"], cwd=sandbox.b, token="secreto")


def test_el_repositorio_no_se_toca_cuando_se_rechaza(sandbox):
    write(sandbox.b, "borrador.txt", "trabajo sin guardar\n")
    antes = (sandbox.b / "README.md").read_text(encoding="utf-8")
    with pytest.raises(ForbiddenGitCommand):
        git_ops.run(["clean", "-fd"], cwd=sandbox.b)
    with pytest.raises(ForbiddenGitCommand):
        git_ops.run(["checkout", "--", "."], cwd=sandbox.b)
    assert (sandbox.b / "borrador.txt").exists()
    assert (sandbox.b / "README.md").read_text(encoding="utf-8") == antes


# --- permisos explícitos (6.4 y 6.6) ---------------------------------------

def test_pull_rebase_solo_con_permiso_explicito():
    with pytest.raises(ForbiddenGitCommand):
        validate_args(["pull", "--rebase"])
    validate_args(["pull", "--rebase"], allow={ALLOW_REBASE_PULL})


def test_borrar_referencias_solo_dentro_del_espacio_de_respaldos():
    permiso = {ALLOW_BACKUP_REF_DELETE}
    validate_args(["update-ref", "-d", "refs/vaiven-backup/2026-01-01_000000_PC"], allow=permiso)
    validate_args(["branch", "-D", "vaiven-backup/2026-01-01_000000_PC"], allow=permiso)
    with pytest.raises(ForbiddenGitCommand):
        validate_args(["update-ref", "-d", "refs/heads/main"], allow=permiso)
    with pytest.raises(ForbiddenGitCommand):
        validate_args(["branch", "-D", "main"], allow=permiso)


def test_un_permiso_no_habilita_a_los_demas():
    with pytest.raises(ForbiddenGitCommand):
        validate_args(["pull", "--rebase"], allow={ALLOW_BACKUP_REF_DELETE})
    with pytest.raises(ForbiddenGitCommand):
        validate_args(["push", "--force"], allow={ALLOW_REBASE_PULL, ALLOW_BACKUP_REF_DELETE})


def test_permiso_desconocido_es_un_error_de_programacion():
    with pytest.raises(ValueError):
        validate_args(["status"], allow={"lo-que-sea"})


# --- ejecución y registro --------------------------------------------------

def test_la_cabecera_de_autenticacion_nunca_se_escribe_en_el_repositorio(sandbox):
    resultado = git_ops.run(["fetch", "origin"], cwd=sandbox.b, token="token-secreto")
    assert resultado.ok
    config = (sandbox.b / ".git" / "config").read_text(encoding="utf-8")
    assert "token-secreto" not in config
    assert "extraheader" not in config


def test_los_argumentos_registrables_ocultan_el_token(sandbox):
    resultado = git_ops.run(["status", "--porcelain=v2"], cwd=sandbox.b, token="token-secreto")
    registrable = " ".join(resultado.safe_args)
    assert "token-secreto" not in registrable
    assert "<oculto>" in registrable


def test_auth_args_codifica_el_token_como_pide_la_seccion_5_3():
    import base64
    args = git_ops.auth_args("abc123")
    assert args[0] == "-c"
    esperado = base64.b64encode(b"x-access-token:abc123").decode()
    assert args[1] == f"http.https://github.com/.extraheader=AUTHORIZATION: basic {esperado}"
    assert git_ops.auth_args(None) == []


def test_check_lanza_excepcion_si_git_falla(sandbox):
    with pytest.raises(GitCommandError):
        git_ops.run(["rev-parse", "--verify", "rama-que-no-existe"], cwd=sandbox.b, check=True)


def test_consultas_de_solo_lectura(sandbox):
    assert git_ops.is_repo(sandbox.b)
    assert not git_ops.is_repo(sandbox.root)
    assert git_ops.current_branch(sandbox.b) == "main"
    assert git_ops.upstream_of(sandbox.b) == "origin/main"
    assert git_ops.git_version().startswith("git version")
    assert git_ops.is_github_remote("https://github.com/u/r.git")
    assert git_ops.is_github_remote("git@github.com:u/r.git")
    assert not git_ops.is_github_remote("https://gitlab.com/u/r.git")
    assert not git_ops.is_github_remote(None)
