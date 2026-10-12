// Minimal service worker. It exists so Android Chrome offers "Install app".
// It deliberately caches nothing: your entries and photos are private and
// should always come fresh from the server, behind your login.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});