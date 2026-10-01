/* AMUNTCHI Android — composants d'interface (fenêtres, tableaux, sélecteurs, fichiers). */
'use strict';

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
function frag(html) { const t = document.createElement('template'); t.innerHTML = html.trim(); return t.content.firstElementChild; }

/* ---------- Fenêtres ---------- */
function openSheet({ title, body = '', buttons = [], full = false, cls = '', onClose }) {
  const m = frag(`<div class="modal"><div class="sheet ${full ? 'full' : ''} ${cls}">
    <div class="sh"><h3>${esc(title)}</h3><button class="x" aria-label="Fermer">✕</button></div>
    <div class="sb"></div>${buttons.length ? '<div class="sf"></div>' : ''}</div></div>`);
  const sb = $('.sb', m);
  if (typeof body === 'string') sb.innerHTML = body; else sb.appendChild(body);
  let closed = false;
  const api = {
    el: m, body: sb,
    close() {
      if (closed) return; closed = true;
      m.remove(); Sheets.list = Sheets.list.filter(x => x !== api);
      onClose && onClose();
    },
  };
  buttons.forEach(b => {
    const el = frag(`<button class="btn ${b.cls || ''}">${esc(b.label)}</button>`);
    el.onclick = async () => {
      if (el.disabled) return;
      el.disabled = true;
      try { await b.onClick(api); } finally { el.disabled = false; }
    };
    $('.sf', m).appendChild(el);
  });
  $('.x', m).onclick = () => api.close();
  m.addEventListener('click', e => { if (e.target === m && !full) api.close(); });
  document.body.appendChild(m);
  Sheets.list.push(api);
  return api;
}
const Sheets = {
  list: [],
  closeTop() { const a = this.list[this.list.length - 1]; if (a) { a.close(); return true; } return false; },
  closeAll() { [...this.list].reverse().forEach(a => a.close()); },
};

function alertBox(msg, title = 'AMUNTCHI') {
  return new Promise(res => {
    openSheet({ title, cls: 'dialog', body: `<div>${esc(msg)}</div>`, onClose: () => res(),
      buttons: [{ label: 'OK', cls: 'primary', onClick: a => a.close() }] });
  });
}
function errorBox(e, title = 'Erreur') { return alertBox(e && e.message ? e.message : String(e), title); }
function confirmBox(msg, title = 'Confirmation', yes = 'Oui', no = 'Non', danger = false) {
  return new Promise(res => {
    openSheet({
      title, cls: 'dialog', body: `<div>${esc(msg)}</div>`,
      buttons: [{ label: no, cls: 'grey', onClick: a => { res(false); a.close(); } },
                { label: yes, cls: danger ? 'red' : 'primary', onClick: a => { res(true); a.close(); } }],
      onClose: () => res(false),
    });
  });
}
function promptBox(label, def = '', { title = 'Saisie', type = 'text', inputmode = '' } = {}) {
  return new Promise(res => {
    const s = openSheet({
      title, cls: 'dialog',
      body: `<label class="f">${esc(label)}</label><input class="i" type="${type}" ${inputmode ? `inputmode="${inputmode}"` : ''} value="${esc(def)}">`,
      buttons: [{ label: 'Annuler', cls: 'grey', onClick: a => { res(null); a.close(); } },
                { label: 'Valider', cls: 'primary', onClick: a => { res($('input', a.el).value); a.close(); } }],
      onClose: () => res(null),
    });
    setTimeout(() => $('input', s.el).focus(), 50);
  });
}
let toastTimer;
function toast(msg, ms = 2600) {
  let t = $('.toast'); if (!t) { t = frag('<div class="toast"></div>'); document.body.appendChild(t); }
  t.textContent = msg; t.style.display = 'block';
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.style.display = 'none', ms);
}

/* ---------- Blocs HTML ---------- */
function headHTML(title, sub = '') {
  const u = App.user ? `${esc(App.user.name)} — ${esc(App.user.role)}` : '';
  return `<div class="head"><h1>${esc(title)}</h1>${sub ? `<div class="sub">${esc(sub)}</div>` : ''}<div class="meta">${esc(frenchDate())}${u ? ' · ' + u : ''}</div></div>`;
}
function cardHTML(t, v, s, color = '#1e73e8', icon = '●') {
  return `<div class="card"><div class="ico" style="background:${color}">${esc(icon)}</div><div class="tx"><div class="t">${esc(t)}</div><div class="v">${esc(v)}</div><div class="s">${esc(s)}</div></div></div>`;
}
/* heads : [libellé | {l, num}] ; rows : tableaux de valeurs (texte échappé) ; keys : identifiants de ligne */
function tableHTML(heads, rows, { keys = null, empty = 'Aucune donnée.', wrapCols = [] } = {}) {
  if (!rows.length) return `<div class="empty">${esc(empty)}</div>`;
  const H = heads.map(h => typeof h === 'string' ? { l: h } : h);
  return `<div class="tw"><table class="t"><thead><tr>${H.map(h => `<th class="${h.num ? 'num' : ''}">${esc(h.l)}</th>`).join('')}</tr></thead><tbody>${
    rows.map((r, i) => `<tr ${keys ? `class="click" data-k="${esc(keys[i])}"` : ''}>${r.map((c, j) => {
      const raw = c && typeof c === 'object' && 'html' in c;
      return `<td class="${H[j] && H[j].num ? 'num ' : ''}${wrapCols.includes(j) ? 'wrap' : ''}">${raw ? c.html : esc(c)}</td>`;
    }).join('')}</tr>`).join('')}</tbody></table></div>`;
}
function onRows(root, fn) { $$('tr.click', root).forEach(tr => tr.onclick = () => fn(tr.dataset.k)); }
function selectHTML(id, options, selected = '', placeholder = '') {
  return `<select class="i" id="${id}">${placeholder !== '' ? `<option value="">${esc(placeholder)}</option>` : ''}${
    options.map(o => { const v = typeof o === 'object' ? o.v : o, l = typeof o === 'object' ? o.l : o;
      return `<option value="${esc(v)}" ${String(v) === String(selected) ? 'selected' : ''}>${esc(l)}</option>`; }).join('')}</select>`;
}
/* Liste avec saisie libre (équivalent d'une liste déroulante modifiable) */
function comboHTML(id, options, value = '') {
  return `<input class="i" id="${id}" list="${id}_l" value="${esc(value)}" autocomplete="off"><datalist id="${id}_l">${options.map(o => `<option value="${esc(o)}">`).join('')}</datalist>`;
}

/* ---------- Sélecteur d'article avec recherche par famille ---------- */
function makePicker(host, { rows, label, sub, onPick, placeholder = 'Rechercher (ex. ampoule 20W)…', hint = 'Ex. « ampoule 20W » : seules les ampoules 20W sont proposées' }) {
  host.classList.add('picker');
  host.innerHTML = `<input class="i" type="search" placeholder="${esc(placeholder)}" autocomplete="off">
    <div class="note">${esc(hint)}</div><div class="res"></div><div class="sel" style="display:none"><span></span><button class="btn sm light">Changer</button></div>`;
  const inp = $('input', host), res = $('.res', host), note = $('.note', host), sel = $('.sel', host);
  let data = rows(), chosen = null, shown = [];
  const render = () => {
    const { rows: found, note: n } = smartSearch(data, inp.value);
    shown = found.slice(0, 60);
    note.textContent = inp.value.trim() ? n : hint;
    note.className = 'note' + (n.startsWith('Aucun') && inp.value.trim() ? ' bad' : inp.value.trim() ? ' good' : '');
    res.innerHTML = shown.length ? shown.map((r, i) => `<div class="it" data-i="${i}"><div class="a">${esc(label(r))}</div><div class="b">${esc(sub(r))}</div></div>`).join('')
      + (found.length > shown.length ? `<div class="it"><div class="b">… ${found.length - shown.length} autre(s) : précisez la recherche</div></div>` : '')
      : '<div class="it"><div class="b">Aucun article</div></div>';
    $$('.it[data-i]', res).forEach(d => d.onclick = () => pick(shown[+d.dataset.i]));
    return found;
  };
  const pick = r => {
    chosen = r; res.style.display = 'none'; sel.style.display = 'flex';
    $('span', sel).textContent = label(r) + ' — ' + sub(r);
    onPick && onPick(r);
  };
  inp.addEventListener('input', () => { if (chosen) { chosen = null; sel.style.display = 'none'; onPick && onPick(null); } res.style.display = ''; render(); });
  inp.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); const f = render(); if (f.length === 1) pick(f[0]); } });
  $('button', sel).onclick = () => { chosen = null; sel.style.display = 'none'; res.style.display = ''; onPick && onPick(null); inp.focus(); inp.select(); };
  render();
  return {
    get value() { return chosen; },
    reload() { data = rows(); chosen = null; sel.style.display = 'none'; res.style.display = ''; render(); },
    clear() { inp.value = ''; this.reload(); },
    focus() { inp.focus(); },
    input: inp,
  };
}

/* ---------- Fichiers : enregistrer / partager (PDF, CSV, sauvegarde) ---------- */
function bytesToBase64(bytes) {
  let s = ''; const CH = 0x8000;
  for (let i = 0; i < bytes.length; i += CH) s += String.fromCharCode.apply(null, bytes.subarray(i, i + CH));
  return btoa(s);
}
/* Module natif Capacitor (null dans un navigateur ordinaire) */
function capPlugin(name) {
  const C = window.Capacitor;
  if (!C || !C.isNativePlatform || !C.isNativePlatform()) return null;
  if (C.Plugins && C.Plugins[name]) return C.Plugins[name];
  return C.registerPlugin ? C.registerPlugin(name) : null;
}
async function saveFile(content, name, mime) {
  const bytes = typeof content === 'string' ? new TextEncoder().encode(content) : content;
  const FS = capPlugin('Filesystem'), SH = capPlugin('Share');
  if (FS && SH) {
    const r = await FS.writeFile({ path: name, data: bytesToBase64(bytes), directory: 'CACHE' });
    try { await SH.share({ title: name, files: [r.uri], dialogTitle: 'Ouvrir ou partager ' + name }); }
    catch (e) { if (!/cancel/i.test(String(e && e.message))) toast('Partage impossible : ' + (e.message || e)); }
    return;
  }
  const url = URL.createObjectURL(new Blob([bytes], { type: mime }));
  const a = document.createElement('a'); a.href = url; a.download = name; document.body.appendChild(a); a.click();
  setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 2000);
}
function readFileAsText(accept = '.csv,text/csv,text/plain') {
  return new Promise(res => {
    const inp = document.createElement('input'); inp.type = 'file'; inp.accept = accept;
    inp.onchange = () => { const f = inp.files[0]; if (!f) return res(null); const r = new FileReader(); r.onload = () => res(r.result); r.readAsText(f, 'utf-8'); };
    inp.click();
  });
}
/* Photo : appareil photo (capture) ou galerie ; retourne une miniature JPEG (data URL) */
function pickImage(capture) {
  return new Promise(res => {
    const inp = document.createElement('input'); inp.type = 'file'; inp.accept = 'image/*';
    if (capture) inp.setAttribute('capture', 'environment');
    inp.onchange = () => {
      const f = inp.files[0]; if (!f) return res(null);
      const url = URL.createObjectURL(f), img = new Image();
      img.onload = () => {
        const m = 480, r = Math.min(1, m / Math.max(img.width, img.height));
        const c = document.createElement('canvas'); c.width = Math.round(img.width * r); c.height = Math.round(img.height * r);
        c.getContext('2d').drawImage(img, 0, 0, c.width, c.height); URL.revokeObjectURL(url);
        res(c.toDataURL('image/jpeg', 0.72));
      };
      img.onerror = () => res(null); img.src = url;
    };
    inp.click();
  });
}

/* ---------- Graphiques (SVG) ---------- */
function lineChartSVG(vals, labels) {
  const W = 340, H = 190, L = 44, R = 10, T = 14, B = 28;
  const raw = Math.max(...vals, 0) / 4, mag = Math.pow(10, Math.floor(Math.log10(raw || 1)));
  const step = raw <= 0 ? 25000 : [1, 2, 2.5, 5, 10].map(m => m * mag).find(x => x >= raw), mx = step * 4;
  let s = `<svg class="chart" viewBox="0 0 ${W} ${H}">`;
  for (let i = 0; i < 5; i++) {
    const y = T + i * (H - B - T) / 4, v = step * (4 - i);
    s += `<line x1="${L}" x2="${W - R}" y1="${y}" y2="${y}" stroke="#e8eef5"/><text x="${L - 6}" y="${y + 3}" font-size="8.5" text-anchor="end" fill="#738397">${v >= 1e6 ? (v / 1e6).toLocaleString('fr') + 'M' : v >= 1000 ? (v / 1000).toLocaleString('fr') + 'k' : v}</text>`;
  }
  const pts = vals.map((v, i) => [L + i * (W - L - R) / (vals.length - 1), H - B - (v / mx) * (H - B - T - 8)]);
  s += `<polyline fill="none" stroke="#1769d3" stroke-width="3" stroke-linejoin="round" points="${pts.map(p => p.join(',')).join(' ')}"/>`;
  pts.forEach((p, i) => { s += `<circle cx="${p[0]}" cy="${p[1]}" r="4" fill="#1769d3" stroke="#fff"/><text x="${p[0]}" y="${H - 10}" font-size="9" text-anchor="middle" fill="#738397">${labels[i]}</text>`; });
  return s + '</svg>';
}
function donutSVG(rows) {
  const colors = ['#1769d3', '#18b65b', '#f5a400', '#ef3038', '#7c4dff', '#00a6a6'];
  if (!rows.length) return '<div class="empty">Aucune donnée : les quantités apparaîtront dès l’enregistrement des produits.</div>';
  const total = rows.reduce((a, r) => a + r.qty, 0); let a0 = -Math.PI / 2; const cx = 80, cy = 80, r = 70, ri = 38;
  let s = '<svg class="chart" viewBox="0 0 160 160" style="max-width:180px;margin:auto">';
  rows.forEach((row, i) => {
    const a1 = a0 + 2 * Math.PI * row.qty / total, big = a1 - a0 > Math.PI ? 1 : 0;
    const p = (a, rr) => `${cx + rr * Math.cos(a)},${cy + rr * Math.sin(a)}`;
    if (rows.length === 1) s += `<circle cx="${cx}" cy="${cy}" r="${(r + ri) / 2}" fill="none" stroke="${colors[0]}" stroke-width="${r - ri}"/>`;
    else s += `<path d="M${p(a0, r)} A${r},${r} 0 ${big} 1 ${p(a1, r)} L${p(a1, ri)} A${ri},${ri} 0 ${big} 0 ${p(a0, ri)} Z" fill="${colors[i % 6]}" stroke="#fff" stroke-width="2"/>`;
    a0 = a1;
  });
  s += `<text x="${cx}" y="${cy + 5}" text-anchor="middle" font-size="15" font-weight="700" fill="#173a5e">${q(total)}</text></svg>`;
  return s + `<div class="legend">${rows.map((r2, i) => `<div><i style="background:${colors[i % 6]}"></i>${esc(r2.name)} (${q(r2.qty)})</div>`).join('')}</div>`;
}
