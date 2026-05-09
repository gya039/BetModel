const SHELL_CACHE = 'de-shell-v1'
const DATA_CACHE  = 'de-data-v1'

const SHELL_ASSETS = [
  './',
  './index.html',
  './manifest.json',
  './favicon.svg',
]

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(SHELL_CACHE).then(c => c.addAll(SHELL_ASSETS))
  )
  self.skipWaiting()
})

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys
          .filter(k => k !== SHELL_CACHE && k !== DATA_CACHE)
          .map(k => caches.delete(k))
      )
    )
  )
  self.clients.claim()
})

self.addEventListener('fetch', e => {
  const { request } = e
  const url = new URL(request.url)

  // MLB data: network-first, fall back to cache
  if (url.pathname.includes('/mlb/predictions/')) {
    e.respondWith(
      fetch(request)
        .then(res => {
          const clone = res.clone()
          caches.open(DATA_CACHE).then(c => c.put(request, clone))
          return res
        })
        .catch(() => caches.match(request))
    )
    return
  }

  // App shell: cache-first
  if (request.mode === 'navigate' || SHELL_ASSETS.some(a => url.pathname.endsWith(a))) {
    e.respondWith(
      caches.match(request).then(cached => cached || fetch(request))
    )
    return
  }
})
