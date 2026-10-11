const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const script = fs.readFileSync(path.join(__dirname, '../app/static/js/coming-soon-photos.js'), 'utf8');

function candidate(overrides = {}) {
    return {
        title: 'File:2021 Toyota RAV4 PHV.jpg', index: 1,
        imageinfo: [{
            mime: 'image/jpeg',
            thumburl: 'https://thumb.wikimedia.org/wikipedia/commons/thumb/example.jpg?utm_source=commons',
            descriptionurl: 'https://commons.wikimedia.org/wiki/File:Example.jpg',
            extmetadata: {
                LicenseShortName: { value: 'CC0' },
                License: { value: 'cc0' },
                LicenseUrl: { value: 'http://creativecommons.org/publicdomain/zero/1.0/deed.en' },
                AttributionRequired: { value: 'false' },
                Restrictions: { value: '' },
                ...overrides,
            },
        }],
    };
}

async function run(pages, options = {}) {
    const requests = [];
    const warnings = [];
    const cards = Array.from({ length: options.count || 1 }, () => {
        const image = { src: '/static/images/vehicle-placeholder.jpg', alt: 'Coming soon' };
        const note = {
            textContent: 'Actual vehicle photos coming soon.', children: [],
            appendChild(child) { this.children.push(child); },
        };
        return {
            dataset: { stockYear: '2021', stockMake: 'Toyota', stockModel: 'RAV4' },
            image, note,
            querySelector(selector) { return selector === 'img' ? image : note; },
        };
    });
    let intersect;
    let observed = 0;
    const context = {
        URL, URLSearchParams, AbortController, setTimeout, clearTimeout,
        console: { warn(...args) { warnings.push(args); } },
        document: {
            querySelectorAll() { return cards; },
            createElement() { return {}; },
        },
        window: {},
        Image: class {
            set src(value) {
                if (options.imageError) this.onerror();
                else this.onload();
            }
        },
        async fetch(url, init) {
            requests.push({ url, init });
            if (options.networkError) throw new Error('Offline');
            return {
                ok: !options.httpError, status: options.httpError || 200,
                async json() { return options.apiError ? { error: { code: 'ratelimited' } } : { query: { pages } }; },
            };
        },
    };
    if (options.lazy) {
        context.IntersectionObserver = class {
            constructor(callback) { intersect = callback; }
            observe() { observed++; }
            unobserve() { observed--; }
        };
        context.window.IntersectionObserver = context.IntersectionObserver;
    }
    vm.runInNewContext(script, context);
    if (options.lazy) {
        assert.equal(requests.length, 0);
        assert.equal(observed, cards.length);
        intersect(cards.map(target => ({ target, isIntersecting: false })));
        assert.equal(requests.length, 0);
        intersect(cards.map(target => ({ target, isIntersecting: true })));
        assert.equal(observed, 0);
    }
    await new Promise(resolve => setImmediate(resolve));
    return { cards, requests, warnings };
}

test('verified CC0 match replaces placeholder and displays representative disclaimer and source', async () => {
    const { cards, requests } = await run({ 1: candidate() });
    assert.equal(cards[0].image.src, 'https://thumb.wikimedia.org/wikipedia/commons/thumb/example.jpg');
    assert.match(cards[0].image.alt, /Representative stock photo.*not the actual vehicle/);
    assert.match(cards[0].note.textContent, /Color, trim and equipment may differ/);
    assert.equal(cards[0].note.children[0].textContent, 'CC0 photo source');
    const url = new URL(requests[0].url);
    assert.equal(url.searchParams.get('gsrsearch'), 'intitle:"2021 toyota rav4" hastemplate:"Cc-zero"');
    assert.equal(url.searchParams.get('iiurlwidth'), '600');
    assert.equal(requests[0].init.credentials, 'omit');
    assert.equal(requests[0].init.referrerPolicy, 'no-referrer');
});

test('duplicate cards share one lookup, and offscreen cards wait for intersection', async () => {
    const { cards, requests } = await run({ 1: candidate() }, { count: 2, lazy: true });
    assert.equal(requests.length, 1);
    assert.ok(cards.every(card => card.image.src.startsWith('https://thumb.wikimedia.org/')));
});

test('reject attribution, share-alike, unknown, restricted and inconsistent licensing', async () => {
    const changes = [
        { LicenseShortName: { value: 'CC BY-SA 4.0' } },
        { License: { value: 'cc-by-4.0' } },
        { LicenseUrl: { value: 'https://example.com/cc0' } },
        { AttributionRequired: { value: 'true' } },
        { Restrictions: { value: 'trademarked' } },
        { Restrictions: undefined },
        { LicenseShortName: undefined },
    ];
    for (const change of changes) {
        const { cards } = await run({ 1: candidate(change) });
        assert.match(cards[0].image.src, /vehicle-placeholder/);
        assert.equal(cards[0].note.children.length, 0);
    }
});

test('reject wrong model/year, photo dates, untrusted URLs and unsupported image types', async () => {
    for (const title of [
        'File:2020 Toyota RAV4 2021.jpg', 'File:Toyota RAV4 photographed in 2021.jpg',
        'File:2021 Toyota RAV4Prime.jpg', 'File:2021 Toyota Camry.jpg',
    ]) {
        const page = candidate();
        page.title = title;
        const { cards } = await run({ 1: page });
        assert.match(cards[0].image.src, /vehicle-placeholder/);
    }
    for (const [key, value] of [
        ['thumburl', 'https://evil.example/car.jpg'],
        ['thumburl', 'http://upload.wikimedia.org/car.jpg'],
        ['descriptionurl', 'javascript:alert(1)'],
        ['mime', 'image/svg+xml'],
    ]) {
        const page = candidate();
        page.imageinfo[0][key] = value;
        const { cards } = await run({ 1: page });
        assert.match(cards[0].image.src, /vehicle-placeholder/);
    }
});

test('empty search preserves placeholder, and failure is explicitly reported without broken images', async () => {
    const empty = await run({});
    assert.match(empty.cards[0].image.src, /vehicle-placeholder/);
    assert.equal(empty.warnings.length, 0);
    for (const options of [
        { networkError: true }, { httpError: 429 }, { apiError: true }, { imageError: true },
    ]) {
        const result = await run({ 1: candidate() }, options);
        assert.match(result.cards[0].image.src, /vehicle-placeholder/);
        assert.match(result.cards[0].note.textContent, /Stock photo unavailable/);
        assert.equal(result.warnings.length, 1);
    }
});
