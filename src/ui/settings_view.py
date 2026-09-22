"""Ajustes (sección 9.3 de SPEC.md)."""

from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from .. import analyzer, auth, config as config_module, startup
from ..logger import get_logger, log_file_path
from . import call_on_ui_thread, theme

log = get_logger("ui.settings")


class SettingsView(ctk.CTkToplevel):
    """Ventana de ajustes. Guarda al cerrar y avisa de qué cambió."""

    def __init__(self, master, config: config_module.Config, on_saved, on_logout):
        super().__init__(master)
        self.config_data = config
        self.on_saved = on_saved
        self.on_logout = on_logout

        self.title("Ajustes")
        self.geometry("620x560")
        self.transient(master)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        cuerpo = ctk.CTkScrollableFrame(self, fg_color="transparent")
        cuerpo.grid(row=0, column=0, sticky="nsew", padx=18, pady=(18, 8))
        cuerpo.grid_columnconfigure(0, weight=1)
        fila = 0

        # --- carpeta raíz ---
        fila = self._titulo(cuerpo, fila, "Carpeta de proyectos")
        marco = ctk.CTkFrame(cuerpo, fg_color="transparent")
        marco.grid(row=fila, column=0, sticky="ew", pady=(0, 14))
        marco.grid_columnconfigure(0, weight=1)
        self.entrada_carpeta = ctk.CTkEntry(marco, height=34)
        self.entrada_carpeta.insert(0, config.root_folder or "")
        self.entrada_carpeta.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ctk.CTkButton(marco, text="Elegir…", width=90, command=self._elegir_carpeta).grid(row=0, column=1)
        fila += 1
        self.aviso_carpeta = ctk.CTkLabel(
            cuerpo, text="", anchor="w", font=ctk.CTkFont(size=11), text_color=theme.TEXT_MUTED,
        )
        self.aviso_carpeta.grid(row=fila, column=0, sticky="w", pady=(0, 12))
        fila += 1

        # --- nombre del equipo ---
        fila = self._titulo(cuerpo, fila, "Nombre de este equipo")
        ctk.CTkLabel(
            cuerpo, text="Aparece en los cambios que subas, para saber de dónde vienen.",
            anchor="w", font=ctk.CTkFont(size=11), text_color=theme.TEXT_MUTED,
        ).grid(row=fila, column=0, sticky="w")
        fila += 1
        self.entrada_equipo = ctk.CTkEntry(cuerpo, height=34)
        self.entrada_equipo.insert(0, config.team_name)
        self.entrada_equipo.grid(row=fila, column=0, sticky="ew", pady=(4, 16))
        fila += 1

        # --- interruptores ---
        self.var_inicio = ctk.BooleanVar(value=config.start_with_windows)
        self.var_revisar = ctk.BooleanVar(value=config.check_on_open)
        fila = self._interruptor(
            cuerpo, fila, "Iniciar con Windows",
            "Vaivén se abre solo al encender el equipo.", self.var_inicio,
        )
        fila = self._interruptor(
            cuerpo, fila, "Revisar el estado al abrir",
            "Solo mira qué hay; nunca sube ni sincroniza sin que lo confirmes.",
            self.var_revisar,
        )

        # --- tema ---
        fila = self._titulo(cuerpo, fila, "Aspecto")
        self.selector_tema = ctk.CTkSegmentedButton(
            cuerpo, **theme.SEGMENTADO, values=["Automático", "Claro", "Oscuro"], command=self._cambiar_tema,
        )
        self.selector_tema.set(
            {"system": "Automático", "light": "Claro", "dark": "Oscuro"}[config.theme]
        )
        self.selector_tema.grid(row=fila, column=0, sticky="w", pady=(4, 16))
        fila += 1

        # --- repos excluidos ---
        fila = self._titulo(cuerpo, fila, "Proyectos que Vaivén debe ignorar")
        ctk.CTkLabel(
            cuerpo, text="Un nombre por línea.", anchor="w",
            font=ctk.CTkFont(size=11), text_color=theme.TEXT_MUTED,
        ).grid(row=fila, column=0, sticky="w")
        fila += 1
        self.caja_excluidos = ctk.CTkTextbox(cuerpo, height=90)
        self.caja_excluidos.insert("1.0", "\n".join(config.excluded_repos))
        self.caja_excluidos.grid(row=fila, column=0, sticky="ew", pady=(4, 16))
        fila += 1

        # --- sesión y registro ---
        fila = self._titulo(cuerpo, fila, "Sesión y registro")
        acciones = ctk.CTkFrame(cuerpo, fg_color="transparent")
        acciones.grid(row=fila, column=0, sticky="w", pady=(0, 10))
        ctk.CTkButton(
            acciones, text="Abrir la carpeta de registros", width=210,
            **theme.secundario(), command=self._abrir_logs,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            acciones, text="Cerrar sesión", width=130,
            **theme.secundario(text_color=theme.DANGER), command=self._cerrar_sesion,
        ).pack(side="left")

        # --- pie ---
        pie = ctk.CTkFrame(self, fg_color="transparent")
        pie.grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 16))
        ctk.CTkButton(
            pie, text="Cancelar", width=110, **theme.secundario(), command=self.destroy,
        ).pack(side="right", padx=(8, 0))
        ctk.CTkButton(
            pie, text="Guardar", width=130,
            font=ctk.CTkFont(size=13, weight="bold"), command=self._guardar,
        ).pack(side="right")

        self._contar_repos()
        self.after(120, lambda: self.focus_force())

    # --- ayudas de construcción -------------------------------------------

    def _titulo(self, padre, fila: int, texto: str) -> int:
        ctk.CTkLabel(
            padre, text=texto, anchor="w", font=ctk.CTkFont(size=14, weight="bold"),
        ).grid(row=fila, column=0, sticky="w", pady=(6, 4))
        return fila + 1

    def _interruptor(self, padre, fila: int, titulo: str, detalle: str, variable) -> int:
        marco = ctk.CTkFrame(padre, fg_color="transparent")
        marco.grid(row=fila, column=0, sticky="ew", pady=(0, 12))
        marco.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            marco, text=titulo, anchor="w", font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            marco, text=detalle, anchor="w",
            font=ctk.CTkFont(size=11), text_color=theme.TEXT_MUTED,
        ).grid(row=1, column=0, sticky="w")
        ctk.CTkSwitch(marco, text="", variable=variable, width=48).grid(row=0, column=1, rowspan=2)
        return fila + 1

    # --- acciones ---------------------------------------------------------

    def _elegir_carpeta(self) -> None:
        elegida = filedialog.askdirectory(
            title="¿Dónde guardas tus proyectos?",
            initialdir=self.entrada_carpeta.get() or str(Path.home()),
            parent=self,
        )
        if elegida:
            self.entrada_carpeta.delete(0, "end")
            self.entrada_carpeta.insert(0, elegida)
            self._contar_repos()

    def _contar_repos(self) -> None:
        """Cuenta los proyectos de la carpeta elegida, sin congelar la ventana."""
        carpeta = self.entrada_carpeta.get().strip()
        if not carpeta or not Path(carpeta).is_dir():
            self.aviso_carpeta.configure(text="Elige la carpeta donde tienes tus proyectos.")
            return
        self.aviso_carpeta.configure(text="Buscando proyectos…")

        def hilo() -> None:
            try:
                cuantos = len(analyzer.discover_repos(carpeta))
                texto = (
                    f"Se han encontrado {cuantos} proyecto"
                    f"{'s' if cuantos != 1 else ''} aquí dentro."
                )
            except Exception as exc:
                log.warning("no se pudo mirar dentro de %s: %s", carpeta, exc)
                texto = "No se pudo mirar dentro de esa carpeta."
            call_on_ui_thread(self, lambda: self.aviso_carpeta.configure(text=texto))

        threading.Thread(target=hilo, daemon=True).start()

    def _cambiar_tema(self, valor: str) -> None:
        interno = {"Automático": "system", "Claro": "light", "Oscuro": "dark"}[valor]
        ctk.set_appearance_mode(interno)

    def _abrir_logs(self) -> None:
        carpeta = log_file_path().parent
        try:
            if os.name == "nt":
                os.startfile(str(carpeta))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", str(carpeta)])
        except Exception as exc:
            log.warning("no se pudo abrir la carpeta de registros: %s", exc)

    def _cerrar_sesion(self) -> None:
        auth.delete_token()
        self.destroy()
        self.on_logout()

    def _guardar(self) -> None:
        datos = self.config_data
        datos.root_folder = self.entrada_carpeta.get().strip() or None
        datos.team_name = self.entrada_equipo.get().strip() or datos.team_name
        datos.check_on_open = bool(self.var_revisar.get())
        datos.theme = {"Automático": "system", "Claro": "light", "Oscuro": "dark"}[
            self.selector_tema.get()
        ]
        datos.excluded_repos = [
            linea.strip() for linea in self.caja_excluidos.get("1.0", "end").splitlines()
            if linea.strip()
        ]

        quiere_inicio = bool(self.var_inicio.get())
        if quiere_inicio != datos.start_with_windows:
            startup.apply(quiere_inicio)
        datos.start_with_windows = quiere_inicio

        datos.save()
        ctk.set_appearance_mode(datos.theme)
        self.destroy()
        self.on_saved(datos)
