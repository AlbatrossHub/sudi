/* eslint-env serviceworker */
/* eslint-disable no-restricted-globals */

const CACHE_NAME = "sudi-jangad-pwa-v4";
const SCOPE_PATH = "/jangad/";
const OFFLINE_URL = "/jangad/offline";
const SHELL_URLS = [
    OFFLINE_URL,
    "/jangad",
];

const shouldBypassServiceWorker = (url) =>
    url.pathname.endsWith("/manifest.webmanifest")
    || url.pathname.includes("/icon/")
    || url.pathname.endsWith("/service-worker.js");

const canHandleRequest = (request) => {
    const url = new URL(request.url);
    if (url.origin !== self.location.origin) {
        return false;
    }
    if (shouldBypassServiceWorker(url)) {
        return false;
    }
    return url.pathname.startsWith(SCOPE_PATH)
        || ["style", "script", "image", "font"].includes(request.destination);
};

const cacheResponse = async (request, response) => {
    if (!response || !response.ok || response.type !== "basic") {
        return response;
    }
    const cache = await caches.open(CACHE_NAME);
    await cache.put(request, response.clone());
    return response;
};

const networkFirst = async (request) => {
    try {
        return await cacheResponse(request, await fetch(request));
    } catch (error) {
        const cached = await caches.match(request);
        if (cached) {
            return cached;
        }
        if (request.mode === "navigate") {
            return caches.match(OFFLINE_URL);
        }
        throw error;
    }
};

self.addEventListener("install", (event) => {
    self.skipWaiting();
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) =>
            Promise.allSettled(SHELL_URLS.map((url) => cache.add(url)))
        )
    );
});

self.addEventListener("activate", (event) => {
    event.waitUntil(
        Promise.all([
            caches.keys().then((cacheNames) =>
                Promise.all(
                    cacheNames
                        .filter((cacheName) => cacheName !== CACHE_NAME && cacheName.startsWith("sudi-jangad-pwa-"))
                        .map((cacheName) => caches.delete(cacheName))
                )
            ),
            self.clients.claim(),
        ])
    );
});

self.addEventListener("fetch", (event) => {
    const request = event.request;
    if (request.method !== "GET" || !canHandleRequest(request)) {
        return;
    }
    event.respondWith(networkFirst(request));
});
