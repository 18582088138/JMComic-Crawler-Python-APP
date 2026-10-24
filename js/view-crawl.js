/* 「爬取」分页：多任务排队 + 智能识别 + 提交云端 + 进度跟踪 + 重试/取消 */
import { $, $$, toast, esc, store, siteMeta } from './util.js';
import { detectRepo, dispatchWorkflow, repoInfo, newIssueUrl } from './github.js';
import * as task from './task.js';

/* 前端识别规则（仅用于即时提示，权威识别在云端） */
const DETECT = [
  { site: 'mangadex', re: /mangadex\.org\/(?:title|chapter)\//i },
  { site: 'nhentai', re: /nhentai\.(?:net|to|xxx)\/g\//i },
  { site: 'mkzhan', re: /mkzhan\.com\//i },
  { site: 'telegraph', re: /telegra\.ph\//i },
  { site: 'wnacg', re: /wnacg\.\w+\/photos-/i },
  { site: 'ehentai', re: /e-hentai\.org\/g\//i },
  { site: 'jmcomic', re: /18comic\.\w+\/(?:album|photo)\/|\bjm\s*[:：]?\s*\d+\b|^\d+$/i },
];

const MODE_LABEL = { both: '在线阅览 + 打包下载', read: '仅在线阅览', zip: '仅打包下载' };
let mode = 'both';
let shelfCache = { ts: 0, comics: [] };  // 防重复提示用的书架快照（60s 缓存）

function detectSite(text) {
  text = (text || '').trim();
  if (!text) return null;
  for (const d of DETECT) if (d.re.test(text)) return d.site;
  // http(s) 链接或裸域名链接都走通用兜底
  if (/^https?:\/\//i.test(text) || /^[\w-]+(\.[\w-]+)+(:\d+)?(\/|$)/.test(text)) return 'generic';
  return null;
}

/* 裸域名链接（www.xxx.com/...）自动补 https:// */
function normalizeTarget(text) {
  text = (text || '').trim();
  if (!/^https?:\/\//i.test(text) && /^[\w-]+(\.[\w-]+)+(:\d+)?\//.test(text)) {
    return 'https://' + text;
  }
  return text;
}

/* 多行/多空格粘贴 → 任务列表 */
function parseTargets(raw) {
  const seen = new Set();
  const out = [];
  for (const part of raw.split(/[\s,，、]+/)) {
    const t = normalizeTarget(part);
    if (!t || seen.has(t)) continue;
    seen.add(t);
    out.push(t);
  }
  return out;
}

function refreshChip() {
  const input = $('#targetInput'), chip = $('#idChip');
  const targets = parseTargets(input.value);
  if (!targets.length) { chip.className = 'idchip'; return; }
  if (targets.length > 1) {
    const dots = targets.map(t => {
      const s = detectSite(t);
      return s && s !== 'generic' ? siteMeta(s).name : (s === 'generic' ? '兜底' : '?');
    });
    chip.className = 'idchip show';
    chip.innerHTML = `📋 识别到 <b>${targets.length}</b> 个任务：${esc(dots.join(' / '))}（一次提交，云端串行完成）`;
    return;
  }
  const site = detectSite(targets[0]);
  if (site === 'generic') {
    chip.className = 'idchip show err';
    chip.innerHTML = '🧪 未收录站点，将用通用爬虫尝试，失败会记入愿望清单';
  } else if (site) {
    const s = siteMeta(site);
    chip.className = 'idchip show';
    chip.innerHTML = `<span class="sdot" style="background:${esc(s.color)}"></span>识别平台：<b>${esc(s.name)}</b>`;
  } else {
    chip.className = 'idchip show err';
    chip.textContent = '⚠️ 无法识别，请输入漫画链接或ID';
  }
  checkDuplicate(targets[0]);
}

/* 防重复提示：书架已有该作品时提示（重爬=抓新章节/更新，属正常操作，仅提醒） */
async function checkDuplicate(target) {
  try {
    if (Date.now() - shelfCache.ts > 60000) {
      const r = await fetch('data/comics.json?t=' + Date.now(), { cache: 'no-store' });
      shelfCache = { ts: Date.now(), comics: r.ok ? (await r.json()).comics || [] : [] };
    }
    const hit = shelfCache.comics.find(c =>
      (c.sourceUrl && c.sourceUrl.replace(/\/$/, '') === target.replace(/\/$/, ''))
      || (c.id === target));
    if (hit) {
      const chip = $('#idChip');
      chip.className = 'idchip show err';
      chip.style.borderColor = 'rgba(251,191,36,.4)';
      chip.innerHTML = `⚠️ 书架已有《${esc((hit.title || '').slice(0, 24))}》（${hit.chapterCount}/${hit.totalChapters || hit.chapterCount} 章）——若为抓新章节请直接提交，将合并进同一本`;
    }
  } catch (e) { /* 提示失败不影响提交 */ }
}

function currentSource() {
  const v = $('#srcSeg .on');
  return v ? v.dataset.src : 'auto';
}

function buildSourceSeg() {
  const seg = $('#srcSeg');
  const opts = [{ key: 'auto', name: '自动识别' }].concat(window.SITES || []);
  seg.innerHTML = opts.map(s =>
    `<button data-src="${esc(s.key)}"${(s.key === 'auto') ? ' class="on"' : ''}>${esc(s.name)}</button>`).join('');
  seg.addEventListener('click', e => {
    const b = e.target.closest('button');
    if (!b) return;
    $$('button', seg).forEach(x => x.classList.toggle('on', x === b));
    const s = (window.SITES || []).find(x => x.key === b.dataset.src);
    $('#targetInput').placeholder = s ? s.hint : '粘贴漫画链接 / ID（支持一次粘贴多个，换行或空格分隔）';
  });
}

/* ================= 提交与排队 ================= */

function submitCurrent(btn) {
  const targets = parseTargets($('#targetInput').value);
  if (!targets.length) { toast('请先输入漫画链接或ID'); $('#targetInput').focus(); return; }
  const repo = detectRepo();
  if (!repo) { location.hash = '#/settings'; toast('请先在设置中填写仓库地址'); return; }

  let source = currentSource();
  if (source === 'auto' && targets.length === 1) source = detectSite(targets[0]) || 'auto';
  const batch = Math.max(1, Math.min(99, parseInt($('#batchInput').value, 10) || 1));

  task.enqueue(targets, source, batch, mode);
  $('#targetInput').value = '';
  refreshChip();
  renderQueueCard();
  toast(targets.length > 1 ? `已加入队列：${targets.length} 个任务` : '已加入队列');
  pumpQueue();
}

/* 队列泵：有运行中的任务就等它完成；否则立即派发队首 */
function pumpQueue() {
  const state = task.getState();
  if (state.active) { renderQueueCard(); return; }  // 有任务在跑，等完成回调
  const claimed = task.claimNextToDispatch();
  if (!claimed) { renderQueueCard(); return; }
  doDispatch(claimed);
}

function doDispatch(claimed) {
  const repo = detectRepo();
  const keep = Math.max(1, Math.min(99, parseInt(store.get('keep', 30), 10) || 30));
  const modeLabel = MODE_LABEL[claimed.mode] || MODE_LABEL.both;
  const targets = claimed.targets;
  const targetText = targets.join('\n');
  const single = targets.length === 1;

  showTaskCard(targets);
  setStepSub('dispatch', `派发中…（共 ${targets.length} 个任务）`);

  const token = store.get('token', '');
  if (token) {
    dispatchWorkflow(repo, {
      TARGET: targetText, SOURCE: claimed.source === 'generic' ? 'auto' : claimed.source,
      BATCH: String(claimed.batch || 1), MODE: modeLabel,
      KEEP_LATEST: String(keep), CLIENT_IMPL: '',  // ⚠️ inputs 全部必须是字符串
    }).then(status => {
      if (status === 204) {
        task.markDispatched();
        setStep('dispatch', 'done');
        setStepSub('run', '工作流已触发，等待运行…');
        task.startLoop(onTaskChange);
        toast('任务已提交 🚀');
      } else {
        task.markDispatchFailed();
        if (status === 404) toast('触发失败(404)：请检查仓库名与 Token 权限');
        else if (status === 401 || status === 403) toast('Token 无效或权限不足（需要 Actions 读写权限）');
        else if (status === 422) toast('触发失败(422)：请强制刷新页面后重试');
        else toast('触发失败：HTTP ' + status);
        renderQueueCard();
      }
    }).catch(() => { task.markDispatchFailed(); toast('网络错误，无法连接 GitHub API'); renderQueueCard(); });
  } else {
    // 免 Token 兜底：一个 issue 携带全部任务（正文每行 task: <链接>）
    repoInfo(repo).then(info => {
      if (info && info.has_issues === false) {
        task.markDispatchFailed();
        toast('仓库未开启 Issues 功能，请按设置页清单开启，或改用 Token 提交', 4500);
        location.hash = '#/settings';
        renderQueueCard();
        return;
      }
      const lines = ['mode: ' + (claimed.mode || 'both')];
      if (claimed.source !== 'auto' && claimed.source !== 'generic') lines.push('source: ' + claimed.source);
      if ((claimed.batch || 1) > 1) lines.push('batch: ' + claimed.batch);
      targets.forEach(t => lines.push('task: ' + t));
      lines.push('', '由云书架网页引导创建，爬取完成后本 issue 会自动收到链接并被关闭。');
      const title = single ? '[jm] ' + targets[0] : `[jm] 多任务 x${targets.length}`;
      task.markDispatched();  // issue 需要用户手动 Create，进入「等待运行」跟踪
      setStepSub('dispatch', '请在打开的页面中点「Create」创建 Issue');
      document.querySelector('.step[data-s="dispatch"]').classList.add('doing');
      task.startLoop(onTaskChange);
      window.open(newIssueUrl(repo, title, lines.join('\n')), '_blank');
      toast('已打开 Issue 创建页，提交后自动开始爬取', 3500);
    }).catch(() => { task.markDispatchFailed(); toast('网络异常，无法预检仓库状态'); renderQueueCard(); });
  }
}

/* ================= 任务状态变化回调 ================= */

function onTaskChange(status) {
  if (status === 'success' || status === 'failed') {
    renderQueueCard();
    if (status === 'success') {
      setStep('dispatch', 'done'); setStep('run', 'done');
      setStep('deploy', 'done'); setStep('done', 'done');
      const btn = $('#goReadBtn');
      btn.style.display = 'flex';
      btn.onclick = () => { location.hash = '#/shelf'; };
    }
    if (status === 'failed') setStep('run', 'fail');
  }
  if (status === 'next') pumpQueue();  // 队列下一个
}

/* ================= 队列卡片渲染 ================= */

function setStep(name, state) {
  const el = document.querySelector('.step[data-s="' + name + '"]');
  if (!el) return;
  el.classList.remove('doing', 'done', 'fail');
  if (state) el.classList.add(state);
}
function setStepSub(name, sub) {
  const el = document.querySelector(`.step[data-s="${name}"] small`);
  if (el) el.textContent = sub;
}

function showTaskCard(targets) {
  $('#taskCard').style.display = 'block';
  $('#taskId').textContent = targets.length > 1 ? `${targets.length} 个任务` : shortTarget(targets[0]);
  ['dispatch', 'run', 'deploy', 'done'].forEach(s => setStep(s, null));
  $('#goReadBtn').style.display = 'none';
  $('#taskCard').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function shortTarget(t) {
  return t.length > 34 ? t.slice(0, 34) + '…' : t;
}

function renderQueueCard() {
  const state = task.getState();
  const box = $('#queueCard');
  const items = [];
  if (state.active) {
    items.push(`<div class="qrow doing"><span class="qd">⚡</span><span class="qt">进行中：${esc(state.active.targets.map(shortTarget).join('，'))}</span>
      <button class="qact" id="cancelActiveBtn">取消</button></div>`);
  }
  state.queue.forEach((q, i) => {
    items.push(`<div class="qrow"><span class="qd">🕐</span><span class="qt">${esc(q.targets.map(shortTarget).join('，'))}</span>
      <button class="qact" data-qidx="${i}">移除</button></div>`);
  });
  if (state.lastFailed) {
    items.push(`<div class="qrow failed"><span class="qd">❌</span><span class="qt">${esc(state.lastFailed.targets.map(shortTarget).join('，'))}（${esc(state.lastFailed.reason || '失败')}）</span>
      <button class="qact" id="retryBtn">重试</button></div>`);
  }
  if (!items.length) {
    box.style.display = 'none';
    if (!state.active) $('#taskCard').style.display = 'none';
    return;
  }
  box.style.display = 'block';
  box.innerHTML = '<b class="qtitle">任务队列</b>' + items.join('');
  box.querySelectorAll('[data-qidx]').forEach(b => {
    b.addEventListener('click', () => {
      task.cancelQueued(parseInt(b.dataset.qidx, 10));
      renderQueueCard();
      toast('已从队列移除');
    });
  });
  const cb = $('#cancelActiveBtn');
  if (cb) cb.addEventListener('click', async () => {
    cb.disabled = true;
    await task.cancelActive();
    $('#taskCard').style.display = 'none';
    renderQueueCard();
    pumpQueue();
  });
  const rb = $('#retryBtn');
  if (rb) rb.addEventListener('click', () => {
    task.retryFailed();
    renderQueueCard();
    pumpQueue();
  });
}

/* 最近一次任务状态（P1 失败可见性） */
function loadLastRun() {
  fetch('data/last_run.json?t=' + Date.now(), { cache: 'no-store' })
    .then(r => (r.ok ? r.json() : null))
    .then(run => {
      if (!run || task.hasPending()) return;
      const box = $('#lastRun');
      const ageH = (Date.now() - new Date(run.ts).getTime()) / 3600000;
      if ((run.status === 'failed' || run.status === 'partial') && ageH < 24) {
        box.style.display = 'block';
        let html = `<b>⚠️ 最近一次任务${run.status === 'partial' ? '部分失败' : '失败'}</b><span>${esc(run.error || '部分目标失败')}</span>`;
        if (run.failed && run.failed.length) {
          html += '<span>' + run.failed.map(f => esc(f.target.slice(0, 40)) + '：' + esc(f.error.slice(0, 60))).join('<br>') + '</span>';
        }
        html += (run.wishlisted ? `<span>已将 ${esc(run.wishlisted)} 记入「设置 → 愿望清单」</span>` : '') +
          (run.run_url ? `<a href="${esc(run.run_url)}" target="_blank" rel="noopener">查看日志 →</a>` : '');
        box.innerHTML = html;
      } else {
        box.style.display = 'none';
      }
    })
    .catch(() => {});
}

export function initCrawlView(onTaskDone) {
  buildSourceSeg();
  const input = $('#targetInput');
  input.addEventListener('input', refreshChip);
  $('#pasteBtn').addEventListener('click', () => {
    if (!navigator.clipboard || !navigator.clipboard.readText) {
      toast('当前浏览器不支持读取剪贴板，请长按输入框粘贴');
      input.focus();
      return;
    }
    navigator.clipboard.readText().then(t => {
      input.value = input.value ? input.value + '\n' + t : t;
      refreshChip();
      toast('已粘贴（追加到现有内容）');
    }, () => toast('读取剪贴板失败，请手动粘贴'));
  });
  $('#modeSeg').addEventListener('click', e => {
    const b = e.target.closest('button');
    if (!b) return;
    mode = b.dataset.mode;
    $$('button', e.currentTarget).forEach(x => x.classList.toggle('on', x === b));
  });
  $('#submitBtn').addEventListener('click', function () { submitCurrent(this); });
  $('#offlineBtn').addEventListener('click', () => { location.href = 'reader.html?local=1'; });
  $('#taskRefreshBtn').addEventListener('click', () => { renderQueueCard(); loadLastRun(); toast('已检查最新状态'); });
  $('#taskResetBtn').addEventListener('click', () => { $('#taskCard').style.display = 'none'; });

  // 恢复：有运行中任务则继续跟踪；队列有剩余则继续派发
  const state = task.getState();
  if (state.active) {
    showTaskCard(state.active.targets);
    setStep('dispatch', 'done');
    setStepSub('run', '恢复任务跟踪…');
    task.startLoop(onTaskChange);
  }
  renderQueueCard();
  loadLastRun();
  if (state.queue.length && !state.active) pumpQueue();
}

/* 作品详情页「抓取更多章节」跳转过来的预填 */
export function applyCrawlPrefill() {
  const pre = store.get('crawl_prefill', null);
  if (!pre) return;
  store.set('crawl_prefill', null);
  if (pre.target) {
    const input = $('#targetInput');
    input.value = input.value ? input.value + '\n' + pre.target : pre.target;
    refreshChip();
  }
  if (pre.batch) {
    $('#batchInput').value = pre.batch;
    toast(`已预填：第 ${pre.batch} 批`, 2600);
  }
}
