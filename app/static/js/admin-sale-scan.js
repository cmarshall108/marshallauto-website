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
    const clearBtn = document.getElementById('sale-scan-clear');
    const pages = document.getElementById('sale-scan-pages');
    const fields = document.getElementById('sale-scan-fields');
    const errorEl = document.getElementById('sale-scan-error');
    const fileInput = document.querySelector('input[type="file"][name="scanned_documents"]');

    if (!printerSelect || !scanBtn || !fields) return;

    const scans = [];

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

    function renderScans() {
        pages.innerHTML = '';
        fields.innerHTML = '';
        scans.forEach((scan, index) => {
            const column = document.createElement('div');
            column.className = 'col-12 col-md-6';
            const frame = document.createElement('iframe');
            frame.src = scan.url;
            frame.title = `Scanned page ${index + 1}`;
            frame.className = 'w-100 rounded border';
            frame.style.height = '280px';
            column.appendChild(frame);
            pages.appendChild(column);

            const input = document.createElement('input');
            input.type = 'hidden';
            input.name = 'scanned_document_data';
            input.value = scan.dataUrl;
            fields.appendChild(input);
        });
        clearBtn.classList.toggle('d-none', scans.length === 0);
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

    async function scanSelectedPrinter() {
        clearError();
        syncPrinterLabel();
        if (!printerSelect.value) {
            showError('Select a printer or scanner before scanning.');
            return;
        }
        if (scans.length >= 20) {
            showError('Scan no more than 20 pages before saving.');
            return;
        }
        const original = scanBtn.innerHTML;
        scanBtn.disabled = true;
        scanBtn.textContent = 'Scanning…';
        try {
            const response = await fetch(`${AGENT}/scan`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ printer_id: printerSelect.value }),
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
            let binary = '';
            const chunk = 8192;
            for (let offset = 0; offset < bytes.length; offset += chunk) {
                binary += String.fromCharCode.apply(null, bytes.subarray(offset, offset + chunk));
            }
            const dataUrl = `data:application/pdf;base64,${btoa(binary)}`;
            scans.push({ dataUrl, url: URL.createObjectURL(new Blob([bytes], { type: 'application/pdf' })) });
            syncPrinterLabel();
            renderScans();
        } catch (error) {
            showError(error && error.message ? error.message : 'The selected printer could not scan.');
        } finally {
            scanBtn.disabled = false;
            scanBtn.innerHTML = original;
        }
    }

    printerSelect.addEventListener('change', syncPrinterLabel);
    scanBtn.addEventListener('click', scanSelectedPrinter);
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
