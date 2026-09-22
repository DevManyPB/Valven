"""Lista de proyectos de la pantalla principal (sección 9.1 de SPEC.md).

Cada fila enseña lo justo para decidir de un vistazo: un punto de color con
el estado, el nombre, la rama, una frase corta sin jerga y la fecha del
último cambio. El detalle técnico vive en la vista de detalle.
"""

from __future__ import annotations

from datetime import datetime, timezone

import customtkinter as ctk

from ..analyzer import RepoState, RepoStatus
from . import theme

#: Filtros de la sección 9.1.
FILTER_ALL = "Todos"
FILTER_ATTENTION = "Necesitan atención"
FILTER_CHANGES = "Con cambios"
FILTERS = (FILTER_ALL, FILTER_ATTENTION, FILTER_CHANGES)


def matches_filter(status: RepoStatus, filtro: str) -> bool:
    if filtro == FILTER_ATTENTION:
        return status.is_blocked or status.state is RepoState.NO_UPSTREAM
    if filtro == FILTER_CHANGES:
        return status.state is not RepoState.UP_TO_DATE
    return True


def human_date(cuando: datetime | None) -> str:
    """Fecha en lenguaje cotidiano: «hace 2 horas», «ayer», «12/09/2026»."""
    if cuando is None:
        return ""
    if cuando.tzinfo is None:
        cuando = cuando.replace(tzinfo=timezone.utc)
    ahora = datetime.now(timezone.utc)
    segundos = (ahora - cuando).total_seconds()
    if segundos < 0:
        return cuando.astimezone().strftime("%d/%m/%Y")
    if segundos < 90:
        return "hace un momento"
    minutos = segundos / 60
    if minutos < 60:
        return f"hace {int(minutos)} minutos"
    horas = minutos / 60
    if horas < 24:
        return f"hace {int(horas)} hora{'s' if int(horas) != 1 else ''}"
    dias = horas / 24
    if dias < 2:
        return "ayer"
    if dias < 7:
        return f"hace {int(dias)} días"
    return cuando.astimezone().strftime("%d/%m/%Y")


class RepoRow(ctk.CTkFrame):
    """Una fila de la lista, clicable para abrir el detalle."""

    def __init__(self, master, status: RepoStatus, on_click, **kwargs):
        super().__init__(master, fg_color=theme.CARD_BG, corner_radius=8, **kwargs)
        self.status = status
        self.on_click = on_click

        self.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            self, text="●", width=18,
            font=ctk.CTkFont(size=18),
            text_color=theme.dot_color(status.color),
        ).grid(row=0, column=0, rowspan=2, padx=(12, 6), pady=10)

        ctk.CTkLabel(
            self, text=status.name, anchor="w",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).grid(row=0, column=1, sticky="ew", pady=(10, 0))

        detalle = status.summary()
        if status.branch:
            detalle = f"{status.branch} · {detalle}"
        ctk.CTkLabel(
            self, text=detalle, anchor="w",
            font=ctk.CTkFont(size=12), text_color=theme.TEXT_MUTED,
        ).grid(row=1, column=1, sticky="ew", pady=(0, 10))

        ctk.CTkLabel(
            self, text=status.label, anchor="e",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=theme.dot_color(status.color),
        ).grid(row=0, column=2, sticky="e", padx=12, pady=(10, 0))

        ctk.CTkLabel(
            self, text=human_date(status.last_local_change), anchor="e",
            font=ctk.CTkFont(size=11), text_color=theme.TEXT_MUTED,
        ).grid(row=1, column=2, sticky="e", padx=12, pady=(0, 10))

        for widget in (self, *self.winfo_children()):
            widget.bind("<Button-1>", self._clic)
            widget.bind("<Enter>", self._entrar)
            widget.bind("<Leave>", self._salir)
            try:
                widget.configure(cursor="hand2")
            except Exception:
                pass

    def _clic(self, _evento=None) -> None:
        self.on_click(self.status)

    def _entrar(self, _evento=None) -> None:
        self.configure(fg_color=theme.CARD_HOVER)

    def _salir(self, _evento=None) -> None:
        self.configure(fg_color=theme.CARD_BG)


class RepoListView(ctk.CTkFrame):
    """El contenedor con scroll de todas las filas."""

    def __init__(self, master, on_select, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.on_select = on_select
        self.filtro = FILTER_ALL
        self.statuses: list[RepoStatus] = []

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        cabecera = ctk.CTkFrame(self, fg_color="transparent")
        cabecera.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        cabecera.grid_columnconfigure(1, weight=1)

        self.selector = ctk.CTkSegmentedButton(
            cabecera, values=list(FILTERS), command=self._cambiar_filtro,
        )
        self.selector.set(FILTER_ALL)
        self.selector.grid(row=0, column=0, sticky="w")

        self.cuenta = ctk.CTkLabel(
            cabecera, text="", font=ctk.CTkFont(size=12), text_color=theme.TEXT_MUTED,
        )
        self.cuenta.grid(row=0, column=1, sticky="e", padx=(12, 0))

        self.lista = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.lista.grid(row=1, column=0, sticky="nsew")
        self.lista.grid_columnconfigure(0, weight=1)

        self.vacio = ctk.CTkLabel(
            self.lista, text="", font=ctk.CTkFont(size=13), text_color=theme.TEXT_MUTED,
        )

    def _cambiar_filtro(self, valor: str) -> None:
        self.filtro = valor
        self.render()

    def set_statuses(self, statuses: list[RepoStatus]) -> None:
        self.statuses = list(statuses)
        self.render()

    def render(self) -> None:
        for hijo in self.lista.winfo_children():
            hijo.destroy()

        visibles = [s for s in self.statuses if matches_filter(s, self.filtro)]
        # Lo que necesita atención, primero.
        visibles.sort(key=lambda s: (not s.is_blocked, s.state is RepoState.UP_TO_DATE, s.name.lower()))

        atencion = sum(1 for s in self.statuses if s.is_blocked)
        self.cuenta.configure(
            text=f"{len(self.statuses)} proyectos"
                 + (f" · {atencion} necesitan tu atención" if atencion else "")
        )

        if not visibles:
            mensaje = (
                "Todavía no hay proyectos. Elige tu carpeta de proyectos en Ajustes."
                if not self.statuses else "Ningún proyecto encaja con este filtro."
            )
            ctk.CTkLabel(
                self.lista, text=mensaje, font=ctk.CTkFont(size=13),
                text_color=theme.TEXT_MUTED,
            ).grid(row=0, column=0, pady=40)
            return

        for fila, status in enumerate(visibles):
            RepoRow(self.lista, status, self.on_select).grid(
                row=fila, column=0, sticky="ew", pady=3, padx=2
            )
