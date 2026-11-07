const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../app/static/js/vehicle-share.js'), 'utf8');

function setup(navigator) {
    let click;
    const root = { dataset: { title: '2020 Honda Civic' } };
    const input = { value: 'https://example.com/inventory/civic' };
    const button = { hidden: true, addEventListener(name, callback) { click = callback; } };
    const status = { textContent: 'Select and copy the link to share it.' };
    const elements = { 'vehicle-share': root, 'vehicle-share-url': input,
        'vehicle-share-button': button, 'vehicle-share-status': status };
    vm.runInNewContext(source, { navigator, document: { getElementById: id => elements[id] } });
    return { button, status, click: () => click() };
}

test('native sharing uses the canonical listing link', async () => {
    let shared;
    const view = setup({ share: async data => { shared = data; } });
    assert.equal(view.button.hidden, false);
    await view.click();
    assert.equal(shared.title, '2020 Honda Civic');
    assert.equal(shared.url, 'https://example.com/inventory/civic');
});

test('clipboard fallback copies the listing link', async () => {
    let copied;
    const view = setup({ clipboard: { writeText: async value => { copied = value; } } });
    await view.click();
    assert.equal(copied, 'https://example.com/inventory/civic');
    assert.equal(view.status.textContent, 'Link copied.');
});

test('failures leave manual copy instructions and cancellation is quiet', async () => {
    const failed = setup({ clipboard: { writeText: async () => { throw new Error('denied'); } } });
    await failed.click();
    assert.match(failed.status.textContent, /Select and copy/);
    const cancelled = setup({ share: async () => { throw Object.assign(new Error(), { name: 'AbortError' }); } });
    await cancelled.click();
    assert.equal(cancelled.status.textContent, 'Select and copy the link to share it.');
    assert.equal(setup({}).button.hidden, true);
});
