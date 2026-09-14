/* Trove Accounts Hub - backend events, wiring and start-up.
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
        $('sidebar-pin').addEventListener('click', () => {
            const open = !document.body.classList.contains('rail-open');
            App.setRail(open);
            App.state.sidebar = { ...App.state.sidebar, expanded: open };
            App.call('save_prefs', { sidebar: App.state.sidebar });
        });

        for (const button of document.querySelectorAll('[data-pane]')) {
            button.addEventListener('click', () => App.showPane(button.dataset.pane));
        }

        $('mods-refresh').addEventListener('click', App.loadMods);
        $('mods-updates').addEventListener('click', App.checkModUpdates);
        $('mods-update-all').addEventListener('click', App.updateAllMods);
        $('mods-folder').addEventListener('click', () => App.call('open_mods_folder'));
        $('mod-filter').addEventListener('input',
            () => App.filterMods($('mod-filter').value));
        $('mods-compact').addEventListener('click', () => {
            const compact = !App.modsCompact;
            App.setModsCompact(compact);
            App.state.mods = { ...App.state.mods, compact: compact };
            App.call('save_prefs', { mods: App.state.mods });
        });
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
            // The message dialog is the top layer: while it is up both keys
            // belong to it, and nothing behind it may act on them - Escape
            // would otherwise close the form the message is about.
            if (App.alertOpen()) {
                // Escape always says no and Enter always says yes, whether or
                // not there is a Cancel button to press.
                if (e.key === 'Escape' || e.key === 'Enter') {
                    e.preventDefault();
                    if (App.alertDismiss) App.alertDismiss(e.key === 'Enter');
                }
                return;
            }
            if (e.key === 'Escape') {
                if (!$('modal-backdrop').classList.contains('hidden')) App.closeModal(false);
                else if (App.pane && App.pane !== 'accounts') App.showPane('accounts');
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
                if (action === 'repair' && !await App.confirm(
                    'Repair re-downloads every file in the manifest. '
                    + 'It can take a long time and use several GB.',
                    { title: 'Repair the installation?', confirmText: 'Repair' })) return;
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
                                 ['opt-remember-password', 'remember_password']]) {
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
        $('theme-club').addEventListener('change', async () => {
            App.state.theme = App.applyTheme(
                { ...App.state.theme, club: $('theme-club').value });
            // The club owns the mark while it is on, and the icon goes with it:
            // back to the cube in the club's colour, and back to the logo when
            // the theme comes off.
            await App.useLogoBitmap(App.logoImage);
            App.renderThemeControls();
            App.renderDrawer();
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

        // --- the mark in the top bar ---
        // callLoud, not call: the answer to picking a file that cannot be used
        // - too big, not an image - is a refusal to what you just asked for,
        // and the status bar it used to go to is behind this very panel.
        $('logo-pick').addEventListener('click', async () => {
            const result = await App.callLoud('Logo', 'browse_for_logo');
            if (!result || result.cancelled) return;
            App.state.logo = result.logo;
            App.logoImage = result.image;
            App.renderBrand(App.state.theme);
            await App.useLogoBitmap(App.logoImage);
            App.renderDrawer();
            keepThumbnail('logo', result);
        });
        $('logo-as-icon').addEventListener('change', async () => {
            App.state.logo = { ...App.state.logo, icon: $('logo-as-icon').checked };
            // Straight back to the cube, or to the logo, without a restart.
            await App.useLogoBitmap(App.logoImage);
            App.call('save_prefs', { logo: App.state.logo });
        });
        $('logo-clear').addEventListener('click', async () => {
            const result = await App.call('clear_logo');
            if (!result) return;
            App.state.logo = result.logo;
            App.logoImage = '';
            App.renderBrand(App.state.theme);
            await App.useLogoBitmap('');
            App.renderDrawer();
        });

        // --- background image ---
        // Deliberately not awaited by the picking above: the picture is already
        // on screen by then, and shrinking it is only for the row underneath.
        async function keepThumbnail(kind, result) {
            if (!result.recent || !result.image) return;
            const small = await App.thumbnail(result.image, 108);
            if (small) await App.call('save_recent_thumb', kind, result.recent, small);
            App.renderRecent(kind);
        }

        $('wallpaper-pick').addEventListener('click', async () => {
            const result = await App.callLoud('Background', 'browse_for_wallpaper');
            if (!result || result.cancelled) return;
            App.state.wallpaper = result.wallpaper;
            App.applyWallpaper(result.wallpaper, result.image);
            App.renderDrawer();
            keepThumbnail('wallpaper', result);
        });
        $('wallpaper-clear').addEventListener('click', async () => {
            const result = await App.call('clear_wallpaper');
            if (!result) return;
            App.state.wallpaper = result.wallpaper;
            App.applyWallpaper(result.wallpaper, '');
            App.renderDrawer();
        });
        // While dragging, only repaint; the save waits for the slider to be let
        // go, so one adjustment is one write and not forty.
        for (const [id, key, label] of [['wallpaper-dim', 'dim', 'wallpaper-dim-value'],
                                        ['wallpaper-blur', 'blur', 'wallpaper-blur-value']]) {
            $(id).addEventListener('input', () => {
                const value = parseFloat($(id).value);
                App.state.wallpaper = { ...App.state.wallpaper, [key]: value };
                App.applyWallpaper(App.state.wallpaper);
                $(label).textContent = key === 'dim'
                    ? Math.round(value * 100) + '%' : value + 'px';
            });
            $(id).addEventListener('change',
                () => App.call('save_prefs', { wallpaper: App.state.wallpaper }));
        }
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
        // The image after the state, which is what says whether there is one
        // and how it should look.
        App.showPane('accounts');
        App.refresh().then(App.loadWallpaper).then(App.loadLogo);
    }

    if (window.pywebview && window.pywebview.api) start();
    else window.addEventListener('pywebviewready', start, { once: true });
})();
