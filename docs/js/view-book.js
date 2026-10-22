/* 「作品详情」视图：目录清单 + 续读 + 抓更多章节 + 下载 + 源站
   路由：#/book/<id>（多话作品按一本管理，章节在此挑选阅读） */
import { $, $$, toast, esc, relTime, fmtSize, store, siteOf, siteMeta } from './util.js';

let currentId = null;
let manifest = null;

function progressOf(id) {
  return store.get('prog_' + id, null);
}

function resumeChapter() {
  const prog = manifest && progressOf(manifest.id);
  if (prog && prog.chKey) {
    const idx = manifest.chapters.findIndex(c => String(c.photoId) === String(prog.chKey));
    if (idx >= 0) return idx + 1;
  }
  if (prog && prog.ch >= 1 && prog.ch <= manifest.chapters.length) return prog.ch;
  return 1;
}

function readUrl(chIndex) {
  return `reader.html?id=${encodeURIComponent(manifest.id)}&ch=${chIndex}`;
}

function render() {
  const m = manifest;
  const sm = siteMeta(m.site || siteOf(m.id));
  const prog = progressOf(m.id);
  const total = m.totalChapters || m.chapterCount;
  const uploaded = m.chapters.length;
  const hasMore = total > uploaded;

  $('#bookTitle').textContent = m.title;
  $('#bookMeta').innerHTML =
    `<span class="sitechip-static" style="--sc:${esc(sm.color)}">${esc(sm.name)}</span>` +
    `<i>${esc(m.author || '未知')}</i><i>${m.pageCount || 0}页</i>` +
    `<i>已上架 ${uploaded}/${total} 章</i><i>${fmtSize(m.sizeBytes)}</i>` +
    `<i>${relTime(m.updatedAt)}更新</i>`;
  $('#bookCover').innerHTML = m.id
    ? `<img src="comics/${encodeURIComponent(m.id)}/cover.jpg?t=${encodeURIComponent(m.updatedAt || '')}" alt="">`
    : '';
  if (m.tags && m.tags.length) {
    $('#bookTags').innerHTML = m.tags.slice(0, 8).map(t => `<span>${esc(t)}</span>`).join('');
    $('#bookTags').style.display = 'flex';
  } else {
    $('#bookTags').style.display = 'none';
  }

  // 主操作区
  const acts = [];
  const resume = resumeChapter();
  acts.push(`<a class="bact primary" href="${readUrl(resume)}">▶ ${prog ? `续读 第${resume}章` : '开始阅读'}</a>`);
  if (m.hasZip && m.zip) {
    acts.push(`<a class="bact" href="comics/${encodeURIComponent(m.id)}/${encodeURIComponent(m.zip)}" download>📦 下载 ZIP（含全部已上架章节）</a>`);
  }
  if (hasMore) {
    acts.push(`<button class="bact" id="fetchMoreBtn">⬆ 抓取更多章节（还有 ${total - uploaded} 章未上架）</button>`);
  }
  if (m.sourceUrl) {
    acts.push(`<a class="bact ghost" href="${esc(m.sourceUrl)}" target="_blank" rel="noopener">🔗 源站查看</a>`);
  }
  $('#bookActs').innerHTML = acts.join('');
  if (hasMore) {
    $('#fetchMoreBtn').addEventListener('click', () => {
      // 预填爬取页：目标=源站链接（没有则书架ID），批次=已上架批数+1
      const batchSize = (window.SITES || []).find(s => s.key === (m.site || siteOf(m.id)))?.batchSize || 20;
      store.set('crawl_prefill', {
        target: m.sourceUrl || m.id,
        batch: Math.floor(uploaded / batchSize) + 1,
      });
      location.hash = '#/crawl';
    });
  }

  // 目录清单
  const chList = $('#bookChapters');
  chList.innerHTML = m.chapters.map(c => {
    const read = prog && prog.chKey && String(c.photoId) === String(prog.chKey);
    const noPages = !c.pages || !c.pages.length;
    return `<a class="chrow${read ? ' reading' : ''}${noPages ? ' nopages' : ''}" href="${noPages ? 'javascript:void(0)' : readUrl(c.index)}">
      <span class="cno">${c.index}</span>
      <span class="ct">${esc(c.title || ('第' + c.index + '章'))}</span>
      ${read ? '<span class="cread">读到这</span>' : ''}
      <span class="cp">${noPages ? '未上架图片' : (c.pageCount || 0) + '页'}</span>
    </a>`;
  }).join('');
}

export function initBookView() {
  // 由 app.js 在 hash 变为 #/book/<id> 时调用 showBook
}

export function showBook(id) {
  currentId = id;
  manifest = null;
  $('#bookTitle').textContent = '加载中…';
  $('#bookChapters').innerHTML = '<div class="sk" style="aspect-ratio:auto;height:56px"></div>';
  fetch(`comics/${encodeURIComponent(id)}/manifest.json?t=${Date.now()}`, { cache: 'no-store' })
    .then(r => { if (!r.ok) throw new Error(r.status); return r.json(); })
    .then(m => { manifest = m; render(); })
    .catch(() => {
      $('#bookTitle').textContent = '本子不存在或尚未爬取';
      $('#bookChapters').innerHTML = '<div class="wempty">manifest 加载失败，可能还在部署中，稍等 1~2 分钟刷新试试。</div>';
    });
}

export function getCurrentBook() {
  return { id: currentId, manifest };
}
