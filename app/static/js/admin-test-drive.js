/**
 * Scan a driver's license from the printer on this computer.
 * Camera capture and file upload remain available only when a scan is not used.
 */
(function () {
    'use strict';

    const startBtn = document.getElementById('td-camera-start');
    const captureBtn = document.getElementById('td-camera-capture');
    const retakeBtn = document.getElementById('td-camera-retake');
    const stopBtn = document.getElementById('td-camera-stop');
    const videoWrap = document.getElementById('td-camera-wrap');
    const video = document.getElementById('td-camera-video');
    const canvas = document.getElementById('td-camera-canvas');
    const previewWrap = document.getElementById('td-camera-preview-wrap');
    const preview = document.getElementById('td-camera-preview');
    const errorEl = document.getElementById('td-camera-error');
    const analysisEl = document.getElementById('td-license-analysis');
    const dataField = document.getElementById('license_image_data');
    const scanField = document.getElementById('license_scan_data');
    const printerSelect = document.getElementById('td-printer-select');
    const scanBtn = document.getElementById('td-license-scan');
    const scanError = document.getElementById('td-scan-error');
    const photoFallback = document.getElementById('td-photo-fallback');
    const fileInput = document.querySelector('input[type="file"][name="license_image"]');

    if (!dataField) return;

    let stream = null;
    let analysisRequest = 0;

    function setField(name, value) {
        const field = document.querySelector(`[name="${name}"]`);
        if (field && value && !field.value.trim()) field.value = value;
    }

    function showAnalysis(message, style) {
        if (!analysisEl) return;
        analysisEl.className = `alert alert-${style} mb-3`;
        analysisEl.textContent = message;
    }

    async function analyzeLicense() {
        const url = window.testDriveLicenseAnalysisUrl;
        if (!url || (!dataField.value && !(fileInput && fileInput.files.length))) return;
        const requestId = ++analysisRequest;
        showAnalysis('Analyzing license image...', 'info');
        const body = new FormData();
        const csrf = document.querySelector('input[name="csrf_token"]');
        if (csrf) body.append('csrf_token', csrf.value);
        if (scanField && scanField.value) {
            body.append('license_scan_data', scanField.value);
        } else if (fileInput && fileInput.files.length) {
            body.append('license_image', fileInput.files[0]);
        } else {
            body.append('license_image_data', dataField.value);
        }
        try {
            const response = await fetch(url, { method: 'POST', body });
            const result = await response.json();
            if (requestId !== analysisRequest) return;
            if (!response.ok || !result.ok) throw new Error(result.error || 'Analysis failed');

            const details = result.details || {};
            setField('customer_name', details.customer_name);
            setField('license_number', details.license_number);
            setField('license_state', details.license_state);
            setField('license_date_of_birth', details.date_of_birth);
            setField('license_expiration_date', details.expiration_date);
            setField('license_address', details.address);

            const authenticity = result.authenticity || {};
            const reasons = (authenticity.reasons || []).join(' ');
            if (authenticity.status === 'suspicious') {
                showAnalysis(`Potential authenticity concern. Verify this license manually before proceeding. ${reasons}`, 'danger');
            } else if (authenticity.status === 'appears_authentic') {
                showAnalysis(`Visual screening found no obvious alteration. Confirm the physical license and customer identity. ${reasons}`, 'success');
            } else {
                showAnalysis(`Authenticity could not be determined from this image. Verify the physical license manually. ${reasons}`, 'warning');
            }
        } catch (error) {
            if (requestId === analysisRequest) {
                showAnalysis(error.message || 'License analysis failed. Enter the details manually.', 'warning');
            }
        }
    }

    function showError(message) {
        if (!errorEl) return;
        errorEl.textContent = message;
        errorEl.classList.remove('d-none');
    }

    function clearError() {
        if (!errorEl) return;
        errorEl.classList.add('d-none');
        errorEl.textContent = '';
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

    async function startCamera() {
        clearError();
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            showError('Camera access is not supported in this browser. Use the file upload below instead.');
            return;
        }
        try {
            stream = await navigator.mediaDevices.getUserMedia({
                video: { facingMode: 'environment', width: { ideal: 1600 } },
                audio: false,
            });
            video.srcObject = stream;
            videoWrap.classList.remove('d-none');
            captureBtn.classList.remove('d-none');
            stopBtn.classList.remove('d-none');
            previewWrap.classList.add('d-none');
        } catch (err) {
            showError('Could not access a camera: ' + (err && err.message ? err.message : err));
        }
    }

    function capturePhoto() {
        if (!video.videoWidth) return;
        canvas.width = video.videoWidth;
        canvas.height = video.videoHeight;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
        const dataUrl = canvas.toDataURL('image/jpeg', 0.85);
        dataField.value = dataUrl;
        preview.src = dataUrl;
        previewWrap.classList.remove('d-none');
        retakeBtn.classList.remove('d-none');
        if (fileInput) fileInput.value = '';
        stopStream();
        analyzeLicense();
    }

    function retake() {
        previewWrap.classList.add('d-none');
        retakeBtn.classList.add('d-none');
        dataField.value = '';
        startCamera();
    }

    if (startBtn) startBtn.addEventListener('click', startCamera);
    if (captureBtn) captureBtn.addEventListener('click', capturePhoto);
    if (stopBtn) stopBtn.addEventListener('click', stopStream);
    if (retakeBtn) retakeBtn.addEventListener('click', retake);

    async function loadPrinters() {
        if (!printerSelect) return;
        printerSelect.replaceChildren(new Option('Looking for printers…', ''));
        try {
            const response = await fetch('http://127.0.0.1:8765/printers', { cache: 'no-store' });
            const payload = await response.json();
            if (!response.ok) throw new Error(payload.error || 'Could not list printers.');
            const printers = payload.printers || [];
            printerSelect.replaceChildren(new Option('Select a printer or scanner', ''));
            printers.forEach((printer) => printerSelect.add(new Option(printer.label, printer.id)));
            if (printers.length === 1) printerSelect.value = printers[0].id;
            const scannerReady = printers.length > 0;
            if (photoFallback) photoFallback.classList.toggle('d-none', scannerReady);
            if (!scannerReady && scanError) {
                scanError.textContent = 'No scanner was found on this computer. Take or upload a license photo instead.';
                scanError.classList.remove('d-none');
            }
        } catch (error) {
            printerSelect.replaceChildren(new Option('Select a printer or scanner', ''));
            if (photoFallback) photoFallback.classList.remove('d-none');
            if (scanError) {
                scanError.textContent = 'No scanner is available on this computer. Take or upload a license photo instead.';
                scanError.classList.remove('d-none');
            }
        }
    }

    async function scanLicense() {
        if (!scanBtn || !scanField || !printerSelect) return;
        if (scanError) scanError.classList.add('d-none');
        if (!printerSelect.value) {
            if (scanError) {
                scanError.textContent = 'Select a printer or scanner before scanning the license.';
                scanError.classList.remove('d-none');
            }
            return;
        }
        const original = scanBtn.innerHTML;
        scanBtn.disabled = true;
        scanBtn.textContent = 'Scanning…';
        try {
            const response = await fetch('http://127.0.0.1:8765/scan', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ printer_id: printerSelect.value, purpose: 'id' }),
            });
            if (!response.ok) {
                let message = 'The selected printer could not scan the license.';
                try {
                    const payload = await response.json();
                    if (payload.error) message = payload.error;
                } catch (parseError) {
                    message = 'The selected printer could not scan the license.';
                }
                throw new Error(message);
            }
            const bytes = new Uint8Array(await response.arrayBuffer());
            const contentType = (response.headers.get('Content-Type') || 'image/jpeg').split(';', 1)[0];
            if (!contentType.startsWith('image/')) {
                throw new Error('The license scan must be an image. Place the license on the glass and scan again.');
            }
            let binary = '';
            for (let offset = 0; offset < bytes.length; offset += 8192) {
                binary += String.fromCharCode.apply(null, bytes.subarray(offset, offset + 8192));
            }
            scanField.value = `data:${contentType};base64,${btoa(binary)}`;
            dataField.value = '';
            if (fileInput) fileInput.value = '';
            preview.src = URL.createObjectURL(new Blob([bytes], { type: contentType }));
            preview.alt = 'Scanned driver license';
            previewWrap.classList.remove('d-none');
            stopStream();
            analyzeLicense();
        } catch (error) {
            if (photoFallback) photoFallback.classList.remove('d-none');
            if (scanError) {
                scanError.textContent = error && error.message ? error.message : 'The selected printer could not scan the license.';
                scanError.classList.remove('d-none');
            }
        } finally {
            scanBtn.disabled = false;
            scanBtn.innerHTML = original;
        }
    }

    if (scanBtn) scanBtn.addEventListener('click', scanLicense);
    loadPrinters();

    if (fileInput) {
        fileInput.addEventListener('change', () => {
            if (fileInput.files && fileInput.files.length) {
                dataField.value = '';
                if (scanField) scanField.value = '';
                previewWrap.classList.add('d-none');
                analyzeLicense();
            }
        });
    }

    window.addEventListener('beforeunload', stopStream);
})();
