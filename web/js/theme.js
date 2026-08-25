/* Trove Accounts Hub — appearance: accent, club themes, fonts, starfield.
 *
 * Every surface in the stylesheet is derived from --accent and --tint, so the
 * whole look follows from the handful of properties set in applyTheme.
 */

(function () {
    'use strict';

    const App = window.App;
    const $ = App.$;
    const el = App.el;

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

    /** Club themes. Each brings its own logo and pins the accent: the colour is
     *  part of the club's identity, so while one is on the accent picker stays
     *  locked (the tint does NOT — that is intensity, not colour, and it stays
     *  the user's).
     *
     *  `name` is not painted next to the logo (see renderBrand); it is the
     *  dropdown label and the logo's hover title. */
    const CLUBS = {
        'mystic-cave': { name: 'Mystic Cave', accent: '#8b5cf6',
                         image: 'img/mystic-cave-hd.png' },
        arsyn:         { name: 'Arsyn', accent: '#a855f7',
                         image: 'img/arsyn.webp' },
        sayro:         { name: 'Sayro', accent: '#b91c1c',
                         image: 'img/sayro.webp' },
    };

    const FONTS = {
        system: '"Segoe UI", system-ui, -apple-system, sans-serif',
        quicksand: 'Quicksand, "Segoe UI", system-ui, sans-serif',
        comfortaa: 'Comfortaa, "Segoe UI", system-ui, sans-serif',
        quantico: 'Quantico, "Segoe UI", system-ui, sans-serif',
    };

    // Slots in the custom-accent row. Once full, the oldest gives up its place:
    // better than disabling the add button and forcing a delete before the user
    // can even try a colour.
    const MAX_CUSTOMS = 8;

    function clubOf(theme) {
        return (theme && CLUBS[theme.club]) ? theme.club : '';
    }
    App.clubOf = clubOf;

    /** The saved colours: six-digit hex only, lowercase, no duplicates.
     *
     *  One list, shared by the three pickers - the accent, an account's name
     *  and a group's colour. It deliberately does NOT filter against any fixed
     *  palette: the three have different ones, so dropping the accent presets
     *  here made colours vanish from the other two. What keeps presets out of
     *  the list is applyTheme, which does not save an accent that is already a
     *  preset of its own row. */
    function normalizeCustoms(list) {
        const seen = [];
        for (const raw of Array.isArray(list) ? list : []) {
            const value = String(raw).toLowerCase();
            if (!/^#[0-9a-f]{6}$/.test(value)) continue;
            if (seen.includes(value)) continue;
            seen.push(value);
        }
        return seen.slice(-MAX_CUSTOMS);
    }

    /** The saved colours, for whoever is drawing a picker. */
    App.savedColours = function () {
        return normalizeCustoms((App.state.theme || {}).customs);
    };

    /** Keep a colour, and persist it. Returns the list as it ended up. */
    App.rememberColour = function (hex) {
        const theme = App.state.theme || {};
        const customs = normalizeCustoms((theme.customs || []).concat([hex]));
        App.state.theme = { ...theme, customs: customs };
        App.call('save_prefs', { theme: App.state.theme });
        return customs;
    };

    /** Forget one. */
    App.forgetColour = function (hex) {
        const theme = App.state.theme || {};
        const customs = normalizeCustoms(theme.customs)
            .filter((c) => c !== String(hex).toLowerCase());
        App.state.theme = { ...theme, customs: customs };
        App.call('save_prefs', { theme: App.state.theme });
        return customs;
    };

    // --- contrast ---------------------------------------------------------

    /** A readable text colour over a given background.
     *
     *  Uses WCAG relative luminance: with light accents (amber, slate) a fixed
     *  white went unreadable on the primary button. That button's real
     *  background is the accent darkened by 12%, so the measurement is taken on
     *  THAT colour and not on the raw accent. */
    function readableOn(hex, mix) {
        const m = /^#?([0-9a-f]{6})$/i.exec(String(hex || ''));
        if (!m) return '#ffffff';
        const n = parseInt(m[1], 16);
        const channels = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((v) => {
            const s = (v / 255) * (mix == null ? 1 : mix);
            return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
        });
        const lum = 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
        // The real contrast ratio is worked out against white and against dark,
        // and the larger wins. With a fixed threshold, greens and cyans stayed
        // white at 2.8:1 — below even the WCAG minimum for bold text (3:1).
        const DARK_LUM = 0.0035;                       // luminance of #0a0c0b
        const vsWhite = 1.05 / (lum + 0.05);
        const vsDark = (lum + 0.05) / (DARK_LUM + 0.05);
        return vsDark > vsWhite ? '#0a0c0b' : '#ffffff';
    }

    /** The accent, lightened just enough to read AS TEXT against the background.
     *
     *  A dark accent — Sayro's red, for one — painted in small text sinks into
     *  the black background: it sits at 2.2:1 where WCAG asks for 4.5:1. Here
     *  the luminance is raised by mixing in the least white that gets there, so
     *  an accent that already read (green, cyan, amber) comes out untouched and
     *  only the one that did not gets changed. */
    function readableAccent(hex) {
        const m = /^#?([0-9a-f]{6})$/i.exec(String(hex || ''));
        if (!m) return 'var(--accent)';
        const n = parseInt(m[1], 16);
        const rgb = [(n >> 16) & 255, (n >> 8) & 255, n & 255];
        // Minimum luminance for 4.5:1 against the background (~0.0035):
        // 0.05 * 4.5 - 0.05.
        const WANTED = 0.1975;
        const lum = (c) => {
            const ch = c.map((v) => {
                const x = v / 255;
                return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4);
            });
            return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2];
        };
        if (lum(rgb) >= WANTED) return '#' + m[1].toLowerCase();
        let lo = 0, hi = 1;
        for (let i = 0; i < 12; i++) {          // bisection: 12 steps is plenty
            const t = (lo + hi) / 2;
            const mixed = rgb.map((v) => v + (255 - v) * t);
            if (lum(mixed) >= WANTED) hi = t; else lo = t;
        }
        const out = rgb.map((v) => Math.round(v + (255 - v) * hi));
        return '#' + out.map((v) => v.toString(16).padStart(2, '0')).join('');
    }


    // --- the window icon --------------------------------------------------

    /** The brand cube, as three quadrilaterals. The same coordinates
     *  `web/img/trove-accounts-hub.svg` carries and `tools/make_icon.py` uses
     *  for the executable's own icon; here they are redrawn live so the icon in
     *  the title bar and on the taskbar wears the accent too. */
    const CUBE_FACES = [
        [[0.00, 1.45], [9.05, 6.10], [0.00, 10.75], [-9.05, 6.10]],
        [[-9.76, 7.35], [-1.29, 12.86], [-0.74, 22.45], [-9.21, 16.94]],
        [[9.76, 7.35], [1.29, 12.86], [0.74, 22.45], [9.21, 16.94]],
    ];
    const CUBE_STROKE = 0.96;
    const CUBE_FILL = 0.86;        // how much of the square the mark takes up

    /** The cube at `size` pixels, in `colour`, as base64 RGBA.
     *
     *  Drawn four times larger and scaled down: at 16 pixels the seams between
     *  the faces are barely a pixel wide, and letting the canvas antialias them
     *  at full size turns them to mush. */
    function cubeIcon(size, colour) {
        const ss = 4;
        const big = size * ss;
        const canvas = document.createElement('canvas');
        canvas.width = canvas.height = big;
        const ctx = canvas.getContext('2d');

        const xs = CUBE_FACES.flat().map((p) => p[0]);
        const ys = CUBE_FACES.flat().map((p) => p[1]);
        const pad = CUBE_STROKE / 2;
        const x0 = Math.min(...xs) - pad, x1 = Math.max(...xs) + pad;
        const y0 = Math.min(...ys) - pad, y1 = Math.max(...ys) + pad;
        const scale = big * CUBE_FILL / Math.max(x1 - x0, y1 - y0);
        const offX = (big - (x1 - x0) * scale) / 2 - x0 * scale;
        const offY = (big - (y1 - y0) * scale) / 2 - y0 * scale;

        ctx.fillStyle = colour;
        ctx.strokeStyle = colour;
        ctx.lineWidth = CUBE_STROKE * scale;
        ctx.lineJoin = 'round';       // the SVG's stroke-linejoin, and what
        ctx.lineCap = 'round';        // makes the corners blunt
        for (const face of CUBE_FACES) {
            ctx.beginPath();
            face.forEach(([x, y], i) => {
                const px = x * scale + offX, py = y * scale + offY;
                if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
            });
            ctx.closePath();
            ctx.fill();
            ctx.stroke();
        }

        const small = document.createElement('canvas');
        small.width = small.height = size;
        const sctx = small.getContext('2d');
        sctx.imageSmoothingQuality = 'high';
        sctx.drawImage(canvas, 0, 0, size, size);

        const bytes = sctx.getImageData(0, 0, size, size).data;
        let binary = '';
        for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
        return btoa(binary);
    }

    // The accent the icon is currently wearing. applyTheme runs on every drag of
    // the tint slider, and rebuilding two icons per frame for a colour that has
    // not moved is work nobody asked for.
    let iconAccent = '';

    /** Repaint the window icon, if the accent actually changed. */
    App.paintWindowIcon = function (accent) {
        if (!accent || accent === iconAccent) return;
        iconAccent = accent;
        // Deliberately not through App.call: that reports failures to the user,
        // and a window icon that did not repaint is not worth a red message.
        // It also stays quiet where there is no Win32 window to hang it on.
        try {
            const frames = { 16: cubeIcon(16, accent), 32: cubeIcon(32, accent) };
            const bridge = App.api();
            if (bridge && bridge.set_window_icon) {
                Promise.resolve(bridge.set_window_icon(frames)).catch(() => {});
            }
        } catch (err) {
            /* no canvas, or no bridge yet: the icon simply stays as it was */
        }
    };

    // --- applying ---------------------------------------------------------

    App.applyTheme = function (theme) {
        const club = clubOf(theme);
        // The user's own accent is kept intact even while a club paints another
        // one: dropping the theme returns to it with nothing lost.
        const own = (theme && theme.accent) || '#22c55e';
        const accent = club ? CLUBS[club].accent : own;
        // A custom accent in use is saved even if the add button was never
        // pressed: it is the one on screen, and losing it when trying the next
        // colour was exactly the problem.
        // The accent in use is kept so it is not lost when trying another, but
        // only when it is not already a swatch in its own fixed row - otherwise
        // the presets would eat every saved slot.
        const isPreset = ACCENTS.some(
            (a) => a.value.toLowerCase() === String(own).toLowerCase());
        const customs = normalizeCustoms(
            (theme && theme.customs || []).concat(isPreset ? [] : [own]));
        const stars = !theme || theme.stars !== false;
        const tint = (theme && typeof theme.tint === 'number') ? theme.tint : 0.45;
        const font = (theme && FONTS[theme.font]) ? theme.font : 'system';
        document.documentElement.style.setProperty('--accent', accent);
        document.documentElement.style.setProperty('--tint', String(tint));
        document.documentElement.style.setProperty('--font', FONTS[font]);
        // 0.88: the primary button paints the accent mixed with black at that %.
        document.documentElement.style.setProperty('--on-accent', readableOn(accent, 0.88));
        // For the accent as written text, not as fill: see readableAccent.
        document.documentElement.style.setProperty('--accent-text', readableAccent(accent));
        document.body.classList.toggle('no-stars', !stars);
        App.renderBrand({ club: club });
        // The title bar and the taskbar wear the accent too. Windows-only; the
        // call is a no-op anywhere else.
        App.paintWindowIcon(accent);
        return { accent: own, stars: stars, tint: tint, font: font, club: club,
                 customs: customs };
    };

    /** The top bar's mark: ONLY an image, the one belonging to the theme in use.
     *
     *  No name beside it and no version. Two of the three club logos are
     *  wordmarks that already carry the name, so writing it separately said it
     *  twice under some themes and once under others. What always names the app
     *  is the status bar, with the same text whichever theme is on.
     *
     *  The own logo goes in as a MASK and not as an <img>, so it takes the
     *  accent colour. */
    App.renderBrand = function (theme) {
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
        lockup.title = App.APP_NAME;
        box.appendChild(lockup);
    };

    /** The starfield as a repeatable background image.
     *
     *  It is drawn ONCE onto a large canvas and kept as a data URI: a small
     *  pattern would read as tiled, and animating real particles would burn CPU
     *  continuously in a window that usually sits in the background. */
    App.makeStarfield = function () {
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
    };

    // --- the settings controls -------------------------------------------

    /** Accent picker in two rows: the fixed palette on top, the colours the
     *  user has saved plus the add button below.
     *
     *  Picking a custom colour saves it: before it lived only in `accent` and
     *  was lost as soon as another one was tried. */
    App.renderThemeControls = function () {
        const theme = App.state.theme || { accent: '#22c55e' };
        const box = $('theme-swatches');
        const customBox = $('theme-customs');
        if (!box || !customBox) return;
        const club = clubOf(theme);
        // With a club on, ITS colour is shown rather than the saved one: what
        // the user sees is what the interface is wearing.
        const accent = String(club ? CLUBS[club].accent : theme.accent).toLowerCase();
        const customs = normalizeCustoms(theme.customs);
        box.classList.toggle('locked', !!club);
        customBox.classList.toggle('locked', !!club);
        $('accent-locked-note').classList.toggle('hidden', !club);

        /** Apply an accent and persist it. `customs` always travels inside the
         *  theme, so a saved colour is not lost when the accent changes. */
        const pick = (value) => {
            App.state.theme = App.applyTheme({ ...App.state.theme, accent: value });
            App.renderThemeControls();
            App.call('save_prefs', { theme: App.state.theme });
        };

        const swatch = (value, title) => {
            const dot = document.createElement('button');
            dot.type = 'button';
            dot.className = 'swatch' + (value.toLowerCase() === accent ? ' active' : '');
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
            // The × lives INSIDE the button (a <span>, not another button:
            // nesting buttons is not valid HTML) and stops propagation so that
            // forgetting does not also mean applying.
            const forget = el('span', 'swatch-x', '&times;');
            forget.addEventListener('click', (e) => {
                e.stopPropagation();
                // The accent in use is never forgotten: it saves itself while
                // it is on (see applyTheme), so the × there would do nothing.
                if (club || value.toLowerCase() === accent) return;
                App.state.theme = App.applyTheme({
                    ...App.state.theme,
                    customs: customs.filter((c) => c !== value),
                });
                App.renderThemeControls();
                App.call('save_prefs', { theme: App.state.theme });
            });
            dot.appendChild(forget);
            customBox.appendChild(dot);
        }

        // Add button: a dotted slot with a "+" and, invisible on top of it, the
        // native colour input, which is what opens the picker dialog.
        const adder = el('label', 'swatch-add', '<span>+</span>');
        adder.title = customs.length >= MAX_CUSTOMS
            ? `Saves a new colour and forgets the oldest (${MAX_CUSTOMS} slots)`
            : 'Save a custom colour';
        const picker = document.createElement('input');
        picker.type = 'color';
        picker.value = /^#[0-9a-f]{6}$/.test(accent) ? accent : '#22c55e';
        picker.disabled = !!club;
        // While moving around the dialog the result shows live; only on accept
        // (change) is it saved, so the row does not fill up with attempts.
        picker.addEventListener('input', () => {
            App.applyTheme({ ...App.state.theme, accent: picker.value });
        });
        picker.addEventListener('change', () => {
            App.state.theme = App.applyTheme({
                ...App.state.theme,
                accent: picker.value,
                customs: customs.concat([picker.value]),
            });
            App.renderThemeControls();
            App.call('save_prefs', { theme: App.state.theme });
        });
        adder.appendChild(picker);
        customBox.appendChild(adder);
    };
})();
