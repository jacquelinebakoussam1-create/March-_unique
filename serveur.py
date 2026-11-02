#!/usr/bin/env python3
"""Serveur du Marché Unique : API JSON + base SQLite.
Aucune installation nécessaire : placez ce fichier à côté de marche_unique.sql
puis lancez  python3 serveur.py
"""
import json, os, re, secrets, sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

DOSSIER = os.path.dirname(os.path.abspath(__file__))
BASE = os.environ.get("BASE", os.path.join(DOSSIER, "marche_unique.db"))
PORT = int(os.environ.get("PORT", 8000))
HOST = os.environ.get("HOST", "127.0.0.1")  # "0.0.0.0" pour être joignable depuis Internet
CLE_DEFINIE = os.environ.get("CLE_ADMIN")
CLE = CLE_DEFINIE or secrets.token_hex(8)
MOYENS = {"t_money", "flooz", "carte", "livraison"}


def db():
    c = sqlite3.connect(BASE)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    return c


def init():
    if not os.path.exists(BASE):
        os.makedirs(os.path.dirname(BASE), exist_ok=True)
        c = sqlite3.connect(BASE)
        with open(os.path.join(DOSSIER, "marche_unique.sql"), encoding="utf-8") as f:
            sql = f.read()
        if os.environ.get("EXEMPLES", "1") == "0":  # en ligne : base vide, sans boutiques d'exemple
            sql = sql.split("-- ---------- Données d'exemple")[0]
        c.executescript(sql)
        c.commit()
        c.close()


class Erreur(Exception):
    def __init__(self, code, msg):
        self.code, self.msg = code, msg


# ---------- Validation des données reçues ----------
def texte(d, k, longueur=100):
    v = str(d.get(k, "")).strip()
    if not v or len(v) > longueur:
        raise Erreur(400, f"Champ « {k} » manquant ou trop long.")
    return v


def entier(d, k, mini=1):
    v = d.get(k)
    if isinstance(v, bool) or not isinstance(v, int) or v < mini:
        raise Erreur(400, f"Champ « {k} » invalide.")
    return v


def tel(d, k):
    v = texte(d, k, 20)
    if len(re.sub(r"\D", "", v)) < 8:
        raise Erreur(400, "Numéro de téléphone invalide (8 chiffres minimum).")
    return v


def client(c, d):
    t, n = tel(d, "telephone"), texte(d, "client")
    r = c.execute("SELECT id FROM clients WHERE telephone=?", (t,)).fetchone()
    if r:
        return r["id"]
    return c.execute("INSERT INTO clients(nom, telephone) VALUES (?,?)", (n, t)).lastrowid


# ---------- Routes publiques ----------
def liste_produits(c, q, d, *_):
    mot = "%" + q.get("q", [""])[0].lower() + "%"
    cat = q.get("categorie", [""])[0]
    rows = c.execute(
        """SELECT p.id, p.nom, p.prix_fcfa, p.categorie, p.stock,
                  b.id AS boutique_id, b.nom AS boutique
           FROM produits p JOIN boutiques b ON b.id = p.boutique_id
           WHERE p.stock > 0 AND lower(p.nom) LIKE ? AND (?='' OR p.categorie=?)
           ORDER BY p.id""", (mot, cat, cat))
    return 200, [dict(r) for r in rows]


def liste_boutiques(c, q, d, *_):
    rows = c.execute("SELECT id, nom, categorie, partage_clients FROM boutiques ORDER BY id")
    return 200, [dict(r) for r in rows]


def creer_boutique(c, q, d, *_):
    cur = c.execute(
        "INSERT INTO boutiques(nom, responsable, telephone_paiement, categorie, partage_clients)"
        " VALUES (?,?,?,?,?)",
        (texte(d, "nom"), texte(d, "responsable"), tel(d, "telephone"),
         texte(d, "categorie"), 0 if d.get("partage_clients") is False else 1))
    return 201, {"id": cur.lastrowid}


def creer_recommandation(c, q, d, *_):
    src, cib = entier(d, "boutique_source_id"), entier(d, "boutique_cible_id")
    s = c.execute("SELECT partage_clients FROM boutiques WHERE id=?", (src,)).fetchone()
    b = c.execute("SELECT partage_clients FROM boutiques WHERE id=?", (cib,)).fetchone()
    if not s or not b:
        raise Erreur(404, "Boutique inconnue.")
    if not (s["partage_clients"] and b["partage_clients"]):
        raise Erreur(403, "Une des deux boutiques a désactivé le partage de clients.")
    cur = c.execute(
        "INSERT INTO recommandations(client_id, boutique_source_id, boutique_cible_id,"
        " produit_recherche) VALUES (?,?,?,?)",
        (client(c, d), src, cib, texte(d, "produit_recherche")))
    return 201, {"id": cur.lastrowid}


def creer_commande(c, q, d, *_):
    moyen = d.get("moyen")
    if moyen not in MOYENS:
        raise Erreur(400, "Moyen de paiement inconnu.")
    lignes = d.get("lignes")
    if not isinstance(lignes, list) or not lignes:
        raise Erreur(400, "Le panier est vide.")
    cl, total, detail = client(c, d), 0, []
    for l in lignes:
        pid, qte = entier(l, "produit_id"), entier(l, "quantite")
        # Le prix vient toujours de la base, jamais du navigateur.
        p = c.execute("SELECT id, boutique_id, prix_fcfa, stock, nom FROM produits WHERE id=?",
                      (pid,)).fetchone()
        if not p:
            raise Erreur(404, f"Produit {pid} inconnu.")
        if p["stock"] < qte:
            raise Erreur(409, f"Stock insuffisant pour « {p['nom']} ».")
        total += p["prix_fcfa"] * qte
        detail.append((p, qte))
    cmd = c.execute("INSERT INTO commandes(client_id, total_fcfa) VALUES (?,?)", (cl, total)).lastrowid
    for p, qte in detail:
        c.execute("INSERT INTO lignes_commande(commande_id, produit_id, boutique_id, quantite,"
                  " prix_unitaire_fcfa) VALUES (?,?,?,?,?)",
                  (cmd, p["id"], p["boutique_id"], qte, p["prix_fcfa"]))
    ref = "MU-" + secrets.token_hex(4).upper()
    c.execute("INSERT INTO paiements(commande_id, moyen, reference_agregateur) VALUES (?,?,?)",
              (cmd, moyen, ref))
    # Rattache la commande à la dernière recommandation ouverte vers une boutique du panier.
    r = c.execute("SELECT id FROM recommandations WHERE client_id=? AND statut IN ('envoyee','contactee')"
                  " AND boutique_cible_id IN (SELECT boutique_id FROM lignes_commande WHERE commande_id=?)"
                  " ORDER BY id DESC LIMIT 1", (cl, cmd)).fetchone()
    if r:
        c.execute("UPDATE recommandations SET commande_id=?, statut='vendue' WHERE id=?", (cmd, r["id"]))
    return 201, {"commande_id": cmd, "total_fcfa": total, "reference": ref}


# ---------- Routes protégées (clé d'accès requise) ----------
def creer_produit(c, q, d, *_):
    cur = c.execute(
        "INSERT INTO produits(boutique_id, nom, prix_fcfa, categorie, stock) VALUES (?,?,?,?,?)",
        (entier(d, "boutique_id"), texte(d, "nom"), entier(d, "prix_fcfa"),
         texte(d, "categorie"), entier(d, "stock", 0)))
    return 201, {"id": cur.lastrowid}


def confirmer_paiement(c, q, d, *_):
    """À brancher plus tard sur le webhook de l'agrégateur (T-Money, Flooz...)."""
    p = c.execute("SELECT id, commande_id, statut FROM paiements WHERE reference_agregateur=?",
                  (texte(d, "reference"),)).fetchone()
    if not p:
        raise Erreur(404, "Référence inconnue.")
    if p["statut"] != "en_attente":
        raise Erreur(409, "Paiement déjà traité.")
    cmd = p["commande_id"]
    for l in c.execute("SELECT produit_id, quantite FROM lignes_commande WHERE commande_id=?", (cmd,)):
        ok = c.execute("UPDATE produits SET stock = stock - ? WHERE id=? AND stock >= ?",
                       (l["quantite"], l["produit_id"], l["quantite"])).rowcount
        if not ok:
            raise Erreur(409, "Stock épuisé : paiement à rembourser.")
    c.execute("UPDATE paiements SET statut='reussi' WHERE id=?", (p["id"],))
    c.execute("UPDATE commandes SET statut='payee' WHERE id=?", (cmd,))
    groupes = c.execute("SELECT boutique_id, SUM(quantite * prix_unitaire_fcfa) AS brut"
                        " FROM lignes_commande WHERE commande_id=? GROUP BY boutique_id", (cmd,)).fetchall()
    for g in groupes:
        r = c.execute("SELECT taux_commission FROM recommandations"
                      " WHERE commande_id=? AND boutique_cible_id=?", (cmd, g["boutique_id"])).fetchone()
        com = g["brut"] * r["taux_commission"] // 100 if r else 0
        c.execute("INSERT INTO reversements(boutique_id, commande_id, montant_brut, commission_fcfa,"
                  " montant_net) VALUES (?,?,?,?,?)", (g["boutique_id"], cmd, g["brut"], com, g["brut"] - com))
    return 200, {"statut": "payee"}


def liste_recommandations(c, q, d, bid):
    rows = c.execute(
        """SELECT r.id, cl.nom AS client, cl.telephone, r.produit_recherche, r.statut,
                  b.nom AS envoye_par
           FROM recommandations r JOIN clients cl ON cl.id = r.client_id
           JOIN boutiques b ON b.id = r.boutique_source_id
           WHERE r.boutique_cible_id=? ORDER BY r.id DESC""", (bid,))
    return 200, [dict(r) for r in rows]


def liste_reversements(c, q, d, bid):
    rows = c.execute("SELECT * FROM reversements WHERE boutique_id=? ORDER BY id DESC", (bid,))
    return 200, [dict(r) for r in rows]


def liste_commissions(c, q, d, *_):
    return 200, [dict(r) for r in c.execute("SELECT * FROM v_commissions_dues")]


ROUTES = [
    ("GET",  r"/api/produits", liste_produits, False),
    ("GET",  r"/api/boutiques", liste_boutiques, False),
    ("POST", r"/api/boutiques", creer_boutique, False),
    ("POST", r"/api/recommandations", creer_recommandation, False),
    ("POST", r"/api/commandes", creer_commande, False),
    ("POST", r"/api/produits", creer_produit, True),
    ("POST", r"/api/paiements/confirmation", confirmer_paiement, True),
    ("GET",  r"/api/boutiques/(\d+)/recommandations", liste_recommandations, True),
    ("GET",  r"/api/boutiques/(\d+)/reversements", liste_reversements, True),
    ("GET",  r"/api/commissions", liste_commissions, True),
]


class Serveur(BaseHTTPRequestHandler):
    timeout = 15  # coupe les connexions qui traînent

    def repondre(self, code, obj):
        corps = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corps)))
        self.end_headers()
        self.wfile.write(corps)

    def traiter(self, methode):
        u, c = urlparse(self.path), None
        try:
            for m, motif, fonction, protege in ROUTES:
                g = re.fullmatch(motif, u.path)
                if m != methode or not g:
                    continue
                if protege and not secrets.compare_digest(
                        self.headers.get("X-Cle", "").encode(), CLE.encode()):
                    raise Erreur(401, "Clé d’accès manquante ou fausse.")
                d = {}
                if methode == "POST":
                    n = int(self.headers.get("Content-Length", 0))
                    if n > 100_000:
                        raise Erreur(413, "Requête trop grande.")
                    d = json.loads(self.rfile.read(n) or b"{}")
                    if not isinstance(d, dict):
                        raise Erreur(400, "JSON invalide.")
                c = db()
                code, rep = fonction(c, parse_qs(u.query), d, *g.groups())
                c.commit()
                return self.repondre(code, rep)
            raise Erreur(404, "Route inconnue.")
        except Erreur as e:
            c and c.rollback()
            self.repondre(e.code, {"erreur": e.msg})
        except (json.JSONDecodeError, ValueError):
            self.repondre(400, {"erreur": "JSON invalide."})
        except sqlite3.IntegrityError:
            c and c.rollback()
            self.repondre(409, {"erreur": "Donnée refusée (doublon ou référence inconnue)."})
        finally:
            c and c.close()

    def do_GET(self):
        if urlparse(self.path).path in ("/", "/index.html"):
            try:
                with open(os.path.join(DOSSIER, "index.html"), "rb") as f:
                    page = f.read()
            except OSError:
                return self.repondre(404, {"erreur": "index.html introuvable."})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)
            return
        self.traiter("GET")

    def do_POST(self):
        self.traiter("POST")

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    if HOST != "127.0.0.1" and len(CLE_DEFINIE or "") < 12:
        raise SystemExit("En ligne, définissez CLE_ADMIN : une clé secrète d’au moins 12 caractères.")
    init()
    print(f"Marché Unique : ouvrez http://127.0.0.1:{PORT}/ dans votre navigateur")
    if not CLE_DEFINIE:
        print(f"Clé d’accès (en-tête X-Cle) pour cette session : {CLE}")
    ThreadingHTTPServer((HOST, PORT), Serveur).serve_forever()
