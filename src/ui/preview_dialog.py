"""Vista previa y confirmación (sección 6.5 de SPEC.md).

Ninguna acción masiva se ejecuta sin pasar por aquí. El diálogo enseña tres
grupos —lo que se hará, lo que se omitirá y lo que ya está al día—, avisa de
archivos grandes o que parecen secretos, deja excluir proyectos con casillas
y solo entonces pregunta.
"""

from __future__ import annotations

import customtkinter as ctk

from ..sync_engine import PUSH, Plan, PlannedAction
from . import NOMBRES_CAMBIO, texto_seguro, theme


class PreviewDialog(ctk.CTkToplevel):
    """Modal de confirmación. ``result`` es ``True`` si el usuario aceptó."""

    def __init__(self, master, plan: Plan, team: str):
        super().__init__(master)
        self.plan = plan
        self.team = team
        self.result = False
        self._casillas: dict[int, ctk.CTkCheckBox] = {}
        self._riesgo_aceptado = ctk.BooleanVar(value=False)

        es_subida = plan.kind == PUSH
        self.title("Subir todo" if es_subida else "Sincronizar todo")
        self.geometry("720x620")
        self.minsize(560, 460)
        self.transient(master)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._cabecera(es_subida)
        self._cuerpo(es_subida)
        self._pie()

        self.protocol("WM_DELETE_WINDOW", self._cancelar)
        self.bind("<Escape>", lambda _e: self._cancelar())
        self.after(120, self._enfocar)

    def _enfocar(self) -> None:
        try:
            self.grab_set()
            self.focus_force()
        except Exception:
            pass

    # --- partes del diálogo ---------------------------------------------

    def _cabecera(self, es_subida: bool) -> None:
        cabecera = ctk.CTkFrame(self, fg_color="transparent")
        cabecera.grid(row=0, column=0, sticky="ew", padx=20, pady=(18, 8))
        cabecera.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            cabecera,
            text="Esto es lo que va a pasar" if es_subida else "Esto es lo que se traerá de GitHub",
            font=ctk.CTkFont(size=19, weight="bold"), anchor="w",
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            cabecera, text="Todavía no se ha tocado nada. Revisa y confirma.",
            font=ctk.CTkFont(size=12), text_color=theme.TEXT_MUTED, anchor="w",
        ).grid(row=1, column=0, sticky="w")

    def _cuerpo(self, es_subida: bool) -> None:
        self.cuerpo = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.cuerpo.grid(row=1, column=0, sticky="nsew", padx=16)
        self.cuerpo.grid_columnconfigure(0, weight=1)
        fila = 0

        if es_subida and self.plan.to_do:
            fila = self._mensaje_de_commit(fila)

        if self.plan.to_do:
            fila = self._grupo(
                fila, "Se hará", theme.SUCCESS,
                [self._tarjeta_accion(a, es_subida) for a in self.plan.to_do],
            )

        if self.plan.skipped:
            fila = self._grupo(
                fila, "Se omitirá (necesita tu atención)", theme.DANGER,
                [self._tarjeta_omitida(a) for a in self.plan.skipped],
            )

        if self.plan.missing:
            fila = self._grupo_faltantes(fila)

        if self.plan.unchanged:
            fila = self._grupo_al_dia(fila)

        if not (self.plan.to_do or self.plan.skipped or self.plan.missing):
            ctk.CTkLabel(
                self.cuerpo, text="No hay nada que hacer: todo está al día.",
                font=ctk.CTkFont(size=14), text_color=theme.TEXT_MUTED,
            ).grid(row=fila, column=0, pady=40)

    def _mensaje_de_commit(self, fila: int) -> int:
        marco = ctk.CTkFrame(self.cuerpo, fg_color=theme.CARD_BG, corner_radius=8)
        marco.grid(row=fila, column=0, sticky="ew", pady=(4, 12))
        marco.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            marco, text="Descripción de los cambios", anchor="w",
            font=ctk.CTkFont(size=12, weight="bold"),
        ).grid(row=0, column=0, sticky="w", padx=12, pady=(10, 2))

        self.entrada_mensaje = ctk.CTkEntry(marco, height=34)
        self.entrada_mensaje.insert(0, self.plan.to_do[0].commit_message or "")
        self.entrada_mensaje.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 12))
        return fila + 1

    def _grupo(self, fila: int, titulo: str, color, tarjetas: list) -> int:
        ctk.CTkLabel(
            self.cuerpo, text=f"{titulo}  ({len(tarjetas)})", anchor="w",
            font=ctk.CTkFont(size=14, weight="bold"), text_color=color,
        ).grid(row=fila, column=0, sticky="w", pady=(10, 4))
        fila += 1
        for tarjeta in tarjetas:
            tarjeta.grid(row=fila, column=0, sticky="ew", pady=3)
            fila += 1
        return fila

    def _tarjeta_accion(self, accion: PlannedAction, es_subida: bool) -> ctk.CTkFrame:
        marco = ctk.CTkFrame(self.cuerpo, fg_color=theme.CARD_BG, corner_radius=8)
        marco.grid_columnconfigure(1, weight=1)

        casilla = ctk.CTkCheckBox(
            marco, text="", width=24, command=self._recalcular,
        )
        casilla.select()
        casilla.grid(row=0, column=0, padx=(10, 4), pady=10)
        self._casillas[id(accion)] = casilla

        ctk.CTkLabel(
            marco, text=accion.name, anchor="w",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=0, column=1, sticky="w", pady=(10, 0))

        ctk.CTkLabel(
            marco, text=self._resumen(accion, es_subida), anchor="w",
            font=ctk.CTkFont(size=12), text_color=theme.TEXT_MUTED, justify="left",
        ).grid(row=1, column=1, sticky="w", pady=(0, 10))

        for aviso in accion.warnings:
            ctk.CTkLabel(
                marco,
                text=("🔑 " if aviso.is_secret else "📦 ") + f"{aviso.path} — {aviso.detail}",
                anchor="w", wraplength=520, justify="left",
                font=ctk.CTkFont(size=12, weight="bold"), text_color=theme.DANGER,
            ).grid(row=marco.grid_size()[1], column=1, sticky="w", padx=(0, 12), pady=(0, 10))

        detalle = self._detalle(accion, es_subida)
        if detalle:
            desplegable = ctk.CTkTextbox(marco, height=min(120, 18 * len(detalle) + 16))
            desplegable.insert("1.0", texto_seguro("\n".join(detalle)))
            desplegable.configure(state="disabled", font=ctk.CTkFont(size=11, family="Consolas"))
            desplegable.grid(row=marco.grid_size()[1], column=1, sticky="ew", padx=(0, 12), pady=(0, 10))
        return marco

    def _resumen(self, accion: PlannedAction, es_subida: bool) -> str:
        status = accion.status
        if es_subida:
            partes = []
            if status.files:
                partes.append(f"{len(status.files)} archivo{'s' if len(status.files) != 1 else ''}")
            if status.ahead:
                partes.append(f"{status.ahead} commit{'s' if status.ahead != 1 else ''}")
            return "Se subirán " + (" y ".join(partes) or "los cambios pendientes")
        return (
            f"Se traerán {status.behind} cambio{'s' if status.behind != 1 else ''} de GitHub"
        )

    def _detalle(self, accion: PlannedAction, es_subida: bool) -> list[str]:
        status = accion.status
        if es_subida:
            lineas = [
                f"{NOMBRES_CAMBIO.get(c.change, c.change):>12}   {c.path}" for c in status.files[:20]
            ]
            lineas += [f"{c.short_sha:>12}   {c.subject}" for c in status.outgoing[:10]]
        else:
            lineas = [
                f"{c.short_sha}  {c.subject}" + (f"  ({c.team})" if c.team else "")
                for c in status.incoming[:20]
            ]
        return lineas

    def _tarjeta_omitida(self, accion: PlannedAction) -> ctk.CTkFrame:
        marco = ctk.CTkFrame(self.cuerpo, fg_color=theme.CARD_BG, corner_radius=8)
        marco.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            marco, text=accion.name, anchor="w",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=0, column=0, sticky="w", padx=12, pady=(10, 0))
        ctk.CTkLabel(
            marco, text=accion.reason, anchor="w", wraplength=620, justify="left",
            font=ctk.CTkFont(size=12), text_color=theme.TEXT_MUTED,
        ).grid(row=1, column=0, sticky="w", padx=12, pady=(0, 4))
        ctk.CTkLabel(
            marco, text="Ábrelo desde la lista para resolverlo por separado.",
            anchor="w", font=ctk.CTkFont(size=11), text_color=theme.TEXT_MUTED,
        ).grid(row=2, column=0, sticky="w", padx=12, pady=(0, 10))
        return marco

    def _grupo_faltantes(self, fila: int) -> int:
        ctk.CTkLabel(
            self.cuerpo, text=f"Proyectos en GitHub que no tienes en este equipo  ({len(self.plan.missing)})",
            anchor="w", font=ctk.CTkFont(size=14, weight="bold"),
        ).grid(row=fila, column=0, sticky="w", pady=(14, 4))
        fila += 1
        for pendiente in self.plan.missing:
            marco = ctk.CTkFrame(self.cuerpo, fg_color=theme.CARD_BG, corner_radius=8)
            marco.grid(row=fila, column=0, sticky="ew", pady=3)
            marco.grid_columnconfigure(1, weight=1)
            casilla = ctk.CTkCheckBox(marco, text="", width=24, command=self._recalcular)
            casilla.grid(row=0, column=0, padx=(10, 4), pady=8)   # desmarcado por defecto
            self._casillas[id(pendiente)] = casilla
            etiqueta = pendiente.name + ("  · privado" if pendiente.private else "")
            ctk.CTkLabel(
                marco, text=etiqueta, anchor="w", font=ctk.CTkFont(size=13),
            ).grid(row=0, column=1, sticky="w", pady=8)
            fila += 1
        return fila

    def _grupo_al_dia(self, fila: int) -> int:
        self._al_dia_abierto = False
        boton = ctk.CTkButton(
            self.cuerpo, text=f"▸ Sin cambios  ({len(self.plan.unchanged)})",
            anchor="w", fg_color="transparent", text_color=theme.TEXT_MUTED, hover=False,
            font=ctk.CTkFont(size=13),
        )
        boton.grid(row=fila, column=0, sticky="ew", pady=(14, 2))
        contenido = ctk.CTkLabel(
            self.cuerpo, anchor="w", justify="left", font=ctk.CTkFont(size=12),
            text_color=theme.TEXT_MUTED,
            text="   " + ", ".join(a.name for a in self.plan.unchanged),
            wraplength=640,
        )

        def alternar() -> None:
            self._al_dia_abierto = not self._al_dia_abierto
            boton.configure(
                text=f"{'▾' if self._al_dia_abierto else '▸'} Sin cambios  ({len(self.plan.unchanged)})"
            )
            if self._al_dia_abierto:
                contenido.grid(row=fila + 1, column=0, sticky="ew", pady=(0, 8))
            else:
                contenido.grid_forget()

        boton.configure(command=alternar)
        return fila + 2

    def _pie(self) -> None:
        pie = ctk.CTkFrame(self, fg_color="transparent")
        pie.grid(row=2, column=0, sticky="ew", padx=20, pady=(8, 18))
        pie.grid_columnconfigure(0, weight=1)

        self.riesgo = ctk.CTkCheckBox(
            pie,
            text="Entiendo el riesgo: quiero subir archivos que pueden contener contraseñas",
            variable=self._riesgo_aceptado, command=self._recalcular,
            font=ctk.CTkFont(size=12), text_color=theme.DANGER,
        )

        self.pregunta = ctk.CTkLabel(
            pie, text=self.plan.question(), anchor="w",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self.pregunta.grid(row=1, column=0, sticky="w", pady=(8, 8))

        botones = ctk.CTkFrame(pie, fg_color="transparent")
        botones.grid(row=1, column=1, sticky="e")
        ctk.CTkButton(
            botones, text="Cancelar", width=110, **theme.secundario(), command=self._cancelar,
        ).pack(side="left", padx=(0, 8))
        self.boton_confirmar = ctk.CTkButton(
            botones, text="Sí, continuar", width=150,
            font=ctk.CTkFont(size=13, weight="bold"), command=self._confirmar,
        )
        self.boton_confirmar.pack(side="left")

        self._recalcular()

    # --- estado del diálogo ----------------------------------------------

    def _sincronizar_seleccion(self) -> None:
        for accion in self.plan.to_do:
            casilla = self._casillas.get(id(accion))
            if casilla is not None:
                accion.selected = bool(casilla.get())
        for pendiente in self.plan.missing:
            casilla = self._casillas.get(id(pendiente))
            if casilla is not None:
                pendiente.selected = bool(casilla.get())

    def _recalcular(self) -> None:
        """Mantiene el botón de confirmar coherente con lo elegido (6.5.5)."""
        self._sincronizar_seleccion()
        self.pregunta.configure(text=self.plan.question())

        hay_secretos = self.plan.has_secret_warnings
        if hay_secretos:
            self.riesgo.grid(row=0, column=0, columnspan=2, sticky="w")
        else:
            self.riesgo.grid_forget()
            self._riesgo_aceptado.set(False)

        bloqueado = self.plan.is_empty or (hay_secretos and not self._riesgo_aceptado.get())
        if bloqueado:
            # CTk deja el relleno azul al desactivar: parecía que se podía pulsar.
            self.boton_confirmar.configure(
                state="disabled", fg_color=theme.DISABLED_BG, text_color_disabled=theme.DISABLED_TEXT,
            )
        else:
            self.boton_confirmar.configure(
                state="normal", fg_color=ctk.ThemeManager.theme["CTkButton"]["fg_color"],
            )

    def _confirmar(self) -> None:
        self._sincronizar_seleccion()
        if hasattr(self, "entrada_mensaje"):
            mensaje = self.entrada_mensaje.get().strip()
            if mensaje:
                for accion in self.plan.to_do:
                    accion.commit_message = mensaje
        self.result = True
        self._cerrar()

    def _cancelar(self) -> None:
        self.result = False
        self._cerrar()

    def _cerrar(self) -> None:
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()


def ask(master, plan: Plan, team: str) -> bool:
    """Abre la vista previa y espera. Devuelve si el usuario confirmó."""
    dialogo = PreviewDialog(master, plan, team)
    master.wait_window(dialogo)
    return dialogo.result
