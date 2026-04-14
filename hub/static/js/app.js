(function () {
    'use strict';

    const FLASH_STORAGE_KEY = 'echotrace.flash';

    function ready(fn) {
        if (document.readyState !== 'loading') {
            fn();
            return;
        }
        document.addEventListener('DOMContentLoaded', fn);
    }

    ready(function () {
        const flash = document.querySelector('[data-flash]');
        const body = document.body;
        const sidebar = document.getElementById('app-sidebar');
        const navToggles = Array.from(document.querySelectorAll('[data-nav-toggle]'));
        const navClosers = Array.from(document.querySelectorAll('[data-nav-close]'));

        restoreFlash();
        bindResponsiveNav();
        bindApiForms();

        function bindApiForms() {
            document.querySelectorAll('form[data-api]').forEach((form) => {
                form.addEventListener('submit', submitApiForm);
            });
        }

        function bindResponsiveNav() {
            if (!sidebar || navToggles.length === 0) {
                return;
            }

            navToggles.forEach((toggle) => {
                toggle.addEventListener('click', () => {
                    setNavState(!body.classList.contains('nav-open'));
                });
            });

            navClosers.forEach((closer) => {
                closer.addEventListener('click', () => {
                    setNavState(false);
                });
            });

            sidebar.querySelectorAll('a').forEach((link) => {
                link.addEventListener('click', () => {
                    if (window.matchMedia('(max-width: 980px)').matches) {
                        setNavState(false);
                    }
                });
            });

            document.addEventListener('keydown', (event) => {
                if (event.key === 'Escape') {
                    setNavState(false);
                }
            });
        }

        function setNavState(isOpen) {
            body.classList.toggle('nav-open', isOpen);
            navToggles.forEach((toggle) => {
                toggle.setAttribute('aria-expanded', String(isOpen));
            });
        }

        function restoreFlash() {
            const stored = sessionStorage.getItem(FLASH_STORAGE_KEY);
            if (!stored) {
                return;
            }
            sessionStorage.removeItem(FLASH_STORAGE_KEY);
            try {
                const parsed = JSON.parse(stored);
                if (parsed && parsed.message) {
                    setFlash(parsed.message, parsed.level || 'info');
                }
            } catch (_error) {
                sessionStorage.removeItem(FLASH_STORAGE_KEY);
            }
        }

        function persistFlash(message, level) {
            sessionStorage.setItem(
                FLASH_STORAGE_KEY,
                JSON.stringify({ message, level })
            );
        }

        function setFlash(message, level = 'info') {
            if (!flash) {
                return;
            }
            flash.textContent = message;
            flash.classList.remove('error', 'success');
            if (level === 'error') {
                flash.classList.add('error');
            } else if (level === 'success') {
                flash.classList.add('success');
            }
            flash.style.display = message ? 'block' : 'none';
        }

        function setLocalFeedback(form, message, level = 'info') {
            let feedback = form.nextElementSibling;
            if (!feedback || !feedback.classList.contains('form-feedback')) {
                feedback = document.createElement('p');
                feedback.className = 'form-feedback';
                feedback.setAttribute('role', 'status');
                feedback.setAttribute('aria-live', 'polite');
                form.insertAdjacentElement('afterend', feedback);
            }
            feedback.textContent = message;
            feedback.classList.remove('error', 'success');
            if (level === 'error') {
                feedback.classList.add('error');
            } else if (level === 'success') {
                feedback.classList.add('success');
            }
        }

        function clearLocalFeedback(form) {
            const feedback = form.nextElementSibling;
            if (feedback && feedback.classList.contains('form-feedback')) {
                feedback.textContent = '';
                feedback.classList.remove('error', 'success');
            }
        }

        function setBusyState(form, isBusy) {
            form.classList.toggle('is-busy', isBusy);
            const buttons = form.querySelectorAll('button[type="submit"], button:not([type])');
            buttons.forEach((button, index) => {
                const currentLabel = button.textContent ? button.textContent.trim() : 'Working';
                if (!button.dataset.originalLabel) {
                    button.dataset.originalLabel = currentLabel;
                }
                button.disabled = isBusy;
                if (index === 0) {
                    button.textContent = isBusy ? 'Working…' : (button.dataset.originalLabel || currentLabel);
                }
            });
        }

        async function submitApiForm(event) {
            event.preventDefault();
            const form = event.currentTarget;
            clearLocalFeedback(form);

            const endpoint = form.dataset.api;
            if (!endpoint) {
                setFlash('Missing API endpoint.', 'error');
                setLocalFeedback(form, 'Missing API endpoint.', 'error');
                return;
            }

            const method = (form.dataset.method || form.method || 'post').toUpperCase();
            const body = buildRequestBody(form);
            if (body === null) {
                return;
            }

            setBusyState(form, true);

            try {
                const response = await fetch(endpoint, {
                    method,
                    headers: {
                        'Accept': 'application/json',
                        'Content-Type': 'application/json'
                    },
                    body: method === 'GET' ? undefined : JSON.stringify(body)
                });

                if (!response.ok) {
                    const errorMessage = await extractErrorMessage(response);
                    setFlash(errorMessage, 'error');
                    setLocalFeedback(form, errorMessage, 'error');
                    return;
                }

                let successMessage = form.dataset.successMessage || `${deriveActionLabel(form)} completed.`;
                const contentType = response.headers.get('content-type') || '';
                if (contentType.includes('application/json')) {
                    const data = await response.json();
                    successMessage = form.dataset.successMessage || data.message || successMessage;
                }

                if (form.dataset.refresh === 'true') {
                    persistFlash(successMessage, 'success');
                    window.location.reload();
                    return;
                }

                setFlash(successMessage, 'success');
                setLocalFeedback(form, successMessage, 'success');
                if (form.dataset.clear === 'true') {
                    form.reset();
                }
            } catch (error) {
                setFlash('Network error while contacting the hub.', 'error');
                setLocalFeedback(form, 'Network error while contacting the hub.', 'error');
                // eslint-disable-next-line no-console
                console.error(error);
            } finally {
                setBusyState(form, false);
            }
        }

        function buildRequestBody(form) {
            const booleanFields = extractCsv(form.dataset.booleans);
            const jsonFields = extractCsv(form.dataset.jsonFields);
            const listFields = extractCsv(form.dataset.listFields);
            const intFields = extractCsv(form.dataset.ints);
            const floatFields = extractCsv(form.dataset.floats);
            const wrapKey = form.dataset.wrap || null;
            const payload = {};
            const formData = new FormData(form);

            try {
                formData.forEach((value, key) => {
                    if (value === null || value === undefined || value === '') {
                        return;
                    }

                    if (jsonFields.includes(key)) {
                        const parsed = value ? JSON.parse(value) : {};
                        setNestedValue(payload, key, parsed);
                        return;
                    }

                    if (listFields.includes(key)) {
                        const parsedList = String(value)
                            .split(/\n|,/)
                            .map((item) => item.trim())
                            .filter(Boolean);
                        setNestedValue(payload, key, parsedList);
                        return;
                    }

                    if (booleanFields.includes(key)) {
                        const booleanValue = value === 'true' || value === 'on' || value === '1';
                        setNestedValue(payload, key, booleanValue);
                        return;
                    }

                    if (intFields.includes(key)) {
                        const parsedInt = parseInt(String(value), 10);
                        if (Number.isNaN(parsedInt)) {
                            throw new Error(`Field ${key} requires an integer.`);
                        }
                        setNestedValue(payload, key, parsedInt);
                        return;
                    }

                    if (floatFields.includes(key)) {
                        const parsedFloat = parseFloat(String(value));
                        if (Number.isNaN(parsedFloat)) {
                            throw new Error(`Field ${key} requires a number.`);
                        }
                        setNestedValue(payload, key, parsedFloat);
                        return;
                    }

                    setNestedValue(payload, key, value);
                });
            } catch (error) {
                const message = error instanceof Error ? error.message : 'Invalid form value.';
                setFlash(message, 'error');
                setLocalFeedback(form, message, 'error');
                return null;
            }

            booleanFields.forEach((field) => {
                if (!hasNestedValue(payload, field)) {
                    setNestedValue(payload, field, false);
                }
            });

            if (!wrapKey) {
                return payload;
            }
            return { [wrapKey]: payload };
        }

        function setNestedValue(target, path, value) {
            const parts = path.split('.');
            let current = target;
            for (let index = 0; index < parts.length - 1; index += 1) {
                const part = parts[index];
                if (!(part in current) || typeof current[part] !== 'object' || current[part] === null) {
                    current[part] = {};
                }
                current = current[part];
            }
            current[parts[parts.length - 1]] = value;
        }

        function hasNestedValue(target, path) {
            const parts = path.split('.');
            let current = target;
            for (let index = 0; index < parts.length; index += 1) {
                const part = parts[index];
                if (!(part in current)) {
                    return false;
                }
                current = current[part];
            }
            return true;
        }

        function extractCsv(value) {
            if (!value) {
                return [];
            }
            return value
                .split(',')
                .map((item) => item.trim())
                .filter(Boolean);
        }

        async function extractErrorMessage(response) {
            const contentType = response.headers.get('content-type') || '';
            if (contentType.includes('application/json')) {
                try {
                    const data = await response.json();
                    return data.message || data.description || 'Request failed.';
                } catch (_error) {
                    return 'Request failed.';
                }
            }

            const text = (await response.text()).trim();
            const bodyMatch = text.match(/<p>(.*?)<\/p>/i);
            if (bodyMatch) {
                return bodyMatch[1];
            }
            return text || 'Request failed.';
        }

        function deriveActionLabel(form) {
            const submitButton = form.querySelector('button[type="submit"], button:not([type])');
            if (submitButton && submitButton.textContent) {
                return submitButton.textContent.trim();
            }
            return 'Request';
        }
    });
})();
