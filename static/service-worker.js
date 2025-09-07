importScripts('https://storage.googleapis.com/workbox-cdn/releases/6.5.4/workbox-sw.js');

workbox.setConfig({ debug: false });
workbox.core.clientsClaim();

workbox.precaching.precacheAndRoute([{"revision":"387e78aeed588765c8b909a5d8857bc8","url":"css/app.css"},{"revision":"84495fa63d59647760bb2aeb3f31753e","url":"css/contact.css"},{"revision":"6a7302bc0fe2938d1eb414c6b7e2eed2","url":"css/custom.css"},{"revision":"6d2170bd0c545974954f21d0551291de","url":"css/flatpickr.min.css"},{"revision":"629164d0ed44baadbdb22be031590ae1","url":"css/login.css"},{"revision":"4a10e10eed8a3d9ee3804acdb378e81d","url":"css/sci_fi_theme.css"},{"revision":"fd5bb0ebd80a31f7249e31e9cbef6e45","url":"css/site.css"},{"revision":"ac7f0643a6ded0a5fdecb17b9754ba84","url":"feedback/889c2f486f7f4bb8b69f6d076787c85d.png"},{"revision":"ac7f0643a6ded0a5fdecb17b9754ba84","url":"feedback/8d713614d32f45d4b91566ea5b7c7c7f.png"},{"revision":"ac7f0643a6ded0a5fdecb17b9754ba84","url":"feedback/b0e5a3d3845e4337abf2e616d2b6f1eb.png"},{"revision":"2ad147487dd08f1f4c4cd6a4651cd6ea","url":"js/admin_users.js"},{"revision":"52ac4e0b6787ec58bdb8c94286b277a7","url":"js/alert_stats.js"},{"revision":"b61b5ec9ab74ddc3d28062e168f29520","url":"js/camera_create.js"},{"revision":"7dff01f2eac19cf7233564ad2591072d","url":"js/camera_table.js"},{"revision":"187ffe7a12d1079ea1653198f9e19187","url":"js/chart.umd.min.js"},{"revision":"f3adb031ff6977be4c66edb0ffb61d76","url":"js/chartjs-plugin-datalabels.min.js"},{"revision":"f9c1245b398e051c4c51c08621252e7b","url":"js/chartjs-plugin-zoom.min.js"},{"revision":"9dbc0a6defb667485e10574bf78d76e3","url":"js/dashboard_graph.js"},{"revision":"5302d7f2278fc49cb3ca228eb4555bd5","url":"js/dashboard_map.js"},{"revision":"9deb4fe731dec69eafec81261ad63fb2","url":"js/email_alerts.js"},{"revision":"ff98a4dc930264379b89b89ecbb7516a","url":"js/feedback.js"},{"revision":"8a267a2031ec977a1e363603f1540e19","url":"js/flatpickr.min.js"},{"revision":"dfb8888916e2761f06999f32cfb3697b","url":"js/i18n.js"},{"revision":"867b8ba6bdda35b4dfdb57c36ed9a737","url":"js/identity_profile.js"},{"revision":"dccd8d6ce188aff01cc91edc1ccfc93a","url":"js/login.js"},{"revision":"2b2e273c3e2d3ce392585d2070b16afb","url":"js/mjpeg_feed.js"},{"revision":"586c68c73add191b8a301d28a420fcfd","url":"js/page_transition.js"},{"revision":"664abddccf6b43f3b036df526a77a4a0","url":"js/photo_uploader.js"},{"revision":"eab51a6ab534a49607a06965f166074d","url":"js/ppe_report.js"},{"revision":"d85a3851c0179bec3443454960c3f5e9","url":"js/profile_avatar.js"},{"revision":"2b93af157fbc7dcbdcfb9744fc834556","url":"js/profile.js"},{"revision":"fa0c52a551a65ad318335e512c85a41d","url":"js/pwa.js"},{"revision":"00ec073a4ae3aab433472020382c9a18","url":"js/report.js"},{"revision":"43f1cf4c8e110f969df4ba90b439a682","url":"js/settings.js"},{"revision":"12bc0f14871c57bc8df902d6319bf845","url":"js/stats_utils.js"},{"revision":"b81e469a3e9050aed3b14705416c013b","url":"js/suggestions.js"},{"revision":"9f9e3936e222dee89b3c896fdca6d677","url":"js/thank_you.js"},{"revision":"184a56243f1e7eb8e845dca473219b20","url":"js/theme.js"},{"revision":"dcf1ecb9aa05696d7f58dc33c2f3d40c","url":"js/validation.js"},{"revision":"69bb41a1e631b0515923d9e44ac219b6","url":"js/webcam_capture.js"},{"revision":"48630c1c10f6bd9be11060c2754fb39e","url":"logo1.png"},{"revision":"d5fedae53391222f0989631c47967655","url":"logo2.png"},{"revision":"b483f5628f0fce75854fca3d5841ec4b","url":"manifest.webmanifest"},{"revision":"65488bef2495a3173ab82bc406d3aa24","url":"offline.html"},{"revision":"e44878370776106937a0804ef497566e","url":"sw-dev.js"}]);

workbox.routing.registerRoute(
  ({url, request}) => url.pathname.startsWith('/api/') && request.method === 'GET',
  new workbox.strategies.StaleWhileRevalidate({
    cacheName: 'api-cache-v1'
  })
);

workbox.routing.registerRoute(
  ({request}) => request.mode === 'navigate',
  new workbox.strategies.NetworkFirst({
    cacheName: 'pages-cache-v1'
  })
);

workbox.routing.registerRoute(
  ({request}) => request.destination === 'image' || request.destination === 'font',
  new workbox.strategies.CacheFirst({
    cacheName: 'static-assets-v1',
    plugins: [
      new workbox.expiration.ExpirationPlugin({ maxEntries: 50, maxAgeSeconds: 30 * 24 * 60 * 60 })
    ]
  })
);

const bgSyncPlugin = new workbox.backgroundSync.BackgroundSyncPlugin('api-sync-queue', {
  maxRetentionTime: 24 * 60
});
workbox.routing.registerRoute(
  /\/api\/.*/
  new workbox.strategies.NetworkOnly({ plugins: [bgSyncPlugin] }),
  'POST'
);
workbox.routing.registerRoute(
  /\/api\/.*/
  new workbox.strategies.NetworkOnly({ plugins: [bgSyncPlugin] }),
  'PUT'
);

self.addEventListener('push', event => {
  const data = event.data ? event.data.json() : {};
  const title = data.title || 'Crowd Management System';
  const options = {
    body: data.body || '',
    icon: 'https://via.placeholder.com/192.png',
    data: { url: data.url || '/' }
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  event.waitUntil(clients.openWindow(event.notification.data && event.notification.data.url ? event.notification.data.url : '/'));
});

workbox.routing.setCatchHandler(async ({ event }) => {
  if (event.request.destination === 'document') {
    return caches.match('/offline.html');
  }
  return Response.error();
});

self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});
