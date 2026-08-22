/*
 * troveinject.exe - the Windows arm of Trove Accounts Hub.
 *
 * On Windows the application hands over the ticket itself (see
 * core/inject.py). On Linux it cannot: the game collects the ticket from a
 * Windows file mapping by doing OpenProcess(launcher pid) + DuplicateHandle,
 * and those are Windows kernel objects a native Linux process can neither
 * create nor share. This program is that launcher, running inside the Wine
 * prefix alongside the game.
 *
 * It does exactly what inject.py does, and for the same reasons:
 *
 *   1. builds the RC4-encrypted "RIFT" blob from the ticket,
 *   2. leaves it in an inheritable, pagefile-backed file mapping, with an
 *      auto-reset event beside it,
 *   3. launches the game - or the anti-cheat loader, if present - passing it
 *      both handles and ITS OWN pid in -k "<map>:<evt>:<pid>",
 *   4. waits on the event, which the game signals once it has read and
 *      decrypted the ticket,
 *   5. and does NOT close the handles: the game duplicates them from us for
 *      the whole session.
 *
 * Because of point 5 this is a server and not a one-shot command: while a
 * session is open, this process has to stay alive. The Linux side starts it
 * (core/winehost.py) and talks to it over stdin/stdout in lines of text:
 *
 *   -> <id> spawn <exe64> <ticket64> <auth64> <parent64> <wait_ms> [pid,pid...]
 *   <- <id> ok <pid> <consumed 0|1> <via_loader 0|1>
 *   -> <id> wait <pid>            <- <id> ok <exit code>          (once it exits)
 *   -> <id> kill <pid>            <- <id> ok
 *   -> <id> list                  <- <id> ok <pid>,<ppid>,<name64> ...
 *   -> <id> ping                  <- <id> ok <the helper's own pid>
 *   <- log <text64>               (at any time)
 *   <- <id> err <message64>
 *
 * The arguments travel base64-encoded because the ticket is multi-line XML and
 * the paths contain spaces; the protocol stays at one line per message, which
 * is what makes it trivial to read from the other side.
 *
 * Cross-compiled from Linux, with no dependencies:
 *   x86_64-w64-mingw32-gcc -O2 -municode -o troveinject.exe troveinject.c
 */

#include <windows.h>
#include <tlhelp32.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define LINE_MAX_BYTES (1 << 20)   /* the base64 ticket runs to tens of KB */

static CRITICAL_SECTION g_out_lock;   /* stdout is shared by the loop and the wait threads */

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

/* Returns malloc'd bytes and writes the length into *out_len. */
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

/* --- the RIFT blob (identical to rift.build_rift_buffer) ----------------- */

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
 * The ticket is trimmed exactly as in Python: from the first line starting
 * with "Signature:" or "<?xml", with the \r stripped and no trailing spaces.
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
    /* Only counts at the start of a line, like the original's split('\n'). */
    if (start != clean && start[-1] != '\n') start = clean;

    size_t slen = strlen(start);
    while (slen && (start[slen - 1] == '\n' || start[slen - 1] == ' '
                    || start[slen - 1] == '\t')) slen--;

    size_t body = 4 + slen + 1;                 /* "RIFT" + ticket + NUL */
    unsigned char *ct = (unsigned char *)malloc(body);
    if (!ct) { free(clean); return NULL; }
    memcpy(ct, "\x54\x46\x49\x52", 4);          /* 'TFIR' == "RIFT" read little-endian */
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
        /* No provider available: something unpredictable within this session. */
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

/* Pids that CANNOT be the session we have just launched: those already
   running before, plus those the application is already watching. Without
   this, two accounts starting at once can claim each other's session. */
struct pidset { DWORD *v; size_t n, cap; };

static void pidset_add(struct pidset *set, DWORD pid)
{
    if (set->n == set->cap) {
        size_t cap = set->cap ? set->cap * 2 : 16;
        DWORD *v = (DWORD *)realloc(set->v, cap * sizeof(DWORD));
        if (!v) return;
        set->v = v; set->cap = cap;
    }
    set->v[set->n++] = pid;
}

static int pidset_has(const struct pidset *set, DWORD pid)
{
    for (size_t i = 0; i < set->n; i++) if (set->v[i] == pid) return 1;
    return 0;
}

/* Adds to `set` the pids already running under that executable name. */
static void pidset_add_running(struct pidset *set, const wchar_t *exe_name)
{
    HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    if (snap == INVALID_HANDLE_VALUE) return;
    PROCESSENTRY32W e; e.dwSize = sizeof e;
    if (Process32FirstW(snap, &e)) {
        do {
            if (_wcsicmp(e.szExeFile, exe_name) == 0) pidset_add(set, e.th32ProcessID);
        } while (Process32NextW(snap, &e));
    }
    CloseHandle(snap);
}

/*
 * Translates the pid from CreateProcess into the game's REAL pid.
 *
 * With the anti-cheat we do not launch the game but the loader, which starts
 * Trove and exits: watching the loader's pid would make it look as though the
 * session closed after a few seconds. The loader's direct child is looked for
 * first; if the loader has already died and the parent link is lost, any game
 * process that was not there before will do. Same rule as
 * inject.resolve_game_pid.
 */
static DWORD resolve_game_pid(DWORD spawn_pid, const wchar_t *exe_name,
                              DWORD timeout_ms, const struct pidset *exclude)
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
                    if (e.th32ProcessID == spawn_pid) { self = e.th32ProcessID; continue; }
                    if (pidset_has(exclude, e.th32ProcessID)) continue;
                    if (e.th32ParentProcessID == spawn_pid) child = e.th32ProcessID;
                    else if (!any) any = e.th32ProcessID;
                } while (Process32NextW(snap, &e));
            }
            CloseHandle(snap);
            if (self) return self;         /* an installation with no loader */
            if (child) return child;
            if (any) return any;
        }
        Sleep(400);
    } while ((long)(deadline - GetTickCount()) > 0);
    return spawn_pid;   /* something has to be watched */
}

/* --- lanzar -------------------------------------------------------------- */

/*
 * The mapping's and the event's handles are NEVER closed: the game duplicates
 * them from this process for as long as the session lasts. It is the same
 * deliberate leak of two handles per launch that Glyph makes, and the reason
 * this helper stays alive until the application closes.
 */
static void cmd_spawn(long id, const wchar_t *exe, const char *ticket,
                      const char *auth, const wchar_t *parent, DWORD wait_ms,
                      const char *exclude_csv)
{
    /* Is the anti-cheat loader sitting next to the executable? */
    wchar_t dir[MAX_PATH * 2];
    wcsncpy(dir, exe, MAX_PATH * 2 - 1); dir[MAX_PATH * 2 - 1] = L'\0';
    wchar_t *slash = wcsrchr(dir, L'\\');
    wchar_t *game_name = slash ? slash + 1 : dir;
    wchar_t loader[MAX_PATH * 2];
    swprintf(loader, MAX_PATH * 2, L"%.*ls\\xldr_Trove_GL_loader_x64.exe",
             slash ? (int)(slash - dir) : 0, dir);
    BOOL via_loader = GetFileAttributesW(loader) != INVALID_FILE_ATTRIBUTES;
    const wchar_t *exe_to_run = via_loader ? loader : exe;

    /* Snapshot first: what was already running cannot be the session we are
       about to open. */
    struct pidset exclude = {NULL, 0, 0};
    for (const char *p = exclude_csv; p && *p; ) {
        pidset_add(&exclude, (DWORD)strtoul(p, NULL, 10));
        const char *comma = strchr(p, ',');
        p = comma ? comma + 1 : NULL;
    }
    pidset_add_running(&exclude, game_name);

    size_t blob_len = 0;
    unsigned char *blob = build_rift(ticket, &blob_len);
    if (!blob) { free(exclude.v); emit_err(id, "out of memory building the ticket blob"); return; }

    SECURITY_ATTRIBUTES sa = {sizeof sa, NULL, TRUE};
    HANDLE hmap = CreateFileMappingW(INVALID_HANDLE_VALUE, &sa, PAGE_READWRITE,
                                     0, (DWORD)blob_len, NULL);
    if (!hmap) { free(blob); free(exclude.v); emit_winerr(id, "CreateFileMapping failed"); return; }
    void *view = MapViewOfFile(hmap, FILE_MAP_WRITE, 0, 0, blob_len);
    if (!view) { CloseHandle(hmap); free(blob); free(exclude.v); emit_winerr(id, "MapViewOfFile failed"); return; }
    memcpy(view, blob, blob_len);
    UnmapViewOfFile(view);
    SecureZeroMemory(blob, blob_len);
    free(blob);

    HANDLE hevent = CreateEventW(&sa, FALSE, FALSE, NULL);
    if (!hevent) { CloseHandle(hmap); free(exclude.v); emit_winerr(id, "CreateEvent failed"); return; }

    /* Reparent: the loader as a child of Glyph, so the launch chain is
       that of a legitimate session (see PROC_THREAD_ATTRIBUTE_PARENT_PROCESS
       in inject.py). With no Glyph around it launches anyway, unparented. */
    HANDLE parent_handle = NULL;
    if (parent && parent[0]) {
        DWORD ppid = find_pid_by_name(parent);
        if (!ppid) {
            emit_log("[inject] reparent: the parent process is not running; "
                     "launching without reparent");
        } else {
            parent_handle = OpenProcess(PROCESS_CREATE_PROCESS, FALSE, ppid);
            if (!parent_handle)
                emit_log("[inject] reparent: OpenProcess failed; launching without reparent");
            else
                emit_log("[inject] reparent: the loader will hang off Glyph");
        }
    }

    SIZE_T attr_size = 0;
    InitializeProcThreadAttributeList(NULL, 1, 0, &attr_size);
    LPPROC_THREAD_ATTRIBUTE_LIST attr =
        (LPPROC_THREAD_ATTRIBUTE_LIST)malloc(attr_size);
    if (!attr || !InitializeProcThreadAttributeList(attr, 1, 0, &attr_size)) {
        if (parent_handle) CloseHandle(parent_handle);
        free(attr); free(exclude.v);
        emit_winerr(id, "InitializeProcThreadAttributeList failed");
        return;
    }
    /* Either the handle allow-list OR the parent, never both: with a
       declared parent the handles would have to be ITS OWN and Windows
       answers 87. */
    HANDLE handles[2] = {hmap, hevent};
    BOOL attr_ok = parent_handle
        ? UpdateProcThreadAttribute(attr, 0, PROC_THREAD_ATTRIBUTE_PARENT_PROCESS,
                                    &parent_handle, sizeof parent_handle, NULL, NULL)
        : UpdateProcThreadAttribute(attr, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
                                    handles, sizeof handles, NULL, NULL);
    if (!attr_ok) {
        DeleteProcThreadAttributeList(attr); free(attr); free(exclude.v);
        if (parent_handle) CloseHandle(parent_handle);
        emit_winerr(id, "UpdateProcThreadAttribute failed");
        return;
    }

    STARTUPINFOEXW si; ZeroMemory(&si, sizeof si);
    si.StartupInfo.cb = sizeof si;
    si.lpAttributeList = attr;
    PROCESS_INFORMATION pi; ZeroMemory(&pi, sizeof pi);

    wchar_t *auth_w = utf8_to_wide((const unsigned char *)auth, strlen(auth));
    wchar_t cmd[8192];
    if (via_loader)
        /* argv[0] = the loader's path, argv[1] = the game's name; the other
           way round the loader aborts with 1038 without starting anything. */
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

    emit_log(via_loader ? "[inject] launching through the anti-cheat loader"
                        : "[inject] launching the game directly");

    /* Inherit handles only on the unparented path, which is the one that
       leans on the allow-list; with a declared parent the ticket travels by
       DuplicateHandle. */
    BOOL ok = CreateProcessW(exe_to_run, cmd, NULL, NULL,
                             parent_handle ? FALSE : TRUE,
                             EXTENDED_STARTUPINFO_PRESENT, NULL, workdir,
                             &si.StartupInfo, &pi);
    SecureZeroMemory(cmd, sizeof cmd);
    DeleteProcThreadAttributeList(attr); free(attr);
    if (parent_handle) CloseHandle(parent_handle);
    if (!ok) {
        CloseHandle(hmap); CloseHandle(hevent); free(exclude.v);
        emit_winerr(id, "CreateProcess failed");
        return;
    }
    CloseHandle(pi.hThread);

    DWORD consumed = WaitForSingleObject(hevent, wait_ms) == WAIT_OBJECT_0;
    if (!consumed) {
        DWORD code = 0;
        GetExitCodeProcess(pi.hProcess, &code);
        emit_log(code == STILL_ACTIVE
                 ? "[inject] the game has not signalled yet; it may still recover"
                 : "[inject] the process exited before consuming the ticket");
    }
    CloseHandle(pi.hProcess);

    DWORD pid = resolve_game_pid(pi.dwProcessId, game_name, 30000, &exclude);
    free(exclude.v);
    emit("%ld ok %lu %d %d", id, (unsigned long)pid, consumed ? 1 : 0,
         via_loader ? 1 : 0);
    /* hmap and hevent stay open on purpose until the helper dies. */
}

/* --- waiting for the exit, without blocking the command loop ------------ */

struct wait_job { long id; DWORD pid; DWORD ms; };

static DWORD WINAPI wait_thread(LPVOID arg)
{
    struct wait_job *job = (struct wait_job *)arg;
    HANDLE h = OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION,
                           FALSE, job->pid);
    if (!h) {
        /* Gone already: to whoever is waiting, that reads as "it exited and we do
       not know with what". */
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

/* --- "is it fully up yet?" ----------------------------------------------
 *
 * WaitForInputIdle returns once the process has finished initialising and is
 * waiting for input. It enumerates and touches no window at all: it only
 * waits. It is what separates "the process exists" from "the game is up", and
 * it is needed so the next account is not launched on top of the previous one.
 */
static DWORD WINAPI ready_thread(LPVOID arg)
{
    struct wait_job *job = (struct wait_job *)arg;
    HANDLE h = OpenProcess(SYNCHRONIZE | PROCESS_QUERY_INFORMATION, FALSE, job->pid);
    if (!h) {
        emit("%ld ok -1", job->id);
        free(job);
        return 0;
    }
    DWORD res = WaitForInputIdle(h, job->ms);
    CloseHandle(h);
    emit("%ld ok %lu", job->id, (unsigned long)res);
    free(job);
    return 0;
}

static void cmd_list(long id)
{
    HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    if (snap == INVALID_HANDLE_VALUE) { emit_winerr(id, "Toolhelp failed"); return; }
    /* The whole line is assembled before being written: a wait thread could
       slip in halfway and split the reply. */
    size_t cap = 1 << 16, len = 0;
    char *buf = (char *)malloc(cap);
    if (!buf) { CloseHandle(snap); emit_err(id, "out of memory"); return; }
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

/* --- command loop -------------------------------------------------------- */

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
    return (char *)raw;   /* always NUL-terminated */
}

int main(void)
{
    InitializeCriticalSection(&g_out_lock);
    setvbuf(stdout, NULL, _IONBF, 0);

    char *line = (char *)malloc(LINE_MAX_BYTES);
    if (!line) return 1;
    emit_log("[inject] helper ready inside the prefix");

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
            char *exclude_csv = next_token(&cursor);
            if (!exe8 || !ticket || !auth || !parent8) {
                emit_err(id, "spawn: missing arguments");
            } else {
                wchar_t *exe = utf8_to_wide((const unsigned char *)exe8, strlen(exe8));
                wchar_t *parent = utf8_to_wide((const unsigned char *)parent8, strlen(parent8));
                if (exe && parent)
                    cmd_spawn(id, exe, ticket, auth, parent, wait_ms,
                              exclude_csv ? exclude_csv : "");
                else emit_err(id, "spawn: out of memory");
                free(exe); free(parent);
            }
            if (ticket) SecureZeroMemory(ticket, strlen(ticket));
            free(exe8); free(ticket); free(auth); free(parent8);
        } else if (strcmp(cmd, "wait") == 0 || strcmp(cmd, "ready") == 0) {
            char *pid_tok = next_token(&cursor);
            char *ms_tok = next_token(&cursor);
            struct wait_job *job = (struct wait_job *)malloc(sizeof *job);
            if (!job) { emit_err(id, "sin memoria"); continue; }
            job->id = id;
            job->pid = pid_tok ? (DWORD)strtoul(pid_tok, NULL, 10) : 0;
            job->ms = ms_tok ? (DWORD)strtoul(ms_tok, NULL, 10) : 120000;
            /* Both waits go on their own thread: the command loop has to keep
               serving while a session starts or runs. */
            LPTHREAD_START_ROUTINE fn = (strcmp(cmd, "wait") == 0)
                                        ? wait_thread : ready_thread;
            HANDLE th = CreateThread(NULL, 0, fn, job, 0, NULL);
            if (th) CloseHandle(th);
            else { emit_err(id, "could not create the wait thread"); free(job); }
        } else if (strcmp(cmd, "kill") == 0) {
            char *pid_tok = next_token(&cursor);
            DWORD pid = pid_tok ? (DWORD)strtoul(pid_tok, NULL, 10) : 0;
            HANDLE h = OpenProcess(PROCESS_TERMINATE, FALSE, pid);
            if (!h) emit_winerr(id, "OpenProcess failed");
            else {
                BOOL ok = TerminateProcess(h, 0);
                CloseHandle(h);
                if (ok) emit("%ld ok", id);
                else emit_winerr(id, "TerminateProcess failed");
            }
        } else if (strcmp(cmd, "quit") == 0) {
            emit("%ld ok", id);
            break;
        } else {
            emit_err(id, "unknown command");
        }
    }
    free(line);
    DeleteCriticalSection(&g_out_lock);
    return 0;
}
