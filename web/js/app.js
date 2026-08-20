/* Trove Accounts Hub — pantalla de cuentas.
 *
 * JS -> Python: window.pywebview.api.<método>(...) devuelve una promesa. Toda
 * respuesta trae {ok: bool} y, si ok es false, un "error" ya legible.
 *
 * Python -> JS: el backend inyecta llamadas a window.__launcherEvent con tramas
 * {op, stage, ...}. Se define antes de arrancar para no perder ninguna.
 *
 * El orden que se ve en pantalla ES el orden de state.accounts y state.groups:
 * arrastrar consiste en reconstruir esas listas y mandarlas al backend.
 */

(function () {
    'use strict';

    const APP_NAME = 'Trove Accounts Hub';

    const $ = (id) => document.getElementById(id);
    const api = () => (window.pywebview && window.pywebview.api) || null;

    let state = { groups: [], accounts: [], installs: [], running: [], versions: {} };
    let hideEmails = true;
    let filterText = '';
    let drag = null;              // {kind: 'account'|'group', id}
    let pendingDrop = null;       // último destino calculado durante el arrastre
    let modalCleanup = null;
    let expanded = {};            // grupos con el "+N more" desplegado
    let looseCollapsed = false;   // ¿está plegada la sección «Ungrouped»?

    // Un grupo enseña como mucho estas tarjetas antes de plegar el resto, para
    // que una carpeta con 20 muleros no empuje al resto fuera de la pantalla.
    const GROUP_PREVIEW = 12;

    const ICONS = {
        caret: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3l5 5-5 5"/></svg>',
        grip: '<svg viewBox="0 0 16 16" fill="currentColor"><circle cx="6" cy="4" r="1.2"/><circle cx="10" cy="4" r="1.2"/><circle cx="6" cy="8" r="1.2"/><circle cx="10" cy="8" r="1.2"/><circle cx="6" cy="12" r="1.2"/><circle cx="10" cy="12" r="1.2"/></svg>',
        gear: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.6a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>',
        trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/></svg>',
        verify: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/></svg>',
        play: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M6.5 4.2v15.6a1 1 0 0 0 1.53.85l12.2-7.8a1 1 0 0 0 0-1.7L8.03 3.35A1 1 0 0 0 6.5 4.2z"/></svg>',
        stop: '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>',
        eye: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1.5 12S5.5 5 12 5s10.5 7 10.5 7-4 7-10.5 7S1.5 12 1.5 12z"/><circle cx="12" cy="12" r="3.2"/></svg>',
        eyeOff: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.6 6.2A8.9 8.9 0 0 1 12 6c6.5 0 10.5 6.6 10.5 6.6a17 17 0 0 1-3.2 3.7M6.2 8A17 17 0 0 0 1.5 12.6S5.5 19 12 19a9.7 9.7 0 0 0 4-.85"/><path d="M9.9 10.5a3.2 3.2 0 0 0 4.3 4.4"/><path d="M3 3l18 18"/></svg>',
        refresh: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-2.64-6.36M21 4v5h-5"/></svg>',
        download: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12m0 0l-4.5-4.5M12 15l4.5-4.5M4 18.5V20a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-1.5"/></svg>',
        wrench: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15.4 3.6a5 5 0 0 0-6.1 6.6L3.7 15.8a2 2 0 0 0 2.8 2.8l5.6-5.6a5 5 0 0 0 6.6-6.1l-2.8 2.8-2.6-.7-.7-2.6z"/><path d="M14.5 14.5l5 5"/></svg>',
        folder: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7.5a2 2 0 0 1 2-2h3.6a2 2 0 0 1 1.5.7l1 1.2H19a2 2 0 0 1 2 2v8.1a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>',
        // Bucle de dos flechas: vuelve a entrar solo. Se distingue a proposito
        // del icono de refresh (una sola flecha) que usa mantenimiento.
        relog: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 2.5l3.5 3.5L17 9.5"/><path d="M3.5 11.5v-1.5a4 4 0 0 1 4-4h13"/><path d="M7 21.5L3.5 18 7 14.5"/><path d="M20.5 12.5v1.5a4 4 0 0 1-4 4h-13"/></svg>',
        rocket: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3c3.5 2 5.5 5.5 5.5 9.5L15 15H9l-2.5-2.5C6.5 8.5 8.5 5 12 3z"/><circle cx="12" cy="10" r="1.6"/><path d="M9 15l-2 4 3-1M15 15l2 4-3-1"/></svg>',
    };

    // Texto del badge de estado y del recuadro de servidor.
    const STATUS_TEXT = {
        unknown: 'Idle',
        ready: 'Ready',
        failed: 'Failed',
        pending: 'No password',
        checking: 'Testing',
        launching: 'Logging in',
        running: 'Running',
    };
    const SERVER_TEXT = { NA: 'NA', EU: 'EU', PTS: 'PTS' };

    // --- utilidades -------------------------------------------------------

    let msgTimer = null;
    /** Mensaje global, en la barra inferior. Lo que afecta a UNA cuenta se ve en
     *  su tarjeta (badge + tooltip), no aquí. */
    function notice(text, kind) {
        const label = $('status-msg');
        label.textContent = text || '';
        label.className = 'msg ' + (kind || '');
        clearTimeout(msgTimer);
        if (text) msgTimer = setTimeout(() => { label.textContent = ''; label.className = 'msg'; }, 7000);
    }
    function clearNotice() { notice('', ''); }

    function logLine(text) {
        const log = $('log');
        log.textContent += (log.textContent ? '\n' : '') + text;
        log.scrollTop = log.scrollHeight;
    }

    async function call(name, ...args) {
        const bridge = api();
        if (!bridge) { notice('The backend bridge is not ready yet.', 'error'); return null; }
        try {
            const result = await bridge[name](...args);
            if (result && result.ok === false) {
                notice(result.error || 'Unknown error.', 'error');
                return null;
            }
            return result;
        } catch (err) {
            notice(String(err), 'error');
            return null;
        }
    }

    function el(tag, className, html) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (html != null) node.innerHTML = html;
        return node;
    }

    /** Botón de acción de la tarjeta: icono, sin texto. El título es la única pista,
     *  así que debe decir qué hace, no cómo se llama. */
    function actionButton(icon, title, className, onClick, opts) {
        const b = el('button', 'act ' + (className || ''), ICONS[icon]);
        b.dataset.icon = icon;
        b.title = title;
        b.setAttribute('aria-label', title);
        if (opts && opts.disabled) b.disabled = true;
        if (onClick) b.addEventListener('click', onClick);
        return b;
    }

    function shownEmail(account) { return hideEmails ? account.masked : account.email; }

    /** Ojo tachado = ahora mismo están ocultos; ojo abierto = se ven. */
    function paintEyeButton() {
        const b = $('toggle-emails');
        b.innerHTML = hideEmails ? ICONS.eyeOff : ICONS.eye;
        b.title = hideEmails ? 'Show emails' : 'Hide emails';
    }

    // --- filtro y conteos -------------------------------------------------

    function matches(account) {
        if (!filterText) return true;
        const needle = filterText.toLowerCase();
        return (account.label || '').toLowerCase().includes(needle)
            || (account.email || '').toLowerCase().includes(needle)
            || (account.region || '').toLowerCase().includes(needle)
            || (STATUS_TEXT[account.status] || '').toLowerCase().includes(needle);
    }

    function accountsOf(groupId) {
        return state.accounts.filter((a) => (a.group || null) === groupId && matches(a));
    }

    function renderCounts() {
        const total = state.accounts.length;
        const running = state.accounts.filter((a) => a.status === 'running').length;
        const shown = state.accounts.filter(matches).length;
        const bits = [`<b>${total}</b> accounts`];
        if (filterText) bits.push(`<b>${shown}</b> shown`);
        if (running) bits.push(`<span class="live">${running} running</span>`);
        $('counts').innerHTML = bits.join(' · ');
    }

    // --- render -----------------------------------------------------------

    function render() {
        const rows = $('rows');
        rows.innerHTML = '';

        const anyAccounts = state.accounts.length > 0;
        $('empty-state').classList.toggle('hidden', anyAccounts || state.groups.length > 0);

        for (const group of state.groups) {
            const list = accountsOf(group.id);
            // Con filtro activo, un grupo sin coincidencias no aporta nada.
            if (filterText && !list.length) continue;
            rows.appendChild(groupHead(group, list));
            if (group.collapsed) continue;
            appendAccounts(rows, list, group.id);
        }

        const loose = accountsOf(null);
        if (loose.length || (!filterText && state.groups.length)) {
            rows.appendChild(looseHead(loose));
        }
        if (!looseCollapsed) appendAccounts(rows, loose, null);

        // Zona para sacar cuentas de su grupo arrastrando.
        if (!filterText) {
            const zone = el('div', 'dropzone', 'Drag here to ungroup');
            zone.addEventListener('dragover', (e) => {
                if (drag && drag.kind === 'account') { e.preventDefault(); zone.classList.add('drop-into'); }
            });
            zone.addEventListener('dragleave', () => zone.classList.remove('drop-into'));
            zone.addEventListener('drop', (e) => {
                e.preventDefault();
                zone.classList.remove('drop-into');
                dropAccount(null, null, 'end');
            });
            rows.appendChild(zone);
        }
        renderCounts();
    }

    /** Zona de destino de un grupo: invisible hasta que empieza un arrastre,
     *  momento en el que aparece con línea discontinua. Es lo que permite soltar
     *  en una categoría vacía, donde no hay ninguna tarjeta que sirva de
     *  referencia. */
    function dropSlot(groupId) {
        const slot = el('div', 'drop-slot');
        slot.addEventListener('dragover', (e) => {
            if (!drag || drag.kind !== 'account') return;
            e.preventDefault();
            slot.classList.add('over');
            // Soltar aquí = al final de este grupo.
            pendingDrop = { email: null, group: groupId, after: true };
        });
        slot.addEventListener('dragleave', () => slot.classList.remove('over'));
        slot.addEventListener('drop', (e) => {
            if (!drag || drag.kind !== 'account') return;
            e.preventDefault();
            slot.classList.remove('over');
            clearDropMarks();
            dropAccount(null, groupId, 'group');
        });
        return slot;
    }

    function appendAccounts(rows, list, groupId) {
        const key = groupId || '__loose__';
        // El filtro ya reduce la lista, así que ahí no plegamos nada.
        const cap = (filterText || expanded[key]) ? list.length : GROUP_PREVIEW;
        const visible = list.slice(0, cap);

        const grid = el('div', 'card-grid');
        for (const account of visible) grid.appendChild(accountCard(account));
        grid.appendChild(dropSlot(groupId));
        // El hueco sobrante de la rejilla es grande; soltar ahí debe colocar la
        // cuenta al final de este grupo en vez de no hacer nada.
        grid.addEventListener('dragover', (e) => {
            if (!drag || drag.kind !== 'account' || e.target.closest('.card')) return;
            e.preventDefault();
            pendingDrop = null;          // al final del grupo
            const moving = document.querySelector('.dragging');
            if (moving && moving.parentNode === grid && moving !== grid.lastElementChild) {
                grid.appendChild(moving);
            }
        });
        grid.addEventListener('drop', (e) => {
            if (!drag || drag.kind !== 'account' || e.target.closest('.card')) return;
            e.preventDefault();
            clearDropMarks();
            dropAccount(null, groupId, 'group');
        });
        rows.appendChild(grid);

        const hidden = list.length - visible.length;
        if (hidden > 0) {
            const label = groupId
                ? `+ ${hidden} more in ${(state.groups.find((g) => g.id === groupId) || {}).name || ''}`
                : `+ ${hidden} more`;
            const more = el('div', 'more-row', label);
            more.addEventListener('click', () => { expanded[key] = true; render(); });
            rows.appendChild(more);
        } else if (expanded[key] && list.length > GROUP_PREVIEW) {
            const less = el('div', 'more-row', 'Show less');
            less.addEventListener('click', () => { delete expanded[key]; render(); });
            rows.appendChild(less);
        }
    }

    function looseHead(list) {
        const head = el('div', 'group-head');
        // Los grupos llevan grip de arrastre y éste no; sin un hueco del mismo
        // ancho su flecha se comería esa posición y quedaría desalineada.
        const spacer = el('span', 'grip');
        spacer.style.visibility = 'hidden';
        const caret = el('span', 'group-caret' + (looseCollapsed ? '' : ' open'), ICONS.caret);
        const name = el('span', 'group-name');
        name.textContent = 'Ungrouped';
        const count = el('span', 'group-count');
        count.textContent = list.length;

        head.append(spacer, caret, name, count);
        head.addEventListener('click', () => { looseCollapsed = !looseCollapsed; render(); });
        return head;
    }

    function groupHead(group, list) {
        const head = el('div', 'group-head');
        head.dataset.groupId = group.id;

        const grip = el('span', 'grip', ICONS.grip);
        const caret = el('span', 'group-caret' + (group.collapsed ? '' : ' open'), ICONS.caret);
        const name = el('span', 'group-name');
        name.textContent = group.name;
        const count = el('span', 'group-count');
        count.textContent = list.length;

        // El estado no se resume aquí: vive en la tarjeta de cada cuenta, que es
        // donde se puede actuar sobre él. Un contador en la cabecera repetía la
        // misma información sin decir de quién era.
        const tools = el('div', 'group-tools');
        const launchable = list.filter((a) => a.status !== 'running'
            && a.status !== 'launching' && a.status !== 'checking');
        if (launchable.length > 1) {
            // El logo es un PNG blanco con alfa; el CSS lo usa como máscara para
            // que tome el color de acento en vez de quedarse blanco fijo.
            const all = el('button', 'launch-all', '<span class="launch-logo"></span>');
            all.title = `Launch all ${launchable.length} accounts in ${group.name}`;
            all.addEventListener('click', (e) => { e.stopPropagation(); launchAll(launchable, group.name); });
            tools.appendChild(all);
        }
        const edit = el('button', 'icon-btn', ICONS.gear);
        edit.title = 'Edit group';
        edit.addEventListener('click', (e) => { e.stopPropagation(); openGroupModal(group); });
        const remove = el('button', 'icon-btn', ICONS.trash);
        remove.title = 'Delete group';
        remove.addEventListener('click', async (e) => {
            e.stopPropagation();
            if (!window.confirm(`Delete the group "${group.name}"? Its accounts move to Ungrouped.`)) return;
            await call('delete_group', group.id);
            refresh();
        });
        tools.append(edit, remove);

        head.append(grip, caret, name, count, tools);
        head.addEventListener('click', async () => {
            group.collapsed = !group.collapsed;
            render();
            await call('update_group', group.id, { collapsed: group.collapsed });
        });
        wireGroupDrag(head, group, grip);
        return head;
    }

    /** Los botones de acción de una cuenta. El último se estira para llenar el
     *  ancho de la tarjeta; de ahí la clase `grow`. */
    function buildActions(account, opts) {
        const grow = opts && opts.grow;
        const actions = el('div', 'actions');
        const busy = account.status === 'launching' || account.status === 'checking';

        actions.appendChild(actionButton('trash', 'Delete account', 'danger', async () => {
            if (!window.confirm(
                `Delete ${account.label}? Its saved session and password are removed.`)) return;
            await call('remove_account', account.email);
            refresh();
        }, { disabled: account.status === 'running' }));
        actions.appendChild(actionButton('gear', 'Edit account', '',
            () => openAccountModal(account)));
        
        actions.appendChild(actionButton('verify', 'Test login without launching', '',
            () => testLogin(account), { disabled: busy }));
            // Auto-relog: encendido en color de acento, apagado apagado. Se guarda
        // en la cuenta y, si tiene partida abierta, se aplica a esa instancia.
        actions.appendChild(actionButton('relog',
            account.auto_relog
                ? 'Auto-relog is ON — signs back in if the game crashes'
                : 'Auto-relog is OFF',
            account.auto_relog ? 'accent' : '',
            async () => {
                account.auto_relog = !account.auto_relog;
                render();
                await call('set_auto_relog', account.email, account.auto_relog);
            }));

        if (account.status === 'running') {
            // No hay botón de "traer al frente": el launcher no toca la ventana
            // del juego. Cerrar sí es cosa suya —lo arrancó él—, pero enumerar
            // ventanas ajenas y robarles el foco no lo necesita nadie aquí.
            const stop = actionButton('stop', `Stop the game (pid ${account.pid})`, 'danger',
                () => stopAccount(account));
            if (grow) stop.classList.add('grow');
            actions.appendChild(stop);
        } else {
            // Si en este equipo no se puede lanzar (falta Wine, falta el
            // ayudante…), el botón lo dice en lugar de fallar al pulsarlo.
            const host = state.host || { ready: true };
            const blocked = !host.ready;
            const play = actionButton('play',
                blocked ? host.detail : (busy ? 'Busy…' : 'Launch'), 'accent',
                (busy || blocked) ? null : () => launch(account),
                { disabled: busy || blocked });
            if (grow) play.classList.add('grow');
            actions.appendChild(play);
        }
        return actions;
    }

    /** Tarjeta de cuenta: identidad arriba, servidor y estado en medio, acciones
     *  abajo. Es el único sitio donde se ve el estado de una cuenta. */
    function accountCard(account) {
        const card = el('div', 'card');
        card.dataset.email = account.email;

        const head = el('div', 'card-head');
        const name = el('span', 'card-name' + (account.flagged ? ' struck' : ''));
        name.textContent = account.label;
        if (account.color && !account.flagged) name.style.color = account.color;
        const mail = el('span', 'card-mail');
        mail.textContent = shownEmail(account);
        head.append(name, mail);

        const meta = el('div', 'card-meta');
        const server = el('button', 'server-cell');
        // El texto va dentro de un span: la corrección óptica se aplica al
        // texto, no al botón (mover el botón desplazaría también su borde).
        const serverText = el('span');
        serverText.textContent = SERVER_TEXT[account.region] || account.region;
        server.appendChild(serverText);
        server.title = 'Change region';
        server.addEventListener('click', (e) => { e.stopPropagation(); openRegionMenu(server, account); });

        const badge = el('span', 'badge ' + account.status);
        badge.append(el('span', 'dot'));
        const text = el('span');
        text.textContent = STATUS_TEXT[account.status] || account.status;
        badge.appendChild(text);
        if (account.detail) {
            badge.dataset.tip = account.detail;
            if (account.status === 'failed') badge.dataset.tipKind = 'error';
        }
        meta.append(server, badge);

        card.append(head, meta, buildActions(account, { grow: true }));
        wireAccountDrag(card, account, null);
        return card;
    }

    // --- menú de región ---------------------------------------------------

    function openRegionMenu(anchor, account) {
        const menu = $('install-menu');
        menu.innerHTML = '';
        for (const region of ['NA', 'EU', 'PTS']) {
            const item = el('button', 'popover-item' + (account.region === region ? ' active' : ''));
            const title = el('span');
            title.textContent = SERVER_TEXT[region];
            item.appendChild(title);
            item.addEventListener('click', async () => {
                closePopover();
                account.region = region;
                render();
                await call('update_account', account.email, { region });
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

    function closePopover() { $('install-menu').classList.add('hidden'); }

    // --- arrastrar y soltar ----------------------------------------------

    function clearDropMarks() {
        for (const node of document.querySelectorAll(
                '.drop-before,.drop-after,.drop-left,.drop-right,.drop-into')) {
            node.classList.remove('drop-before', 'drop-after',
                                  'drop-left', 'drop-right', 'drop-into');
        }
    }

    /** ¿La cuenta arrastrada va DESPUÉS de la que hay debajo del cursor?
     *
     *  En rejilla el orden fluye de izquierda a derecha, así que decide el eje X:
     *  marcar arriba/abajo no diría dónde va a caer la tarjeta. */
    function dropSide(node, event) {
        const box = node.getBoundingClientRect();
        return { after: event.clientX > box.left + box.width / 2 };
    }

    /** El arrastre se arma sólo desde el grip: con la cabecera entera arrastrable,
     *  un clic con dos píxeles de movimiento sobre un botón inicia un arrastre
     *  en lugar de la acción. */
    function armDragFromGrip(node, grip) {
        node.draggable = false;
        // Marca de "esto se arma y se desarma": sólo estos nodos toca disarmDrag.
        node.dataset.gripDrag = '1';
        if (grip) grip.addEventListener('mousedown', () => { node.draggable = true; });
    }

    /** Desarma SÓLO lo que se arma desde un grip (las cabeceras de grupo).
     *
     *  Antes recorría todos los [draggable="true"], y como las cuentas nacen
     *  arrastrables de forma permanente, el primer mouseup en cualquier punto de
     *  la ventana las dejaba inertes hasta el siguiente repintado. */
    function disarmDrag() {
        for (const n of document.querySelectorAll('[data-grip-drag]')) n.draggable = false;
    }

    function wireAccountDrag(row, account, grip) {
        // Sin grip visible, la tarjeta se arrastra desde cualquier punto suyo.
        row.draggable = true;
        row.addEventListener('dragstart', (e) => {
            drag = { kind: 'account', id: account.email };
            pendingDrop = null;
            e.dataTransfer.effectAllowed = 'move';
            e.dataTransfer.setData('text/plain', account.email);
            // Todo cambio de aspecto va en el siguiente tick. Dentro del
            // propio dragstart, el navegador captura como imagen de arrastre
            // una tarjeta ya vaciada, y alterar la disposición —revelar las zonas de
            // destino lo altera— llega a cancelar la operación.
            setTimeout(() => {
                if (!drag) return;
                row.classList.add('dragging');
                document.body.classList.add('dragging-account');
            }, 0);
        });
        row.addEventListener('dragend', () => {
            document.body.classList.remove('dragging-account');
            row.classList.remove('dragging');
            drag = null;
            pendingDrop = null;
            clearDropMarks();
            render();   // el DOM quedó movido a mano: se repinta desde el estado
        });
        row.addEventListener('dragover', (e) => {
            if (!drag || drag.kind !== 'account' || drag.id === account.email) return;
            e.preventDefault();
            for (const n of document.querySelectorAll('.drop-into')) {
                n.classList.remove('drop-into');
            }
            const after = dropSide(row, e).after;
            // Se recuerda el destino: tras mover el nodo, el elemento bajo el
            // cursor puede ser otro, así que el drop usa esto y no lo que haya
            // debajo en ese instante.
            pendingDrop = { email: account.email, group: account.group || null, after: after };

            const moving = document.querySelector('.dragging');
            if (!moving || moving === row) return;
            const target = after ? row.nextSibling : row;
            // Reinsertar cuando ya está en su sitio es lo que provoca el
            // parpadeo: cada movimiento recoloca la rejilla y dispara otro
            // dragover, que vuelve a moverlo.
            if (moving !== target && moving.nextSibling !== target) {
                row.parentNode.insertBefore(moving, target);
            }
        });
        row.addEventListener('drop', (e) => {
            if (!drag || drag.kind !== 'account') return;
            e.preventDefault();
            const d = pendingDrop || { email: account.email,
                                       group: account.group || null,
                                       after: dropSide(row, e).after };
            clearDropMarks();
            dropAccount(d.email, d.group, d.after ? 'after' : 'before');
        });
    }

    function wireGroupDrag(head, group, grip) {
        armDragFromGrip(head, grip);
        head.addEventListener('dragstart', (e) => {
            drag = { kind: 'group', id: group.id };
            head.classList.add('dragging');
            e.dataTransfer.effectAllowed = 'move';
            e.dataTransfer.setData('text/plain', group.id);
        });
        head.addEventListener('dragend', () => {
            head.classList.remove('dragging'); drag = null; clearDropMarks();
        });
        head.addEventListener('dragover', (e) => {
            if (!drag) return;
            if (drag.kind === 'group' && drag.id === group.id) return;
            // Si la cuenta ya vive en este grupo, la cabecera no ofrece nada:
            // no la resaltamos para no invitar a soltar aquí.
            if (drag.kind === 'account' && inGroup(drag.id, group.id)) return;
            e.preventDefault();
            clearDropMarks();
            head.classList.add(drag.kind === 'account' ? 'drop-into' : 'drop-before');
        });
        head.addEventListener('drop', (e) => {
            if (!drag) return;
            e.preventDefault();
            clearDropMarks();
            if (drag.kind === 'group') { dropGroup(group.id); return; }
            if (inGroup(drag.id, group.id)) return;   // ya estaba aquí: nada que mover
            dropAccount(null, group.id, 'group');
        });
    }

    function inGroup(email, groupId) {
        const account = state.accounts.find((a) => a.email === email);
        return !!account && (account.group || null) === (groupId || null);
    }

    function dropAccount(targetEmail, groupId, where) {
        const moved = state.accounts.find((a) => a.email === drag.id);
        if (!moved) return;
        const rest = state.accounts.filter((a) => a.email !== moved.email);
        moved.group = groupId;

        let index;
        if (targetEmail) {
            const at = rest.findIndex((a) => a.email === targetEmail);
            index = where === 'after' ? at + 1 : at;
        } else if (where === 'group') {
            // Al final del grupo destino: mandarla al principio hacía que una
            // cuenta soltada sobre la cabecera saltase al primer puesto.
            let last = -1;
            rest.forEach((a, i) => { if ((a.group || null) === groupId) last = i; });
            index = last === -1 ? rest.length : last + 1;
        } else {
            index = rest.length;
        }
        rest.splice(index, 0, moved);
        state.accounts = rest;
        render();
        call('reorder', { accounts: state.accounts.map((a) => ({ email: a.email, group: a.group || null })) });
    }

    function dropGroup(targetId) {
        const moved = state.groups.find((g) => g.id === drag.id);
        if (!moved) return;
        const rest = state.groups.filter((g) => g.id !== moved.id);
        const at = rest.findIndex((g) => g.id === targetId);
        rest.splice(at === -1 ? rest.length : at, 0, moved);
        state.groups = rest;
        render();
        call('reorder', { groups: state.groups.map((g) => g.id) });
    }

    // --- modales ----------------------------------------------------------

    function openModal(title, body, options) {
        options = options || {};
        $('modal-title').textContent = title;
        const container = $('modal-body');
        container.innerHTML = '';
        container.appendChild(body);
        $('modal-confirm').textContent = options.confirmText || 'OK';
        $('modal-cancel').textContent = options.cancelText || 'Cancel';
        $('modal-backdrop').classList.remove('hidden');

        modalCleanup = options.onCancel || null;
        $('modal-confirm').onclick = async () => {
            if (options.onConfirm) {
                const keep = await options.onConfirm();
                if (keep === false) return;
            }
            closeModal(true);
        };
        const focusable = body.querySelector('input, select');
        if (focusable) setTimeout(() => focusable.focus(), 30);
    }

    function closeModal(confirmed) {
        $('modal-backdrop').classList.add('hidden');
        if (!confirmed && modalCleanup) modalCleanup();
        modalCleanup = null;
        $('modal-confirm').onclick = null;
    }

    function field(labelText, input) {
        const wrap = el('div', 'field');
        const label = el('label');
        label.textContent = labelText;
        wrap.append(label, input);
        return wrap;
    }

    function textInput(type, placeholder, value) {
        const input = document.createElement('input');
        input.type = type;
        input.placeholder = placeholder || '';
        input.value = value || '';
        return input;
    }

    function selectInput(options, value) {
        const select = document.createElement('select');
        for (const option of options) {
            const node = document.createElement('option');
            node.value = option.value;
            node.textContent = option.label;
            select.appendChild(node);
        }
        select.value = value;
        return select;
    }

    const PALETTE = ['#22c55e', '#38bdf8', '#a371f7', '#f59e0b', '#f43f5e',
                     '#14b8a6', '#ec4899', '#6366f1', '#84cc16', '#94a3b8'];

    function swatches(current, onPick) {
        const wrap = el('div', 'swatches');
        for (const colour of PALETTE) {
            const dot = el('button', 'swatch' + (colour === current ? ' active' : ''));
            dot.type = 'button';
            dot.style.background = colour;
            dot.addEventListener('click', () => {
                for (const other of wrap.children) other.classList.remove('active');
                dot.classList.add('active');
                onPick(colour);
            });
            wrap.appendChild(dot);
        }
        return wrap;
    }

    function groupOptions() {
        return [{ value: '', label: 'Ungrouped' }]
            .concat(state.groups.map((g) => ({ value: g.id, label: g.name })));
    }

    function openAccountModal(account) {
        const body = el('div', 'field');
        body.style.gap = '14px';

        const name = textInput('text', account.masked, account.name || '');
        const region = selectInput(['NA', 'EU', 'PTS'].map((r) => ({ value: r, label: SERVER_TEXT[r] })),
                                   account.region);
        const group = selectInput(groupOptions(), account.group || '');
        const password = textInput('password', account.has_saved_password
            ? 'Saved — type to change it' : 'Not saved');

        const flagged = document.createElement('input');
        flagged.type = 'checkbox';
        flagged.checked = !!account.flagged;
        const flaggedLabel = el('label', 'check');
        const flaggedText = el('span');
        flaggedText.textContent = 'Flag this account (shown struck through)';
        flaggedLabel.append(flagged, flaggedText);

        const signout = el('button', 'btn ghost');
        signout.textContent = 'Sign out (forget ticket and password)';
        signout.addEventListener('click', async () => {
            closeModal(true);
            await call('logout', account.email);
            await refresh();
            notice(`${account.label} signed out.`, 'ok');
        });

        body.append(
            field('Display name', name),
            field('Server', region),
            field('Group', group),
            field('Password', password),
            flaggedLabel, signout,
        );

        openModal('Edit account', body, {
            confirmText: 'Save',
            onConfirm: async () => {
                await call('update_account', account.email, {
                    name: name.value.trim(),
                    region: region.value,
                    group: group.value || null,
                    flagged: flagged.checked,
                });
                if (password.value) await call('set_password', account.email, password.value);
                await refresh();
            },
        });
    }

    function openAddAccountModal() {
        const body = el('div', 'field');
        body.style.gap = '14px';
        const email = textInput('email', 'you@example.com');
        const password = textInput('password', 'Glyph password');
        const name = textInput('text', 'Optional');
        const region = selectInput(['NA', 'EU', 'PTS'].map((r) => ({ value: r, label: SERVER_TEXT[r] })), 'EU');
        const group = selectInput(groupOptions(), '');

        body.append(
            field('Glyph email', email),
            field('Password', password),
            field('Display name', name),
            field('Server', region),
            field('Group', group),
        );

        openModal('Add account', body, {
            confirmText: 'Add',
            onConfirm: async () => {
                if (!email.value.includes('@')) { notice('Enter a valid email address.', 'error'); return false; }
                const result = await call('add_account', {
                    email: email.value.trim(), password: password.value,
                    name: name.value.trim(), region: region.value,
                    group: group.value, remember_password: true,
                });
                if (!result) return false;
                await refresh();
            },
        });
    }

    function openGroupModal(group) {
        const body = el('div', 'field');
        body.style.gap = '14px';
        const name = textInput('text', 'Group name', group ? group.name : '');
        let colour = group ? group.color : PALETTE[state.groups.length % PALETTE.length];
        body.append(field('Name', name), field('Colour', swatches(colour, (c) => { colour = c; })));

        openModal(group ? 'Edit group' : 'New group', body, {
            confirmText: group ? 'Save' : 'Create',
            onConfirm: async () => {
                if (!name.value.trim()) { notice('Give the group a name.', 'error'); return false; }
                if (group) {
                    await call('update_group', group.id, { name: name.value.trim(), color: colour });
                } else {
                    const created = await call('create_group', name.value.trim());
                    if (created && created.group) {
                        await call('update_group', created.group.id, { color: colour });
                    }
                }
                await refresh();
            },
        });
    }

    function open2faModal(email, label) {
        const body = el('div', 'field');
        body.style.gap = '12px';
        const text = el('p');
        text.textContent = `Trion sent a code to ${label}. Enter it to continue.`;
        const code = textInput('text', 'Code');
        code.setAttribute('inputmode', 'numeric');
        body.append(text, field('Code', code));

        openModal('Two-step verification', body, {
            confirmText: 'Submit',
            onConfirm: async () => {
                if (!code.value.trim()) return false;
                await call('submit_2fa', email, code.value.trim());
            },
            onCancel: () => call('cancel_2fa', email),
        });
    }

    // --- acciones ---------------------------------------------------------

    function withPassword(account, title, confirmText, run) {
        clearNotice();
        if (account.status !== 'pending') return run('');

        const body = el('div', 'field');
        body.style.gap = '12px';
        const text = el('p');
        text.textContent = `${account.label} has no saved password and no active session.`;
        const password = textInput('password', 'Glyph password');
        body.append(text, field('Password', password));

        openModal(title, body, {
            confirmText: confirmText,
            onConfirm: async () => {
                if (!password.value) return false;
                await run(password.value);
            },
        });
    }

    function launch(account) {
        return withPassword(account, 'Password needed', 'Launch', async (password) => {
            account.status = 'launching';
            render();
            const result = await call('play', { email: account.email, password: password });
            if (!result || result.started === false) {
                if (result && result.error) notice(result.error, 'error');
                refresh();
            }
        });
    }

    function testLogin(account) {
        return withPassword(account, 'Test login', 'Test', async (password) => {
            account.status = 'checking';
            render();
            const result = await call('test_login', { email: account.email, password: password });
            if (!result || result.started === false) {
                if (result && result.error) notice(result.error, 'error');
                refresh();
            }
        });
    }

    /** Lanza varias cuentas de golpe.
     *
     *  Las que no tienen contraseña ni sesión se saltan: cada una abriría su
     *  propio diálogo y el usuario acabaría con una pila de modales encima.
     *  El backend ya admite lanzamientos en paralelo, así que no serializamos. */
    async function launchAll(list, groupName) {
        const ready = list.filter((a) => a.status !== 'pending');
        const skipped = list.length - ready.length;
        if (!ready.length) {
            notice(`No account in ${groupName} can launch without typing a password.`, 'error');
            return;
        }
        if (!window.confirm(
            `Launch ${ready.length} account(s) from ${groupName}?`
            + (skipped ? `\n\n${skipped} skipped: no saved password or session.` : ''))) return;

        for (const account of ready) account.status = 'launching';
        render();
        for (const account of ready) {
            await call('play', { email: account.email, password: '' });
        }
        if (skipped) notice(`${skipped} account(s) skipped: no saved password.`, 'error');
        refresh();
    }

    async function stopAccount(account) {
        if (!account.pid) return;
        const result = await call('stop', account.pid);
        if (result) refresh();
    }

    // --- instalación y tema ------------------------------------------------

    function renderInstallChips() {
        const live = state.game_path || '';
        $('install-live').textContent = live ? 'Live: ' + live : 'Set Live install';
        const needsPts = state.accounts.some((a) => a.region === 'PTS') || !!state.pts_game_path;
        const chip = $('install-pts');
        chip.classList.toggle('hidden', !needsPts);
        chip.textContent = state.pts_game_path ? 'PTS: ' + state.pts_game_path : 'Set PTS install';
    }

    function openInstallMenu(anchor, kind) {
        const menu = $('install-menu');
        menu.innerHTML = '';
        menu.style.minWidth = '270px';
        const wanted = kind === 'pts' ? 'pts' : 'live';
        const current = kind === 'pts' ? state.pts_game_path : state.game_path;
        const matching = (state.installs || []).filter((g) => g.kind === wanted || g.source === 'custom');

        if (!matching.length) menu.appendChild(el('div', 'popover-item', '<span>No installation detected</span>'));
        for (const game of matching) {
            const item = el('button', 'popover-item' + (game.path === current ? ' active' : ''));
            const title = el('span'); title.textContent = game.name;
            const path = el('small'); path.textContent = game.path;
            item.append(title, path);
            item.addEventListener('click', async () => {
                closePopover();
                await call('set_install', game.path, wanted);
                refresh();
            });
            menu.appendChild(item);
        }
        menu.appendChild(el('div', 'popover-sep'));
        const browse = el('button', 'popover-item', '<span>Browse for folder…</span>');
        browse.addEventListener('click', async () => {
            closePopover();
            const result = await call('browse_for_install', wanted);
            if (result && !result.cancelled) refresh();
        });
        menu.appendChild(browse);

        menu.classList.remove('hidden');
        const box = anchor.getBoundingClientRect();
        menu.style.left = Math.max(8, Math.min(box.left, window.innerWidth - menu.offsetWidth - 8)) + 'px';
        menu.style.top = Math.max(8, box.top - menu.offsetHeight - 6) + 'px';
    }

    const ACCENTS = [
        { name: 'Green', value: '#22c55e' },
        { name: 'Cyan', value: '#38bdf8' },
        { name: 'Violet', value: '#7c5cfc' },
        { name: 'Amber', value: '#f5c842' },
        { name: 'Rose', value: '#fb7185' },
        { name: 'Teal', value: '#14b8a6' },
        { name: 'Indigo', value: '#6366f1' },
        { name: 'Slate', value: '#94a3b8' },
    ];

    /** Temas de club. Cada uno trae su logo y fija el acento: el color es parte
     *  de la identidad del club, así que mientras uno esté puesto el selector
     *  de acento queda bloqueado (el tinte NO — eso es intensidad, no color, y
     *  sigue siendo del usuario).
     *
     *  `name` no se pinta al lado del logo (ver renderBrand); es el rótulo del
     *  desplegable y el título del logo al pasar por encima. */
    const CLUBS = {
        'mystic-cave': { name: 'Mystic Cave', accent: '#8b5cf6',
                         image: 'img/mystic-cave-hd.png' },
        arsyn:         { name: 'Arsyn', accent: '#a855f7',
                         image: 'img/arsyn.webp' },
        sayro:         { name: 'Sayro', accent: '#b91c1c',
                         image: 'img/sayro.webp' },
    };

    function clubOf(theme) {
        return (theme && CLUBS[theme.club]) ? theme.club : '';
    }

    /** Marca de la barra superior: SÓLO una imagen, la del tema puesto.
     *
     *  Ni nombre al lado ni versión. Dos de los tres logos de club son rótulos
     *  que ya llevan el nombre dentro, así que escribirlo aparte lo decía dos
     *  veces en unos temas y una en otros. Quien nombra siempre la aplicación
     *  es la barra de estado, con el mismo texto se ponga el tema que se ponga.
     *
     *  El logo propio va como MÁSCARA y no como <img>: así toma el color de
     *  acento, igual que hacía el cuadrado que había antes aquí. */
    function renderBrand(theme) {
        const box = $('brand');
        if (!box) return;
        box.innerHTML = '';
        const club = CLUBS[clubOf(theme)];
        if (club) {
            const logo = document.createElement('img');
            logo.className = 'brand-logo';
            logo.src = club.image;
            logo.alt = club.name;
            logo.title = club.name;
            box.appendChild(logo);
            return;
        }
        const lockup = el('span', 'brand-lockup');
        lockup.title = APP_NAME;
        box.appendChild(lockup);
    }

    // Huecos de la fila de acentos propios. Al llenarla, el más viejo cede el
    // sitio: es preferible a bloquear el botón de añadir y obligar a borrar
    // antes de poder probar un color.
    const MAX_CUSTOMS = 8;

    /** Acentos guardados: sólo hex de 6 dígitos, en minúsculas, sin repetidos y
     *  sin los que ya están en la paleta fija (ahí ya se pueden elegir). */
    function normalizeCustoms(list) {
        const presets = ACCENTS.map((a) => a.value.toLowerCase());
        const seen = [];
        for (const raw of Array.isArray(list) ? list : []) {
            const value = String(raw).toLowerCase();
            if (!/^#[0-9a-f]{6}$/.test(value)) continue;
            if (presets.includes(value) || seen.includes(value)) continue;
            seen.push(value);
        }
        return seen.slice(-MAX_CUSTOMS);
    }

    const FONTS = {
        system: '"Segoe UI", system-ui, -apple-system, sans-serif',
        quicksand: 'Quicksand, "Segoe UI", system-ui, sans-serif',
        comfortaa: 'Comfortaa, "Segoe UI", system-ui, sans-serif',
    };

    /** Color de texto legible sobre un fondo dado.
     *
     *  Usa la luminancia relativa de WCAG: con acentos claros (ámbar, slate) el
     *  blanco fijo se volvía ilegible sobre el botón principal. El fondo real de
     *  ese botón es el acento oscurecido un 12%, así que se mide sobre ESE color
     *  y no sobre el acento puro. */
    function readableOn(hex, mix) {
        const m = /^#?([0-9a-f]{6})$/i.exec(String(hex || ''));
        if (!m) return '#ffffff';
        const n = parseInt(m[1], 16);
        const channels = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((v) => {
            const s = (v / 255) * (mix == null ? 1 : mix);
            return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
        });
        const lum = 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
        // Se calcula el contraste real contra blanco y contra oscuro y gana el
        // mayor. Con un umbral fijo, verdes y cianes se quedaban en blanco a
        // 2,8:1 — por debajo incluso del mínimo de WCAG para negrita (3:1).
        const DARK_LUM = 0.0035;                       // luminancia de #0a0c0b
        const vsWhite = 1.05 / (lum + 0.05);
        const vsDark = (lum + 0.05) / (DARK_LUM + 0.05);
        return vsDark > vsWhite ? '#0a0c0b' : '#ffffff';
    }

    function applyTheme(theme) {
        const club = clubOf(theme);
        // El acento propio se guarda intacto aunque el club pinte otro: al
        // quitar el tema se vuelve a él sin haber perdido nada.
        const own = (theme && theme.accent) || '#22c55e';
        const accent = club ? CLUBS[club].accent : own;
        // Un acento a medida en uso queda guardado aunque nunca se pulsara el
        // botón de añadir: es el que hay puesto, perderlo al probar otro era
        // justo el problema.
        const customs = normalizeCustoms((theme && theme.customs || []).concat([own]));
        const stars = !theme || theme.stars !== false;
        const tint = (theme && typeof theme.tint === 'number') ? theme.tint : 0.45;
        const font = (theme && FONTS[theme.font]) ? theme.font : 'system';
        document.documentElement.style.setProperty('--accent', accent);
        document.documentElement.style.setProperty('--tint', String(tint));
        document.documentElement.style.setProperty('--font', FONTS[font]);
        // 0.88: el botón principal pinta el acento mezclado con negro a ese %.
        document.documentElement.style.setProperty('--on-accent', readableOn(accent, 0.88));
        document.body.classList.toggle('no-stars', !stars);
        renderBrand({ club: club });
        return { accent: own, stars: stars, tint: tint, font: font, club: club,
                 customs: customs };
    }

    /** Campo de estrellas como imagen de fondo repetible.
     *
     *  Se dibuja UNA vez sobre un lienzo grande y se guarda como data-URI: un
     *  patrón pequeño se notaría repetido, y animar partículas de verdad
     *  gastaría CPU constante en una ventana que suele estar de fondo. */
    function makeStarfield() {
        const size = 900;
        const canvas = document.createElement('canvas');
        canvas.width = canvas.height = size;
        const ctx = canvas.getContext('2d');
        for (let i = 0; i < 340; i++) {
            const x = Math.random() * size;
            const y = Math.random() * size;
            const r = Math.random() < 0.86 ? 0.6 : 1.1;
            ctx.globalAlpha = 0.18 + Math.random() * 0.5;
            ctx.fillStyle = '#ffffff';
            ctx.beginPath();
            ctx.arc(x, y, r, 0, Math.PI * 2);
            ctx.fill();
        }
        document.documentElement.style.setProperty(
            '--stars', `url("${canvas.toDataURL('image/png')}")`);
    }

    /** Selector de acento en dos filas: arriba la paleta fija, abajo los colores
     *  que ha guardado el usuario más el botón de añadir.
     *
     *  Elegir un color a medida lo guarda: antes vivía sólo en `accent` y se
     *  perdía en cuanto se probaba otro. */
    function renderThemeControls() {
        const theme = state.theme || { accent: '#22c55e' };
        const box = $('theme-swatches');
        const customBox = $('theme-customs');
        if (!box || !customBox) return;
        const club = clubOf(theme);
        // Con club puesto se enseña SU color, no el guardado: lo que ve el
        // usuario es lo que tiene la interfaz delante.
        const accent = String(club ? CLUBS[club].accent : theme.accent).toLowerCase();
        const customs = normalizeCustoms(theme.customs);
        box.classList.toggle('locked', !!club);
        customBox.classList.toggle('locked', !!club);
        $('accent-locked-note').classList.toggle('hidden', !club);

        /** Aplica un acento y lo persiste. `customs` viaja siempre en el tema,
         *  así que un color guardado no se pierde al cambiar de acento. */
        const pick = (value) => {
            state.theme = applyTheme({ ...state.theme, accent: value });
            renderThemeControls();
            call('save_prefs', { theme: state.theme });
        };

        const swatch = (value, title) => {
            const dot = document.createElement('button');
            dot.type = 'button';
            dot.className = 'theme-swatch' + (value.toLowerCase() === accent ? ' active' : '');
            dot.style.background = value;
            dot.title = title;
            dot.disabled = !!club;
            return dot;
        };

        box.innerHTML = '';
        for (const preset of ACCENTS) {
            const dot = swatch(preset.value, preset.name);
            dot.addEventListener('click', () => pick(preset.value));
            box.appendChild(dot);
        }

        customBox.innerHTML = '';
        for (const value of customs) {
            const dot = swatch(value, value.toUpperCase() + ' — click to use, × to forget');
            dot.addEventListener('click', () => pick(value));
            // La × va DENTRO del botón (un <span>, no otro botón: anidar botones
            // no es HTML válido) y detiene la propagación para que borrar no
            // signifique además aplicar.
            const forget = el('span', 'swatch-x', '&times;');
            forget.addEventListener('click', (e) => {
                e.stopPropagation();
                // El acento en uso no se olvida: se guarda solo mientras esté
                // puesto (ver applyTheme), así que la × ahí no haría nada.
                if (club || value.toLowerCase() === accent) return;
                state.theme = applyTheme({
                    ...state.theme,
                    customs: customs.filter((c) => c !== value),
                });
                renderThemeControls();
                call('save_prefs', { theme: state.theme });
            });
            dot.appendChild(forget);
            customBox.appendChild(dot);
        }

        // Botón de añadir: una casilla de puntos con un «+» y, encima e
        // invisible, el selector nativo de color, que es quien abre el diálogo.
        const adder = el('label', 'theme-add', '<span>+</span>');
        adder.title = customs.length >= MAX_CUSTOMS
            ? `Saves a new colour and forgets the oldest (${MAX_CUSTOMS} slots)`
            : 'Save a custom colour';
        const picker = document.createElement('input');
        picker.type = 'color';
        picker.value = /^#[0-9a-f]{6}$/.test(accent) ? accent : '#22c55e';
        picker.disabled = !!club;
        // Mientras se mueve por el diálogo se ve el resultado en vivo; sólo al
        // aceptarlo (change) se guarda, para no llenar la fila de tanteos.
        picker.addEventListener('input', () => {
            applyTheme({ ...state.theme, accent: picker.value });
        });
        picker.addEventListener('change', () => {
            state.theme = applyTheme({
                ...state.theme,
                accent: picker.value,
                customs: customs.concat([picker.value]),
            });
            renderThemeControls();
            call('save_prefs', { theme: state.theme });
        });
        adder.appendChild(picker);
        customBox.appendChild(adder);
    }

    // --- panel de ajustes -------------------------------------------------

    function openDrawer() {
        $('drawer').classList.remove('hidden');
        $('drawer-backdrop').classList.remove('hidden');
        renderDrawer();
    }
    function closeDrawer() {
        $('drawer').classList.add('hidden');
        $('drawer-backdrop').classList.add('hidden');
    }
    function renderFolders() {
        const box = $('folder-list');
        if (!box) return;
        box.innerHTML = '';
        for (const folder of (state.folders || [])) {
            const row = el('div', 'folder-row');
            const label = el('span', 'folder-label');
            label.textContent = folder.label;
            const path = el('span', 'folder-path');
            path.textContent = folder.path;
            path.title = folder.path;
            const open = el('button', 'act', ICONS.folder);
            open.title = 'Open in file manager';
            open.addEventListener('click', () => call('open_folder', folder.kind));
            const text = el('div', 'folder-text');
            text.append(label, path);
            row.append(text, open);
            box.appendChild(row);
        }
    }

    /** Lo que cambia según el sistema: los ajustes de Wine sólo existen donde
     *  hacen falta, y el aviso sobre dónde acaban las contraseñas depende de si
     *  hay un almacén de secretos detrás. */
    function renderPlatform() {
        const host = state.host || {};
        const wine = host.kind === 'wine';
        $('wine-section').classList.toggle('hidden', !wine);
        if (wine) {
            const status = $('wine-status');
            status.textContent = host.ready
                ? 'Ready: the game will be launched inside its prefix.'
                : host.detail;
            status.classList.toggle('bad', !host.ready);
            $('wine-binary').value = state.wine_binary || '';
            $('wine-prefix').value = state.wine_prefix || '';
        }

        const vault = state.vault || {};
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

    function renderDrawer() {
        const versions = state.versions || {};
        $('version-live').textContent = versions['live-us'] || 'not synced';
        $('version-pts').textContent = versions['pts'] || 'not synced';
        $('maint-pts').classList.toggle('hidden', !state.pts_game_path);
        $('opt-update-first').checked = !!state.update_first;
        $('opt-remember-password').checked = !!state.remember_password;
        $('opt-reparent').checked = !!state.reparent_glyph;
        renderFolders();
        renderPlatform();
        $('theme-club').value = clubOf(state.theme);
        const font = (state.theme || {}).font || 'system';
        for (const b of document.querySelectorAll('[data-font]')) {
            b.classList.toggle('active', b.dataset.font === font);
        }
        $('opt-stars').checked = (state.theme || {}).stars !== false;
        const tint = (state.theme || {}).tint;
        $('theme-tint').value = String(typeof tint === 'number' ? tint : 0.45);
        $('tint-value').textContent = Math.round($('theme-tint').value * 100) + '%';
        renderThemeControls();
    }

    // --- estado -----------------------------------------------------------

    function applyState(next) {
        state = next;
        state.theme = applyTheme(next.theme);
        hideEmails = !!next.hide_emails;
        paintEyeButton();
        render();
        renderInstallChips();
        if (!$('drawer').classList.contains('hidden')) renderDrawer();
        stampSweep();
    }

    function stampSweep() {
        const now = new Date();
        const pad = (n) => String(n).padStart(2, '0');
        $('sweep').textContent =
            `Last sweep ${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
    }

    async function refresh() {
        const result = await call('get_state');
        if (result && result.state) applyState(result.state);
    }

    // --- eventos del backend ----------------------------------------------

    function onEvent(payload) {
        if (!payload) return;

        if (payload.op === 'installs') {
            if (payload.installs) { state.installs = payload.installs; renderInstallChips(); }
            return;
        }
        if (payload.op === 'running') {
            if (payload.instances) state.running = payload.instances;
            if (payload.message) logLine(payload.message);
            refresh();
            return;
        }
        if (payload.stage === 'log') { logLine(payload.message); return; }
        if (payload.stage === '2fa_required') {
            open2faModal(payload.email, payload.label || payload.email);
            return;
        }
        if (payload.stage === 'downloading') {
            const total = payload.total || 0;
            $('progress').classList.remove('hidden');
            const fill = $('bar-fill');
            fill.classList.remove('indeterminate');
            fill.style.transform = 'scaleX(' + (total ? payload.current / total : 0) + ')';
            notice(`${payload.current.toLocaleString()} / ${total.toLocaleString()} files`);
            return;
        }
        if (payload.stage === 'settled') { refresh(); return; }

        if (payload.message) {
            logLine(payload.message);
            if (!payload.done) {
                $('progress').classList.remove('hidden');
                $('bar-fill').classList.add('indeterminate');
                notice(payload.message);
            }
        }
        if (payload.done) {
            $('progress').classList.add('hidden');
            $('bar-fill').classList.remove('indeterminate');
            // Lo que pertenece a una cuenta ya lo cuenta su tarjeta.
            if (!payload.email) {
                notice(payload.message || (payload.ok === false ? 'The operation failed.' : 'Done.'),
                       payload.ok === false ? 'error' : 'ok');
            }
            refresh();
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
            tip.style.left = Math.max(8, Math.min(box.left, window.innerWidth - tip.offsetWidth - 8)) + 'px';
            const top = box.bottom + 6;
            tip.style.top = (top + tip.offsetHeight > window.innerHeight
                ? box.top - tip.offsetHeight - 6 : top) + 'px';
        });
        document.addEventListener('mouseout', (e) => {
            if (e.target.closest && e.target.closest('[data-tip]')) tip.classList.add('hidden');
        });
    }

    // --- conexión ---------------------------------------------------------

    function wire() {
        $('add-account').addEventListener('click', openAddAccountModal);
        $('add-group').addEventListener('click', () => openGroupModal(null));
        $('open-settings').addEventListener('click', openDrawer);
        $('close-drawer').addEventListener('click', closeDrawer);
        $('drawer-backdrop').addEventListener('click', closeDrawer);
        document.querySelector('[data-action="add-first"]').addEventListener('click', openAddAccountModal);

        $('filter').addEventListener('input', () => {
            filterText = $('filter').value.trim();
            render();
        });

        $('toggle-emails').addEventListener('click', async () => {
            hideEmails = !hideEmails;
            paintEyeButton();
            render();
            await call('save_prefs', { hide_emails: hideEmails });
        });

        $('modal-cancel').addEventListener('click', () => closeModal(false));
        $('modal-backdrop').addEventListener('click', (e) => {
            if (e.target === $('modal-backdrop')) closeModal(false);
        });
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                if (!$('modal-backdrop').classList.contains('hidden')) closeModal(false);
                else if (!$('drawer').classList.contains('hidden')) closeDrawer();
                closePopover();
            }
            if (e.key === 'Enter' && !$('modal-backdrop').classList.contains('hidden')) {
                $('modal-confirm').click();
            }
            // Ctrl+F o "/" para saltar al filtro.
            if ((e.ctrlKey && e.key === 'f') || (e.key === '/' && e.target.tagName !== 'INPUT')) {
                e.preventDefault();
                $('filter').focus();
            }
        });

        $('install-live').addEventListener('click', (e) => {
            e.stopPropagation(); openInstallMenu($('install-live'), 'live');
        });
        $('install-pts').addEventListener('click', (e) => {
            e.stopPropagation(); openInstallMenu($('install-pts'), 'pts');
        });
        document.addEventListener('click', closePopover);

        for (const b of document.querySelectorAll('[data-maint]')) {
            b.addEventListener('click', async () => {
                const action = b.dataset.maint;
                if (action === 'repair' && !window.confirm(
                    'Repair re-downloads every file in the manifest. '
                    + 'It can take a long time and use several GB.\n\nContinue?')) return;
                notice('Preparing…');
                const result = await call(action, b.dataset.target);
                if (result && result.started === false) {
                    notice(result.error || 'Another operation is already running.', 'error');
                }
            });
        }
        for (const b of document.querySelectorAll('[data-folder]')) {
            b.addEventListener('click', () => call('open_folder', b.dataset.folder));
        }
        $('rescan').addEventListener('click', async () => {
            const result = await call('rescan_installs');
            if (result) {
                state.installs = result.installs || [];
                renderInstallChips();
                notice(`${state.installs.length} installation(s) found.`, 'ok');
            }
        });

        for (const [id, key] of [['opt-update-first', 'update_first'],
                                 ['opt-remember-password', 'remember_password'],
                                 ['opt-reparent', 'reparent_glyph']]) {
            $(id).addEventListener('change', () => {
                state[key] = $(id).checked;
                call('save_prefs', { [key]: $(id).checked });
            });
        }

        document.addEventListener('dragover', (e) => e.preventDefault());
        document.addEventListener('drop', (e) => e.preventDefault());
        document.addEventListener('mouseup', disarmDrag);
        document.addEventListener('dragend', disarmDrag);
    }

    /** Sólo repinta el tiempo de las instancias vivas: un render completo cada
     *  segundo rompería el hover y podría desviar clics. */
    function tickRunning() {
        if (!state.running || !state.running.length) return;
        for (const instance of state.running) instance.uptime += 1;
    }

    /** Rellena con SVG los botones que el HTML deja marcados con data-icon. */
    function paintIcons() {
        for (const node of document.querySelectorAll('[data-icon]')) {
            const name = node.dataset.icon;
            if (ICONS[name]) node.innerHTML = ICONS[name];
        }
        $('open-settings').innerHTML = ICONS.gear;
    }

    function start() {
        wire();
        wireTooltip();
        paintIcons();
        renderBrand(null);        // hasta que llegue el estado, la marca por defecto
        makeStarfield();
        $('opt-stars').addEventListener('change', () => {
            state.theme = { ...state.theme, stars: $('opt-stars').checked };
            applyTheme(state.theme);
            call('save_prefs', { theme: state.theme });
        });
        for (const b of document.querySelectorAll('[data-font]')) {
            b.addEventListener('click', () => {
                state.theme = { ...state.theme, font: b.dataset.font };
                applyTheme(state.theme);
                for (const other of document.querySelectorAll('[data-font]')) {
                    other.classList.toggle('active', other === b);
                }
                call('save_prefs', { theme: state.theme });
            });
        }
        $('theme-club').addEventListener('change', () => {
            state.theme = applyTheme({ ...state.theme, club: $('theme-club').value });
            renderThemeControls();
            call('save_prefs', { theme: state.theme });
        });
        for (const id of ['wine-binary', 'wine-prefix']) {
            // Al salir del campo, no en cada tecla: cambiar el prefijo a medio
            // escribir dejaría al ayudante apuntando a un sitio inexistente.
            $(id).addEventListener('change', () => {
                const key = id.replace('-', '_');
                state[key] = $(id).value.trim();
                call('save_prefs', { [key]: state[key] }).then(refresh);
            });
        }
        $('theme-tint').addEventListener('input', () => {
            const value = parseFloat($('theme-tint').value);
            state.theme = { ...state.theme, tint: value };
            applyTheme(state.theme);
            $('tint-value').textContent = Math.round(value * 100) + '%';
        });
        $('theme-tint').addEventListener('change',
            () => call('save_prefs', { theme: state.theme }));
        refresh();
        setInterval(tickRunning, 1000);
    }

    if (window.pywebview && window.pywebview.api) start();
    else window.addEventListener('pywebviewready', start, { once: true });
})();
