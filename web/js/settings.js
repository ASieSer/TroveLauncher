/* Trove Accounts Hub — the settings drawer and the install chips in the top bar. */

(function () {
    'use strict';

    const App = window.App;
    const $ = App.$;
    const el = App.el;

    // --- install chips ----------------------------------------------------

    /** One install chip: a dim `LIVE` label plus the path in the accent colour.
     *  They are separate nodes because they are two different things, and
     *  because the left-hand ellipsis must only apply to the path. */
    function fillInstallChip(chip, kind, path) {
        chip.innerHTML = '';
        if (!path) {
            const empty = el('span', 'chip-empty');
            empty.textContent = `Set ${kind} install`;
            chip.appendChild(empty);
            return;
        }
        const label = el('span', 'chip-label');
        label.textContent = kind;
        const text = el('span', 'chip-path');
        text.textContent = path;
        chip.append(label, text);
        chip.title = `${kind} installation — ${path}`;
    }

    App.renderInstallChips = function () {
        fillInstallChip($('install-live'), 'Live', App.state.game_path || '');
        const needsPts = App.state.accounts.some((a) => a.region === 'PTS')
            || !!App.state.pts_game_path;
        const chip = $('install-pts');
        chip.classList.toggle('hidden', !needsPts);
        fillInstallChip(chip, 'PTS', App.state.pts_game_path || '');
    };

    App.openInstallMenu = function (anchor, kind) {
        const menu = $('install-menu');
        menu.innerHTML = '';
        menu.style.minWidth = '270px';
        const wanted = kind === 'pts' ? 'pts' : 'live';
        const current = kind === 'pts' ? App.state.pts_game_path : App.state.game_path;
        const matching = (App.state.installs || []).filter(
            (g) => g.kind === wanted || g.source === 'custom');

        if (!matching.length) {
            menu.appendChild(el('div', 'popover-item',
                                '<span>No installation detected</span>'));
        }
        for (const game of matching) {
            const item = el('button',
                'popover-item' + (game.path === current ? ' active' : ''));
            const title = el('span'); title.textContent = game.name;
            const path = el('small'); path.textContent = game.path;
            item.append(title, path);
            item.addEventListener('click', async () => {
                App.closePopover();
                await App.call('set_install', game.path, wanted);
                App.refresh();
            });
            menu.appendChild(item);
        }
        menu.appendChild(el('div', 'popover-sep'));
        const browse = el('button', 'popover-item', '<span>Browse for folder…</span>');
        browse.addEventListener('click', async () => {
            App.closePopover();
            const result = await App.call('browse_for_install', wanted);
            if (result && !result.cancelled) App.refresh();
        });
        menu.appendChild(browse);

        menu.classList.remove('hidden');
        const box = anchor.getBoundingClientRect();
        menu.style.left = Math.max(8,
            Math.min(box.left, window.innerWidth - menu.offsetWidth - 8)) + 'px';
        menu.style.top = Math.max(8, box.top - menu.offsetHeight - 6) + 'px';
    };

    // --- the drawer -------------------------------------------------------

    App.openDrawer = function () {
        $('drawer').classList.remove('hidden');
        $('drawer-backdrop').classList.remove('hidden');
        App.renderDrawer();
    };

    App.closeDrawer = function () {
        $('drawer').classList.add('hidden');
        $('drawer-backdrop').classList.add('hidden');
    };

    function renderFolders() {
        const box = $('folder-list');
        if (!box) return;
        box.innerHTML = '';
        for (const folder of (App.state.folders || [])) {
            const row = el('div', 'folder-row');
            const label = el('span', 'folder-label');
            label.textContent = folder.label;
            const path = el('span', 'folder-path');
            path.textContent = folder.path;
            path.title = folder.path;
            const open = el('button', 'act', App.ICONS.folder);
            open.title = 'Open in file manager';
            open.addEventListener('click', () => App.call('open_folder', folder.kind));
            const text = el('div', 'folder-text');
            text.append(label, path);
            row.append(text, open);
            box.appendChild(row);
        }
    }

    /** What changes with the platform: the Wine settings only exist where they
     *  are needed, and the note about where passwords end up depends on whether
     *  there is a secret store behind it. */
    function renderPlatform() {
        const host = App.state.host || {};
        const wine = host.kind === 'wine';
        $('wine-section').classList.toggle('hidden', !wine);
        if (wine) {
            const status = $('wine-status');
            status.textContent = host.ready
                ? 'Launching with ' + (host.binary || 'wine') + ' in ' + (host.prefix || '?')
                : host.detail;
            status.classList.toggle('bad', !host.ready);
            // A warning is not a blocker: the game can still be launched, but
            // with this runner the anti-cheat will not start. See
            // WineHost.status.
            const warning = $('wine-warning');
            warning.textContent = host.warning || '';
            warning.classList.toggle('hidden', !host.warning);
            $('wine-binary').value = App.state.wine_binary || '';
            $('wine-prefix').value = App.state.wine_prefix || '';
            renderWineRunners(host);
        }

        const vault = App.state.vault || {};
        const note = $('vault-note');
        if (vault.available) {
            note.innerHTML = 'Accounts themselves are always stored. This only covers the '
                + '<em>password</em>, kept by ' + (vault.backend === 'DPAPI'
                    ? 'Windows DPAPI, so only this Windows user on this machine can read it'
                    : 'your desktop keyring (' + vault.backend + ')')
                + '. Without it you must retype it once the Trion session expires (~48h), and '
                + 'auto-relog cannot work at all — a background relaunch has no way to ask you.';
            note.classList.remove('bad');
        } else {
            note.textContent = 'There is nowhere safe to keep secrets on this machine ('
                + (vault.detail || 'no secret store') + '), so passwords will not be '
                + 'remembered and your session will have to be signed in again on every '
                + 'start. Nothing is ever written unencrypted.';
            note.classList.add('bad');
        }
    }

    /** The Proton builds installed here, so one can be picked without typing a
     *  path. */
    function renderWineRunners(host) {
        const box = $('wine-runners');
        box.innerHTML = '';
        const runners = host.runners || [];
        if (!runners.length) return;
        const title = el('p', 'note');
        title.textContent = 'Proton found on this machine:';
        box.appendChild(title);
        for (const runner of runners) {
            const row = el('button', 'runner-row'
                + (runner.wine === host.binary ? ' active' : ''));
            const name = el('span'); name.textContent = runner.name;
            const path = el('small'); path.textContent = runner.wine;
            row.append(name, path);
            row.addEventListener('click', async () => {
                // Clearing the field goes back to picking automatically;
                // pressing here is the opposite: pin THIS runner and keep it.
                App.state.wine_binary = runner.wine;
                $('wine-binary').value = runner.wine;
                await App.call('save_prefs', { wine_binary: runner.wine });
                App.refresh();
            });
            box.appendChild(row);
        }
    }

    App.renderDrawer = function () {
        const versions = App.state.versions || {};
        $('version-live').textContent = versions['live-us'] || 'not synced';
        $('version-pts').textContent = versions['pts'] || 'not synced';
        $('maint-pts').classList.toggle('hidden', !App.state.pts_game_path);
        $('opt-update-first').checked = !!App.state.update_first;
        $('opt-remember-password').checked = !!App.state.remember_password;
        $('opt-reparent').checked = !!App.state.reparent_glyph;
        renderFolders();
        renderPlatform();
        $('theme-club').value = App.clubOf(App.state.theme);
        const font = (App.state.theme || {}).font || 'system';
        for (const b of document.querySelectorAll('[data-font]')) {
            b.classList.toggle('active', b.dataset.font === font);
        }
        $('opt-stars').checked = (App.state.theme || {}).stars !== false;
        const tint = (App.state.theme || {}).tint;
        $('theme-tint').value = String(typeof tint === 'number' ? tint : 0.45);
        $('tint-value').textContent = Math.round($('theme-tint').value * 100) + '%';
        App.renderThemeControls();
    };
})();
