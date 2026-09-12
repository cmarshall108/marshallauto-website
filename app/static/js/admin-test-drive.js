/**
 * In-browser camera capture for the driver's license photo on the test-drive form.
 * Works with any camera the OS exposes to the browser (webcam, or a document
 * scanner/scan station registered as a UVC video device) — no native driver
 * integration is possible from a web page, so this is the closest equivalent
 * to "connect to a scanner". A plain file upload (below) covers flatbed/network
 * scanners that save an image file instead.
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
    const fileInput = document.querySelector('input[type="file"][name="license_image"]');

    if (!startBtn || !dataField) return;

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
        if (fileInput && fileInput.files.length) {
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

    startBtn.addEventListener('click', startCamera);
    captureBtn.addEventListener('click', capturePhoto);
    stopBtn.addEventListener('click', stopStream);
    retakeBtn.addEventListener('click', retake);

    if (fileInput) {
        fileInput.addEventListener('change', () => {
            if (fileInput.files && fileInput.files.length) {
                dataField.value = '';
                previewWrap.classList.add('d-none');
                analyzeLicense();
            }
        });
    }

    window.addEventListener('beforeunload', stopStream);
})();
