/* 「设置」分页：仓库/Token/首次检查清单/本地存储/愿望清单 */
import { $, toast, esc, relTime, fmtSize, store } from './util.js';
import { detectRepo } from './github.js';
import * as dlstore from './dlstore.js';

function loadWishlist() {
  const box = $('#wishList');
  fetch('data/wishlist.json?t=' + Date.now(), { cache: 'no-store' })
    .then(r => (r.ok ? r.json() : null))
    .then(data => {
      const items = (data && data.items) || [];
      if (!items.length) {
        box.innerHTML = '<div class="wempty">暂无记录。提交了未收录站点的链接且通用爬虫尝试失败后，站点会自动记入这里，作为后续开发候选。</div>';
        return;
      }
      box.innerHTML = items.map(i => `
        <div class="wish">
          <span class="wdot"></span>
          <div class="winfo"><b>${esc(i.domain)}</b><span>${esc(i.reason || '')}</span></div>
          <span class="wtime">${relTime(i.ts)}</span>
        </div>`).join('');
    })
    .catch(() => {
      box.innerHTML = '<div class="wempty">暂时无法读取愿望清单。</div>';
    });
}

/* ---------- 本地漫画存储面板 ---------- */

async function renderStoragePanel() {
  const mode = dlstore.storeMode();
  const dirName = mode === 'fsa' ? await dlstore.customDirName() : '';
  const persisted = await dlstore.persisted();
  $('#storeModeHint').innerHTML = mode === 'fsa'
    ? `已选择自定义文件夹：<b>${esc(dirName || '(已失效，请重选)')}</b>（资源管理器可直接看到；每次冷启动首次操作需点一次授权）`
    : '当前使用浏览器内置存储（OPFS）：全平台可用，对用户不可见，数据随站点保存。';

  const est = await dlstore.estimate();
  $('#storageEst').textContent = est
    ? `浏览器存储占用 ${fmtSize(est.usage)} / 配额约 ${fmtSize(est.quota)}${persisted ? '（已申请防自动清理）' : ''}`
    : '当前浏览器不支持查询存储占用。';

  const list = dlstore.listLocal();
  const box = $('#localList');
  if (!list.length) {
    box.innerHTML = '<div class="wempty">还没有下载过本子。到书架点「阅读」即可自动下载。</div>';
  } else {
    box.innerHTML = list.map(r => `
      <div class="storow" data-id="${esc(r.id)}">
        <span class="sdot">${r.store === 'fsa' ? '📁' : '🧠'}</span>
        <div class="st"><b title="${esc(r.title)}">${esc(r.title)}</b>
          <span>${esc(r.id)} · ${fmtSize(r.bytes)} · ${relTime(r.ts)}下载</span></div>
        <button class="sdel">删除</button>
      </div>`).join('');
    box.querySelectorAll('.storow').forEach(row => {
      row.querySelector('.sdel').addEventListener('click', async () => {
        const id = row.dataset.id;
        await dlstore.deleteLocal(id);
        toast('已删除本地包（云端不受影响）');
        renderStoragePanel();
      });
    });
  }
}

export function initSettingsView() {
  $('#repoInput').value = detectRepo();
  $('#tokenInput').value = store.get('token', '');
  $('#keepInput').value = store.get('keep', 30);
  $('#taskTimeoutInput').value = store.get('task_timeout', 15);
  $('#saveSettingsBtn').addEventListener('click', () => {
    store.set('repo', $('#repoInput').value.trim());
    store.set('token', $('#tokenInput').value.trim());
    const keep = Math.max(1, Math.min(99, parseInt($('#keepInput').value, 10) || 30));
    store.set('keep', keep);
    const tmo = Math.max(5, Math.min(120, parseInt($('#taskTimeoutInput').value, 10) || 15));
    store.set('task_timeout', tmo);
    toast('设置已保存（仅存储在本机浏览器）');
  });
  $('#makeTokenBtn').addEventListener('click', () => {
    window.open('https://github.com/settings/tokens/new?scopes=public_repo&description=' +
      encodeURIComponent('云书架一键提交'), '_blank');
    toast('在打开的页面拉到底点 Generate token，复制 ghp_ 字符串回来粘贴', 4500);
  });
  // 多设备配置搬家（含 Token，仅经剪贴板，不经任何服务器）
  $('#exportCfgBtn').addEventListener('click', () => {
    const cfg = {
      repo: $('#repoInput').value.trim() || detectRepo(),
      token: $('#tokenInput').value.trim() || store.get('token', ''),
      keep: store.get('keep', 30),
      task_timeout: store.get('task_timeout', 15),
    };
    if (!cfg.token) { toast('当前设备还没配置 Token，无可导出'); return; }
    const text = btoa(unescape(encodeURIComponent(JSON.stringify(cfg))));
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text)
        .then(() => toast('配置已复制，到新设备粘贴导入即可', 3000))
        .catch(() => toast('复制失败，请手动选择复制：' + text, 6000));
    } else {
      toast('请手动复制：' + text, 8000);
    }
  });
  $('#importCfgBtn').addEventListener('click', () => {
    if (!navigator.clipboard || !navigator.clipboard.readText) {
      toast('当前浏览器不支持读取剪贴板，请手动粘贴到 Token 输入框');
      return;
    }
    navigator.clipboard.readText().then(t => {
      try {
        const cfg = JSON.parse(decodeURIComponent(escape(atob(t.trim()))));
        if (cfg.repo) { store.set('repo', cfg.repo); $('#repoInput').value = cfg.repo; }
        if (cfg.token) { store.set('token', cfg.token); $('#tokenInput').value = cfg.token; }
        if (cfg.keep) { store.set('keep', cfg.keep); $('#keepInput').value = cfg.keep; }
        if (cfg.task_timeout) { store.set('task_timeout', cfg.task_timeout); $('#taskTimeoutInput').value = cfg.task_timeout; }
        toast('✅ 配置已导入并保存，以后所有操作后台静默完成', 3500);
      } catch (e) {
        toast('剪贴板内容不是有效的配置，请先在旧设备点「导出配置」');
      }
    }, () => toast('读取剪贴板失败，请检查浏览器权限'));
  });
  $('#refreshWishBtn').addEventListener('click', () => { loadWishlist(); toast('已刷新'); });

  // 本地存储面板
  $('#pickDirBtn').addEventListener('click', async () => {
    try {
      const name = await dlstore.pickCustomDir();
      toast(`已选择文件夹「${name}」，之后的下载会保存到这里`, 3500);
    } catch (e) {
      if (e && e.name !== 'AbortError') toast('选择文件夹失败：' + (e.message || e));
    }
    renderStoragePanel();
  });
  $('#useOpfsBtn').addEventListener('click', () => {
    dlstore.useOpfs();
    toast('已切回浏览器内置存储（原文件夹里的文件不会被删除）');
    renderStoragePanel();
  });
  $('#clearAllLocalBtn').addEventListener('click', async function () {
    if (this.dataset.armed !== '1') {
      this.dataset.armed = '1';
      this.textContent = '⚠️ 确认清空全部本地漫画？';
      setTimeout(() => { this.dataset.armed = ''; this.textContent = '🗑 清空全部本地漫画'; }, 4000);
      return;
    }
    this.disabled = true;
    const n = await dlstore.clearAllLocal();
    this.disabled = false;
    this.dataset.armed = '';
    this.textContent = '🗑 清空全部本地漫画';
    toast(`已清空 ${n} 本的本地包`);
    renderStoragePanel();
  });
  renderStoragePanel();

  loadWishlist();
}
