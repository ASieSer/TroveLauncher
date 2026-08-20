#!/bin/sh
# Compila el ayudante Win32 que Trove Accounts Hub usa dentro del prefijo de
# Wine para entregarle el ticket al juego (ver native/troveinject.c).
#
# Sólo hace falta para REGENERAR el binario; la aplicación trae uno ya
# compilado en native/troveinject.exe.
#
#   sudo apt install mingw-w64      # o el paquete equivalente
#   tools/build_helper.sh
set -eu
cd "$(dirname "$0")/.."
CC=${CC:-x86_64-w64-mingw32-gcc}
$CC -Os -s -Wall -Wextra -o native/troveinject.exe native/troveinject.c
echo "native/troveinject.exe  $(wc -c < native/troveinject.exe) bytes"
