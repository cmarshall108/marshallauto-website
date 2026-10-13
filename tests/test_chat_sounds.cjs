const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const script = fs.readFileSync(path.join(__dirname, '../app/static/js/chat-sounds.js'), 'utf8');
const monitor = fs.readFileSync(path.join(__dirname, '../app/static/js/admin-chat-notifications.js'), 'utf8');
const widget = fs.readFileSync(path.join(__dirname, '../app/static/js/chat-widget.js'), 'utf8');

function setup(options = {}) {
    const handlers = {}, windowHandlers = {}, warnings = [], values = new Map();
    const played = [], contexts = [];
    if (options.muted) values.set('marshall-chat-sounds-customer', 'off');
    const button = {
        attributes: {}, listeners: {}, icon: {},
        querySelector() { return this.icon; },
        setAttribute(name, value) { this.attributes[name] = value; },
        addEventListener(name, callback) { this.listeners[name] = callback; },
    };
    const context = {
        window: {
            localStorage: {
                getItem(key) {
                    if (options.storageError) throw new Error('Storage disabled');
                    return values.get(key) || null;
                },
                setItem(key, value) {
                    if (options.storageError) throw new Error('Storage disabled');
                    values.set(key, value);
                },
            },
            addEventListener(name, callback) { windowHandlers[name] = callback; },
        },
        document: {
            addEventListener(name, callback) { handlers[name] = callback; },
        },
        console: { warn(...args) { warnings.push(args); } },
    };
    if (!options.unsupported) context.window.AudioContext = class {
        constructor() {
            if (options.audioError) throw new Error('Audio denied');
            this.state = 'suspended';
            this.currentTime = 10;
            contexts.push(this);
        }
        async resume() { this.state = 'running'; }
        createGain() {
            return {
                gain: { setValueAtTime() {}, linearRampToValueAtTime() {}, exponentialRampToValueAtTime() {} },
                connect() {}, disconnect() {},
            };
        }
        createOscillator() {
            const oscillator = {
                notes: [], frequency: { setValueAtTime(value) { oscillator.notes.push(value); } },
                connect() {}, disconnect() {}, start() { played.push(oscillator); }, stop() {},
            };
            return oscillator;
        }
    };
    vm.runInNewContext(script, context);
    const sounds = context.window.MarshallChatSounds.create(button, 'customer');
    return { sounds, button, handlers, windowHandlers, played, contexts, values, warnings };
}

test('incoming-message sound waits for interaction and never plays initial history or duplicate polls', async () => {
    const s = setup();
    s.sounds.observe('chat', 10);
    s.sounds.observe('chat', 11);
    assert.equal(s.contexts.length, 0);
    assert.equal(s.played.length, 0);
    assert.equal(s.button.attributes['aria-label'], 'Enable sounds');
    assert.equal(s.button.icon.className, 'bi bi-volume-down');
    await s.handlers.click();
    assert.equal(s.button.attributes['aria-label'], 'Sounds on');
    assert.equal(s.button.icon.className, 'bi bi-volume-up');
    assert.match(s.button.title, /Mute sounds for new incoming chat messages/);
    s.sounds.observe('chat', 11);
    assert.equal(s.played.length, 0);
    s.sounds.observe('chat', 12);
    assert.equal(s.played.length, 1);
    assert.deepEqual(s.played[0].notes, [660, 880]);
    s.sounds.observe('chat', 12);
    s.sounds.observe('chat', 9);
    s.sounds.observe('another-chat', 100);
    assert.equal(s.played.length, 1);
    s.played[0].onended();
});

test('mute persists, suppresses incoming sounds, and synchronizes between tabs', async () => {
    const s = setup();
    await s.handlers.keydown();
    s.sounds.observe('chat', 1);
    s.button.listeners.click();
    assert.equal(s.button.attributes['aria-label'], 'Sounds off');
    assert.equal(s.button.icon.className, 'bi bi-volume-mute');
    assert.match(s.button.title, /Turn on sounds for new incoming chat messages/);
    assert.equal(s.values.get('marshall-chat-sounds-customer'), 'off');
    s.sounds.observe('chat', 2);
    assert.equal(s.played.length, 0);
    s.button.listeners.click();
    s.sounds.observe('chat', 3);
    assert.equal(s.played.length, 1);
    s.windowHandlers.storage({ key: 'marshall-chat-sounds-customer', newValue: 'off' });
    s.sounds.observe('chat', 4);
    assert.equal(s.played.length, 1);
    const muted = setup({ muted: true });
    await muted.handlers.click();
    assert.equal(muted.contexts.length, 0);
    assert.equal(muted.button.attributes['aria-label'], 'Sounds off');
});

test('unsupported or rejected audio and unavailable storage do not break chat', async () => {
    const unavailable = setup({ unsupported: true });
    await unavailable.handlers.click();
    assert.equal(unavailable.button.disabled, true);
    assert.equal(unavailable.button.attributes['aria-label'], 'Sounds unavailable');
    const denied = setup({ audioError: true });
    await denied.handlers.click();
    assert.equal(denied.button.attributes['aria-label'], 'Sounds unavailable');
    assert.equal(denied.warnings.length, 1);
    const noStorage = setup({ storageError: true });
    await noStorage.handlers.click();
    noStorage.button.listeners.click();
    assert.equal(noStorage.button.attributes['aria-label'], 'Sounds off');
    assert.equal(noStorage.warnings.length, 2);
});

test('suspended audio does not replay stale messages when re-enabled', async () => {
    const s = setup();
    await s.handlers.click();
    s.sounds.observe('chat', 1);
    s.contexts[0].state = 'suspended';
    s.sounds.observe('chat', 2);
    assert.equal(s.played.length, 0);
    await s.handlers.click();
    s.sounds.observe('chat', 2);
    assert.equal(s.played.length, 0);
    s.sounds.observe('chat', 3);
    assert.equal(s.played.length, 1);
});

function setupMonitor(options = {}) {
    const observations = [], intervals = new Map(), classes = new Set(), attrs = {};
    const elements = new Map(), handlers = {}, windowHandlers = {};
    const warnings = [], posts = [], beacons = [], requests = [], timers = new Map();
    const values = options.values || new Map();
    function element(id) {
        if (!elements.has(id)) elements.set(id, {
            dataset: { csrf: 'csrf-token', userId: '42' }, hidden: true, listeners: {},
            addEventListener(event, callback) { this.listeners[event] = callback; },
        });
        return elements.get(id);
    }
    const action = {
        classList: { toggle(name, active) { if (active) classes.add(name); else classes.delete(name); } },
        setAttribute(name, value) { attrs[name] = value; },
    };
    const s = { next: options.next || { success: true, latest_customer_message_id: 10, unread: 1 } };
    let sequence = 0, timerId = 0;
    const context = {
        window: {
            MarshallChat: {
                nonce: () => 'page-' + (++sequence),
                async post(url, data, csrf) {
                    posts.push({ url, data, csrf });
                    if (s.postError) throw s.postError;
                    if (s.pendingPost) await s.pendingPost;
                    return { success: true };
                },
                async request(url, options) {
                    requests.push({ url, options });
                    if (options) return { success: true };
                    if (s.next instanceof Error) throw s.next;
                    return s.next;
                },
            },
            MarshallChatSounds: { create: (_, role) => {
                assert.equal(role, 'admin');
                return { observe: (channel, id) => observations.push([channel, id]) };
            } },
            sessionStorage: {
                getItem(key) {
                    if (options.storageError) throw new Error('Storage disabled');
                    return values.get(key) ?? null;
                },
                setItem(key, value) {
                    if (options.storageError) throw new Error('Storage disabled');
                    values.set(key, value);
                },
            },
            setInterval(callback, delay) { intervals.set(delay, callback); },
            setTimeout(callback) { timers.set(++timerId, callback); return timerId; },
            clearTimeout(id) { timers.delete(id); },
            addEventListener(event, callback) { windowHandlers[event] = callback; },
        },
        document: {
            visibilityState: 'visible',
            getElementById: element,
            querySelector() { return action; },
            addEventListener(event, callback) { handlers[event] = callback; },
        },
        FormData: class {
            constructor() { this.values = new Map(); }
            set(key, value) { this.values.set(key, value); }
        },
        navigator: {
            sendBeacon(url, data) { beacons.push({ url, data }); return !options.beaconFailure; },
        },
        console: { warn(...args) { warnings.push(args); } },
    };
    if (options.noSounds) delete context.window.MarshallChatSounds;
    vm.runInNewContext(monitor, context);
    handlers.DOMContentLoaded();
    return Object.assign(s, {
        observations, intervals, classes, attrs, element, warnings, posts, beacons,
        requests, values, timers, handlers, windowHandlers, context,
        flush: () => new Promise(resolve => setImmediate(resolve)),
    });
}

test('global admin monitor tracks incoming cursor, unread state, toasts and connection errors', async () => {
    const s = setupMonitor();
    await s.flush();
    const status = s.element('admin-chat-notification-status');
    const toast = s.element('admin-chat-toast');
    assert.match(status.textContent, /1 unread/);
    assert.ok(s.classes.has('chat-has-unread'));
    assert.equal(toast.hidden, false);
    s.element('admin-chat-toast-dismiss').listeners.click();
    assert.equal(toast.hidden, true);
    s.next = { latest_customer_message_id: 10, unread: 0 };
    await s.intervals.get(5000)();
    assert.equal(status.textContent, 'No unread chats');
    assert.equal(toast.hidden, true);
    s.next = { latest_customer_message_id: 12, unread: 1 };
    await s.intervals.get(5000)();
    assert.equal(toast.hidden, false);
    assert.deepEqual(s.observations, [['inbox', 10], ['inbox', 10], ['inbox', 12]]);
    [...s.timers.values()][0]();
    assert.equal(toast.hidden, true);
    await s.intervals.get(5000)();
    assert.equal(toast.hidden, true);
    s.next = new Error('Disconnected');
    await s.intervals.get(5000)();
    assert.equal(status.textContent, 'Chat alerts disconnected');
    assert.equal(s.element('admin-chat-toast-title').textContent, 'Chat connection alert');
    s.element('admin-chat-toast-dismiss').listeners.click();
    await s.intervals.get(5000)();
    assert.equal(toast.hidden, true);
    assert.equal(s.warnings.length, 2);
    s.next = { latest_customer_message_id: 13, unread: 2 };
    await s.intervals.get(5000)();
    assert.equal(s.element('admin-chat-toast-message').textContent, '2 conversations need your attention.');
});

test('automatic presence renews on every page, even hidden, and releases on close with CSRF', async () => {
    const s = setupMonitor({ noSounds: true });
    await s.flush();
    assert.equal(s.posts.length, 1);
    assert.equal(s.posts[0].url, '/admin/api/chat/presence');
    assert.equal(s.posts[0].data.available, true);
    assert.equal(s.posts[0].csrf, 'csrf-token');
    assert.equal(s.element('admin-chat-presence-status').textContent, 'You are available for live chat');
    s.context.document.visibilityState = 'hidden';
    await s.intervals.get(20000)();
    assert.equal(s.posts.length, 2);
    s.windowHandlers.pagehide();
    const data = s.beacons[0].data.values;
    assert.equal(data.get('id'), s.posts[0].data.id);
    assert.equal(data.get('available'), 'false');
    assert.equal(data.get('closing'), 'true');
    assert.equal(data.get('csrf_token'), 'csrf-token');
    await s.intervals.get(20000)();
    assert.equal(s.posts.length, 2);
    const requests = s.requests.length;
    await s.intervals.get(5000)();
    assert.equal(s.requests.length, requests);
    s.windowHandlers.pageshow({ persisted: true });
    await s.flush();
    assert.equal(s.posts.length, 3);
    assert.notEqual(s.posts[2].data.id, s.posts[0].data.id);
});

test('toast cursor survives page navigation without replaying unread history', async () => {
    const first = setupMonitor();
    await first.flush();
    const next = setupMonitor({ values: first.values });
    await next.flush();
    assert.equal(next.element('admin-chat-toast').hidden, true);
    next.next = { latest_customer_message_id: 11, unread: 1 };
    await next.intervals.get(5000)();
    assert.equal(next.element('admin-chat-toast').hidden, false);
    const noUnread = setupMonitor({ next: { latest_customer_message_id: 20, unread: 0 } });
    await noUnread.flush();
    assert.equal(noUnread.element('admin-chat-toast').hidden, true);
});

test('availability failures notify once, retry and recover; disabled storage does not break monitoring', async () => {
    const s = setupMonitor({ storageError: true });
    await s.flush();
    assert.equal(s.warnings.length, 1);
    s.postError = new Error('Offline');
    await s.intervals.get(20000)();
    assert.match(s.element('admin-chat-presence-status').textContent, /disconnected/);
    assert.equal(s.element('admin-chat-toast-title').textContent, 'Chat availability alert');
    s.element('admin-chat-toast-dismiss').listeners.click();
    await s.intervals.get(20000)();
    assert.equal(s.element('admin-chat-toast').hidden, true);
    s.postError = null;
    await s.intervals.get(20000)();
    assert.equal(s.element('admin-chat-presence-status').textContent, 'You are available for live chat');
});

test('failed close beacon uses keepalive and a late heartbeat is released again', async () => {
    const s = setupMonitor({ beaconFailure: true });
    await s.flush();
    let finish;
    s.pendingPost = new Promise(resolve => { finish = resolve; });
    const heartbeat = s.intervals.get(20000)();
    s.windowHandlers.pagehide();
    assert.equal(s.requests.at(-1).options.keepalive, true);
    finish();
    await heartbeat;
    assert.equal(s.beacons.length, 2);
    assert.equal(s.requests.at(-1).options.body.values.get('closing'), 'true');
});

test('customer widget baselines recovered history and notifies only new agent messages, even while hidden', async () => {
    const elements = new Map(), intervals = [], observations = [], rendered = [];
    function element(id) {
        if (elements.has(id)) return elements.get(id);
        const classes = new Set();
        if (['chat-widget-panel', 'chat-widget-success'].includes(id)) classes.add('d-none');
        const item = {
            value: '', listeners: {}, disabled: false,
            classList: {
                add(name) { classes.add(name); }, remove(name) { classes.delete(name); },
                contains(name) { return classes.has(name); },
                toggle(name, active) { if (active) classes.add(name); else classes.delete(name); },
            },
            setAttribute() {}, focus() {}, replaceChildren() {},
            appendChild(child) { child.parentElement = this; },
            addEventListener(event, callback) { this.listeners[event] = callback; },
            querySelector(selector) { return element(id + selector); },
        };
        elements.set(id, item);
        return item;
    }
    let ready, conversation = { id: 'first-chat', closed: false };
    let messages = [{ id: 1, sender: 'customer' }, { id: 2, sender: 'agent' }];
    const context = {
        window: {
            MarshallChat: {
                async request(url) {
                    if (url === '/api/chat/status') return { online: true, conversation };
                    const after = Number(new URL(url, 'https://dealer.example').searchParams.get('after'));
                    return { online: true, conversation, messages: messages.filter(m => m.id > after), has_more: false };
                },
                append(_, rows) { rendered.push(...rows); },
            },
            MarshallChatSounds: { create: () => ({ observe: (id, latest) => observations.push([id, latest]) }) },
            setInterval(callback) { intervals.push(callback); },
        },
        document: {
            visibilityState: 'hidden',
            getElementById: element,
            addEventListener(event, callback) { if (event === 'DOMContentLoaded') ready = callback; },
        },
    };
    vm.runInNewContext(widget, context);
    ready();
    const flush = () => new Promise(resolve => setImmediate(resolve));
    await flush();
    assert.deepEqual(observations, [['first-chat', 2]]);
    assert.equal(element('customer-chat-sounds').parentElement, element('chat-reply-actions'));
    messages.push({ id: 3, sender: 'customer' });
    await intervals[0]();
    await flush();
    assert.deepEqual(observations, [['first-chat', 2]]);
    messages.push({ id: 4, sender: 'agent' }, { id: 5, sender: 'agent' });
    await intervals[0]();
    await flush();
    assert.deepEqual(observations, [['first-chat', 2], ['first-chat', 5]]);
    assert.ok(element('chat-widget-toggle').classList.contains('chat-has-unread'));
    await intervals[0]();
    await flush();
    assert.equal(observations.length, 2);
    conversation = { id: 'second-chat', closed: false };
    messages = [{ id: 6, sender: 'agent' }];
    await intervals[0]();
    await flush();
    assert.deepEqual(observations[2], ['second-chat', 6]);
    assert.equal(rendered.length, 6);
    conversation = null;
    await intervals[0]();
    await flush();
    element('chat-new').listeners.click();
    assert.equal(element('customer-chat-sounds').parentElement, element('chat-initial-actions'));
});
