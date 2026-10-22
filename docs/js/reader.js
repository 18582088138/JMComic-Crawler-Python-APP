/* 阅读器：卷轴/翻页双模式 + 章节跳转 + 进度记忆 + SW 全本离线缓存 + 本地 ZIP 解包 */
'use strict';

const $ = s => document.querySelector(s);
let toastTimer = null;
function toast(msg, ms) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), ms || 1800);
}
function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
const store = {
  get(k, d) {
    try { const v = localStorage.getItem('jm_' + k); return v == null ? d : JSON.parse(v); }
    catch (e) { return d; }
  },
  set(k, v) { try { localStorage.setItem('jm_' + k, JSON.stringify(v)); } catch (e) {} },
};

/* ================= 状态 ================= */
const qs = new URLSearchParams(location.search);
const isLocal = qs.get('local') === '1';
const S = {
  id: qs.get('id'),
  manifest: null,
  baseUrl: '',
  ch: parseInt(qs.get('ch') || '0', 10) || 0,   // 1-based，0 表示未指定
  p: parseInt(qs.get('p') || '0', 10) || 0,
  mode: store.get('defMode', 'scroll'),
  dir: store.get('dir', 'ltr'),
  fit: store.get('fit', 'contain'),
  ui: true,
};

function chapter() { return S.manifest.chapters[S.ch - 1]; }
function pageCount() { return chapter().pages.length; }
function pageUrl(chIdx, pIdx) {
  const u = S.manifest.chapters[chIdx - 1].pages[pIdx - 1];
  return /^(blob|data|https?):/.test(u) ? u : (S.baseUrl + u);
}

/* ================= Service Worker ================= */
let swReady = false;
function registerSW() {
  if (!('serviceWorker' in navigator) || isLocal) return;
  navigator.serviceWorker.register('sw.js')
    .then(reg => navigator.serviceWorker.ready.then(() => { swReady = true; updateOfflineUI(); }))
    .catch(() => {});
  // 新 SW 接管（部署了新前端）时自动刷新一次（进度已自动保存）
  let refreshing = false;
  navigator.serviceWorker.addEventListener('controllerchange', () => {
    if (refreshing) return;
    refreshing = true;
    setTimeout(() => location.reload(), 600);
  });
}

/* ================= 全本离线缓存 ================= */
let caching = false;
function updateOfflineUI() {
  const btn = $('#offlineBtn');
  if (!S.manifest || isLocal) return;
  btn.style.display = 'grid';
  btn.classList.toggle('on', !!store.get('offline_' + S.id, false));
  $('#offlineRow').style.display = 'flex';
  const done = store.get('offline_' + S.id, false);
  $('#offlineHint').textContent = done
    ? '已缓存全本，断网可看；点右侧按钮可重新缓存'
    : (swReady ? '把全本图片缓存到本机，断网也能看' : '当前浏览器不支持离线缓存（需 HTTPS 与现代浏览器）');
  $('#doOfflineBtn').textContent = done ? '重新缓存' : '开始缓存';
}

async function cacheWholeComic() {
  if (caching || !S.manifest) return;
  if (!swReady) { toast('当前环境不支持离线缓存'); return; }
  caching = true;
  const urls = [];
  S.manifest.chapters.forEach(c => c.pages.forEach((u, i) => urls.push(pageUrl(c.index, i + 1))));
  const bar = $('#dlBar'), fill = $('#dlBar i'), prog = $('#dlProg');
  bar.style.display = 'block';
  prog.style.display = 'block';
  let doneCount = 0, failCount = 0;
  const CONCURRENCY = 4;
  const queue = urls.slice();
  toast('开始缓存全本 ' + urls.length + ' 张图…');
  async function worker() {
    while (queue.length) {
      const u = queue.shift();
      try { await fetch(u, { cache: 'no-cache' }); } catch (e) { failCount++; }
      doneCount++;
      const p = doneCount / urls.length;
      fill.style.width = (p * 100).toFixed(1) + '%';
      prog.style.setProperty('--p', p.toFixed(3));
    }
  }
  await Promise.all(Array.from({ length: CONCURRENCY }, worker));
  caching = false;
  bar.style.display = 'none';
  prog.style.display = 'none';
  if (failCount === 0) {
    store.set('offline_' + S.id, true);
    toast('✅ 全本已缓存，断网也能看');
  } else {
    toast(`缓存完成，${failCount} 张失败（阅读时会自动补缓存）`, 3000);
    if (doneCount - failCount > urls.length * 0.8) store.set('offline_' + S.id, true);
  }
  updateOfflineUI();
}

/* ================= 初始化 ================= */
function boot() {
  registerSW();
  if (isLocal) {
    initLocalGate();
    return;
  }
  if (!S.id) { fatal('缺少本子ID参数'); return; }
  S.baseUrl = 'comics/' + encodeURIComponent(S.id) + '/';
  fetch(S.baseUrl + 'manifest.json?t=' + Date.now(), { cache: 'no-store' })
    .then(r => { if (!r.ok) throw new Error(r.status); return r.json(); })
    .then(initWithManifest)
    .catch(() => fatal('本子不存在或尚未爬取', 'manifest 加载失败，可能还在部署中，稍等 1~2 分钟刷新试试'));
}

function initWithManifest(m) {
  if (!m.chapters || !m.chapters.length) { fatal('本子内容为空'); return; }
  S.manifest = m;
  if (!S.id) S.id = m.id;
  document.title = '《' + m.title + '》- 云书架';
  $('#comicTitle').textContent = m.title;
  if (m.hasZip && m.zip) {
    const zb = $('#zipBtn');
    zb.style.display = 'grid';
    zb.addEventListener('click', () => { location.href = S.baseUrl + encodeURIComponent(m.zip); });
  }
  // 恢复进度（优先按章节 key 定位——批次合并后章节序号可能变化）
  if (!S.ch) {
    const prog = store.get('prog_' + S.id, null);
    if (prog && prog.chKey) {
      const idx = m.chapters.findIndex(c => String(c.photoId) === String(prog.chKey));
      if (idx >= 0) { S.ch = idx + 1; S.p = prog.p || 1; }
    }
    if (!S.ch && prog && prog.ch <= m.chapters.length) {
      S.ch = prog.ch;
      S.p = prog.p || 1;
    }
    if (S.ch) {
      setTimeout(() => toast('已恢复到 第' + S.ch + '章 第' + S.p + '页'), 600);
    } else { S.ch = 1; S.p = 1; }
  }
  if (!S.p) S.p = 1;
  buildChapterList();
  initSettings();
  updateOfflineUI();
  applyMode(S.mode, true);
}

function fatal(t, sub) {
  const f = $('#fatal');
  f.classList.add('show');
  if (t) $('#fatalTitle').textContent = t;
  if (sub) $('#fatalSub').textContent = sub;
}

/* ================= 离线 ZIP 阅读 ================= */
function naturalCompare(a, b) {
  const ka = a.split(/(\d+)/), kb = b.split(/(\d+)/);
  for (let i = 0; i < Math.max(ka.length, kb.length); i++) {
    const x = ka[i] || '', y = kb[i] || '';
    if (x === y) continue;
    const nx = parseInt(x, 10), ny = parseInt(y, 10);
    if (!isNaN(nx) && !isNaN(ny)) return nx - ny;
    return x < y ? -1 : 1;
  }
  return 0;
}

function initLocalGate() {
  document.title = '离线阅读 - 云书架';
  const gate = $('#localGate');
  gate.classList.add('show');
  $('#pickZipBtn').addEventListener('click', () => $('#zipFile').click());
  $('#zipFile').addEventListener('change', function () {
    const f = this.files[0];
    if (!f) return;
    const st = $('#lgStatus');
    if (typeof JSZip === 'undefined') { st.textContent = '❌ 解压组件(JSZip)未加载，请检查 vendor/jszip.min.js'; return; }
    st.textContent = '解析中… ' + f.name;
    JSZip.loadAsync(f).then(zip => {
      const IMG = /\.(jpe?g|png|webp|gif|avif|bmp)$/i;
      const groups = {}, order = [];
      zip.forEach((rel, entry) => {
        if (entry.dir || !IMG.test(rel)) return;
        const dir = rel.indexOf('/') >= 0 ? rel.slice(0, rel.lastIndexOf('/')) : '';
        if (!groups[dir]) { groups[dir] = []; order.push(dir); }
        groups[dir].push(rel);
      });
      order.sort(naturalCompare);
      if (!order.length) throw new Error('ZIP 中没有找到图片文件');
      const chapters = order.map((dir, i) => {
        const files = groups[dir].slice().sort(naturalCompare);
        const title = dir ? dir.split('/').pop().replace(/^\d+_/, '') : '全部图片';
        return {
          index: i + 1, photoId: 'local', title, pageCount: files.length,
          pages: files.map(p => zip.file(p)),
        };
      });
      return Promise.all(chapters.map(c =>
        Promise.all(c.pages.map(entry => entry.async('blob').then(b => URL.createObjectURL(b))))
          .then(urls => { c.pages = urls; })
      )).then(() => chapters);
    }).then(chapters => {
      const total = chapters.reduce((s, c) => s + c.pageCount, 0);
      const m = {
        id: 'local_' + f.name, title: f.name.replace(/\.zip$/i, ''), author: '本地文件',
        authors: [], tags: [], description: '', updatedAt: new Date().toISOString(),
        hasReader: true, hasZip: false, zip: null,
        pageCount: total, chapterCount: chapters.length, chapters,
      };
      S.id = m.id;
      gate.classList.remove('show');
      initWithManifest(m);
      toast('已加载 ' + chapters.length + ' 章 / ' + total + ' 页');
    }).catch(e => {
      st.textContent = '❌ ' + (e && e.message ? e.message : '无法解析该 ZIP 文件');
    });
  });
}

/* ================= 进度保存 ================= */
let saveTimer = null;
function saveProgress() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    const ch = S.manifest && S.manifest.chapters[S.ch - 1];
    store.set('prog_' + S.id, { ch: S.ch, chKey: ch ? String(ch.photoId) : null, p: S.p, t: Date.now() });
  }, 400);
}

/* ================= UI 显隐 ================= */
function setUI(on) {
  S.ui = on;
  document.body.classList.toggle('ui-hidden', !on);
}
function toggleUI() { setUI(!S.ui); }
setTimeout(() => { if (S.manifest) setUI(false); }, 4000);

/* ================= 顶/底栏事件 ================= */
$('#backBtn').addEventListener('click', () => { location.href = 'index.html#/shelf'; });
$('#pageSlider').addEventListener('input', function () { goPage(parseInt(this.value, 10)); });
$('#prevChBtn').addEventListener('click', () => goChapter(S.ch - 1));
$('#nextChBtn').addEventListener('click', () => goChapter(S.ch + 1));
$('#mScroll').addEventListener('click', () => applyMode('scroll'));
$('#mPaged').addEventListener('click', () => applyMode('paged'));
$('#offlineBtn').addEventListener('click', cacheWholeComic);
$('#doOfflineBtn').addEventListener('click', cacheWholeComic);

/* ================= 模式切换 ================= */
function applyMode(mode, first) {
  S.mode = mode;
  store.set('defMode', mode);
  document.body.classList.toggle('scroll-mode', mode === 'scroll');
  document.body.classList.toggle('paged-mode', mode === 'paged');
  $('#mScroll').classList.toggle('on', mode === 'scroll');
  $('#mPaged').classList.toggle('on', mode === 'paged');
  renderChapter(first);
}
function goChapter(n) {
  if (!S.manifest) return;
  if (n < 1) { toast('已经是第一章了'); return; }
  if (n > S.manifest.chapters.length) { toast('已经是最后一章了'); return; }
  S.ch = n;
  S.p = 1;
  $('#endCard').classList.remove('show');
  buildChapterList();
  renderChapter();
  saveProgress();
}
function goPage(n) {
  const max = pageCount();
  if (n < 1) n = 1;
  if (n > max) n = max;
  S.p = n;
  if (S.mode === 'paged') renderPaged();
  else scrollToPage(n);
  saveProgress();
}
function nextPage() {
  if (S.p < pageCount()) { S.p++; renderPaged(); saveProgress(); }
  else if (S.ch < S.manifest.chapters.length) { showEndCard(); }
  else { showEndCard(true); }
}
function prevPage() {
  if (S.p > 1) { S.p--; renderPaged(); saveProgress(); }
  else if (S.ch > 1) {
    S.ch--;
    S.p = pageCount();
    buildChapterList();
    renderChapter();
    saveProgress();
    toast('上一章 第' + S.p + '页');
  } else toast('已经是第一页了');
}
function showEndCard(all) {
  $('#endTitle').textContent = all ? '🎉 全部读完啦' : '第 ' + S.ch + ' 章完';
  $('#endSub').textContent = all ? '《' + S.manifest.title + '》已读到最后' : '休息一下，还是接着看下一章？';
  $('#endNextBtn').style.display = all ? 'none' : 'inline-block';
  $('#endCard').classList.add('show');
}
$('#endNextBtn').addEventListener('click', () => goChapter(S.ch + 1));
$('#endHomeBtn').addEventListener('click', () => { location.href = 'index.html#/shelf'; });

/* ================= 渲染分发 ================= */
function renderChapter(first) {
  const ch = chapter();
  $('#chTitle').textContent = '第' + S.ch + '章 · ' + ch.title + '（共' + ch.pageCount + '页）';
  const sl = $('#pageSlider');
  sl.max = ch.pageCount;
  sl.value = S.p;
  $('#pageLabel').textContent = S.p + '/' + ch.pageCount;
  $('#prevChBtn').style.opacity = S.ch > 1 ? 1 : .35;
  $('#nextChBtn').style.opacity = S.ch < S.manifest.chapters.length ? 1 : .35;
  if (S.mode === 'scroll') renderScroll(first);
  else renderPaged();
}

/* ================= 卷轴模式 ================= */
let loadObserver = null, trackObserver = null;
function renderScroll(first) {
  teardownObservers();
  $('#endCard').classList.remove('show');
  const view = $('#scrollView');
  const ch = chapter();
  let html = '';
  for (let i = 1; i <= ch.pages.length; i++) {
    html += '<div class="spage" data-p="' + i + '">' +
      '<img data-src="' + esc(pageUrl(S.ch, i)) + '" alt="第' + i + '页">' +
      '<div class="pno">' + i + ' / ' + ch.pages.length + '</div></div>';
  }
  html += '<div class="chnav">' +
    '<button id="sPrevCh" ' + (S.ch <= 1 ? 'disabled' : '') + '>← 上一章</button>' +
    '<button id="sNextCh" class="primary" ' + (S.ch >= S.manifest.chapters.length ? 'disabled' : '') + '>下一章 →</button></div>';
  view.innerHTML = html;
  window.scrollTo(0, 0);

  loadObserver = new IntersectionObserver(entries => {
    entries.forEach(en => {
      if (!en.isIntersecting) return;
      const img = en.target.querySelector('img');
      if (img && img.dataset.src) { loadScrollImg(img); loadObserver.unobserve(en.target); }
    });
  }, { rootMargin: '160% 0px' });
  view.querySelectorAll('.spage').forEach(el => loadObserver.observe(el));

  trackObserver = new IntersectionObserver(entries => {
    entries.forEach(en => {
      if (!en.isIntersecting) return;
      const p = parseInt(en.target.dataset.p, 10);
      if (p !== S.p) { S.p = p; syncPageUI(); saveProgress(); }
    });
  }, { rootMargin: '-45% 0px -45% 0px' });
  view.querySelectorAll('.spage').forEach(el => trackObserver.observe(el));

  const sp = $('#sPrevCh'), sn = $('#sNextCh');
  if (sp) sp.addEventListener('click', e => { e.stopPropagation(); goChapter(S.ch - 1); });
  if (sn) sn.addEventListener('click', e => { e.stopPropagation(); goChapter(S.ch + 1); });

  if (S.p > 1) setTimeout(() => scrollToPage(S.p, true), 80);
}
function loadScrollImg(img) {
  img.src = img.dataset.src;
  img.addEventListener('error', () => {
    const box = img.parentElement;
    if (box.querySelector('.failed')) return;
    const f = document.createElement('div');
    f.className = 'failed';
    f.textContent = '⚠️ 加载失败，点击重试';
    f.style.minHeight = '160px';
    f.addEventListener('click', e => {
      e.stopPropagation();
      f.remove();
      const u = img.dataset.src;
      img.src = /^blob:/.test(u) ? u : (u + (u.indexOf('?') >= 0 ? '&' : '?') + 'retry=' + Date.now());
    });
    box.appendChild(f);
  }, { once: true });
}
function scrollToPage(n, instant) {
  const el = document.querySelector('.spage[data-p="' + n + '"]');
  if (el) el.scrollIntoView({ behavior: instant ? 'auto' : 'smooth', block: 'start' });
  S.p = n;
  syncPageUI();
}
function syncPageUI() {
  const sl = $('#pageSlider');
  if (document.activeElement !== sl) sl.value = S.p;
  $('#pageLabel').textContent = S.p + '/' + pageCount();
}
function teardownObservers() {
  if (loadObserver) { loadObserver.disconnect(); loadObserver = null; }
  if (trackObserver) { trackObserver.disconnect(); trackObserver = null; }
}
$('#scrollView').addEventListener('click', e => {
  if (e.target.closest('button,a')) return;
  toggleUI();
});
let lastY = 0;
window.addEventListener('scroll', () => {
  if (S.mode !== 'scroll') return;
  const y = window.scrollY;
  if (Math.abs(y - lastY) > 60) {
    if (y > lastY && y > 200 && S.ui) setUI(false);
    else if (y < lastY && !S.ui) setUI(true);
    lastY = y;
  }
}, { passive: true });

/* ================= 翻页模式 ================= */
const preloadCache = {};
function renderPaged() {
  teardownObservers();
  const pv = $('#pageView');
  pv.classList.toggle('fit-width', S.fit === 'width');
  pv.scrollTop = 0;
  const img = $('#pimg');
  img.classList.remove('ok');
  $('#spinner').style.display = 'block';
  $('#pretry').style.display = 'none';
  const url = pageUrl(S.ch, S.p);
  loadOne(url, ok => {
    $('#spinner').style.display = 'none';
    if (!ok) { $('#pretry').style.display = 'block'; return; }
    img.src = url;
  });
  syncPageUI();
  preloadAround();
}
$('#pimg').addEventListener('load', function () { this.classList.add('ok'); });
$('#pretry').addEventListener('click', () => renderPaged());
function loadOne(url, cb) {
  if (preloadCache[url] === 'ok') { cb(true); return; }
  const im = new Image();
  im.onload = () => { preloadCache[url] = 'ok'; cb(true); };
  im.onerror = () => cb(false);
  im.src = url;
}
function preloadAround() {
  [[S.ch, S.p + 1], [S.ch, S.p + 2], [S.ch, S.p - 1]].forEach(pair => {
    const c = pair[0], p = pair[1];
    if (c >= 1 && c <= S.manifest.chapters.length && p >= 1 && p <= S.manifest.chapters[c - 1].pages.length) {
      const u = pageUrl(c, p);
      if (!preloadCache[u]) {
        const im = new Image();
        im.onload = () => { preloadCache[u] = 'ok'; };
        im.src = u;
      }
    }
  });
}
$('#zones').addEventListener('click', e => {
  const z = e.target.dataset.z;
  if (z === 'c') { toggleUI(); return; }
  const isNext = (S.dir === 'ltr') ? (z === 'r') : (z === 'l');
  flashArrow(isNext ? 'r' : 'l');
  if (isNext) nextPage(); else prevPage();
});
function flashArrow(side) {
  const el = side === 'l' ? $('#arrL') : $('#arrR');
  el.classList.add('show');
  setTimeout(() => el.classList.remove('show'), 260);
}
let tsX = 0, tsY = 0, tValid = false;
document.addEventListener('touchstart', e => {
  if (S.mode !== 'paged') return;
  const t = e.changedTouches[0];
  tsX = t.clientX;
  tsY = t.clientY;
  tValid = true;
}, { passive: true });
document.addEventListener('touchend', e => {
  if (S.mode !== 'paged' || !tValid) return;
  tValid = false;
  const t = e.changedTouches[0];
  const dx = t.clientX - tsX, dy = t.clientY - tsY;
  if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy) * 1.6) {
    const isNext = (S.dir === 'ltr') ? (dx < 0) : (dx > 0);
    if (isNext) nextPage(); else prevPage();
  }
}, { passive: true });
document.addEventListener('keydown', e => {
  if (!S.manifest) return;
  const k = e.key;
  if (k === 'ArrowLeft') { if (S.mode === 'paged') (S.dir === 'ltr' ? prevPage : nextPage)(); }
  else if (k === 'ArrowRight' || k === ' ') {
    if (S.mode === 'paged') { (S.dir === 'ltr' ? nextPage : prevPage)(); e.preventDefault(); }
  } else if (k === 'ArrowDown' && S.mode === 'paged') nextPage();
  else if (k === 'ArrowUp' && S.mode === 'paged') prevPage();
});

/* ================= 章节弹层 ================= */
const mask = $('#mask'), chSheet = $('#chSheet'), setSheet = $('#setSheet');
function openSheet(el) { closeSheets(); mask.classList.add('show'); el.classList.add('show'); }
function closeSheets() { mask.classList.remove('show'); chSheet.classList.remove('show'); setSheet.classList.remove('show'); }
mask.addEventListener('click', closeSheets);
$('#chBtn').addEventListener('click', () => openSheet(chSheet));
$('#setBtn').addEventListener('click', () => openSheet(setSheet));
function buildChapterList() {
  const box = $('#chList');
  box.innerHTML = S.manifest.chapters.map(c =>
    '<div class="chitem' + (c.index === S.ch ? ' cur' : '') + '" data-ch="' + c.index + '">' +
    '<div class="no">' + c.index + '</div>' +
    '<div class="ct"><b>' + esc(c.title || ('第' + c.index + '章')) + '</b><span>' + c.pageCount + ' 页</span></div></div>'
  ).join('');
  box.querySelectorAll('.chitem').forEach(el => {
    el.addEventListener('click', () => {
      closeSheets();
      goChapter(parseInt(el.dataset.ch, 10));
    });
  });
  const cur = box.querySelector('.chitem.cur');
  if (cur) setTimeout(() => cur.scrollIntoView({ block: 'center' }), 60);
}

/* ================= 设置 ================= */
function initSettings() {
  document.querySelectorAll('.optseg[data-k]').forEach(seg => {
    const k = seg.dataset.k;
    const cur = (k === 'defMode') ? S.mode : (k === 'dir' ? S.dir : S.fit);
    seg.querySelectorAll('button').forEach(b => b.classList.toggle('on', b.dataset.v === cur));
    seg.addEventListener('click', e => {
      const b = e.target.closest('button');
      if (!b) return;
      const v = b.dataset.v;
      seg.querySelectorAll('button').forEach(x => x.classList.toggle('on', x === b));
      if (k === 'defMode') { store.set('defMode', v); if (v !== S.mode) applyMode(v); }
      else if (k === 'dir') { S.dir = v; store.set('dir', v); toast(v === 'rtl' ? '已切换：右侧上一页 / 左侧下一页' : '已切换：左侧上一页 / 右侧下一页'); }
      else if (k === 'fit') { S.fit = v; store.set('fit', v); if (S.mode === 'paged') renderPaged(); }
    });
  });
}

boot();
