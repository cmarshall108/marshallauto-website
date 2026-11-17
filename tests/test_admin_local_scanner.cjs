const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(
    path.join(__dirname, '../app/static/js/admin-local-scanner.js'),
    'utf8',
);

test('scanner client fetches the local token and sends it with scanner requests', async () => {
    const calls = [];
    const scanResponse = { ok: true, status: 200 };
    const context = {
        fetch: async (url, options = {}) => {
            calls.push({ url, options });
            if (url.endsWith('/token')) {
                return { ok: true, status: 200, json: async () => ({ token: 'per-install-token' }) };
            }
            if (url.endsWith('/printers')) {
                return { ok: true, status: 200, json: async () => ({ printers: ['scanner'] }) };
            }
            return scanResponse;
        },
        window: {},
    };
    vm.runInNewContext(source, context);

    assert.deepEqual(await context.window.LocalScannerAgent.listPrinters(), ['scanner']);
    const response = await context.window.LocalScannerAgent.scan('scanner', 'document');

    assert.equal(response, scanResponse);
    assert.equal(calls[0].url, 'http://127.0.0.1:8765/token');
    assert.equal(calls[1].options.headers.Authorization, 'Bearer per-install-token');
    assert.equal(calls[2].options.headers.Authorization, 'Bearer per-install-token');
    assert.deepEqual(JSON.parse(calls[2].options.body), {
        printer_id: 'scanner',
        purpose: 'document',
    });
});

test('scanner client explains when the helper rejects this website origin', async () => {
    const context = {
        fetch: async () => ({
            ok: false,
            status: 403,
            json: async () => ({ error: 'Origin is not allowed.' }),
        }),
        window: {},
    };
    vm.runInNewContext(source, context);

    await assert.rejects(
        context.window.LocalScannerAgent.listPrinters(),
        /does not allow this website origin/,
    );
});
