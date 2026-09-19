const CACHE = "pomogay-v4";
const ASSETS = [
    "/static/style.css",
    "/static/icon-192.png",
    "/static/icon-512.png"
];

self.addEventListener("install", event => {
    self.skipWaiting();
    event.waitUntil(
        caches.open(CACHE).then(cache => cache.addAll(ASSETS))
    );
});

self.addEventListener("activate", event => {
    event.waitUntil(
        caches.keys().then(keys => Promise.all(
            keys.filter(k => k !== CACHE).map(k => caches.delete(k))
        )).then(() => self.clients.claim())
    );
});

self.addEventListener("fetch", event => {
    var url = event.request.url;

    // HTML и навигация — network-first (свежая версия, кеш только если офлайн)
    if (event.request.mode === 'navigate' || url.includes('/goal/') || url.includes('/goals') || url.includes('/profile')) {
        event.respondWith(
            fetch(event.request).catch(() => caches.match(event.request))
        );
        return;
    }

    // CSS и статика — network-first с fallback на кеш
    if (url.includes('/static/')) {
        event.respondWith(
            fetch(event.request).then(response => {
                var copy = response.clone();
                caches.open(CACHE).then(cache => cache.put(event.request, copy));
                return response;
            }).catch(() => caches.match(event.request))
        );
        return;
    }

    // Всё остальное — сеть
    event.respondWith(fetch(event.request));
});
