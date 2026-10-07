const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const test = require('node:test');

const script = path.resolve(__dirname, '../deploy/ensure_ffmpeg.sh');

function run(files) {
    const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'ensure-ffmpeg-'));
    try {
        for (const [name, source] of Object.entries(files)) {
            fs.writeFileSync(path.join(folder, name), `#!/bin/bash\n${source}\n`, { mode: 0o755 });
        }
        return spawnSync('/bin/bash', [script], {
            env: { ...process.env, PATH: folder }, encoding: 'utf8',
        });
    } finally {
        fs.rmSync(folder, { recursive: true, force: true });
    }
}

test('already available ffmpeg is verified without package installation', () => {
    const result = run({
        ffmpeg: '[[ "$1" == "-version" ]]',
        'apt-get': 'echo "must not run apt" >&2; exit 99',
    });
    assert.equal(result.status, 0);
    assert.match(result.stdout, /ffmpeg is available/);
    assert.doesNotMatch(result.stderr, /must not run apt/);
});

test('broken executable is reported explicitly', () => {
    const result = run({ ffmpeg: 'exit 1' });
    assert.equal(result.status, 1);
    assert.match(result.stderr, /exists but cannot run/);
});

test('unsupported host gets actionable installation instructions', () => {
    const result = run({});
    assert.equal(result.status, 1);
    assert.match(result.stderr, /sudo apt-get update/);
});

test('Ubuntu package installation failures are not reported as success', {
    skip: process.getuid?.() !== 0,
}, () => {
    const result = run({ 'apt-get': 'echo "apt failed" >&2; exit 1' });
    assert.equal(result.status, 1);
    assert.match(result.stderr, /installation failed/);
    assert.doesNotMatch(result.stdout, /installed and verified/);
});
