/*
 * troveinject.exe — el brazo Windows de Trove Accounts Hub.
 *
 * En Windows la aplicación entrega el ticket ella misma (ver core/inject.py).
 * En Linux no puede: el juego recoge el ticket de un file-mapping de Windows
 * haciendo OpenProcess(pid del lanzador) + DuplicateHandle, y esos son objetos
 * del kernel de Windows que un proceso Linux nativo no puede ni crear ni
 * compartir. Este programa es ese lanzador, corriendo dentro del prefijo de
 * Wine, junto al juego.
 *
 * Hace exactamente lo que hace inject.py, y por las mismas razones:
 *
 *   1. arma el blob "RIFT" cifrado con RC4 a partir del ticket,
 *   2. lo deja en un file-mapping heredable respaldado por el fichero de
 *      paginación, con un evento auto-reset al lado,
 *   3. lanza el juego —o el loader del anti-cheat, si está— pasándole los dos
 *      handles y SU PROPIO pid en -k "<map>:<evt>:<pid>",
 *   4. espera en el evento, que el juego señala al leer y descifrar el ticket,
 *   5. y NO cierra los handles: el juego los duplica desde nosotros durante
 *      toda la partida.
 *
 * Por ese punto 5 esto es un servidor y no una orden suelta: mientras haya una
 * partida abierta, este proceso tiene que seguir vivo. Lo arranca el lado
 * Linux (core/winehost.py) y le habla por stdin/stdout con líneas de texto:
 *
 *   -> <id> spawn <exe64> <ticket64> <auth64> <parent64> <wait_ms>
 *   <- <id> ok <pid> <consumed 0|1> <via_loader 0|1>
 *   -> <id> wait <pid>            <- <id> ok <código de salida>   (cuando salga)
 *   -> <id> kill <pid>            <- <id> ok
 *   -> <id> list                  <- <id> ok <pid>,<ppid>,<nombre64> ...
 *   -> <id> ping                  <- <id> ok <pid del propio ayudante>
 *   <- log <texto64>              (en cualquier momento)
 *   <- <id> err <mensaje64>
 *
 * Los argumentos van en base64 porque el ticket es XML multilínea y las rutas
 * llevan espacios; el protocolo se queda en una línea por mensaje, que es lo
 * que hace trivial leerlo desde el otro lado.
 *
 * Se compila cruzado desde Linux, sin dependencias:
 *   x86_64-w64-mingw32-gcc -O2 -municode -o troveinject.exe troveinject.c
 */

#include <windows.h>
#include <tlhelp32.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define LINE_MAX_BYTES (1 << 20)   /* el ticket en base64 ronda las decenas de KB */

static CRITICAL_SECTION g_out_lock;   /* stdout lo comparten el bucle y los hilos de wait */

/* --- salida ------------------------------------------------------------- */

static const char B64[] =
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

static char *b64_encode(const unsigned char *in, size_t len)
{
    size_t out_len = 4 * ((len + 2) / 3);
    char *out = (char *)malloc(out_len + 1);
    if (!out) return NULL;
    size_t i, j;
    for (i = 0, j = 0; i < len;) {
        unsigned a = i < len ? in[i++] : 0;
        unsigned b = i < len ? in[i++] : 0;
        unsigned c = i < len ? in[i++] : 0;
        unsigned t = (a << 16) | (b << 8) | c;
        out[j++] = B64[(t >> 18) & 0x3F];
        out[j++] = B64[(t >> 12) & 0x3F];
        out[j++] = B64[(t >> 6) & 0x3F];
        out[j++] = B64[t & 0x3F];
    }
    static const int pad[] = {0, 2, 1};
    for (int p = 0; p < pad[len % 3]; p++) out[out_len - 1 - p] = '=';
    out[out_len] = '\0';
    return out;
}

static int b64_val(char c)
{
    if (c >= 'A' && c <= 'Z') return c - 'A';
    if (c >= 'a' && c <= 'z') return c - 'a' + 26;
    if (c >= '0' && c <= '9') return c - '0' + 52;
    if (c == '+') return 62;
    if (c == '/') return 63;
    return -1;
}

/* Devuelve bytes reservados con malloc y escribe la longitud en *out_len. */
static unsigned char *b64_decode(const char *in, size_t *out_len)
{
    size_t len = strlen(in);
    unsigned char *out = (unsigned char *)malloc(len / 4 * 3 + 4);
    if (!out) return NULL;
    size_t o = 0;
    int quad[4], n = 0;
    for (size_t i = 0; i < len; i++) {
        if (in[i] == '=' || in[i] == '\r' || in[i] == '\n') continue;
        int v = b64_val(in[i]);
        if (v < 0) continue;
        quad[n++] = v;
        if (n == 4) {
            unsigned t = (quad[0] << 18) | (quad[1] << 12) | (quad[2] << 6) | quad[3];
            out[o++] = (t >> 16) & 0xFF;
            out[o++] = (t >> 8) & 0xFF;
            out[o++] = t & 0xFF;
            n = 0;
        }
    }
    if (n == 3) {
        unsigned t = (quad[0] << 18) | (quad[1] << 12) | (quad[2] << 6);
        out[o++] = (t >> 16) & 0xFF;
        out[o++] = (t >> 8) & 0xFF;
    } else if (n == 2) {
        unsigned t = (quad[0] << 18) | (quad[1] << 12);
        out[o++] = (t >> 16) & 0xFF;
    }
    out[o] = '\0';
    *out_len = o;
    return out;
}

static void emit(const char *fmt, ...)
{
    va_list ap;
    EnterCriticalSection(&g_out_lock);
    va_start(ap, fmt);
    vfprintf(stdout, fmt, ap);
    va_end(ap);
    fputc('\n', stdout);
    fflush(stdout);
    LeaveCriticalSection(&g_out_lock);
}

static void emit_log(const char *text)
{
    char *b = b64_encode((const unsigned char *)text, strlen(text));
    if (!b) return;
    emit("log %s", b);
    free(b);
}

static void emit_err(long id, const char *text)
{
    char *b = b64_encode((const unsigned char *)text, strlen(text));
    if (!b) return;
    emit("%ld err %s", id, b);
    free(b);
}

static void emit_winerr(long id, const char *what)
{
    char buf[512];
    snprintf(buf, sizeof buf, "%s (error %lu)", what, (unsigned long)GetLastError());
    emit_err(id, buf);
}

/* --- utf-8 <-> utf-16 ---------------------------------------------------- */

static wchar_t *utf8_to_wide(const unsigned char *s, size_t len)
{
    int n = MultiByteToWideChar(CP_UTF8, 0, (const char *)s, (int)len, NULL, 0);
    wchar_t *w = (wchar_t *)malloc((size_t)(n + 1) * sizeof(wchar_t));
    if (!w) return NULL;
    MultiByteToWideChar(CP_UTF8, 0, (const char *)s, (int)len, w, n);
    w[n] = L'\0';
    return w;
}

static char *wide_to_utf8(const wchar_t *w)
{
    int n = WideCharToMultiByte(CP_UTF8, 0, w, -1, NULL, 0, NULL, NULL);
    char *s = (char *)malloc((size_t)n);
    if (!s) return NULL;
    WideCharToMultiByte(CP_UTF8, 0, w, -1, s, n, NULL, NULL);
    return s;
}

/* --- el blob RIFT (idéntico a inject.build_rift_buffer) ------------------ */

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

/*
 * rcKey(8) ++ len(uint32 LE) ++ RC4("RIFT" ++ ticket ++ \0)
 *
 * El ticket se recorta igual que en Python: desde la primera línea que empiece
 * por "Signature:" o "<?xml", sin los \r y sin espacios al final.
 */
static unsigned char *build_rift(const char *ticket, size_t *out_len)
{
    size_t n = strlen(ticket);
    char *clean = (char *)malloc(n + 1);
    if (!clean) return NULL;
    size_t c = 0;
    for (size_t i = 0; i < n; i++) if (ticket[i] != '\r') clean[c++] = ticket[i];
    clean[c] = '\0';

    const char *start = clean;
    const char *sig = strstr(clean, "Signature:");
    const char *xml = strstr(clean, "<?xml");
    if (sig && xml) start = sig < xml ? sig : xml;
    else if (sig)   start = sig;
    else if (xml)   start = xml;
    /* Sólo cuenta si abre línea, como el split('\n') del original. */
    if (start != clean && start[-1] != '\n') start = clean;

    size_t slen = strlen(start);
    while (slen && (start[slen - 1] == '\n' || start[slen - 1] == ' '
                    || start[slen - 1] == '\t')) slen--;

    size_t body = 4 + slen + 1;                 /* "RIFT" + ticket + NUL */
    unsigned char *ct = (unsigned char *)malloc(body);
    if (!ct) { free(clean); return NULL; }
    memcpy(ct, "\x54\x46\x49\x52", 4);          /* 'TFIR' == "RIFT" leído LE */
    memcpy(ct + 4, start, slen);
    ct[4 + slen] = '\0';
    free(clean);

    unsigned char key[8];
    HCRYPTPROV prov = 0;
    if (CryptAcquireContextW(&prov, NULL, NULL, PROV_RSA_FULL,
                             CRYPT_VERIFYCONTEXT | CRYPT_SILENT)) {
        CryptGenRandom(prov, sizeof key, key);
        CryptReleaseContext(prov, 0);
    } else {
        /* Sin proveedor, algo impredecible dentro de esta sesión. */
        LARGE_INTEGER qpc; QueryPerformanceCounter(&qpc);
        unsigned long long seed = (unsigned long long)qpc.QuadPart
                                ^ ((unsigned long long)GetCurrentProcessId() << 32);
        for (int i = 0; i < 8; i++) { seed = seed * 6364136223846793005ULL + 1442695040888963407ULL;
                                      key[i] = (unsigned char)(seed >> 33); }
    }
    rc4(key, sizeof key, ct, body);

    size_t total = 8 + 4 + body;
    unsigned char *out = (unsigned char *)malloc(total);
    if (!out) { free(ct); return NULL; }
    memcpy(out, key, 8);
    unsigned int le = (unsigned int)body;
    out[8] = le & 0xFF; out[9] = (le >> 8) & 0xFF;
    out[10] = (le >> 16) & 0xFF; out[11] = (le >> 24) & 0xFF;
    memcpy(out + 12, ct, body);
    free(ct);
    *out_len = total;
    return out;
}

/* --- procesos ------------------------------------------------------------ */

static DWORD find_pid_by_name(const wchar_t *name)
{
    HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    if (snap == INVALID_HANDLE_VALUE) return 0;
    PROCESSENTRY32W e; e.dwSize = sizeof e;
    DWORD found = 0;
    if (Process32FirstW(snap, &e)) {
        do {
            if (_wcsicmp(e.szExeFile, name) == 0) found = e.th32ProcessID;
        } while (Process32NextW(snap, &e));
    }
    CloseHandle(snap);
    return found;
}

/*
 * Traduce el pid de CreateProcess al pid REAL del juego.
 *
 * Con el anti-cheat no lanzamos el juego sino el loader, que arranca Trove y
 * termina: vigilar el pid del loader haría creer que la partida se cerró a los
 * pocos segundos. Se busca primero el hijo directo del loader; si el loader ya
 * murió y se perdió el parentesco, vale cualquier proceso del juego que no
 * estuviera antes. Mismo criterio que inject.resolve_game_pid.
 */
static DWORD resolve_game_pid(DWORD spawn_pid, const wchar_t *exe_name,
                              DWORD timeout_ms)
{
    DWORD deadline = GetTickCount() + timeout_ms;
    do {
        HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
        if (snap != INVALID_HANDLE_VALUE) {
            PROCESSENTRY32W e; e.dwSize = sizeof e;
            DWORD child = 0, any = 0, self = 0;
            if (Process32FirstW(snap, &e)) {
                do {
                    if (_wcsicmp(e.szExeFile, exe_name) != 0) continue;
                    if (e.th32ProcessID == spawn_pid) self = e.th32ProcessID;
                    else if (e.th32ParentProcessID == spawn_pid) child = e.th32ProcessID;
                    else if (!any) any = e.th32ProcessID;
                } while (Process32NextW(snap, &e));
            }
            CloseHandle(snap);
            if (self) return self;         /* instalación sin loader */
            if (child) return child;
            if (any) return any;
        }
        Sleep(400);
    } while ((long)(deadline - GetTickCount()) > 0);
    return spawn_pid;   /* algo hay que vigilar */
}

/* --- lanzar -------------------------------------------------------------- */

/*
 * Los handles del mapping y del evento NO se cierran nunca: el juego los
 * duplica desde este proceso mientras dure la partida. Es la misma fuga
 * deliberada de dos handles por lanzamiento que hace Glyph, y la razón de que
 * este ayudante siga vivo hasta que la aplicación se cierre.
 */
static void cmd_spawn(long id, const wchar_t *exe, const char *ticket,
                      const char *auth, const wchar_t *parent, DWORD wait_ms)
{
    /* ¿Está el loader del anti-cheat junto al ejecutable? */
    wchar_t dir[MAX_PATH * 2];
    wcsncpy(dir, exe, MAX_PATH * 2 - 1); dir[MAX_PATH * 2 - 1] = L'\0';
    wchar_t *slash = wcsrchr(dir, L'\\');
    wchar_t *game_name = slash ? slash + 1 : dir;
    wchar_t loader[MAX_PATH * 2];
    swprintf(loader, MAX_PATH * 2, L"%.*ls\\xldr_Trove_GL_loader_x64.exe",
             slash ? (int)(slash - dir) : 0, dir);
    BOOL via_loader = GetFileAttributesW(loader) != INVALID_FILE_ATTRIBUTES;
    const wchar_t *exe_to_run = via_loader ? loader : exe;

    size_t blob_len = 0;
    unsigned char *blob = build_rift(ticket, &blob_len);
    if (!blob) { emit_err(id, "no hay memoria para el blob del ticket"); return; }

    SECURITY_ATTRIBUTES sa = {sizeof sa, NULL, TRUE};
    HANDLE hmap = CreateFileMappingW(INVALID_HANDLE_VALUE, &sa, PAGE_READWRITE,
                                     0, (DWORD)blob_len, NULL);
    if (!hmap) { free(blob); emit_winerr(id, "CreateFileMapping falló"); return; }
    void *view = MapViewOfFile(hmap, FILE_MAP_WRITE, 0, 0, blob_len);
    if (!view) { CloseHandle(hmap); free(blob); emit_winerr(id, "MapViewOfFile falló"); return; }
    memcpy(view, blob, blob_len);
    UnmapViewOfFile(view);
    SecureZeroMemory(blob, blob_len);
    free(blob);

    HANDLE hevent = CreateEventW(&sa, FALSE, FALSE, NULL);
    if (!hevent) { CloseHandle(hmap); emit_winerr(id, "CreateEvent falló"); return; }

    /* Reparent: el loader como hijo de Glyph, para que la cadena de lanzamiento
       sea la de una partida legítima (ver PROC_THREAD_ATTRIBUTE_PARENT_PROCESS
       en inject.py). Si no está Glyph, se lanza igual sin reparent. */
    HANDLE parent_handle = NULL;
    if (parent && parent[0]) {
        DWORD ppid = find_pid_by_name(parent);
        if (!ppid) {
            emit_log("[inject] reparent: el proceso padre no está corriendo; "
                     "se lanza sin reparent");
        } else {
            parent_handle = OpenProcess(PROCESS_CREATE_PROCESS, FALSE, ppid);
            if (!parent_handle)
                emit_log("[inject] reparent: OpenProcess falló; se lanza sin reparent");
            else
                emit_log("[inject] reparent: el loader colgará de Glyph");
        }
    }

    SIZE_T attr_size = 0;
    InitializeProcThreadAttributeList(NULL, 1, 0, &attr_size);
    LPPROC_THREAD_ATTRIBUTE_LIST attr =
        (LPPROC_THREAD_ATTRIBUTE_LIST)malloc(attr_size);
    if (!attr || !InitializeProcThreadAttributeList(attr, 1, 0, &attr_size)) {
        if (parent_handle) CloseHandle(parent_handle);
        free(attr);
        emit_winerr(id, "InitializeProcThreadAttributeList falló");
        return;
    }
    /* O la lista blanca de handles O el padre, nunca las dos: con un padre
       declarado, los handles tendrían que ser SUYOS y Windows responde 87. */
    HANDLE handles[2] = {hmap, hevent};
    BOOL attr_ok = parent_handle
        ? UpdateProcThreadAttribute(attr, 0, PROC_THREAD_ATTRIBUTE_PARENT_PROCESS,
                                    &parent_handle, sizeof parent_handle, NULL, NULL)
        : UpdateProcThreadAttribute(attr, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
                                    handles, sizeof handles, NULL, NULL);
    if (!attr_ok) {
        DeleteProcThreadAttributeList(attr); free(attr);
        if (parent_handle) CloseHandle(parent_handle);
        emit_winerr(id, "UpdateProcThreadAttribute falló");
        return;
    }

    STARTUPINFOEXW si; ZeroMemory(&si, sizeof si);
    si.StartupInfo.cb = sizeof si;
    si.lpAttributeList = attr;
    PROCESS_INFORMATION pi; ZeroMemory(&pi, sizeof pi);

    wchar_t *auth_w = utf8_to_wide((const unsigned char *)auth, strlen(auth));
    wchar_t cmd[8192];
    if (via_loader)
        /* argv[0] = ruta del loader, argv[1] = nombre del juego; al revés el
           loader aborta con 1038 sin llegar a arrancar nada. */
        swprintf(cmd, 8192, L"\"%ls\" %ls -k %08llx:%08llx:%lu -C \"%ls\" -lang en",
                 exe_to_run, game_name, (unsigned long long)(ULONG_PTR)hmap,
                 (unsigned long long)(ULONG_PTR)hevent,
                 (unsigned long)GetCurrentProcessId(), auth_w);
    else
        swprintf(cmd, 8192, L"\"%ls\" -k %08llx:%08llx:%lu -C \"%ls\"",
                 exe, (unsigned long long)(ULONG_PTR)hmap,
                 (unsigned long long)(ULONG_PTR)hevent,
                 (unsigned long)GetCurrentProcessId(), auth_w);
    free(auth_w);

    wchar_t workdir[MAX_PATH * 2];
    wcsncpy(workdir, exe_to_run, MAX_PATH * 2 - 1); workdir[MAX_PATH * 2 - 1] = L'\0';
    wchar_t *wslash = wcsrchr(workdir, L'\\');
    if (wslash) *wslash = L'\0';

    emit_log(via_loader ? "[inject] lanzando a través del loader del anti-cheat"
                        : "[inject] lanzando el juego directamente");

    /* Heredar handles sólo en la ruta sin reparent, que es la que se apoya en
       la lista blanca; con padre declarado el ticket va por DuplicateHandle. */
    BOOL ok = CreateProcessW(exe_to_run, cmd, NULL, NULL,
                             parent_handle ? FALSE : TRUE,
                             EXTENDED_STARTUPINFO_PRESENT, NULL, workdir,
                             &si.StartupInfo, &pi);
    SecureZeroMemory(cmd, sizeof cmd);
    DeleteProcThreadAttributeList(attr); free(attr);
    if (parent_handle) CloseHandle(parent_handle);
    if (!ok) {
        CloseHandle(hmap); CloseHandle(hevent);
        emit_winerr(id, "CreateProcess falló");
        return;
    }
    CloseHandle(pi.hThread);

    DWORD consumed = WaitForSingleObject(hevent, wait_ms) == WAIT_OBJECT_0;
    if (!consumed) {
        DWORD code = 0;
        GetExitCodeProcess(pi.hProcess, &code);
        emit_log(code == STILL_ACTIVE
                 ? "[inject] el juego no ha señalado todavía; puede recuperarse"
                 : "[inject] el proceso salió antes de consumir el ticket");
    }
    CloseHandle(pi.hProcess);

    DWORD pid = resolve_game_pid(pi.dwProcessId, game_name, 30000);
    emit("%ld ok %lu %d %d", id, (unsigned long)pid, consumed ? 1 : 0,
         via_loader ? 1 : 0);
    /* hmap y hevent se quedan abiertos a propósito hasta que muera el ayudante. */
}

/* --- esperar la salida, sin bloquear el bucle de órdenes ----------------- */

struct wait_job { long id; DWORD pid; };

static DWORD WINAPI wait_thread(LPVOID arg)
{
    struct wait_job *job = (struct wait_job *)arg;
    HANDLE h = OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION,
                           FALSE, job->pid);
    if (!h) {
        /* Ya no existe: para el que espera, eso es "salió y no sabemos con qué". */
        emit("%ld ok -1", job->id);
        free(job);
        return 0;
    }
    WaitForSingleObject(h, INFINITE);
    DWORD code = 0;
    GetExitCodeProcess(h, &code);
    CloseHandle(h);
    emit("%ld ok %ld", job->id, (long)code);
    free(job);
    return 0;
}

static void cmd_list(long id)
{
    HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    if (snap == INVALID_HANDLE_VALUE) { emit_winerr(id, "Toolhelp falló"); return; }
    /* Se arma la línea entera antes de escribirla: un hilo de wait podría
       colarse en medio y partir la respuesta. */
    size_t cap = 1 << 16, len = 0;
    char *buf = (char *)malloc(cap);
    if (!buf) { CloseHandle(snap); emit_err(id, "sin memoria"); return; }
    len += (size_t)snprintf(buf, cap, "%ld ok", id);
    PROCESSENTRY32W e; e.dwSize = sizeof e;
    if (Process32FirstW(snap, &e)) {
        do {
            char *name = wide_to_utf8(e.szExeFile);
            if (!name) continue;
            char *n64 = b64_encode((const unsigned char *)name, strlen(name));
            free(name);
            if (!n64) continue;
            size_t need = strlen(n64) + 32;
            if (len + need >= cap) {
                cap *= 2;
                char *bigger = (char *)realloc(buf, cap);
                if (!bigger) { free(n64); break; }
                buf = bigger;
            }
            len += (size_t)snprintf(buf + len, cap - len, " %lu,%lu,%s",
                                    (unsigned long)e.th32ProcessID,
                                    (unsigned long)e.th32ParentProcessID, n64);
            free(n64);
        } while (Process32NextW(snap, &e));
    }
    CloseHandle(snap);
    emit("%s", buf);
    free(buf);
}

/* --- bucle de órdenes ---------------------------------------------------- */

static char *next_token(char **cursor)
{
    char *s = *cursor;
    while (*s == ' ') s++;
    if (!*s) { *cursor = s; return NULL; }
    char *start = s;
    while (*s && *s != ' ') s++;
    if (*s) { *s = '\0'; s++; }
    *cursor = s;
    return start;
}

static char *decode_arg(const char *token)
{
    size_t len = 0;
    unsigned char *raw = b64_decode(token ? token : "", &len);
    return (char *)raw;   /* siempre termina en NUL */
}

int main(void)
{
    InitializeCriticalSection(&g_out_lock);
    setvbuf(stdout, NULL, _IONBF, 0);

    char *line = (char *)malloc(LINE_MAX_BYTES);
    if (!line) return 1;
    emit_log("[inject] ayudante listo dentro del prefijo");

    while (fgets(line, LINE_MAX_BYTES, stdin)) {
        char *nl = strpbrk(line, "\r\n");
        if (nl) *nl = '\0';
        if (!line[0]) continue;

        char *cursor = line;
        char *id_tok = next_token(&cursor);
        char *cmd = next_token(&cursor);
        if (!id_tok || !cmd) continue;
        long id = strtol(id_tok, NULL, 10);

        if (strcmp(cmd, "ping") == 0) {
            emit("%ld ok %lu", id, (unsigned long)GetCurrentProcessId());
        } else if (strcmp(cmd, "list") == 0) {
            cmd_list(id);
        } else if (strcmp(cmd, "spawn") == 0) {
            char *exe8 = decode_arg(next_token(&cursor));
            char *ticket = decode_arg(next_token(&cursor));
            char *auth = decode_arg(next_token(&cursor));
            char *parent8 = decode_arg(next_token(&cursor));
            char *wait_tok = next_token(&cursor);
            DWORD wait_ms = wait_tok ? (DWORD)strtoul(wait_tok, NULL, 10) : 30000;
            if (!exe8 || !ticket || !auth || !parent8) {
                emit_err(id, "spawn: faltan argumentos");
            } else {
                wchar_t *exe = utf8_to_wide((const unsigned char *)exe8, strlen(exe8));
                wchar_t *parent = utf8_to_wide((const unsigned char *)parent8, strlen(parent8));
                if (exe && parent) cmd_spawn(id, exe, ticket, auth, parent, wait_ms);
                else emit_err(id, "spawn: no hay memoria");
                free(exe); free(parent);
            }
            if (ticket) SecureZeroMemory(ticket, strlen(ticket));
            free(exe8); free(ticket); free(auth); free(parent8);
        } else if (strcmp(cmd, "wait") == 0) {
            char *pid_tok = next_token(&cursor);
            struct wait_job *job = (struct wait_job *)malloc(sizeof *job);
            if (!job) { emit_err(id, "sin memoria"); continue; }
            job->id = id;
            job->pid = pid_tok ? (DWORD)strtoul(pid_tok, NULL, 10) : 0;
            HANDLE th = CreateThread(NULL, 0, wait_thread, job, 0, NULL);
            if (th) CloseHandle(th);
            else { emit_err(id, "no se pudo crear el hilo de espera"); free(job); }
        } else if (strcmp(cmd, "kill") == 0) {
            char *pid_tok = next_token(&cursor);
            DWORD pid = pid_tok ? (DWORD)strtoul(pid_tok, NULL, 10) : 0;
            HANDLE h = OpenProcess(PROCESS_TERMINATE, FALSE, pid);
            if (!h) emit_winerr(id, "OpenProcess falló");
            else {
                BOOL ok = TerminateProcess(h, 0);
                CloseHandle(h);
                if (ok) emit("%ld ok", id);
                else emit_winerr(id, "TerminateProcess falló");
            }
        } else if (strcmp(cmd, "quit") == 0) {
            emit("%ld ok", id);
            break;
        } else {
            emit_err(id, "orden desconocida");
        }
    }
    free(line);
    DeleteCriticalSection(&g_out_lock);
    return 0;
}
