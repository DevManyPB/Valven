<div align="center">

<img src="assets/icon.png" width="110" alt="Vaivén">

# Vaivén

**Mantén tus proyectos de GitHub iguales en dos ordenadores, con dos botones.**

[![Pruebas](https://img.shields.io/badge/pruebas-332%20pasando-brightgreen)](tests/)
[![Licencia](https://img.shields.io/badge/licencia-MIT-blue)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue)](https://www.python.org/)
[![Plataforma](https://img.shields.io/badge/plataforma-Windows-lightgrey)](#crear-el-programa)

</div>

---

Si trabajas en dos equipos —el portátil y el de casa— y estás cansado de
hacer `git pull` y `git push` proyecto por proyecto, Vaivén hace eso por ti:
**⬆ Subir todo** al terminar en un sitio, **⬇ Sincronizar todo** al sentarte
en el otro.

Lo que lo diferencia de un script es que **está construido para no perder tu
trabajo**. Antes de tocar nada te enseña qué va a pasar, te pide
confirmación, y guarda un respaldo que puedes deshacer. Los comandos de Git
que destruyen cosas están prohibidos por diseño: no es que la aplicación
evite usarlos, es que `git_ops.py` los rechaza lanzando una excepción.

> *English: Vaivén keeps your GitHub repositories in sync between two Windows
> computers with two buttons, refusing to run any destructive Git command and
> backing up before every operation. The interface and documentation are in
> Spanish. See [ARQUITECTURA.md](ARQUITECTURA.md) for the design.*

## Qué hace

- **Ve el estado de todos tus proyectos** de un vistazo, con colores.
- **Sube todo** lo que tengas sin subir, en todos los proyectos a la vez.
- **Sincroniza todo** lo que haya nuevo en GitHub, solo donde es seguro.
- **Se detiene** y te explica qué pasa cuando algo necesita tu decisión.
- **Deshace** cualquier operación, y deshacer también se puede deshacer.
- **Avisa** si estás a punto de subir un `.env`, una clave privada o un token
  pegado en el código, aunque ya lo tuvieras en un commit.
- **Inicia sesión** en GitHub desde el navegador, sin tokens que copiar.
- **Atajos:** <kbd>F5</kbd> o <kbd>Ctrl</kbd>+<kbd>R</kbd> revisan el estado;
  <kbd>Ctrl</kbd>+<kbd>,</kbd> abre los ajustes; <kbd>Esc</kbd> cierra el detalle.

## Cómo te protege

| | |
|---|---|
| **Nunca fuerza nada** | `push --force`, `reset --hard`, `clean -f` y compañía están prohibidos en el código, y solo se admite una lista cerrada de comandos. Hay 71 pruebas que lo verifican. |
| **Respalda antes de actuar** | Cada operación deja un punto de retorno, incluidos los archivos que aún no están en Git. |
| **No toca lo dudoso** | Si tienes trabajo más nuevo que GitHub, «Sincronizar todo» salta ese proyecto y te dice que lo subas primero. |
| **Solo avanza en falso** | Lo único que sincroniza solo es `git pull --ff-only`, que por diseño no puede sobrescribir nada. |
| **No filtra tu sesión** | El token nunca toca el disco ni el registro. Vive en el Administrador de credenciales de Windows. |

## Lo que necesitas

1. **Windows** (10 u 11).
2. **Git** 2.31 o posterior. Si no lo tienes, descárgalo de <https://git-scm.com> e instálalo
   con las opciones por defecto. Vaivén avisa si falta.
3. Una **cuenta de GitHub**.

## Crear el programa

Doble clic en **`build.bat`**. El script instala lo necesario y genera
**`dist\Vaiven.exe`**: un único archivo que puedes copiar donde quieras y
abrir con doble clic, sin necesidad de tener Python instalado.

> **Aviso de Windows la primera vez.** Como el `.exe` no está firmado
> digitalmente, SmartScreen puede decir «Windows protegió su PC». Pulsa
> **Más información** y después **Ejecutar de todas formas**. Solo pasa
> la primera vez.

Haz esto en cada uno de tus dos equipos, o genera el `.exe` una vez y cópialo
al otro.

**No hay nada que configurar.** Vaivén trae el Client ID público del proyecto
y funciona nada más abrirlo.

<details>
<summary><b>Opcional: iniciar sesión sin pegar el código</b></summary>

Por defecto, iniciar sesión es: pulsas el botón → se abre GitHub → pegas un
código de 8 caracteres que Vaivén ya ha copiado al portapapeles → Authorize.

Si prefieres que sea solo «pulsar y autorizar», sin código, necesitas tu
propia OAuth App:

1. <https://github.com/settings/developers> → **New OAuth App**.
2. **Authorization callback URL**, copiada tal cual:
   `http://127.0.0.1:49732/vaiven/callback`
3. Copia el **Client ID** y genera un **Client Secret**.
4. Crea `%APPDATA%\Vaiven\oauth.json`:

   ```json
   {
     "client_id": "Ov23li…",
     "client_secret": "…"
   }
   ```

Ese archivo está **fuera del repositorio** a propósito: un secreto en un
repositorio público deja de serlo. Vaivén detecta solo si existe y cambia de
método.

¿Por qué no viene así de serie? Porque GitHub exige un *client secret* para
ese flujo —sus OAuth Apps no admiten PKCE— y un secreto publicado en un
repositorio abierto no sirve de nada. El método por código no necesita
ninguno, y por eso es el que funciona para todo el mundo sin configurar nada.

</details>


## Cómo se usa

### La primera vez que lo abres

1. Pulsa **Iniciar sesión con GitHub**. Vaivén copia un código al
   portapapeles y abre tu navegador en GitHub.
2. Pega el código (Ctrl+V), pulsa **Continue** y luego **Authorize**. No
   volverá a pedírtelo.
3. Elige tu **carpeta de proyectos** (la que contiene todos tus repositorios).
4. Confirma el **nombre de este equipo** (`PORTATIL`, `PC-MESA`…). Ese nombre
   aparece luego en los cambios, para que sepas de dónde vienen.

### El día a día

- **Cuando termines de trabajar** en un equipo → **⬆ Subir todo**.
- **Cuando te sientes en el otro** → **⬇ Sincronizar todo**.

Antes de hacer nada, Vaivén siempre te enseña una pantalla con:

- **Se hará:** los proyectos que va a tocar y qué cambios exactamente.
- **Se omitirá:** los que necesitan tu atención, con el motivo explicado.
- **Sin cambios:** los que ya están al día.

Nada ocurre hasta que pulsas **Sí, continuar**.

### Los colores de la lista

| Color | Qué significa |
|---|---|
| 🟢 Verde | Al día: este equipo y GitHub son iguales. |
| 🔵 Azul | Tienes trabajo aquí que todavía no está en GitHub. |
| 🟡 Amarillo | GitHub tiene cambios nuevos que aún no has traído. |
| 🔴 Rojo | Necesita tu atención: ábrelo para ver las opciones. |
| ⚪ Gris | No aplica (sin rama en GitHub, no clonado, etc.). |

## Preguntas frecuentes

**¿Y si los dos equipos han cambiado el mismo proyecto?**
Vaivén lo marca en rojo como «Divergido» y no lo toca. Ábrelo y pulsa
**Intentar combinar**: si los cambios no se pisan, los une solos; si se
pisan, lo deja exactamente como estaba y te ofrece abrirlo en VS Code.

**¿Y si tengo cosas a medias sin guardar?**
No se pierden. Puedes usar **Guardar aparte y sincronizar**: Vaivén las
aparta, trae lo de GitHub y te las devuelve. Si chocan, se quedan guardadas y
te avisa.

**¿Dónde está mi contraseña?**
En ningún sitio. Tu contraseña la escribes en GitHub, en tu navegador;
Vaivén nunca la ve. La sesión que GitHub le devuelve se guarda en el
Administrador de credenciales de Windows, nunca en un archivo. Los registros
tampoco la contienen.

**¿Puedo quitarle el acceso a Vaivén?**
Sí, cuando quieras, desde
<https://github.com/settings/applications> → Vaivén → **Revoke access**.

**¿Dónde están los registros?**
En `%APPDATA%\Vaiven\logs`. Hay un botón para abrirlos en Ajustes.

**¿Puedo hacer que se abra al encender el equipo?**
Sí, en Ajustes → **Iniciar con Windows**. Al arrancar solo mira cómo está
todo y te avisa; nunca sube ni sincroniza por su cuenta.

---

## Contribuir

Las aportaciones son bienvenidas. Antes de nada, dos lecturas cortas:

- **[ARQUITECTURA.md](ARQUITECTURA.md)** — cómo está construido y por qué.
- **[CONTRIBUTING.md](CONTRIBUTING.md)** — cómo montar el entorno y qué se
  espera de un cambio.

Puesta en marcha rápida:

```bash
git clone https://github.com/DevManyPB/Valven.git
cd Valven
python -m venv .venv
.venv/bin/pip install -r requirements.txt     # Windows: .venv\Scripts\pip
.venv/bin/python -m pytest                    # 332 pruebas, ~15 s
.venv/bin/python src/main.py                  # ejecutar sin empaquetar
```

Las pruebas no tocan GitHub ni ningún repositorio real: montan un remoto
*bare* local y dos clones que hacen de portátil y PC de mesa.

**La regla irrenunciable del proyecto:** ningún cambio puede hacer que
Vaivén ejecute un comando de Git capaz de destruir trabajo del usuario. Si
una función nueva parece necesitarlo, casi seguro hay otra forma; y si de
verdad no la hay, se añade un permiso explícito en `git_ops.py` con su
prueba, nunca una excepción silenciosa.

## Documentación del proyecto

| Archivo | Qué contiene |
|---|---|
| [ARQUITECTURA.md](ARQUITECTURA.md) | Cómo está construido: módulos, flujos, diseño. |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Cómo desarrollar y enviar cambios. |
| [DECISIONES.md](DECISIONES.md) | Las 36 decisiones de diseño, con su porqué. |
| [SPEC.md](SPEC.md) | La especificación original del proyecto. |

## Licencia

[MIT](LICENSE) — úsalo, modifícalo y distribúyelo con libertad.
