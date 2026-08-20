# Trove Launcher

Launcher propio para Trove: mantiene la instalación al día contra el CDN de
Trion, se autentica con tus credenciales de Glyph y arranca el juego, sin
necesidad del cliente de Glyph.

Interfaz HTML/CSS/JS sobre WebView2 (pywebview), backend Python.

> Porta código de [BetterTroveTools](https://github.com/AallynReed/BetterTroveTools)
> (MIT). Ver [`NOTICE.md`](NOTICE.md).

## Requisitos

- Windows (la ruta de lanzamiento usa las APIs Win32 de procesos y handles)
- Python 3.10 o superior
- WebView2 Runtime — ya viene con Windows 11
- Una cuenta de Glyph con Trove

## Instalación

```bash
pip install -r requirements.txt
```

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
| `launch.py` | Cadenas de servidor de autenticación por región y foco de la ventana. |
| `installs.py` | Detección de instalaciones: registro, Steam y carpetas propias. |
| `prefs.py` | Preferencias, cuentas y contraseñas cifradas con DPAPI. |
| `service.py` | Orquestador: hilo de trabajo, 2FA, auto-relog y progreso. |

### Interfaz (`web/`)

| Fichero | Responsabilidad |
| --- | --- |
| `index.html` | Estructura: barra superior con la marca, barra de acciones, tablero de cuentas, barra de estado y panel de ajustes. |
| `css/app.css` | Tema oscuro plano. Un único color vivo —el acento— tiñe el fondo entero en un porcentaje bajo y marca la acción principal y los estados activos. |
| `js/app.js` | Todo el comportamiento: pintado del tablero, arrastrar y soltar, diálogos, preferencias y eventos que llegan del backend. |

Las cuentas se pintan siempre como tarjetas, agrupadas por categorías que se
pliegan y se reordenan arrastrando. **El estado de una cuenta vive en su
tarjeta y en ningún otro sitio**: la cabecera de un grupo no lo resume, porque
repetir «2 running» ahí no dice de quién es ni deja actuar sobre ello. Lo único
global es el recuento del encabezado y los mensajes de operación de la barra
inferior, donde también está la versión.

#### Apariencia

Todo se ajusta desde Ajustes → Appearance y se guarda en `theme` dentro de
`prefs.json`:

- **Font** — tipografía de toda la interfaz: del sistema, Quicksand o Comfortaa.
- **Club theme** — `Mystic Cave`, `Arsyn`, `Sayro` o ninguno. Un club cambia la
  marca de la barra superior por su logo (`web/img/`) y su nombre, y **fija el
  acento** a su color: morado en Mystic Cave y Arsyn, rojo oscuro en Sayro.
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

**El loader del anti-cheat.** Si existe `xldr_Trove_GL_loader_x64.exe` junto al
ejecutable, se lanza a través de él con el nombre del juego como `argv[1]` (no
como `argv[0]`: ahí el loader aborta con el código 1038). Si no existe, se lanza
el juego directamente.

### Almacenamiento

Todo va a `%APPDATA%/TroveLauncher`:

- `prefs.json` — preferencias, cuentas y alias
- `auth-<hash>.bin` — ticket por cuenta, cifrado con DPAPI
- `cred-<hash>.bin` — contraseña por cuenta, cifrada con DPAPI
- `update-<rama>-<hash>.sqlite` — estado de "qué hay en disco" por instalación
- `macaddr.txt` — identificador de dispositivo sintético, estable entre sesiones

Las contraseñas nunca se guardan en texto plano: si DPAPI no está disponible,
sencillamente no se recuerdan.

## Estado

Funcionalidad completa y en uso. La interfaz ya no es un borrador, pero sigue
moviéndose: lo que se toca son colores, densidad y rótulos, no lo que hay
debajo.
