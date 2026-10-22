/* 应用入口：分页路由 + 启动 */
import { $, $$ } from './util.js';
import { initCrawlView, applyCrawlPrefill } from './view-crawl.js';
import { initFindView } from './view-find.js';
import { initShelfView, loadShelf } from './view-shelf.js';
import { initSettingsView } from './view-settings.js';
import { initBookView, showBook } from './view-book.js';

const TABS = ['crawl', 'find', 'shelf', 'settings', 'book'];
const NAV_TABS = ['crawl', 'find', 'shelf', 'settings'];

function currentRoute() {
  const m = /^#\/(\w+)(?:\/(.+))?$/.exec(location.hash);
  const name = m && m[1];
  return { name: TABS.includes(name) ? name : 'shelf', param: (m && m[2]) || '' };
}

function switchTab(name, param) {
  TABS.forEach(t => {
    const v = $('#view-' + t);
    if (v) v.classList.toggle('on', t === name);
  });
  // 详情页高亮「书架」标签
  const navActive = NAV_TABS.includes(name) ? name : 'shelf';
  $$('.tabbar button').forEach(b => b.classList.toggle('on', b.dataset.tab === navActive));
  if (name === 'shelf') loadShelf(); // 回书架总是拿最新数据
  if (name === 'book' && param) showBook(decodeURIComponent(param));
  if (name === 'crawl') applyCrawlPrefill();
}

function registerSW() {
  if (!('serviceWorker' in navigator)) return;
  navigator.serviceWorker.register('sw.js').catch(() => {});
  // 新 SW 接管（部署了新前端）时自动刷新一次，避免旧缓存 JS 继续服役
  let refreshing = false;
  navigator.serviceWorker.addEventListener('controllerchange', () => {
    if (refreshing) return;
    refreshing = true;
    setTimeout(() => location.reload(), 600);
  });
}

function watchOnline() {
  const el = $('#netState');
  const update = () => {
    const off = !navigator.onLine;
    el.classList.toggle('off', off);
    el.lastChild.textContent = off ? '离线（可读已缓存）' : '在线';
  };
  window.addEventListener('online', update);
  window.addEventListener('offline', update);
  update();
}

window.addEventListener('hashchange', () => {
  const r = currentRoute();
  switchTab(r.name, r.param);
});

document.addEventListener('DOMContentLoaded', () => {
  $$('.tabbar button').forEach(b => {
    b.addEventListener('click', () => { location.hash = '#/' + b.dataset.tab; });
  });
  initCrawlView(() => loadShelf());
  initFindView();
  initShelfView();
  initSettingsView();
  initBookView();
  const r = currentRoute();
  switchTab(r.name, r.param);
  registerSW();
  watchOnline();
});
