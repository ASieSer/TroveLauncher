/* Trove Accounts Hub — backend events, wiring and start-up.
 *
 * Loaded last: every other module has already registered what it offers on
 * window.App by the time this one runs.
 */

(function () {
    'use strict';

    const App = window.App;
    const $ = App.$;

    // --- backend events ---------------------------------------------------

    // What is running right now, keyed by account ('' = the operations that
    // belong to no account: check, update, repair). The bottom indicator is one
    // for the whole app, so it lights up with the first and goes out with the
    // last: bar and text together, which is what you expect on seeing them.
    const inflight = new Map();

    function startActivity(key, message, indeterminate) {
        inflight.set(key, message);
        $('progress').classList.remove('hidden');
        $('bar-fill').classList.toggle('indeterminate', indeterminate !== false);
        App.notice(message, '', true);
    }

    App.pendingActivity = function () {
        return inflight.size ? [...inflight.values()].pop() : '';
    };

    function endActivity(key) {
        inflight.delete(key);
        const pending = App.pendingActivity();
        if (pending) {
            // Still work to do: the bar stays and the text moves to the latest.
            App.notice(pending, '', true);
            return;
        }
        $('progress').classList.add('hidden');
        $('bar-fill').classList.remove('indeterminate');
        App.clearNotice();
    }

    function onEvent(payload) {
        if (!payload) return;

        if (payload.op === 'installs') {
            if (payload.installs) {
                App.state.installs = payload.installs;
                App.renderInstallChips();
            }
            return;
        }
        if (payload.op === 'running') {
            if (payload.message) App.logLine(payload.message);
            App.refresh();
            return;
        }
        if (payload.stage === 'log') { App.logLine(payload.message); return; }
        if (payload.stage === '2fa_required') {
            App.open2faModal(payload.email, payload.label || payload.email);
            return;
        }
        const key = payload.email || '';

        if (payload.stage === 'downloading') {
            const total = payload.total || 0;
            $('bar-fill').style.transform =
                'scaleX(' + (total ? payload.current / total : 0) + ')';
            startActivity(key,
                `${payload.current.toLocaleString()} / ${total.toLocaleString()} files`,
                false);
            return;
        }
        // 'settled' closes a launch even when no 'done' arrived (a cancelled
        // 2FA, for one): without it the bar would keep spinning.
        if (payload.stage === 'settled') { endActivity(key); App.refresh(); return; }

        if (payload.message) {
            App.logLine(payload.message);
            if (!payload.done) startActivity(key, payload.message);
        }
        if (payload.done) {
            endActivity(key);
            // Anything belonging to one account is already told by its card;
            // the text down here goes out with the bar instead of hanging on.
            if (!payload.email) {
                App.notice(
                    payload.message
                        || (payload.ok === false ? 'The operation failed.' : 'Done.'),
                    payload.ok === false ? 'error' : 'ok');
            }
            App.refresh();
        }
    }

    window.__launcherEvent = onEvent;

    // --- tooltip ----------------------------------------------------------

    function wireTooltip() {
        const tip = $('tooltip');
        document.addEventListener('mouseover', (e) => {
            const host = e.target.closest && e.target.closest('[data-tip]');
            if (!host || !host.dataset.tip) return;
            tip.textContent = host.dataset.tip;
            tip.className = 'tooltip ' + (host.dataset.tipKind || '');
            const box = host.getBoundingClientRect();
            tip.style.left = Math.max(8,
                Math.min(box.left, window.innerWidth - tip.offsetWidth - 8)) + 'px';
            const top = box.bottom + 6;
            tip.style.top = (top + tip.offsetHeight > window.innerHeight
                ? box.top - tip.offsetHeight - 6 : top) + 'px';
        });
        document.addEventListener('mouseout', (e) => {
            if (e.target.closest && e.target.closest('[data-tip]')) {
                tip.classList.add('hidden');
            }
        });
    }

    // --- wiring -----------------------------------------------------------

    function wire() {
        $('add-account').addEventListener('click', App.openAddAccountModal);
        $('add-group').addEventListener('click', () => App.openGroupModal(null));
        $('open-settings').addEventListener('click', App.openDrawer);
        $('close-drawer').addEventListener('click', App.closeDrawer);
        $('drawer-backdrop').addEventListener('click', App.closeDrawer);
        document.querySelector('[data-action="add-first"]')
            .addEventListener('click', App.openAddAccountModal);

        $('filter').addEventListener('input', () => {
            App.filterText = $('filter').value.trim();
            App.render();
        });

        $('toggle-emails').addEventListener('click', async () => {
            App.hideEmails = !App.hideEmails;
            App.paintEyeButton();
            App.render();
            await App.call('save_prefs', { hide_emails: App.hideEmails });
        });

        $('modal-cancel').addEventListener('click', () => App.closeModal(false));
        $('modal-backdrop').addEventListener('click', (e) => {
            if (e.target === $('modal-backdrop')) App.closeModal(false);
        });
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                if (!$('modal-backdrop').classList.contains('hidden')) App.closeModal(false);
                else if (!$('drawer').classList.contains('hidden')) App.closeDrawer();
                App.closePopover();
            }
            if (e.key === 'Enter' && !$('modal-backdrop').classList.contains('hidden')) {
                $('modal-confirm').click();
            }
            // Ctrl+F or "/" jumps to the filter.
            if ((e.ctrlKey && e.key === 'f') || (e.key === '/' && e.target.tagName !== 'INPUT')) {
                e.preventDefault();
                $('filter').focus();
            }
        });

        $('install-live').addEventListener('click', (e) => {
            e.stopPropagation(); App.openInstallMenu($('install-live'), 'live');
        });
        $('install-pts').addEventListener('click', (e) => {
            e.stopPropagation(); App.openInstallMenu($('install-pts'), 'pts');
        });
        document.addEventListener('click', App.closePopover);

        for (const b of document.querySelectorAll('[data-maint]')) {
            b.addEventListener('click', async () => {
                const action = b.dataset.maint;
                if (action === 'repair' && !window.confirm(
                    'Repair re-downloads every file in the manifest. '
                    + 'It can take a long time and use several GB.\n\nContinue?')) return;
                App.notice('Preparing…');
                const result = await App.call(action, b.dataset.target);
                if (result && result.started === false) {
                    App.notice(result.error || 'Another operation is already running.',
                               'error');
                }
            });
        }
        for (const b of document.querySelectorAll('[data-folder]')) {
            b.addEventListener('click', () => App.call('open_folder', b.dataset.folder));
        }
        $('clear-log').addEventListener('click', App.clearLog);

        $('rescan').addEventListener('click', async () => {
            const result = await App.call('rescan_installs');
            if (result) {
                App.state.installs = result.installs || [];
                App.renderInstallChips();
                App.notice(`${App.state.installs.length} installation(s) found.`, 'ok');
            }
        });

        for (const [id, key] of [['opt-update-first', 'update_first'],
                                 ['opt-remember-password', 'remember_password'],
                                 ['opt-reparent', 'reparent_glyph']]) {
            $(id).addEventListener('change', () => {
                App.state[key] = $(id).checked;
                App.call('save_prefs', { [key]: $(id).checked });
            });
        }

        document.addEventListener('dragover', (e) => e.preventDefault());
        document.addEventListener('drop', (e) => e.preventDefault());
        document.addEventListener('mouseup', App.disarmDrag);
        document.addEventListener('dragend', App.disarmDrag);
    }

    /** Fills in the SVG for the buttons the HTML marks with data-icon. */
    function paintIcons() {
        for (const node of document.querySelectorAll('[data-icon]')) {
            const name = node.dataset.icon;
            if (App.ICONS[name]) node.innerHTML = App.ICONS[name];
        }
        $('open-settings').innerHTML = App.ICONS.gear;
    }

    function wireAppearance() {
        $('opt-stars').addEventListener('change', () => {
            App.state.theme = { ...App.state.theme, stars: $('opt-stars').checked };
            App.applyTheme(App.state.theme);
            App.call('save_prefs', { theme: App.state.theme });
        });
        for (const b of document.querySelectorAll('[data-font]')) {
            b.addEventListener('click', () => {
                App.state.theme = { ...App.state.theme, font: b.dataset.font };
                App.applyTheme(App.state.theme);
                for (const other of document.querySelectorAll('[data-font]')) {
                    other.classList.toggle('active', other === b);
                }
                App.call('save_prefs', { theme: App.state.theme });
            });
        }
        $('theme-club').addEventListener('change', () => {
            App.state.theme = App.applyTheme(
                { ...App.state.theme, club: $('theme-club').value });
            App.renderThemeControls();
            App.call('save_prefs', { theme: App.state.theme });
        });
        $('theme-tint').addEventListener('input', () => {
            const value = parseFloat($('theme-tint').value);
            App.state.theme = { ...App.state.theme, tint: value };
            App.applyTheme(App.state.theme);
            $('tint-value').textContent = Math.round(value * 100) + '%';
        });
        $('theme-tint').addEventListener('change',
            () => App.call('save_prefs', { theme: App.state.theme }));
        for (const id of ['wine-binary', 'wine-prefix']) {
            // On leaving the field, not on every keystroke: changing the prefix
            // half-typed would point the helper at somewhere that is not there.
            $(id).addEventListener('change', () => {
                const key = id.replace('-', '_');
                App.state[key] = $(id).value.trim();
                App.call('save_prefs', { [key]: App.state[key] }).then(App.refresh);
            });
        }
    }

    function start() {
        wire();
        wireAppearance();
        wireTooltip();
        paintIcons();
        App.renderBrand(null);      // until the state arrives, the default mark
        App.makeStarfield();
        App.refresh();
    }

    if (window.pywebview && window.pywebview.api) start();
    else window.addEventListener('pywebviewready', start, { once: true });
})();
