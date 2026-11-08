/* 阅读器（V2.0 zip-only）：卷轴/翻页双模式 + 章节跳转 + 进度记忆 + 本地 ZIP 直读
   - 新格式本子：漫画包（zip/分卷）自动下载到本机（OPFS/自定义文件夹），
     manifest 为目录钥匙、JSZip 按条目直读（STORED 零解压），分卷按需懒加载
   - 未下载 → 引导页一键下载（进度/断点续传）；下载后离线秒开
   - 旧格式（迁移前的线上散图）→ 回退在线阅读，兼容过渡期
   - 手动 ZIP 解包（reader.html?local=1）保留 */
'use strict';

import * as dlstore from './dlstore.js';

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
function fmtMB(b) {
  return b >= 10485760 ? (b / 1048576).toFixed(0) + 'MB' : (b / 1048576).toFixed(1) + 'MB';
}

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

/* ================= 页面来源（本地 zip / 旧格式在线） ================= */
let zipSource = null;          // 本地 zip 源；null = 旧格式在线/手动解包 blob
const pageCache = new Map();   // "ch:p" → objectURL（换章回收）

function makeZipSource(m, getBlob) {
  const files = dlstore.bookFiles(m);
  const offsets = [];
  let acc = 0;
  m.chapters.forEach(c => { offsets.push(acc); acc += (c.pageCount || 0); });
  const parts = files.length > 1
    ? m.parts.map(p => ({ file: p.file, first: p.firstPage, last: p.lastPage }))
    : [{ file: files[0] && files[0].name, first: 1, last: acc }];
  const loaded = {};  // 文件名 → JSZip（分卷按需加载）
  async function zipOf(file) {
    if (!loaded[file]) {
      if (typeof JSZip === 'undefined') throw new Error('解压组件未加载（vendor/jszip）');
      const blob = await getBlob(file);
      loaded[file] = await JSZip.loadAsync(blob);
    }
    return loaded[file];
  }
  return {
    async getEntry(chIdx, pIdx) {
      const key = chIdx + ':' + pIdx;
      if (pageCache.has(key)) return pageCache.get(key);
      const ch = m.chapters[chIdx - 1];
      const gp = offsets[chIdx - 1] + pIdx;   // 全局页序 → 定位分卷
      let part = parts.find(p => gp >= p.first && gp <= p.last) || parts[0];
      const zip = await zipOf(part.file);
      const entryName = ch.pages[pIdx - 1];
      const entry = zip.file(entryName);
      if (!entry) throw new Error('包内缺少页面 ' + entryName);
      const blob = await entry.async('blob');
      const url = URL.createObjectURL(blob);
      pageCache.set(key, url);
      return url;
    },
  };
}

function releasePageCache() {
  pageCache.forEach(u => { try { URL.revokeObjectURL(u); } catch (e) { /* noop */ } });
  pageCache.clear();
}

/* 解析页面 URL：本地 zip → objectURL；旧格式/手动解包 → 原路径直读 */
async function resolvePage(chIdx, pIdx) {
  if (zipSource) return zipSource.getEntry(chIdx, pIdx);
  const u = S.manifest.chapters[chIdx - 1].pages[pIdx - 1];
  return /^(blob|data|https?):/.test(u) ? u : (S.baseUrl + u);
}

/* ================= Service Worker ================= */
function registerSW() {
  if (!('serviceWorker' in navigator) || isLocal) return;
  navigator.serviceWorker.register('sw.js').catch(() => {});
  // 新 SW 接管（部署了新前端）时自动刷新一次（进度已自动保存）
  let refreshing = false;
  navigator.serviceWorker.addEventListener('controllerchange', () => {
    if (refreshing) return;
    refreshing = true;
    setTimeout(() => location.reload(), 600);
  });
}

/* 新格式（v3）判别：pages 指向 zip 内条目（而非 v2 的线上散图 images/...）。
   注意 v2 书也有 zip 字段，不能用「有没有 zip」判断——否则迁移前线上阅读会被破坏 */
function isV3Manifest(m) {
  if (m.parts && m.parts.length) return true;
  const withPages = (m.chapters || []).filter(c => c.pages && c.pages.length);
  return withPages.length > 0 && withPages.every(c => !String(c.pages[0]).startsWith('images/'));
}

/* ================= 初始化 ================= */
async function boot() {
  registerSW();
  if (isLocal) {
    initLocalGate();
    return;
  }
  if (!S.id) { fatal('缺少本子ID参数'); return; }
  let m;
  try {
    const res = await dlstore.fetchBookManifest(S.id);
    m = res.manifest;
    S.baseUrl = res.baseUrl;
  } catch (e) {
    fatal('本子不存在或尚未爬取', 'manifest 加载失败，可能还在部署中，稍等 1~2 分钟刷新试试');
    return;
  }
  if (!m.chapters || !m.chapters.length) { fatal('本子不存在或尚未爬取'); return; }

  if (!isV3Manifest(m)) {
    // 旧格式（V2.0 迁移前）：线上散图仍在 → 在线阅读
    initWithManifest(m);
    return;
  }

  // 新格式：漫画包在 data 分支，先确认本地是否已下载
  const state = await dlstore.checkFiles(m);
  if (state === 'ready') return initWithManifest(m, true);
  showDownloadGate(m, state === 'grant');
}

/* 下载/授权引导页：首次阅读一键下载（或补授权），完成后自动开读 */
function showDownloadGate(m, needGrant) {
  const gate = $('#dlGate');
  gate.classList.add('show');
  const btn = $('#dlStartBtn'), st = $('#dlGateStatus'), bar = $('#dlGateBar');
  $('#dlGateTitle').textContent = needGrant ? '需要本地文件夹授权' : '下载到本机后阅读';
  $('#dlGateHint').textContent = needGrant
    ? '你选择了把漫画保存到自定义文件夹，需要点一下授权（每次冷启动仅需一次）'
    : '漫画包保存在 GitHub 分支上，首次阅读需下载到本机；下载后离线秒开，之后无需等待。';
  const files = dlstore.bookFiles(m);
  $('#dlGateSize').textContent = needGrant ? '' :
    `共 ${m.pageCount || 0} 页 · ${files.length > 1 ? files.length + ' 个分卷包' : '1 个压缩包'}`;
  btn.textContent = needGrant ? '授权并打开' : '开始下载';
  st.textContent = '';
  bar.style.display = 'none';

  btn.onclick = async () => {
    btn.disabled = true;
    try {
      if (needGrant) {
        const ok = await dlstore.grantFsAccess();
        if (!ok) { st.textContent = '未获得授权，仍可点击重试'; btn.disabled = false; return; }
        gate.classList.remove('show');
        initWithManifest(m, true);
        return;
      }
      bar.style.display = 'block';
      btn.textContent = '下载中…';
      await dlstore.downloadBook(m, p => {
        if (p.total) {
          $('#dlGateFill').style.width = Math.min(100, p.received / p.total * 100).toFixed(1) + '%';
        }
        st.textContent = `第 ${p.fileIndex}/${p.fileCount} 个包 · ${fmtMB(p.received)}`
          + (p.total ? ` / ${fmtMB(p.total)}` : '');
      });
      gate.classList.remove('show');
      initWithManifest(m, true);
      toast('✅ 下载完成，之后离线秒开');
    } catch (e) {
      st.textContent = '❌ ' + (e.message || e) + '（已保留断点，点按钮续传）';
      btn.textContent = '重试下载';
      btn.disabled = false;
    }
  };
}

function initWithManifest(m, useLocal) {
  S.manifest = m;
  if (!S.id) S.id = m.id;
  if (useLocal) {
    zipSource = makeZipSource(m, file => dlstore.getFileBlob(m.id, file));
  }
  document.title = '《' + m.title + '》- 云书架';
  $('#comicTitle').textContent = m.title;
  if (dlstore.bookFiles(m).length) {
    const zb = $('#zipBtn');
    zb.style.display = 'grid';
    zb.addEventListener('click', () => {
      const n = dlstore.saveToSystem(m);
      if (n) toast(n > 1 ? `已开始 ${n} 个分卷的下载（见浏览器下载列表）` : '已开始下载到系统文件夹');
    });
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
  applyMode(S.mode, true);
  startSweeper();
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
  releasePageCache();   // 换章回收 objectURL，长本不占内存
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
      '<img data-ch="' + S.ch + '" data-p="' + i + '" alt="第' + i + '页">' +
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
async function loadScrollImg(img) {
  const ch = parseInt(img.dataset.ch, 10), p = parseInt(img.dataset.p, 10);
  if (!ch || !p || img.dataset.loading) return;
  img.dataset.loading = '1';
  try {
    let u = await resolvePage(ch, p);
    if (!/^blob:/.test(u) && img.dataset.retry) {
      u += (u.indexOf('?') >= 0 ? '&' : '?') + 'retry=' + Date.now();
    }
    img.dataset.retry = '';
    img.src = u;
    img.addEventListener('error', function onErr() {
      img.removeEventListener('error', onErr);
      showFailedImg(img, ch, p);
    });
  } catch (e) {
    showFailedImg(img, ch, p);
  }
}
function showFailedImg(img, ch, p) {
  const box = img.parentElement;
  if (box.querySelector('.failed')) return;
  const f = document.createElement('div');
  f.className = 'failed';
  f.textContent = '⚠️ 加载失败，点击重试';
  f.style.minHeight = '160px';
  f.addEventListener('click', e => {
    e.stopPropagation();
    f.remove();
    img.dataset.retry = '1';
    delete img.dataset.loading;
    loadScrollImg(img);
  });
  box.appendChild(f);
}

/* 巡检兜底：懒加载观察器时序可能漏图，定期扫描未加载的可见页 */
let sweepTimer = null;
function startSweeper() {
  stopSweeper();
  sweepTimer = setInterval(() => {
    if (S.mode !== 'scroll') return;
    document.querySelectorAll('.spage img[data-ch]').forEach(img => {
      const rect = img.parentElement.getBoundingClientRect();
      const near = rect.top < innerHeight * 2 && rect.bottom > -innerHeight * 2;
      if (near && !img.src) loadScrollImg(img);
    });
    // 已加载但宽度异常小（疑似半加载）的在线图自动重试一次（blob 直读图不需要）
    document.querySelectorAll('.spage img[src]').forEach(img => {
      if (img.complete && img.naturalWidth === 0 && !img.dataset.swept) {
        img.dataset.swept = '1';
        const u = img.src.split('?')[0].split('#')[0];
        if (!/^blob:/.test(u)) img.src = u + '?retry=' + Date.now();
      }
    });
  }, 3000);
}
function stopSweeper() {
  if (sweepTimer) { clearInterval(sweepTimer); sweepTimer = null; }
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
async function renderPaged() {
  teardownObservers();
  const pv = $('#pageView');
  pv.classList.toggle('fit-width', S.fit === 'width');
  pv.scrollTop = 0;
  const img = $('#pimg');
  img.classList.remove('ok');
  $('#spinner').style.display = 'block';
  $('#pretry').style.display = 'none';
  let url;
  try {
    url = await resolvePage(S.ch, S.p);
  } catch (e) {
    $('#spinner').style.display = 'none';
    $('#pretry').style.display = 'block';
    return;
  }
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
function loadOne(url, cb, retried) {
  if (preloadCache[url] === 'ok') { cb(true); return; }
  const im = new Image();
  im.onload = () => { preloadCache[url] = 'ok'; cb(true); };
  im.onerror = () => {
    if (!retried && !/^blob:/.test(url)) {
      // 自动重试一次再交还给用户
      setTimeout(() => loadOne(url + '?retry=' + Date.now(), cb, true), 1500);
      return;
    }
    cb(false);
  };
  im.src = url;
}
function preloadAround() {
  [[S.ch, S.p + 1], [S.ch, S.p + 2], [S.ch, S.p - 1]].forEach(async pair => {
    const c = pair[0], p = pair[1];
    if (c >= 1 && c <= S.manifest.chapters.length && p >= 1 && p <= S.manifest.chapters[c - 1].pages.length) {
      try {
        const u = await resolvePage(c, p);
        if (!preloadCache[u]) {
          const im = new Image();
          im.onload = () => { preloadCache[u] = 'ok'; };
          im.src = u;
        }
      } catch (e) { /* 预加载失败不影响主路径 */ }
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
