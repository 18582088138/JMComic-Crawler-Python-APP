/* 「找本子」分页：每个已收录站点一张卡片，点击跳转站点主页 */
import { $, esc } from './util.js';

const SITE_DESC = {
  jmcomic: '禁漫天堂，纯数字车号',
  mangadex: '多语言漫画社区，官方 API 最稳定',
  nhentai: '同人志画廊，整本单章',
  manhuagui: '看漫画，热门连载日漫',
  dmzj: '动漫之家，国漫日漫连载',
  wnacg: '紳士漫畫，整本单章',
  ehentai: 'E-Hentai 公开画廊',
  bika: '哔咔漫画，需 App 漫画ID',
};

function logoText(name) {
  return name.replace(/[^A-Za-z一-鿿]/g, '').slice(0, 2) || name.slice(0, 2);
}

export function initFindView() {
  const box = $('#siteGrid');
  const sites = window.SITES || [];
  box.innerHTML = sites.map(s => `
    <div class="sitecard" style="--sc:${esc(s.color)}" data-home="${esc(s.home)}">
      <div class="slogo">${esc(logoText(s.name))}</div>
      <div class="sinfo">
        <b>${esc(s.name)}</b>
        <span>${esc(SITE_DESC[s.key] || s.hint)}</span>
      </div>
      <div class="go">›</div>
    </div>`).join('');
  box.querySelectorAll('.sitecard').forEach(el => {
    el.addEventListener('click', () => {
      window.open(el.dataset.home, '_blank');
    });
  });
}
