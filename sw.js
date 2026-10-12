// Service worker — handles incoming Web Push notifications for lesson reminders.
// Registered from tutor-app.html via navigator.serviceWorker.register('/sw.js').

self.addEventListener('push', (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) { /* ignore malformed payload */ }

  const title = data.title || 'תזכורת שיעור';
  const options = {
    body: data.body || '',
    // Same tag replaces instead of stacking; other kinds (booking requests,
    // signups) send their own tag so they don't wipe out a lesson reminder.
    tag: data.tag || 'lesson-reminder',
    renotify: true,
    dir: 'rtl',
    lang: 'he',
    data: { url: data.url || '/' },
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || '/';
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windowClients) => {
      for (const client of windowClients) {
        if ('focus' in client) return client.focus();
      }
      if (clients.openWindow) return clients.openWindow(url);
    })
  );
});
