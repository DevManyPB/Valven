# Cómo está construido Vaivén

Esta guía explica el diseño interno: qué hace cada módulo, cómo fluye la
información y por qué está hecho así. Si solo quieres **usar** la aplicación,
el [README](README.md) es suficiente.

---

## La idea de fondo

Vaivén no intenta ser un cliente de Git. Es una capa fina sobre el `git` que
ya tienes instalado, construida alrededor de una sola obsesión:

> **Nunca perder trabajo del usuario.** Ante cualquier duda, detenerse,
> explicar y ofrecer opciones; jamás decidir por su cuenta algo irreversible.

Casi todas las decisiones de diseño se explican por esa frase. Si alguna vez
dudas de cómo implementar algo aquí, elige lo que más conserve.

---

## Mapa de módulos

```
                        ┌──────────────┐
                        │   src/ui/    │  Interfaz (CustomTkinter)
                        └──────┬───────┘
                               │  solo llama hacia abajo
         ┌─────────────────────┼─────────────────────┐
         ▼                     ▼                     ▼
  ┌─────────────┐      ┌──────────────┐      ┌──────────────┐
  │ sync_engine │─────▶│    safety    │      │ auth         │
  │  qué hacer  │      │  respaldos   │      │ github_api   │
  └──────┬──────┘      └──────┬───────┘      └──────┬───────┘
         │                    │                     │
         ▼                    ▼                     │
  ┌─────────────┐      ┌──────────────┐             │
  │  analyzer   │─────▶│   git_ops    │◀────────────┘
  │ qué pasa    │      │ ÚNICA puerta │
  └─────────────┘      │   a git.exe  │
                       └──────────────┘
                              │
                       ┌──────┴───────┐
                       │  config.py   │  preferencias
                       │  logger.py   │  registro censurado
                       └──────────────┘
```

Las dependencias van siempre hacia abajo. La interfaz no ejecuta Git nunca;
`git_ops` no sabe nada de ventanas. Por eso todo lo importante se puede
probar sin abrir una sola ventana.

| Módulo | Responsabilidad | Líneas aprox. |
|---|---|---|
| `git_ops.py` | Única puerta a `git`. Rechaza comandos destructivos. | 420 |
| `analyzer.py` | Calcula el estado de cada repositorio. Solo lee. | 450 |
| `safety.py` | Respaldos, restaurar, deshacer, limpieza. | 400 |
| `sync_engine.py` | Planifica y ejecuta «Subir todo» / «Sincronizar todo». | 560 |
| `auth.py` | Inicio de sesión con GitHub, dos caminos. | 480 |
| `github_api.py` | Usuario y lista de repositorios. Solo lectura. | 160 |
| `config.py` | Preferencias en `%APPDATA%\Vaiven\config.json`. | 150 |
| `logger.py` | Registro rotativo con censura de credenciales. | 120 |
| `startup.py` | «Iniciar con Windows». | 150 |
| `main.py` | Arranque, instancia única, comprobación de Git. | 160 |
| `ui/` | Seis vistas + tema + utilidades de hilos. | 1.900 |

---

## `git_ops.py` — el cuello de botella deliberado

Todo comando de Git pasa por `git_ops.run()`, y **solo** por ahí. Esa
restricción es lo que hace posible garantizar la seguridad: basta auditar un
archivo para saber qué puede llegar a ejecutarse.

`validate_args()` rechaza, lanzando `ForbiddenGitCommand`:

| Familia | Por qué |
|---|---|
| `push --force`, `-f`, `--force-with-lease`, `--delete`, `:rama`, `--mirror` | reescriben o borran el historial de GitHub |
| `reset --hard`, `--merge` | borran los cambios sin guardar |
| `clean -f`, `-fd`, `-ffdx` | borran archivos que Git no sigue |
| `checkout -- <ruta>`, `checkout -f`, `switch -f`, `restore` | descartan cambios sin guardar |
| `pull` sin `--ff-only` | podría sobrescribir trabajo al combinar |
| `branch -D`, `update-ref -d` fuera del espacio de respaldos | borran referencias del usuario |
| `stash drop`, `stash clear` | el escondite guarda respaldos |
| `reflog expire`, `gc --prune`, `filter-branch` | destruyen la red de seguridad de Git |

El analizador de argumentos salta las opciones globales (`git -c … push -f`
también se rechaza), entiende las opciones cortas agrupadas (`clean -ffdx`) y
distingue las refspecs destructivas (`push origin :main`).

### Permisos explícitos

Tres operaciones son peligrosas en general pero imprescindibles en un flujo
concreto y ya respaldado. Solo funcionan si quien llama las pide por su
nombre, y **ninguna acción masiva puede pasarlos**:

```python
run(["pull", "--rebase"], allow={ALLOW_REBASE_PULL})        # combinar divergido
run(["reset", "--hard", sha], allow={ALLOW_BACKUP_RESTORE}) # restaurar respaldo
run(["update-ref", "-d", ref], allow={ALLOW_BACKUP_REF_DELETE})
```

Un permiso no habilita a los demás. Hay pruebas que lo comprueban.

### Autenticación sin tocar el disco

La sesión de GitHub viaja en el entorno del proceso `git` y nunca se escribe
en `.git/config` ni aparece en la línea de comandos:

```
GIT_CONFIG_COUNT=1
GIT_CONFIG_KEY_0=http.https://github.com/.extraheader
GIT_CONFIG_VALUE_0=AUTHORIZATION: basic <base64>
```

Es lo mismo que `git -c http...extraheader=...`, pero la línea de comandos la
puede leer cualquier proceso del equipo y el entorno no (D38). Y por si
acaso, `logger.py` instala un filtro que censura tokens y cabeceras en
**cualquier** mensaje, venga de donde venga.

---

## `analyzer.py` — el estado de cada proyecto

Antes de ofrecer o ejecutar cualquier acción se hace `git fetch` y se calcula
el estado. El orden de decisión va de lo que exige intervención humana a lo
que se puede automatizar:

```
¿merge/rebase a medias o archivos en conflicto?  → En conflicto      🔴
¿HEAD suelto?                                    → HEAD suelto       ⚪
¿sin rama en GitHub?                             → Sin rama remota   ⚪
¿commits en ambos lados?                         → Divergido         🔴
¿atrasado y con cambios sin guardar?             → Atrasado + local  🔴
¿atrasado y limpio?                              → Atrasado          🟡  ← lo único
¿commits sin subir?                              → Adelantado        🔵     automatizable
¿archivos sin guardar?                           → Cambios locales   🔵
                                                 → Al día            🟢
```

**Solo el estado amarillo se sincroniza automáticamente**, y siempre con
`git pull --ff-only`, que por diseño no puede sobrescribir trabajo.

`RepoStatus` conserva siempre los contadores y la lista de archivos aunque el
estado sea otro, para que la interfaz pueda decir «3 commits y 5 archivos sin
subir» en vez de una etiqueta seca.

Cada commit entrante o saliente se lee con su asunto, autor, fecha y **equipo
de origen**, que sale del trailer `Synced-From:` que Vaivén añade a lo que
sube. Por eso la lista puede decir «GitHub tiene 1 cambio nuevo del PORTATIL».

---

## `safety.py` — respaldos que sí incluyen los archivos nuevos

Antes de cualquier operación que modifique un repositorio se crea un respaldo
con **tres** partes:

```
refs/vaiven-backup/<fecha>_<EQUIPO>              → dónde estaba HEAD
refs/vaiven-backup/<fecha>_<EQUIPO>-stash        → cambios de archivos seguidos
refs/vaiven-backup/<fecha>_<EQUIPO>-untracked    → archivos nuevos
```

La tercera existe por un detalle que cuesta descubrir:

> `git stash create --include-untracked` **acepta la opción y la ignora**. El
> commit que devuelve solo tiene dos padres, sin árbol de archivos no
> seguidos. Verificado con Git 2.53.

Así que los archivos nuevos se guardan a mano: se listan con
`git ls-files --others --exclude-standard` usando el índice real, y después
se escriben en un **índice temporal** (`GIT_INDEX_FILE`) para construir un
árbol propio. El índice del usuario no se toca en ningún momento. El orden
importa: calcular la lista con el índice temporal ya activo haría que Git
considerase «nuevos» también todos los archivos seguidos.

Los respaldos viven bajo `refs/vaiven-backup/`, que Git no replica: **nunca
se suben a GitHub**. Se conservan 30 días o los últimos 20 por repositorio,
lo que sea mayor — es decir, uno se borra solo si cumple las dos condiciones.

Restaurar crea primero un respaldo del estado actual, de modo que **deshacer
también es reversible**.

---

## `sync_engine.py` — planificar y ejecutar, separados

```
     analyze_all()          plan_push()/plan_sync()       PreviewDialog        execute()
  ┌─────────────────┐     ┌────────────────────────┐    ┌─────────────┐    ┌────────────┐
  │ estado de todos │────▶│ Se hará / Se omitirá / │───▶│ el usuario  │───▶│ repo a repo│
  │ los proyectos   │     │ Sin cambios + avisos   │    │  confirma   │    │ + respaldo │
  └─────────────────┘     └────────────────────────┘    └─────────────┘    └────────────┘
       solo lee                   no toca nada            puede excluir      única fase
                                                          proyectos          que escribe
```

Esa separación es lo que hace fiable la vista previa, y permite probar todos
los escenarios sin interfaz.

**Un plan no es una autorización para actuar a ciegas.** Entre la vista
previa y la ejecución pueden pasar minutos, así que:

- `sync_repo()` repite el análisis y se detiene si el repositorio ya no está
  «Atrasado y limpio».
- `push_repo()` hace `fetch` después de confirmar, y si GitHub avanzó mientras
  tanto **no sube nada**: marca el repositorio como Divergido.

Los avisos de la vista previa detectan archivos de más de 50 MB (GitHub
rechaza a partir de 100) y secretos, de dos maneras: por el nombre del
archivo (`.env`, `*.pem`, `id_rsa*`, `credentials*.json`…) y por su contenido
(tokens de GitHub, claves privadas, claves de AWS, Google, Slack o Stripe).
Se revisan los cambios sin guardar **y** todos los commits pendientes de
subir, porque los dos acaban en GitHub (D44). Si hay secretos, el botón de confirmar queda deshabilitado hasta que el usuario los
excluya o marque «Entiendo el riesgo».

---

## Inicio de sesión

Hay dos caminos y la aplicación usa el primero que pueda:

| | Web directo | Por código (reserva) |
|---|---|---|
| Qué ve el usuario | Pulsa «Authorize» y ya | Pega un código ya copiado |
| Necesita Client Secret | **Sí** | No |
| Necesita un puerto libre | Sí (`127.0.0.1:49732`) | No |
| Cuándo se usa | Si hay `oauth.json` propio | Por defecto, y si el puerto falla |

**Por qué hacen falta los dos.** Las OAuth Apps de GitHub exigen
`client_secret` para canjear el código del login web, y un secreto en un
repositorio público no es un secreto. El login web usa además PKCE y solo
acepta la respuesta que trae el `state` de su propio intento (D43), pero eso
no sustituye al secreto. Así que:

- El repositorio trae solo el **Client ID público**, con el que el inicio de
  sesión por código funciona sin configurar nada.
- Quien quiera el web directo pone sus credenciales en
  `%APPDATA%\Vaiven\oauth.json`, que está fuera del repositorio.

La sesión resultante se guarda **solo** en el almacén de credenciales del
sistema (`keyring`), nunca en un archivo.

---

## La interfaz

Regla única: **nada bloquea la ventana**. Todo lo que hable con Git o con la
red va a un hilo de fondo y vuelve con `call_on_ui_thread()`, que descarta el
aviso en silencio si la ventana ya se cerró.

Hay una prueba que falla si alguna vista llama a `self.after(0, …)`
directamente.

### Todo texto se acota antes de dibujarlo

Este proyecto se cayó dos veces por lo mismo, y merece la pena contarlo
porque el síntoma aparecía muy lejos de la causa.

**La barra de estado.** `analyzer.analyze_all()` y `sync_engine.execute()`
informan del avance con una llamada de la misma forma, pero una entregaba el
nombre del repositorio y la otra el objeto `RepoStatus` completo. La barra
hacía `f"… {nombre}…"`, así que en un proyecto con 622 archivos convertía el
objeto entero en una cadena de **76.673 caracteres** y la metía en una
etiqueta de una sola línea. El servidor gráfico se negaba a reservar ese mapa
de píxeles y la aplicación moría con `X BadAlloc`.

Hoy las dos llamadas entregan un nombre, `etiqueta_segura()` acorta a 160
caracteres cualquier texto de una línea, y la barra de estado solo se escribe
desde `VaivenApp._estado()`.

**Las diferencias.**

Tk reserva, para cada línea de un widget de texto, un mapa de píxeles tan
ancho como la línea entera. Medido con `git diff` de un archivo de una sola
línea:

| Longitud | Antes | Ahora |
|---|---|---|
| 200.000 caracteres | 19 s congelada | 0,1 s |
| 2.000.000 caracteres | cuelgue → `X BadAlloc` | 0,1 s |

No es rebuscado: un JavaScript minificado o un `package-lock.json` pasan de
ahí con facilidad. `texto_seguro()` recorta cada línea a 400 caracteres y el
total a 1.500 líneas. Hay una prueba que recorre las vistas y falla si alguna
inserta texto de Git sin pasarlo por ese filtro.

---

## Las pruebas

328 pruebas, sin tocar GitHub ni ningún repositorio real.

```
tests/conftest.py            dos clones de un remoto bare local = dos equipos
tests/test_git_ops.py        71 comandos destructivos rechazados, 28 permitidos
tests/test_analyzer.py       los 10 estados, cada uno provocado de verdad
tests/test_safety.py         respaldar, estropear, restaurar, comparar
tests/test_sync_scenarios.py los escenarios completos de dos equipos
tests/test_auth.py           Device Flow y login web con un GitHub simulado
tests/test_ui_logic.py       filtros, fechas, instancia única, empaquetado
```

El andamiaje monta un repositorio *bare* local que hace de GitHub y dos
clones que hacen de portátil y PC de mesa. Eso permite provocar de verdad
cada situación —divergido, conflicto a medias, push rechazado porque el
remoto avanzó— en vez de simularla.

Las pruebas usan `subprocess` directamente, no `git_ops`, para que el
andamiaje no dependa del código que está probando.

```bash
.venv/bin/python -m pytest          # todo
.venv/bin/python -m pytest -k safety
```

---

## Decisiones

`DECISIONES.md` recoge las 36 decisiones de diseño con su porqué: las
ambigüedades del SPEC y cómo se resolvieron, los fallos encontrados en uso
real y los límites que se eligieron. Si algo del código parece raro, es
probable que la explicación esté ahí.

`SPEC.md` es la especificación original del proyecto.
