(function () {
    const form = document.querySelector('[data-mvr-overlay]');
    if (!form) {
        return;
    }
    form.querySelectorAll('input[type="radio"]').forEach(function (radio) {
        radio.addEventListener('click', function () {
            if (radio.dataset.wasChecked === 'true') {
                radio.checked = false;
                radio.dataset.wasChecked = 'false';
                return;
            }
            form.querySelectorAll('input[name="' + radio.name + '"]').forEach(function (peer) {
                peer.dataset.wasChecked = 'false';
            });
            radio.dataset.wasChecked = 'true';
        });
    });
}());
