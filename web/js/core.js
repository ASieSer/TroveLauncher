/* Trove Accounts Hub - shared ground floor: bridge, state and DOM helpers.
 *
 * JS -> Python: window.pywebview.api.<method>(...) returns a promise. Every
 * reply carries {ok: bool} and, when ok is false, a readable "error".
 *
 * Python -> JS: the backend injects calls to window.__launcherEvent with
 * {op, stage, ...} frames. See events.js.
 *
 * The order on screen IS the order of App.state.accounts and .groups: dragging
 * means rebuilding those lists and sending them back.
 *
 * Everything shared between modules hangs off `window.App`. The files are
 * plain scripts, not ES modules, because the page is loaded over file:// and
 * Chromium refuses module scripts from that origin.
 */

window.App = window.App || {};

(function () {
    'use strict';

    const App = window.App;

    App.APP_NAME = 'Trove Accounts Hub';

    // --- shared state -----------------------------------------------------

    App.state = { groups: [], accounts: [], installs: [], versions: {} };
    App.hideEmails = true;
    App.filterText = '';
    App.drag = null;            // {kind: 'account'|'group', id}
    App.pendingDrop = null;     // last target worked out during a drag
    App.modalCleanup = null;
    App.expanded = {};          // groups with their "+N more" unfolded
    App.looseCollapsed = false; // is the "Ungrouped" section folded?

    // A group shows at most this many cards before folding the rest away, so a
    // bucket with 20 alts cannot push everything else off the screen.
    App.GROUP_PREVIEW = 12;

    // --- helpers ----------------------------------------------------------

    const $ = (id) => document.getElementById(id);
    App.$ = $;

    App.api = () => (window.pywebview && window.pywebview.api) || null;

    App.el = function (tag, className, html) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (html != null) node.innerHTML = html;
        return node;
    };

    /** An account's action button: icon, no text. The title is the only clue,
     *  so it must say what the button does, not what it is called. */
    App.actionButton = function (icon, title, className, onClick, opts) {
        const b = App.el('button', 'act ' + (className || ''), App.ICONS[icon]);
        b.dataset.icon = icon;
        b.title = title;
        b.setAttribute('aria-label', title);
        if (opts && opts.disabled) b.disabled = true;
        if (onClick) b.addEventListener('click', onClick);
        return b;
    };

    App.shownEmail = function (account) {
        return App.hideEmails ? account.masked : account.email;
    };

    // --- the status line --------------------------------------------------

    let msgTimer = null;

    /** Global message, in the bottom bar. Anything about ONE account shows on
     *  its card (badge + tooltip) instead of here.
     *
     *  A result clears itself after 7 seconds; the text of something STILL
     *  RUNNING does not (`sticky`), because whoever lit it turns it off - else
     *  a slow launch leaves the bar spinning without saying what for. */
    App.notice = function (text, kind, sticky) {
        const label = $('status-msg');
        label.textContent = text || '';
        label.className = 'msg ' + (kind || '');
        clearTimeout(msgTimer);
        if (text && !sticky) {
            msgTimer = setTimeout(() => {
                // If something is still running meanwhile, the space is its
                // own: an empty bar says nothing.
                const pending = App.pendingActivity();
                if (pending) { App.notice(pending, '', true); return; }
                label.textContent = '';
                label.className = 'msg';
            }, 7000);
        }
    };

    App.clearNotice = function () { App.notice('', ''); };

    /** A notice that shows up, waits, and goes away on its own.
     *
     *  Not the status bar: that one says what is happening right now and gets
     *  overwritten by whatever happens next. This is for something that just
     *  happened and is worth reading once - and it can hold two lines, which
     *  the bar cannot.
     *
     *  `html` is built by the caller, so anything coming from a file name has
     *  to be escaped there.
     */
    App.toast = function (html, kind, seconds, act) {
        const box = $('toasts');
        if (!box) return;
        const card = App.el('div', 'toast ' + (kind || ''), html);
        if (act) card.classList.add('clickable');
        const take = () => {
            card.classList.add('going');
            setTimeout(() => card.remove(), 260);
        };
        // Clicking always takes it away; `act` is what it does on the way out,
        // so a toast that reports something can also be the way to go and see
        // it, without becoming a thing you have to dismiss carefully.
        card.addEventListener('click', () => { if (act) act(); take(); });
        box.appendChild(card);
        setTimeout(take, (seconds || 8) * 1000);
    };

    /** Text going into a toast's HTML. */
    App.escape = function (text) {
        return String(text).replace(/[&<>"]/g,
            (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
    };

    // The log keeps only the last stretch. Appending forever grows a string
    // that nothing ever trims, and a launcher left open all day with several
    // accounts running produces a lot of lines nobody is going to read.
    const LOG_MAX_LINES = 500;
    const logLines = [];

    /** How many lines are being held, next to the button that empties them.
     *  Also says the ceiling once it has been reached, so a log that stops
     *  growing does not look stuck. */
    function sayCount() {
        const count = $('log-count');
        if (!count) return;
        count.textContent = logLines.length
            ? `${logLines.length} LINE${logLines.length === 1 ? '' : 'S'}`
              + (logLines.length >= LOG_MAX_LINES ? ' · OLDEST DROPPED' : '')
            : 'EMPTY';
    }

    App.clearLog = function () {
        logLines.length = 0;
        const log = $('log');
        if (log) log.textContent = '';
        sayCount();
    };

    App.logLine = function (text) {
        logLines.push(String(text));
        if (logLines.length > LOG_MAX_LINES) logLines.shift();

        const log = $('log');
        // Only follow the tail if that is where the user already was. Scrolling
        // up to read something and being yanked back down by the next line is
        // worse than missing it.
        const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 24;
        log.textContent = logLines.join('\n');
        sayCount();
        if (atBottom) log.scrollTop = log.scrollHeight;
    };

    // --- the bridge -------------------------------------------------------

    /** One bridge call. `report` decides how a failure is told: the bottom bar
     *  for the running commentary, a dialog when the user has to read it. */
    async function invoke(name, args, report) {
        const bridge = App.api();
        if (!bridge) {
            await report('The backend bridge is not ready yet.');
            return null;
        }
        try {
            const result = await bridge[name](...args);
            if (result && result.ok === false) {
                await report(result.error || 'Unknown error.');
                return null;
            }
            return result;
        } catch (err) {
            await report(String(err));
            return null;
        }
    }

    App.call = function (name, ...args) {
        return invoke(name, args, (text) => App.notice(text, 'error'));
    };

    /** Like App.call, but a failure stops the user with a dialog they have to
     *  acknowledge, and only resolves once they have.
     *
     *  For what is asked for explicitly and can plainly not be done - adding an
     *  account that is already in the list. The bar is for what happens while
     *  you watch; it fades after seven seconds, and a form left open on top of
     *  it hides the answer to the question you just asked. */
    App.callLoud = function (title, name, ...args) {
        return invoke(name, args, (text) => App.alert(text, title));
    };

    // --- filter -----------------------------------------------------------

    App.matches = function (account) {
        if (!App.filterText) return true;
        const needle = App.filterText.toLowerCase();
        return (account.label || '').toLowerCase().includes(needle)
            || (account.email || '').toLowerCase().includes(needle)
            || (account.region || '').toLowerCase().includes(needle)
            || (App.STATUS_TEXT[account.status] || '').toLowerCase().includes(needle);
    };

    App.accountsOf = function (groupId) {
        return App.state.accounts.filter(
            (a) => (a.group || null) === groupId && App.matches(a));
    };

    // --- whole-window state -----------------------------------------------

    /** Accounts the user has pressed stop on, until the work lets go of them.
     *
     *  The thread doing the launching answers at its next step, which can be a
     *  couple of seconds or, mid-update, a file. Without this the card went on
     *  reading "Logging in" the whole time and the press looked ignored - which
     *  is how a stop that does work gets reported as one that does not. */
    App.stopping = new Set();

    App.applyState = function (next) {
        App.state = next;
        for (const account of App.state.accounts || []) {
            const busy = account.status === 'launching'
                      || account.status === 'checking';
            if (!busy) App.stopping.delete(account.email);
            else if (App.stopping.has(account.email)) account.status = 'stopping';
        }
        App.state.theme = App.applyTheme(next.theme);
        // Only the dim and the blur: the image itself is fetched once, by
        // loadWallpaper, and nothing here can have changed it.
        App.applyWallpaper(next.wallpaper);
        App.modsCompact = !!(next.mods && next.mods.compact);
        if (App.sayCompact) App.sayCompact();
        App.setRail(!!(next.sidebar && next.sidebar.expanded));
        App.hideEmails = !!next.hide_emails;
        App.paintEyeButton();
        App.render();
        App.renderInstallChips();
        App.stampVersion(next.version);
        if (App.pane === 'settings') App.renderDrawer();
        App.stampSweep();
    };

    /** The version in the status bar. It comes from the backend rather than
     *  being written into the HTML, so it always matches what was built. */
    App.stampVersion = function (version) {
        const box = $('app-version');
        if (box) box.textContent = version ? 'v' + version : '';
    };

    App.stampSweep = function () {
        const now = new Date();
        const pad = (n) => String(n).padStart(2, '0');
        $('sweep').textContent =
            `Last sweep ${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
    };

    App.refresh = async function () {
        const result = await App.call('get_state');
        if (result && result.state) App.applyState(result.state);
    };

    /** Crossed-out eye = they are hidden right now; open eye = they are shown. */
    App.paintEyeButton = function () {
        const b = $('toggle-emails');
        b.innerHTML = App.hideEmails ? App.ICONS.eyeOff : App.ICONS.eye;
        b.title = App.hideEmails ? 'Show emails' : 'Hide emails';
    };
})();
