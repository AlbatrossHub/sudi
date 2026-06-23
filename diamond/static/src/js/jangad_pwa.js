(function () {
    "use strict";

    const SERVICE_WORKER_URL = "/diamond/jangad/service-worker.js";
    const SERVICE_WORKER_SCOPE = "/diamond/jangad/";
    const DISMISSED_KEY = "sudi_jangad_pwa_install_dismissed";

    let deferredInstallPrompt = null;

    const isStandalone = () =>
        window.matchMedia("(display-mode: standalone)").matches
        || window.navigator.standalone === true;

    const isIOS = () =>
        /iPad|iPhone|iPod/.test(window.navigator.userAgent)
        || (window.navigator.platform === "MacIntel" && window.navigator.maxTouchPoints > 1);

    const showElement = (element, show) => {
        if (element) {
            element.classList.toggle("d-none", !show);
        }
    };

    const debugLog = (hypothesisId, location, message, data = {}) => {
        // #region agent log
        fetch("http://127.0.0.1:7357/ingest/e2f1820a-d82f-4f47-b2df-091777e10ca5", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-Debug-Session-Id": "1294e1",
            },
            body: JSON.stringify({
                sessionId: "1294e1",
                runId: "post-fix",
                hypothesisId,
                location,
                message,
                data,
                timestamp: Date.now(),
            }),
        }).catch(() => {});
        // #endregion
    };

    const registerServiceWorker = async () => {
        debugLog("B", "jangad_pwa.js:registerServiceWorker", "registration start", {
            hasServiceWorker: "serviceWorker" in navigator,
            isSecureContext: window.isSecureContext,
        });
        if (!("serviceWorker" in navigator) || !window.isSecureContext) {
            return;
        }
        try {
            const registration = await navigator.serviceWorker.register(SERVICE_WORKER_URL, {
                scope: SERVICE_WORKER_SCOPE,
            });
            debugLog("B", "jangad_pwa.js:registerServiceWorker", "registration success", {
                scope: registration.scope,
                active: Boolean(registration.active),
                installing: Boolean(registration.installing),
                waiting: Boolean(registration.waiting),
            });
        } catch (error) {
            debugLog("B", "jangad_pwa.js:registerServiceWorker", "registration failed", {
                error: String(error),
            });
            console.error("Jangad PWA service worker registration failed", error);
        }
    };

    const initInstallUi = () => {
        const wrap = document.querySelector("[data-sudi-jangad-pwa]");
        if (!wrap || isStandalone() || window.localStorage.getItem(DISMISSED_KEY)) {
            return;
        }

        const androidBanner = wrap.querySelector("[data-sudi-jangad-pwa-android]");
        const iosBanner = wrap.querySelector("[data-sudi-jangad-pwa-ios]");
        const installButton = wrap.querySelector("[data-sudi-jangad-pwa-install]");
        const dismissButtons = wrap.querySelectorAll("[data-sudi-jangad-pwa-dismiss]");

        dismissButtons.forEach((button) => {
            button.addEventListener("click", () => {
                window.localStorage.setItem(DISMISSED_KEY, "1");
                showElement(androidBanner, false);
                showElement(iosBanner, false);
            });
        });

        if (isIOS()) {
            showElement(iosBanner, true);
        }

        window.addEventListener("beforeinstallprompt", (event) => {
            event.preventDefault();
            deferredInstallPrompt = event;
            debugLog("C", "jangad_pwa.js:beforeinstallprompt", "install prompt captured", {});
            showElement(iosBanner, false);
            showElement(androidBanner, true);
        });

        installButton?.addEventListener("click", async () => {
            if (!deferredInstallPrompt) {
                return;
            }
            showElement(androidBanner, false);
            deferredInstallPrompt.prompt();
            await deferredInstallPrompt.userChoice;
            deferredInstallPrompt = null;
        });

        window.addEventListener("appinstalled", () => {
            showElement(androidBanner, false);
            showElement(iosBanner, false);
            window.localStorage.setItem(DISMISSED_KEY, "1");
        });
    };

    const checkManifestIcons = async () => {
        const iconUrls = [
            "/diamond/jangad/icon/192.png",
            "/diamond/jangad/icon/512.png",
        ];
        const results = await Promise.all(iconUrls.map(async (url) => {
            const response = await fetch(url);
            const blob = await response.blob();
            return {
                url,
                ok: response.ok,
                contentType: response.headers.get("content-type"),
                size: blob.size,
            };
        }));
        debugLog("A", "jangad_pwa.js:checkManifestIcons", "manifest icon probe", { results });
    };

    document.addEventListener("DOMContentLoaded", () => {
        debugLog("D", "jangad_pwa.js:DOMContentLoaded", "pwa init", {
            manifestLinked: Boolean(document.querySelector('link[rel="manifest"]')),
        });
        registerServiceWorker();
        initInstallUi();
        checkManifestIcons();
    });
})();
