/* Trove Accounts Hub — what the card buttons actually do. */

(function () {
    'use strict';

    const App = window.App;

    // Gap between "Launch all" requests (ms). See launchAll.
    const LAUNCH_ALL_STAGGER = 900;

    App.launch = function (account) {
        return App.withPassword(account, 'Password needed', 'Launch', async (password) => {
            account.status = 'launching';
            App.render();
            const result = await App.call('play',
                { email: account.email, password: password });
            if (!result || result.started === false) {
                if (result && result.error) App.notice(result.error, 'error');
                App.refresh();
            }
        });
    };

    App.testLogin = function (account) {
        return App.withPassword(account, 'Test login', 'Test', async (password) => {
            account.status = 'checking';
            App.render();
            const result = await App.call('test_login',
                { email: account.email, password: password });
            if (!result || result.started === false) {
                if (result && result.error) App.notice(result.error, 'error');
                App.refresh();
            }
        });
    };

    /** Launch several accounts, one after another.
     *
     *  Accounts with neither a password nor a session are skipped: each would
     *  open its own dialog and the user would end up under a stack of modals.
     *
     *  The start order is imposed by the backend (see `_spawn_game`), which is
     *  the side that can. Here the requests are merely spaced out, so ten
     *  authentications do not hit Trion in the same instant and the rows can be
     *  seen advancing one at a time instead of all lighting up at once. */
    App.launchAll = async function (list, groupName) {
        const ready = list.filter((a) => a.status !== 'pending');
        const skipped = list.length - ready.length;
        if (!ready.length) {
            App.notice(
                `No account in ${groupName} can launch without typing a password.`,
                'error');
            return;
        }
        if (!window.confirm(
            `Launch ${ready.length} account(s) from ${groupName}?`
            + (skipped ? `\n\n${skipped} skipped: no saved password or session.` : ''))) return;

        for (const account of ready) account.status = 'launching';
        App.render();
        for (const account of ready) {
            await App.call('play', { email: account.email, password: '' });
            await new Promise((done) => setTimeout(done, LAUNCH_ALL_STAGGER));
        }
        if (skipped) App.notice(`${skipped} account(s) skipped: no saved password.`, 'error');
        App.refresh();
    };

    App.stopAccount = async function (account) {
        if (!account.pid) return;
        const result = await App.call('stop', account.pid);
        if (result) App.refresh();
    };
})();
