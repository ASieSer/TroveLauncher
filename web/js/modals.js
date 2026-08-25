/* Trove Accounts Hub — the modal dialogs and the small inputs they are made of. */

(function () {
    'use strict';

    const App = window.App;
    const $ = App.$;
    const el = App.el;

    // --- the shell --------------------------------------------------------

    function openModal(title, body, options) {
        options = options || {};
        $('modal-title').textContent = title;
        const container = $('modal-body');
        container.innerHTML = '';
        container.appendChild(body);
        $('modal-confirm').textContent = options.confirmText || 'OK';
        $('modal-cancel').textContent = options.cancelText || 'Cancel';
        $('modal-backdrop').classList.remove('hidden');

        App.modalCleanup = options.onCancel || null;
        $('modal-confirm').onclick = async () => {
            if (options.onConfirm) {
                const keep = await options.onConfirm();
                if (keep === false) return;
            }
            App.closeModal(true);
        };
        const focusable = body.querySelector('input, select');
        if (focusable) setTimeout(() => focusable.focus(), 30);
    }

    App.closeModal = function (confirmed) {
        $('modal-backdrop').classList.add('hidden');
        if (!confirmed && App.modalCleanup) App.modalCleanup();
        App.modalCleanup = null;
        $('modal-confirm').onclick = null;
    };

    // --- inputs -----------------------------------------------------------

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

    /** The colour picker used by the account and group dialogs.
     *
     *  Two rows, the same shape as the accent picker in Settings: the fixed
     *  palette on top, the colours you have saved below, and the add button at
     *  the end of that second row. The saved list is the same one - a colour
     *  kept here shows up for the accent too, and the other way round.
     */
    function swatches(current, onPick) {
        const wrap = el('div', 'swatch-rows');
        const fixed = el('div', 'swatches');
        const saved = el('div', 'swatches customs');
        wrap.append(fixed, saved);

        let chosen = String(current || '').toLowerCase();

        const mark = () => {
            for (const dot of wrap.querySelectorAll('.swatch')) {
                dot.classList.toggle('active',
                    (dot.dataset.colour || '') === chosen);
            }
        };

        const pick = (colour) => {
            chosen = String(colour).toLowerCase();
            mark();
            onPick(colour);
        };

        const dot = (colour, forgettable) => {
            const b = el('button', 'swatch');
            b.type = 'button';
            b.dataset.colour = String(colour).toLowerCase();
            b.style.background = colour;
            b.title = String(colour).toUpperCase();
            b.addEventListener('click', () => pick(colour));
            if (forgettable) {
                // The x sits inside the swatch and stops the click from also
                // applying the colour it is removing.
                const forget = el('span', 'swatch-x', '&times;');
                forget.addEventListener('click', (e) => {
                    e.stopPropagation();
                    App.forgetColour(colour);
                    drawSaved();
                });
                b.appendChild(forget);
            }
            return b;
        };

        for (const colour of PALETTE) fixed.appendChild(dot(colour, false));

        function drawSaved() {
            saved.innerHTML = '';
            for (const colour of App.savedColours()) {
                saved.appendChild(dot(colour, true));
            }

            const adder = el('label', 'swatch-add', '<span>+</span>');
            adder.title = 'Save a colour of your own';
            const picker = document.createElement('input');
            picker.type = 'color';
            picker.value = /^#[0-9a-f]{6}$/.test(chosen) ? chosen : PALETTE[0];
            // While the dialog is open the colour follows live; it is only
            // saved once accepted, so the row does not fill with attempts.
            picker.addEventListener('input', () => pick(picker.value));
            picker.addEventListener('change', () => {
                App.rememberColour(picker.value);
                pick(picker.value);
                drawSaved();
            });
            adder.appendChild(picker);
            saved.appendChild(adder);
            mark();
        }

        drawSaved();
        mark();
        return wrap;
    }

    function groupOptions() {
        return [{ value: '', label: 'Ungrouped' }]
            .concat(App.state.groups.map((g) => ({ value: g.id, label: g.name })));
    }

    const regionOptions = () =>
        ['NA', 'EU', 'PTS'].map((r) => ({ value: r, label: App.SERVER_TEXT[r] }));

    // --- dialogs ----------------------------------------------------------

    App.openAccountModal = function (account) {
        const body = el('div', 'field');
        body.style.gap = '14px';

        const name = textInput('text', account.masked, account.name || '');
        const region = selectInput(regionOptions(), account.region);
        const group = selectInput(groupOptions(), account.group || '');
        const password = textInput('password', account.has_saved_password
            ? 'Saved — type to change it' : 'Not saved');

        let colour = account.color || PALETTE[0];
        const colourField = field('Name colour',
                                  swatches(colour, (c) => { colour = c; }));

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
            App.closeModal(true);
            await App.call('logout', account.email);
            await App.refresh();
            App.notice(`${account.label} signed out.`, 'ok');
        });

        body.append(
            field('Display name', name),
            field('Server', region),
            field('Group', group),
            field('Password', password),
            colourField,
            flaggedLabel, signout,
        );

        openModal('Edit account', body, {
            confirmText: 'Save',
            onConfirm: async () => {
                await App.call('update_account', account.email, {
                    name: name.value.trim(),
                    region: region.value,
                    group: group.value || null,
                    color: colour,
                    flagged: flagged.checked,
                });
                if (password.value) {
                    await App.call('set_password', account.email, password.value);
                }
                await App.refresh();
            },
        });
    };

    App.openAddAccountModal = function () {
        const body = el('div', 'field');
        body.style.gap = '14px';
        const email = textInput('email', 'you@example.com');
        const password = textInput('password', 'Glyph password');
        const name = textInput('text', 'Optional');
        const region = selectInput(regionOptions(), 'EU');
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
                if (!email.value.includes('@')) {
                    App.notice('Enter a valid email address.', 'error');
                    return false;
                }
                const result = await App.call('add_account', {
                    email: email.value.trim(), password: password.value,
                    name: name.value.trim(), region: region.value,
                    group: group.value, remember_password: true,
                });
                if (!result) return false;
                await App.refresh();
            },
        });
    };

    App.openGroupModal = function (group) {
        const body = el('div', 'field');
        body.style.gap = '14px';
        const name = textInput('text', 'Group name', group ? group.name : '');
        let colour = group ? group.color
                           : PALETTE[App.state.groups.length % PALETTE.length];
        body.append(field('Name', name),
                    field('Colour', swatches(colour, (c) => { colour = c; })));

        openModal(group ? 'Edit group' : 'New group', body, {
            confirmText: group ? 'Save' : 'Create',
            onConfirm: async () => {
                if (!name.value.trim()) {
                    App.notice('Give the group a name.', 'error');
                    return false;
                }
                if (group) {
                    await App.call('update_group', group.id,
                                   { name: name.value.trim(), color: colour });
                } else {
                    const created = await App.call('create_group', name.value.trim());
                    if (created && created.group) {
                        await App.call('update_group', created.group.id, { color: colour });
                    }
                }
                await App.refresh();
            },
        });
    };

    App.open2faModal = function (email, label) {
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
                await App.call('submit_2fa', email, code.value.trim());
            },
            onCancel: () => App.call('cancel_2fa', email),
        });
    };

    /** Runs `run(password)`, asking for the password first only when the
     *  account has neither a saved one nor a live session. */
    App.withPassword = function (account, title, confirmText, run) {
        App.clearNotice();
        if (account.status !== 'pending') return run('');

        const body = el('div', 'field');
        body.style.gap = '12px';
        const text = el('p');
        text.textContent =
            `${account.label} has no saved password and no active session.`;
        const password = textInput('password', 'Glyph password');
        body.append(text, field('Password', password));

        openModal(title, body, {
            confirmText: confirmText,
            onConfirm: async () => {
                if (!password.value) return false;
                await run(password.value);
            },
        });
    };
})();
