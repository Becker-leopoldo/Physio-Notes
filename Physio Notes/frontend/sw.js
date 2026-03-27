self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', () => self.clients.claim());

// Notifica todos os clientes abertos que há uma nova versão
self.addEventListener('activate', async () => {
  const clients = await self.clients.matchAll({ type: 'window' });
  clients.forEach(client => client.postMessage({ type: 'SW_UPDATED' }));
});
