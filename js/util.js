/* 基础工具 */
export const $ = (s, el) => (el || document).querySelector(s);
export const $$ = (s, el) => Array.from((el || document).querySelectorAll(s));

let toastTimer = null;
export function toast(msg, ms) {
  const t = $('#toast');
  if (!t) return;
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), ms || 2200);
}

export function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

export function relTime(iso) {
  if (!iso) return '';
  const d = (Date.now() - new Date(iso).getTime()) / 1000;
  if (d < 60) return '刚刚';
  if (d < 3600) return Math.floor(d / 60) + '分钟前';
  if (d < 86400) return Math.floor(d / 3600) + '小时前';
  if (d < 86400 * 30) return Math.floor(d / 86400) + '天前';
  return iso.slice(0, 10);
}

export function fmtSize(b) {
  if (!b) return '';
  return b > 1048576 ? (b / 1048576).toFixed(1) + 'MB' : (b / 1024).toFixed(0) + 'KB';
}

export const store = {
  get(k, d) {
    try {
      const v = localStorage.getItem('jm_' + k);
      return v == null ? d : JSON.parse(v);
    } catch (e) { return d; }
  },
  set(k, v) {
    try { localStorage.setItem('jm_' + k, JSON.stringify(v)); } catch (e) { /* 满/隐私模式 */ }
  },
};

const PREFIX_TO_SITE = {
  md: 'mangadex', nh: 'nhentai', mg: 'manhuagui', dz: 'dmzj',
  wn: 'wnacg', eh: 'ehentai', bk: 'bika', cm: 'copymanga', gx: 'generic',
};

export function siteOf(id) {
  const m = /^([a-z]+)-/.exec(String(id));
  return m ? (PREFIX_TO_SITE[m[1]] || m[1]) : 'jmcomic';
}

const FALLBACK_COLORS = { jmcomic: '#ff5c8a' };
export function siteMeta(siteKey) {
  const s = (window.SITES || []).find(x => x.key === siteKey);
  return s || { key: siteKey, name: siteKey, color: FALLBACK_COLORS[siteKey] || '#6b7280', prefix: '' };
}

export function fmtId(id) {
  return /^\d+$/.test(String(id)) ? 'JM' + id : String(id);
}
