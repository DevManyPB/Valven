"""Inicio de sesión con GitHub desde el navegador (sección 5 de SPEC.md).

Hay dos caminos, y la app usa el primero que pueda:

1. **Web directo** (el normal). Vaivén levanta un servidor diminuto en tu
   propio equipo, abre el navegador en GitHub y espera. Tú solo pulsas
   «Authorize» y GitHub devuelve la respuesta a ese servidor. No hay ningún
   código que copiar ni pegar.

2. **Por código** (reserva). Si el puerto local está ocupado o bloqueado por
   un cortafuegos, se usa el Device Flow: Vaivén enseña un código corto, lo
   copia al portapapeles y tú lo pegas en GitHub. Funciona siempre, porque no
   necesita escuchar en ningún puerto.

En los dos casos el usuario nunca ve un token, y la sesión se guarda **solo**
en el Administrador de credenciales de Windows a través de ``keyring``, jamás
en un archivo.

Configuración única, que el usuario hace una vez (ver README.md): crear una
OAuth App en GitHub y pegar aquí su Client ID y su Client Secret.
"""

from __future__ import annotations

import http.server
import json
import os
import secrets
import socket
import subprocess
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path

from .logger import get_logger

log = get_logger("auth")

#: Client ID público de la OAuth App del proyecto. Un Client ID **no es un
#: secreto**: GitHub lo publica en cada redirección, y está aquí para que
#: cualquiera que descargue Vaivén pueda iniciar sesión sin configurar nada.
PROJECT_CLIENT_ID = "Ov23licmREBuynsfJFde"

#: Archivo opcional, **fuera del repositorio**, donde guardar credenciales
#: propias: ``%APPDATA%\Vaiven\oauth.json``.
CREDENTIALS_FILENAME = "oauth.json"


def _local_credentials() -> dict:
    """Credenciales guardadas en la carpeta de datos, si las hay."""
    from . import config

    try:
        datos = json.loads((config.data_dir() / CREDENTIALS_FILENAME).read_text(encoding="utf-8"))
        return datos if isinstance(datos, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError, UnicodeDecodeError):
        return {}


def save_credentials(client_id: str = "", client_secret: str = "") -> Path:
    """Guarda credenciales propias fuera del repositorio.

    Sirve para que quien quiera el inicio de sesión web directo pueda usar su
    propia OAuth App sin tocar el código ni arriesgarse a publicar su secreto.
    """
    from . import config

    destino = config.data_dir() / CREDENTIALS_FILENAME
    datos = _local_credentials()
    if client_id:
        datos["client_id"] = client_id
    if client_secret:
        datos["client_secret"] = client_secret
    destino.write_text(json.dumps(datos, indent=2), encoding="utf-8")
    try:
        destino.chmod(0o600)   # solo su dueño puede leerlo
    except OSError:
        pass
    log.info("credenciales propias guardadas en %s", destino)
    return destino


_locales = _local_credentials()

#: Client ID en uso. Por orden: variable de entorno, archivo propio, proyecto.
CLIENT_ID = (
    os.environ.get("VAIVEN_CLIENT_ID")
    or _locales.get("client_id")
    or PROJECT_CLIENT_ID
)

#: Client Secret. **Nunca** se guarda en el repositorio: sin él, Vaivén usa el
#: inicio de sesión por código, que no necesita ningún secreto. Con él, usa el
#: inicio de sesión web directo. Ver ARQUITECTURA.md, «Inicio de sesión».
CLIENT_SECRET = (
    os.environ.get("VAIVEN_CLIENT_SECRET")
    or _locales.get("client_secret")
    or ""
)

#: Permisos mínimos: leer y escribir repositorios, y leer el perfil.
SCOPES = "repo read:user"

AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
DEVICE_CODE_URL = "https://github.com/login/device/code"
ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
VERIFICATION_URL = "https://github.com/login/device"

GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"

#: Dirección a la que GitHub devuelve la respuesta del login web.
#: El puerto es fijo a propósito: GitHub exige que la dirección coincida
#: **exactamente** con la que esté registrada en la OAuth App.
CALLBACK_HOST = "127.0.0.1"
CALLBACK_PORT = 49732
CALLBACK_PATH = "/vaiven/callback"
REDIRECT_URI = f"http://{CALLBACK_HOST}:{CALLBACK_PORT}{CALLBACK_PATH}"

#: Cuánto se espera a que el usuario pulse «Authorize» (5 minutos).
WEB_LOGIN_TIMEOUT = 300

#: Dónde guarda ``keyring`` la sesión.
KEYRING_SERVICE = "Vaiven"
KEYRING_USERNAME = "github"

HTTP_TIMEOUT = 30


class AuthError(Exception):
    """Cualquier problema durante el inicio de sesión."""


class CodeExpired(AuthError):
    """El código caducó antes de que el usuario lo autorizara (5.2.5)."""


class AccessDenied(AuthError):
    """El usuario rechazó la autorización en GitHub."""


class SessionExpired(AuthError):
    """GitHub ya no acepta la sesión guardada (5.3)."""


class ClientIdMissing(AuthError):
    """Falta configurar el Client ID de la OAuth App."""


class PortUnavailable(AuthError):
    """El puerto del login web está ocupado; hay que usar el de reserva."""


@dataclass
class DeviceCode:
    """Lo que GitHub devuelve al pedir un código (5.2.2)."""

    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int

    @property
    def expires_at(self) -> float:
        return time.monotonic() + self.expires_in


def _session():
    """Sesión HTTP. Se importa aquí para que la app arranque sin red."""
    import requests

    session = requests.Session()
    session.headers.update({
        "Accept": "application/json",
        "User-Agent": "Vaiven",
    })
    return session


def client_id_is_configured() -> bool:
    return bool(CLIENT_ID) and "AQUIELCLIENTID" not in CLIENT_ID


def client_secret_is_configured() -> bool:
    """¿Hay secreto propio configurado? Si no, se usa el login por código."""
    return bool(CLIENT_SECRET) and "AQUIELCLIENTSECRET" not in CLIENT_SECRET


def web_login_configured() -> bool:
    """¿Se puede usar el inicio de sesión web directo, sin pegar códigos?"""
    return client_id_is_configured() and client_secret_is_configured()


def _require_client_id() -> None:
    if not client_id_is_configured():
        raise ClientIdMissing(
            "Falta el Client ID de la OAuth App de GitHub. "
            "Sigue los pasos del README para crearla y pégalo en src/auth.py."
        )


# --------------------------------------------------------------------------
# Login web directo: el usuario solo pulsa «Authorize»
# --------------------------------------------------------------------------

#: Lo que ve el usuario en el navegador cuando ya ha autorizado.
_PAGINA_OK = """<!doctype html><html lang="es"><head><meta charset="utf-8">
<title>Vaivén</title><style>
 :root{color-scheme:light dark}
 body{margin:0;min-height:100vh;display:grid;place-items:center;
      font-family:system-ui,-apple-system,"Segoe UI",sans-serif;
      background:#f8fafc;color:#0f172a}
 @media (prefers-color-scheme:dark){body{background:#0f172a;color:#e2e8f0}}
 .caja{text-align:center;padding:48px 40px;max-width:26rem}
 .marca{font-size:2.25rem;font-weight:700;letter-spacing:-.02em}
 .ok{font-size:3rem;line-height:1;margin:.5rem 0 1rem;color:#059669}
 p{font-size:1.05rem;line-height:1.55;margin:.4rem 0}
 .tenue{opacity:.65;font-size:.9rem;margin-top:1.5rem}
</style></head><body><div class="caja">
 <div class="marca">Vaivén</div>
 <div class="ok">&#10003;</div>
 <p><strong>Sesión iniciada.</strong></p>
 <p>Ya puedes cerrar esta pestaña y volver a Vaivén.</p>
 <p class="tenue">No tendrás que volver a hacer esto.</p>
</div></body></html>"""

_PAGINA_ERROR = """<!doctype html><html lang="es"><head><meta charset="utf-8">
<title>Vaivén</title><style>
 :root{color-scheme:light dark}
 body{margin:0;min-height:100vh;display:grid;place-items:center;
      font-family:system-ui,-apple-system,"Segoe UI",sans-serif;
      background:#f8fafc;color:#0f172a}
 @media (prefers-color-scheme:dark){body{background:#0f172a;color:#e2e8f0}}
 .caja{text-align:center;padding:48px 40px;max-width:26rem}
 .marca{font-size:2.25rem;font-weight:700;letter-spacing:-.02em}
 .mal{font-size:3rem;line-height:1;margin:.5rem 0 1rem;color:#dc2626}
 p{font-size:1.05rem;line-height:1.55;margin:.4rem 0}
</style></head><body><div class="caja">
 <div class="marca">Vaivén</div>
 <div class="mal">&#10007;</div>
 <p><strong>No se pudo completar el inicio de sesión.</strong></p>
 <p>Cierra esta pestaña y vuelve a intentarlo desde Vaivén.</p>
</div></body></html>"""


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Atiende la única visita que hará GitHub al volver del navegador."""

    server_version = "Vaiven"

    def do_GET(self) -> None:  # noqa: N802 (nombre impuesto por la clase base)
        partes = urllib.parse.urlsplit(self.path)
        if partes.path != CALLBACK_PATH:
            self.send_error(404)
            return

        consulta = urllib.parse.parse_qs(partes.query)
        servidor = self.server
        servidor.code = (consulta.get("code") or [None])[0]        # type: ignore[attr-defined]
        servidor.state = (consulta.get("state") or [None])[0]      # type: ignore[attr-defined]
        servidor.error = (consulta.get("error_description") or consulta.get("error") or [None])[0]  # type: ignore[attr-defined]

        correcto = bool(servidor.code) and not servidor.error      # type: ignore[attr-defined]
        cuerpo = (_PAGINA_OK if correcto else _PAGINA_ERROR).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(cuerpo)

    def log_message(self, *args) -> None:
        """Silencio: el .exe no debe escribir nada en consola."""


@dataclass
class WebLogin:
    """Un intento de login web en marcha."""

    url: str
    state: str
    _server: http.server.HTTPServer | None = field(default=None, repr=False)

    def close(self) -> None:
        if self._server is not None:
            self._server.server_close()
            self._server = None


def web_login_available(port: int = CALLBACK_PORT) -> bool:
    """¿Se puede escuchar en el puerto del login web?"""
    prueba = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        prueba.bind((CALLBACK_HOST, port))
        return True
    except OSError:
        return False
    finally:
        prueba.close()


def start_web_login(port: int = CALLBACK_PORT) -> WebLogin:
    """Levanta el servidor local y devuelve la dirección de GitHub a abrir.

    El ``state`` es un valor aleatorio que se comprueba al volver: impide que
    otra página del navegador pueda colar una respuesta falsa.
    """
    _require_client_id()
    if not client_secret_is_configured():
        raise ClientIdMissing(
            "Falta el Client Secret de la OAuth App de GitHub. "
            "Sigue los pasos del README para copiarlo en src/auth.py."
        )

    try:
        servidor = http.server.HTTPServer((CALLBACK_HOST, port), _CallbackHandler)
    except OSError as exc:
        raise PortUnavailable(
            f"El puerto {port} está ocupado; se usará el inicio de sesión por código."
        ) from exc

    servidor.timeout = 1
    servidor.code = None      # type: ignore[attr-defined]
    servidor.state = None     # type: ignore[attr-defined]
    servidor.error = None     # type: ignore[attr-defined]

    state = secrets.token_urlsafe(24)
    consulta = urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "redirect_uri": f"http://{CALLBACK_HOST}:{port}{CALLBACK_PATH}",
        "scope": SCOPES,
        "state": state,
    })
    log.info("login web iniciado, escuchando en el puerto %s", port)
    return WebLogin(url=f"{AUTHORIZE_URL}?{consulta}", state=state, _server=servidor)


def wait_for_authorization(
    login: WebLogin,
    *,
    timeout: int = WEB_LOGIN_TIMEOUT,
    cancel: threading.Event | None = None,
    on_wait=None,
    now=time.monotonic,
) -> str:
    """Espera a que GitHub devuelva el código de autorización.

    Devuelve ese código. Lanza :class:`CodeExpired` si el usuario nunca
    autoriza, o :class:`AccessDenied` si lo rechaza en GitHub.
    """
    servidor = login._server
    if servidor is None:
        raise AuthError("El inicio de sesión ya se había cerrado.")

    limite = now() + timeout
    try:
        while True:
            if cancel is not None and cancel.is_set():
                raise AuthError("Inicio de sesión cancelado.")
            if now() >= limite:
                raise CodeExpired(
                    "Se ha agotado el tiempo de espera. "
                    "Pulsa «Intentar de nuevo» para empezar otra vez."
                )
            servidor.handle_request()   # vuelve en 1 s aunque no llegue nada

            if servidor.error:          # type: ignore[attr-defined]
                detalle = str(servidor.error)   # type: ignore[attr-defined]
                if "denied" in detalle.lower():
                    raise AccessDenied("Has cancelado el inicio de sesión en GitHub.")
                raise AuthError(f"GitHub rechazó el inicio de sesión: {detalle}")

            if servidor.code:           # type: ignore[attr-defined]
                if servidor.state != login.state:   # type: ignore[attr-defined]
                    raise AuthError(
                        "La respuesta de GitHub no coincide con la petición; "
                        "por seguridad no se ha iniciado sesión."
                    )
                log.info("autorización recibida de GitHub")
                return str(servidor.code)   # type: ignore[attr-defined]

            if on_wait:
                on_wait(max(0, int(limite - now())))
    finally:
        login.close()


def exchange_code(code: str, session=None, *, port: int = CALLBACK_PORT) -> str:
    """Canjea el código de autorización por la sesión (el token)."""
    session = session or _session()
    try:
        respuesta = session.post(
            ACCESS_TOKEN_URL,
            data={
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "code": code,
                "redirect_uri": f"http://{CALLBACK_HOST}:{port}{CALLBACK_PATH}",
            },
            timeout=HTTP_TIMEOUT,
        )
    except Exception as exc:
        raise AuthError("Se perdió la conexión con GitHub al iniciar sesión.") from exc

    datos = _json(respuesta)
    if datos.get("access_token"):
        log.info("sesión de GitHub obtenida por login web")
        return datos["access_token"]
    raise AuthError(_describe(datos) or "GitHub no devolvió la sesión.")


def request_device_code(session=None) -> DeviceCode:
    """Paso 2 de la sección 5.2: pedir el código a GitHub."""
    _require_client_id()
    session = session or _session()
    try:
        respuesta = session.post(
            DEVICE_CODE_URL,
            data={"client_id": CLIENT_ID, "scope": SCOPES},
            timeout=HTTP_TIMEOUT,
        )
    except Exception as exc:
        raise AuthError("No se pudo contactar con GitHub. Comprueba tu conexión.") from exc

    datos = _json(respuesta)
    if "device_code" not in datos:
        raise AuthError(_describe(datos) or "GitHub no devolvió un código de acceso.")

    log.info("código de dispositivo solicitado; caduca en %ss", datos.get("expires_in"))
    return DeviceCode(
        device_code=datos["device_code"],
        user_code=datos["user_code"],
        verification_uri=datos.get("verification_uri") or VERIFICATION_URL,
        expires_in=int(datos.get("expires_in", 900)),
        interval=int(datos.get("interval", 5)),
    )


def poll_for_token(
    code: DeviceCode,
    session=None,
    *,
    on_wait=None,
    sleep=time.sleep,
    now=time.monotonic,
) -> str:
    """Paso 5: preguntar a GitHub hasta obtener la sesión.

    Respeta el ``interval`` que indica GitHub y lo aumenta si responde
    ``slow_down``. Lanza :class:`CodeExpired` si se agota el plazo.
    """
    session = session or _session()
    intervalo = max(1, code.interval)
    limite = now() + code.expires_in

    while True:
        if now() >= limite:
            raise CodeExpired(
                "El código ha caducado. Pulsa «Intentar de nuevo» para pedir uno nuevo."
            )
        sleep(intervalo)
        try:
            respuesta = session.post(
                ACCESS_TOKEN_URL,
                data={
                    "client_id": CLIENT_ID,
                    "device_code": code.device_code,
                    "grant_type": GRANT_TYPE,
                },
                timeout=HTTP_TIMEOUT,
            )
        except Exception as exc:
            raise AuthError("Se perdió la conexión con GitHub durante el inicio de sesión.") from exc

        datos = _json(respuesta)
        if datos.get("access_token"):
            log.info("sesión de GitHub obtenida")
            return datos["access_token"]

        error = datos.get("error")
        if error == "authorization_pending":
            if on_wait:
                on_wait(max(0, int(limite - now())))
            continue
        if error == "slow_down":
            intervalo = int(datos.get("interval", intervalo + 5))
            log.info("GitHub pide esperar más: %ss", intervalo)
            continue
        if error == "expired_token":
            raise CodeExpired(
                "El código ha caducado. Pulsa «Intentar de nuevo» para pedir uno nuevo."
            )
        if error == "access_denied":
            raise AccessDenied("Has cancelado el inicio de sesión en GitHub.")
        raise AuthError(_describe(datos) or "GitHub rechazó el inicio de sesión.")


def _json(respuesta) -> dict:
    try:
        datos = respuesta.json()
    except ValueError:
        return {}
    return datos if isinstance(datos, dict) else {}


def _describe(datos: dict) -> str:
    return datos.get("error_description") or datos.get("error") or ""


# --------------------------------------------------------------------------
# Guardar la sesión (solo en el Administrador de credenciales)
# --------------------------------------------------------------------------

def save_token(token: str) -> None:
    """Guarda la sesión en el almacén de credenciales del sistema (5.2.6)."""
    import keyring

    keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, token)
    log.info("sesión guardada en el almacén de credenciales")


def load_token() -> str | None:
    """Recupera la sesión guardada, o ``None`` si no hay ninguna."""
    try:
        import keyring

        return keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
    except Exception as exc:
        log.warning("no se pudo leer el almacén de credenciales: %s", exc)
        return None


def delete_token() -> None:
    """Cerrar sesión: borra la sesión guardada (5.2.7)."""
    try:
        import keyring

        keyring.delete_password(KEYRING_SERVICE, KEYRING_USERNAME)
        log.info("sesión borrada del almacén de credenciales")
    except Exception as exc:
        log.info("no había sesión que borrar: %s", exc)


def is_logged_in() -> bool:
    return bool(load_token())


# --------------------------------------------------------------------------
# Ayudas de la interfaz
# --------------------------------------------------------------------------

def copy_to_clipboard(text: str, widget=None) -> bool:
    """Copia el código al portapapeles (5.2.3). Devuelve si lo consiguió."""
    if widget is not None:
        try:
            widget.clipboard_clear()
            widget.clipboard_append(text)
            widget.update_idletasks()
            return True
        except Exception as exc:
            log.warning("no se pudo copiar con la ventana: %s", exc)

    if os.name == "nt":
        try:
            subprocess.run(
                ["clip"], input=text.encode("utf-16-le"), check=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return True
        except Exception as exc:
            log.warning("no se pudo copiar al portapapeles: %s", exc)
            return False

    for programa in (["xclip", "-selection", "clipboard"], ["wl-copy"], ["pbcopy"]):
        try:
            subprocess.run(programa, input=text.encode("utf-8"), check=True)
            return True
        except Exception:
            continue
    return False


def open_verification_page(url: str = VERIFICATION_URL) -> bool:
    """Abre el navegador en la página de autorización (5.2.3)."""
    try:
        return webbrowser.open(url, new=2)
    except Exception as exc:
        log.warning("no se pudo abrir el navegador: %s", exc)
        return False
