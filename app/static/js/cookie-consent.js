/**
 * Cookie consent banner: shows once per browser until a choice is made.
 * Accept -> Consent Mode granted + Facebook Pixel loads.
 * Decline -> Consent Mode stays denied, Facebook Pixel never loads.
 */
(function () {
    'use strict';

    var STORAGE_KEY = 'ma_cookie_consent';

    function getStored() {
        try { return window.localStorage.getItem(STORAGE_KEY); } catch (e) { return null; }
    }

    function setStored(value) {
        try { window.localStorage.setItem(STORAGE_KEY, value); } catch (e) { /* private mode */ }
    }

    document.addEventListener('DOMContentLoaded', function () {
        var banner = document.getElementById('cookie-consent-banner');
        if (!banner) return;

        if (!getStored()) {
            banner.classList.remove('d-none');
        }

        var accept = document.getElementById('cookie-consent-accept');
        var decline = document.getElementById('cookie-consent-decline');

        if (accept) {
            accept.addEventListener('click', function () {
                setStored('granted');
                banner.classList.add('d-none');
                if (typeof window.gtag === 'function') {
                    window.gtag('consent', 'update', {
                        ad_storage: 'granted',
                        ad_user_data: 'granted',
                        ad_personalization: 'granted',
                        analytics_storage: 'granted',
                    });
                }
                if (typeof window.__loadFacebookPixel === 'function') {
                    window.__loadFacebookPixel();
                }
            });
        }

        if (decline) {
            decline.addEventListener('click', function () {
                setStored('denied');
                banner.classList.add('d-none');
            });
        }
    });
})();
