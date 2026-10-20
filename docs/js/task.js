/* 任务进度状态机：提交后轮询书架索引 + Actions 运行状态 */
import { $, store, toast, fmtId } from './util.js';
import { detectRepo, fetchRecentRuns } from './github.js';

let pending = store.get('pending', null); // {id, target, source, ts}
let pollTimer = null, elapsedTimer = null, lastRunPoll = 0;
let onDoneCallback = null;

export function getPending() { return pending; }

function setStep(name, state, sub) {
  const el = document.querySelector('.step[data-s="' + name + '"]');
  if (!el) return;
  el.classList.remove('doing', 'done', 'fail');
  if (state) el.classList.add(state);
  if (sub) { const s = el.querySelector('small'); if (s) s.textContent = sub; }
}

function resetSteps() {
  ['dispatch', 'run', 'deploy', 'done'].forEach(s => setStep(s, null));
  const link = $('#runLink');
  if (link) link.style.display = 'none';
}

export function showTask(id) {
  $('#taskCard').style.display = 'block';
  $('#taskId').textContent = fmtId(id);
  resetSteps();
  $('#goReadBtn').style.display = 'none';
}

export function setPending(p) {
  pending = p;
  store.set('pending', p);
}

export function startTracking(onDone) {
  onDoneCallback = onDone;
  startElapsed();
  stopPolling();
  pollShelf();
  pollRun(true);
  pollTimer = setInterval(() => {
    if (!pending) { stopPolling(); return; }
    if (Date.now() - pending.ts > 20 * 60 * 1000) {
      setStep('deploy', 'fail', '等待超时：请到 Actions 页面或 Issue 中查看任务状态');
      stopPolling();
      return;
    }
    pollShelf();
    pollRun(false);
  }, 15000);
}

export function stopTracking() {
  pending = null;
  store.set('pending', null);
  stopPolling();
  stopElapsed();
  $('#taskCard').style.display = 'none';
}

function stopPolling() { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } }

function startElapsed() {
  stopElapsed();
  elapsedTimer = setInterval(() => {
    if (!pending) { stopElapsed(); return; }
    const s = Math.floor((Date.now() - pending.ts) / 1000);
    $('#elapsed').textContent = '已等待 ' + Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
  }, 1000);
}

function stopElapsed() {
  if (elapsedTimer) { clearInterval(elapsedTimer); elapsedTimer = null; }
  const el = $('#elapsed');
  if (el) el.textContent = '';
}

function pollShelf() {
  fetch('data/comics.json?t=' + Date.now(), { cache: 'no-store' })
    .then(r => (r.ok ? r.json() : null))
    .then(data => {
      if (!data || !pending) return;
      const comics = data.comics || [];
      let hit = comics.find(c => String(c.id) === String(pending.id));
      if (!hit) {
        // 兜底：接受提交后新上架的任何本子（auto 识别出的 ID 可能与输入不一致）
        hit = comics.find(c => new Date(c.updatedAt || 0).getTime() >= pending.ts - 15000);
      }
      if (hit) taskDone(hit.id);
    })
    .catch(() => {});
}

function pollRun(force) {
  const repo = detectRepo();
  if (!repo) return;
  // 无 token 也可以查公开仓库的运行状态，但限流 60 次/小时，放慢到 75 秒一次
  const minGap = store.get('token', '') ? 0 : 75000;
  const now = Date.now();
  if (!force && now - lastRunPoll < minGap) return;
  lastRunPoll = now;
  fetchRecentRuns(repo).then(runs => {
    if (!runs || !pending) return;
    const run = runs.find(x => new Date(x.created_at).getTime() >= pending.ts - 90000);
    const waited = Date.now() - pending.ts;
    if (!run) {
      if (waited > 120000) setStep('run', 'doing', '未找到运行记录：若刚提交请稍候；若用 Issue 方式请确认已点「Create」');
      return;
    }
    const link = $('#runLink');
    link.href = run.html_url;
    link.style.display = 'inline-block';
    if (run.status === 'queued') {
      setStep('dispatch', 'done'); setStep('run', 'doing', '排队中…');
    } else if (run.status === 'in_progress') {
      setStep('dispatch', 'done');
      setStep('run', 'doing', waited > 8 * 60000 ? '下载时间较长（本子较大或源站较慢），可点下方链接查看日志' : '正在云端下载…');
    } else if (run.status === 'completed') {
      if (run.conclusion === 'success') {
        setStep('dispatch', 'done'); setStep('run', 'done'); setStep('deploy', 'doing');
      } else {
        setStep('run', 'fail', '工作流执行失败：' + run.conclusion + '，点击查看日志');
        stopPolling();
      }
    }
  }).catch(() => {});
}

function taskDone(foundId) {
  if (!pending) return;
  const id = foundId || pending.id;
  setStep('dispatch', 'done'); setStep('run', 'done'); setStep('deploy', 'done'); setStep('done', 'done');
  const btn = $('#goReadBtn');
  btn.style.display = 'flex';
  btn.onclick = () => { location.href = 'reader.html?id=' + encodeURIComponent(id); };
  toast('🎉 已上架书架！');
  pending = null;
  store.set('pending', null);
  stopPolling();
  stopElapsed();
  if (onDoneCallback) onDoneCallback(id);
}

export function resumeTrackingIfNeeded(onDone) {
  if (!pending) return;
  showTask(pending.id);
  setStep('dispatch', 'done');
  setStep('run', 'doing', '恢复任务跟踪…');
  startTracking(onDone);
}

export function checkNow() {
  if (!pending) { toast('当前没有进行中的任务'); return; }
  pollShelf();
  pollRun(true);
  toast('已检查最新状态');
}
