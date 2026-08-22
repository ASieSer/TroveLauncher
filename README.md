# Trove Accounts Hub

Launcher propio para Trove: mantiene la instalación al día contra el CDN de
Trion, se autentica con tus credenciales de Glyph y arranca el juego, sin
necesidad del cliente de Glyph. **Windows y Linux** — en Linux el juego se lanza
dentro de su prefijo de Wine o Proton (ver [Linux](#linux)).

Interfaz HTML/CSS/JS sobre pywebview (WebView2 en Windows, WebKitGTK en Linux),
backend Python.

> Porta código de [BetterTroveTools](https://github.com/AallynReed/BetterTroveTools)
> (MIT). Ver [`NOTICE.md`](NOTICE.md).

## Requisitos

- Python 3.10 o superior
- Una cuenta de Glyph con Trove
- **En Windows**: WebView2 Runtime, que ya viene con Windows 11
- **En Linux**: Wine, y WebKitGTK para la ventana

## Instalación

```bash
pip install -r requirements.txt
```

En Linux hacen falta además dos cosas del sistema, porque no vienen por pip:

```bash
# la ventana (pywebview dibuja con el WebKitGTK del sistema)
sudo apt install python3-gi gir1.2-webkit2-4.1
# lanzar el juego, que es de Windows
sudo apt install wine64
```

> **Mejor con Proton.** Si el juego está instalado dentro de un prefijo de
> Proton, la aplicación lo lanza con el Proton de ese prefijo sin que haya que
> decirle nada, y hace bien: al Wine del sistema le suele faltar un símbolo que
> el anti-cheat de Trove necesita — ver [Linux](#linux).

> **Si usas un entorno virtual, créalo con `--system-site-packages`.** `gi` es
> un paquete del sistema y un venv normal no lo ve, así que la ventana no
> abriría:
>
> ```bash
> python3 -m venv --system-site-packages .venv
> ```

El llavero del escritorio (GNOME Keyring, KWallet…) es donde se guardan la
contraseña y el ticket; suele estar ya en cualquier escritorio. Sin él la
aplicación funciona, pero no recuerda nada — ver *Almacenamiento*.

`python tools/check_linux.py` dice de una vez qué falta: el motor de la ventana,
Wine, el ayudante, el llavero y las instalaciones que encuentra.

## Uso

```bash
python main.py
```

Con `--debug` se abren las DevTools de WebView2 con F12:

```bash
python main.py --debug
```

## Qué hace cada cosa

### Núcleo (`core/`)

| Módulo | Responsabilidad |
| --- | --- |
| `cdn.py` | Cliente del CDN de actualizaciones. Tres capas: puntero (versión actual) → manifiesto (`path:hash:size`) → archivos. |
| `updater.py` | Sincronización incremental con estado en SQLite. Nunca borra archivos. |
| `trionauth.py` | Autenticación contra `auth.trionworlds.com`, 2FA por email, keep-alive y caché del ticket cifrada con DPAPI. |
| `inject.py` | Entrega del ticket al juego tal y como lo hace Glyph: blob RIFT cifrado con RC4 en un file-mapping heredable. |
| `launch.py` | Cadenas de servidor de autenticación por región. |
| `rift.py` | El blob del ticket (RC4 + cabecera «RIFT»). Código puro, compartido con el ayudante de Wine. |
| `gamehost.py` | Lanzar, esperar, cerrar: Windows directo o, en Linux, a través de Wine. |
| `winehost.py` | El otro extremo del ayudante que corre dentro del prefijo. |
| `vault.py` | Dónde se guardan los secretos: DPAPI en Windows, llavero del escritorio en Linux. |
| `installs.py` | Detección de instalaciones: registro, Steam y carpetas propias. |
| `prefs.py` | Preferencias, cuentas y contraseñas cifradas con DPAPI. |
| `service.py` | Orquestador: hilo de trabajo, 2FA, auto-relog y progreso. |

### Interfaz (`web/`)

| Fichero | Responsabilidad |
| --- | --- |
| `index.html` | Estructura: barra superior con la marca, barra de acciones, tablero de cuentas, barra de estado y panel de ajustes. |
| `img/trove-accounts-hub.svg` | Logo propio: cubo isométrico y rótulo en dos líneas justificadas al mismo ancho. El texto va trazado a curvas desde la Comfortaa que ya viaja con la app, así que no depende de ninguna fuente al pintarse. Lo genera `tools/make_logo.py` (`pip install fonttools brotli`), que no hace falta para usar la aplicación. |
| `css/app.css` | Tema oscuro plano. Un único color vivo —el acento— tiñe el fondo entero en un porcentaje bajo y marca la acción principal y los estados activos. |
| `js/app.js` | Todo el comportamiento: pintado del tablero, arrastrar y soltar, diálogos, preferencias y eventos que llegan del backend. |

**La barra superior enseña sólo una imagen**: el logo del tema puesto, sin
nombre al lado. Dos de los tres logos de club son rótulos que ya llevan el
nombre dentro, así que escribirlo aparte lo decía dos veces en unos temas y una
en otros. Quien nombra la aplicación es la barra de estado, abajo a la
izquierda, con el mismo texto se ponga el tema que se ponga. El logo propio va
como máscara CSS, no como imagen, para que tome el color de acento.

Las cuentas se pintan siempre como tarjetas, agrupadas por categorías que se
pliegan y se reordenan arrastrando. **El estado de una cuenta vive en su
tarjeta y en ningún otro sitio**: la cabecera de un grupo no lo resume, porque
repetir «2 running» ahí no dice de quién es ni deja actuar sobre ello. Lo único
global es el recuento del encabezado y los mensajes de operación de la barra
inferior, donde también está la versión.

#### Lanzar y volver a entrar

- **«Launch all» lanza de una en una.** Las cuentas se preparan a la vez
  (actualizar, autenticar), pero **arrancan en fila y con un respiro entre
  ellas**. Con el loader del anti-cheat por medio, el pid del juego hay que
  salir a buscarlo entre los procesos y lo único que lo distingue es que antes no
  estaba: dos arranques simultáneos se adjudican el mismo Trove y acaban con una
  partida sin vigilar y otra mostrada con el nombre de la vecina. Y una carpeta
  se actualiza una vez, no una por cuenta.
- **Auto-relog, por cuenta, con el botón del bucle.** Vuelve a entrar cuando la
  partida termina, **se haya caído o se haya cerrado con normalidad**: que te
  echen por inactividad cierra el juego limpiamente y es justo la vez que uno
  quiere volver. No se relanza lo que cierras tú desde el launcher, ni lo que se
  muere nada más arrancar. Si la caída dejó abierta la ventana de reporte de
  fallos de Trove, se cierra con la partida.

#### Apariencia

Todo se ajusta desde Ajustes → Appearance y se guarda en `theme` dentro de
`prefs.json`:

- **Font** — tipografía de toda la interfaz: del sistema, Quicksand, Comfortaa
  o Quantico. Las tres de Google Fonts viajan en `web/fonts/` y se cargan de
  ahí: la ventana abre desde `file://` y no puede depender de que haya internet.
- **Club theme** — `Mystic Cave`, `Arsyn`, `Sayro` o ninguno. Un club cambia el
  logo de la barra superior por el suyo (`web/img/`) y **fija el acento** a su
  color: morado en Mystic Cave y Arsyn, rojo oscuro en Sayro.
  Mientras haya club puesto, el selector de acento se ve pero no se toca.
  Quitarlo devuelve el color que hubiera elegido el usuario, que se guarda
  intacto todo el tiempo.
- **Accent** — dos filas: arriba la paleta fija, abajo los colores propios. Un
  color a medida **se guarda al elegirlo** (el botón `+` abre el selector del
  sistema) y se queda ahí para volver a él; el que esté en uso siempre está
  guardado, y el resto se olvidan con la `×` que aparece encima. Son ocho
  huecos: al llenarlos, el más viejo cede el sitio.
- **Tint strength** — cuánto acento llega al fondo. **No lo bloquea ningún
  tema**: es intensidad, no color, y sigue siendo del usuario aunque el club
  mande en el acento. A 0% la interfaz queda gris neutra.
- **Background particles** — el campo de estrellas del fondo.

### Linux

Trove es un juego de Windows: en Linux corre bajo Wine o Proton. La aplicación,
en cambio, corre nativa — y ahí aparece el único problema de verdad del port.

**El ticket no se puede entregar desde fuera del prefijo.** El juego no lo lee
de la línea de órdenes: lo saca de un *file-mapping* de Windows haciendo
`OpenProcess(pid del lanzador)` + `DuplicateHandle`. Son objetos del kernel de
Windows; un proceso Linux nativo ni los crea ni los comparte. No es cuestión de
portar unas llamadas: no existe el equivalente.

La salida es un ayudante que vive donde vive el juego:

```
  interfaz + servicio  (Python, nativo en Linux)
            │  órdenes por tubería
            ▼
  native/troveinject.exe   ← corre con wine, DENTRO del prefijo
            │  CreateProcess + handles heredables
            ▼
  xldr_Trove_GL_loader_x64.exe → Trove_x64.exe
```

Ese ayudante hace exactamente lo que hace `inject.py` en Windows, y por las
mismas razones. Detalles que importan:

- **Un solo ayudante por sesión, y vive mientras viva la aplicación.** Los
  handles del ticket tienen que seguir abiertos mientras haya partida, porque el
  juego los duplica *de él*. Es la misma fuga deliberada de dos handles por
  lanzamiento que hace Glyph.
- **El prefijo se deduce de la ruta del juego.** Un prefijo distinto es
  literalmente otro disco C:, donde el juego no existe. Si la instalación cuelga
  de `…/compatdata/<appid>/pfx/drive_c/…`, ése es el prefijo. Se puede forzar
  otro en Ajustes → Wine.
- **Y el prefijo elige el runner.** Si es de Proton, se lanza con *ese* Proton
  —el que lo creó, que Steam apunta en `config_info` y en `version`— y no con el
  Wine del PATH. No es un capricho: el loader del anti-cheat importa
  `WSCEnumProtocols32` de `ws2_32`, y **el Wine del sistema no siempre exporta
  ese símbolo**. Cuando falta, el loader muere con un «procedure entry point
  could not be located», el juego no llega a abrirse y el launcher se queda en
  *Logging in* sin que nada explique por qué. Proton sí lo trae. Los Proton
  instalados se listan en Ajustes → Wine para elegir uno con un clic, y si el
  runner en uso no tiene el símbolo se avisa **antes** de lanzar (también lo dice
  `tools/check_linux.py`).
- **Las instalaciones se buscan dentro de los prefijos**: los de Proton bajo
  `steamapps/compatdata/*/pfx`, y los de Wine al uso (`~/.wine`, Lutris,
  Bottles). Dentro, la estructura es la de Windows.
- El binario del ayudante viaja compilado (`native/troveinject.exe`, 59 KB)
  porque quien juega en Linux no tiene por qué tener un compilador cruzado. La
  fuente está al lado y `tools/build_helper.sh` lo reproduce con mingw-w64.

Lo que **no** cambia entre sistemas: el actualizador, la autenticación, el
almacén de estado y toda la interfaz. Son Python puro y ya cruzaban.

### Detalles que conviene no romper

**El hash del manifiesto no es recalculable.** Es un token opaco de "¿ha
cambiado esto?", nunca un hash del contenido. Un archivo se vuelve a descargar
si falta en disco o si su token difiere del que guardamos — jamás comparando un
hash recalculado. Por eso un archivo modificado en local nunca se pisa.

**El modo `adopt`.** Al apuntar por primera vez a una instalación que ya existe,
la base de datos está vacía y una sincronización normal se traería el juego
entero. Con `adopt`, cualquier archivo que ya esté en disco con el tamaño exacto
del manifiesto se da por bueno. Reparar (`reset()` + `adopt=False`) fuerza la
descarga completa cuando esa confianza no era correcta.

**Los handles del ticket se quedan abiertos a propósito.** El juego lee el blob
con `OpenProcess(pid_del_launcher)` + `DuplicateHandle`. Si cerramos el mapping
y el evento justo tras lanzar, el juego duplica un objeto vacío y vuelve a la
pantalla de login. Por eso `inject.py` los guarda en `_SESSION_HANDLES` y los
mantiene abiertos mientras el launcher viva.

**El launcher no toca las ventanas del juego.** Ni las trae al frente, ni las
restaura, ni las enumera: nada de `EnumWindows`, `ShowWindow` ni
`SetForegroundWindow`. Ahorrarle un alt-tab a alguien no compensa ponerse a
manipular ventanas ajenas desde un cliente de terceros. Cerrar una partida sí
sigue estando, porque el proceso lo arrancó el propio launcher.

**Dos implementaciones del mismo blob.** El formato del ticket lo escriben
`core/rift.py` (Python, para Windows) y `native/troveinject.c` (C, para Wine).
Dos códigos que tienen que coincidir byte a byte o el juego rebota al login, así
que no se confía en que coincidan: `tools/test_wine_helper.py` monta un Trove de
mentira que recoge el ticket como el de verdad y comprueba que lo que descifra
es *exactamente* lo que habría armado Windows.

**El loader del anti-cheat.** Si existe `xldr_Trove_GL_loader_x64.exe` junto al
ejecutable, se lanza a través de él con el nombre del juego como `argv[1]` (no
como `argv[0]`: ahí el loader aborta con el código 1038). Si no existe, se lanza
el juego directamente.

### Almacenamiento

Todo va a `%APPDATA%/TroveAccountsHub`:

- `prefs.json` — preferencias, cuentas y alias
- `auth-<hash>.bin` — ticket por cuenta, cifrado con DPAPI
- `cred-<hash>.bin` — contraseña por cuenta, cifrada con DPAPI
- `update-<rama>-<hash>.sqlite` — estado de "qué hay en disco" por instalación
- `macaddr.txt` — identificador de dispositivo sintético, estable entre sesiones

Las contraseñas nunca se guardan en texto plano: si DPAPI no está disponible,
sencillamente no se recuerdan.

**La carpeta anterior se adopta, no se abandona.** La aplicación se llamaba
Trove Launcher y guardaba en `%APPDATA%/TroveLauncher`. Al arrancar por primera
vez con el nombre nuevo, si la carpeta nueva no tiene `prefs.json` y la vieja sí,
se copia su contenido entero y se deja constancia en `adopted-from.txt`. Detalles
que importan:

- Se **copia**, no se mueve: volver a una versión anterior sigue encontrando sus
  datos donde estaban. El precio es un duplicado en disco.
- Nunca pisa un fichero que ya exista en destino, y un fallo a medias no impide
  arrancar: lo que se haya traído se queda y el resto sigue en la carpeta vieja.
- Los blobs DPAPI (`auth-*.bin`, `cred-*.bin`) se descifran igual desde la ruta
  nueva: van atados al usuario de Windows y a la máquina, no a la carpeta.
- La entropía DPAPI de las contraseñas sigue diciendo
  `TroveLauncher.credentials.v1`. Es un identificador, no un nombre a la vista:
  cambiarlo dejaría ilegibles las contraseñas ya guardadas.

Como esto corre una sola vez en la máquina de cada usuario y sobre sus cuentas,
`tools/test_paths_adopt.py` lo repite a voluntad contra carpetas temporales
(`python tools/test_paths_adopt.py`, sin dependencias).

### Secretos

La contraseña y el ticket —que es una credencial viva unas 48 horas— no se
guardan en la carpeta de datos, sino en el almacén del sistema:

| | Dónde | Qué lo protege |
| --- | --- | --- |
| Windows | `<datos>/cred-<hash>.bin`, `auth-<hash>.bin` | DPAPI, ámbito de usuario |
| Linux | Llavero del escritorio (`keyring`) | La sesión del usuario |
| Sin ninguno | En ningún sitio | — |

**Si no hay almacén, no se guarda nada**: la contraseña no se recuerda y el
ticket vive sólo en memoria, así que hay que volver a entrar en el siguiente
arranque. Es molesto y es lo correcto; antes, fuera de Windows, el ticket se
escribía en claro.

### Pruebas

No hay framework: son guiones sueltos, sin dependencias salvo donde se indica.

| | Qué comprueba |
| --- | --- |
| `tools/test_vault.py` | El almacén de secretos y su degradación sin llavero. |
| `tools/test_paths_adopt.py` | La adopción de la carpeta de datos anterior. |
| `tools/test_installs_prefix.py` | Encontrar Trove dentro de prefijos de Proton y Wine (necesita mingw-w64). |
| `tools/test_proton_runner.py` | Elegir el runner del prefijo y avisar del símbolo que le falta al Wine del sistema. |
| `tools/test_launch_order.py` | Que «Launch all» reparta una partida por cuenta y que el auto-relog haga lo que dice. |
| `tools/test_wine_helper.py` | La entrega del ticket de extremo a extremo bajo Wine (necesita wine64 y mingw-w64). |
| `tools/check_linux.py` | No es una prueba: comprueba que ESTE equipo está listo para usarla en Linux. |

## Estado

Funcionalidad completa y en uso. La interfaz ya no es un borrador, pero sigue
moviéndose: lo que se toca son colores, densidad y rótulos, no lo que hay
debajo.
