/* GitHub API 封装：任务触发 / 状态轮询 / 仓库预检 */
import { store } from './util.js';

export function detectRepo() {
  const saved = store.get('repo', '');
  if (saved) return saved;
  const h = location.hostname;
  if (/\.github\.io$/.test(h)) {
    const owner = h.split('.')[0];
    const seg = location.pathname.split('/').filter(Boolean);
    if (seg.length) return owner + '/' + seg[0];
  }
  return '';
}

function headers(withAuth) {
  const h = { Accept: 'application/vnd.github+json' };
  const token = store.get('token', '');
  if (withAuth && token) h.Authorization = 'Bearer ' + token;
  return h;
}

export async function dispatchWorkflow(repo, inputs, workflow = 'pages_crawler.yml') {
  const r = await fetch(`https://api.github.com/repos/${repo}/actions/workflows/${workflow}/dispatches`, {
    method: 'POST',
    headers: { ...headers(true), 'Content-Type': 'application/json' },
    body: JSON.stringify({ ref: 'master', inputs }),
  });
  return r.status;
}

export async function fetchRecentRuns(repo) {
  const r = await fetch(
    `https://api.github.com/repos/${repo}/actions/workflows/pages_crawler.yml/runs?per_page=3`,
    { headers: headers(true) });
  if (!r.ok) return null;
  const data = await r.json();
  return data.workflow_runs || [];
}

export async function repoInfo(repo) {
  const r = await fetch(`https://api.github.com/repos/${repo}`, { headers: headers(true) });
  return r.ok ? r.json() : null;
}

export function newIssueUrl(repo, title, body) {
  return `https://github.com/${repo}/issues/new?title=${encodeURIComponent(title)}&body=${encodeURIComponent(body)}`;
}

export async function cancelRun(repo, runId, token) {
  const r = await fetch(`https://api.github.com/repos/${repo}/actions/runs/${runId}/cancel`, {
    method: 'POST',
    headers: { Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json' },
  });
  return r.status;
}
