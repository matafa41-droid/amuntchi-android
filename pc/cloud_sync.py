"""AMUNTCHI - Synchronisation Cloud PC / Android (Supabase).

Principe
--------
* Chaque modification locale (ajout, modification, suppression) est notee dans
  la table ``sync_log`` par des declencheurs SQLite propres a la connexion de
  l'application.  Les ecritures faites par la synchronisation elle-meme ne sont
  donc jamais renvoyees en boucle.
* Le Cloud (table ``amuntchi_records`` de Supabase) garde la derniere version de
  chaque enregistrement, identifie par (table, id), avec un numero d'ordre
  ``seq`` qui permet de ne telecharger que les nouveautes.
* Le PC numerote ses enregistrements normalement (1, 2, 3...).  Chaque telephone
  utilise sa propre plage negative (-1000001, -1000002... pour le telephone 1),
  ce qui evite toute collision : deux ventes faites en meme temps hors ligne
  sur le PC et sur un telephone sont toutes les deux conservees.
* Les quantites en stock sont recalculees a partir des mouvements, et les
  soldes des credits a partir des paiements : rien ne s'ecrase.
* En cas de modification du meme enregistrement sur deux appareils (ex. prix
  d'un produit), la modification la plus recente l'emporte.

Ce module n'utilise pas Tkinter : il peut etre teste seul.
"""
import json
import base64
import io
import urllib.request
import urllib.error
from pathlib import Path

# Tables echangees avec le Cloud (le stock est recalcule, il n'est pas echange).
CLOUD_TABLES = ['users', 'categories', 'units', 'conversions', 'locations', 'suppliers',
                'clients', 'products', 'photos', 'movements', 'purchases', 'purchase_lines',
                'sales', 'sale_lines', 'invoices', 'credits', 'installments', 'payments',
                'expenses', 'transfers', 'transfer_lines', 'inventories', 'inventory_lines',
                'audit']

# Colonnes uniques : en cas de doublon venu d'un autre appareil, la valeur est
# suffixee pour ne perdre aucun enregistrement.
UNIQUE_COLS = {'products': 'code', 'categories': 'name', 'units': 'name',
               'locations': 'name', 'users': 'username', 'invoices': 'number'}

EPOCH = '1970-01-01T00:00:00.000Z'
UTC_NOW_SQL = "strftime('%Y-%m-%dT%H:%M:%fZ','now')"
PHONE_BAND = 1_000_000          # telephone n : ids de -(n*1e6+1) a -(n*1e6+999999)


# --------------------------------------------------------------------------
# Journal des modifications locales
# --------------------------------------------------------------------------
def install_change_log(conn):
    """Cree les tables de suivi et les declencheurs TEMPORAIRES de la connexion.

    Seule la connexion de l'application installe ces declencheurs : les
    ecritures de la synchronisation (faites en mode « muet ») ne sont pas notees.
    """
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS photos(id INTEGER PRIMARY KEY, data TEXT);
        CREATE TABLE IF NOT EXISTS sync_log(seq INTEGER PRIMARY KEY AUTOINCREMENT,
            tbl TEXT NOT NULL, rid INTEGER NOT NULL, ts TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_sync_log_row ON sync_log(tbl, rid);
        CREATE TABLE IF NOT EXISTS sync_meta(tbl TEXT NOT NULL, rid INTEGER NOT NULL,
            changed_at TEXT, PRIMARY KEY(tbl, rid));
        CREATE TEMP TABLE IF NOT EXISTS sync_mute(v INTEGER);
    ''')
    if conn.execute('SELECT COUNT(*) FROM temp.sync_mute').fetchone()[0] == 0:
        conn.execute('INSERT INTO temp.sync_mute(v) VALUES(0)')
    for t in CLOUD_TABLES:
        for kind, ref in (('INSERT', 'NEW'), ('UPDATE', 'NEW'), ('DELETE', 'OLD')):
            conn.execute(f'''CREATE TEMP TRIGGER IF NOT EXISTS amx_{t}_{kind.lower()}
                AFTER {kind} ON main.{t}
                WHEN (SELECT v FROM sync_mute LIMIT 1)=0
                BEGIN
                    INSERT INTO sync_log(tbl,rid,ts) VALUES('{t}',{ref}.id,{UTC_NOW_SQL});
                END''')
        # Une modification qui change l'identifiant laisse une trace de l'ancien.
        conn.execute(f'''CREATE TEMP TRIGGER IF NOT EXISTS amx_{t}_rekey
            AFTER UPDATE OF id ON main.{t}
            WHEN (SELECT v FROM sync_mute LIMIT 1)=0 AND OLD.id<>NEW.id
            BEGIN
                INSERT INTO sync_log(tbl,rid,ts) VALUES('{t}',OLD.id,{UTC_NOW_SQL});
            END''')
    conn.commit()


def set_mute(conn, on):
    conn.execute('UPDATE temp.sync_mute SET v=?', (1 if on else 0,))


def pending_count(conn):
    return conn.execute('SELECT COUNT(*) FROM (SELECT DISTINCT tbl,rid FROM sync_log)').fetchone()[0]


def _columns(conn, table):
    return [r[1] for r in conn.execute(f'PRAGMA table_info({table})')]


def _row_dict(conn, table, rid):
    cur = conn.execute(f'SELECT * FROM {table} WHERE id=?', (rid,))
    row = cur.fetchone()
    if row is None:
        return None
    names = [d[0] for d in cur.description]
    return {k: row[i] for i, k in enumerate(names)}


# --------------------------------------------------------------------------
# Premiere mise en service
# --------------------------------------------------------------------------
def initialize(conn, user_id=None):
    """A faire une seule fois sur le PC avant la premiere synchronisation.

    1. Les quantites en stock qui ne correspondent pas aux mouvements (anciennes
       versions) recoivent un mouvement « Solde d'ouverture » pour que le stock
       puisse etre recalcule a l'identique sur tous les appareils.
    2. Tous les enregistrements existants sont marques « a envoyer », avec une
       date ancienne : si le Cloud contient deja une version (reinstallation),
       c'est la version du Cloud qui est gardee.
    Retourne le nombre de corrections de stock.
    """
    from core import now  # import tardif : evite une dependance circulaire
    fixes = 0
    rows = conn.execute('''SELECT s.product_id,s.location_id,s.qty,
            COALESCE((SELECT SUM(m.qty) FROM movements m WHERE m.product_id=s.product_id
                      AND m.location_id=s.location_id),0) mv
            FROM stocks s''').fetchall()
    for pid, lid, qty, mv in rows:
        delta = float(qty or 0) - float(mv or 0)
        if abs(delta) > 1e-9:
            conn.execute('INSERT INTO movements(product_id,location_id,type,qty,user_id,date,ref,note) '
                         'VALUES(?,?,?,?,?,?,?,?)',
                         (pid, lid, "Solde d'ouverture (synchronisation)", delta, user_id, now(),
                          '', 'Stock existant avant la mise en service du Cloud'))
            fixes += 1
    set_mute(conn, True)
    try:
        for t in CLOUD_TABLES:
            conn.execute(f"INSERT INTO sync_log(tbl,rid,ts) SELECT '{t}',id,? FROM {t}", (EPOCH,))
        # Les corrections de stock ci-dessus ont deja ete notees avec leur date
        # reelle par les declencheurs : c'est la date la plus recente qui compte.
    finally:
        set_mute(conn, False)
    conn.commit()
    return fixes


# --------------------------------------------------------------------------
# Envoi
# --------------------------------------------------------------------------
def prepare_push(conn, device):
    """Liste des enregistrements a envoyer et dernier numero de journal couvert."""
    maxseq = conn.execute('SELECT COALESCE(MAX(seq),0) FROM sync_log').fetchone()[0]
    if not maxseq:
        return [], 0
    out = []
    for tbl, rid, ts in conn.execute('SELECT tbl,rid,MAX(ts) FROM sync_log WHERE seq<=? '
                                     'GROUP BY tbl,rid ORDER BY MIN(seq)', (maxseq,)).fetchall():
        if tbl not in CLOUD_TABLES:
            continue
        data = _row_dict(conn, tbl, rid)
        out.append({'tbl': tbl, 'id': int(rid), 'data': data, 'deleted': data is None,
                    'device': device, 'changed_at': ts})
    return out, maxseq


def mark_pushed(conn, records, maxseq):
    conn.execute('DELETE FROM sync_log WHERE seq<=?', (maxseq,))
    conn.executemany('INSERT INTO sync_meta(tbl,rid,changed_at) VALUES(?,?,?) '
                     'ON CONFLICT(tbl,rid) DO UPDATE SET changed_at=excluded.changed_at',
                     [(r['tbl'], r['id'], r['changed_at']) for r in records])
    conn.commit()


# --------------------------------------------------------------------------
# Reception
# --------------------------------------------------------------------------
def apply_records(conn, records):
    """Applique les enregistrements recus du Cloud. Retourne le nombre applique.

    Doit etre appele en mode muet (set_mute) pour ne pas renvoyer ces donnees.
    """
    cols_cache = {}
    applied = 0
    for rec in records:
        tbl = rec.get('tbl')
        if tbl not in CLOUD_TABLES:
            continue
        rid = int(rec['id'])
        ts = rec.get('changed_at') or EPOCH
        pend = conn.execute('SELECT MAX(ts) FROM sync_log WHERE tbl=? AND rid=?', (tbl, rid)).fetchone()[0]
        if pend and pend > ts:
            continue                                   # notre version locale est plus recente
        meta = conn.execute('SELECT changed_at FROM sync_meta WHERE tbl=? AND rid=?', (tbl, rid)).fetchone()
        if meta and meta[0] and meta[0] >= ts and not pend:
            continue                                   # deja connu
        if rec.get('deleted'):
            conn.execute(f'DELETE FROM {tbl} WHERE id=?', (rid,))
        else:
            data = rec.get('data') or {}
            if isinstance(data, str):
                data = json.loads(data)
            cols = cols_cache.get(tbl) or cols_cache.setdefault(tbl, _columns(conn, tbl))
            values = {k: data.get(k) for k in cols if k in data and k != 'id'}
            _upsert(conn, tbl, rid, values)
        conn.execute('DELETE FROM sync_log WHERE tbl=? AND rid=?', (tbl, rid))
        conn.execute('INSERT INTO sync_meta(tbl,rid,changed_at) VALUES(?,?,?) '
                     'ON CONFLICT(tbl,rid) DO UPDATE SET changed_at=excluded.changed_at', (tbl, rid, ts))
        applied += 1
    conn.commit()
    return applied


def _upsert(conn, tbl, rid, values, retry=True):
    import sqlite3
    try:
        if values:
            sets = ','.join(f'{k}=?' for k in values)
            cur = conn.execute(f'UPDATE {tbl} SET {sets} WHERE id=?', (*values.values(), rid))
            if cur.rowcount:
                return
        cols = ['id', *values.keys()]
        conn.execute(f"INSERT INTO {tbl} ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
                     (rid, *values.values()))
    except sqlite3.IntegrityError:
        col = UNIQUE_COLS.get(tbl)
        if not retry or not col or values.get(col) is None:
            raise
        values = dict(values)
        values[col] = f"{values[col]}~{abs(rid)}"
        _upsert(conn, tbl, rid, values, retry=False)


# --------------------------------------------------------------------------
# Valeurs recalculees (identiques sur tous les appareils)
# --------------------------------------------------------------------------
def recompute_stocks(conn):
    conn.execute('DELETE FROM stocks')
    conn.execute('''INSERT INTO stocks(product_id,location_id,qty)
                    SELECT product_id,location_id,SUM(qty) FROM movements
                    WHERE product_id IS NOT NULL AND location_id IS NOT NULL
                    GROUP BY product_id,location_id''')
    conn.commit()


def recompute_credit(conn, credit_id):
    """Avance, solde et echeances d'un credit a partir des paiements recus."""
    cr = conn.execute('SELECT id,sale_id,total,advance,balance FROM credits WHERE id=?', (credit_id,)).fetchone()
    if not cr:
        return
    _, sale_id, total, old_adv, old_bal = cr
    sale = conn.execute('SELECT paid FROM sales WHERE id=?', (sale_id,)).fetchone()
    paid_sum = float(conn.execute('SELECT COALESCE(SUM(amount),0) FROM payments WHERE credit_id=?',
                                  (credit_id,)).fetchone()[0])
    first = float(sale[0] or 0) if sale else max(float(old_adv or 0) - paid_sum, 0)
    advance = round(first + paid_sum, 2)
    balance = round(float(total or 0) - advance, 2)
    if abs(advance - float(old_adv or 0)) > 1e-6 or abs(balance - float(old_bal or 0)) > 1e-6:
        conn.execute('UPDATE credits SET advance=?,balance=? WHERE id=?', (advance, balance, credit_id))
    reste = paid_sum
    for iid, amount, status in conn.execute('SELECT id,amount,status FROM installments WHERE credit_id=? '
                                            'ORDER BY num', (credit_id,)).fetchall():
        amount = float(amount or 0)
        if reste + 1e-6 >= amount:
            new = 'Payé'
            reste -= amount
        else:
            new = 'En attente'
            reste = 0
        if status != new:
            conn.execute('UPDATE installments SET status=? WHERE id=?', (new, iid))


def recompute_all_credits(conn):
    for (cid,) in conn.execute('SELECT id FROM credits').fetchall():
        recompute_credit(conn, cid)
    conn.commit()


# --------------------------------------------------------------------------
# Photos des produits (miniature JPEG partagee entre PC et telephone)
# --------------------------------------------------------------------------
def photo_to_base64(path, size=480):
    try:
        from PIL import Image
        im = Image.open(path).convert('RGB')
        im.thumbnail((size, size))
        buf = io.BytesIO()
        im.save(buf, 'JPEG', quality=72)
        return 'data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode('ascii')
    except Exception:
        return None


def photo_from_base64(data, dest):
    try:
        raw = data.split(',', 1)[1] if ',' in data else data
        Path(dest).write_bytes(base64.b64decode(raw))
        return Path(dest)
    except Exception:
        return None


# --------------------------------------------------------------------------
# Client Supabase (authentification + API REST)
# --------------------------------------------------------------------------
class CloudError(Exception):
    pass


class SupabaseClient:
    TABLE = 'amuntchi_records'

    def __init__(self, url, key, email, password, timeout=25):
        self.url = (url or '').strip().rstrip('/')
        self.key = (key or '').strip()
        self.email = (email or '').strip()
        self.password = password or ''
        self.timeout = timeout
        self.token = None
        if not (self.url and self.key and self.email and self.password):
            raise CloudError('Paramètres Cloud incomplets (adresse, clé, e-mail et mot de passe).')

    def _request(self, method, path, body=None, headers=None, auth=True):
        h = {'apikey': self.key, 'Content-Type': 'application/json'}
        if auth and self.token:
            h['Authorization'] = f'Bearer {self.token}'
        h.update(headers or {})
        data = json.dumps(body, ensure_ascii=False, default=str).encode('utf-8') if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode('utf-8')
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            detail = e.read().decode('utf-8', 'replace')[:300]
            if e.code in (400, 401) and 'grant_type' in path:
                raise CloudError('E-mail ou mot de passe du compte Cloud incorrect.')
            raise CloudError(f'Erreur Cloud HTTP {e.code} : {detail}')
        except urllib.error.URLError as e:
            raise CloudError(f'Pas de connexion Internet ou adresse Cloud injoignable ({e.reason}).')
        except OSError as e:
            raise CloudError(f'Connexion impossible : {e}')

    def login(self):
        r = self._request('POST', '/auth/v1/token?grant_type=password',
                          {'email': self.email, 'password': self.password}, auth=False)
        self.token = (r or {}).get('access_token')
        if not self.token:
            raise CloudError('Connexion au Cloud refusée.')
        return True

    def fetch_since(self, since, page=1000):
        out = []
        last = since
        while True:
            rows = self._request('GET', f'/rest/v1/{self.TABLE}?select=*&seq=gt.{int(last)}'
                                        f'&order=seq.asc&limit={page}') or []
            out.extend(rows)
            if len(rows) < page:
                return out
            last = rows[-1]['seq']

    def push(self, records, batch=400):
        for i in range(0, len(records), batch):
            chunk = [{k: r[k] for k in ('tbl', 'id', 'data', 'deleted', 'device', 'changed_at')}
                     for r in records[i:i + batch]]
            self._request('POST', f'/rest/v1/{self.TABLE}?on_conflict=tbl,id', chunk,
                          headers={'Prefer': 'resolution=merge-duplicates,return=minimal'})


# --------------------------------------------------------------------------
# Cycle complet (utilise par les tests et par l'application)
# --------------------------------------------------------------------------
OVERLAP = 200   # relecture de securite des derniers numeros (ecritures simultanees)


def fetch_remote(client, last_seq):
    """Etape reseau 1 : connexion + telechargement des nouveautes."""
    client.login()
    return client.fetch_since(max(0, int(last_seq or 0) - OVERLAP))


def apply_remote(conn, records):
    """Etape locale : application des nouveautes et recalculs. Retourne (appliques, dernier seq)."""
    last = max([int(r.get('seq') or 0) for r in records] or [0])
    set_mute(conn, True)
    try:
        n = apply_records(conn, records)
        if n:
            recompute_stocks(conn)
            recompute_all_credits(conn)
    finally:
        set_mute(conn, False)
        conn.commit()
    return n, last
