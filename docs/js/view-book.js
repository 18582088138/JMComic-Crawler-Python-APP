/* 「作品详情」视图：目录清单 + 续读 + 抓更多章节 + 保存ZIP/分卷 + 本地删除/下架 + 源站
   路由：#/book/<id>（多话作品按一本管理，章节在此挑选阅读） */
import { $, $$, toast, esc, relTime, fmtSize, store, siteOf, siteMeta } from './util.js';
import { detectRepo, dispatchWorkflow, newIssueUrl } from './github.js';
import * as dlstore from './dlstore.js';

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
    `<i>${relTime(m.updatedAt)}更新</i>` +
    `<i class="bid-detail" title="书架ID">ID: ${esc(m.id)}</i>`;
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
  // 保存压缩包到本机：单卷直链，分卷逐卷列出（raw 导航下载）
  const files = dlstore.bookFiles(m);
  const repo = detectRepo();
  files.forEach(f => {
    const label = files.length > 1
      ? `📦 保存 ${esc(f.name)}（${f.bytes ? fmtSize(f.bytes) : '分卷'}）`
      : '📦 保存 ZIP 到本机（含全部已上架章节）';
    const url = dlstore.rawUrls(repo, m, f.name)[0];
    acts.push(`<a class="bact" href="${esc(url)}" download>${label}</a>`);
  });
  if (hasMore) {
    acts.push(`<button class="bact" id="fetchMoreBtn">⬆ 抓取更多章节（还有 ${total - uploaded} 章未上架）</button>`);
  }
  if (m.sourceUrl) {
    acts.push(`<a class="bact ghost" href="${esc(m.sourceUrl)}" target="_blank" rel="noopener">🔗 源站查看</a>`);
  }
  // 删除双选项：本地删除（仅本机副本） / 下架（云端删除）
  const downloaded = !!dlstore.getReg(m.id);
  acts.push(`<button class="bact ghost" id="delLocalBtn" ${downloaded ? '' : 'disabled'}>🗑 本地删除（仅本机副本）</button>`);
  acts.push(`<button class="bact ghost danger" id="delCloudBtn">🧹 下架（从书架与云端删除）</button>`);
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

  // 删除双选项事件
  $('#delLocalBtn').addEventListener('click', async () => {
    await dlstore.deleteLocal(m.id, m);
    toast('已删除本机副本（云端不受影响）');
    render();
  });
  $('#delCloudBtn').addEventListener('click', async function () {
    if (this.dataset.armed !== '1') {
      this.dataset.armed = '1';
      this.textContent = '⚠️ 确认从云端删除？';
      setTimeout(() => { this.dataset.armed = ''; this.textContent = '🧹 下架（从书架与云端删除）'; }, 4000);
      return;
    }
    this.disabled = true;
    const token = store.get('token', '');
    if (token) {
      try {
        const status = await dispatchWorkflow(repo, {
          TARGET: '__delete__', DELETE_IDS: m.id, SOURCE: 'auto', BATCH: '1',
          MODE: '在线阅览 + 打包下载', KEEP_LATEST: String(store.get('keep', 30)), CLIENT_IMPL: '',
        });
        if (status === 204) {
          toast('下架任务已提交，约 1~2 分钟生效（本地副本不受影响）', 3500);
          location.hash = '#/shelf';
        } else {
          toast('触发失败：HTTP ' + status);
          this.disabled = false;
        }
      } catch (e) {
        toast('网络错误，请重试');
        this.disabled = false;
      }
    } else {
      window.open(newIssueUrl(repo, '[jm] __delete__',
        'delete: ' + m.id + '\n\n删除任务，由作品详情页引导创建。'), '_blank');
      toast('请在打开的页面点 Create 提交（约 1~2 分钟生效）', 4500);
      location.hash = '#/shelf';
    }
  });

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
