#!/bin/sh
# Builds the Win32 helper Trove Accounts Hub uses inside the Wine prefix to
# hand the ticket to the game (see native/troveinject.c).
#
# Only needed to REGENERATE the binary; the application ships one already built
# at native/troveinject.exe.
#
#   sudo apt install mingw-w64      # o el paquete equivalente
#   tools/build_helper.sh
set -eu
cd "$(dirname "$0")/.."
CC=${CC:-x86_64-w64-mingw32-gcc}
$CC -Os -s -Wall -Wextra -o native/troveinject.exe native/troveinject.c
echo "native/troveinject.exe  $(wc -c < native/troveinject.exe) bytes"
