# DECISIONES

Registro de las ambigüedades de `SPEC.md` y de la opción elegida en cada una.
Criterio constante, según las instrucciones del propio SPEC: **ante la duda,
la opción más segura para los datos del usuario.**

---

## Fase 1 — Base y Git seguro

### D1. `pull --rebase` contra la lista de comandos prohibidos

**Ambigüedad.** La sección 6.1 prohíbe «`pull` sin `--ff-only`», pero la 6.6(a)
propone `git pull --rebase` para combinar un repositorio divergido.

**Decisión.** `pull --rebase` está prohibido por defecto y solo se permite si
quien llama pasa el permiso explícito `ALLOW_REBASE_PULL` (`git_ops.py`). Ese
permiso se reserva para la acción **individual** de la sección 6.6(a), que
siempre va precedida de un respaldo. **Ninguna acción masiva** («Subir todo»,
«Sincronizar todo») puede pasarlo. Un permiso no habilita a los demás, y hay
pruebas que lo comprueban.

### D2. La lista de prohibidos se ha ampliado

**Ambigüedad.** La sección 6.1 da una lista concreta, pero Git ofrece más
formas de perder trabajo que las listadas.

**Decisión.** Se prohíbe también, por el mismo criterio de seguridad:

| Comando | Por qué |
|---|---|
| `push --delete`, `push origin :rama`, `push --mirror`, `push --prune` | borran ramas en GitHub |
| `push --force-if-includes` | variante de push forzado no citada |
| `reset --merge` | descarta cambios sin guardar igual que `--hard` |
| `checkout -f` / `switch -f` / `switch --discard-changes` | descartan cambios sin guardar |
| `restore` (siempre, no solo `restore .`) | la app nunca necesita descartar cambios |
| `stash drop` / `stash clear` | el escondite guarda respaldos (6.4) |
| `reflog expire` / `reflog delete` | borran la red de seguridad de Git |
| `gc --prune` (salvo `--prune=never`) | puede eliminar objetos de los respaldos |
| `filter-branch`, `filter-repo`, `replace` | reescriben el historial |
| `rebase` que no sea `--abort` / `--continue` / `--skip` / `--quit` | el rebase se hace solo vía D1 |

`reset` sin `--hard`/`--merge` (es decir, `--soft` y `--mixed`) sí se permite:
no toca el árbol de trabajo.

### D3. `branch -D` «sobre ramas que no sean de respaldo expiradas»

**Ambigüedad.** La caducidad de un respaldo la calcula `safety.py`, que es de
la fase 2, pero la validación vive en `git_ops.py`, que es de la fase 1.

**Decisión.** `git_ops.py` comprueba lo que puede verificar por sí mismo: que
**todas** las referencias afectadas estén dentro del espacio de nombres de
respaldos (`refs/vaiven-backup/` o `vaiven-backup/`) y que quien llama pase el
permiso `ALLOW_BACKUP_REF_DELETE`. La comprobación de antigüedad (30 días o
últimos 20 por repo) la hará `safety.py` antes de pedir ese permiso. Se aplica
la misma regla a `update-ref -d`, que es lo que realmente usará la limpieza.

### D4. Orden de precedencia cuando un repo encaja en varios estados

**Ambigüedad.** La tabla 6.2 no dice qué estado mostrar si, por ejemplo, un
repo está adelantado y además tiene archivos sin guardar.

**Decisión.** El orden va de lo que exige intervención humana a lo que se puede
automatizar: `En conflicto` → `HEAD suelto` → `Sin rama remota` → `Divergido`
→ `Atrasado con cambios locales` → `Atrasado` → `Adelantado` → `Cambios
locales` → `Al día`. Para que no se pierda información, `RepoStatus` conserva
siempre los contadores y la lista de archivos, y expone `has_newer_work`, que
es verdadero tanto si hay commits sin subir como si hay archivos sin guardar
(es la condición de la sección 6.3, que trata ambos casos igual).

### D5. Estado `Error` añadido a la tabla

**Decisión.** Se añade un estado `Error al analizar` (color gris) que no está
en la tabla 6.2, para carpetas que no son repositorios o que fallan al
inspeccionarse. Está en `BLOCKED_STATES`: nunca entra en una acción masiva.
Sin él, un fallo de lectura se confundiría con «Al día» (sección 11: el error
de un repo no detiene a los demás).

### D6. El estado `No clonado` no lo puede calcular el analizador

**Ambigüedad.** «No clonado» solo se conoce comparando la lista de repos de
GitHub con lo que hay en disco, y esa lista llega en la fase 4.

**Decisión.** `analyzer.py` expone `not_cloned_status(nombre, url)` para que
`github_api.py` construya esos estados cuando exista. El analizador solo
recorre carpetas locales.

### D7. Un `fetch` fallido no aborta el análisis

**Decisión.** Siguiendo la sección 11, si `git fetch` falla (sin internet,
sesión caducada) el análisis continúa con la información local y marca
`fetch_ok = False` y `fetch_error`. Los contadores de adelante/atrás quedarán
desactualizados, así que la interfaz debe avisar de que el estado es local.

### D8. Descubrimiento de repositorios

**Decisión.** `discover_repos` recorre hasta 2 niveles (sección 9.2) y **no
entra dentro de un repositorio ya encontrado**: un submódulo no se trata como
proyecto independiente. Los repos cuyo remoto no sea de GitHub no se excluyen
del listado: se marcan con `is_github = False` para poder mostrarlos como «No
es de GitHub» (sección 11).

### D9. La autenticación solo cubre los remotos HTTPS de GitHub

**Decisión.** La cabecera de la sección 5.3 se inyecta para
`https://github.com/`. Un repositorio con remoto SSH (`git@github.com:...`)
seguirá usando la clave SSH del usuario, que ya funciona sin Vaivén. No se
reescribe ningún remoto: cambiar la URL de un repo del usuario sería una
modificación no pedida.

### D10. Censura de credenciales como filtro de `logging`

**Decisión.** Además de sustituir la cabecera en los argumentos antes de
registrarlos, `logger.py` instala un filtro que censura tokens y cabeceras en
**cualquier** mensaje, venga de donde venga. Así ningún módulo futuro puede
filtrar un token al log por descuido (secciones 4 y 11).

### D11. Carpeta de datos fuera de Windows

**Decisión.** El destino es `%APPDATA%\Vaiven` (sección 4). Para poder
desarrollar y probar en Linux, `config.data_dir()` cae en
`$XDG_CONFIG_HOME/vaiven` o `~/.config/vaiven`, y la variable de entorno
`VAIVEN_DATA_DIR` permite redirigirla (la usan las pruebas). El comportamiento
en Windows no cambia.

### D12. Versión de Python

**Nota.** El SPEC fija Python 3.12 y así se empaquetará. El entorno de
desarrollo actual tiene Python 3.14; el código no usa nada exclusivo de 3.13+
y la sintaxis de tipos empleada (`X | None`) es válida desde 3.10.

### D13. Archivo de pruebas añadido

**Decisión.** La estructura de la sección 3 no incluye un archivo de pruebas
para `git_ops.py`, pero la sección 6.1 exige probar que los comandos
prohibidos se rechazan. Se añade `tests/test_git_ops.py`, junto con
`tests/test_config.py` y `tests/test_logger.py`.

---

## Fase 2 — Seguridad

### D14. `git stash create --include-untracked` no guarda los archivos nuevos

**Problema.** La sección 6.4 indica usar `git stash create --include-untracked`
para respaldar los cambios sin guardar. Git **acepta la opción sin quejarse
pero la ignora**: el commit de stash que devuelve solo tiene dos padres (HEAD
y el índice), sin árbol de archivos no seguidos. Comprobado con Git 2.53. Con
esa sola llamada, un archivo nuevo que el usuario aún no ha añadido a Git no
quedaría respaldado, y el criterio de aceptación de la fase 2 exige
recuperarlo.

**Decisión.** El respaldo tiene tres partes en vez de dos:

1. `refs/vaiven-backup/<id>` apuntando a `HEAD`.
2. `refs/vaiven-backup/<id>-stash`, el commit de `git stash create` (cambios
   de archivos ya seguidos).
3. `refs/vaiven-backup/<id>-untracked`, un commit construido por Vaivén con
   los archivos nuevos. Se listan con `git ls-files --others
   --exclude-standard` usando el índice real y después se escriben en un
   **índice temporal** (`GIT_INDEX_FILE`), de modo que el índice del usuario
   no se toca en ningún momento. El orden importa: si se calculara la lista
   con el índice temporal ya activo, Git consideraría «nuevos» también todos
   los archivos seguidos.

`.gitignore` se respeta, así que `node_modules` y compañía nunca entran en un
respaldo.

### D15. Restaurar necesita `reset --hard`, que está prohibido

**Ambigüedad.** Devolver un repositorio a un estado anterior exige mover
`HEAD`, el índice y el árbol de trabajo a la vez, que es justo lo que hace
`reset --hard`, prohibido en la sección 6.1.

**Decisión.** Se añade un tercer permiso explícito, `ALLOW_BACKUP_RESTORE`,
que solo usa `safety.restore_backup()` y solo **después** de haber creado un
respaldo del estado actual. Así deshacer es reversible, como pide el SPEC. La
restauración es de tres pasos: `reset --hard` al commit guardado, `stash
apply` de los cambios de archivos seguidos y escritura directa de los
archivos nuevos desde su árbol. Si `stash apply` fallara, se escriben también
sus blobs archivo a archivo, lo que no puede dar conflicto.

### D16. Restaurar no borra archivos que aparecieron después

**Decisión.** Si desde el respaldo se han creado archivos nuevos que no
existían entonces, la restauración **no los borra**. Restaurar recupera lo
guardado; borrar lo demás sería destruir trabajo que nadie pidió destruir.
Los archivos que sí existían se sobrescriben con su contenido original, y su
versión actual queda guardada en el respaldo de seguridad que se crea
automáticamente justo antes.

### D17. «30 días o los últimos 20, lo que sea mayor»

**Decisión.** Un respaldo se borra solo si cumple **las dos** condiciones: es
más antiguo que 30 días **y** no está entre los 20 más recientes de su
repositorio. Es la lectura que más conserva. La limpieza se ejecuta tras cada
análisis, y borra las referencias con `update-ref -d` bajo el permiso
`ALLOW_BACKUP_REF_DELETE`.

### D18. «Deshacer» ignora los respaldos de restauración

**Decisión.** `undo_last` salta los respaldos cuyo motivo empieza por «antes
de restaurar». Si no, pulsar «Deshacer» dos veces devolvería al usuario al
punto de partida en lugar de seguir retrocediendo, que es lo que espera.

---

## Fase 3 — Motor de sincronización

### D19. El plan y la ejecución están separados

**Decisión.** `plan_push` / `plan_sync` no tocan nada: solo deciden y
explican. `execute` solo hace lo que el plan seleccionado dice. Esa
separación es lo que permite que la vista previa de la sección 6.5 sea
fiable, y hace que todos los escenarios de la sección 13 se puedan probar sin
interfaz.

### D20. Se vuelve a comprobar el estado justo antes de actuar

**Decisión.** Entre la vista previa y la ejecución pueden pasar minutos.
`sync_repo` repite el análisis y se detiene si el repositorio ya no está
«Atrasado y limpio»; `push_repo` hace `fetch` después de confirmar y no sube
nada si GitHub avanzó mientras tanto (escenario 5). Un plan nunca es una
autorización para actuar a ciegas.

### D21. Una rama sin upstream no se confunde con «nada que subir»

**Decisión.** Sin rama en GitHub no se pueden contar commits por delante, así
que `ahead` vale 0 aunque haya trabajo. La decisión de publicar la rama se
toma **antes** de mirar ese contador, y requiere confirmación explícita
(`allow_set_upstream`), como pide la sección 7.6.

### D22. Un repositorio ilegible es un error, no un éxito

**Decisión.** Si la carpeta ya no es un repositorio de Git, `push_repo` y
`sync_repo` devuelven un error inmediatamente en vez de recorrer los pasos y
concluir que «no había nada que subir». Aparece en el resumen final y no
detiene a los demás (sección 11).

### D23. Detección de secretos y de archivos grandes

**Decisión.** A los patrones de la sección 6.5 se añaden `.env.*`,
`id_dsa*`, `id_ecdsa*`, `id_ed25519*`, `*.pfx`, `*.p12` y
`service-account*.json`, que son igual de sensibles. Un archivo que ya esté
en `.gitignore` no genera aviso, porque no se va a subir. El aviso de tamaño
salta a partir de 50 MB, aunque GitHub rechaza a partir de 100, para avisar
antes de que el push falle.

---

## Fase 4 — Login con GitHub

### D24. El Client ID se puede pasar por variable de entorno

**Decisión.** `CLIENT_ID` sigue siendo una constante en `src/auth.py`, como
pide la sección 5.1, pero admite ser sobrescrita con `VAIVEN_CLIENT_ID`. Así
las pruebas no dependen de una OAuth App real y se puede probar la aplicación
sin editar el código fuente. Si no está configurado, el mensaje de error
remite al README en vez de fallar de forma críptica.

### D25. El portapapeles sin dependencias nuevas

**Decisión.** Copiar el código (sección 5.2.3) se hace primero con el propio
widget de Tk, que funciona en cualquier sistema y no añade dependencias. Si
eso falla, se recurre a `clip` en Windows y a `xclip`/`wl-copy`/`pbcopy`
fuera. Si tampoco, se avisa al usuario de que lo copie a mano en vez de
dejarlo bloqueado.

---

## Fase 5 — Interfaz

### D26. Toda vuelta desde un hilo de fondo está protegida

**Problema.** Tkinter solo permite tocar widgets desde su propio hilo, y
`widget.after(...)` lanza `RuntimeError` si la ventana ya se cerró. Si el
usuario cierra Vaivén mientras hay un análisis en marcha, el hilo de fondo
reventaba al volver.

**Decisión.** Todas las vueltas al hilo de la interfaz pasan por
`src/ui/call_on_ui_thread`, que descarta el aviso en silencio si la ventana
ya no existe. Hay una prueba que comprueba que ninguna vista llama a
`self.after(0, …)` directamente.

### D27. Los botones grandes se bloquean mientras se trabaja

**Decisión.** Mientras hay una operación en marcha, «Subir todo»,
«Sincronizar todo» y «Revisar estado» quedan deshabilitados y aparece una
barra de progreso con el nombre del repositorio en curso. Es la forma más
simple de garantizar que no se lancen dos acciones masivas a la vez sobre los
mismos repositorios.

### D28. Un archivo nuevo en `src/ui/`

**Decisión.** La sección 3 no lista `src/ui/theme.py`, pero tener los colores
de los estados en un solo sitio evita repetirlos en cinco vistas. Junto con
`src/ui/__init__.py` (que aloja `call_on_ui_thread`), son los únicos archivos
de interfaz que no estaban en la estructura original.

---

## Fase 6 — Empaquetado

### D29. Los recursos dentro del `.exe`

**Decisión.** PyInstaller en modo `--onefile` extrae los archivos añadidos a
una carpeta temporal que anuncia en `sys._MEIPASS`. `config.resource_path()`
lo tiene en cuenta, de modo que el icono se encuentra igual empaquetado que
en desarrollo. `build.bat` añade la carpeta con `--add-data "assets;assets"`.

### D30. El acceso directo de inicio, sin dependencias

**Decisión.** Crear un `.lnk` de verdad necesita COM, que no está en la
biblioteca estándar y obligaría a añadir `pywin32`. Se genera con un
VBScript efímero ejecutado por `cscript`, que viene con Windows. Si eso
fallara, se crea un `.cmd` equivalente en la misma carpeta de Inicio: menos
elegante, mismo efecto. `is_enabled()` reconoce las dos formas.

### D31. Instancia única mediante un puerto local

**Decisión.** La sección 10 pide que, si la app ya está abierta, se traiga su
ventana al frente. Se consigue ocupando un puerto de escucha en `127.0.0.1`:
si está ocupado, es que Vaivén ya corre, y la copia nueva le manda un aviso
para que se muestre y se cierra sin hacer nada. Es portable y no deja
archivos de bloqueo huérfanos si el programa termina de forma abrupta.

---

## Cambio posterior — Inicio de sesión web directo

### D32. Se sustituye el Device Flow por el flujo de código de autorización

**Petición del usuario.** «Lo que quiero es que le dé a iniciar y me mande a
GitHub a iniciar sesión, pero en la web», es decir, sin pegar ningún código.

**Por qué el SPEC había elegido otra cosa.** La sección 5 escogió el Device
Flow precisamente porque «no necesita secreto de cliente». Es el método que
GitHub recomienda para aplicaciones de escritorio, y por una razón real: las
OAuth Apps de GitHub **no admiten PKCE**, que es el mecanismo estándar con el
que una app instalada puede canjear un código sin guardar ningún secreto. Sin
PKCE, el canje en `/login/oauth/access_token` exige `client_secret`.

**Decisión.** Se implementa el flujo de código de autorización como camino
principal, asumiendo que el `client_secret` viaje dentro del ejecutable, y se
**conserva el Device Flow como reserva automática**.

Cómo funciona el camino principal:

1. Vaivén levanta un servidor HTTP mínimo en `127.0.0.1:49732` y genera un
   `state` aleatorio.
2. Abre el navegador en `https://github.com/login/oauth/authorize` con ese
   `state` y `redirect_uri=http://127.0.0.1:49732/vaiven/callback`.
3. El usuario pulsa «Authorize». GitHub redirige a esa dirección local.
4. El servidor comprueba que el `state` coincide —si no, rechaza el inicio de
   sesión—, responde con una página que dice «Ya puedes cerrar esta pestaña»
   y se apaga.
5. El código se canjea por la sesión, que va a `keyring` como siempre.

**El puerto es fijo a propósito.** GitHub exige que la dirección de vuelta
coincida exactamente con la registrada en la OAuth App, así que no se pueden
probar puertos alternativos. Si el 49732 está ocupado o bloqueado, no se
insiste: se pasa sola al Device Flow, que no escucha en ningún puerto y por
eso funciona en cualquier circunstancia. Esa es la razón de conservar el
código anterior en lugar de borrarlo.

**Alcance real de tener el secreto dentro del `.exe`.** Quien lo extrajera
**no obtendría acceso a los repositorios**: para eso hace falta que el titular
de la cuenta autorice desde GitHub. Lo que sí podría es hacer pasar otro
programa por Vaivén ante el usuario. Como la OAuth App es personal y de un
solo usuario, el riesgo es pequeño y se puede anular en cualquier momento con
«Reset client secret» en GitHub. El README lo explica en esos términos.

**Se usa `127.0.0.1` y no `localhost`.** En algunos equipos `localhost`
resuelve primero a `::1` (IPv6) mientras el servidor escucha en IPv4, y la
vuelta del navegador fallaría. Con la dirección literal no hay ambigüedad.

### D33. El puerto se libera siempre

**Decisión.** `wait_for_authorization` cierra el servidor en un `finally`, y
la ventana principal llama a `LoginView.stop()` tanto al cambiar de pantalla
como al cerrarse (`WM_DELETE_WINDOW`). Un puerto que quedara ocupado obligaría
al siguiente intento a caer en el método de reserva sin motivo.

### D34. `SO_REUSEADDR` en el cerrojo de instancia única, salvo en Windows

**Problema encontrado.** Al cerrar Vaivén, el puerto del cerrojo (49731) queda
unos segundos en estado `TIME_WAIT`. Durante esa ventana, volver a abrir la
aplicación fallaba: la copia nueva no podía ocupar el puerto, concluía que ya
había otra abierta, le mandaba un aviso que nadie recibía y se cerraba. El
resultado para el usuario era que **Vaivén no arrancaba** si lo cerraba y lo
volvía a abrir enseguida.

**Decisión.** Se activa `SO_REUSEADDR` en el socket de escucha, que permite
ocupar un puerto en `TIME_WAIT` pero sigue rechazando el bind si hay otro
proceso escuchando de verdad: justo la semántica que hace falta. **En Windows
no se activa**, porque allí `SO_REUSEADDR` tiene otro significado —deja que
dos procesos ocupen el mismo puerto— y el cerrojo dejaría de detectar la
segunda copia. Windows no sufre el problema original, porque el bind es
exclusivo por defecto y el puerto de un socket de escucha cerrado se libera
de inmediato.

### D35. Todo texto que venga de Git se acota antes de dibujarlo

**Fallo encontrado en uso real.** Tras iniciar sesión correctamente, la
aplicación se cerró sola con un error del servidor gráfico:

```
X Error of failed request: BadAlloc (insufficient resources for operation)
Major opcode of failed request: 53 (X_CreatePixmap)
```

**Causa.** Tk reserva, para cada línea de un widget de texto, un mapa de
píxeles tan ancho como la línea entera. Una sola línea muy larga basta para
agotar esa reserva. Medido con `git diff` de un archivo de una sola línea:

| Longitud de la línea | Tiempo en dibujarse |
|---|---|
| 20.000 caracteres | 0,1 s |
| 200.000 caracteres | 19 s con la ventana congelada |
| 2.000.000 caracteres | cuelgue indefinido, y después `BadAlloc` |

No es un caso rebuscado: un JavaScript minificado, un `package-lock.json`, un
JSON exportado o un bloque en base64 pasan de esa longitud con facilidad, y
aparecen en proyectos normales.

**Decisión.** `src/ui/texto_seguro()` recorta cada línea a 400 caracteres y
el total a 1.500 líneas, indicando en el propio texto lo que se ha omitido.
Lo aplican todas las vistas que muestran algo procedente de Git: las
diferencias, la lista de archivos cambiados y el desplegable de la vista
previa. Recortar es lo correcto aquí: estas ventanas sirven para echar un
vistazo, y para leer el contenido entero está el botón «Abrir en VS Code».
Tras el cambio, una línea de 20 millones de caracteres se dibuja en 0,1 s.

Hay una prueba que recorre las vistas y falla si alguna inserta texto de Git
en un widget sin pasarlo por ese filtro.

### D36. «Ver diferencias» y el recuento de proyectos, fuera del hilo de la interfaz

**Decisión.** `git diff` y `discover_repos()` se ejecutaban en el hilo de la
ventana, que es justo lo que la sección 2 prohíbe («la interfaz nunca se
congela»). Ahora los dos se calculan en segundo plano: la ventana de
diferencias aparece al instante con «Calculando las diferencias…», y el
recuento de proyectos de Ajustes muestra «Buscando proyectos…» mientras mira
dentro de la carpeta elegida.
