# Atribución

Este proyecto porta y adapta código de **BetterTroveTools**, de Aallyn Reed,
publicado bajo licencia MIT:

- Repositorio: https://github.com/AallynReed/BetterTroveTools
- Copyright (c) 2026-Present Aallyn Reed
- Texto completo de la licencia: [`LICENSE-BetterTroveTools`](LICENSE-BetterTroveTools)

## Qué se ha portado

Los cinco módulos del núcleo, tomados de `backend/trove_launcher/` y adaptados
al paquete `core/` de este proyecto:

| Fichero local | Origen | Cambios |
| --- | --- | --- |
| `core/cdn.py` | `backend/trove_launcher/cdn.py` | Sin cambios funcionales. |
| `core/updater.py` | `backend/trove_launcher/updater.py` | Sin cambios funcionales. |
| `core/trionauth.py` | `backend/trove_launcher/trionauth.py` | Sin cambios funcionales. |
| `core/inject.py` | `backend/trove_launcher/inject.py` | Sin cambios funcionales. |
| `core/launch.py` | `backend/trove_launcher/launch.py` | Sin cambios funcionales. |

A su vez, ese código fue vendorizado por BetterTroveTools desde el proyecto
TroveImposter, según indica su propia documentación.

La validación de ejecutables mediante cabecera PE que hay en `core/installs.py`
se basa en `utils/executable.py` del mismo proyecto.

## Qué es propio de este repositorio

`core/paths.py`, `core/prefs.py`, `core/installs.py`, `core/service.py`,
`api.py`, `main.py` y todo lo que hay bajo `web/`.
