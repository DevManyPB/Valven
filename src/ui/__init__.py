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


#: Longitud máxima de una frase de una sola línea (barra de estado, avisos).
MAX_ETIQUETA = 160


def etiqueta_segura(texto, *, maximo: int = MAX_ETIQUETA) -> str:
    """Acorta un texto de una sola línea antes de ponerlo en una etiqueta.

    Una etiqueta de Tk no ajusta el texto si no tiene ``wraplength``, así que
    dibuja la línea entera de una vez. Basta con que a una de estas frases
    llegue algo inesperadamente largo —la representación de un objeto, la
    salida de un comando— para que la aplicación se caiga.
    """
    linea = " ".join(str(texto).split())
    return linea if len(linea) <= maximo else f"{linea[:maximo]}…"


#: Cómo se llama cada tipo de cambio en pantalla (sin jerga de Git).
NOMBRES_CAMBIO = {
    "modified": "modificado", "added": "nuevo", "deleted": "borrado",
    "renamed": "renombrado", "untracked": "nuevo", "conflict": "en conflicto",
}


def ruta_corta(ruta, *, maximo: int = 70) -> str:
    """Acorta una ruta por la izquierda, que es lo menos informativo.

    ``C:\\Users\\jhon\\Documentos\\Proyectos\\web`` se queda en
    ``…\\Proyectos\\web``: el final es lo que distingue un proyecto de otro.
    """
    texto = str(ruta)
    if len(texto) <= maximo:
        return texto
    cola = texto[-(maximo - 1):]
    for separador in ("/", "\\"):
        corte = cola.find(separador)
        if corte > 0:
            cola = cola[corte:]
            break
    return f"…{cola}"


def imagen_redonda(imagen, tamano: int):
    """Recorta una imagen de Pillow en círculo, lista para un avatar.

    Se escala al doble y se reduce al final para que el borde no salga
    dentado: Tk no suaviza las imágenes.
    """
    from PIL import Image, ImageDraw

    grande = tamano * 4
    cuadrada = imagen.convert("RGBA").resize((grande, grande), Image.LANCZOS)
    mascara = Image.new("L", (grande, grande), 0)
    ImageDraw.Draw(mascara).ellipse((0, 0, grande - 1, grande - 1), fill=255)
    cuadrada.putalpha(mascara)
    return cuadrada.resize((tamano, tamano), Image.LANCZOS)


def descargar_avatar(url: str | None, tamano: int = 40):
    """Foto de perfil de GitHub, redonda, o ``None`` si no se puede.

    Es una imagen pública: la petición no lleva la sesión del usuario.
    """
    if not url:
        return None
    try:
        import io

        import requests
        from PIL import Image

        separador = "&" if "?" in url else "?"
        respuesta = requests.get(f"{url}{separador}s={tamano * 2}", timeout=10)
        respuesta.raise_for_status()
        return imagen_redonda(Image.open(io.BytesIO(respuesta.content)), tamano)
    except Exception:
        return None

