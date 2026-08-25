/* Trove Accounts Hub — shared ground floor: bridge, state and DOM helpers.
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
     *  RUNNING does not (`sticky`), because whoever lit it turns it off — else
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

    // The log keeps only the last stretch. Appending forever grows a string
    // that nothing ever trims, and a launcher left open all day with several
    // accounts relogging produces a lot of lines nobody is going to read.
    const LOG_MAX_LINES = 500;
    const logLines = [];

    App.clearLog = function () {
        logLines.length = 0;
        const log = $('log');
        if (log) log.textContent = '';
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
        if (atBottom) log.scrollTop = log.scrollHeight;
    };

    // --- the bridge -------------------------------------------------------

    App.call = async function (name, ...args) {
        const bridge = App.api();
        if (!bridge) {
            App.notice('The backend bridge is not ready yet.', 'error');
            return null;
        }
        try {
            const result = await bridge[name](...args);
            if (result && result.ok === false) {
                App.notice(result.error || 'Unknown error.', 'error');
                return null;
            }
            return result;
        } catch (err) {
            App.notice(String(err), 'error');
            return null;
        }
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

    App.applyState = function (next) {
        App.state = next;
        App.state.theme = App.applyTheme(next.theme);
        App.hideEmails = !!next.hide_emails;
        App.paintEyeButton();
        App.render();
        App.renderInstallChips();
        App.stampVersion(next.version);
        if (!$('drawer').classList.contains('hidden')) App.renderDrawer();
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
