/* 「找本子」分页：每站一张卡片，点击跳转，长按复制站点地址 */
import { $, toast, esc } from './util.js';

const SITE_DESC = {
  jmcomic: '禁漫天堂，纯数字车号',
  mangadex: '多语言漫画社区，官方 API 最稳定',
  nhentai: '同人志画廊，整本单章（需代理）',
  manhuagui: '看漫画，热门连载日漫（需代理）',
  mkzhan: '漫客栈，国漫连载',
  telegraph: 'Telegra 即时阅读文章，整篇单章（需代理）',
  wnacg: '紳士漫畫，整本单章（需代理）',
  ehentai: 'E-Hentai 公开画廊（需代理）',
  bika: '无网页版，ID 需从 App 分享链接获取',
};

function logoText(name) {
  return name.replace(/[^A-Za-z一-鿿]/g, '').slice(0, 2) || name.slice(0, 2);
}

function copyText(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    return navigator.clipboard.writeText(text).then(() => true, () => false);
  }
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.cssText = 'position:fixed;opacity:0';
  document.body.appendChild(ta);
  ta.select();
  let ok = false;
  try { ok = document.execCommand('copy'); } catch (e) { /* noop */ }
  ta.remove();
  return Promise.resolve(ok);
}

/* 长按（500ms）复制地址；普通点击打开站点 */
function bindPress(el, url, name) {
  let timer = null, longPressed = false;
  const start = () => {
    longPressed = false;
    timer = setTimeout(() => {
      longPressed = true;
      copyText(url).then(ok => toast(ok ? `已复制 ${name} 地址，可粘贴到其他浏览器打开` : '复制失败：' + url, 3000));
      if (navigator.vibrate) navigator.vibrate(30);
    }, 500);
  };
  const cancel = () => { clearTimeout(timer); timer = null; };
  el.addEventListener('touchstart', start, { passive: true });
  el.addEventListener('touchend', cancel);
  el.addEventListener('touchmove', cancel);
  el.addEventListener('mousedown', start);
  el.addEventListener('mouseup', cancel);
  el.addEventListener('mouseleave', cancel);
  el.addEventListener('contextmenu', e => e.preventDefault());
  el.addEventListener('click', () => {
    if (longPressed) return; // 长按已复制，不再跳转
    window.open(url, '_blank');
  });
}

export function initFindView() {
  const box = $('#siteGrid');
  const sites = window.SITES || [];
  box.innerHTML = sites.map(s => `
    <div class="sitecell">
      <div class="sitecard" style="--sc:${esc(s.color)}" data-home="${esc(s.home)}">
        <div class="slogo">${esc(logoText(s.name))}</div>
        <div class="sinfo">
          <b>${esc(s.name)}</b>
          <span>${esc(SITE_DESC[s.key] || s.hint)}</span>
        </div>
        <div class="go">›</div>
      </div>
      ${(s.mirrors && s.mirrors.length)
        ? `<div class="mirrors">${s.mirrors.map(m =>
            `<a href="${esc(m)}" target="_blank" rel="noopener">${esc(m.replace(/^https?:\/\//, ''))}</a>`).join('')}</div>`
        : ''}
    </div>`).join('');
  box.querySelectorAll('.sitecard').forEach(el => {
    const name = el.querySelector('b').textContent;
    bindPress(el, el.dataset.home, name);
  });
}
