"""Ventana principal de Vaivén (sección 9.1 de SPEC.md).

Regla de oro de esta clase: **nada bloquea la interfaz**. Todo lo que hable
con Git o con la red se ejecuta en un hilo de fondo y vuelve al hilo de la
ventana con ``after``, que es la única forma segura de tocar widgets de Tk.
"""

from __future__ import annotations

import threading
from pathlib import Path

import customtkinter as ctk

from .. import analyzer, auth, config as config_module, github_api, safety, startup, sync_engine
from ..analyzer import RepoStatus
from ..logger import get_logger
from . import call_on_ui_thread, etiqueta_segura, theme
from .login_view import LoginView
from .preview_dialog import ask
from .repo_detail_view import RepoDetailView
from .repo_list_view import RepoListView
from .settings_view import SettingsView

log = get_logger("ui.app")


class VaivenApp(ctk.CTk):
    """La ventana que ve el usuario."""

    def __init__(self, config: config_module.Config | None = None) -> None:
        super().__init__()
        self.config_data = config or config_module.Config.load()
        self.token: str | None = None
        self.user: github_api.GitHubUser | None = None
        self.statuses: list[RepoStatus] = []
        self._ocupado = False

        ctk.set_appearance_mode(self.config_data.theme)
        ctk.set_default_color_theme("blue")

        self.title("Vaivén")
        self.geometry("980x720")
        self.minsize(760, 560)
        self._poner_icono()

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.contenedor = ctk.CTkFrame(self, fg_color="transparent")
        self.contenedor.grid(row=0, column=0, sticky="nsew")
        self.contenedor.grid_columnconfigure(0, weight=1)
        self.contenedor.grid_rowconfigure(0, weight=1)

        self.login = None
        self.principal = None

        self.protocol("WM_DELETE_WINDOW", self._cerrar)

        self.token = auth.load_token()
        if self.token:
            self._mostrar_principal()
        else:
            self._mostrar_login()

    def _cerrar(self) -> None:
        """Suelta lo que haya quedado abierto antes de irse."""
        if self.login is not None:
            self.login.stop()
        self.destroy()

    def _poner_icono(self) -> None:
        icono = config_module.icon_path()
        if icono.exists():
            try:
                self.iconbitmap(str(icono))
            except Exception:
                pass

    # --- cambio de pantalla -----------------------------------------------

    def _limpiar(self) -> None:
        # El login puede tener un puerto abierto esperando a GitHub.
        if self.login is not None:
            self.login.stop()
            self.login = None
        for hijo in self.contenedor.winfo_children():
            hijo.destroy()

    def _mostrar_login(self) -> None:
        self._limpiar()
        self.login = LoginView(self.contenedor, on_success=self._tras_login)
        self.login.grid(row=0, column=0, sticky="nsew", padx=40, pady=40)

    def _tras_login(self, token: str) -> None:
        self.token = token
        self._mostrar_principal()

    def _cerrar_sesion(self) -> None:
        self.token = None
        self.user = None
        self.statuses = []
        self._mostrar_login()

    # --- pantalla principal -----------------------------------------------

    def _mostrar_principal(self) -> None:
        self._limpiar()
        self.login = None
        marco = ctk.CTkFrame(self.contenedor, fg_color="transparent")
        marco.grid(row=0, column=0, sticky="nsew")
        marco.grid_columnconfigure(0, weight=1)
        marco.grid_rowconfigure(2, weight=1)
        self.principal = marco

        self._barra_superior(marco)
        self._acciones(marco)

        self.lista = RepoListView(marco, on_select=self._abrir_detalle)
        self.lista.grid(row=2, column=0, sticky="nsew", padx=24, pady=(4, 4))

        self._barra_inferior(marco)

        self._cargar_usuario()
        if self.config_data.root_folder:
            if self.config_data.check_on_open:
                self.after(300, self.revisar_estado)
        else:
            self.after(400, self._pedir_carpeta)

    def _barra_superior(self, padre) -> None:
        barra = ctk.CTkFrame(padre, fg_color="transparent")
        barra.grid(row=0, column=0, sticky="ew", padx=24, pady=(18, 6))
        barra.grid_columnconfigure(1, weight=1)

        self.avatar = ctk.CTkLabel(
            barra, text="●", width=34, font=ctk.CTkFont(size=26),
            text_color=theme.TEXT_MUTED,
        )
        self.avatar.grid(row=0, column=0, rowspan=2, padx=(0, 10))

        self.usuario_label = ctk.CTkLabel(
            barra, text="Cargando…", anchor="w", font=ctk.CTkFont(size=15, weight="bold"),
        )
        self.usuario_label.grid(row=0, column=1, sticky="w")

        self.equipo_label = ctk.CTkLabel(
            barra, text=f"Este equipo: {self.config_data.team_name}", anchor="w",
            font=ctk.CTkFont(size=12), text_color=theme.TEXT_MUTED,
        )
        self.equipo_label.grid(row=1, column=1, sticky="w")

        ctk.CTkButton(
            barra, text="⚙  Ajustes", width=110, height=34,
            fg_color="transparent", border_width=1, command=self._abrir_ajustes,
        ).grid(row=0, column=2, rowspan=2, sticky="e")

    def _acciones(self, padre) -> None:
        marco = ctk.CTkFrame(padre, fg_color="transparent")
        marco.grid(row=1, column=0, sticky="ew", padx=24, pady=(10, 10))
        marco.grid_columnconfigure((0, 1), weight=1)

        self.boton_subir = ctk.CTkButton(
            marco, text="⬆  Subir todo", height=64,
            font=ctk.CTkFont(size=17, weight="bold"),
            fg_color=theme.PUSH_COLOR, hover_color=theme.PUSH_HOVER,
            command=self.subir_todo,
        )
        self.boton_subir.grid(row=0, column=0, sticky="ew", padx=(0, 6))

        self.boton_sincronizar = ctk.CTkButton(
            marco, text="⬇  Sincronizar todo", height=64,
            font=ctk.CTkFont(size=17, weight="bold"),
            fg_color=theme.SYNC_COLOR, hover_color=theme.SYNC_HOVER,
            command=self.sincronizar_todo,
        )
        self.boton_sincronizar.grid(row=0, column=1, sticky="ew", padx=(6, 6))

        self.boton_revisar = ctk.CTkButton(
            marco, text="↻  Revisar estado", height=64, width=170,
            fg_color="transparent", border_width=1, command=self.revisar_estado,
        )
        self.boton_revisar.grid(row=0, column=2)

    def _barra_inferior(self, padre) -> None:
        barra = ctk.CTkFrame(padre, fg_color="transparent")
        barra.grid(row=3, column=0, sticky="ew", padx=24, pady=(4, 16))
        barra.grid_columnconfigure(0, weight=1)

        self.estado_label = ctk.CTkLabel(
            barra, text="", anchor="w", font=ctk.CTkFont(size=12),
            text_color=theme.TEXT_MUTED, wraplength=760, justify="left",
        )
        self.estado_label.grid(row=0, column=0, sticky="ew")

        self.progreso = ctk.CTkProgressBar(barra, height=6)
        self.progreso.set(0)

    # --- utilidades de hilos ----------------------------------------------

    def _bloquear(self, ocupado: bool, texto: str = "") -> None:
        """Evita que se lance otra operación mientras hay una en marcha."""
        self._ocupado = ocupado
        estado = "disabled" if ocupado else "normal"
        for boton in (self.boton_subir, self.boton_sincronizar, self.boton_revisar):
            boton.configure(state=estado)
        if ocupado:
            self.progreso.grid(row=1, column=0, sticky="ew", pady=(6, 0))
            self.progreso.configure(mode="indeterminate")
            self.progreso.start()
        else:
            self.progreso.stop()
            self.progreso.grid_forget()
        if texto:
            self._estado(texto)

    def _estado(self, texto, *, color=None) -> None:
        """Único punto de escritura de la barra de estado.

        Pasa siempre por ``etiqueta_segura``: es una etiqueta de una sola
        línea, y basta con que le llegue algo inesperadamente largo para que
        Tk intente dibujar un mapa de píxeles imposible y la aplicación se
        caiga. Ninguna otra parte de la ventana debe tocar ``estado_label``.
        """
        self.estado_label.configure(
            text=etiqueta_segura(texto), text_color=color or theme.TEXT_MUTED
        )

    def _en_segundo_plano(self, trabajo, al_terminar, texto: str) -> None:
        if self._ocupado:
            return
        self._bloquear(True, texto)

        def hilo() -> None:
            try:
                resultado = trabajo()
                error = None
            except Exception as exc:
                log.exception("fallo en «%s»", texto)
                resultado, error = None, exc
            call_on_ui_thread(self, self._recoger, al_terminar, resultado, error)

        threading.Thread(target=hilo, daemon=True).start()

    def _recoger(self, al_terminar, resultado, error) -> None:
        self._bloquear(False)
        if error is not None:
            self._estado(error, color=theme.DANGER)
            return
        al_terminar(resultado)

    def _avance(self, hechos: int, total: int, nombre) -> None:
        etiqueta = etiqueta_segura(nombre, maximo=60) if nombre else ""
        texto = f"({hechos}/{total}) {etiqueta}…" if etiqueta else f"({hechos}/{total}) terminando…"
        call_on_ui_thread(self, self._estado, texto)

    # --- usuario y carpeta -------------------------------------------------

    def _cargar_usuario(self) -> None:
        if not self.token:
            return

        def trabajo():
            return github_api.GitHubClient(self.token).get_user()

        def hilo() -> None:
            try:
                usuario = trabajo()
            except auth.SessionExpired:
                call_on_ui_thread(self, self._sesion_caducada)
                return
            except Exception as exc:
                log.warning("no se pudo leer el usuario de GitHub: %s", exc)
                call_on_ui_thread(self, lambda: self.usuario_label.configure(text="Sin conexión con GitHub"))
                return
            call_on_ui_thread(self, self._pintar_usuario, usuario)

        threading.Thread(target=hilo, daemon=True).start()

    def _pintar_usuario(self, usuario: github_api.GitHubUser) -> None:
        self.user = usuario
        self.usuario_label.configure(text=usuario.display_name)
        self.avatar.configure(text="●", text_color=theme.SUCCESS)

    def _sesion_caducada(self) -> None:
        """Sección 5.3: si GitHub rechaza la sesión, se vuelve al login."""
        auth.delete_token()
        self._cerrar_sesion()
        if self.login:
            self.login._mostrar_error(
                "Tu sesión de GitHub ha caducado. Vuelve a iniciar sesión, no se ha perdido nada."
            )

    def _pedir_carpeta(self) -> None:
        self._estado("Todavía no has elegido tu carpeta de proyectos. Ábrela desde Ajustes.")
        self._abrir_ajustes()

    def _abrir_ajustes(self) -> None:
        SettingsView(
            self, self.config_data,
            on_saved=self._ajustes_guardados, on_logout=self._cerrar_sesion,
        )

    def _ajustes_guardados(self, datos: config_module.Config) -> None:
        self.config_data = datos
        self.equipo_label.configure(text=f"Este equipo: {datos.team_name}")
        self.revisar_estado()

    # --- análisis -----------------------------------------------------------

    def _repos_locales(self) -> list[Path]:
        raiz = self.config_data.root_folder
        if not raiz:
            return []
        return [
            ruta for ruta in analyzer.discover_repos(raiz)
            if not self.config_data.is_excluded(ruta.name)
        ]

    def revisar_estado(self) -> None:
        """Botón «↻ Revisar estado» (sección 9.1)."""
        rutas = self._repos_locales()
        if not rutas:
            self.lista.set_statuses([])
            self._estado(
                "No se han encontrado proyectos en esa carpeta."
                if self.config_data.root_folder else
                "Elige tu carpeta de proyectos en Ajustes."
            )
            return

        self._en_segundo_plano(
            lambda: analyzer.analyze_all(rutas, token=self.token, on_progress=self._avance),
            self._estado_listo,
            f"Revisando {len(rutas)} proyectos…",
        )

    def _estado_listo(self, statuses: list[RepoStatus]) -> None:
        self.statuses = statuses
        self.lista.set_statuses(statuses)
        atencion = [s for s in statuses if s.is_blocked]
        pendientes = [s for s in statuses if s.behind]
        partes = []
        if pendientes:
            partes.append(
                f"{len(pendientes)} proyecto{'s' if len(pendientes) != 1 else ''} "
                f"tiene{'n' if len(pendientes) != 1 else ''} cambios nuevos en GitHub"
            )
        if atencion:
            partes.append(
                f"{len(atencion)} necesita{'n' if len(atencion) != 1 else ''} tu atención"
            )
        self._estado(
            " · ".join(partes) or "Todo está al día.",
            color=theme.DANGER if atencion else theme.TEXT_MUTED,
        )
        safety.cleanup()

    def _abrir_detalle(self, status: RepoStatus) -> None:
        RepoDetailView(self, status, self.config_data.team_name, self.token, self.revisar_estado)

    # --- acciones masivas ---------------------------------------------------

    def subir_todo(self) -> None:
        """Botón «⬆ Subir todo» (secciones 6.5 y 7)."""
        identidad = sync_engine.ensure_git_identity()
        if identidad is None and not self._pedir_identidad():
            return

        rutas = self._repos_locales()
        if not rutas:
            self._estado("No hay proyectos que subir.")
            return

        self._en_segundo_plano(
            lambda: sync_engine.plan_push(
                analyzer.analyze_all(rutas, token=self.token, on_progress=self._avance),
                self.config_data.team_name,
            ),
            self._confirmar_y_ejecutar,
            "Mirando qué hay que subir…",
        )

    def sincronizar_todo(self) -> None:
        """Botón «⬇ Sincronizar todo» (secciones 6.5 y 8)."""
        rutas = self._repos_locales()
        if not rutas and not self.config_data.root_folder:
            self._estado("Elige tu carpeta de proyectos en Ajustes.")
            return

        def trabajo():
            statuses = analyzer.analyze_all(rutas, token=self.token, on_progress=self._avance)
            faltan = self._buscar_lo_que_falta(statuses)
            return sync_engine.plan_sync(statuses, missing=faltan)

        self._en_segundo_plano(trabajo, self._confirmar_y_ejecutar, "Mirando qué hay en GitHub…")

    def _buscar_lo_que_falta(self, statuses: list[RepoStatus]) -> list[sync_engine.MissingRepo]:
        """Proyectos que están en GitHub y no en este equipo (sección 8)."""
        if not self.token:
            return []
        try:
            remotos = github_api.GitHubClient(self.token).list_repos()
        except Exception as exc:
            log.info("no se pudo consultar la lista de GitHub: %s", exc)
            return []
        locales = {s.name for s in statuses}
        return [
            sync_engine.MissingRepo(repo.name, repo.clone_url, repo.private)
            for repo in github_api.missing_locally(remotos, locales)
            if not self.config_data.is_excluded(repo.name)
        ]

    def _confirmar_y_ejecutar(self, plan: sync_engine.Plan) -> None:
        """Vista previa obligatoria antes de tocar nada (6.5)."""
        if not ask(self, plan, self.config_data.team_name):
            self._estado("Cancelado. No se ha tocado nada.")
            return

        self._en_segundo_plano(
            lambda: sync_engine.execute(
                plan, self.config_data.team_name, token=self.token,
                root=self.config_data.root_folder, on_progress=self._avance,
            ),
            self._mostrar_resumen,
            "Trabajando…",
        )

    def _mostrar_resumen(self, informe: sync_engine.RunReport) -> None:
        """Resumen final de la sección 6.5.6."""
        ResultDialog(self, informe)
        self._estado(
            informe.summary(),
            color=theme.DANGER if informe.failed else theme.SUCCESS,
        )
        self.revisar_estado()

    # --- identidad de Git (7.1) --------------------------------------------

    def _pedir_identidad(self) -> bool:
        dialogo = IdentityDialog(self)
        self.wait_window(dialogo)
        if not dialogo.result:
            self._estado("Git necesita saber tu nombre y tu correo antes de guardar cambios.")
            return False
        sync_engine.ensure_git_identity(*dialogo.result)
        return True


class IdentityDialog(ctk.CTkToplevel):
    """Pide el nombre y el correo para Git, una sola vez (sección 7.1)."""

    def __init__(self, master):
        super().__init__(master)
        self.result: tuple[str, str] | None = None
        self.title("Solo una vez")
        self.geometry("460x280")
        self.transient(master)
        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self, text="¿Cómo quieres que aparezcan tus cambios?",
            font=ctk.CTkFont(size=16, weight="bold"), wraplength=400,
        ).grid(row=0, column=0, padx=24, pady=(24, 4), sticky="w")
        ctk.CTkLabel(
            self, text="Git guarda tu nombre y tu correo junto a cada cambio. "
                       "Solo hace falta configurarlo una vez.",
            font=ctk.CTkFont(size=12), text_color=theme.TEXT_MUTED,
            wraplength=400, justify="left", anchor="w",
        ).grid(row=1, column=0, padx=24, sticky="w")

        self.nombre = ctk.CTkEntry(self, placeholder_text="Tu nombre", height=36)
        self.nombre.grid(row=2, column=0, padx=24, pady=(16, 8), sticky="ew")
        self.correo = ctk.CTkEntry(self, placeholder_text="Tu correo de GitHub", height=36)
        self.correo.grid(row=3, column=0, padx=24, pady=(0, 16), sticky="ew")

        botones = ctk.CTkFrame(self, fg_color="transparent")
        botones.grid(row=4, column=0, padx=24, pady=(0, 20), sticky="e")
        ctk.CTkButton(
            botones, text="Cancelar", width=100, fg_color="transparent",
            border_width=1, command=self.destroy,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(botones, text="Guardar", width=120, command=self._guardar).pack(side="left")

        self.after(120, lambda: (self.focus_force(), self.nombre.focus()))

    def _guardar(self) -> None:
        nombre, correo = self.nombre.get().strip(), self.correo.get().strip()
        if nombre and "@" in correo:
            self.result = (nombre, correo)
            self.destroy()


class ResultDialog(ctk.CTkToplevel):
    """Resumen de lo ocurrido, con enlace al registro (sección 6.5.6)."""

    def __init__(self, master, informe: sync_engine.RunReport):
        super().__init__(master)
        self.title("Resumen")
        self.geometry("620x480")
        self.transient(master)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            self, text=informe.summary(), anchor="w",
            font=ctk.CTkFont(size=17, weight="bold"),
        ).grid(row=0, column=0, sticky="w", padx=20, pady=(20, 8))

        cuerpo = ctk.CTkScrollableFrame(self, fg_color="transparent")
        cuerpo.grid(row=1, column=0, sticky="nsew", padx=16)
        cuerpo.grid_columnconfigure(0, weight=1)

        for fila, resultado in enumerate(informe.results):
            marco = ctk.CTkFrame(cuerpo, fg_color=theme.CARD_BG, corner_radius=8)
            marco.grid(row=fila, column=0, sticky="ew", pady=3)
            marco.grid_columnconfigure(1, weight=1)
            icono = "✓" if resultado.ok else ("–" if resultado.skipped else "✕")
            color = theme.SUCCESS if resultado.ok else (
                theme.TEXT_MUTED if resultado.skipped else theme.DANGER
            )
            ctk.CTkLabel(
                marco, text=icono, width=24, font=ctk.CTkFont(size=16, weight="bold"),
                text_color=color,
            ).grid(row=0, column=0, rowspan=2, padx=(12, 6), pady=8)
            ctk.CTkLabel(
                marco, text=resultado.name, anchor="w",
                font=ctk.CTkFont(size=13, weight="bold"),
            ).grid(row=0, column=1, sticky="w", pady=(8, 0))
            ctk.CTkLabel(
                marco, text=resultado.message, anchor="w", wraplength=480, justify="left",
                font=ctk.CTkFont(size=12), text_color=theme.TEXT_MUTED,
            ).grid(row=1, column=1, sticky="w", pady=(0, 8))

        pie = ctk.CTkFrame(self, fg_color="transparent")
        pie.grid(row=2, column=0, sticky="ew", padx=20, pady=(8, 18))
        ctk.CTkButton(
            pie, text="Ver el registro", width=140, fg_color="transparent",
            border_width=1, command=self._abrir_log,
        ).pack(side="left")
        ctk.CTkButton(pie, text="Entendido", width=120, command=self.destroy).pack(side="right")

        self.after(120, lambda: self.focus_force())

    def _abrir_log(self) -> None:
        import os
        import subprocess

        from ..logger import log_file_path

        try:
            if os.name == "nt":
                os.startfile(str(log_file_path()))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", str(log_file_path())])
        except Exception as exc:
            log.warning("no se pudo abrir el registro: %s", exc)
