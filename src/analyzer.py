"""Cálculo del estado de cada repositorio (sección 6.2 de SPEC.md).

El análisis es **obligatorio** antes de ofrecer o ejecutar cualquier acción.
Este módulo solo lee: no modifica ningún repositorio (el único comando que
toca la red es ``git fetch``, que nunca altera el árbol de trabajo).
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from . import git_ops
from .git_ops import GitError
from .logger import get_logger

log = get_logger("analyzer")

#: Trailer con el que se marca el equipo de origen de cada commit (sección 7).
TEAM_TRAILER = "Synced-From"

#: Profundidad máxima al buscar repositorios bajo la carpeta raíz (sección 9.2).
DEFAULT_SCAN_DEPTH = 2

#: Carpetas que nunca merece la pena recorrer al buscar repositorios.
_SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", "venv", ".venv", "env",
    "dist", "build", ".idea", ".vscode", "target", "vendor", ".tox",
}


class RepoState(str, Enum):
    """Los estados de la tabla de la sección 6.2."""

    UP_TO_DATE = "up_to_date"
    LOCAL_CHANGES = "local_changes"
    AHEAD = "ahead"
    BEHIND = "behind"
    DIVERGED = "diverged"
    BEHIND_WITH_LOCAL_CHANGES = "behind_with_local_changes"
    CONFLICT = "conflict"
    NO_UPSTREAM = "no_upstream"
    DETACHED_HEAD = "detached_head"
    NOT_CLONED = "not_cloned"
    ERROR = "error"


#: Texto que ve el usuario, en español y sin jerga de Git (sección 9.4).
STATE_LABELS: dict[RepoState, str] = {
    RepoState.UP_TO_DATE: "Al día",
    RepoState.LOCAL_CHANGES: "Cambios locales",
    RepoState.AHEAD: "Adelantado",
    RepoState.BEHIND: "Atrasado",
    RepoState.DIVERGED: "Divergido",
    RepoState.BEHIND_WITH_LOCAL_CHANGES: "Atrasado con cambios locales",
    RepoState.CONFLICT: "En conflicto",
    RepoState.NO_UPSTREAM: "Sin rama remota",
    RepoState.DETACHED_HEAD: "HEAD suelto",
    RepoState.NOT_CLONED: "No clonado",
    RepoState.ERROR: "Error al analizar",
}

#: Color del indicador en la lista de proyectos (sección 6.2).
STATE_COLORS: dict[RepoState, str] = {
    RepoState.UP_TO_DATE: "verde",
    RepoState.LOCAL_CHANGES: "azul",
    RepoState.AHEAD: "azul",
    RepoState.BEHIND: "amarillo",
    RepoState.DIVERGED: "rojo",
    RepoState.BEHIND_WITH_LOCAL_CHANGES: "rojo",
    RepoState.CONFLICT: "rojo",
    RepoState.NO_UPSTREAM: "gris",
    RepoState.DETACHED_HEAD: "gris",
    RepoState.NOT_CLONED: "gris",
    RepoState.ERROR: "gris",
}

#: Estados que "Sincronizar todo" y "Subir todo" nunca tocan en masa (6.3).
BLOCKED_STATES = frozenset({
    RepoState.DIVERGED,
    RepoState.BEHIND_WITH_LOCAL_CHANGES,
    RepoState.CONFLICT,
    RepoState.DETACHED_HEAD,
    RepoState.ERROR,
})


@dataclass
class CommitInfo:
    """Un commit entrante o saliente, con su equipo de origen."""

    sha: str
    subject: str = ""
    author: str = ""
    date: datetime | None = None
    team: str | None = None

    @property
    def short_sha(self) -> str:
        return self.sha[:7]


@dataclass
class FileChange:
    """Un archivo modificado, nuevo, borrado o en conflicto."""

    path: str
    change: str  # modified | added | deleted | renamed | untracked | conflict
    staged: bool = False
    old_path: str | None = None


@dataclass
class RepoStatus:
    """Retrato completo de un repositorio en un instante dado."""

    name: str
    path: Path
    state: RepoState = RepoState.ERROR
    branch: str | None = None
    upstream: str | None = None
    ahead: int = 0
    behind: int = 0
    files: list[FileChange] = field(default_factory=list)
    incoming: list[CommitInfo] = field(default_factory=list)
    outgoing: list[CommitInfo] = field(default_factory=list)
    remote_url: str | None = None
    is_github: bool = False
    last_local_change: datetime | None = None
    fetch_ok: bool = True
    fetch_error: str | None = None
    error: str | None = None

    # --- derivados ------------------------------------------------------

    @property
    def label(self) -> str:
        return STATE_LABELS[self.state]

    @property
    def color(self) -> str:
        return STATE_COLORS[self.state]

    @property
    def dirty(self) -> bool:
        """¿Hay cambios sin guardar en un commit?"""
        return bool(self.files)

    @property
    def has_conflicts(self) -> bool:
        return any(f.change == "conflict" for f in self.files)

    @property
    def modified(self) -> list[FileChange]:
        return [f for f in self.files if f.change in ("modified", "renamed")]

    @property
    def added(self) -> list[FileChange]:
        return [f for f in self.files if f.change in ("added", "untracked")]

    @property
    def deleted(self) -> list[FileChange]:
        return [f for f in self.files if f.change == "deleted"]

    @property
    def is_blocked(self) -> bool:
        """¿Queda fuera de las acciones masivas y necesita atención (6.6)?"""
        return self.state in BLOCKED_STATES

    @property
    def can_sync(self) -> bool:
        """Solo se sincroniza automáticamente lo atrasado y limpio (6.3)."""
        return self.state == RepoState.BEHIND and not self.dirty

    @property
    def can_push(self) -> bool:
        """Se sube lo que tiene trabajo propio y no está bloqueado (7)."""
        if self.is_blocked:
            return False
        return self.state in (RepoState.AHEAD, RepoState.LOCAL_CHANGES, RepoState.NO_UPSTREAM) and (
            self.ahead > 0 or bool(self.files)
        )

    @property
    def has_newer_work(self) -> bool:
        """Este equipo tiene trabajo que GitHub no tiene (6.3)."""
        return self.ahead > 0 or bool(self.files)

    def summary(self) -> str:
        """Frase corta para la lista de proyectos (sección 9.1)."""
        if self.state == RepoState.ERROR:
            return self.error or "No se pudo analizar este proyecto"
        if self.state == RepoState.NOT_CLONED:
            return "Está en GitHub pero no en este equipo"
        if self.state == RepoState.DETACHED_HEAD:
            return "No estás en ninguna rama"
        if self.state == RepoState.NO_UPSTREAM:
            return "Esta rama todavía no existe en GitHub"
        if self.state == RepoState.CONFLICT:
            return "Hay una combinación a medias que debes resolver"
        if self.state == RepoState.UP_TO_DATE:
            return "Al día"

        parts: list[str] = []
        if self.behind:
            teams = sorted({c.team for c in self.incoming if c.team})
            origin = f" del {teams[0]}" if len(teams) == 1 else ""
            parts.append(
                f"GitHub tiene {self.behind} cambio{'s' if self.behind != 1 else ''} nuevo"
                f"{'s' if self.behind != 1 else ''}{origin}"
            )
        if self.ahead:
            parts.append(f"{self.ahead} commit{'s' if self.ahead != 1 else ''} por subir")
        if self.files:
            count = len(self.files)
            parts.append(f"{count} archivo{'s' if count != 1 else ''} sin subir")
        return " · ".join(parts) or "Al día"


# --------------------------------------------------------------------------
# Descubrimiento de repositorios
# --------------------------------------------------------------------------

def discover_repos(root: "str | Path", max_depth: int = DEFAULT_SCAN_DEPTH) -> list[Path]:
    """Busca repositorios Git bajo ``root``, hasta ``max_depth`` niveles (9.2).

    Devuelve las rutas ordenadas por nombre. No entra dentro de un repositorio
    ya encontrado: los submódulos no se tratan como proyectos independientes.
    """
    root = Path(root)
    found: list[Path] = []
    if not root.is_dir():
        return found

    def walk(directory: Path, depth: int) -> None:
        if (directory / ".git").exists():
            found.append(directory)
            return
        if depth >= max_depth:
            return
        try:
            entries = sorted(directory.iterdir())
        except (PermissionError, OSError):
            return
        for entry in entries:
            if entry.is_dir() and not entry.name.startswith(".") and entry.name not in _SKIP_DIRS:
                walk(entry, depth + 1)

    walk(root, 0)
    return sorted(found, key=lambda p: p.name.lower())


# --------------------------------------------------------------------------
# Lectura del estado
# --------------------------------------------------------------------------

def _parse_porcelain_v2(output: str) -> list[FileChange]:
    """Convierte ``git status --porcelain=v2 -z`` en una lista de cambios."""
    changes: list[FileChange] = []
    fields = output.split("\0")
    i = 0
    while i < len(fields):
        entry = fields[i]
        i += 1
        if not entry:
            continue
        kind = entry[0]
        if kind == "?":
            changes.append(FileChange(path=entry[2:], change="untracked"))
        elif kind == "u":
            parts = entry.split(" ", 10)
            changes.append(FileChange(path=parts[-1], change="conflict"))
        elif kind == "1":
            parts = entry.split(" ", 8)
            xy, path = parts[1], parts[-1]
            changes.append(_from_xy(xy, path))
        elif kind == "2":
            # Renombrado o copiado: la ruta original viene en el campo siguiente.
            parts = entry.split(" ", 9)
            xy, path = parts[1], parts[-1]
            old = fields[i] if i < len(fields) else None
            i += 1
            change = _from_xy(xy, path)
            change.change = "renamed"
            change.old_path = old
            changes.append(change)
        # '!' (ignorado) no se pide y se descarta si apareciera.
    return changes


def _from_xy(xy: str, path: str) -> FileChange:
    """Interpreta el par de letras de estado de ``porcelain=v2``."""
    index_status, worktree_status = (xy + "..")[0], (xy + "..")[1]
    staged = index_status not in (".", " ")
    if "D" in (index_status, worktree_status):
        change = "deleted"
    elif index_status == "A":
        change = "added"
    elif index_status in ("R", "C") or worktree_status in ("R", "C"):
        change = "renamed"
    else:
        change = "modified"
    return FileChange(path=path, change=change, staged=staged)


def _in_progress_operation(repo: Path) -> str | None:
    """Detecta una combinación, rebase o cherry-pick a medias."""
    git_dir = repo / ".git"
    if git_dir.is_file():  # worktree o submódulo: .git es un archivo con la ruta real
        result = git_ops.run(["rev-parse", "--git-dir"], cwd=repo)
        if result.ok:
            candidate = Path(result.out)
            git_dir = candidate if candidate.is_absolute() else repo / candidate
    markers = {
        "MERGE_HEAD": "combinación",
        "REBASE_HEAD": "rebase",
        "CHERRY_PICK_HEAD": "cherry-pick",
        "REVERT_HEAD": "revert",
    }
    for marker, name in markers.items():
        if (git_dir / marker).exists():
            return name
    for directory in ("rebase-merge", "rebase-apply"):
        if (git_dir / directory).exists():
            return "rebase"
    return None


def _commits(repo: Path, revision_range: str, limit: int = 50) -> list[CommitInfo]:
    """Lee commits con su asunto, autor, fecha y equipo de origen (6.2)."""
    separator = "\x1f"
    fmt = separator.join(["%H", "%s", "%an", "%cI", f"%(trailers:key={TEAM_TRAILER},valueonly,separator=%x2C)"])
    result = git_ops.run(
        ["log", f"--max-count={limit}", f"--format={fmt}", revision_range],
        cwd=repo,
    )
    if not result.ok:
        return []
    commits: list[CommitInfo] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split(separator)
        while len(parts) < 5:
            parts.append("")
        commits.append(
            CommitInfo(
                sha=parts[0],
                subject=parts[1],
                author=parts[2],
                date=_parse_date(parts[3]),
                team=parts[4].strip() or None,
            )
        )
    return commits


def _parse_date(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.strip())
    except (ValueError, AttributeError):
        return None


def _last_local_change(repo: Path, files: list[FileChange]) -> datetime | None:
    """Fecha de la última modificación local: último commit o archivo tocado."""
    dates: list[datetime] = []
    result = git_ops.run(["log", "-1", "--format=%cI"], cwd=repo)
    if result.ok and result.out:
        parsed = _parse_date(result.out)
        if parsed:
            dates.append(parsed)
    for change in files[:200]:
        try:
            stamp = (repo / change.path).stat().st_mtime
        except OSError:
            continue
        dates.append(datetime.fromtimestamp(stamp, tz=timezone.utc).astimezone())
    return max(dates) if dates else None


def _decide_state(
    *, detached: bool, in_progress: str | None, has_conflicts: bool,
    upstream: str | None, ahead: int, behind: int, dirty: bool,
) -> RepoState:
    """Traduce los datos crudos a uno de los estados de la sección 6.2.

    El orden es deliberado: primero lo que exige intervención humana, y solo
    al final lo que se puede automatizar.
    """
    if in_progress or has_conflicts:
        return RepoState.CONFLICT
    if detached:
        return RepoState.DETACHED_HEAD
    if not upstream:
        return RepoState.NO_UPSTREAM
    if ahead and behind:
        return RepoState.DIVERGED
    if behind and dirty:
        return RepoState.BEHIND_WITH_LOCAL_CHANGES
    if behind:
        return RepoState.BEHIND
    if ahead:
        return RepoState.AHEAD
    if dirty:
        return RepoState.LOCAL_CHANGES
    return RepoState.UP_TO_DATE


def analyze_repo(
    path: "str | Path",
    *,
    fetch: bool = True,
    token: str | None = None,
    timeout: int = git_ops.DEFAULT_TIMEOUT,
) -> RepoStatus:
    """Analiza un repositorio y devuelve su estado. Nunca lo modifica."""
    path = Path(path)
    status = RepoStatus(name=path.name, path=path)

    try:
        if not git_ops.is_repo(path):
            status.state = RepoState.ERROR
            status.error = "Esta carpeta no es un repositorio de Git"
            return status

        status.remote_url = git_ops.remote_url(path)
        status.is_github = git_ops.is_github_remote(status.remote_url)

        if fetch and status.remote_url:
            fetched = git_ops.run(
                ["fetch", "--quiet", "--prune", "origin"],
                cwd=path, token=token, timeout=timeout,
            )
            status.fetch_ok = fetched.ok
            if not fetched.ok:
                # Sin red se sigue adelante: el análisis muestra el estado local (11).
                status.fetch_error = fetched.stderr.strip()[:300] or "No se pudo contactar con GitHub"
                log.info("fetch falló en %s: %s", path.name, status.fetch_error)

        status.branch = git_ops.current_branch(path)
        detached = status.branch is None
        status.upstream = None if detached else git_ops.upstream_of(path)

        state_out = git_ops.run(
            ["status", "--porcelain=v2", "-z", "--untracked-files=all"], cwd=path
        )
        status.files = _parse_porcelain_v2(state_out.stdout) if state_out.ok else []

        if status.upstream:
            counts = git_ops.run(
                ["rev-list", "--left-right", "--count", "HEAD...@{u}"], cwd=path
            )
            if counts.ok and counts.out:
                numbers = counts.out.split()
                if len(numbers) == 2:
                    status.ahead, status.behind = int(numbers[0]), int(numbers[1])
            if status.ahead:
                status.outgoing = _commits(path, "@{u}..HEAD")
            if status.behind:
                status.incoming = _commits(path, "HEAD..@{u}")

        status.state = _decide_state(
            detached=detached,
            in_progress=_in_progress_operation(path),
            has_conflicts=any(f.change == "conflict" for f in status.files),
            upstream=status.upstream,
            ahead=status.ahead,
            behind=status.behind,
            dirty=bool(status.files),
        )
        status.last_local_change = _last_local_change(path, status.files)

    except GitError as exc:
        status.state = RepoState.ERROR
        status.error = str(exc)
        log.error("error al analizar %s: %s", path, exc)

    return status


def not_cloned_status(name: str, remote_url: str, root: "str | Path | None" = None) -> RepoStatus:
    """Estado para un repo que existe en GitHub pero no en este equipo (8)."""
    return RepoStatus(
        name=name,
        path=Path(root) / name if root else Path(name),
        state=RepoState.NOT_CLONED,
        remote_url=remote_url,
        is_github=git_ops.is_github_remote(remote_url),
    )


def analyze_all(
    paths: "list[str | Path]",
    *,
    fetch: bool = True,
    token: str | None = None,
    on_progress=None,
    max_workers: int | None = None,
) -> list[RepoStatus]:
    """Analiza varios repositorios en paralelo, sin bloquear a quien llama.

    ``on_progress(hechos, total, estado)`` se invoca tras cada repositorio,
    para que la interfaz muestre el avance (sección 2).
    """
    paths = list(paths)
    total = len(paths)
    if not total:
        return []
    workers = max_workers or min(8, max(1, (os.cpu_count() or 4)))
    results: list[RepoStatus] = [None] * total  # type: ignore[list-item]

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(analyze_repo, path, fetch=fetch, token=token): index
            for index, path in enumerate(paths)
        }
        done = 0
        for future in as_completed(futures):
            index = futures[future]
            try:
                results[index] = future.result()
            except Exception as exc:  # pragma: no cover - red de seguridad
                path = Path(paths[index])
                results[index] = RepoStatus(
                    name=path.name, path=path, state=RepoState.ERROR, error=str(exc)
                )
            done += 1
            if on_progress:
                on_progress(done, total, results[index])
    return results
