/* 「书架」分页：站点筛选 + 喜欢置顶 + 分页 + 阅读/下载 + 离线角标 */
import { $, $$, toast, esc, relTime, store, siteOf, siteMeta } from './util.js';

const PAGE_SIZE = 12;
let shelfData = [];
let siteCounts = {};
let filter = 'all';
let page = 1;
let favs = new Set(store.get('favs', []));

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

function renderFilter() {
  const box = $('#siteFilter');
  const chips = [{ key: 'all', name: `全部 ${shelfData.length}` }];
  for (const [key, count] of Object.entries(siteCounts)) {
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
      <div class="m"><i>${esc(c.author || '未知')}</i><i>${c.pageCount || 0}页</i><i>${relTime(c.updatedAt)}</i></div>
      <div class="bacts">${readBtn}${dlBtn}</div></div></div>`;
  }).join('');

  box.querySelectorAll('.cover img').forEach(img => {
    img.addEventListener('load', () => img.classList.add('ok'));
    img.addEventListener('error', () => img.remove());
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
      if (c && c.hasReader !== false) location.href = 'reader.html?id=' + encodeURIComponent(c.id);
    });
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
    })
    .catch(() => {
      box.innerHTML = `<div class="empty" style="grid-column:1/-1"><div class="art">🛠️</div>
        <p>暂时无法读取书架<br>若是首次使用，请先到「设置」完成 Pages 开启步骤<br>之后再点击刷新</p></div>`;
    });
}

export function initShelfView() {
  $('#prevPage').addEventListener('click', () => { if (page > 1) { page--; renderShelf(); } });
  $('#nextPage').addEventListener('click', () => { page++; renderShelf(); });
  $('#refreshShelf').addEventListener('click', () => { loadShelf(); toast('已刷新'); });
  loadShelf();
}
