/**
 * List printers installed on this computer and scan a page from the selected one.
 * The public site cannot see macOS printers, so this talks to the local agent.
 */
(function () {
    'use strict';

    const AGENT = 'http://127.0.0.1:8765';
    const printerSelect = document.getElementById('sale-printer-select');
    const printerLabel = document.getElementById('sale-printer-label');
    const scanBtn = document.getElementById('sale-scan-start');
    const idScanBtn = document.getElementById('sale-id-scan-start');
    const clearBtn = document.getElementById('sale-scan-clear');
    const pages = document.getElementById('sale-scan-pages');
    const idPages = document.getElementById('sale-id-scan-pages');
    const fields = document.getElementById('sale-scan-fields');
    const idFields = document.getElementById('sale-id-scan-fields');
    const errorEl = document.getElementById('sale-scan-error');
    const idErrorEl = document.getElementById('sale-id-scan-error');
    const fileInput = document.querySelector('input[type="file"][name="scanned_documents"]');

    if (!printerSelect || !scanBtn || !fields) return;

    const scans = [];
    const idScans = [];

    function showError(message, target) {
        const element = target || errorEl;
        if (!element) return;
        element.textContent = message;
        element.classList.remove('d-none');
    }

    function clearError(target) {
        const element = target || errorEl;
        if (!element) return;
        element.textContent = '';
        element.classList.add('d-none');
    }

    function selectedPrinterLabel() {
        const option = printerSelect.selectedOptions[0];
        return option && option.value ? option.textContent.trim() : '';
    }

    function syncPrinterLabel() {
        if (printerLabel) printerLabel.value = selectedPrinterLabel();
    }

    function renderScanList(items, pageRoot, fieldRoot, fieldName, altPrefix) {
        if (!pageRoot || !fieldRoot) return;
        pageRoot.innerHTML = '';
        fieldRoot.innerHTML = '';
        items.forEach((scan, index) => {
            const column = document.createElement('div');
            column.className = 'col-12 col-md-6';
            const image = document.createElement('img');
            image.src = scan.url;
            image.alt = `${altPrefix} ${index + 1}`;
            image.className = 'w-100 rounded border bg-white';
            column.appendChild(image);
            pageRoot.appendChild(column);

            const input = document.createElement('input');
            input.type = 'hidden';
            input.name = fieldName;
            input.value = scan.dataUrl;
            fieldRoot.appendChild(input);
        });
    }

    function renderScans() {
        renderScanList(scans, pages, fields, 'scanned_document_data', 'Scanned page');
        renderScanList(idScans, idPages, idFields, 'buyer_id_scan_data', 'Scanned customer ID');
        if (clearBtn) clearBtn.classList.toggle('d-none', scans.length === 0);
    }

    async function loadPrinters() {
        printerSelect.replaceChildren(new Option('Looking for printers…', ''));
        try {
            const response = await fetch(`${AGENT}/printers`, { cache: 'no-store' });
            const payload = await response.json();
            if (!response.ok) throw new Error(payload.error || 'Could not list printers.');
            const printers = payload.printers || [];
            printerSelect.replaceChildren(new Option('Select a printer or scanner', ''));
            printers.forEach((printer) => {
                printerSelect.add(new Option(printer.label, printer.id));
            });
            if (printers.length === 1) {
                printerSelect.value = printers[0].id;
                syncPrinterLabel();
            }
            if (!printers.length) {
                showError('No scanner was found on this computer. Attach a scanned file below instead.');
            }
        } catch (error) {
            printerSelect.replaceChildren(new Option('Select a printer or scanner', ''));
            showError('Printers on this computer are unavailable. Start the local printer agent, then reload this page.');
        }
    }

    async function scanSelectedPrinter(target) {
        const isId = target === 'id';
        const button = isId ? idScanBtn : scanBtn;
        const bucket = isId ? idScans : scans;
        const errorTarget = isId ? idErrorEl : errorEl;
        clearError(errorTarget);
        syncPrinterLabel();
        if (!printerSelect.value) {
            showError('Select a printer or scanner before scanning.', errorTarget);
            return;
        }
        if (!isId && bucket.length >= 20) {
            showError('Scan no more than 20 pages before saving.', errorTarget);
            return;
        }
        if (isId && bucket.length >= 2) {
            showError('Scan no more than the front and back of the ID before saving.', errorTarget);
            return;
        }
        const original = button.innerHTML;
        scanBtn.disabled = true;
        if (idScanBtn) idScanBtn.disabled = true;
        button.textContent = 'Scanning…';
        try {
            const response = await fetch(`${AGENT}/scan`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ printer_id: printerSelect.value, purpose: isId ? 'id' : 'document' }),
            });
            if (!response.ok) {
                let message = 'The selected printer could not scan.';
                try {
                    const payload = await response.json();
                    if (payload.error) message = payload.error;
                } catch (parseError) {
                    message = 'The selected printer could not scan.';
                }
                throw new Error(message);
            }
            const bytes = new Uint8Array(await response.arrayBuffer());
            const contentType = (response.headers.get('Content-Type') || 'image/jpeg').split(';', 1)[0];
            if (isId && !contentType.startsWith('image/')) {
                throw new Error('The customer ID scan must be an image. Place the ID on the glass and scan again.');
            }
            let binary = '';
            const chunk = 8192;
            for (let offset = 0; offset < bytes.length; offset += chunk) {
                binary += String.fromCharCode.apply(null, bytes.subarray(offset, offset + chunk));
            }
            const dataUrl = `data:${contentType};base64,${btoa(binary)}`;
            bucket.push({ dataUrl, url: URL.createObjectURL(new Blob([bytes], { type: contentType })) });
            syncPrinterLabel();
            renderScans();
        } catch (error) {
            showError(error && error.message ? error.message : 'The selected printer could not scan.', errorTarget);
        } finally {
            scanBtn.disabled = false;
            if (idScanBtn) idScanBtn.disabled = false;
            button.innerHTML = original;
        }
    }

    printerSelect.addEventListener('change', syncPrinterLabel);
    scanBtn.addEventListener('click', () => scanSelectedPrinter('document'));
    if (idScanBtn) idScanBtn.addEventListener('click', () => scanSelectedPrinter('id'));
    clearBtn.addEventListener('click', () => {
        scans.forEach((scan) => URL.revokeObjectURL(scan.url));
        scans.length = 0;
        renderScans();
    });
    if (fileInput) fileInput.addEventListener('change', syncPrinterLabel);
    const form = scanBtn.closest('form');
    if (form) form.addEventListener('submit', syncPrinterLabel);
    loadPrinters();
})();
