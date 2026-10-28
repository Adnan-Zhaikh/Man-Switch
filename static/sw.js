// Service worker: lets the app be installed and receives Web Push alerts.
// It deliberately caches nothing: your entries and photos are private and
// should always come fresh from the server, behind your login.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});

self.addEventListener("push", e => {
  let data = {};
  try { data = e.data ? e.data.json() : {}; } catch (_) { data = { body: e.data ? e.data.text() : "" }; }
  e.waitUntil(
    self.registration.showNotification(data.title || "Man Switch", {
      body: data.body || "",
      icon: "/icon-192.png",
      badge: "/icon-192.png",
      tag: data.tag || "man-switch",
      renotify: true,
      requireInteraction: !!data.urgent,
      data: { url: data.url || "/" },
    })
  );
});

self.addEventListener("notificationclick", e => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || "/";
  e.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(list => {
      for (const c of list) { if ("focus" in c) return c.focus(); }
      return self.clients.openWindow(url);
    })
  );
});
