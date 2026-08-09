const CACHE = "pomogay-v3";
const ASSETS = [
    "/",
    "/static/style.css",
    "/static/icon-192.png",
    "/static/icon-512.png",
    "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap"
];

self.addEventListener("install", event => {
    event.waitUntil(
        caches.open(CACHE).then(cache => cache.addAll(ASSETS))
    );
});

self.addEventListener("fetch", event => {
    if (event.request.url.includes('/static/style.css')) {
        event.respondWith(
            fetch(event.request).catch(() => caches.match(event.request))
        );
    } else {
        event.respondWith(
            caches.match(event.request).then(response => response || fetch(event.request))
        );
    }
});
