(function () {
    'use strict';
    document.addEventListener('DOMContentLoaded', function () {
        var chat = window.MarshallChat;
        var toggle = document.getElementById('chat-widget-toggle');
        var panel = document.getElementById('chat-widget-panel');
        var form = document.getElementById('chat-widget-form');
        if (!toggle || !panel || !form || !chat) return;
        var errorEl = document.getElementById('chat-widget-error');
        var successEl = document.getElementById('chat-widget-success');
        var statusEl = document.getElementById('chat-widget-status');
        var live = document.getElementById('chat-live');
        var log = document.getElementById('chat-messages');
        var replyForm = document.getElementById('chat-reply-form');
        var reply = document.getElementById('chat-reply');
        var end = document.getElementById('chat-end');
        var newChat = document.getElementById('chat-new');
        var csrf = form.querySelector('[name="csrf_token"]').value;
        var sounds = window.MarshallChatSounds.create(document.getElementById('customer-chat-sounds'), 'customer');
        var online = false, conversation = null, cursor = 0, polling = false;
        var transcriptLoaded = false;
        var startingNew = false, initialSending = false, pending = null, transcriptPending = null;
        function error(message) {
            errorEl.textContent = message;
            errorEl.classList.remove('d-none');
        }
        function clearError() { errorEl.classList.add('d-none'); }
        function renderStatus() {
            if (conversation) {
                statusEl.textContent = conversation.blocked
                    ? 'This chat was blocked by our team. Please call us for assistance.'
                    : conversation.closed
                    ? 'This conversation has ended. You can start a new one below.'
                    : online ? 'Our team is online. Replies appear here.'
                    : 'Our team is away. Your messages are saved for follow-up; replies will appear here when an agent returns.';
                replyForm.classList.toggle('d-none', conversation.closed);
                end.classList.toggle('d-none', conversation.closed);
                newChat.classList.toggle('d-none', !conversation.closed || conversation.blocked);
            } else {
                statusEl.textContent = !successEl.classList.contains('d-none')
                    ? 'Message received. Our team will follow up by email or phone.'
                    : online
                    ? 'Our team is online. Send your question to start a live chat.'
                    : 'Our team is offline. Leave a message and we will get back to you.';
                form.querySelector('[type="submit"]').textContent = online ? 'Start live chat' : 'Send Message';
            }
        }
        function useConversation(value) {
            if (!conversation || conversation.id !== value.id) {
                cursor = 0;
                transcriptLoaded = false;
                log.replaceChildren();
                pending = null;
            }
            conversation = value;
            form.classList.add('d-none');
            successEl.classList.add('d-none');
            live.classList.remove('d-none');
            renderStatus();
        }
        function transcript() {
            if (transcriptPending) return transcriptPending;
            transcriptPending = loadTranscript().finally(function () { transcriptPending = null; });
            return transcriptPending;
        }
        async function loadTranscript() {
            if (!conversation) return;
            var id = conversation.id;
            var notify = transcriptLoaded, latestAgent = 0;
            var data;
            do {
                data = await chat.request('/api/chat/messages?after=' + cursor);
                if (!conversation || conversation.id !== id) return;
                useConversation(data.conversation);
                online = data.online;
                chat.append(log, data.messages, 'customer');
                data.messages.forEach(function (message) {
                    if (message.sender === 'agent') latestAgent = Math.max(latestAgent, message.id);
                });
                if (data.messages.length) {
                    cursor = data.messages[data.messages.length - 1].id;
                    if (panel.classList.contains('d-none') && data.messages.some(function (m) { return m.sender === 'agent'; })) {
                        toggle.classList.add('chat-has-unread');
                        toggle.setAttribute('aria-label', 'Chat with us: new reply');
                    }
                }
            } while (data.has_more);
            if (!notify || latestAgent) sounds.observe(id, latestAgent);
            transcriptLoaded = true;
            renderStatus();
        }
        async function poll(force) {
            if (polling || initialSending || !force && !conversation && document.visibilityState === 'hidden') return;
            polling = true;
            try {
                var data = await chat.request('/api/chat/status');
                online = data.online;
                if (data.conversation && !startingNew) useConversation(data.conversation);
                await transcript();
                renderStatus();
            } catch (err) { error(err.message || 'Could not connect to chat. Please retry or call us.'); }
            finally { polling = false; }
        }
        function closePanel() {
            panel.classList.add('d-none');
            toggle.setAttribute('aria-expanded', 'false');
            toggle.querySelector('i').className = 'bi bi-chat-dots-fill';
            toggle.focus();
        }
        toggle.addEventListener('click', function () {
            if (panel.classList.contains('d-none')) {
                panel.classList.remove('d-none');
                toggle.setAttribute('aria-expanded', 'true');
                toggle.setAttribute('aria-label', 'Chat with us');
                toggle.classList.remove('chat-has-unread');
                toggle.querySelector('i').className = 'bi bi-x-lg';
                poll(true);
                (conversation && !conversation.closed ? reply : form.querySelector('[name="name"]')).focus();
            } else { closePanel(); }
        });
        document.getElementById('chat-widget-close').addEventListener('click', closePanel);
        panel.addEventListener('keydown', function (event) { if (event.key === 'Escape') closePanel(); });
        form.addEventListener('submit', async function (event) {
            event.preventDefault();
            if (initialSending) return;
            initialSending = true;
            clearError();
            var button = form.querySelector('[type="submit"]');
            button.disabled = true;
            button.textContent = 'Sending...';
            var data = new FormData(form);
            var liveRequested = online;
            if (liveRequested) data.set('chat_mode', 'live');
            try {
                var result;
                try { result = await chat.request('/contact-submit', { method: 'POST', body: data }); }
                catch (err) {
                    if (!liveRequested || err.status !== 409) throw err;
                    online = false;
                    data.delete('chat_mode');
                    result = await chat.request('/contact-submit', { method: 'POST', body: data });
                }
                if (result.conversation) {
                    startingNew = false;
                    useConversation(result.conversation);
                    await transcript();
                    reply.focus();
                } else {
                    form.classList.add('d-none');
                    successEl.classList.remove('d-none');
                }
                if (window.MarshallAnalytics) window.MarshallAnalytics.trackLead({ source: result.conversation ? 'live_chat' : 'live_chat_widget' });
            } catch (err) { error(err.message || 'Network error. Your message was not confirmed; retry or call us.'); }
            finally {
                initialSending = false;
                button.disabled = false;
                renderStatus();
            }
        });
        replyForm.addEventListener('submit', async function (event) {
            event.preventDefault();
            if (!conversation) return;
            clearError();
            var button = replyForm.querySelector('[type="submit"]');
            if (button.disabled) return;
            button.disabled = true;
            var body = reply.value;
            if (!pending || pending.body !== body) pending = { body: body, client_nonce: chat.nonce() };
            try {
                await chat.post('/api/chat/messages', pending, csrf);
                reply.value = '';
                pending = null;
                await transcript();
            } catch (err) { error(err.message || 'Message not confirmed. Retry to send without creating a duplicate.'); }
            finally { button.disabled = false; }
        });
        end.addEventListener('click', async function () {
            end.disabled = true;
            try {
                await chat.post('/api/chat/end', {}, csrf);
                conversation.closed = true;
                renderStatus();
            } catch (err) { error(err.message || 'Could not end the chat. Please retry.'); }
            finally { end.disabled = false; }
        });
        newChat.addEventListener('click', function () {
            startingNew = true;
            conversation = null;
            live.classList.add('d-none');
            form.classList.remove('d-none');
            form.querySelector('[name="message"]').value = '';
            clearError();
            renderStatus();
            form.querySelector('[name="message"]').focus();
        });
        poll(true);
        window.setInterval(function () {
            if (conversation || !panel.classList.contains('d-none')) poll();
        }, 5000);
        document.addEventListener('visibilitychange', function () {
            if (document.visibilityState === 'visible' && (conversation || !panel.classList.contains('d-none'))) poll();
        });
    });
})();
