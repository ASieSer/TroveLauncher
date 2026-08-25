/* Trove Accounts Hub — the board: groups, account cards and the region menu.
 *
 * Everything here paints from App.state and nothing else, so a repaint after
 * any change is always safe.
 */

(function () {
    'use strict';

    const App = window.App;
    const $ = App.$;
    const el = App.el;

    // --- counts -----------------------------------------------------------

    function renderCounts() {
        const total = App.state.accounts.length;
        const running = App.state.accounts.filter((a) => a.status === 'running').length;
        const shown = App.state.accounts.filter(App.matches).length;
        const bits = [`<b>${total}</b> accounts`];
        if (App.filterText) bits.push(`<b>${shown}</b> shown`);
        if (running) bits.push(`<span class="live">${running} running</span>`);
        $('counts').innerHTML = bits.join(' · ');
    }

    // --- the board --------------------------------------------------------

    App.render = function () {
        const rows = $('rows');
        rows.innerHTML = '';

        const anyAccounts = App.state.accounts.length > 0;
        $('empty-state').classList.toggle('hidden',
            anyAccounts || App.state.groups.length > 0);

        for (const group of App.state.groups) {
            const list = App.accountsOf(group.id);
            // With a filter on, a group with no matches adds nothing.
            if (App.filterText && !list.length) continue;
            // The group's colour frames the whole thing - header, cards and
            // buttons - rather than sitting in a dot nobody notices. The CSS
            // derives border and wash from this one custom property.
            const box = el('div', 'group' + (group.collapsed ? ' folded' : ''));
            box.style.setProperty('--group', group.color || 'var(--line-hi)');
            box.appendChild(groupHead(group, list));
            if (!group.collapsed) appendAccounts(box, list, group.id);
            App.wireGroupBox(box, group);
            rows.appendChild(box);
        }

        const loose = App.accountsOf(null);
        if (loose.length || (!App.filterText && App.state.groups.length)) {
            // Ungrouped gets the same frame so the board reads as one rhythm,
            // but in the neutral line colour: it is not a group and has no
            // colour of its own to wear.
            const box = el('div', 'group loose' + (App.looseCollapsed ? ' folded' : ''));
            box.appendChild(looseHead(loose));
            if (!App.looseCollapsed) appendAccounts(box, loose, null);
            rows.appendChild(box);
        } else if (!App.looseCollapsed) {
            appendAccounts(rows, loose, null);
        }

        // Somewhere to drag an account out of its group.
        if (!App.filterText) {
            const zone = el('div', 'dropzone', 'Drag here to ungroup');
            zone.addEventListener('dragover', (e) => {
                if (App.drag && App.drag.kind === 'account') {
                    e.preventDefault();
                    zone.classList.add('drop-into');
                }
            });
            zone.addEventListener('dragleave', () => zone.classList.remove('drop-into'));
            zone.addEventListener('drop', (e) => {
                e.preventDefault();
                zone.classList.remove('drop-into');
                App.dropAccount(null, null, 'end');
            });
            rows.appendChild(zone);
        }
        renderCounts();
    };

    function appendAccounts(rows, list, groupId) {
        const key = groupId || '__loose__';
        // The filter already trims the list, so nothing is folded away there.
        const cap = (App.filterText || App.expanded[key]) ? list.length : App.GROUP_PREVIEW;
        const visible = list.slice(0, cap);

        const grid = el('div', 'card-grid');
        for (const account of visible) grid.appendChild(accountCard(account));
        grid.appendChild(App.dropSlot(groupId));
        // The grid's leftover space is large; dropping there should put the
        // account at the end of this group instead of doing nothing.
        grid.addEventListener('dragover', (e) => {
            if (!App.drag || App.drag.kind !== 'account' || e.target.closest('.card')) return;
            e.preventDefault();
            App.pendingDrop = null;          // at the end of the group
            const moving = document.querySelector('.dragging');
            if (moving && moving.parentNode === grid && moving !== grid.lastElementChild) {
                grid.appendChild(moving);
            }
        });
        grid.addEventListener('drop', (e) => {
            if (!App.drag || App.drag.kind !== 'account' || e.target.closest('.card')) return;
            e.preventDefault();
            App.clearDropMarks();
            App.dropAccount(null, groupId, 'group');
        });
        rows.appendChild(grid);

        const hidden = list.length - visible.length;
        if (hidden > 0) {
            const label = groupId
                ? `+ ${hidden} more in ${(App.state.groups.find((g) => g.id === groupId) || {}).name || ''}`
                : `+ ${hidden} more`;
            const more = el('div', 'more-row', label);
            more.addEventListener('click', () => { App.expanded[key] = true; App.render(); });
            rows.appendChild(more);
        } else if (App.expanded[key] && list.length > App.GROUP_PREVIEW) {
            const less = el('div', 'more-row', 'Show less');
            less.addEventListener('click', () => { delete App.expanded[key]; App.render(); });
            rows.appendChild(less);
        }
    }

    function looseHead(list) {
        const head = el('div', 'group-head');
        // Groups carry a drag grip and this one does not; without a spacer of
        // the same width its caret would take that position and sit misaligned.
        const spacer = el('span', 'grip');
        spacer.style.visibility = 'hidden';
        const caret = el('span', 'group-caret' + (App.looseCollapsed ? '' : ' open'),
                         App.ICONS.caret);
        const name = el('span', 'group-name');
        name.textContent = 'Ungrouped';
        const count = el('span', 'group-count');
        count.textContent = list.length;

        head.append(spacer, caret, name, count);
        head.addEventListener('click', () => {
            App.looseCollapsed = !App.looseCollapsed;
            App.render();
        });
        return head;
    }

    function groupHead(group, list) {
        const head = el('div', 'group-head');
        head.dataset.groupId = group.id;

        const grip = el('span', 'grip', App.ICONS.grip);
        const caret = el('span', 'group-caret' + (group.collapsed ? '' : ' open'),
                         App.ICONS.caret);
        const name = el('span', 'group-name');
        name.textContent = group.name;
        const count = el('span', 'group-count');
        count.textContent = list.length;

        // Status is not summarised here: it lives on each account's card, which
        // is where it can be acted on. A counter in the header repeated the same
        // information without saying whose it was.
        const tools = el('div', 'group-tools');
        const launchable = list.filter((a) => a.status !== 'running'
            && a.status !== 'launching' && a.status !== 'checking');
        if (launchable.length > 1) {
            // The logo is a white PNG with alpha; the CSS uses it as a mask so
            // it takes the accent colour instead of staying fixed white.
            const all = el('button', 'launch-all', '<span class="launch-logo"></span>');
            all.title = `Launch all ${launchable.length} accounts in ${group.name}`;
            all.addEventListener('click', (e) => {
                e.stopPropagation();
                App.launchAll(launchable, group.name);
            });
            tools.appendChild(all);
        }
        const edit = el('button', 'icon-btn', App.ICONS.gear);
        edit.title = 'Edit group';
        edit.addEventListener('click', (e) => { e.stopPropagation(); App.openGroupModal(group); });
        const remove = el('button', 'icon-btn', App.ICONS.trash);
        remove.title = 'Delete group';
        remove.addEventListener('click', async (e) => {
            e.stopPropagation();
            if (!window.confirm(
                `Delete the group "${group.name}"? Its accounts move to Ungrouped.`)) return;
            await App.call('delete_group', group.id);
            App.refresh();
        });
        tools.append(edit, remove);

        head.append(grip, caret, name, count, tools);
        head.addEventListener('click', async () => {
            group.collapsed = !group.collapsed;
            App.render();
            await App.call('update_group', group.id, { collapsed: group.collapsed });
        });
        App.wireGroupDrag(head, group, grip);
        return head;
    }

    /** An account's action buttons. The last one stretches to fill the card's
     *  width; hence the `grow` class. */
    function buildActions(account, opts) {
        const grow = opts && opts.grow;
        const actions = el('div', 'actions');
        const busy = account.status === 'launching' || account.status === 'checking';

        actions.appendChild(App.actionButton('trash', 'Delete account', 'danger', async () => {
            if (!window.confirm(
                `Delete ${account.label}? Its saved session and password are removed.`)) return;
            await App.call('remove_account', account.email);
            App.refresh();
        }, { disabled: account.status === 'running' }));

        actions.appendChild(App.actionButton('gear', 'Edit account', '',
            () => App.openAccountModal(account)));

        actions.appendChild(App.actionButton('login', 'Test login without launching', '',
            () => App.testLogin(account), { disabled: busy }));

        // Auto-relog: accent-coloured when on, plain when off. It is saved on
        // the account and, if that account has a game open, applied to that
        // running instance too.
        actions.appendChild(App.actionButton('relog',
            account.auto_relog
                ? 'Auto-relog is ON — signs back in whenever the game closes '
                  + '(crash, or kicked for being idle)'
                : 'Auto-relog is OFF',
            account.auto_relog ? 'accent' : '',
            async () => {
                account.auto_relog = !account.auto_relog;
                App.render();
                await App.call('set_auto_relog', account.email, account.auto_relog);
            }));

        if (account.status === 'running') {
            // There is no "bring to front" button: the launcher never touches
            // the game's window. Closing it is fair game — it started it — but
            // enumerating other processes' windows and stealing focus is not
            // something anything here needs.
            const stop = App.actionButton('stop', `Stop the game (pid ${account.pid})`,
                                          'danger', () => App.stopAccount(account));
            if (grow) stop.classList.add('grow');
            actions.appendChild(stop);
        } else {
            // If this machine cannot launch (no Wine, no helper…), the button
            // says so instead of failing when pressed.
            const host = App.state.host || { ready: true };
            const blocked = !host.ready;
            const play = App.actionButton('play',
                blocked ? host.detail : (busy ? 'Busy…' : 'Launch'), 'accent',
                (busy || blocked) ? null : () => App.launch(account),
                { disabled: busy || blocked });
            if (grow) play.classList.add('grow');
            actions.appendChild(play);
        }
        return actions;
    }

    /** Account card: identity on top, server and status in the middle, actions
     *  at the bottom. It is the only place an account's status is shown. */
    function accountCard(account) {
        const card = el('div', 'card');
        card.dataset.email = account.email;

        const head = el('div', 'card-head');
        const name = el('span', 'card-name' + (account.flagged ? ' struck' : ''));
        name.textContent = account.label;
        if (account.color && !account.flagged) name.style.color = account.color;
        const mail = el('span', 'card-mail');
        mail.textContent = App.shownEmail(account);
        head.append(name, mail);

        const meta = el('div', 'card-meta');
        const server = el('button', 'server-cell');
        // The text goes inside a span: the optical correction applies to the
        // text, not to the button (moving the button would shift its border).
        const serverText = el('span');
        serverText.textContent = App.SERVER_TEXT[account.region] || account.region;
        server.appendChild(serverText);
        server.title = 'Change region';
        server.addEventListener('click', (e) => {
            e.stopPropagation();
            openRegionMenu(server, account);
        });

        const badge = el('span', 'badge ' + account.status);
        badge.append(el('span', 'dot'));
        const text = el('span');
        text.textContent = App.STATUS_TEXT[account.status] || account.status;
        badge.appendChild(text);
        if (account.detail) {
            badge.dataset.tip = account.detail;
            if (account.status === 'failed') badge.dataset.tipKind = 'error';
        }
        meta.append(server, badge);

        card.append(head, meta, buildActions(account, { grow: true }));
        App.wireAccountDrag(card, account);
        return card;
    }

    // --- region menu ------------------------------------------------------

    function openRegionMenu(anchor, account) {
        const menu = $('install-menu');
        menu.innerHTML = '';
        for (const region of ['NA', 'EU', 'PTS']) {
            const item = el('button',
                'popover-item' + (account.region === region ? ' active' : ''));
            const title = el('span');
            title.textContent = App.SERVER_TEXT[region];
            item.appendChild(title);
            item.addEventListener('click', async () => {
                App.closePopover();
                account.region = region;
                App.render();
                await App.call('update_account', account.email, { region });
            });
            menu.appendChild(item);
        }
        menu.classList.remove('hidden');
        menu.style.minWidth = '150px';
        const box = anchor.getBoundingClientRect();
        menu.style.left = Math.min(box.left, window.innerWidth - menu.offsetWidth - 8) + 'px';
        menu.style.top = Math.min(box.bottom + 4,
            window.innerHeight - menu.offsetHeight - 8) + 'px';
    }

    App.closePopover = function () { $('install-menu').classList.add('hidden'); };
})();
