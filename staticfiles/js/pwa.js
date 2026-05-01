/**
 * PWA Client: Service Worker registration, IndexedDB offline storage, and sync queue.
 */
(function () {
  'use strict';

  // ── Constants ──────────────────────────────────────
  const DB_NAME = 'pos_offline';
  const DB_VERSION = 1;
  const STORES = {
    MENU: 'menu',
    TABLES: 'tables',
    ORDERS: 'orders',
    SYNC_QUEUE: 'sync_queue',
    META: 'meta',
  };

  // ── IndexedDB Setup ────────────────────────────────
  function openDB() {
    return new Promise((resolve, reject) => {
      const request = indexedDB.open(DB_NAME, DB_VERSION);
      request.onupgradeneeded = (event) => {
        const db = event.target.result;
        if (!db.objectStoreNames.contains(STORES.MENU)) {
          db.createObjectStore(STORES.MENU, { keyPath: 'id' });
        }
        if (!db.objectStoreNames.contains(STORES.TABLES)) {
          db.createObjectStore(STORES.TABLES, { keyPath: 'id' });
        }
        if (!db.objectStoreNames.contains(STORES.ORDERS)) {
          db.createObjectStore(STORES.ORDERS, { keyPath: 'id' });
        }
        if (!db.objectStoreNames.contains(STORES.SYNC_QUEUE)) {
          db.createObjectStore(STORES.SYNC_QUEUE, { keyPath: 'offlineId', autoIncrement: false });
        }
        if (!db.objectStoreNames.contains(STORES.META)) {
          db.createObjectStore(STORES.META, { keyPath: 'key' });
        }
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  }

  function dbPut(storeName, data) {
    return openDB().then(db => {
      return new Promise((resolve, reject) => {
        const tx = db.transaction(storeName, 'readwrite');
        const store = tx.objectStore(storeName);
        if (Array.isArray(data)) {
          data.forEach(item => store.put(item));
        } else {
          store.put(data);
        }
        tx.oncomplete = () => resolve();
        tx.onerror = () => reject(tx.error);
      });
    });
  }

  function dbGetAll(storeName) {
    return openDB().then(db => {
      return new Promise((resolve, reject) => {
        const tx = db.transaction(storeName, 'readonly');
        const store = tx.objectStore(storeName);
        const request = store.getAll();
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
      });
    });
  }

  function dbDelete(storeName, key) {
    return openDB().then(db => {
      return new Promise((resolve, reject) => {
        const tx = db.transaction(storeName, 'readwrite');
        const store = tx.objectStore(storeName);
        store.delete(key);
        tx.oncomplete = () => resolve();
        tx.onerror = () => reject(tx.error);
      });
    });
  }

  function dbClear(storeName) {
    return openDB().then(db => {
      return new Promise((resolve, reject) => {
        const tx = db.transaction(storeName, 'readwrite');
        tx.objectStore(storeName).clear();
        tx.oncomplete = () => resolve();
        tx.onerror = () => reject(tx.error);
      });
    });
  }

  // ── CSRF Token ─────────────────────────────────────
  function getCSRFToken() {
    const cookie = document.cookie.split(';').find(c => c.trim().startsWith('csrftoken='));
    return cookie ? cookie.split('=')[1] : '';
  }

  // ── Data Caching ───────────────────────────────────
  async function cacheMenuData() {
    try {
      const resp = await fetch('/api/menu/');
      if (resp.ok) {
        const data = await resp.json();
        await dbClear(STORES.MENU);
        await dbPut(STORES.MENU, data.items);
        await dbPut(STORES.META, { key: 'categories', value: data.categories });
        await dbPut(STORES.META, { key: 'menu_cached_at', value: new Date().toISOString() });
      }
    } catch (e) {
      console.log('Menu cache: using offline data');
    }
  }

  async function cacheTablesData() {
    try {
      const resp = await fetch('/api/tables/');
      if (resp.ok) {
        const data = await resp.json();
        await dbClear(STORES.TABLES);
        await dbPut(STORES.TABLES, data.tables);
      }
    } catch (e) {
      console.log('Tables cache: using offline data');
    }
  }

  async function cacheOrdersData() {
    try {
      const resp = await fetch('/api/orders/');
      if (resp.ok) {
        const data = await resp.json();
        await dbClear(STORES.ORDERS);
        await dbPut(STORES.ORDERS, data.orders);
      }
    } catch (e) {
      console.log('Orders cache: using offline data');
    }
  }

  async function refreshAllData() {
    await Promise.all([cacheMenuData(), cacheTablesData(), cacheOrdersData()]);
  }

  // ── Sync Queue ─────────────────────────────────────
  function generateOfflineId() {
    return 'offline_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);
  }

  async function addToSyncQueue(url, data) {
    const item = {
      offlineId: generateOfflineId(),
      url: url,
      data: data,
      csrfToken: getCSRFToken(),
      createdAt: new Date().toISOString(),
    };
    await dbPut(STORES.SYNC_QUEUE, item);
    // Try to register for background sync
    if ('serviceWorker' in navigator && 'SyncManager' in window) {
      const reg = await navigator.serviceWorker.ready;
      try {
        await reg.sync.register('sync-orders');
      } catch {
        // Background sync not available, will sync manually
      }
    }
    updateSyncBadge();
    return item.offlineId;
  }

  async function processSyncQueue() {
    const queue = await dbGetAll(STORES.SYNC_QUEUE);
    if (queue.length === 0) return;

    let synced = 0;
    let failed = 0;

    for (const item of queue) {
      try {
        const response = await fetch(item.url, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': item.csrfToken || getCSRFToken(),
          },
          body: JSON.stringify(item.data),
          credentials: 'same-origin',
        });

        if (response.ok) {
          await dbDelete(STORES.SYNC_QUEUE, item.offlineId);
          synced++;
        } else if (response.status >= 400 && response.status < 500) {
          // Client error — won't succeed on retry, remove it
          console.warn('Sync item failed permanently:', item.offlineId, response.status);
          await dbDelete(STORES.SYNC_QUEUE, item.offlineId);
          failed++;
        } else {
          // Server error — keep for retry
          failed++;
        }
      } catch {
        // Network error — keep for retry
        failed++;
        break; // Still offline, stop trying
      }
    }

    updateSyncBadge();

    if (synced > 0) {
      showNotification(`${synced} offline order${synced > 1 ? 's' : ''} synced successfully`);
      await refreshAllData();
    }
    if (failed > 0) {
      console.log(`${failed} items still pending sync`);
    }
  }

  // ── Offline Order Placement ────────────────────────
  window.POS = window.POS || {};

  /**
   * Place an order — online or offline.
   * @param {Object} orderData - { table_id, items: [{id, qty, title, price}], notes, attendant_id }
   * @returns {Promise<Object>} - { success, order_id?, offline_id?, offline: bool }
   */
  window.POS.placeOrder = async function (orderData) {
    if (navigator.onLine) {
      try {
        const resp = await fetch('/api/place-order/', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCSRFToken(),
          },
          body: JSON.stringify(orderData),
          credentials: 'same-origin',
        });
        if (resp.ok) {
          const result = await resp.json();
          await refreshAllData();
          return { success: true, order_id: result.order_id, offline: false };
        }
        const err = await resp.json();
        return { success: false, error: err.error || 'Server error', offline: false };
      } catch {
        // Fall through to offline
      }
    }

    // Offline: queue the order
    const offlineId = await addToSyncQueue('/api/place-order/', orderData);

    // Also store as a local order for display
    const localOrder = {
      id: offlineId,
      table_id: orderData.table_id,
      table_number: orderData.table_number || '?',
      status: 'active',
      total: orderData.items.reduce((sum, i) => sum + (i.price * i.qty), 0),
      item_count: orderData.items.reduce((sum, i) => sum + i.qty, 0),
      items: orderData.items.map(i => ({
        menu_item_title: i.title,
        quantity: i.qty,
        unit_price: i.price,
        subtotal: i.price * i.qty,
      })),
      created_at: new Date().toISOString(),
      waiter: 'You',
      _offline: true,
      _offlineId: offlineId,
    };
    await dbPut(STORES.ORDERS, localOrder);

    return { success: true, offline_id: offlineId, offline: true };
  };

  /**
   * Update order status — online or offline.
   */
  window.POS.updateOrderStatus = async function (orderId, statusData) {
    if (navigator.onLine) {
      try {
        const resp = await fetch(`/api/orders/${orderId}/status/`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCSRFToken(),
          },
          body: JSON.stringify(statusData),
          credentials: 'same-origin',
        });
        if (resp.ok) {
          await refreshAllData();
          return { success: true, offline: false };
        }
      } catch {
        // Fall through to offline
      }
    }

    const offlineId = await addToSyncQueue(`/api/orders/${orderId}/status/`, statusData);
    return { success: true, offline_id: offlineId, offline: true };
  };

  /**
   * Get cached menu items for offline display.
   */
  window.POS.getOfflineMenu = function () {
    return dbGetAll(STORES.MENU);
  };

  /**
   * Get cached tables for offline display.
   */
  window.POS.getOfflineTables = function () {
    return dbGetAll(STORES.TABLES);
  };

  /**
   * Get cached orders for offline display.
   */
  window.POS.getOfflineOrders = function () {
    return dbGetAll(STORES.ORDERS);
  };

  /**
   * Get pending sync count.
   */
  window.POS.getPendingSyncCount = async function () {
    const queue = await dbGetAll(STORES.SYNC_QUEUE);
    return queue.length;
  };

  // ── UI: Online/Offline Status ──────────────────────
  function createStatusIndicator() {
    const indicator = document.createElement('div');
    indicator.id = 'pwa-status';
    indicator.style.cssText = `
      position: fixed; bottom: 16px; left: 16px; z-index: 9999;
      display: flex; align-items: center; gap: 8px;
      padding: 8px 14px; border-radius: 24px;
      font-family: 'Inter', sans-serif; font-size: 12px; font-weight: 600;
      color: #fff; transition: all 0.3s ease;
      box-shadow: 0 4px 12px rgba(0,0,0,0.2);
      pointer-events: auto; cursor: pointer;
    `;
    indicator.innerHTML = `
      <span id="pwa-dot" style="width:8px;height:8px;border-radius:50%;"></span>
      <span id="pwa-text"></span>
      <span id="pwa-sync-badge" style="display:none;background:#ef4444;color:#fff;font-size:10px;
        font-weight:700;min-width:16px;height:16px;line-height:16px;text-align:center;
        border-radius:50%;padding:0 3px;"></span>
    `;
    indicator.onclick = () => {
      if (navigator.onLine) processSyncQueue();
    };
    document.body.appendChild(indicator);
    updateStatusUI();
  }

  function updateStatusUI() {
    const el = document.getElementById('pwa-status');
    const dot = document.getElementById('pwa-dot');
    const text = document.getElementById('pwa-text');
    if (!el) return;

    if (navigator.onLine) {
      el.style.background = '#059669';
      dot.style.background = '#34d399';
      text.textContent = 'Online';
      // Auto-hide after 3s when online
      setTimeout(() => {
        if (navigator.onLine) el.style.opacity = '0.5';
      }, 3000);
    } else {
      el.style.background = '#dc2626';
      el.style.opacity = '1';
      dot.style.background = '#fca5a5';
      text.textContent = 'Offline — orders will sync when back online';
    }
    updateSyncBadge();
  }

  async function updateSyncBadge() {
    const badge = document.getElementById('pwa-sync-badge');
    if (!badge) return;
    try {
      const count = await window.POS.getPendingSyncCount();
      if (count > 0) {
        badge.textContent = count;
        badge.style.display = 'inline-block';
      } else {
        badge.style.display = 'none';
      }
    } catch {
      badge.style.display = 'none';
    }
  }

  function showNotification(message) {
    const el = document.getElementById('pwa-status');
    const text = document.getElementById('pwa-text');
    if (!el || !text) return;
    el.style.background = '#2563eb';
    el.style.opacity = '1';
    text.textContent = message;
    setTimeout(updateStatusUI, 4000);
  }

  // ── Service Worker Messages ────────────────────────
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.addEventListener('message', event => {
      if (event.data?.type === 'SYNC_REQUESTED') {
        processSyncQueue();
      }
    });
  }

  // ── Online/Offline Events ──────────────────────────
  window.addEventListener('online', () => {
    updateStatusUI();
    showNotification('Back online — syncing...');
    processSyncQueue();
  });

  window.addEventListener('offline', () => {
    updateStatusUI();
  });

  // ── Service Worker Registration ────────────────────
  async function registerSW() {
    if ('serviceWorker' in navigator) {
      try {
        const registration = await navigator.serviceWorker.register('/sw.js', { scope: '/' });
        console.log('SW registered:', registration.scope);

        // Check for updates periodically
        setInterval(() => registration.update(), 60 * 60 * 1000); // hourly
      } catch (err) {
        console.error('SW registration failed:', err);
      }
    }
  }

  // ── Initialize ─────────────────────────────────────
  document.addEventListener('DOMContentLoaded', () => {
    registerSW();
    createStatusIndicator();

    // Cache data on load if online
    if (navigator.onLine) {
      // Small delay to not compete with page load
      setTimeout(refreshAllData, 2000);
    }

    // Sync any pending items
    if (navigator.onLine) {
      setTimeout(processSyncQueue, 3000);
    }
  });

})();
