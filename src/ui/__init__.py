"""Interfaz de Vaivén."""

from __future__ import annotations

import tkinter


def call_on_ui_thread(widget, callback, *args) -> bool:
    """Devuelve el control al hilo de la ventana desde un hilo de fondo.

    Tkinter solo permite tocar widgets desde su propio hilo, y ``after`` es la
    única forma segura de hacerlo. Si la ventana ya se cerró mientras la
    operación de fondo seguía en marcha, no hay nada que actualizar y no debe
    romperse nada: simplemente se descarta el aviso.
    """
    try:
        widget.after(0, callback, *args)
        return True
    except (RuntimeError, tkinter.TclError):
        return False


#: Límites con los que Tk puede dibujar sin ahogarse.
MAX_ANCHO_LINEA = 400
MAX_LINEAS = 1500


def texto_seguro(
    texto: str, *, max_lineas: int = MAX_LINEAS, max_ancho: int = MAX_ANCHO_LINEA
) -> str:
    """Recorta un texto para que se pueda dibujar sin bloquear la ventana.

    Tk reserva para cada línea un mapa de píxeles tan ancho como la línea
    entera. Una sola línea larga —un JavaScript minificado, un JSON en una
    sola línea, un ``package-lock.json``— basta para congelar la ventana
    durante decenas de segundos y, si es lo bastante larga, para que el
    servidor gráfico se niegue a reservar memoria y la aplicación se caiga.

    Recortar es aquí lo correcto: esta ventana sirve para echar un vistazo,
    y para leer el contenido entero está el botón «Abrir en VS Code».
    """
    if not texto:
        return ""
    lineas = texto.splitlines()
    recortadas: list[str] = []
    for linea in lineas[:max_lineas]:
        if len(linea) > max_ancho:
            linea = f"{linea[:max_ancho]} … [línea recortada, {len(linea):,} caracteres]"
        recortadas.append(linea)
    sobran = len(lineas) - max_lineas
    if sobran > 0:
        recortadas.append("")
        recortadas.append(f"… y {sobran:,} líneas más. Ábrelo en VS Code para verlo entero.")
    return "\n".join(recortadas)
