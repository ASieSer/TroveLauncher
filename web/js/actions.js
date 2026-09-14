/* Trove Accounts Hub - what the card buttons actually do. */

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
        if (!await App.confirm(
            skipped ? `${skipped} of them are skipped: no saved password or session.`
                    : 'They start one at a time, not all at once.',
            { title: `Launch ${ready.length} account(s) from ${groupName}?`,
              confirmText: 'Launch' })) return;

        for (const account of ready) account.status = 'launching';
        App.render();
        for (const account of ready) {
            await App.call('play', { email: account.email, password: '' });
            await new Promise((done) => setTimeout(done, LAUNCH_ALL_STAGGER));
        }
        if (skipped) App.notice(`${skipped} account(s) skipped: no saved password.`, 'error');
        App.refresh();
    };

    /** Stop a launch or a check that has not finished.
     *
     *  Distinct from stopAccount, which closes a game that is already open.
     *  This one only asks: the thread doing the work is inside a request to
     *  Trion or an update and answers at its next step, so the card stays busy
     *  for a moment after the press. */
    App.cancelLaunch = async function (account) {
        // Said on the card before anything is asked of the other side, so the
        // press is acknowledged at once even though the answer takes a moment.
        App.stopping.add(account.email);
        account.status = 'stopping';
        App.render();
        const result = await App.call('cancel_launch', account.email);
        if (!result) App.stopping.delete(account.email);
        App.refresh();
    };

    App.stopAccount = async function (account) {
        if (!account.pid) return;
        const result = await App.call('stop', account.pid);
        if (result) App.refresh();
    };
})();
