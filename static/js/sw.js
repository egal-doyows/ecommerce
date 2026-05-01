const CACHE_VERSION = 'pos-v1';
const STATIC_CACHE = `static-${CACHE_VERSION}`;
const DATA_CACHE = `data-${CACHE_VERSION}`;
const OFFLINE_PAGE = '/restpos/offline/';

// Static assets to pre-cache
const PRECACHE_URLS = [
  '/restpos/',
  '/restpos/offline/',
  '/static/css/styles.css',
  '/static/manifest.json',
  '/static/js/pwa.js',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  // Vendor CSS
  '/static/vendor/css/bootstrap-flatly.min.css',
  '/static/vendor/css/fontawesome.min.css',
  '/static/vendor/css/inter-font.css',
  // Vendor JS
  '/static/vendor/js/tailwind.js',
  '/static/vendor/js/jquery-3.5.1.min.js',
  '/static/vendor/js/bootstrap.bundle.min.js',
];

// API endpoints to cache for offline reads
const API_CACHE_PATTERNS = [
  '/restpos/api/menu/',
  '/restpos/api/tables/',
  '/restpos/api/orders/',
];

// POST endpoints that can be queued offline
const SYNC_ENDPOINTS = [
  '/restpos/api/place-order/',
  '/restpos/api/orders/',  // for status updates
];

// ── Install ──────────────────────────────────────────
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(STATIC_CACHE)
      .then(cache => cache.addAll(PRECACHE_URLS))
      .then(() => self.skipWaiting())
      .catch(err => {
        console.warn('Precache failed (some URLs may require auth):', err);
        return self.skipWaiting();
      })
  );
});

// ── Activate ─────────────────────────────────────────
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys.filter(k => k !== STATIC_CACHE && k !== DATA_CACHE)
          .map(k => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
});

// ── Fetch ────────────────────────────────────────────
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  // Skip non-GET for fetch handling (POST sync handled separately)
  if (event.request.method !== 'GET') return;

  // CDN assets: cache-first
  if (CDN_PATTERNS.some(p => url.hostname.includes(p) || url.href.includes(p))) {
    event.respondWith(cacheFirst(event.request, STATIC_CACHE));
    return;
  }

  // Static files: cache-first
  if (url.pathname.startsWith('/static/') || url.pathname.startsWith('/media/')) {
    event.respondWith(cacheFirst(event.request, STATIC_CACHE));
    return;
  }

  // API data: network-first, fall back to cache
  if (API_CACHE_PATTERNS.some(p => url.pathname.startsWith(p))) {
    event.respondWith(networkFirst(event.request, DATA_CACHE));
    return;
  }

  // HTML pages: network-first, fall back to cache, then offline page
  if (event.request.headers.get('accept')?.includes('text/html')) {
    event.respondWith(networkFirstPage(event.request));
    return;
  }

  // Everything else: network-first
  event.respondWith(networkFirst(event.request, STATIC_CACHE));
});

// ── Cache strategies ─────────────────────────────────
async function cacheFirst(request, cacheName) {
  const cached = await caches.match(request);
  if (cached) return cached;
  try {
    const response = await fetch(request);
    if (response.ok) {
      const cache = await caches.open(cacheName);
      cache.put(request, response.clone());
    }
    return response;
  } catch {
    return new Response('', { status: 503, statusText: 'Offline' });
  }
}

async function networkFirst(request, cacheName) {
  try {
    const response = await fetch(request);
    if (response.ok) {
      const cache = await caches.open(cacheName);
      cache.put(request, response.clone());
    }
    return response;
  } catch {
    const cached = await caches.match(request);
    return cached || new Response('{"offline": true}', {
      headers: { 'Content-Type': 'application/json' },
      status: 503,
    });
  }
}

async function networkFirstPage(request) {
  try {
    const response = await fetch(request);
    if (response.ok) {
      const cache = await caches.open(STATIC_CACHE);
      cache.put(request, response.clone());
    }
    return response;
  } catch {
    // Try cached version of the page
    const cached = await caches.match(request);
    if (cached) return cached;
    // Fall back to cached home page or offline page
    const offlinePage = await caches.match(OFFLINE_PAGE);
    if (offlinePage) return offlinePage;
    const homePage = await caches.match('/');
    if (homePage) return homePage;
    return new Response('<h1>Offline</h1><p>Please check your connection.</p>', {
      headers: { 'Content-Type': 'text/html' },
    });
  }
}

// ── Background Sync ──────────────────────────────────
self.addEventListener('sync', event => {
  if (event.tag === 'sync-orders') {
    event.waitUntil(syncOfflineQueue());
  }
});

async function syncOfflineQueue() {
  // Message all clients to trigger sync from their IndexedDB
  const clients = await self.clients.matchAll();
  clients.forEach(client => {
    client.postMessage({ type: 'SYNC_REQUESTED' });
  });
}

// Listen for sync requests from the client
self.addEventListener('message', event => {
  if (event.data?.type === 'SYNC_QUEUE_ITEM') {
    syncQueueItem(event.data.payload).then(result => {
      event.source?.postMessage({
        type: 'SYNC_RESULT',
        payload: result,
      });
    });
  }
  if (event.data?.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});

async function syncQueueItem(item) {
  try {
    const response = await fetch(item.url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': item.csrfToken,
      },
      body: JSON.stringify(item.data),
      credentials: 'same-origin',
    });
    const result = await response.json();
    return { success: response.ok, offlineId: item.offlineId, result };
  } catch {
    return { success: false, offlineId: item.offlineId, error: 'Network error' };
  }
}
