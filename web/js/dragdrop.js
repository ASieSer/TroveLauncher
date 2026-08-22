/* Trove Accounts Hub — dragging accounts and groups around.
 *
 * Two rules learned the hard way, both about HTML5 drag and drop:
 *
 *   * nothing may change the layout inside `dragstart`. The browser is still
 *     deciding whether to start the drag and taking the drag image; moving
 *     things under the cursor at that moment cancels the whole operation. Any
 *     appearance change is deferred by one tick.
 *   * the dragged node itself is moved through the DOM to open the gap, rather
 *     than inserting a placeholder. Inserting one reflowed the grid, which put
 *     a different element under the cursor, which fired another dragover — the
 *     flicker loop.
 */

(function () {
    'use strict';

    const App = window.App;
    const el = App.el;

    App.clearDropMarks = function () {
        for (const node of document.querySelectorAll(
                '.drop-before,.drop-after,.drop-left,.drop-right,.drop-into')) {
            node.classList.remove('drop-before', 'drop-after',
                                  'drop-left', 'drop-right', 'drop-into');
        }
    };

    /** Does the dragged account go AFTER the one under the cursor?
     *
     *  In the grid the order flows left to right, so the X axis decides:
     *  marking above/below would not say where the card is going to land. */
    function dropSide(node, event) {
        const box = node.getBoundingClientRect();
        return { after: event.clientX > box.left + box.width / 2 };
    }

    /** A group's drop zone: invisible until a drag starts, at which point it
     *  appears as a dashed outline. It is what makes dropping into an empty
     *  group possible, where there is no card to aim at. */
    App.dropSlot = function (groupId) {
        const slot = el('div', 'drop-slot');
        slot.addEventListener('dragover', (e) => {
            if (!App.drag || App.drag.kind !== 'account') return;
            e.preventDefault();
            slot.classList.add('over');
            // Dropping here = at the end of this group.
            App.pendingDrop = { email: null, group: groupId, after: true };
        });
        slot.addEventListener('dragleave', () => slot.classList.remove('over'));
        slot.addEventListener('drop', (e) => {
            if (!App.drag || App.drag.kind !== 'account') return;
            e.preventDefault();
            slot.classList.remove('over');
            App.clearDropMarks();
            App.dropAccount(null, groupId, 'group');
        });
        return slot;
    };

    /** A drag is armed from the grip only: with the whole header draggable, a
     *  click with two pixels of travel over a button starts a drag instead of
     *  the action. */
    function armDragFromGrip(node, grip) {
        node.draggable = false;
        // Mark of "this one arms and disarms": the only nodes disarmDrag touches.
        node.dataset.gripDrag = '1';
        if (grip) grip.addEventListener('mousedown', () => { node.draggable = true; });
    }

    /** Disarm ONLY what is armed from a grip (the group headers).
     *
     *  This used to walk every [draggable="true"], and since cards are born
     *  permanently draggable, the first mouseup anywhere in the window left
     *  them inert until the next repaint. */
    App.disarmDrag = function () {
        for (const n of document.querySelectorAll('[data-grip-drag]')) n.draggable = false;
    };

    App.wireAccountDrag = function (row, account) {
        // With no visible grip, the card drags from anywhere on it.
        row.draggable = true;
        row.addEventListener('dragstart', (e) => {
            App.drag = { kind: 'account', id: account.email };
            App.pendingDrop = null;
            e.dataTransfer.effectAllowed = 'move';
            e.dataTransfer.setData('text/plain', account.email);
            // Every appearance change waits for the next tick — see the note at
            // the top of this file.
            setTimeout(() => {
                if (!App.drag) return;
                row.classList.add('dragging');
                document.body.classList.add('dragging-account');
            }, 0);
        });
        row.addEventListener('dragend', () => {
            document.body.classList.remove('dragging-account');
            row.classList.remove('dragging');
            App.drag = null;
            App.pendingDrop = null;
            App.clearDropMarks();
            App.render();   // the DOM was moved by hand: repaint from state
        });
        row.addEventListener('dragover', (e) => {
            if (!App.drag || App.drag.kind !== 'account'
                || App.drag.id === account.email) return;
            e.preventDefault();
            for (const n of document.querySelectorAll('.drop-into')) {
                n.classList.remove('drop-into');
            }
            const after = dropSide(row, e).after;
            // The target is remembered: once the node moves, the element under
            // the cursor may be another one, so the drop uses this and not
            // whatever happens to be underneath at that instant.
            App.pendingDrop = { email: account.email,
                                group: account.group || null, after: after };

            const moving = document.querySelector('.dragging');
            if (!moving || moving === row) return;
            const target = after ? row.nextSibling : row;
            // Re-inserting when it is already in place is what caused the
            // flicker: each move relaid the grid and fired another dragover,
            // which moved it again.
            if (moving !== target && moving.nextSibling !== target) {
                row.parentNode.insertBefore(moving, target);
            }
        });
        row.addEventListener('drop', (e) => {
            if (!App.drag || App.drag.kind !== 'account') return;
            e.preventDefault();
            const d = App.pendingDrop || { email: account.email,
                                           group: account.group || null,
                                           after: dropSide(row, e).after };
            App.clearDropMarks();
            App.dropAccount(d.email, d.group, d.after ? 'after' : 'before');
        });
    };

    App.wireGroupDrag = function (head, group, grip) {
        armDragFromGrip(head, grip);
        head.addEventListener('dragstart', (e) => {
            App.drag = { kind: 'group', id: group.id };
            head.classList.add('dragging');
            e.dataTransfer.effectAllowed = 'move';
            e.dataTransfer.setData('text/plain', group.id);
        });
        head.addEventListener('dragend', () => {
            head.classList.remove('dragging');
            App.drag = null;
            App.clearDropMarks();
        });
        head.addEventListener('dragover', (e) => {
            if (!App.drag) return;
            if (App.drag.kind === 'group' && App.drag.id === group.id) return;
            // If the account already lives in this group the header offers
            // nothing: it is not highlighted, so it does not invite a drop.
            if (App.drag.kind === 'account' && inGroup(App.drag.id, group.id)) return;
            e.preventDefault();
            App.clearDropMarks();
            head.classList.add(App.drag.kind === 'account' ? 'drop-into' : 'drop-before');
        });
        head.addEventListener('drop', (e) => {
            if (!App.drag) return;
            e.preventDefault();
            App.clearDropMarks();
            if (App.drag.kind === 'group') { dropGroup(group.id); return; }
            if (inGroup(App.drag.id, group.id)) return;   // already here: nothing to move
            App.dropAccount(null, group.id, 'group');
        });
    };

    function inGroup(email, groupId) {
        const account = App.state.accounts.find((a) => a.email === email);
        return !!account && (account.group || null) === (groupId || null);
    }

    App.dropAccount = function (targetEmail, groupId, where) {
        const moved = App.state.accounts.find((a) => a.email === App.drag.id);
        if (!moved) return;
        const rest = App.state.accounts.filter((a) => a.email !== moved.email);
        moved.group = groupId;

        let index;
        if (targetEmail) {
            const at = rest.findIndex((a) => a.email === targetEmail);
            index = where === 'after' ? at + 1 : at;
        } else if (where === 'group') {
            // At the end of the target group: sending it to the front made an
            // account dropped on the header jump to first place.
            let last = -1;
            rest.forEach((a, i) => { if ((a.group || null) === groupId) last = i; });
            index = last === -1 ? rest.length : last + 1;
        } else {
            index = rest.length;
        }
        rest.splice(index, 0, moved);
        App.state.accounts = rest;
        App.render();
        App.call('reorder', {
            accounts: App.state.accounts.map(
                (a) => ({ email: a.email, group: a.group || null })),
        });
    };

    function dropGroup(targetId) {
        const moved = App.state.groups.find((g) => g.id === App.drag.id);
        if (!moved) return;
        const rest = App.state.groups.filter((g) => g.id !== moved.id);
        const at = rest.findIndex((g) => g.id === targetId);
        rest.splice(at === -1 ? rest.length : at, 0, moved);
        App.state.groups = rest;
        App.render();
        App.call('reorder', { groups: App.state.groups.map((g) => g.id) });
    }
})();
