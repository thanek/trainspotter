const SHELL_CACHE = "trainspotter-shell-v3";
const LIBRARY_CACHE = "trainspotter-libraries-v1";
const KNOWN_CACHES = [SHELL_CACHE, LIBRARY_CACHE];

const SHELL_FILES = [
  "./",
  "index.html",
  "app.js",
  "style.css",
  "manifest.webmanifest",
  "favicon.ico",
  "icons/favicon.svg",
  "icons/icon.svg",
  "icons/icon-192.png",
  "icons/icon-512.png",
  "icons/apple-touch-icon.png",
];

const VERSIONED_LIBRARIES = [
  "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css",
  "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js",
];

self.addEventListener("install", (event) => {
  event.waitUntil(Promise.all([
    caches.open(SHELL_CACHE).then((cache) => cache.addAll(SHELL_FILES)),
    caches.open(LIBRARY_CACHE).then((cache) => cache.addAll(VERSIONED_LIBRARIES)),
  ]).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((names) => Promise.all(names.filter((n) => !KNOWN_CACHES.includes(n)).map((n) => caches.delete(n))))
      .then(() => self.clients.claim()),
  );
});

function isLiveData(url) {
  return url.origin === self.location.origin && url.pathname.startsWith("/api/");
}

function isAppShell(url) {
  return url.origin === self.location.origin;
}

function isVersionedLibrary(url) {
  return VERSIONED_LIBRARIES.includes(url.href);
}

async function networkFirst(request) {
  const cache = await caches.open(SHELL_CACHE);
  try {
    const response = await fetch(request);
    if (response.ok) cache.put(request, response.clone());
    return response;
  } catch (offline) {
    const cached = await cache.match(request, { ignoreSearch: request.mode === "navigate" });
    if (cached) return cached;
    if (request.mode === "navigate") return cache.match("index.html");
    throw offline;
  }
}

async function cacheFirst(request) {
  const cached = await caches.match(request);
  if (cached) return cached;
  const response = await fetch(request);
  if (response.ok) (await caches.open(LIBRARY_CACHE)).put(request, response.clone());
  return response;
}

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  const url = new URL(event.request.url);
  if (isLiveData(url)) return;
  if (isVersionedLibrary(url)) event.respondWith(cacheFirst(event.request));
  else if (isAppShell(url)) event.respondWith(networkFirst(event.request));
});
