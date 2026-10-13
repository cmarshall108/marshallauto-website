(function () {
    'use strict';
    document.addEventListener('DOMContentLoaded', function () {
        var root = document.getElementById('admin-chat-monitor');
        var chat = window.MarshallChat;
        if (!root || !chat) return;
        var button = document.getElementById('admin-chat-sounds');
        var sounds = window.MarshallChatSounds ? window.MarshallChatSounds.create(button, 'admin') : null;
        var status = document.getElementById('admin-chat-notification-status');
        var presenceStatus = document.getElementById('admin-chat-presence-status');
        var action = document.querySelector('.admin-chat-action');
        var toast = document.getElementById('admin-chat-toast');
        var busy = false, presenceBusy = false, active = true;
        var presenceId = chat.nonce(), toastTimer = null;
        var alertsFailed = false, presenceFailed = false, storageFailed = false;
        var cursorKey = 'marshall-admin-chat-cursor-' + root.dataset.userId;
        var latest = null;
        try {
            var stored = window.sessionStorage.getItem(cursorKey);
            if (stored !== null && /^\d+$/.test(stored)) latest = Number(stored);
        } catch (error) {
            storageFailed = true;
            console.warn('Chat alert history could not be restored:', error);
        }
        function notify(title, message) {
            document.getElementById('admin-chat-toast-title').textContent = title;
            document.getElementById('admin-chat-toast-message').textContent = message;
            toast.hidden = false;
            window.clearTimeout(toastTimer);
            toastTimer = window.setTimeout(function () { toast.hidden = true; }, 12000);
        }
        document.getElementById('admin-chat-toast-dismiss').addEventListener('click', function () {
            toast.hidden = true;
            window.clearTimeout(toastTimer);
        });
        function release(identifier) {
            var data = new FormData();
            data.set('csrf_token', root.dataset.csrf);
            data.set('id', identifier);
            data.set('available', 'false');
            data.set('closing', 'true');
            if (navigator.sendBeacon && navigator.sendBeacon('/admin/api/chat/presence', data)) return;
            chat.request('/admin/api/chat/presence', {
                method: 'POST', body: data, keepalive: true,
            }).catch(function (error) {
                console.warn('Chat could not release availability; it will expire automatically:', error);
            });
        }
        async function heartbeat() {
            if (presenceBusy || !active) return;
            presenceBusy = true;
            var identifier = presenceId;
            try {
                await chat.post('/admin/api/chat/presence', { id: identifier, available: true }, root.dataset.csrf);
                if (!active || identifier !== presenceId) {
                    release(identifier);
                    return;
                }
                presenceStatus.textContent = 'You are available for live chat';
                presenceFailed = false;
            } catch (error) {
                presenceStatus.textContent = 'Availability disconnected; expires within 90 seconds';
                if (active && !presenceFailed) notify('Chat availability alert', 'Availability could not be renewed. Check your connection or sign in again.');
                presenceFailed = true;
                console.warn('Admin chat availability could not refresh:', error);
            } finally {
                presenceBusy = false;
                if (active && identifier !== presenceId) heartbeat();
            }
        }
        async function poll() {
            if (busy || !active) return;
            busy = true;
            try {
                var data = await chat.request('/admin/api/chat/notifications');
                if (!active) return;
                if (sounds) sounds.observe('inbox', data.latest_customer_message_id);
                if (data.unread > 0 && (latest === null || data.latest_customer_message_id > latest)) {
                    notify('New chat message', data.unread + (data.unread === 1 ? ' conversation needs' : ' conversations need') + ' your attention.');
                }
                latest = data.latest_customer_message_id;
                if (!storageFailed) {
                    try { window.sessionStorage.setItem(cursorKey, String(latest)); }
                    catch (error) {
                        storageFailed = true;
                        console.warn('Chat alert history could not be saved:', error);
                    }
                }
                alertsFailed = false;
                status.textContent = data.unread ? data.unread + ' unread chats' : 'No unread chats';
                if (action) {
                    action.classList.toggle('chat-has-unread', data.unread > 0);
                    action.setAttribute('aria-label', 'Open Live Chat inbox: ' + data.unread + ' unread chats');
                }
            } catch (error) {
                status.textContent = 'Chat alerts disconnected';
                if (active && !alertsFailed) notify('Chat connection alert', 'New message alerts could not refresh. Check your connection or sign in again.');
                alertsFailed = true;
                console.warn('Admin chat alerts could not refresh:', error);
            } finally { busy = false; }
        }
        heartbeat();
        poll();
        window.setInterval(heartbeat, 20000);
        window.setInterval(poll, 5000);
        document.addEventListener('visibilitychange', function () {
            if (document.visibilityState === 'visible') {
                heartbeat();
                poll();
            }
        });
        window.addEventListener('pagehide', function () {
            active = false;
            release(presenceId);
        });
        window.addEventListener('pageshow', function (event) {
            if (!event.persisted) return;
            presenceId = chat.nonce();
            active = true;
            heartbeat();
            poll();
        });
    });
})();
