(function () {
    'use strict';
    document.addEventListener('DOMContentLoaded', function () {
        var button = document.getElementById('admin-chat-sounds');
        var chat = window.MarshallChat;
        if (!button || !chat || !window.MarshallChatSounds) return;
        var sounds = window.MarshallChatSounds.create(button, 'admin');
        var status = document.getElementById('admin-chat-notification-status');
        var action = document.querySelector('.admin-chat-action');
        var busy = false;
        async function poll() {
            if (busy) return;
            busy = true;
            try {
                var data = await chat.request('/admin/api/chat/notifications');
                sounds.observe('inbox', data.latest_customer_message_id);
                status.textContent = data.unread ? data.unread + ' unread chats' : 'No unread chats';
                if (action) {
                    action.classList.toggle('chat-has-unread', data.unread > 0);
                    action.setAttribute('aria-label', 'Open Live Chat inbox: ' + data.unread + ' unread chats');
                }
            } catch (error) {
                status.textContent = 'Chat alerts disconnected';
                console.warn('Admin chat alerts could not refresh:', error);
            } finally { busy = false; }
        }
        poll();
        window.setInterval(poll, 5000);
        document.addEventListener('visibilitychange', function () {
            if (document.visibilityState === 'visible') poll();
        });
    });
})();
