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
    const dataField = document.getElementById('license_image_data');
    const fileInput = document.querySelector('input[type="file"][name="license_image"]');

    if (!startBtn || !dataField) return;

    let stream = null;

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
            }
        });
    }

    window.addEventListener('beforeunload', stopStream);
})();
