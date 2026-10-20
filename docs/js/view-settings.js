/* 「设置」分页：仓库/Token/首次检查清单/愿望清单 */
import { $, toast, esc, relTime, store } from './util.js';
import { detectRepo } from './github.js';

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

export function initSettingsView() {
  $('#repoInput').value = detectRepo();
  $('#tokenInput').value = store.get('token', '');
  $('#saveSettingsBtn').addEventListener('click', () => {
    store.set('repo', $('#repoInput').value.trim());
    store.set('token', $('#tokenInput').value.trim());
    toast('设置已保存（仅存储在本机浏览器）');
  });
  $('#makeTokenBtn').addEventListener('click', () => {
    window.open('https://github.com/settings/tokens/new?scopes=public_repo&description=' +
      encodeURIComponent('云书架一键提交'), '_blank');
    toast('在打开的页面拉到底点 Generate token，复制 ghp_ 字符串回来粘贴', 4500);
  });
  $('#refreshWishBtn').addEventListener('click', () => { loadWishlist(); toast('已刷新'); });
  loadWishlist();
}
