(function () {
    'use strict';
    var root = document.getElementById('test-drive-reminders');
    if (!root) return;
    var list = document.getElementById('test-drive-reminder-list');
    var error = document.getElementById('test-drive-reminder-error');
    var busy = false;

    async function refresh() {
        if (busy) return;
        busy = true;
        try {
            var response = await fetch(root.dataset.url, {credentials: 'same-origin'});
            if (!response.ok || !(response.headers.get('content-type') || '').includes('application/json')) {
                throw new Error('Reminders could not be loaded');
            }
            var data = await response.json();
            list.replaceChildren();
            data.reminders.forEach(function (reminder) {
                var alert = document.createElement('div');
                alert.className = 'alert alert-info d-flex flex-wrap align-items-center gap-2';
                var message = document.createElement('span');
                message.textContent = (reminder.phase === 'due' ? 'Test drive scheduled now: ' : 'Test drive reminder (30 minutes before): ')
                    + reminder.customer + ' — ' + reminder.vehicle + ', ' + reminder.time;
                var link = document.createElement('a');
                link.href = '/admin/test-drives/calendar';
                link.textContent = 'Calendar';
                var button = document.createElement('button');
                button.className = 'btn btn-sm btn-outline-secondary ms-auto';
                button.type = 'button';
                button.textContent = 'Dismiss';
                button.addEventListener('click', async function () {
                    button.disabled = true;
                    try {
                        var body = new FormData();
                        body.set('csrf_token', root.dataset.csrf);
                        var result = await fetch('/admin/test-drives/' + encodeURIComponent(reminder.id)
                            + '/reminders/' + encodeURIComponent(reminder.phase) + '/dismiss', {
                            method: 'POST', credentials: 'same-origin', body: body,
                        });
                        if (!result.ok || !(result.headers.get('content-type') || '').includes('application/json')) {
                            throw new Error('Reminder could not be dismissed');
                        }
                        alert.remove();
                        error.hidden = true;
                    } catch (dismissError) {
                        error.textContent = 'Could not dismiss the test drive reminder. Check your connection and try again.';
                        error.hidden = false;
                        button.disabled = false;
                        console.warn(dismissError);
                    }
                });
                alert.append(message, link, button);
                list.append(alert);
            });
            error.hidden = true;
        } catch (refreshError) {
            error.textContent = 'Test drive reminders could not refresh. Check your connection or sign in again.';
            error.hidden = false;
            console.warn(refreshError);
        } finally {
            busy = false;
        }
    }
    refresh();
    window.setInterval(refresh, 30000);
}());
