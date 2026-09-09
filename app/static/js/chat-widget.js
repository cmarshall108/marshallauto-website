/**
 * Floating live-chat-style widget. Posts to the existing /contact-submit
 * lead endpoint (source=live_chat_widget) — no third-party chat service required.
 */
(function () {
    'use strict';

    document.addEventListener('DOMContentLoaded', function () {
        var toggle = document.getElementById('chat-widget-toggle');
        var panel = document.getElementById('chat-widget-panel');
        var closeBtn = document.getElementById('chat-widget-close');
        var form = document.getElementById('chat-widget-form');
        var successEl = document.getElementById('chat-widget-success');
        var errorEl = document.getElementById('chat-widget-error');
        if (!toggle || !panel || !form) return;

        function openPanel() {
            panel.classList.remove('d-none');
            toggle.setAttribute('aria-expanded', 'true');
            toggle.querySelector('i').className = 'bi bi-x-lg';
        }

        function closePanel() {
            panel.classList.add('d-none');
            toggle.setAttribute('aria-expanded', 'false');
            toggle.querySelector('i').className = 'bi bi-chat-dots-fill';
        }

        toggle.addEventListener('click', function () {
            if (panel.classList.contains('d-none')) { openPanel(); } else { closePanel(); }
        });
        if (closeBtn) closeBtn.addEventListener('click', closePanel);

        form.addEventListener('submit', function (e) {
            e.preventDefault();
            errorEl.classList.add('d-none');
            var submitBtn = form.querySelector('button[type="submit"]');
            if (submitBtn) { submitBtn.disabled = true; submitBtn.textContent = 'Sending...'; }

            fetch('/contact-submit', {
                method: 'POST',
                body: new FormData(form),
                headers: { 'X-Requested-With': 'XMLHttpRequest' },
            })
                .then(function (r) { return r.json().then(function (data) { return { ok: r.ok, data: data }; }); })
                .then(function (result) {
                    if (result.ok && result.data.success) {
                        form.classList.add('d-none');
                        successEl.classList.remove('d-none');
                    } else {
                        var msg = 'Sorry, something went wrong. Please call us instead.';
                        if (result.data && result.data.errors) {
                            var firstKey = Object.keys(result.data.errors)[0];
                            if (firstKey && result.data.errors[firstKey][0]) msg = result.data.errors[firstKey][0];
                        }
                        errorEl.textContent = msg;
                        errorEl.classList.remove('d-none');
                    }
                })
                .catch(function () {
                    errorEl.textContent = 'Network error. Please call us instead.';
                    errorEl.classList.remove('d-none');
                })
                .finally(function () {
                    if (submitBtn) { submitBtn.disabled = false; submitBtn.textContent = 'Send Message'; }
                });
        });
    });
})();
