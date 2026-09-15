/* Trove Accounts Hub - what the card buttons actually do. */

(function () {
    'use strict';

    const App = window.App;

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
