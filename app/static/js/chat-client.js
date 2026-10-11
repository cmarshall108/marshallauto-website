/* Shared transport and safe transcript rendering for visitor and staff chat. */
(function () {
    'use strict';
    function request(url, options) {
        options = options || {};
        options.credentials = 'same-origin';
        options.headers = Object.assign({ Accept: 'application/json', 'X-Requested-With': 'XMLHttpRequest' }, options.headers || {});
        return fetch(url, options).then(async function (response) {
            var data;
            try { data = await response.json(); }
            catch (error) {
                throw new Error('Chat returned an unexpected response. Refresh the page or try again.');
            }
            if (!response.ok || !data.success) {
                var message = data.message || 'Chat request failed. Please try again.';
                if (data.errors) {
                    var key = Object.keys(data.errors)[0];
                    if (key && data.errors[key][0]) message = data.errors[key][0];
                }
                var error = new Error(message);
                error.status = response.status;
                throw error;
            }
            return data;
        });
    }
    function post(url, data, csrf) {
        return request(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
            body: JSON.stringify(data),
        });
    }
    function nonce() {
        if (window.crypto && window.crypto.randomUUID) return window.crypto.randomUUID();
        return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
            var r = Math.random() * 16 | 0;
            return (c === 'x' ? r : (r & 3 | 8)).toString(16);
        });
    }
    function append(log, messages, ownSender) {
        var nearBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 60;
        messages.forEach(function (message) {
            var item = document.createElement('div');
            item.className = 'chat-message' + (message.sender === ownSender ? ' chat-message-own' : '');
            var sender = document.createElement('strong');
            sender.className = 'chat-message-sender';
            sender.textContent = message.sender === 'agent' ? 'Marshall Auto team' : 'Customer';
            var text = document.createElement('p');
            text.textContent = message.body;
            var time = document.createElement('time');
            time.dateTime = message.created_at;
            time.textContent = new Date(message.created_at).toLocaleString([], {
                month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
            });
            item.append(sender, text, time);
            if (ownSender === 'agent' && message.suspicious) {
                var warning = document.createElement('small');
                warning.className = 'chat-spam-warning';
                warning.textContent = 'Review: possible spam. ' + (message.spam_reasons || '');
                item.append(warning);
            }
            log.appendChild(item);
        });
        if (nearBottom) log.scrollTop = log.scrollHeight;
    }
    window.MarshallChat = { request: request, post: post, nonce: nonce, append: append };
})();
