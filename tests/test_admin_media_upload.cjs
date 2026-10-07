const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../app/static/js/admin-media-upload.js'), 'utf8');

function setup(options = {}) {
    let submit;
    const calls = [];
    const navigations = [];
    const status = {
        textContent: '', append(value) { this.textContent += typeof value === 'string' ? value : value.textContent; },
    };
    const controls = [
        { disabled: false }, { disabled: false },
    ];
    controls.images = { files: options.photos || [] };
    controls.video_file = { files: options.video ? [options.video] : [] };
    controls.remove_video = { checked: !!options.removeVideo };
    const form = {
        elements: controls, action: '/admin/vehicles/new',
        dataset: { mediaFileLimit: String(90 * 1048576 - 65536), isNew: '1' },
        addEventListener(name, callback) { if (name === 'submit') submit = callback; },
    };
    class FormData extends Map {
        constructor(initial) {
            super();
            if (initial) {
                this.set('csrf_token', 'csrf');
                this.set('images', 'bulk files');
                this.set('video_file', 'bulk video');
                this.set('post_to_facebook', 'y');
            }
        }
    }
    const saved = {
        success: true, upload_url: '/admin/vehicles/1/media',
        finish_url: '/admin/vehicles/1/media/finish',
        edit_url: '/admin/vehicles/1/edit', redirect_url: '/admin/vehicles',
    };
    let active = 0;
    let maxActive = 0;
    const fetch = async (url, request) => {
        active++;
        maxActive = Math.max(active, maxActive);
        calls.push({ url, ...request });
        await Promise.resolve();
        active--;
        if (options.failAt === calls.length) {
            return {
                ok: false, status: 520,
                headers: { get: () => 'text/html' },
            };
        }
        if (options.validationError && calls.length === 1) {
            return {
                ok: false, status: 400, headers: { get: () => 'application/json' },
                json: async () => ({ success: false, message: 'Correct the form.', errors: { year: ['Invalid year'] } }),
            };
        }
        return {
            ok: true, status: 200, headers: { get: () => 'application/json' },
            json: async () => calls.length === 1 ? saved : { success: true },
        };
    };
    vm.runInNewContext(source, {
        FormData, fetch,
        document: {
            getElementById: id => id === 'vehicle-form' ? form : status,
            createElement: () => ({}),
        },
        window: { addEventListener() {}, location: { assign: url => navigations.push(url) } },
    });
    return {
        calls, navigations, status, controls, maxActive: () => maxActive,
        submit: async (prevented = false) => {
            const event = { defaultPrevented: prevented, preventDefault() { this.defaultPrevented = true; } };
            await submit(event);
            return event;
        },
    };
}

test('20 photos and video are uploaded sequentially after saving metadata', async () => {
    const photos = Array.from({ length: 20 }, (_, index) => ({ name: `${index}.jpg`, size: 8 * 1048576 }));
    const video = { name: 'video.mov', size: 1024 };
    const view = setup({ photos, video });
    await view.submit();
    assert.equal(view.calls.length, 23);
    assert.equal(view.maxActive(), 1);
    assert.equal(view.calls[0].body.has('images'), false);
    assert.equal(view.calls[0].body.has('video_file'), false);
    for (let index = 0; index < 20; index++) {
        assert.equal(view.calls[index + 1].body.get('images'), photos[index]);
        assert.equal(view.calls[index + 1].body.has('video_file'), false);
    }
    assert.equal(view.calls[21].body.get('video_file'), video);
    assert.equal(view.calls[22].body.get('post_to_facebook'), '1');
    assert.deepEqual(view.navigations, ['/admin/vehicles']);
});

test('oversized individual video is rejected before saving, not a large aggregate photo selection', async () => {
    const view = setup({ video: { name: 'large.mov', size: 100 * 1048576 } });
    await view.submit();
    assert.equal(view.calls.length, 0);
    assert.match(view.status.textContent, /Nothing has been saved/);
});

test('520 stops uploads, preserves saved media, and links to the saved vehicle without replaying', async () => {
    const view = setup({
        photos: [{ name: 'one.jpg', size: 10 }, { name: 'two.jpg', size: 10 }], failAt: 3,
    });
    await view.submit();
    assert.equal(view.calls.length, 3);
    assert.match(view.status.textContent, /HTTP 520/);
    assert.match(view.status.textContent, /1 photo\(s\) were saved/);
    assert.match(view.status.textContent, /Open saved vehicle/);
    assert.equal(view.controls[0].disabled, true);
    assert.equal(view.navigations.length, 0);
});

test('validation errors restore controls without uploading files', async () => {
    const view = setup({ photos: [{ name: 'one.jpg', size: 10 }], validationError: true });
    await view.submit();
    assert.equal(view.calls.length, 1);
    assert.match(view.status.textContent, /year: Invalid year/);
    assert.equal(view.controls[0].disabled, false);
});

test('existing submit guards and no-media submits retain normal behavior', async () => {
    const view = setup();
    assert.equal((await view.submit()).defaultPrevented, false);
    await view.submit(true);
    assert.equal(view.calls.length, 0);
});

test('remove video takes precedence over a selected video file', async () => {
    const view = setup({ video: { name: 'video.mov', size: 10 }, removeVideo: true });
    assert.equal((await view.submit()).defaultPrevented, false);
    assert.equal(view.calls.length, 0);
});
