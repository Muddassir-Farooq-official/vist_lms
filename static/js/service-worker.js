/* The portal's service worker: shows push notifications and opens the right page when one is tapped.
   It does not cache pages (everything always comes fresh from the server). Served at /sw.js. */
"use strict";

self.addEventListener("install", function () { self.skipWaiting(); });
self.addEventListener("activate", function (event) { event.waitUntil(self.clients.claim()); });

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