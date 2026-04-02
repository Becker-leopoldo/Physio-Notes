const CACHE_NAME = 'physio-notes-v1';

self.addEventListener('install', () => self.skipWaiting());

self.addEventListener('activate', async () => {
  self.clients.claim();

  // Notifica clientes abertos que há nova versão
  const clients = await self.clients.matchAll({ type: 'window' });
  clients.forEach(client => client.postMessage({ type: 'SW_UPDATED' }));

  // Notificação nativa de nova versão (se permissão concedida)
  if (self.Notification && self.Notification.permission === 'granted') {
    self.registration.showNotification('Physio Notes atualizado ✓', {
      body: 'Nova versão instalada e pronta para uso.',
      icon: '/icon-192.png',
      badge: '/icon-192.png',
      tag: 'update',
      renotify: false,
    });
  }
});

// Push notification vindo do backend (futuro)
self.addEventListener('push', event => {
  const data = event.data?.json() || {};
  const title = data.title || 'Physio Notes';
  const options = {
    body: data.body || '',
    icon: '/icon-192.png',
    badge: '/icon-192.png',
    tag: data.tag || 'default',
    data: { url: data.url || '/' },
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

// Clique na notificação — abre o app
self.addEventListener('notificationclick', event => {
  event.notification.close();
  const url = event.notification.data?.url || '/';
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(clients => {
      const existing = clients.find(c => c.url.includes(self.location.origin));
      if (existing) return existing.focus();
      return self.clients.openWindow(url);
    })
  );
});

// Fetch: sem cache de requests de API, cache só de assets estáticos
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  // Não intercepta chamadas de API
  if (url.pathname.startsWith('/pacientes') ||
      url.pathname.startsWith('/sessoes') ||
      url.pathname.startsWith('/auth') ||
      url.pathname.startsWith('/transcrever') ||
      url.pathname.startsWith('/billing') ||
      url.pathname.startsWith('/configuracoes') ||
      url.pathname.startsWith('/admin')) {
    return;
  }
});
