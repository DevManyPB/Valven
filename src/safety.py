"""Respaldos, restauración y deshacer (sección 6.4 de SPEC.md).

Antes de **cualquier** operación que modifique un repositorio se crea un
respaldo. Un respaldo de Vaivén guarda tres cosas:

1. Dónde estaba ``HEAD`` (referencia ``refs/vaiven-backup/<id>``).
2. Los cambios sin guardar de archivos ya seguidos por Git (un commit de
   *stash* creado con ``git stash create``, que no altera la carpeta).
3. Los archivos nuevos todavía no añadidos a Git, que ``git stash create``
   **no** guarda: se escriben en un árbol aparte usando un índice temporal.

Nada de esto se sube nunca a GitHub: vive solo en el repositorio local, bajo
un espacio de nombres propio que Git no replica.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config, git_ops
from .git_ops import (
    ALLOW_BACKUP_REF_DELETE,
    ALLOW_BACKUP_RESTORE,
    BACKUP_REF_PREFIX,
    GitError,
)
from .logger import get_logger

log = get_logger("safety")

#: Cuánto se conserva un respaldo (sección 6.4).
RETENTION_DAYS = 30
#: Cuántos respaldos se conservan por repositorio pase lo que pase.
RETENTION_COUNT = 20

INDEX_VERSION = 1

_index_lock = threading.Lock()


class BackupError(GitError):
    """No se pudo crear o restaurar un respaldo."""


@dataclass
class Backup:
    """Una fotografía completa de un repositorio en un instante."""

    id: str
    repo_name: str
    repo_path: str
    created_at: str
    reason: str
    team: str
    branch: str | None = None
    head_sha: str | None = None
    head_ref: str | None = None
    stash_sha: str | None = None
    stash_ref: str | None = None
    untracked_sha: str | None = None
    untracked_ref: str | None = None
    files_saved: list[str] = field(default_factory=list)

    @property
    def created(self) -> datetime:
        try:
            return datetime.fromisoformat(self.created_at)
        except ValueError:
            return datetime.now(timezone.utc)

    @property
    def has_uncommitted(self) -> bool:
        return bool(self.stash_sha or self.untracked_sha)

    def describe(self) -> str:
        """Texto para la pantalla de respaldos, en lenguaje sencillo (9.4)."""
        cuando = self.created.astimezone().strftime("%d/%m/%Y a las %H:%M")
        detalle = f", con {len(self.files_saved)} archivo{'s' if len(self.files_saved) != 1 else ''} sin subir" if self.files_saved else ""
        return f"{cuando} — {self.reason}{detalle}"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Backup":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})


# --------------------------------------------------------------------------
# Índice de respaldos (backups.json)
# --------------------------------------------------------------------------

def _index_path() -> Path:
    return config.backups_index_path()


def load_index(path: Path | None = None) -> list[Backup]:
    """Lee ``backups.json``. Un archivo ausente o corrupto no rompe nada."""
    path = path or _index_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError, UnicodeDecodeError):
        return []
    entries = data.get("backups", []) if isinstance(data, dict) else data
    result: list[Backup] = []
    for entry in entries if isinstance(entries, list) else []:
        try:
            result.append(Backup.from_dict(entry))
        except (TypeError, AttributeError):
            continue
    return result


def save_index(backups: list[Backup], path: Path | None = None) -> None:
    """Escribe ``backups.json`` de forma atómica."""
    path = path or _index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": INDEX_VERSION, "backups": [b.to_dict() for b in backups]}
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".backups-", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _add_to_index(backup: Backup) -> None:
    with _index_lock:
        entries = load_index()
        entries.append(backup)
        save_index(entries)


def _remove_from_index(backup_ids: set[str]) -> None:
    with _index_lock:
        entries = [b for b in load_index() if b.id not in backup_ids]
        save_index(entries)


def list_backups(repo_path: "str | Path | None" = None) -> list[Backup]:
    """Respaldos existentes, del más reciente al más antiguo."""
    entries = load_index()
    if repo_path is not None:
        target = str(Path(repo_path).resolve())
        entries = [b for b in entries if str(Path(b.repo_path).resolve()) == target]
    return sorted(entries, key=lambda b: b.created_at, reverse=True)


def last_backup(repo_path: "str | Path") -> Backup | None:
    entries = list_backups(repo_path)
    return entries[0] if entries else None


def get_backup(backup_id: str) -> Backup | None:
    return next((b for b in load_index() if b.id == backup_id), None)


# --------------------------------------------------------------------------
# Crear un respaldo
# --------------------------------------------------------------------------

def _new_id(team: str, when: datetime | None = None) -> str:
    when = when or datetime.now()
    limpio = "".join(c if c.isalnum() or c in "-_" else "-" for c in team) or "EQUIPO"
    return f"{when.strftime('%Y-%m-%d_%H%M%S')}_{limpio}"


def _untracked_files(repo: Path) -> list[str]:
    """Archivos nuevos que Git todavía no sigue (respetando .gitignore)."""
    result = git_ops.run(
        ["ls-files", "--others", "--exclude-standard", "-z"], cwd=repo
    )
    if not result.ok:
        return []
    return [name for name in result.stdout.split("\0") if name]


def _commit_untracked(repo: Path, paths: list[str], parent: str | None) -> str | None:
    """Guarda los archivos nuevos en un commit propio, vía índice temporal.

    ``git stash create --include-untracked`` acepta la opción pero **no**
    guarda los archivos no seguidos, así que se construye el árbol a mano.
    El índice real del usuario no se toca: se usa ``GIT_INDEX_FILE``.
    """
    if not paths:
        return None
    handle, index_name = tempfile.mkstemp(prefix="vaiven-index-")
    os.close(handle)
    os.unlink(index_name)  # git lo crea; debe no existir para partir vacío
    entorno = {"GIT_INDEX_FILE": index_name}
    try:
        added = git_ops.run(
            ["update-index", "--add", "-z", "--stdin"],
            cwd=repo,
            env=entorno,
            input_bytes=("\0".join(paths) + "\0").encode("utf-8"),
        )
        if not added.ok:
            log.warning("no se pudieron guardar los archivos nuevos: %s", added.stderr.strip())
            return None
        tree = git_ops.run(["write-tree"], cwd=repo, env=entorno)
        if not tree.ok or not tree.out:
            return None
        args = ["commit-tree", tree.out, "-m", "Vaiven: archivos nuevos sin seguir"]
        if parent:
            args[2:2] = ["-p", parent]
        commit = git_ops.run(args, cwd=repo, env=entorno)
        return commit.out if commit.ok and commit.out else None
    finally:
        Path(index_name).unlink(missing_ok=True)


def create_backup(
    repo_path: "str | Path",
    reason: str,
    team: str | None = None,
    *,
    when: datetime | None = None,
) -> Backup:
    """Crea un respaldo completo antes de tocar el repositorio (6.4).

    No modifica el árbol de trabajo ni el índice: solo escribe objetos y
    referencias nuevas dentro del repositorio local.
    """
    repo = Path(repo_path)
    team = team or config.Config.load().team_name
    when = when or datetime.now()
    backup_id = _new_id(team, when)

    head = git_ops.run(["rev-parse", "HEAD"], cwd=repo)
    head_sha = head.out if head.ok and head.out else None

    backup = Backup(
        id=backup_id,
        repo_name=repo.name,
        repo_path=str(repo.resolve()),
        created_at=when.astimezone().isoformat(timespec="seconds"),
        reason=reason,
        team=team,
        branch=git_ops.current_branch(repo),
        head_sha=head_sha,
    )

    if head_sha:
        ref = f"{BACKUP_REF_PREFIX}{backup_id}"
        written = git_ops.run(["update-ref", ref, head_sha, "-m", reason], cwd=repo)
        if not written.ok:
            raise BackupError(
                f"No se pudo crear el respaldo de «{repo.name}»: {written.stderr.strip()}"
            )
        backup.head_ref = ref

    # 1) Cambios sin guardar de archivos ya seguidos.
    if head_sha:
        stash = git_ops.run(["stash", "create", "Vaiven: respaldo automático"], cwd=repo)
        if stash.ok and stash.out:
            backup.stash_sha = stash.out
            backup.stash_ref = f"{BACKUP_REF_PREFIX}{backup_id}-stash"
            git_ops.run(["update-ref", backup.stash_ref, stash.out, "-m", reason], cwd=repo)

    # 2) Archivos nuevos, que el stash no cubre.
    nuevos = _untracked_files(repo)
    if nuevos:
        sha = _commit_untracked(repo, nuevos, head_sha)
        if sha:
            backup.untracked_sha = sha
            backup.untracked_ref = f"{BACKUP_REF_PREFIX}{backup_id}-untracked"
            git_ops.run(["update-ref", backup.untracked_ref, sha, "-m", reason], cwd=repo)

    cambiados = git_ops.run(["status", "--porcelain=v2", "-z", "--untracked-files=all"], cwd=repo)
    if cambiados.ok:
        backup.files_saved = [
            entry.split(" ")[-1] if entry[0] != "?" else entry[2:]
            for entry in cambiados.stdout.split("\0")
            if entry and entry[0] in "12?u"
        ]

    _add_to_index(backup)
    log.info(
        "respaldo %s de %s (%s): head=%s stash=%s nuevos=%s",
        backup.id, repo.name, reason, (head_sha or "-")[:7],
        (backup.stash_sha or "-")[:7], len(nuevos),
    )
    return backup


# --------------------------------------------------------------------------
# Restaurar
# --------------------------------------------------------------------------

def _write_tree_to_worktree(repo: Path, tree_sha: str, only: set[str] | None = None) -> list[str]:
    """Escribe en disco los archivos de un árbol, sin pasar por el índice."""
    listing = git_ops.run(["ls-tree", "-r", "-z", tree_sha], cwd=repo)
    if not listing.ok:
        return []
    escritos: list[str] = []
    for entry in listing.stdout.split("\0"):
        if not entry or "\t" not in entry:
            continue
        meta, path = entry.split("\t", 1)
        parts = meta.split()
        if len(parts) < 3 or parts[1] != "blob":
            continue
        mode, blob_sha = parts[0], parts[2]
        if only is not None and path not in only:
            continue
        destino = repo / path
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(git_ops.read_blob(repo, blob_sha))
        if mode.endswith("755"):
            destino.chmod(destino.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        escritos.append(path)
    return escritos


def restore_backup(backup: "Backup | str", *, make_safety_copy: bool = True) -> Backup:
    """Devuelve un repositorio al estado guardado en ``backup``.

    Antes de tocar nada crea otro respaldo del estado actual, de modo que
    **deshacer también sea reversible** (sección 6.4). Devuelve ese respaldo
    de seguridad.
    """
    if isinstance(backup, str):
        encontrado = get_backup(backup)
        if encontrado is None:
            raise BackupError(f"No existe el respaldo «{backup}»")
        backup = encontrado

    repo = Path(backup.repo_path)
    if not git_ops.is_repo(repo):
        raise BackupError(f"La carpeta del respaldo ya no es un repositorio: {repo}")

    previo = (
        create_backup(repo, f"antes de restaurar el respaldo del {backup.created.astimezone():%d/%m/%Y %H:%M}", backup.team)
        if make_safety_copy
        else backup
    )

    permiso = {ALLOW_BACKUP_RESTORE}

    # 1) Volver al commit guardado: recupera HEAD, el índice y los archivos seguidos.
    if backup.head_sha:
        vuelta = git_ops.run(["reset", "--hard", backup.head_sha], cwd=repo, allow=permiso)
        if not vuelta.ok:
            raise BackupError(
                f"No se pudo restaurar «{backup.repo_name}»: {vuelta.stderr.strip()}"
            )

    # 2) Recuperar los cambios sin guardar de archivos seguidos.
    if backup.stash_sha:
        aplicado = git_ops.run(["stash", "apply", backup.stash_sha], cwd=repo)
        if not aplicado.ok:
            # El stash se creó contra este mismo HEAD, así que aplicar los blobs
            # directamente es equivalente y no puede fallar por conflicto.
            _write_tree_to_worktree(repo, backup.stash_sha)
            log.info("respaldo %s: cambios recuperados archivo a archivo", backup.id)

    # 3) Recuperar los archivos nuevos, que el stash no guarda.
    if backup.untracked_sha:
        anteriores = set(_untracked_tree_paths(repo, backup))
        _write_tree_to_worktree(repo, backup.untracked_sha, only=anteriores or None)

    log.info("respaldo %s restaurado en %s", backup.id, repo.name)
    return previo


def _untracked_tree_paths(repo: Path, backup: Backup) -> list[str]:
    """Rutas guardadas en el árbol de archivos nuevos de un respaldo."""
    if not backup.untracked_sha:
        return []
    listing = git_ops.run(["ls-tree", "-r", "--name-only", "-z", backup.untracked_sha], cwd=repo)
    if not listing.ok:
        return []
    return [name for name in listing.stdout.split("\0") if name]


def undo_last(repo_path: "str | Path") -> Backup | None:
    """Deshace la última operación de Vaivén en un repositorio (6.4).

    Busca el respaldo más reciente que no sea una copia de seguridad creada
    por otra restauración, y vuelve a él.
    """
    candidatos = [b for b in list_backups(repo_path) if not b.reason.startswith("antes de restaurar")]
    if not candidatos:
        log.info("no hay ningún respaldo que deshacer en %s", repo_path)
        return None
    return restore_backup(candidatos[0])


# --------------------------------------------------------------------------
# Limpieza
# --------------------------------------------------------------------------

def expired_backups(
    backups: list[Backup] | None = None,
    *,
    now: datetime | None = None,
    days: int = RETENTION_DAYS,
    keep: int = RETENTION_COUNT,
) -> list[Backup]:
    """Respaldos que ya se pueden borrar.

    Se conservan «30 días o los últimos 20 por repo, lo que sea mayor»: un
    respaldo solo caduca si es más antiguo que ``days`` **y** además no está
    entre los ``keep`` más recientes de su repositorio.
    """
    entries = backups if backups is not None else load_index()
    now = now or datetime.now(timezone.utc)
    limite = now - timedelta(days=days)

    por_repo: dict[str, list[Backup]] = {}
    for backup in entries:
        por_repo.setdefault(backup.repo_path, []).append(backup)

    caducados: list[Backup] = []
    for grupo in por_repo.values():
        grupo.sort(key=lambda b: b.created_at, reverse=True)
        for backup in grupo[keep:]:
            created = backup.created
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if created < limite:
                caducados.append(backup)
    return caducados


def cleanup(
    *, now: datetime | None = None, days: int = RETENTION_DAYS, keep: int = RETENTION_COUNT
) -> list[Backup]:
    """Borra los respaldos caducados y sus referencias. Devuelve los borrados."""
    caducados = expired_backups(now=now, days=days, keep=keep)
    if not caducados:
        return []
    permiso = {ALLOW_BACKUP_REF_DELETE}
    for backup in caducados:
        repo = Path(backup.repo_path)
        if not repo.exists():
            continue
        for ref in (backup.head_ref, backup.stash_ref, backup.untracked_ref):
            if ref:
                git_ops.run(["update-ref", "-d", ref], cwd=repo, allow=permiso)
    _remove_from_index({b.id for b in caducados})
    log.info("limpieza: %s respaldos caducados eliminados", len(caducados))
    return caducados


def backup_refs(repo_path: "str | Path") -> list[str]:
    """Referencias de respaldo que existen realmente en el repositorio."""
    result = git_ops.run(
        ["for-each-ref", "--format=%(refname)", BACKUP_REF_PREFIX], cwd=repo_path
    )
    return result.lines() if result.ok else []
