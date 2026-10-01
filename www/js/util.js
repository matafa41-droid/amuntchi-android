/* AMUNTCHI Android — utilitaires communs (mêmes règles que le logiciel PC). */
'use strict';

const APP_VERSION = '5.0';
const COMM = {
  name: 'Mini Quincaillerie AMUNTCHI',
  rccm: 'RCCM/NE/AGA/2020/A/124',
  address: 'Tchirozerine',
  phone: '+227 96105518 / 94206364',
  email: 'quincaillerieamuntchi@gmail.com',
};
const ROLES = ['Administrateur', 'Responsable magasin', 'Vendeur', 'Caissier', 'Utilisateur', 'Consultation'];
const PERMS = {
  'Administrateur': ['all'],
  'Responsable magasin': ['dashboard', 'products', 'stock', 'purchases', 'transfers', 'inventory', 'reports'],
  'Vendeur': ['products', 'sales', 'clients', 'invoices', 'payments'],
  'Caissier': ['dashboard', 'sales', 'clients', 'payments', 'expenses'],
  'Utilisateur': ['sales', 'clients', 'invoices', 'payments'],
  'Consultation': ['dashboard', 'products', 'stock', 'clients', 'reports'],
};
const USER_LINKED_TABLES = ['movements', 'purchases', 'sales', 'payments', 'expenses', 'transfers', 'inventories', 'audit'];
const PAYMENT_MODES = ['Espèces', 'Virement', 'Mobile Money', 'Autre'];
const MAGASIN = 'Magasin';
const BOUTIQUE = 'Boutique';
const FR_DAYS = ['Dimanche', 'Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi'];
const FR_MONTHS = ['Janvier', 'Février', 'Mars', 'Avril', 'Mai', 'Juin', 'Juillet', 'Août', 'Septembre', 'Octobre', 'Novembre', 'Décembre'];

const pad = (n, w = 2) => String(n).padStart(w, '0');
function isoDate(d) { return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; }
function nowStr() { const d = new Date(); return `${isoDate(d)} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`; }
function today() { return isoDate(new Date()); }
function addDays(dateStr, n) { const d = new Date(dateStr + 'T12:00:00'); d.setDate(d.getDate() + n); return isoDate(d); }
function frenchDate(d = new Date()) { return `${FR_DAYS[d.getDay()]} ${pad(d.getDate())} ${FR_MONTHS[d.getMonth()]} ${d.getFullYear()}`; }

/* 1250.5 -> "1 251 FCFA" (même affichage que le PC) */
function money(v) {
  const n = Math.round(Number(v) || 0);
  const s = String(Math.abs(n)).replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
  return (n < 0 ? '-' : '') + s + ' FCFA';
}
/* Quantité au format court (équivalent de :g) */
function q(v) {
  const n = Number(v) || 0;
  const r = Math.round(n * 1e6) / 1e6;
  return String(r);
}
function qs(v) { const n = Number(v) || 0; return (n > 0 ? '+' : '') + q(n); }
/* Conversion tolérante : « 1 250,50 » -> 1250.5 */
function nfloat(v) {
  const s = String(v ?? '').replace(/[\s ]/g, '').replace(',', '.');
  if (s === '') return 0;
  const n = Number(s);
  if (!isFinite(n)) throw new Error(`Nombre invalide : « ${v} »`);
  return n;
}
function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function tsUTC() { return new Date().toISOString(); }
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

/* ------------------------------------------------------------------ */
/* Recherche produits par famille (copie fidèle de core.smart_search)  */
/* ------------------------------------------------------------------ */
const UNIT_ALIASES = { watts: 'w', watt: 'w', volts: 'v', volt: 'v', amperes: 'a', ampere: 'a', litres: 'l', litre: 'l', metres: 'm', metre: 'm', kilos: 'kg', kilo: 'kg' };
const UNIT_RE = new RegExp('\\b(' + Object.keys(UNIT_ALIASES).join('|') + ')\\b', 'g');

function normTokens(text) {
  let s = String(text ?? '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '');
  s = s.replace(/(\d),(\d)/g, '$1.$2');
  s = s.replace(/[^a-z0-9.]+/g, ' ');
  s = s.replace(/(?<!\d)\.|\.(?!\d)/g, ' ');
  s = s.replace(UNIT_RE, m => UNIT_ALIASES[m]);
  s = s.replace(/(\d)\s+(w|kw|v|a|mm|cm|m|l|ml|kg|g)\b/g, '$1$2');
  return s.split(/\s+/).filter(Boolean);
}
function singular(t) {
  if (t.length > 3 && !/\d/.test(t) && /[sx]$/.test(t)) return t.slice(0, -1);
  return t;
}
const isWord = t => t.length >= 2 && /^[a-z]+$/.test(t);
function productFamily(name) {
  for (const t of normTokens(name)) if (isWord(t)) return singular(t);
  return '';
}
function tokMatch(qt, toks) {
  if (/\d/.test(qt)) return toks.includes(qt);
  const s = singular(qt);
  return toks.some(t => t.startsWith(s) || singular(t) === s);
}
const cap = s => s.charAt(0).toUpperCase() + s.slice(1);

/* rows : objets avec name, brand, code, category. Retourne {rows, note}. */
function smartSearch(rows, term) {
  rows = [...rows];
  const raw = String(term ?? '').trim();
  if (!raw) return { rows, note: '' };
  const exact = rows.filter(r => String(r.code ?? '').trim().toLowerCase() === raw.toLowerCase());
  if (exact.length) return { rows: exact, note: `Référence « ${raw} » trouvée.` };
  const qtoks = normTokens(raw);
  if (!qtoks.length) return { rows, note: '' };
  const toksOf = r => normTokens(['name', 'brand', 'code', 'category'].map(k => r[k] ?? '').join(' '));
  const allMatch = (r, toks) => { const pt = toksOf(r); return toks.every(x => tokMatch(x, pt)); };
  const words = qtoks.filter(isWord);
  if (words.length) {
    const head = singular(words[0]);
    const rest = qtoks.filter(t => t !== words[0]);
    let fam = rows.filter(r => productFamily(r.name) === head);
    if (!fam.length && head.length >= 3) fam = rows.filter(r => productFamily(r.name).startsWith(head));
    let label = null;
    if (fam.length) {
      label = [...new Set(fam.map(r => productFamily(r.name)))].sort().map(cap).join(', ');
    } else {
      fam = rows.filter(r => normTokens(r.category).some(t => singular(t).startsWith(head)));
      if (fam.length) label = 'catégorie ' + [...new Set(fam.map(r => r.category))].sort().join(', ');
    }
    if (fam.length) {
      if (!rest.length) return { rows: fam, note: `Famille : ${label} — ${fam.length} article(s).` };
      const hits = fam.filter(r => allMatch(r, rest));
      if (hits.length) return { rows: hits, note: `Famille : ${label} — ${hits.length} article(s) correspondant à « ${rest.join(' ')} ».` };
      return { rows: fam, note: `Aucun article « ${raw} ». Affichage de toute la famille ${label} (${fam.length} article(s)).` };
    }
  }
  const hits = rows.filter(r => allMatch(r, qtoks));
  return { rows: hits, note: hits.length ? `${hits.length} article(s) contenant « ${raw} ».` : `Aucun article pour « ${raw} ».` };
}

/* ------------------------------------------------------------------ */
/* Mots de passe : PBKDF2-SHA256, même format que le PC                */
/* ------------------------------------------------------------------ */
const PBKDF2_ITER = 200000;
const HASH_PREFIX = 'pbkdf2_sha256$';
const hex = buf => [...new Uint8Array(buf)].map(b => b.toString(16).padStart(2, '0')).join('');
const unhex = h => new Uint8Array((h.match(/.{2}/g) || []).map(x => parseInt(x, 16)));
async function pbkdf2Hex(password, saltHex, iterations) {
  const key = await crypto.subtle.importKey('raw', new TextEncoder().encode(password), 'PBKDF2', false, ['deriveBits']);
  const bits = await crypto.subtle.deriveBits({ name: 'PBKDF2', hash: 'SHA-256', salt: unhex(saltHex), iterations }, key, 256);
  return hex(bits);
}
async function hashPassword(pw) {
  const salt = hex(crypto.getRandomValues(new Uint8Array(16)));
  return `${HASH_PREFIX}${PBKDF2_ITER}$${salt}$${await pbkdf2Hex(pw, salt, PBKDF2_ITER)}`;
}
const isHashed = s => !!s && String(s).startsWith(HASH_PREFIX);
async function verifyPassword(pw, stored) {
  if (!stored) return false;
  stored = String(stored);
  if (isHashed(stored)) {
    try {
      const [, it, salt, digest] = stored.split('$');
      return (await pbkdf2Hex(pw, salt, parseInt(it, 10))) === digest;
    } catch (e) { return false; }
  }
  return pw === stored;
}
function passwordStrengthError(pw) {
  if (pw.length < 6) return 'Le mot de passe doit contenir au moins 6 caractères.';
  if (['admin', 'motdepasse', 'password', '123456', 'amuntchi'].includes(pw.toLowerCase())) return 'Ce mot de passe est trop courant. Choisissez-en un autre.';
  return null;
}

/* CSV avec séparateur « ; » et BOM (ouvrable dans Excel) */
function toCSV(rows) {
  const cell = v => { const s = String(v ?? ''); return /[;"\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; };
  return '﻿' + rows.map(r => r.map(cell).join(';')).join('\r\n');
}
function parseCSV(text) {
  text = text.replace(/^﻿/, '');
  const firstLine = text.split(/\r?\n/)[0] || '';
  const sep = (firstLine.split(';').length > firstLine.split(',').length) ? ';' : ',';
  const rows = []; let row = [], cur = '', inq = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (inq) {
      if (c === '"' && text[i + 1] === '"') { cur += '"'; i++; }
      else if (c === '"') inq = false; else cur += c;
    } else if (c === '"') inq = true;
    else if (c === sep) { row.push(cur); cur = ''; }
    else if (c === '\n' || c === '\r') {
      if (c === '\r' && text[i + 1] === '\n') i++;
      row.push(cur); rows.push(row); row = []; cur = '';
    } else cur += c;
  }
  if (cur !== '' || row.length) { row.push(cur); rows.push(row); }
  const head = (rows.shift() || []).map(h => h.trim());
  return rows.filter(r => r.some(x => x.trim() !== '')).map(r => Object.fromEntries(head.map((h, i) => [h, r[i] ?? ''])));
}
