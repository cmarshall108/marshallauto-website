/* Marshall Auto site analytics + UX helpers */
(function () {
    'use strict';

    const UTM_KEYS = ['utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content', 'gclid', 'fbclid'];
    const UTM_STORAGE_KEY = 'ma_utm';

    function safeParse(json) {
        try {
            return JSON.parse(json);
        } catch (e) {
            return null;
        }
    }

    function getConfig() {
        const el = document.getElementById('site-analytics');
        if (!el) return {};
        return {
            pageType: el.dataset.pageType || 'other',
            businessName: el.dataset.businessName || '',
            businessPhone: el.dataset.businessPhone || '',
            vehicle: safeParse(el.dataset.vehicle || 'null'),
            inventory: safeParse(el.dataset.inventory || 'null'),
            leadValue: parseFloat(el.dataset.leadValue || '1') || 1,
            collectUrl: el.dataset.collectUrl || '/api/analytics/collect',
        };
    }

    function uuidv4() {
        if (window.crypto && typeof window.crypto.randomUUID === 'function') {
            return window.crypto.randomUUID();
        }
        // RFC4122-ish fallback
        return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
            const r = (Math.random() * 16) | 0;
            const v = c === 'x' ? r : (r & 0x3) | 0x8;
            return v.toString(16);
        });
    }

    function storageGet(key) {
        try { return localStorage.getItem(key); } catch (e) { return null; }
    }
    function storageSet(key, val) {
        try { localStorage.setItem(key, val); } catch (e) { /* private mode */ }
    }
    function sessionGet(key) {
        try { return sessionStorage.getItem(key); } catch (e) { return null; }
    }
    function sessionSet(key, val) {
        try { sessionStorage.setItem(key, val); } catch (e) { /* private mode */ }
    }

    function getVisitorId() {
        const KEY = 'ma_vid';
        let id = storageGet(KEY);
        if (!id || id.length < 16) {
            id = uuidv4();
            storageSet(KEY, id);
        }
        return id;
    }

    function getSessionId() {
        const KEY = 'ma_sid';
        let id = sessionGet(KEY);
        if (!id || id.length < 16) {
            id = uuidv4();
            sessionSet(KEY, id);
        }
        return id;
    }

    function getLandingPath() {
        const KEY = 'ma_landing';
        let path = sessionGet(KEY);
        if (!path) {
            path = (window.location.pathname || '/').slice(0, 512);
            sessionSet(KEY, path);
        }
        return path;
    }

    function captureUtmParams() {
        try {
            const params = new URLSearchParams(window.location.search);
            const found = {};
            let hasAny = false;
            UTM_KEYS.forEach((key) => {
                const val = params.get(key);
                if (val) {
                    found[key] = val.slice(0, 255);
                    hasAny = true;
                }
            });
            if (hasAny) {
                sessionStorage.setItem(UTM_STORAGE_KEY, JSON.stringify(found));
            }
        } catch (e) { /* private mode */ }
    }

    function getStoredUtm() {
        try {
            return safeParse(sessionStorage.getItem(UTM_STORAGE_KEY) || 'null') || {};
        } catch (e) {
            return {};
        }
    }

    function appendUtmHiddenFields(form) {
        const utm = getStoredUtm();
        Object.keys(utm).forEach((key) => {
            let input = form.querySelector(`input[name="${key}"]`);
            if (!input) {
                input = document.createElement('input');
                input.type = 'hidden';
                input.name = key;
                form.appendChild(input);
            }
            input.value = utm[key];
        });
    }

    window.dataLayer = window.dataLayer || [];

    const Analytics = {
        config: {},
        pageViewId: null,
        visitorId: null,
        sessionId: null,
        startedAt: 0,
        maxScroll: 0,
        heartbeatTimer: null,
        engagedSent: false,
        scrollMarks: {},
        unloading: false,

        init() {
            this.config = getConfig();
            this.visitorId = getVisitorId();
            this.sessionId = getSessionId();
            this.startedAt = Date.now();
            captureUtmParams();
            this.trackPageContext();
            this.bindCtaClicks();
            this.bindOutboundAndDownloads();
            this.bindInventoryFilters();
            this.trackViewItem();
            this.trackInventorySearch();
            this.startFirstPartyTracking();
        },

        push(payload) {
            try {
                window.dataLayer.push(payload);
            } catch (e) { /* ignore */ }
        },

        gtagEvent(name, params) {
            if (typeof window.gtag === 'function') {
                try {
                    window.gtag('event', name, params || {});
                } catch (e) { /* ignore */ }
            }
        },

        fbqTrack(name, params) {
            if (typeof window.fbq === 'function') {
                try {
                    window.fbq('track', name, params || {});
                } catch (e) { /* ignore */ }
            }
        },

        fbqCustom(name, params) {
            if (typeof window.fbq === 'function') {
                try {
                    window.fbq('trackCustom', name, params || {});
                } catch (e) { /* ignore */ }
            }
        },

        event(name, params) {
            const payload = Object.assign({ event: name }, params || {});
            this.push(payload);
            this.gtagEvent(name, params);
            this.sendFirstPartyEvent(name, params || {});
        },

        /* ---------- First-party admin analytics ---------- */

        identityPayload() {
            return {
                visitor_id: this.visitorId,
                session_id: this.sessionId,
                page_view_id: this.pageViewId || undefined,
            };
        },

        durationSeconds() {
            return Math.max(0, Math.round((Date.now() - this.startedAt) / 1000));
        },

        isEngaged() {
            return this.durationSeconds() >= 15 || this.maxScroll >= 50;
        },

        postCollect(body, useBeacon) {
            const url = this.config.collectUrl || '/api/analytics/collect';
            const json = JSON.stringify(body);
            try {
                if (useBeacon && navigator.sendBeacon) {
                    const blob = new Blob([json], { type: 'application/json' });
                    if (navigator.sendBeacon(url, blob)) return Promise.resolve({ ok: true, beacon: true });
                }
            } catch (e) { /* fall through */ }

            return fetch(url, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                body: json,
                credentials: 'same-origin',
                keepalive: !!useBeacon,
            }).then((res) => res.json().catch(() => ({ ok: res.ok }))).catch(() => ({ ok: false }));
        },

        startFirstPartyTracking() {
            // Skip admin pages if main.js is ever loaded there
            if ((window.location.pathname || '').indexOf('/admin') === 0) return;

            const utm = getStoredUtm();
            const v = this.config.vehicle || {};
            const payload = Object.assign({
                type: 'pageview',
                path: window.location.pathname || '/',
                page_type: this.config.pageType || 'other',
                page_title: (document.title || '').slice(0, 255),
                query_string: (window.location.search || '').replace(/^\?/, '').slice(0, 512),
                referrer: document.referrer || '',
                landing_path: getLandingPath(),
                vehicle_id: v.id || null,
                language: (navigator.language || '').slice(0, 32),
                screen_width: window.screen ? window.screen.width : null,
                screen_height: window.screen ? window.screen.height : null,
                timezone: (function () {
                    try { return Intl.DateTimeFormat().resolvedOptions().timeZone || ''; }
                    catch (e) { return ''; }
                })(),
                user_agent: navigator.userAgent || '',
            }, this.identityPayload(), utm);

            this.postCollect(payload, false).then((res) => {
                if (res && res.page_view_id) {
                    this.pageViewId = res.page_view_id;
                }
            });

            // Scroll depth
            const onScroll = () => {
                const doc = document.documentElement;
                const body = document.body;
                const scrollTop = window.pageYOffset || doc.scrollTop || body.scrollTop || 0;
                const height = Math.max(
                    body.scrollHeight, doc.scrollHeight,
                    body.offsetHeight, doc.offsetHeight,
                    body.clientHeight, doc.clientHeight
                );
                const win = window.innerHeight || doc.clientHeight || 0;
                const denom = Math.max(1, height - win);
                const pct = Math.min(100, Math.round((scrollTop / denom) * 100));
                if (pct > this.maxScroll) this.maxScroll = pct;

                [25, 50, 75, 90, 100].forEach((mark) => {
                    if (this.maxScroll >= mark && !this.scrollMarks[mark]) {
                        this.scrollMarks[mark] = true;
                        this.sendFirstPartyEvent('scroll_depth', {
                            label: String(mark),
                            value: mark,
                            meta: { scroll_depth_pct: mark },
                        });
                    }
                });
            };
            window.addEventListener('scroll', onScroll, { passive: true });
            onScroll();

            // Heartbeat every 15s while tab is visible
            this.heartbeatTimer = window.setInterval(() => {
                if (document.visibilityState === 'hidden') return;
                this.sendEngagementUpdate(false, true);
            }, 15000);

            const flush = () => {
                if (this.unloading) return;
                this.unloading = true;
                this.sendEngagementUpdate(true, false, true);
            };
            window.addEventListener('pagehide', flush);
            window.addEventListener('beforeunload', flush);
            document.addEventListener('visibilitychange', () => {
                if (document.visibilityState === 'hidden') {
                    this.sendEngagementUpdate(false, false, true);
                }
            });
        },

        sendEngagementUpdate(isExit, heartbeat, useBeacon) {
            if (!this.pageViewId && !heartbeat) {
                // Still try exit update if we somehow lack id — skip
                if (!this.pageViewId) return;
            }
            const engaged = this.isEngaged();
            if (engaged && !this.engagedSent) {
                this.engagedSent = true;
                this.sendFirstPartyEvent('engaged_time', {
                    value: this.durationSeconds(),
                    meta: { scroll_depth_pct: this.maxScroll },
                }, useBeacon);
            }
            const body = Object.assign({
                type: 'update',
                duration_seconds: this.durationSeconds(),
                scroll_depth_pct: this.maxScroll,
                is_engaged: engaged,
                is_exit: !!isExit,
                heartbeat: !!heartbeat,
            }, this.identityPayload());
            this.postCollect(body, !!useBeacon);
        },

        sendFirstPartyEvent(name, params, useBeacon) {
            if (!name) return;
            // Avoid recursive noise
            if (name === 'page_context') return;
            const v = this.config.vehicle || {};
            const meta = Object.assign({}, params || {});
            // Keep payload lean
            delete meta.items;
            const body = Object.assign({
                type: 'event',
                event_name: name,
                path: window.location.pathname || '/',
                page_type: this.config.pageType || 'other',
                vehicle_id: (params && params.vehicle_id) || v.id || null,
                label: (params && (params.label || params.link_text || params.search_term || params.cta_type || params.gallery_action)) || null,
                value: (params && (params.value != null ? params.value : params.price)) || null,
                meta: meta,
            }, this.identityPayload());
            this.postCollect(body, !!useBeacon);
        },

        trackPageContext() {
            const cfg = this.config;
            this.push({
                event: 'page_context',
                page_type: cfg.pageType,
                business_name: cfg.businessName,
            });
        },

        trackViewItem() {
            const v = this.config.vehicle;
            if (!v || this.config.pageType !== 'vehicle_detail') return;
            const item = {
                item_id: String(v.id || v.stock_number || ''),
                item_name: v.title || '',
                item_brand: v.make || '',
                item_category: v.body_style || 'Vehicle',
                item_variant: [v.year, v.model, v.trim].filter(Boolean).join(' '),
                price: Number(v.price) || 0,
                quantity: 1,
            };
            const params = {
                currency: 'USD',
                value: Number(v.price) || 0,
                items: [item],
                vehicle_id: v.id,
                vehicle_year: v.year,
                vehicle_make: v.make,
                vehicle_model: v.model,
                title_status: v.title_status || '',
            };
            this.event('view_item', params);
            this.fbqTrack('ViewContent', {
                content_ids: [String(v.id || '')],
                content_type: 'vehicle',
                content_name: v.title || '',
                value: Number(v.price) || 0,
                currency: 'USD',
            });
        },

        trackInventorySearch() {
            const inv = this.config.inventory;
            if (!inv || this.config.pageType !== 'inventory') return;
            const term = inv.search || inv.make || inv.body_style || inv.title_status || '';
            if (!term && !inv.has_filters) return;
            this.event('view_search_results', {
                search_term: inv.search || '',
                make: inv.make || '',
                body_style: inv.body_style || '',
                title_status: inv.title_status || '',
                results_count: inv.total || 0,
                page: inv.page || 1,
            });
            if (inv.search) {
                this.event('search', { search_term: inv.search });
            }
        },

        trackLead(extra) {
            const cfg = this.config;
            const v = cfg.vehicle || {};
            const utm = getStoredUtm();
            const value = Number(v.price) || cfg.leadValue || 1;
            const params = Object.assign({
                currency: 'USD',
                value: value,
                lead_source: (extra && extra.source) || 'form',
                vehicle_id: (extra && extra.vehicle_id) || v.id || null,
                vehicle_title: v.title || '',
                page_type: cfg.pageType,
            }, utm, extra || {});
            this.event('generate_lead', params);
            this.fbqTrack('Lead', {
                content_name: params.lead_source,
                content_category: cfg.pageType,
                value: value,
                currency: 'USD',
                content_ids: params.vehicle_id ? [String(params.vehicle_id)] : undefined,
            });
            this.fbqTrack('Contact');
        },

        trackCta(kind, href, label) {
            const params = {
                link_url: href || '',
                link_text: label || '',
                cta_type: kind,
                page_type: this.config.pageType,
            };
            if (kind === 'tel') {
                this.event('click_to_call', params);
                this.fbqCustom('ClickToCall', params);
            } else if (kind === 'sms') {
                this.event('click_to_sms', params);
                this.fbqCustom('ClickToSms', params);
            } else if (kind === 'email') {
                this.event('click_to_email', params);
            } else {
                this.event('select_content', params);
            }
        },

        bindCtaClicks() {
            document.addEventListener('click', (e) => {
                const a = e.target.closest('a[href]');
                if (!a) return;
                const href = (a.getAttribute('href') || '').trim();
                const label = (a.textContent || '').trim().slice(0, 80);
                if (href.startsWith('tel:')) {
                    this.trackCta('tel', href, label);
                } else if (href.startsWith('sms:')) {
                    this.trackCta('sms', href, label);
                } else if (href.startsWith('mailto:')) {
                    this.trackCta('email', href, label);
                }
            }, true);
        },

        bindOutboundAndDownloads() {
            document.addEventListener('click', (e) => {
                const a = e.target.closest('a[href]');
                if (!a) return;
                const href = a.href || '';
                if (!href) return;
                const label = (a.textContent || '').trim().slice(0, 80);
                // CarFax / PDF downloads
                if (/\.pdf($|\?)/i.test(href) || href.includes('/carfax/')) {
                    this.event('file_download', {
                        file_extension: 'pdf',
                        link_url: href,
                        link_text: label,
                        page_type: this.config.pageType,
                    });
                    this.fbqCustom('CarfaxDownload', { link_url: href });
                    return;
                }
                // External outbound
                try {
                    const url = new URL(href, window.location.origin);
                    if (url.origin !== window.location.origin && !href.startsWith('tel:') && !href.startsWith('sms:') && !href.startsWith('mailto:')) {
                        this.event('click', {
                            link_url: href,
                            link_domain: url.hostname,
                            outbound: true,
                            link_text: label,
                        });
                    }
                } catch (err) { /* ignore */ }
            }, true);
        },

        bindInventoryFilters() {
            const form = document.querySelector('#inventoryFilters form, form[data-track-inventory]');
            if (!form) return;
            form.addEventListener('submit', () => {
                const fd = new FormData(form);
                this.event('inventory_filter', {
                    search_term: fd.get('q') || '',
                    make: fd.get('make') || '',
                    body_style: fd.get('body_style') || '',
                    title_status: fd.get('title_status') || '',
                    min_price: fd.get('min_price') || '',
                    max_price: fd.get('max_price') || '',
                    max_mileage: fd.get('max_mileage') || '',
                    sort: fd.get('sort') || '',
                });
            });
        },

        trackGallery(action, index, total) {
            this.event('gallery_engagement', {
                gallery_action: action,
                image_index: index,
                image_count: total,
                vehicle_id: (this.config.vehicle && this.config.vehicle.id) || null,
            });
        },

        trackPaymentCalculated(data) {
            this.event('payment_calculated', data);
            this.fbqCustom('PaymentCalculated', data);
        },
    };

    window.MarshallAnalytics = Analytics;

    document.addEventListener('DOMContentLoaded', function () {
        Analytics.init();

        // Reserve space for sticky mobile CTA
        if (document.querySelector('.mobile-cta-bar')) {
            document.body.classList.add('has-mobile-cta');
        }

        // Navbar active state
        const navLinks = document.querySelectorAll('.navbar-nav .nav-link');
        const currentPath = window.location.pathname;
        navLinks.forEach(link => {
            const href = link.getAttribute('href');
            if (href && currentPath.startsWith(href) && href !== '/') {
                link.classList.add('active');
            } else if (href === '/' && currentPath === '/') {
                link.classList.add('active');
            }
        });

        // Close mobile nav after tapping a link
        const mainNav = document.getElementById('mainNav');
        if (mainNav && window.bootstrap) {
            mainNav.querySelectorAll('a').forEach(link => {
                link.addEventListener('click', () => {
                    const collapse = bootstrap.Collapse.getInstance(mainNav);
                    if (collapse && window.getComputedStyle(mainNav).display !== 'none' && mainNav.classList.contains('show')) {
                        collapse.hide();
                    }
                });
            });
        }

        // Vehicle gallery: photos mode + optional 360° spin scrub + highlight bubbles
        const gallery = document.getElementById('vehicle-gallery');
        const galleryStage = document.getElementById('gallery-stage') || gallery;
        const mainImage = document.getElementById('main-gallery-image');
        const thumbButtons = Array.from(document.querySelectorAll('.thumbnail-list .thumbnail-btn, .thumbnail-list img[data-full]'));
        const galleryPrev = document.getElementById('gallery-prev');
        const galleryNext = document.getElementById('gallery-next');
        const galleryCounter = document.getElementById('gallery-counter');
        const swipeHint = document.getElementById('gallery-swipe-hint');
        const swipeHintText = document.getElementById('gallery-swipe-hint-text');
        const spinBadge = document.getElementById('gallery-spin-badge');
        const spinHelp = document.getElementById('gallery-spin-help');
        const thumbsList = document.getElementById('gallery-thumbs');
        const modeButtons = Array.from(document.querySelectorAll('.gallery-mode-toggle [data-gallery-mode], .btn-group [data-gallery-mode]'));
        const hotspotsEl = document.getElementById('gallery-hotspots');
        const hotspotCard = document.getElementById('gallery-hotspot-card');
        const hotspotCardClose = document.getElementById('gallery-hotspot-card-close');
        const hotspotCardIcon = document.getElementById('gallery-hotspot-card-icon');
        const hotspotCardKicker = document.getElementById('gallery-hotspot-card-kicker');
        const hotspotCardTitle = document.getElementById('gallery-hotspot-card-title');
        const hotspotCardText = document.getElementById('gallery-hotspot-card-text');
        const highlightsBadge = document.getElementById('gallery-highlights-badge');
        const highlightsBadgeText = document.getElementById('gallery-highlights-badge-text');
        const maximizeBtn = document.getElementById('gallery-maximize');
        const lightbox = document.getElementById('gallery-lightbox');
        const lightboxImage = document.getElementById('gallery-lightbox-image');
        const lightboxClose = document.getElementById('gallery-lightbox-close');
        const lightboxPrev = document.getElementById('gallery-lightbox-prev');
        const lightboxNext = document.getElementById('gallery-lightbox-next');
        const lightboxCounter = document.getElementById('gallery-lightbox-counter');

        if (mainImage && thumbButtons.length >= 1 && gallery) {
            const parseHighlights = (raw) => {
                if (!raw) return [];
                const parsed = typeof raw === 'string' ? safeParse(raw) : raw;
                return Array.isArray(parsed) ? parsed : [];
            };

            const slides = thumbButtons.map((el, index) => {
                const img = el.tagName === 'IMG' ? el : el.querySelector('img');
                return {
                    el,
                    index,
                    src: el.dataset.full || (img && (img.dataset.full || img.currentSrc || img.src)) || '',
                    alt: (img && img.alt) || mainImage.alt || '',
                    highlights: parseHighlights(el.dataset.highlights),
                    imageId: el.dataset.imageId || null,
                };
            }).filter((s) => s.src);

            if (slides.length >= 1) {
                let currentIndex = Math.max(0, slides.findIndex((s) => s.el.classList.contains('active')));
                if (currentIndex < 0) currentIndex = 0;

                const multi = slides.length > 1;
                let lightboxOpen = false;
                let lightboxLastFocus = null;
                const spinReady = multi && gallery.dataset.spinReady === 'true' && slides.length >= 4;
                const highlightsEnabled = gallery.dataset.highlightsEnabled !== 'false';
                let mode = 'photos'; // 'photos' | 'spin'
                let spinOriginIndex = 0;
                let spinAccumPx = 0;
                let openHighlightId = null;
                let suppressClickUntil = 0;

                const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
                const hideSwipeHint = () => {
                    if (!swipeHint) return;
                    swipeHint.classList.add('is-hidden');
                };

                const severityLabel = (severity) => {
                    if (severity === 'positive') return 'Feature';
                    if (severity === 'caution') return 'Condition note';
                    if (severity === 'issue') return 'Needs attention';
                    return 'Highlight';
                };

                const closeHotspotCard = () => {
                    openHighlightId = null;
                    if (hotspotCard) hotspotCard.hidden = true;
                    if (hotspotsEl) {
                        hotspotsEl.querySelectorAll('.gallery-hotspot.is-active').forEach((btn) => {
                            btn.classList.remove('is-active');
                            btn.setAttribute('aria-expanded', 'false');
                        });
                    }
                };

                const openHotspotCard = (item, bubbleBtn) => {
                    if (!hotspotCard || !item) return;
                    openHighlightId = item.id;
                    if (hotspotCardKicker) {
                        hotspotCardKicker.textContent = severityLabel(item.severity);
                        hotspotCardKicker.dataset.severity = item.severity || 'info';
                    }
                    if (hotspotCardTitle) hotspotCardTitle.textContent = item.label || 'Detail';
                    if (hotspotCardText) {
                        hotspotCardText.textContent = item.description || 'Tap another bubble to explore this photo.';
                    }
                    if (hotspotCardIcon) {
                        const icon = (item.icon || 'info-circle').replace(/[^a-z0-9-]/gi, '');
                        hotspotCardIcon.innerHTML = `<i class="bi bi-${icon}" aria-hidden="true"></i>`;
                        hotspotCardIcon.dataset.severity = item.severity || 'info';
                    }
                    hotspotCard.hidden = false;
                    hotspotCard.dataset.severity = item.severity || 'info';
                    if (hotspotsEl) {
                        hotspotsEl.querySelectorAll('.gallery-hotspot').forEach((btn) => {
                            const active = btn === bubbleBtn;
                            btn.classList.toggle('is-active', active);
                            btn.setAttribute('aria-expanded', active ? 'true' : 'false');
                        });
                    }
                    Analytics.trackGallery('highlight_open', currentIndex, slides.length);
                };

                /**
                 * Map image-space % (0-100 of natural image) into the painted
                 * content box under object-fit: cover | contain | fill.
                 * Hotspot layer is inset:0 over the full stage, so we convert
                 * to % of the stage (hotspotsEl) box.
                 */
                const mapImagePctToStagePct = (xPct, yPct) => {
                    const stage = hotspotsEl || galleryStage || gallery;
                    if (!stage || !mainImage) {
                        return { left: xPct, top: yPct, visible: true };
                    }
                    const stageRect = stage.getBoundingClientRect();
                    const stageW = stageRect.width || stage.clientWidth || 1;
                    const stageH = stageRect.height || stage.clientHeight || 1;
                    const natW = mainImage.naturalWidth || 0;
                    const natH = mainImage.naturalHeight || 0;
                    if (!natW || !natH || stageW < 2 || stageH < 2) {
                        return { left: xPct, top: yPct, visible: true };
                    }

                    const fit = (window.getComputedStyle(mainImage).objectFit || 'fill').toLowerCase();
                    const imgRatio = natW / natH;
                    const stageRatio = stageW / stageH;
                    let contentW = stageW;
                    let contentH = stageH;
                    let offsetX = 0;
                    let offsetY = 0;

                    if (fit === 'cover') {
                        if (imgRatio > stageRatio) {
                            contentH = stageH;
                            contentW = stageH * imgRatio;
                            offsetX = (stageW - contentW) / 2;
                            offsetY = 0;
                        } else {
                            contentW = stageW;
                            contentH = stageW / imgRatio;
                            offsetX = 0;
                            offsetY = (stageH - contentH) / 2;
                        }
                    } else if (fit === 'contain') {
                        if (imgRatio > stageRatio) {
                            contentW = stageW;
                            contentH = stageW / imgRatio;
                            offsetX = 0;
                            offsetY = (stageH - contentH) / 2;
                        } else {
                            contentH = stageH;
                            contentW = stageH * imgRatio;
                            offsetX = (stageW - contentW) / 2;
                            offsetY = 0;
                        }
                    } else {
                        // fill / none / scale-down → treat as stretch to stage
                        contentW = stageW;
                        contentH = stageH;
                        offsetX = 0;
                        offsetY = 0;
                    }

                    const px = offsetX + (Number(xPct) / 100) * contentW;
                    const py = offsetY + (Number(yPct) / 100) * contentH;
                    const left = (px / stageW) * 100;
                    const top = (py / stageH) * 100;
                    const pad = 1.5;
                    const visible = left >= -pad && left <= 100 + pad && top >= -pad && top <= 100 + pad;
                    return {
                        left: Math.max(-5, Math.min(105, left)),
                        top: Math.max(-5, Math.min(105, top)),
                        visible,
                    };
                };

                const renderHotspots = (index) => {
                    if (!hotspotsEl) return;
                    closeHotspotCard();
                    hotspotsEl.innerHTML = '';

                    const hideForSpin = mode === 'spin';
                    gallery.classList.toggle('has-hotspots', false);
                    if (highlightsBadge) {
                        highlightsBadge.hidden = hideForSpin;
                    }
                    if (!highlightsEnabled || hideForSpin) return;

                    const slide = slides[index];
                    const items = (slide && slide.highlights) || [];
                    if (highlightsBadgeText) {
                        const total = slides.reduce((sum, s) => sum + ((s.highlights && s.highlights.length) || 0), 0);
                        if (total > 0) {
                            highlightsBadgeText.textContent = items.length
                                ? `${items.length} on this photo · ${total} total`
                                : `${total} highlights`;
                        }
                    }
                    if (!items.length) return;

                    gallery.classList.add('has-hotspots');
                    items.forEach((item, i) => {
                        const xImg = Math.max(0, Math.min(100, Number(item.x_pct) || 50));
                        const yImg = Math.max(0, Math.min(100, Number(item.y_pct) || 50));
                        const mapped = mapImagePctToStagePct(xImg, yImg);
                        if (!mapped.visible) return;
                        const btn = document.createElement('button');
                        btn.type = 'button';
                        btn.className = `gallery-hotspot severity-${item.severity || 'info'}`;
                        btn.style.left = `${mapped.left}%`;
                        btn.style.top = `${mapped.top}%`;
                        btn.dataset.xPct = String(xImg);
                        btn.dataset.yPct = String(yImg);
                        btn.dataset.highlightId = item.id != null ? String(item.id) : String(i);
                        btn.setAttribute('aria-label', `${item.label || 'Highlight'}. ${item.description || ''}`.trim());
                        btn.setAttribute('aria-expanded', 'false');
                        btn.innerHTML = `
                            <span class="gallery-hotspot-pulse" aria-hidden="true"></span>
                            <span class="gallery-hotspot-core" aria-hidden="true">
                                <i class="bi bi-${(item.icon || 'info-circle').replace(/[^a-z0-9-]/gi, '')}"></i>
                            </span>
                        `;
                        btn.addEventListener('click', (e) => {
                            e.preventDefault();
                            e.stopPropagation();
                            if (Date.now() < suppressClickUntil) return;
                            if (openHighlightId != null && String(openHighlightId) === String(item.id)) {
                                closeHotspotCard();
                                return;
                            }
                            openHotspotCard(item, btn);
                        });
                        btn.addEventListener('pointerdown', (e) => {
                            // Keep swipe/spin gestures from starting on a bubble
                            e.stopPropagation();
                        });
                        hotspotsEl.appendChild(btn);
                    });
                };

                const relayoutHotspots = () => {
                    if (!hotspotsEl || !gallery.classList.contains('has-hotspots')) return;
                    hotspotsEl.querySelectorAll('.gallery-hotspot').forEach((btn) => {
                        const xImg = Number(btn.dataset.xPct);
                        const yImg = Number(btn.dataset.yPct);
                        if (Number.isNaN(xImg) || Number.isNaN(yImg)) return;
                        const mapped = mapImagePctToStagePct(xImg, yImg);
                        btn.style.left = `${mapped.left}%`;
                        btn.style.top = `${mapped.top}%`;
                        btn.hidden = !mapped.visible;
                    });
                };

                const updateCounter = (index) => {
                    if (galleryCounter) {
                        galleryCounter.textContent = `${index + 1} / ${slides.length}`;
                    }
                    if (lightboxCounter) {
                        lightboxCounter.textContent = `${index + 1} / ${slides.length}`;
                    }
                    if (mode === 'spin') {
                        gallery.setAttribute(
                            'aria-label',
                            `360 spin frame ${index + 1} of ${slides.length}. Drag left or right to rotate.`
                        );
                    } else {
                        const n = ((slides[index] && slides[index].highlights) || []).length;
                        const highlightHint = n > 0 ? ` ${n} highlight bubble${n === 1 ? '' : 's'} available.` : '';
                        gallery.setAttribute(
                            'aria-label',
                            multi
                                ? `Photo ${index + 1} of ${slides.length}. Swipe left or right to change photos.${highlightHint}`
                                : `Vehicle photo.${highlightHint}`
                        );
                    }
                };

                const syncLightboxImage = (index) => {
                    if (!lightbox || !lightboxImage) return;
                    const slide = slides[index];
                    if (!slide) return;
                    if (lightboxImage.getAttribute('src') !== slide.src) {
                        lightboxImage.src = slide.src;
                    }
                    lightboxImage.alt = slide.alt
                        ? `${slide.alt} (enlarged)`
                        : 'Enlarged vehicle photo';
                    if (lightboxCounter) {
                        lightboxCounter.textContent = `${index + 1} / ${slides.length}`;
                    }
                };

                const closeLightbox = () => {
                    if (!lightbox || !lightboxOpen) return;
                    lightboxOpen = false;
                    lightbox.hidden = true;
                    document.body.classList.remove('gallery-lightbox-open');
                    if (maximizeBtn) {
                        maximizeBtn.setAttribute('aria-expanded', 'false');
                    }
                    const restore = lightboxLastFocus;
                    lightboxLastFocus = null;
                    if (restore && typeof restore.focus === 'function') {
                        try { restore.focus(); } catch (_) { /* ignore */ }
                    } else if (maximizeBtn) {
                        try { maximizeBtn.focus(); } catch (_) { /* ignore */ }
                    }
                };

                const openLightbox = () => {
                    if (!lightbox || !lightboxImage) return;
                    if (mode === 'spin') {
                        setMode('photos', 'maximize_exit_spin');
                    }
                    closeHotspotCard();
                    lightboxLastFocus = document.activeElement;
                    lightboxOpen = true;
                    syncLightboxImage(currentIndex);
                    lightbox.hidden = false;
                    document.body.classList.add('gallery-lightbox-open');
                    if (maximizeBtn) {
                        maximizeBtn.setAttribute('aria-expanded', 'true');
                    }
                    const focusTarget = lightboxClose || lightbox;
                    window.setTimeout(() => {
                        try { focusTarget.focus(); } catch (_) { /* ignore */ }
                    }, 0);
                    Analytics.trackGallery('maximize_open', currentIndex, slides.length);
                };

                const setActiveSlide = (index, action, options) => {
                    const opts = options || {};
                    const total = slides.length;
                    const normalized = ((index % total) + total) % total;
                    const slide = slides[normalized];
                    if (!slide) return;
                    if (normalized === currentIndex && !opts.force) {
                        updateCounter(normalized);
                        if (!opts.skipHotspots) renderHotspots(normalized);
                        if (lightboxOpen) syncLightboxImage(normalized);
                        return;
                    }

                    currentIndex = normalized;
                    const useFade = mode !== 'spin' || !!opts.forceFade;
                    if (useFade) {
                        gallery.classList.add('is-changing');
                    }
                    mainImage.style.transform = 'translate3d(0,0,0)';
                    if (mainImage.getAttribute('src') !== slide.src) {
                        mainImage.src = slide.src;
                    }
                    if (slide.alt) mainImage.alt = slide.alt;

                    const reveal = () => {
                        gallery.classList.remove('is-changing');
                        if (!opts.skipHotspots) renderHotspots(normalized);
                    };
                    if (!useFade) {
                        reveal();
                    } else if (mainImage.complete) {
                        reveal();
                    } else {
                        mainImage.addEventListener('load', reveal, { once: true });
                        mainImage.addEventListener('error', reveal, { once: true });
                    }

                    slides.forEach((s) => {
                        s.el.classList.toggle('active', s.index === normalized);
                        if (s.el.hasAttribute('aria-current')) {
                            s.el.setAttribute('aria-current', s.index === normalized ? 'true' : 'false');
                        }
                    });

                    if (!opts.skipScroll && multi) {
                        try {
                            slide.el.scrollIntoView({
                                behavior: reduceMotion || mode === 'spin' ? 'auto' : 'smooth',
                                inline: 'center',
                                block: 'nearest',
                            });
                        } catch (_) { /* ignore */ }
                    }

                    updateCounter(normalized);
                    if (lightboxOpen) {
                        syncLightboxImage(normalized);
                    }
                    if (!opts.quiet) {
                        hideSwipeHint();
                        Analytics.trackGallery(action || 'thumb_click', normalized, total);
                    }

                    // Prefetch neighbors
                    if (multi) {
                        [normalized - 1, normalized + 1].forEach((i) => {
                            const n = slides[((i % total) + total) % total];
                            if (!n) return;
                            const pre = new Image();
                            pre.src = n.src;
                        });
                    }
                };

                const goNext = (action) => setActiveSlide(currentIndex + 1, action || 'next');
                const goPrev = (action) => setActiveSlide(currentIndex - 1, action || 'prev');

                const setMode = (nextMode, action) => {
                    if (nextMode === 'spin' && !spinReady) return;
                    if (nextMode !== 'photos' && nextMode !== 'spin') return;
                    if (mode === nextMode) return;

                    mode = nextMode;
                    gallery.dataset.galleryMode = mode;
                    gallery.classList.toggle('is-spin-mode', mode === 'spin');

                    modeButtons.forEach((btn) => {
                        const active = btn.getAttribute('data-gallery-mode') === mode;
                        btn.classList.toggle('active', active);
                        btn.setAttribute('aria-pressed', active ? 'true' : 'false');
                    });

                    if (spinBadge) spinBadge.hidden = mode !== 'spin';
                    if (spinHelp) spinHelp.hidden = mode !== 'spin';
                    if (thumbsList) {
                        thumbsList.classList.toggle('is-spin-dimmed', mode === 'spin');
                    }

                    if (galleryPrev) galleryPrev.hidden = mode === 'spin';
                    if (galleryNext) galleryNext.hidden = mode === 'spin';

                    if (swipeHintText) {
                        swipeHintText.textContent = mode === 'spin'
                            ? 'Drag to spin 360°'
                            : 'Swipe for more photos';
                    }
                    if (swipeHint) {
                        swipeHint.classList.remove('is-hidden');
                        window.setTimeout(hideSwipeHint, mode === 'spin' ? 4500 : 5000);
                    }

                    gallery.setAttribute(
                        'aria-roledescription',
                        mode === 'spin' ? '360 degree image spinner' : 'carousel'
                    );
                    closeHotspotCard();
                    renderHotspots(currentIndex);
                    updateCounter(currentIndex);
                    Analytics.trackGallery(action || (mode === 'spin' ? 'spin_mode' : 'photos_mode'), currentIndex, slides.length);
                };

                updateCounter(currentIndex);
                renderHotspots(currentIndex);

                // Re-map bubbles when object-fit content box changes
                let hotspotResizeTimer = null;
                const scheduleHotspotRelayout = () => {
                    if (hotspotResizeTimer) window.clearTimeout(hotspotResizeTimer);
                    hotspotResizeTimer = window.setTimeout(() => {
                        relayoutHotspots();
                    }, 50);
                };
                window.addEventListener('resize', scheduleHotspotRelayout);
                if (typeof ResizeObserver !== 'undefined' && (galleryStage || gallery)) {
                    try {
                        const ro = new ResizeObserver(scheduleHotspotRelayout);
                        ro.observe(galleryStage || gallery);
                    } catch (_) { /* ignore */ }
                }
                mainImage.addEventListener('load', () => {
                    renderHotspots(currentIndex);
                });

                if (hotspotCardClose) {
                    hotspotCardClose.addEventListener('click', (e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        closeHotspotCard();
                    });
                }
                if (hotspotCard) {
                    hotspotCard.addEventListener('pointerdown', (e) => e.stopPropagation());
                    hotspotCard.addEventListener('click', (e) => e.stopPropagation());
                }
                document.addEventListener('keydown', (e) => {
                    if (e.key === 'Escape') {
                        if (lightboxOpen) {
                            e.preventDefault();
                            closeLightbox();
                            return;
                        }
                        closeHotspotCard();
                        return;
                    }
                    if (!lightboxOpen) return;
                    if (e.key === 'ArrowRight' && multi) {
                        e.preventDefault();
                        goNext('lightbox_key_next');
                    } else if (e.key === 'ArrowLeft' && multi) {
                        e.preventDefault();
                        goPrev('lightbox_key_prev');
                    }
                });

                if (maximizeBtn) {
                    maximizeBtn.setAttribute('aria-expanded', 'false');
                    maximizeBtn.setAttribute('aria-controls', 'gallery-lightbox');
                    maximizeBtn.addEventListener('click', (e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        openLightbox();
                    });
                    maximizeBtn.addEventListener('pointerdown', (e) => e.stopPropagation());
                }
                if (lightboxClose) {
                    lightboxClose.addEventListener('click', (e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        closeLightbox();
                    });
                }
                if (lightboxPrev) {
                    lightboxPrev.addEventListener('click', (e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        goPrev('lightbox_prev');
                    });
                }
                if (lightboxNext) {
                    lightboxNext.addEventListener('click', (e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        goNext('lightbox_next');
                    });
                }
                if (lightbox) {
                    lightbox.addEventListener('click', (e) => {
                        if (e.target && e.target.closest && e.target.closest('[data-lightbox-close]')) {
                            e.preventDefault();
                            closeLightbox();
                        }
                    });
                }
                // Double-click / double-tap photo to maximize (photos mode only)
                let lastStageTapAt = 0;
                if (galleryStage) {
                    galleryStage.addEventListener('dblclick', (e) => {
                        if (e.target.closest('.gallery-hotspot, .gallery-hotspot-card, .gallery-nav, .gallery-maximize-btn')) return;
                        if (mode === 'spin') return;
                        e.preventDefault();
                        openLightbox();
                    });
                    galleryStage.addEventListener('click', (e) => {
                        if (e.target.closest('.gallery-hotspot, .gallery-hotspot-card, .gallery-nav, .gallery-maximize-btn')) return;
                        if (mode === 'spin' || Date.now() < suppressClickUntil) return;
                        const now = Date.now();
                        if (now - lastStageTapAt < 320) {
                            lastStageTapAt = 0;
                            openLightbox();
                            return;
                        }
                        lastStageTapAt = now;
                    });
                }

                modeButtons.forEach((btn) => {
                    btn.addEventListener('click', (e) => {
                        e.preventDefault();
                        setMode(btn.getAttribute('data-gallery-mode'), 'mode_toggle');
                    });
                });

                slides.forEach((slide) => {
                    slide.el.addEventListener('click', (e) => {
                        e.preventDefault();
                        if (mode === 'spin') {
                            setMode('photos', 'thumb_exit_spin');
                        }
                        setActiveSlide(slide.index, 'thumb_click');
                    });
                });

                if (galleryPrev) {
                    galleryPrev.addEventListener('click', (e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        if (mode === 'spin') setMode('photos', 'arrow_exit_spin');
                        goPrev('arrow_prev');
                    });
                }
                if (galleryNext) {
                    galleryNext.addEventListener('click', (e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        if (mode === 'spin') setMode('photos', 'arrow_exit_spin');
                        goNext('arrow_next');
                    });
                }

                gallery.addEventListener('keydown', (e) => {
                    if (e.key === 'ArrowRight' && multi) {
                        e.preventDefault();
                        goNext(mode === 'spin' ? 'spin_key_next' : 'key_next');
                    } else if (e.key === 'ArrowLeft' && multi) {
                        e.preventDefault();
                        goPrev(mode === 'spin' ? 'spin_key_prev' : 'key_prev');
                    } else if ((e.key === 's' || e.key === 'S') && spinReady) {
                        e.preventDefault();
                        setMode(mode === 'spin' ? 'photos' : 'spin', 'key_mode_toggle');
                    }
                });

                // Pointer-based swipe (photos) / continuous scrub (spin)
                const SWIPE_THRESHOLD_PX = 48;
                const SWIPE_THRESHOLD_RATIO = 0.14;
                const AXIS_LOCK_PX = 8;
                let pointerId = null;
                let startX = 0;
                let startY = 0;
                let lastX = 0;
                let startTime = 0;
                let axis = null; // 'x' | 'y' | null
                let dragging = false;
                let lastSpinTrackAt = 0;

                const pxPerFrame = () => {
                    const width = gallery.offsetWidth || 320;
                    // ~full drag width ≈ one full rotation through all frames
                    return Math.max(18, Math.min(56, width / Math.max(slides.length, 8)));
                };

                const resetDragStyles = () => {
                    gallery.classList.remove('is-dragging', 'is-swiping-x', 'is-spinning');
                    mainImage.style.transform = 'translate3d(0,0,0)';
                };

                const onPointerDown = (e) => {
                    if (e.target.closest('.gallery-nav, .gallery-hotspot, .gallery-hotspot-card, .gallery-maximize-btn')) return;
                    if (e.pointerType === 'mouse' && e.button !== 0) return;
                    if (!multi && mode !== 'spin') return;

                    pointerId = e.pointerId;
                    startX = lastX = e.clientX;
                    startY = e.clientY;
                    startTime = Date.now();
                    axis = null;
                    dragging = true;
                    spinOriginIndex = currentIndex;
                    spinAccumPx = 0;
                    closeHotspotCard();

                    try {
                        galleryStage.setPointerCapture(pointerId);
                    } catch (_) { /* ignore */ }

                    gallery.classList.add('is-dragging');
                    if (mode === 'spin') {
                        gallery.classList.add('is-spinning');
                    }
                };

                const onPointerMove = (e) => {
                    if (!dragging || e.pointerId !== pointerId) return;

                    const dx = e.clientX - startX;
                    const dy = e.clientY - startY;
                    lastX = e.clientX;

                    if (!axis) {
                        if (Math.abs(dx) < AXIS_LOCK_PX && Math.abs(dy) < AXIS_LOCK_PX) return;
                        axis = Math.abs(dx) > Math.abs(dy) ? 'x' : 'y';
                        if (axis === 'x') {
                            gallery.classList.add('is-swiping-x');
                        }
                    }

                    if (axis === 'y') {
                        // Let the page scroll vertically
                        return;
                    }

                    // Horizontal gesture: prevent page scroll / pull-to-refresh conflicts
                    if (e.cancelable) e.preventDefault();

                    if (mode === 'spin') {
                        spinAccumPx = dx;
                        // Drag right → previous frames (car rotates with the drag)
                        const frames = Math.round(-spinAccumPx / pxPerFrame());
                        const target = spinOriginIndex + frames;
                        if (target !== currentIndex) {
                            const now = Date.now();
                            const quiet = now - lastSpinTrackAt < 700;
                            if (!quiet) lastSpinTrackAt = now;
                            setActiveSlide(target, 'spin_scrub', {
                                quiet: quiet,
                                skipScroll: true,
                            });
                        }
                        return;
                    }

                    const width = gallery.offsetWidth || 1;
                    const resistance = 0.92;
                    const offset = dx * resistance;
                    const maxPull = width * 0.35;
                    const clamped = Math.max(-maxPull, Math.min(maxPull, offset));
                    mainImage.style.transform = `translate3d(${clamped}px,0,0)`;
                };

                const onPointerUp = (e) => {
                    if (!dragging || e.pointerId !== pointerId) return;
                    dragging = false;

                    const dx = (e.clientX || lastX) - startX;
                    const elapsed = Math.max(Date.now() - startTime, 1);
                    const velocity = Math.abs(dx) / elapsed; // px/ms
                    const width = gallery.offsetWidth || 1;
                    const distanceThreshold = Math.max(SWIPE_THRESHOLD_PX, width * SWIPE_THRESHOLD_RATIO);
                    const isFlick = velocity > 0.45 && Math.abs(dx) > 24;
                    const committedAxis = axis;
                    const wasSpin = mode === 'spin';

                    pointerId = null;
                    axis = null;

                    try {
                        galleryStage.releasePointerCapture(e.pointerId);
                    } catch (_) { /* ignore */ }

                    resetDragStyles();

                    if (committedAxis !== 'x') return;

                    // Avoid accidental bubble clicks right after a swipe
                    if (Math.abs(dx) > 10) {
                        suppressClickUntil = Date.now() + 280;
                    }

                    if (wasSpin) {
                        // Final frame already applied during scrub; log end of gesture
                        Analytics.trackGallery('spin_end', currentIndex, slides.length);
                        return;
                    }

                    if (Math.abs(dx) >= distanceThreshold || isFlick) {
                        if (dx < 0) goNext('swipe_next');
                        else goPrev('swipe_prev');
                    }
                };

                const onPointerCancel = (e) => {
                    if (!dragging || (pointerId !== null && e.pointerId !== pointerId)) return;
                    dragging = false;
                    pointerId = null;
                    axis = null;
                    resetDragStyles();
                };

                galleryStage.addEventListener('pointerdown', onPointerDown);
                galleryStage.addEventListener('pointermove', onPointerMove, { passive: false });
                galleryStage.addEventListener('pointerup', onPointerUp);
                galleryStage.addEventListener('pointercancel', onPointerCancel);
                galleryStage.addEventListener('lostpointercapture', onPointerCancel);

                // Prefetch all gallery images for snappy mobile swipes / spin
                if (multi) {
                    slides.forEach((slide) => {
                        const img = new Image();
                        img.src = slide.src;
                    });
                }

                // Auto-hide swipe hint after first interaction window
                window.setTimeout(hideSwipeHint, 5000);
            }
        }

        // AJAX contact forms
        const ajaxForms = document.querySelectorAll('form[data-ajax]');
        ajaxForms.forEach(form => {
            form.addEventListener('submit', function (e) {
                e.preventDefault();
                const submitBtn = form.querySelector('button[type="submit"]');
                const originalText = submitBtn ? submitBtn.innerHTML : '';
                if (submitBtn) {
                    submitBtn.disabled = true;
                    submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Sending...';
                }

                appendUtmHiddenFields(form);
                const formData = new FormData(form);
                fetch(form.action, {
                    method: 'POST',
                    body: formData,
                    headers: {
                        'X-Requested-With': 'XMLHttpRequest'
                    }
                })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        showAlert(data.message, 'success');
                        Analytics.trackLead({
                            source: formData.get('source') || 'ajax',
                            vehicle_id: formData.get('vehicle_id') || null,
                            lead_id: data.lead_id || null,
                        });
                        form.reset();
                    } else {
                        showAlert('Please correct the errors and try again.', 'danger');
                    }
                })
                .catch(() => {
                    showAlert('Something went wrong. Please call us or try again.', 'danger');
                })
                .finally(() => {
                    if (submitBtn) {
                        submitBtn.disabled = false;
                        submitBtn.innerHTML = originalText;
                    }
                });
            });
        });

        // Non-AJAX contact form: attach UTM fields before submit
        document.querySelectorAll('form:not([data-ajax])').forEach((form) => {
            if (form.querySelector('input[name="csrf_token"], input[name="name"]')) {
                form.addEventListener('submit', () => appendUtmHiddenFields(form));
            }
        });

        // Flash success on contact page = lead conversion (full page POST)
        const flashSuccess = document.querySelector('.alert-success');
        if (flashSuccess && (window.location.pathname.indexOf('/contact') !== -1)) {
            Analytics.trackLead({ source: 'contact' });
        }

        // Finance calculator
        const calcForm = document.getElementById('finance-calculator');
        if (calcForm) {
            calcForm.addEventListener('submit', function (e) {
                e.preventDefault();
                const price = parseFloat(document.getElementById('calc-price').value) || 0;
                const down = parseFloat(document.getElementById('calc-down').value) || 0;
                const rate = parseFloat(document.getElementById('calc-rate').value) || 0;
                const term = parseInt(document.getElementById('calc-term').value) || 60;

                const principal = price - down;
                const monthlyRate = rate / 100 / 12;
                const payment = monthlyRate === 0
                    ? principal / term
                    : (principal * monthlyRate) / (1 - Math.pow(1 + monthlyRate, -term));

                const totalCost = payment * term;
                const totalInterest = totalCost - principal;

                document.getElementById('calc-monthly').textContent = '$' + payment.toFixed(0).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
                document.getElementById('calc-total').textContent = '$' + totalCost.toFixed(0).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
                document.getElementById('calc-interest').textContent = '$' + totalInterest.toFixed(0).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
                document.getElementById('calc-results').classList.remove('d-none');

                Analytics.trackPaymentCalculated({
                    currency: 'USD',
                    value: Math.round(payment),
                    vehicle_price: price,
                    down_payment: down,
                    interest_rate: rate,
                    term_months: term,
                    monthly_payment: Math.round(payment * 100) / 100,
                    total_interest: Math.round(totalInterest * 100) / 100,
                });
            });
        }
    });

    function showAlert(message, type) {
        const wrapper = document.createElement('div');
        wrapper.className = `alert alert-${type} alert-dismissible fade show mt-3`;
        wrapper.innerHTML = `${message}<button type="button" class="btn-close" data-bs-dismiss="alert"></button>`;
        const container = document.querySelector('main, .container') || document.body;
        container.prepend(wrapper);
        setTimeout(() => {
            if (window.bootstrap) {
                const alert = bootstrap.Alert.getOrCreateInstance(wrapper);
                if (alert) alert.close();
            }
        }, 6000);
    }

    window.showAlert = showAlert;
})();
