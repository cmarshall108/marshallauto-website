(function () {
    'use strict';
    const root = document.getElementById('vehicle-share');
    const input = document.getElementById('vehicle-share-url');
    const button = document.getElementById('vehicle-share-button');
    const status = document.getElementById('vehicle-share-status');
    if (!root || !input || !button || !status) return;
    if (!navigator.share && !(navigator.clipboard && navigator.clipboard.writeText)) return;
    button.hidden = false;
    button.textContent = navigator.share ? 'Share' : 'Copy';
    button.addEventListener('click', async function () {
        try {
            if (navigator.share) {
                await navigator.share({ title: root.dataset.title, url: input.value });
            } else {
                await navigator.clipboard.writeText(input.value);
                status.textContent = 'Link copied.';
            }
        } catch (error) {
            if (error.name === 'AbortError') return;
            status.textContent = 'Could not share automatically. Select and copy the link above.';
        }
    });
})();
