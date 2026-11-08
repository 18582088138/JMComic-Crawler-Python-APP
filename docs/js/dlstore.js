/* 本地下载与存储管理（V2.0 zip-only 阅读模型）
   漫画内容只存在于 data/<site> 分支的 zip/分卷里，首次阅读自动经
   raw.githubusercontent.com 下载到本机（OPFS 或自定义文件夹），JSZip 直读。
   - 断点续传：raw 支持 Range(206)，中断保留半包，重试从断点继续
   - 双存储：OPFS（全平台默认，浏览器私有目录） / 自定义文件夹（桌面 Chromium）
   - 注册表 jm_dl_<id> 记录已下载（体积/文件清单），书架角标与存储面板用
*/
import { store, siteOf, toast } from './util.js';
import { detectRepo } from './github.js';

/* ---------- 文件清单（manifest 或书架条目皆可） ---------- */

export function bookFiles(o) {
  if (!o) return [];
  if (Array.isArray(o.parts) && o.parts.length) {
    return o.parts.map(p => ({ name: p.file, bytes: p.bytes || 0 }));
  }
  if (o.zip) return [{ name: o.zip, bytes: o.sizeBytes || 0 }];
  if (o.zipName) return [{ name: o.zipName, bytes: o.sizeBytes || 0 }];
  if (o.zipParts > 1) {  // 书架条目只有分卷数：文件名按约定推导
    const base = (o.zipName || (o.id + '.zip')).replace(/\.zip$/, '');
    return Array.from({ length: o.zipParts }, (_, i) => ({
      name: `${base}.part${String(i + 1).padStart(2, '0')}.zip`, bytes: 0,
    }));
  }
  return [];
}

export function rawUrls(repo, o, fileName) {
  const site = o.site || siteOf(o.id);
  const p = `data/${site}/comics/${encodeURIComponent(o.id)}/${encodeURIComponent(fileName)}`;
  return [
    `https://raw.githubusercontent.com/${repo}/${p}`,
    `https://github.com/${repo}/raw/${p}`,
    // 开发/mock 兜底：本地 http.server 调试或迁移过渡期 gh-pages 上的旧 zip
    `comics/${encodeURIComponent(o.id)}/${encodeURIComponent(fileName)}`,
  ];
}

/* ---------- 注册表（localStorage jm_dl_<id>） ---------- */

const regKey = id => 'dl_' + id;

export function getReg(id) {
  return store.get(regKey(id), null);
}

export function clearReg(id) {
  try { localStorage.removeItem('jm_' + regKey(id)); } catch (e) { /* noop */ }
}

export function listLocal() {
  const out = [];
  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i);
    if (k && k.indexOf('jm_dl_') === 0) {
      try { out.push(JSON.parse(localStorage.getItem(k))); } catch (e) { /* noop */ }
    }
  }
  return out.sort((a, b) => (b.ts || 0) - (a.ts || 0));
}

/* ---------- IndexedDB（存自定义文件夹句柄） ---------- */

const IDB_NAME = 'cloudshelf';
const IDB_STORE = 'kv';
const DIR_KEY = 'download_dir';

function idbOpen() {
  return new Promise((res, rej) => {
    const r = indexedDB.open(IDB_NAME, 1);
    r.onupgradeneeded = () => r.result.createObjectStore(IDB_STORE);
    r.onsuccess = () => res(r.result);
    r.onerror = () => rej(r.error);
  });
}

async function idbGet(key) {
  try {
    const db = await idbOpen();
    return new Promise((res, rej) => {
      const t = db.transaction(IDB_STORE).objectStore(IDB_STORE).get(key);
      t.onsuccess = () => res(t.result || null);
      t.onerror = () => rej(t.error);
    });
  } catch (e) { return null; }
}

async function idbSet(key, val) {
  const db = await idbOpen();
  return new Promise((res, rej) => {
    const t = db.transaction(IDB_STORE, 'readwrite').objectStore(IDB_STORE).put(val, key);
    t.onsuccess = () => res();
    t.onerror = () => rej(t.error);
  });
}

/* ---------- 存储后端 ---------- */

async function opfsZips() {
  const root = await navigator.storage.getDirectory();
  return root.getDirectoryHandle('zips', { create: true });
}

export function fsaSupported() {
  return typeof window.showDirectoryPicker === 'function';
}

export async function pickCustomDir() {
  if (!fsaSupported()) throw new Error('当前浏览器不支持自定义文件夹（仅桌面 Chrome/Edge），已用浏览器内置存储');
  const h = await window.showDirectoryPicker({ mode: 'readwrite' });
  await idbSet(DIR_KEY, h);
  store.set('dl_store', 'fsa');
  return h.name;
}

export function useOpfs() {
  store.set('dl_store', 'opfs');
}

export async function customDirName() {
  const h = await idbGet(DIR_KEY);
  return h ? h.name : '';
}

export function storeMode() {
  return store.get('dl_store', 'opfs') === 'fsa' && fsaSupported() ? 'fsa' : 'opfs';
}

async function currentDir() {
  if (storeMode() === 'fsa') {
    const h = await idbGet(DIR_KEY);
    if (h) return { mode: 'fsa', dir: h };
  }
  return { mode: 'opfs', dir: await opfsZips() };
}

export async function fsaGranted() {
  if (storeMode() !== 'fsa') return true;
  const h = await idbGet(DIR_KEY);
  if (!h) return false;
  try { return await h.queryPermission({ mode: 'readwrite' }) === 'granted'; }
  catch (e) { return false; }
}

/** 授权本地文件夹（必须在点击等用户手势里调用） */
export async function grantFsAccess() {
  if (storeMode() !== 'fsa') return true;
  const h = await idbGet(DIR_KEY);
  if (!h) return false;
  try { return await h.requestPermission({ mode: 'readwrite' }) === 'granted'; }
  catch (e) { return false; }
}

/** 检查本子是否已下载且文件齐全：'ready' | 'grant'（需授权）| 'none' */
export async function checkFiles(m) {
  const reg = getReg(m.id);
  if (!reg) return 'none';
  try {
    const { mode, dir } = await currentDir();
    if (mode === 'fsa') {
      if (await dir.queryPermission({ mode: 'readwrite' }) !== 'granted') return 'grant';
    }
    for (const f of bookFiles(m)) {
      await dir.getFileHandle(f.name);  // 缺文件会抛 NotFound → 'none'
    }
    return 'ready';
  } catch (e) {
    return 'none';
  }
}

export async function getFileBlob(id, fileName) {
  const { dir } = await currentDir();
  const fh = await dir.getFileHandle(fileName);
  return (await fh.getFile());
}

/* ---------- 下载（进度 + 断点续传） ---------- */

async function downloadOne(dir, m, fileName, repo, cb) {
  // 断点：已存在的半包
  let existing = 0;
  try {
    const fh = await dir.getFileHandle(fileName);
    existing = (await fh.getFile()).size;
  } catch (e) { existing = 0; }

  let lastErr = null;
  for (const base of rawUrls(repo, m, fileName)) {
    const ac = new AbortController();
    let stallTimer = null;
    const bumpStall = () => {
      clearTimeout(stallTimer);
      stallTimer = setTimeout(() => ac.abort(), 20000);   // 20 秒无数据即判定卡死
    };
    const connTimer = setTimeout(() => ac.abort(), 45000);  // 连接超时 45s
    let writable = null;
    try {
      let res, offset = 0;
      if (existing > 0) {
        res = await fetch(base, { signal: ac.signal, headers: { Range: 'bytes=' + existing + '-' } });
        if (res.status === 206) offset = existing;
        else res = await fetch(base, { signal: ac.signal });   // 不支持 Range / 文件已变：整包重来
      } else {
        res = await fetch(base, { signal: ac.signal });
      }
      clearTimeout(connTimer);
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const cr = res.headers.get('content-range');
      const total = offset + Number(
        (cr && cr.split('/').pop()) || res.headers.get('content-length') || 0);
      if (total && offset && total === existing) return { name: fileName, size: existing };  // 上次已完成

      const fh = await dir.getFileHandle(fileName, { create: true });
      writable = await fh.createWritable({ keepExistingData: offset > 0 });
      if (offset > 0) await writable.seek(offset);
      let received = offset;
      bumpStall();
      if (!res.body) {  // 老浏览器无流：一次性写入
        const buf = await res.arrayBuffer();
        await writable.write(buf);
        received += buf.byteLength;
      } else {
        const reader = res.body.getReader();
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          await writable.write(value);
          received += value.byteLength;
          bumpStall();
          if (cb) cb(received, total);
        }
      }
      clearTimeout(stallTimer);
      await writable.close();
      const finalSize = (await fh.getFile()).size;
      if (total && finalSize !== total) {
        throw new Error('下载不完整（' + Math.round(finalSize / 1048576) + '/'
          + Math.round(total / 1048576) + 'MB），断点已保留，点重试续传');
      }
      return { name: fileName, size: finalSize };
    } catch (e) {
      lastErr = (e && e.name === 'AbortError')
        ? new Error('网络连接超时（raw 被墙或代理未开）')
        : e;
      // 尽力把已写入的半包落盘，供下次断点续传
      if (writable) { try { await writable.close(); } catch (e3) { /* noop */ } }
      try {
        const fh = await dir.getFileHandle(fileName);
        existing = (await fh.getFile()).size;   // 留给下一轮当断点
      } catch (e2) { existing = 0; }
    } finally {
      clearTimeout(connTimer);
      clearTimeout(stallTimer);
    }
  }
  throw lastErr || new Error('下载失败（网络不通，请检查代理后重试）');
}

/**
 * 下载整本（zip/分卷全部）到本地存储。
 * onProgress({fileIndex, fileCount, fileName, received, total})
 * 返回注册表记录。必须在用户手势内调用（可能弹文件夹授权）。
 */
export async function downloadBook(m, onProgress) {
  const repo = detectRepo();
  if (!repo) throw new Error('未识别仓库，请到设置页检查');
  const files = bookFiles(m);
  if (!files.length) throw new Error('该本子没有可下载的压缩包');

  const { mode, dir } = await currentDir();
  if (mode === 'fsa') {
    if (await dir.queryPermission({ mode: 'readwrite' }) !== 'granted') {
      if (await dir.requestPermission({ mode: 'readwrite' }) !== 'granted') {
        throw new Error('未获得文件夹访问授权');
      }
    }
  }
  ensurePersist();

  const done = [];
  let bytesBefore = 0;
  for (let i = 0; i < files.length; i++) {
    const info = await downloadOne(dir, m, files[i].name, repo, (received, total) => {
      if (onProgress) onProgress({
        fileIndex: i + 1, fileCount: files.length, fileName: files[i].name,
        received: bytesBefore + received,
        total: bytesBefore + (total || (files[i].bytes || 0)),
      });
    });
    done.push(info);
    bytesBefore += info.size;
  }
  const reg = {
    id: m.id, ts: Date.now(), title: m.title || m.id,
    site: m.site || siteOf(m.id), store: mode,
    bytes: bytesBefore,
    files: done.map(d => ({ name: d.name, size: d.size })),
  };
  store.set(regKey(m.id), reg);
  return reg;
}

/* ---------- 删除 / 清理 ---------- */

export async function deleteLocal(id, m) {
  try {
    const { dir } = await currentDir();
    const o = m || getReg(id) || { id };
    for (const f of bookFiles(o)) {
      try { await dir.removeEntry(f.name); } catch (e) { /* 已不在 */ }
    }
  } catch (e) { /* 存储不可用（如未授权）：注册表照清 */ }
  clearReg(id);
}

export async function clearAllLocal() {
  const all = listLocal();
  for (const r of all) await deleteLocal(r.id, { id: r.id, parts: r.files && null });
  return all.length;
}

/* ---------- 存储面板 ---------- */

export async function estimate() {
  try {
    const e = await navigator.storage.estimate();
    return { usage: e.usage || 0, quota: e.quota || 0 };
  } catch (e) { return null; }
}

export async function persisted() {
  try { return await navigator.storage.persisted(); } catch (e) { return false; }
}

export function ensurePersist() {
  try {
    if (navigator.storage && navigator.storage.persist) navigator.storage.persist().catch(() => {});
  } catch (e) { /* noop */ }
}

/* ---------- 保存到系统下载（导航下载，绕过 CORS，含 Releases 时代兼容） ---------- */

export function saveToSystem(o) {
  const repo = detectRepo();
  if (!repo) { toast('未识别仓库，请到设置页检查'); return 0; }
  const files = bookFiles(o);
  if (!files.length) return 0;
  for (const f of files) {
    const a = document.createElement('a');
    a.href = rawUrls(repo, o, f.name)[0];
    a.download = f.name;
    document.body.appendChild(a);
    a.click();
    a.remove();
  }
  return files.length;
}
