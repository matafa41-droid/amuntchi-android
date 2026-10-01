/* AMUNTCHI Android — base de données locale (hors ligne) et synchronisation Cloud.
 *
 * Les données sont gardées en mémoire et enregistrées dans IndexedDB (stockage
 * permanent du téléphone). Chaque modification est notée « à envoyer » ; la
 * synchronisation envoie ces modifications à Supabase et récupère celles du PC
 * et des autres téléphones. Mêmes règles que cloud_sync.py côté PC.
 */
'use strict';

const CLOUD_TABLES = ['users', 'categories', 'units', 'conversions', 'locations', 'suppliers',
  'clients', 'products', 'photos', 'movements', 'purchases', 'purchase_lines',
  'sales', 'sale_lines', 'invoices', 'credits', 'installments', 'payments',
  'expenses', 'transfers', 'transfer_lines', 'inventories', 'inventory_lines', 'audit'];
const UNIQUE_COLS = { products: 'code', categories: 'name', units: 'name', locations: 'name', users: 'username', invoices: 'number' };
const PHONE_BAND = 1000000;
const OVERLAP = 200;
const EPOCH = '1970-01-01T00:00:00.000Z';

/* ----------------------------- IndexedDB ----------------------------- */
const IDB = {
  db: null,
  open() {
    return new Promise((res, rej) => {
      const r = indexedDB.open('amuntchi', 1);
      r.onupgradeneeded = () => {
        const d = r.result;
        d.createObjectStore('rows', { keyPath: 'k' });
        d.createObjectStore('pending', { keyPath: 'k' });
        d.createObjectStore('meta', { keyPath: 'key' });
      };
      r.onsuccess = () => { IDB.db = r.result; res(); };
      r.onerror = () => rej(r.error);
    });
  },
  getAll(store) {
    return new Promise((res, rej) => {
      const t = IDB.db.transaction(store, 'readonly').objectStore(store).getAll();
      t.onsuccess = () => res(t.result); t.onerror = () => rej(t.error);
    });
  },
  /* ops : [[store, 'put'|'del', value|key], ...] dans UNE transaction */
  write(ops) {
    if (!ops.length) return Promise.resolve();
    const stores = [...new Set(ops.map(o => o[0]))];
    return new Promise((res, rej) => {
      const tx = IDB.db.transaction(stores, 'readwrite');
      for (const [s, kind, v] of ops) { const os = tx.objectStore(s); kind === 'put' ? os.put(v) : os.delete(v); }
      tx.oncomplete = () => res(); tx.onerror = () => rej(tx.error); tx.onabort = () => rej(tx.error);
    });
  },
  clearAll() {
    return new Promise((res, rej) => {
      const tx = IDB.db.transaction(['rows', 'pending', 'meta'], 'readwrite');
      ['rows', 'pending', 'meta'].forEach(s => tx.objectStore(s).clear());
      tx.oncomplete = () => res(); tx.onerror = () => rej(tx.error);
    });
  },
};

/* --------------------------- Données en mémoire --------------------------- */
const DB = {
  t: {},              // table -> Map(id -> ligne)
  known: new Map(),   // 'table|id' -> horodatage de la version connue
  pending: new Map(), // 'table|id' -> {tbl,id,ts}  (modifications à envoyer)
  meta: {},           // réglages propres à ce téléphone
  stocks: new Map(),  // 'pid|lid' -> quantité (recalculée à partir des mouvements)
  _queue: [],         // écritures IndexedDB en attente
  _flushing: null,
  _lastTs: '',
  listeners: [],

  async load() {
    await IDB.open();
    CLOUD_TABLES.forEach(t => DB.t[t] = new Map());
    for (const r of await IDB.getAll('rows')) {
      if (!DB.t[r.tbl]) DB.t[r.tbl] = new Map();
      DB.t[r.tbl].set(r.id, r.data);
      if (r.ts) DB.known.set(r.k, r.ts);
    }
    for (const p of await IDB.getAll('pending')) DB.pending.set(p.k, p);
    for (const m of await IDB.getAll('meta')) DB.meta[m.key] = m.value;
    DB.recomputeStocks();
  },

  /* ---- persistance ---- */
  _push(op) {
    DB._queue.push(op);
    if (!DB._flushing) DB._flushing = Promise.resolve().then(DB._flush);
  },
  async _flush() {
    while (DB._queue.length) {
      const ops = DB._queue.splice(0, 500);
      try { await IDB.write(ops); } catch (e) { console.error('IDB', e); }
    }
    DB._flushing = null;
  },
  async flush() { while (DB._flushing) await DB._flushing; },
  _saveRow(tbl, id) {
    const k = tbl + '|' + id, data = DB.t[tbl].get(id);
    if (data === undefined) DB._push(['rows', 'del', k]);
    else DB._push(['rows', 'put', { k, tbl, id, data, ts: DB.known.get(k) || '' }]);
  },
  _nextTs() {
    let ts = tsUTC();
    if (ts <= DB._lastTs) { const d = new Date(Date.parse(DB._lastTs) + 1); ts = d.toISOString(); }
    DB._lastTs = ts; return ts;
  },
  _mark(tbl, id) {
    const k = tbl + '|' + id, p = { k, tbl, id, ts: DB._nextTs() };
    DB.pending.set(k, p); DB._push(['pending', 'put', p]);
    DB.listeners.forEach(f => { try { f('local'); } catch (e) {} });
  },
  setMeta(key, value) { DB.meta[key] = value; DB._push(['meta', 'put', { key, value }]); },
  getMeta(key, def = '') { return DB.meta[key] ?? def; },

  /* ---- lecture ---- */
  all(tbl) { return [...(DB.t[tbl] || new Map()).values()]; },
  get(tbl, id) { return (DB.t[tbl] || new Map()).get(Number(id)); },
  find(tbl, fn) { for (const r of DB.t[tbl].values()) if (fn(r)) return r; return null; },
  filter(tbl, fn) { return DB.all(tbl).filter(fn); },

  /* ---- écriture (notée « à envoyer ») ---- */
  deviceNo() { return parseInt(DB.getMeta('device_no', '1'), 10) || 1; },
  nextId(tbl) {
    const n = DB.deviceNo(), hi = -(n * PHONE_BAND + 1), lo = -(n * PHONE_BAND + PHONE_BAND - 1);
    let next = Number(DB.getMeta('next_' + tbl, hi));
    for (const id of DB.t[tbl].keys()) if (id <= next && id >= lo) next = id - 1;
    if (next < lo) throw new Error('Plage d’identifiants épuisée pour ' + tbl);
    DB.setMeta('next_' + tbl, next - 1);
    return next;
  },
  insert(tbl, row) {
    const id = DB.nextId(tbl);
    const data = { ...row, id };
    DB.t[tbl].set(id, data); DB._saveRow(tbl, id); DB._mark(tbl, id);
    if (tbl === 'movements') DB._stockAdd(data);
    return id;
  },
  /* Écriture avec un identifiant imposé (photo d'un produit : même id que le produit). */
  put(tbl, id, row) {
    id = Number(id);
    DB.t[tbl].set(id, { ...row, id }); DB._saveRow(tbl, id); DB._mark(tbl, id);
  },
  update(tbl, id, patch) {
    id = Number(id);
    const cur = DB.t[tbl].get(id);
    if (!cur) throw new Error('Enregistrement introuvable.');
    DB.t[tbl].set(id, { ...cur, ...patch, id }); DB._saveRow(tbl, id); DB._mark(tbl, id);
  },
  remove(tbl, id) {
    id = Number(id);
    if (!DB.t[tbl].has(id)) return;
    DB.t[tbl].delete(id); DB._saveRow(tbl, id); DB._mark(tbl, id);
    if (tbl === 'movements') DB.recomputeStocks();
  },
  /* Mise à jour d'une valeur recalculée : enregistrée mais pas envoyée. */
  _quiet(tbl, id, patch) {
    const cur = DB.t[tbl].get(id); if (!cur) return;
    DB.t[tbl].set(id, { ...cur, ...patch }); DB._saveRow(tbl, id);
  },

  /* ---- stock ---- */
  _stockAdd(m) {
    if (m.product_id == null || m.location_id == null) return;
    const k = m.product_id + '|' + m.location_id;
    DB.stocks.set(k, (DB.stocks.get(k) || 0) + Number(m.qty || 0));
  },
  recomputeStocks() { DB.stocks = new Map(); for (const m of DB.t.movements.values()) DB._stockAdd(m); },
  stock(pid, lid) { return Math.round((DB.stocks.get(pid + '|' + lid) || 0) * 1e6) / 1e6; },
  adjust(pid, lid, delta, type, user, ref = '', note = '') {
    const nv = DB.stock(pid, lid) + delta;
    if (nv < -1e-9) throw new Error('Stock insuffisant');
    return DB.insert('movements', { product_id: pid, location_id: lid, type, qty: delta, user_id: user, date: nowStr(), ref: String(ref), note });
  },
  locationId(name) {
    const r = DB.find('locations', l => String(l.name).toLowerCase() === name.toLowerCase());
    return r ? r.id : null;
  },
  magasinId() { return DB.locationId(MAGASIN); },
  boutiqueId() { return DB.locationId(BOUTIQUE); },

  /* ---- crédits : avance, solde et échéances recalculés à partir des paiements ---- */
  recomputeCredit(cid, send = false) {
    const cr = DB.get('credits', cid); if (!cr) return;
    const sale = DB.get('sales', cr.sale_id);
    const paidSum = DB.all('payments').filter(p => p.credit_id === cr.id).reduce((a, p) => a + Number(p.amount || 0), 0);
    const first = sale ? Number(sale.paid || 0) : Math.max(Number(cr.advance || 0) - paidSum, 0);
    const advance = Math.round((first + paidSum) * 100) / 100;
    const balance = Math.round((Number(cr.total || 0) - advance) * 100) / 100;
    const upd = send ? (t, i, p) => DB.update(t, i, p) : (t, i, p) => DB._quiet(t, i, p);
    if (Math.abs(advance - Number(cr.advance || 0)) > 1e-6 || Math.abs(balance - Number(cr.balance || 0)) > 1e-6) upd('credits', cr.id, { advance, balance });
    let reste = paidSum;
    for (const it of DB.all('installments').filter(i => i.credit_id === cr.id).sort((a, b) => a.num - b.num)) {
      const amount = Number(it.amount || 0); let st;
      if (reste + 1e-6 >= amount) { st = 'Payé'; reste -= amount; } else { st = 'En attente'; reste = 0; }
      if (it.status !== st) upd('installments', it.id, { status: st });
    }
  },
  recomputeAllCredits() { for (const id of DB.t.credits.keys()) DB.recomputeCredit(id, false); },

  /* ---- réception des données du Cloud ---- */
  applyRemote(records) {
    let applied = 0;
    for (const rec of records) {
      const tbl = rec.tbl; if (!CLOUD_TABLES.includes(tbl)) continue;
      const id = Number(rec.id), k = tbl + '|' + id, ts = rec.changed_at || EPOCH;
      const pend = DB.pending.get(k);
      if (pend && pend.ts > ts) continue;
      const known = DB.known.get(k);
      if (known && known >= ts && !pend) continue;
      if (rec.deleted) DB.t[tbl].delete(id);
      else {
        let data = typeof rec.data === 'string' ? JSON.parse(rec.data) : { ...(rec.data || {}) };
        data.id = id;
        const col = UNIQUE_COLS[tbl];
        if (col && data[col] != null) {
          const clash = DB.find(tbl, r => r.id !== id && String(r[col]).toLowerCase() === String(data[col]).toLowerCase());
          if (clash) data[col] = `${data[col]}~${Math.abs(id)}`;
        }
        DB.t[tbl].set(id, data);
      }
      DB.known.set(k, ts);
      if (pend) { DB.pending.delete(k); DB._push(['pending', 'del', k]); }
      DB._saveRow(tbl, id);
      applied++;
    }
    if (applied) { DB.recomputeStocks(); DB.recomputeAllCredits(); }
    return applied;
  },

  pendingCount() { return DB.pending.size; },
  hasData() { return DB.t.locations.size > 0 && DB.t.users.size > 0; },
  async resetLocal() { await DB.flush(); await IDB.clearAll(); },
};

/* ------------------------------ Supabase ------------------------------ */
class CloudError extends Error {}
const Cloud = {
  busy: false,
  lastError: '',
  lastOk: '',
  token: null,
  cfg() {
    return {
      url: String(DB.getMeta('supa_url', '')).trim().replace(/\/+$/, ''),
      key: String(DB.getMeta('supa_key', '')).trim(),
      email: String(DB.getMeta('supa_email', '')).trim(),
      password: String(DB.getMeta('supa_password', '')),
    };
  },
  configured() { const c = Cloud.cfg(); return !!(c.url && c.key && c.email && c.password); },
  async req(method, path, body, extra = {}, auth = true) {
    const c = Cloud.cfg();
    const h = { apikey: c.key, 'Content-Type': 'application/json', ...extra };
    if (auth && Cloud.token) h.Authorization = 'Bearer ' + Cloud.token;
    let resp;
    const ctl = new AbortController(); const timer = setTimeout(() => ctl.abort(), 30000);
    try {
      resp = await fetch(c.url + path, { method, headers: h, body: body === undefined ? undefined : JSON.stringify(body), signal: ctl.signal });
    } catch (e) {
      throw new CloudError('Pas de connexion Internet ou adresse Cloud injoignable.');
    } finally { clearTimeout(timer); }
    const txt = await resp.text();
    if (!resp.ok) {
      if (path.includes('grant_type') && (resp.status === 400 || resp.status === 401)) throw new CloudError('E-mail ou mot de passe du compte Cloud incorrect.');
      throw new CloudError(`Erreur Cloud ${resp.status} : ${txt.slice(0, 200)}`);
    }
    return txt ? JSON.parse(txt) : null;
  },
  async login() {
    const c = Cloud.cfg();
    if (!Cloud.configured()) throw new CloudError('Paramètres Cloud incomplets.');
    const r = await Cloud.req('POST', '/auth/v1/token?grant_type=password', { email: c.email, password: c.password }, {}, false);
    Cloud.token = r && r.access_token;
    if (!Cloud.token) throw new CloudError('Connexion au Cloud refusée.');
  },
  async fetchSince(since) {
    const out = []; let last = since;
    for (;;) {
      const rows = await Cloud.req('GET', `/rest/v1/amuntchi_records?select=*&seq=gt.${last}&order=seq.asc&limit=1000`) || [];
      out.push(...rows);
      if (rows.length < 1000) return out;
      last = rows[rows.length - 1].seq;
    }
  },
  async push(records) {
    for (let i = 0; i < records.length; i += 400) {
      await Cloud.req('POST', '/rest/v1/amuntchi_records?on_conflict=tbl,id', records.slice(i, i + 400),
        { Prefer: 'resolution=merge-duplicates,return=minimal' });
    }
  },
  /* Cycle complet : réception puis envoi. Retourne {received, sent}. */
  async sync() {
    if (Cloud.busy) return null;
    Cloud.busy = true; Cloud.notify();
    try {
      await Cloud.login();
      const lastSeq = Number(DB.getMeta('last_seq', 0)) || 0;
      const remote = await Cloud.fetchSince(Math.max(0, lastSeq - OVERLAP));
      const received = DB.applyRemote(remote);
      const maxSeq = remote.reduce((m, r) => Math.max(m, Number(r.seq) || 0), lastSeq);
      DB.setMeta('last_seq', maxSeq);
      const device = DB.getMeta('device_name', 'TEL-' + DB.deviceNo());
      const snap = [...DB.pending.values()];
      const records = snap.map(p => {
        const data = DB.get(p.tbl, p.id);
        return { tbl: p.tbl, id: p.id, data: data ?? null, deleted: data === undefined, device, changed_at: p.ts };
      });
      if (records.length) await Cloud.push(records);
      for (const p of snap) {
        const cur = DB.pending.get(p.k);
        if (cur && cur.ts === p.ts) { DB.pending.delete(p.k); DB._push(['pending', 'del', p.k]); DB.known.set(p.k, p.ts); DB._saveRow(p.tbl, p.id); }
      }
      await DB.flush();
      Cloud.lastOk = nowStr(); Cloud.lastError = '';
      DB.setMeta('last_sync', Cloud.lastOk);
      return { received, sent: records.length };
    } catch (e) {
      Cloud.lastError = e.message || String(e);
      throw e;
    } finally {
      Cloud.busy = false; Cloud.notify();
    }
  },
  notify() { DB.listeners.forEach(f => { try { f('cloud'); } catch (e) {} }); },
};
