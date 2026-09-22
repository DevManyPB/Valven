"""Pantalla de inicio de sesión con GitHub (sección 5 de SPEC.md).

Lo normal es que el usuario solo vea dos cosas: un botón aquí y el botón
«Authorize» de GitHub en su navegador. No hay nada que copiar ni pegar.

Si el puerto que necesita ese camino no está disponible —cortafuegos, otro
programa ocupándolo—, la pantalla cambia sola al inicio de sesión por
código, que funciona en cualquier circunstancia.
"""

from __future__ import annotations

import threading

import customtkinter as ctk

from .. import auth, config as config_module
from ..logger import get_logger
from . import call_on_ui_thread, theme

log = get_logger("ui.login")


class LoginView(ctk.CTkFrame):
    """Pide un código, abre el navegador y espera a GitHub en segundo plano."""

    def __init__(self, master, on_success, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.on_success = on_success
        self._cancelar = threading.Event()
        self._codigo: auth.DeviceCode | None = None
        self._intento: auth.WebLogin | None = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.grid_rowconfigure(9, weight=1)

        logo = self._logo()
        ctk.CTkLabel(
            self, text="Vaivén", font=ctk.CTkFont(size=40, weight="bold"),
            image=logo, compound="top",
        ).grid(row=1, column=0, pady=(0, 4))
        ctk.CTkLabel(
            self,
            text="Mantén tus proyectos iguales en el portátil y en el PC de mesa",
            font=ctk.CTkFont(size=14), text_color=theme.TEXT_MUTED,
        ).grid(row=2, column=0, pady=(0, 28))

        self.boton = ctk.CTkButton(
            self, text="Iniciar sesión con GitHub", height=46, width=280,
            font=ctk.CTkFont(size=15, weight="bold"), command=self.start,
        )
        self.boton.grid(row=3, column=0, pady=(0, 18))

        self.instruccion = ctk.CTkLabel(self, text="", font=ctk.CTkFont(size=14), wraplength=460)
        self.instruccion.grid(row=4, column=0, pady=(0, 6))

        self.codigo_label = ctk.CTkLabel(
            self, text="", font=ctk.CTkFont(size=34, weight="bold", family="Consolas"),
        )
        self.codigo_label.grid(row=5, column=0, pady=(0, 6))

        self.aviso_copiado = ctk.CTkLabel(
            self, text="", font=ctk.CTkFont(size=12), text_color=theme.TEXT_MUTED,
        )
        self.aviso_copiado.grid(row=6, column=0)

        self.acciones = ctk.CTkFrame(self, fg_color="transparent")
        self.acciones.grid(row=7, column=0, pady=(12, 0))
        self.boton_navegador = ctk.CTkButton(
            self.acciones, text="Abrir el navegador otra vez", width=200,
            **theme.secundario(), command=self._abrir_navegador,
        )
        self.boton_copiar = ctk.CTkButton(
            self.acciones, text="Copiar el código", width=150,
            **theme.secundario(), command=self._copiar,
        )

        self.error = ctk.CTkLabel(
            self, text="", font=ctk.CTkFont(size=13), text_color=theme.DANGER, wraplength=460,
        )
        self.error.grid(row=8, column=0, pady=(14, 0))

    def _logo(self):
        """El icono de la app sobre el nombre, si está disponible."""
        ruta = config_module.resource_path("assets", "icon.png")
        try:
            from PIL import Image

            imagen = Image.open(ruta)
            self._imagen_logo = ctk.CTkImage(light_image=imagen, dark_image=imagen, size=(88, 88))
            return self._imagen_logo
        except Exception:
            return None

    # --- flujo ----------------------------------------------------------

    def start(self) -> None:
        """Pulsación del botón: abre GitHub en el navegador."""
        self._cancelar.clear()
        self.boton.configure(state="disabled", text="Abriendo GitHub en tu navegador…")
        self.error.configure(text="")
        self.instruccion.configure(text="")
        self.codigo_label.configure(text="")
        self.aviso_copiado.configure(text="")
        self.boton_navegador.pack_forget()
        self.boton_copiar.pack_forget()
        threading.Thread(target=self._entrar_por_la_web, daemon=True).start()

    # --- camino normal: el usuario solo pulsa «Authorize» -----------------

    def _entrar_por_la_web(self) -> None:
        if not auth.web_login_configured():
            # Sin credenciales propias se usa el inicio de sesión por código,
            # que solo necesita el Client ID público del proyecto. Es lo que
            # verá cualquiera que descargue Vaivén sin configurar nada.
            log.info("sin secreto propio: se usa el inicio de sesión por código")
            self._pedir_codigo()
            return
        try:
            intento = auth.start_web_login()
        except auth.PortUnavailable as exc:
            # Reserva: el inicio de sesión por código no necesita puertos.
            log.info("%s", exc)
            call_on_ui_thread(self, self._avisar_reserva)
            self._pedir_codigo()
            return
        except auth.AuthError as exc:
            call_on_ui_thread(self, self._mostrar_error, str(exc))
            return

        self._intento = intento
        call_on_ui_thread(self, self._esperando_autorizacion)
        auth.open_verification_page(intento.url)

        try:
            codigo = auth.wait_for_authorization(
                intento, cancel=self._cancelar, on_wait=self._quedan
            )
            token = auth.exchange_code(codigo, verifier=intento.verifier)
        except auth.CodeExpired as exc:
            call_on_ui_thread(self, self._caducado, str(exc))
            return
        except auth.AuthError as exc:
            call_on_ui_thread(self, self._mostrar_error, str(exc))
            return

        if self._cancelar.is_set():
            return
        auth.save_token(token)
        call_on_ui_thread(self, self.on_success, token)

    def _esperando_autorizacion(self) -> None:
        self.instruccion.configure(
            text="Se ha abierto GitHub en tu navegador.\n"
                 "Pulsa «Authorize» allí y volverás aquí automáticamente."
        )
        self.boton.configure(text="Esperando a que autorices en GitHub…")
        self.boton_navegador.pack(side="left", padx=6)

    def _avisar_reserva(self) -> None:
        self.instruccion.configure(
            text="No se ha podido usar el inicio de sesión directo en este equipo.\n"
                 "Se usará el método por código, que funciona igual de bien."
        )

    # --- reserva: inicio de sesión por código ------------------------------

    def _pedir_codigo(self) -> None:
        try:
            codigo = auth.request_device_code()
        except auth.AuthError as exc:
            call_on_ui_thread(self, self._mostrar_error, str(exc))
            return
        call_on_ui_thread(self, self._mostrar_codigo, codigo)

    def _mostrar_codigo(self, codigo: auth.DeviceCode) -> None:
        """Pasos 3 y 4: mostrar el código en grande, copiarlo y abrir el navegador."""
        self._codigo = codigo
        self.codigo_label.configure(text=codigo.user_code)
        self.instruccion.configure(
            text="Pega este código en la página de GitHub que acaba de abrirse\n"
                 "y pulsa «Continue» para autorizar a Vaivén:"
        )
        copiado = auth.copy_to_clipboard(codigo.user_code, widget=self)
        self.aviso_copiado.configure(
            text="Ya está copiado en el portapapeles: solo tienes que pegarlo (Ctrl+V)."
            if copiado else "Cópialo a mano: no se pudo usar el portapapeles."
        )
        self.boton_navegador.pack(side="left", padx=6)
        self.boton_copiar.pack(side="left", padx=6)
        auth.open_verification_page(codigo.verification_uri)
        self.boton.configure(text="Esperando a que autorices en GitHub…")
        threading.Thread(target=self._esperar, args=(codigo,), daemon=True).start()

    def _esperar(self, codigo: auth.DeviceCode) -> None:
        """Paso 5: preguntar a GitHub hasta obtener la sesión."""
        try:
            token = auth.poll_for_token(codigo, on_wait=self._quedan)
        except auth.CodeExpired as exc:
            call_on_ui_thread(self, self._caducado, str(exc))
            return
        except auth.AuthError as exc:
            call_on_ui_thread(self, self._mostrar_error, str(exc))
            return
        if self._cancelar.is_set():
            return
        auth.save_token(token)
        call_on_ui_thread(self, self.on_success, token)

    def _quedan(self, segundos: int) -> None:
        minutos, resto = divmod(max(0, segundos), 60)
        call_on_ui_thread(
            self, lambda: self.boton.configure(
                text=f"Esperando a que autorices… ({minutos}:{resto:02d})"
            ),
        )

    def _caducado(self, mensaje: str) -> None:
        self._intento = None
        self.codigo_label.configure(text="")
        self.instruccion.configure(text="")
        self.aviso_copiado.configure(text="")
        self.boton_navegador.pack_forget()
        self.boton_copiar.pack_forget()
        self.error.configure(text=mensaje)
        self.boton.configure(state="normal", text="Intentar de nuevo")

    def _mostrar_error(self, mensaje: str) -> None:
        self.error.configure(text=mensaje)
        self.boton.configure(state="normal", text="Intentar de nuevo")

    # --- botones auxiliares ---------------------------------------------

    def _abrir_navegador(self) -> None:
        if self._intento is not None:
            auth.open_verification_page(self._intento.url)
        elif self._codigo:
            auth.open_verification_page(self._codigo.verification_uri)

    def _copiar(self) -> None:
        if self._codigo and auth.copy_to_clipboard(self._codigo.user_code, widget=self):
            self.aviso_copiado.configure(text="Copiado otra vez.")

    def stop(self) -> None:
        """Se llama al cerrar la ventana: suelta el puerto del login web."""
        self._cancelar.set()
        if self._intento is not None:
            self._intento.close()
            self._intento = None
