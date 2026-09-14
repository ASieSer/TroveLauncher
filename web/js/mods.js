/* Trove Accounts Hub - the mods pane.
 *
 * Everything shown about a mod is read out of the .tmod itself by core/mods.py;
 * nothing here asks the network about anything. Turning one off renames it to
 * `.tmod.disabled`, which is what the game's loader honours.
 *
 * Preview images are NOT part of the listing: seventy of them is around nine
 * megabytes of base64, and the list is useful long before any of that arrives.
 * They are fetched right after it, in batches, and each batch paints as it
 * lands.
 */

(function () {
    'use strict';

    const App = window.App;
    const $ = App.$;
    const el = App.el;

    // How many previews travel in one call. Not a limit on how many are
    // fetched - every mod's picture is asked for - just how many share a trip
    // over the bridge, so the window keeps painting while they arrive.
    const BATCH = 15;

    let mods = [];            // what the folder holds, as the backend read it
    let previews = {};        // file name -> data URI, "" when it has none
    let filterText = '';
    // The tag being shown on its own, lowercased; '' is all of them.
    let tagFilter = '';
    // Not tags anyone could write: tags are trimmed, so none is ever empty or
    // wrapped in spaces.
    const UNTAGGED = ' none ';
    const NEWS = ' update ';

    /** The categories a mod can be filed under.
     *
     *  A fixed list, and the spelling here is the spelling shown. What is in
     *  the file is free text typed by whoever packed the mod - "GUI", "gui",
     *  "Utilit" - so the match is made in lower case and anything that is not
     *  on this list, misspelt or simply absent, is Uncategorized. That is the
     *  point of a fixed list: a folder of seventy mods was otherwise offering
     *  a dozen buttons, several of them one mod each and one of them a typo.
     *
     *  They are all shown even at zero. A category that appears only once
     *  something happens to be filed under it is a list that changes shape
     *  under you; this way the row is the same every time you look at it.
     */
    const CATEGORIES = [
        'Allies', 'Banners', 'Boats and Sails', 'Cosmetics', 'Costumes',
        'Dragons', 'GUI', 'Helmets', 'Language', 'Mag Riders',
        'Mounts', 'NPCs', 'Utility', 'Waypoint', 'Wings', 'VFX',
    ];

    /** Tags that mean a category under another name.
     *
     *  Only where the two are plainly the same thing: eight of the mods here
     *  are tagged UI and there is no UI category, only GUI, and filing them
     *  under nothing would have been reporting a tidy folder as an untidy one.
     *  Not a place to be clever - a tag that merely sounds close is the
     *  author's word and stays outside.
     */
    const ALSO_KNOWN_AS = { ui: 'gui' };
    let updates = {};         // file name -> what Mods Hub said about it
    // Bumped whenever the folder is read again; a run with an old number stops.
    let run = 0;

    App.modsCompact = false;

    // --- fetching the pictures ---------------------------------------------

    /** Fetch every preview, a batch at a time, painting each batch as it lands.
     *
     *  This used to fetch only what was on screen, which was the wrong kind of
     *  clever: cards that scrolled into view together queued up together, and
     *  everything past the first batch was dropped on the floor and never asked
     *  for again. Those mods sat there with an empty frame for ever - not even
     *  the "no picture" mark, because nothing ever came back to draw it.
     *
     *  All of them is roughly nine megabytes for seventy mods, which is a
     *  couple of seconds in the background and no bookkeeping at all.
     */
    async function fetchPreviews() {
        if (App.modsCompact) return;      // compact shows none, so asks for none
        const mine = ++run;
        const files = mods.map((m) => m.file)
                          .filter((f) => previews[f] === undefined);
        for (let i = 0; i < files.length; i += BATCH) {
            const slice = files.slice(i, i + BATCH);
            const result = await App.call('mod_previews', slice);
            if (mine !== run) return;     // the folder was read again meanwhile
            if (!result) return;
            Object.assign(previews, result.previews || {});
            for (const file of slice) {
                // A mod the backend said nothing about has no picture. Writing
                // that down is what makes the mark appear instead of a blank.
                if (previews[file] === undefined) previews[file] = '';
                paintPreview(file);
            }
        }
    }

    function paintPreview(file) {
        const holder = document.querySelector(
            `.mod[data-file="${CSS.escape(file)}"] .mod-shot`);
        if (!holder) return;
        const image = previews[file];
        if (image === undefined) return;         // still on its way
        if (!image) {
            // Said, not left blank: an empty frame reads as a picture that is
            // still coming, and a third of the mods here simply do not ship one.
            holder.classList.add('empty');
            holder.innerHTML = App.ICONS.noimage;
            holder.title = 'This mod has no preview image';
            return;
        }
        const img = document.createElement('img');
        img.src = image;
        img.alt = '';
        holder.innerHTML = '';
        holder.appendChild(img);
        holder.classList.add('loaded');
    }

    // --- the list ------------------------------------------------------------

    /** The switch, told what it is. Nothing did this before: the saved setting
     *  painted the list compact but left the control saying it was off. */
    function sayCompact() {
        const knob = $('mods-compact');
        if (knob) knob.setAttribute('aria-checked', String(!!App.modsCompact));
    }
    App.sayCompact = sayCompact;

    /** The categories one mod falls under, lower case, possibly none.
     *
     *  Its tags are free text, comma separated, and matched against the fixed
     *  list above without regard for case. A mod tagged "Vfx,Utility" is in
     *  both; one tagged "Boomeranger" is in neither and lands in Uncategorized
     *  along with the ones that carry no tag at all.
     */
    function tagsOf(mod) {
        const known = new Set(CATEGORIES.map((c) => c.toLowerCase()));
        const out = new Set();
        for (let tag of String(mod.tags || '').split(',')) {
            tag = tag.trim().toLowerCase();
            tag = ALSO_KNOWN_AS[tag] || tag;
            if (known.has(tag)) out.add(tag);     // a Set: UI and GUI on one
        }                                         // mod are still one GUI
        return [...out];
    }

    function matches(mod) {
        if (tagFilter === NEWS) {
            if (!(updates[mod.file] || {}).behind) return false;
        } else if (tagFilter) {
            const mine = tagsOf(mod);
            // Uncategorized is its own choice in the list: with a good part of
            // a folder falling outside the categories, "which ones are filed
            // under nothing" is a real question.
            if (tagFilter === UNTAGGED ? mine.length : !mine.includes(tagFilter)) {
                return false;
            }
        }

        if (!filterText) return true;
        const needle = filterText.toLowerCase();
        return (mod.name + ' ' + mod.author + ' ' + mod.tags)
            .toLowerCase().includes(needle);
    }

    /** Rebuild the row of filter buttons.
     *
     *  The categories are always all of them, in the order they are written at
     *  the top of this file, each with what the folder currently holds. Only
     *  All, Updates and Uncategorized come and go with the contents.
     */
    function fillTags() {
        const picker = $('mods-filters');
        if (!picker) return;
        const count = new Map(CATEGORIES.map((c) => [c.toLowerCase(), 0]));
        let loose = 0;
        for (const mod of mods) {
            const mine = tagsOf(mod);
            if (!mine.length) loose += 1;
            for (const tag of mine) count.set(tag, (count.get(tag) || 0) + 1);
        }

        const waiting = mods.filter((m) => (updates[m.file] || {}).behind).length;
        const offered = [['', 'All', mods.length, '']];
        // Only once there is something behind it: a button that always reads
        // zero is a button you learn to ignore. The categories are different -
        // they are the shape of the list, and that is worth keeping still.
        if (waiting) offered.push([NEWS, 'Updates', waiting, 'news']);
        for (const name of CATEGORIES) {
            offered.push([name.toLowerCase(), name, count.get(name.toLowerCase()), '']);
        }
        offered.push([UNTAGGED, 'Uncategorized', loose, '']);

        if (!offered.some(([value]) => value === tagFilter)) tagFilter = '';

        // The grid fills DOWN its columns - that is what pins it to three rows
        // whatever the count - so the order is dealt out beforehand to put them
        // back in reading order: what lands on the first row, left to right, is
        // the start of the list. Without this the alphabet ran downwards and
        // Utility sat above Waypoint in a column instead of beside it.
        const rows = 3;
        const columns = Math.ceil(offered.length / rows);
        const dealt = [];
        for (let column = 0; column < columns; column++) {
            for (let row = 0; row < rows; row++) {
                const one = offered[row * columns + column];
                if (one) dealt.push(one);
            }
        }

        picker.innerHTML = '';
        for (const [value, label, howMany, kind] of dealt) {
            const chip = el('button', 'chip ' + kind
                            + (value === tagFilter ? ' on' : '')
                            + (howMany ? '' : ' zero'));
            chip.type = 'button';
            // Nothing to show behind it, so nothing to press: it is there to
            // say the category exists and that this folder has none of it.
            chip.disabled = !howMany;
            chip.append(el('span', 'name', label), el('span', 'n', String(howMany)));
            chip.addEventListener('click', () => App.filterModsByTag(
                value === tagFilter ? '' : value));   // pressing it again clears
            picker.appendChild(chip);
        }
    }

    /** Show only what has a newer version waiting. What the update toast does
     *  when it is pressed. */
    App.showModUpdates = function () {
        App.showPane('mods');
        App.filterModsByTag(NEWS);
    };

    function card(mod) {
        const node = el('div', 'mod' + (mod.enabled ? '' : ' off'));
        node.dataset.file = mod.file;

        if (!App.modsCompact) {
            const shot = el('div', 'mod-shot');
            node.appendChild(shot);
        }

        const body = el('div', 'mod-body');
        const name = el('div', 'mod-name');
        name.textContent = mod.name;
        name.title = mod.name;
        const meta = el('div', 'mod-meta');
        const bits = [];
        if (mod.author) bits.push(mod.author);
        if (mod.version) bits.push('v' + mod.version);
        if (mod.tags) bits.push(mod.tags);
        meta.textContent = bits.join(' · ');
        body.append(name, meta);

        // The switch says what the mod IS, and clicking it says what to make it.
        const toggle = el('button', 'switch mod-toggle');
        toggle.type = 'button';
        toggle.setAttribute('role', 'switch');
        toggle.setAttribute('aria-checked', String(mod.enabled));
        toggle.title = mod.enabled ? 'Enabled - click to disable'
                                   : 'Disabled - click to enable';
        toggle.appendChild(el('span', 'knob'));
        toggle.addEventListener('click', () => flip(mod, node));

        // Behind the published version. A link, because updating is still
        // something you do on the mod's own page - this only tells you.
        const news = updates[mod.file];
        if (news && news.behind) {
            // The whole card goes blue, so a folder of seventy shows at a
            // glance which few have something waiting.
            node.classList.add('has-update');
            const row = el('div', 'mod-update-row');

            const version = el('span', 'mod-version');
            version.textContent = `${news.installed_tag} → ${news.latest_tag}`;
            const go = el('button', 'mod-update', App.ICONS.download);
            go.type = 'button';
            go.title = `Update to ${news.latest_tag}`
                + ((news.changelog || '') ? `\n\n${news.changelog.slice(0, 400)}`
                                          : '');
            go.addEventListener('click', () => install(mod, go));

            const page = el('button', 'mod-page', '<span class="globe"></span>');
            page.type = 'button';
            page.title = "Open the mod's page";
            page.addEventListener('click', () => {
                if (news.page_url) App.call('open_page', news.page_url);
            });
            row.append(version, go, page);
            body.appendChild(row);
        }

        // Two mods writing the same file: the game loads one of them and the
        // other silently does nothing. Worth saying before you go looking for
        // why a mod "stopped working".
        if (mod.conflicts && mod.conflicts.length) {
            const warn = el('span', 'mod-clash', App.ICONS.warn);
            warn.title = mod.conflicts.map(
                (c) => `${c.file} - also in ${c.with.join(', ')}`).join('\n');
            body.appendChild(warn);
            node.classList.add('clashes');
        }

        node.append(body, toggle);
        return node;
    }

    async function flip(mod, node) {
        node.classList.add('working');
        const result = await App.call('set_mod_enabled', mod.file, !mod.enabled);
        node.classList.remove('working');
        if (!result || !result.mod) return;
        // Renaming is still one of the ways a mod can be off, so a file that
        // changed name takes its picture with it.
        const before = mod.file;
        if (before !== result.mod.file) {
            previews[result.mod.file] = previews[before];
            delete previews[before];
        }
        // The folder is read again rather than this one entry patched in.
        // Turning a mod on can start a fight with ANOTHER mod, and that one's
        // card has to say so too - which the reply about this one cannot know.
        // Reading all seventy takes about four milliseconds.
        const turnedOn = !mod.enabled;
        await App.loadMods();
        if (turnedOn) warnAboutConflicts(result.mod.file);
    }

    /** Say it out loud when the mod just switched on now fights with another.
     *
     *  The little mark on the card is there for whoever goes looking; this is
     *  for the moment it starts being true, because the whole problem with a
     *  clash is that nothing appears to go wrong - the game loads one of them
     *  and the other quietly does nothing.
     */
    function warnAboutConflicts(file) {
        const mod = mods.find((m) => m.file === file);
        if (!mod || !mod.conflicts || !mod.conflicts.length) return;
        const others = [];
        for (const clash of mod.conflicts) {
            for (const name of clash.with) {
                if (!others.includes(name)) others.push(name);
            }
        }
        const files = mod.conflicts.map((c) => c.file);
        const e = App.escape;
        App.toast(
            `<b>${e(mod.name)}</b> conflicts with `
            + `<b>${others.map(e).join('</b>, <b>')}</b>.<br>`
            + `Both write ${files.map((f) => `<code>${e(f)}</code>`).join(', ')} - `
            + `the game loads one of them and ignores the other.`,
            'warn');
    }

    function render() {
        const list = $('mod-list');
        if (!list) return;
        list.innerHTML = '';
        list.classList.toggle('compact', App.modsCompact);

        fillTags();
        const shown = mods.filter(matches);
        for (const mod of shown) {
            list.appendChild(card(mod));
            // Whatever is already in hand goes straight on; the rest is on its
            // way and paints itself when it lands.
            if (!App.modsCompact) paintPreview(mod.file);
        }

        const on = mods.filter((m) => m.enabled).length;
        const clashing = mods.filter((m) => m.conflicts && m.conflicts.length).length;
        // The conflict count is always there, even at zero. Appearing only when
        // it had something to say made the whole line jump sideways at the very
        // moment you were reading it - the counts are right-aligned, so a new
        // segment shoves everything before it along.
        // Three short lines rather than one long one: stacked beside a block
        // of buttons three rows tall they fill the same height, where the one
        // line left the row lopsided and pushed the buttons narrow.
        //
        // The typed filter adds a fourth. A chosen category does not: it
        // already carries its number on the button that chose it, and saying
        // it twice on one line was the same fact reported by two things.
        $('mod-counts').innerHTML = mods.length
            ? `<span><b>${mods.length}</b> mods</span>`
              + `<span class="live">${on} on</span>`
              + `<span class="${clashing ? 'bad' : 'quiet'}">${clashing} conflicts</span>`
              + (filterText ? `<span><b>${shown.length}</b> shown</span>` : '')
            : '';
        // Only there when it has something to do.
        const waiting = mods.filter((m) => (updates[m.file] || {}).behind).length;
        const all = $('mods-update-all');
        all.classList.toggle('hidden', !waiting);
        if (!all.disabled) all.textContent = `Update all (${waiting})`;

        const empty = $('mods-empty');
        empty.classList.toggle('hidden', !!shown.length);
        if (!shown.length) {
            empty.querySelector('p').textContent = mods.length
                ? 'No mod matches that.'
                : (App.modsDetail || 'No mods in the folder yet.');
        }
    }

    /** Download the newest release over this mod, once the user asks for it.
     *
     *  Everything that makes this safe is on the Python side: the game must be
     *  closed, the download has to match the checksum published with it, and
     *  the file it replaces is kept so this can be undone. */
    async function install(mod, button) {
        if (button) { button.disabled = true; button.classList.add('working'); }
        const result = await App.call('update_mod', mod.file);
        if (!result) { await App.loadMods(); return null; }
        delete updates[mod.file];
        const e = App.escape;
        App.toast(`<b>${e(result.name)}</b> updated `
                  + `${e(result.from)} → <b>${e(result.to)}</b>.<br>`
                  + `The previous file is kept, so this can be put back.`);
        await App.loadMods();
        return result;
    }

    /** Update everything that has something waiting, one after another.
     *
     *  In sequence and not all at once: each one downloads a file and writes it
     *  into the game's folder, and doing that four times over at the same
     *  moment is how you find out which of the four went wrong. */
    App.updateAllMods = async function () {
        const pending = mods.filter((m) => (updates[m.file] || {}).behind);
        if (!pending.length) return;
        const button = $('mods-update-all');
        button.disabled = true;
        let done = 0;
        for (let i = 0; i < pending.length; i++) {
            button.textContent = `Updating ${i + 1} of ${pending.length}…`;
            if (await install(pending[i], null)) done += 1;
        }
        button.disabled = false;
        button.textContent = 'Update all';
        if (done < pending.length) {
            App.toast(`<b>${pending.length - done}</b> of them could not be `
                      + `updated. The rest were.`, 'warn');
        }
    };

    /** Ask Mods Hub about every installed mod. Only ever from the button. */
    App.checkModUpdates = async function () {
        const button = $('mods-updates');
        button.disabled = true;
        button.textContent = 'Checking…';
        const result = await App.call('check_mod_updates');
        button.disabled = false;
        button.textContent = 'Check for updates';
        if (!result) return;
        updates = result.updates || {};
        render();
        const e = App.escape;
        if (result.behind) {
            const names = mods.filter((m) => (updates[m.file] || {}).behind)
                              .map((m) => m.name);
            App.toast(
                `<b>${result.behind}</b> of your mods `
                + `${result.behind === 1 ? 'has' : 'have'} a newer version:<br>`
                + names.map(e).join(', ')
                + `<span class="hint">Click to show just those</span>`,
                'info', 12, App.showModUpdates);
        } else {
            // Said out loud, because "nothing happened" and "nothing to report"
            // look identical otherwise.
            App.toast(`Everything Mods Hub knows about is up to date `
                      + `(<b>${result.known}</b> of ${result.checked} mods).`);
        }
    };

    App.renderMods = render;

    /** Read the folder again. Previews already in hand are kept: the pictures
     *  inside a .tmod do not change without the file changing. */
    App.loadMods = async function () {
        const result = await App.call('list_mods');
        if (!result) return;
        mods = result.mods || [];
        App.modsFolder = result.folder || '';
        App.modsDetail = result.detail || '';
        // The game rewrites its config when it closes, so anything changed here
        // meanwhile would be thrown away without a word.
        if (result.game_running) {
            App.notice('Trove is open: it rewrites its mod list when it closes, '
                       + 'so changes made now may be undone.', 'error');
        }
        render();
        fetchPreviews();
    };

    App.setModsCompact = function (compact) {
        App.modsCompact = !!compact;
        sayCompact();
        render();
        // Coming back out of compact, anything not fetched yet still needs to be.
        fetchPreviews();
    };

    App.filterMods = function (text) {
        filterText = String(text || '').trim();
        render();
    };

    App.filterModsByTag = function (tag) {
        tagFilter = String(tag || '');
        render();
    };
})();
