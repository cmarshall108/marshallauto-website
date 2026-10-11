(function () {
    'use strict';

    const lookups = new Map();
    const licenseUrl = /^https?:\/\/creativecommons\.org\/publicdomain\/zero\/1\.0\/(?:deed(?:\.[a-z-]+)?\/?)?$/i;

    function normalize(value) {
        return String(value || '').normalize('NFKC').toLowerCase()
            .replace(/[^\p{L}\p{N}]+/gu, ' ').trim();
    }

    function safeUrl(value, hosts) {
        if (typeof value !== 'string') return null;
        try {
            const url = new URL(value);
            if (url.protocol !== 'https:' || !hosts.includes(url.hostname) || url.username || url.password) return null;
            url.search = '';
            return url.href;
        } catch (error) {
            return null;
        }
    }

    async function findPhoto(key) {
        const params = new URLSearchParams({
            action: 'query', generator: 'search', gsrnamespace: '6', gsrlimit: '10',
            gsrsearch: 'intitle:"' + key + '" hastemplate:"Cc-zero"',
            prop: 'imageinfo', iiprop: 'url|extmetadata|mime', iiurlwidth: '600',
            format: 'json', origin: '*',
        });
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 8000);
        try {
            const response = await fetch('https://commons.wikimedia.org/w/api.php?' + params, {
                credentials: 'omit', referrerPolicy: 'no-referrer', signal: controller.signal,
            });
            if (!response.ok) throw new Error('Stock photo service returned HTTP ' + response.status);
            const data = await response.json();
            if (data.error) throw new Error('Stock photo service: ' + data.error.code);
            const pages = Object.values((data.query && data.query.pages) || {})
                .sort((a, b) => a.index - b.index);
            for (const page of pages) {
                // A year elsewhere in the filename may be the photo date, not the model year.
                const title = normalize(String(page.title || '').replace(/^File:/, ''));
                if (!title.startsWith(key + ' ')) continue;
                const info = page.imageinfo && page.imageinfo[0];
                if (!info || !['image/jpeg', 'image/png', 'image/webp'].includes(info.mime)) continue;
                const metadata = info.extmetadata || {};
                const value = field => metadata[field] && metadata[field].value;
                if (value('LicenseShortName') !== 'CC0' || value('License') !== 'cc0'
                    || !licenseUrl.test(value('LicenseUrl') || '')
                    || value('AttributionRequired') !== 'false'
                    || typeof value('Restrictions') !== 'string' || value('Restrictions').trim()) continue;
                const image = safeUrl(info.thumburl, ['upload.wikimedia.org', 'thumb.wikimedia.org']);
                const source = safeUrl(info.descriptionurl, ['commons.wikimedia.org']);
                if (image && source) return { image, source };
            }
            return null;
        } finally {
            clearTimeout(timeout);
        }
    }

    function loadImage(url) {
        return new Promise((resolve, reject) => {
            const image = new Image();
            const timeout = setTimeout(() => {
                image.onload = image.onerror = null;
                reject(new Error('Stock photo download timed out'));
            }, 8000);
            image.referrerPolicy = 'no-referrer';
            image.onload = () => {
                clearTimeout(timeout);
                resolve();
            };
            image.onerror = () => {
                clearTimeout(timeout);
                reject(new Error('Stock photo could not be loaded'));
            };
            image.src = url;
        });
    }

    async function populate(card) {
        const key = [card.dataset.stockYear, card.dataset.stockMake, card.dataset.stockModel]
            .map(normalize).join(' ');
        const note = card.querySelector('.stock-photo-note');
        const image = card.querySelector('img');
        try {
            if (!lookups.has(key)) lookups.set(key, findPhoto(key));
            const photo = await lookups.get(key);
            if (!photo) return;
            await loadImage(photo.image);
            image.referrerPolicy = 'no-referrer';
            image.src = photo.image;
            image.alt = 'Representative stock photo of ' + key + '; not the actual vehicle';
            note.textContent = 'Representative photo, not the actual vehicle. Color, trim and equipment may differ. ';
            const source = document.createElement('a');
            source.href = photo.source;
            source.target = '_blank';
            source.rel = 'noopener noreferrer';
            source.textContent = 'CC0 photo source';
            note.appendChild(source);
        } catch (error) {
            console.warn('Coming Soon stock photo unavailable:', error);
            note.textContent = 'Stock photo unavailable. Actual vehicle photos coming soon.';
        }
    }

    const cards = document.querySelectorAll('[data-stock-year][data-stock-make][data-stock-model]');
    if ('IntersectionObserver' in window) {
        const observer = new IntersectionObserver(entries => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    observer.unobserve(entry.target);
                    populate(entry.target);
                }
            });
        }, { rootMargin: '200px' });
        cards.forEach(card => observer.observe(card));
    } else {
        cards.forEach(populate);
    }
})();
