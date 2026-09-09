/**
 * Client-side vehicle tools: favorites, compare, and recently-viewed.
 * Pure localStorage — no login required. Summaries are fetched from
 * /api/vehicles-summary so we don't duplicate vehicle data in storage.
 */
(function () {
    'use strict';

    var FAVORITES_KEY = 'ma_favorites';
    var COMPARE_KEY = 'ma_compare';
    var RECENT_KEY = 'ma_recently_viewed';
    var COMPARE_MAX = 3;
    var RECENT_MAX = 8;

    function readList(key) {
        try {
            var raw = window.localStorage.getItem(key);
            var parsed = raw ? JSON.parse(raw) : [];
            return Array.isArray(parsed) ? parsed : [];
        } catch (e) {
            return [];
        }
    }

    function writeList(key, list) {
        try {
            window.localStorage.setItem(key, JSON.stringify(list));
        } catch (e) { /* storage unavailable (private mode, quota) — fail silently */ }
    }

    function getFavorites() { return readList(FAVORITES_KEY); }
    function isFavorite(slug) { return getFavorites().indexOf(slug) !== -1; }

    function toggleFavorite(slug) {
        var list = getFavorites();
        var idx = list.indexOf(slug);
        var nowActive;
        if (idx === -1) {
            list.push(slug);
            nowActive = true;
        } else {
            list.splice(idx, 1);
            nowActive = false;
        }
        writeList(FAVORITES_KEY, list);
        updateFavoritesBadge();
        return nowActive;
    }

    function getCompare() { return readList(COMPARE_KEY); }
    function isCompare(slug) { return getCompare().indexOf(slug) !== -1; }

    function toggleCompare(slug) {
        var list = getCompare();
        var idx = list.indexOf(slug);
        var nowActive;
        if (idx === -1) {
            if (list.length >= COMPARE_MAX) {
                window.alert('You can compare up to ' + COMPARE_MAX + ' vehicles at a time. Remove one first.');
                return isCompare(slug);
            }
            list.push(slug);
            nowActive = true;
        } else {
            list.splice(idx, 1);
            nowActive = false;
        }
        writeList(COMPARE_KEY, list);
        updateCompareBar();
        return nowActive;
    }

    function trackRecentlyViewed(entry) {
        if (!entry || !entry.slug) return;
        var list = readList(RECENT_KEY).filter(function (s) { return s !== entry.slug; });
        list.unshift(entry.slug);
        if (list.length > RECENT_MAX) list = list.slice(0, RECENT_MAX);
        writeList(RECENT_KEY, list);
    }

    function getRecentlyViewed(excludeSlug) {
        var list = readList(RECENT_KEY);
        if (excludeSlug) list = list.filter(function (s) { return s !== excludeSlug; });
        return list;
    }

    function fetchSummaries(slugs) {
        if (!slugs || !slugs.length) return Promise.resolve([]);
        var url = '/api/vehicles-summary?slugs=' + encodeURIComponent(slugs.join(','));
        return fetch(url, { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : { vehicles: [] }; })
            .then(function (data) { return data.vehicles || []; })
            .catch(function () { return []; });
    }

    function escapeHtml(str) {
        return String(str == null ? '' : str).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    function vehicleCardHtml(v, opts) {
        opts = opts || {};
        var badge = v.status && v.status !== 'available'
            ? '<span class="badge-condition bg-secondary">' + escapeHtml(v.status) + '</span>'
            : '<span class="badge-condition">' + escapeHtml(v.condition || '') + '</span>';
        var removeBtn = opts.showRemove
            ? '<button type="button" class="favorite-btn is-active js-tools-remove" data-slug="' + escapeHtml(v.slug) + '" aria-label="Remove from saved"><i class="bi bi-heart-fill"></i></button>'
            : '';
        return (
            '<div class="vehicle-card">' +
                removeBtn +
                '<a href="' + v.url + '" class="text-decoration-none text-dark">' +
                    '<div class="card-img-wrapper">' +
                        '<img src="' + v.image + '" class="card-img-top" alt="' + escapeHtml(v.title) + '" width="600" height="400" loading="lazy" decoding="async">' +
                        badge +
                    '</div>' +
                    '<div class="card-body p-4">' +
                        '<h5 class="card-title">' + escapeHtml(v.title) + '</h5>' +
                        '<p class="quick-specs mb-2"><i class="bi bi-speedometer2 me-1"></i> ' + escapeHtml(v.mileage) + '</p>' +
                        '<p class="price mb-0">' + escapeHtml(v.price) + '</p>' +
                    '</div>' +
                '</a>' +
            '</div>'
        );
    }

    function renderListPage(opts) {
        var grid = document.getElementById(opts.gridId);
        var empty = document.getElementById(opts.emptyId);
        if (!grid) return;
        var slugs = opts.slugs || [];
        if (!slugs.length) {
            if (empty) empty.classList.remove('d-none');
            return;
        }
        fetchSummaries(slugs).then(function (vehicles) {
            if (!vehicles.length) {
                if (empty) empty.classList.remove('d-none');
                return;
            }
            grid.innerHTML = vehicles.map(function (v) {
                return vehicleCardHtml(v, opts);
            }).join('');
            if (opts.showRemove) {
                grid.querySelectorAll('.js-tools-remove').forEach(function (btn) {
                    btn.addEventListener('click', function (e) {
                        e.preventDefault();
                        e.stopPropagation();
                        toggleFavorite(btn.getAttribute('data-slug'));
                        renderListPage(opts.slugs ? opts : Object.assign({}, opts, { slugs: getFavorites() }));
                        opts.slugs = getFavorites();
                        renderListPage(opts);
                    });
                });
            }
        });
    }

    var COMPARE_ROWS = [
        { label: 'Price', key: 'price' },
        { label: 'Year', key: 'year' },
        { label: 'Make', key: 'make' },
        { label: 'Model', key: 'model' },
        { label: 'Trim', key: 'trim' },
        { label: 'Mileage', key: 'mileage' },
        { label: 'Body Style', key: 'body_style' },
        { label: 'Fuel Type', key: 'fuel_type' },
        { label: 'Drivetrain', key: 'drivetrain' },
        { label: 'Transmission', key: 'transmission' },
        { label: 'Exterior Color', key: 'exterior_color' },
        { label: 'Condition', key: 'condition' },
        { label: 'Title Status', key: 'title_status' },
    ];

    function renderComparePage(opts) {
        var table = document.getElementById(opts.tableId);
        var tbody = document.getElementById(opts.tableBodyId);
        var empty = document.getElementById(opts.emptyId);
        var slugs = getCompare();
        if (!slugs.length) {
            if (empty) empty.classList.remove('d-none');
            return;
        }
        fetchSummaries(slugs).then(function (vehicles) {
            if (!vehicles.length) {
                if (empty) empty.classList.remove('d-none');
                return;
            }
            var rows = '';
            rows += '<tr><th scope="row">Vehicle</th>' + vehicles.map(function (v) {
                return '<td><img src="' + v.image + '" alt="' + escapeHtml(v.title) + '" style="width:100%;max-width:220px;border-radius:8px;" loading="lazy">' +
                    '<div class="fw-bold mt-2">' + escapeHtml(v.title) + '</div>' +
                    '<a href="' + v.url + '" class="btn btn-sm btn-primary mt-2">View Details</a></td>';
            }).join('') + '</tr>';
            COMPARE_ROWS.forEach(function (row) {
                rows += '<tr><th scope="row">' + row.label + '</th>' + vehicles.map(function (v) {
                    return '<td>' + escapeHtml(v[row.key] || 'N/A') + '</td>';
                }).join('') + '</tr>';
            });
            tbody.innerHTML = rows;
            table.style.display = '';
        });
    }

    function updateFavoritesBadge() {
        var badge = document.getElementById('favorites-count-badge');
        if (!badge) return;
        var count = getFavorites().length;
        badge.textContent = count;
        badge.classList.toggle('d-none', count === 0);
    }

    function updateCompareBar() {
        var bar = document.getElementById('compare-bar');
        if (!bar) return;
        var count = getCompare().length;
        var countEl = document.getElementById('compare-bar-count');
        if (countEl) countEl.textContent = count;
        bar.classList.toggle('d-none', count === 0);
    }

    function bindCardControls() {
        document.querySelectorAll('.js-favorite-btn').forEach(function (btn) {
            var slug = btn.getAttribute('data-slug');
            if (isFavorite(slug)) {
                btn.classList.add('is-active');
                btn.querySelector('i').className = 'bi bi-heart-fill';
            }
            btn.addEventListener('click', function (e) {
                e.preventDefault();
                e.stopPropagation();
                var active = toggleFavorite(slug);
                btn.classList.toggle('is-active', active);
                btn.querySelector('i').className = active ? 'bi bi-heart-fill' : 'bi bi-heart';
            });
        });
        document.querySelectorAll('.js-compare-checkbox').forEach(function (input) {
            var slug = input.getAttribute('data-slug');
            input.checked = isCompare(slug);
            input.addEventListener('click', function (e) { e.stopPropagation(); });
            input.addEventListener('change', function () {
                var active = toggleCompare(slug);
                input.checked = active;
            });
        });
        updateFavoritesBadge();
        updateCompareBar();
    }

    function trackCurrentVehicleView() {
        var el = document.getElementById('site-analytics');
        if (!el || el.getAttribute('data-page-type') !== 'vehicle_detail') return;
        var raw = el.getAttribute('data-vehicle');
        if (!raw) return;
        try {
            var v = JSON.parse(raw);
            if (v && v.slug) trackRecentlyViewed({ slug: v.slug });
        } catch (e) { /* ignore malformed payload */ }
    }

    document.addEventListener('DOMContentLoaded', function () {
        bindCardControls();
        trackCurrentVehicleView();
    });

    var compareClear = document.getElementById('compare-bar-clear');
    if (compareClear) {
        compareClear.addEventListener('click', function () {
            writeList(COMPARE_KEY, []);
            updateCompareBar();
            document.querySelectorAll('.js-compare-checkbox').forEach(function (i) { i.checked = false; });
        });
    }

    window.VehicleTools = {
        getFavorites: getFavorites,
        isFavorite: isFavorite,
        toggleFavorite: toggleFavorite,
        getCompare: getCompare,
        isCompare: isCompare,
        toggleCompare: toggleCompare,
        trackRecentlyViewed: trackRecentlyViewed,
        getRecentlyViewed: getRecentlyViewed,
        fetchSummaries: fetchSummaries,
        renderListPage: renderListPage,
        renderComparePage: renderComparePage,
        vehicleCardHtml: vehicleCardHtml,
    };
})();
