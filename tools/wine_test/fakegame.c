/*
 * Un Trove de mentira, para probar la entrega del ticket sin tener el juego.
 *
 * Hace lo mismo que hace el juego de verdad, que es lo único que aquí importa:
 *
 *   1. lee -k "<map>:<evt>:<pid del lanzador>",
 *   2. abre el proceso del lanzador y DUPLICA de él los dos handles —así es
 *      como lo hace Trove, y por eso el lanzador tiene que seguir vivo—,
 *   3. mapea el blob, lo descifra con RC4 usando la clave de los 8 primeros
 *      bytes y comprueba la firma "RIFT",
 *   4. deja el ticket descifrado en un fichero para que el test lo compare,
 *   5. y señala el evento, que es lo que el lanzador está esperando.
 *
 * Con --stay se queda vivo hasta que lo maten (para probar kill/wait); si no,
 * sale con el código 7, que el test comprueba.
 *
 *   x86_64-w64-mingw32-gcc -O2 -o fakegame.exe fakegame.c
 */

#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void rc4(const unsigned char *key, size_t klen,
                unsigned char *data, size_t dlen)
{
    unsigned char s[256];
    for (int i = 0; i < 256; i++) s[i] = (unsigned char)i;
    for (int i = 0, j = 0; i < 256; i++) {
        j = (j + s[i] + key[i % klen]) & 0xFF;
        unsigned char t = s[i]; s[i] = s[j]; s[j] = t;
    }
    int i = 0, j = 0;
    for (size_t n = 0; n < dlen; n++) {
        i = (i + 1) & 0xFF;
        j = (j + s[i]) & 0xFF;
        unsigned char t = s[i]; s[i] = s[j]; s[j] = t;
        data[n] ^= s[(s[i] + s[j]) & 0xFF];
    }
}

static void report(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
    fputc('\n', stderr);
    fflush(stderr);
}

int main(int argc, char **argv)
{
    const char *k = NULL, *auth = NULL, *out = getenv("FAKEGAME_OUT");
    int stay = 0;
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-k") == 0 && i + 1 < argc) k = argv[++i];
        else if (strcmp(argv[i], "-C") == 0 && i + 1 < argc) auth = argv[++i];
        else if (strcmp(argv[i], "--stay") == 0) stay = 1;
    }
    /* También por entorno: el lanzador arma la línea de órdenes él solo y no
       hay dónde colar un argumento extra desde el test. */
    if (getenv("FAKEGAME_STAY")) stay = 1;
    if (!k) { report("sin -k"); return 2; }

    unsigned long long hmap = 0, hevt = 0;
    unsigned long lpid = 0;
    if (sscanf(k, "%llx:%llx:%lu", &hmap, &hevt, &lpid) != 3) {
        report("-k ilegible: %s", k);
        return 3;
    }

    HANDLE launcher = OpenProcess(PROCESS_DUP_HANDLE, FALSE, (DWORD)lpid);
    if (!launcher) { report("OpenProcess(%lu) falló: %lu", lpid, GetLastError()); return 4; }

    HANDLE my_map = NULL, my_evt = NULL;
    if (!DuplicateHandle(launcher, (HANDLE)(ULONG_PTR)hmap, GetCurrentProcess(),
                         &my_map, 0, FALSE, DUPLICATE_SAME_ACCESS)) {
        report("DuplicateHandle(map) falló: %lu", GetLastError());
        return 5;
    }
    if (!DuplicateHandle(launcher, (HANDLE)(ULONG_PTR)hevt, GetCurrentProcess(),
                         &my_evt, 0, FALSE, DUPLICATE_SAME_ACCESS)) {
        report("DuplicateHandle(event) falló: %lu", GetLastError());
        return 6;
    }
    CloseHandle(launcher);

    unsigned char *view = (unsigned char *)MapViewOfFile(my_map, FILE_MAP_READ, 0, 0, 0);
    if (!view) { report("MapViewOfFile falló: %lu", GetLastError()); return 7; }

    unsigned char key[8];
    memcpy(key, view, 8);
    unsigned int len = (unsigned int)view[8] | ((unsigned int)view[9] << 8)
                     | ((unsigned int)view[10] << 16) | ((unsigned int)view[11] << 24);
    unsigned char *ct = (unsigned char *)malloc(len);
    memcpy(ct, view + 12, len);
    UnmapViewOfFile(view);
    rc4(key, 8, ct, len);

    if (memcmp(ct, "\x54\x46\x49\x52", 4) != 0) { report("sin firma RIFT"); return 8; }

    if (out) {
        FILE *f = fopen(out, "wb");
        if (f) {
            /* El ticket va tras la firma y termina en NUL. */
            fwrite(ct + 4, 1, len - 5, f);
            fclose(f);
        }
        char apath[1024];
        snprintf(apath, sizeof apath, "%s.auth", out);
        FILE *fa = fopen(apath, "wb");
        if (fa) { fputs(auth ? auth : "", fa); fclose(fa); }
    }
    free(ct);

    /* Esto es lo que desbloquea al lanzador. */
    SetEvent(my_evt);
    report("ticket consumido (%u bytes)", len);

    if (stay) { for (;;) Sleep(1000); }
    Sleep(300);
    return 7;
}
