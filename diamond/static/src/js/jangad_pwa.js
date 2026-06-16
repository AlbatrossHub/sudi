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

    const registerServiceWorker = async () => {
        if (!("serviceWorker" in navigator) || !window.isSecureContext) {
            return;
        }
        try {
            await navigator.serviceWorker.register(SERVICE_WORKER_URL, {
                scope: SERVICE_WORKER_SCOPE,
            });
        } catch (error) {
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

    document.addEventListener("DOMContentLoaded", () => {
        registerServiceWorker();
        initInstallUi();
    });
})();
