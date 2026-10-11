const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../app/static/js/chat-client.js'), 'utf8');

function load(fetch, document = {}) {
    const window = { crypto: { randomUUID: () => '12345678-1234-4234-8234-123456789abc' } };
    vm.runInNewContext(source, { window, fetch, document, Date, Error, Math, Object, JSON });
    return window.MarshallChat;
}

test('chat transport includes credentials, JSON, and CSRF protection', async () => {
    let request;
    const chat = load(async (url, options) => {
        request = { url, options };
        return { ok: true, json: async () => ({ success: true }) };
    });
    await chat.post('/api/chat/messages', { body: 'Hello' }, 'csrf-token');
    assert.equal(request.url, '/api/chat/messages');
    assert.equal(request.options.credentials, 'same-origin');
    assert.equal(request.options.headers.Accept, 'application/json');
    assert.equal(request.options.headers['X-CSRFToken'], 'csrf-token');
    assert.equal(request.options.headers['Content-Type'], 'application/json');
    assert.equal(request.options.body, '{"body":"Hello"}');
    assert.match(chat.nonce(), /^[0-9a-f-]{36}$/);
});

test('chat transport exposes server failures, validation errors, and invalid responses', async () => {
    const denied = load(async () => ({
        ok: false, status: 403, json: async () => ({ success: false, message: 'Access denied' }),
    }));
    await assert.rejects(denied.request('/api/chat/messages'), error => {
        assert.equal(error.status, 403);
        assert.equal(error.message, 'Access denied');
        return true;
    });
    const invalid = load(async () => ({
        ok: false, status: 400,
        json: async () => ({ success: false, errors: { email: ['Invalid email'] } }),
    }));
    await assert.rejects(invalid.request('/contact-submit'), /Invalid email/);
    const unexpected = load(async () => ({
        ok: true, json: async () => { throw new SyntaxError('Not JSON'); },
    }));
    await assert.rejects(unexpected.request('/api/chat/status'), /unexpected response/);
    const network = load(async () => { throw new Error('Disconnected'); });
    await assert.rejects(network.request('/api/chat/status'), /Disconnected/);
});

test('transcripts render customer content as plain text, retain order, and preserve scroll position', () => {
    const document = {
        createElement(tag) {
            return {
                tag, children: [],
                append(...children) { this.children.push(...children); },
                set innerHTML(value) { throw new Error('Chat must not use innerHTML'); },
            };
        },
    };
    const chat = load(async () => {}, document);
    const log = {
        children: [], scrollHeight: 1000, scrollTop: 100, clientHeight: 200,
        appendChild(child) { this.children.push(child); },
    };
    chat.append(log, [
        { id: 1, sender: 'customer', body: '<script>alert(1)</script>\nQuestion',
            created_at: '2026-10-10T12:00:00Z' },
        { id: 2, sender: 'agent', body: 'Hello!', created_at: '2026-10-10T12:01:00Z' },
    ], 'customer');
    assert.equal(log.children.length, 2);
    assert.equal(log.children[0].children[1].textContent, '<script>alert(1)</script>\nQuestion');
    assert.equal(log.children[0].className, 'chat-message chat-message-own');
    assert.equal(log.children[1].children[0].textContent, 'Marshall Auto team');
    assert.equal(log.children[1].children[1].textContent, 'Hello!');
    assert.equal(log.scrollTop, 100);
    log.scrollTop = 790;
    chat.append(log, [{ sender: 'agent', body: 'Another reply', created_at: '2026-10-10T12:02:00Z' }], 'customer');
    assert.equal(log.scrollTop, 1000);
    chat.append(log, [{
        sender: 'customer', body: 'Suspicious content', suspicious: true,
        spam_reasons: '<img onerror=alert(1)>', created_at: '2026-10-10T12:03:00Z',
    }], 'agent');
    const warning = log.children[3].children[3];
    assert.equal(warning.textContent, 'Review: possible spam. <img onerror=alert(1)>');
});
