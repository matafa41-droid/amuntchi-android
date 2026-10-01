"""AMUNTCHI - Noyau applicatif (configuration, utilitaires, sécurité, base de données).

Ce module regroupe tout ce qui ne dépend pas de l'interface graphique afin
d'alléger app.py et de faciliter la maintenance et les tests.
"""
import os, sys, sqlite3, shutil, datetime, hashlib, hmac, secrets, subprocess
from pathlib import Path

APP = 'Amuntchi'
VERSION = '5.0'
BASE = Path(os.getenv('APPDATA') or Path.home()) / 'Amuntchi'
BASE.mkdir(parents=True, exist_ok=True)
ASSETS = BASE / 'assets'
ASSETS.mkdir(exist_ok=True)
DB_PATH = BASE / 'amuntchi_v2.db'
LOGO = ASSETS / 'logo.jpg'

COMM = {
    'name': 'Mini Quincaillerie AMUNTCHI',
    'rccm': 'RCCM/NE/AGA/2020/A/124',
    'address': 'Tchirozerine',
    'phone': '+227 96105518 / 94206364',
    'email': 'quincaillerieamuntchi@gmail.com',
}

ROLES = ['Administrateur', 'Responsable magasin', 'Vendeur', 'Caissier', 'Utilisateur', 'Consultation']
# Le role « Utilisateur » n'a acces qu'aux onglets Ventes / caisse et Clients & credits.
PERMS = {
    'Administrateur': {'all'},
    'Responsable magasin': {'dashboard', 'products', 'stock', 'purchases', 'transfers', 'inventory', 'reports'},
    # Vendeur : uniquement Produits (boutique), Ventes / caisse et Clients & credits.
    'Vendeur': {'products', 'sales', 'clients', 'invoices', 'payments'},
    'Caissier': {'dashboard', 'sales', 'clients', 'payments', 'expenses'},
    'Utilisateur': {'sales', 'clients', 'invoices', 'payments'},
    'Consultation': {'dashboard', 'products', 'stock', 'clients', 'reports'},
}
# Tables conservant une reference vers l'utilisateur (pour la suppression de compte).
USER_LINKED_TABLES = ['movements', 'purchases', 'sales', 'payments', 'expenses', 'transfers', 'inventories', 'audit']
PAYMENT_MODES = ['Espèces', 'Virement', 'Mobile Money', 'Autre']

# Emplacements de référence : le magasin reçoit les achats, la boutique vend.
MAGASIN = 'Magasin'
BOUTIQUE = 'Boutique'

# Unités de stockage et de vente de la quincaillerie (liste unique et fermée).
UNITS = [
    ('1/8 Litre', '1/8 L'),
    ('1/4 Litre', '1/4 L'),
    ('1/2 Litre', '1/2 L'),
    ('Litre', 'L'),
    ('4 Litres', '4 L'),
    ('17 Litres', '17 L'),
    ('Carton', 'Carton'),
    ('Paquet', 'Paquet'),
    ('Boite', 'Boite'),
    ('Pièce', 'Pièce'),
    ('Kilo', 'Kilo'),
    ('Mètre', 'Mètre'),
    ('Rouleau', 'Rouleau'),
    ('Sac', 'Sac'),
]

# --------------------------------------------------------------------------
# Utilitaires généraux
# --------------------------------------------------------------------------

def now():
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def today():
    return datetime.date.today().isoformat()


FRENCH_DAYS = ['Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi', 'Dimanche']
FRENCH_MONTHS = ['Janvier', 'Février', 'Mars', 'Avril', 'Mai', 'Juin', 'Juillet',
                 'Août', 'Septembre', 'Octobre', 'Novembre', 'Décembre']


def french_date(d=None):
    d = d or datetime.date.today()
    return f'{FRENCH_DAYS[d.weekday()]} {d.day:02d} {FRENCH_MONTHS[d.month - 1]} {d.year}'


def money(v):
    return f'{float(v or 0):,.0f} FCFA'.replace(',', ' ')


def nfloat(v):
    """Conversion tolérante : accepte '1 250,50' comme '1250.50'."""
    return float(str(v).replace(' ', '').replace('\u00a0', '').replace(',', '.'))


def open_path(path):
    """Ouvre un fichier ou un dossier avec l'explorateur du système (Windows, macOS, Linux)."""
    path = str(path)
    try:
        if sys.platform.startswith('win'):
            os.startfile(path)  # noqa: S606 - API Windows officielle
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', path])
        else:
            subprocess.Popen(['xdg-open', path])
        return True
    except Exception:
        return False


def maximize(window):
    """Plein écran compatible Windows, macOS et Linux."""
    try:
        window.state('zoomed')
        return
    except Exception:
        pass
    try:
        window.attributes('-zoomed', True)
        return
    except Exception:
        pass
    try:
        window.geometry(f'{window.winfo_screenwidth()}x{window.winfo_screenheight()}+0+0')
    except Exception:
        pass


# --------------------------------------------------------------------------
# Sécurité des mots de passe (PBKDF2-SHA256 avec sel aléatoire)
# --------------------------------------------------------------------------
PBKDF2_ITERATIONS = 200_000
HASH_PREFIX = 'pbkdf2_sha256$'


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'),
                             bytes.fromhex(salt), PBKDF2_ITERATIONS).hex()
    return f'{HASH_PREFIX}{PBKDF2_ITERATIONS}${salt}${dk}'


def is_hashed(stored: str) -> bool:
    return bool(stored) and str(stored).startswith(HASH_PREFIX)


def verify_password(password: str, stored: str) -> bool:
    """Vérifie un mot de passe. Accepte encore les anciennes valeurs en clair
    (bases créées par les versions précédentes) le temps de la migration."""
    if not stored:
        return False
    stored = str(stored)
    if is_hashed(stored):
        try:
            _, iterations, salt, digest = stored.split('$', 3)
            dk = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'),
                                     bytes.fromhex(salt), int(iterations)).hex()
            return hmac.compare_digest(dk, digest)
        except Exception:
            return False
    return hmac.compare_digest(password, stored)


def password_strength_error(password: str):
    """Retourne un message si le mot de passe est trop faible, sinon None."""
    if len(password) < 6:
        return 'Le mot de passe doit contenir au moins 6 caractères.'
    if password.lower() in ('admin', 'motdepasse', 'password', '123456', 'amuntchi'):
        return 'Ce mot de passe est trop courant. Choisissez-en un autre.'
    return None


# --------------------------------------------------------------------------
# Base de données
# --------------------------------------------------------------------------
SYNC_TABLES = ['categories', 'units', 'conversions', 'locations', 'suppliers', 'clients',
               'products', 'stocks', 'movements', 'purchases', 'purchase_lines', 'sales',
               'sale_lines', 'invoices', 'credits', 'installments', 'payments', 'expenses',
               'transfers', 'transfer_lines', 'inventories', 'inventory_lines']

DEFAULT_SETTINGS = {
    'invoice_prefix': 'AM-',
    'invoice_next': '1',
    'sync_folder': str(BASE / 'sync'),
    'sync_server_url': '',
    'sync_device_name': 'AMUNTCHI-PC',
    'cloud_folder': '',
    'cloud_url': '',
    'cloud_token': '',
    'last_sync_at': '',
    'password_migrated': '0',
    # Synchronisation Cloud Supabase (V5)
    'supa_url': '',
    'supa_key': '',
    'supa_email': '',
    'supa_password': '',
    'cloud_auto': '1',
    'cloud_last_seq': '0',
    'cloud_initialized': '0',
    'cloud_last_sync': '',
}


class DB:
    def __init__(self):
        self.c = sqlite3.connect(DB_PATH, timeout=15)
        self.c.row_factory = sqlite3.Row
        self.c.execute('PRAGMA foreign_keys=ON')
        self.c.execute('PRAGMA journal_mode=WAL')
        self.init()
        # Journal des modifications pour la synchronisation Cloud PC / Android.
        from cloud_sync import install_change_log
        install_change_log(self.c)

    def q(self, s, p=()):
        cur = self.c.cursor()
        cur.execute(s, p)
        self.c.commit()
        return cur.lastrowid

    def rows(self, s, p=()):
        return self.c.execute(s, p).fetchall()

    def one(self, s, p=()):
        return self.c.execute(s, p).fetchone()

    def setting(self, key, default=''):
        r = self.one('SELECT value FROM settings WHERE key=?', (key,))
        return (r['value'] if r and r['value'] is not None else default)

    def set_setting(self, key, value):
        self.q('INSERT INTO settings(key,value) VALUES(?,?) '
               'ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, str(value)))

    def init(self):
        self.c.executescript('''
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,name TEXT,username TEXT UNIQUE,password TEXT,role TEXT,photo TEXT,active INTEGER DEFAULT 1,created_at TEXT);
        CREATE TABLE IF NOT EXISTS categories(id INTEGER PRIMARY KEY,name TEXT UNIQUE);
        CREATE TABLE IF NOT EXISTS units(id INTEGER PRIMARY KEY,name TEXT UNIQUE,symbol TEXT);
        CREATE TABLE IF NOT EXISTS conversions(id INTEGER PRIMARY KEY,source_id INTEGER,target_id INTEGER,factor REAL);
        CREATE TABLE IF NOT EXISTS locations(id INTEGER PRIMARY KEY,name TEXT UNIQUE,type TEXT);
        CREATE TABLE IF NOT EXISTS suppliers(id INTEGER PRIMARY KEY,name TEXT,phone TEXT,email TEXT,address TEXT);
        CREATE TABLE IF NOT EXISTS clients(id INTEGER PRIMARY KEY,name TEXT,phone TEXT,email TEXT,address TEXT);
        CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY,code TEXT UNIQUE,name TEXT,photo TEXT,category_id INTEGER,brand TEXT,stock_unit_id INTEGER,sale_unit_id INTEGER,purchase_price REAL DEFAULT 0,sale_price REAL DEFAULT 0,min_stock REAL DEFAULT 0,barcode TEXT,active INTEGER DEFAULT 1,notes TEXT);
        CREATE TABLE IF NOT EXISTS stocks(product_id INTEGER,location_id INTEGER,qty REAL DEFAULT 0,PRIMARY KEY(product_id,location_id));
        CREATE TABLE IF NOT EXISTS movements(id INTEGER PRIMARY KEY,product_id INTEGER,location_id INTEGER,type TEXT,qty REAL,user_id INTEGER,date TEXT,ref TEXT,note TEXT);
        CREATE TABLE IF NOT EXISTS purchases(id INTEGER PRIMARY KEY,supplier_id INTEGER,location_id INTEGER,date TEXT,total REAL,status TEXT,user_id INTEGER);
        CREATE TABLE IF NOT EXISTS purchase_lines(id INTEGER PRIMARY KEY,purchase_id INTEGER,product_id INTEGER,qty REAL,price REAL);
        CREATE TABLE IF NOT EXISTS sales(id INTEGER PRIMARY KEY,client_id INTEGER,location_id INTEGER,date TEXT,total REAL,paid REAL,status TEXT,payment_mode TEXT,user_id INTEGER,credit INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS sale_lines(id INTEGER PRIMARY KEY,sale_id INTEGER,product_id INTEGER,qty REAL,unit TEXT,price REAL,discount REAL DEFAULT 0,cost REAL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS invoices(id INTEGER PRIMARY KEY,sale_id INTEGER,number TEXT UNIQUE,date TEXT,status TEXT);
        CREATE TABLE IF NOT EXISTS credits(id INTEGER PRIMARY KEY,sale_id INTEGER,client_id INTEGER,total REAL,advance REAL,balance REAL);
        CREATE TABLE IF NOT EXISTS installments(id INTEGER PRIMARY KEY,credit_id INTEGER,num INTEGER,amount REAL,due_date TEXT,status TEXT DEFAULT 'En attente');
        CREATE TABLE IF NOT EXISTS payments(id INTEGER PRIMARY KEY,client_id INTEGER,credit_id INTEGER,amount REAL,date TEXT,mode TEXT,user_id INTEGER,note TEXT);
        CREATE TABLE IF NOT EXISTS expenses(id INTEGER PRIMARY KEY,category TEXT,amount REAL,date TEXT,receipt TEXT,user_id INTEGER,mode TEXT);
        CREATE TABLE IF NOT EXISTS transfers(id INTEGER PRIMARY KEY,source_id INTEGER,dest_id INTEGER,date TEXT,status TEXT,user_id INTEGER);
        CREATE TABLE IF NOT EXISTS transfer_lines(id INTEGER PRIMARY KEY,transfer_id INTEGER,product_id INTEGER,qty REAL);
        CREATE TABLE IF NOT EXISTS inventories(id INTEGER PRIMARY KEY,location_id INTEGER,date TEXT,status TEXT,user_id INTEGER);
        CREATE TABLE IF NOT EXISTS inventory_lines(id INTEGER PRIMARY KEY,inventory_id INTEGER,product_id INTEGER,theoretical REAL,real_qty REAL,difference REAL);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,user_id INTEGER,action TEXT,date TEXT,old_value TEXT,new_value TEXT,reason TEXT);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);
        CREATE INDEX IF NOT EXISTS idx_sales_date ON sales(date);
        CREATE INDEX IF NOT EXISTS idx_movements_product ON movements(product_id);
        CREATE INDEX IF NOT EXISTS idx_sale_lines_sale ON sale_lines(sale_id);
        CREATE INDEX IF NOT EXISTS idx_products_barcode ON products(barcode);
        ''')
        self.migrate_schema()
        if not self.one('SELECT id FROM users LIMIT 1'):
            self.q('INSERT INTO users(name,username,password,role,created_at) VALUES(?,?,?,?,?)',
                   ('Administrateur', 'admin', hash_password('admin'), 'Administrateur', now()))
        if not self.one('SELECT id FROM locations LIMIT 1'):
            self.q('INSERT INTO locations(name,type) VALUES(?,?)', ('Magasin', 'magasin'))
            self.q('INSERT INTO locations(name,type) VALUES(?,?)', ('Boutique', 'boutique'))
        self.ensure_units()
        if not self.one('SELECT id FROM categories LIMIT 1'):
            for n in ['Fer & Métallurgie', 'Quincaillerie générale', 'Peinture', 'Plomberie',
                      'Électricité', 'Outillage', 'Menuiserie', 'Sanitaire']:
                self.q('INSERT INTO categories(name) VALUES(?)', (n,))
        for key, value in DEFAULT_SETTINGS.items():
            if self.one('SELECT value FROM settings WHERE key=?', (key,)) is None:
                self.q('INSERT INTO settings(key,value) VALUES(?,?)', (key, value))
        self.migrate_passwords()

    def ensure_units(self):
        """Impose la liste d'unités de la quincaillerie et retire les anciennes."""
        wanted = {n: s for n, s in UNITS}
        for name, symbol in UNITS:
            r = self.one('SELECT id,symbol FROM units WHERE name=?', (name,))
            if r is None:
                self.q('INSERT INTO units(name,symbol) VALUES(?,?)', (name, symbol))
            elif r['symbol'] != symbol:
                self.q('UPDATE units SET symbol=? WHERE id=?', (symbol, r['id']))
        for r in self.rows('SELECT id,name FROM units'):
            if r['name'] not in wanted:
                # Les produits qui utilisaient une unité supprimée repassent sans unité.
                self.q('UPDATE products SET stock_unit_id=NULL WHERE stock_unit_id=?', (r['id'],))
                self.q('UPDATE products SET sale_unit_id=NULL WHERE sale_unit_id=?', (r['id'],))
                self.q('DELETE FROM units WHERE id=?', (r['id'],))

    def location_id(self, name):
        r = self.one('SELECT id FROM locations WHERE lower(name)=lower(?)', (name,))
        if r:
            return int(r['id'])
        return self.q('INSERT INTO locations(name,type) VALUES(?,?)', (name, name.lower()))

    def magasin_id(self):
        return self.location_id(MAGASIN)

    def boutique_id(self):
        return self.location_id(BOUTIQUE)

    def products_at(self, location_id, only_positive=True):
        """Produits présents à un emplacement, avec quantité et unités."""
        having = 'HAVING qty>0' if only_positive else ''
        return self.rows(f'''SELECT p.id,p.code,p.name,p.brand,p.purchase_price,p.sale_price,
                p.min_stock,p.photo,COALESCE(c.name,'-') category,
                COALESCE(su.name,'-') stock_unit,COALESCE(vu.name,'-') sale_unit,
                COALESCE(s.qty,0) qty
                FROM products p
                LEFT JOIN stocks s ON s.product_id=p.id AND s.location_id=?
                LEFT JOIN categories c ON c.id=p.category_id
                LEFT JOIN units su ON su.id=p.stock_unit_id
                LEFT JOIN units vu ON vu.id=p.sale_unit_id
                WHERE p.active=1 GROUP BY p.id {having} ORDER BY p.name''', (location_id,))

    def migrate_schema(self):
        """Ajoute les colonnes introduites après la V2 sans casser les bases existantes."""
        cols = {r['name'] for r in self.rows('PRAGMA table_info(sale_lines)')}
        if 'cost' not in cols:
            self.c.execute('ALTER TABLE sale_lines ADD COLUMN cost REAL DEFAULT 0')
            self.c.commit()

    def migrate_passwords(self):
        """Convertit les mots de passe encore stockés en clair vers PBKDF2."""
        converted = 0
        for r in self.rows('SELECT id,password FROM users'):
            if not is_hashed(r['password'] or ''):
                self.q('UPDATE users SET password=? WHERE id=?',
                       (hash_password(r['password'] or ''), r['id']))
                converted += 1
        if converted:
            self.q('INSERT INTO audit(user_id,action,date,old_value,new_value,reason) '
                   'VALUES(?,?,?,?,?,?)',
                   (None, 'Migration sécurité mots de passe', now(), 'texte clair',
                    'PBKDF2-SHA256', f'{converted} compte(s) migré(s)'))
        self.set_setting('password_migrated', '1')
        return converted

    # ---------------- Stock ----------------
    def stock(self, pid, lid):
        r = self.one('SELECT qty FROM stocks WHERE product_id=? AND location_id=?', (pid, lid))
        return float(r['qty']) if r else 0

    def adjust(self, pid, lid, delta, typ, user, ref='', note=''):
        new = self.stock(pid, lid) + delta
        if new < -1e-9:
            raise ValueError('Stock insuffisant')
        self.q('INSERT OR REPLACE INTO stocks(product_id,location_id,qty) VALUES(?,?,?)',
               (pid, lid, new))
        self.q('INSERT INTO movements(product_id,location_id,type,qty,user_id,date,ref,note) '
               'VALUES(?,?,?,?,?,?,?,?)', (pid, lid, typ, delta, user, now(), ref, note))

    def find_product_by_code(self, code):
        """Recherche par code-barres puis par référence (lecteur de code-barres)."""
        code = (code or '').strip()
        if not code:
            return None
        return self.one('SELECT * FROM products WHERE active=1 AND '
                        '(barcode=? OR lower(code)=lower(?)) LIMIT 1', (code, code))

    def find_product_by_reference(self, ref, location_id=None):
        """Recherche par référence (code produit). Si un emplacement est fourni,
        le produit doit y être disponible."""
        ref = (ref or '').strip()
        if not ref:
            return None
        if location_id is None:
            return self.one('SELECT * FROM products WHERE active=1 AND lower(code)=lower(?) LIMIT 1',
                            (ref,))
        return self.one('''SELECT p.* FROM products p
                JOIN stocks s ON s.product_id=p.id AND s.location_id=?
                WHERE p.active=1 AND lower(p.code)=lower(?) AND s.qty>0 LIMIT 1''',
                        (location_id, ref))

    def last_business_change(self):
        """Horodatage de la dernière écriture métier locale (détection de conflit de synchro)."""
        stamps = []
        for sql in ['SELECT MAX(date) d FROM sales', 'SELECT MAX(date) d FROM purchases',
                    'SELECT MAX(date) d FROM movements', 'SELECT MAX(date) d FROM payments',
                    'SELECT MAX(date) d FROM expenses']:
            r = self.one(sql)
            if r and r['d']:
                stamps.append(str(r['d']))
        return max(stamps) if stamps else ''

    def backup_now(self, prefix='backup'):
        path = BASE / f"{prefix}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        self.c.commit()
        shutil.copy2(DB_PATH, path)
        return path


# ---------------- Moteur de recherche produits (V4.1) ----------------
import re as _re
import unicodedata as _ud

_UNIT_ALIASES = {'watts': 'w', 'watt': 'w', 'volts': 'v', 'volt': 'v', 'amperes': 'a', 'ampere': 'a',
                 'litres': 'l', 'litre': 'l', 'metres': 'm', 'metre': 'm', 'kilos': 'kg', 'kilo': 'kg'}


def _norm(text):
    """Minuscules, sans accents, '20 W' -> '20w', '2,5' -> '2.5'."""
    s = _ud.normalize('NFD', str(text or '').lower())
    s = ''.join(ch for ch in s if _ud.category(ch) != 'Mn')
    s = _re.sub(r'(\d),(\d)', r'\1.\2', s)
    s = _re.sub(r'[^a-z0-9.]+', ' ', s)
    s = _re.sub(r'(?<!\d)\.|\.(?!\d)', ' ', s)
    s = _re.sub(r'\b(' + '|'.join(_UNIT_ALIASES) + r')\b', lambda m: _UNIT_ALIASES[m.group(1)], s)
    s = _re.sub(r'(\d)\s+(w|kw|v|a|mm|cm|m|l|ml|kg|g)\b', r'\1\2', s)
    return s.split()


def _singular(tok):
    if len(tok) > 3 and not any(c.isdigit() for c in tok) and tok[-1] in 'sx':
        return tok[:-1]
    return tok


def _is_word(tok):
    return len(tok) >= 2 and tok.isalpha()


def product_family(name):
    """Famille d'un article = premier mot de sa désignation (ex. 'Ampoule LED 20W' -> 'ampoule')."""
    for t in _norm(name):
        if _is_word(t):
            return _singular(t)
    return ''


def _tok_match(q, toks):
    if any(c.isdigit() for c in q):
        return q in toks                      # 20w ne doit pas trouver 120w
    q = _singular(q)
    return any(t.startswith(q) or _singular(t) == q for t in toks)


def smart_search(rows, term):
    """Recherche par famille d'articles.

    'ampoule 20W' : la famille 'ampoule' est reconnue, seules les ampoules sont
    gardées, puis on filtre sur '20W'. Si aucune ampoule 20W n'existe, toutes
    les ampoules sont affichées (jamais d'autres familles).
    Retourne (lignes, message)."""
    rows = list(rows)
    raw = (term or '').strip()
    if not raw:
        return rows, ''
    exact = [r for r in rows if (r['code'] or '').strip().lower() == raw.lower()]
    if exact:
        return exact, f'Référence « {raw} » trouvée.'
    qtoks = _norm(raw)
    if not qtoks:
        return rows, ''

    def toks_of(r):
        return _norm(' '.join(str(r[k] or '') for k in ('name', 'brand', 'code', 'category')))

    def all_match(r, toks):
        pt = toks_of(r)
        return all(_tok_match(q, pt) for q in toks)

    words = [t for t in qtoks if _is_word(t)]
    if words:
        head = _singular(words[0])
        rest = [t for t in qtoks if t != words[0]]
        fam = [r for r in rows if product_family(r['name']) == head]
        if not fam and len(head) >= 3:
            fam = [r for r in rows if product_family(r['name']).startswith(head)]
        label = None
        if fam:
            label = sorted({product_family(r['name']) for r in fam})
            label = ', '.join(x.capitalize() for x in label)
        else:
            fam = [r for r in rows if any(_singular(t).startswith(head) for t in _norm(r['category']))]
            if fam:
                label = 'catégorie ' + ', '.join(sorted({r['category'] for r in fam}))
        if fam:
            if not rest:
                return fam, f'Famille : {label} — {len(fam)} article(s).'
            hits = [r for r in fam if all_match(r, rest)]
            if hits:
                return hits, f'Famille : {label} — {len(hits)} article(s) correspondant à « {" ".join(rest)} ».'
            return fam, (f'Aucun article « {raw} ». Affichage de toute la famille {label} '
                         f'({len(fam)} article(s)).')
    hits = [r for r in rows if all_match(r, qtoks)]
    return hits, (f'{len(hits)} article(s) contenant « {raw} ».' if hits else f'Aucun article pour « {raw} ».')
