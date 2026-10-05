(function () {
    'use strict';

    var prompt = document.getElementById('vehicle-visit-prompt');
    if (!prompt) return;

    var sessionKey = 'ma_visit_prompt_seen';
    var dismissalKey = 'ma_visit_prompt_dismissed_until';
    var interested = false;
    var shown = false;
    var activeSeconds = 0;
    var lastInteraction = Date.now();
    var pointerStart = null;

    function suppressed() {
        try {
            return (!shown && window.sessionStorage.getItem(sessionKey) === '1') ||
                Number(window.localStorage.getItem(dismissalKey)) > Date.now();
        } catch (error) {
            return true;
        }
    }

    if (suppressed()) return;

    function blocked() {
        var focused = document.activeElement;
        if (focused && focused.matches('input, textarea, select, [contenteditable="true"]')) return true;
        if (window.innerWidth < 992 && window.innerHeight < 540) return true;
        if (document.querySelector('.modal.show, .modal-backdrop, .offcanvas.show, #mainNav.show, #mainNav.collapsing')) return true;
        if (document.querySelector('#gallery-lightbox:not([hidden]), #gallery-hotspot-card:not([hidden])')) return true;
        if (document.querySelector('#cookie-consent-banner:not(.d-none), #chat-widget-panel:not(.d-none), #compare-bar:not(.d-none)')) return true;
        return Array.from(document.querySelectorAll('video')).some(function (video) {
            return !video.paused && !video.ended;
        });
    }

    function positionPrompt() {
        var bottom = 16;
        if (window.innerWidth < 992) {
            document.querySelectorAll('.mobile-cta-bar, #chat-widget-toggle').forEach(function (element) {
                var rect = element.getBoundingClientRect();
                if (rect.height && rect.top < window.innerHeight) {
                    bottom = Math.max(bottom, window.innerHeight - rect.top + 12);
                }
            });
        }
        prompt.style.bottom = bottom + 'px';
    }

    function tick() {
        if (suppressed()) {
            prompt.hidden = true;
            window.clearInterval(timer);
            return;
        }
        if (document.hidden || blocked()) {
            prompt.hidden = true;
            return;
        }
        if (Date.now() - lastInteraction < 60000) activeSeconds += 1;
        if (!interested || activeSeconds < 20) return;
        if (!shown) {
            try {
                window.sessionStorage.setItem(sessionKey, '1');
            } catch (error) {
                window.clearInterval(timer);
                return;
            }
            shown = true;
        }
        positionPrompt();
        prompt.hidden = false;
    }

    function dismiss() {
        prompt.hidden = true;
        window.clearInterval(timer);
        try {
            window.localStorage.setItem(dismissalKey, String(Date.now() + 7 * 24 * 60 * 60 * 1000));
        } catch (error) {}
        if (prompt.contains(document.activeElement)) {
            var gallery = document.getElementById('vehicle-gallery');
            if (gallery) gallery.focus({ preventScroll: true });
        }
    }

    document.addEventListener('click', function (event) {
        lastInteraction = Date.now();
        if (event.target.closest('#vehicle-gallery, #gallery-thumbs, #vehicleTabs, .js-favorite-btn, .js-compare-checkbox')) {
            interested = true;
        }
        if (blocked()) prompt.hidden = true;
    });
    document.addEventListener('keydown', function (event) {
        lastInteraction = Date.now();
        if (event.target.closest('#vehicle-gallery') && ['ArrowLeft', 'ArrowRight'].indexOf(event.key) !== -1) interested = true;
        if (event.key === 'Escape' && !prompt.hidden && !event.defaultPrevented) dismiss();
    });
    document.addEventListener('scroll', function () {
        lastInteraction = Date.now();
        var details = document.getElementById('vehicleTabs');
        if (details && window.scrollY > 150) {
            var rect = details.getBoundingClientRect();
            if (rect.top < window.innerHeight && rect.bottom > 0) interested = true;
        }
    }, { passive: true });
    var gallery = document.getElementById('vehicle-gallery');
    if (gallery) {
        gallery.addEventListener('pointerdown', function (event) {
            pointerStart = event.clientX;
            lastInteraction = Date.now();
        });
        gallery.addEventListener('pointerup', function (event) {
            if (pointerStart !== null && Math.abs(event.clientX - pointerStart) > 30) interested = true;
            pointerStart = null;
        });
        gallery.addEventListener('pointercancel', function () { pointerStart = null; });
    }
    prompt.querySelectorAll('[data-visit-dismiss]').forEach(function (button) {
        button.addEventListener('click', dismiss);
    });
    document.getElementById('visit-prompt-directions').addEventListener('click', dismiss);
    document.addEventListener('focusin', function () {
        if (blocked()) prompt.hidden = true;
    });
    document.addEventListener('show.bs.modal', function () { prompt.hidden = true; });
    window.addEventListener('resize', positionPrompt);
    var timer = window.setInterval(tick, 1000);
})();