"""AMUNTCHI - Gestion de stock & quincaillerie (interface graphique).

Version 5.0 : synchronisation Cloud (Supabase) avec l'application Android.
Version 3.0 : mots de passe hachés, compatibilité multi-plateforme,
rapports filtrables avec marges, lecture de code-barres et contrôle de conflit
de synchronisation. Le noyau (base, sécurité, utilitaires) est dans core.py.
"""
import os, sys, sqlite3, shutil, datetime, csv, json, urllib.request, urllib.error, threading, queue, time
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog

from core import (APP, VERSION, BASE, ASSETS, DB_PATH, LOGO, COMM, ROLES, PERMS,
                 PAYMENT_MODES, SYNC_TABLES, MAGASIN, BOUTIQUE, UNITS, DB, USER_LINKED_TABLES,
                 now, today, french_date, money, nfloat,
                 open_path, maximize, hash_password, verify_password, is_hashed,
                 password_strength_error, smart_search)
import cloud_sync

if sys.platform.startswith('win'):
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
try:
    from PIL import Image, ImageTk
except Exception:
    Image = ImageTk = None
try:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import ImageReader
except Exception:
    canvas = None

def _is_id(txt):
    """Identifiant numérique, positif (PC) ou négatif (enregistrement créé sur un téléphone)."""
    txt=str(txt).strip()
    return txt.lstrip('-').isdigit() and txt.count('-')<=1

SRC = Path(__file__).parent / 'assets' / 'logo.jpg'
if SRC.exists() and not LOGO.exists():
    shutil.copy2(SRC, LOGO)

class CloudManager:
    """Synchronisation Cloud en arrière-plan : le réseau dans un fil séparé,
    la base de données toujours dans le fil de l'interface (aucun conflit d'accès)."""
    AUTO_EVERY=120          # secondes entre deux synchronisations automatiques
    QUICK_AFTER=15          # délai après une opération locale
    RETRY_AFTER=60          # délai après un échec (pas d'Internet)
    def __init__(self,app):
        self.app=app; self.busy=False; self.q=queue.Queue()
        self.last_try=0; self.last_ok=0; self.failed=False; self.done_cb=None; self.manual=False
        app.after(5000,self._tick)
    def db(self): return self.app.db
    def configured(self):
        return all(self.db().setting(k,'').strip() for k in ('supa_url','supa_key','supa_email','supa_password'))
    def client(self):
        d=self.db()
        return cloud_sync.SupabaseClient(d.setting('supa_url'),d.setting('supa_key'),d.setting('supa_email'),d.setting('supa_password'))
    def status(self,text,color='#bcd0e2'):
        lbl=getattr(self.app,'sync_lbl',None)
        try:
            if lbl is not None and lbl.winfo_exists(): lbl.config(text=text,fg=color)
        except Exception: pass
    def _tick(self):
        try:
            if self.app.user and not self.busy and self.configured() and self.db().setting('cloud_auto','1')=='1':
                now_=time.time(); since=now_-self.last_try
                pending=cloud_sync.pending_count(self.db().c)
                wait=self.RETRY_AFTER if self.failed else (self.QUICK_AFTER if pending else self.AUTO_EVERY)
                if since>=wait: self.sync()
        except Exception: pass
        self.app.after(5000,self._tick)
    def sync(self,manual=False,done=None):
        if self.busy:
            if manual: messagebox.showinfo('Cloud','Une synchronisation est déjà en cours.')
            return
        if not self.configured():
            if manual: messagebox.showwarning('Cloud','Renseignez d’abord l’adresse, la clé, l’e-mail et le mot de passe du Cloud (Paramètres).')
            return
        self.manual=manual; self.done_cb=done; self.last_try=time.time()
        d=self.db()
        try:
            if d.setting('cloud_initialized','0')!='1':
                d.backup_now('pre_cloud_v5_backup')
                fixes=cloud_sync.initialize(d.c,(self.app.user or {}).get('id'))
                d.set_setting('cloud_initialized','1')
                if fixes: self.app.audit('Mise en service Cloud','',f'{fixes} solde(s) d’ouverture de stock créés')
            client=self.client()
        except Exception as e:
            return self._fail(str(e))
        self.busy=True; self.status('Cloud : synchronisation…','#ffd27a')
        last=int(d.setting('cloud_last_seq','0') or 0)
        def work():
            try: self.q.put(('fetched',cloud_sync.fetch_remote(client,last),client))
            except Exception as e: self.q.put(('error',str(e),None))
        threading.Thread(target=work,daemon=True).start()
        self.app.after(200,self._poll)
    def _poll(self):
        try: kind,payload,client=self.q.get_nowait()
        except queue.Empty:
            self.app.after(200,self._poll); return
        d=self.db()
        if kind=='error': return self._fail(payload)
        if kind=='fetched':
            try:
                n,last=cloud_sync.apply_remote(d.c,payload)
                if last>int(d.setting('cloud_last_seq','0') or 0): d.set_setting('cloud_last_seq',str(last))
                self.received=n
                recs,maxseq=cloud_sync.prepare_push(d.c,d.setting('sync_device_name','PC') or 'PC')
            except Exception as e:
                return self._fail(f'Application des données reçues : {e}')
            if not recs: return self._success(0)
            def work():
                try: client.push(recs); self.q.put(('pushed',(recs,maxseq),None))
                except Exception as e: self.q.put(('error',str(e),None))
            threading.Thread(target=work,daemon=True).start()
            self.app.after(200,self._poll); return
        if kind=='pushed':
            recs,maxseq=payload
            cloud_sync.mark_pushed(d.c,recs,maxseq)
            return self._success(len(recs))
    def _success(self,sent):
        self.busy=False; self.failed=False; self.last_ok=time.time()
        stamp=now(); self.db().set_setting('cloud_last_sync',stamp)
        recu=getattr(self,'received',0)
        self.status(f'Cloud : à jour ({stamp[11:16]})\n{recu} reçu(s), {sent} envoyé(s)','#9be7b0')
        if recu: self.app.refresh_after_sync()
        if self.manual: messagebox.showinfo('Cloud',f'Synchronisation terminée.\nChangements reçus : {recu}\nChangements envoyés : {sent}')
        if self.done_cb:
            try: self.done_cb()
            except Exception: pass
    def _fail(self,msg):
        self.busy=False; self.failed=True
        try: n=cloud_sync.pending_count(self.db().c)
        except Exception: n=0
        self.status(f'Cloud : hors ligne\n{n} modification(s) en attente','#ffb4b4')
        if self.manual: messagebox.showerror('Cloud',f'Synchronisation impossible :\n{msg}\n\nLes données restent enregistrées sur ce poste et seront envoyées plus tard.')
        if self.done_cb:
            try: self.done_cb()
            except Exception: pass

class App(tk.Tk):
    def __init__(self):
        super().__init__(); self.title(f'AMUNTCHI - Gestion de Quincaillerie  V{VERSION}'); self.geometry('1600x950'); self.minsize(1250,760); maximize(self); self.configure(bg='#f4f7fb'); self.db=DB(); self.user=None; self.cart=[]; self._report_rows=[]; self._report_totals={}; self._screen=None; self.cloud=CloudManager(self)
        st=ttk.Style(self); st.theme_use('clam'); st.configure('Treeview',rowheight=34,font=('Segoe UI',10),background='white',fieldbackground='white',foreground='#173a5e'); st.configure('Treeview.Heading',rowheight=34,font=('Segoe UI',10,'bold'),background='#eaf1f8',foreground='#173a5e'); st.map('Treeview',background=[('selected','#dcecff')],foreground=[('selected','#173a5e')])
        self.login()
    def clear(self):
        for w in self.winfo_children(): w.destroy()
    def audit(self,a,old='',new='',reason=''):
        if self.user: self.db.q('INSERT INTO audit(user_id,action,date,old_value,new_value,reason) VALUES(?,?,?,?,?,?)',(self.user['id'],a,now(),str(old),str(new),reason))
    def allowed(self,p): return 'all' in PERMS.get(self.user['role'],set()) or p in PERMS.get(self.user['role'],set())
    def login(self):
        self.clear(); self.configure(bg='#eef2f6'); f=tk.Frame(self,bg='white',bd=1,relief='solid'); f.place(relx=.5,rely=.5,anchor='center',width=760,height=820)
        if LOGO.exists() and Image:
            im=Image.open(LOGO); im.thumbnail((300,200)); self.limg=ImageTk.PhotoImage(im); tk.Label(f,image=self.limg,bg='white').pack(pady=20)
        tk.Label(f,text='AMUNTCHI',font=('Segoe UI',36,'bold'),fg='#173a5e',bg='white').pack(pady=(2,4)); tk.Label(f,text=f'Application de gestion de quincaillerie — V{VERSION}',font=('Segoe UI',14),fg='#666',bg='white').pack(pady=(0,30))
        tk.Label(f,text='Identifiant',font=('Segoe UI',13,'bold'),bg='white').pack(anchor='w',padx=85); u=tk.Entry(f,font=('Segoe UI',15)); u.pack(fill='x',padx=85,pady=10,ipady=6)
        tk.Label(f,text='Mot de passe',font=('Segoe UI',13,'bold'),bg='white').pack(anchor='w',padx=85); p=tk.Entry(f,show='•',font=('Segoe UI',15)); p.pack(fill='x',padx=85,pady=10,ipady=6)
        def go(e=None):
            ident=u.get().strip(); pwd=p.get()
            r=self.db.one('SELECT * FROM users WHERE lower(username)=lower(?) AND active=1',(ident,))
            if not r or not verify_password(pwd,r['password']):
                messagebox.showerror('Connexion','Identifiant ou mot de passe incorrect'); return
            # Re-hachage transparent si la valeur stockée est encore en clair.
            if not is_hashed(r['password']):
                self.db.q('UPDATE users SET password=? WHERE id=?',(hash_password(pwd),r['id']))
            self.user=dict(r); self.audit('Connexion'); self.main()
            if pwd=='admin' and r['username']=='admin':
                messagebox.showwarning('Sécurité','Vous utilisez encore le mot de passe par défaut.\nModifiez-le dans Paramètres > Sécurité.')
        tk.Button(f,text='SE CONNECTER',command=go,bg='#062d5c',fg='white',relief='flat',font=('Segoe UI',14,'bold'),height=3).pack(fill='x',padx=85,pady=30)
        tk.Label(f,text='Compte initial : admin / admin  (à modifier après la première connexion)',font=('Segoe UI',11),fg='#888',bg='white').pack(pady=8); p.bind('<Return>',go); u.focus()
    def main(self):
        self.clear(); self.configure(bg='#f4f7fb'); self.side=tk.Frame(self,bg='#062d5c',width=270); self.side.pack(side='left',fill='y'); self.side.pack_propagate(False); self.body=tk.Frame(self,bg='#f4f7fb'); self.body.pack(side='right',fill='both',expand=True)
        
        if LOGO.exists() and Image:
            try:
                im=Image.open(LOGO).convert('RGB'); im.thumbnail((76,76)); self.side_logo=ImageTk.PhotoImage(im); tk.Label(self.side,image=self.side_logo,bg='#062d5c').pack(pady=(18,2))
            except Exception: pass
        tk.Label(self.side,text='AMUNTCHI',font=('Segoe UI',20,'bold'),fg='white',bg='#062d5c').pack(pady=(2,2)); tk.Label(self.side,text=self.user['name'],fg='white',bg='#062d5c').pack(); tk.Label(self.side,text=self.user['role'],fg='#bcd0e2',bg='#062d5c').pack(pady=(0,18))
        self._first_screen=None
        menus=[('Tableau de bord','dashboard'),('Produits (boutique)','products'),('Stock (magasin)','stock_view'),
               ('Transferts','transfers'),('Achats / réceptions','purchases'),('Ventes / caisse','sales'),
               ('Clients & crédits','clients'),('Dépenses','expenses'),('Inventaires','inventory'),
               ('Rapports','reports'),('Paramètres','settings')]
        for label,cmd in menus:
            perm={'dashboard':'dashboard','products':'products','stock_view':'stock','transfers':'transfers','purchases':'purchases','sales':'sales','clients':'clients','expenses':'expenses','inventory':'inventory','reports':'reports','settings':'admin'}[cmd]
            if self.allowed(perm):
                self._first_screen = self._first_screen or cmd
                tk.Button(self.side,text=label,command=lambda c=cmd:self.show(c),anchor='w',bg='#062d5c',fg='white',
                          activebackground='#285a85',relief='flat',bd=0,font=('Segoe UI',10,'bold'),padx=20,pady=9).pack(fill='x')
        if self.user['role']=='Administrateur':
            for label,cmd in [('Utilisateurs','users'),('Journal d’audit','audit_view')]: tk.Button(self.side,text='   '+label,command=getattr(self,cmd),anchor='w',bg='#062d5c',fg='white',relief='flat',bd=0,padx=18,pady=9).pack(fill='x')
        tk.Button(self.side,text='Mon mot de passe',command=self.change_own_password,anchor='w',bg='#062d5c',fg='white',activebackground='#285a85',relief='flat',bd=0,font=('Segoe UI',10,'bold'),padx=18,pady=9).pack(fill='x')
        tk.Button(self.side,text='Déconnexion',command=self.login,anchor='w',bg='#062d5c',fg='#ffdede',relief='flat',bd=0,padx=18,pady=15).pack(side='bottom',fill='x')
        self.sync_lbl=tk.Label(self.side,text='Cloud : non configuré' if not self.cloud.configured() else 'Cloud : en attente',fg='#bcd0e2',bg='#062d5c',font=('Segoe UI',8),justify='left',cursor='hand2')
        self.sync_lbl.pack(side='bottom',anchor='w',padx=18,pady=(4,0))
        self.sync_lbl.bind('<Button-1>',lambda e:self.cloud.sync(manual=True))
        self.show(self._first_screen or 'sales')
        if self.cloud.configured(): self.after(1500,self.cloud.sync)
    def show(self,name):
        self._screen=name; getattr(self,name)()
    def refresh_after_sync(self):
        """Réaffiche l'écran courant après réception de données (sauf caisse et fenêtres ouvertes)."""
        if self._screen in ('dashboard','products','stock_view','transfers','purchases','clients','expenses','inventory'):
            if not any(isinstance(w,tk.Toplevel) for w in self.winfo_children()):
                try: getattr(self,self._screen)()
                except Exception: pass
    def header(self,title,sub=''):
        # Mémorise l'écran affiché (pour le réactualiser après une synchronisation).
        try: self._screen=sys._getframe(1).f_code.co_name
        except Exception: pass
        # Libère les raccourcis molette posés par une page défilante précédente.
        for seq in ('<MouseWheel>','<Button-4>','<Button-5>'):
            try: self.unbind_all(seq)
            except Exception: pass
        for w in self.body.winfo_children(): w.destroy()
        h=tk.Frame(self.body,bg='white',height=118,highlightbackground='#dce5ef',highlightthickness=1); h.pack(fill='x'); h.pack_propagate(False)
        left=tk.Frame(h,bg='white'); left.pack(side='left',fill='y',padx=26)
        tk.Label(left,text=title,font=('Segoe UI',24,'bold'),fg='#173a5e',bg='white').pack(anchor='w',pady=(20,0)); tk.Label(left,text=sub,fg='#6b7b8c',bg='white',font=('Segoe UI',10)).pack(anchor='w',pady=(2,0))
        right=tk.Frame(h,bg='white'); right.pack(side='right',fill='y',padx=24)
        tk.Label(right,text=french_date(),font=('Segoe UI',10,'bold'),fg='#173a5e',bg='white').pack(anchor='e',pady=(17,0)); tk.Label(right,text=datetime.datetime.now().strftime('%H:%M:%S'),font=('Segoe UI',11),fg='#173a5e',bg='white').pack(anchor='e'); tk.Label(right,text=self.user['name']+'   '+self.user['role'],font=('Segoe UI',9),fg='#4b6380',bg='white').pack(anchor='e',pady=(4,0))
    def card(self,p,t,v,s,accent='#1e73e8',icon='●'):
        """Carte d'indicateur : hauteur souple pour que titre, valeur et
        sous-titre restent toujours entièrement visibles."""
        f=tk.Frame(p,bg='white',highlightbackground='#dce5ef',highlightthickness=1)
        f.pack(side='left',fill='both',expand=True,padx=6,pady=4)
        ico=tk.Frame(f,bg=accent,width=64); ico.pack(side='left',fill='y'); ico.pack_propagate(False)
        tk.Label(ico,text=str(icon),font=('Segoe UI',13,'bold'),fg='white',bg=accent,
                 wraplength=58,justify='center').pack(expand=True)
        txt=tk.Frame(f,bg='white'); txt.pack(side='left',fill='both',expand=True,padx=14,pady=14)
        tk.Label(txt,text=t,font=('Segoe UI',10,'bold'),fg='#334d68',bg='white',
                 anchor='w',justify='left',wraplength=260).pack(anchor='w',fill='x')
        tk.Label(txt,text=v,font=('Segoe UI',15,'bold'),fg='#082b55',bg='white',
                 anchor='w',justify='left',wraplength=260).pack(anchor='w',fill='x',pady=(5,3))
        tk.Label(txt,text=s,font=('Segoe UI',9),fg='#75869a',bg='white',
                 anchor='w',justify='left',wraplength=260).pack(anchor='w',fill='x',pady=(0,2))
        return f
    def table(self,parent,heads,rows):
        """Tableau avec défilement vertical ET horizontal : aucune colonne n'est perdue."""
        fr=tk.Frame(parent,bg='white'); fr.pack(fill='both',expand=True,padx=20,pady=10)
        cols=[x[0] for x in heads]
        tv=ttk.Treeview(fr,columns=cols,show='headings')
        for c,l,w in heads:
            tv.heading(c,text=l,anchor='center'); tv.column(c,width=w,minwidth=w,anchor='center',stretch=True)
        ys=ttk.Scrollbar(fr,orient='vertical',command=tv.yview)
        xs=ttk.Scrollbar(fr,orient='horizontal',command=tv.xview)
        tv.configure(yscrollcommand=ys.set,xscrollcommand=xs.set)
        tv.grid(row=0,column=0,sticky='nsew'); ys.grid(row=0,column=1,sticky='ns'); xs.grid(row=1,column=0,sticky='ew')
        fr.rowconfigure(0,weight=1); fr.columnconfigure(0,weight=1)
        for r in rows: tv.insert('', 'end', values=list(r))
        return tv
    def dashboard(self):
        self.header('Tableau de bord','Synthèse activité, stock magasin, boutique, caisse et crédits')
        wrap=tk.Frame(self.body,bg='#f4f7fb'); wrap.pack(fill='both',expand=True,padx=16,pady=12)
        top=tk.Frame(wrap,bg='#f4f7fb'); top.pack(fill='x')
        mid_id=self.db.magasin_id(); bid=self.db.boutique_id()
        jour=today()+' 00:00:00'
        v=self.db.one('SELECT COALESCE(SUM(total),0) t,COALESCE(SUM(paid),0) p,COUNT(*) n FROM sales WHERE date>=?',(jour,))
        ach=self.db.one('SELECT COALESCE(SUM(total),0) t,COUNT(*) n FROM purchases WHERE date>=?',(jour,))
        qm=float(self.db.one('SELECT COALESCE(SUM(qty),0) v FROM stocks WHERE location_id=?',(mid_id,))['v'])
        qb=float(self.db.one('SELECT COALESCE(SUM(qty),0) v FROM stocks WHERE location_id=?',(bid,))['v'])
        cred=self.db.one('SELECT COALESCE(SUM(balance),0) b,COUNT(*) n FROM credits WHERE balance>0')
        self.card(top,'Ventes du jour',money(v['t']),f"{v['n']} vente(s)  |  encaissé : {money(v['p'])}",'#18b65b','VTE')
        self.card(top,'Achats du jour',money(ach['t']),f"{ach['n']} réception(s) au magasin",'#1e73e8','ACH')
        self.card(top,'Quantité en stock',f'{qm+qb:g}',f'Magasin : {qm:g}  |  Boutique : {qb:g}','#f5a400','STK')
        self.card(top,'Crédits en cours',money(cred['b']),f"{cred['n']} crédit(s) client ouvert(s)",'#ef3038','CRD')
        mid=tk.Frame(wrap,bg='#f4f7fb',height=330); mid.pack(fill='both',expand=True,pady=(8,0)); mid.pack_propagate(False)
        sales_panel=tk.Frame(mid,bg='white',highlightbackground='#dce5ef',highlightthickness=1); sales_panel.pack(side='left',fill='both',expand=True,padx=6,pady=6)
        tk.Label(sales_panel,text='Évolution des ventes (7 derniers jours)',font=('Segoe UI',12,'bold'),fg='#173a5e',bg='white').pack(anchor='w',padx=18,pady=(14,4))
        c=tk.Canvas(sales_panel,bg='white',highlightthickness=0,height=245); c.pack(fill='both',expand=True,padx=14,pady=8); c.bind('<Configure>',lambda e:self._draw_sales_chart(c))
        stock_panel=tk.Frame(mid,bg='white',highlightbackground='#dce5ef',highlightthickness=1); stock_panel.pack(side='left',fill='both',expand=True,padx=6,pady=6)
        tk.Label(stock_panel,text='Répartition du stock par catégorie',font=('Segoe UI',12,'bold'),fg='#173a5e',bg='white').pack(anchor='w',padx=18,pady=(14,4))
        c2=tk.Canvas(stock_panel,bg='white',highlightthickness=0,height=245); c2.pack(fill='both',expand=True,padx=14,pady=8); c2.bind('<Configure>',lambda e:self._draw_stock_chart(c2))
        low=self.db.rows('''SELECT p.code,p.name,COALESCE(c.name,'-') category,COALESCE(u.name,'-') unit,p.min_stock,
                COALESCE((SELECT qty FROM stocks s WHERE s.product_id=p.id AND s.location_id=?),0) qm,
                COALESCE((SELECT qty FROM stocks s WHERE s.product_id=p.id AND s.location_id=?),0) qb,
                COALESCE((SELECT SUM(qty) FROM stocks s WHERE s.product_id=p.id),0) qty
                FROM products p LEFT JOIN categories c ON c.id=p.category_id
                LEFT JOIN units u ON u.id=p.sale_unit_id WHERE p.active=1
                GROUP BY p.id HAVING qty<=p.min_stock ORDER BY qty''',(mid_id,bid))
        alert=tk.Frame(wrap,bg='white',highlightbackground='#dce5ef',highlightthickness=1); alert.pack(fill='both',expand=True,pady=(8,0))
        head=tk.Frame(alert,bg='white'); head.pack(fill='x')
        tk.Label(head,text=f'Alertes stock  ({len(low)} produit(s) sous le seuil minimum)',font=('Segoe UI',12,'bold'),fg='#173a5e',bg='white').pack(side='left',padx=18,pady=12)
        tk.Button(head,text='Voir le stock magasin',command=self.stock_view,bg='#e7f0ff',fg='#1769d3',relief='flat',font=('Segoe UI',9,'bold'),padx=12,pady=4).pack(side='right',padx=16,pady=9)
        cols=[('code','Référence',110),('name','Produit',240),('category','Catégorie',160),('qm','Magasin',100),
              ('qb','Boutique',100),('qty','Total',100),('min','Seuil',90),('unit','Unité',110)]
        self.table(alert,cols,[(r['code'],r['name'],r['category'],f"{float(r['qm']):g}",f"{float(r['qb']):g}",
                               f"{float(r['qty']):g}",f"{float(r['min_stock'] or 0):g}",r['unit']) for r in low[:40]])
        foot=tk.Frame(wrap,bg='#f4f7fb'); foot.pack(fill='x',pady=(8,0))
        tk.Label(foot,text='Mini Quincaillerie AMUNTCHI  |  Tchirozerine - Niger',font=('Segoe UI',9,'bold'),fg='#173a5e',bg='#f4f7fb').pack(side='left')
        tk.Label(foot,text='Base de données : amuntchi_v2.db   |   Connecté',font=('Segoe UI',9),fg='#2d7d46',bg='#f4f7fb').pack(side='right')
    def _draw_sales_chart(self,c):
        c.delete('all'); w=max(c.winfo_width(),500); h=max(c.winfo_height(),220); left,bottom,right,top=55,h-38,w-20,18
        for i in range(5):
            y=top+i*(bottom-top)/4; c.create_line(left,y,right,y,fill='#e8eef5'); c.create_text(left-8,y,text=str(int((4-i)*25000)),anchor='e',fill='#738397',font=('Segoe UI',8))
        vals=[]; labels=[]
        for d in range(6,-1,-1):
            day=(datetime.date.today()-datetime.timedelta(days=d)).isoformat(); end=(datetime.date.fromisoformat(day)+datetime.timedelta(days=1)).isoformat(); r=self.db.one("SELECT COALESCE(SUM(total),0) v FROM sales WHERE date>=? AND date<?",(day+' 00:00:00',end+' 00:00:00')); vals.append(float(r['v'])); labels.append(datetime.date.fromisoformat(day).strftime('%d/%m'))
        mx=max(max(vals),1); pts=[]
        for i,v in enumerate(vals):
            x=left+i*(right-left)/6; y=bottom-(v/mx)*(bottom-top-12); pts += [x,y]; c.create_oval(x-4,y-4,x+4,y+4,fill='#1769d3',outline='white',width=1); c.create_text(x,bottom+16,text=labels[i],fill='#738397',font=('Segoe UI',8))
        if len(pts)>=4: c.create_line(*pts,fill='#1769d3',width=3,smooth=True); c.create_text(right-5,top+8,text='Ventes (FCFA)',anchor='e',fill='#1769d3',font=('Segoe UI',9,'bold'))
    def _draw_stock_chart(self,c):
        c.delete('all'); rows=self.db.rows('''SELECT COALESCE(cat.name,'Sans catégorie') name,COALESCE(SUM(s.qty),0) qty FROM products p LEFT JOIN stocks s ON s.product_id=p.id LEFT JOIN categories cat ON cat.id=p.category_id GROUP BY cat.id,cat.name HAVING qty>0 ORDER BY qty DESC LIMIT 6''')
        if not rows:
            c.create_oval(45,30,205,190,outline='#c8ced6',width=38); c.create_text(125,110,text='Aucune\ndonnée',font=('Segoe UI',11,'bold'),fill='#173a5e'); c.create_text(245,105,text='Les données apparaîtront dès\nl’enregistrement des produits.',anchor='w',font=('Segoe UI',9),fill='#6d7d90'); return
        total=sum(float(r['qty']) for r in rows); cx,cy=125,110; radius=78; start=0; colors=['#1769d3','#18b65b','#f5a400','#ef3038','#7c4dff','#00a6a6']
        for i,r in enumerate(rows):
            extent=360*float(r['qty'])/total; c.create_arc(cx-radius,cy-radius,cx+radius,cy+radius,start=start,extent=extent,fill=colors[i%len(colors)],outline='white',width=2); start+=extent
        c.create_oval(cx-40,cy-40,cx+40,cy+40,fill='white',outline='white'); c.create_text(cx,cy,text=f'{total:g}',font=('Segoe UI',11,'bold'),fill='#173a5e')
        for i,r in enumerate(rows): c.create_rectangle(245,30+i*28,257,42+i*28,fill=colors[i%len(colors)],outline=''); c.create_text(265,36+i*28,text=f"{r['name']}  ({float(r['qty']):g})",anchor='w',font=('Segoe UI',8),fill='#53677e')
    def _product_photo_path(self,raw,pid=None):
        if raw and not str(raw).startswith('cloud:'):
            p=Path(str(raw))
            if p.exists(): return p
            alt=ASSETS/p.name
            if alt.exists(): return alt
        if pid is not None:
            # Photo prise sur un téléphone (ou sur un autre poste) : copie Cloud.
            r=self.db.one('SELECT data FROM photos WHERE id=?',(pid,))
            if r and r['data']:
                return cloud_sync.photo_from_base64(r['data'],ASSETS/f'cloud_photo_{pid}.jpg')
        return None
    def _product_table(self,parent,rows,place_label):
        cols=[('id','ID',60),('code','Référence',120),('name','Désignation',260),('brand','Marque',130),
              ('category','Catégorie',150),('qty',f'Quantité {place_label}',140),('unit','Unité de vente',130),
              ('sell','Prix de vente',130),('min','Seuil',80)]
        data=[(r['id'],r['code'],r['name'],r['brand'] or '',r['category'],f"{float(r['qty']):g}",
               r['sale_unit'],money(r['sale_price']),f"{float(r['min_stock'] or 0):g}") for r in rows]
        tv=self.table(parent,cols,data)
        tv.bind('<Double-1>',lambda e: self.product_detail(int(tv.item(tv.focus(),'values')[0])) if tv.focus() else None)
        return tv
    def products(self):
        """Onglet Produits : articles disponibles à la BOUTIQUE (issus des transferts)."""
        self.header('Produits (boutique)','Articles disponibles à la vente, transférés du magasin vers la boutique')
        bid=self.db.boutique_id()
        bar=tk.Frame(self.body,bg='#f4f6f8'); bar.pack(fill='x',padx=20,pady=(10,8))
        search=tk.StringVar(); se=tk.Entry(bar,textvariable=search,width=32,font=('Segoe UI',10)); se.pack(side='left'); se.bind('<Return>',lambda e:load())
        tk.Button(bar,text='Rechercher',command=lambda:load(),bg='#173a5e',fg='white',relief='flat',padx=12,pady=4).pack(side='left',padx=5)
        show_empty=tk.BooleanVar(value=False)
        tk.Checkbutton(bar,text='Afficher aussi les articles épuisés en boutique',variable=show_empty,bg='#f4f6f8',command=lambda:load()).pack(side='left',padx=12)
        tk.Button(bar,text='+ Nouveau produit',command=self.product_form,bg='#2e7d32',fg='white',relief='flat',padx=12,pady=4).pack(side='right',padx=5)
        tk.Button(bar,text='Fiche du produit',command=lambda:open_selected(),bg='#1769d3',fg='white',relief='flat',padx=12,pady=4).pack(side='right',padx=5)
        tk.Button(bar,text='Importer CSV',command=self.import_products,relief='flat',padx=12,pady=4).pack(side='right',padx=5)
        info=tk.Label(self.body,text='',bg='#f4f7fb',fg='#53677e',font=('Segoe UI',9)); info.pack(anchor='w',padx=25)
        zone=tk.Frame(self.body,bg='white'); zone.pack(fill='both',expand=True,padx=20,pady=5)
        holder={}
        def open_selected():
            tv=holder.get('tv')
            if tv is None or not tv.focus(): messagebox.showwarning('Produits','Sélectionnez d’abord un produit.'); return
            self.product_detail(int(tv.item(tv.focus(),'values')[0]))
        def load():
            for w in zone.winfo_children(): w.destroy()
            rows=self.db.products_at(bid,only_positive=not show_empty.get())
            rows,note=smart_search(rows,search.get())
            if not rows:
                tk.Label(zone,text=(note if search.get().strip() else 'Aucun produit en boutique. Utilisez l’onglet Transferts pour envoyer des articles du magasin vers la boutique.'),
                         bg='white',fg='#8a5a00',font=('Segoe UI',10),wraplength=900,justify='left').pack(anchor='w',padx=20,pady=25)
            else:
                holder['tv']=self._product_table(zone,rows,'boutique')
            total=sum(float(r['qty']) for r in rows)
            info.config(text=(note+'  |  ' if note else '')+f'{len(rows)} article(s) en boutique  |  quantité totale : {total:g}  |  double-cliquez sur une ligne pour voir la fiche complète',fg='#b3261e' if note.startswith('Aucun') else '#53677e')
        load()
    def stock_view(self):
        """Onglet Stock : articles disponibles au MAGASIN (issus des achats/réceptions)."""
        self.header('Stock (magasin)','Articles reçus par achat ou réception, en attente de transfert vers la boutique')
        mid=self.db.magasin_id()
        bar=tk.Frame(self.body,bg='#f4f6f8'); bar.pack(fill='x',padx=20,pady=(10,8))
        search=tk.StringVar(); se=tk.Entry(bar,textvariable=search,width=32,font=('Segoe UI',10)); se.pack(side='left'); se.bind('<Return>',lambda e:load())
        tk.Button(bar,text='Rechercher',command=lambda:load(),bg='#173a5e',fg='white',relief='flat',padx=12,pady=4).pack(side='left',padx=5)
        show_all=tk.BooleanVar(value=False)
        tk.Checkbutton(bar,text='Afficher aussi les articles à zéro',variable=show_all,bg='#f4f6f8',command=lambda:load()).pack(side='left',padx=12)
        tk.Button(bar,text='+ Réception / achat',command=self.purchase_form,bg='#2e7d32',fg='white',relief='flat',padx=12,pady=4).pack(side='right',padx=5)
        tk.Button(bar,text='Transférer vers la boutique',command=self.transfer_form,bg='#1769d3',fg='white',relief='flat',padx=12,pady=4).pack(side='right',padx=5)
        tk.Button(bar,text='+ Nouveau produit',command=self.product_form,relief='flat',padx=12,pady=4).pack(side='right',padx=5)
        info=tk.Label(self.body,text='',bg='#f4f7fb',fg='#53677e',font=('Segoe UI',9)); info.pack(anchor='w',padx=25)
        zone=tk.Frame(self.body,bg='white'); zone.pack(fill='both',expand=True,padx=20,pady=5)
        def load():
            for w in zone.winfo_children(): w.destroy()
            rows=self.db.products_at(mid,only_positive=not show_all.get())
            rows,note=smart_search(rows,search.get())
            if not rows:
                tk.Label(zone,text=(note if search.get().strip() else 'Aucun article au magasin. Enregistrez une réception (Achats / réceptions) pour alimenter le stock.'),
                         bg='white',fg='#8a5a00',font=('Segoe UI',10),wraplength=900,justify='left').pack(anchor='w',padx=20,pady=25)
            else:
                self._product_table(zone,rows,'magasin')
            total=sum(float(r['qty']) for r in rows)
            valeur=sum(float(r['qty'])*float(r['purchase_price'] or 0) for r in rows)
            info.config(text=(note+'  |  ' if note else '')+f'{len(rows)} article(s) au magasin  |  quantité totale : {total:g}  |  valeur d’achat : {money(valeur)}',fg='#b3261e' if note.startswith('Aucun') else '#53677e')
        load()
    def product_detail(self,pid):
        """Fiche complète d'un produit : photo, caractéristiques, stocks et mouvements."""
        r=self.db.one('''SELECT p.*,COALESCE(c.name,'-') category,COALESCE(su.name,'-') stock_unit,
                COALESCE(vu.name,'-') sale_unit FROM products p
                LEFT JOIN categories c ON c.id=p.category_id
                LEFT JOIN units su ON su.id=p.stock_unit_id
                LEFT JOIN units vu ON vu.id=p.sale_unit_id WHERE p.id=?''',(pid,))
        if not r: return
        w=tk.Toplevel(self); w.title(f"Fiche produit — {r['name']}"); w.geometry('980x720'); w.configure(bg='#f4f7fb'); w.transient(self)
        head=tk.Frame(w,bg='white',highlightbackground='#dce5ef',highlightthickness=1); head.pack(fill='x',padx=16,pady=(16,8))
        photo_box=tk.Frame(head,bg='#eef2f6',width=230,height=230); photo_box.pack(side='left',padx=16,pady=16); photo_box.pack_propagate(False)
        path=self._product_photo_path(r['photo'],pid)
        if path and Image:
            try:
                im=Image.open(path).convert('RGB'); im.thumbnail((220,220))
                w._img=ImageTk.PhotoImage(im); tk.Label(photo_box,image=w._img,bg='#eef2f6').pack(expand=True)
            except Exception:
                tk.Label(photo_box,text='Photo illisible',bg='#eef2f6',fg='#8a5a00').pack(expand=True)
        else:
            tk.Label(photo_box,text='Aucune photo\nenregistrée',bg='#eef2f6',fg='#7b8b9c',font=('Segoe UI',10),justify='center').pack(expand=True)
        infos=tk.Frame(head,bg='white'); infos.pack(side='left',fill='both',expand=True,padx=10,pady=16)
        tk.Label(infos,text=r['name'],font=('Segoe UI',18,'bold'),fg='#173a5e',bg='white',wraplength=560,justify='left').pack(anchor='w')
        tk.Label(infos,text=f"Référence : {r['code']}",font=('Segoe UI',11),fg='#53677e',bg='white').pack(anchor='w',pady=(2,10))
        mid=self.db.magasin_id(); bid=self.db.boutique_id()
        qm=self.db.stock(pid,mid); qb=self.db.stock(pid,bid)
        champs=[('Catégorie',r['category']),('Marque',r['brand'] or '-'),
                ('Unité de stockage',r['stock_unit']),('Unité de vente',r['sale_unit']),
                ('Prix d’achat',money(r['purchase_price'])),('Prix de vente',money(r['sale_price'])),
                ('Marge unitaire',money(float(r['sale_price'] or 0)-float(r['purchase_price'] or 0))),
                ('Seuil minimum',f"{float(r['min_stock'] or 0):g}"),
                ('Quantité au magasin',f'{qm:g}'),('Quantité en boutique',f'{qb:g}'),
                ('Quantité totale',f'{qm+qb:g}'),('Statut',"Actif" if r['active'] else "Inactif")]
        grid=tk.Frame(infos,bg='white'); grid.pack(anchor='w',fill='x')
        for i,(k,v) in enumerate(champs):
            tk.Label(grid,text=k+' :',font=('Segoe UI',9,'bold'),fg='#53677e',bg='white',anchor='w').grid(row=i//2,column=(i%2)*2,sticky='w',padx=(0,8),pady=3)
            tk.Label(grid,text=str(v),font=('Segoe UI',10,'bold'),fg='#082b55',bg='white',anchor='w',wraplength=210,justify='left').grid(row=i//2,column=(i%2)*2+1,sticky='w',padx=(0,28),pady=3)
        if r['notes']:
            tk.Label(infos,text='Observations : '+str(r['notes']),font=('Segoe UI',9),fg='#53677e',bg='white',wraplength=560,justify='left').pack(anchor='w',pady=(10,0))
        mv=tk.Frame(w,bg='white',highlightbackground='#dce5ef',highlightthickness=1); mv.pack(fill='both',expand=True,padx=16,pady=8)
        tk.Label(mv,text='Derniers mouvements de cet article',font=('Segoe UI',11,'bold'),fg='#173a5e',bg='white').pack(anchor='w',padx=16,pady=(12,2))
        rows=self.db.rows('''SELECT m.date,m.type,m.qty,COALESCE(l.name,'-') lieu,COALESCE(u.name,'-') agent,COALESCE(m.ref,'') ref
                FROM movements m LEFT JOIN locations l ON l.id=m.location_id
                LEFT JOIN users u ON u.id=m.user_id WHERE m.product_id=? ORDER BY m.date DESC,m.id DESC LIMIT 40''',(pid,))
        if rows:
            self.table(mv,[('date','Date',150),('type','Opération',180),('qty','Quantité',110),('lieu','Emplacement',140),('agent','Utilisateur',150),('ref','Réf.',90)],
                       [(x['date'],x['type'],f"{float(x['qty']):+g}",x['lieu'],x['agent'],x['ref']) for x in rows])
        else:
            tk.Label(mv,text='Aucun mouvement enregistré pour cet article.',bg='white',fg='#7b8b9c').pack(anchor='w',padx=18,pady=16)
        foot=tk.Frame(w,bg='#f4f7fb'); foot.pack(fill='x',padx=16,pady=(0,14))
        tk.Button(foot,text='Modifier le produit',command=lambda:(w.destroy(),self.product_form(pid)),bg='#173a5e',fg='white',relief='flat',font=('Segoe UI',10,'bold'),padx=16,pady=8).pack(side='left')
        tk.Button(foot,text='Fermer',command=w.destroy,relief='flat',padx=16,pady=8).pack(side='right')
    def capture_photo(self,target_var,parent=None):
        """Prise de photo par scan (webcam). Nécessite opencv-python."""
        try:
            import cv2
        except Exception:
            messagebox.showwarning('Scan photo',
                'Le module de capture n’est pas installé.\n\n'
                'Installez-le une seule fois avec la commande :\n    pip install opencv-python\n\n'
                'Vous pouvez en attendant choisir une photo depuis la galerie.',parent=parent or self)
            return
        cam=None
        for index in (0,1,2):
            c=cv2.VideoCapture(index)
            if c is not None and c.isOpened(): cam=c; break
            if c is not None: c.release()
        if cam is None:
            messagebox.showerror('Scan photo','Aucune caméra détectée sur cet ordinateur.',parent=parent or self); return
        win=tk.Toplevel(parent or self); win.title('Scanner la photo du produit'); win.configure(bg='#0b1a2b')
        preview=tk.Label(win,bg='#0b1a2b'); preview.pack(padx=12,pady=12)
        tk.Label(win,text='Placez le produit devant la caméra puis cliquez sur « Capturer ».',
                 bg='#0b1a2b',fg='white',font=('Segoe UI',10)).pack(pady=(0,8))
        state={'frame':None,'running':True}
        def refresh():
            if not state['running']: return
            ok,frame=cam.read()
            if ok:
                state['frame']=frame
                if Image:
                    rgb=cv2.cvtColor(frame,cv2.COLOR_BGR2RGB)
                    im=Image.fromarray(rgb); im.thumbnail((640,480))
                    win._imgtk=ImageTk.PhotoImage(im); preview.configure(image=win._imgtk)
            win.after(40,refresh)
        def stop():
            state['running']=False
            try: cam.release()
            except Exception: pass
            win.destroy()
        def shoot():
            frame=state['frame']
            if frame is None:
                messagebox.showwarning('Scan photo','Image non disponible, réessayez.',parent=win); return
            ASSETS.mkdir(parents=True,exist_ok=True)
            fn=ASSETS/f"produit_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
            cv2.imwrite(str(fn),frame)
            target_var.set(str(fn)); stop()
            messagebox.showinfo('Scan photo','Photo enregistrée.',parent=parent or self)
        btns=tk.Frame(win,bg='#0b1a2b'); btns.pack(pady=(0,14))
        tk.Button(btns,text='CAPTURER',command=shoot,bg='#2e7d32',fg='white',relief='flat',font=('Segoe UI',11,'bold'),padx=24,pady=8).pack(side='left',padx=8)
        tk.Button(btns,text='Annuler',command=stop,relief='flat',padx=20,pady=8).pack(side='left',padx=8)
        win.protocol('WM_DELETE_WINDOW',stop); refresh()
    def pick_photo_from_gallery(self,target_var,parent=None):
        f=filedialog.askopenfilename(title='Choisir une photo dans la galerie',
            filetypes=[('Images','*.jpg *.jpeg *.png *.webp *.bmp'),('Tous les fichiers','*.*')])
        if not f: return
        try:
            ASSETS.mkdir(parents=True,exist_ok=True)
            dest=ASSETS/f"produit_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}{Path(f).suffix.lower()}"
            shutil.copy2(f,dest); target_var.set(str(dest))
        except Exception:
            target_var.set(f)
    def product_form(self,pid=None):
        r=self.db.one('SELECT * FROM products WHERE id=?',(pid,)) if pid else None
        w=tk.Toplevel(self); w.title('Modifier le produit' if pid else 'Nouveau produit')
        w.geometry('640x820'); w.configure(bg='#f4f7fb'); w.transient(self); w.grab_set()
        tk.Label(w,text='Modifier le produit' if pid else 'Enregistrer un nouveau produit',
                 font=('Segoe UI',16,'bold'),fg='#173a5e',bg='#f4f7fb').pack(pady=(16,10))
        actions={}
        btns=tk.Frame(w,bg='#f4f7fb'); btns.pack(side='bottom',fill='x',padx=30,pady=14)
        tk.Button(btns,text='ANNULER',command=w.destroy,bg='#777777',fg='white',relief='flat',font=('Segoe UI',10,'bold'),pady=10).pack(side='left',fill='x',expand=True,padx=(0,6))
        tk.Button(btns,text='ENREGISTRER',command=lambda:actions['save'](),bg='#173a5e',fg='white',relief='flat',font=('Segoe UI',11,'bold'),pady=10).pack(side='left',fill='x',expand=True,padx=(6,0))
        canvas=tk.Canvas(w,bg='#f4f7fb',highlightthickness=0); canvas.pack(side='left',fill='both',expand=True,padx=(24,0))
        sbar=ttk.Scrollbar(w,orient='vertical',command=canvas.yview); sbar.pack(side='right',fill='y')
        canvas.configure(yscrollcommand=sbar.set)
        body=tk.Frame(canvas,bg='#f4f7fb')
        holder=canvas.create_window((0,0),window=body,anchor='nw')
        def _fit(e=None):
            canvas.configure(scrollregion=canvas.bbox('all')); canvas.itemconfigure(holder,width=canvas.winfo_width()-8)
        body.bind('<Configure>',_fit); canvas.bind('<Configure>',_fit)
        canvas.bind_all('<MouseWheel>',lambda e: canvas.yview_scroll(int(-1*(e.delta/120)) if abs(e.delta)>=120 else -1*e.delta,'units'))
        canvas.bind_all('<Button-4>',lambda e: canvas.yview_scroll(-3,'units'))
        canvas.bind_all('<Button-5>',lambda e: canvas.yview_scroll(3,'units'))
        vals={k:tk.StringVar(value=str(r[k]) if r and r[k] is not None else '') for k in
              ['code','name','brand','purchase_price','sale_price','min_stock','notes']}
        for k,l in [('code','Référence du produit'),('name','Désignation'),('brand','Marque'),
                    ('purchase_price','Prix d’achat (FCFA)'),('sale_price','Prix de vente (FCFA)'),
                    ('min_stock','Seuil minimum d’alerte'),('notes','Observations')]:
            tk.Label(body,text=l,font=('Segoe UI',10,'bold'),fg='#173a5e',bg='#f4f7fb').pack(anchor='w',pady=(8,2))
            tk.Entry(body,textvariable=vals[k],font=('Segoe UI',11)).pack(fill='x',ipady=4)
        cats=self.db.rows('SELECT * FROM categories ORDER BY name')
        units=self.db.rows('SELECT * FROM units ORDER BY id')
        cv=tk.StringVar(); su=tk.StringVar(); uv=tk.StringVar()
        tk.Label(body,text='Catégorie',font=('Segoe UI',10,'bold'),fg='#173a5e',bg='#f4f7fb').pack(anchor='w',pady=(8,2))
        ttk.Combobox(body,textvariable=cv,values=[f"{x['id']} - {x['name']}" for x in cats],font=('Segoe UI',11)).pack(fill='x',ipady=3)
        unit_values=[f"{x['id']} - {x['name']}" for x in units]
        tk.Label(body,text='Unité de stockage',font=('Segoe UI',10,'bold'),fg='#173a5e',bg='#f4f7fb').pack(anchor='w',pady=(8,2))
        ttk.Combobox(body,textvariable=su,values=unit_values,state='readonly',font=('Segoe UI',11)).pack(fill='x',ipady=3)
        tk.Label(body,text='Unité de vente',font=('Segoe UI',10,'bold'),fg='#173a5e',bg='#f4f7fb').pack(anchor='w',pady=(8,2))
        ttk.Combobox(body,textvariable=uv,values=unit_values,state='readonly',font=('Segoe UI',11)).pack(fill='x',ipady=3)
        photo=tk.StringVar(value=r['photo'] if r and r['photo'] else '')
        tk.Label(body,text='Photo du produit',font=('Segoe UI',10,'bold'),fg='#173a5e',bg='#f4f7fb').pack(anchor='w',pady=(12,2))
        prow=tk.Frame(body,bg='#f4f7fb'); prow.pack(fill='x')
        tk.Entry(prow,textvariable=photo,font=('Segoe UI',9),state='readonly').pack(side='left',fill='x',expand=True,ipady=4)
        tk.Button(prow,text='Scanner (caméra)',command=lambda:self.capture_photo(photo,w),bg='#1769d3',fg='white',relief='flat',font=('Segoe UI',9,'bold'),padx=10,pady=4).pack(side='left',padx=(8,0))
        tk.Button(prow,text='Galerie',command=lambda:self.pick_photo_from_gallery(photo,w),bg='#173a5e',fg='white',relief='flat',font=('Segoe UI',9,'bold'),padx=10,pady=4).pack(side='left',padx=(6,0))
        apercu=tk.Label(body,bg='#f4f7fb',fg='#7b8b9c',font=('Segoe UI',9))
        apercu.pack(anchor='w',pady=(8,0))
        def show_preview(*a):
            path=self._product_photo_path(photo.get(),pid)
            if path and Image:
                try:
                    im=Image.open(path).convert('RGB'); im.thumbnail((150,150))
                    w._preview=ImageTk.PhotoImage(im); apercu.configure(image=w._preview,text=''); return
                except Exception: pass
            apercu.configure(image='',text='Aucune photo sélectionnée')
        photo.trace_add('write',show_preview); show_preview()
        if r and r['category_id']:
            rr=self.db.one('SELECT name FROM categories WHERE id=?',(r['category_id'],))
            if rr: cv.set(f"{r['category_id']} - {rr['name']}")
        if r and r['stock_unit_id']:
            rr=self.db.one('SELECT name FROM units WHERE id=?',(r['stock_unit_id'],))
            if rr: su.set(f"{r['stock_unit_id']} - {rr['name']}")
        if r and r['sale_unit_id']:
            rr=self.db.one('SELECT name FROM units WHERE id=?',(r['sale_unit_id'],))
            if rr: uv.set(f"{r['sale_unit_id']} - {rr['name']}")
        def resolve_category(value):
            value=value.strip()
            if not value: return None
            if ' - ' in value and _is_id(value.split(' - ',1)[0]): return int(value.split(' - ',1)[0])
            rr=self.db.one('SELECT id FROM categories WHERE lower(name)=lower(?)',(value,))
            return int(rr['id']) if rr else self.db.q('INSERT INTO categories(name) VALUES(?)',(value,))
        def resolve_unit(value):
            value=value.strip()
            if not value: return None
            if ' - ' in value and _is_id(value.split(' - ',1)[0]): return int(value.split(' - ',1)[0])
            rr=self.db.one('SELECT id FROM units WHERE lower(name)=lower(?)',(value,))
            return int(rr['id']) if rr else None
        def save():
            try:
                if not vals['code'].get().strip() or not vals['name'].get().strip():
                    raise ValueError('La référence et la désignation sont obligatoires.')
                cat=resolve_category(cv.get()); stock_unit=resolve_unit(su.get()); unit=resolve_unit(uv.get())
                buy=nfloat(vals['purchase_price'].get() or 0); sell=nfloat(vals['sale_price'].get() or 0)
                mn=nfloat(vals['min_stock'].get() or 0)
                a=[vals['code'].get().strip(),vals['name'].get().strip(),vals['brand'].get().strip()]
                if pid:
                    self.db.q('UPDATE products SET code=?,name=?,brand=?,purchase_price=?,sale_price=?,min_stock=?,notes=?,category_id=?,stock_unit_id=?,sale_unit_id=?,photo=? WHERE id=?',
                              (*a,buy,sell,mn,vals['notes'].get(),cat,stock_unit,unit,photo.get(),pid))
                    self.audit('Modification produit',pid,vals['name'].get()); new_id=pid
                else:
                    new_id=self.db.q('INSERT INTO products(code,name,brand,purchase_price,sale_price,min_stock,notes,category_id,stock_unit_id,sale_unit_id,photo) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                              (*a,buy,sell,mn,vals['notes'].get(),cat,stock_unit,unit,photo.get()))
                    self.audit('Création produit','',vals['name'].get())
                old_photo=(r['photo'] if r else '') or ''
                if photo.get() and (photo.get()!=old_photo or not self.db.one('SELECT id FROM photos WHERE id=?',(new_id,))):
                    data=cloud_sync.photo_to_base64(photo.get())
                    if data: self.db.q('INSERT OR REPLACE INTO photos(id,data) VALUES(?,?)',(new_id,data))
                w.destroy()
                self.stock_view()
                if not pid:
                    messagebox.showinfo('Produit','Produit enregistré. Enregistrez une réception (Achats / réceptions) pour lui donner une quantité au magasin.')
            except sqlite3.IntegrityError:
                messagebox.showerror('Produit','Cette référence de produit existe déjà.',parent=w)
            except Exception as e:
                messagebox.showerror('Produit',str(e),parent=w)
        actions['save']=save
        w.bind('<Destroy>',lambda e:[canvas.unbind_all(s) for s in ('<MouseWheel>','<Button-4>','<Button-5>')])
    def import_products(self):
        f=filedialog.askopenfilename(filetypes=[('CSV','*.csv')]);
        if not f:return
        with open(f,encoding='utf-8-sig') as h:
            for r in csv.DictReader(h):
                try:self.db.q('INSERT OR IGNORE INTO products(code,name,brand,purchase_price,sale_price,min_stock) VALUES(?,?,?,?,?,?)',(r.get('code'),r.get('name'),r.get('brand',''),nfloat(r.get('purchase_price') or 0),nfloat(r.get('sale_price') or 0),nfloat(r.get('min_stock') or 0)))
                except: pass
        self.products()
    def transfers(self):
        self.header('Transferts de stock','Magasin vers Boutique et Boutique vers Magasin, mise à jour automatique des quantités')
        bar=tk.Frame(self.body,bg='#f4f6f8'); bar.pack(fill='x',padx=20,pady=(10,8))
        tk.Button(bar,text='+ Nouveau transfert',command=self.transfer_form,bg='#173a5e',fg='white',relief='flat',font=('Segoe UI',10,'bold'),padx=14,pady=6).pack(side='right')
        mid=self.db.magasin_id(); bid=self.db.boutique_id()
        resume=tk.Frame(self.body,bg='#f4f7fb'); resume.pack(fill='x',padx=16,pady=(0,6))
        qm=self.db.one('SELECT COALESCE(SUM(qty),0) v FROM stocks WHERE location_id=?',(mid,))['v']
        qb=self.db.one('SELECT COALESCE(SUM(qty),0) v FROM stocks WHERE location_id=?',(bid,))['v']
        self.card(resume,'Quantité au magasin',f'{float(qm):g}','Stock disponible à transférer','#1e73e8','MAG')
        self.card(resume,'Quantité en boutique',f'{float(qb):g}','Articles disponibles à la vente','#18b65b','BTQ')
        rows=self.db.rows('''SELECT t.id,a.name src,b.name dst,t.date,t.status,COALESCE(u.name,'-') user,
                (SELECT COUNT(*) FROM transfer_lines tl WHERE tl.transfer_id=t.id) lignes,
                (SELECT COALESCE(SUM(tl.qty),0) FROM transfer_lines tl WHERE tl.transfer_id=t.id) total
                FROM transfers t LEFT JOIN locations a ON a.id=t.source_id
                LEFT JOIN locations b ON b.id=t.dest_id LEFT JOIN users u ON u.id=t.user_id ORDER BY t.date DESC,t.id DESC''')
        zone=tk.Frame(self.body,bg='white',highlightbackground='#dce5ef',highlightthickness=1); zone.pack(fill='both',expand=True,padx=20,pady=8)
        tk.Label(zone,text=f'Historique des transferts ({len(rows)})',font=('Segoe UI',11,'bold'),fg='#173a5e',bg='white').pack(anchor='w',padx=16,pady=(12,2))
        if rows:
            tv=self.table(zone,[('id','N°',60),('src','Source',150),('dst','Destination',150),('date','Date',160),
                                ('lignes','Articles',100),('total','Quantité totale',140),('status','Statut',110),('user','Responsable',150)],
                          [(r['id'],r['src'],r['dst'],r['date'],r['lignes'],f"{float(r['total']):g}",r['status'],r['user']) for r in rows])
            tv.bind('<Double-1>',lambda e: self.transfer_detail(int(tv.item(tv.focus(),'values')[0])) if tv.focus() else None)
        else:
            tk.Label(zone,text='Aucun transfert enregistré.',bg='white',fg='#7b8b9c').pack(anchor='w',padx=18,pady=16)
    def transfer_detail(self,tid):
        rows=self.db.rows('''SELECT p.code,p.name,tl.qty,COALESCE(u.name,'-') unit FROM transfer_lines tl
                JOIN products p ON p.id=tl.product_id LEFT JOIN units u ON u.id=p.stock_unit_id
                WHERE tl.transfer_id=?''',(tid,))
        t=self.db.one('''SELECT t.*,a.name src,b.name dst FROM transfers t
                LEFT JOIN locations a ON a.id=t.source_id LEFT JOIN locations b ON b.id=t.dest_id WHERE t.id=?''',(tid,))
        if not t: return
        w=tk.Toplevel(self); w.title(f'Transfert n° {tid}'); w.geometry('760x480'); w.configure(bg='#f4f7fb')
        tk.Label(w,text=f"Transfert n° {tid} : {t['src']} vers {t['dst']}",font=('Segoe UI',14,'bold'),fg='#173a5e',bg='#f4f7fb').pack(anchor='w',padx=20,pady=(18,2))
        tk.Label(w,text=f"Date : {t['date']}   |   Statut : {t['status']}",bg='#f4f7fb',fg='#53677e').pack(anchor='w',padx=20,pady=(0,10))
        fr=tk.Frame(w,bg='white',highlightbackground='#dce5ef',highlightthickness=1); fr.pack(fill='both',expand=True,padx=20,pady=8)
        self.table(fr,[('code','Référence',130),('name','Désignation',300),('qty','Quantité',120),('unit','Unité',140)],
                   [(r['code'],r['name'],f"{float(r['qty']):g}",r['unit']) for r in rows])
        tk.Button(w,text='Fermer',command=w.destroy,relief='flat',padx=18,pady=8).pack(anchor='e',padx=20,pady=12)
    def transfer_form(self):
        """Transfert multi-articles entre magasin et boutique."""
        w=tk.Toplevel(self); w.title('Nouveau transfert'); w.geometry('900x700'); w.configure(bg='#f4f7fb'); w.transient(self); w.grab_set()
        tk.Label(w,text='Transfert de stock',font=('Segoe UI',16,'bold'),fg='#173a5e',bg='#f4f7fb').pack(pady=(16,8))
        mid=self.db.magasin_id(); bid=self.db.boutique_id()
        top=tk.Frame(w,bg='white',highlightbackground='#dce5ef',highlightthickness=1); top.pack(fill='x',padx=20)
        sens=tk.StringVar(value=f'{MAGASIN} vers {BOUTIQUE}')
        tk.Label(top,text='Sens du transfert',font=('Segoe UI',10,'bold'),fg='#173a5e',bg='white').grid(row=0,column=0,padx=16,pady=12,sticky='w')
        ttk.Combobox(top,textvariable=sens,values=[f'{MAGASIN} vers {BOUTIQUE}',f'{BOUTIQUE} vers {MAGASIN}'],state='readonly',width=28,font=('Segoe UI',11)).grid(row=0,column=1,pady=12,sticky='w')
        tk.Label(top,text='Article',font=('Segoe UI',10,'bold'),fg='#173a5e',bg='white').grid(row=1,column=0,padx=16,pady=(0,6),sticky='w')
        pv=tk.StringVar(); combo=ttk.Combobox(top,textvariable=pv,state='readonly',width=52,font=('Segoe UI',10)); combo.grid(row=1,column=1,pady=(0,6),sticky='w')
        tk.Label(top,text='Quantité',font=('Segoe UI',10,'bold'),fg='#173a5e',bg='white').grid(row=1,column=2,padx=(18,6),pady=(0,6),sticky='e')
        q=tk.Entry(top,width=12,font=('Segoe UI',11)); q.grid(row=1,column=3,pady=(0,6),sticky='w'); q.insert(0,'1')
        dispo=tk.Label(top,text='',bg='white',fg='#1769d3',font=('Segoe UI',9,'bold')); dispo.grid(row=2,column=1,sticky='w',pady=(0,12))
        lines=[]
        def source_id(): return mid if sens.get().startswith(MAGASIN) else bid
        def dest_id(): return bid if sens.get().startswith(MAGASIN) else mid
        def reload_products(*a):
            rows=self.db.products_at(source_id(),only_positive=True)
            combo['values']=[f"{r['id']} - {r['code']} - {r['name']}  (dispo {float(r['qty']):g} {r['stock_unit']})" for r in rows]
            pv.set(''); dispo.config(text=f"{len(rows)} article(s) disponible(s) en source" if rows else 'Aucun article disponible dans cet emplacement')
            del lines[:]; refresh()
        def on_pick(*a):
            if not pv.get(): return
            pid=int(pv.get().split(' - ',1)[0]); available=self.db.stock(pid,source_id())
            dispo.config(text=f'Quantité disponible en source : {available:g}')
        sens.trace_add('write',reload_products); pv.trace_add('write',on_pick)
        foot=tk.Frame(w,bg='#f4f7fb'); foot.pack(side='bottom',fill='x',padx=20,pady=16)
        brow=tk.Frame(w,bg='#f4f7fb'); brow.pack(side='bottom',fill='x',padx=20,pady=(0,4))
        mid_zone=tk.Frame(w,bg='#f4f7fb'); mid_zone.pack(fill='both',expand=True,padx=20,pady=10)
        cols=('pid','name','qty')
        tv=ttk.Treeview(mid_zone,columns=cols,show='headings',height=10)
        for c,l,wd in [('pid','ID',60),('name','Article à transférer',480),('qty','Quantité',140)]:
            tv.heading(c,text=l,anchor='center'); tv.column(c,width=wd,anchor='center')
        tv.pack(fill='both',expand=True)
        def refresh():
            for x in tv.get_children(): tv.delete(x)
            for r in lines: tv.insert('', 'end',values=(r['pid'],r['name'],f"{r['qty']:g}"))
        def add_line():
            try:
                if not pv.get(): raise ValueError('Sélectionnez un article.')
                pid=int(pv.get().split(' - ',1)[0])
                prod=self.db.one('SELECT code,name FROM products WHERE id=?',(pid,))
                qty=nfloat(q.get() or 0)
                if qty<=0: raise ValueError('La quantité doit être supérieure à 0.')
                available=self.db.stock(pid,source_id())
                deja=sum(x['qty'] for x in lines if x['pid']==pid)
                if qty+deja>available: raise ValueError(f'Quantité disponible : {available:g} (déjà sélectionné : {deja:g})')
                exist=next((x for x in lines if x['pid']==pid),None)
                if exist: exist['qty']+=qty
                else: lines.append({'pid':pid,'name':f"{prod['code']} - {prod['name']}",'qty':qty})
                refresh()
            except Exception as e: messagebox.showerror('Transfert',str(e),parent=w)
        def remove_line():
            if not tv.focus(): return
            pid=int(tv.item(tv.focus(),'values')[0])
            for x in list(lines):
                if x['pid']==pid: lines.remove(x)
            refresh()
        tk.Button(brow,text='+ Ajouter à la liste',command=add_line,bg='#173a5e',fg='white',relief='flat',font=('Segoe UI',10,'bold'),padx=14,pady=6).pack(side='left')
        tk.Button(brow,text='Retirer la ligne',command=remove_line,relief='flat',padx=14,pady=6).pack(side='left',padx=8)
        def save():
            try:
                if not lines: raise ValueError('Ajoutez au moins un article à transférer.')
                si=source_id(); di=dest_id()
                if si==di: raise ValueError('Source et destination identiques.')
                tid=self.db.q('INSERT INTO transfers(source_id,dest_id,date,status,user_id) VALUES(?,?,?,?,?)',(si,di,now(),'Validé',self.user['id']))
                for x in lines:
                    self.db.q('INSERT INTO transfer_lines(transfer_id,product_id,qty) VALUES(?,?,?)',(tid,x['pid'],x['qty']))
                    self.db.adjust(x['pid'],si,-x['qty'],'Transfert sortie',self.user['id'],str(tid))
                    self.db.adjust(x['pid'],di,x['qty'],'Transfert entrée',self.user['id'],str(tid))
                self.audit('Transfert',tid,f"{len(lines)} article(s) : {sens.get()}")
                total=sum(x['qty'] for x in lines); w.destroy(); self.transfers()
                messagebox.showinfo('Transfert',f'Transfert n° {tid} validé.\n{sens.get()}\n{len(lines)} article(s), quantité totale {total:g}.\nLes quantités ont été mises à jour automatiquement.')
            except Exception as e: messagebox.showerror('Transfert',str(e),parent=w)
        tk.Button(foot,text='ANNULER',command=w.destroy,bg='#777777',fg='white',relief='flat',font=('Segoe UI',10,'bold'),pady=10).pack(side='left',fill='x',expand=True,padx=(0,6))
        tk.Button(foot,text='VALIDER LE TRANSFERT',command=save,bg='#2e7d32',fg='white',relief='flat',font=('Segoe UI',11,'bold'),pady=10).pack(side='left',fill='x',expand=True,padx=(6,0))
        reload_products()
    def purchases(self):
        self.header('Achats & réceptions','Toute réception entre directement au STOCK MAGASIN')
        bar=tk.Frame(self.body,bg='#f4f6f8'); bar.pack(fill='x',padx=20,pady=(10,8))
        tk.Button(bar,text='+ Nouvelle réception',command=self.purchase_form,bg='#2e7d32',fg='white',relief='flat',font=('Segoe UI',10,'bold'),padx=14,pady=6).pack(side='right')
        tk.Label(bar,text='Les articles reçus sont ajoutés au magasin, puis transférés vers la boutique pour la vente.',bg='#f4f6f8',fg='#53677e',font=('Segoe UI',9)).pack(side='left',padx=5)
        rows=self.db.rows('''SELECT a.id,COALESCE(f.name,'-') supplier,a.date,a.total,a.status,COALESCE(u.name,'-') user,
                (SELECT COALESCE(SUM(pl.qty),0) FROM purchase_lines pl WHERE pl.purchase_id=a.id) qty,
                (SELECT COUNT(*) FROM purchase_lines pl WHERE pl.purchase_id=a.id) lignes
                FROM purchases a LEFT JOIN suppliers f ON f.id=a.supplier_id
                LEFT JOIN users u ON u.id=a.user_id ORDER BY a.date DESC,a.id DESC''')
        zone=tk.Frame(self.body,bg='white',highlightbackground='#dce5ef',highlightthickness=1); zone.pack(fill='both',expand=True,padx=20,pady=8)
        tk.Label(zone,text=f'Historique des réceptions ({len(rows)})',font=('Segoe UI',11,'bold'),fg='#173a5e',bg='white').pack(anchor='w',padx=16,pady=(12,2))
        if rows:
            self.table(zone,[('id','N°',60),('supplier','Fournisseur',200),('date','Date',160),('lignes','Articles',90),
                             ('qty','Quantité reçue',140),('total','Montant',140),('status','Statut',160),('user','Utilisateur',150)],
                       [(r['id'],r['supplier'],r['date'],r['lignes'],f"{float(r['qty']):g}",money(r['total']),r['status'],r['user']) for r in rows])
        else:
            tk.Label(zone,text='Aucune réception enregistrée.',bg='white',fg='#7b8b9c').pack(anchor='w',padx=18,pady=16)
    def purchase_form(self):
        w=tk.Toplevel(self); w.title('Réception / achat'); w.geometry('660x780'); w.configure(bg='#f4f7fb'); w.transient(self); w.grab_set()
        tk.Label(w,text='Nouvelle réception',font=('Segoe UI',16,'bold'),fg='#173a5e',bg='#f4f7fb').pack(pady=(16,4))
        tk.Label(w,text=f'Destination imposée : STOCK {MAGASIN.upper()}',font=('Segoe UI',10,'bold'),fg='#2e7d32',bg='#f4f7fb').pack(pady=(0,10))
        body=tk.Frame(w,bg='#f4f7fb'); body.pack(fill='both',expand=True,padx=30)
        sup=self.db.rows('SELECT * FROM suppliers ORDER BY name')
        pro=self.db.rows('SELECT id,code,name FROM products WHERE active=1 ORDER BY name')
        mid=self.db.magasin_id()
        tk.Label(body,text='Fournisseur',font=('Segoe UI',10,'bold'),fg='#173a5e',bg='#f4f7fb').pack(anchor='w',pady=(8,2))
        s=tk.StringVar(); ttk.Combobox(body,textvariable=s,values=[f"{x['id']} - {x['name']}" for x in sup],font=('Segoe UI',11)).pack(fill='x',ipady=3)
        tk.Label(body,text='Sélectionnez un fournisseur existant ou saisissez directement son nom.',fg='#667788',bg='#f4f7fb',font=('Segoe UI',8)).pack(anchor='w',pady=(2,0))
        tk.Label(body,text='Téléphone du fournisseur (si nouveau)',font=('Segoe UI',10,'bold'),fg='#173a5e',bg='#f4f7fb').pack(anchor='w',pady=(10,2))
        phone=tk.Entry(body,font=('Segoe UI',11)); phone.pack(fill='x',ipady=4)
        tk.Label(body,text='Article reçu',font=('Segoe UI',10,'bold'),fg='#173a5e',bg='#f4f7fb').pack(anchor='w',pady=(10,2))
        p=tk.StringVar(); ttk.Combobox(body,textvariable=p,values=[f"{x['id']} - {x['code']} - {x['name']}" for x in pro],state='readonly',font=('Segoe UI',11)).pack(fill='x',ipady=3)
        tk.Button(body,text='+ Créer un nouveau produit',command=lambda:(w.destroy(),self.product_form()),relief='flat',font=('Segoe UI',9)).pack(anchor='w',pady=(4,0))
        tk.Label(body,text='QUANTITÉ REÇUE (obligatoire)',font=('Segoe UI',10,'bold'),fg='#a51d1d',bg='#f4f7fb').pack(anchor='w',pady=(12,2))
        q=tk.Entry(body,font=('Segoe UI',13)); q.pack(fill='x',ipady=5)
        etat=tk.Label(body,text='',bg='#f4f7fb',fg='#1769d3',font=('Segoe UI',9,'bold')); etat.pack(anchor='w',pady=(4,0))
        def on_pick(*a):
            if not p.get(): return
            pid=int(p.get().split(' - ',1)[0]); etat.config(text=f'Quantité actuelle au magasin : {self.db.stock(pid,mid):g}')
            pr_row=self.db.one('SELECT purchase_price FROM products WHERE id=?',(pid,))
            if pr_row and not pr.get(): pr.insert(0,f"{float(pr_row['purchase_price'] or 0):g}")
        tk.Label(body,text='Prix d’achat unitaire (FCFA)',font=('Segoe UI',10,'bold'),fg='#173a5e',bg='#f4f7fb').pack(anchor='w',pady=(12,2))
        pr=tk.Entry(body,font=('Segoe UI',11)); pr.pack(fill='x',ipady=4)
        p.trace_add('write',on_pick)
        def save():
            try:
                sv=s.get().strip()
                if not sv: raise ValueError('Veuillez sélectionner ou saisir le fournisseur.')
                if ' - ' in sv and _is_id(sv.split(' - ',1)[0]): sid=int(sv.split(' - ',1)[0])
                else:
                    existing=self.db.one('SELECT id FROM suppliers WHERE lower(trim(name))=lower(trim(?))',(sv,))
                    sid=int(existing['id']) if existing else self.db.q('INSERT INTO suppliers(name,phone) VALUES(?,?)',(sv,phone.get().strip()))
                if not p.get(): raise ValueError('Veuillez sélectionner l’article reçu.')
                if not q.get().strip(): raise ValueError('La quantité reçue est obligatoire.')
                pid=int(p.get().split(' - ',1)[0]); qty=nfloat(q.get())
                if qty<=0: raise ValueError('La quantité reçue doit être supérieure à 0.')
                price=nfloat(pr.get() or 0)
                if price<0: raise ValueError('Le prix d’achat ne peut pas être négatif.')
                total=qty*price
                aid=self.db.q('INSERT INTO purchases(supplier_id,location_id,date,total,status,user_id) VALUES(?,?,?,?,?,?)',
                              (sid,mid,now(),total,'Réception validée (magasin)',self.user['id']))
                self.db.q('INSERT INTO purchase_lines(purchase_id,product_id,qty,price) VALUES(?,?,?,?)',(aid,pid,qty,price))
                self.db.adjust(pid,mid,qty,'Achat / entrée magasin',self.user['id'],str(aid))
                if price>0: self.db.q('UPDATE products SET purchase_price=? WHERE id=?',(price,pid))
                self.audit('Réception achat',aid,total)
                nouveau=self.db.stock(pid,mid)
                w.destroy(); self.stock_view()
                messagebox.showinfo('Réception',f'Réception n° {aid} validée.\nQuantité ajoutée au magasin : {qty:g}\nNouvelle quantité au magasin : {nouveau:g}')
            except Exception as e: messagebox.showerror('Achat',str(e),parent=w)
        foot=tk.Frame(w,bg='#f4f7fb'); foot.pack(side='bottom',fill='x',padx=30,pady=18)
        tk.Button(foot,text='ANNULER',command=w.destroy,bg='#777777',fg='white',relief='flat',font=('Segoe UI',10,'bold'),pady=10).pack(side='left',fill='x',expand=True,padx=(0,6))
        tk.Button(foot,text='VALIDER LA RÉCEPTION',command=save,bg='#2e7d32',fg='white',relief='flat',font=('Segoe UI',11,'bold'),pady=10).pack(side='left',fill='x',expand=True,padx=(6,0))
    def sales(self):
        """Caisse : vente uniquement des articles disponibles en BOUTIQUE."""
        self.header('Ventes / caisse','Vente des articles de la boutique — recherche par famille, sélection par référence ou par scan')
        self.cart=[]
        bid=self.db.boutique_id()
        dispo=self.db.products_at(bid,only_positive=True)
        top=tk.Frame(self.body,bg='white',highlightbackground='#dce5ef',highlightthickness=1); top.pack(fill='x',padx=20,pady=10)
        tk.Label(top,text='Article de la boutique',font=('Segoe UI',10,'bold'),fg='#173a5e',bg='white').grid(row=1,column=0,padx=(16,6),pady=(4,4),sticky='w')
        def label_of(r): return f"{r['id']} - {r['code']} - {r['name']}  (dispo {float(r['qty']):g} {r['sale_unit']} — {money(r['sale_price'])})"
        tk.Label(top,text='Rechercher un article :',font=('Segoe UI',10,'bold'),fg='#173a5e',bg='white').grid(row=0,column=0,padx=(16,6),pady=(12,4),sticky='w')
        find_v=tk.StringVar(); find_e=tk.Entry(top,textvariable=find_v,width=34,font=('Segoe UI',12)); find_e.grid(row=0,column=1,pady=(12,4),sticky='w')
        find_info=tk.Label(top,text='Ex. « ampoule 20W » : seules les ampoules 20W sont proposées',bg='white',fg='#65788c',font=('Segoe UI',8))
        find_info.grid(row=0,column=2,columnspan=3,sticky='w',padx=8,pady=(12,4))
        pv=tk.StringVar()
        combo=ttk.Combobox(top,textvariable=pv,state='readonly',width=56,font=('Segoe UI',10),
                           values=[label_of(r) for r in dispo])
        combo.grid(row=1,column=1,pady=(4,4),sticky='w')
        tk.Label(top,text='Quantité',font=('Segoe UI',10,'bold'),fg='#173a5e',bg='white').grid(row=1,column=2,padx=(16,4),pady=(4,4),sticky='e')
        q=tk.Entry(top,width=10,font=('Segoe UI',11)); q.insert(0,'1'); q.grid(row=1,column=3,pady=(4,4),sticky='w')
        tk.Button(top,text='Ajouter au panier',command=lambda:add(),bg='#173a5e',fg='white',relief='flat',font=('Segoe UI',10,'bold'),padx=14,pady=5).grid(row=1,column=4,padx=12,pady=(4,4))
        tk.Label(top,text='Référence / scan du produit :',font=('Segoe UI',10,'bold'),fg='#173a5e',bg='white').grid(row=2,column=0,padx=(16,6),pady=(2,12),sticky='w')
        scan=tk.Entry(top,width=34,font=('Segoe UI',12)); scan.grid(row=2,column=1,pady=(2,12),sticky='w')
        tk.Label(top,text='Saisissez ou scannez la référence puis Entrée',bg='white',fg='#65788c',font=('Segoe UI',8)).grid(row=2,column=2,columnspan=3,sticky='w',padx=8,pady=(2,12))
        etat=tk.Label(self.body,text=f'{len(dispo)} article(s) disponible(s) en boutique',bg='#f4f7fb',fg='#53677e',font=('Segoe UI',9))
        etat.pack(anchor='w',padx=25)
        cols=('id','product','qty','price','total')
        tv=ttk.Treeview(self.body,columns=cols,show='headings',height=10)
        for c,l,wd in [('id','N°',60),('product','Article',400),('qty','Quantité',110),('price','Prix unitaire',150),('total','Total ligne',150)]:
            tv.heading(c,text=l,anchor='center'); tv.column(c,width=wd,anchor='center')
        tv.pack(fill='both',expand=True,padx=20,pady=8)
        bottom=tk.Frame(self.body,bg='#f4f7fb'); bottom.pack(fill='x',padx=20,pady=(0,10))
        left=tk.Frame(bottom,bg='#f4f7fb'); left.pack(side='left',fill='x',expand=True)
        cli=self.db.rows('SELECT * FROM clients ORDER BY name')
        tk.Label(left,text='Client (obligatoire pour une vente à crédit)',bg='#f4f7fb',fg='#173a5e',font=('Segoe UI',9,'bold')).pack(anchor='w')
        cli_v=tk.StringVar(); ttk.Combobox(left,textvariable=cli_v,values=[f"{x['id']} - {x['name']}" for x in cli],width=42,font=('Segoe UI',10)).pack(anchor='w',pady=(2,6))
        tk.Label(left,text='Montant payé (FCFA)',bg='#f4f7fb',fg='#173a5e',font=('Segoe UI',9,'bold')).pack(anchor='w')
        pay=tk.Entry(left,width=20,font=('Segoe UI',11)); pay.pack(anchor='w',pady=(2,6)); pay.insert(0,'0')
        tk.Label(left,text='Mode de paiement',bg='#f4f7fb',fg='#173a5e',font=('Segoe UI',9,'bold')).pack(anchor='w')
        mode=tk.StringVar(value='Espèces'); ttk.Combobox(left,textvariable=mode,values=PAYMENT_MODES,state='readonly',width=22,font=('Segoe UI',10)).pack(anchor='w',pady=(2,6))
        credit=tk.IntVar(); tk.Checkbutton(left,text='Vente à crédit',variable=credit,bg='#f4f7fb',font=('Segoe UI',10,'bold'),fg='#a51d1d',activebackground='#f4f7fb').pack(anchor='w')
        right=tk.Frame(bottom,bg='#f4f7fb'); right.pack(side='right')
        total_lbl=tk.Label(right,text='TOTAL : 0 FCFA',font=('Segoe UI',16,'bold'),fg='#082b55',bg='#f4f7fb'); total_lbl.pack(anchor='e',pady=(4,8))
        tk.Button(right,text='Retirer la ligne',command=lambda:remove_line(),relief='flat',padx=14,pady=6).pack(anchor='e',pady=(0,6))
        tk.Button(right,text='VALIDER LA VENTE + FACTURE PDF',command=lambda:validate(),bg='#2e7d32',fg='white',relief='flat',font=('Segoe UI',11,'bold'),padx=18,pady=10).pack(anchor='e')
        def filter_articles(event=None):
            found,note=smart_search(dispo,find_v.get())
            combo['values']=[label_of(r) for r in found]
            if pv.get() and pv.get() not in combo['values']: pv.set('')
            if not find_v.get().strip():
                find_info.config(text='Ex. « ampoule 20W » : seules les ampoules 20W sont proposées',fg='#65788c')
            else:
                find_info.config(text=note,fg='#b3261e' if note.startswith('Aucun') else '#2e7d32')
            if len(found)==1: pv.set(combo['values'][0])
            return found
        def find_enter(event=None):
            found=filter_articles()
            if len(found)==1: q.focus(); q.select_range(0,'end')
            elif found:
                combo.focus()
                try: self.tk.call('ttk::combobox::Post',combo)
                except Exception: pass
        find_e.bind('<KeyRelease>',lambda e: filter_articles() if e.keysym not in ('Return','KP_Enter') else None)
        find_e.bind('<Return>',find_enter); find_e.bind('<KP_Enter>',find_enter)
        q.bind('<Return>',lambda e:(add(),find_e.focus(),find_e.select_range(0,'end')))
        def push(pid,qty):
            prod=self.db.one('SELECT * FROM products WHERE id=? AND active=1',(pid,))
            if not prod: raise ValueError('Article introuvable ou inactif.')
            available=self.db.stock(pid,bid)
            exist=next((x for x in self.cart if x['pid']==pid),None)
            deja=exist['qty'] if exist else 0
            if qty+deja>available: raise ValueError(f'Quantité disponible en boutique : {available:g}')
            if exist: exist['qty']+=qty
            else: self.cart.append({'pid':pid,'name':prod['name'],'qty':qty,'price':prod['sale_price'],'cost':prod['purchase_price'] or 0,'loc':bid})
            refresh()
        def add():
            try:
                if not pv.get().strip(): raise ValueError('Sélectionnez un article de la boutique.')
                qty=nfloat(q.get() or 0)
                if qty<=0: raise ValueError('La quantité doit être supérieure à 0.')
                push(int(pv.get().split(' - ',1)[0]),qty)
            except Exception as e: messagebox.showerror('Panier',str(e))
        def add_by_ref(event=None):
            try:
                ref=scan.get().strip()
                if not ref: return
                prod=self.db.find_product_by_reference(ref,bid)
                if not prod: raise ValueError(f'Aucun article disponible en boutique avec la référence « {ref} ».')
                push(prod['id'],nfloat(q.get() or 1) or 1)
                scan.delete(0,'end'); scan.focus()
            except Exception as e:
                messagebox.showerror('Référence / scan',str(e)); scan.select_range(0,'end'); scan.focus()
        scan.bind('<Return>',add_by_ref)
        def remove_line():
            if not tv.focus(): return
            idx=int(tv.item(tv.focus(),'values')[0])
            if 0<=idx<len(self.cart): self.cart.pop(idx); refresh()
        def refresh():
            for x in tv.get_children(): tv.delete(x)
            for i,r in enumerate(self.cart):
                tv.insert('', 'end',values=(i,r['name'],f"{r['qty']:g}",money(r['price']),money(r['qty']*r['price'])))
            total=sum(x['qty']*x['price'] for x in self.cart)
            total_lbl.config(text=f'TOTAL : {money(total)}')
            if not credit.get(): pay.delete(0,'end'); pay.insert(0,f'{total:g}')
        def validate():
            try:
                if not self.cart: raise ValueError('Le panier est vide.')
                client_id=int(cli_v.get().split(' - ')[0]) if cli_v.get() else None
                paid=nfloat(pay.get() or 0); total=sum(x['qty']*x['price'] for x in self.cart)
                if paid<0 or paid>total: raise ValueError('Montant payé invalide.')
                if credit.get() and not client_id: raise ValueError('Un client est obligatoire pour une vente à crédit.')
                sale=self.db.q('INSERT INTO sales(client_id,location_id,date,total,paid,status,payment_mode,user_id,credit) VALUES(?,?,?,?,?,?,?,?,?)',
                               (client_id,bid,now(),total,paid,'Validée',mode.get(),self.user['id'],1 if credit.get() else 0))
                for x in self.cart:
                    self.db.q('INSERT INTO sale_lines(sale_id,product_id,qty,unit,price,discount,cost) VALUES(?,?,?,?,?,0,?)',
                              (sale,x['pid'],x['qty'],'',x['price'],x.get('cost') or 0))
                    self.db.adjust(x['pid'],bid,-x['qty'],'Vente boutique',self.user['id'],str(sale))
                inv=self.invoice_number()
                self.db.q('INSERT INTO invoices(sale_id,number,date,status) VALUES(?,?,?,?)',(sale,inv,now(),'Validée'))
                balance=total-paid
                if credit.get() and balance>0:
                    cid=self.db.q('INSERT INTO credits(sale_id,client_id,total,advance,balance) VALUES(?,?,?,?,?)',(sale,client_id,total,paid,balance))
                    n=simpledialog.askinteger('Échéances','Nombre d’échéances mensuelles',initialvalue=3,minvalue=1,maxvalue=24,parent=self)
                    if not n: n=3
                    each=balance/n
                    for i in range(1,n+1):
                        self.db.q('INSERT INTO installments(credit_id,num,amount,due_date) VALUES(?,?,?,?)',
                                  (cid,i,each,(datetime.date.today()+datetime.timedelta(days=30*i)).isoformat()))
                self.audit('Vente',sale,total); self.make_invoice_pdf(sale)
                messagebox.showinfo('Vente',f'Vente validée. Facture {inv}.\nLes quantités en boutique ont été mises à jour.')
                self.cart=[]; self.sales()
            except Exception as e: messagebox.showerror('Vente',str(e))
        find_e.focus()
        if not dispo:
            messagebox.showwarning('Caisse','Aucun article disponible en boutique.\nTransférez d’abord des articles du magasin vers la boutique (onglet Transferts).')
        refresh()
    def invoice_number(self):
        prefix=self.db.one("SELECT value FROM settings WHERE key='invoice_prefix'")['value']; n=int(self.db.one("SELECT value FROM settings WHERE key='invoice_next'")['value']); self.db.q("UPDATE settings SET value=? WHERE key='invoice_next'",(str(n+1),)); return f'{prefix}{n:06d}'
    def make_invoice_pdf(self,sale_id):
        """Generate the AMUNTCHI invoice in the requested A4 layout."""
        if canvas is None:
            return None
        inv=self.db.one('SELECT i.number,i.date,s.*,c.name client_name FROM invoices i JOIN sales s ON s.id=i.sale_id LEFT JOIN clients c ON c.id=s.client_id WHERE s.id=?',(sale_id,))
        lines=self.db.rows('SELECT sl.*,p.code,p.name FROM sale_lines sl JOIN products p ON p.id=sl.product_id WHERE sl.sale_id=? ORDER BY sl.id',(sale_id,))
        fn=BASE/f"Facture_{inv['number']}.pdf"
        c=canvas.Canvas(str(fn),pagesize=A4)
        W,H=A4

        # --- En-tête ---
        if LOGO.exists():
            c.drawImage(ImageReader(str(LOGO)),35,H-105,width=105,height=78,preserveAspectRatio=True,mask='auto')
        c.setFillColorRGB(0,0,0)
        c.setFont('Helvetica-Bold',17)
        c.drawCentredString(W/2+45,H-45,'Mini Quincaillerie AMUNTCHI')
        c.setFont('Helvetica',10)
        c.drawCentredString(W/2+45,H-62,COMM['rccm'])
        c.drawCentredString(W/2+45,H-76,'TEL : '+COMM['phone'])
        c.drawCentredString(W/2+45,H-90,'Adresse Email : '+COMM['email'])

        c.setFont('Helvetica-Bold',12)
        c.drawString(40,H-125,'FACTURE N° '+str(inv['number']))

        # --- Tableau principal, proche du modèle fourni ---
        x0=30; table_top=H-150; table_w=W-60
        col_w=[45, 250, 90, 70, 80]
        header_h=30; row_h=24; nrows=15
        headers=['Item','Désignation','Prix Unitaire','Quantité','Prix Total']
        c.setLineWidth(0.8)
        c.setFont('Helvetica-Bold',10)
        # header background
        c.setFillColorRGB(0.97,0.97,0.97)
        c.rect(x0,table_top-header_h,table_w,header_h,fill=1,stroke=0)
        c.setFillColorRGB(0,0,0)
        # vertical lines
        xpos=x0
        for w in col_w:
            c.line(xpos,table_top,xpos,table_top-header_h-nrows*row_h)
            xpos += w
        c.line(xpos,table_top,xpos,table_top-header_h-nrows*row_h)
        # horizontal lines
        c.line(x0,table_top,x0+table_w,table_top)
        c.line(x0,table_top-header_h,x0+table_w,table_top-header_h)
        for i in range(nrows):
            yy=table_top-header_h-(i+1)*row_h
            c.line(x0,yy,x0+table_w,yy)
        # headers centered
        xpos=x0
        for h,w in zip(headers,col_w):
            c.drawCentredString(xpos+w/2,table_top-20,h)
            xpos += w

        # data rows
        c.setFont('Helvetica',8.8)
        y=table_top-header_h-16
        for idx,x in enumerate(lines[:nrows],1):
            vals=[str(idx),str(x['name'])[:48],money(x['price']),f"{x['qty']:.2f}",money(x['qty']*x['price'])]
            xpos=x0
            for j,(val,w) in enumerate(zip(vals,col_w)):
                c.drawCentredString(xpos+w/2,y,val)
                xpos += w
            y -= row_h

        # --- Bloc total / payé / reste à payer ---
        box_y=table_top-header_h-nrows*row_h
        label_x=x0+col_w[0]+col_w[1]+col_w[2]
        label_w=col_w[3]
        value_x=label_x+label_w
        value_w=col_w[4]
        c.setFont('Helvetica-Bold',10)
        for i,label in enumerate(['TOTAL','Payé','Reste à Payé']):
            yy=box_y-i*24
            c.line(label_x,yy,label_x+label_w+value_w,yy)
            c.drawCentredString(label_x+label_w/2,yy-17,label)
        c.line(label_x,box_y-72,label_x+label_w+value_w,box_y-72)
        c.line(label_x,box_y,label_x,box_y-72)
        c.line(label_x+label_w,box_y,label_x+label_w,box_y-72)
        c.line(label_x+label_w+value_w,box_y,label_x+label_w+value_w,box_y-72)
        c.drawCentredString(value_x+value_w/2,box_y-17,money(inv['total']))
        c.setFont('Helvetica',10)
        c.drawCentredString(value_x+value_w/2,box_y-41,money(inv['paid']))
        c.drawCentredString(value_x+value_w/2,box_y-65,money(inv['total']-inv['paid']))

        # --- Signatures ---
        c.setFont('Helvetica-Bold',10)
        c.drawString(35,58,'Le Gérant:')
        client=inv['client_name'] or ''
        c.drawRightString(W-35,58,'Client:')
        if client:
            c.setFont('Helvetica',9)
            c.drawRightString(W-35,43,client[:42])
        c.save()
        return fn
    def clients(self):
        """Clients & crédits : ventes à crédit, soldes et accès au compte client."""
        self.header('Clients & crédits','Ventes à crédit, échéances et paiements par client')
        bar=tk.Frame(self.body,bg='#f4f6f8'); bar.pack(fill='x',padx=20,pady=(10,8))
        tk.Button(bar,text='+ Client',command=self.client_form,bg='#173a5e',fg='white',relief='flat',font=('Segoe UI',10,'bold'),padx=14,pady=6).pack(side='right',padx=5)
        tk.Button(bar,text='Enregistrer un paiement',command=self.payment_form,bg='#2e7d32',fg='white',relief='flat',font=('Segoe UI',10,'bold'),padx=14,pady=6).pack(side='right',padx=5)
        tk.Label(bar,text='Double-cliquez sur un client pour ouvrir son compte (articles, montants, échéances, paiement).',bg='#f4f6f8',fg='#53677e',font=('Segoe UI',9)).pack(side='left',padx=5)
        cards=tk.Frame(self.body,bg='#f4f7fb'); cards.pack(fill='x',padx=16,pady=(0,4))
        tot=self.db.one('SELECT COALESCE(SUM(total),0) t,COALESCE(SUM(balance),0) b,COUNT(*) n FROM credits')
        encaisse=self.db.one('SELECT COALESCE(SUM(amount),0) v FROM payments')['v']
        self.card(cards,'Ventes à crédit',f"{tot['n']}",'Nombre de crédits ouverts','#1e73e8','NBR')
        self.card(cards,'Montant total à crédit',money(tot['t']),'Toutes ventes à crédit','#7c4dff','TOT')
        self.card(cards,'Déjà encaissé',money(encaisse),'Paiements reçus','#18b65b','PAY')
        self.card(cards,'Reste à payer',money(tot['b']),'Solde clients','#ef3038','RST')
        nb=tk.Frame(self.body,bg='#f4f7fb'); nb.pack(fill='both',expand=True,padx=16,pady=8)
        tabs=ttk.Notebook(nb); tabs.pack(fill='both',expand=True)
        # Onglet 1 : comptes clients
        t1=tk.Frame(tabs,bg='white'); tabs.add(t1,text='  Comptes clients  ')
        rows=self.db.rows('''SELECT c.id,c.name,c.phone,
                COALESCE((SELECT SUM(cr.total) FROM credits cr WHERE cr.client_id=c.id),0) total,
                COALESCE((SELECT SUM(cr.advance) FROM credits cr WHERE cr.client_id=c.id),0) paye,
                COALESCE((SELECT SUM(cr.balance) FROM credits cr WHERE cr.client_id=c.id),0) reste,
                COALESCE((SELECT COUNT(*) FROM credits cr WHERE cr.client_id=c.id),0) nbcredits
                FROM clients c ORDER BY reste DESC,c.name''')
        tv=self.table(t1,[('id','ID',60),('name','Client',240),('phone','Téléphone',160),('nb','Crédits',90),
                          ('total','Total à crédit',160),('paye','Déjà payé',150),('reste','Reste à payer',160)],
                      [(r['id'],r['name'],r['phone'] or '',r['nbcredits'],money(r['total']),money(r['paye']),money(r['reste'])) for r in rows])
        tv.bind('<Double-1>',lambda e: self.client_detail(int(tv.item(tv.focus(),'values')[0])) if tv.focus() else None)
        tk.Button(t1,text='Ouvrir le compte du client sélectionné',
                  command=lambda: self.client_detail(int(tv.item(tv.focus(),'values')[0])) if tv.focus() else messagebox.showwarning('Clients','Sélectionnez un client.'),
                  bg='#1769d3',fg='white',relief='flat',font=('Segoe UI',10,'bold'),padx=16,pady=7).pack(anchor='e',padx=14,pady=10)
        # Onglet 2 : ventes à crédit
        t2=tk.Frame(tabs,bg='white'); tabs.add(t2,text='  Ventes à crédit  ')
        cr=self.db.rows('''SELECT cr.id,COALESCE(cl.name,'-') client,s.date,i.number,cr.total,cr.advance,cr.balance,
                (SELECT COUNT(*) FROM installments it WHERE it.credit_id=cr.id) ech,
                (SELECT COUNT(*) FROM installments it WHERE it.credit_id=cr.id AND it.status='Payé') ech_ok
                FROM credits cr LEFT JOIN clients cl ON cl.id=cr.client_id
                LEFT JOIN sales s ON s.id=cr.sale_id LEFT JOIN invoices i ON i.sale_id=cr.sale_id
                ORDER BY s.date DESC,cr.id DESC''')
        if cr:
            tv2=self.table(t2,[('id','Crédit',80),('client','Client',200),('date','Date de vente',160),('number','Facture',130),
                               ('total','Total',140),('advance','Payé',140),('balance','Reste',140),('ech','Échéances',120)],
                           [(r['id'],r['client'],r['date'],r['number'] or '-',money(r['total']),money(r['advance']),money(r['balance']),f"{r['ech_ok']}/{r['ech']}") for r in cr])
            tv2.bind('<Double-1>',lambda e: self.credit_detail(int(tv2.item(tv2.focus(),'values')[0])) if tv2.focus() else None)
        else:
            tk.Label(t2,text='Aucune vente à crédit enregistrée.',bg='white',fg='#7b8b9c',font=('Segoe UI',10)).pack(anchor='w',padx=20,pady=20)
        return tv
    def client_detail(self,client_id):
        """Compte client : articles, montants, échéances et paiement direct."""
        c=self.db.one('SELECT * FROM clients WHERE id=?',(client_id,))
        if not c: return
        w=tk.Toplevel(self); w.title(f"Compte client — {c['name']}"); w.geometry('1040x760'); w.configure(bg='#f4f7fb'); w.transient(self)
        head=tk.Frame(w,bg='white',highlightbackground='#dce5ef',highlightthickness=1); head.pack(fill='x',padx=16,pady=(16,8))
        tk.Label(head,text=c['name'],font=('Segoe UI',18,'bold'),fg='#173a5e',bg='white').pack(anchor='w',padx=18,pady=(14,2))
        tk.Label(head,text=f"Téléphone : {c['phone'] or '-'}    |    Email : {c['email'] or '-'}    |    Adresse : {c['address'] or '-'}",
                 font=('Segoe UI',10),fg='#53677e',bg='white').pack(anchor='w',padx=18,pady=(0,12))
        cards=tk.Frame(w,bg='#f4f7fb'); cards.pack(fill='x',padx=12)
        agg=self.db.one('''SELECT COALESCE(SUM(total),0) t,COALESCE(SUM(advance),0) a,COALESCE(SUM(balance),0) b,COUNT(*) n
                FROM credits WHERE client_id=?''',(client_id,))
        self.card(cards,'Total à crédit',money(agg['t']),f"{agg['n']} vente(s) à crédit",'#1e73e8','TOT')
        self.card(cards,'Montant payé',money(agg['a']),'Avances et paiements','#18b65b','PAY')
        self.card(cards,'Reste à payer',money(agg['b']),'Solde du compte','#ef3038','RST')
        retard=self.db.one('''SELECT COUNT(*) n FROM installments it JOIN credits cr ON cr.id=it.credit_id
                WHERE cr.client_id=? AND it.status!='Payé' AND it.due_date<?''',(client_id,today()))['n']
        self.card(cards,'Échéances en retard',str(retard),'À relancer','#a51d1d','RET')
        nb=ttk.Notebook(w); nb.pack(fill='both',expand=True,padx=16,pady=10)
        # Articles achetés à crédit
        t1=tk.Frame(nb,bg='white'); nb.add(t1,text='  Articles achetés à crédit  ')
        arts=self.db.rows('''SELECT s.date,COALESCE(i.number,'-') facture,p.name,sl.qty,sl.price,(sl.qty*sl.price) montant,
                CASE WHEN cr.balance<=0 THEN 'Soldé' ELSE 'En cours' END etat
                FROM credits cr JOIN sales s ON s.id=cr.sale_id JOIN sale_lines sl ON sl.sale_id=s.id
                JOIN products p ON p.id=sl.product_id LEFT JOIN invoices i ON i.sale_id=s.id
                WHERE cr.client_id=? ORDER BY s.date DESC,sl.id''',(client_id,))
        if arts:
            self.table(t1,[('date','Date',150),('facture','Facture',130),('name','Article',280),('qty','Qté',90),
                           ('price','Prix unitaire',140),('montant','Montant',140),('etat','État du crédit',130)],
                       [(r['date'],r['facture'],r['name'],f"{float(r['qty']):g}",money(r['price']),money(r['montant']),r['etat']) for r in arts])
        else:
            tk.Label(t1,text='Aucun article acheté à crédit par ce client.',bg='white',fg='#7b8b9c').pack(anchor='w',padx=20,pady=20)
        # Échéances
        t2=tk.Frame(nb,bg='white'); nb.add(t2,text='  Échéances  ')
        ech=self.db.rows('''SELECT it.credit_id,it.num,it.amount,it.due_date,it.status FROM installments it
                JOIN credits cr ON cr.id=it.credit_id WHERE cr.client_id=? ORDER BY it.due_date''',(client_id,))
        if ech:
            self.table(t2,[('credit','Crédit',100),('num','N° échéance',130),('amount','Montant',160),
                           ('due','Date d’échéance',160),('status','Statut',140),('retard','Observation',180)],
                       [(r['credit_id'],r['num'],money(r['amount']),r['due_date'],r['status'],
                         'En retard' if (r['status']!='Payé' and str(r['due_date'])<today()) else '') for r in ech])
        else:
            tk.Label(t2,text='Aucune échéance enregistrée.',bg='white',fg='#7b8b9c').pack(anchor='w',padx=20,pady=20)
        # Paiements reçus
        t3=tk.Frame(nb,bg='white'); nb.add(t3,text='  Paiements reçus  ')
        pays=self.db.rows('''SELECT pa.date,pa.amount,pa.mode,pa.credit_id,COALESCE(u.name,'-') agent FROM payments pa
                LEFT JOIN users u ON u.id=pa.user_id WHERE pa.client_id=? ORDER BY pa.date DESC,pa.id DESC''',(client_id,))
        if pays:
            self.table(t3,[('date','Date',170),('amount','Montant',160),('mode','Mode',150),('credit','Crédit',110),('agent','Encaissé par',180)],
                       [(r['date'],money(r['amount']),r['mode'],r['credit_id'],r['agent']) for r in pays])
        else:
            tk.Label(t3,text='Aucun paiement enregistré.',bg='white',fg='#7b8b9c').pack(anchor='w',padx=20,pady=20)
        foot=tk.Frame(w,bg='#f4f7fb'); foot.pack(fill='x',padx=16,pady=(0,16))
        tk.Button(foot,text='ENREGISTRER UN PAIEMENT POUR CE CLIENT',command=lambda:(w.destroy(),self.payment_form(client_id)),
                  bg='#2e7d32',fg='white',relief='flat',font=('Segoe UI',11,'bold'),padx=18,pady=10).pack(side='left')
        tk.Button(foot,text='Fermer',command=w.destroy,relief='flat',padx=18,pady=10).pack(side='right')
    def credit_detail(self,credit_id):
        r=self.db.one('''SELECT cr.*,COALESCE(cl.name,'-') client FROM credits cr
                LEFT JOIN clients cl ON cl.id=cr.client_id WHERE cr.id=?''',(credit_id,))
        if r: self.client_detail(r['client_id'])
    def payment_form(self,client_id=None):
        w=tk.Toplevel(self); w.title('Paiement de crédit'); w.geometry('620x520'); w.configure(bg='#f4f7fb'); w.transient(self); w.grab_set()
        tk.Label(w,text='Encaisser un paiement',font=('Segoe UI',16,'bold'),fg='#173a5e',bg='#f4f7fb').pack(pady=(18,10))
        sql='''SELECT cr.id,COALESCE(cl.name,'-') name,cr.balance,cr.total FROM credits cr
               LEFT JOIN clients cl ON cl.id=cr.client_id WHERE cr.balance>0'''
        params=()
        if client_id: sql+=' AND cr.client_id=?'; params=(client_id,)
        cr=self.db.rows(sql+' ORDER BY cr.id DESC',params)
        body=tk.Frame(w,bg='#f4f7fb'); body.pack(fill='both',expand=True,padx=35)
        tk.Label(body,text='Crédit à régler',font=('Segoe UI',10,'bold'),fg='#173a5e',bg='#f4f7fb').pack(anchor='w',pady=(8,2))
        vals=[f"{x['id']} - {x['name']} - reste {money(x['balance'])}" for x in cr]
        cv=tk.StringVar(value=vals[0] if vals else '')
        ttk.Combobox(body,textvariable=cv,values=vals,state='readonly',font=('Segoe UI',10)).pack(fill='x',ipady=3)
        tk.Label(body,text='Montant du paiement (FCFA)',font=('Segoe UI',10,'bold'),fg='#173a5e',bg='#f4f7fb').pack(anchor='w',pady=(12,2))
        a=tk.Entry(body,font=('Segoe UI',13)); a.pack(fill='x',ipady=5)
        info=tk.Label(body,text='',bg='#f4f7fb',fg='#1769d3',font=('Segoe UI',9,'bold')); info.pack(anchor='w',pady=(4,0))
        def on_pick(*x):
            if not cv.get(): return
            cid=int(cv.get().split(' - ',1)[0]); row=self.db.one('SELECT total,advance,balance FROM credits WHERE id=?',(cid,))
            if row: info.config(text=f"Total {money(row['total'])}  |  déjà payé {money(row['advance'])}  |  reste {money(row['balance'])}")
        cv.trace_add('write',on_pick); on_pick()
        tk.Label(body,text='Mode de paiement',font=('Segoe UI',10,'bold'),fg='#173a5e',bg='#f4f7fb').pack(anchor='w',pady=(12,2))
        m=tk.StringVar(value='Espèces'); ttk.Combobox(body,textvariable=m,values=PAYMENT_MODES,state='readonly',font=('Segoe UI',10)).pack(fill='x',ipady=3)
        def save():
            try:
                if not cv.get(): raise ValueError('Aucun crédit à régler.')
                cid=int(cv.get().split(' - ',1)[0]); amount=nfloat(a.get() or 0)
                r=self.db.one('SELECT * FROM credits WHERE id=?',(cid,))
                if amount<=0: raise ValueError('Le montant doit être supérieur à 0.')
                if amount>float(r['balance']): raise ValueError(f"Montant supérieur au solde ({money(r['balance'])}).")
                self.db.q('INSERT INTO payments(client_id,credit_id,amount,date,mode,user_id) VALUES(?,?,?,?,?,?)',
                          (r['client_id'],cid,amount,now(),m.get(),self.user['id']))
                # Avance, solde et échéances recalculés à partir de tous les paiements
                # (même règle sur le PC et sur les téléphones).
                cloud_sync.recompute_credit(self.db.c,cid); self.db.c.commit()
                self.audit('Paiement crédit',cid,amount)
                solde=self.db.one('SELECT balance FROM credits WHERE id=?',(cid,))['balance']
                w.destroy(); self.clients()
                messagebox.showinfo('Paiement',f"Paiement de {money(amount)} enregistré.\nNouveau solde du crédit : {money(solde)}")
            except Exception as e: messagebox.showerror('Paiement',str(e),parent=w)
        foot=tk.Frame(w,bg='#f4f7fb'); foot.pack(side='bottom',fill='x',padx=35,pady=18)
        tk.Button(foot,text='ANNULER',command=w.destroy,bg='#777777',fg='white',relief='flat',font=('Segoe UI',10,'bold'),pady=10).pack(side='left',fill='x',expand=True,padx=(0,6))
        tk.Button(foot,text='VALIDER LE PAIEMENT',command=save,bg='#2e7d32',fg='white',relief='flat',font=('Segoe UI',11,'bold'),pady=10).pack(side='left',fill='x',expand=True,padx=(6,0))
        if not cr: messagebox.showinfo('Paiement','Aucun crédit en cours à régler.',parent=w)
    def client_form(self):
        w=tk.Toplevel(self); w.title('Client'); w.geometry('460x430'); vs=[tk.StringVar() for _ in range(4)]
        for v,l in zip(vs,['Nom','Téléphone','Email','Adresse']): tk.Label(w,text=l).pack(anchor='w',padx=25,pady=(15,2)); tk.Entry(w,textvariable=v).pack(fill='x',padx=25)
        def save():
            if not vs[0].get().strip(): return messagebox.showerror('Client','Nom obligatoire')
            self.db.q('INSERT INTO clients(name,phone,email,address) VALUES(?,?,?,?)',tuple(v.get() for v in vs)); self.audit('Création client','',vs[0].get()); w.destroy(); self.clients()
        tk.Button(w,text='ENREGISTRER',command=save,bg='#173a5e',fg='white',relief='flat').pack(fill='x',padx=25,pady=25)
    def expenses(self):
        """Toutes les dépenses enregistrées, avec totaux."""
        self.header('Dépenses','Historique complet des dépenses journalières et mensuelles')
        bar=tk.Frame(self.body,bg='#f4f6f8'); bar.pack(fill='x',padx=20,pady=(10,8))
        tk.Button(bar,text='+ Nouvelle dépense',command=self.expense_form,bg='#173a5e',fg='white',relief='flat',font=('Segoe UI',10,'bold'),padx=14,pady=6).pack(side='right')
        search=tk.StringVar(); tk.Entry(bar,textvariable=search,width=28,font=('Segoe UI',10)).pack(side='left')
        tk.Button(bar,text='Rechercher',command=lambda:load(),bg='#173a5e',fg='white',relief='flat',padx=12,pady=4).pack(side='left',padx=5)
        cards=tk.Frame(self.body,bg='#f4f7fb'); cards.pack(fill='x',padx=16,pady=(0,4))
        zone=tk.Frame(self.body,bg='white',highlightbackground='#dce5ef',highlightthickness=1); zone.pack(fill='both',expand=True,padx=20,pady=8)
        def load():
            for x in cards.winfo_children(): x.destroy()
            for x in zone.winfo_children(): x.destroy()
            jour=self.db.one("SELECT COALESCE(SUM(amount),0) v FROM expenses WHERE date>=?",(today()+' 00:00:00',))['v']
            mois=self.db.one("SELECT COALESCE(SUM(amount),0) v FROM expenses WHERE date>=?",(datetime.date.today().replace(day=1).isoformat()+' 00:00:00',))['v']
            agg=self.db.one('SELECT COALESCE(SUM(amount),0) v,COUNT(*) n FROM expenses')
            self.card(cards,'Dépenses du jour',money(jour),french_date(),'#f5a400','JR')
            self.card(cards,'Dépenses du mois',money(mois),'Mois en cours','#1e73e8','MOIS')
            self.card(cards,'Total des dépenses',money(agg['v']),f"{agg['n']} dépense(s) enregistrée(s)",'#a51d1d','TOT')
            rows=self.db.rows('''SELECT e.id,e.category,e.amount,e.date,COALESCE(e.mode,'-') mode,COALESCE(u.name,'-') agent
                    FROM expenses e LEFT JOIN users u ON u.id=e.user_id ORDER BY e.date DESC,e.id DESC''')
            term=search.get().strip().lower()
            if term: rows=[r for r in rows if term in (r['category'] or '').lower() or term in str(r['date'])]
            tk.Label(zone,text=f'Liste des dépenses ({len(rows)})',font=('Segoe UI',11,'bold'),fg='#173a5e',bg='white').pack(anchor='w',padx=16,pady=(12,2))
            if rows:
                self.table(zone,[('id','N°',70),('cat','Catégorie / motif',300),('amount','Montant',160),
                                 ('date','Date et heure',180),('mode','Mode de paiement',160),('agent','Enregistrée par',180)],
                           [(r['id'],r['category'] or '-',money(r['amount']),r['date'],r['mode'],r['agent']) for r in rows])
            else:
                tk.Label(zone,text='Aucune dépense enregistrée pour le moment.',bg='white',fg='#7b8b9c',font=('Segoe UI',10)).pack(anchor='w',padx=18,pady=18)
        load()
    def expense_form(self):
        w=tk.Toplevel(self); w.title('Nouvelle dépense'); w.geometry('540x480'); w.configure(bg='#f4f7fb'); w.transient(self); w.grab_set()
        tk.Label(w,text='Enregistrer une dépense',font=('Segoe UI',16,'bold'),fg='#173a5e',bg='#f4f7fb').pack(pady=(18,12))
        body=tk.Frame(w,bg='#f4f7fb'); body.pack(fill='both',expand=True,padx=35)
        cat=tk.StringVar(); amount=tk.StringVar()
        motifs=[r['category'] for r in self.db.rows('SELECT DISTINCT category FROM expenses WHERE category IS NOT NULL AND category<>""')]
        base=['Transport','Carburant','Électricité','Eau','Loyer','Salaires','Entretien','Téléphone','Fournitures','Divers']
        tk.Label(body,text='Catégorie / motif',font=('Segoe UI',10,'bold'),fg='#173a5e',bg='#f4f7fb').pack(anchor='w',pady=(8,2))
        ttk.Combobox(body,textvariable=cat,values=sorted(set(base+motifs)),font=('Segoe UI',11)).pack(fill='x',ipady=3)
        tk.Label(body,text='Montant (FCFA)',font=('Segoe UI',10,'bold'),fg='#173a5e',bg='#f4f7fb').pack(anchor='w',pady=(12,2))
        tk.Entry(body,textvariable=amount,font=('Segoe UI',13)).pack(fill='x',ipady=5)
        tk.Label(body,text='Mode de paiement',font=('Segoe UI',10,'bold'),fg='#173a5e',bg='#f4f7fb').pack(anchor='w',pady=(12,2))
        m=tk.StringVar(value='Espèces'); ttk.Combobox(body,textvariable=m,values=PAYMENT_MODES,state='readonly',font=('Segoe UI',11)).pack(fill='x',ipady=3)
        def save():
            try:
                if not cat.get().strip(): raise ValueError('Indiquez la catégorie ou le motif de la dépense.')
                montant=nfloat(amount.get() or 0)
                if montant<=0: raise ValueError('Le montant doit être supérieur à 0.')
                eid=self.db.q('INSERT INTO expenses(category,amount,date,user_id,mode) VALUES(?,?,?,?,?)',
                              (cat.get().strip(),montant,now(),self.user['id'],m.get()))
                self.audit('Dépense',eid,montant)
                w.destroy(); self.expenses()
                messagebox.showinfo('Dépense',f'Dépense n° {eid} enregistrée : {money(montant)}.')
            except Exception as e: messagebox.showerror('Dépense',str(e),parent=w)
        foot=tk.Frame(w,bg='#f4f7fb'); foot.pack(side='bottom',fill='x',padx=35,pady=18)
        tk.Button(foot,text='ANNULER',command=w.destroy,bg='#777777',fg='white',relief='flat',font=('Segoe UI',10,'bold'),pady=10).pack(side='left',fill='x',expand=True,padx=(0,6))
        tk.Button(foot,text='ENREGISTRER',command=save,bg='#173a5e',fg='white',relief='flat',font=('Segoe UI',11,'bold'),pady=10).pack(side='left',fill='x',expand=True,padx=(6,0))
    def inventory(self):
        self.header('Inventaires','Comparaison du stock théorique et du stock réel, magasin et boutique')
        bar=tk.Frame(self.body,bg='#f4f6f8'); bar.pack(fill='x',padx=20,pady=(10,8))
        tk.Button(bar,text='+ Nouvel inventaire',command=self.inventory_form,bg='#173a5e',fg='white',relief='flat',font=('Segoe UI',10,'bold'),padx=14,pady=6).pack(side='right')
        rows=self.db.rows('''SELECT i.id,COALESCE(l.name,'-') lieu,i.date,i.status,COALESCE(u.name,'-') agent,
                COALESCE((SELECT SUM(il.theoretical) FROM inventory_lines il WHERE il.inventory_id=i.id),0) theo,
                COALESCE((SELECT SUM(il.real_qty) FROM inventory_lines il WHERE il.inventory_id=i.id),0) reel,
                COALESCE((SELECT SUM(il.difference) FROM inventory_lines il WHERE il.inventory_id=i.id),0) ecart,
                COALESCE((SELECT p.name FROM inventory_lines il JOIN products p ON p.id=il.product_id WHERE il.inventory_id=i.id LIMIT 1),'-') article
                FROM inventories i LEFT JOIN locations l ON l.id=i.location_id
                LEFT JOIN users u ON u.id=i.user_id ORDER BY i.date DESC,i.id DESC''')
        zone=tk.Frame(self.body,bg='white',highlightbackground='#dce5ef',highlightthickness=1); zone.pack(fill='both',expand=True,padx=20,pady=8)
        tk.Label(zone,text=f'Historique des inventaires ({len(rows)})',font=('Segoe UI',11,'bold'),fg='#173a5e',bg='white').pack(anchor='w',padx=16,pady=(12,2))
        if rows:
            self.table(zone,[('id','N°',60),('lieu','Emplacement',150),('article','Article',240),('date','Date',160),
                             ('theo','Stock théorique',150),('reel','Stock réel',130),('ecart','Écart',110),('agent','Responsable',150)],
                       [(r['id'],r['lieu'],r['article'],r['date'],f"{float(r['theo']):g}",f"{float(r['reel']):g}",f"{float(r['ecart']):+g}",r['agent']) for r in rows])
        else:
            tk.Label(zone,text='Aucun inventaire enregistré.',bg='white',fg='#7b8b9c').pack(anchor='w',padx=18,pady=16)
    def inventory_form(self):
        w=tk.Toplevel(self); w.title('Inventaire'); w.geometry('680x660'); w.configure(bg='#f4f7fb'); w.transient(self); w.grab_set()
        tk.Label(w,text='Nouvel inventaire',font=('Segoe UI',16,'bold'),fg='#173a5e',bg='#f4f7fb').pack(pady=(18,10))
        body=tk.Frame(w,bg='#f4f7fb'); body.pack(fill='both',expand=True,padx=35)
        loc=self.db.rows('SELECT id,name FROM locations ORDER BY id')
        tk.Label(body,text='Emplacement à inventorier',font=('Segoe UI',10,'bold'),fg='#173a5e',bg='#f4f7fb').pack(anchor='w',pady=(8,2))
        lv=tk.StringVar(); ttk.Combobox(body,textvariable=lv,values=[f"{x['id']} - {x['name']}" for x in loc],state='readonly',font=('Segoe UI',11)).pack(fill='x',ipady=3)
        tk.Label(body,text='Article',font=('Segoe UI',10,'bold'),fg='#173a5e',bg='#f4f7fb').pack(anchor='w',pady=(12,2))
        pv=tk.StringVar(); pcombo=ttk.Combobox(body,textvariable=pv,state='readonly',font=('Segoe UI',11)); pcombo.pack(fill='x',ipady=3)
        panel=tk.Frame(body,bg='white',highlightbackground='#dce5ef',highlightthickness=1); panel.pack(fill='x',pady=14)
        lbl_place=tk.Label(panel,text='Sélectionnez un emplacement et un article',font=('Segoe UI',10),fg='#53677e',bg='white')
        lbl_place.pack(anchor='w',padx=16,pady=(12,2))
        lbl_qty=tk.Label(panel,text='—',font=('Segoe UI',22,'bold'),fg='#082b55',bg='white'); lbl_qty.pack(anchor='w',padx=16,pady=(0,12))
        tk.Label(body,text='Stock réel compté',font=('Segoe UI',10,'bold'),fg='#173a5e',bg='#f4f7fb').pack(anchor='w',pady=(4,2))
        rv=tk.Entry(body,font=('Segoe UI',13)); rv.pack(fill='x',ipady=5)
        ecart_lbl=tk.Label(body,text='',bg='#f4f7fb',fg='#a51d1d',font=('Segoe UI',10,'bold')); ecart_lbl.pack(anchor='w',pady=(6,0))
        state={'theo':None}
        def reload_products(*a):
            pv.set('')
            if not lv.get(): return
            lid=int(lv.get().split(' - ',1)[0])
            rows=self.db.products_at(lid,only_positive=False)
            pcombo['values']=[f"{r['id']} - {r['code']} - {r['name']}" for r in rows]
            lbl_place.config(text=f"Emplacement : {lv.get().split(' - ',1)[1]}")
        def show_qty(*a):
            if not lv.get() or not pv.get(): return
            lid=int(lv.get().split(' - ',1)[0]); pid=int(pv.get().split(' - ',1)[0])
            theo=self.db.stock(pid,lid); state['theo']=theo
            unit=self.db.one('''SELECT COALESCE(u.name,'') n FROM products p LEFT JOIN units u ON u.id=p.stock_unit_id WHERE p.id=?''',(pid,))
            place=lv.get().split(' - ',1)[1]
            lbl_place.config(text=f"Quantité disponible en {place.lower()} pour cet article :")
            lbl_qty.config(text=f"{theo:g} {unit['n'] if unit else ''}".strip())
            if not rv.get().strip(): rv.insert(0,f'{theo:g}')
            on_count()
        def on_count(*a):
            if state['theo'] is None: return
            try: reel=nfloat(rv.get() or 0)
            except Exception: ecart_lbl.config(text=''); return
            d=reel-state['theo']
            ecart_lbl.config(text=f"Écart constaté : {d:+g}" + ('  (aucun écart)' if abs(d)<1e-9 else '  — le stock sera ajusté automatiquement'),
                             fg='#2d7d46' if abs(d)<1e-9 else '#a51d1d')
        lv.trace_add('write',reload_products); pv.trace_add('write',show_qty); rv.bind('<KeyRelease>',on_count)
        def save():
            try:
                if not lv.get(): raise ValueError('Sélectionnez un emplacement.')
                if not pv.get(): raise ValueError('Sélectionnez un article.')
                lid=int(lv.get().split(' - ',1)[0]); pid=int(pv.get().split(' - ',1)[0])
                if not rv.get().strip(): raise ValueError('Saisissez le stock réel compté.')
                real=nfloat(rv.get()); theo=self.db.stock(pid,lid); diff=real-theo
                place=lv.get().split(' - ',1)[1]
                iid=self.db.q('INSERT INTO inventories(location_id,date,status,user_id) VALUES(?,?,?,?)',(lid,now(),'Validé',self.user['id']))
                self.db.q('INSERT INTO inventory_lines(inventory_id,product_id,theoretical,real_qty,difference) VALUES(?,?,?,?,?)',(iid,pid,theo,real,diff))
                if abs(diff)>1e-9:
                    self.db.adjust(pid,lid,diff,'Ajustement inventaire',self.user['id'],str(iid),'Correction inventaire')
                self.audit('Inventaire',iid,f'écart {diff:+g}')
                nouveau=self.db.stock(pid,lid)
                w.destroy(); self.inventory()
                messagebox.showinfo('Inventaire',
                    f"Inventaire n° {iid} validé.\nEmplacement : {place}\n"
                    f"Stock théorique : {theo:g}\nStock réel compté : {real:g}\nÉcart : {diff:+g}\n"
                    f"Quantité disponible désormais en {place.lower()} : {nouveau:g}")
            except Exception as e: messagebox.showerror('Inventaire',str(e),parent=w)
        foot=tk.Frame(w,bg='#f4f7fb'); foot.pack(side='bottom',fill='x',padx=35,pady=18)
        tk.Button(foot,text='ANNULER',command=w.destroy,bg='#777777',fg='white',relief='flat',font=('Segoe UI',10,'bold'),pady=10).pack(side='left',fill='x',expand=True,padx=(0,6))
        tk.Button(foot,text='VALIDER L’INVENTAIRE',command=save,bg='#173a5e',fg='white',relief='flat',font=('Segoe UI',11,'bold'),pady=10).pack(side='left',fill='x',expand=True,padx=(6,0))
        if loc: lv.set(f"{loc[0]['id']} - {loc[0]['name']}")
    def _period_bounds(self,preset):
        d=datetime.date.today()
        if preset=='Aujourd’hui': return d,d
        if preset=='7 derniers jours': return d-datetime.timedelta(days=6),d
        if preset=='Mois en cours': return d.replace(day=1),d
        if preset=='Mois précédent':
            first=d.replace(day=1); last_prev=first-datetime.timedelta(days=1)
            return last_prev.replace(day=1),last_prev
        if preset=='Année en cours': return d.replace(month=1,day=1),d
        return None,None
    def reports(self):
        self.header('Rapports & statistiques','Filtrage par période, marges par produit et exports')
        wrap=tk.Frame(self.body,bg='#f4f7fb'); wrap.pack(fill='both',expand=True,padx=16,pady=12)
        bar=tk.Frame(wrap,bg='white',highlightbackground='#dce5ef',highlightthickness=1); bar.pack(fill='x')
        tk.Label(bar,text='Période :',bg='white',font=('Segoe UI',10,'bold'),fg='#173a5e').grid(row=0,column=0,padx=(18,6),pady=12)
        preset=tk.StringVar(value='Mois en cours')
        presets=['Aujourd’hui','7 derniers jours','Mois en cours','Mois précédent','Année en cours','Tout','Personnalisé']
        ttk.Combobox(bar,textvariable=preset,values=presets,state='readonly',width=18).grid(row=0,column=1,pady=12)
        start=tk.StringVar(); end=tk.StringVar()
        tk.Label(bar,text='Du',bg='white').grid(row=0,column=2,padx=(18,4)); tk.Entry(bar,textvariable=start,width=13).grid(row=0,column=3)
        tk.Label(bar,text='Au',bg='white').grid(row=0,column=4,padx=(12,4)); tk.Entry(bar,textvariable=end,width=13).grid(row=0,column=5)
        tk.Label(bar,text='format AAAA-MM-JJ',bg='white',fg='#65788c',font=('Segoe UI',8)).grid(row=0,column=6,padx=8)
        cards=tk.Frame(wrap,bg='#f4f7fb'); cards.pack(fill='x',pady=(10,0))
        table_zone=tk.Frame(wrap,bg='white',highlightbackground='#dce5ef',highlightthickness=1); table_zone.pack(fill='both',expand=True,pady=10)
        self._report_rows=[]; self._report_totals={}

        def resolve_period():
            if preset.get()=='Tout': return None,None
            if preset.get()=='Personnalisé':
                try:
                    a=datetime.date.fromisoformat(start.get().strip()) if start.get().strip() else None
                    b=datetime.date.fromisoformat(end.get().strip()) if end.get().strip() else None
                except ValueError:
                    raise ValueError('Dates invalides. Utilisez le format AAAA-MM-JJ.')
                if a and b and a>b: raise ValueError('La date de début est postérieure à la date de fin.')
                return a,b
            a,b=self._period_bounds(preset.get()); start.set(a.isoformat() if a else ''); end.set(b.isoformat() if b else ''); return a,b

        def clause(field='date'):
            a,b=resolve_period(); cond=''; params=[]
            if a: cond+=f' AND {field}>=?'; params.append(a.isoformat()+' 00:00:00')
            if b: cond+=f' AND {field}<?'; params.append((b+datetime.timedelta(days=1)).isoformat()+' 00:00:00')
            return cond,params,a,b

        def load():
            try:
                cond,params,a,b=clause('s.date')
                sales_total=self.db.one(f'SELECT COALESCE(SUM(total),0) v FROM sales s WHERE 1=1{cond}',params)['v']
                cash=self.db.one(f'SELECT COALESCE(SUM(paid),0) v FROM sales s WHERE 1=1{cond}',params)['v']
                nb=self.db.one(f'SELECT COUNT(*) v FROM sales s WHERE 1=1{cond}',params)['v']
                pcond,pparams,_,_=clause('a.date')
                purchases=self.db.one(f'SELECT COALESCE(SUM(total),0) v FROM purchases a WHERE 1=1{pcond}',pparams)['v']
                econd,eparams,_,_=clause('e.date')
                expenses=self.db.one(f'SELECT COALESCE(SUM(amount),0) v FROM expenses e WHERE 1=1{econd}',eparams)['v']
                credits=self.db.one('SELECT COALESCE(SUM(balance),0) v FROM credits WHERE balance>0')['v']
                rows=self.db.rows(f'''SELECT p.code,p.name,COALESCE(c.name,'-') category,
                        SUM(sl.qty) qty, SUM(sl.qty*sl.price) revenue,
                        SUM(sl.qty*COALESCE(NULLIF(sl.cost,0),p.purchase_price)) cost
                        FROM sale_lines sl JOIN sales s ON s.id=sl.sale_id
                        JOIN products p ON p.id=sl.product_id
                        LEFT JOIN categories c ON c.id=p.category_id
                        WHERE 1=1{cond} GROUP BY p.id ORDER BY revenue DESC''',params)
                margin=sum(float(r['revenue'] or 0)-float(r['cost'] or 0) for r in rows)
                gross=sum(float(r['revenue'] or 0) for r in rows)
                rate=(margin/gross*100) if gross else 0
                for w in cards.winfo_children(): w.destroy()
                line1=tk.Frame(cards,bg='#f4f7fb'); line1.pack(fill='x')
                self.card(line1,'Chiffre d’affaires',money(sales_total),f'{nb} vente(s)','#18b65b','CA')
                self.card(line1,'Marge brute',money(margin),f'Taux {rate:.1f} %','#1e73e8','MRG')
                self.card(line1,'Achats',money(purchases),'Réceptions','#7c4dff','ACH')
                self.card(line1,'Dépenses',money(expenses),'Charges','#f5a400','DEP')
                line2=tk.Frame(cards,bg='#f4f7fb'); line2.pack(fill='x',pady=(8,0))
                self.card(line2,'Encaissé',money(cash),'Règlements sur ventes','#00a6a6','ENC')
                self.card(line2,'Reste à encaisser',money(float(sales_total)-float(cash)),'Sur la période','#ef3038','DU')
                self.card(line2,'Résultat indicatif',money(margin-float(expenses)),'Marge brute moins dépenses','#062d5c','RES')
                self.card(line2,'Crédits en cours',money(credits),'Tous clients','#a51d1d','CRD')
                for w in table_zone.winfo_children(): w.destroy()
                titre='toute la période' if not a else f"du {a.isoformat()} au {(b or datetime.date.today()).isoformat()}"
                tk.Label(table_zone,text=f'Marges par produit — {titre}',font=('Segoe UI',12,'bold'),fg='#173a5e',bg='white').pack(anchor='w',padx=18,pady=(14,2))
                heads=[('code','Code',110),('name','Produit',260),('category','Catégorie',160),('qty','Qté vendue',110),
                       ('revenue','CA',140),('cost','Coût',140),('margin','Marge',140),('rate','Taux',90)]
                data=[]
                for r in rows:
                    rev=float(r['revenue'] or 0); cst=float(r['cost'] or 0); mg=rev-cst
                    data.append((r['code'],r['name'],r['category'],f"{float(r['qty'] or 0):g}",money(rev),money(cst),money(mg),f"{(mg/rev*100) if rev else 0:.1f} %"))
                if not data:
                    tk.Label(table_zone,text='Aucune vente sur la période sélectionnée.',bg='white',fg='#65788c',font=('Segoe UI',10)).pack(anchor='w',padx=20,pady=20)
                else:
                    self.table(table_zone,heads,data)
                self._report_rows=[(r['code'],r['name'],r['category'],float(r['qty'] or 0),float(r['revenue'] or 0),float(r['cost'] or 0),float(r['revenue'] or 0)-float(r['cost'] or 0)) for r in rows]
                self._report_totals={'periode':titre,'ca':float(sales_total),'marge':margin,'achats':float(purchases),'depenses':float(expenses),'encaisse':float(cash),'credits':float(credits),'ventes':nb}
            except Exception as e:
                messagebox.showerror('Rapports',str(e))
        tk.Button(bar,text='Appliquer',command=load,bg='#173a5e',fg='white',relief='flat',font=('Segoe UI',9,'bold'),padx=16,pady=5).grid(row=0,column=7,padx=10)
        actions=tk.Frame(wrap,bg='#f4f7fb'); actions.pack(fill='x',pady=(8,0),before=table_zone)
        tk.Button(actions,text='Exporter le rapport (CSV)',command=lambda:self.export_report_csv(),bg='#2e7d32',fg='white',relief='flat',font=('Segoe UI',9,'bold'),padx=14,pady=6).pack(side='left')
        tk.Button(actions,text='Exporter le rapport (PDF)',command=lambda:self.export_report_pdf(),bg='#1769d3',fg='white',relief='flat',font=('Segoe UI',9,'bold'),padx=14,pady=6).pack(side='left',padx=8)
        tk.Button(actions,text='Exporter produits CSV',command=self.export_products,relief='flat',padx=14,pady=6).pack(side='left',padx=8)
        tk.Button(actions,text='Ouvrir le dossier des factures',command=lambda:(open_path(BASE) or messagebox.showinfo('Dossier',str(BASE))),relief='flat',padx=14,pady=6).pack(side='left',padx=8)
        preset.trace_add('write',lambda *a: load())
        load()
    def export_report_csv(self):
        if not self._report_totals: messagebox.showwarning('Rapport','Affichez d’abord un rapport.'); return
        f=filedialog.asksaveasfilename(defaultextension='.csv',filetypes=[('CSV','*.csv')],initialfile='rapport_amuntchi.csv')
        if not f: return
        t=self._report_totals
        with open(f,'w',newline='',encoding='utf-8-sig') as h:
            wr=csv.writer(h,delimiter=';')
            wr.writerow(['Mini Quincaillerie AMUNTCHI - Rapport',t['periode']])
            wr.writerow([])
            wr.writerow(['Chiffre d’affaires',round(t['ca'],2)]); wr.writerow(['Marge brute',round(t['marge'],2)])
            wr.writerow(['Achats',round(t['achats'],2)]); wr.writerow(['Dépenses',round(t['depenses'],2)])
            wr.writerow(['Encaissé',round(t['encaisse'],2)]); wr.writerow(['Crédits en cours',round(t['credits'],2)])
            wr.writerow([]); wr.writerow(['code','produit','categorie','qte_vendue','ca','cout','marge'])
            for r in self._report_rows: wr.writerow([r[0],r[1],r[2],r[3],round(r[4],2),round(r[5],2),round(r[6],2)])
        self.audit('Export rapport CSV','',f); messagebox.showinfo('Rapport','Export CSV terminé.')
    def export_report_pdf(self):
        if canvas is None: messagebox.showerror('Rapport','Module PDF (reportlab) indisponible.'); return
        if not self._report_totals: messagebox.showwarning('Rapport','Affichez d’abord un rapport.'); return
        t=self._report_totals
        fn=BASE/f"Rapport_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        c=canvas.Canvas(str(fn),pagesize=A4); W,H=A4
        if LOGO.exists():
            try: c.drawImage(ImageReader(str(LOGO)),35,H-95,width=95,height=70,preserveAspectRatio=True,mask='auto')
            except Exception: pass
        c.setFont('Helvetica-Bold',15); c.drawCentredString(W/2+40,H-45,COMM['name'])
        c.setFont('Helvetica',9); c.drawCentredString(W/2+40,H-60,COMM['rccm']); c.drawCentredString(W/2+40,H-73,'TEL : '+COMM['phone'])
        c.setFont('Helvetica-Bold',12); c.drawString(35,H-115,'RAPPORT D’ACTIVITÉ')
        c.setFont('Helvetica',10); c.drawString(35,H-130,f"Période : {t['periode']}   |   Édité le {french_date()}")
        y=H-160; c.setFont('Helvetica-Bold',10)
        for label,val in [('Chiffre d’affaires',t['ca']),('Marge brute',t['marge']),('Achats',t['achats']),
                          ('Dépenses',t['depenses']),('Encaissé',t['encaisse']),
                          ('Résultat indicatif',t['marge']-t['depenses']),('Crédits en cours',t['credits'])]:
            c.drawString(40,y,label+' :'); c.drawRightString(300,y,money(val)); y-=16
        y-=14; c.setFont('Helvetica-Bold',10); c.drawString(35,y,'Marges par produit'); y-=16
        cols=[(35,'Code'),(100,'Produit'),(300,'Qté'),(360,'CA'),(440,'Coût'),(520,'Marge')]
        c.setFont('Helvetica-Bold',8.5)
        for x,l in cols: c.drawString(x,y,l)
        y-=4; c.line(35,y,W-35,y); y-=12; c.setFont('Helvetica',8.5)
        for r in self._report_rows:
            if y<60:
                c.showPage(); y=H-60; c.setFont('Helvetica',8.5)
            c.drawString(35,y,str(r[0] or '')[:12]); c.drawString(100,y,str(r[1])[:32])
            c.drawRightString(340,y,f'{r[3]:g}'); c.drawRightString(425,y,money(r[4]))
            c.drawRightString(505,y,money(r[5])); c.drawRightString(W-35,y,money(r[6])); y-=14
        c.save(); self.audit('Export rapport PDF','',str(fn))
        open_path(fn); messagebox.showinfo('Rapport',f'Rapport PDF créé :\n{fn}')
    def export_products(self):
        f=filedialog.asksaveasfilename(defaultextension='.csv',filetypes=[('CSV','*.csv')],initialfile='produits_amuntchi.csv');
        if not f:return
        mid=self.db.magasin_id(); bid=self.db.boutique_id()
        rows=self.db.rows('''SELECT p.code,p.name,p.brand,p.purchase_price,p.sale_price,p.min_stock,
                COALESCE((SELECT qty FROM stocks s WHERE s.product_id=p.id AND s.location_id=?),0) magasin,
                COALESCE((SELECT qty FROM stocks s WHERE s.product_id=p.id AND s.location_id=?),0) boutique,
                COALESCE((SELECT SUM(qty) FROM stocks s WHERE s.product_id=p.id),0) stock,
                COALESCE(su.name,'') unite_stock,COALESCE(vu.name,'') unite_vente
                FROM products p LEFT JOIN units su ON su.id=p.stock_unit_id
                LEFT JOIN units vu ON vu.id=p.sale_unit_id GROUP BY p.id ORDER BY p.name''',(mid,bid))
        with open(f,'w',newline='',encoding='utf-8-sig') as h:
            wr=csv.writer(h,delimiter=';')
            wr.writerow(['code','designation','marque','prix_achat','prix_vente','seuil','unite_stock','unite_vente','qte_magasin','qte_boutique','qte_totale'])
            for r in rows:
                wr.writerow([r[x] for x in ['code','name','brand','purchase_price','sale_price','min_stock','unite_stock','unite_vente','magasin','boutique','stock']])
        messagebox.showinfo('Export','Export terminé')
    def settings(self):
        self.header('Paramètres & sauvegarde','Configuration commerciale, synchronisation et sécurité')
        # Zone défilante : la page est longue, toutes les sections restent accessibles.
        shell=tk.Frame(self.body,bg='#f4f7fb'); shell.pack(fill='both',expand=True)
        cv=tk.Canvas(shell,bg='#f4f7fb',highlightthickness=0); cv.pack(side='left',fill='both',expand=True,padx=(25,0),pady=18)
        vs=ttk.Scrollbar(shell,orient='vertical',command=cv.yview); vs.pack(side='right',fill='y',pady=18)
        cv.configure(yscrollcommand=vs.set)
        outer=tk.Frame(cv,bg='#f4f7fb')
        win=cv.create_window((0,0),window=outer,anchor='nw')
        def _resize(event=None):
            cv.configure(scrollregion=cv.bbox('all')); cv.itemconfigure(win,width=cv.winfo_width()-10)
        outer.bind('<Configure>',_resize); cv.bind('<Configure>',_resize)
        def _wheel(event):
            cv.yview_scroll(int(-1*(event.delta/120)) if abs(event.delta)>=120 else -1*event.delta,'units')
        cv.bind_all('<MouseWheel>',_wheel)
        cv.bind_all('<Button-4>',lambda e: cv.yview_scroll(-3,'units'))
        cv.bind_all('<Button-5>',lambda e: cv.yview_scroll(3,'units'))

        # Numérotation facture
        f=tk.Frame(outer,bg='white',bd=1,relief='solid'); f.pack(fill='x',pady=(0,12))
        tk.Label(f,text='Numérotation facture',font=('Segoe UI',12,'bold'),fg='#173a5e',bg='white').grid(row=0,column=0,columnspan=3,sticky='w',padx=20,pady=(16,8))
        pre=tk.StringVar(value=self.db.setting('invoice_prefix','AM-'))
        tk.Label(f,text='Préfixe :',bg='white').grid(row=1,column=0,padx=20,pady=10,sticky='w')
        tk.Entry(f,textvariable=pre,width=20).grid(row=1,column=1,pady=10,sticky='w')
        def save_prefix():
            self.db.q("UPDATE settings SET value=? WHERE key='invoice_prefix'",(pre.get().strip(),))
            self.audit('Modification paramètres','invoice_prefix',pre.get().strip())
            messagebox.showinfo('Paramètres','Préfixe enregistré.')
        tk.Button(f,text='Enregistrer',command=save_prefix,bg='#173a5e',fg='white',relief='flat').grid(row=1,column=2,padx=15,pady=10)

        # Gestion des catégories
        catf=tk.Frame(outer,bg='white',bd=1,relief='solid'); catf.pack(fill='x',pady=12)
        tk.Label(catf,text='Catégories de produits',font=('Segoe UI',12,'bold'),fg='#173a5e',bg='white').grid(row=0,column=0,columnspan=3,sticky='w',padx=20,pady=(16,8))
        cat_entry=tk.Entry(catf,width=50); cat_entry.grid(row=1,column=0,padx=20,pady=8,sticky='we')
        cat_list=tk.Listbox(catf,height=5,font=('Segoe UI',10)); cat_list.grid(row=2,column=0,columnspan=2,padx=20,pady=(2,14),sticky='we')
        def refresh_categories():
            cat_list.delete(0,'end')
            for rr in self.db.rows('SELECT id,name FROM categories ORDER BY name'):
                cat_list.insert('end',f"{rr['id']} - {rr['name']}")
        def add_category():
            name=cat_entry.get().strip()
            if not name: return
            try:
                self.db.q('INSERT INTO categories(name) VALUES(?)',(name,))
                cat_entry.delete(0,'end'); refresh_categories(); self.audit('Création catégorie','',name)
            except Exception as e: messagebox.showerror('Catégorie',str(e))
        def delete_category():
            sel=cat_list.curselection()
            if not sel: return
            item=cat_list.get(sel[0]); cid=int(item.split(' - ',1)[0]); name=item.split(' - ',1)[1]
            used=self.db.one('SELECT COUNT(*) c FROM products WHERE category_id=?',(cid,))['c']
            if used:
                messagebox.showwarning('Catégorie',f'Impossible de supprimer « {name} » : {used} produit(s) utilisent cette catégorie.')
                return
            if messagebox.askyesno('Catégorie',f'Supprimer « {name} » ?'):
                self.db.q('DELETE FROM categories WHERE id=?',(cid,)); refresh_categories(); self.audit('Suppression catégorie',cid,name)
        tk.Button(catf,text='+ Ajouter',command=add_category,bg='#173a5e',fg='white',relief='flat').grid(row=1,column=1,padx=8,pady=8)
        tk.Button(catf,text='Supprimer',command=delete_category,bg='#b3261e',fg='white',relief='flat').grid(row=1,column=2,padx=8,pady=8)
        catf.columnconfigure(0,weight=1)
        refresh_categories()

        # Synchronisation Cloud Supabase (V5)
        cf=tk.Frame(outer,bg='white',bd=1,relief='solid'); cf.pack(fill='x',pady=12)
        tk.Label(cf,text='Synchronisation Cloud PC / Android (Supabase)',font=('Segoe UI',12,'bold'),fg='#173a5e',bg='white').grid(row=0,column=0,columnspan=4,sticky='w',padx=20,pady=(16,4))
        tk.Label(cf,text='Le PC et les téléphones Android partagent les mêmes données via Supabase. Chaque appareil travaille même sans Internet '
                 'et envoie / reçoit les changements dès que la connexion revient. Les ventes faites en même temps sur plusieurs appareils sont toutes conservées.',
                 fg='#65788c',bg='white',wraplength=1050,justify='left').grid(row=1,column=0,columnspan=4,sticky='w',padx=20,pady=(0,10))
        cvars={k:tk.StringVar(value=self.db.setting(k,'')) for k in ('supa_url','supa_key','supa_email','supa_password','sync_device_name')}
        for i,(k,label,secret) in enumerate([('supa_url','Adresse du projet (Project URL) :',False),('supa_key','Clé publique (anon public key) :',True),
                                             ('supa_email','E-mail du compte boutique :',False),('supa_password','Mot de passe du compte boutique :',True),
                                             ('sync_device_name','Nom de cet appareil :',False)]):
            tk.Label(cf,text=label,bg='white').grid(row=2+i,column=0,padx=20,pady=6,sticky='w')
            tk.Entry(cf,textvariable=cvars[k],width=70,show='•' if secret else '').grid(row=2+i,column=1,columnspan=3,pady=6,sticky='we',padx=(0,20))
        cf.columnconfigure(1,weight=1)
        auto=tk.BooleanVar(value=self.db.setting('cloud_auto','1')=='1')
        tk.Checkbutton(cf,text='Synchronisation automatique (toutes les 2 minutes et peu après chaque opération)',variable=auto,bg='white').grid(row=7,column=0,columnspan=4,sticky='w',padx=16,pady=(4,2))
        etat=tk.Label(cf,text='',bg='white',fg='#1769d3',font=('Segoe UI',9,'bold'),justify='left')
        etat.grid(row=9,column=0,columnspan=4,sticky='w',padx=20,pady=(2,14))
        def show_state():
            try: n=cloud_sync.pending_count(self.db.c)
            except Exception: n=0
            last=self.db.setting('cloud_last_sync','') or 'jamais'
            etat.config(text=f'Dernière synchronisation réussie : {last}   |   Modifications en attente d’envoi : {n}')
        def save_cloud():
            for k,v in cvars.items(): self.db.set_setting(k,v.get().strip())
            self.db.set_setting('cloud_auto','1' if auto.get() else '0')
            self.audit('Modification paramètres Cloud','',cvars['supa_url'].get().strip())
            messagebox.showinfo('Cloud','Paramètres Cloud enregistrés.'); show_state()
        def test_cloud():
            for k,v in cvars.items(): self.db.set_setting(k,v.get().strip())
            try:
                self.cloud.client().login()
                messagebox.showinfo('Cloud','Connexion au Cloud réussie.')
            except Exception as e: messagebox.showerror('Cloud',str(e))
        def sync_now():
            for k,v in cvars.items(): self.db.set_setting(k,v.get().strip())
            self.db.set_setting('cloud_auto','1' if auto.get() else '0')
            self.cloud.sync(manual=True,done=show_state)
        brow=tk.Frame(cf,bg='white'); brow.grid(row=8,column=0,columnspan=4,sticky='w',padx=20,pady=8)
        tk.Button(brow,text='Enregistrer',command=save_cloud,bg='#173a5e',fg='white',relief='flat',padx=14,pady=5).pack(side='left')
        tk.Button(brow,text='Tester la connexion',command=test_cloud,bg='#1769d3',fg='white',relief='flat',padx=14,pady=5).pack(side='left',padx=8)
        tk.Button(brow,text='Synchroniser maintenant',command=sync_now,bg='#2e7d32',fg='white',relief='flat',padx=14,pady=5).pack(side='left')
        show_state()

        # Sécurité des comptes
        secf=tk.Frame(outer,bg='white',bd=1,relief='solid'); secf.pack(fill='x',pady=12)
        tk.Label(secf,text='Sécurité des comptes',font=('Segoe UI',12,'bold'),fg='#173a5e',bg='white').pack(anchor='w',padx=20,pady=(14,4))
        migr='oui' if self.db.setting('password_migrated')=='1' else 'non'
        tk.Label(secf,text=f'Les mots de passe sont chiffrés (PBKDF2-SHA256, sel aléatoire, 200 000 itérations). Migration effectuée : {migr}.',fg='#65788c',bg='white',wraplength=1000,justify='left').pack(anchor='w',padx=20,pady=(0,6))
        srow=tk.Frame(secf,bg='white'); srow.pack(anchor='w',padx=20,pady=(0,15))
        tk.Button(srow,text='Changer mon mot de passe',command=self.change_own_password,bg='#173a5e',fg='white',relief='flat',font=('Segoe UI',10,'bold'),padx=14,pady=6).pack(side='left')
        if self.user.get('role')=='Administrateur':
            tk.Button(srow,text='Gérer les utilisateurs',command=self.users,bg='#1769d3',fg='white',relief='flat',font=('Segoe UI',10,'bold'),padx=14,pady=6).pack(side='left',padx=8)

        # Sauvegarde
        bf=tk.Frame(outer,bg='white',bd=1,relief='solid'); bf.pack(fill='x',pady=12)
        tk.Label(bf,text='Sauvegarde',font=('Segoe UI',12,'bold'),fg='#173a5e',bg='white').pack(anchor='w',padx=20,pady=(14,8))
        brow=tk.Frame(bf,bg='white'); brow.pack(anchor='w',padx=20,pady=(0,15))
        tk.Button(brow,text='Sauvegarder la base de données',command=self.backup,bg='#2e7d32',fg='white',relief='flat',font=('Segoe UI',10,'bold'),padx=14,pady=6).pack(side='left')
        tk.Button(brow,text='Ouvrir le dossier des données',command=lambda:(open_path(BASE) or messagebox.showinfo('Dossier',str(BASE))),relief='flat',padx=14,pady=6).pack(side='left',padx=8)

        # Réinitialisation sécurisée
        rf=tk.Frame(outer,bg='#fff8f8',bd=1,relief='solid'); rf.pack(fill='x',pady=12)
        tk.Label(rf,text='Réinitialisation sécurisée des données',font=('Segoe UI',12,'bold'),fg='#a51d1d',bg='#fff8f8').pack(anchor='w',padx=20,pady=(14,4))
        tk.Label(rf,text='Cette opération crée automatiquement une sauvegarde avant de supprimer les données commerciales.',fg='#7a4a4a',bg='#fff8f8').pack(anchor='w',padx=20,pady=3)
        tk.Button(rf,text='RÉINITIALISER LES DONNÉES',command=self.secure_reset,bg='#b3261e',fg='white',relief='flat',font=('Segoe UI',10,'bold')).pack(anchor='w',padx=20,pady=14)

    # ------------------------------------------------------------------
    # Sécurité des comptes
    # ------------------------------------------------------------------
    def change_own_password(self):
        w=tk.Toplevel(self); w.title('Changer mon mot de passe'); w.geometry('520x360'); w.transient(self); w.grab_set(); w.configure(bg='#f4f7fb')
        tk.Label(w,text='Changer mon mot de passe',font=('Segoe UI',15,'bold'),fg='#173a5e',bg='#f4f7fb').pack(pady=(20,12))
        old=tk.StringVar(); new=tk.StringVar(); conf=tk.StringVar()
        for label,var in [('Mot de passe actuel',old),('Nouveau mot de passe',new),('Confirmer le nouveau mot de passe',conf)]:
            tk.Label(w,text=label,bg='#f4f7fb',font=('Segoe UI',10,'bold'),fg='#173a5e').pack(anchor='w',padx=40,pady=(8,2))
            tk.Entry(w,textvariable=var,show='•',font=('Segoe UI',12)).pack(fill='x',padx=40)
        def save():
            r=self.db.one('SELECT password FROM users WHERE id=?',(self.user['id'],))
            if not r or not verify_password(old.get(),r['password']):
                messagebox.showerror('Sécurité','Mot de passe actuel incorrect.',parent=w); return
            if new.get()!=conf.get():
                messagebox.showerror('Sécurité','Les deux nouveaux mots de passe ne correspondent pas.',parent=w); return
            faible=password_strength_error(new.get())
            if faible: messagebox.showwarning('Mot de passe',faible,parent=w); return
            self.db.q('UPDATE users SET password=? WHERE id=?',(hash_password(new.get()),self.user['id']))
            self.audit('Changement mot de passe',self.user['username'],'PBKDF2-SHA256')
            w.destroy(); messagebox.showinfo('Sécurité','Mot de passe modifié avec succès.')
        tk.Button(w,text='ENREGISTRER',command=save,bg='#173a5e',fg='white',relief='flat',font=('Segoe UI',11,'bold'),height=2).pack(fill='x',padx=40,pady=25)
        w.bind('<Return>',lambda e:save())

    def reset_user_password(self):
        """Un administrateur définit un nouveau mot de passe pour un autre compte."""
        if self.user.get('role')!='Administrateur':
            messagebox.showerror('Sécurité','Seul un Administrateur peut réinitialiser un mot de passe.'); return
        tree=getattr(self,'_users_tree',None)
        if tree is None or not tree.selection():
            messagebox.showwarning('Utilisateurs','Sélectionnez d’abord un utilisateur.'); return
        uid=int(tree.selection()[0]); target=self.db.one('SELECT id,name,username FROM users WHERE id=?',(uid,))
        if not target: return
        w=tk.Toplevel(self); w.title('Réinitialiser un mot de passe'); w.geometry('520x380'); w.transient(self); w.grab_set(); w.configure(bg='#f4f7fb')
        tk.Label(w,text=f"Compte : {target['name']} ({target['username']})",font=('Segoe UI',12,'bold'),fg='#173a5e',bg='#f4f7fb').pack(pady=(22,14))
        admin_pw=tk.StringVar(); new=tk.StringVar(); conf=tk.StringVar()
        for label,var in [('Votre mot de passe administrateur',admin_pw),('Nouveau mot de passe du compte',new),('Confirmer',conf)]:
            tk.Label(w,text=label,bg='#f4f7fb',font=('Segoe UI',10,'bold'),fg='#173a5e').pack(anchor='w',padx=40,pady=(8,2))
            tk.Entry(w,textvariable=var,show='•',font=('Segoe UI',12)).pack(fill='x',padx=40)
        def save():
            me=self.db.one('SELECT password FROM users WHERE id=?',(self.user['id'],))
            if not me or not verify_password(admin_pw.get(),me['password']):
                messagebox.showerror('Sécurité','Mot de passe administrateur incorrect.',parent=w); return
            if new.get()!=conf.get():
                messagebox.showerror('Sécurité','Les deux mots de passe ne correspondent pas.',parent=w); return
            faible=password_strength_error(new.get())
            if faible: messagebox.showwarning('Mot de passe',faible,parent=w); return
            self.db.q('UPDATE users SET password=? WHERE id=?',(hash_password(new.get()),uid))
            self.audit('Réinitialisation mot de passe',target['username'],'Nouveau mot de passe défini')
            w.destroy(); messagebox.showinfo('Sécurité',f"Mot de passe du compte « {target['name']} » réinitialisé.")
        tk.Button(w,text='VALIDER',command=save,bg='#1769d3',fg='white',relief='flat',font=('Segoe UI',11,'bold'),height=2).pack(fill='x',padx=40,pady=25)

    # ------------------------------------------------------------------
    # Contrôle de conflit avant tout remplacement de données
    # ------------------------------------------------------------------
    def _conflict_confirmed(self,payload,source):
        """Compare l'horodatage du paquet avec les dernières écritures locales."""
        remote=str(payload.get('created_at') or '')
        local=self.db.last_business_change()
        last_sync=self.db.setting('last_sync_at','')
        device=str(payload.get('device') or 'inconnu')
        details=(f"Source : {source}\nAppareil d’origine : {device}\n"
                 f"Paquet créé le : {remote or 'non renseigné'}\n"
                 f"Dernière écriture locale : {local or 'aucune'}\n"
                 f"Dernière synchronisation : {last_sync or 'jamais'}")
        if remote and local and local > remote:
            return messagebox.askyesno('Conflit de synchronisation détecté',
                'ATTENTION : des opérations ont été enregistrées sur ce poste APRÈS la création du paquet.\n\n'
                f'{details}\n\nContinuer écrasera ces opérations locales (une sauvegarde sera créée).\n'
                'Voulez-vous vraiment continuer ?',icon='warning')
        if last_sync and remote and remote <= last_sync:
            return messagebox.askyesno('Paquet déjà appliqué ?',
                f'Ce paquet ne semble pas plus récent que la dernière synchronisation.\n\n{details}\n\nContinuer quand même ?')
        return messagebox.askyesno('Synchronisation',
            f'{details}\n\nLes données métier locales vont être remplacées.\n'
            'Une sauvegarde automatique sera créée. Continuer ?')

    def _sync_tables(self):
        return list(SYNC_TABLES)

    def _sync_payload(self):
        payload={'format':'AMUNTCHI_SYNC_V1','app':'Amuntchi','version':VERSION,'created_at':now(),
                 'device':self.db.setting('sync_device_name','AMUNTCHI-PC'),
                 'last_local_change':self.db.last_business_change(),'tables':{}}
        for table in self._sync_tables():
            rows=self.db.rows(f'SELECT * FROM {table}')
            payload['tables'][table]=[dict(r) for r in rows]
        return payload

    def export_sync_package(self):
        folder=self.db.one("SELECT value FROM settings WHERE key='sync_folder'")['value'] or str(BASE/'sync')
        os.makedirs(folder,exist_ok=True)
        fn=os.path.join(folder,f"amuntchi_sync_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        try:
            with open(fn,'w',encoding='utf-8') as h: json.dump(self._sync_payload(),h,ensure_ascii=False,indent=2,default=str)
            self.audit('Export synchronisation','',fn)
            messagebox.showinfo('Synchronisation',f'Paquet créé :\n{fn}')
        except Exception as e:
            messagebox.showerror('Synchronisation',str(e))

    def import_sync_package(self):
        f=filedialog.askopenfilename(filetypes=[('Paquet AMUNTCHI','*.json')],initialdir=self.db.one("SELECT value FROM settings WHERE key='sync_folder'")['value'] or str(BASE/'sync'))
        if not f:return
        try:
            with open(f,encoding='utf-8') as h: payload=json.load(h)
            if payload.get('format')!='AMUNTCHI_SYNC_V1': raise ValueError('Paquet de synchronisation AMUNTCHI non reconnu.')
            if not self._conflict_confirmed(payload,f): return
            backup_path=self.db.backup_now('pre_sync_backup')
            # Remplacement transactionnel des tables métier
            self.db.c.execute('BEGIN')
            try:
                for table in reversed(self._sync_tables()):
                    self.db.c.execute(f'DELETE FROM {table}')
                order=['categories','units','conversions','locations','suppliers','clients','products','stocks','movements','purchases','purchase_lines','sales','sale_lines','invoices','credits','installments','payments','expenses','transfers','transfer_lines','inventories','inventory_lines']
                for table in order:
                    rows=payload['tables'].get(table,[])
                    if not rows: continue
                    cols=list(rows[0].keys()); marks=','.join('?' for _ in cols)
                    self.db.c.executemany(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({marks})",[[r.get(c) for c in cols] for r in rows])
                self.db.c.commit()
            except Exception:
                self.db.c.rollback(); raise
            self.db.set_setting('last_sync_at',str(payload.get('created_at') or now()))
            self.audit('Import synchronisation','',f)
            messagebox.showinfo('Synchronisation',f'Synchronisation importée.\nSauvegarde locale : {backup_path}')
            self.main()
        except Exception as e:
            messagebox.showerror('Synchronisation',str(e))

    def cloud_upload(self):
        folder=(self.db.one("SELECT value FROM settings WHERE key='cloud_folder'")['value'] or '').strip()
        url=(self.db.one("SELECT value FROM settings WHERE key='cloud_url'")['value'] or '').strip()
        token=(self.db.one("SELECT value FROM settings WHERE key='cloud_token'")['value'] or '').strip()
        payload=self._sync_payload()
        stamp=datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        local_error=None
        if folder:
            try:
                os.makedirs(folder,exist_ok=True)
                fn=os.path.join(folder,f"amuntchi_cloud_{stamp}.json")
                with open(fn,'w',encoding='utf-8') as h: json.dump(payload,h,ensure_ascii=False,indent=2,default=str)
            except Exception as e:
                local_error=str(e)
        if url:
            try:
                endpoint=url.rstrip('/') + '/sync'
                body=json.dumps(payload,ensure_ascii=False,default=str).encode('utf-8')
                headers={'Content-Type':'application/json'}
                if token: headers['Authorization']=f'Bearer {token}'
                req=urllib.request.Request(endpoint,data=body,method='POST',headers=headers)
                with urllib.request.urlopen(req,timeout=20) as resp:
                    if resp.status < 200 or resp.status >= 300: raise RuntimeError(f'HTTP {resp.status}')
            except Exception as e:
                messagebox.showerror('Cloud',f'Échec de l’envoi Cloud : {e}'); return
        if not folder and not url:
            messagebox.showwarning('Cloud','Configurez d’abord un dossier Cloud synchronisé ou une URL HTTPS.')
            return
        self.db.set_setting('last_sync_at',payload['created_at'])
        self.audit('Synchronisation Cloud - envoi')
        messagebox.showinfo('Cloud','Données envoyées vers le Cloud.' + (f'\nErreur dossier : {local_error}' if local_error else ''))

    def cloud_download(self):
        folder=(self.db.one("SELECT value FROM settings WHERE key='cloud_folder'")['value'] or '').strip()
        url=(self.db.one("SELECT value FROM settings WHERE key='cloud_url'")['value'] or '').strip()
        token=(self.db.one("SELECT value FROM settings WHERE key='cloud_token'")['value'] or '').strip()
        f=None
        if folder and os.path.isdir(folder):
            candidates=[os.path.join(folder,x) for x in os.listdir(folder) if x.startswith('amuntchi_cloud_') and x.endswith('.json')]
            if candidates: f=max(candidates,key=os.path.getmtime)
        if url:
            try:
                endpoint=url.rstrip('/') + '/sync/latest'
                headers={}
                if token: headers['Authorization']=f'Bearer {token}'
                req=urllib.request.Request(endpoint,headers=headers,method='GET')
                with urllib.request.urlopen(req,timeout=20) as resp:
                    if resp.status < 200 or resp.status >= 300: raise RuntimeError(f'HTTP {resp.status}')
                    payload=json.loads(resp.read().decode('utf-8'))
                self._apply_cloud_payload(payload,'Cloud HTTPS')
                return
            except Exception as e:
                if not f:
                    messagebox.showerror('Cloud',f'Échec de récupération Cloud : {e}'); return
        if not f:
            messagebox.showwarning('Cloud','Aucun paquet Cloud trouvé.')
            return
        try:
            with open(f,encoding='utf-8') as h: payload=json.load(h)
            self._apply_cloud_payload(payload,f)
        except Exception as e:
            messagebox.showerror('Cloud',str(e))

    def _apply_cloud_payload(self,payload,source):
        if payload.get('format')!='AMUNTCHI_SYNC_V1': raise ValueError('Paquet Cloud AMUNTCHI non reconnu.')
        if not self._conflict_confirmed(payload,source): return
        backup_path=self.db.backup_now('pre_cloud_backup')
        self.db.c.execute('BEGIN')
        try:
            for table in reversed(self._sync_tables()): self.db.c.execute(f'DELETE FROM {table}')
            for table in self._sync_tables():
                rows=payload.get('tables',{}).get(table,[])
                if not rows: continue
                cols=list(rows[0].keys()); marks=','.join('?' for _ in cols)
                self.db.c.executemany(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({marks})",[[r.get(c) for c in cols] for r in rows])
            self.db.c.commit()
        except Exception:
            self.db.c.rollback(); raise
        self.db.set_setting('last_sync_at',str(payload.get('created_at') or now()))
        self.audit('Synchronisation Cloud - récupération','',str(source))
        messagebox.showinfo('Cloud',f'Données Cloud récupérées.\nSauvegarde locale : {backup_path}')
        self.main()

    def secure_reset(self):
        if self.user.get('role')!='Administrateur':
            messagebox.showerror('Sécurité','Seul un Administrateur peut réinitialiser les données.'); return
        w=tk.Toplevel(self); w.title('Réinitialisation sécurisée'); w.geometry('560x330'); w.transient(self); w.grab_set()
        tk.Label(w,text='RÉINITIALISATION DES DONNÉES',font=('Segoe UI',16,'bold'),fg='#a51d1d').pack(pady=(24,10))
        tk.Label(w,text='Une sauvegarde automatique sera créée avant l’opération.').pack(pady=4)
        tk.Label(w,text='Mot de passe administrateur actuel :').pack(anchor='w',padx=40,pady=(18,4))
        pw=tk.Entry(w,show='•',font=('Segoe UI',12)); pw.pack(fill='x',padx=40)
        tk.Label(w,text='Tapez RESET AMUNTCHI pour confirmer :').pack(anchor='w',padx=40,pady=(14,4))
        confirm=tk.Entry(w,font=('Segoe UI',12)); confirm.pack(fill='x',padx=40)
        def do_reset():
            admin=self.db.one("SELECT password FROM users WHERE id=? AND role='Administrateur'",(self.user['id'],))
            if not admin or not verify_password(pw.get(),admin['password']):
                messagebox.showerror('Sécurité','Mot de passe administrateur incorrect.',parent=w); return
            if confirm.get().strip()!='RESET AMUNTCHI':
                messagebox.showerror('Sécurité','Texte de confirmation incorrect.',parent=w); return
            try:
                backup_path=self.db.backup_now('pre_reset_backup')
                tables=['movements','purchase_lines','purchases','sale_lines','invoices','installments','credits','payments','expenses','transfer_lines','transfers','inventory_lines','inventories','stocks','products','suppliers','clients','categories','conversions']
                self.db.c.execute('BEGIN')
                for t in tables: self.db.c.execute(f'DELETE FROM {t}')
                self.db.c.commit()
                self.audit('Réinitialisation sécurisée','', 'Données commerciales supprimées',f'Sauvegarde: {backup_path}')
                w.destroy(); messagebox.showinfo('Réinitialisation',f'Réinitialisation terminée.\nSauvegarde : {backup_path}'); self.main()
            except Exception as e:
                self.db.c.rollback(); messagebox.showerror('Réinitialisation',str(e),parent=w)
        tk.Button(w,text='ANNULER',command=w.destroy).pack(side='left',padx=40,pady=25)
        tk.Button(w,text='CONFIRMER LA RÉINITIALISATION',command=do_reset,bg='#b3261e',fg='white',relief='flat').pack(side='right',padx=40,pady=25)
    def backup(self):
        f=filedialog.asksaveasfilename(defaultextension='.db',filetypes=[('Base SQLite','*.db')],initialfile=f'amuntchi_backup_{datetime.date.today().isoformat()}.db');
        if f: shutil.copy2(DB_PATH,f); messagebox.showinfo('Sauvegarde','Sauvegarde créée avec succès')
    def users(self):
        self.header('Utilisateurs & rôles','Comptes, photos et permissions')
        bar=tk.Frame(self.body,bg='#f4f6f8'); bar.pack(fill='x',padx=20,pady=(10,8))
        tk.Button(bar,text='+ Utilisateur',command=self.user_form,bg='#173a5e',fg='white',relief='flat',font=('Segoe UI',10,'bold'),padx=12,pady=6).pack(side='right',padx=5)
        tk.Button(bar,text='Supprimer l’utilisateur',command=self.delete_user,bg='#c62828',fg='white',relief='flat',font=('Segoe UI',10,'bold'),padx=12,pady=6).pack(side='right',padx=5)
        tk.Button(bar,text='Renommer / modifier',command=self.edit_user,bg='#6a1b9a',fg='white',relief='flat',font=('Segoe UI',10,'bold'),padx=12,pady=6).pack(side='right',padx=5)
        tk.Button(bar,text='Modifier le statut',command=self.change_user_status,bg='#2e7d32',fg='white',relief='flat',font=('Segoe UI',10,'bold'),padx=12,pady=6).pack(side='right',padx=5)
        tk.Button(bar,text='Réinitialiser le mot de passe',command=self.reset_user_password,bg='#1769d3',fg='white',relief='flat',font=('Segoe UI',10,'bold'),padx=12,pady=6).pack(side='right',padx=5)
        tk.Label(self.body,text='Double-cliquez sur un utilisateur pour le renommer, changer son rôle ou son statut. '
                 'Seul l’administrateur accède à toutes les fonctionnalités.',
                 bg='#f4f6f8',fg='#53677e',font=('Segoe UI',9),anchor='w',justify='left').pack(fill='x',padx=22,pady=(0,6))
        fr=tk.Frame(self.body,bg='white'); fr.pack(fill='both',expand=True,padx=20,pady=5)
        cols=('id','name','username','role','active','date')
        tv=ttk.Treeview(fr,columns=cols,show='headings',selectmode='browse')
        for c,l,w in [('id','ID',60),('name','Nom',250),('username','Identifiant',180),('role','Rôle',180),('active','Statut',110),('date','Créé le',180)]:
            tv.heading(c,text=l,anchor='center'); tv.column(c,width=w,anchor='center')
        tv.pack(side='left',fill='both',expand=True)
        ys=ttk.Scrollbar(fr,orient='vertical',command=tv.yview); ys.pack(side='right',fill='y'); tv.configure(yscrollcommand=ys.set)
        def load():
            for x in tv.get_children(): tv.delete(x)
            rows=self.db.rows('SELECT id,name,username,role,active,created_at FROM users ORDER BY name')
            for r in rows:
                tv.insert('', 'end',iid=str(r['id']),values=(r['id'],r['name'],r['username'],r['role'],'Actif' if r['active'] else 'Inactif',r['created_at']))
        load()
        tv.bind('<Double-1>',lambda e:self.edit_user(tv))
        self._users_tree=tv

    def change_user_status(self, tree=None):
        tree = tree or getattr(self,'_users_tree',None)
        if tree is None: return
        sel=tree.selection()
        if not sel:
            messagebox.showwarning('Utilisateurs','Sélectionnez d’abord un utilisateur.',parent=self); return
        uid=int(sel[0])
        user=self.db.one('SELECT id,name,username,role,active FROM users WHERE id=?',(uid,))
        if not user: return
        # Éviter de désactiver le dernier administrateur actif.
        if user['active'] and user['role']=='Administrateur':
            n=self.db.one("SELECT COUNT(*) n FROM users WHERE role='Administrateur' AND active=1")['n']
            if n <= 1:
                messagebox.showwarning('Statut','Impossible de désactiver le dernier administrateur actif.',parent=self); return
        new_active=0 if user['active'] else 1
        action='Activation utilisateur' if new_active else 'Désactivation utilisateur'
        lib='Actif' if new_active else 'Inactif'
        if not messagebox.askyesno('Modifier le statut',f'Voulez-vous rendre le compte « {user["name"]} » {lib.lower()} ?',parent=self): return
        try:
            self.db.q('UPDATE users SET active=? WHERE id=?',(new_active,uid))
            self.audit(action,user['username'],f'Statut: {lib}')
            self.users()
            messagebox.showinfo('Statut',f'Le compte « {user["name"]} » est maintenant {lib}.',parent=self)
        except Exception as e:
            messagebox.showerror('Statut',str(e),parent=self)

    def _selected_user(self, tree=None):
        """Renvoie l'utilisateur selectionne dans la liste des comptes."""
        tree = tree if hasattr(tree,'selection') else getattr(self,'_users_tree',None)
        if tree is None: return None
        sel=tree.selection()
        if not sel:
            messagebox.showwarning('Utilisateurs','Sélectionnez d’abord un utilisateur dans la liste.',parent=self); return None
        return self.db.one('SELECT * FROM users WHERE id=?',(int(sel[0]),))

    def edit_user(self, tree=None):
        """Renommer un compte (nom affiché), changer son identifiant, son rôle et son statut."""
        u=self._selected_user(tree)
        if not u: return
        w=tk.Toplevel(self); w.title('Modifier le compte'); w.geometry('560x520'); w.resizable(False,False)
        w.configure(bg='#f4f7fb'); w.transient(self); w.grab_set(); w.update_idletasks()
        sw,sh=w.winfo_screenwidth(),w.winfo_screenheight()
        w.geometry(f'560x520+{max(0,(sw-560)//2)}+{max(0,(sh-520)//2)}')
        tk.Label(w,text='Modifier le compte',font=('Segoe UI',18,'bold'),fg='#173a5e',bg='#f4f7fb').pack(pady=(18,4))
        tk.Label(w,text=f'Compte n° {u["id"]} — créé le {u["created_at"]}',bg='#f4f7fb',fg='#6b7b8c',font=('Segoe UI',9)).pack()
        form=tk.Frame(w,bg='#f4f7fb'); form.pack(fill='x',padx=45,pady=(14,0))
        name=tk.StringVar(value=u['name']); ident=tk.StringVar(value=u['username'])
        role=tk.StringVar(value=u['role']); active=tk.BooleanVar(value=bool(u['active']))
        for label,var in [('Nom affiché (ex. : Salifou Yahaya)',name),('Identifiant de connexion',ident)]:
            tk.Label(form,text=label,font=('Segoe UI',10,'bold'),bg='#f4f7fb',fg='#173a5e').pack(anchor='w',pady=(10,2))
            tk.Entry(form,textvariable=var,font=('Segoe UI',11)).pack(fill='x',ipady=5)
        tk.Label(form,text='Rôle',font=('Segoe UI',10,'bold'),bg='#f4f7fb',fg='#173a5e').pack(anchor='w',pady=(12,2))
        ttk.Combobox(form,textvariable=role,values=ROLES,state='readonly',font=('Segoe UI',11)).pack(fill='x',ipady=3)
        tk.Label(form,text='Administrateur : toutes les fonctionnalités.   Vendeur : Produits (boutique), '
                 'Ventes / caisse et Clients & crédits.   Utilisateur : Ventes / caisse et Clients & crédits.',
                 bg='#f4f7fb',fg='#6b7b8c',font=('Segoe UI',9),wraplength=440,justify='left').pack(anchor='w',pady=(4,0))
        tk.Checkbutton(form,text='Compte actif',variable=active,bg='#f4f7fb',fg='#173a5e',
                       activebackground='#f4f7fb',selectcolor='white').pack(anchor='w',pady=(12,4))
        def save():
            nom=name.get().strip(); ids=ident.get().strip()
            if not nom: messagebox.showwarning('Compte','Veuillez saisir le nom affiché.',parent=w); return
            if not ids: messagebox.showwarning('Compte','Veuillez saisir l’identifiant de connexion.',parent=w); return
            autre=self.db.one('SELECT id FROM users WHERE lower(username)=lower(?) AND id<>?',(ids,u['id']))
            if autre: messagebox.showerror('Identifiant déjà utilisé',f'L’identifiant « {ids} » est déjà pris.',parent=w); return
            act=1 if active.get() else 0
            if u['role']=='Administrateur' and (role.get()!='Administrateur' or act==0):
                n=self.db.one("SELECT COUNT(*) n FROM users WHERE role='Administrateur' AND active=1 AND id<>?",(u['id'],))['n']
                if n<1:
                    messagebox.showwarning('Compte','Impossible : ce compte est le dernier administrateur actif.',parent=w); return
            changements=[]
            if nom!=u['name']: changements.append(f"Nom affiché : {u['name']} -> {nom}")
            if ids!=u['username']: changements.append(f"Identifiant : {u['username']} -> {ids}")
            if role.get()!=u['role']: changements.append(f"Rôle : {u['role']} -> {role.get()}")
            if act!=int(u['active']): changements.append('Statut : ' + ('Actif' if act else 'Inactif'))
            if not changements:
                messagebox.showinfo('Compte','Aucune modification à enregistrer.',parent=w); return
            if not messagebox.askyesno('Valider les modifications',
                'Voulez-vous enregistrer les modifications suivantes ?\n\n- ' + '\n- '.join(changements),parent=w): return
            try:
                self.db.q('UPDATE users SET name=?,username=?,role=?,active=? WHERE id=?',(nom,ids,role.get(),act,u['id']))
                self.audit('Modification utilisateur',f"{u['name']} / {u['username']} / {u['role']}",f"{nom} / {ids} / {role.get()}")
                if self.user and self.user['id']==u['id']:
                    self.user=dict(self.db.one('SELECT * FROM users WHERE id=?',(u['id'],)))
                    w.destroy(); self.main(); return
                w.destroy(); self.users()
                messagebox.showinfo('Compte','Modifications enregistrées :\n\n- ' + '\n- '.join(changements))
            except Exception as e:
                messagebox.showerror('Compte',str(e),parent=w)
        b=tk.Frame(w,bg='#f4f7fb'); b.pack(side='bottom',fill='x',padx=45,pady=(12,20))
        tk.Button(b,text='ANNULER',command=w.destroy,bg='#777777',fg='white',relief='flat',font=('Segoe UI',10,'bold'),pady=11).pack(side='left',fill='x',expand=True,padx=(0,6))
        tk.Button(b,text='VALIDER ET ENREGISTRER',command=save,bg='#2e7d32',fg='white',relief='flat',font=('Segoe UI',10,'bold'),pady=11).pack(side='left',fill='x',expand=True,padx=(6,0))
        w.bind('<Return>',lambda e:save())

    def delete_user(self, tree=None):
        """Suppression definitive d'un compte, l'historique des operations etant conserve."""
        u=self._selected_user(tree)
        if not u: return
        if self.user and self.user['id']==u['id']:
            messagebox.showwarning('Suppression','Vous ne pouvez pas supprimer le compte avec lequel vous êtes connecté.',parent=self); return
        if u['role']=='Administrateur':
            n=self.db.one("SELECT COUNT(*) n FROM users WHERE role='Administrateur' AND id<>?",(u['id'],))['n']
            if n<1:
                messagebox.showwarning('Suppression','Impossible de supprimer le dernier administrateur.',parent=self); return
        if not messagebox.askyesno('Supprimer définitivement',
            f'Supprimer définitivement le compte « {u["name"]} » (identifiant {u["username"]}) ?\n\n'
            'Le compte ne pourra plus se connecter. Les ventes, achats, paiements et autres\n'
            'opérations déjà enregistrés sont conservés dans l’historique.',parent=self,icon='warning'): return
        try:
            for tbl in USER_LINKED_TABLES:
                try: self.db.q(f'UPDATE {tbl} SET user_id=NULL WHERE user_id=?',(u['id'],))
                except Exception: pass
            self.db.q('DELETE FROM users WHERE id=?',(u['id'],))
            self.audit('Suppression utilisateur',f"{u['name']} / {u['username']} / {u['role']}",'Compte supprimé')
            self.users()
            messagebox.showinfo('Suppression',f'Le compte « {u["name"]} » a été supprimé définitivement.')
        except Exception as e:
            messagebox.showerror('Suppression',str(e),parent=self)

    def user_form(self):
        w=tk.Toplevel(self)
        w.title('Nouvel utilisateur')
        w.geometry('560x780')
        w.resizable(False,False)
        w.configure(bg='#f4f7fb')
        w.transient(self); w.grab_set()
        w.update_idletasks()
        sw,sh=w.winfo_screenwidth(),w.winfo_screenheight()
        x=max(0,(sw-560)//2); y=max(0,(sh-780)//2)
        w.geometry(f'560x780+{x}+{y}')

        tk.Label(w,text='Créer un utilisateur',font=('Segoe UI',18,'bold'),fg='#173a5e',bg='#f4f7fb').pack(pady=(18,12))
        form=tk.Frame(w,bg='#f4f7fb'); form.pack(fill='x',padx=45)
        name=tk.StringVar(); username=tk.StringVar(); password=tk.StringVar(); confirm=tk.StringVar(); photo=tk.StringVar(); role=tk.StringVar(value='Vendeur'); active=tk.BooleanVar(value=True)
        fields=[('Nom complet',name,False),('Identifiant',username,False),('Mot de passe',password,True),('Confirmer le mot de passe',confirm,True)]
        for label,var,secret in fields:
            tk.Label(form,text=label,font=('Segoe UI',10,'bold'),bg='#f4f7fb',fg='#173a5e').pack(anchor='w',pady=(7,2))
            tk.Entry(form,textvariable=var,show='•' if secret else '',font=('Segoe UI',11)).pack(fill='x',ipady=5)
        tk.Label(form,text='Rôle',font=('Segoe UI',10,'bold'),bg='#f4f7fb',fg='#173a5e').pack(anchor='w',pady=(10,2))
        ttk.Combobox(form,textvariable=role,values=ROLES,state='readonly',font=('Segoe UI',11)).pack(fill='x',ipady=3)
        tk.Label(form,text='Photo (optionnelle)',font=('Segoe UI',10,'bold'),bg='#f4f7fb',fg='#173a5e').pack(anchor='w',pady=(10,2))
        photo_row=tk.Frame(form,bg='#f4f7fb'); photo_row.pack(fill='x')
        tk.Entry(photo_row,textvariable=photo,font=('Segoe UI',10)).pack(side='left',fill='x',expand=True,ipady=5)
        tk.Button(photo_row,text='Choisir...',command=lambda: photo.set(filedialog.askopenfilename(filetypes=[('Images','*.jpg *.jpeg *.png *.webp')]) or photo.get())).pack(side='left',padx=(8,0),ipady=4)
        tk.Checkbutton(form,text='Compte actif',variable=active,bg='#f4f7fb',fg='#173a5e',activebackground='#f4f7fb',selectcolor='white').pack(anchor='w',pady=(10,4))

        def save():
            nom=name.get().strip(); ident=username.get().strip(); pwd=password.get(); pwd2=confirm.get()
            if not nom: messagebox.showwarning('Utilisateur','Veuillez saisir le nom complet.',parent=w); return
            if not ident: messagebox.showwarning('Utilisateur','Veuillez saisir un identifiant.',parent=w); return
            if not pwd: messagebox.showwarning('Utilisateur','Veuillez saisir un mot de passe.',parent=w); return
            if pwd != pwd2: messagebox.showerror('Utilisateur','Les deux mots de passe ne correspondent pas.',parent=w); return
            faible=password_strength_error(pwd)
            if faible: messagebox.showwarning('Mot de passe',faible,parent=w); return
            existing=self.db.one('SELECT id,name FROM users WHERE lower(username)=lower(?)',(ident,))
            if existing:
                messagebox.showerror('Identifiant déjà utilisé',f'L’identifiant « {ident} » est déjà utilisé.\nVeuillez choisir un autre identifiant.',parent=w); return
            try:
                self.db.q('INSERT INTO users(name,username,password,role,photo,active,created_at) VALUES(?,?,?,?,?,?,?)',(nom,ident,hash_password(pwd),role.get(),photo.get().strip(),1 if active.get() else 0,now()))
                self.audit('Création utilisateur','',ident)
                w.destroy()
                self.users()
                messagebox.showinfo('Utilisateur',f'Le compte « {nom} » a été créé avec succès.\nRôle : {role.get()}')
            except sqlite3.IntegrityError as e:
                if 'users.username' in str(e): messagebox.showerror('Identifiant déjà utilisé',f'L’identifiant « {ident} » existe déjà.\nVeuillez en choisir un autre.',parent=w)
                else: messagebox.showerror('Utilisateur',str(e),parent=w)
            except Exception as e: messagebox.showerror('Utilisateur',str(e),parent=w)
        buttons=tk.Frame(w,bg='#f4f7fb'); buttons.pack(fill='x',padx=45,pady=(12,20))
        tk.Button(buttons,text='ANNULER',command=w.destroy,bg='#777777',fg='white',relief='flat',font=('Segoe UI',10,'bold'),pady=11).pack(side='left',fill='x',expand=True,padx=(0,6))
        tk.Button(buttons,text='ENREGISTRER',command=save,bg='#173a5e',fg='white',relief='flat',font=('Segoe UI',10,'bold'),pady=11).pack(side='left',fill='x',expand=True,padx=(6,0))
        w.bind('<Return>',lambda e:save())
    def audit_view(self):
        self.header('Journal d’audit','Traçabilité des opérations sensibles'); rows=self.db.rows('SELECT a.id,u.name,a.action,a.date,a.old_value,a.new_value,a.reason FROM audit a LEFT JOIN users u ON u.id=a.user_id ORDER BY a.date DESC,a.id DESC LIMIT 500'); self.table(self.body,[('id','ID',50),('user','Utilisateur',160),('action','Action',220),('date','Date/heure',160),('old','Ancienne valeur',150),('new','Nouvelle valeur',180),('reason','Motif',180)],[(r['id'],r['name'],r['action'],r['date'],r['old_value'],r['new_value'],r['reason']) for r in rows])

def _selftest():
    """Auto-test de fabrication : ouvre réellement chaque écran puis quitte.
    Résultat écrit dans selftest.log (dossier des données) ; code de sortie 0 = succès."""
    import traceback
    log=[]; ok=True
    for name in ('showinfo','showwarning','showerror'): setattr(messagebox,name,lambda *a,**k: None)
    messagebox.askyesno=lambda *a,**k: False
    try:
        app=App(); app.update()
        app.user=dict(app.db.one("SELECT * FROM users WHERE username='admin'")); app.main(); app.update()
        for s in ['dashboard','products','stock_view','transfers','purchases','sales','clients','expenses','inventory','reports','settings','users','audit_view']:
            try: app.show(s); app.update(); log.append('OK  '+s)
            except Exception: ok=False; log.append('ERR '+s+'\n'+traceback.format_exc())
        for f in ('product_form','transfer_form','purchase_form','inventory_form','payment_form','client_form','expense_form','user_form','change_own_password'):
            try:
                getattr(app,f)(); app.update(); log.append('OK  '+f)
            except Exception: ok=False; log.append('ERR '+f+'\n'+traceback.format_exc())
            for w in app.winfo_children():
                if isinstance(w,tk.Toplevel): w.destroy()
        try: app.destroy()
        except Exception: pass
    except Exception:
        ok=False; log.append('ERR démarrage\n'+traceback.format_exc())
    log.append('RESULTAT: '+('SUCCES' if ok else 'ECHEC'))
    (BASE/'selftest.log').write_text('\n'.join(log),encoding='utf-8')
    os._exit(0 if ok else 1)

if __name__=='__main__':
    if '--selftest' in sys.argv: _selftest()
    App().mainloop()
