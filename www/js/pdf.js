/* AMUNTCHI Android — petit générateur PDF (A4, Helvetica, lignes, image JPEG).
 * Reproduit la facture et le rapport du logiciel PC. */
'use strict';

class MiniPDF {
  constructor() {
    this.W = 595.2756; this.H = 841.8898;   // A4 en points
    this.pages = []; this.images = []; this.newPage();
    this.font = 'F1'; this.size = 10;
  }
  newPage() { this.cur = []; this.pages.push(this.cur); this.font = 'F1'; }
  setFont(bold, size) { this.font = bold ? 'F2' : 'F1'; this.size = size; }
  _enc(str) {
    let out = '';
    for (const ch of String(str ?? '')) {
      let c = ch.codePointAt(0);
      if (c >= 128) c = PDF_UMAP[c] || 63;          // caractère hors WinAnsi -> « ? »
      const s = String.fromCharCode(c);
      out += (s === '(' || s === ')' || s === '\\') ? '\\' + s : s;
    }
    return out;
  }
  width(str, bold = this.font === 'F2', size = this.size) {
    const w = PDF_WIDTHS[bold ? 'Helvetica-Bold' : 'Helvetica']; let t = 0;
    for (const ch of String(str ?? '')) { let c = ch.codePointAt(0); if (c >= 128) c = PDF_UMAP[c] || 63; t += w[c] || 556; }
    return t * size / 1000;
  }
  _f(n) { return (Math.round(n * 100) / 100).toString(); }
  text(x, y, s) { this.cur.push(`BT /${this.font} ${this._f(this.size)} Tf ${this._f(x)} ${this._f(y)} Td (${this._enc(s)}) Tj ET`); }
  textC(x, y, s) { this.text(x - this.width(s) / 2, y, s); }
  textR(x, y, s) { this.text(x - this.width(s), y, s); }
  fill(r, g, b) { this.cur.push(`${this._f(r)} ${this._f(g)} ${this._f(b)} rg`); }
  stroke(r, g, b) { this.cur.push(`${this._f(r)} ${this._f(g)} ${this._f(b)} RG`); }
  lineWidth(w) { this.cur.push(`${this._f(w)} w`); }
  line(x1, y1, x2, y2) { this.cur.push(`${this._f(x1)} ${this._f(y1)} m ${this._f(x2)} ${this._f(y2)} l S`); }
  rect(x, y, w, h, fill = false, stroke = true) {
    this.cur.push(`${this._f(x)} ${this._f(y)} ${this._f(w)} ${this._f(h)} re ${fill && stroke ? 'B' : fill ? 'f' : 'S'}`);
  }
  /* jpeg : Uint8Array ; l'image est ajustée dans le cadre en gardant ses proportions */
  image(jpeg, x, y, w, h) {
    const dim = MiniPDF.jpegSize(jpeg); if (!dim) return;
    const r = Math.min(w / dim.w, h / dim.h), dw = dim.w * r, dh = dim.h * r;
    const name = 'Im' + (this.images.length + 1);
    this.images.push({ name, data: jpeg, w: dim.w, h: dim.h, comps: dim.c });
    this.cur.push(`q ${this._f(dw)} 0 0 ${this._f(dh)} ${this._f(x + (w - dw) / 2)} ${this._f(y + (h - dh) / 2)} cm /${name} Do Q`);
  }
  static jpegSize(b) {
    let i = 2;
    while (i < b.length) {
      if (b[i] !== 0xFF) return null;
      const m = b[i + 1], len = (b[i + 2] << 8) + b[i + 3];
      if (m >= 0xC0 && m <= 0xCF && m !== 0xC4 && m !== 0xC8 && m !== 0xCC)
        return { h: (b[i + 5] << 8) + b[i + 6], w: (b[i + 7] << 8) + b[i + 8], c: b[i + 9] };
      i += 2 + len;
    }
    return null;
  }
  output() {
    const chunks = []; let len = 0; const offs = [];
    const push = (x) => { const u = typeof x === 'string' ? MiniPDF.latin1(x) : x; chunks.push(u); len += u.length; };
    const obj = (n, body) => { offs[n] = len; push(`${n} 0 obj\n`); if (typeof body === 'function') body(); else push(body); push('\nendobj\n'); };
    push('%PDF-1.4\n%\xE2\xE3\xCF\xD3\n');
    const nPages = this.pages.length, firstPage = 5, imgStart = firstPage + nPages * 2;
    const xobj = this.images.map((im, i) => `/${im.name} ${imgStart + i} 0 R`).join(' ');
    obj(1, '<< /Type /Catalog /Pages 2 0 R >>');
    obj(2, `<< /Type /Pages /Kids [${this.pages.map((_, i) => `${firstPage + i * 2} 0 R`).join(' ')}] /Count ${nPages} >>`);
    obj(3, '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>');
    obj(4, '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>');
    this.pages.forEach((ops, i) => {
      const n = firstPage + i * 2, content = ops.join('\n');
      obj(n, `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${this._f(this.W)} ${this._f(this.H)}] /Resources << /Font << /F1 3 0 R /F2 4 0 R >> /XObject << ${xobj} >> >> /Contents ${n + 1} 0 R >>`);
      obj(n + 1, () => { push(`<< /Length ${content.length} >>\nstream\n`); push(content); push('\nendstream'); });
    });
    this.images.forEach((im, i) => obj(imgStart + i, () => {
      const cs = im.comps === 1 ? '/DeviceGray' : im.comps === 4 ? '/DeviceCMYK' : '/DeviceRGB';
      push(`<< /Type /XObject /Subtype /Image /Width ${im.w} /Height ${im.h} /ColorSpace ${cs} /BitsPerComponent 8 /Filter /DCTDecode /Length ${im.data.length} >>\nstream\n`);
      push(im.data); push('\nendstream');
    }));
    const total = imgStart + this.images.length, xref = len;
    let x = `xref\n0 ${total}\n0000000000 65535 f \n`;
    for (let n = 1; n < total; n++) x += String(offs[n]).padStart(10, '0') + ' 00000 n \n';
    push(x + `trailer\n<< /Size ${total} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`);
    const out = new Uint8Array(len); let p = 0; for (const c of chunks) { out.set(c, p); p += c.length; }
    return out;
  }
  static latin1(s) { const u = new Uint8Array(s.length); for (let i = 0; i < s.length; i++) u[i] = s.charCodeAt(i) & 255; return u; }
}

let LOGO_BYTES = null;
async function logoBytes() {
  if (LOGO_BYTES) return LOGO_BYTES;
  try { LOGO_BYTES = new Uint8Array(await (await fetch('img/logo.jpg')).arrayBuffer()); } catch (e) { LOGO_BYTES = null; }
  return LOGO_BYTES;
}

/* Facture : même mise en page que make_invoice_pdf du PC. */
async function buildInvoicePDF(saleId) {
  const inv = DB.find('invoices', i => i.sale_id === saleId);
  const sale = DB.get('sales', saleId);
  if (!inv || !sale) throw new Error('Facture introuvable.');
  const client = sale.client_id != null ? DB.get('clients', sale.client_id) : null;
  const lines = DB.filter('sale_lines', l => l.sale_id === saleId).sort((a, b) => Math.abs(a.id) - Math.abs(b.id))
    .map(l => ({ ...l, name: (DB.get('products', l.product_id) || {}).name || '?' }));
  const pdf = new MiniPDF(), W = pdf.W, H = pdf.H;
  const logo = await logoBytes(); if (logo) pdf.image(logo, 35, H - 105, 105, 78);
  pdf.fill(0, 0, 0);
  pdf.setFont(true, 17); pdf.textC(W / 2 + 45, H - 45, COMM.name);
  pdf.setFont(false, 10); pdf.textC(W / 2 + 45, H - 62, COMM.rccm);
  pdf.textC(W / 2 + 45, H - 76, 'TEL : ' + COMM.phone);
  pdf.textC(W / 2 + 45, H - 90, 'Adresse Email : ' + COMM.email);
  pdf.setFont(true, 12); pdf.text(40, H - 125, 'FACTURE N° ' + inv.number);
  pdf.setFont(false, 9); pdf.textR(W - 40, H - 125, 'Date : ' + String(inv.date || sale.date || '').slice(0, 16));
  const x0 = 30, top = H - 150, tw = W - 60, colW = [45, 250, 90, 70, 80], hh = 30, rh = 24, nrows = 15;
  const heads = ['Item', 'Désignation', 'Prix Unitaire', 'Quantité', 'Prix Total'];
  pdf.lineWidth(0.8);
  pdf.fill(0.97, 0.97, 0.97); pdf.rect(x0, top - hh, tw, hh, true, false); pdf.fill(0, 0, 0);
  let xp = x0;
  for (const w of colW) { pdf.line(xp, top, xp, top - hh - nrows * rh); xp += w; }
  pdf.line(xp, top, xp, top - hh - nrows * rh);
  pdf.line(x0, top, x0 + tw, top); pdf.line(x0, top - hh, x0 + tw, top - hh);
  for (let i = 0; i < nrows; i++) { const yy = top - hh - (i + 1) * rh; pdf.line(x0, yy, x0 + tw, yy); }
  pdf.setFont(true, 10); xp = x0;
  heads.forEach((h, i) => { pdf.textC(xp + colW[i] / 2, top - 20, h); xp += colW[i]; });
  pdf.setFont(false, 8.8); let y = top - hh - 16;
  lines.slice(0, nrows).forEach((l, idx) => {
    const vals = [String(idx + 1), String(l.name).slice(0, 48), money(l.price), Number(l.qty).toFixed(2), money(l.qty * l.price)];
    xp = x0; vals.forEach((v, j) => { pdf.textC(xp + colW[j] / 2, y, v); xp += colW[j]; }); y -= rh;
  });
  const by = top - hh - nrows * rh, lx = x0 + colW[0] + colW[1] + colW[2], lw = colW[3], vx = lx + lw, vw = colW[4];
  pdf.setFont(true, 10);
  ['TOTAL', 'Payé', 'Reste à Payé'].forEach((lab, i) => { const yy = by - i * 24; pdf.line(lx, yy, lx + lw + vw, yy); pdf.textC(lx + lw / 2, yy - 17, lab); });
  pdf.line(lx, by - 72, lx + lw + vw, by - 72); pdf.line(lx, by, lx, by - 72); pdf.line(lx + lw, by, lx + lw, by - 72); pdf.line(lx + lw + vw, by, lx + lw + vw, by - 72);
  pdf.textC(vx + vw / 2, by - 17, money(sale.total));
  pdf.setFont(false, 10);
  pdf.textC(vx + vw / 2, by - 41, money(sale.paid));
  pdf.textC(vx + vw / 2, by - 65, money(Number(sale.total) - Number(sale.paid)));
  pdf.setFont(true, 10); pdf.text(35, 58, 'Le Gérant:'); pdf.textR(W - 35, 58, 'Client:');
  if (client && client.name) { pdf.setFont(false, 9); pdf.textR(W - 35, 43, String(client.name).slice(0, 42)); }
  return { bytes: pdf.output(), name: `Facture_${inv.number}.pdf` };
}

/* Rapport d'activité : même contenu que export_report_pdf du PC. */
async function buildReportPDF(t, rows) {
  const pdf = new MiniPDF(), W = pdf.W, H = pdf.H;
  const logo = await logoBytes(); if (logo) pdf.image(logo, 35, H - 95, 95, 70);
  pdf.setFont(true, 15); pdf.textC(W / 2 + 40, H - 45, COMM.name);
  pdf.setFont(false, 9); pdf.textC(W / 2 + 40, H - 60, COMM.rccm); pdf.textC(W / 2 + 40, H - 73, 'TEL : ' + COMM.phone);
  pdf.setFont(true, 12); pdf.text(35, H - 115, 'RAPPORT D’ACTIVITÉ');
  pdf.setFont(false, 10); pdf.text(35, H - 130, `Période : ${t.periode}   |   Édité le ${frenchDate()}`);
  let y = H - 160; pdf.setFont(true, 10);
  [['Chiffre d’affaires', t.ca], ['Marge brute', t.marge], ['Achats', t.achats], ['Dépenses', t.depenses],
   ['Encaissé', t.encaisse], ['Résultat indicatif', t.marge - t.depenses], ['Crédits en cours', t.credits]]
    .forEach(([l, v]) => { pdf.text(40, y, l + ' :'); pdf.textR(300, y, money(v)); y -= 16; });
  y -= 14; pdf.text(35, y, 'Marges par produit'); y -= 16;
  pdf.setFont(true, 8.5); [[35, 'Code'], [100, 'Produit'], [300, 'Qté'], [360, 'CA'], [440, 'Coût'], [520, 'Marge']].forEach(([x, l]) => pdf.text(x, y, l));
  y -= 4; pdf.line(35, y, W - 35, y); y -= 12; pdf.setFont(false, 8.5);
  for (const r of rows) {
    if (y < 60) { pdf.newPage(); y = H - 60; pdf.setFont(false, 8.5); }
    pdf.text(35, y, String(r.code || '').slice(0, 12)); pdf.text(100, y, String(r.name).slice(0, 32));
    pdf.textR(340, y, q(r.qty)); pdf.textR(425, y, money(r.revenue)); pdf.textR(505, y, money(r.cost)); pdf.textR(W - 35, y, money(r.revenue - r.cost)); y -= 14;
  }
  const d = new Date();
  return { bytes: pdf.output(), name: `Rapport_${isoDate(d).replace(/-/g, '')}_${pad(d.getHours())}${pad(d.getMinutes())}.pdf` };
}
