const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../app/static/js/vehicle-visit.js'), 'utf8');

function setup(options = {}) {
    let now = 1000000;
    let interval;
    let overlay = false;
    const listeners = {};
    const buttons = [{}, {}];
    const directions = {};
    const gallery = {};
    const details = { getBoundingClientRect: () => ({ top: 300, bottom: 350 }) };
    const session = new Map(options.session ? [['ma_visit_prompt_seen', '1']] : []);
    const local = new Map(options.dismissed ? [['ma_visit_prompt_dismissed_until', String(now + 10000)]] : []);
    function storage(values) {
        return {
            getItem: key => {
                if (options.storageUnavailable) throw new Error('Unavailable');
                return values.get(key) || null;
            },
            setItem: (key, value) => values.set(key, value),
        };
    }
    function listen(element) {
        element.handlers = {};
        element.addEventListener = (name, callback) => { element.handlers[name] = callback; };
        return element;
    }
    [gallery, directions, ...buttons].forEach(listen);
    const prompt = {
        hidden: true,
        style: {},
        contains: () => false,
        querySelectorAll: () => buttons,
    };
    const document = {
        hidden: false,
        activeElement: { matches: () => false },
        getElementById: id => ({
            'vehicle-visit-prompt': prompt,
            'vehicle-gallery': gallery,
            vehicleTabs: details,
            'visit-prompt-directions': directions,
        })[id],
        querySelector: () => overlay ? {} : null,
        querySelectorAll: selector => selector === 'video' ? [] : [
            { getBoundingClientRect: () => ({ top: 730, height: 70 }) },
            { getBoundingClientRect: () => ({ top: 650, height: 56 }) },
        ],
        addEventListener: (name, callback) => { listeners[name] = callback; },
    };
    const window = {
        innerWidth: options.mobile ? 390 : 1366,
        innerHeight: options.mobile ? 800 : 900,
        scrollY: 0,
        sessionStorage: storage(session),
        localStorage: storage(local),
        setInterval: callback => { interval = callback; return 1; },
        clearInterval: () => { interval = null; },
        addEventListener: () => {},
    };
    vm.runInNewContext(source, { document, window, Date: { now: () => now } });
    return {
        prompt, document, window, local, session, buttons, directions, gallery,
        click(selector = '.js-favorite-btn') {
            listeners.click?.({ target: { closest: value => value.includes(selector) ? {} : null } });
        },
        tick(seconds) {
            for (let elapsed = 0; elapsed < seconds; elapsed++) {
                now += 1000;
                interval?.();
            }
        },
        key(event) { listeners.keydown?.(event); },
        scroll() { listeners.scroll?.(); },
        overlay(value) { overlay = value; },
    };
}

test('waits for both twenty active seconds and vehicle interest', () => {
    const view = setup();
    view.click();
    view.tick(19);
    assert.equal(view.prompt.hidden, true);
    view.tick(1);
    assert.equal(view.prompt.hidden, false);
    assert.equal(view.session.get('ma_visit_prompt_seen'), '1');
    const idle = setup();
    idle.tick(25);
    assert.equal(idle.prompt.hidden, true);
});

test('hidden pages and open overlays do not count toward the delay', () => {
    const view = setup();
    view.click();
    view.document.hidden = true;
    view.tick(25);
    view.document.hidden = false;
    view.overlay(true);
    view.tick(25);
    view.overlay(false);
    view.click();
    view.tick(19);
    assert.equal(view.prompt.hidden, true);
    view.tick(1);
    assert.equal(view.prompt.hidden, false);
    view.overlay(true);
    view.tick(1);
    assert.equal(view.prompt.hidden, true);
});

test('does not interrupt typing or short mobile viewports', () => {
    const view = setup({ mobile: true });
    view.click();
    view.document.activeElement = { matches: () => true };
    view.tick(25);
    assert.equal(view.prompt.hidden, true);
    view.document.activeElement = { matches: () => false };
    view.window.innerHeight = 500;
    view.tick(25);
    assert.equal(view.prompt.hidden, true);
});

test('close, Not now, directions, and Escape suppress the invitation for a week', () => {
    for (const action of ['close', 'later', 'directions', 'escape']) {
        const view = setup();
        view.click();
        view.tick(20);
        if (action === 'escape') view.key({ key: 'Escape', target: { closest: () => null } });
        else if (action === 'directions') view.directions.handlers.click();
        else view.buttons[action === 'close' ? 0 : 1].handlers.click();
        assert.equal(view.prompt.hidden, true);
        assert.equal(Number(view.local.get('ma_visit_prompt_dismissed_until')), 1020000 + 7 * 86400000);
        view.tick(30);
        assert.equal(view.prompt.hidden, true);
    }
});

test('prior display, dismissal, and unavailable storage suppress repeats', () => {
    for (const options of [{ session: true }, { dismissed: true }, { storageUnavailable: true }]) {
        const view = setup(options);
        view.click();
        view.tick(30);
        assert.equal(view.prompt.hidden, true);
    }
});

test('mobile placement leaves both chat and the fixed action bar unobscured', () => {
    const view = setup({ mobile: true });
    view.click();
    view.tick(20);
    assert.equal(view.prompt.hidden, false);
    assert.equal(view.prompt.style.bottom, '162px');
});

test('scrolling into specifications and swiping photos count as interest', () => {
    const scrollView = setup();
    scrollView.window.scrollY = 500;
    scrollView.scroll();
    scrollView.tick(20);
    assert.equal(scrollView.prompt.hidden, false);
    const swipeView = setup();
    swipeView.gallery.handlers.pointerdown({ clientX: 200 });
    swipeView.gallery.handlers.pointerup({ clientX: 100 });
    swipeView.tick(20);
    assert.equal(swipeView.prompt.hidden, false);
});