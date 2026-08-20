/*
 * Un loader de anti-cheat de mentira, con la misma firma que el de XIGNCODE:
 *
 *   loader.exe <nombre del juego> <argumentos del juego...>
 *
 * Arranca el ejecutable que le nombran en argv[1] con el resto de argumentos y
 * SE MUERE, que es justo lo que obliga al ayudante a resolver el pid real del
 * juego en vez de vigilar el del loader. Si argv[1] no parece un ejecutable,
 * sale con 1038, como el de verdad cuando le desplazan los argumentos.
 *
 *   x86_64-w64-mingw32-gcc -O2 -o xldr_Trove_GL_loader_x64.exe fakeloader.c
 */

#include <windows.h>
#include <stdio.h>
#include <string.h>

int main(int argc, char **argv)
{
    if (argc < 2) return 1038;
    const char *game = argv[1];
    size_t n = strlen(game);
    if (n < 4 || _stricmp(game + n - 4, ".exe") != 0) {
        fprintf(stderr, "loader: argv[1] no es un ejecutable (%s)\n", game);
        return 1038;   /* el error real cuando el juego no va en argv[1] */
    }

    char dir[MAX_PATH];
    GetModuleFileNameA(NULL, dir, sizeof dir);
    char *slash = strrchr(dir, '\\');
    if (slash) *slash = '\0';

    char cmd[8192];
    int len = snprintf(cmd, sizeof cmd, "\"%s\\%s\"", dir, game);
    for (int i = 2; i < argc && len < (int)sizeof cmd; i++)
        len += snprintf(cmd + len, sizeof cmd - len,
                        strchr(argv[i], ' ') ? " \"%s\"" : " %s", argv[i]);

    STARTUPINFOA si; ZeroMemory(&si, sizeof si); si.cb = sizeof si;
    PROCESS_INFORMATION pi; ZeroMemory(&pi, sizeof pi);
    /* Hereda los handles, como hace el loader real: el juego los necesita. */
    if (!CreateProcessA(NULL, cmd, NULL, NULL, TRUE, 0, NULL, dir, &si, &pi)) {
        fprintf(stderr, "loader: CreateProcess falló %lu\n", GetLastError());
        return 2;
    }
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return 0;   /* el loader se va y deja al juego corriendo */
}
