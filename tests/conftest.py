"""Andamiaje de pruebas: dos equipos (A y B) contra un mismo remoto.

Se simula el escenario real del usuario (portátil y PC de mesa) con un
repositorio *bare* local que hace de GitHub y dos clones que hacen de equipos.
Todo ocurre en carpetas temporales; no se toca ningún repositorio real ni la
configuración de Git del usuario.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TEAM_A = "PORTATIL"
TEAM_B = "PC-MESA"


@pytest.fixture(autouse=True, scope="session")
def _isolated_git_environment(tmp_path_factory):
    """Aísla Git de la configuración global del usuario durante las pruebas."""
    home = tmp_path_factory.mktemp("git-home")
    previous = dict(os.environ)
    os.environ.update({
        "HOME": str(home),
        "USERPROFILE": str(home),
        "GIT_CONFIG_GLOBAL": str(home / "gitconfig"),
        "GIT_CONFIG_SYSTEM": str(home / "gitsystem"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Vaiven Test",
        "GIT_AUTHOR_EMAIL": "test@vaiven.local",
        "GIT_COMMITTER_NAME": "Vaiven Test",
        "GIT_COMMITTER_EMAIL": "test@vaiven.local",
        "VAIVEN_DATA_DIR": str(home / "datos"),
    })
    yield
    os.environ.clear()
    os.environ.update(previous)


def git(repo: Path, *args: str, check: bool = True) -> str:
    """Ejecuta git directamente, sin pasar por git_ops (que es lo que probamos)."""
    done = subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, check=False
    )
    if check and done.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} falló: {done.stderr or done.stdout}")
    return done.stdout.strip()


def write(repo: Path, name: str, content: str) -> Path:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def commit(repo: Path, name: str, content: str, message: str, team: str | None = None) -> str:
    """Crea o modifica un archivo y lo confirma, con el trailer de equipo (7)."""
    write(repo, name, content)
    git(repo, "add", "-A")
    full = f"{message}\n\nSynced-From: {team}" if team else message
    git(repo, "commit", "-m", full)
    return git(repo, "rev-parse", "HEAD")


@dataclass
class Sandbox:
    """Un remoto y dos clones que hacen de portátil y PC de mesa."""

    root: Path
    remote: Path
    a: Path
    b: Path

    def push(self, repo: Path) -> None:
        git(repo, "push", "origin", "HEAD")

    def fetch(self, repo: Path) -> None:
        git(repo, "fetch", "origin")


@pytest.fixture
def sandbox(tmp_path: Path) -> Sandbox:
    """Remoto vacío + equipo A con un commit inicial + equipo B clonado de él."""
    root = tmp_path / "sandbox"
    root.mkdir()
    remote = root / "remoto.git"
    remote.mkdir()
    git(remote, "init", "--bare", "-b", "main", ".")

    a = root / "equipo-a"
    git(root, "clone", str(remote), str(a))
    commit(a, "README.md", "proyecto de prueba\n", "Commit inicial", TEAM_A)
    git(a, "push", "-u", "origin", "main")

    b = root / "equipo-b"
    git(root, "clone", str(remote), str(b))

    return Sandbox(root=root, remote=remote, a=a, b=b)
