/* 「爬取」分页：智能识别输入 → 提交云端任务 → 进度跟踪 */
import { $, $$, toast, esc, store, siteMeta } from './util.js';
import { detectRepo, dispatchWorkflow, repoInfo, newIssueUrl } from './github.js';
import * as task from './task.js';

/* 前端识别规则（仅用于即时提示，权威识别在云端） */
const DETECT = [
  { site: 'mangadex', re: /mangadex\.org\/(?:title|chapter)\//i },
  { site: 'nhentai', re: /nhentai\.(?:net|to|xxx)\/g\//i },
  { site: 'manhuagui', re: /manhuagui\.com\/comic\//i },
  { site: 'mangabz', re: /mangabz\.com\//i },
  { site: 'mheee', re: /mheee\.com\//i },
  { site: 'baozimh', re: /baozimh\.com\//i },
  { site: 'mkzhan', re: /mkzhan\.com\//i },
  { site: 'kuaikan', re: /kuaikanmanhua\.com\//i },
  { site: 'copymanga', re: /(?:copymanga|mangacopy)\.\w+\/comic\//i },
  { site: 'wnacg', re: /wnacg\.\w+\/photos-/i },
  { site: 'ehentai', re: /e-hentai\.org\/g\//i },
  { site: 'bika', re: /picacomic\.com\/|\b[0-9a-f]{24}\b/i },
  { site: 'jmcomic', re: /18comic\.\w+\/(?:album|photo)\/|\bjm\s*[:：]?\s*\d+\b|^\d+$/i },
];

const MODE_LABEL = { both: '在线阅览 + 打包下载', read: '仅在线阅览', zip: '仅打包下载' };
let mode = 'both';

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

function refreshChip() {
  const input = $('#targetInput'), chip = $('#idChip');
  const v = input.value.trim();
  if (!v) { chip.className = 'idchip'; return; }
  const site = detectSite(v);
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
    $('#targetInput').placeholder = s ? s.hint : '粘贴任意已支持站点的漫画链接 / ID，自动识别平台';
  });
}

function submitCurrent(btn) {
  const target = normalizeTarget($('#targetInput').value);
  if (!target) { toast('请先输入漫画链接或ID'); $('#targetInput').focus(); return; }
  const repo = detectRepo();
  if (!repo) { location.hash = '#/settings'; toast('请先在设置中填写仓库地址'); return; }

  let source = currentSource();
  if (source === 'auto') source = detectSite(target) || 'auto';
  const batch = Math.max(1, Math.min(99, parseInt($('#batchInput').value, 10) || 1));
  const keep = Math.max(1, Math.min(99, parseInt(store.get('keep', 30), 10) || 30));

  btn.disabled = true;
  const label = btn.textContent;
  btn.textContent = '提交中…';
  const done = () => { btn.disabled = false; btn.textContent = label; };

  const token = store.get('token', '');
  if (token) {
    dispatchWorkflow(repo, {
      TARGET: target, SOURCE: source === 'generic' ? 'auto' : source,
      BATCH: String(batch), MODE: MODE_LABEL[mode],
      KEEP_LATEST: String(keep), CLIENT_IMPL: '',  // ⚠️ inputs 全部必须是字符串，否则 GitHub 返回 422
    }).then(status => {
      done();
      if (status === 204) {
        task.setPending({ id: target, target, source, ts: Date.now() });
        task.showTask(target);
        document.querySelector('.step[data-s="dispatch"]').classList.add('done');
        setStepSub('run', '工作流已触发，等待运行…');
        task.startTracking(() => {});
        toast('任务已提交 🚀');
        $('#taskCard').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      } else if (status === 404) toast('触发失败(404)：请检查仓库名与 Token 权限');
      else if (status === 401 || status === 403) toast('Token 无效或权限不足（需要 Actions 读写权限）');
      else if (status === 422) toast('触发失败(422)：工作流已更新，请强制刷新页面（清缓存）后重试');
      else toast('触发失败：HTTP ' + status);
    }).catch(() => { done(); toast('网络错误，无法连接 GitHub API'); });
  } else {
    // 免 Token 兜底：开 issue 触发。先预检 Issues 是否开启
    repoInfo(repo).then(info => {
      done();
      if (info && info.has_issues === false) {
        toast('仓库未开启 Issues 功能，请按设置页清单开启，或改用 Token 提交', 4500);
        location.hash = '#/settings';
        return;
      }
      const lines = ['mode: ' + mode];
      if (source !== 'auto' && source !== 'generic') lines.push('source: ' + source);
      if (source !== 'jmcomic' && batch > 1) lines.push('batch: ' + batch);
      lines.push('', '由云书架网页引导创建，爬取完成后本 issue 会自动收到链接并被关闭。');
      task.setPending({ id: target, target, source, ts: Date.now() });
      task.showTask(target);
      setStepSub('dispatch', '请在打开的页面中点「Create」创建 Issue');
      document.querySelector('.step[data-s="dispatch"]').classList.add('doing');
      task.startTracking(() => {});
      window.open(newIssueUrl(repo, '[jm] ' + target, lines.join('\n')), '_blank');
      toast('已打开 Issue 创建页，提交后自动开始爬取', 3500);
    }).catch(() => { done(); toast('网络异常，无法预检仓库状态'); });
  }
}

function setStepSub(name, sub) {
  const el = document.querySelector(`.step[data-s="${name}"] small`);
  if (el) el.textContent = sub;
}

/* 最近一次任务状态（P1 失败可见性） */
function loadLastRun() {
  fetch('data/last_run.json?t=' + Date.now(), { cache: 'no-store' })
    .then(r => (r.ok ? r.json() : null))
    .then(run => {
      if (!run || task.getPending()) return;
      const box = $('#lastRun');
      const ageH = (Date.now() - new Date(run.ts).getTime()) / 3600000;
      if (run.status === 'failed' && ageH < 24) {
        box.style.display = 'block';
        box.innerHTML = `<b>⚠️ 最近一次任务失败</b><span>${esc(run.error || '未知原因')}</span>` +
          (run.wishlisted ? `<span>已将 ${esc(run.wishlisted)} 记入「设置 → 愿望清单」</span>` : '') +
          (run.run_url ? `<a href="${esc(run.run_url)}" target="_blank" rel="noopener">查看日志 →</a>` : '');
      } else {
        box.style.display = 'none';
      }
    })
    .catch(() => {});
}

/* 作品详情页「抓取更多章节」跳转过来的预填 */
export function applyCrawlPrefill() {
  const pre = store.get('crawl_prefill', null);
  if (!pre) return;
  store.set('crawl_prefill', null);
  if (pre.target) {
    const input = $('#targetInput');
    input.value = pre.target;
    refreshChip();
  }
  if (pre.batch) {
    $('#batchInput').value = pre.batch;
    toast(`已预填：第 ${pre.batch} 批`, 2600);
  }
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
    navigator.clipboard.readText().then(t => { input.value = t; refreshChip(); toast('已粘贴'); },
      () => toast('读取剪贴板失败，请手动粘贴'));
  });
  $('#modeSeg').addEventListener('click', e => {
    const b = e.target.closest('button');
    if (!b) return;
    mode = b.dataset.mode;
    $$('button', e.currentTarget).forEach(x => x.classList.toggle('on', x === b));
  });
  $('#submitBtn').addEventListener('click', function () { submitCurrent(this); });
  $('#offlineBtn').addEventListener('click', () => { location.href = 'reader.html?local=1'; });
  $('#taskRefreshBtn').addEventListener('click', () => task.checkNow());
  $('#taskResetBtn').addEventListener('click', () => task.stopTracking());
  task.resumeTrackingIfNeeded(onTaskDone);
  loadLastRun();
}
