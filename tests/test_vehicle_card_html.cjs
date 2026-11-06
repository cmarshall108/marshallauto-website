const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../app/static/js/vehicle-tools.js'), 'utf8');

function loadTools() {
    const document = {
        addEventListener() {},
        querySelectorAll: () => [],
        getElementById: () => null,
    };
    const window = { localStorage: { getItem: () => null, setItem() {} } };
    vm.runInNewContext(source, { document, window, fetch: () => Promise.resolve({ ok: true, json: async () => ({ vehicles: [] }) }) });
    return window.VehicleTools;
}

test('client vehicle cards nest low-miles inside vehicle-card-badges', () => {
    const tools = loadTools();
    const html = tools.vehicleCardHtml({
        slug: 'low-mile-kia',
        url: '/inventory/low-mile-kia',
        title: '2016 Kia Forte LX',
        image: '/static/images/vehicle-placeholder.jpg',
        image_alt: '2016 Kia Forte',
        mileage: '27,671 mi',
        price: '$6,999',
        condition: 'used',
        status: 'available',
        title_status: 'rebuilt',
        is_low_mileage: true,
        low_mileage_badge: '27K MILES',
    });
    assert.match(html, /class="card-img-wrapper"/);
    assert.match(html, /class="vehicle-card-badges"/);
    assert.match(html, /class="badge-low-miles"/);
    assert.match(html, /27K MILES/);
    assert.match(html, /Rebuilt Title/);
    // Badge must be inside the absolute badges container, not a bare sibling of the image.
    const wrapper = html.match(/class="card-img-wrapper">([\s\S]*?)<\/div>\s*<div class="card-body/)[1];
    const badgesIdx = wrapper.indexOf('vehicle-card-badges');
    const lowIdx = wrapper.indexOf('badge-low-miles');
    assert.ok(badgesIdx !== -1 && lowIdx !== -1 && lowIdx > badgesIdx);
});
