/* 「书架」分页：站点筛选 + 喜欢置顶 + 分页 + 阅读/下载 + 离线角标 + 满库清理 */
import { $, $$, toast, esc, relTime, store, siteOf, siteMeta } from './util.js';
import { detectRepo, dispatchWorkflow, repoInfo, newIssueUrl } from './github.js';

const PAGE_SIZE = 12;
let shelfData = [];
let siteCounts = {};
let filter = 'all';
let page = 1;
let favs = new Set(store.get('favs', []));
let selecting = false;      // 选择模式
let selected = new Set();   // 已选中的书架 ID
let deleteArmed = false;    // 删除二次确认状态

function toggleFav(id) {
  id = String(id);
  if (favs.has(id)) favs.delete(id); else favs.add(id);
  store.set('favs', Array.from(favs));
}

function progressOf(id) {
  return store.get('prog_' + id, null);
}

function visibleComics() {
  let list = shelfData;
  if (filter !== 'all') list = list.filter(c => (c.site || siteOf(c.id)) === filter);
  // 喜欢的排在前面（同组内保持更新时间倒序）
  return list.slice().sort((a, b) =>
    (favs.has(String(b.id)) ? 1 : 0) - (favs.has(String(a.id)) ? 1 : 0));
}

/* ---------- 满库清理（超出保留数的本子提示「下载或清理」） ---------- */
function keepLimit() {
  return Math.max(1, Math.min(99, parseInt(store.get('keep', 30), 10) || 30));
}

function overflowBooks() {
  const keep = keepLimit();
  const bySite = {};
  shelfData.forEach(c => {
    const s = c.site || siteOf(c.id);
    (bySite[s] = bySite[s] || []).push(c);
  });
  const overflow = [];
  for (const list of Object.values(bySite)) {
    list.sort((a, b) => String(b.updatedAt).localeCompare(String(a.updatedAt)));
    overflow.push(...list.slice(keep)); // 索引已按更新时间倒序，超出 keep 的都是待清理
  }
  return overflow;
}

function renderOverflowBanner() {
  let banner = $('#overflowBanner');
  const overflow = overflowBooks();
  if (!overflow.length) {
    if (banner) banner.style.display = 'none';
    return;
  }
  if (!banner) {
    banner = document.createElement('div');
    banner.id = 'overflowBanner';
    banner.className = 'card warn-banner';
    const tools = document.querySelector('#view-shelf .shelftools');
    tools.parentNode.insertBefore(banner, tools.nextSibling);
  }
  const keep = keepLimit();
  banner.style.display = 'block';
  banner.innerHTML = `
    <h2><span class="dot" style="background:var(--warn)"></span>书架已满</h2>
    <div class="hint">${overflow.length} 本旧本子超出保留数（每站 ${keep} 本）。
    可先把它们下载到本地，再点「确认清理」从书架删除；若不理会，超出 ${keep}+10 宽限后会被自动硬删。</div>
    <div class="overflow-list">${overflow.map(c => {
      const zipName = c.zipName || ('JM' + c.id + '.zip');
      return `<div class="orow">
        <span class="ot" title="${esc(c.title)}">${esc(c.title)}</span>
        <span class="os">${esc(siteMeta(c.site || siteOf(c.id)).name)}</span>
        ${c.hasZip ? `<a class="odl" href="comics/${encodeURIComponent(c.id)}/${encodeURIComponent(zipName)}" download>📦 下载</a>` : '<span class="os">无ZIP</span>'}
      </div>`;
    }).join('')}</div>
    <button class="bigbtn" id="cleanupBtn">🧹 确认清理这 ${overflow.length} 本</button>`;
  $('#cleanupBtn').addEventListener('click', function () { submitCleanup(this); });
}

function submitCleanup(btn) {
  const repo = detectRepo();
  if (!repo) { location.hash = '#/settings'; toast('请先在设置中填写仓库地址'); return; }
  btn.disabled = true;
  btn.textContent = '提交中…';
  const restore = () => { btn.disabled = false; renderOverflowBanner(); };
  const token = store.get('token', '');
  const onSubmitted = () => {
    toast('清理任务已提交，约 1~2 分钟生效', 3000);
    // 90 秒后自动刷新书架确认结果
    setTimeout(() => { loadShelf(); }, 90000);
  };
  if (token) {
    dispatchWorkflow(repo, {
      TARGET: '__cleanup__', SOURCE: 'auto', LIMIT: '3',
      MODE: '在线阅览 + 打包下载', KEEP_LATEST: String(keepLimit()), CLIENT_IMPL: '',
    }).then(status => {
      restore();
      if (status === 204) onSubmitted();
      else if (status === 401 || status === 403) toast('Token 无效或权限不足');
      else if (status === 422) toast('触发失败(422)：请强制刷新页面后重试');
      else toast('触发失败：HTTP ' + status);
    }).catch(() => { restore(); toast('网络错误'); });
  } else {
    repoInfo(repo).then(info => {
      restore();
      if (info && info.has_issues === false) {
        toast('仓库未开启 Issues，请按设置页清单开启，或改用 Token 提交', 4500);
        location.hash = '#/settings';
        return;
      }
      window.open(newIssueUrl(repo, '[jm] __cleanup__',
        'keep: ' + keepLimit() + '\n\n保留清理任务，由书架「确认清理」引导创建。'), '_blank');
      onSubmitted();
    }).catch(() => { restore(); toast('网络异常'); });
  }
}

function renderFilter() {
  const box = $('#siteFilter');
  const chips = [{ key: 'all', name: `全部 ${shelfData.length}` }];
  for (const [key, count] of Object.entries(siteCounts)) {
    if (!count) continue; // 跳过空站点（下架/清理后的残留索引）
    const s = siteMeta(key);
    chips.push({ key, name: `${s.name} ${count}`, color: s.color });
  }
  box.innerHTML = chips.map(c =>
    `<button data-k="${esc(c.key)}" class="${c.key === filter ? 'on' : ''}">${esc(c.name)}</button>`).join('');
  box.querySelectorAll('button').forEach(b => {
    b.addEventListener('click', () => {
      filter = b.dataset.k;
      page = 1;
      renderFilter();
      renderShelf();
    });
  });
}

function renderShelf() {
  const box = $('#shelf');
  const all = visibleComics();
  const pages = Math.max(1, Math.ceil(all.length / PAGE_SIZE));
  if (page > pages) page = pages;
  const slice = all.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  if (!all.length) {
    box.innerHTML = `<div class="empty" style="grid-column:1/-1"><div class="art">📚</div>
      <p>书架空空如也<br>到「爬取」页粘贴漫画链接，开始第一次云端爬取吧</p></div>`;
    $('#pager').style.display = 'none';
    return;
  }

  box.innerHTML = slice.map((c, i) => {
    const prog = progressOf(c.id);
    const site = c.site || siteOf(c.id);
    const sm = siteMeta(site);
    let badges = '';
    if (c.hasZip) badges += '<span class="badge zip">ZIP</span>';
    if (store.get('offline_' + c.id, false)) badges += '<span class="badge off">已离线</span>';
    if (prog) badges += `<span class="badge prog">读到 ${prog.ch}章${prog.p ? ' ' + prog.p + '页' : ''}</span>`;
    const readBtn = c.hasReader !== false
      ? `<a class="read" href="reader.html?id=${encodeURIComponent(c.id)}">📖 阅读</a>` : '';
    const zipName = c.zipName || ('JM' + c.id + '.zip');
    const dlBtn = c.hasZip
      ? `<a class="dl" href="comics/${encodeURIComponent(c.id)}/${encodeURIComponent(zipName)}" download>📦 下载</a>` : '';
    const favOn = favs.has(String(c.id));
    return `<div class="book" style="animation-delay:${i * 0.04}s" data-id="${esc(c.id)}">
      <div class="cover"><div class="ph">📖</div>
      ${c.cover ? `<img loading="lazy" src="${esc(c.cover)}?t=${encodeURIComponent(c.updatedAt || '')}" alt="">` : ''}
      <button class="favbtn${favOn ? ' on' : ''}" data-fav="${esc(c.id)}" title="${favOn ? '取消喜欢' : '喜欢'}">
      <svg viewBox="0 0 24 24"><path d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z"/></svg></button>
      <div class="badges">${badges}</div>
      <span class="sitechip" style="--sc:${esc(sm.color)}">${esc(sm.name)}</span></div>
      <div class="binfo"><div class="t">${esc(c.title)}</div>
      <div class="bid" title="书架ID：点按复制" data-copyid="${esc(c.id)}">${esc(c.id)}</div>
      <div class="m"><i>${esc(c.author || '未知')}</i><i>${c.pageCount || 0}页</i><i>${relTime(c.updatedAt)}</i></div>
      <div class="bacts">${readBtn}${dlBtn}</div></div></div>`;
  }).join('');

  box.querySelectorAll('.cover img').forEach(img => {
    img.addEventListener('load', () => img.classList.add('ok'));
    img.addEventListener('error', () => img.remove());
  });
  box.querySelectorAll('.bid').forEach(el => {
    el.addEventListener('click', e => {
      e.stopPropagation();
      const id = el.dataset.copyid;
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(id).then(() => toast('已复制 ID：' + id), () => toast('复制失败'));
      } else {
        toast('ID：' + id);
      }
    });
  });
  box.querySelectorAll('.favbtn').forEach(btn => {
    btn.addEventListener('click', e => {
      e.stopPropagation();
      toggleFav(btn.dataset.fav);
      renderShelf();
      toast(favs.has(String(btn.dataset.fav)) ? '已标记喜欢，置顶显示 ❤' : '已取消喜欢');
    });
  });
  box.querySelectorAll('.book').forEach(el => {
    el.addEventListener('click', e => {
      if (e.target.closest('a,button')) return;
      const c = all.find(x => String(x.id) === el.dataset.id);
      if (!c) return;
      if (selecting) {
        toggleSelect(c.id, el);
        return;
      }
      // 点开进作品详情（目录清单），阅读按钮仍直达阅读器
      location.hash = '#/book/' + encodeURIComponent(c.id);
    });
    // 选择模式下的勾选框
    if (selecting) {
      el.classList.add('selectable');
      el.classList.toggle('selected', selected.has(String(c.id)));
    } else {
      el.classList.remove('selectable', 'selected');
    }
  });

  // 分页器
  const pager = $('#pager');
  if (pages > 1) {
    pager.style.display = 'flex';
    $('#pageInfo').textContent = `${page} / ${pages} · 共${all.length}本`;
    $('#prevPage').disabled = page <= 1;
    $('#nextPage').disabled = page >= pages;
  } else {
    pager.style.display = 'none';
    $('#pageInfo').textContent = all.length ? `共${all.length}本` : '';
  }
}

export function loadShelf() {
  const box = $('#shelf');
  box.innerHTML = '<div class="sk"></div><div class="sk"></div><div class="sk"></div>';
  fetch('data/comics.json?t=' + Date.now(), { cache: 'no-store' })
    .then(r => { if (!r.ok) throw new Error(r.status); return r.json(); })
    .then(data => {
      shelfData = data.comics || [];
      siteCounts = data.sites || {};
      // 旧数据没有 sites 汇总时按条目推断
      if (!data.sites) {
        siteCounts = {};
        shelfData.forEach(c => {
          const s = c.site || siteOf(c.id);
          siteCounts[s] = (siteCounts[s] || 0) + 1;
        });
      }
      renderFilter();
      renderShelf();
      renderOverflowBanner();
    })
    .catch(() => {
      box.innerHTML = `<div class="empty" style="grid-column:1/-1"><div class="art">🛠️</div>
        <p>暂时无法读取书架<br>若是首次使用，请先到「设置」完成 Pages 开启步骤<br>之后再点击刷新</p></div>`;
    });
}

/* ---------- 选择模式与批量删除 ---------- */
function toggleSelect(id, el) {
  id = String(id);
  if (selected.has(id)) selected.delete(id); else selected.add(id);
  el.classList.toggle('selected', selected.has(id));
  updateSelBar();
}

function setSelecting(on) {
  selecting = on;
  selected.clear();
  deleteArmed = false;
  document.body.classList.toggle('shelf-selecting', on);
  const btn = $('#manageShelf');
  if (btn) btn.textContent = on ? '取消' : '管理';
  renderShelf();
  updateSelBar();
}

function updateSelBar() {
  let bar = $('#selBar');
  if (!selecting) {
    if (bar) bar.style.display = 'none';
    return;
  }
  if (!bar) {
    bar = document.createElement('div');
    bar.id = 'selBar';
    document.body.appendChild(bar);
  }
  bar.style.display = 'flex';
  const n = selected.size;
  bar.innerHTML = `
    <button class="sb-item" id="selAllBtn">${n && n === visibleComics().length ? '全不选' : '全选'}</button>
    <span class="sb-count">已选 ${n} 本</span>
    <button class="sb-item sb-del" id="delSelBtn" ${n ? '' : 'disabled'}>${deleteArmed ? `确认删除 ${n} 本？` : '🗑 删除'}</button>
    <button class="sb-item" id="selCancelBtn">取消</button>`;
  $('#selAllBtn').addEventListener('click', () => {
    const all = visibleComics();
    if (selected.size === all.length) selected.clear();
    else all.forEach(c => selected.add(String(c.id)));
    renderShelf();
    updateSelBar();
  });
  $('#selCancelBtn').addEventListener('click', () => setSelecting(false));
  $('#delSelBtn').addEventListener('click', function () {
    if (!selected.size) return;
    if (!deleteArmed) {
      deleteArmed = true;
      updateSelBar();
      setTimeout(() => { if (deleteArmed) { deleteArmed = false; updateSelBar(); } }, 5000);
      return;
    }
    submitDelete(this);
  });
}

function submitDelete(btn) {
  const repo = detectRepo();
  if (!repo) { location.hash = '#/settings'; toast('请先在设置中填写仓库地址'); return; }
  const ids = Array.from(selected);
  btn.disabled = true;
  btn.textContent = '提交中…';
  const restore = () => { btn.disabled = false; updateSelBar(); };
  const onSubmitted = () => {
    toast(`删除任务已提交（${ids.length} 本），约 1~2 分钟生效`, 3500);
    // 本地同步清理：进度/离线标记/收藏 + SW 图片缓存
    ids.forEach(id => {
      localStorage.removeItem('jm_prog_' + id);
      localStorage.removeItem('jm_offline_' + id);
      favs.delete(String(id));
    });
    store.set('favs', Array.from(favs));
    purgeSWCache(ids);
    setSelecting(false);
    setTimeout(() => { loadShelf(); }, 90000);
  };
  const token = store.get('token', '');
  if (token) {
    dispatchWorkflow(repo, {
      TARGET: '__delete__', DELETE_IDS: ids.join(','), SOURCE: 'auto', BATCH: '1',
      MODE: '在线阅览 + 打包下载', KEEP_LATEST: String(keepLimit()), CLIENT_IMPL: '',
    }).then(status => {
      restore();
      if (status === 204) onSubmitted();
      else if (status === 401 || status === 403) toast('Token 无效或权限不足');
      else if (status === 422) toast('触发失败(422)：请强制刷新页面后重试');
      else toast('触发失败：HTTP ' + status);
    }).catch(() => { restore(); toast('网络错误'); });
  } else {
    repoInfo(repo).then(info => {
      restore();
      if (info && info.has_issues === false) {
        toast('仓库未开启 Issues，请按设置页清单开启，或改用 Token 提交', 4500);
        location.hash = '#/settings';
        return;
      }
      window.open(newIssueUrl(repo, '[jm] __delete__',
        'delete: ' + ids.join(',') + '\n\n删除任务，由书架批量删除引导创建。'), '_blank');
      onSubmitted();
    }).catch(() => { restore(); toast('网络异常'); });
  }
}

/* 通知 SW 清除被删本子的离线图片缓存 */
function purgeSWCache(ids) {
  try {
    const msg = { type: 'purge', ids };
    if (navigator.serviceWorker && navigator.serviceWorker.controller) {
      navigator.serviceWorker.controller.postMessage(msg);
    } else if (navigator.serviceWorker) {
      navigator.serviceWorker.ready.then(reg => reg.active && reg.active.postMessage(msg)).catch(() => {});
    }
  } catch (e) { /* 缓存清理失败不影响主流程 */ }
}

export function initShelfView() {
  $('#manageShelf').addEventListener('click', () => setSelecting(!selecting));
  $('#prevPage').addEventListener('click', () => { if (page > 1) { page--; renderShelf(); } });
  $('#nextPage').addEventListener('click', () => { page++; renderShelf(); });
  $('#refreshShelf').addEventListener('click', () => { loadShelf(); toast('已刷新'); });
  loadShelf();
}
