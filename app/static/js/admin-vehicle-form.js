/**
 * Cascading typeahead + VIN decode prefills for admin vehicle add/edit form.
 * Free-text values always allowed; suggestions come from catalog + inventory.
 * VIN decode fills empty fields and merges default features (does not wipe user input).
 */
(function () {
    'use strict';

    const form = document.getElementById('vehicle-form');
    if (!form) return;

    const catalogUrl = form.dataset.catalogUrl;
    const vinDecodeUrl = form.dataset.vinDecodeUrl;
    let catalog = null;
    let activeMenu = null;
    let activeIndex = -1;
    let lastDecodedVin = '';
    let vinDecodeInFlight = false;

    const fields = {
        year: form.querySelector('#year'),
        make: form.querySelector('#make'),
        model: form.querySelector('#model'),
        trim: form.querySelector('#trim'),
        body_style: form.querySelector('#body_style'),
        transmission: form.querySelector('#transmission'),
        drivetrain: form.querySelector('#drivetrain'),
        fuel_type: form.querySelector('#fuel_type'),
        engine: form.querySelector('#engine'),
        exterior_color: form.querySelector('#exterior_color'),
        interior_color: form.querySelector('#interior_color'),
        features: form.querySelector('#features'),
        vin: form.querySelector('#vin'),
    };

    const vinDecodeBtn = form.querySelector('#vin-decode-btn');
    const vinDecodeStatus = form.querySelector('#vin-decode-status');

    function norm(value) {
        return String(value || '').trim().toLowerCase();
    }

    function uniquePreserve(values) {
        const seen = new Set();
        const out = [];
        (values || []).forEach((raw) => {
            const text = String(raw || '').trim();
            if (!text) return;
            const key = text.toLowerCase();
            if (seen.has(key)) return;
            seen.add(key);
            out.push(text);
        });
        return out;
    }

    function filterPrefix(values, query, limit) {
        const items = uniquePreserve(values);
        const q = norm(query);
        if (!q) return items.slice(0, limit);
        const starts = items.filter((v) => v.toLowerCase().startsWith(q));
        const contains = items.filter(
            (v) => v.toLowerCase().includes(q) && !starts.includes(v)
        );
        return starts.concat(contains).slice(0, limit);
    }

    function modelsForMake(make) {
        if (!catalog) return [];
        const map = catalog.make_models || {};
        if (map[make]) return map[make];
        const key = Object.keys(map).find((k) => k.toLowerCase() === norm(make));
        return key ? map[key] : [];
    }

    function trimsForMakeModel(make, model) {
        if (!catalog || !make || !model) return [];
        const key = `${norm(make)}|${norm(model)}`;
        return (catalog.model_trims && catalog.model_trims[key]) || [];
    }

    function suggestionsFor(fieldName, query) {
        if (!catalog) return [];
        const limit = 25;
        const make = fields.make ? fields.make.value : '';
        const model = fields.model ? fields.model.value : '';

        switch (fieldName) {
            case 'make':
                return filterPrefix(catalog.makes || [], query, limit);
            case 'model': {
                let values = modelsForMake(make);
                if (!values.length) {
                    values = [];
                    Object.values(catalog.make_models || {}).forEach((list) => {
                        values = values.concat(list);
                    });
                }
                return filterPrefix(values, query, limit);
            }
            case 'trim': {
                let values = trimsForMakeModel(make, model);
                if (!values.length && make) {
                    const prefix = `${norm(make)}|`;
                    Object.keys(catalog.model_trims || {}).forEach((k) => {
                        if (k.startsWith(prefix)) {
                            values = values.concat(catalog.model_trims[k]);
                        }
                    });
                }
                if (!values.length) {
                    Object.values(catalog.model_trims || {}).forEach((list) => {
                        values = values.concat(list);
                    });
                }
                return filterPrefix(values, query, limit);
            }
            case 'body_style':
                return filterPrefix(catalog.body_styles || [], query, limit);
            case 'transmission':
                return filterPrefix(catalog.transmissions || [], query, limit);
            case 'drivetrain':
                return filterPrefix(catalog.drivetrains || [], query, limit);
            case 'fuel_type':
                return filterPrefix(catalog.fuel_types || [], query, limit);
            case 'engine':
                return filterPrefix(catalog.engines || [], query, limit);
            case 'exterior_color':
                return filterPrefix(catalog.exterior_colors || [], query, limit);
            case 'interior_color':
                return filterPrefix(catalog.interior_colors || [], query, limit);
            case 'features': {
                // Suggest for the token currently being typed after the last comma.
                const raw = String(query || '');
                const parts = raw.split(',');
                const current = parts[parts.length - 1].replace(/^\s+/, '');
                return filterPrefix(catalog.features || [], current, limit);
            }
            default:
                return [];
        }
    }

    function closeMenu() {
        if (activeMenu) {
            activeMenu.remove();
            activeMenu = null;
        }
        activeIndex = -1;
        document.querySelectorAll('.typeahead-wrap.is-open').forEach((el) => {
            el.classList.remove('is-open');
        });
    }

    function ensureWrap(input) {
        let wrap = input.closest('.typeahead-wrap');
        if (wrap) return wrap;
        wrap = document.createElement('div');
        wrap.className = 'typeahead-wrap';
        input.parentNode.insertBefore(wrap, input);
        wrap.appendChild(input);
        return wrap;
    }

    function applyFeatureSuggestion(input, value) {
        const raw = input.value || '';
        const parts = raw.split(',');
        const prefix = parts
            .slice(0, -1)
            .map((p) => p.trim())
            .filter(Boolean);
        prefix.push(value);
        input.value = prefix.join(', ') + ', ';
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.focus();
    }

    function selectSuggestion(input, fieldName, value) {
        if (fieldName === 'features') {
            applyFeatureSuggestion(input, value);
        } else {
            input.value = value;
            input.dispatchEvent(new Event('input', { bubbles: true }));
            input.dispatchEvent(new Event('change', { bubbles: true }));
        }
        closeMenu();
        // Move focus forward for faster data entry
        if (fieldName === 'make' && fields.model) fields.model.focus();
        else if (fieldName === 'model' && fields.trim) fields.trim.focus();
    }

    function openMenu(input, fieldName, items) {
        closeMenu();
        if (!items.length) return;

        const wrap = ensureWrap(input);
        wrap.classList.add('is-open');

        const menu = document.createElement('ul');
        menu.className = 'typeahead-menu';
        menu.setAttribute('role', 'listbox');
        menu.id = `${input.id || fieldName}-typeahead`;

        items.forEach((item, idx) => {
            const li = document.createElement('li');
            li.className = 'typeahead-item';
            li.setAttribute('role', 'option');
            li.dataset.index = String(idx);
            li.textContent = item;
            li.addEventListener('mousedown', (e) => {
                e.preventDefault();
                selectSuggestion(input, fieldName, item);
            });
            menu.appendChild(li);
        });

        wrap.appendChild(menu);
        activeMenu = menu;
        activeIndex = -1;
        input.setAttribute('aria-expanded', 'true');
        input.setAttribute('aria-controls', menu.id);
    }

    function highlight(delta) {
        if (!activeMenu) return;
        const items = Array.from(activeMenu.querySelectorAll('.typeahead-item'));
        if (!items.length) return;
        activeIndex = (activeIndex + delta + items.length) % items.length;
        items.forEach((el, i) => {
            el.classList.toggle('is-active', i === activeIndex);
            if (i === activeIndex) {
                el.scrollIntoView({ block: 'nearest' });
            }
        });
    }

    function bindField(input, fieldName) {
        if (!input) return;
        ensureWrap(input);
        input.setAttribute('autocomplete', 'off');
        input.setAttribute('autocapitalize', 'words');
        input.setAttribute('spellcheck', fieldName === 'features' ? 'true' : 'false');
        input.setAttribute('role', 'combobox');
        input.setAttribute('aria-autocomplete', 'list');
        input.setAttribute('aria-expanded', 'false');

        const show = () => {
            if (!catalog) return;
            const q =
                fieldName === 'features'
                    ? input.value
                    : input.value;
            const items = suggestionsFor(fieldName, q);
            if (document.activeElement === input) {
                openMenu(input, fieldName, items);
            }
        };

        input.addEventListener('focus', show);
        input.addEventListener('input', () => {
            show();
            if (fieldName === 'make') {
                // Keep model/trim free-text; just refresh their menus if open
            }
        });
        input.addEventListener('keydown', (e) => {
            if (!activeMenu) {
                if (e.key === 'ArrowDown') {
                    show();
                    e.preventDefault();
                }
                return;
            }
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                highlight(1);
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                highlight(-1);
            } else if (e.key === 'Enter') {
                if (activeIndex >= 0) {
                    e.preventDefault();
                    const el = activeMenu.querySelector(
                        `.typeahead-item[data-index="${activeIndex}"]`
                    );
                    if (el) selectSuggestion(input, fieldName, el.textContent);
                }
            } else if (e.key === 'Escape') {
                e.preventDefault();
                closeMenu();
            } else if (e.key === 'Tab') {
                closeMenu();
            }
        });
        input.addEventListener('blur', () => {
            // Delay so mousedown on item can fire first
            setTimeout(() => {
                if (!wrapContainsFocus(input)) closeMenu();
            }, 120);
        });
    }

    function wrapContainsFocus(input) {
        const wrap = input.closest('.typeahead-wrap');
        return wrap && wrap.contains(document.activeElement);
    }

    function enhanceDrivetrainSelect() {
        // Keep native select; no typeahead needed for 4 fixed options.
        // Catalog still powers other free-text fields.
    }

    function addHint(input, text) {
        if (!input) return;
        const col = input.closest('[class*="col-"]') || input.parentElement;
        if (!col || col.querySelector('.typeahead-hint')) return;
        const hint = document.createElement('div');
        hint.className = 'form-text typeahead-hint';
        hint.textContent = text;
        col.appendChild(hint);
    }

    Object.keys(fields).forEach((name) => {
        if (name === 'drivetrain' || name === 'year' || name === 'vin') return;
        bindField(fields[name], name);
    });
    enhanceDrivetrainSelect();

    addHint(fields.make, 'Suggestions appear as you type — custom values are allowed.');
    addHint(fields.model, 'Filtered by make when a known make is entered.');
    addHint(fields.trim, 'Filtered by make + model when available.');
    addHint(fields.features, 'Type a feature and pick suggestions; separate with commas. VIN decode merges defaults here.');

    document.addEventListener('click', (e) => {
        if (activeMenu && !e.target.closest('.typeahead-wrap')) {
            closeMenu();
        }
    });

    // ---- VIN decode prefills ----

    function setVinStatus(message, kind) {
        if (!vinDecodeStatus) return;
        vinDecodeStatus.textContent = message || '';
        vinDecodeStatus.classList.remove(
            'text-danger',
            'text-success',
            'text-warning',
            'text-muted',
            'text-primary'
        );
        const map = {
            error: 'text-danger',
            success: 'text-success',
            warning: 'text-warning',
            muted: 'text-muted',
            loading: 'text-primary',
        };
        vinDecodeStatus.classList.add(map[kind] || 'text-muted');
    }

    function normalizeVinClient(raw) {
        return String(raw || '')
            .toUpperCase()
            .replace(/[^A-HJ-NPR-Z0-9]/g, '');
    }

    function isEmptyField(el) {
        if (!el) return true;
        return !String(el.value || '').trim();
    }

    function setIfEmpty(el, value) {
        if (!el) return false;
        if (value === null || value === undefined) return false;
        const text = String(value).trim();
        if (!text) return false;
        if (!isEmptyField(el)) return false;
        el.value = text;
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
        el.classList.add('vin-prefilled');
        return true;
    }

    function parseFeatureList(raw) {
        return uniquePreserve(
            String(raw || '')
                .split(',')
                .map((p) => p.trim())
                .filter(Boolean)
        );
    }

    function mergeFeatures(existingRaw, incoming) {
        const merged = uniquePreserve(parseFeatureList(existingRaw).concat(incoming || []));
        return merged.join(', ');
    }

    function applyVinDecode(data, { force } = { force: false }) {
        if (!data || !data.ok) return;
        const vehicle = data.vehicle || {};
        const filled = [];

        const fieldMap = [
            ['year', vehicle.year],
            ['make', vehicle.make],
            ['model', vehicle.model],
            ['trim', vehicle.trim],
            ['body_style', vehicle.body_style],
            ['drivetrain', vehicle.drivetrain],
            ['fuel_type', vehicle.fuel_type],
            ['engine', vehicle.engine],
            ['transmission', vehicle.transmission],
        ];

        fieldMap.forEach(([name, value]) => {
            const el = fields[name];
            if (!el) return;
            if (force && value !== null && value !== undefined && String(value).trim()) {
                // Force only when user clicked Decode and field is empty OR
                // we still respect non-empty user values (never overwrite).
                if (setIfEmpty(el, value)) filled.push(name);
            } else if (setIfEmpty(el, value)) {
                filled.push(name);
            }
        });

        // Features: always merge (never wipe existing chips/text)
        const incomingFeatures = Array.isArray(data.features) ? data.features : [];
        if (fields.features && incomingFeatures.length) {
            const before = parseFeatureList(fields.features.value);
            const next = mergeFeatures(fields.features.value, incomingFeatures);
            if (next !== String(fields.features.value || '').trim()) {
                fields.features.value = next;
                fields.features.dispatchEvent(new Event('input', { bubbles: true }));
                fields.features.classList.add('vin-prefilled');
                const after = parseFeatureList(next);
                const added = after.length - before.length;
                if (added > 0) filled.push(`features (+${added})`);
            }
        }

        // Normalize VIN field to cleaned value from server
        if (fields.vin && data.vin) {
            fields.vin.value = data.vin;
        }

        const warn = (data.warnings && data.warnings.length)
            ? ` Note: ${data.warnings[0]}`
            : '';
        if (filled.length) {
            setVinStatus(
                `Prefill applied: ${filled.join(', ')}. Review before saving.${warn}`,
                warn ? 'warning' : 'success'
            );
        } else {
            setVinStatus(
                `VIN decoded (${vehicle.year || ''} ${vehicle.make || ''} ${vehicle.model || ''}). Fields already had values; features merged if new.${warn}`.replace(/\s+/g, ' ').trim(),
                warn ? 'warning' : 'muted'
            );
        }
    }

    function decodeVin(opts) {
        const options = opts || {};
        if (!vinDecodeUrl || !fields.vin) return;
        if (vinDecodeInFlight) return;

        const vin = normalizeVinClient(fields.vin.value);
        fields.vin.value = vin;

        if (vin.length !== 17) {
            if (options.interactive) {
                setVinStatus('Enter a full 17-character VIN to decode.', 'error');
            }
            return;
        }
        if (!/^[A-HJ-NPR-Z0-9]{17}$/.test(vin)) {
            if (options.interactive) {
                setVinStatus('VIN has invalid characters (I, O, Q are not allowed).', 'error');
            }
            return;
        }
        if (!options.force && vin === lastDecodedVin) {
            return;
        }

        vinDecodeInFlight = true;
        if (vinDecodeBtn) {
            vinDecodeBtn.disabled = true;
            vinDecodeBtn.classList.add('disabled');
        }
        setVinStatus('Decoding VIN via NHTSA…', 'loading');

        const url = `${vinDecodeUrl}?vin=${encodeURIComponent(vin)}`;
        fetch(url, {
            headers: { Accept: 'application/json' },
            credentials: 'same-origin',
        })
            .then(async (r) => {
                let data = null;
                try {
                    data = await r.json();
                } catch (e) {
                    data = null;
                }
                if (!data) {
                    throw new Error('Invalid response from VIN decode.');
                }
                if (!data.ok) {
                    const msg = data.error || 'VIN could not be decoded.';
                    setVinStatus(msg, 'error');
                    return;
                }
                lastDecodedVin = data.vin || vin;
                applyVinDecode(data, { force: !!options.force });
            })
            .catch((err) => {
                setVinStatus(
                    (err && err.message) || 'VIN decode failed. Check connection and try again.',
                    'error'
                );
            })
            .finally(() => {
                vinDecodeInFlight = false;
                if (vinDecodeBtn) {
                    vinDecodeBtn.disabled = false;
                    vinDecodeBtn.classList.remove('disabled');
                }
            });
    }

    if (fields.vin) {
        fields.vin.addEventListener('input', () => {
            const cleaned = normalizeVinClient(fields.vin.value).slice(0, 17);
            if (fields.vin.value !== cleaned) {
                const start = fields.vin.selectionStart;
                fields.vin.value = cleaned;
                if (typeof start === 'number') {
                    fields.vin.setSelectionRange(
                        Math.min(start, cleaned.length),
                        Math.min(start, cleaned.length)
                    );
                }
            }
            if (cleaned !== lastDecodedVin) {
                // Allow re-decode after VIN edits
            }
        });
        fields.vin.addEventListener('blur', () => {
            const vin = normalizeVinClient(fields.vin.value);
            if (vin.length === 17 && vin !== lastDecodedVin) {
                decodeVin({ interactive: false, force: false });
            }
        });
        fields.vin.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                // Don't submit the whole vehicle form on Enter in VIN
                e.preventDefault();
                decodeVin({ interactive: true, force: true });
            }
        });
    }

    if (vinDecodeBtn) {
        vinDecodeBtn.addEventListener('click', (e) => {
            e.preventDefault();
            decodeVin({ interactive: true, force: true });
        });
    }

    // Load catalog
    if (catalogUrl) {
        fetch(catalogUrl, {
            headers: { Accept: 'application/json' },
            credentials: 'same-origin',
        })
            .then((r) => {
                if (!r.ok) throw new Error('catalog load failed');
                return r.json();
            })
            .then((data) => {
                catalog = data;
                form.dataset.catalogReady = '1';
            })
            .catch(() => {
                catalog = {
                    makes: [],
                    make_models: {},
                    model_trims: {},
                    body_styles: [],
                    transmissions: [],
                    drivetrains: [],
                    fuel_types: [],
                    engines: [],
                    exterior_colors: [],
                    interior_colors: [],
                    features: [],
                };
            });
    }
})();
