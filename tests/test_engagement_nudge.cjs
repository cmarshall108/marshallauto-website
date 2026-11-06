const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../app/static/js/engagement-nudge.js'), 'utf8');

function setup(options = {}) {
    let now = 1_000_000;
    let interval;
    let timeoutCb;
    const listeners = {};
    const session = new Map(options.session ? [['ma_engage_nudge_seen', '1']] : []);
    const local = new Map(options.dismissed ? [['ma_engage_nudge_dismissed_until', String(now + 10_000)]] : []);
    function storage(values) {
        return {
            getItem: key => {
                if (options.storageUnavailable) throw new Error('Unavailable');
                return values.get(key) || null;
            },
            setItem: (key, value) => values.set(key, value),
        };
    }
    const dismissButtons = [{}, {}];
    const chatBtn = {};
    const callBtn = {};
    [chatBtn, callBtn, ...dismissButtons].forEach(el => {
        el.handlers = {};
        el.addEventListener = (name, cb) => { el.handlers[name] = cb; };
    });
    const nudge = {
        hidden: true,
        style: {},
        setAttribute() {},
        querySelectorAll: () => dismissButtons,
    };
    const chatToggle = {
        classList: {
            values: new Set(),
            add(v) { this.values.add(v); },
            remove(v) { this.values.delete(v); },
        },
        click() { this.clicked = true; },
        getBoundingClientRect: () => ({ top: 700, height: 56 }),
    };
    const body = { getAttribute: () => options.pageType || 'inventory', scrollHeight: 3000 };
    const document = {
        body,
        hidden: false,
        activeElement: { matches: () => false },
        getElementById: id => ({
            'engagement-nudge': nudge,
            'engagement-nudge-title': { textContent: '' },
            'engagement-nudge-chat': chatBtn,
            'engagement-nudge-call': callBtn,
            'chat-widget-toggle': chatToggle,
        })[id],
        querySelector: () => null,
        querySelectorAll: selector => {
            if (selector === 'video') return [];
            if (selector.includes('mobile-cta-bar')) {
                return [
                    { getBoundingClientRect: () => ({ top: 730, height: 70 }) },
                    { getBoundingClientRect: () => ({ top: 650, height: 56 }) },
                ];
            }
            return [];
        },
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
        setTimeout: callback => { timeoutCb = callback; return 1; },
        addEventListener: (name, callback) => { listeners['window:' + name] = callback; },
        dataLayer: [],
    };
    vm.runInNewContext(source, { document, window, Date: { now: () => now } });
    return {
        nudge, document, window, local, session, chatBtn, chatToggle, listeners,
        arm() { timeoutCb?.(); },
        tick(seconds) {
            for (let i = 0; i < seconds; i++) {
                now += 1000;
                interval?.();
            }
        },
        interact() { listeners.click?.({}); },
        exitIntent() {
            listeners.mouseout?.({ clientY: 0, relatedTarget: null, toElement: null });
        },
    };
}

test('idle shoppers see the nudge once after dwell', () => {
    const view = setup();
    view.arm();
    view.interact();
    // Need 45 dwell seconds and 35 idle seconds without further interaction.
    view.tick(34);
    assert.equal(view.nudge.hidden, true);
    view.tick(11);
    assert.equal(view.nudge.hidden, false);
    assert.equal(view.session.get('ma_engage_nudge_seen'), '1');
    assert.ok(view.chatToggle.classList.values.has('chat-attention'));
});

test('exit intent can show the nudge after the page has settled', () => {
    const view = setup();
    view.arm();
    view.tick(13);
    view.exitIntent();
    assert.equal(view.nudge.hidden, false);
});

test('chat action opens the widget and dismisses', () => {
    const view = setup();
    view.arm();
    view.tick(13);
    view.exitIntent();
    view.chatBtn.handlers.click();
    assert.equal(view.nudge.hidden, true);
    assert.equal(view.chatToggle.clicked, true);
});

test('session seen and dismissal suppress repeats', () => {
    for (const options of [{ session: true }, { dismissed: true }, { storageUnavailable: true }]) {
        const view = setup(options);
        view.arm();
        view.tick(60);
        view.exitIntent();
        assert.equal(view.nudge.hidden, true);
    }
});

test('non-shopping pages do not mount the nudge logic', () => {
    // Script returns early before interval if page type is not shopping —
    // body pageType about should leave nudge untouched/hidden forever.
    const view = setup({ pageType: 'about' });
    // arm/timeout may be undefined because script returned early
    view.arm();
    view.tick(80);
    view.exitIntent();
    assert.equal(view.nudge.hidden, true);
});
