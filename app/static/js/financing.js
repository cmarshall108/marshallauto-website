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

        const zipCache = new Map();
        const normalizeZip = (value) => String(value || '').replace(/\D/g, '').slice(0, 5);

        const setHint = (input, message, isSuggestion) => {
            if (!input) return;
            const hint = document.getElementById(`${input.id}-hint`);
            if (!hint) return;
            if (!message) {
                if (hint.dataset.addressHint !== undefined || hint.dataset.defaultHint) {
                    hint.textContent = hint.dataset.defaultHint || '';
                    hint.classList.toggle('d-none', !hint.dataset.defaultHint);
                }
                return;
            }
            if (!hint.dataset.defaultHint && hint.textContent.trim()) {
                hint.dataset.defaultHint = hint.textContent.trim();
            }
            hint.dataset.addressHint = '1';
            hint.classList.remove('d-none');
            hint.textContent = message;
            if (isSuggestion) {
                hint.classList.add('text-primary');
            } else {
                hint.classList.remove('text-primary');
            }
        };

        const applyZipSuggestion = async (zipInput) => {
            const group = zipInput.closest('.row') || applicationForm;
            const cityInput = group.querySelector('[data-address-city]');
            const stateInput = group.querySelector('[data-address-state]');
            // Prefer sibling fields in the same person section.
            const prefix = (zipInput.name || '').split('_')[0];
            const scopedCity = document.getElementById(`${prefix}-${zipInput.name.includes('previous') ? 'previous_city' : 'residence_city'}`)
                || cityInput;
            const scopedState = document.getElementById(`${prefix}-${zipInput.name.includes('previous') ? 'previous_state' : 'residence_state'}`)
                || stateInput;
            const zip5 = normalizeZip(zipInput.value);
            if (zip5.length !== 5) {
                setHint(zipInput, '');
                return;
            }
            let payload = zipCache.get(zip5);
            if (!payload) {
                try {
                    const response = await fetch(`/api/zip/${zip5}`, {
                        headers: { Accept: 'application/json' },
                    });
                    if (!response.ok) {
                        setHint(zipInput, 'Could not verify that ZIP code. Double-check city and state.');
                        return;
                    }
                    payload = await response.json();
                    zipCache.set(zip5, payload);
                } catch (error) {
                    setHint(zipInput, 'Could not look up that ZIP right now. Please confirm city and state.');
                    return;
                }
            }
            if (!payload || !payload.city) {
                setHint(zipInput, 'ZIP not found. Check the code or enter city and state manually.');
                return;
            }
            const cityEmpty = !(scopedCity?.value || '').trim();
            const stateEmpty = !(scopedState?.value || '').trim();
            if (cityEmpty && scopedCity) scopedCity.value = payload.city;
            if (stateEmpty && scopedState) scopedState.value = payload.state || '';
            const cityValue = (scopedCity?.value || '').trim().toLowerCase();
            const stateValue = (scopedState?.value || '').trim().toUpperCase();
            const cityMatches = payload.cities.some(
                (name) => name.toLowerCase() === cityValue,
            );
            if (stateValue && payload.state && stateValue !== payload.state) {
                setHint(
                    zipInput,
                    `ZIP ${zip5} is usually in ${payload.state}. Suggested city: ${payload.city}.`,
                    true,
                );
            } else if (cityValue && !cityMatches) {
                const alt = payload.cities.slice(0, 3).join(', ');
                setHint(
                    zipInput,
                    `ZIP ${zip5} is commonly ${alt} (${payload.state}). Update city if needed.`,
                    true,
                );
            } else if (cityEmpty || stateEmpty) {
                setHint(
                    zipInput,
                    `Suggested from ZIP: ${payload.city}, ${payload.state}`,
                    true,
                );
            } else {
                setHint(zipInput, `Matched ${payload.city}, ${payload.state}`, true);
            }
            if (scopedState) scopedState.value = scopedState.value.toUpperCase();
        };

        applicationForm.querySelectorAll('[data-address-zip]').forEach((zipInput) => {
            zipInput.addEventListener('blur', () => {
                applyZipSuggestion(zipInput);
            });
            zipInput.addEventListener('change', () => {
                applyZipSuggestion(zipInput);
            });
        });
        applicationForm.querySelectorAll('[data-address-state]').forEach((stateInput) => {
            stateInput.addEventListener('blur', () => {
                stateInput.value = stateInput.value.trim().toUpperCase();
            });
        });
    });
})();
