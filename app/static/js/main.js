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
        };
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
        init() {
            this.config = getConfig();
            captureUtmParams();
            this.trackPageContext();
            this.bindCtaClicks();
            this.bindOutboundAndDownloads();
            this.bindInventoryFilters();
            this.trackViewItem();
            this.trackInventorySearch();
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

        // Vehicle gallery thumbnail switcher + swipe
        const mainImage = document.getElementById('main-gallery-image');
        const thumbnails = Array.from(document.querySelectorAll('.thumbnail-list img'));
        if (mainImage && thumbnails.length) {
            const setActiveThumb = (index, action) => {
                const thumb = thumbnails[index];
                if (!thumb) return;
                mainImage.src = thumb.dataset.full || thumb.src;
                thumbnails.forEach(t => t.classList.remove('active'));
                thumb.classList.add('active');
                thumb.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' });
                Analytics.trackGallery(action || 'thumb_click', index, thumbnails.length);
            };

            thumbnails.forEach((thumb, index) => {
                thumb.addEventListener('click', function () {
                    setActiveThumb(index, 'thumb_click');
                });
            });

            let touchStartX = 0;
            let touchEndX = 0;
            mainImage.addEventListener('touchstart', (e) => {
                touchStartX = e.changedTouches[0].screenX;
            }, { passive: true });
            mainImage.addEventListener('touchend', (e) => {
                touchEndX = e.changedTouches[0].screenX;
                const delta = touchEndX - touchStartX;
                if (Math.abs(delta) < 40) return;
                const currentIndex = thumbnails.findIndex(t => t.classList.contains('active'));
                const nextIndex = delta < 0
                    ? Math.min(currentIndex + 1, thumbnails.length - 1)
                    : Math.max(currentIndex - 1, 0);
                if (nextIndex !== currentIndex) {
                    setActiveThumb(nextIndex, delta < 0 ? 'swipe_next' : 'swipe_prev');
                }
            }, { passive: true });
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
