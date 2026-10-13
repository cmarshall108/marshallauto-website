(function () {
    'use strict';
    document.addEventListener('DOMContentLoaded', function () {
        var root = document.getElementById('live-chat-inbox');
        var chat = window.MarshallChat;
        if (!root || !chat) return;
        var csrf = root.dataset.csrf;
        var selected = null, cursor = 0, pageNumber = 1, refreshing = false;
        var pending = null, transcriptPending = null, drafts = {};
        var list = document.getElementById('chat-conversations');
        var log = document.getElementById('chat-inbox-messages');
        var filter = document.getElementById('chat-inbox-filter');
        var form = document.getElementById('chat-inbox-reply');
        var body = document.getElementById('chat-inbox-body');
        var errorEl = document.getElementById('chat-inbox-error');
        function error(message) {
            errorEl.textContent = message;
            errorEl.classList.remove('d-none');
        }
        function clearError() { errorEl.classList.add('d-none'); }
        function api(id, suffix) { return '/admin/api/chat/' + id + '/' + suffix; }
        function selectConversation(conversation) {
            if (selected && selected.id === conversation.id) return;
            if (selected) drafts[selected.id] = body.value;
            selected = conversation;
            cursor = 0;
            pending = null;
            body.value = drafts[conversation.id] || '';
            log.replaceChildren();
            document.getElementById('chat-inbox-empty').classList.add('d-none');
            document.getElementById('chat-inbox-conversation').classList.remove('d-none');
            document.getElementById('chat-customer-name').textContent = conversation.name;
            document.getElementById('chat-customer-contact').textContent = [conversation.email, conversation.phone].filter(Boolean).join(' | ');
            var leadLink = document.getElementById('chat-customer-lead');
            leadLink.href = '/admin/leads' + (conversation.lead_id ? '?lead_id=' + conversation.lead_id : '');
            clearError();
            // A pending request for the previous selection checks its ID before rendering.
            transcript().then(function () { return refresh(true); }).catch(function (err) { error(err.message || 'Could not load the conversation.'); });
        }
        function transcript() {
            if (!selected) return Promise.resolve();
            if (transcriptPending) {
                return transcriptPending.then(function () {
                    return selected ? transcript() : undefined;
                });
            }
            transcriptPending = loadTranscript().finally(function () { transcriptPending = null; });
            return transcriptPending;
        }
        async function loadTranscript() {
            var id = selected.id, data;
            do {
                data = await chat.request(api(id, 'messages') + '?after=' + cursor);
                if (!selected || selected.id !== id) return;
                selected = data.conversation;
                chat.append(log, data.messages, 'agent');
                if (data.messages.length) cursor = data.messages[data.messages.length - 1].id;
            } while (data.has_more);
            form.classList.toggle('d-none', selected.closed);
            document.getElementById('chat-inbox-end').classList.toggle('d-none', selected.closed);
            document.getElementById('chat-inbox-block').textContent = selected.blocked ? 'Unblock chat' : 'Block as spam';
            document.getElementById('chat-conversation-status').textContent = selected.blocked
                ? 'Blocked as spam. This browser cannot send more live-chat messages. Unblocking does not reopen the ended chat.'
                : selected.suspicious
                ? 'Flagged for review: possible spam. Check the transcript before replying.'
                : selected.closed
                ? 'This chat has ended. The transcript is retained for follow-up.'
                : 'Open conversation. Replies are saved even if the customer has closed the widget.';
            if (document.visibilityState === 'visible') {
                await chat.post(api(id, 'read'), { through: cursor }, csrf);
            }
        }
        async function refresh(force) {
            if (refreshing || !force && document.visibilityState === 'hidden') return;
            refreshing = true;
            try {
                var filterValue = filter.value, requestedPage = pageNumber;
                var data = await chat.request('/admin/api/chat/conversations?page=' + requestedPage + '&closed=' + (filterValue === 'closed'));
                if (filter.value !== filterValue || pageNumber !== requestedPage) return;
                list.replaceChildren();
                if (!data.conversations.length) {
                    var empty = document.createElement('p');
                    empty.className = 'small text-muted';
                    empty.textContent = 'No conversations in this view.';
                    list.appendChild(empty);
                }
                data.conversations.forEach(function (conversation) {
                    var button = document.createElement('button');
                    button.type = 'button';
                    button.className = 'chat-conversation-item';
                    button.setAttribute('aria-pressed', String(!!selected && selected.id === conversation.id));
                    var title = document.createElement('strong');
                    title.textContent = conversation.name + (conversation.unread ? ' - New message' : '')
                        + (conversation.blocked ? ' - Blocked' : conversation.suspicious ? ' - Review for spam' : '');
                    var detail = document.createElement('span');
                    detail.className = 'small text-muted d-block';
                    detail.textContent = new Date(conversation.updated_at).toLocaleString();
                    button.append(title, detail);
                    button.addEventListener('click', function () { selectConversation(conversation); });
                    list.appendChild(button);
                });
                document.getElementById('chat-page').textContent = 'Page ' + pageNumber;
                document.getElementById('chat-page-prev').disabled = pageNumber <= 1;
                document.getElementById('chat-page-next').disabled = !data.has_more;
                await transcript();
            } catch (err) { error(err.message || 'Could not refresh the inbox. Please retry.'); }
            finally { refreshing = false; }
        }
        function changeView() {
            if (selected) drafts[selected.id] = body.value;
            selected = null;
            document.getElementById('chat-inbox-conversation').classList.add('d-none');
            document.getElementById('chat-inbox-empty').classList.remove('d-none');
            refresh(true);
        }
        filter.addEventListener('change', function () { pageNumber = 1; changeView(); });
        document.getElementById('chat-page-prev').addEventListener('click', function () { pageNumber -= 1; changeView(); });
        document.getElementById('chat-page-next').addEventListener('click', function () { pageNumber += 1; changeView(); });
        form.addEventListener('submit', async function (event) {
            event.preventDefault();
            if (!selected) return;
            var button = form.querySelector('[type="submit"]'), id = selected.id, text = body.value;
            if (button.disabled) return;
            button.disabled = true;
            clearError();
            if (!pending || pending.body !== text || pending.id !== id) {
                pending = { id: id, body: text, client_nonce: chat.nonce() };
            }
            try {
                await chat.post(api(id, 'messages'), { body: pending.body, client_nonce: pending.client_nonce }, csrf);
                drafts[id] = '';
                if (selected && selected.id === id) {
                    body.value = '';
                    pending = null;
                    await transcript();
                }
                await refresh(true);
            } catch (err) { error(err.message || 'Reply not confirmed. Retry to send without creating a duplicate.'); }
            finally { button.disabled = false; }
        });
        document.getElementById('chat-inbox-end').addEventListener('click', async function () {
            if (!selected) return;
            var button = this, id = selected.id;
            button.disabled = true;
            try {
                await chat.post(api(id, 'end'), {}, csrf);
                await transcript();
                await refresh(true);
            } catch (err) { error(err.message || 'Could not end the conversation.'); }
            finally { button.disabled = false; }
        });
        document.getElementById('chat-inbox-block').addEventListener('click', async function () {
            if (!selected) return;
            var button = this, id = selected.id, blocked = !selected.blocked;
            if (!window.confirm(blocked
                ? 'Block this chat as spam and move its lead to Spam? This ends the chat and prevents this browser from starting another live chat.'
                : 'Unblock this browser and restore the linked lead? The ended chat stays closed.')) return;
            button.disabled = true;
            clearError();
            try {
                await chat.post(api(id, 'block'), { blocked: blocked }, csrf);
                await transcript();
                await refresh(true);
            } catch (err) { error(err.message || 'Could not update chat blocking. Please retry.'); }
            finally { button.disabled = false; }
        });
        refresh(true);
        window.setInterval(function () { refresh(); }, 5000);
        document.addEventListener('visibilitychange', function () {
            if (document.visibilityState === 'visible') refresh();
        });
    });
})();
