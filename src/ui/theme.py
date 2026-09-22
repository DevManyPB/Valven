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

TEXT_MUTED = ("#6b7280", "#9ca3af")
CARD_BG = ("#f3f4f6", "#2b2b2b")
CARD_HOVER = ("#e5e7eb", "#343434")
DANGER = ("#b91c1c", "#f87171")
SUCCESS = ("#15803d", "#4ade80")

PUSH_COLOR = ("#1d4ed8", "#1d4ed8")
PUSH_HOVER = ("#1e40af", "#1e40af")
SYNC_COLOR = ("#047857", "#047857")
SYNC_HOVER = ("#065f46", "#065f46")


def dot_color(nombre: str) -> tuple[str, str]:
    return STATE_DOT.get(nombre, STATE_DOT["gris"])
