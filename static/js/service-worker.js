/* The portal's service worker (served at /sw.js).
   - Shows push notifications and opens the right page when one is tapped.
   - When the phone is offline, or the portal does not answer (e.g. during an update), shows the portal's own
     "You're offline" page instead of the browser's error.
   - Keeps a copy of the portal's own design files (styles, scripts, fonts, icons) to use only when offline.
   It never stores pages with anyone's data or any /api/ answer: online, everything always comes fresh from the server. */
"use strict";

const CACHE = "vist-shell-v1";
const OFFLINE_URL = "/offline";
const PRECACHE = [OFFLINE_URL, "/static/js/offline.js", "/branding/app-icon-192.png"];

self.addEventListener("install", function (event) {
  event.waitUntil(caches.open(CACHE).then(function (c) { return c.addAll(PRECACHE); }).catch(function () { /* next time */ })
    .then(function () { return self.skipWaiting(); }));
});

self.addEventListener("activate", function (event) {
  event.waitUntil(caches.keys().then(function (keys) {
    return Promise.all(keys.filter(function (k) { return k !== CACHE; }).map(function (k) { return caches.delete(k); }));
  }).then(function () { return caches.open(CACHE); }).then(function (c) { return c.add(OFFLINE_URL).catch(function () {}); })   // refresh name / colour
    .then(function () { return self.clients.claim(); }));
});

function offlinePage() {
  return caches.match(OFFLINE_URL).then(function (r) {
    return r || new Response("<h1>You're offline</h1><p>Check your connection and try again.</p>", { headers: { "Content-Type": "text/html" } });
  });
}

self.addEventListener("fetch", function (event) {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // Opening a page: always from the server. No connection, or the server not answering (502/503/504) -> the offline page.
  if (req.mode === "navigate") {
    event.respondWith(fetch(req).then(function (res) {
      return [502, 503, 504].indexOf(res.status) >= 0 ? offlinePage() : res;
    }).catch(offlinePage));
    return;
  }

  // The portal's own design files: fresh from the server when online (a copy is kept), the copy only when offline.
  if (url.pathname.startsWith("/static/") || url.pathname.startsWith("/branding/app-icon")) {
    event.respondWith(fetch(req).then(function (res) {
      if (res.ok && res.type === "basic") {
        const copy = res.clone();
        caches.open(CACHE).then(function (c) { c.put(req, copy); }).catch(function () {});
      }
      return res;
    }).catch(function () {
      return caches.match(req).then(function (r) { return r || Response.error(); });
    }));
  }
  // Everything else (/api/, downloads, uploads ...) is left to the browser exactly as before.
});

self.addEventListener("push", function (event) {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) { data = { body: event.data ? event.data.text() : "" }; }
  const title = data.title || "New notice";
  const options = {
    body: data.body || "",
    icon: "/branding/app-icon-192.png",
    badge: "/branding/app-icon-192.png?maskable=1",
    tag: data.tag || undefined,
    renotify: !!data.tag,
    data: { url: data.url || "/" },
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", function (event) {
  event.notification.close();
  const url = new URL((event.notification.data && event.notification.data.url) || "/", self.location.origin);
  if (url.origin !== self.location.origin) return;            // only ever open pages of this site
  event.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(function (list) {
    for (const c of list) {
      if (new URL(c.url).origin === url.origin && "focus" in c) { c.navigate(url.href); return c.focus(); }
    }
    return self.clients.openWindow(url.href);
  }));
});

self.addEventListener("pushsubscriptionchange", function (event) {
  // The browser renewed this device's push address: register the new one (the cookie identifies the person).
  event.waitUntil(self.registration.pushManager.subscribe(event.oldSubscription ? event.oldSubscription.options : { userVisibleOnly: true })
    .then(function (sub) {
      return fetch("/api/v1/push/subscribe", { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify(sub.toJSON()) });
    }).catch(function () { /* the person can turn it on again from the menu */ }));
});