{% load static %}/* Service worker for the Gym Finder PWA (served by dashboard/pwa.py at /sw.js).
 *
 * Caching strategy, by kind of request:
 *   - /static/ files ........ cache-first (file names are content-hashed, so they never go stale)
 *   - /api/ GET requests .... network-first, falling back to the last saved response when offline
 *   - page navigations ...... network-first, falling back to the last visited copy, then /offline/
 *   - map tiles ............. cache-first, only tiles the user has already viewed (capped)
 *   - Bootstrap/Leaflet/fonts stale-while-revalidate
 * Never cached: admin, login, API writes. Logging in or out clears the saved pages, so another
 * person using the same device cannot be shown someone else's signed-in page.
 */
const VERSION = '{{ version }}';
const SHELL = 'gf-shell-' + VERSION;   // app shell, replaced when VERSION changes
const PAGES = 'gf-pages';              // last visited HTML pages
const DATA = 'gf-data';                // GeoJSON from /api/
const TILES = 'gf-tiles';              // map tiles
const CDN = 'gf-cdn';                  // third-party CSS/JS/fonts
const KEEP = [SHELL, PAGES, DATA, TILES, CDN];

const LIMITS = { [DATA]: 60, [TILES]: 400, [CDN]: 40, [PAGES]: 20 };
const PRECACHE = [
    '/offline/',
    '{% static "css/theme.css" %}',
    '{% static "js/pwa.js" %}',
    '{% static "icons/icon-192.png" %}',
    '{% static "icons/icon-512.png" %}',
];
const CDN_HOSTS = ['cdn.jsdelivr.net', 'unpkg.com', 'fonts.googleapis.com', 'fonts.gstatic.com'];
const NEVER_CACHE = ['/admin/', '/accounts/', '/api-auth/'];

self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(SHELL).then((cache) => cache.addAll(PRECACHE)).then(() => self.skipWaiting())
    );
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys()
            .then((names) => Promise.all(names.filter((n) => !KEEP.includes(n)).map((n) => caches.delete(n))))
            .then(() => self.clients.claim())
    );
});

self.addEventListener('fetch', (event) => {
    const request = event.request;
    const url = new URL(request.url);
    const sameOrigin = url.origin === self.location.origin;

    if (request.method !== 'GET') {
        // Signing in or out changes who the saved pages belong to, so forget them.
        if (sameOrigin && url.pathname.startsWith('/accounts/')) {
            event.respondWith(fetch(request).then(async (response) => {
                await caches.delete(PAGES);
                return response;
            }));
        }
        return;  // everything else (API writes etc.) goes straight to the network
    }

    if (sameOrigin) {
        if (NEVER_CACHE.some((prefix) => url.pathname.startsWith(prefix))) return;
        if (url.pathname.startsWith('/static/')) {
            event.respondWith(cacheFirst(request, SHELL));
        } else if (url.pathname.startsWith('/api/')) {
            event.respondWith(networkFirst(request, DATA, 4000, apiOffline));
        } else if (request.mode === 'navigate') {
            event.respondWith(networkFirst(request, PAGES, 5000, pageOffline));
        }
    } else if (url.hostname.endsWith('tile.openstreetmap.org')) {
        event.respondWith(cacheFirst(request, TILES));
    } else if (CDN_HOSTS.includes(url.hostname)) {
        event.respondWith(staleWhileRevalidate(request, CDN));
    }
});

// ---------- helpers ----------

function cacheable(response) {
    // Opaque responses (status 0) are what <img> tiles and no-cors assets return; they are fine to keep.
    return response && (response.ok || response.type === 'opaque');
}

async function trim(cacheName) {
    const cache = await caches.open(cacheName);
    const keys = await cache.keys();
    const excess = keys.length - (LIMITS[cacheName] || 50);
    for (let i = 0; i < excess; i++) await cache.delete(keys[i]);  // oldest first
}

async function store(cacheName, request, response) {
    if (!cacheable(response)) return response;
    const cache = await caches.open(cacheName);
    await cache.put(request, response.clone());
    trim(cacheName);
    return response;
}

async function cacheFirst(request, cacheName) {
    const cached = await caches.match(request);
    if (cached) return cached;
    const response = await fetch(request);
    return store(cacheName, request, response);
}

async function staleWhileRevalidate(request, cacheName) {
    const cache = await caches.open(cacheName);
    const cached = await cache.match(request);
    const refresh = fetch(request).then((response) => store(cacheName, request, response)).catch(() => cached);
    return cached || refresh;
}

// Try the network; if it is slow (weak signal) or down, use the saved copy.
async function networkFirst(request, cacheName, timeoutMs, offlineResponse) {
    const network = fetch(request).then((response) => store(cacheName, request, response));
    network.catch(() => {});  // a late failure after we already answered is not an error
    const timer = new Promise((resolve) => setTimeout(() => resolve(null), timeoutMs));
    try {
        const winner = await Promise.race([network, timer]);
        if (winner) return winner;
    } catch (error) { /* offline: fall through to the saved copy */ }

    const cached = await (await caches.open(cacheName)).match(request);
    if (cached) return cached;
    try {
        return await network;      // slow but alive and nothing saved: keep waiting for it
    } catch (error) {
        return offlineResponse(request);
    }
}

function apiOffline() {
    // Same error shape as the rest of the API (see api/exceptions.py)
    return new Response(
        JSON.stringify({ error: 'You are offline and this data has not been saved yet.', code: 'offline' }),
        { status: 503, headers: { 'Content-Type': 'application/json' } }
    );
}

async function pageOffline() {
    return (await caches.match('/offline/')) || new Response('You are offline.', { status: 503 });
}
