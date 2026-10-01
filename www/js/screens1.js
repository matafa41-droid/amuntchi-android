/* AMUNTCHI Android — écrans (1/2) : tableau de bord, produits, stock, transferts, achats, caisse. */
'use strict';

/* ---------------- Aides de lecture ---------------- */
const byName = (a, b) => String(a.name ?? '').localeCompare(String(b.name ?? ''), 'fr', { sensitivity: 'base' });
const byDateDesc = (a, b) => String(b.date ?? '').localeCompare(String(a.date ?? '')) || (Math.abs(b.id) - Math.abs(a.id));
const nameOf = (tbl, id, def = '-') => { const r = id == null ? null : DB.get(tbl, id); return r ? r.name : def; };
const userName = id => nameOf('users', id);
const no = id => String(Math.abs(Number(id)));          // numéro affiché (les ids des téléphones sont négatifs)
function productView(p, lid) {
  return {
    ...p, category: nameOf('categories', p.category_id), stock_unit: nameOf('units', p.stock_unit_id),
    sale_unit: nameOf('units', p.sale_unit_id), qty: lid == null ? 0 : DB.stock(p.id, lid),
  };
}
function productsAt(lid, onlyPositive = true) {
  return DB.filter('products', p => Number(p.active ?? 1) === 1).map(p => productView(p, lid))
    .filter(p => !onlyPositive || p.qty > 0).sort(byName);
}
function photoOf(pid) { const r = DB.get('photos', pid); return r && r.data ? r.data : null; }
function main() { return $('#main'); }
function render(html) { main().innerHTML = html; window.scrollTo(0, 0); return main(); }

/* ======================= Tableau de bord ======================= */
Screens.dashboard = function () {
  const mid = DB.magasinId(), bid = DB.boutiqueId(), jour = today() + ' 00:00:00';
  const vj = DB.filter('sales', s => s.date >= jour);
  const aj = DB.filter('purchases', a => a.date >= jour);
  let qm = 0, qb = 0;
  for (const [k, v] of DB.stocks) { const lid = Number(k.split('|')[1]); if (lid === mid) qm += v; else if (lid === bid) qb += v; }
  const cr = DB.filter('credits', c => Number(c.balance) > 0);
  const vals = [], labels = [];
  for (let d = 6; d >= 0; d--) {
    const day = addDays(today(), -d), next = addDays(day, 1);
    vals.push(DB.filter('sales', s => s.date >= day + ' 00:00:00' && s.date < next + ' 00:00:00').reduce((a, s) => a + Number(s.total || 0), 0));
    labels.push(day.slice(8, 10) + '/' + day.slice(5, 7));
  }
  const cats = new Map();
  for (const p of DB.all('products')) {
    let t = 0; for (const l of DB.all('locations')) t += DB.stock(p.id, l.id);
    const n = nameOf('categories', p.category_id, 'Sans catégorie'); cats.set(n, (cats.get(n) || 0) + t);
  }
  const catRows = [...cats].map(([name, qty]) => ({ name, qty })).filter(r => r.qty > 0).sort((a, b) => b.qty - a.qty).slice(0, 6);
  const low = DB.filter('products', p => Number(p.active ?? 1) === 1).map(p => {
    const m = DB.stock(p.id, mid), b = DB.stock(p.id, bid);
    let tot = 0; for (const l of DB.all('locations')) tot += DB.stock(p.id, l.id);
    return { p, m, b, tot };
  }).filter(x => x.tot <= Number(x.p.min_stock || 0)).sort((a, b) => a.tot - b.tot);
  render(headHTML('Tableau de bord', 'Synthèse activité, stock magasin, boutique, caisse et crédits') +
    `<div class="cards">
      ${cardHTML('Ventes du jour', money(vj.reduce((a, s) => a + Number(s.total || 0), 0)), `${vj.length} vente(s) | encaissé : ${money(vj.reduce((a, s) => a + Number(s.paid || 0), 0))}`, '#18b65b', 'VTE')}
      ${cardHTML('Achats du jour', money(aj.reduce((a, s) => a + Number(s.total || 0), 0)), `${aj.length} réception(s) au magasin`, '#1e73e8', 'ACH')}
      ${cardHTML('Quantité en stock', q(qm + qb), `Magasin : ${q(qm)} | Boutique : ${q(qb)}`, '#f5a400', 'STK')}
      ${cardHTML('Crédits en cours', money(cr.reduce((a, c) => a + Number(c.balance || 0), 0)), `${cr.length} crédit(s) client ouvert(s)`, '#ef3038', 'CRD')}
    </div>
    <div class="panel"><h2>Évolution des ventes (7 derniers jours)</h2>${lineChartSVG(vals, labels)}</div>
    <div class="panel"><h2>Répartition du stock par catégorie</h2>${donutSVG(catRows)}</div>
    <div class="panel"><div class="bar"><h2 class="grow" style="margin:0">Alertes stock (${low.length} produit(s) sous le seuil minimum)</h2>
      ${App.allowed('stock') ? '<button class="btn sm light" id="goStock">Voir le stock magasin</button>' : ''}</div>
      ${tableHTML(['Référence', 'Produit', 'Catégorie', { l: 'Magasin', num: 1 }, { l: 'Boutique', num: 1 }, { l: 'Total', num: 1 }, { l: 'Seuil', num: 1 }, 'Unité'],
        low.slice(0, 40).map(x => [x.p.code, x.p.name, nameOf('categories', x.p.category_id), q(x.m), q(x.b), q(x.tot), q(x.p.min_stock), nameOf('units', x.p.sale_unit_id)]),
        { keys: low.slice(0, 40).map(x => x.p.id), empty: 'Aucun produit sous le seuil minimum.' })}</div>
    <div class="foot-info"><span>${esc(COMM.name)} | ${esc(COMM.address)} - Niger</span><span>${DB.pendingCount() ? DB.pendingCount() + ' modification(s) en attente d’envoi' : 'Données à jour sur ce téléphone'}</span></div>`);
  const g = $('#goStock'); if (g) g.onclick = () => App.go('stock');
  onRows(main(), k => productDetail(Number(k)));
};

/* ======================= Produits (boutique) / Stock (magasin) ======================= */
function productListScreen(kind) {
  const isShop = kind === 'products';
  const lid = isShop ? DB.boutiqueId() : DB.magasinId();
  const place = isShop ? 'boutique' : 'magasin';
  render((isShop ? headHTML('Produits (boutique)', 'Articles disponibles à la vente, transférés du magasin vers la boutique')
                 : headHTML('Stock (magasin)', 'Articles reçus par achat ou réception, en attente de transfert vers la boutique')) +
    `<div class="bar"><input class="i grow" id="q" type="search" placeholder="Rechercher (ex. ampoule 20W)…" value="${esc(App.memo[kind + '_q'] || '')}">
      <button class="btn primary" id="go">Rechercher</button></div>
    <label class="check"><input type="checkbox" id="all" ${App.memo[kind + '_all'] ? 'checked' : ''}>${isShop ? 'Afficher aussi les articles épuisés en boutique' : 'Afficher aussi les articles à zéro'}</label>
    <div class="bar">${isShop
      ? '<button class="btn green" id="new">+ Nouveau produit</button><button class="btn light" id="imp">Importer CSV</button>'
      : `${App.allowed('purchases') ? '<button class="btn green" id="rec">+ Réception / achat</button>' : ''}${App.allowed('transfers') ? '<button class="btn blue" id="trf">Transférer vers la boutique</button>' : ''}<button class="btn light" id="new">+ Nouveau produit</button>`}</div>
    <div class="info" id="info"></div><div id="zone"></div>`);
  const load = () => {
    App.memo[kind + '_q'] = $('#q').value; App.memo[kind + '_all'] = $('#all').checked;
    const base = productsAt(lid, !$('#all').checked);
    const { rows, note } = smartSearch(base, $('#q').value);
    const tot = rows.reduce((a, r) => a + r.qty, 0);
    const extra = isShop ? 'touchez une ligne pour voir la fiche complète'
      : 'valeur d’achat : ' + money(rows.reduce((a, r) => a + r.qty * Number(r.purchase_price || 0), 0));
    const info = $('#info');
    info.textContent = (note ? note + ' | ' : '') + `${rows.length} article(s) en ${place} | quantité totale : ${q(tot)} | ${extra}`;
    info.className = 'info' + (note.startsWith('Aucun') ? ' bad' : '');
    $('#zone').innerHTML = tableHTML(['Réf.', 'Désignation', 'Marque', 'Catégorie', { l: 'Qté ' + place, num: 1 }, 'Unité vente', { l: 'Prix vente', num: 1 }, { l: 'Seuil', num: 1 }],
      rows.map(r => [r.code, r.name, r.brand || '', r.category, q(r.qty), r.sale_unit, money(r.sale_price), q(r.min_stock)]),
      { keys: rows.map(r => r.id), wrapCols: [1], empty: $('#q').value.trim() ? note : (isShop ? 'Aucun produit en boutique. Utilisez l’onglet Transferts pour envoyer des articles du magasin vers la boutique.' : 'Aucun article au magasin. Enregistrez une réception (Achats / réceptions) pour alimenter le stock.') });
    onRows($('#zone'), k => productDetail(Number(k)));
  };
  $('#go').onclick = load; $('#q').addEventListener('keydown', e => { if (e.key === 'Enter') load(); });
  $('#q').addEventListener('input', () => { clearTimeout(App._qt); App._qt = setTimeout(load, 250); });
  $('#all').onchange = load;
  $('#new').onclick = () => productForm();
  if ($('#imp')) $('#imp').onclick = importProducts;
  if ($('#rec')) $('#rec').onclick = purchaseForm;
  if ($('#trf')) $('#trf').onclick = transferForm;
  load();
}
Screens.products = () => productListScreen('products');
Screens.stock = () => productListScreen('stock');

function productDetail(pid) {
  const p = DB.get('products', pid); if (!p) return;
  const v = productView(p, null), mid = DB.magasinId(), bid = DB.boutiqueId();
  const qm = DB.stock(pid, mid), qb = DB.stock(pid, bid), ph = photoOf(pid);
  const mv = DB.filter('movements', m => m.product_id === pid).sort(byDateDesc).slice(0, 40);
  const champs = [['Catégorie', v.category], ['Marque', p.brand || '-'], ['Unité de stockage', v.stock_unit], ['Unité de vente', v.sale_unit],
    ['Prix d’achat', money(p.purchase_price)], ['Prix de vente', money(p.sale_price)], ['Marge unitaire', money(Number(p.sale_price || 0) - Number(p.purchase_price || 0))],
    ['Seuil minimum', q(p.min_stock)], ['Quantité au magasin', q(qm)], ['Quantité en boutique', q(qb)], ['Quantité totale', q(qm + qb)], ['Statut', Number(p.active ?? 1) ? 'Actif' : 'Inactif']];
  const s = openSheet({
    title: 'Fiche produit', full: true,
    body: `<div class="panel"><div class="photo">${ph ? `<img src="${ph}" alt="">` : 'Aucune photo<br>enregistrée'}</div>
      <h2 style="font-size:19px;margin:0">${esc(p.name)}</h2><div class="info">Référence : ${esc(p.code)}</div>
      <div class="kv">${champs.map(([k, x]) => `<div><small>${esc(k)}</small><b>${esc(x)}</b></div>`).join('')}</div>
      ${p.notes ? `<div class="info" style="margin-top:10px">Observations : ${esc(p.notes)}</div>` : ''}</div>
      <div class="panel"><h2>Derniers mouvements de cet article</h2>
      ${tableHTML(['Date', 'Opération', { l: 'Quantité', num: 1 }, 'Emplacement', 'Utilisateur', 'Réf.'],
        mv.map(m => [m.date, m.type, qs(m.qty), nameOf('locations', m.location_id), userName(m.user_id), m.ref ? no(m.ref) : '']),
        { empty: 'Aucun mouvement enregistré pour cet article.' })}</div>`,
    buttons: [{ label: 'Fermer', cls: 'grey', onClick: a => a.close() },
              { label: 'Modifier le produit', cls: 'primary', onClick: a => { a.close(); productForm(pid); } }],
  });
  return s;
}

function productForm(pid = null, after = null) {
  const r = pid ? DB.get('products', pid) : null;
  const cats = DB.all('categories').sort(byName), units = DB.all('units').sort((a, b) => a.id - b.id);
  const unitOpts = units.map(u => ({ v: u.id, l: u.name }));
  let photo = pid ? photoOf(pid) : null, photoChanged = false;
  const val = k => r && r[k] != null ? (typeof r[k] === 'number' && ['purchase_price', 'sale_price', 'min_stock'].includes(k) ? q(r[k]) : r[k]) : '';
  const s = openSheet({
    title: pid ? 'Modifier le produit' : 'Enregistrer un nouveau produit', full: true,
    body: `<label class="f">Référence du produit</label><input class="i" id="code" value="${esc(val('code'))}">
      <label class="f">Désignation (commencez par le type d’article : « Ampoule LED 20W »)</label><input class="i" id="name" value="${esc(val('name'))}">
      <label class="f">Marque</label><input class="i" id="brand" value="${esc(val('brand'))}">
      <div class="row2"><div><label class="f">Prix d’achat (FCFA)</label><input class="i" id="buy" inputmode="decimal" value="${esc(val('purchase_price'))}"></div>
      <div><label class="f">Prix de vente (FCFA)</label><input class="i" id="sell" inputmode="decimal" value="${esc(val('sale_price'))}"></div></div>
      <label class="f">Seuil minimum d’alerte</label><input class="i" id="min" inputmode="decimal" value="${esc(val('min_stock'))}">
      <label class="f">Observations</label><input class="i" id="notes" value="${esc(val('notes'))}">
      <label class="f">Catégorie (choisissez ou saisissez une nouvelle)</label>${comboHTML('cat', cats.map(c => c.name), r ? nameOf('categories', r.category_id, '') : '')}
      <label class="f">Unité de stockage</label>${selectHTML('su', unitOpts, r ? r.stock_unit_id ?? '' : '', '—')}
      <label class="f">Unité de vente</label>${selectHTML('uv', unitOpts, r ? r.sale_unit_id ?? '' : '', '—')}
      <label class="f">Photo du produit</label>
      <div class="bar"><button class="btn blue" id="cam">Appareil photo</button><button class="btn primary" id="gal">Galerie</button><button class="btn light" id="nophoto">Retirer</button></div>
      <div class="photo" id="prev"></div>`,
    buttons: [{ label: 'Annuler', cls: 'grey', onClick: a => a.close() }, { label: 'Enregistrer', cls: 'primary', onClick: a => save(a) }],
  });
  const showPrev = () => { $('#prev', s.el).innerHTML = photo ? `<img src="${photo}" alt="">` : 'Aucune photo sélectionnée'; };
  showPrev();
  $('#cam', s.el).onclick = async () => { const d = await pickImage(true); if (d) { photo = d; photoChanged = true; showPrev(); } };
  $('#gal', s.el).onclick = async () => { const d = await pickImage(false); if (d) { photo = d; photoChanged = true; showPrev(); } };
  $('#nophoto', s.el).onclick = () => { photo = null; photoChanged = true; showPrev(); };
  async function save(a) {
    try {
      const g = id => $('#' + id, s.el).value;
      const code = g('code').trim(), name = g('name').trim();
      if (!code || !name) throw new Error('La référence et la désignation sont obligatoires.');
      if (DB.find('products', x => x.id !== pid && String(x.code) === code)) throw new Error('Cette référence de produit existe déjà.');
      const buy = nfloat(g('buy')), sell = nfloat(g('sell')), mn = nfloat(g('min'));
      let cat = null; const cv = g('cat').trim();
      if (cv) {
        const ex = DB.find('categories', c => String(c.name).toLowerCase() === cv.toLowerCase());
        cat = ex ? ex.id : DB.insert('categories', { name: cv });
      }
      const data = { code, name, brand: g('brand').trim(), purchase_price: buy, sale_price: sell, min_stock: mn, notes: g('notes'),
        category_id: cat, stock_unit_id: g('su') ? Number(g('su')) : null, sale_unit_id: g('uv') ? Number(g('uv')) : null };
      let id = pid;
      if (pid) { DB.update('products', pid, data); App.audit('Modification produit', pid, name); }
      else { id = DB.insert('products', { ...data, photo: '', active: 1, barcode: null }); App.audit('Création produit', '', name); }
      if (photoChanged) {
        if (photo) { DB.get('photos', id) ? DB.update('photos', id, { data: photo }) : DB.put('photos', id, { data: photo }); DB.update('products', id, { photo: 'cloud:' + id }); }
        else if (DB.get('photos', id)) DB.remove('photos', id);
      }
      a.close();
      if (after) after(id); else App.go('stock');
      if (!pid) alertBox('Produit enregistré. Enregistrez une réception (Achats / réceptions) pour lui donner une quantité au magasin.', 'Produit');
    } catch (e) { errorBox(e, 'Produit'); }
  }
}

async function importProducts() {
  const txt = await readFileAsText(); if (!txt) return;
  let n = 0, skip = 0;
  for (const r of parseCSV(txt)) {
    const code = String(r.code || '').trim(), name = String(r.name || r.designation || '').trim();
    if (!code || DB.find('products', p => String(p.code) === code)) { skip++; continue; }
    try {
      DB.insert('products', { code, name, brand: r.brand || r.marque || '', purchase_price: nfloat(r.purchase_price || r.prix_achat || 0),
        sale_price: nfloat(r.sale_price || r.prix_vente || 0), min_stock: nfloat(r.min_stock || r.seuil || 0), active: 1, photo: '', category_id: null });
      n++;
    } catch (e) { skip++; }
  }
  App.audit('Import produits CSV', '', `${n} produit(s)`);
  await alertBox(`${n} produit(s) importé(s).${skip ? `\n${skip} ligne(s) ignorée(s) (référence vide ou déjà existante).` : ''}`, 'Import CSV');
  App.go('products');
}

/* ======================= Transferts ======================= */
Screens.transfers = function () {
  const mid = DB.magasinId(), bid = DB.boutiqueId();
  let qm = 0, qb = 0;
  for (const [k, v] of DB.stocks) { const lid = Number(k.split('|')[1]); if (lid === mid) qm += v; else if (lid === bid) qb += v; }
  const rows = DB.all('transfers').sort(byDateDesc).map(t => {
    const ls = DB.filter('transfer_lines', l => l.transfer_id === t.id);
    return { t, n: ls.length, tot: ls.reduce((a, l) => a + Number(l.qty || 0), 0) };
  });
  render(headHTML('Transferts de stock', 'Magasin vers Boutique et Boutique vers Magasin, mise à jour automatique des quantités') +
    `<div class="bar"><button class="btn primary grow" id="new">+ Nouveau transfert</button></div>
    <div class="cards">${cardHTML('Quantité au magasin', q(qm), 'Stock disponible à transférer', '#1e73e8', 'MAG')}${cardHTML('Quantité en boutique', q(qb), 'Articles disponibles à la vente', '#18b65b', 'BTQ')}</div>
    <div class="panel"><h2>Historique des transferts (${rows.length})</h2>
    ${tableHTML(['N°', 'Source', 'Destination', 'Date', { l: 'Articles', num: 1 }, { l: 'Qté totale', num: 1 }, 'Statut', 'Responsable'],
      rows.map(x => [no(x.t.id), nameOf('locations', x.t.source_id), nameOf('locations', x.t.dest_id), x.t.date, x.n, q(x.tot), x.t.status, userName(x.t.user_id)]),
      { keys: rows.map(x => x.t.id), empty: 'Aucun transfert enregistré.' })}</div>`);
  $('#new').onclick = transferForm;
  onRows(main(), k => transferDetail(Number(k)));
};
function transferDetail(tid) {
  const t = DB.get('transfers', tid); if (!t) return;
  const ls = DB.filter('transfer_lines', l => l.transfer_id === tid);
  openSheet({
    title: `Transfert n° ${no(tid)}`,
    body: `<div class="info"><b>${esc(nameOf('locations', t.source_id))} vers ${esc(nameOf('locations', t.dest_id))}</b><br>Date : ${esc(t.date)} | Statut : ${esc(t.status)}</div>` +
      tableHTML(['Référence', 'Désignation', { l: 'Quantité', num: 1 }, 'Unité'], ls.map(l => {
        const p = DB.get('products', l.product_id) || {}; return [p.code || '', p.name || '?', q(l.qty), nameOf('units', p.stock_unit_id)];
      }), { wrapCols: [1] }),
    buttons: [{ label: 'Fermer', cls: 'grey', onClick: a => a.close() }],
  });
}
function transferForm() {
  const mid = DB.magasinId(), bid = DB.boutiqueId(), lines = [];
  const s = openSheet({
    title: 'Transfert de stock', full: true,
    body: `<label class="f">Sens du transfert</label>${selectHTML('sens', [{ v: 'mb', l: `${MAGASIN} vers ${BOUTIQUE}` }, { v: 'bm', l: `${BOUTIQUE} vers ${MAGASIN}` }], 'mb')}
      <label class="f">Article</label><div id="pk"></div><div class="info good" id="dispo"></div>
      <label class="f">Quantité</label><div class="bar"><input class="i grow" id="qty" inputmode="decimal" value="1"><button class="btn primary" id="add">+ Ajouter à la liste</button></div>
      <div class="panel"><h2>Articles à transférer</h2><div class="cart" id="lines"></div></div>`,
    buttons: [{ label: 'Annuler', cls: 'grey', onClick: a => a.close() }, { label: 'Valider le transfert', cls: 'green', onClick: a => save(a) }],
  });
  const src = () => $('#sens', s.el).value === 'mb' ? mid : bid, dst = () => src() === mid ? bid : mid;
  const pk = makePicker($('#pk', s.el), {
    rows: () => productsAt(src(), true), label: r => `${r.code} - ${r.name}`, sub: r => `dispo ${q(r.qty)} ${r.stock_unit}`,
    onPick: r => { $('#dispo', s.el).textContent = r ? `Quantité disponible en source : ${q(DB.stock(r.id, src()))}` : ''; if (r) $('#qty', s.el).focus(); },
  });
  const refresh = () => {
    $('#lines', s.el).innerHTML = lines.length ? lines.map((l, i) => `<div class="line"><div class="n"><b>${esc(l.name)}</b></div><div class="m">${q(l.qty)}</div><button data-i="${i}">✕</button></div>`).join('') : '<div class="empty">Aucun article ajouté.</div>';
    $$('#lines button', s.el).forEach(b => b.onclick = () => { lines.splice(+b.dataset.i, 1); refresh(); });
  };
  $('#sens', s.el).onchange = () => { pk.reload(); lines.length = 0; refresh(); $('#dispo', s.el).textContent = ''; };
  $('#add', s.el).onclick = () => {
    try {
      const r = pk.value; if (!r) throw new Error('Sélectionnez un article.');
      const qty = nfloat($('#qty', s.el).value); if (qty <= 0) throw new Error('La quantité doit être supérieure à 0.');
      const avail = DB.stock(r.id, src()), deja = lines.filter(x => x.pid === r.id).reduce((a, x) => a + x.qty, 0);
      if (qty + deja > avail + 1e-9) throw new Error(`Quantité disponible : ${q(avail)} (déjà sélectionné : ${q(deja)})`);
      const ex = lines.find(x => x.pid === r.id); if (ex) ex.qty += qty; else lines.push({ pid: r.id, name: `${r.code} - ${r.name}`, qty });
      refresh(); pk.clear(); $('#qty', s.el).value = '1';
    } catch (e) { errorBox(e, 'Transfert'); }
  };
  refresh();
  async function save(a) {
    try {
      if (!lines.length) throw new Error('Ajoutez au moins un article à transférer.');
      const si = src(), di = dst(), sens = $('#sens', s.el).selectedOptions[0].textContent;
      for (const x of lines) if (DB.stock(x.pid, si) + 1e-9 < x.qty) throw new Error(`Stock insuffisant pour ${x.name}`);
      const tid = DB.insert('transfers', { source_id: si, dest_id: di, date: nowStr(), status: 'Validé', user_id: App.user.id });
      for (const x of lines) {
        DB.insert('transfer_lines', { transfer_id: tid, product_id: x.pid, qty: x.qty });
        DB.adjust(x.pid, si, -x.qty, 'Transfert sortie', App.user.id, tid);
        DB.adjust(x.pid, di, x.qty, 'Transfert entrée', App.user.id, tid);
      }
      App.audit('Transfert', tid, `${lines.length} article(s) : ${sens}`);
      const tot = lines.reduce((t, x) => t + x.qty, 0);
      a.close(); App.go('transfers');
      alertBox(`Transfert n° ${no(tid)} validé.\n${sens}\n${lines.length} article(s), quantité totale ${q(tot)}.\nLes quantités ont été mises à jour automatiquement.`, 'Transfert');
    } catch (e) { errorBox(e, 'Transfert'); }
  }
}

/* ======================= Achats / réceptions ======================= */
Screens.purchases = function () {
  const rows = DB.all('purchases').sort(byDateDesc).map(a => {
    const ls = DB.filter('purchase_lines', l => l.purchase_id === a.id);
    return { a, n: ls.length, qty: ls.reduce((t, l) => t + Number(l.qty || 0), 0) };
  });
  render(headHTML('Achats & réceptions', 'Toute réception entre directement au STOCK MAGASIN') +
    `<div class="bar"><button class="btn green grow" id="new">+ Nouvelle réception</button></div>
    <div class="info">Les articles reçus sont ajoutés au magasin, puis transférés vers la boutique pour la vente.</div>
    <div class="panel"><h2>Historique des réceptions (${rows.length})</h2>
    ${tableHTML(['N°', 'Fournisseur', 'Date', { l: 'Articles', num: 1 }, { l: 'Qté reçue', num: 1 }, { l: 'Montant', num: 1 }, 'Statut', 'Utilisateur'],
      rows.map(x => [no(x.a.id), nameOf('suppliers', x.a.supplier_id), x.a.date, x.n, q(x.qty), money(x.a.total), x.a.status, userName(x.a.user_id)]),
      { empty: 'Aucune réception enregistrée.' })}</div>`);
  $('#new').onclick = purchaseForm;
};
function purchaseForm() {
  const mid = DB.magasinId(), sups = DB.all('suppliers').sort(byName);
  const s = openSheet({
    title: 'Nouvelle réception', full: true,
    body: `<div class="info good">Destination imposée : STOCK ${MAGASIN.toUpperCase()}</div>
      <label class="f">Fournisseur</label>${comboHTML('sup', sups.map(x => x.name))}
      <div class="hint">Sélectionnez un fournisseur existant ou saisissez directement son nom.</div>
      <label class="f">Téléphone du fournisseur (si nouveau)</label><input class="i" id="phone" inputmode="tel">
      <label class="f">Article reçu</label><div id="pk"></div>
      <button class="btn sm light" id="newp" style="margin-top:6px">+ Créer un nouveau produit</button>
      <label class="f req">QUANTITÉ REÇUE (obligatoire)</label><input class="i big" id="qty" inputmode="decimal">
      <div class="info good" id="etat"></div>
      <label class="f">Prix d’achat unitaire (FCFA)</label><input class="i" id="price" inputmode="decimal">`,
    buttons: [{ label: 'Annuler', cls: 'grey', onClick: a => a.close() }, { label: 'Valider la réception', cls: 'green', onClick: a => save(a) }],
  });
  const pk = makePicker($('#pk', s.el), {
    rows: () => DB.filter('products', p => Number(p.active ?? 1) === 1).map(p => productView(p, mid)).sort(byName),
    label: r => `${r.code} - ${r.name}`, sub: r => `au magasin : ${q(r.qty)} ${r.stock_unit}`,
    onPick: r => {
      $('#etat', s.el).textContent = r ? `Quantité actuelle au magasin : ${q(DB.stock(r.id, mid))}` : '';
      if (r && !$('#price', s.el).value) $('#price', s.el).value = q(r.purchase_price);
      if (r) $('#qty', s.el).focus();
    },
  });
  $('#newp', s.el).onclick = () => productForm(null, () => pk.reload());
  async function save(a) {
    try {
      const sv = $('#sup', s.el).value.trim();
      if (!sv) throw new Error('Veuillez sélectionner ou saisir le fournisseur.');
      const ex = DB.find('suppliers', x => String(x.name).trim().toLowerCase() === sv.toLowerCase());
      const r = pk.value; if (!r) throw new Error('Veuillez sélectionner l’article reçu.');
      if (!$('#qty', s.el).value.trim()) throw new Error('La quantité reçue est obligatoire.');
      const qty = nfloat($('#qty', s.el).value); if (qty <= 0) throw new Error('La quantité reçue doit être supérieure à 0.');
      const price = nfloat($('#price', s.el).value); if (price < 0) throw new Error('Le prix d’achat ne peut pas être négatif.');
      const sid = ex ? ex.id : DB.insert('suppliers', { name: sv, phone: $('#phone', s.el).value.trim(), email: '', address: '' });
      const total = qty * price;
      const aid = DB.insert('purchases', { supplier_id: sid, location_id: mid, date: nowStr(), total, status: 'Réception validée (magasin)', user_id: App.user.id });
      DB.insert('purchase_lines', { purchase_id: aid, product_id: r.id, qty, price });
      DB.adjust(r.id, mid, qty, 'Achat / entrée magasin', App.user.id, aid);
      if (price > 0) DB.update('products', r.id, { purchase_price: price });
      App.audit('Réception achat', aid, total);
      a.close(); App.go('stock');
      alertBox(`Réception n° ${no(aid)} validée.\nQuantité ajoutée au magasin : ${q(qty)}\nNouvelle quantité au magasin : ${q(DB.stock(r.id, mid))}`, 'Réception');
    } catch (e) { errorBox(e, 'Achat'); }
  }
}

/* ======================= Ventes / caisse ======================= */
Screens.sales = function () {
  const bid = DB.boutiqueId(); App.cart = [];
  const clients = DB.all('clients').sort(byName);
  render(headHTML('Ventes / caisse', 'Vente des articles de la boutique — recherche par famille, référence ou scan') +
    `<div class="panel"><label class="f" style="margin-top:0">Rechercher un article de la boutique</label><div id="pk"></div>
      <div class="row2"><div><label class="f">Quantité</label><input class="i big" id="qty" inputmode="decimal" value="1"></div>
      <div style="display:flex;align-items:flex-end"><button class="btn primary block" id="add">Ajouter au panier</button></div></div>
      <label class="f">Référence / scan du produit</label><input class="i" id="scan" placeholder="Saisissez la référence puis Entrée" autocomplete="off">
    </div>
    <div class="info" id="etat"></div>
    <div class="panel"><h2>Panier</h2><div class="cart" id="cart"></div><div class="total" id="total">TOTAL : 0 FCFA</div></div>
    <div class="panel">
      <label class="f" style="margin-top:0">Client (obligatoire pour une vente à crédit)</label>
      <div class="bar" style="margin:0">${selectHTML('cli', clients.map(c => ({ v: c.id, l: c.name })), '', '— Client comptant —').replace('class="i"', 'class="i grow"')}<button class="btn light" id="newcli">+</button></div>
      <label class="check" style="color:#a51d1d"><input type="checkbox" id="credit">Vente à crédit</label>
      <div class="row2"><div><label class="f">Montant payé (FCFA)</label><input class="i big" id="pay" inputmode="decimal" value="0"></div>
      <div><label class="f">Mode de paiement</label>${selectHTML('mode', PAYMENT_MODES, 'Espèces')}</div></div>
      <button class="btn green block" id="ok" style="margin-top:14px;min-height:54px;font-size:16px">VALIDER LA VENTE + FACTURE PDF</button>
    </div>
    <div class="panel"><h2>Ventes du jour</h2><div id="today"></div></div>`);
  const etat = $('#etat');
  const dispo = () => productsAt(bid, true);
  etat.textContent = `${dispo().length} article(s) disponible(s) en boutique`;
  const pk = makePicker($('#pk'), {
    rows: dispo, label: r => `${r.code} - ${r.name}`, sub: r => `dispo ${q(r.qty)} ${r.sale_unit} — ${money(r.sale_price)}`,
    onPick: r => { if (r) { $('#qty').focus(); $('#qty').select(); } },
  });
  const refresh = () => {
    const c = App.cart;
    $('#cart').innerHTML = c.length ? c.map((x, i) => `<div class="line"><div class="n"><b>${esc(x.name)}</b><small>${q(x.qty)} × ${money(x.price)}</small></div><div class="m">${money(x.qty * x.price)}</div><button data-i="${i}">✕</button></div>`).join('') : '<div class="empty">Le panier est vide.</div>';
    $$('#cart button').forEach(b => b.onclick = () => { App.cart.splice(+b.dataset.i, 1); refresh(); });
    const total = c.reduce((t, x) => t + x.qty * x.price, 0);
    $('#total').textContent = 'TOTAL : ' + money(total);
    if (!$('#credit').checked) $('#pay').value = q(total);
  };
  const push = (pid, qty) => {
    const prod = DB.get('products', pid);
    if (!prod || Number(prod.active ?? 1) !== 1) throw new Error('Article introuvable ou inactif.');
    const avail = DB.stock(pid, bid), ex = App.cart.find(x => x.pid === pid), deja = ex ? ex.qty : 0;
    if (qty + deja > avail + 1e-9) throw new Error(`Quantité disponible en boutique : ${q(avail)}`);
    if (ex) ex.qty += qty; else App.cart.push({ pid, name: prod.name, qty, price: Number(prod.sale_price || 0), cost: Number(prod.purchase_price || 0) });
    refresh();
  };
  const add = () => {
    try {
      const r = pk.value; if (!r) throw new Error('Sélectionnez un article de la boutique.');
      const qty = nfloat($('#qty').value); if (qty <= 0) throw new Error('La quantité doit être supérieure à 0.');
      push(r.id, qty); pk.clear(); $('#qty').value = '1'; pk.focus(); toast('Ajouté au panier');
    } catch (e) { errorBox(e, 'Panier'); }
  };
  $('#add').onclick = add;
  $('#qty').addEventListener('keydown', e => { if (e.key === 'Enter') add(); });
  $('#scan').addEventListener('keydown', e => {
    if (e.key !== 'Enter') return;
    const ref = $('#scan').value.trim(); if (!ref) return;
    try {
      const p = DB.find('products', x => Number(x.active ?? 1) === 1 && String(x.code).toLowerCase() === ref.toLowerCase() && DB.stock(x.id, bid) > 0);
      if (!p) throw new Error(`Aucun article disponible en boutique avec la référence « ${ref} ».`);
      push(p.id, nfloat($('#qty').value || 1) || 1); $('#scan').value = ''; toast(p.name + ' ajouté');
    } catch (err) { errorBox(err, 'Référence / scan'); }
  });
  $('#credit').onchange = refresh;
  $('#newcli').onclick = () => clientForm(id => {
    const sel = $('#cli'); const o = document.createElement('option'); o.value = id; o.textContent = DB.get('clients', id).name; sel.appendChild(o); sel.value = id;
  });
  $('#ok').onclick = validate;
  const showToday = () => {
    const ss = DB.filter('sales', x => x.date >= today() + ' 00:00:00').sort(byDateDesc);
    $('#today').innerHTML = tableHTML(['Facture', 'Heure', 'Client', { l: 'Total', num: 1 }, { l: 'Payé', num: 1 }],
      ss.map(x => [(DB.find('invoices', i => i.sale_id === x.id) || {}).number || '-', String(x.date).slice(11, 16), nameOf('clients', x.client_id, 'Comptant'), money(x.total), money(x.paid)]),
      { keys: ss.map(x => x.id), empty: 'Aucune vente aujourd’hui.' }) + (ss.length ? '<div class="hint">Touchez une vente pour ouvrir ou partager sa facture PDF.</div>' : '');
    onRows($('#today'), k => openInvoice(Number(k)));
  };
  showToday(); refresh();
  if (!dispo().length) alertBox('Aucun article disponible en boutique.\nTransférez d’abord des articles du magasin vers la boutique (onglet Transferts).', 'Caisse');

  async function validate() {
    const btn = $('#ok'); if (btn.disabled) return; btn.disabled = true;
    try {
      if (!App.cart.length) throw new Error('Le panier est vide.');
      const clientId = $('#cli').value ? Number($('#cli').value) : null;
      const paid = nfloat($('#pay').value), total = App.cart.reduce((t, x) => t + x.qty * x.price, 0);
      const credit = $('#credit').checked;
      if (paid < 0 || paid > total + 1e-9) throw new Error('Montant payé invalide.');
      if (credit && !clientId) throw new Error('Un client est obligatoire pour une vente à crédit.');
      for (const x of App.cart) if (DB.stock(x.pid, bid) + 1e-9 < x.qty) throw new Error(`Stock boutique insuffisant pour ${x.name}.`);
      let nEch = 3;
      if (credit && total - paid > 0) {
        const v = await promptBox('Nombre d’échéances mensuelles (1 à 24)', '3', { title: 'Échéances', type: 'number', inputmode: 'numeric' });
        nEch = Math.min(24, Math.max(1, parseInt(v || '3', 10) || 3));
      }
      const sale = DB.insert('sales', { client_id: clientId, location_id: bid, date: nowStr(), total, paid, status: 'Validée', payment_mode: $('#mode').value, user_id: App.user.id, credit: credit ? 1 : 0 });
      for (const x of App.cart) {
        DB.insert('sale_lines', { sale_id: sale, product_id: x.pid, qty: x.qty, unit: '', price: x.price, discount: 0, cost: x.cost || 0 });
        DB.adjust(x.pid, bid, -x.qty, 'Vente boutique', App.user.id, sale);
      }
      const inv = App.invoiceNumber();
      DB.insert('invoices', { sale_id: sale, number: inv, date: nowStr(), status: 'Validée' });
      const balance = total - paid;
      if (credit && balance > 0) {
        const cid = DB.insert('credits', { sale_id: sale, client_id: clientId, total, advance: paid, balance });
        const each = balance / nEch;
        for (let i = 1; i <= nEch; i++) DB.insert('installments', { credit_id: cid, num: i, amount: each, due_date: addDays(today(), 30 * i), status: 'En attente' });
      }
      App.audit('Vente', sale, total);
      App.cart = []; App.go('sales');
      const go = await confirmBox(`Vente validée. Facture ${inv}.\nLes quantités en boutique ont été mises à jour.`, 'Vente', 'Facture PDF', 'OK');
      if (go) openInvoice(sale);
    } catch (e) { errorBox(e, 'Vente'); }
    finally { btn.disabled = false; }
  }
};
async function openInvoice(saleId) {
  try { const { bytes, name } = await buildInvoicePDF(saleId); await saveFile(bytes, name, 'application/pdf'); }
  catch (e) { errorBox(e, 'Facture'); }
}
