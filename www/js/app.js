/* AMUNTCHI Android — démarrage, configuration, connexion, menu, synchronisation automatique. */
'use strict';

const Screens = {};
const MENUS = [
  ['Tableau de bord', 'dashboard', 'dashboard', 'TB'], ['Produits (boutique)', 'products', 'products', 'PRD'],
  ['Stock (magasin)', 'stock', 'stock', 'STK'], ['Transferts', 'transfers', 'transfers', 'TRF'],
  ['Achats / réceptions', 'purchases', 'purchases', 'ACH'], ['Ventes / caisse', 'sales', 'sales', 'VTE'],
  ['Clients & crédits', 'clients', 'clients', 'CLI'], ['Dépenses', 'expenses', 'expenses', 'DEP'],
  ['Inventaires', 'inventory', 'inventory', 'INV'], ['Rapports', 'reports', 'reports', 'RAP'], ['Paramètres', 'settings', 'admin', 'PAR'],
];
const TITLES = Object.fromEntries(MENUS.map(m => [m[1], m[0]]).concat([['users', 'Utilisateurs'], ['audit', 'Journal d’audit']]));

const App = {
  user: null, screen: null, cart: [], memo: {},
  allowed(p) { const s = PERMS[(App.user || {}).role] || []; return s.includes('all') || s.includes(p); },
  audit(action, old = '', nw = '', reason = '') {
    if (App.user) DB.insert('audit', { user_id: App.user.id, action, date: nowStr(), old_value: String(old ?? ''), new_value: String(nw ?? ''), reason: String(reason ?? '') });
  },
  invoicePrefix() { return DB.getMeta('invoice_prefix', '') || `AM-T${DB.deviceNo()}-`; },
  invoiceNumber() {
    let n = parseInt(DB.getMeta('invoice_next', '1'), 10) || 1, num;
    do { num = App.invoicePrefix() + String(n).padStart(6, '0'); n++; } while (DB.find('invoices', i => i.number === num));
    DB.setMeta('invoice_next', String(n)); return num;
  },
  firstScreen() { const m = MENUS.find(m => App.allowed(m[2])); return m ? m[1] : 'sales'; },
  home() { App.go(App.firstScreen()); },
  go(name) {
    Sheets.closeAll(); App.closeDrawer();
    if (!Screens[name]) name = App.firstScreen();
    const menu = MENUS.find(m => m[1] === name);
    if (menu && !App.allowed(menu[2])) name = App.firstScreen();
    App.screen = name;
    $('#ttl').textContent = TITLES[name] || 'AMUNTCHI';
    $$('.drawer a[data-s]').forEach(a => a.classList.toggle('active', a.dataset.s === name));
    try { Screens[name](); } catch (e) { console.error(e); errorBox(e); }
  },
  /* Réaffiche l'écran après réception de données (sauf caisse ou fenêtre ouverte) */
  refreshAfterSync() {
    if (!App.user || Sheets.list.length || App.screen === 'sales') return;
    const ae = document.activeElement; if (ae && ae.tagName === 'INPUT' && ae.value) return;
    App.go(App.screen);
  },

  /* ---------- interface principale ---------- */
  shell() {
    document.body.innerHTML = `<div class="topbar"><button class="iconbtn" id="menu" aria-label="Menu">☰</button><div class="ttl" id="ttl">AMUNTCHI</div>
      <button class="chip" id="chip"><span class="dot"></span><span class="t">Cloud</span></button></div>
      <div class="scrim" id="scrim"></div><nav class="drawer" id="drawer"></nav><main id="main"></main>`;
    $('#menu').onclick = App.openDrawer; $('#scrim').onclick = App.closeDrawer;
    $('#chip').onclick = () => App.syncNow(true).catch(e => errorBox(e, 'Synchronisation'));
    App.buildDrawer(); App.updateChip();
  },
  buildDrawer() {
    const u = App.user;
    let h = `<div class="brand"><img src="img/logo.jpg" alt=""><div class="n">AMUNTCHI</div><div class="u">${esc(u.name)}</div><div class="r">${esc(u.role)}</div></div>`;
    for (const [label, s, perm, ic] of MENUS) if (App.allowed(perm)) h += `<a href="#" data-s="${s}"><span class="ic">${ic}</span>${esc(label)}</a>`;
    if (u.role === 'Administrateur') h += `<a href="#" class="sub" data-s="users">Utilisateurs</a><a href="#" class="sub" data-s="audit">Journal d’audit</a>`;
    h += `<a href="#" id="mypw"><span class="ic">MDP</span>Mon mot de passe</a><a href="#" id="sync"><span class="ic">SYN</span>Synchroniser</a><a href="#" class="logout" id="out"><span class="ic">⏻</span>Déconnexion</a>`;
    $('#drawer').innerHTML = h;
    $$('#drawer a[data-s]').forEach(a => a.onclick = e => { e.preventDefault(); App.go(a.dataset.s); });
    $('#mypw').onclick = e => { e.preventDefault(); App.closeDrawer(); changeOwnPassword(); };
    $('#sync').onclick = e => { e.preventDefault(); App.closeDrawer(); App.syncNow(true).catch(err => errorBox(err, 'Synchronisation')); };
    $('#out').onclick = e => { e.preventDefault(); App.logout(); };
  },
  openDrawer() { $('#drawer').classList.add('open'); $('#scrim').classList.add('show'); },
  closeDrawer() { const d = $('#drawer'); if (d) { d.classList.remove('open'); $('#scrim').classList.remove('show'); } },
  updateChip() {
    const c = $('#chip'); if (!c) return;
    const n = DB.pendingCount();
    let cls = '', txt;
    if (!Cloud.configured()) { cls = 'off'; txt = 'Cloud non configuré'; }
    else if (Cloud.busy) { cls = 'busy'; txt = 'Synchro…'; }
    else if (Cloud.lastError) { cls = 'off'; txt = n ? `Hors ligne · ${n} en attente` : 'Hors ligne'; }
    else if (n) { cls = 'wait'; txt = `${n} en attente`; }
    else txt = Cloud.lastOk ? `À jour ${Cloud.lastOk.slice(11, 16)}` : 'Cloud';
    c.className = 'chip ' + cls; $('.t', c).textContent = txt;
  },

  /* ---------- synchronisation ---------- */
  async syncNow(manual = false) {
    if (!Cloud.configured()) { if (manual) throw new Error('Configurez d’abord le Cloud (Paramètres).'); return; }
    if (Cloud.busy) { if (manual) toast('Synchronisation déjà en cours…'); return; }
    try {
      const r = await Cloud.sync();
      if (!r) return;
      if (r.received) App.refreshAfterSync();
      if (manual) toast(`Synchronisation terminée : ${r.received} reçu(s), ${r.sent} envoyé(s).`, 3500);
    } catch (e) {
      if (manual) throw new Error(`Synchronisation impossible :\n${e.message}\n\nLes données restent enregistrées sur ce téléphone et seront envoyées plus tard.`);
    } finally { App.updateChip(); }
  },
  scheduleSync() {
    let lastTry = 0, quickTimer = null;
    const auto = () => DB.getMeta('cloud_auto', '1') === '1' && Cloud.configured() && App.user;
    const run = () => { lastTry = Date.now(); App.syncNow(false); };
    setInterval(() => {
      if (!auto() || Cloud.busy) return;
      const wait = Cloud.lastError ? 60e3 : 120e3;
      if (Date.now() - lastTry >= wait) run();
    }, 10e3);
    DB.listeners.push(kind => {
      App.updateChip();
      if (kind === 'local' && auto()) { clearTimeout(quickTimer); quickTimer = setTimeout(run, 15e3); }
    });
    window.addEventListener('online', () => { if (auto()) run(); });
    document.addEventListener('visibilitychange', () => { if (!document.hidden && auto() && Date.now() - lastTry > 30e3) run(); });
    if (auto()) setTimeout(run, 1500);
  },

  /* ---------- connexion ---------- */
  login() {
    App.user = null; Sheets.closeAll();
    document.body.innerHTML = `<div class="login"><div class="box"><img src="img/logo.jpg" alt=""><h1>AMUNTCHI</h1><div class="v">Gestion de quincaillerie — Android V${APP_VERSION}</div>
      <label class="f">Identifiant</label><input class="i" id="u" autocapitalize="off" autocomplete="username">
      <label class="f">Mot de passe</label><input class="i" id="p" type="password" autocomplete="current-password">
      <button class="btn primary block" id="go" style="margin-top:18px;min-height:52px">SE CONNECTER</button>
      <div class="foot">Mêmes comptes que sur le PC.<br><a href="#" id="resync">Mettre à jour depuis le Cloud</a></div></div></div>`;
    const go = async () => {
      const ident = $('#u').value.trim(), pwd = $('#p').value;
      const r = DB.find('users', x => String(x.username).toLowerCase() === ident.toLowerCase() && Number(x.active) === 1);
      $('#go').disabled = true; $('#go').textContent = 'Vérification…';
      const ok = r && await verifyPassword(pwd, r.password);
      $('#go').disabled = false; $('#go').textContent = 'SE CONNECTER';
      if (!ok) return errorBox('Identifiant ou mot de passe incorrect', 'Connexion');
      if (!isHashed(r.password)) DB.update('users', r.id, { password: await hashPassword(pwd) });
      App.user = { ...DB.get('users', r.id) };
      App.audit('Connexion (Android)');
      App.shell(); App.home();
      if (pwd === 'admin' && r.username === 'admin') alertBox('Vous utilisez encore le mot de passe par défaut.\nModifiez-le dans « Mon mot de passe ».', 'Sécurité');
    };
    $('#go').onclick = go; $('#p').addEventListener('keydown', e => { if (e.key === 'Enter') go(); });
    $('#resync').onclick = async e => {
      e.preventDefault();
      try { await Cloud.sync(); toast('Données mises à jour depuis le Cloud.'); } catch (err) { errorBox(err, 'Cloud'); }
    };
  },
  logout() { App.user = null; App.login(); },

  /* ---------- première mise en service ---------- */
  setup() {
    document.body.innerHTML = `<div class="login"><div class="box" style="max-width:560px"><img src="img/logo.jpg" alt="" style="width:110px"><h1 style="font-size:24px">Mise en service</h1>
      <div class="v">Reliez ce téléphone au Cloud de la boutique pour récupérer les produits, les stocks et les comptes du PC.</div>
      ${cloudFieldsHTML()}
      <button class="btn green block" id="go" style="margin-top:18px;min-height:52px">CONNECTER ET TÉLÉCHARGER LES DONNÉES</button>
      <div class="info" id="st" style="margin-top:10px"></div>
      <div class="foot">Ces informations figurent dans le guide d’installation (projet Supabase de la boutique).<br>Le PC doit avoir été synchronisé au moins une fois.</div></div></div>`;
    $('#go').onclick = async () => {
      const st = $('#st');
      try {
        saveCloudFields();
        $('#go').disabled = true; st.className = 'info'; st.textContent = 'Connexion au Cloud…';
        await Cloud.login();
        st.textContent = 'Téléchargement des données…';
        const r = await Cloud.sync();
        if (!DB.hasData()) throw new Error('Le Cloud ne contient encore aucune donnée.\nLancez d’abord « Synchroniser maintenant » sur le PC (Paramètres), puis réessayez.');
        DB.setMeta('setup_done', '1'); await DB.flush();
        await alertBox(`Mise en service réussie.\n${r.received} enregistrement(s) reçu(s) du Cloud.\nConnectez-vous avec vos identifiants habituels.`, 'Cloud');
        App.login(); App.scheduleSync();
      } catch (e) { st.className = 'info bad'; st.textContent = e.message; }
      finally { const b = $('#go'); if (b) b.disabled = false; }
    };
  },

  async boot() {
    try { await DB.load(); } catch (e) { document.body.innerHTML = `<div class="login"><div class="box">Erreur de stockage : ${esc(e.message)}</div></div>`; return; }
    const keep = localStorage.getItem('amx_keep');
    if (keep) { try { Object.entries(JSON.parse(keep)).forEach(([k, v]) => v !== '' && DB.setMeta(k, v)); } catch (e) {} localStorage.removeItem('amx_keep'); }
    if (navigator.storage && navigator.storage.persist) navigator.storage.persist().catch(() => {});
    App.backButton();
    if (DB.getMeta('setup_done', '') !== '1' || !DB.hasData()) return App.setup();
    App.login(); App.scheduleSync();
  },
  backButton() {
    const A = capPlugin('App');
    if (!A) return;
    A.addListener('backButton', () => {
      if (Sheets.closeTop()) return;
      if ($('#drawer') && $('#drawer').classList.contains('open')) return App.closeDrawer();
      if (App.user && App.screen && App.screen !== App.firstScreen()) return App.home();
      A.minimizeApp().catch(() => A.exitApp());
    });
    A.addListener('resume', () => { if (App.user && Cloud.configured() && DB.getMeta('cloud_auto', '1') === '1') App.syncNow(false); });
  },
};

document.addEventListener('DOMContentLoaded', () => App.boot());
