/* Trove Accounts Hub — icons and the fixed labels that go with them.
 *
 * Icons are inline SVG rather than image files so they inherit `currentColor`:
 * one button style then covers every state (idle, accent, danger) without a
 * second copy of the artwork per colour.
 */

(function () {
    'use strict';

    const App = window.App;

    App.ICONS = {
        caret: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3l5 5-5 5"/></svg>',
        grip: '<svg viewBox="0 0 16 16" fill="currentColor"><circle cx="6" cy="4" r="1.2"/><circle cx="10" cy="4" r="1.2"/><circle cx="6" cy="8" r="1.2"/><circle cx="10" cy="8" r="1.2"/><circle cx="6" cy="12" r="1.2"/><circle cx="10" cy="12" r="1.2"/></svg>',
        gear: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.6a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>',
        trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/></svg>',
        // Test the sign-in. The only icon that is not inline SVG: the artwork
        // comes from web/img/log-in.png and is used as a mask, so it still
        // takes currentColor like the rest (see .mask-icon).
        login: '<span class="mask-icon login"></span>',
        play: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M6.5 4.2v15.6a1 1 0 0 0 1.53.85l12.2-7.8a1 1 0 0 0 0-1.7L8.03 3.35A1 1 0 0 0 6.5 4.2z"/></svg>',
        stop: '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>',
        eye: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1.5 12S5.5 5 12 5s10.5 7 10.5 7-4 7-10.5 7S1.5 12 1.5 12z"/><circle cx="12" cy="12" r="3.2"/></svg>',
        eyeOff: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.6 6.2A8.9 8.9 0 0 1 12 6c6.5 0 10.5 6.6 10.5 6.6a17 17 0 0 1-3.2 3.7M6.2 8A17 17 0 0 0 1.5 12.6S5.5 19 12 19a9.7 9.7 0 0 0 4-.85"/><path d="M9.9 10.5a3.2 3.2 0 0 0 4.3 4.4"/><path d="M3 3l18 18"/></svg>',
        refresh: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-2.64-6.36M21 4v5h-5"/></svg>',
        download: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12m0 0l-4.5-4.5M12 15l4.5-4.5M4 18.5V20a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-1.5"/></svg>',
        wrench: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15.4 3.6a5 5 0 0 0-6.1 6.6L3.7 15.8a2 2 0 0 0 2.8 2.8l5.6-5.6a5 5 0 0 0 6.6-6.1l-2.8 2.8-2.6-.7-.7-2.6z"/><path d="M14.5 14.5l5 5"/></svg>',
        folder: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7.5a2 2 0 0 1 2-2h3.6a2 2 0 0 1 1.5.7l1 1.2H19a2 2 0 0 1 2 2v8.1a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>',
        // A two-arrow loop: it signs itself back in. Deliberately different from
        // the refresh icon (a single arrow) that Maintenance uses.
        relog: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 2.5l3.5 3.5L17 9.5"/><path d="M3.5 11.5v-1.5a4 4 0 0 1 4-4h13"/><path d="M7 21.5L3.5 18 7 14.5"/><path d="M20.5 12.5v1.5a4 4 0 0 1-4 4h-13"/></svg>',
    };

    // Status badge text.
    App.STATUS_TEXT = {
        unknown: 'Idle',
        ready: 'Ready',
        failed: 'Failed',
        pending: 'No password',
        checking: 'Testing',
        launching: 'Logging in',
        running: 'Running',
    };

    App.SERVER_TEXT = { NA: 'NA', EU: 'EU', PTS: 'PTS' };
})();
