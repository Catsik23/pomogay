const CACHE = "pomogay-v2";
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
    event.respondWith(
        caches.match(event.request).then(response => response || fetch(event.request))
    );
});
