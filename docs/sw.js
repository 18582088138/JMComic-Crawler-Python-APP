/* 云书架 Service Worker：真·离线
   - 应用外壳（HTML/CSS/JS/vendor）：stale-while-revalidate
   - 漫画图片 comics/*：cache-first（看过即缓存，配合阅读器「全本缓存」实现断网阅读）
   - 数据 data/*.json：network-first（书架/状态永远尽量拿最新，离线时回退缓存）
*/
const VERSION = 'v1';
const SHELL_CACHE = 'shell-' + VERSION;
const COMIC_CACHE = 'comics-' + VERSION;
const DATA_CACHE = 'data-' + VERSION;

const SHELL = [
  './', 'index.html', 'reader.html', 'manifest.webmanifest', 'icon.svg',
  'css/app.css', 'css/reader.css',
  'js/app.js', 'js/util.js', 'js/github.js', 'js/task.js',
  'js/view-crawl.js', 'js/view-find.js', 'js/view-shelf.js', 'js/view-settings.js',
  'js/reader.js', 'js/sites-data.js',
  'vendor/jszip.min.js',
];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(SHELL_CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys
        .filter(k => ![SHELL_CACHE, COMIC_CACHE, DATA_CACHE].includes(k))
        .map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

function isSameOrigin(url) {
  return url.origin === self.location.origin;
}

function inScope(url, seg) {
  // 兼容仓库子路径部署（https://owner.github.io/<repo>/...）
  return isSameOrigin(url) && url.pathname.includes('/' + seg + '/');
}

/* 书架删除本子时，同步清除其离线图片缓存 */
self.addEventListener('message', e => {
  const data = e.data || {};
  if (data.type === 'purge' && Array.isArray(data.ids)) {
    e.waitUntil((async () => {
      const cache = await caches.open(COMIC_CACHE);
      const keys = await cache.keys();
      await Promise.all(keys
        .filter(req => data.ids.some(id => req.url.includes('/comics/' + id + '/')))
        .map(req => cache.delete(req)));
    })());
  }
});

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (!isSameOrigin(url)) return;

  // 漫画图片：cache-first
  if (inScope(url, 'comics')) {
    e.respondWith(
      caches.open(COMIC_CACHE).then(async cache => {
        const hit = await cache.match(req, { ignoreSearch: true });
        if (hit) return hit;
        const resp = await fetch(req);
        if (resp.ok) cache.put(req, resp.clone());
        return resp;
      })
    );
    return;
  }

  // 数据：network-first
  if (inScope(url, 'data')) {
    e.respondWith(
      caches.open(DATA_CACHE).then(async cache => {
        try {
          const resp = await fetch(req);
          if (resp.ok) cache.put(req, resp.clone());
          return resp;
        } catch (err) {
          const hit = await cache.match(req, { ignoreSearch: true });
          if (hit) return hit;
          throw err;
        }
      })
    );
    return;
  }

  // 页面导航：network-first 拿到新前端；离线回退缓存外壳
  if (req.mode === 'navigate') {
    e.respondWith(
      fetch(req)
        .then(resp => {
          if (resp.ok) {
            const copy = resp.clone();
            caches.open(SHELL_CACHE).then(c => c.put(req, copy));
          }
          return resp;
        })
        .catch(() => caches.match(req, { ignoreSearch: true })
          .then(hit => hit || caches.match('index.html')))
    );
    return;
  }

  // 其余静态资源：stale-while-revalidate
  e.respondWith(
    caches.open(SHELL_CACHE).then(async cache => {
      const hit = await cache.match(req, { ignoreSearch: true });
      const fetching = fetch(req).then(resp => {
        if (resp.ok) cache.put(req, resp.clone());
        return resp;
      }).catch(() => hit);
      return hit || fetching;
    })
  );
});
