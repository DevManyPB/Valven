# SPEC — Vaivén

**Vaivén** es una aplicación de escritorio para Windows (.exe) que mantiene sincronizados todos los repositorios de GitHub del usuario entre su portátil y su PC de mesa, con dos acciones principales ("Subir todo" y "Sincronizar todo"), inicio de sesión con GitHub en el navegador y un sistema de seguridad que impide perder trabajo.

> **Instrucciones para Claude Code:** implementa este proyecto por fases, en el orden de la sección 12. Al terminar cada fase, verifica los criterios de aceptación de esa fase antes de continuar. No uses nunca comandos destructivos de Git (ver sección 6.1). Si algo de este documento es ambiguo, elige la opción más segura para los datos del usuario y déjalo anotado en `DECISIONES.md`.

---

## 0. Nombre de la aplicación

- Nombre visible para el usuario (título de ventana, textos, README, nombre de la OAuth App en GitHub): **Vaivén** (con tilde).
- Nombre técnico en archivos, carpetas, ejecutable y referencias de Git: **Vaiven** / `vaiven` (sin tilde, para evitar problemas de codificación en rutas y comandos).
- El icono debe transmitir la idea de ir y volver entre dos equipos (por ejemplo, dos flechas en direcciones opuestas formando un ciclo).

## 1. Objetivo y contexto

El usuario trabaja en dos equipos Windows (portátil y PC de mesa), ambos con Git instalado. Hoy tiene que hacer `git pull` / `git push` manualmente en cada proyecto. Quiere:

1. Una app que se abra con doble clic (un `.exe`), sin escribir comandos.
2. Iniciar sesión en GitHub desde el navegador, sin copiar ni pegar tokens.
3. Un botón **"Subir todo"** que suba los cambios de todos los proyectos.
4. Un botón **"Sincronizar todo"** que baje los cambios de GitHub a todos los proyectos.
5. Ver el estado de cada proyecto y los cambios antes de hacer nada.
6. Que la app **pida confirmación** y **nunca haga perder cambios**, especialmente si la copia local va más adelantada que GitHub.

## 2. Stack técnico

- **Lenguaje:** Python 3.12
- **Interfaz:** CustomTkinter (aspecto moderno, modo claro/oscuro, ligero)
- **HTTP:** `requests` (API de GitHub)
- **Almacenamiento seguro de la sesión:** `keyring` (usa el Administrador de credenciales de Windows)
- **Empaquetado:** PyInstaller, modo `--onefile --windowed`, con icono propio
- **Git:** se usa el `git.exe` ya instalado en el sistema, invocado vía `subprocess`
- **Sin dependencias de IA:** la app no usa ningún modelo ni API de Anthropic.

Requisito técnico importante: toda llamada a `subprocess` debe usar `creationflags=subprocess.CREATE_NO_WINDOW` para que no aparezcan ventanas negras de consola al ejecutar Git desde el `.exe`.

Todas las operaciones de Git y de red se ejecutan en hilos de fondo; la interfaz nunca se congela. Se muestra progreso por repositorio.

## 3. Estructura del proyecto

```
vaiven/
├── SPEC.md
├── DECISIONES.md
├── README.md                 # instrucciones de uso para el usuario final
├── requirements.txt
├── build.bat                 # genera dist/Vaiven.exe con un doble clic
├── assets/
│   └── icon.ico
├── src/
│   ├── main.py               # punto de entrada
│   ├── config.py             # lectura/escritura de configuración
│   ├── auth.py               # login con GitHub (Device Flow) + keyring
│   ├── github_api.py         # listar repos, datos del usuario
│   ├── git_ops.py            # wrapper seguro sobre git.exe
│   ├── analyzer.py           # calcula el estado de cada repo
│   ├── safety.py             # respaldos, reglas de bloqueo, deshacer
│   ├── sync_engine.py        # orquesta "Subir todo" y "Sincronizar todo"
│   ├── startup.py            # opción "Iniciar con Windows"
│   ├── logger.py             # log rotativo
│   └── ui/
│       ├── app_window.py
│       ├── login_view.py
│       ├── repo_list_view.py
│       ├── preview_dialog.py # vista previa + confirmación
│       ├── repo_detail_view.py
│       └── settings_view.py
└── tests/
    ├── test_analyzer.py
    ├── test_safety.py
    └── test_sync_scenarios.py
```

## 4. Configuración y datos locales

Carpeta: `%APPDATA%\Vaiven\`

- `config.json`: carpeta raíz de proyectos, nombre del equipo (por defecto el hostname, editable, ej. "PORTATIL" / "PC-MESA"), iniciar con Windows (sí/no), revisar al abrir (sí/no), repos excluidos, tema claro/oscuro.
- `logs/vaiven.log`: log rotativo (5 archivos de 1 MB). Nunca escribe el token en el log.
- `backups.json`: índice de respaldos creados (repo, fecha, tipo, referencia Git, motivo).

La sesión de GitHub se guarda **solo** en el Administrador de credenciales de Windows vía `keyring`, nunca en archivos de texto.

## 5. Inicio de sesión con GitHub (en el navegador, sin tokens manuales)

Se usa el **OAuth Device Flow** de GitHub, pensado para apps de escritorio: no necesita secreto de cliente y el usuario nunca ve ni copia un token.

### 5.1 Configuración única (la hace el usuario una vez, documentarlo en README.md)
1. En GitHub → Settings → Developer settings → OAuth Apps → New OAuth App.
2. Nombre: Vaivén. Homepage y callback URL: `http://localhost` (no se usan, pero son obligatorios).
3. Marcar **"Enable Device Flow"**.
4. Copiar el **Client ID** (es público, no es secreto) y ponerlo en `src/auth.py` como constante `CLIENT_ID`.

### 5.2 Flujo en la app
1. Pantalla inicial con botón **"Iniciar sesión con GitHub"**.
2. La app pide un código a `https://github.com/login/device/code` con scopes `repo read:user`.
3. La app **copia el código al portapapeles automáticamente**, lo muestra en grande y **abre el navegador** en `https://github.com/login/device`.
4. El usuario pega el código y autoriza en GitHub.
5. La app consulta `https://github.com/login/oauth/access_token` respetando el intervalo indicado (y `slow_down`), hasta obtener la sesión o que expire el código (mostrar mensaje claro y botón "Intentar de nuevo").
6. Guarda la sesión en `keyring`, obtiene el usuario (`GET /user`) y muestra su nombre y avatar.
7. Botón **"Cerrar sesión"** en ajustes: borra la sesión de `keyring`.

### 5.3 Uso de la sesión en Git
Para que el usuario inicie sesión **una sola vez**, las operaciones de red de Git (`fetch`, `pull`, `push`, `clone`) reciben la autenticación por línea de comandos, sin escribirla nunca en `.git/config` ni en disco:

```
git -c http.https://github.com/.extraheader="AUTHORIZATION: basic <base64('x-access-token:' + token)>" <comando>
```

> Implementación: la misma configuración se pasa por las variables de entorno `GIT_CONFIG_COUNT` / `GIT_CONFIG_KEY_<n>` / `GIT_CONFIG_VALUE_<n>` (Git 2.31+), para que el token no aparezca en la línea de comandos. Ver D38 en DECISIONES.md.

Si la sesión caduca o GitHub responde 401, la app vuelve a la pantalla de login con un mensaje amable.

## 6. Sistema de seguridad (núcleo del proyecto)

Principio: **la app nunca destruye trabajo.** Ante cualquier duda, se detiene, avisa y ofrece opciones; nunca decide por su cuenta algo irreversible.

### 6.1 Comandos prohibidos
El módulo `git_ops.py` debe rechazar (lanzar excepción) cualquier intento de ejecutar:
- `push --force`, `push -f`, `push --force-with-lease`
- `reset --hard`
- `clean -f` / `clean -fd`
- `checkout -- .`, `restore .` sobre cambios sin guardar
- `branch -D` sobre ramas que no sean de respaldo expiradas
- `pull` sin `--ff-only`

Añadir un test que verifique que estos comandos son rechazados.

### 6.2 Análisis previo obligatorio
Antes de mostrar cualquier botón de acción como disponible, y antes de ejecutar cualquier acción, `analyzer.py` hace `git fetch` en cada repo y calcula su estado:

| Estado | Significado | Color |
|---|---|---|
| Al día | Local y GitHub son iguales, sin cambios sin guardar | Verde |
| Cambios locales | Hay archivos modificados o nuevos sin subir | Azul |
| Adelantado | Hay commits locales que no están en GitHub | Azul |
| Atrasado | GitHub tiene commits que no están en este equipo | Amarillo |
| Divergido | Ambos lados tienen commits distintos | Rojo |
| Atrasado con cambios locales | GitHub tiene commits nuevos y además hay cambios sin guardar aquí | Rojo |
| En conflicto | Hay un merge/rebase a medias o archivos en conflicto | Rojo |
| Sin rama remota | La rama no tiene upstream en GitHub | Gris |
| HEAD suelto | Detached HEAD | Gris |
| No clonado | Existe en GitHub pero no en este equipo | Gris |

Datos que se obtienen por repo: rama actual, commits adelante/atrás (`git rev-list --left-right --count HEAD...@{u}`), archivos modificados/nuevos/borrados (`git status --porcelain=v2`), mensaje, autor, fecha y **equipo de origen** de los commits entrantes y salientes, fecha de la última modificación local.

### 6.3 Detección de "versión más adelantada"
Este es el caso que más preocupa al usuario: tener trabajo más nuevo en este equipo y perderlo al sincronizar.

- Si el repo está **Adelantado** o tiene **Cambios locales**, "Sincronizar todo" **no lo toca** y muestra: *"Este equipo tiene trabajo más nuevo que GitHub (3 commits y 5 archivos sin subir). Súbelo primero para no perderlo."* con botón **"Subir este proyecto"**.
- Si está **Divergido** o **Atrasado con cambios locales**, se bloquea para ambas acciones masivas y se ofrecen opciones individuales (sección 6.6).
- Solo se sincroniza automáticamente un repo **Atrasado y limpio**, y siempre con `git pull --ff-only`, que por diseño es imposible que sobrescriba trabajo.

### 6.4 Respaldo automático antes de actuar
Antes de cualquier operación que modifique un repositorio, `safety.py` crea un respaldo:

1. Si hay cambios sin guardar: `git stash create --include-untracked` (no altera la carpeta de trabajo) y se guarda el resultado.
2. Se crea una referencia de respaldo: `refs/vaiven-backup/<AAAA-MM-DD_HHMMSS>_<EQUIPO>` apuntando a `HEAD` y otra al stash si existe.
3. Se registra en `backups.json` con el motivo (ej. "antes de sincronizar").

Los respaldos se conservan 30 días o los últimos 20 por repo (lo que sea mayor). Nunca se suben a GitHub.

Botón **"Deshacer última operación"** por repo: restaura desde el último respaldo (crea primero un respaldo del estado actual, para que deshacer también sea reversible). Pantalla **"Respaldos"** para ver y restaurar cualquiera de la lista.

### 6.5 Vista previa y confirmación
Ninguna acción masiva se ejecuta directamente. Al pulsar un botón:

1. Se ejecuta el análisis (6.2).
2. Se abre `preview_dialog.py` con tres grupos:
   - **Se hará:** repos que se van a subir o bajar, con la lista de commits/archivos afectados (desplegable por repo).
   - **Se omitirá (necesita tu atención):** repos bloqueados, con el motivo en lenguaje sencillo.
   - **Sin cambios:** repos al día (colapsado).
3. Advertencias destacadas si aplica: archivos de más de 50 MB (GitHub rechaza >100 MB), archivos que parecen secretos (`.env`, `*.pem`, `*.key`, `id_rsa*`, `credentials*.json`) que no estén en `.gitignore`.
4. Casillas para excluir repos concretos de esta ejecución.
5. Pregunta final: **"¿Estás seguro de que quieres subir/sincronizar N proyectos?"** con botones **"Sí, continuar"** y **"Cancelar"**. Si hay advertencias de secretos, el botón de confirmar queda deshabilitado hasta marcar "Entiendo el riesgo" o excluir esos archivos.
6. Al terminar, resumen: cuántos OK, cuántos omitidos, cuántos con error, y enlace al log.

### 6.6 Opciones para repos bloqueados (acción individual, nunca masiva)
- **Divergido:** (a) "Intentar combinar automáticamente" → respaldo + `git pull --rebase`; si hay conflicto, `git rebase --abort` inmediato y aviso; (b) "Abrir en VS Code" para resolverlo a mano; (c) "Ver diferencias".
- **Atrasado con cambios locales:** (a) "Subir mis cambios primero" (commit + luego tratar como divergido si aplica); (b) "Guardar mis cambios aparte y sincronizar" → respaldo, `git stash push -u`, `git pull --ff-only`, `git stash pop`; si el `pop` da conflicto, se deja el stash intacto y se avisa de que los cambios están a salvo en el respaldo.
- **En conflicto / HEAD suelto / sin rama remota:** solo se informa y se ofrece "Abrir en VS Code" o "Abrir carpeta".

## 7. Acción "Subir todo"

Para cada repo seleccionado en la vista previa:
1. Comprobar que `user.name` y `user.email` de Git están configurados; si no, pedirlos una vez en la app y guardarlos con `git config --global`.
2. Respaldo (6.4).
3. `git add -A` (respeta `.gitignore`).
4. `git commit -m "<mensaje>"`. Mensaje por defecto editable en la vista previa: `Sync desde <EQUIPO> — 2026-09-22 15:30`. Añadir al commit el trailer `Synced-From: <EQUIPO>` para poder mostrar el equipo de origen.
5. `git fetch`; si el remoto avanzó mientras tanto, **no hacer push**, marcar el repo como Divergido y pasar a 6.6.
6. `git push` (sin force, nunca). Si la rama no tiene upstream: `git push -u origin <rama>` solo tras confirmación explícita.

## 8. Acción "Sincronizar todo"

Para cada repo seleccionado:
1. Solo repos en estado **Atrasado y limpio** (6.3).
2. Respaldo (6.4).
3. `git pull --ff-only`.
4. Verificar que el nuevo `HEAD` coincide con `@{u}`.

Además, en la vista previa aparece la sección **"Proyectos en GitHub que no tienes en este equipo"** (vía `GET /user/repos?per_page=100`, con paginación, incluyendo privados y de organizaciones), con casillas para clonarlos en la carpeta raíz. Por defecto desmarcados.

## 9. Interfaz

### 9.1 Pantalla principal
- Barra superior: avatar y usuario de GitHub, nombre del equipo, botón de ajustes.
- Dos botones grandes: **"⬆ Subir todo"** y **"⬇ Sincronizar todo"**, más un botón secundario **"↻ Revisar estado"**.
- Lista de proyectos: nombre, rama, indicador de color del estado, texto corto ("2 commits por subir", "Al día", "GitHub tiene 1 cambio nuevo del PORTATIL"), fecha del último cambio.
- Clic en un proyecto → vista detalle: commits entrantes/salientes, archivos cambiados, botones individuales (Subir, Sincronizar, Ver diferencias, Abrir en VS Code, Abrir carpeta, Deshacer, Respaldos).
- Filtro: Todos / Necesitan atención / Con cambios.

### 9.2 Primer arranque (asistente)
1. Comprobar que Git está instalado (`git --version`). Si no, mensaje con enlace a git-scm.com y no continuar.
2. Login con GitHub (sección 5).
3. Elegir carpeta raíz de proyectos (selector de carpetas de Windows). Detectar automáticamente los repos dentro (hasta 2 niveles de profundidad) cuyo remoto apunte a github.com.
4. Confirmar nombre del equipo.
5. Preguntar si quiere "Iniciar con Windows".

### 9.3 Ajustes
Carpeta raíz, nombre del equipo, repos excluidos, iniciar con Windows, revisar estado al abrir, tema, cerrar sesión, abrir carpeta de logs.

### 9.4 Textos
Toda la interfaz en español, con lenguaje sencillo. Evitar jerga de Git en los mensajes principales (decir "cambios sin subir" en vez de "working tree dirty"); los términos técnicos pueden aparecer en la vista detalle.

## 10. Ejecución como .exe

- `build.bat` instala dependencias y ejecuta PyInstaller:
  `pyinstaller --onefile --windowed --name Vaiven --icon assets/icon.ico src/main.py`
- Resultado: `dist\Vaiven.exe`, un único archivo que se abre con doble clic, sin consola ni comandos.
- **Iniciar con Windows** (opcional, desde ajustes): crear/borrar un acceso directo en `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup`.
- **Revisar al abrir** (opcional): al iniciar, hace solo `fetch` y análisis, y muestra una notificación tipo "3 proyectos tienen cambios nuevos en GitHub". **Nunca** sube ni sincroniza sin que el usuario confirme.
- Una sola instancia: si ya está abierta, traer la ventana existente al frente.
- README.md debe explicar que Windows SmartScreen puede mostrar un aviso la primera vez porque el `.exe` no está firmado ("Más información" → "Ejecutar de todas formas").

## 11. Manejo de errores

- Sin internet: mensaje claro, el análisis muestra solo el estado local.
- Repo sin remoto de GitHub: se ignora y se lista como "No es de GitHub".
- Error de un repo no detiene los demás; se muestra en el resumen final.
- Timeout de 120 s por operación de red de Git.
- Todo error se registra en el log con el comando ejecutado (sin la cabecera de autenticación).

## 12. Fases de implementación

**Fase 1 — Base y Git seguro.** Estructura, `config.py`, `logger.py`, `git_ops.py` con lista de comandos prohibidos, `analyzer.py`.
*Aceptación:* los tests de `analyzer` detectan correctamente todos los estados de la tabla 6.2 en repos de prueba creados en carpetas temporales (con un "remoto" bare local); los comandos prohibidos lanzan excepción.

**Fase 2 — Seguridad.** `safety.py`: respaldos, índice, restaurar, deshacer, limpieza por antigüedad.
*Aceptación:* crear cambios sin guardar → respaldo → modificarlos → restaurar devuelve exactamente el contenido original, incluidos archivos nuevos.

**Fase 3 — Motor de sincronización.** `sync_engine.py` con Subir todo y Sincronizar todo según secciones 7 y 8, incluido el caso 6.6.
*Aceptación:* pasan todos los escenarios de la sección 13.

**Fase 4 — Login con GitHub.** `auth.py` (Device Flow + keyring) y `github_api.py` (usuario, lista de repos paginada).
*Aceptación:* login abre el navegador, el código se copia solo, la sesión persiste tras cerrar y abrir la app, cerrar sesión la borra.

**Fase 5 — Interfaz.** Todas las vistas de la sección 9, operaciones en hilos, progreso visible.
*Aceptación:* flujo completo desde primer arranque hasta subir y sincronizar sin usar la terminal; la ventana nunca se congela.

**Fase 6 — Empaquetado.** `build.bat`, icono, inicio con Windows, instancia única, README.md.
*Aceptación:* `dist\Vaiven.exe` funciona con doble clic en un equipo Windows con Git, sin Python instalado y sin ventanas de consola.

## 13. Escenarios de prueba obligatorios

Simular los dos equipos con dos clones del mismo remoto bare local:

1. Equipo A sube cambios → Equipo B sincroniza → B queda igual que A.
2. B tiene 2 commits sin subir y pulsa "Sincronizar todo" → B no se modifica y aparece el aviso de "trabajo más nuevo".
3. B tiene archivos modificados sin commit y el remoto tiene commits nuevos → bloqueado; la opción "Guardar mis cambios aparte y sincronizar" funciona y los cambios siguen presentes al final.
4. A y B hacen commits distintos (divergido) → ambas acciones masivas lo omiten; "Intentar combinar" funciona si no hay conflicto y, si lo hay, aborta dejando el repo como estaba.
5. Push rechazado porque el remoto avanzó durante la operación → no se fuerza nada, el repo pasa a Divergido.
6. Archivo `.env` nuevo sin ignorar → advertencia y botón de confirmar bloqueado.
7. Tras cualquier operación, "Deshacer" devuelve el repo al estado previo exacto.
8. En ningún escenario se pierde un solo archivo o commit (verificar comparando hashes y contenido).

## 14. Fuera de alcance (por ahora)

- macOS/Linux (el código debe ser portable, pero solo se empaqueta para Windows).
- Resolución visual de conflictos dentro de la app (se delega a VS Code).
- Git LFS.
- Firma digital del `.exe`.
