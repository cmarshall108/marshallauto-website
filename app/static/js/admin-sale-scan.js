/**
 * Capture every page from a printer/scanner the browser can see, then submit
 * those scans with the sale details for the vehicle currently open.
 */
(function () {
    'use strict';

    const printerSelect = document.getElementById('sale-printer-select');
    const printerLabel = document.getElementById('sale-printer-label');
    const startBtn = document.getElementById('sale-scan-start');
    const captureBtn = document.getElementById('sale-scan-capture');
    const stopBtn = document.getElementById('sale-scan-stop');
    const clearBtn = document.getElementById('sale-scan-clear');
    const videoWrap = document.getElementById('sale-scan-wrap');
    const video = document.getElementById('sale-scan-video');
    const canvas = document.getElementById('sale-scan-canvas');
    const pages = document.getElementById('sale-scan-pages');
    const fields = document.getElementById('sale-scan-fields');
    const errorEl = document.getElementById('sale-scan-error');
    const fileInput = document.querySelector('input[type="file"][name="scanned_documents"]');

    if (!printerSelect || !startBtn || !fields) return;

    let stream = null;
    const captures = [];

    function showError(message) {
        if (!errorEl) return;
        errorEl.textContent = message;
        errorEl.classList.remove('d-none');
    }

    function clearError() {
        if (!errorEl) return;
        errorEl.textContent = '';
        errorEl.classList.add('d-none');
    }

    function selectedPrinterLabel() {
        const option = printerSelect.selectedOptions[0];
        return option && option.value ? option.textContent.trim() : '';
    }

    function syncPrinterLabel() {
        if (printerLabel) printerLabel.value = selectedPrinterLabel();
    }

    function renderCaptures() {
        pages.innerHTML = '';
        fields.innerHTML = '';
        captures.forEach((dataUrl, index) => {
            const column = document.createElement('div');
            column.className = 'col-6 col-md-3';
            const image = document.createElement('img');
            image.src = dataUrl;
            image.alt = `Captured scan page ${index + 1}`;
            image.className = 'w-100 rounded border';
            column.appendChild(image);
            pages.appendChild(column);

            const input = document.createElement('input');
            input.type = 'hidden';
            input.name = 'scanned_document_data';
            input.value = dataUrl;
            fields.appendChild(input);
        });
        clearBtn.classList.toggle('d-none', captures.length === 0);
    }

    async function loadPrinters() {
        if (!navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) {
            showError('This browser cannot list printers. Attach the scanned files below instead.');
            return;
        }
        try {
            const devices = await navigator.mediaDevices.enumerateDevices();
            const cameras = devices.filter((device) => device.kind === 'videoinput');
            printerSelect.replaceChildren(new Option('Select a printer or scanner', ''));
            cameras.forEach((device, index) => {
                const label = device.label || `Printer / scanner ${index + 1}`;
                printerSelect.add(new Option(label, device.deviceId));
            });
            if (!cameras.length) {
                showError('No printer or scanner camera was found. Scan to a file, then attach it below.');
            }
        } catch (error) {
            showError('Could not list printers: ' + (error && error.message ? error.message : error));
        }
    }

    function stopStream() {
        if (stream) {
            stream.getTracks().forEach((track) => track.stop());
            stream = null;
        }
        videoWrap.classList.add('d-none');
        captureBtn.classList.add('d-none');
        stopBtn.classList.add('d-none');
    }

    async function startSelectedPrinter() {
        clearError();
        syncPrinterLabel();
        if (!printerSelect.value) {
            showError('Select a printer or scanner before capturing documents.');
            return;
        }
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            showError('This browser cannot capture from a printer. Attach the scanned files below instead.');
            return;
        }
        stopStream();
        try {
            stream = await navigator.mediaDevices.getUserMedia({
                video: {
                    deviceId: { exact: printerSelect.value },
                    width: { ideal: 1600 },
                },
                audio: false,
            });
            video.srcObject = stream;
            videoWrap.classList.remove('d-none');
            captureBtn.classList.remove('d-none');
            stopBtn.classList.remove('d-none');
        } catch (error) {
            showError('Could not open the selected printer: ' + (error && error.message ? error.message : error));
        }
    }

    function capturePage() {
        if (!video.videoWidth) return;
        if (captures.length >= 20) {
            showError('Capture no more than 20 pages before saving.');
            return;
        }
        canvas.width = video.videoWidth;
        canvas.height = video.videoHeight;
        canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
        captures.push(canvas.toDataURL('image/jpeg', 0.85));
        syncPrinterLabel();
        renderCaptures();
        clearError();
    }

    printerSelect.addEventListener('change', () => {
        syncPrinterLabel();
        stopStream();
    });
    startBtn.addEventListener('click', startSelectedPrinter);
    captureBtn.addEventListener('click', capturePage);
    stopBtn.addEventListener('click', stopStream);
    clearBtn.addEventListener('click', () => {
        captures.length = 0;
        renderCaptures();
    });
    if (fileInput) {
        fileInput.addEventListener('change', syncPrinterLabel);
    }
    const form = startBtn.closest('form');
    if (form) form.addEventListener('submit', syncPrinterLabel);
    window.addEventListener('beforeunload', stopStream);
    loadPrinters();
})();
