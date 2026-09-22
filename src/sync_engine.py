"""Motor de «Subir todo» y «Sincronizar todo» (secciones 7, 8 y 6.6).

El motor trabaja en dos tiempos, y esa separación es deliberada:

1. **Planificar** (`plan_push` / `plan_sync`) decide qué se hará, qué se
   omitirá y por qué, sin tocar nada. Es lo que alimenta la vista previa.
2. **Ejecutar** (`execute`) hace solo lo que el usuario confirmó en esa vista
   previa, repositorio por repositorio, respaldando antes de cada uno.

Ninguna acción masiva toca jamás un repositorio bloqueado; para esos están
las acciones individuales de la sección 6.6, al final del módulo.
"""

from __future__ import annotations

import fnmatch
import functools
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import analyzer, git_ops, safety
from .analyzer import RepoState, RepoStatus
from .git_ops import ALLOW_REBASE_PULL, GitError
from .logger import get_logger

log = get_logger("sync_engine")

#: Tamaño a partir del cual se avisa (GitHub rechaza a partir de 100 MB).
BIG_FILE_BYTES = 50 * 1024 * 1024

#: Archivos que suelen contener secretos (sección 6.5).
SECRET_PATTERNS = (
    ".env", ".env.*", "*.pem", "*.key", "id_rsa*", "id_dsa*", "id_ecdsa*",
    "id_ed25519*", "credentials*.json", "*.pfx", "*.p12", "service-account*.json",
)

PUSH = "push"
SYNC = "sync"


# --------------------------------------------------------------------------
# Avisos de la vista previa
# --------------------------------------------------------------------------

@dataclass
class Warning:
    """Algo que el usuario debería mirar antes de confirmar (6.5)."""

    kind: str          # "archivo_grande" | "secreto"
    path: str
    detail: str

    @property
    def is_secret(self) -> bool:
        return self.kind == "secreto"


def looks_like_secret(path: str) -> bool:
    nombre = Path(path).name
    return any(fnmatch.fnmatch(nombre, patron) for patron in SECRET_PATTERNS)


def _is_ignored(repo: Path, path: str) -> bool:
    return git_ops.run(["check-ignore", "-q", "--", path], cwd=repo).returncode == 0


def find_warnings(status: RepoStatus) -> list[Warning]:
    """Archivos grandes o que parecen secretos entre los que se van a subir."""
    avisos: list[Warning] = []
    for cambio in status.files:
        if cambio.change == "deleted":
            continue
        ruta = status.path / cambio.path
        try:
            tamano = ruta.stat().st_size
        except OSError:
            continue
        if tamano >= BIG_FILE_BYTES:
            avisos.append(Warning(
                kind="archivo_grande",
                path=cambio.path,
                detail=f"Ocupa {tamano / (1024 * 1024):.0f} MB; GitHub rechaza los de más de 100 MB",
            ))
        if looks_like_secret(cambio.path) and not _is_ignored(status.path, cambio.path):
            avisos.append(Warning(
                kind="secreto",
                path=cambio.path,
                detail="Este archivo suele contener contraseñas o claves privadas",
            ))
    return avisos


# --------------------------------------------------------------------------
# El plan
# --------------------------------------------------------------------------

@dataclass
class PlannedAction:
    """Lo que se hará (o no) con un repositorio concreto."""

    status: RepoStatus
    action: str                     # "push" | "pull" | "clone" | "skip"
    reason: str = ""                # por qué se omite, en lenguaje sencillo
    commit_message: str | None = None
    selected: bool = True           # casilla de la vista previa (6.5.4)
    warnings: list[Warning] = field(default_factory=list)
    offers: list[str] = field(default_factory=list)  # opciones de la sección 6.6

    @property
    def name(self) -> str:
        return self.status.name

    @property
    def has_secrets(self) -> bool:
        return any(w.is_secret for w in self.warnings)


@dataclass
class MissingRepo:
    """Un repositorio que está en GitHub pero no en este equipo (sección 8)."""

    name: str
    clone_url: str
    private: bool = False
    selected: bool = False          # desmarcado por defecto, como pide el SPEC


@dataclass
class Plan:
    """El resultado del análisis, listo para la vista previa (6.5)."""

    kind: str                                   # PUSH | SYNC
    to_do: list[PlannedAction] = field(default_factory=list)
    skipped: list[PlannedAction] = field(default_factory=list)
    unchanged: list[PlannedAction] = field(default_factory=list)
    missing: list[MissingRepo] = field(default_factory=list)

    @property
    def selected_actions(self) -> list[PlannedAction]:
        return [a for a in self.to_do if a.selected]

    @property
    def warnings(self) -> list[Warning]:
        return [w for a in self.selected_actions for w in a.warnings]

    @property
    def has_secret_warnings(self) -> bool:
        return any(w.is_secret for w in self.warnings)

    @property
    def is_empty(self) -> bool:
        return not self.selected_actions and not any(m.selected for m in self.missing)

    def question(self) -> str:
        """La pregunta final de confirmación (6.5.5)."""
        cuantos = len(self.selected_actions)
        verbo = "subir" if self.kind == PUSH else "sincronizar"
        if cuantos == 1:
            return f"¿Seguro que quieres {verbo} 1 proyecto?"
        return f"¿Seguro que quieres {verbo} {cuantos} proyectos?"


def default_commit_message(team: str, when: datetime | None = None) -> str:
    """El mensaje por defecto de la sección 7.4, editable en la vista previa."""
    when = when or datetime.now()
    return f"Sync desde {team} — {when:%Y-%m-%d %H:%M}"


def _blocked_reason(status: RepoStatus, kind: str) -> tuple[str, list[str]]:
    """Motivo en lenguaje sencillo y opciones individuales (6.5.2 y 6.6)."""
    if status.state is RepoState.DIVERGED:
        return (
            f"Este equipo y GitHub han seguido caminos distintos "
            f"({status.ahead} cambio{'s' if status.ahead != 1 else ''} aquí y "
            f"{status.behind} en GitHub). Hay que combinarlos antes de nada.",
            ["combinar", "abrir_vscode", "ver_diferencias"],
        )
    if status.state is RepoState.BEHIND_WITH_LOCAL_CHANGES:
        return (
            f"GitHub tiene {status.behind} cambio{'s' if status.behind != 1 else ''} nuevo"
            f"{'s' if status.behind != 1 else ''} y aquí hay "
            f"{len(status.files)} archivo{'s' if len(status.files) != 1 else ''} sin subir.",
            ["subir_primero", "guardar_aparte", "ver_diferencias"],
        )
    if status.state is RepoState.CONFLICT:
        return (
            "Hay una combinación a medias sin resolver. Termínala antes de continuar.",
            ["abrir_vscode", "abrir_carpeta"],
        )
    if status.state is RepoState.DETACHED_HEAD:
        return (
            "No estás en ninguna rama, así que no se sabe dónde subir o bajar los cambios.",
            ["abrir_vscode", "abrir_carpeta"],
        )
    if status.state is RepoState.NO_UPSTREAM:
        return (
            f"La rama «{status.branch}» todavía no existe en GitHub.",
            ["subir_rama_nueva", "abrir_carpeta"],
        )
    if status.state is RepoState.ERROR:
        return (status.error or "No se pudo analizar este proyecto", ["abrir_carpeta"])
    if status.state is RepoState.NOT_CLONED:
        return ("Está en GitHub pero no en este equipo", ["clonar"])
    return ("", [])


def plan_push(
    statuses: list[RepoStatus], team: str, message: str | None = None
) -> Plan:
    """Decide qué repositorios se subirán (sección 7)."""
    plan = Plan(kind=PUSH)
    mensaje = message or default_commit_message(team)

    for status in statuses:
        if status.state is RepoState.NOT_CLONED:
            continue
        if status.is_blocked or status.state is RepoState.NO_UPSTREAM:
            motivo, opciones = _blocked_reason(status, PUSH)
            plan.skipped.append(PlannedAction(status, "skip", motivo, offers=opciones))
        elif status.can_push:
            plan.to_do.append(PlannedAction(
                status, PUSH,
                commit_message=mensaje,
                warnings=find_warnings(status),
            ))
        elif status.state is RepoState.BEHIND:
            plan.skipped.append(PlannedAction(
                status, "skip",
                f"GitHub tiene {status.behind} cambio{'s' if status.behind != 1 else ''} "
                "que aún no tienes. Sincroniza este proyecto primero.",
                offers=["sincronizar"],
            ))
        else:
            plan.unchanged.append(PlannedAction(status, "skip", "Al día", selected=False))
    return plan


def plan_sync(statuses: list[RepoStatus], missing: list[MissingRepo] | None = None) -> Plan:
    """Decide qué repositorios se sincronizarán (secciones 8 y 6.3)."""
    plan = Plan(kind=SYNC, missing=list(missing or []))

    for status in statuses:
        if status.state is RepoState.NOT_CLONED:
            continue
        if status.is_blocked:
            motivo, opciones = _blocked_reason(status, SYNC)
            plan.skipped.append(PlannedAction(status, "skip", motivo, offers=opciones))
        elif status.can_sync:
            plan.to_do.append(PlannedAction(status, "pull"))
        elif status.has_newer_work:
            # 6.3: el caso que más preocupa al usuario.
            partes = []
            if status.ahead:
                partes.append(f"{status.ahead} commit{'s' if status.ahead != 1 else ''}")
            if status.files:
                partes.append(f"{len(status.files)} archivo{'s' if len(status.files) != 1 else ''}")
            plan.skipped.append(PlannedAction(
                status, "skip",
                f"Este equipo tiene trabajo más nuevo que GitHub ({' y '.join(partes)} sin subir). "
                "Súbelo primero para no perderlo.",
                offers=["subir_este"],
            ))
        else:
            plan.unchanged.append(PlannedAction(status, "skip", "Al día", selected=False))
    return plan


# --------------------------------------------------------------------------
# Ejecución
# --------------------------------------------------------------------------

@dataclass
class RepoResult:
    """Qué pasó con un repositorio concreto."""

    name: str
    action: str
    ok: bool
    message: str
    backup_id: str | None = None
    skipped: bool = False
    new_state: RepoState | None = None


@dataclass
class RunReport:
    """El resumen final de la sección 6.5.6."""

    kind: str
    results: list[RepoResult] = field(default_factory=list)

    @property
    def done(self) -> list[RepoResult]:
        return [r for r in self.results if r.ok and not r.skipped]

    @property
    def skipped(self) -> list[RepoResult]:
        return [r for r in self.results if r.skipped]

    @property
    def failed(self) -> list[RepoResult]:
        return [r for r in self.results if not r.ok and not r.skipped]

    def summary(self) -> str:
        return (
            f"{len(self.done)} completado{'s' if len(self.done) != 1 else ''}, "
            f"{len(self.skipped)} omitido{'s' if len(self.skipped) != 1 else ''}, "
            f"{len(self.failed)} con error{'es' if len(self.failed) != 1 else ''}"
        )


def _exclusivo(action: str):
    """Ejecuta la operación con el repositorio reservado (``safety.repo_lock``).

    Si otra ventana ya está trabajando en él, se omite con un mensaje claro
    en lugar de pisarse.
    """
    def decorador(funcion):
        @functools.wraps(funcion)
        def envoltura(status: RepoStatus, *args, **kwargs) -> RepoResult:
            try:
                with safety.repo_lock(status.path):
                    return funcion(status, *args, **kwargs)
            except safety.RepoBusy as exc:
                return RepoResult(status.name, action, ok=False, skipped=True, message=str(exc))
        return envoltura
    return decorador


def ensure_git_identity(name: str | None = None, email: str | None = None) -> tuple[str, str] | None:
    """Comprueba (y si hace falta configura) el nombre y correo de Git (7.1).

    Devuelve la identidad configurada, o ``None`` si falta y no se ha aportado.
    """
    actual_name = git_ops.config_get(None, "user.name", global_scope=True)
    actual_email = git_ops.config_get(None, "user.email", global_scope=True)
    if actual_name and actual_email:
        return actual_name, actual_email
    if not (name and email):
        return None
    git_ops.run(["config", "--global", "user.name", name], check=True)
    git_ops.run(["config", "--global", "user.email", email], check=True)
    return name, email


def _with_trailer(message: str, team: str) -> str:
    """Añade el trailer que identifica el equipo de origen (sección 7.4)."""
    if f"{analyzer.TEAM_TRAILER}:" in message:
        return message
    return f"{message.rstrip()}\n\n{analyzer.TEAM_TRAILER}: {team}"


@_exclusivo(PUSH)
def push_repo(
    status: RepoStatus,
    team: str,
    message: str,
    *,
    token: str | None = None,
    allow_set_upstream: bool = False,
) -> RepoResult:
    """Sube un repositorio siguiendo los seis pasos de la sección 7."""
    repo = status.path
    nombre = status.name
    respaldo = None
    if status.state is RepoState.ERROR or not git_ops.is_repo(repo):
        return RepoResult(
            nombre, PUSH, ok=False,
            message=status.error or "Esta carpeta ya no es un repositorio de Git",
        )
    try:
        # 2) Respaldo antes de tocar nada.
        respaldo = safety.create_backup(repo, safety.PUSH_REASON, team)

        # 3-4) Guardar los cambios sin subir en un commit.
        if status.files:
            git_ops.run(["add", "-A"], cwd=repo, check=True)
            hay_algo = git_ops.run(["diff", "--cached", "--quiet"], cwd=repo).returncode != 0
            if hay_algo:
                git_ops.run(
                    ["commit", "-m", _with_trailer(message, team)], cwd=repo, check=True
                )

        # 5) ¿Ha avanzado GitHub mientras tanto? Entonces no se sube nada.
        git_ops.run(["fetch", "--quiet", "origin"], cwd=repo, token=token)
        despues = analyzer.analyze_repo(repo, fetch=False)
        if despues.behind:
            return RepoResult(
                nombre, PUSH, ok=False, skipped=True, backup_id=respaldo.id,
                new_state=RepoState.DIVERGED,
                message=(
                    "GitHub ha cambiado mientras se subía, así que no se ha subido nada. "
                    "Ahora hay que combinar los dos lados."
                ),
            )

        # 6) Subir. Nunca forzado.
        if not despues.upstream:
            # Rama nueva: sin rama en GitHub no hay nada con lo que comparar,
            # así que se pide confirmación explícita (7.6).
            if not allow_set_upstream:
                return RepoResult(
                    nombre, PUSH, ok=False, skipped=True, backup_id=respaldo.id,
                    new_state=despues.state,
                    message=f"La rama «{despues.branch}» aún no existe en GitHub; hace falta tu confirmación",
                )
            subido = git_ops.run(
                ["push", "-u", "origin", despues.branch or "HEAD"], cwd=repo, token=token
            )
        else:
            if despues.ahead == 0:
                return RepoResult(
                    nombre, PUSH, ok=True, backup_id=respaldo.id,
                    new_state=despues.state, message="No había nada nuevo que subir",
                )
            subido = git_ops.run(["push", "origin", "HEAD"], cwd=repo, token=token)

        if not subido.ok:
            return RepoResult(
                nombre, PUSH, ok=False, backup_id=respaldo.id,
                message=_readable_error(subido.stderr),
            )

        final = analyzer.analyze_repo(repo, fetch=False)
        return RepoResult(
            nombre, PUSH, ok=True, backup_id=respaldo.id, new_state=final.state,
            message=(
                f"{despues.ahead} cambio{'s' if despues.ahead != 1 else ''} subido"
                f"{'s' if despues.ahead != 1 else ''} a GitHub"
                if despues.ahead else "Rama nueva publicada en GitHub"
            ),
        )

    except GitError as exc:
        log.error("fallo al subir %s: %s", nombre, exc)
        return RepoResult(
            nombre, PUSH, ok=False,
            backup_id=respaldo.id if respaldo else None,
            message=_readable_error(str(exc)),
        )


@_exclusivo(SYNC)
def sync_repo(status: RepoStatus, team: str, *, token: str | None = None) -> RepoResult:
    """Baja los cambios de GitHub siguiendo la sección 8."""
    repo = status.path
    nombre = status.name
    respaldo = None
    if status.state is RepoState.ERROR or not git_ops.is_repo(repo):
        return RepoResult(
            nombre, SYNC, ok=False,
            message=status.error or "Esta carpeta ya no es un repositorio de Git",
        )
    try:
        # 1) Solo repos atrasados y limpios (6.3). Se vuelve a comprobar aquí.
        actual = analyzer.analyze_repo(repo, fetch=True, token=token)
        if not actual.can_sync:
            return RepoResult(
                nombre, SYNC, ok=False, skipped=True, new_state=actual.state,
                message=(
                    "Ya no se puede sincronizar sin riesgo: "
                    f"el proyecto está «{actual.label.lower()}»."
                ),
            )

        # 2) Respaldo.
        respaldo = safety.create_backup(repo, "antes de sincronizar", team)

        # 3) Avance rápido: por diseño no puede sobrescribir trabajo.
        bajado = git_ops.run(["pull", "--ff-only", "origin"], cwd=repo, token=token)
        if not bajado.ok:
            return RepoResult(
                nombre, SYNC, ok=False, backup_id=respaldo.id,
                message=_readable_error(bajado.stderr),
            )

        # 4) Comprobar que HEAD coincide ahora con la rama de GitHub.
        cabeza = git_ops.run(["rev-parse", "HEAD"], cwd=repo).out
        remota = git_ops.run(["rev-parse", "@{u}"], cwd=repo).out
        if cabeza != remota:
            return RepoResult(
                nombre, SYNC, ok=False, backup_id=respaldo.id,
                message="La sincronización no terminó de aplicarse; el respaldo sigue disponible",
            )

        final = analyzer.analyze_repo(repo, fetch=False)
        traidos = actual.behind
        return RepoResult(
            nombre, SYNC, ok=True, backup_id=respaldo.id, new_state=final.state,
            message=f"{traidos} cambio{'s' if traidos != 1 else ''} traído"
                    f"{'s' if traidos != 1 else ''} de GitHub",
        )

    except GitError as exc:
        log.error("fallo al sincronizar %s: %s", nombre, exc)
        return RepoResult(
            nombre, SYNC, ok=False,
            backup_id=respaldo.id if respaldo else None,
            message=_readable_error(str(exc)),
        )


def clone_repo(missing: MissingRepo, root: "str | Path", *, token: str | None = None) -> RepoResult:
    """Clona un repositorio que está en GitHub y no en este equipo (sección 8)."""
    destino = Path(root) / missing.name
    if destino.exists():
        return RepoResult(
            missing.name, "clone", ok=False, skipped=True,
            message="Ya existe una carpeta con ese nombre; no se ha tocado",
        )
    clonado = git_ops.run(
        ["clone", missing.clone_url, str(destino)], cwd=root, token=token, timeout=600
    )
    if not clonado.ok:
        return RepoResult(missing.name, "clone", ok=False, message=_readable_error(clonado.stderr))
    return RepoResult(missing.name, "clone", ok=True, message="Descargado de GitHub")


def execute(
    plan: Plan,
    team: str,
    *,
    token: str | None = None,
    root: "str | Path | None" = None,
    allow_set_upstream: bool = False,
    on_progress=None,
) -> RunReport:
    """Ejecuta el plan confirmado. El fallo de un repo no detiene a los demás (11)."""
    report = RunReport(kind=plan.kind)
    acciones = plan.selected_actions
    clonables = [m for m in plan.missing if m.selected] if root else []
    total = len(acciones) + len(clonables)
    hechos = 0

    for accion in acciones:
        if on_progress:
            on_progress(hechos, total, accion.name)
        try:
            if accion.action == PUSH:
                resultado = push_repo(
                    accion.status, team,
                    accion.commit_message or default_commit_message(team),
                    token=token, allow_set_upstream=allow_set_upstream,
                )
            else:
                resultado = sync_repo(accion.status, team, token=token)
        except Exception as exc:  # pragma: no cover - red de seguridad
            log.exception("error inesperado en %s", accion.name)
            resultado = RepoResult(accion.name, accion.action, ok=False, message=str(exc))
        report.results.append(resultado)
        hechos += 1

    for pendiente in clonables:
        if on_progress:
            on_progress(hechos, total, pendiente.name)
        report.results.append(clone_repo(pendiente, root, token=token))  # type: ignore[arg-type]
        hechos += 1

    if on_progress:
        on_progress(hechos, total, None)
    log.info("«%s» terminado: %s", plan.kind, report.summary())
    return report


# --------------------------------------------------------------------------
# Acciones individuales para repositorios bloqueados (sección 6.6)
# --------------------------------------------------------------------------

@_exclusivo("merge")
def try_merge(status: RepoStatus, team: str, *, token: str | None = None) -> RepoResult:
    """6.6(a) — «Intentar combinar automáticamente» un repositorio divergido.

    Si el rebase da conflicto se aborta **inmediatamente** y el repositorio
    queda exactamente como estaba.
    """
    repo = status.path
    if status.files:
        return RepoResult(
            status.name, "merge", ok=False, skipped=True,
            message="Antes de combinar hay que guardar o subir los cambios sin guardar",
        )

    respaldo = safety.create_backup(repo, "antes de combinar", team)

    combinado = git_ops.run(
        ["pull", "--rebase", "origin"], cwd=repo, token=token, allow={ALLOW_REBASE_PULL}
    )
    if not combinado.ok:
        git_ops.run(["rebase", "--abort"], cwd=repo)
        final = analyzer.analyze_repo(repo, fetch=False)
        return RepoResult(
            status.name, "merge", ok=False, backup_id=respaldo.id, new_state=final.state,
            message=(
                "Los cambios se pisan entre sí y no se pueden combinar solos. "
                "Nada se ha modificado: ábrelo en VS Code para resolverlo a mano."
            ),
        )

    final = analyzer.analyze_repo(repo, fetch=False)
    return RepoResult(
        status.name, "merge", ok=True, backup_id=respaldo.id, new_state=final.state,
        message="Los dos lados se han combinado sin conflictos",
    )


@_exclusivo("stash_sync")
def stash_and_sync(status: RepoStatus, team: str, *, token: str | None = None) -> RepoResult:
    """6.6(b) — «Guardar mis cambios aparte y sincronizar».

    Si al recuperarlos hay conflicto, el escondite se deja intacto y se avisa
    de que los cambios siguen a salvo.
    """
    repo = status.path
    respaldo = safety.create_backup(repo, "antes de guardar los cambios aparte", team)

    antes = _stash_top(repo)
    guardado = git_ops.run(["stash", "push", "-u", "-m", "Vaiven: antes de sincronizar"], cwd=repo)
    if not guardado.ok:
        return RepoResult(
            status.name, "stash_sync", ok=False, backup_id=respaldo.id,
            message=_readable_error(guardado.stderr),
        )
    # Sin cambios, «stash push» termina bien pero no guarda nada; entonces un
    # «stash pop» sacaría un escondite antiguo del usuario. Solo se recupera
    # lo que se ha guardado aquí.
    guardo_algo = _stash_top(repo) not in (None, antes)

    bajado = git_ops.run(["pull", "--ff-only", "origin"], cwd=repo, token=token)
    if not bajado.ok:
        if guardo_algo:
            git_ops.run(["stash", "pop"], cwd=repo)
        return RepoResult(
            status.name, "stash_sync", ok=False, backup_id=respaldo.id,
            message="No se pudo traer los cambios de GitHub; tus cambios se han devuelto tal cual",
        )

    recuperado = git_ops.run(["stash", "pop"], cwd=repo) if guardo_algo else None
    if recuperado is not None and not recuperado.ok:
        return RepoResult(
            status.name, "stash_sync", ok=False, backup_id=respaldo.id,
            new_state=analyzer.analyze_repo(repo, fetch=False).state,
            message=(
                "Se han traído los cambios de GitHub, pero tus cambios chocan con ellos. "
                "Están a salvo: siguen guardados aparte y también en el respaldo."
            ),
        )

    final = analyzer.analyze_repo(repo, fetch=False)
    return RepoResult(
        status.name, "stash_sync", ok=True, backup_id=respaldo.id, new_state=final.state,
        message="Sincronizado, y tus cambios siguen aquí",
    )


def _stash_top(repo: Path) -> str | None:
    """Commit del escondite más reciente, o ``None`` si está vacío."""
    cima = git_ops.run(["rev-parse", "-q", "--verify", "refs/stash"], cwd=repo)
    return cima.out if cima.ok and cima.out else None


def push_this_repo(
    status: RepoStatus, team: str, message: str | None = None, *,
    token: str | None = None, allow_set_upstream: bool = False,
) -> RepoResult:
    """6.6 — «Subir este proyecto» / «Subir mis cambios primero»."""
    return push_repo(
        status, team, message or default_commit_message(team),
        token=token, allow_set_upstream=allow_set_upstream,
    )


@_exclusivo("undo")
def undo(status: RepoStatus) -> RepoResult:
    """Deshace la última operación de Vaivén en un repositorio (6.4)."""
    candidato = safety.undo_candidate(status.path)
    try:
        previo = safety.undo_last(status.path)
    except GitError as exc:
        return RepoResult(status.name, "undo", ok=False, message=str(exc))
    if previo is None:
        return RepoResult(
            status.name, "undo", ok=False, skipped=True,
            message="No hay ninguna operación reciente que deshacer",
        )
    mensaje = "El proyecto ha vuelto a como estaba antes de la última operación"
    if candidato is not None and candidato.reason == safety.PUSH_REASON:
        # Deshacer es local: lo que ya llegó a GitHub sigue allí (nunca se
        # fuerza un push), y ahora aparecerá como cambios por traer.
        final = analyzer.analyze_repo(status.path, fetch=False)
        if final.behind:
            mensaje += (
                ". Lo que ya se había subido sigue en GitHub; aquí aparecerá "
                "como cambios por traer"
            )
    return RepoResult(
        status.name, "undo", ok=True, backup_id=previo.id,
        message=mensaje,
    )


# --------------------------------------------------------------------------

_TRADUCCIONES = (
    ("could not read Username", "GitHub ha pedido usuario y contraseña: vuelve a iniciar sesión."),
    ("Authentication failed", "GitHub ha rechazado la sesión: vuelve a iniciar sesión."),
    ("403", "GitHub no ha permitido la operación (403). Puede faltar permiso sobre ese repositorio."),
    ("401", "La sesión de GitHub ha caducado. Vuelve a iniciar sesión."),
    ("non-fast-forward", "GitHub ha cambiado mientras tanto; hay que combinar los dos lados."),
    ("Could not resolve host", "No hay conexión con GitHub. Comprueba tu internet."),
    ("unable to access", "No se pudo conectar con GitHub. Comprueba tu internet."),
    ("exceeds GitHub's file size limit", "Hay un archivo demasiado grande para GitHub (más de 100 MB)."),
    ("Not possible to fast-forward", "No se puede traer sin combinar: los dos lados han cambiado."),
)


def _readable_error(raw: str) -> str:
    """Traduce los errores de Git a algo que el usuario entienda (9.4)."""
    texto = (raw or "").strip()
    for marca, amable in _TRADUCCIONES:
        if marca.lower() in texto.lower():
            return amable
    primera = next((line for line in texto.splitlines() if line.strip()), "")
    return primera[:200] or "La operación no se pudo completar; mira el registro para más detalles."
