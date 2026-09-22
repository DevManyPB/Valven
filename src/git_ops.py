"""Envoltorio seguro sobre ``git.exe``.

Este módulo es la **única** puerta por la que Vaivén ejecuta Git. Su trabajo
principal no es ejecutar comandos, sino **rechazar** los que pueden destruir
trabajo del usuario (sección 6.1 de SPEC.md).

Reglas adicionales que cumple siempre:

* ``creationflags=CREATE_NO_WINDOW`` en Windows, para que el ``.exe`` no abra
  ventanas negras de consola (sección 2).
* La autenticación viaja en variables de entorno ``GIT_CONFIG_*`` (el
  equivalente de ``-c http...extraheader``) y nunca se escribe en
  ``.git/config`` ni en disco. Tampoco en la línea de comandos, que cualquier
  proceso del equipo puede leer (sección 5.3 y decisión D38).
* Tiempo máximo de 120 s por operación de red (sección 11).
* Todo se registra en el log **sin** la cabecera de autenticación (sección 11).
"""

from __future__ import annotations

import base64
import os
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .logger import get_logger

log = get_logger("git_ops")

#: Tiempo máximo por operación (sección 11 de SPEC.md).
DEFAULT_TIMEOUT = 120

#: Espacio de nombres de las referencias de respaldo (sección 6.4).
BACKUP_REF_PREFIX = "refs/vaiven-backup/"
BACKUP_BRANCH_PREFIX = "vaiven-backup/"

#: Marcador con el que se sustituye la cabecera de autenticación al registrar.
_AUTH_PLACEHOLDER = "http.https://github.com/.extraheader=<oculto>"

# --- permisos explícitos --------------------------------------------------
# Algunas operaciones son peligrosas en general pero imprescindibles en un
# flujo concreto y ya respaldado. Solo se permiten si quien llama las pide
# por su nombre, nunca por defecto y nunca desde una acción masiva.

ALLOW_REBASE_PULL = "rebase_pull"              # 6.6(a): combinar un repo divergido
ALLOW_BACKUP_REF_DELETE = "backup_ref_delete"  # 6.4: limpiar respaldos caducados
ALLOW_BACKUP_RESTORE = "backup_restore"        # 6.4: deshacer desde un respaldo

KNOWN_PERMISSIONS = frozenset({
    ALLOW_REBASE_PULL, ALLOW_BACKUP_REF_DELETE, ALLOW_BACKUP_RESTORE,
})


class GitError(Exception):
    """Error genérico de Git."""


class GitNotInstalled(GitError):
    """No se encontró ``git`` en el sistema (sección 9.2)."""


class GitTimeout(GitError):
    """La operación superó el tiempo máximo."""


class GitCommandError(GitError):
    """Git devolvió un código de salida distinto de cero."""

    def __init__(self, result: "GitResult") -> None:
        self.result = result
        super().__init__(
            f"git {' '.join(result.safe_args)} falló ({result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )


class ForbiddenGitCommand(GitError):
    """El comando está prohibido porque puede destruir trabajo (sección 6.1)."""

    def __init__(self, reason: str, args: "list[str] | tuple[str, ...]") -> None:
        self.reason = reason
        self.git_args = list(args)
        super().__init__(f"Comando de Git prohibido por seguridad: {reason} (git {' '.join(self.git_args)})")


@dataclass
class GitResult:
    """Resultado de una ejecución de Git."""

    args: list[str]
    returncode: int
    stdout: str
    stderr: str
    cwd: str | None = None
    safe_args: list[str] = field(default_factory=list)
    stdout_bytes: bytes = b""

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def out(self) -> str:
        return self.stdout.strip()

    def lines(self) -> list[str]:
        return [line for line in self.stdout.splitlines() if line]


# --------------------------------------------------------------------------
# Análisis de los argumentos
# --------------------------------------------------------------------------

#: Opciones globales de git que consumen el argumento siguiente.
_GLOBAL_OPTS_WITH_VALUE = {
    "-c", "-C", "--git-dir", "--work-tree", "--namespace",
    "--exec-path", "--config-env", "--super-prefix",
}


def split_command(args: list[str] | tuple[str, ...]) -> tuple[str | None, list[str]]:
    """Separa el subcomando de git de sus argumentos, saltando opciones globales."""
    items = list(args)
    i = 0
    while i < len(items):
        item = items[i]
        if item in _GLOBAL_OPTS_WITH_VALUE:
            i += 2
            continue
        if item.startswith("-"):
            i += 1
            continue
        return item, items[i + 1:]
    return None, []


def _options(rest: list[str]) -> list[str]:
    """Opciones del subcomando, ignorando lo que va tras ``--``."""
    out: list[str] = []
    for item in rest:
        if item == "--":
            break
        if item.startswith("-"):
            out.append(item)
    return out


def _operands(rest: list[str]) -> list[str]:
    """Argumentos que no son opciones (ramas, refs, rutas...)."""
    return [item for item in rest if not item.startswith("-") and item != "--"]


def _has_flag(rest: list[str], *names: str) -> bool:
    """¿Aparece alguna de estas opciones largas (con o sin ``=valor``)?"""
    for item in _options(rest):
        if item in names:
            return True
        if "=" in item and item.split("=", 1)[0] in names:
            return True
    return False


def _has_short(rest: list[str], letter: str) -> bool:
    """¿Aparece una opción corta, suelta o agrupada (``-f``, ``-fd``, ``-ffdx``)?"""
    for item in _options(rest):
        if item.startswith("--") or item == "-":
            continue
        if letter in item[1:]:
            return True
    return False


def _is_backup_ref(ref: str) -> bool:
    return ref.startswith(BACKUP_REF_PREFIX) or ref.startswith(BACKUP_BRANCH_PREFIX)


#: Subcomandos que Vaivén puede ejecutar. Todo lo demás se rechaza aunque no
#: figure abajo como destructivo: una lista blanca no tiene huecos que olvidar.
ALLOWED_COMMANDS = frozenset({
    # lectura
    "status", "log", "rev-list", "rev-parse", "symbolic-ref", "cat-file",
    "check-ignore", "config", "diff", "ls-files", "ls-tree", "for-each-ref",
    "remote", "show", "merge-base",
    # red
    "fetch", "pull", "push", "clone",
    # guardar trabajo
    "add", "commit", "stash",
    # respaldos (fontanería que solo escribe objetos y referencias nuevas)
    "update-ref", "update-index", "write-tree", "commit-tree",
    # ramas y combinaciones, con las restricciones de abajo
    "checkout", "switch", "branch", "reset", "rebase",
})

#: Subcomandos de ``git remote`` que solo leen.
_REMOTE_READ_ONLY = frozenset({"get-url", "show"})


def validate_args(
    args: list[str] | tuple[str, ...],
    allow: "frozenset[str] | set[str] | tuple[str, ...]" = (),
) -> None:
    """Rechaza los comandos prohibidos de la sección 6.1.

    Lanza :class:`ForbiddenGitCommand` si el comando puede destruir trabajo.
    ``allow`` habilita excepciones concretas y nombradas (ver *permisos
    explícitos* arriba); una acción masiva nunca debe pasar nada aquí.
    """
    allow = frozenset(allow)
    unknown = allow - KNOWN_PERMISSIONS
    if unknown:
        raise ValueError(f"Permiso desconocido: {sorted(unknown)}")

    command, rest = split_command(args)
    if command is None:
        return

    def deny(reason: str) -> None:
        raise ForbiddenGitCommand(reason, args)

    # --- push: jamás forzado, jamás borrando ramas remotas ---------------
    if command == "push":
        if _has_flag(rest, "--force", "--force-with-lease", "--force-if-includes") or _has_short(rest, "f"):
            deny("'git push' con --force / -f / --force-with-lease reescribe el historial de GitHub")
        if _has_flag(rest, "--delete", "--prune", "--mirror") or _has_short(rest, "d"):
            deny("'git push' no puede borrar ni reflejar ramas remotas")
        for operand in _operands(rest):
            if operand.startswith(":") or operand.startswith("+"):
                deny(f"refspec destructivo en 'git push': {operand!r}")

    # --- pull: solo avance rápido ----------------------------------------
    elif command == "pull":
        if _has_flag(rest, "--force") or _has_short(rest, "f"):
            deny("'git pull --force' puede sobrescribir referencias locales")
        if _has_flag(rest, "--ff-only"):
            if _has_flag(rest, "--rebase", "--no-ff", "--merge"):
                deny("'git pull --ff-only' combinado con --rebase/--no-ff/--merge deja de ser un avance rápido")
        elif _has_flag(rest, "--rebase") and ALLOW_REBASE_PULL in allow:
            pass  # 6.6(a), solo tras respaldo y solo para un repo concreto
        else:
            deny("'git pull' debe llevar --ff-only para no poder sobrescribir trabajo")

    # --- reset: nunca contra el árbol de trabajo -------------------------
    elif command == "reset":
        if _has_flag(rest, "--hard", "--merge") and ALLOW_BACKUP_RESTORE not in allow:
            deny("'git reset --hard/--merge' borra los cambios sin guardar")

    # --- clean: nunca borra archivos del usuario --------------------------
    elif command == "clean":
        if _has_flag(rest, "--force") or _has_short(rest, "f"):
            deny("'git clean -f' borra archivos que no están en Git")

    # --- checkout / switch / restore: nunca descartan cambios -------------
    elif command == "checkout":
        if _has_flag(rest, "--force") or _has_short(rest, "f"):
            deny("'git checkout --force' descarta los cambios sin guardar")
        if "--" in rest or _has_flag(rest, "--pathspec-from-file"):
            deny("'git checkout -- <ruta>' descarta los cambios sin guardar")
        if _has_short(rest, "B") or _has_flag(rest, "--patch", "--ours", "--theirs", "--merge") or _has_short(rest, "p"):
            deny("'git checkout -B/--patch/--ours/--theirs' puede descartar trabajo")
        # «checkout <rama>» o «checkout -b <nueva> [<origen>]»; un operando
        # más serían rutas, y «checkout HEAD archivo» sobrescribe el archivo.
        maximo = 2 if _has_short(rest, "b") else 1
        if len(_operands(rest)) > maximo:
            deny("'git checkout <commit> <ruta>' sobrescribe archivos con cambios sin guardar")
    elif command == "switch":
        if _has_flag(rest, "--force", "--discard-changes") or _has_short(rest, "f"):
            deny("'git switch --force' descarta los cambios sin guardar")
        if _has_flag(rest, "--force-create") or _has_short(rest, "C"):
            deny("'git switch -C' mueve una rama existente y puede dejar commits huérfanos")
    elif command == "restore":
        deny("'git restore' descarta los cambios sin guardar")

    # --- branch: solo se borran ramas de respaldo -------------------------
    elif command == "branch":
        forced = _has_flag(rest, "--force") or _has_short(rest, "f")
        deleting = _has_flag(rest, "--delete") or _has_short(rest, "d") or _has_short(rest, "D")
        if _has_short(rest, "D") or (deleting and forced):
            targets = _operands(rest)
            if ALLOW_BACKUP_REF_DELETE not in allow or not targets or not all(map(_is_backup_ref, targets)):
                deny("'git branch -D' solo se permite sobre ramas de respaldo de Vaivén caducadas")
        elif forced or _has_short(rest, "M") or _has_short(rest, "C"):
            deny("'git branch -f/-M/-C' mueve o pisa una rama existente")

    # --- update-ref: solo toca el espacio de respaldos --------------------
    elif command == "update-ref":
        if _has_flag(rest, "--stdin"):
            deny("'git update-ref --stdin' no se puede validar")
        if _has_flag(rest, "--delete") or _has_short(rest, "d"):
            targets = _operands(rest)
            if ALLOW_BACKUP_REF_DELETE not in allow or not targets or not all(map(_is_backup_ref, targets)):
                deny("'git update-ref -d' solo se permite sobre referencias de respaldo de Vaivén")
        else:
            # El primer operando es la referencia que se escribe; lo que sigue
            # es el valor nuevo y, tras -m, el motivo.
            targets = _operands(rest)
            target = targets[0] if targets else ""
            restoring_head = target == "HEAD" and ALLOW_BACKUP_RESTORE in allow
            if not (_is_backup_ref(target) or restoring_head):
                deny("'git update-ref' solo puede escribir referencias de respaldo de Vaivén")

    # --- symbolic-ref: leer sí; cambiar de rama solo al restaurar ----------
    elif command == "symbolic-ref":
        if _has_flag(rest, "--delete") or _has_short(rest, "d"):
            deny("'git symbolic-ref -d' deja el repositorio sin HEAD")
        if len(_operands(rest)) > 1 and ALLOW_BACKUP_RESTORE not in allow:
            deny("'git symbolic-ref' solo cambia de rama al restaurar un respaldo")

    elif command == "remote":
        sub = next((item for item in rest if not item.startswith("-")), None)
        if sub is not None and sub not in _REMOTE_READ_ONLY:
            deny(f"'git remote {sub}' modifica los remotos del usuario")

    # --- el escondite guarda respaldos: no se vacía -----------------------
    elif command == "stash":
        sub = next((item for item in rest if not item.startswith("-")), None)
        if sub in {"drop", "clear"}:
            deny(f"'git stash {sub}' elimina cambios guardados que Vaivén usa como respaldo")

    # --- rebase: solo abortar o continuar uno ya empezado -----------------
    elif command == "rebase":
        if not _has_flag(rest, "--abort", "--continue", "--skip", "--quit"):
            deny("'git rebase' solo se permite como --abort/--continue (usa 'pull --rebase' tras respaldo)")

    # --- reescrituras de historial ----------------------------------------
    elif command in {"filter-branch", "filter-repo", "replace"}:
        deny(f"'git {command}' reescribe el historial")
    elif command == "reflog":
        if any(item == "expire" or item == "delete" for item in _operands(rest)):
            deny("'git reflog expire/delete' borra la red de seguridad de Git")
    elif command == "gc":
        if _has_flag(rest, "--prune") and not any(
            item == "--prune=never" for item in _options(rest)
        ):
            deny("'git gc --prune' puede eliminar objetos de los que dependen los respaldos")

    if command not in ALLOWED_COMMANDS:
        deny(f"'git {command}' no está entre los comandos que Vaivén puede usar")


# --------------------------------------------------------------------------
# Ejecución
# --------------------------------------------------------------------------

def _creation_flags() -> int:
    """``CREATE_NO_WINDOW`` en Windows, 0 en el resto (sección 2)."""
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


AUTH_CONFIG_KEY = "http.https://github.com/.extraheader"


def auth_header(token: str) -> str:
    """Valor de la cabecera de la sección 5.3 para ``token``."""
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return f"AUTHORIZATION: basic {basic}"


def auth_env(token: str | None, base: "dict[str, str] | None" = None) -> dict[str, str]:
    """Variables ``GIT_CONFIG_*`` que autentican la operación (5.3, D38).

    Equivalen a ``-c http...extraheader=...`` pero no aparecen en la línea de
    comandos, que en Windows y Linux puede leer cualquier proceso del equipo.
    Se añaden detrás de las que ya hubiera en ``base``, sin pisarlas.
    """
    if not token:
        return {}
    base = base if base is not None else os.environ
    try:
        index = int(base.get("GIT_CONFIG_COUNT", "0"))
    except ValueError:
        index = 0
    return {
        "GIT_CONFIG_COUNT": str(index + 1),
        f"GIT_CONFIG_KEY_{index}": AUTH_CONFIG_KEY,
        f"GIT_CONFIG_VALUE_{index}": auth_header(token),
    }


def _safe_args(args: list[str]) -> list[str]:
    """Copia de los argumentos con la cabecera de autenticación sustituida."""
    out: list[str] = []
    for item in args:
        if "extraheader=" in item.lower():
            out.append(_AUTH_PLACEHOLDER)
        else:
            out.append(item)
    return out


def _environment(extra: "dict[str, str] | None" = None) -> dict[str, str]:
    """Entorno sin interacción: Git nunca debe quedarse esperando al usuario."""
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ASKPASS"] = ""
    env["GCM_INTERACTIVE"] = "never"
    env["LC_ALL"] = "C"
    env.pop("GIT_DIR", None)
    env.pop("GIT_WORK_TREE", None)
    env.pop("GIT_INDEX_FILE", None)
    if extra:
        env.update(extra)
    return env


def run(
    args: list[str] | tuple[str, ...],
    cwd: "str | Path | None" = None,
    *,
    token: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    check: bool = False,
    allow: "frozenset[str] | set[str] | tuple[str, ...]" = (),
    input_bytes: bytes | None = None,
    binary: bool = False,
    env: "dict[str, str] | None" = None,
) -> GitResult:
    """Ejecuta ``git`` tras validar que el comando no es destructivo.

    :param args: argumentos de git, sin el propio ``git``.
    :param cwd: carpeta del repositorio.
    :param token: si se indica, se añade la cabecera de autenticación
        (por el entorno, nunca por la línea de comandos).
    :param check: lanza :class:`GitCommandError` si el código de salida no es 0.
    :param allow: permisos explícitos (ver *permisos explícitos*).
    :param input_bytes: datos que se envían por la entrada estándar.
    :param binary: si es cierto, la salida se devuelve en ``stdout_bytes``.
    :param env: variables de entorno adicionales (p. ej. ``GIT_INDEX_FILE``).
    """
    args = list(args)
    validate_args(args, allow)

    full = args
    safe = _safe_args(full)
    entorno = _environment(env)
    entorno.update(auth_env(token, entorno))
    workdir = str(cwd) if cwd is not None else None

    log.debug("ejecutando: git %s (en %s)", " ".join(shlex.quote(a) for a in safe), workdir or ".")
    try:
        # Si hay que enviar datos binarios por la entrada, el proceso se
        # comunica en bytes y la salida se descodifica después.
        text_mode = not binary and input_bytes is None
        completed = subprocess.run(
            ["git", *full],
            cwd=workdir,
            capture_output=True,
            text=text_mode,
            encoding="utf-8" if text_mode else None,
            errors="replace" if text_mode else None,
            timeout=timeout,
            env=entorno,
            creationflags=_creation_flags(),
            input=input_bytes if input_bytes is not None else None,
            stdin=None if input_bytes is not None else subprocess.DEVNULL,
        )
    except FileNotFoundError as exc:
        raise GitNotInstalled(
            "No se ha encontrado Git en este equipo. Instálalo desde https://git-scm.com"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        log.error("tiempo agotado (%ss): git %s", timeout, " ".join(safe))
        raise GitTimeout(
            f"La operación de Git tardó más de {timeout} segundos y se ha cancelado."
        ) from exc

    def _as_text(value) -> str:
        if value is None:
            return ""
        return value if isinstance(value, str) else value.decode("utf-8", "replace")

    result = GitResult(
        args=args,
        returncode=completed.returncode,
        stdout="" if binary else _as_text(completed.stdout),
        stderr=_as_text(completed.stderr),
        cwd=workdir,
        safe_args=safe,
        stdout_bytes=(completed.stdout or b"") if binary else b"",
    )
    if not result.ok:
        log.warning(
            "git %s devolvió %s: %s",
            " ".join(safe), result.returncode, result.stderr.strip()[:500],
        )
        if check:
            raise GitCommandError(result)
    return result


# --------------------------------------------------------------------------
# Utilidades de consulta (solo lectura)
# --------------------------------------------------------------------------

def is_git_installed() -> bool:
    try:
        return run(["--version"]).ok
    except GitNotInstalled:
        return False


def git_version() -> str:
    """Versión de Git instalada; lanza :class:`GitNotInstalled` si no hay."""
    result = run(["--version"], check=True)
    return result.out


def is_repo(path: "str | Path") -> bool:
    """¿``path`` es la raíz de un repositorio Git?"""
    path = Path(path)
    if not (path / ".git").exists():
        return False
    result = run(["rev-parse", "--is-inside-work-tree"], cwd=path)
    return result.ok and result.out == "true"


def remote_url(path: "str | Path", remote: str = "origin") -> str | None:
    result = run(["remote", "get-url", remote], cwd=path)
    return result.out or None if result.ok else None


def is_github_remote(url: str | None) -> bool:
    if not url:
        return False
    lowered = url.lower()
    return "github.com" in lowered or lowered.startswith("git@github.com")


def current_branch(path: "str | Path") -> str | None:
    """Rama actual, o ``None`` si el HEAD está suelto (*detached*)."""
    result = run(["symbolic-ref", "--quiet", "--short", "HEAD"], cwd=path)
    return result.out if result.ok and result.out else None


def upstream_of(path: "str | Path") -> str | None:
    result = run(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], cwd=path)
    return result.out if result.ok and result.out else None


def config_get(path: "str | Path | None", key: str, global_scope: bool = False) -> str | None:
    args = ["config", "--global", "--get", key] if global_scope else ["config", "--get", key]
    result = run(args, cwd=path)
    return result.out if result.ok and result.out else None


def read_blob(repo: "str | Path", sha: str) -> bytes:
    """Contenido binario exacto de un objeto de Git."""
    result = run(["cat-file", "blob", sha], cwd=repo, binary=True, check=True)
    return result.stdout_bytes
