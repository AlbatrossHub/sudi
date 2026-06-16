(function () {
    "use strict";

    const DB_NAME = "sudi_jangad_uploads";
    const STORE_NAME = "pending_uploads";
    const MAX_DIMENSION = 1600;
    const JPEG_QUALITY = 0.72;
    const MAX_QUEUE_SIZE = 10;
    const MAX_COMPRESSED_SIZE = 2 * 1024 * 1024;
    const ALLOWED_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);

    const formatBytes = (bytes) => {
        if (!bytes) {
            return "0 KB";
        }
        const units = ["B", "KB", "MB"];
        let value = bytes;
        let unitIndex = 0;
        while (value >= 1024 && unitIndex < units.length - 1) {
            value /= 1024;
            unitIndex += 1;
        }
        return `${value.toFixed(unitIndex ? 1 : 0)} ${units[unitIndex]}`;
    };

    const setAlert = (element, message) => {
        if (!element) {
            return;
        }
        element.textContent = message || "";
        element.classList.toggle("d-none", !message);
    };

    const setProgress = (wrap, bar, percent) => {
        if (!wrap || !bar) {
            return;
        }
        wrap.classList.toggle("d-none", percent <= 0 || percent >= 100);
        bar.style.width = `${percent}%`;
        bar.textContent = `${percent}%`;
    };

    const normalizePhone = (phone) => {
        const digits = (phone || "").replace(/\D+/g, "");
        return digits.length > 10 ? digits.slice(-10) : digits;
    };

    const debounce = (callback, delay = 300) => {
        let timeout;
        return (...args) => {
            window.clearTimeout(timeout);
            timeout = window.setTimeout(() => callback(...args), delay);
        };
    };

    const getPickupAddressPayload = (form) => {
        const mode = form.querySelector("[data-sudi-pickup-address-mode]")?.value || "existing";
        const manualPickupAddress = form.querySelector("[data-sudi-pickup-address-manual]")?.value.trim() || "";
        const pickupAddressId = form.querySelector("[data-sudi-pickup-address-id]")?.value || "";
        return {
            pickupAddressId: mode === "manual" ? "" : pickupAddressId,
            pickupAddressMode: mode === "manual" ? "manual" : "existing",
            manualPickupAddress: mode === "manual" ? manualPickupAddress : "",
        };
    };

    const initPickupAddressLookup = (form) => {
        const phoneInput = form.querySelector("input[name='phone']");
        const addressUrl = form.dataset.addressUrl;
        const selectedInput = form.querySelector("[data-sudi-pickup-address-id]");
        const modeInput = form.querySelector("[data-sudi-pickup-address-mode]");
        const status = form.querySelector("[data-sudi-pickup-address-status]");
        const optionsWrap = form.querySelector("[data-sudi-pickup-address-options]");
        const manualButton = form.querySelector("[data-sudi-pickup-address-manual-button]");
        const manualWrap = form.querySelector("[data-sudi-pickup-address-manual-wrap]");
        const manualInput = form.querySelector("[data-sudi-pickup-address-manual]");
        if (!phoneInput || !addressUrl || !selectedInput || !modeInput || !status || !optionsWrap) {
            return;
        }

        console.info("Jangad pickup address lookup initialized");
        let suggestions = [];

        const setStatus = (message) => {
            status.textContent = message || "";
        };

        const setManualMode = () => {
            selectedInput.value = "";
            modeInput.value = "manual";
            manualWrap?.classList.remove("d-none");
            setStatus("Enter the pickup address manually.");
            renderSuggestions();
        };

        const selectSuggestion = (suggestionId) => {
            selectedInput.value = String(suggestionId);
            modeInput.value = "existing";
            if (manualInput) {
                manualInput.value = "";
            }
            manualWrap?.classList.add("d-none");
            setStatus("Pickup address selected.");
            renderSuggestions();
        };

        const renderSuggestions = () => {
            optionsWrap.textContent = "";
            optionsWrap.classList.toggle("d-none", !suggestions.length);
            suggestions.forEach((suggestion) => {
                const button = document.createElement("button");
                button.type = "button";
                button.className = "list-group-item list-group-item-action";
                if (String(suggestion.id) === selectedInput.value && modeInput.value !== "manual") {
                    button.classList.add("active");
                }

                const title = document.createElement("div");
                title.className = "fw-semibold";
                title.textContent = suggestion.name || "Pickup Address";
                const address = document.createElement("div");
                address.className = "small";
                address.style.whiteSpace = "pre-line";
                address.textContent = suggestion.address || "";
                button.append(title, address);
                button.addEventListener("click", () => selectSuggestion(suggestion.id));
                optionsWrap.appendChild(button);
            });
        };

        const applySuggestions = (nextSuggestions) => {
            suggestions = nextSuggestions || [];
            if (!suggestions.length) {
                selectedInput.value = "";
                setStatus("No saved pickup address found. Please enter the pickup address manually.");
                setManualMode();
                return;
            }
            const currentId = selectedInput.value;
            const currentSuggestion = suggestions.find((suggestion) => String(suggestion.id) === currentId);
            const defaultSuggestion = currentSuggestion || suggestions.find((suggestion) => suggestion.is_default) || suggestions[0];
            selectSuggestion(defaultSuggestion.id);
            setStatus(suggestions.length === 1 ? "Pickup address selected automatically." : "Select the pickup address.");
        };

        const lookupAddresses = async () => {
            const phoneDigits = normalizePhone(phoneInput.value);
            if (phoneDigits.length !== 10) {
                suggestions = [];
                selectedInput.value = "";
                optionsWrap.textContent = "";
                optionsWrap.classList.add("d-none");
                setStatus("Enter a 10-digit phone number to search pickup addresses.");
                return;
            }

            try {
                setStatus("Searching pickup addresses...");
                console.info("Searching Jangad pickup addresses", phoneDigits);
                const response = await fetch(`${addressUrl}?phone=${encodeURIComponent(phoneDigits)}`, {
                    credentials: "same-origin",
                });
                const contentType = response.headers.get("content-type") || "";
                if (!contentType.includes("application/json")) {
                    throw new Error("Pickup address lookup did not return JSON. Please refresh the page after module upgrade.");
                }
                const data = await response.json();
                if (!response.ok || !data.success) {
                    throw new Error(data.error || "Could not load pickup addresses.");
                }
                console.info("Jangad pickup address suggestions", data.addresses || []);
                applySuggestions(data.addresses || []);
            } catch (error) {
                console.error("Jangad pickup address lookup failed", error);
                selectedInput.value = "";
                setStatus(error.message);
                setManualMode();
            }
        };

        manualButton?.addEventListener("click", setManualMode);
        manualInput?.addEventListener("input", () => {
            if (manualInput.value.trim()) {
                selectedInput.value = "";
                modeInput.value = "manual";
            }
        });
        phoneInput.addEventListener("input", debounce(lookupAddresses));

        let initialSuggestions = [];
        try {
            initialSuggestions = JSON.parse(form.dataset.initialAddresses || "[]");
        } catch {
            initialSuggestions = [];
        }
        if (modeInput.value === "manual" && manualInput?.value.trim()) {
            suggestions = initialSuggestions;
            manualWrap?.classList.remove("d-none");
            renderSuggestions();
            setStatus("Enter the pickup address manually.");
        } else if (initialSuggestions.length) {
            applySuggestions(initialSuggestions);
        } else if (normalizePhone(phoneInput.value).length === 10) {
            lookupAddresses();
        } else {
            setStatus("Enter a 10-digit phone number to search pickup addresses.");
        }
    };

    const openDb = () =>
        new Promise((resolve, reject) => {
            const request = indexedDB.open(DB_NAME, 1);
            request.onupgradeneeded = () => {
                const db = request.result;
                if (!db.objectStoreNames.contains(STORE_NAME)) {
                    db.createObjectStore(STORE_NAME, { keyPath: "id", autoIncrement: true });
                }
            };
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error);
        });

    const withStore = async (mode, callback) => {
        const db = await openDb();
        return new Promise((resolve, reject) => {
            const transaction = db.transaction(STORE_NAME, mode);
            const store = transaction.objectStore(STORE_NAME);
            const result = callback(store);
            transaction.oncomplete = () => {
                db.close();
                resolve(result);
            };
            transaction.onerror = () => {
                db.close();
                reject(transaction.error);
            };
        });
    };

    const requestToPromise = (request) =>
        new Promise((resolve, reject) => {
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error);
        });

    const getPendingUploads = async () => {
        const db = await openDb();
        return new Promise((resolve, reject) => {
            const transaction = db.transaction(STORE_NAME, "readonly");
            const request = transaction.objectStore(STORE_NAME).getAll();
            request.onsuccess = () => resolve(request.result || []);
            request.onerror = () => reject(request.error);
            transaction.oncomplete = () => db.close();
            transaction.onerror = () => db.close();
        });
    };

    const addPendingUpload = async (payload) => {
        const pending = await getPendingUploads();
        if (pending.length >= MAX_QUEUE_SIZE) {
            throw new Error("Pending upload queue is full. Please retry existing uploads before adding more.");
        }
        await withStore("readwrite", (store) => store.add(payload));
    };

    const deletePendingUpload = async (id) => {
        await withStore("readwrite", (store) => store.delete(id));
    };

    const loadImage = async (file) => {
        if ("createImageBitmap" in window) {
            return createImageBitmap(file);
        }
        return new Promise((resolve, reject) => {
            const image = new Image();
            const url = URL.createObjectURL(file);
            image.onload = () => {
                URL.revokeObjectURL(url);
                resolve(image);
            };
            image.onerror = () => {
                URL.revokeObjectURL(url);
                reject(new Error("Could not read the image."));
            };
            image.src = url;
        });
    };

    const compressImage = async (file) => {
        if (!ALLOWED_TYPES.has(file.type)) {
            throw new Error("Please upload a PNG, JPEG, or WebP image.");
        }
        const image = await loadImage(file);
        const ratio = Math.min(1, MAX_DIMENSION / Math.max(image.width, image.height));
        const width = Math.max(1, Math.round(image.width * ratio));
        const height = Math.max(1, Math.round(image.height * ratio));
        const canvas = document.createElement("canvas");
        canvas.width = width;
        canvas.height = height;
        const context = canvas.getContext("2d");
        context.drawImage(image, 0, 0, width, height);

        const blob = await new Promise((resolve, reject) => {
            canvas.toBlob(
                (result) => result ? resolve(result) : reject(new Error("Could not compress the image.")),
                "image/jpeg",
                JPEG_QUALITY
            );
        });
        if (blob.size > MAX_COMPRESSED_SIZE) {
            throw new Error("The compressed image is still too large. Please retake a clearer, closer image.");
        }
        return new File([blob], "jangad.jpg", { type: "image/jpeg" });
    };

    const uploadPayload = async (form, payload) => {
        const formData = new FormData();
        const csrfToken = form.querySelector("input[name='csrf_token']")?.value;
        if (csrfToken) {
            formData.append("csrf_token", csrfToken);
        }
        formData.append("phone", payload.phone);
        formData.append("pickup_address_id", payload.pickupAddressId || "");
        formData.append("pickup_address_mode", payload.pickupAddressMode || "existing");
        formData.append("manual_pickup_address", payload.manualPickupAddress || "");
        formData.append("jangad_image", payload.file, payload.file.name || "jangad.jpg");

        const response = await fetch(form.dataset.jsonUrl, {
            method: "POST",
            body: formData,
            credentials: "same-origin",
        });
        const data = await response.json();
        if (!response.ok || !data.success) {
            throw new Error(data.error || "Upload failed. Please try again.");
        }
        return data;
    };

    const updatePendingUi = async (pendingElement, retryButton) => {
        const count = (await getPendingUploads()).length;
        setAlert(
            pendingElement,
            count ? `${count} upload${count === 1 ? "" : "s"} saved on this device. They will retry when online.` : ""
        );
        retryButton?.classList.toggle("d-none", !count);
    };

    const queueUpload = async (payload, pendingElement, retryButton) => {
        await addPendingUpload({
            phone: payload.phone,
            pickupAddressId: payload.pickupAddressId || "",
            pickupAddressMode: payload.pickupAddressMode || "existing",
            manualPickupAddress: payload.manualPickupAddress || "",
            file: payload.file,
            createdAt: new Date().toISOString(),
        });
        await updatePendingUi(pendingElement, retryButton);
    };

    const retryPendingUploads = async (form, elements) => {
        if (!navigator.onLine) {
            setAlert(elements.pending, "Pending upload is saved on this device. It will retry when online.");
            return;
        }
        const pending = await getPendingUploads();
        for (const item of pending) {
            try {
                const data = await uploadPayload(form, item);
                await deletePendingUpload(item.id);
                setAlert(elements.success, `Uploaded pending Jangad successfully. Receipt: ${data.receipt_name}`);
                setAlert(elements.error, "");
            } catch (error) {
                setAlert(elements.error, error.message);
                break;
            }
        }
        await updatePendingUi(elements.pending, elements.retryButton);
    };

    const initForm = async (form) => {
        if (!("indexedDB" in window) || !("fetch" in window) || !("FormData" in window)) {
            return;
        }

        const elements = {
            error: document.querySelector("[data-sudi-jangad-error]"),
            success: document.querySelector("[data-sudi-jangad-success]"),
            pending: document.querySelector("[data-sudi-jangad-pending]"),
            size: form.querySelector("[data-sudi-jangad-size]"),
            progressWrap: form.querySelector("[data-sudi-jangad-progress-wrap]"),
            progress: form.querySelector("[data-sudi-jangad-progress]"),
            submitButton: form.querySelector("[data-sudi-jangad-submit]"),
            retryButton: form.querySelector("[data-sudi-jangad-retry]"),
        };

        await updatePendingUi(elements.pending, elements.retryButton);
        window.addEventListener("online", () => retryPendingUploads(form, elements));
        elements.retryButton?.addEventListener("click", () => retryPendingUploads(form, elements));

        form.addEventListener("submit", async (event) => {
            event.preventDefault();
            setAlert(elements.error, "");
            setAlert(elements.success, "");

            const phone = form.querySelector("input[name='phone']")?.value.trim();
            const file = form.querySelector("input[name='jangad_image']")?.files?.[0];
            const pickupAddress = getPickupAddressPayload(form);
            if (!phone || !file) {
                setAlert(elements.error, "Please enter your phone number and select a Jangad image.");
                return;
            }
            if (!pickupAddress.pickupAddressId && !pickupAddress.manualPickupAddress) {
                setAlert(elements.error, "Please select or enter a pickup address.");
                return;
            }

            try {
                elements.submitButton.disabled = true;
                setProgress(elements.progressWrap, elements.progress, 15);
                const compressedFile = await compressImage(file);
                elements.size.textContent = `Original: ${formatBytes(file.size)} | Compressed: ${formatBytes(compressedFile.size)}`;
                setProgress(elements.progressWrap, elements.progress, 55);

                const payload = { phone, file: compressedFile, ...pickupAddress };
                if (!navigator.onLine) {
                    await queueUpload(payload, elements.pending, elements.retryButton);
                    setAlert(elements.success, "Network is offline. The compressed image was saved on this device and will retry when online.");
                    return;
                }

                const data = await uploadPayload(form, payload);
                setProgress(elements.progressWrap, elements.progress, 100);
                setAlert(elements.success, `Jangad uploaded successfully. Receipt: ${data.receipt_name}`);
                form.reset();
                await retryPendingUploads(form, elements);
            } catch (error) {
                try {
                    const phoneValue = form.querySelector("input[name='phone']")?.value.trim();
                    const selectedFile = form.querySelector("input[name='jangad_image']")?.files?.[0];
                    const pickupAddress = getPickupAddressPayload(form);
                    if (phoneValue && selectedFile && error.name !== "TypeError") {
                        throw error;
                    }
                    const compressedFile = selectedFile ? await compressImage(selectedFile) : null;
                    if (phoneValue && compressedFile && (pickupAddress.pickupAddressId || pickupAddress.manualPickupAddress)) {
                        await queueUpload(
                            { phone: phoneValue, file: compressedFile, ...pickupAddress },
                            elements.pending,
                            elements.retryButton
                        );
                        setAlert(elements.success, "Upload failed, so the compressed image was saved on this device for retry.");
                    } else {
                        setAlert(elements.error, error.message);
                    }
                } catch (queueError) {
                    setAlert(elements.error, queueError.message || error.message);
                }
            } finally {
                elements.submitButton.disabled = false;
                setProgress(elements.progressWrap, elements.progress, 0);
            }
        });
    };

    const initJangadUpload = () => {
        const form = document.querySelector("[data-sudi-jangad-upload]");
        if (form) {
            initPickupAddressLookup(form);
            initForm(form);
        }
    };

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initJangadUpload);
    } else {
        initJangadUpload();
    }
})();
