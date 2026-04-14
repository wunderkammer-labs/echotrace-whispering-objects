(function () {
    'use strict';

    function ready(fn) {
        if (document.readyState !== 'loading') {
            fn();
        } else {
            document.addEventListener('DOMContentLoaded', fn);
        }
    }

    ready(function () {
        const flash = document.querySelector('[data-flash]');

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

        async function submitApiForm(event) {
            event.preventDefault();
            const form = event.currentTarget;
            const endpoint = form.dataset.api;
            if (!endpoint) {
                setFlash('Missing API endpoint.', 'error');
                return;
            }

            const method = (form.dataset.method || form.method || 'post').toUpperCase();
            const booleanFields = extractCsv(form.dataset.booleans);
            const jsonFields = extractCsv(form.dataset.jsonFields);
            const listFields = extractCsv(form.dataset.listFields);
            const intFields = extractCsv(form.dataset.ints);
            const floatFields = extractCsv(form.dataset.floats);
            const wrapKey = form.dataset.wrap || null;

            const formData = new FormData(form);
            const payload = {};

            formData.forEach((value, key) => {
                if (value === null || value === undefined || value === '') {
                    return;
                }

                if (jsonFields.includes(key)) {
                    try {
                        const parsed = value ? JSON.parse(value) : {};
                        setNestedValue(payload, key, parsed);
                    } catch (_error) {
                        setFlash(`Invalid JSON provided for ${key}.`, 'error');
                        throw new Error(`Invalid JSON: ${key}`);
                    }
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
                        setFlash(`Field ${key} requires an integer.`, 'error');
                        throw new Error(`Invalid int: ${key}`);
                    }
                    setNestedValue(payload, key, parsedInt);
                    return;
                }

                if (floatFields.includes(key)) {
                    const parsedFloat = parseFloat(String(value));
                    if (Number.isNaN(parsedFloat)) {
                        setFlash(`Field ${key} requires a number.`, 'error');
                        throw new Error(`Invalid float: ${key}`);
                    }
                    setNestedValue(payload, key, parsedFloat);
                    return;
                }

                setNestedValue(payload, key, value);
            });

            booleanFields.forEach((field) => {
                if (!hasNestedValue(payload, field)) {
                    setNestedValue(payload, field, false);
                }
            });

            const body = wrapKey ? { [wrapKey]: payload } : payload;

            try {
                const response = await fetch(endpoint, {
                    method,
                    headers: {
                        'Content-Type': 'application/json',
                        'Accept': 'application/json'
                    },
                    body: method === 'GET' ? undefined : JSON.stringify(body)
                });

                if (!response.ok) {
                    const errorMessage = await extractErrorMessage(response);
                    setFlash(errorMessage || 'Request failed.', 'error');
                    return;
                }

                const contentType = response.headers.get('content-type') || '';
                if (contentType.includes('application/json')) {
                    const data = await response.json();
                    const successMessage =
                        form.dataset.successMessage ||
                        data.message ||
                        `${deriveActionLabel(form)} completed.`;
                    setFlash(successMessage, 'success');
                    if (form.dataset.refresh === 'true') {
                        window.location.reload();
                    } else if (form.dataset.clear === 'true') {
                        form.reset();
                    }
                } else {
                    setFlash(form.dataset.successMessage || `${deriveActionLabel(form)} completed.`, 'success');
                }
            } catch (error) {
                setFlash('Network error while contacting the hub.', 'error');
                // eslint-disable-next-line no-console
                console.error(error);
            }
        }

        document.querySelectorAll('form[data-api]').forEach((form) => {
            form.addEventListener('submit', submitApiForm);
        });

        document.querySelectorAll('button[data-reboot]').forEach((button) => {
            button.addEventListener('click', () => {
                const nodeId = button.dataset.reboot;
                setFlash(`Reboot command queued for ${nodeId} (placeholder).`, 'success');
            });
        });

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
