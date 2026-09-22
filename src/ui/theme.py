"""Colores y tipografías compartidos por toda la interfaz."""

from __future__ import annotations

#: Color del punto de estado de cada proyecto (sección 6.2).
#: Cada entrada es (modo claro, modo oscuro).
STATE_DOT = {
    "verde": ("#15803d", "#4ade80"),
    "azul": ("#1d4ed8", "#60a5fa"),
    "amarillo": ("#a16207", "#fbbf24"),
    "rojo": ("#b91c1c", "#f87171"),
    "gris": ("#6b7280", "#9ca3af"),
}

TEXT = ("#111827", "#e5e7eb")
TEXT_MUTED = ("#6b7280", "#9ca3af")
BORDER = ("#d1d5db", "#4b5563")
CARD_BG = ("#f3f4f6", "#2b2b2b")
CARD_HOVER = ("#e5e7eb", "#343434")
DANGER = ("#b91c1c", "#f87171")
SUCCESS = ("#15803d", "#4ade80")

#: Botón principal desactivado: gris, para que no parezca pulsable.
DISABLED_BG = ("#d1d5db", "#3f3f46")
DISABLED_TEXT = ("#6b7280", "#a1a1aa")

PUSH_COLOR = ("#1d4ed8", "#1d4ed8")
PUSH_HOVER = ("#1e40af", "#1e40af")
SYNC_COLOR = ("#047857", "#047857")
SYNC_HOVER = ("#065f46", "#065f46")


def secundario(**cambios) -> dict:
    """Estilo de los botones con borde y sin relleno.

    CustomTkinter pinta el texto de los botones en blanco en los dos temas,
    pensando en el relleno azul. Sin relleno, en el tema claro el texto
    quedaba blanco sobre gris claro: invisible. Aquí el texto y el borde
    siguen al tema. ``cambios`` sustituye lo que haga falta (p. ej. el color
    del texto de un botón peligroso).
    """
    estilo = {
        "fg_color": "transparent",
        "border_width": 1,
        "border_color": BORDER,
        "text_color": TEXT,
        "hover_color": CARD_HOVER,
    }
    estilo.update(cambios)
    return estilo


#: Selector de pestañas legible en los dos temas: la opción elegida es una
#: «pastilla» clara sobre gris en el tema claro, y azul en el oscuro.
SEGMENTADO = {
    "fg_color": ("#e5e7eb", "#2b2b2b"),
    "unselected_color": ("#e5e7eb", "#2b2b2b"),
    "unselected_hover_color": ("#d1d5db", "#343434"),
    "selected_color": ("#ffffff", "#1f6aa5"),
    "selected_hover_color": ("#f9fafb", "#144870"),
    "text_color": TEXT,
}


def dot_color(nombre: str) -> tuple[str, str]:
    return STATE_DOT.get(nombre, STATE_DOT["gris"])
