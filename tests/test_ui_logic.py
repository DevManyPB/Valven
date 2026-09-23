"""Fase 5 y 6 — la lógica de la interfaz, el arranque y el empaquetado.

No se abren ventanas: se prueba la lógica que vive junto a la interfaz
(filtros, fechas, plan de la vista previa) y todo lo de la fase 6.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src import startup, sync_engine
from src.analyzer import RepoState, RepoStatus
from src.main import SingleInstance, check_git
from src.ui.repo_list_view import (
    FILTER_ALL, FILTER_ATTENTION, FILTER_CHANGES, human_date, matches_filter,
)


def _estado(state: RepoState, **kwargs) -> RepoStatus:
    return RepoStatus(name="x", path=Path("/x"), state=state, **kwargs)


# --- filtros de la lista (sección 9.1) -------------------------------------

@pytest.mark.parametrize("state", list(RepoState))
def test_el_filtro_todos_muestra_todo(state):
    assert matches_filter(_estado(state), FILTER_ALL)


def test_el_filtro_de_atencion_muestra_lo_bloqueado():
    assert matches_filter(_estado(RepoState.DIVERGED), FILTER_ATTENTION)
    assert matches_filter(_estado(RepoState.CONFLICT), FILTER_ATTENTION)
    assert matches_filter(_estado(RepoState.NO_UPSTREAM), FILTER_ATTENTION)
    assert not matches_filter(_estado(RepoState.UP_TO_DATE), FILTER_ATTENTION)
    assert not matches_filter(_estado(RepoState.BEHIND), FILTER_ATTENTION)


def test_el_filtro_con_cambios_oculta_lo_que_esta_al_dia():
    assert not matches_filter(_estado(RepoState.UP_TO_DATE), FILTER_CHANGES)
    assert matches_filter(_estado(RepoState.BEHIND), FILTER_CHANGES)
    assert matches_filter(_estado(RepoState.AHEAD), FILTER_CHANGES)


# --- fechas en lenguaje cotidiano ------------------------------------------

def test_fechas_en_lenguaje_cotidiano():
    ahora = datetime.now(timezone.utc)
    assert human_date(None) == ""
    assert human_date(ahora) == "hace un momento"
    assert human_date(ahora - timedelta(minutes=20)) == "hace 20 minutos"
    assert human_date(ahora - timedelta(hours=1)) == "hace 1 hora"
    assert human_date(ahora - timedelta(hours=5)) == "hace 5 horas"
    assert human_date(ahora - timedelta(days=1, hours=2)) == "ayer"
    assert human_date(ahora - timedelta(days=4)) == "hace 4 días"
    assert "/" in human_date(ahora - timedelta(days=40))


def test_una_fecha_sin_zona_horaria_no_rompe_nada():
    assert human_date(datetime(2020, 1, 1)) != ""


# --- estado del diálogo de vista previa (sección 6.5) ----------------------

def test_la_confirmacion_se_bloquea_si_hay_secretos_seleccionados():
    status = _estado(RepoState.LOCAL_CHANGES)
    accion = sync_engine.PlannedAction(
        status, "push",
        warnings=[sync_engine.Warning("secreto", ".env", "contraseñas")],
    )
    plan = sync_engine.Plan(kind=sync_engine.PUSH, to_do=[accion])

    assert plan.has_secret_warnings
    accion.selected = False
    assert not plan.has_secret_warnings   # excluirlo desbloquea (6.5.4)


def test_la_pregunta_final_cuenta_lo_seleccionado():
    acciones = [
        sync_engine.PlannedAction(_estado(RepoState.AHEAD, ahead=1), "push") for _ in range(3)
    ]
    plan = sync_engine.Plan(kind=sync_engine.PUSH, to_do=acciones)
    assert plan.question() == "¿Seguro que quieres subir 3 proyectos?"
    acciones[0].selected = False
    assert plan.question() == "¿Seguro que quieres subir 2 proyectos?"


def test_un_archivo_grande_avisa_pero_no_bloquea():
    accion = sync_engine.PlannedAction(
        _estado(RepoState.LOCAL_CHANGES), "push",
        warnings=[sync_engine.Warning("archivo_grande", "video.mp4", "Ocupa 60 MB")],
    )
    plan = sync_engine.Plan(kind=sync_engine.PUSH, to_do=[accion])
    assert plan.warnings and not plan.has_secret_warnings


# --- las vistas se pueden importar sin pantalla ----------------------------

def test_todas_las_vistas_se_importan():
    """Un error de sintaxis o un import roto se detecta aquí, sin abrir nada."""
    import importlib
    for modulo in (
        "src.ui.theme", "src.ui.login_view", "src.ui.repo_list_view",
        "src.ui.preview_dialog", "src.ui.repo_detail_view",
        "src.ui.settings_view", "src.ui.app_window", "src.main",
    ):
        assert importlib.import_module(modulo) is not None


# --- instancia única (sección 10) ------------------------------------------

def test_solo_se_abre_una_copia():
    primera = SingleInstance(49733)
    segunda = SingleInstance(49733)
    try:
        assert primera.acquire() is True
        assert segunda.acquire() is False
    finally:
        primera.release()
        segunda.release()


def test_la_segunda_copia_avisa_a_la_primera():
    primera = SingleInstance(49734)
    assert primera.acquire()
    avisos = []
    primera.listen(lambda: avisos.append(True))
    try:
        SingleInstance(49734).notify_existing()
        import time
        for _ in range(50):
            if avisos:
                break
            time.sleep(0.02)
        assert avisos == [True]
    finally:
        primera.release()


def test_el_cerrojo_se_libera_al_cerrar():
    primera = SingleInstance(49735)
    assert primera.acquire()
    primera.release()
    segunda = SingleInstance(49735)
    assert segunda.acquire()
    segunda.release()


def test_se_comprueba_que_git_esta_instalado():
    """Sección 9.2.1: sin Git la app no continúa."""
    assert check_git() is not None


# --- iniciar con Windows (sección 10) --------------------------------------

def test_iniciar_con_windows_crea_y_borra_el_acceso_directo(tmp_path, monkeypatch):
    monkeypatch.setenv("VAIVEN_STARTUP_DIR", str(tmp_path))

    assert not startup.is_enabled()
    assert startup.enable()
    assert startup.is_enabled()
    assert list(tmp_path.iterdir())

    assert startup.disable()
    assert not startup.is_enabled()
    assert list(tmp_path.iterdir()) == []


def test_aplicar_la_preferencia_de_inicio(tmp_path, monkeypatch):
    monkeypatch.setenv("VAIVEN_STARTUP_DIR", str(tmp_path))
    startup.apply(True)
    assert startup.is_enabled()
    startup.apply(False)
    assert not startup.is_enabled()


def test_fuera_de_windows_no_falla(monkeypatch):
    monkeypatch.delenv("VAIVEN_STARTUP_DIR", raising=False)
    if os.name != "nt":
        assert startup.startup_dir() is None
        assert startup.enable() is False
        assert startup.disable() is False


# --- empaquetado (sección 10) ----------------------------------------------

RAIZ = Path(__file__).resolve().parents[1]


def test_existe_el_icono():
    icono = RAIZ / "assets" / "icon.ico"
    assert icono.exists()
    assert icono.read_bytes()[:4] == b"\x00\x00\x01\x00"   # cabecera .ico


def test_build_bat_genera_el_exe_como_pide_el_spec():
    contenido = (RAIZ / "build.bat").read_text(encoding="utf-8", errors="replace")
    assert "--onefile" in contenido
    assert "--windowed" in contenido
    assert "--name Vaiven" in contenido
    assert "icon.ico" in contenido
    assert "src\\main.py" in contenido


def test_build_bat_usa_saltos_de_linea_de_windows():
    assert b"\r\n" in (RAIZ / "build.bat").read_bytes()


def test_el_readme_explica_lo_que_pide_el_spec():
    texto = (RAIZ / "README.md").read_text(encoding="utf-8")
    assert "SmartScreen" in texto              # sección 10
    assert "Ejecutar de todas formas" in texto
    assert "Client ID" in texto                # sección 5.1
    assert "git-scm.com" in texto              # sección 9.2


def test_la_documentacion_del_proyecto_abierto_esta_completa():
    for archivo in ("LICENSE", "ARQUITECTURA.md", "CONTRIBUTING.md", "DECISIONES.md"):
        assert (RAIZ / archivo).exists(), archivo
    readme = (RAIZ / "README.md").read_text(encoding="utf-8")
    for enlace in ("ARQUITECTURA.md", "CONTRIBUTING.md", "DECISIONES.md", "LICENSE"):
        assert enlace in readme, f"el README no enlaza {enlace}"


def test_enable_device_flow_se_documenta_para_quien_bifurque():
    """Quien use su propia OAuth App necesita marcar esa casilla."""
    texto = (RAIZ / "CONTRIBUTING.md").read_text(encoding="utf-8")
    assert "Enable Device Flow" in texto


def test_ningun_secreto_en_el_repositorio():
    """Lo que se publica no puede contener credenciales."""
    from src import auth

    assert auth.PROJECT_CLIENT_ID, "el Client ID público debe venir en el código"
    fuente = (RAIZ / "src" / "auth.py").read_text(encoding="utf-8")
    assert 'CLIENT_SECRET = (' in fuente
    assert 'client_secret"' not in fuente.split("def save_credentials")[0].replace(
        'or _locales.get("client_secret")', ""
    )

    sospechosos = []
    for archivo in list(RAIZ.rglob("*.py")) + list(RAIZ.rglob("*.md")):
        if ".venv" in archivo.parts or ".git" in archivo.parts:
            continue
        for numero, linea in enumerate(archivo.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            # Un secreto de GitHub son 40 caracteres hexadecimales seguidos.
            import re
            if re.search(r"\b[0-9a-f]{40}\b", linea):
                sospechosos.append(f"{archivo.relative_to(RAIZ)}:{numero}")
    assert not sospechosos, f"posible secreto publicado en: {sospechosos}"


def test_el_gitignore_protege_las_credenciales():
    texto = (RAIZ / ".gitignore").read_text(encoding="utf-8")
    for patron in ("oauth.json", ".env", ".venv/"):
        assert patron in texto, patron


def test_requirements_incluye_todo_el_stack():
    texto = (RAIZ / "requirements.txt").read_text(encoding="utf-8").lower()
    for paquete in ("customtkinter", "requests", "keyring", "pyinstaller", "pytest"):
        assert paquete in texto


def test_la_estructura_de_archivos_es_la_del_spec():
    for ruta in (
        "src/main.py", "src/config.py", "src/auth.py", "src/github_api.py",
        "src/git_ops.py", "src/analyzer.py", "src/safety.py", "src/sync_engine.py",
        "src/startup.py", "src/logger.py",
        "src/ui/app_window.py", "src/ui/login_view.py", "src/ui/repo_list_view.py",
        "src/ui/preview_dialog.py", "src/ui/repo_detail_view.py", "src/ui/settings_view.py",
        "tests/test_analyzer.py", "tests/test_safety.py", "tests/test_sync_scenarios.py",
        "build.bat", "README.md", "SPEC.md", "DECISIONES.md", "requirements.txt",
        "assets/icon.ico",
    ):
        assert (RAIZ / ruta).exists(), ruta


# --- la app no usa IA (sección 2) ------------------------------------------

def test_la_aplicacion_no_depende_de_ninguna_ia():
    prohibidos = ("anthropic", "openai", "claude")
    for archivo in (RAIZ / "src").rglob("*.py"):
        texto = archivo.read_text(encoding="utf-8").lower()
        for palabra in prohibidos:
            assert f"import {palabra}" not in texto, archivo
    requisitos = (RAIZ / "requirements.txt").read_text(encoding="utf-8").lower()
    assert not any(p in requisitos for p in prohibidos)


# --- los hilos de fondo no rompen nada si se cierra la ventana -------------

def test_un_aviso_a_una_ventana_cerrada_se_descarta():
    """Si el usuario cierra Vaivén mientras hay una operación en marcha, el
    hilo de fondo no debe reventar al volver a la interfaz."""
    from src.ui import call_on_ui_thread

    class VentanaCerrada:
        def after(self, *_args):
            raise RuntimeError("main thread is not in main loop")

    assert call_on_ui_thread(VentanaCerrada(), lambda: None) is False


def test_un_aviso_a_una_ventana_viva_se_entrega():
    from src.ui import call_on_ui_thread

    entregados = []

    class VentanaViva:
        def after(self, _retardo, callback, *args):
            entregados.append((callback, args))

    assert call_on_ui_thread(VentanaViva(), print, "hola") is True
    assert entregados[0][1] == ("hola",)


def test_ninguna_vista_llama_a_after_sin_proteccion():
    """Toda vuelta al hilo de la interfaz pasa por call_on_ui_thread."""
    for archivo in (RAIZ / "src" / "ui").glob("*.py"):
        texto = archivo.read_text(encoding="utf-8")
        assert "self.after(0" not in texto, archivo


def test_reabrir_enseguida_no_deja_la_app_sin_arrancar():
    """Un puerto recién cerrado queda unos segundos en TIME_WAIT. Si eso
    impidiera volver a ocuparlo, cerrar Vaivén y abrirlo de nuevo dejaría la
    app sin arrancar, creyendo que ya hay otra copia."""
    import socket

    primera = SingleInstance(49736)
    assert primera.acquire()
    # Una conexión real y cerrada es lo que provoca el TIME_WAIT.
    with socket.create_connection(("127.0.0.1", 49736), timeout=2):
        pass
    primera._socket.accept()[0].close()
    primera.release()

    segunda = SingleInstance(49736)
    try:
        assert segunda.acquire(), "el puerto no se pudo reutilizar tras cerrar"
    finally:
        segunda.release()


# --- una línea larguísima no debe tumbar la ventana ------------------------

def test_una_linea_enorme_se_recorta():
    """Tk reserva un mapa de píxeles tan ancho como la línea: un JSON
    minificado o un package-lock.json congelaban la ventana y podían llegar
    a tumbarla con un error del servidor gráfico."""
    from src.ui import MAX_ANCHO_LINEA, texto_seguro

    resultado = texto_seguro("y" * 2_000_000)

    assert len(resultado) < MAX_ANCHO_LINEA + 100
    assert "línea recortada" in resultado
    assert "2,000,000 caracteres" in resultado


def test_un_texto_normal_no_se_toca():
    from src.ui import texto_seguro

    original = "diff --git a/x.py b/x.py\n+una línea\n-otra línea"
    assert texto_seguro(original) == original


def test_se_limita_tambien_el_numero_de_lineas():
    from src.ui import MAX_LINEAS, texto_seguro

    resultado = texto_seguro("\n".join(f"línea {i}" for i in range(MAX_LINEAS + 500)))

    assert resultado.count("\n") <= MAX_LINEAS + 3
    assert "500 líneas más" in resultado
    assert "VS Code" in resultado


def test_el_texto_vacio_no_rompe_nada():
    from src.ui import texto_seguro
    assert texto_seguro("") == ""


def test_cada_linea_se_recorta_por_separado():
    from src.ui import texto_seguro

    resultado = texto_seguro("corta\n" + "x" * 10_000 + "\notra corta")
    lineas = resultado.splitlines()
    assert lineas[0] == "corta"
    assert "línea recortada" in lineas[1]
    assert lineas[2] == "otra corta"


def test_las_vistas_que_muestran_texto_de_git_lo_acotan():
    """Todo lo que venga de git y acabe en un widget de texto pasa el filtro."""
    for nombre in ("repo_detail_view.py", "preview_dialog.py"):
        texto = (RAIZ / "src" / "ui" / nombre).read_text(encoding="utf-8")
        for linea in texto.splitlines():
            if ".insert(" in linea and "texto_seguro" not in linea:
                # Solo se permiten textos fijos escritos por nosotros.
                assert '"' in linea.split(".insert(")[1], f"{nombre}: {linea.strip()}"


def test_ninguna_vista_recorre_el_disco_en_el_hilo_de_la_interfaz():
    """discover_repos puede tardar; nunca debe llamarse en el hilo de la UI."""
    for archivo in (RAIZ / "src" / "ui").glob("*.py"):
        for numero, linea in enumerate(archivo.read_text(encoding="utf-8").splitlines(), 1):
            if "discover_repos" in linea and "def " not in linea:
                contexto = archivo.read_text(encoding="utf-8")
                assert "threading.Thread" in contexto, f"{archivo.name}:{numero}"


# --- el avance del análisis no puede desbordar la barra de estado ----------

def test_el_avance_entrega_un_nombre_y_no_el_estado_entero(sandbox):
    """Entregar aquí el RepoStatus completo hacía que la barra de estado
    intentara dibujar su representación —más de 70.000 caracteres en un
    proyecto con cientos de archivos— y tumbaba la aplicación."""
    from src.analyzer import analyze_all

    recibido = []
    analyze_all([sandbox.a, sandbox.b], fetch=False,
                on_progress=lambda hechos, total, nombre: recibido.append(nombre))

    assert sorted(recibido) == ["equipo-a", "equipo-b"]
    for nombre in recibido:
        assert isinstance(nombre, str)
        assert len(nombre) < 100


def test_los_dos_motores_informan_del_avance_igual(sandbox):
    """analyze_all y sync_engine.execute deben tener el mismo contrato."""
    from src import sync_engine
    from src.analyzer import analyze_all

    del_analisis, de_la_ejecucion = [], []
    analyze_all([sandbox.b], fetch=False,
                on_progress=lambda h, t, n: del_analisis.append(type(n)))
    sync_engine.execute(
        sync_engine.plan_push([], "PC-MESA"), "PC-MESA",
        on_progress=lambda h, t, n: de_la_ejecucion.append(type(n)),
    )
    assert del_analisis == [str]
    assert de_la_ejecucion == [type(None)]   # el último aviso no lleva nombre


def test_una_etiqueta_nunca_dibuja_una_linea_desmedida():
    from src.ui import MAX_ETIQUETA, etiqueta_segura

    assert etiqueta_segura("corto") == "corto"
    largo = etiqueta_segura("x" * 100_000)
    assert len(largo) == MAX_ETIQUETA + 1
    assert largo.endswith("…")


def test_la_etiqueta_aplana_los_saltos_de_linea():
    """Una etiqueta de una línea no debe recibir texto multilínea."""
    from src.ui import etiqueta_segura
    assert etiqueta_segura("una\nfrase   con\n\nhuecos") == "una frase con huecos"


def test_la_etiqueta_acepta_cualquier_objeto():
    from src.ui import etiqueta_segura
    from src.analyzer import RepoState, RepoStatus

    estado = RepoStatus(name="x", path=Path("/x"), state=RepoState.UP_TO_DATE)
    assert len(etiqueta_segura(estado)) <= 161


def test_la_barra_de_estado_solo_se_escribe_desde_un_sitio():
    """Todo pasa por _estado(), que acota el texto. Si alguien escribe en la
    etiqueta directamente, se salta el filtro y la app puede caerse."""
    texto = (RAIZ / "src" / "ui" / "app_window.py").read_text(encoding="utf-8")
    escrituras = [
        numero for numero, linea in enumerate(texto.splitlines(), 1)
        if "estado_label.configure(" in linea
    ]
    assert len(escrituras) == 1, f"escrituras directas en las líneas {escrituras}"
    assert "def _estado(" in texto


# --- utilidades de la interfaz ---------------------------------------------

def test_la_ruta_corta_conserva_el_final():
    from src.ui import ruta_corta
    larga = "C:\\Users\\jhon\\Documentos\\Proyectos\\trabajo\\clientes\\2026\\tienda-online"
    corta = ruta_corta(larga, maximo=40)
    assert corta.startswith("…\\") and corta.endswith("tienda-online")
    assert len(corta) <= 40
    assert ruta_corta("/home/jhon/web", maximo=40) == "/home/jhon/web"


def test_el_avatar_es_redondo_y_del_tamano_pedido():
    from PIL import Image
    from src.ui import imagen_redonda
    avatar = imagen_redonda(Image.new("RGB", (100, 60), "red"), 40)
    assert avatar.size == (40, 40)
    assert avatar.getpixel((0, 0))[3] == 0          # esquina transparente
    assert avatar.getpixel((20, 20))[3] == 255      # centro opaco


def test_sin_direccion_no_hay_avatar():
    from src.ui import descargar_avatar
    assert descargar_avatar(None) is None
    assert descargar_avatar("") is None


def test_los_botones_con_borde_siguen_al_tema():
    """En el tema claro, el texto blanco de CTk sobre fondo claro era invisible."""
    from src.ui import theme
    estilo = theme.secundario()
    assert estilo["fg_color"] == "transparent"
    assert estilo["text_color"] == theme.TEXT
    assert theme.secundario(text_color=theme.DANGER)["text_color"] == theme.DANGER
    for archivo in (RAIZ / "src" / "ui").glob("*.py"):
        texto = archivo.read_text(encoding="utf-8")
        assert 'fg_color="transparent", border_width' not in texto, archivo.name


def test_el_filtro_sin_descargar_no_muestra_proyectos_locales(sandbox):
    from src.analyzer import analyze_repo
    from src.ui.repo_list_view import FILTER_ALL, FILTER_REMOTE, FILTERS, matches_filter
    estado = analyze_repo(sandbox.b)
    assert FILTER_REMOTE in FILTERS
    assert not matches_filter(estado, FILTER_REMOTE)
    assert matches_filter(estado, FILTER_ALL)
