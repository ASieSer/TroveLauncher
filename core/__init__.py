"""Núcleo del launcher: hablar con el CDN de Trion, autenticarse y arrancar Trove.

Módulos portados desde BetterTroveTools (MIT, (c) 2026-Present Aallyn Reed) —
ver LICENSE-BetterTroveTools y NOTICE en la raíz del proyecto:

  * ``cdn``        - cliente del CDN de actualizaciones + parsers de puntero/manifiesto.
  * ``updater``    - mantiene una instalación al día (descarga delta, estado en sqlite).
  * ``trionauth``  - credenciales de Glyph -> ticket listo para lanzar, cacheado con DPAPI.
  * ``inject``     - entrega ese ticket a Trove_x64.exe igual que hace Glyph.
  * ``launch``     - cadenas de servidor de auth por región + traer la ventana al frente.

Módulos propios:

  * ``paths``      - dónde guardamos nuestros datos.
  * ``prefs``      - preferencias, cuentas guardadas y contraseñas cifradas con DPAPI.
  * ``installs``   - detección de instalaciones de Trove (registro, Steam, personalizadas).
  * ``service``    - orquestador: hilo de trabajo, 2FA, auto-relog, progreso hacia la UI.

Sólo dependen de ``requests`` + stdlib + ctypes. La ruta de lanzamiento es
Windows-only (usa las APIs Win32 de procesos y handles); el actualizador por sí
solo funciona en cualquier plataforma.
"""
