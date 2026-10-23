/* 任务系统：本地队列 + 进度跟踪 + 重试/取消

模型：
- 多行粘贴 → 一个 run 内串行完成（dispatch 一次，多行 TARGET）
- 连续分批提交 → 本地队列（jm_crawl_queue），当前 run 完成后自动派发下一个
  （云端并发组只保留 1 运行 + 1 待运行，快速连发会丢任务，所以串行化在前端做）
- 失败任务存入 jm_last_failed，爬取页提供「重试」
- 取消：排队中的直接移除；运行中的 run 有 token 时调 API 取消
*/
import { $, store, toast } from './util.js';
import { detectRepo, fetchRecentRuns, cancelRun } from './github.js';

let queue = store.get('crawl_queue', []);        // [{targets, source, batch, mode, ts}]
let active = store.get('crawl_active', null);    // {targets, ts, source, runId?}
let lastFailed = store.get('last_failed', null); // {targets, ts, reason}
let pollTimer = null, elapsedTimer = null, lastRunPoll = 0;
let onChangeCallback = null;

export function getState() {
  return { queue, active, lastFailed };
}

export function hasPending() {
  return !!(active || queue.length);
}

/* ================= 入队与派发 ================= */

export function enqueue(targets, source, batch, mode) {
  queue.push({ targets, source, batch, mode, ts: Date.now() });
  store.set('crawl_queue', queue);
  startLoop();
}

function dispatchNext() {
  if (active || !queue.length) return false;
  const item = queue.shift();
  store.set('crawl_queue', queue);
  active = { targets: item.targets, source: item.source, ts: Date.now(), mode: item.mode, batch: item.batch };
  store.set('crawl_active', active);
  return true; // 由调用方真正 dispatch（view-crawl 里做）
}

/* 外部接口：领取待派发任务（view-crawl 调用，真正发 dispatch/issue） */
export function claimNextToDispatch() {
  return dispatchNext() ? active : null;
}

export function markDispatchFailed() {
  // dispatch 没发出去：放回队首
  if (active) {
    queue.unshift({ targets: active.targets, source: active.source, batch: active.batch, mode: active.mode, ts: active.ts });
    store.set('crawl_queue', queue);
    active = null;
    store.set('crawl_active', null);
  }
}

export function markDispatched() {
  startLoop();
  if (onChangeCallback) onChangeCallback();
}

/* ================= 取消 ================= */

export function cancelQueued(index) {
  queue.splice(index, 1);
  store.set('crawl_queue', queue);
  if (onChangeCallback) onChangeCallback();
}

export async function cancelActive() {
  const repo = detectRepo();
  const token = store.get('token', '');
  if (active && active.runId && token && repo) {
    const status = await cancelRun(repo, active.runId, token);
    if (status === 202) toast('已请求取消该次运行');
    else toast('取消请求失败：HTTP ' + status);
  }
  // 无论云端是否取消成功，本地先终止跟踪
  if (active) {
    lastFailed = { targets: active.targets, ts: Date.now(), reason: '已手动取消' };
    store.set('last_failed', lastFailed);
  }
  active = null;
  store.set('crawl_active', null);
  if (onChangeCallback) onChangeCallback();
  // 队列下一个立刻可派发（view-crawl 负责真正发）
  return true;
}

/* ================= 重试 ================= */

export function retryFailed() {
  if (!lastFailed) return null;
  enqueue(lastFailed.targets, 'auto', lastFailed.batch || 1, lastFailed.mode || 'both');
  lastFailed = null;
  store.set('last_failed', null);
  if (onChangeCallback) onChangeCallback();
  return true;
}

/* ================= 轮询循环 ================= */

function setStep(name, state, sub) {
  const el = document.querySelector('.step[data-s="' + name + '"]');
  if (!el) return;
  el.classList.remove('doing', 'done', 'fail');
  if (state) el.classList.add(state);
  if (sub) { const s = el.querySelector('small'); if (s) s.textContent = sub; }
}

export function startLoop(onChange) {
  if (onChange) onChangeCallback = onChange;
  stopLoop();
  pollOnce(true);
  pollTimer = setInterval(() => pollOnce(false), 15000);
  startElapsed();
}

function stopLoop() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}

function startElapsed() {
  stopElapsed();
  elapsedTimer = setInterval(() => {
    const el = $('#elapsed');
    if (!el || !active) { stopElapsed(); return; }
    const s = Math.floor((Date.now() - active.ts) / 1000);
    el.textContent = '已等待 ' + Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
  }, 1000);
}

function stopElapsed() {
  if (elapsedTimer) { clearInterval(elapsedTimer); elapsedTimer = null; }
  const el = $('#elapsed');
  if (el) el.textContent = '';
}

function pollOnce(force) {
  if (!active) return;
  if (Date.now() - active.ts > 30 * 60 * 1000) {
    finishActive('failed', '等待超时（30 分钟）');
    return;
  }
  pollShelf();
  pollRun(force);
}

function pollShelf() {
  fetch('data/comics.json?t=' + Date.now(), { cache: 'no-store' })
    .then(r => (r.ok ? r.json() : null))
    .then(data => {
      if (!data || !active) return;
      // 书架出现本次时间窗内的新本子 → 至少一个任务完成（进度展示用）
      const hit = (data.comics || []).find(c =>
        new Date(c.updatedAt || 0).getTime() >= active.ts - 15000);
      if (hit) setStep('deploy', 'doing', '检测到新本子上架：《' + (hit.title || '').slice(0, 20) + '》…');
    }).catch(() => {});
}

function pollRun(force) {
  const repo = detectRepo();
  if (!repo || !active) return;
  const minGap = store.get('token', '') ? 0 : 75000;
  const now = Date.now();
  if (!force && now - lastRunPoll < minGap) return;
  lastRunPoll = now;
  fetchRecentRuns(repo).then(runs => {
    if (!runs || !active) return;
    const run = runs.find(x => new Date(x.created_at).getTime() >= active.ts - 90000);
    if (!run) {
      if (Date.now() - active.ts > 120000) setStep('run', 'doing', '未找到运行记录：若刚提交请稍候；若用 Issue 方式请确认已点「Create」');
      return;
    }
    active.runId = run.id;
    const link = $('#runLink');
    if (link) { link.href = run.html_url; link.style.display = 'inline-block'; }
    if (run.status === 'queued') {
      setStep('dispatch', 'done'); setStep('run', 'doing', '排队中…');
    } else if (run.status === 'in_progress') {
      setStep('dispatch', 'done');
      setStep('run', 'doing', Date.now() - active.ts > 8 * 60000 ? '下载时间较长（本子较大或源站较慢），可点下方链接查看日志' : '正在云端下载…');
    } else if (run.status === 'completed') {
      if (run.conclusion === 'success') {
        finishActive('success');
      } else {
        finishActive('failed', '工作流执行失败：' + run.conclusion);
      }
    }
  }).catch(() => {});
}

function finishActive(status, reason) {
  const finished = active;
  active = null;
  store.set('crawl_active', null);
  if (status === 'success') {
    lastFailed = null;
    store.set('last_failed', null);
    toast('🎉 任务完成，已上架书架！');
  } else {
    lastFailed = { targets: finished.targets, ts: Date.now(), reason: reason || '执行失败', batch: finished.batch, mode: finished.mode };
    store.set('last_failed', lastFailed);
    toast('❌ ' + (reason || '执行失败'), 4000);
  }
  stopLoop();
  stopElapsed();
  if (onChangeCallback) onChangeCallback(status, finished);
  // 队列还有任务则立即派发下一个（view-crawl 负责）
  if (queue.length && onChangeCallback) onChangeCallback('next');
}
