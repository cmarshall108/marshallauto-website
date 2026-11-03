(function () {
    'use strict';

    const form = document.getElementById('sale-receipt-form');
    const chargesRoot = document.getElementById('receipt-other-charges');
    const addChargeButton = document.getElementById('receipt-add-charge');
    const chargesField = document.getElementById('other_charges_data');
    const totalDisplay = document.getElementById('receipt-total');
    if (!form || !chargesRoot || !addChargeButton || !chargesField || !totalDisplay) return;

    const money = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' });

    function amountInCents(value) {
        const amount = Number.parseFloat(value);
        return Number.isFinite(amount) && amount > 0 ? Math.round(amount * 100) : 0;
    }

    function addCharge(description = '', amount = '') {
        if (chargesRoot.children.length >= 20) return;
        const row = document.createElement('div');
        row.className = 'row g-2 align-items-center';
        const label = document.createElement('div');
        label.className = 'col-md-6';
        const descriptionField = document.createElement('input');
        descriptionField.type = 'text';
        descriptionField.maxLength = 128;
        descriptionField.required = true;
        descriptionField.className = 'form-control';
        descriptionField.placeholder = 'Charge description';
        descriptionField.setAttribute('aria-label', 'Other charge description');
        descriptionField.value = description;
        label.appendChild(descriptionField);

        const value = document.createElement('div');
        value.className = 'col-md-4';
        const amountField = document.createElement('input');
        amountField.type = 'number';
        amountField.min = '0';
        amountField.max = '99999999.99';
        amountField.step = '0.01';
        amountField.required = true;
        amountField.className = 'form-control receipt-other-amount';
        amountField.placeholder = 'Amount ($)';
        amountField.setAttribute('aria-label', 'Other charge amount in dollars');
        amountField.value = amount;
        value.appendChild(amountField);

        const remove = document.createElement('div');
        remove.className = 'col-md-2';
        const removeButton = document.createElement('button');
        removeButton.type = 'button';
        removeButton.className = 'btn btn-outline-danger w-100';
        removeButton.textContent = 'Remove';
        removeButton.addEventListener('click', () => {
            row.remove();
            updateTotal();
        });
        remove.appendChild(removeButton);
        row.append(label, value, remove);
        chargesRoot.appendChild(row);
        descriptionField.addEventListener('input', updateTotal);
        amountField.addEventListener('input', updateTotal);
        updateTotal();
    }

    function updateTotal() {
        let cents = 0;
        form.querySelectorAll('.receipt-amount').forEach((field) => {
            cents += amountInCents(field.value);
        });
        chargesRoot.querySelectorAll('.receipt-other-amount').forEach((field) => {
            cents += amountInCents(field.value);
        });
        form.querySelectorAll('.receipt-credit').forEach((field) => {
            cents -= amountInCents(field.value);
        });
        cents = Math.max(0, cents);
        totalDisplay.textContent = money.format(cents / 100);
    }

    try {
        const existing = JSON.parse(window.initialSaleReceiptCharges || '[]');
        if (Array.isArray(existing)) {
            existing.slice(0, 20).forEach((charge) => {
                if (charge && typeof charge === 'object') {
                    addCharge(charge.description || '', charge.amount || '');
                }
            });
        }
    } catch (error) {
        chargesField.value = '[]';
    }

    addChargeButton.addEventListener('click', () => addCharge());
    form.querySelectorAll('.receipt-amount, .receipt-credit').forEach((field) => {
        field.addEventListener('input', updateTotal);
    });
    form.addEventListener('submit', () => {
        const charges = Array.from(chargesRoot.children).map((row) => ({
            description: row.querySelector('input[type="text"]').value.trim(),
            amount: row.querySelector('input[type="number"]').value,
        }));
        chargesField.value = JSON.stringify(charges);
    });
    updateTotal();
})();
