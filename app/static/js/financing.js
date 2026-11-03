(() => {
    'use strict';

    document.addEventListener('DOMContentLoaded', () => {
        document.querySelectorAll('.navbar-toggler').forEach((toggle) => {
            toggle.addEventListener('click', () => {
                const panel = document.querySelector(toggle.getAttribute('data-bs-target'));
                if (!panel) return;
                const expanded = toggle.getAttribute('aria-expanded') === 'true';
                toggle.setAttribute('aria-expanded', String(!expanded));
                panel.classList.toggle('show', !expanded);
            });
        });

        document.querySelectorAll('[data-toggle-secret]').forEach((toggle) => {
            toggle.addEventListener('click', () => {
                const input = document.getElementById(toggle.dataset.toggleSecret);
                if (!input) return;
                const reveal = input.type === 'password';
                input.type = reveal ? 'text' : 'password';
                toggle.textContent = reveal ? 'Hide' : 'Show';
                toggle.setAttribute(
                    'aria-label',
                    `${reveal ? 'Hide' : 'Show'} Social Security number`,
                );
            });
        });

        const vehicleSelect = document.querySelector('[data-vehicle-select]');
        const requestedAmount = document.getElementById('requested_amount');
        if (vehicleSelect && requestedAmount) {
            if (requestedAmount.value) {
                requestedAmount.dataset.edited = 'true';
            }
            requestedAmount.addEventListener('input', () => {
                requestedAmount.dataset.edited = 'true';
            });
            vehicleSelect.addEventListener('change', () => {
                const option = vehicleSelect.selectedOptions[0];
                if (requestedAmount.dataset.edited !== 'true' && option?.dataset.price) {
                    requestedAmount.value = Number(option.dataset.price).toFixed(2);
                }
            });
            if (vehicleSelect.value) {
                vehicleSelect.dispatchEvent(new Event('change'));
            }
        }

        const jointCredit = document.getElementById('joint_credit');
        const coapplicantDetails = document.getElementById('coapplicant-details');
        const applicationForm = document.getElementById('financing-application');
        if (!applicationForm) return;

        const updateRequiredFields = (prefix, enabled) => {
            const employmentStatus = document.getElementById(`${prefix}-employment_status`);
            const activeEmployment = ['employed', 'self-employed'].includes(
                employmentStatus?.value,
            );
            const employmentRequired = new Set([
                'employer_name_address', 'occupation', 'employment_date',
                'employment_gross', 'employment_net',
            ]);
            const baseRequired = new Set([
                'name', 'ssn', 'dob', 'residence_address', 'residence_years',
                'residence_months', 'dependents', 'email', 'housing_status',
                'employment_status', 'total_gross_income', 'total_net_income',
                'alimony_obligation', 'signature',
            ]);

            applicationForm.querySelectorAll(`[name^="${prefix}_"]`).forEach((field) => {
                const name = field.name.slice(prefix.length + 1);
                const requiredForEmployment = employmentRequired.has(name) && activeEmployment;
                const requiredForPriorResidence = (
                    name === 'previous_address'
                    || name === 'previous_residence_years'
                    || name === 'previous_residence_months'
                ) && Number(document.getElementById(`${prefix}-residence_years`)?.value || 0) < 2;
                const requiredForPriorJob = (
                    name === 'previous_employer' || name === 'previous_employment_date'
                ) && activeEmployment && isWithinTwoYears(
                    document.getElementById(`${prefix}-employment_date`)?.value,
                );
                const requiredForIncome = name === 'other_income_source'
                    && Number(document.getElementById(`${prefix}-other_income_gross`)?.value || 0) > 0;
                const requiredForSupport = name === 'alimony_amount'
                    && document.getElementById(`${prefix}-alimony_obligation`)?.value === 'yes';
                field.required = enabled && (
                    baseRequired.has(name)
                    || requiredForEmployment
                    || requiredForPriorResidence
                    || requiredForPriorJob
                    || requiredForIncome
                    || requiredForSupport
                );
            });
        };

        const updateJointCredit = () => {
            const joint = Boolean(jointCredit?.checked);
            if (coapplicantDetails) {
                coapplicantDetails.open = joint;
            }
            updateRequiredFields('applicant', true);
            updateRequiredFields('coapplicant', joint);
            validatePhonePair('applicant');
            validatePhonePair('coapplicant');
        };

        const isWithinTwoYears = (value) => {
            if (!value) return false;
            const startDate = new Date(`${value}T00:00:00`);
            if (Number.isNaN(startDate.getTime())) return false;
            const twoYearsAgo = new Date();
            twoYearsAgo.setFullYear(twoYearsAgo.getFullYear() - 2);
            return startDate > twoYearsAgo;
        };

        const validatePhonePair = (prefix) => {
            const home = document.getElementById(`${prefix}-home_phone`);
            const cell = document.getElementById(`${prefix}-cell_phone`);
            const required = prefix === 'applicant' || Boolean(jointCredit?.checked);
            const message = !required || home?.value || cell?.value
                ? ''
                : 'Enter at least one home or cell phone number.';
            home?.setCustomValidity(message);
            cell?.setCustomValidity(message);
        };
        ['applicant', 'coapplicant'].forEach((prefix) => {
            const home = document.getElementById(`${prefix}-home_phone`);
            const cell = document.getElementById(`${prefix}-cell_phone`);
            home?.addEventListener('input', () => validatePhonePair(prefix));
            cell?.addEventListener('input', () => validatePhonePair(prefix));
            validatePhonePair(prefix);
        });

        applicationForm.addEventListener('change', (event) => {
            const field = event.target;
            if (!field.name) return;
            const prefix = field.name.startsWith('coapplicant_')
                ? 'coapplicant'
                : 'applicant';
            updateRequiredFields(prefix, prefix === 'applicant' || Boolean(jointCredit?.checked));
        });
        jointCredit?.addEventListener('change', updateJointCredit);
        updateJointCredit();
    });
})();
