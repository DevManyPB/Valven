"""Punto de entrada de Vaivén.

Se encarga de lo que debe ocurrir antes de que aparezca la ventana:

* comprobar que Git está instalado (sección 9.2.1),
* asegurarse de que no hay otra copia abierta (sección 10),
* preparar el registro y la configuración.
"""

from __future__ import annotations

import os
import socket
import sys
import threading
from pathlib import Path

# Permite ejecutar «python src/main.py» además de «python -m src.main».
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "src"

from . import config as config_module  # noqa: E402
from . import git_ops, logger  # noqa: E402

#: Puerto local que se usa como cerrojo de instancia única (sección 10).
SINGLE_INSTANCE_PORT = 49731


class SingleInstance:
    """Cerrojo de una sola instancia, con aviso a la ventana ya abierta.

    Se ocupa un puerto de escucha en el propio equipo. Si ya está ocupado es
    que Vaivén está abierto: se le manda un aviso para que traiga su ventana
    al frente y esta copia se cierra sin hacer nada.
    """

    def __init__(self, port: int = SINGLE_INSTANCE_PORT) -> None:
        self.port = port
        self._socket: socket.socket | None = None

    def acquire(self) -> bool:
        servidor = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if os.name != "nt":
            # Sin esto, un puerto recién cerrado queda en TIME_WAIT unos
            # segundos y Vaivén creería que ya hay otra copia abierta: al
            # cerrarlo y volver a abrirlo enseguida, no arrancaría.
            # En Windows no se pone: allí SO_REUSEADDR deja que dos procesos
            # ocupen el mismo puerto y el cerrojo dejaría de servir de nada.
            servidor.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            servidor.bind(("127.0.0.1", self.port))
            servidor.listen(1)
        except OSError:
            servidor.close()
            return False
        self._socket = servidor
        return True

    def notify_existing(self) -> None:
        """Pide a la copia que ya está abierta que se muestre."""
        try:
            with socket.create_connection(("127.0.0.1", self.port), timeout=2) as conexion:
                conexion.sendall(b"mostrar")
        except OSError:
            pass

    def listen(self, on_message) -> None:
        """Atiende avisos de otras copias en un hilo de fondo."""
        if self._socket is None:
            return

        def bucle() -> None:
            while True:
                try:
                    conexion, _ = self._socket.accept()  # type: ignore[union-attr]
                except OSError:
                    return
                with conexion:
                    try:
                        conexion.recv(32)
                    except OSError:
                        continue
                on_message()

        threading.Thread(target=bucle, daemon=True).start()

    def release(self) -> None:
        if self._socket is not None:
            self._socket.close()
            self._socket = None


def check_git() -> str | None:
    """Sección 9.2.1: sin Git no se puede continuar."""
    try:
        return git_ops.git_version()
    except git_ops.GitError:
        return None


def show_git_missing() -> None:
    """Aviso claro con el enlace de descarga, sin abrir la app entera."""
    import tkinter
    from tkinter import messagebox

    raiz = tkinter.Tk()
    raiz.withdraw()
    messagebox.showerror(
        "Falta Git",
        "Vaivén necesita Git para funcionar y no lo ha encontrado en este equipo.\n\n"
        "Descárgalo de https://git-scm.com, instálalo con las opciones por defecto "
        "y vuelve a abrir Vaivén.",
    )
    raiz.destroy()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv

    log = logger.setup()
    log.info("Vaivén arrancando")

    cerrojo = SingleInstance()
    if not cerrojo.acquire():
        log.info("ya había una copia abierta; se le pasa el testigo")
        cerrojo.notify_existing()
        return 0

    try:
        version = check_git()
        if version is None:
            log.error("Git no está instalado")
            show_git_missing()
            return 1
        log.info("usando %s", version)

        configuracion = config_module.Config.load()

        from .ui.app_window import VaivenApp

        app = VaivenApp(configuracion)

        def traer_al_frente() -> None:
            app.after(0, lambda: (app.deiconify(), app.lift(), app.focus_force()))

        cerrojo.listen(traer_al_frente)
        app.mainloop()
        return 0
    finally:
        cerrojo.release()
        log.info("Vaivén cerrado")


if __name__ == "__main__":
    raise SystemExit(main())
