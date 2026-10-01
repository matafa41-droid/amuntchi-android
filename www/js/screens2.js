/* AMUNTCHI Android — écrans (2/2) : clients & crédits, dépenses, inventaires, rapports,
 * paramètres, utilisateurs, journal d'audit, mot de passe. */
'use strict';

/* ======================= Clients & crédits ======================= */
Screens.clients = function () {
  const credits = DB.all('credits');
  const tot = credits.reduce((a, c) => a + Number(c.total || 0), 0), bal = credits.reduce((a, c) => a + Number(c.balance || 0), 0);
  const enc = DB.all('payments').reduce((a, p) => a + Number(p.amount || 0), 0);
  const comptes = DB.all('clients').map(c => {
    const cs = credits.filter(x => x.client_id === c.id);
    return { c, n: cs.length, total: cs.reduce((a, x) => a + Number(x.total || 0), 0), paye: cs.reduce((a, x) => a + Number(x.advance || 0), 0), reste: cs.reduce((a, x) => a + Number(x.balance || 0), 0) };
  }).sort((a, b) => (b.reste - a.reste) || byName(a.c, b.c));
  const ventes = credits.map(cr => {
    const s = DB.get('sales', cr.sale_id) || {}, its = DB.filter('installments', i => i.credit_id === cr.id);
    return { cr, date: s.date || '', inv: (DB.find('invoices', i => i.sale_id === cr.sale_id) || {}).number || '-', ech: its.length, ok: its.filter(i => i.status === 'Payé').length };
  }).sort((a, b) => String(b.date).localeCompare(String(a.date)));
  const tab = App.memo.clientsTab || 'comptes';
  render(headHTML('Clients & crédits', 'Ventes à crédit, échéances et paiements par client') +
    `<div class="bar"><button class="btn primary" id="newc">+ Client</button><button class="btn green" id="pay">Enregistrer un paiement</button></div>
    <div class="cards">${cardHTML('Ventes à crédit', String(credits.length), 'Nombre de crédits ouverts', '#1e73e8', 'NBR')}${cardHTML('Montant total à crédit', money(tot), 'Toutes ventes à crédit', '#7c4dff', 'TOT')}
      ${cardHTML('Déjà encaissé', money(enc), 'Paiements reçus', '#18b65b', 'PAY')}${cardHTML('Reste à payer', money(bal), 'Solde clients', '#ef3038', 'RST')}</div>
    <div class="tabs"><button data-t="comptes" class="${tab === 'comptes' ? 'on' : ''}">Comptes clients</button><button data-t="ventes" class="${tab === 'ventes' ? 'on' : ''}">Ventes à crédit</button></div>
    <div class="info">Touchez un client pour ouvrir son compte (articles, montants, échéances, paiement).</div>
    <div id="zone"></div>`);
  const show = t => {
    App.memo.clientsTab = t; $$('.tabs button').forEach(b => b.classList.toggle('on', b.dataset.t === t));
    if (t === 'comptes') {
      $('#zone').innerHTML = tableHTML(['Client', 'Téléphone', { l: 'Crédits', num: 1 }, { l: 'Total à crédit', num: 1 }, { l: 'Déjà payé', num: 1 }, { l: 'Reste à payer', num: 1 }],
        comptes.map(x => [x.c.name, x.c.phone || '', x.n, money(x.total), money(x.paye), money(x.reste)]), { keys: comptes.map(x => x.c.id), empty: 'Aucun client enregistré.' });
      onRows($('#zone'), k => clientDetail(Number(k)));
    } else {
      $('#zone').innerHTML = tableHTML(['Crédit', 'Client', 'Date de vente', 'Facture', { l: 'Total', num: 1 }, { l: 'Payé', num: 1 }, { l: 'Reste', num: 1 }, 'Échéances'],
        ventes.map(x => [no(x.cr.id), nameOf('clients', x.cr.client_id), x.date, x.inv, money(x.cr.total), money(x.cr.advance), money(x.cr.balance), `${x.ok}/${x.ech}`]),
        { keys: ventes.map(x => x.cr.client_id), empty: 'Aucune vente à crédit enregistrée.' });
      onRows($('#zone'), k => clientDetail(Number(k)));
    }
  };
  $$('.tabs button').forEach(b => b.onclick = () => show(b.dataset.t));
  $('#newc').onclick = () => clientForm(() => App.go('clients'));
  $('#pay').onclick = () => paymentForm();
  show(tab);
};
function clientDetail(cid) {
  const c = DB.get('clients', cid); if (!c) return;
  const crs = DB.filter('credits', x => x.client_id === cid);
  const ids = new Set(crs.map(x => x.id));
  const agg = { t: crs.reduce((a, x) => a + Number(x.total || 0), 0), a: crs.reduce((a, x) => a + Number(x.advance || 0), 0), b: crs.reduce((a, x) => a + Number(x.balance || 0), 0) };
  const ech = DB.filter('installments', i => ids.has(i.credit_id)).sort((a, b) => String(a.due_date).localeCompare(String(b.due_date)));
  const retard = ech.filter(i => i.status !== 'Payé' && String(i.due_date) < today()).length;
  const arts = [];
  crs.map(cr => ({ cr, s: DB.get('sales', cr.sale_id) })).filter(x => x.s).sort((a, b) => String(b.s.date).localeCompare(String(a.s.date))).forEach(({ cr, s }) => {
    const inv = (DB.find('invoices', i => i.sale_id === s.id) || {}).number || '-';
    DB.filter('sale_lines', l => l.sale_id === s.id).sort((a, b) => Math.abs(a.id) - Math.abs(b.id)).forEach(l =>
      arts.push([s.date, inv, nameOf('products', l.product_id, '?'), q(l.qty), money(l.price), money(l.qty * l.price), Number(cr.balance) <= 0 ? 'Soldé' : 'En cours']));
  });
  const pays = DB.filter('payments', p => p.client_id === cid).sort(byDateDesc);
  const s = openSheet({
    title: 'Compte client', full: true,
    body: `<div class="panel"><h2 style="font-size:19px;margin-bottom:4px">${esc(c.name)}</h2>
      <div class="info">Téléphone : ${esc(c.phone || '-')}<br>Email : ${esc(c.email || '-')}<br>Adresse : ${esc(c.address || '-')}</div></div>
      <div class="cards">${cardHTML('Total à crédit', money(agg.t), `${crs.length} vente(s) à crédit`, '#1e73e8', 'TOT')}${cardHTML('Montant payé', money(agg.a), 'Avances et paiements', '#18b65b', 'PAY')}
        ${cardHTML('Reste à payer', money(agg.b), 'Solde du compte', '#ef3038', 'RST')}${cardHTML('Échéances en retard', String(retard), 'À relancer', '#a51d1d', 'RET')}</div>
      <div class="tabs"><button data-t="a" class="on">Articles à crédit</button><button data-t="e">Échéances</button><button data-t="p">Paiements reçus</button></div>
      <div id="cz"></div>`,
    buttons: [{ label: 'Fermer', cls: 'grey', onClick: a => a.close() },
              { label: 'Encaisser un paiement', cls: 'green', onClick: a => { a.close(); paymentForm(cid); } }],
  });
  const zones = {
    a: () => tableHTML(['Date', 'Facture', 'Article', { l: 'Qté', num: 1 }, { l: 'Prix unitaire', num: 1 }, { l: 'Montant', num: 1 }, 'État du crédit'], arts, { empty: 'Aucun article acheté à crédit par ce client.' }),
    e: () => tableHTML(['Crédit', 'N° échéance', { l: 'Montant', num: 1 }, 'Date d’échéance', 'Statut', 'Observation'],
      ech.map(i => [no(i.credit_id), i.num, money(i.amount), i.due_date, i.status, (i.status !== 'Payé' && String(i.due_date) < today()) ? { html: '<span class="neg">En retard</span>' } : '']), { empty: 'Aucune échéance enregistrée.' }),
    p: () => tableHTML(['Date', { l: 'Montant', num: 1 }, 'Mode', 'Crédit', 'Encaissé par'],
      pays.map(p => [p.date, money(p.amount), p.mode, p.credit_id != null ? no(p.credit_id) : '', userName(p.user_id)]), { empty: 'Aucun paiement enregistré.' }),
  };
  const show = t => { $$('.tabs button', s.el).forEach(b => b.classList.toggle('on', b.dataset.t === t)); $('#cz', s.el).innerHTML = zones[t](); };
  $$('.tabs button', s.el).forEach(b => b.onclick = () => show(b.dataset.t));
  show('a');
}
function paymentForm(clientId = null) {
  const crs = DB.filter('credits', c => Number(c.balance) > 0 && (clientId == null || c.client_id === clientId))
    .sort((a, b) => String((DB.get('sales', b.sale_id) || {}).date || '').localeCompare(String((DB.get('sales', a.sale_id) || {}).date || '')));
  const s = openSheet({
    title: 'Encaisser un paiement',
    body: crs.length ? `<label class="f">Crédit à régler</label>${selectHTML('cr', crs.map(c => ({ v: c.id, l: `${no(c.id)} - ${nameOf('clients', c.client_id)} - reste ${money(c.balance)}` })), crs[0].id)}
      <div class="info good" id="inf"></div>
      <label class="f">Montant du paiement (FCFA)</label><input class="i big" id="amt" inputmode="decimal">
      <label class="f">Mode de paiement</label>${selectHTML('mode', PAYMENT_MODES, 'Espèces')}` : '<div class="empty">Aucun crédit en cours à régler.</div>',
    buttons: [{ label: 'Annuler', cls: 'grey', onClick: a => a.close() }].concat(crs.length ? [{ label: 'Valider le paiement', cls: 'green', onClick: a => save(a) }] : []),
  });
  if (!crs.length) return;
  const inf = () => { const c = DB.get('credits', Number($('#cr', s.el).value)); $('#inf', s.el).textContent = `Total ${money(c.total)} | déjà payé ${money(c.advance)} | reste ${money(c.balance)}`; };
  $('#cr', s.el).onchange = inf; inf();
  async function save(a) {
    try {
      const cid = Number($('#cr', s.el).value), amount = nfloat($('#amt', s.el).value), r = DB.get('credits', cid);
      if (amount <= 0) throw new Error('Le montant doit être supérieur à 0.');
      if (amount > Number(r.balance) + 1e-9) throw new Error(`Montant supérieur au solde (${money(r.balance)}).`);
      DB.insert('payments', { client_id: r.client_id, credit_id: cid, amount, date: nowStr(), mode: $('#mode', s.el).value, user_id: App.user.id, note: '' });
      DB.recomputeCredit(cid, true);
      App.audit('Paiement crédit', cid, amount);
      a.close(); App.go('clients');
      alertBox(`Paiement de ${money(amount)} enregistré.\nNouveau solde du crédit : ${money(DB.get('credits', cid).balance)}`, 'Paiement');
    } catch (e) { errorBox(e, 'Paiement'); }
  }
}
function clientForm(after) {
  openSheet({
    title: 'Nouveau client',
    body: `<label class="f">Nom</label><input class="i" id="n"><label class="f">Téléphone</label><input class="i" id="p" inputmode="tel">
      <label class="f">Email</label><input class="i" id="e" type="email"><label class="f">Adresse</label><input class="i" id="a">`,
    buttons: [{ label: 'Annuler', cls: 'grey', onClick: s => s.close() }, {
      label: 'Enregistrer', cls: 'primary', onClick: s => {
        const g = id => $('#' + id, s.el).value;
        if (!g('n').trim()) return errorBox('Nom obligatoire', 'Client');
        const id = DB.insert('clients', { name: g('n').trim(), phone: g('p'), email: g('e'), address: g('a') });
        App.audit('Création client', '', g('n').trim()); s.close(); after && after(id);
      },
    }],
  });
}

/* ======================= Dépenses ======================= */
Screens.expenses = function () {
  render(headHTML('Dépenses', 'Historique complet des dépenses journalières et mensuelles') +
    `<div class="bar"><input class="i grow" id="q" type="search" placeholder="Motif ou date (AAAA-MM-JJ)"><button class="btn primary" id="go">Rechercher</button></div>
    <div class="bar"><button class="btn primary grow" id="new">+ Nouvelle dépense</button></div><div class="cards" id="cards"></div><div class="panel" id="zone"></div>`);
  const load = () => {
    const all = DB.all('expenses'), j = today() + ' 00:00:00', m = today().slice(0, 8) + '01 00:00:00';
    const sum = a => a.reduce((t, e) => t + Number(e.amount || 0), 0);
    $('#cards').innerHTML = cardHTML('Dépenses du jour', money(sum(all.filter(e => e.date >= j))), frenchDate(), '#f5a400', 'JR') +
      cardHTML('Dépenses du mois', money(sum(all.filter(e => e.date >= m))), 'Mois en cours', '#1e73e8', 'MOIS') +
      cardHTML('Total des dépenses', money(sum(all)), `${all.length} dépense(s) enregistrée(s)`, '#a51d1d', 'TOT');
    const term = $('#q').value.trim().toLowerCase();
    const rows = all.filter(e => !term || String(e.category || '').toLowerCase().includes(term) || String(e.date).includes(term)).sort(byDateDesc);
    $('#zone').innerHTML = `<h2>Liste des dépenses (${rows.length})</h2>` + tableHTML(['N°', 'Catégorie / motif', { l: 'Montant', num: 1 }, 'Date et heure', 'Mode', 'Enregistrée par'],
      rows.map(e => [no(e.id), e.category || '-', money(e.amount), e.date, e.mode || '-', userName(e.user_id)]), { empty: 'Aucune dépense enregistrée pour le moment.', wrapCols: [1] });
  };
  $('#go').onclick = load; $('#q').addEventListener('input', load);
  $('#new').onclick = expenseForm;
  load();
};
function expenseForm() {
  const base = ['Transport', 'Carburant', 'Électricité', 'Eau', 'Loyer', 'Salaires', 'Entretien', 'Téléphone', 'Fournitures', 'Divers'];
  const motifs = [...new Set([...base, ...DB.all('expenses').map(e => e.category).filter(Boolean)])].sort((a, b) => a.localeCompare(b, 'fr'));
  openSheet({
    title: 'Enregistrer une dépense',
    body: `<label class="f">Catégorie / motif</label>${comboHTML('cat', motifs)}<label class="f">Montant (FCFA)</label><input class="i big" id="amt" inputmode="decimal">
      <label class="f">Mode de paiement</label>${selectHTML('mode', PAYMENT_MODES, 'Espèces')}`,
    buttons: [{ label: 'Annuler', cls: 'grey', onClick: a => a.close() }, {
      label: 'Enregistrer', cls: 'primary', onClick: a => {
        try {
          const cat = $('#cat', a.el).value.trim(); if (!cat) throw new Error('Indiquez la catégorie ou le motif de la dépense.');
          const m = nfloat($('#amt', a.el).value); if (m <= 0) throw new Error('Le montant doit être supérieur à 0.');
          const id = DB.insert('expenses', { category: cat, amount: m, date: nowStr(), user_id: App.user.id, mode: $('#mode', a.el).value, receipt: null });
          App.audit('Dépense', id, m); a.close(); App.go('expenses');
          alertBox(`Dépense n° ${no(id)} enregistrée : ${money(m)}.`, 'Dépense');
        } catch (e) { errorBox(e, 'Dépense'); }
      },
    }],
  });
}

/* ======================= Inventaires ======================= */
Screens.inventory = function () {
  const rows = DB.all('inventories').sort(byDateDesc).map(i => {
    const ls = DB.filter('inventory_lines', l => l.inventory_id === i.id), s = k => ls.reduce((a, l) => a + Number(l[k] || 0), 0);
    return { i, theo: s('theoretical'), reel: s('real_qty'), ecart: s('difference'), art: ls.length ? nameOf('products', ls[0].product_id) : '-' };
  });
  render(headHTML('Inventaires', 'Comparaison du stock théorique et du stock réel, magasin et boutique') +
    `<div class="bar"><button class="btn primary grow" id="new">+ Nouvel inventaire</button></div>
    <div class="panel"><h2>Historique des inventaires (${rows.length})</h2>${tableHTML(['N°', 'Emplacement', 'Article', 'Date', { l: 'Théorique', num: 1 }, { l: 'Réel', num: 1 }, { l: 'Écart', num: 1 }, 'Responsable'],
      rows.map(x => [no(x.i.id), nameOf('locations', x.i.location_id), x.art, x.i.date, q(x.theo), q(x.reel), qs(x.ecart), userName(x.i.user_id)]), { empty: 'Aucun inventaire enregistré.', wrapCols: [2] })}</div>`);
  $('#new').onclick = inventoryForm;
};
function inventoryForm() {
  const locs = DB.all('locations').sort((a, b) => a.id - b.id);
  let theo = null;
  const s = openSheet({
    title: 'Nouvel inventaire', full: true,
    body: `<label class="f">Emplacement à inventorier</label>${selectHTML('loc', locs.map(l => ({ v: l.id, l: l.name })), locs[0] ? locs[0].id : '')}
      <label class="f">Article</label><div id="pk"></div>
      <div class="panel" style="margin-top:12px"><div class="info" id="pl">Sélectionnez un emplacement et un article</div><div class="big-qty" id="bq">—</div></div>
      <label class="f">Stock réel compté</label><input class="i big" id="real" inputmode="decimal"><div class="info" id="ec"></div>`,
    buttons: [{ label: 'Annuler', cls: 'grey', onClick: a => a.close() }, { label: 'Valider l’inventaire', cls: 'primary', onClick: a => save(a) }],
  });
  const lid = () => Number($('#loc', s.el).value), place = () => $('#loc', s.el).selectedOptions[0].textContent;
  const onCount = () => {
    if (theo == null) return; let r; try { r = nfloat($('#real', s.el).value); } catch (e) { $('#ec', s.el).textContent = ''; return; }
    const d = r - theo, el = $('#ec', s.el);
    el.textContent = `Écart constaté : ${qs(d)}` + (Math.abs(d) < 1e-9 ? '  (aucun écart)' : '  — le stock sera ajusté automatiquement');
    el.className = 'info ' + (Math.abs(d) < 1e-9 ? 'good' : 'bad');
  };
  const pk = makePicker($('#pk', s.el), {
    rows: () => productsAt(lid(), false), label: r => `${r.code} - ${r.name}`, sub: r => `${q(r.qty)} ${r.stock_unit}`,
    onPick: r => {
      if (!r) { theo = null; $('#bq', s.el).textContent = '—'; return; }
      theo = DB.stock(r.id, lid());
      $('#pl', s.el).textContent = `Quantité disponible en ${place().toLowerCase()} pour cet article :`;
      $('#bq', s.el).textContent = `${q(theo)} ${r.stock_unit !== '-' ? r.stock_unit : ''}`.trim();
      $('#real', s.el).value = q(theo); onCount(); $('#real', s.el).focus(); $('#real', s.el).select();
    },
  });
  $('#loc', s.el).onchange = () => { pk.reload(); theo = null; $('#pl', s.el).textContent = `Emplacement : ${place()}`; $('#bq', s.el).textContent = '—'; };
  $('#real', s.el).addEventListener('input', onCount);
  async function save(a) {
    try {
      const r = pk.value; if (!r) throw new Error('Sélectionnez un article.');
      if (!$('#real', s.el).value.trim()) throw new Error('Saisissez le stock réel compté.');
      const real = nfloat($('#real', s.el).value), L = lid(), th = DB.stock(r.id, L), diff = real - th, pl = place();
      if (real < 0) throw new Error('Le stock réel ne peut pas être négatif.');
      const iid = DB.insert('inventories', { location_id: L, date: nowStr(), status: 'Validé', user_id: App.user.id });
      DB.insert('inventory_lines', { inventory_id: iid, product_id: r.id, theoretical: th, real_qty: real, difference: diff });
      if (Math.abs(diff) > 1e-9) DB.adjust(r.id, L, diff, 'Ajustement inventaire', App.user.id, iid, 'Correction inventaire');
      App.audit('Inventaire', iid, `écart ${qs(diff)}`);
      a.close(); App.go('inventory');
      alertBox(`Inventaire n° ${no(iid)} validé.\nEmplacement : ${pl}\nStock théorique : ${q(th)}\nStock réel compté : ${q(real)}\nÉcart : ${qs(diff)}\nQuantité disponible désormais en ${pl.toLowerCase()} : ${q(DB.stock(r.id, L))}`, 'Inventaire');
    } catch (e) { errorBox(e, 'Inventaire'); }
  }
}

/* ======================= Rapports ======================= */
function periodBounds(p) {
  const d = new Date(), iso = isoDate;
  if (p === 'Aujourd’hui') return [iso(d), iso(d)];
  if (p === '7 derniers jours') return [addDays(iso(d), -6), iso(d)];
  if (p === 'Mois en cours') return [iso(d).slice(0, 8) + '01', iso(d)];
  if (p === 'Mois précédent') { const f = new Date(d.getFullYear(), d.getMonth(), 1), l = new Date(f - 864e5); return [iso(l).slice(0, 8) + '01', iso(l)]; }
  if (p === 'Année en cours') return [d.getFullYear() + '-01-01', iso(d)];
  return [null, null];
}
Screens.reports = function () {
  const presets = ['Aujourd’hui', '7 derniers jours', 'Mois en cours', 'Mois précédent', 'Année en cours', 'Tout', 'Personnalisé'];
  render(headHTML('Rapports & statistiques', 'Filtrage par période, marges par produit et exports') +
    `<div class="panel"><label class="f" style="margin-top:0">Période</label>${selectHTML('pre', presets, App.memo.preset || 'Mois en cours')}
      <div class="row2"><div><label class="f">Du</label><input class="i" type="date" id="a"></div><div><label class="f">Au</label><input class="i" type="date" id="b"></div></div>
      <button class="btn primary block" id="go" style="margin-top:10px">Appliquer</button></div>
    <div class="cards" id="cards"></div>
    <div class="bar"><button class="btn green" id="csv">Rapport CSV</button><button class="btn blue" id="pdf">Rapport PDF</button><button class="btn light" id="pcsv">Produits CSV</button></div>
    <div class="panel" id="zone"></div>`);
  let last = null;
  const resolve = () => {
    const p = $('#pre').value;
    if (p === 'Tout') { $('#a').value = ''; $('#b').value = ''; return [null, null]; }
    if (p === 'Personnalisé') {
      const a = $('#a').value || null, b = $('#b').value || null;
      if (a && b && a > b) throw new Error('La date de début est postérieure à la date de fin.');
      return [a, b];
    }
    const [a, b] = periodBounds(p); $('#a').value = a || ''; $('#b').value = b || ''; return [a, b];
  };
  const load = () => {
    try {
      App.memo.preset = $('#pre').value;
      const [a, b] = resolve();
      const inP = d => (!a || d >= a + ' 00:00:00') && (!b || d < addDays(b, 1) + ' 00:00:00');
      const sales = DB.filter('sales', s => inP(String(s.date)));
      const sids = new Set(sales.map(s => s.id));
      const ca = sales.reduce((t, s) => t + Number(s.total || 0), 0), cash = sales.reduce((t, s) => t + Number(s.paid || 0), 0);
      const achats = DB.filter('purchases', x => inP(String(x.date))).reduce((t, x) => t + Number(x.total || 0), 0);
      const dep = DB.filter('expenses', x => inP(String(x.date))).reduce((t, x) => t + Number(x.amount || 0), 0);
      const credits = DB.filter('credits', c => Number(c.balance) > 0).reduce((t, c) => t + Number(c.balance || 0), 0);
      const per = new Map();
      for (const l of DB.all('sale_lines')) {
        if (!sids.has(l.sale_id)) continue;
        const p = DB.get('products', l.product_id); if (!p) continue;
        const r = per.get(p.id) || { code: p.code, name: p.name, category: nameOf('categories', p.category_id), qty: 0, revenue: 0, cost: 0 };
        const unitCost = Number(l.cost || 0) || Number(p.purchase_price || 0);
        r.qty += Number(l.qty || 0); r.revenue += l.qty * l.price; r.cost += l.qty * unitCost; per.set(p.id, r);
      }
      const rows = [...per.values()].sort((x, y) => y.revenue - x.revenue);
      const marge = rows.reduce((t, r) => t + r.revenue - r.cost, 0), gross = rows.reduce((t, r) => t + r.revenue, 0), rate = gross ? marge / gross * 100 : 0;
      $('#cards').innerHTML = cardHTML('Chiffre d’affaires', money(ca), `${sales.length} vente(s)`, '#18b65b', 'CA') + cardHTML('Marge brute', money(marge), `Taux ${rate.toFixed(1)} %`, '#1e73e8', 'MRG') +
        cardHTML('Achats', money(achats), 'Réceptions', '#7c4dff', 'ACH') + cardHTML('Dépenses', money(dep), 'Charges', '#f5a400', 'DEP') +
        cardHTML('Encaissé', money(cash), 'Règlements sur ventes', '#00a6a6', 'ENC') + cardHTML('Reste à encaisser', money(ca - cash), 'Sur la période', '#ef3038', 'DU') +
        cardHTML('Résultat indicatif', money(marge - dep), 'Marge brute moins dépenses', '#062d5c', 'RES') + cardHTML('Crédits en cours', money(credits), 'Tous clients', '#a51d1d', 'CRD');
      const titre = !a ? 'toute la période' : `du ${a} au ${b || today()}`;
      $('#zone').innerHTML = `<h2>Marges par produit — ${esc(titre)}</h2>` + tableHTML(['Code', 'Produit', 'Catégorie', { l: 'Qté vendue', num: 1 }, { l: 'CA', num: 1 }, { l: 'Coût', num: 1 }, { l: 'Marge', num: 1 }, { l: 'Taux', num: 1 }],
        rows.map(r => [r.code, r.name, r.category, q(r.qty), money(r.revenue), money(r.cost), money(r.revenue - r.cost), `${(r.revenue ? (r.revenue - r.cost) / r.revenue * 100 : 0).toFixed(1)} %`]),
        { empty: 'Aucune vente sur la période sélectionnée.', wrapCols: [1] });
      last = { rows, t: { periode: titre, ca, marge, achats, depenses: dep, encaisse: cash, credits, ventes: sales.length } };
    } catch (e) { errorBox(e, 'Rapports'); }
  };
  $('#go').onclick = load; $('#pre').onchange = load;
  $('#csv').onclick = async () => {
    if (!last) return; const t = last.t, r2 = n => Math.round(n * 100) / 100;
    const data = [['Mini Quincaillerie AMUNTCHI - Rapport', t.periode], [], ['Chiffre d’affaires', r2(t.ca)], ['Marge brute', r2(t.marge)], ['Achats', r2(t.achats)],
      ['Dépenses', r2(t.depenses)], ['Encaissé', r2(t.encaisse)], ['Crédits en cours', r2(t.credits)], [], ['code', 'produit', 'categorie', 'qte_vendue', 'ca', 'cout', 'marge'],
      ...last.rows.map(r => [r.code, r.name, r.category, r.qty, r2(r.revenue), r2(r.cost), r2(r.revenue - r.cost)])];
    await saveFile(toCSV(data), 'rapport_amuntchi.csv', 'text/csv'); App.audit('Export rapport CSV');
  };
  $('#pdf').onclick = async () => {
    if (!last) return;
    try { const { bytes, name } = await buildReportPDF(last.t, last.rows); await saveFile(bytes, name, 'application/pdf'); App.audit('Export rapport PDF', '', name); }
    catch (e) { errorBox(e, 'Rapport'); }
  };
  $('#pcsv').onclick = async () => {
    const mid = DB.magasinId(), bid = DB.boutiqueId();
    const data = [['code', 'designation', 'marque', 'prix_achat', 'prix_vente', 'seuil', 'unite_stock', 'unite_vente', 'qte_magasin', 'qte_boutique', 'qte_totale'],
      ...DB.all('products').sort(byName).map(p => {
        const m = DB.stock(p.id, mid), b = DB.stock(p.id, bid); let t = 0; for (const l of DB.all('locations')) t += DB.stock(p.id, l.id);
        return [p.code, p.name, p.brand || '', p.purchase_price, p.sale_price, p.min_stock, nameOf('units', p.stock_unit_id, ''), nameOf('units', p.sale_unit_id, ''), m, b, t];
      })];
    await saveFile(toCSV(data), 'produits_amuntchi.csv', 'text/csv');
  };
  load();
};

/* ======================= Paramètres ======================= */
Screens.settings = function () {
  const cats = () => DB.all('categories').sort(byName);
  const auto = DB.getMeta('cloud_auto', '1') === '1';
  render(headHTML('Paramètres & sauvegarde', 'Configuration commerciale, synchronisation et sécurité') +
    `<div class="panel"><h2>Numérotation facture (ce téléphone)</h2>
      <div class="row2"><div><label class="f">Préfixe</label><input class="i" id="pre" value="${esc(App.invoicePrefix())}"></div>
      <div><label class="f">Prochain numéro</label><input class="i" id="nxt" inputmode="numeric" value="${esc(DB.getMeta('invoice_next', '1'))}"></div></div>
      <div class="hint">Chaque appareil a son propre préfixe pour que deux factures n’aient jamais le même numéro.</div>
      <button class="btn primary" id="savePre" style="margin-top:10px">Enregistrer</button></div>
    <div class="panel"><h2>Catégories de produits</h2><div class="bar"><input class="i grow" id="catn" placeholder="Nouvelle catégorie"><button class="btn primary" id="addCat">+ Ajouter</button></div><div id="catl"></div></div>
    <div class="panel"><h2>Synchronisation Cloud PC / Android (Supabase)</h2>
      <div class="info">Ce téléphone, le PC et les autres téléphones partagent les mêmes données. Sans Internet, tout est enregistré sur le téléphone et envoyé dès que la connexion revient.</div>
      ${cloudFieldsHTML()}
      <label class="check"><input type="checkbox" id="auto" ${auto ? 'checked' : ''}>Synchronisation automatique (toutes les 2 minutes et peu après chaque opération)</label>
      <div class="bar"><button class="btn primary" id="cSave">Enregistrer</button><button class="btn blue" id="cTest">Tester la connexion</button><button class="btn green" id="cSync">Synchroniser maintenant</button></div>
      <div class="info good" id="cState"></div></div>
    <div class="panel"><h2>Sécurité des comptes</h2><div class="info">Les mots de passe sont chiffrés (PBKDF2-SHA256, sel aléatoire, 200 000 itérations), comme sur le PC.</div>
      <div class="bar"><button class="btn primary" id="pw">Changer mon mot de passe</button>${App.user.role === 'Administrateur' ? '<button class="btn blue" id="us">Gérer les utilisateurs</button>' : ''}</div></div>
    <div class="panel"><h2>Sauvegarde</h2><div class="bar"><button class="btn green" id="bak">Exporter une sauvegarde (JSON)</button></div></div>
    <div class="panel"><h2>Ce téléphone</h2><div class="info">Efface les données de ce téléphone puis les télécharge à nouveau depuis le Cloud. Les modifications non envoyées seraient perdues.</div>
      <button class="btn light" id="relo">Réinitialiser ce téléphone</button></div>
    <div class="panel" style="background:#fff8f8"><h2 style="color:#a51d1d">Réinitialisation sécurisée des données</h2>
      <div class="info">Supprime les données commerciales sur TOUS les appareils (PC et téléphones) après synchronisation. Exportez d’abord une sauvegarde.</div>
      <button class="btn red" id="reset">RÉINITIALISER LES DONNÉES</button></div>`);
  const showCats = () => {
    $('#catl').innerHTML = tableHTML(['Catégorie', 'Produits'], cats().map(c => [c.name, DB.filter('products', p => p.category_id === c.id).length]), { keys: cats().map(c => c.id), empty: 'Aucune catégorie.' }) + '<div class="hint">Touchez une catégorie pour la supprimer.</div>';
    onRows($('#catl'), async k => {
      const c = DB.get('categories', Number(k)), used = DB.filter('products', p => p.category_id === c.id).length;
      if (used) return alertBox(`Impossible de supprimer « ${c.name} » : ${used} produit(s) utilisent cette catégorie.`, 'Catégorie');
      if (await confirmBox(`Supprimer « ${c.name} » ?`, 'Catégorie', 'Supprimer', 'Annuler', true)) { DB.remove('categories', c.id); App.audit('Suppression catégorie', c.id, c.name); showCats(); }
    });
  };
  showCats();
  $('#addCat').onclick = () => {
    const n = $('#catn').value.trim(); if (!n) return;
    if (DB.find('categories', c => String(c.name).toLowerCase() === n.toLowerCase())) return errorBox('Cette catégorie existe déjà.', 'Catégorie');
    DB.insert('categories', { name: n }); App.audit('Création catégorie', '', n); $('#catn').value = ''; showCats();
  };
  $('#savePre').onclick = () => {
    const n = parseInt($('#nxt').value, 10); if (!(n >= 1)) return errorBox('Numéro invalide.', 'Paramètres');
    DB.setMeta('invoice_prefix', $('#pre').value.trim()); DB.setMeta('invoice_next', String(n));
    App.audit('Modification paramètres', 'invoice_prefix', $('#pre').value.trim()); toast('Numérotation enregistrée.');
  };
  const state = () => { $('#cState').textContent = `Dernière synchronisation réussie : ${DB.getMeta('last_sync', '') || 'jamais'} | Modifications en attente d’envoi : ${DB.pendingCount()}${Cloud.lastError ? ' | Dernière erreur : ' + Cloud.lastError : ''}`; };
  state();
  const store = () => { saveCloudFields(); DB.setMeta('cloud_auto', $('#auto').checked ? '1' : '0'); };
  $('#cSave').onclick = () => { try { store(); App.audit('Modification paramètres Cloud'); toast('Paramètres Cloud enregistrés.'); } catch (e) { errorBox(e, 'Cloud'); } };
  $('#cTest').onclick = async () => { try { store(); await Cloud.login(); alertBox('Connexion au Cloud réussie.', 'Cloud'); } catch (e) { errorBox(e, 'Cloud'); } };
  $('#cSync').onclick = async () => { try { store(); await App.syncNow(true); } catch (e) { errorBox(e, 'Cloud'); } state(); };
  $('#pw').onclick = changeOwnPassword;
  if ($('#us')) $('#us').onclick = () => App.go('users');
  $('#bak').onclick = async () => {
    const data = { format: 'AMUNTCHI_BACKUP_ANDROID', version: APP_VERSION, created_at: nowStr(), device: DB.getMeta('device_name', ''), tables: {} };
    CLOUD_TABLES.forEach(t => data.tables[t] = DB.all(t));
    await saveFile(JSON.stringify(data), `amuntchi_sauvegarde_${today()}.json`, 'application/json');
  };
  $('#relo').onclick = async () => {
    if (DB.pendingCount() && !await confirmBox(`${DB.pendingCount()} modification(s) ne sont pas encore envoyées au Cloud et seront PERDUES.\nContinuer ?`, 'Attention', 'Continuer', 'Annuler', true)) return;
    if (!await confirmBox('Effacer les données de ce téléphone et tout retélécharger depuis le Cloud ?', 'Réinitialiser ce téléphone', 'Oui', 'Non', true)) return;
    const keep = {}; ['supa_url', 'supa_key', 'supa_email', 'supa_password', 'device_no', 'device_name', 'cloud_auto', 'invoice_prefix', 'invoice_next'].forEach(k => keep[k] = DB.getMeta(k, ''));
    await DB.resetLocal(); localStorage.setItem('amx_keep', JSON.stringify(keep)); location.reload();
  };
  $('#reset').onclick = secureReset;
};
function cloudFieldsHTML() {
  const v = k => esc(DB.getMeta(k, ''));
  return `<label class="f">Adresse du projet (Project URL)</label><input class="i" id="c_url" value="${v('supa_url')}" placeholder="https://xxxx.supabase.co" autocapitalize="off">
    <label class="f">Clé publique (anon public key)</label><input class="i" id="c_key" value="${v('supa_key')}" autocapitalize="off">
    <label class="f">E-mail du compte boutique</label><input class="i" id="c_mail" type="email" value="${v('supa_email')}" autocapitalize="off">
    <label class="f">Mot de passe du compte boutique</label><input class="i" id="c_pw" type="password" value="${v('supa_password')}">
    <div class="row2"><div><label class="f">Numéro de ce téléphone (1 à 9)</label><input class="i" id="c_no" inputmode="numeric" value="${esc(DB.getMeta('device_no', '1'))}"></div>
    <div><label class="f">Nom de l’appareil</label><input class="i" id="c_name" value="${esc(DB.getMeta('device_name', 'TEL-1'))}"></div></div>
    <div class="hint">Chaque téléphone doit avoir un numéro différent (1 pour le premier, 2 pour le second…).</div>`;
}
function saveCloudFields() {
  const n = parseInt($('#c_no').value, 10);
  if (!(n >= 1 && n <= 9)) throw new Error('Le numéro du téléphone doit être compris entre 1 et 9.');
  const old = DB.getMeta('device_no', '');
  if (old && String(old) !== String(n) && DB.hasData()) throw new Error('Le numéro de ce téléphone ne peut plus être changé après la mise en service (risque de doublons). Utilisez « Réinitialiser ce téléphone » si nécessaire.');
  DB.setMeta('supa_url', $('#c_url').value.trim()); DB.setMeta('supa_key', $('#c_key').value.trim());
  DB.setMeta('supa_email', $('#c_mail').value.trim()); DB.setMeta('supa_password', $('#c_pw').value);
  DB.setMeta('device_no', String(n)); DB.setMeta('device_name', $('#c_name').value.trim() || 'TEL-' + n);
}
function secureReset() {
  if (App.user.role !== 'Administrateur') return errorBox('Seul un Administrateur peut réinitialiser les données.', 'Sécurité');
  openSheet({
    title: 'Réinitialisation des données',
    body: `<div class="info bad">Toutes les données commerciales (produits, stocks, ventes, clients, crédits, dépenses…) seront supprimées sur ce téléphone, puis sur le PC et les autres téléphones à la prochaine synchronisation.</div>
      <label class="f">Mot de passe administrateur actuel</label><input class="i" type="password" id="pw">
      <label class="f">Tapez RESET AMUNTCHI pour confirmer</label><input class="i" id="cf" autocapitalize="characters">`,
    buttons: [{ label: 'Annuler', cls: 'grey', onClick: a => a.close() }, {
      label: 'Confirmer', cls: 'red', onClick: async a => {
        const me = DB.get('users', App.user.id);
        if (!me || me.role !== 'Administrateur' || !await verifyPassword($('#pw', a.el).value, me.password)) return errorBox('Mot de passe administrateur incorrect.', 'Sécurité');
        if ($('#cf', a.el).value.trim() !== 'RESET AMUNTCHI') return errorBox('Texte de confirmation incorrect.', 'Sécurité');
        const tables = ['movements', 'purchase_lines', 'purchases', 'sale_lines', 'invoices', 'installments', 'credits', 'payments', 'expenses', 'transfer_lines', 'transfers', 'inventory_lines', 'inventories', 'photos', 'products', 'suppliers', 'clients', 'categories', 'conversions'];
        for (const t of tables) for (const id of [...DB.t[t].keys()]) DB.remove(t, id);
        DB.recomputeStocks();
        App.audit('Réinitialisation sécurisée', '', 'Données commerciales supprimées');
        a.close(); await alertBox('Réinitialisation terminée.', 'Réinitialisation'); App.home();
      },
    }],
  });
}

/* ======================= Utilisateurs ======================= */
Screens.users = function () {
  if (App.user.role !== 'Administrateur') return App.home();
  const rows = DB.all('users').sort(byName);
  render(headHTML('Utilisateurs & rôles', 'Comptes et permissions') +
    `<div class="bar"><button class="btn primary grow" id="new">+ Utilisateur</button></div>
    <div class="info">Touchez un utilisateur pour le renommer, changer son rôle, son statut, son mot de passe ou le supprimer. Seul l’administrateur accède à toutes les fonctionnalités.</div>
    ${tableHTML(['Nom', 'Identifiant', 'Rôle', 'Statut', 'Créé le'], rows.map(u => [u.name, u.username, u.role, Number(u.active) ? 'Actif' : 'Inactif', u.created_at || '']), { keys: rows.map(u => u.id) })}`);
  $('#new').onclick = userForm;
  onRows(main(), k => userActions(Number(k)));
};
function userActions(uid) {
  const u = DB.get('users', uid); if (!u) return;
  openSheet({
    title: `${u.name} (${u.username})`,
    body: `<div class="info">Rôle : ${esc(u.role)} — ${Number(u.active) ? 'Actif' : 'Inactif'}</div>
      <button class="btn purple block" id="ed">Renommer / modifier</button><div style="height:8px"></div>
      <button class="btn green block" id="st">Modifier le statut</button><div style="height:8px"></div>
      <button class="btn blue block" id="rp">Réinitialiser le mot de passe</button><div style="height:8px"></div>
      <button class="btn red block" id="dl">Supprimer l’utilisateur</button>`,
    buttons: [{ label: 'Fermer', cls: 'grey', onClick: a => a.close() }],
  });
  const top = Sheets.list[Sheets.list.length - 1], close = () => top.close();
  $('#ed', top.el).onclick = () => { close(); editUser(uid); };
  $('#st', top.el).onclick = () => { close(); changeUserStatus(uid); };
  $('#rp', top.el).onclick = () => { close(); resetUserPassword(uid); };
  $('#dl', top.el).onclick = () => { close(); deleteUser(uid); };
}
const activeAdmins = (exceptId = null) => DB.filter('users', x => x.role === 'Administrateur' && Number(x.active) === 1 && x.id !== exceptId).length;
async function changeUserStatus(uid) {
  const u = DB.get('users', uid);
  if (Number(u.active) && u.role === 'Administrateur' && activeAdmins() <= 1) return alertBox('Impossible de désactiver le dernier administrateur actif.', 'Statut');
  const na = Number(u.active) ? 0 : 1, lib = na ? 'Actif' : 'Inactif';
  if (!await confirmBox(`Voulez-vous rendre le compte « ${u.name} » ${lib.toLowerCase()} ?`, 'Modifier le statut')) return;
  DB.update('users', uid, { active: na }); App.audit(na ? 'Activation utilisateur' : 'Désactivation utilisateur', u.username, 'Statut: ' + lib);
  App.go('users'); alertBox(`Le compte « ${u.name} » est maintenant ${lib}.`, 'Statut');
}
function editUser(uid) {
  const u = DB.get('users', uid);
  openSheet({
    title: 'Modifier le compte',
    body: `<div class="info">Compte n° ${no(u.id)} — créé le ${esc(u.created_at || '')}</div>
      <label class="f">Nom affiché (ex. : Salifou Yahaya)</label><input class="i" id="n" value="${esc(u.name)}">
      <label class="f">Identifiant de connexion</label><input class="i" id="u" value="${esc(u.username)}" autocapitalize="off">
      <label class="f">Rôle</label>${selectHTML('r', ROLES, u.role)}
      <div class="hint">Administrateur : toutes les fonctionnalités. Vendeur : Produits (boutique), Ventes / caisse et Clients & crédits. Utilisateur : Ventes / caisse et Clients & crédits.</div>
      <label class="check"><input type="checkbox" id="a" ${Number(u.active) ? 'checked' : ''}>Compte actif</label>`,
    buttons: [{ label: 'Annuler', cls: 'grey', onClick: a => a.close() }, {
      label: 'Valider et enregistrer', cls: 'green', onClick: async s => {
        const nom = $('#n', s.el).value.trim(), ids = $('#u', s.el).value.trim(), role = $('#r', s.el).value, act = $('#a', s.el).checked ? 1 : 0;
        if (!nom) return errorBox('Veuillez saisir le nom affiché.', 'Compte');
        if (!ids) return errorBox('Veuillez saisir l’identifiant de connexion.', 'Compte');
        if (DB.find('users', x => x.id !== u.id && String(x.username).toLowerCase() === ids.toLowerCase())) return errorBox(`L’identifiant « ${ids} » est déjà pris.`, 'Identifiant déjà utilisé');
        if (u.role === 'Administrateur' && (role !== 'Administrateur' || !act) && activeAdmins(u.id) < 1) return errorBox('Impossible : ce compte est le dernier administrateur actif.', 'Compte');
        const ch = [];
        if (nom !== u.name) ch.push(`Nom affiché : ${u.name} -> ${nom}`);
        if (ids !== u.username) ch.push(`Identifiant : ${u.username} -> ${ids}`);
        if (role !== u.role) ch.push(`Rôle : ${u.role} -> ${role}`);
        if (act !== Number(u.active)) ch.push('Statut : ' + (act ? 'Actif' : 'Inactif'));
        if (!ch.length) return alertBox('Aucune modification à enregistrer.', 'Compte');
        if (!await confirmBox('Voulez-vous enregistrer les modifications suivantes ?\n\n- ' + ch.join('\n- '), 'Valider les modifications')) return;
        DB.update('users', u.id, { name: nom, username: ids, role, active: act });
        App.audit('Modification utilisateur', `${u.name} / ${u.username} / ${u.role}`, `${nom} / ${ids} / ${role}`);
        s.close();
        if (App.user.id === u.id) { App.user = { ...DB.get('users', u.id) }; App.buildDrawer(); }
        App.go('users'); alertBox('Modifications enregistrées :\n\n- ' + ch.join('\n- '), 'Compte');
      },
    }],
  });
}
async function deleteUser(uid) {
  const u = DB.get('users', uid);
  if (App.user.id === u.id) return alertBox('Vous ne pouvez pas supprimer le compte avec lequel vous êtes connecté.', 'Suppression');
  if (u.role === 'Administrateur' && DB.filter('users', x => x.role === 'Administrateur' && x.id !== u.id).length < 1) return alertBox('Impossible de supprimer le dernier administrateur.', 'Suppression');
  if (!await confirmBox(`Supprimer définitivement le compte « ${u.name} » (identifiant ${u.username}) ?\n\nLe compte ne pourra plus se connecter. Les ventes, achats, paiements et autres opérations déjà enregistrés sont conservés dans l’historique.`, 'Supprimer définitivement', 'Supprimer', 'Annuler', true)) return;
  for (const t of USER_LINKED_TABLES) for (const r of DB.filter(t, x => x.user_id === u.id)) DB.update(t, r.id, { user_id: null });
  DB.remove('users', u.id); App.audit('Suppression utilisateur', `${u.name} / ${u.username} / ${u.role}`, 'Compte supprimé');
  App.go('users'); alertBox(`Le compte « ${u.name} » a été supprimé définitivement.`, 'Suppression');
}
function userForm() {
  openSheet({
    title: 'Créer un utilisateur',
    body: `<label class="f">Nom complet</label><input class="i" id="n"><label class="f">Identifiant</label><input class="i" id="u" autocapitalize="off">
      <label class="f">Mot de passe</label><input class="i" type="password" id="p"><label class="f">Confirmer le mot de passe</label><input class="i" type="password" id="p2">
      <label class="f">Rôle</label>${selectHTML('r', ROLES, 'Vendeur')}<label class="check"><input type="checkbox" id="a" checked>Compte actif</label>`,
    buttons: [{ label: 'Annuler', cls: 'grey', onClick: a => a.close() }, {
      label: 'Enregistrer', cls: 'primary', onClick: async s => {
        const g = id => $('#' + id, s.el).value, nom = g('n').trim(), ident = g('u').trim(), pw = g('p');
        if (!nom) return errorBox('Veuillez saisir le nom complet.', 'Utilisateur');
        if (!ident) return errorBox('Veuillez saisir un identifiant.', 'Utilisateur');
        if (!pw) return errorBox('Veuillez saisir un mot de passe.', 'Utilisateur');
        if (pw !== g('p2')) return errorBox('Les deux mots de passe ne correspondent pas.', 'Utilisateur');
        const f = passwordStrengthError(pw); if (f) return errorBox(f, 'Mot de passe');
        if (DB.find('users', x => String(x.username).toLowerCase() === ident.toLowerCase())) return errorBox(`L’identifiant « ${ident} » est déjà utilisé.\nVeuillez choisir un autre identifiant.`, 'Identifiant déjà utilisé');
        DB.insert('users', { name: nom, username: ident, password: await hashPassword(pw), role: g('r'), photo: '', active: $('#a', s.el).checked ? 1 : 0, created_at: nowStr() });
        App.audit('Création utilisateur', '', ident); s.close(); App.go('users');
        alertBox(`Le compte « ${nom} » a été créé avec succès.\nRôle : ${g('r')}`, 'Utilisateur');
      },
    }],
  });
}
function resetUserPassword(uid) {
  const t = DB.get('users', uid);
  openSheet({
    title: 'Réinitialiser un mot de passe',
    body: `<div class="info"><b>Compte : ${esc(t.name)} (${esc(t.username)})</b></div>
      <label class="f">Votre mot de passe administrateur</label><input class="i" type="password" id="a">
      <label class="f">Nouveau mot de passe du compte</label><input class="i" type="password" id="n"><label class="f">Confirmer</label><input class="i" type="password" id="c">`,
    buttons: [{ label: 'Annuler', cls: 'grey', onClick: a => a.close() }, {
      label: 'Valider', cls: 'blue', onClick: async s => {
        const me = DB.get('users', App.user.id);
        if (!me || !await verifyPassword($('#a', s.el).value, me.password)) return errorBox('Mot de passe administrateur incorrect.', 'Sécurité');
        if ($('#n', s.el).value !== $('#c', s.el).value) return errorBox('Les deux mots de passe ne correspondent pas.', 'Sécurité');
        const f = passwordStrengthError($('#n', s.el).value); if (f) return errorBox(f, 'Mot de passe');
        DB.update('users', uid, { password: await hashPassword($('#n', s.el).value) });
        App.audit('Réinitialisation mot de passe', t.username, 'Nouveau mot de passe défini');
        s.close(); alertBox(`Mot de passe du compte « ${t.name} » réinitialisé.`, 'Sécurité');
      },
    }],
  });
}
function changeOwnPassword() {
  openSheet({
    title: 'Changer mon mot de passe',
    body: `<label class="f">Mot de passe actuel</label><input class="i" type="password" id="o"><label class="f">Nouveau mot de passe</label><input class="i" type="password" id="n">
      <label class="f">Confirmer le nouveau mot de passe</label><input class="i" type="password" id="c">`,
    buttons: [{ label: 'Annuler', cls: 'grey', onClick: a => a.close() }, {
      label: 'Enregistrer', cls: 'primary', onClick: async s => {
        const me = DB.get('users', App.user.id);
        if (!me || !await verifyPassword($('#o', s.el).value, me.password)) return errorBox('Mot de passe actuel incorrect.', 'Sécurité');
        if ($('#n', s.el).value !== $('#c', s.el).value) return errorBox('Les deux nouveaux mots de passe ne correspondent pas.', 'Sécurité');
        const f = passwordStrengthError($('#n', s.el).value); if (f) return errorBox(f, 'Mot de passe');
        DB.update('users', App.user.id, { password: await hashPassword($('#n', s.el).value) });
        App.audit('Changement mot de passe', App.user.username, 'PBKDF2-SHA256');
        s.close(); alertBox('Mot de passe modifié avec succès.', 'Sécurité');
      },
    }],
  });
}

/* ======================= Journal d'audit ======================= */
Screens.audit = function () {
  const rows = DB.all('audit').sort(byDateDesc).slice(0, 500);
  render(headHTML('Journal d’audit', 'Traçabilité des opérations sensibles (PC et téléphones)') +
    tableHTML(['Date/heure', 'Utilisateur', 'Action', 'Ancienne valeur', 'Nouvelle valeur', 'Motif'],
      rows.map(a => [a.date, userName(a.user_id), a.action, a.old_value || '', a.new_value || '', a.reason || '']), { empty: 'Journal vide.', wrapCols: [2] }));
};
