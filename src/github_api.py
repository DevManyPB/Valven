"""Acceso a la API de GitHub: usuario y lista de repositorios (sección 5.2.6 y 8).

Solo lectura. Nada de lo que hay aquí modifica nada en GitHub: los cambios se
hacen siempre con Git, a través de ``git_ops.py``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from .auth import AuthError, SessionExpired
from .logger import get_logger

log = get_logger("github_api")

API_BASE = "https://api.github.com"
HTTP_TIMEOUT = 30
PER_PAGE = 100
MAX_PAGES = 50  # 5.000 repositorios; más que de sobra


@dataclass
class GitHubUser:
    """Los datos que se muestran en la barra superior (9.1)."""

    login: str
    name: str | None = None
    avatar_url: str | None = None

    @property
    def display_name(self) -> str:
        return self.name or self.login


@dataclass
class GitHubRepo:
    """Un repositorio tal y como lo ve GitHub."""

    name: str
    full_name: str
    clone_url: str
    private: bool = False
    fork: bool = False
    archived: bool = False
    default_branch: str = "main"
    owner: str = ""
    description: str = ""
    pushed_at: datetime | None = None

    @property
    def is_owned_by(self) -> str:
        return self.owner


class GitHubClient:
    """Cliente mínimo de la API de GitHub."""

    def __init__(self, token: str, session=None) -> None:
        self.token = token
        self._session = session

    @property
    def session(self):
        if self._session is None:
            import requests

            self._session = requests.Session()
        self._session.headers.update({
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "Vaiven",
        })
        return self._session

    def _get(self, path: str, params: dict | None = None):
        url = path if path.startswith("http") else f"{API_BASE}{path}"
        try:
            respuesta = self.session.get(url, params=params, timeout=HTTP_TIMEOUT)
        except Exception as exc:
            raise AuthError("No se pudo contactar con GitHub. Comprueba tu conexión.") from exc

        if respuesta.status_code == 401:
            raise SessionExpired("La sesión de GitHub ha caducado. Vuelve a iniciar sesión.")
        if respuesta.status_code == 403:
            raise AuthError(
                "GitHub ha denegado la petición. Puede que se haya alcanzado el límite de uso; "
                "espera unos minutos."
            )
        if respuesta.status_code >= 400:
            raise AuthError(f"GitHub respondió {respuesta.status_code} al pedir {path}.")
        return respuesta

    def get_user(self) -> GitHubUser:
        """Nombre y avatar del usuario que ha iniciado sesión (5.2.6)."""
        datos = self._get("/user").json()
        return GitHubUser(
            login=datos.get("login", ""),
            name=datos.get("name"),
            avatar_url=datos.get("avatar_url"),
        )

    def list_repos(self, *, include_forks: bool = True, include_archived: bool = False) -> list[GitHubRepo]:
        """Todos los repositorios del usuario, con paginación (sección 8).

        Incluye los privados y los de organizaciones a los que tenga acceso.
        """
        repos: list[GitHubRepo] = []
        for pagina in range(1, MAX_PAGES + 1):
            respuesta = self._get("/user/repos", params={
                "per_page": PER_PAGE,
                "page": pagina,
                "affiliation": "owner,collaborator,organization_member",
                "sort": "full_name",
            })
            lote = respuesta.json()
            if not isinstance(lote, list) or not lote:
                break
            for datos in lote:
                repo = GitHubRepo(
                    name=datos.get("name", ""),
                    full_name=datos.get("full_name", ""),
                    clone_url=datos.get("clone_url", ""),
                    private=bool(datos.get("private")),
                    fork=bool(datos.get("fork")),
                    archived=bool(datos.get("archived")),
                    default_branch=datos.get("default_branch") or "main",
                    owner=(datos.get("owner") or {}).get("login", ""),
                    description=datos.get("description") or "",
                    pushed_at=_fecha(datos.get("pushed_at")),
                )
                if repo.fork and not include_forks:
                    continue
                if repo.archived and not include_archived:
                    continue
                repos.append(repo)
            if len(lote) < PER_PAGE:
                break
        log.info("GitHub devolvió %s repositorios", len(repos))
        return repos

    def check(self) -> bool:
        """¿Sigue siendo válida la sesión?"""
        try:
            self.get_user()
            return True
        except SessionExpired:
            return False


def _fecha(valor) -> datetime | None:
    try:
        return datetime.fromisoformat(str(valor).replace("Z", "+00:00")) if valor else None
    except ValueError:
        return None


_GITHUB_URL = re.compile(r"github\.com[:/]+([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", re.IGNORECASE)


def repo_key(url: str | None) -> str | None:
    """``dueño/nombre`` en minúsculas a partir de cualquier dirección de GitHub.

    Sirve para las formas HTTPS, SSH (``git@github.com:dueño/repo.git``) y
    con credenciales incrustadas; ``None`` si no es de GitHub.
    """
    encontrado = _GITHUB_URL.search(url or "")
    if not encontrado:
        return None
    return f"{encontrado.group(1)}/{encontrado.group(2)}".lower()


def missing_locally(
    remote_repos: list[GitHubRepo],
    local_names: set[str],
    local_remotes: "set[str] | list[str | None]" = (),
) -> list[GitHubRepo]:
    """Repositorios que están en GitHub y no en este equipo (sección 8).

    Un repo está en el equipo si alguna carpeta apunta a él (``local_remotes``,
    las direcciones de ``origin``), aunque la carpeta se llame distinto. El
    nombre de la carpeta cuenta también: clonar encima de una carpeta con ese
    nombre no se puede, así que ofrecerlo solo llevaría a un error.
    """
    minusculas = {name.lower() for name in local_names}
    claves = {clave for clave in map(repo_key, local_remotes) if clave}
    return [
        repo for repo in remote_repos
        if repo.name.lower() not in minusculas
        and (repo_key(repo.clone_url) or repo.full_name.lower()) not in claves
    ]
