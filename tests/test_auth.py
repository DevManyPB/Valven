"""Fase 4 — inicio de sesión con GitHub (sección 5) y API (sección 8).

No se contacta con GitHub: se usa una sesión HTTP falsa que devuelve las
mismas respuestas que documenta GitHub para el Device Flow.
"""

from __future__ import annotations

import json

import pytest

from src import auth, github_api
from src.auth import AccessDenied, CodeExpired, DeviceCode, SessionExpired


class RespuestaFalsa:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        if isinstance(self._payload, str):
            raise ValueError("no es json")
        return self._payload


class SesionFalsa:
    """Devuelve respuestas preparadas y recuerda lo que se le pidió."""

    def __init__(self, respuestas):
        self.respuestas = list(respuestas)
        self.peticiones = []
        self.headers = {}

    def post(self, url, data=None, timeout=None):
        self.peticiones.append((url, data))
        return self.respuestas.pop(0)

    def get(self, url, params=None, timeout=None):
        self.peticiones.append((url, params))
        return self.respuestas.pop(0)


@pytest.fixture(autouse=True)
def _client_id(monkeypatch):
    monkeypatch.setattr(auth, "CLIENT_ID", "Ov23liDEPRUEBA")


# --- pedir el código (5.2.2) -----------------------------------------------

def test_pedir_codigo_usa_los_permisos_del_spec():
    sesion = SesionFalsa([RespuestaFalsa({
        "device_code": "dc-123", "user_code": "ABCD-1234",
        "verification_uri": "https://github.com/login/device",
        "expires_in": 900, "interval": 5,
    })])

    codigo = auth.request_device_code(sesion)

    url, datos = sesion.peticiones[0]
    assert url == auth.DEVICE_CODE_URL
    assert datos["scope"] == "repo read:user"
    assert datos["client_id"] == "Ov23liDEPRUEBA"
    assert codigo.user_code == "ABCD-1234"
    assert codigo.interval == 5


def test_sin_client_id_el_mensaje_explica_que_hacer(monkeypatch):
    monkeypatch.setattr(auth, "CLIENT_ID", "Ov23liPONMEAQUIELCLIENTID")
    assert not auth.client_id_is_configured()
    with pytest.raises(auth.ClientIdMissing) as error:
        auth.request_device_code(SesionFalsa([]))
    assert "README" in str(error.value)


def test_una_respuesta_inesperada_no_rompe_la_app():
    sesion = SesionFalsa([RespuestaFalsa({"error": "unknown", "error_description": "vaya"})])
    with pytest.raises(auth.AuthError) as error:
        auth.request_device_code(sesion)
    assert "vaya" in str(error.value)


# --- esperar la autorización (5.2.5) ---------------------------------------

def _codigo(expires_in=900, interval=1):
    return DeviceCode("dc-123", "ABCD-1234", auth.VERIFICATION_URL, expires_in, interval)


def test_espera_hasta_que_el_usuario_autoriza():
    sesion = SesionFalsa([
        RespuestaFalsa({"error": "authorization_pending"}),
        RespuestaFalsa({"error": "authorization_pending"}),
        RespuestaFalsa({"access_token": "gho_sesion"}),
    ])
    esperas = []

    token = auth.poll_for_token(
        _codigo(), sesion, sleep=lambda s: esperas.append(s), on_wait=lambda r: None
    )

    assert token == "gho_sesion"
    assert len(sesion.peticiones) == 3
    assert esperas == [1, 1, 1]


def test_respeta_slow_down():
    """GitHub puede pedir que se pregunte más despacio (5.2.5)."""
    sesion = SesionFalsa([
        RespuestaFalsa({"error": "slow_down", "interval": 10}),
        RespuestaFalsa({"access_token": "gho_sesion"}),
    ])
    esperas = []

    auth.poll_for_token(_codigo(interval=5), sesion, sleep=lambda s: esperas.append(s))

    assert esperas == [5, 10]


def test_el_codigo_caducado_se_avisa_con_claridad():
    sesion = SesionFalsa([RespuestaFalsa({"error": "expired_token"})])
    with pytest.raises(CodeExpired) as error:
        auth.poll_for_token(_codigo(), sesion, sleep=lambda s: None)
    assert "Intentar de nuevo" in str(error.value)


def test_deja_de_preguntar_cuando_se_agota_el_plazo():
    reloj = iter([0, 100, 1000, 2000])
    sesion = SesionFalsa([RespuestaFalsa({"error": "authorization_pending"})] * 5)
    with pytest.raises(CodeExpired):
        auth.poll_for_token(
            _codigo(expires_in=900), sesion, sleep=lambda s: None, now=lambda: next(reloj)
        )


def test_si_el_usuario_cancela_en_github():
    sesion = SesionFalsa([RespuestaFalsa({"error": "access_denied"})])
    with pytest.raises(AccessDenied):
        auth.poll_for_token(_codigo(), sesion, sleep=lambda s: None)


# --- guardar la sesión (5.2.6 y 5.2.7) -------------------------------------

class KeyringFalso:
    def __init__(self):
        self.almacen = {}

    def set_password(self, servicio, usuario, valor):
        self.almacen[(servicio, usuario)] = valor

    def get_password(self, servicio, usuario):
        return self.almacen.get((servicio, usuario))

    def delete_password(self, servicio, usuario):
        if (servicio, usuario) not in self.almacen:
            raise RuntimeError("no existe")
        del self.almacen[(servicio, usuario)]


@pytest.fixture
def keyring_falso(monkeypatch):
    import sys
    falso = KeyringFalso()
    monkeypatch.setitem(sys.modules, "keyring", falso)
    return falso


def test_la_sesion_persiste_y_se_puede_borrar(keyring_falso):
    assert not auth.is_logged_in()

    auth.save_token("gho_sesion")
    assert auth.load_token() == "gho_sesion"
    assert auth.is_logged_in()
    assert keyring_falso.almacen[("Vaiven", "github")] == "gho_sesion"

    auth.delete_token()
    assert auth.load_token() is None
    assert not auth.is_logged_in()


def test_cerrar_sesion_sin_sesion_no_falla(keyring_falso):
    auth.delete_token()  # no debe lanzar nada


def test_si_el_almacen_falla_se_trata_como_sin_sesion(monkeypatch):
    import sys

    class KeyringRoto:
        def get_password(self, *a):
            raise RuntimeError("almacén no disponible")

    monkeypatch.setitem(sys.modules, "keyring", KeyringRoto())
    assert auth.load_token() is None


def test_el_token_no_se_escribe_en_ningun_archivo(keyring_falso, tmp_path, monkeypatch):
    from src import config
    monkeypatch.setenv("VAIVEN_DATA_DIR", str(tmp_path))
    auth.save_token("gho_muy_secreto")
    config.Config(root_folder=str(tmp_path)).save()

    for archivo in tmp_path.rglob("*"):
        if archivo.is_file():
            assert "gho_muy_secreto" not in archivo.read_text(encoding="utf-8", errors="ignore")


# --- API de GitHub (secciones 5.2.6 y 8) -----------------------------------

def test_datos_del_usuario():
    sesion = SesionFalsa([RespuestaFalsa({
        "login": "jhon", "name": "Jhon", "avatar_url": "https://avatars/1",
    })])
    usuario = github_api.GitHubClient("gho_x", sesion).get_user()
    assert usuario.login == "jhon"
    assert usuario.display_name == "Jhon"
    assert usuario.avatar_url == "https://avatars/1"


def test_el_usuario_sin_nombre_muestra_el_login():
    sesion = SesionFalsa([RespuestaFalsa({"login": "jhon", "name": None})])
    assert github_api.GitHubClient("gho_x", sesion).get_user().display_name == "jhon"


def _repo(nombre, **extra):
    datos = {
        "name": nombre, "full_name": f"jhon/{nombre}",
        "clone_url": f"https://github.com/jhon/{nombre}.git",
        "private": False, "fork": False, "archived": False,
        "default_branch": "main", "owner": {"login": "jhon"},
    }
    datos.update(extra)
    return datos


def test_la_lista_de_repos_recorre_todas_las_paginas():
    pagina1 = [_repo(f"repo{i}") for i in range(100)]
    pagina2 = [_repo("ultimo")]
    sesion = SesionFalsa([RespuestaFalsa(pagina1), RespuestaFalsa(pagina2)])

    repos = github_api.GitHubClient("gho_x", sesion).list_repos()

    assert len(repos) == 101
    assert repos[-1].name == "ultimo"
    assert sesion.peticiones[0][1]["page"] == 1
    assert sesion.peticiones[1][1]["page"] == 2
    assert sesion.peticiones[0][1]["per_page"] == 100


def test_la_lista_incluye_privados_y_de_organizaciones():
    sesion = SesionFalsa([RespuestaFalsa([
        _repo("privado", private=True),
        _repo("de-la-empresa", owner={"login": "empresa"}, full_name="empresa/de-la-empresa"),
    ])])

    repos = github_api.GitHubClient("gho_x", sesion).list_repos()

    assert {r.name for r in repos} == {"privado", "de-la-empresa"}
    assert repos[0].private
    assert repos[1].owner == "empresa"
    assert "organization_member" in sesion.peticiones[0][1]["affiliation"]


def test_los_archivados_se_omiten_por_defecto():
    sesion = SesionFalsa([RespuestaFalsa([_repo("vivo"), _repo("viejo", archived=True)])])
    repos = github_api.GitHubClient("gho_x", sesion).list_repos()
    assert [r.name for r in repos] == ["vivo"]


def test_una_sesion_caducada_pide_volver_a_entrar():
    """Sección 5.3: si GitHub responde 401, se vuelve al login."""
    sesion = SesionFalsa([RespuestaFalsa({}, status_code=401)])
    with pytest.raises(SessionExpired) as error:
        github_api.GitHubClient("gho_viejo", sesion).get_user()
    assert "Vuelve a iniciar sesión" in str(error.value)


def test_comprobar_la_sesion_devuelve_falso_en_vez_de_romperse():
    sesion = SesionFalsa([RespuestaFalsa({}, status_code=401)])
    assert github_api.GitHubClient("gho_viejo", sesion).check() is False


def test_que_falta_por_clonar():
    remotos = [
        github_api.GitHubRepo("uno", "jhon/uno", "https://github.com/jhon/uno.git"),
        github_api.GitHubRepo("dos", "jhon/dos", "https://github.com/jhon/dos.git"),
    ]
    faltan = github_api.missing_locally(remotos, {"UNO"})
    assert [r.name for r in faltan] == ["dos"]


# --- login web directo: el usuario solo pulsa «Authorize» ------------------

@pytest.fixture(autouse=True)
def _client_secret(monkeypatch):
    monkeypatch.setattr(auth, "CLIENT_SECRET", "secreto-de-prueba")


def _puerto_libre() -> int:
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    puerto = s.getsockname()[1]
    s.close()
    return puerto


def test_la_direccion_de_github_lleva_todo_lo_necesario():
    import urllib.parse
    puerto = _puerto_libre()
    intento = auth.start_web_login(port=puerto)
    try:
        partes = urllib.parse.urlsplit(intento.url)
        consulta = urllib.parse.parse_qs(partes.query)
        assert f"{partes.scheme}://{partes.netloc}{partes.path}" == auth.AUTHORIZE_URL
        assert consulta["client_id"] == ["Ov23liDEPRUEBA"]
        assert consulta["scope"] == ["repo read:user"]
        assert consulta["redirect_uri"] == [f"http://127.0.0.1:{puerto}/vaiven/callback"]
        assert consulta["state"] == [intento.state]
        assert len(intento.state) >= 20      # aleatorio, no adivinable
    finally:
        intento.close()


def test_dos_intentos_no_comparten_el_state():
    a = auth.start_web_login(port=_puerto_libre())
    b = auth.start_web_login(port=_puerto_libre())
    try:
        assert a.state != b.state
    finally:
        a.close()
        b.close()


def _responder(url: str) -> None:
    """Simula la vuelta del navegador desde GitHub."""
    import urllib.request
    try:
        urllib.request.urlopen(url, timeout=5).read()
    except Exception:
        pass


def test_el_usuario_autoriza_y_vuelve_solo():
    import threading as th
    puerto = _puerto_libre()
    intento = auth.start_web_login(port=puerto)
    th.Timer(0.2, _responder, args=[
        f"http://127.0.0.1:{puerto}/vaiven/callback?code=abc123&state={intento.state}"
    ]).start()

    codigo = auth.wait_for_authorization(intento, timeout=10)

    assert codigo == "abc123"


def test_la_pagina_del_navegador_confirma_en_espanol():
    import threading as th
    import urllib.request
    puerto = _puerto_libre()
    intento = auth.start_web_login(port=puerto)
    paginas = []

    def visitar():
        try:
            paginas.append(urllib.request.urlopen(
                f"http://127.0.0.1:{puerto}/vaiven/callback?code=x&state={intento.state}",
                timeout=5,
            ).read().decode("utf-8"))
        except Exception as exc:
            paginas.append(f"fallo: {exc}")

    visitante = th.Timer(0.2, visitar)
    visitante.start()
    try:
        auth.wait_for_authorization(intento, timeout=10)
    finally:
        # El navegador simulado termina de leer en su propio hilo: hay que
        # esperarlo antes de mirar lo que recibió.
        visitante.join(10)

    assert paginas, "el navegador no llegó a recibir la página"
    assert "Sesión iniciada" in paginas[0]
    assert "volver a Vaivén" in paginas[0]


def test_un_state_que_no_coincide_se_ignora_y_se_sigue_esperando():
    """Otra página del navegador no puede colar una respuesta falsa, ni
    tampoco abortar el inicio de sesión visitando la dirección antes que
    GitHub (con un código, con un error o sin state)."""
    import threading as th
    puerto = _puerto_libre()
    intento = auth.start_web_login(port=puerto)
    base = f"http://127.0.0.1:{puerto}/vaiven/callback"

    def visitas():
        _responder(f"{base}?code=falso&state=inventado")
        _responder(f"{base}?error=access_denied")
        _responder(f"{base}?code=falso")
        _responder(f"{base}?code=bueno&state={intento.state}")

    th.Timer(0.2, visitas).start()

    assert auth.wait_for_authorization(intento, timeout=10) == "bueno"


def test_el_login_web_usa_pkce():
    import base64
    import hashlib
    import urllib.parse
    intento = auth.start_web_login(port=_puerto_libre())
    try:
        consulta = urllib.parse.parse_qs(urllib.parse.urlsplit(intento.url).query)
        esperado = base64.urlsafe_b64encode(
            hashlib.sha256(intento.verifier.encode()).digest()
        ).rstrip(b"=").decode()
        assert consulta["code_challenge_method"] == ["S256"]
        assert consulta["code_challenge"] == [esperado]
        assert 43 <= len(intento.verifier) <= 128
        assert intento.verifier not in intento.url
    finally:
        intento.close()


def test_el_verificador_pkce_se_envia_al_canjear():
    sesion = SesionFalsa([RespuestaFalsa({"access_token": "gho_x"})])
    auth.exchange_code("abc", sesion, verifier="verificador-secreto")
    _, datos = sesion.peticiones[0]
    assert datos["code_verifier"] == "verificador-secreto"


def test_las_credenciales_propias_solo_las_lee_su_dueno(tmp_path, monkeypatch):
    import os
    import stat
    monkeypatch.setenv("VAIVEN_DATA_DIR", str(tmp_path))
    destino = auth.save_credentials("id", "secreto")
    assert destino.read_text(encoding="utf-8").count("secreto") == 1
    if os.name != "nt":
        assert stat.S_IMODE(destino.stat().st_mode) == 0o600


def test_si_el_usuario_cancela_en_la_web():
    import threading as th
    puerto = _puerto_libre()
    intento = auth.start_web_login(port=puerto)
    th.Timer(0.2, _responder, args=[
        f"http://127.0.0.1:{puerto}/vaiven/callback?error=access_denied"
        f"&error_description=The+user+has+denied+your+application&state={intento.state}"
    ]).start()

    with pytest.raises(auth.AccessDenied):
        auth.wait_for_authorization(intento, timeout=10)


def test_si_nadie_autoriza_se_agota_el_tiempo():
    intento = auth.start_web_login(port=_puerto_libre())
    with pytest.raises(auth.CodeExpired) as error:
        auth.wait_for_authorization(intento, timeout=0)
    assert "Intentar de nuevo" in str(error.value)


def test_el_puerto_se_suelta_siempre():
    """Tras un intento fallido, el puerto queda libre para el siguiente."""
    puerto = _puerto_libre()
    intento = auth.start_web_login(port=puerto)
    with pytest.raises(auth.CodeExpired):
        auth.wait_for_authorization(intento, timeout=0)
    assert auth.web_login_available(puerto)


def test_si_el_puerto_esta_ocupado_se_avisa_para_usar_la_reserva():
    puerto = _puerto_libre()
    ocupando = auth.start_web_login(port=puerto)
    try:
        assert not auth.web_login_available(puerto)
        with pytest.raises(auth.PortUnavailable):
            auth.start_web_login(port=puerto)
    finally:
        ocupando.close()


def test_canjear_el_codigo_por_la_sesion():
    sesion = SesionFalsa([RespuestaFalsa({"access_token": "gho_sesion_web"})])

    token = auth.exchange_code("abc123", sesion, port=49732)

    url, datos = sesion.peticiones[0]
    assert url == auth.ACCESS_TOKEN_URL
    assert datos["code"] == "abc123"
    assert datos["client_secret"] == "secreto-de-prueba"
    assert datos["redirect_uri"] == "http://127.0.0.1:49732/vaiven/callback"
    assert token == "gho_sesion_web"


def test_si_github_no_devuelve_sesion_se_explica():
    sesion = SesionFalsa([RespuestaFalsa({
        "error": "bad_verification_code",
        "error_description": "El código ha caducado",
    })])
    with pytest.raises(auth.AuthError) as error:
        auth.exchange_code("viejo", sesion)
    assert "caducado" in str(error.value)


def test_sin_client_secret_el_mensaje_remite_al_readme(monkeypatch):
    monkeypatch.setattr(auth, "CLIENT_SECRET", "PONMEAQUIELCLIENTSECRET")
    assert not auth.client_secret_is_configured()
    with pytest.raises(auth.ClientIdMissing) as error:
        auth.start_web_login(port=_puerto_libre())
    assert "README" in str(error.value)


def test_el_login_por_codigo_sigue_funcionando_como_reserva():
    """El camino de reserva no se ha roto al añadir el login web."""
    sesion = SesionFalsa([RespuestaFalsa({
        "device_code": "dc", "user_code": "ABCD-1234",
        "verification_uri": auth.VERIFICATION_URL, "expires_in": 900, "interval": 1,
    })])
    codigo = auth.request_device_code(sesion)
    assert codigo.user_code == "ABCD-1234"


# --- qué repos de GitHub faltan en este equipo -----------------------------

@pytest.mark.parametrize("url", [
    "https://github.com/Jhon/Web.git",
    "https://github.com/jhon/web",
    "git@github.com:jhon/web.git",
    "ssh://git@github.com/jhon/web.git",
    "https://x-access-token:abc@github.com/jhon/web.git",
])
def test_la_clave_de_un_repo_no_depende_de_la_forma_de_la_direccion(url):
    assert github_api.repo_key(url) == "jhon/web"


def test_la_clave_de_algo_que_no_es_github_es_none():
    assert github_api.repo_key("https://gitlab.com/jhon/web.git") is None
    assert github_api.repo_key(None) is None


def test_una_carpeta_con_otro_nombre_cuenta_como_descargado():
    """«mi-web» apunta a jhon/web: no hay que ofrecer descargar «web» otra vez."""
    remotos = [
        github_api.GitHubRepo("web", "jhon/web", "https://github.com/jhon/web.git"),
        github_api.GitHubRepo("api", "jhon/api", "https://github.com/jhon/api.git"),
    ]
    faltan = github_api.missing_locally(
        remotos, {"mi-web"}, ["git@github.com:jhon/web.git", None]
    )
    assert [r.name for r in faltan] == ["api"]


def test_la_lista_de_github_trae_descripcion_y_fecha():
    sesion = SesionFalsa([RespuestaFalsa([{
        "name": "web", "full_name": "jhon/web", "clone_url": "https://github.com/jhon/web.git",
        "description": "Mi web", "pushed_at": "2026-09-20T10:00:00Z", "owner": {"login": "jhon"},
    }])])
    repos = github_api.GitHubClient("t", session=sesion).list_repos()
    assert repos[0].description == "Mi web"
    assert repos[0].pushed_at.year == 2026
