/**
 * Soft engagement nudge: exit-intent / idle helper that offers chat, call, or
 * inventory without hard-blocking popups. Once per session; dismissible for days.
 */
(function () {
    'use strict';

    var nudge = document.getElementById('engagement-nudge');
    if (!nudge) return;

    var sessionKey = 'ma_engage_nudge_seen';
    var dismissalKey = 'ma_engage_nudge_dismissed_until';
    var shown = false;
    var armed = false;
    var idleSeconds = 0;
    var dwellSeconds = 0;
    var lastInteraction = Date.now();
    var pageType = (document.body && document.body.getAttribute('data-page-type')) || 'other';
    var isShopping = ['home', 'inventory', 'vehicle_detail', 'coming_soon', 'service_area'].indexOf(pageType) !== -1;
    if (!isShopping) return;

    function suppressed() {
        try {
            if (window.sessionStorage.getItem(sessionKey) === '1') return true;
            if (Number(window.localStorage.getItem(dismissalKey)) > Date.now()) return true;
        } catch (error) {
            return true;
        }
        return false;
    }

    if (suppressed()) return;

    function blocked() {
        var focused = document.activeElement;
        if (focused && focused.matches('input, textarea, select, [contenteditable="true"]')) return true;
        if (window.innerWidth < 992 && window.innerHeight < 540) return true;
        if (document.querySelector('.modal.show, .modal-backdrop, .offcanvas.show, #mainNav.show, #mainNav.collapsing')) return true;
        if (document.querySelector('#gallery-lightbox:not([hidden]), #gallery-hotspot-card:not([hidden])')) return true;
        if (document.querySelector('#cookie-consent-banner:not(.d-none)')) return true;
        if (document.querySelector('#chat-widget-panel:not(.d-none)')) return true;
        if (document.querySelector('#vehicle-visit-prompt:not([hidden])')) return true;
        if (document.querySelector('#compare-bar:not(.d-none)')) return true;
        return Array.from(document.querySelectorAll('video')).some(function (video) {
            return !video.paused && !video.ended;
        });
    }

    function positionNudge() {
        var bottom = 16;
        if (window.innerWidth < 992) {
            document.querySelectorAll('.mobile-cta-bar, #chat-widget-toggle').forEach(function (element) {
                var rect = element.getBoundingClientRect();
                if (rect.height && rect.top < window.innerHeight) {
                    bottom = Math.max(bottom, window.innerHeight - rect.top + 12);
                }
            });
        } else {
            var toggle = document.getElementById('chat-widget-toggle');
            if (toggle) {
                var rect = toggle.getBoundingClientRect();
                if (rect.height && rect.top < window.innerHeight) {
                    bottom = Math.max(bottom, window.innerHeight - rect.top + 12);
                }
            }
        }
        nudge.style.bottom = bottom + 'px';
    }

    function markSeen() {
        try {
            window.sessionStorage.setItem(sessionKey, '1');
        } catch (error) {}
    }

    function show(reason) {
        if (shown || suppressed() || blocked()) return;
        shown = true;
        markSeen();
        positionNudge();
        nudge.hidden = false;
        nudge.setAttribute('data-reason', reason || '');
        var title = document.getElementById('engagement-nudge-title');
        if (title && pageType === 'vehicle_detail') {
            title.textContent = 'Still interested in this vehicle?';
        }
        var chatToggle = document.getElementById('chat-widget-toggle');
        if (chatToggle) {
            chatToggle.classList.add('chat-attention');
            window.setTimeout(function () {
                chatToggle.classList.remove('chat-attention');
            }, 8000);
        }
        try {
            if (window.dataLayer) {
                window.dataLayer.push({ event: 'engagement_nudge_shown', reason: reason || '', page_type: pageType });
            }
        } catch (error) {}
    }

    function dismiss(days) {
        nudge.hidden = true;
        window.clearInterval(timer);
        try {
            window.localStorage.setItem(
                dismissalKey,
                String(Date.now() + (days || 3) * 24 * 60 * 60 * 1000)
            );
        } catch (error) {}
        markSeen();
    }

    function openChat() {
        dismiss(1);
        var toggle = document.getElementById('chat-widget-toggle');
        if (toggle) toggle.click();
    }

    function noteInteraction() {
        lastInteraction = Date.now();
        idleSeconds = 0;
        if (!armed && Date.now() - pageStart > 8000) armed = true;
    }

    var pageStart = Date.now();

    function tick() {
        if (suppressed()) {
            nudge.hidden = true;
            window.clearInterval(timer);
            return;
        }
        dwellSeconds += 1;
        if (document.hidden || blocked()) {
            if (!nudge.hidden) nudge.hidden = true;
            return;
        }
        if (Date.now() - lastInteraction >= 1000) idleSeconds += 1;
        // Idle shoppers: gentle offer after meaningful dwell.
        if (!shown && armed && idleSeconds >= 35 && dwellSeconds >= 45) {
            show('idle');
        }
    }

    document.addEventListener('mousemove', noteInteraction, { passive: true });
    document.addEventListener('scroll', noteInteraction, { passive: true });
    document.addEventListener('keydown', noteInteraction);
    document.addEventListener('click', noteInteraction);
    document.addEventListener('touchstart', noteInteraction, { passive: true });

    // Desktop exit intent (cursor leaves through top of viewport).
    document.addEventListener('mouseout', function (event) {
        if (shown || suppressed() || blocked()) return;
        if (event.relatedTarget || event.toElement) return;
        if (event.clientY > 8) return;
        if (Date.now() - pageStart < 12000) return;
        if (!armed && Date.now() - pageStart < 20000) return;
        show('exit_intent');
    });

    // Mobile: brief pause after deep scroll suggests consideration.
    var maxScroll = 0;
    window.addEventListener('scroll', function () {
        maxScroll = Math.max(maxScroll, window.scrollY || 0);
        if (shown || suppressed() || blocked()) return;
        if (window.innerWidth >= 992) return;
        if (maxScroll < Math.min(900, document.body.scrollHeight * 0.45)) return;
        if (Date.now() - pageStart < 25000) return;
        if (idleSeconds < 12) return;
        show('scroll_pause');
    }, { passive: true });

    nudge.querySelectorAll('[data-engage-dismiss]').forEach(function (button) {
        button.addEventListener('click', function () { dismiss(3); });
    });
    var chatBtn = document.getElementById('engagement-nudge-chat');
    if (chatBtn) chatBtn.addEventListener('click', openChat);
    var callBtn = document.getElementById('engagement-nudge-call');
    if (callBtn) callBtn.addEventListener('click', function () { dismiss(1); });

    document.addEventListener('focusin', function () {
        if (blocked() && !nudge.hidden) nudge.hidden = true;
    });
    document.addEventListener('show.bs.modal', function () { nudge.hidden = true; });
    window.addEventListener('resize', positionNudge);

    // Soft arm after the visitor has had a chance to look around.
    window.setTimeout(function () { armed = true; }, 10000);
    var timer = window.setInterval(tick, 1000);
})();
