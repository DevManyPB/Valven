# Contribuir a Vaivén

Gracias por el interés. Esta guía cuenta cómo montar el entorno, qué se
espera de un cambio y dónde está cada cosa.

## Antes de empezar

Lee **[ARQUITECTURA.md](ARQUITECTURA.md)**. Son quince minutos y explican por
qué el código está organizado así; sin eso, muchas decisiones parecen
arbitrarias cuando no lo son.

## Montar el entorno

```bash
git clone https://github.com/DevManyPB/Valven.git
cd Valven
python -m venv .venv

# Linux / macOS
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest
.venv/bin/python src/main.py

# Windows
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m pytest
.venv\Scripts\python src\main.py
```

Necesitas **Python 3.12 o superior** y **Git** instalado. Se desarrolla en
cualquier sistema; solo se empaqueta para Windows.

## La regla irrenunciable

> **Ningún cambio puede hacer que Vaivén ejecute un comando de Git capaz de
> destruir trabajo del usuario.**

`src/git_ops.py` es la única puerta a `git`, y rechaza esos comandos lanzando
`ForbiddenGitCommand`. Si tu función parece necesitar uno:

1. Casi siempre hay otra forma. Búscala primero.
2. Si de verdad no la hay, añade un **permiso explícito** con nombre propio
   (como `ALLOW_BACKUP_RESTORE`), documenta por qué en `DECISIONES.md`, y
   añade pruebas de que ese permiso no habilita a los demás y de que ninguna
   acción masiva puede pasarlo.
3. Nunca añadas una excepción silenciosa ni llames a `subprocess` saltándote
   `git_ops`.

Hay una prueba que comprueba lo primero y otra que recorre las vistas
buscando atajos. Si las rompes, es a propósito o es un error.

## Qué se espera de un cambio

- **Pruebas.** Todo arreglo trae una prueba que falla antes y pasa después.
  Toda función nueva trae pruebas de su comportamiento, no de su
  implementación.
- **Sin tocar nada real.** Las pruebas no contactan con GitHub ni usan
  repositorios del sistema. `tests/conftest.py` monta un remoto *bare* local
  y dos clones que hacen de portátil y PC de mesa; úsalo.
- **Español, y sin jerga en la interfaz.** El código, los comentarios y los
  mensajes están en español. En la pantalla principal se dice «cambios sin
  subir», no «working tree dirty»; los términos técnicos pueden aparecer en
  la vista de detalle.
- **Comentarios que expliquen el porqué.** El qué ya lo dice el código. Los
  comentarios valiosos de este proyecto son los que explican por qué algo
  está hecho de una forma que parece rara (y suele haber un motivo medido).
- **La ventana nunca se congela.** Todo lo que hable con Git o con la red va
  en un hilo de fondo y vuelve con `call_on_ui_thread()`.
- **Todo texto de Git se acota.** Si vas a mostrar salida de `git` en un
  widget, pásala por `texto_seguro()`. Una sola línea larga puede tumbar la
  aplicación; está medido en ARQUITECTURA.md.

## Ejecutar las pruebas

```bash
.venv/bin/python -m pytest                 # las 328
.venv/bin/python -m pytest -k safety       # solo respaldos
.venv/bin/python -m pytest -v tests/test_sync_scenarios.py
```

Si alguna falla de forma intermitente, dilo en la incidencia: ya ha pasado
un par de veces y siempre ha sido una carrera real, no mala suerte.

## Empaquetar

```
build.bat
```

Genera `dist\Vaiven.exe` con PyInstaller. Solo funciona en Windows.

## Credenciales

**Nunca** subas un Client Secret al repositorio. El Client ID sí es público
y está en `src/auth.py` a propósito. Si quieres probar el inicio de sesión
web directo, pon tus credenciales en `%APPDATA%\Vaiven\oauth.json`
(o `~/.config/vaiven/oauth.json` fuera de Windows), que está ignorado.

Si bifurcas el proyecto y quieres usar tu propia OAuth App, recuerda marcar
**Enable Device Flow** en su configuración de GitHub: es lo que permite el
inicio de sesión por código, que es el que funciona sin ningún secreto y por
tanto el predeterminado para quien solo descarga la aplicación.

## Incidencias

Al abrir una incidencia ayuda mucho incluir:

- Qué esperabas y qué pasó.
- El estado en el que estaba el proyecto (el color y la etiqueta que muestra
  Vaivén).
- Las últimas líneas de `%APPDATA%\Vaiven\logs\vaiven.log`. El registro
  **no contiene tu token**: hay un filtro que lo censura. Aun así, échale un
  vistazo antes de pegarlo.

## Ideas que encajan

Cosas que el proyecto dejó fuera a propósito y que se estudiarían con gusto:

- Empaquetado para macOS y Linux (el código ya es portable).
- Resolución de conflictos dentro de la aplicación.
- Soporte para Git LFS.
- Traducción de la interfaz a otros idiomas.
