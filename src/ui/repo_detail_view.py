"""Detalle de un proyecto y sus acciones individuales (secciones 9.1 y 6.6).

Aquí sí se puede usar vocabulario de Git: es la pantalla a la que llega quien
quiere entender qué está pasando. Las acciones son siempre de un solo
proyecto; las masivas viven en la pantalla principal.
"""

from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path

import customtkinter as ctk

from .. import analyzer, safety, sync_engine
from ..analyzer import RepoState, RepoStatus
from ..logger import get_logger
from . import NOMBRES_CAMBIO, call_on_ui_thread, etiqueta_segura, ruta_corta, texto_seguro, theme

log = get_logger("ui.detail")


def open_folder(path: Path) -> None:
    """Abre el explorador de archivos en la carpeta del proyecto."""
    try:
        if os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as exc:
        log.warning("no se pudo abrir la carpeta %s: %s", path, exc)


def open_in_vscode(path: Path) -> bool:
    """Abre el proyecto en VS Code (sección 6.6)."""
    for programa in ("code", "code.cmd"):
        try:
            subprocess.Popen(
                [programa, str(path)],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return True
        except Exception:
            continue
    open_folder(path)
    return False


class RepoDetailView(ctk.CTkToplevel):
    """Ventana de detalle con los botones individuales del proyecto."""

    def __init__(self, master, status: RepoStatus, team: str, token: str | None, on_changed):
        super().__init__(master)
        self.status = status
        self.team = team
        self.token = token
        self.on_changed = on_changed
        self._ocupado = False
        self._botones_activos: list[ctk.CTkButton] = []

        self.title(status.name)
        self.geometry("700x600")
        self.minsize(560, 460)
        self.transient(master)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self.zona_cabecera = ctk.CTkFrame(self, fg_color="transparent")
        self.zona_cabecera.grid(row=0, column=0, sticky="ew", padx=20, pady=(18, 6))
        self.zona_botones = ctk.CTkFrame(self, fg_color="transparent")
        self.zona_botones.grid(row=1, column=0, sticky="ew", padx=20, pady=(8, 4))
        self._contenido()
        self._barra_estado()
        self._pintar()

        self.bind("<Escape>", lambda _e: self.destroy())
        self.after(120, self._enfocar)

    def _enfocar(self) -> None:
        try:
            self.focus_force()
        except Exception:
            pass

    def _pintar(self) -> None:
        """Dibuja todo lo que depende del estado del proyecto."""
        self._cabecera()
        self._botones()
        self._pintar_contenido()
        self._pintar_pie()

    # --- partes -----------------------------------------------------------

    def _cabecera(self) -> None:
        marco = self.zona_cabecera
        for hijo in marco.winfo_children():
            hijo.destroy()
        marco.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            marco, text=self.status.name, anchor="w",
            font=ctk.CTkFont(size=20, weight="bold"),
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(
            marco, text=f"● {self.status.label}", anchor="e",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=theme.dot_color(self.status.color),
        ).grid(row=0, column=1, sticky="e")

        rama = self.status.branch or "sin rama"
        ctk.CTkLabel(
            marco, text=f"{rama} · {self.status.summary()}", anchor="w",
            font=ctk.CTkFont(size=12), text_color=theme.TEXT_MUTED, wraplength=620, justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 0))

        ctk.CTkLabel(
            marco, text=ruta_corta(self.status.path), anchor="w",
            font=ctk.CTkFont(size=11), text_color=theme.TEXT_MUTED,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(2, 0))

    def _boton(self, padre, texto, orden, *, destacado=False, **kwargs) -> ctk.CTkButton:
        estilo = {} if destacado else theme.secundario()
        estilo.update(kwargs)
        widget = ctk.CTkButton(padre, text=texto, height=34, command=orden, **estilo)
        widget.pack(side="left", padx=(0, 6), pady=2)
        self._botones_activos.append(widget)
        return widget

    def _botones(self) -> None:
        """Dos filas: lo que resuelve el estado actual, y las herramientas.

        Antes iban en una sola fila junto a «Respaldos» y «Deshacer», y con
        más de tres acciones se tapaban unos botones a otros.
        """
        for hijo in self.zona_botones.winfo_children():
            hijo.destroy()
        self._botones_activos = []

        principales = ctk.CTkFrame(self.zona_botones, fg_color="transparent")
        estado = self.status.state
        if self.status.can_push:
            self._boton(principales, "⬆ Subir este proyecto", self._subir, destacado=True)
        if self.status.can_sync:
            self._boton(principales, "⬇ Sincronizar", self._sincronizar, destacado=True)
        if estado is RepoState.DIVERGED:
            self._boton(principales, "Intentar combinar", self._combinar, destacado=True)
        if estado is RepoState.BEHIND_WITH_LOCAL_CHANGES:
            self._boton(principales, "⬆ Subir mis cambios primero", self._subir, destacado=True)
            self._boton(principales, "Guardar aparte y sincronizar", self._guardar_aparte)
        if estado is RepoState.NO_UPSTREAM:
            self._boton(principales, "⬆ Publicar esta rama en GitHub", self._publicar_rama, destacado=True)
        if principales.winfo_children():
            principales.pack(fill="x", pady=(0, 4))

        herramientas = ctk.CTkFrame(self.zona_botones, fg_color="transparent")
        herramientas.pack(fill="x")
        self._boton(herramientas, "Ver diferencias", self._ver_diferencias)
        self._boton(herramientas, "Abrir en VS Code", lambda: open_in_vscode(self.status.path))
        self._boton(herramientas, "Abrir carpeta", lambda: open_folder(self.status.path))

    def _contenido(self) -> None:
        self.contenido = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.contenido.grid(row=2, column=0, sticky="nsew", padx=16, pady=(6, 6))
        self.contenido.grid_columnconfigure(0, weight=1)

    def _pintar_contenido(self) -> None:
        for hijo in self.contenido.winfo_children():
            hijo.destroy()
        fila = 0

        if self.status.incoming:
            fila = self._bloque_commits(
                fila, f"Llegan de GitHub ({len(self.status.incoming)})", self.status.incoming
            )
        if self.status.outgoing:
            fila = self._bloque_commits(
                fila, f"Esperan a subirse ({len(self.status.outgoing)})", self.status.outgoing
            )
        if self.status.files:
            fila = self._bloque_archivos(fila)
        if not (self.status.incoming or self.status.outgoing or self.status.files):
            ctk.CTkLabel(
                self.contenido, text="No hay nada pendiente en este proyecto.",
                font=ctk.CTkFont(size=13), text_color=theme.TEXT_MUTED,
            ).grid(row=fila, column=0, pady=30)

    def _bloque_commits(self, fila: int, titulo: str, commits) -> int:
        ctk.CTkLabel(
            self.contenido, text=titulo, anchor="w",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).grid(row=fila, column=0, sticky="w", pady=(10, 4))
        fila += 1
        for commit in commits[:40]:
            marco = ctk.CTkFrame(self.contenido, fg_color=theme.CARD_BG, corner_radius=6)
            marco.grid(row=fila, column=0, sticky="ew", pady=2)
            marco.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(
                marco, text=commit.subject or "(sin descripción)", anchor="w",
                font=ctk.CTkFont(size=12),
            ).grid(row=0, column=0, sticky="w", padx=10, pady=(7, 0))
            detalle = f"{commit.short_sha} · {commit.author}"
            if commit.team:
                detalle += f" · desde {commit.team}"
            if commit.date:
                detalle += f" · {commit.date.astimezone():%d/%m/%Y %H:%M}"
            ctk.CTkLabel(
                marco, text=detalle, anchor="w",
                font=ctk.CTkFont(size=11), text_color=theme.TEXT_MUTED,
            ).grid(row=1, column=0, sticky="w", padx=10, pady=(0, 7))
            fila += 1
        return fila

    def _bloque_archivos(self, fila: int) -> int:
        ctk.CTkLabel(
            self.contenido, text=f"Archivos con cambios ({len(self.status.files)})", anchor="w",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).grid(row=fila, column=0, sticky="w", pady=(14, 4))
        fila += 1
        caja = ctk.CTkTextbox(self.contenido, height=min(260, 20 * len(self.status.files) + 20))
        caja.insert("1.0", texto_seguro("\n".join(
            f"{NOMBRES_CAMBIO.get(c.change, c.change):>12}   {c.path}" for c in self.status.files
        )))
        caja.configure(state="disabled", font=ctk.CTkFont(size=11, family="Consolas"))
        caja.grid(row=fila, column=0, sticky="ew", pady=2)
        return fila + 1

    def _barra_estado(self) -> None:
        pie = ctk.CTkFrame(self, fg_color="transparent")
        pie.grid(row=3, column=0, sticky="ew", padx=20, pady=(4, 16))
        pie.grid_columnconfigure(0, weight=1)
        self.mensaje = ctk.CTkLabel(
            pie, text="", anchor="w", font=ctk.CTkFont(size=12), wraplength=380, justify="left",
        )
        self.mensaje.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        self.boton_respaldos = ctk.CTkButton(
            pie, text="Respaldos", height=32, width=100,
            **theme.secundario(), command=self._respaldos,
        )
        self.boton_respaldos.grid(row=0, column=1, padx=(0, 6))
        self.boton_deshacer = ctk.CTkButton(
            pie, text="Deshacer última operación", height=32, width=190,
            **theme.secundario(text_color=theme.DANGER), command=self._deshacer,
        )
        self.boton_deshacer.grid(row=0, column=2)

    def _pintar_pie(self) -> None:
        """«Deshacer» solo se ofrece si hay algo que deshacer."""
        hay_algo = safety.undo_candidate(self.status.path) is not None
        self.boton_deshacer.configure(state="normal" if hay_algo and not self._ocupado else "disabled")

    # --- acciones individuales (sección 6.6) ------------------------------

    def _bloquear(self, ocupado: bool) -> None:
        self._ocupado = ocupado
        estado = "disabled" if ocupado else "normal"
        for boton in (*self._botones_activos, self.boton_respaldos, self.boton_deshacer):
            try:
                boton.configure(state=estado)
            except Exception:
                pass
        if not ocupado:
            self._pintar_pie()

    def _en_segundo_plano(self, trabajo, etiqueta: str) -> None:
        """Ejecuta una operación sin congelar la ventana (sección 2).

        Al terminar vuelve a analizar el proyecto y redibuja la ventana: los
        botones que tocan dependen del estado nuevo, no del de antes.
        """
        if self._ocupado:
            return
        self.mensaje.configure(text=f"{etiqueta}…", text_color=theme.TEXT_MUTED)
        self._bloquear(True)

        def hilo() -> None:
            try:
                resultado = trabajo()
            except Exception as exc:  # pragma: no cover - red de seguridad
                log.exception("fallo en la acción «%s»", etiqueta)
                resultado = sync_engine.RepoResult(self.status.name, etiqueta, False, str(exc))
            try:
                nuevo = analyzer.analyze_repo(self.status.path, fetch=False)
            except Exception:  # pragma: no cover - red de seguridad
                nuevo = None
            call_on_ui_thread(self, self._terminado, resultado, nuevo)

        threading.Thread(target=hilo, daemon=True).start()

    def _terminado(self, resultado: sync_engine.RepoResult, nuevo: RepoStatus | None) -> None:
        if nuevo is not None:
            self.status = nuevo
            self._pintar()
        self._bloquear(False)
        self.mensaje.configure(
            text=etiqueta_segura(resultado.message, maximo=300),
            text_color=theme.SUCCESS if resultado.ok else theme.DANGER,
        )
        self.on_changed()

    def _subir(self) -> None:
        self._en_segundo_plano(
            lambda: sync_engine.push_this_repo(self.status, self.team, token=self.token),
            "Subiendo",
        )

    def _publicar_rama(self) -> None:
        self._en_segundo_plano(
            lambda: sync_engine.push_this_repo(
                self.status, self.team, token=self.token, allow_set_upstream=True
            ),
            "Publicando la rama",
        )

    def _sincronizar(self) -> None:
        self._en_segundo_plano(
            lambda: sync_engine.sync_repo(self.status, self.team, token=self.token),
            "Sincronizando",
        )

    def _combinar(self) -> None:
        self._en_segundo_plano(
            lambda: sync_engine.try_merge(self.status, self.team, token=self.token),
            "Combinando",
        )

    def _guardar_aparte(self) -> None:
        self._en_segundo_plano(
            lambda: sync_engine.stash_and_sync(self.status, self.team, token=self.token),
            "Guardando tus cambios aparte",
        )

    def _deshacer(self) -> None:
        self._en_segundo_plano(lambda: sync_engine.undo(self.status), "Deshaciendo")

    def _ver_diferencias(self) -> None:
        """Muestra las diferencias sin congelar la ventana ni ahogar a Tk."""
        ventana = ctk.CTkToplevel(self)
        ventana.title(f"Diferencias — {self.status.name}")
        ventana.geometry("760x520")
        ventana.transient(self)
        caja = ctk.CTkTextbox(ventana, font=ctk.CTkFont(size=12, family="Consolas"))
        caja.pack(fill="both", expand=True, padx=12, pady=12)
        caja.insert("1.0", "Calculando las diferencias…")
        caja.configure(state="disabled")

        def calcular() -> str:
            from .. import git_ops

            resumen = git_ops.run(["diff", "--stat", "HEAD"], cwd=self.status.path)
            detalle = git_ops.run(["diff", "HEAD"], cwd=self.status.path)
            junto = f"{resumen.stdout}\n{detalle.stdout}".strip()
            return junto or "No hay diferencias."

        def mostrar(texto: str) -> None:
            caja.configure(state="normal")
            caja.delete("1.0", "end")
            caja.insert("1.0", texto_seguro(texto))
            caja.configure(state="disabled")

        def hilo() -> None:
            try:
                texto = calcular()
            except Exception as exc:
                log.warning("no se pudieron calcular las diferencias: %s", exc)
                texto = "No se pudieron calcular las diferencias de este proyecto."
            call_on_ui_thread(ventana, mostrar, texto)

        threading.Thread(target=hilo, daemon=True).start()

    def _respaldos(self) -> None:
        BackupsView(self, self.status, self.on_changed)


class BackupsView(ctk.CTkToplevel):
    """Pantalla «Respaldos»: ver y restaurar cualquiera de la lista (6.4)."""

    def __init__(self, master, status: RepoStatus, on_changed):
        super().__init__(master)
        self.status = status
        self.on_changed = on_changed

        self.title(f"Respaldos — {status.name}")
        self.geometry("620x480")
        self.transient(master)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            self, text="Cada respaldo devuelve el proyecto a como estaba en ese momento.",
            anchor="w", font=ctk.CTkFont(size=12), text_color=theme.TEXT_MUTED,
        ).grid(row=0, column=0, sticky="w", padx=18, pady=(16, 8))

        self.lista = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.lista.grid(row=1, column=0, sticky="nsew", padx=14)
        self.lista.grid_columnconfigure(0, weight=1)

        self.mensaje = ctk.CTkLabel(self, text="", anchor="w", font=ctk.CTkFont(size=12))
        self.mensaje.grid(row=2, column=0, sticky="ew", padx=18, pady=(6, 14))

        self._pintar()
        self.after(120, lambda: self.focus_force())

    def _pintar(self) -> None:
        for hijo in self.lista.winfo_children():
            hijo.destroy()
        respaldos = safety.list_backups(self.status.path)
        if not respaldos:
            ctk.CTkLabel(
                self.lista, text="Todavía no hay respaldos de este proyecto.",
                font=ctk.CTkFont(size=13), text_color=theme.TEXT_MUTED,
            ).grid(row=0, column=0, pady=30)
            return
        for fila, respaldo in enumerate(respaldos):
            marco = ctk.CTkFrame(self.lista, fg_color=theme.CARD_BG, corner_radius=8)
            marco.grid(row=fila, column=0, sticky="ew", pady=3)
            marco.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(
                marco, text=respaldo.describe(), anchor="w", font=ctk.CTkFont(size=12),
                wraplength=400, justify="left",
            ).grid(row=0, column=0, sticky="w", padx=12, pady=10)
            ctk.CTkButton(
                marco, text="Restaurar", width=100, height=30,
                command=lambda r=respaldo: self._restaurar(r),
            ).grid(row=0, column=1, padx=10, pady=8)

    def _restaurar(self, respaldo) -> None:
        self.mensaje.configure(text="Restaurando…", text_color=theme.TEXT_MUTED)

        def hilo() -> None:
            try:
                safety.restore_backup(respaldo)
                texto, color = "El proyecto ha vuelto a ese estado.", theme.SUCCESS
            except Exception as exc:
                texto, color = str(exc), theme.DANGER
            call_on_ui_thread(self, lambda: self.mensaje.configure(text=texto, text_color=color))
            call_on_ui_thread(self, self._pintar)
            call_on_ui_thread(self, self.on_changed)

        threading.Thread(target=hilo, daemon=True).start()
