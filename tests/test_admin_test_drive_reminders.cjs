const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const script = fs.readFileSync(path.join(__dirname, '../app/static/js/admin-test-drive-reminders.js'), 'utf8');

function setup(fetch) {
    function element() {
        return {
            children: [], listeners: {}, hidden: false,
            append(...items) { this.children.push(...items); },
            replaceChildren() { this.children = []; },
            addEventListener(name, callback) { this.listeners[name] = callback; },
            remove() { this.removed = true; },
        };
    }
    const root = element();
    root.dataset = { url: '/admin/api/test-drives/reminders', csrf: 'token' };
    const list = element(), error = element();
    let poll;
    const context = {
        document: {
            getElementById(id) {
                return { 'test-drive-reminders': root, 'test-drive-reminder-list': list,
                    'test-drive-reminder-error': error }[id];
            },
            createElement: element,
        },
        window: { setInterval(callback, ms) { poll = callback; assert.equal(ms, 30000); } },
        fetch, FormData, console: { warn() {} },
    };
    vm.runInNewContext(script, context);
    return { list, error, poll };
}

function response(data, ok = true) {
    return { ok, headers: { get: () => 'application/json' }, json: async () => data };
}

test('renders reminders, dismisses them with CSRF, and refreshes on schedule', async () => {
    const calls = [];
    const s = setup(async (url, options) => {
        calls.push({url, options});
        if (options.method === 'POST') return response({success: true});
        return response({reminders: [{id: 4, phase: 'upcoming', customer: 'Pat',
            vehicle: 'Toyota Camry', time: 'Jun 10, 2030 at 10:00 AM ET'}]});
    });
    await new Promise(setImmediate);
    assert.equal(s.list.children.length, 1);
    const alert = s.list.children[0];
    assert.match(alert.children[0].textContent, /Pat.*Toyota Camry/);
    await alert.children[2].listeners.click();
    assert.equal(calls[1].url, '/admin/test-drives/4/reminders/upcoming/dismiss');
    assert.equal(calls[1].options.body.get('csrf_token'), 'token');
    assert.equal(alert.removed, true);
    await s.poll();
    assert.equal(calls.length, 3);
});

test('shows connection and dismissal failures instead of hiding reminders', async () => {
    let fail = true;
    const s = setup(async (url, options) => {
        if (fail || options.method === 'POST') return response({}, false);
        return response({reminders: [{id: 4, phase: 'due', customer: 'Pat', vehicle: 'Toyota', time: '10 AM'}]});
    });
    await new Promise(setImmediate);
    assert.equal(s.error.hidden, false);
    fail = false;
    await s.poll();
    assert.equal(s.error.hidden, true);
    const alert = s.list.children[0];
    await alert.children[2].listeners.click();
    assert.equal(s.error.hidden, false);
    assert.equal(alert.removed, undefined);
    assert.equal(alert.children[2].disabled, false);
});
