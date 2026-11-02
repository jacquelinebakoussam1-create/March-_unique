-- Marché Unique : base de données (SQLite)
-- Les montants sont en FCFA, stockés en entiers (pas de centimes).

PRAGMA foreign_keys = ON;

CREATE TABLE boutiques (
  id                 INTEGER PRIMARY KEY,
  nom                TEXT NOT NULL UNIQUE,
  responsable        TEXT NOT NULL,
  telephone_paiement TEXT NOT NULL,
  categorie          TEXT NOT NULL,
  partage_clients    INTEGER NOT NULL DEFAULT 1 CHECK (partage_clients IN (0, 1)),
  cree_le            TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE produits (
  id          INTEGER PRIMARY KEY,
  boutique_id INTEGER NOT NULL REFERENCES boutiques(id),
  nom         TEXT NOT NULL,
  prix_fcfa   INTEGER NOT NULL CHECK (prix_fcfa > 0),
  categorie   TEXT NOT NULL,
  stock       INTEGER NOT NULL DEFAULT 0 CHECK (stock >= 0)
);

CREATE TABLE clients (
  id        INTEGER PRIMARY KEY,
  nom       TEXT NOT NULL,
  telephone TEXT NOT NULL UNIQUE
);

CREATE TABLE commandes (
  id         INTEGER PRIMARY KEY,
  client_id  INTEGER NOT NULL REFERENCES clients(id),
  total_fcfa INTEGER NOT NULL CHECK (total_fcfa >= 0),
  statut     TEXT NOT NULL DEFAULT 'en_attente'
             CHECK (statut IN ('en_attente', 'payee', 'livree', 'annulee')),
  cree_le    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Une commande peut contenir des produits de plusieurs boutiques.
-- Le prix est copié ici pour que l'historique ne change pas si le prix du produit change.
CREATE TABLE lignes_commande (
  id                   INTEGER PRIMARY KEY,
  commande_id          INTEGER NOT NULL REFERENCES commandes(id),
  produit_id           INTEGER NOT NULL REFERENCES produits(id),
  boutique_id          INTEGER NOT NULL REFERENCES boutiques(id),
  quantite             INTEGER NOT NULL CHECK (quantite > 0),
  prix_unitaire_fcfa   INTEGER NOT NULL CHECK (prix_unitaire_fcfa > 0)
);

CREATE TABLE paiements (
  id                    INTEGER PRIMARY KEY,
  commande_id           INTEGER NOT NULL REFERENCES commandes(id),
  moyen                 TEXT NOT NULL
                        CHECK (moyen IN ('t_money', 'flooz', 'carte', 'livraison')),
  reference_agregateur  TEXT,
  statut                TEXT NOT NULL DEFAULT 'en_attente'
                        CHECK (statut IN ('en_attente', 'reussi', 'echoue', 'rembourse')),
  cree_le               TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Partage de clients : la boutique source envoie un client vers la boutique cible.
CREATE TABLE recommandations (
  id                  INTEGER PRIMARY KEY,
  client_id           INTEGER NOT NULL REFERENCES clients(id),
  boutique_source_id  INTEGER NOT NULL REFERENCES boutiques(id),
  boutique_cible_id   INTEGER NOT NULL REFERENCES boutiques(id),
  produit_recherche   TEXT NOT NULL,
  statut              TEXT NOT NULL DEFAULT 'envoyee'
                      CHECK (statut IN ('envoyee', 'contactee', 'vendue', 'perdue')),
  commande_id         INTEGER REFERENCES commandes(id),
  taux_commission     INTEGER NOT NULL DEFAULT 5 CHECK (taux_commission BETWEEN 0 AND 100),
  cree_le             TEXT NOT NULL DEFAULT (datetime('now')),
  CHECK (boutique_source_id <> boutique_cible_id)
);

-- Argent à verser à chaque boutique pour une commande payée.
CREATE TABLE reversements (
  id               INTEGER PRIMARY KEY,
  boutique_id      INTEGER NOT NULL REFERENCES boutiques(id),
  commande_id      INTEGER NOT NULL REFERENCES commandes(id),
  montant_brut     INTEGER NOT NULL CHECK (montant_brut >= 0),
  commission_fcfa  INTEGER NOT NULL DEFAULT 0 CHECK (commission_fcfa >= 0),
  montant_net      INTEGER NOT NULL CHECK (montant_net >= 0),
  statut           TEXT NOT NULL DEFAULT 'a_payer' CHECK (statut IN ('a_payer', 'paye')),
  paye_le          TEXT,
  UNIQUE (boutique_id, commande_id)
);

CREATE INDEX idx_produits_boutique ON produits(boutique_id);
CREATE INDEX idx_lignes_commande   ON lignes_commande(commande_id);
CREATE INDEX idx_reco_cible        ON recommandations(boutique_cible_id, statut);

-- Commissions de partage de clients : ce que la boutique qui reçoit doit
-- à la boutique qui a envoyé le client, pour chaque vente conclue.
CREATE VIEW v_commissions_dues AS
SELECT r.id                    AS recommandation_id,
       r.boutique_source_id    AS boutique_a_payer,
       r.boutique_cible_id     AS boutique_debitrice,
       r.commande_id,
       SUM(l.quantite * l.prix_unitaire_fcfa)                        AS ventes_fcfa,
       SUM(l.quantite * l.prix_unitaire_fcfa) * r.taux_commission / 100 AS commission_fcfa
FROM recommandations r
JOIN lignes_commande l ON l.commande_id = r.commande_id
                      AND l.boutique_id = r.boutique_cible_id
JOIN commandes c       ON c.id = r.commande_id AND c.statut IN ('payee', 'livree')
WHERE r.statut = 'vendue'
GROUP BY r.id;

-- ---------- Données d'exemple ----------
INSERT INTO boutiques (id, nom, responsable, telephone_paiement, categorie) VALUES
 (1, 'Tech Express',  'Kossi A.',  '90000001', 'Téléphones'),
 (2, 'Mode & Style',  'Ama T.',    '90000002', 'Mode'),
 (3, 'Maison Claire', 'Yawo K.',   '90000003', 'Maison'),
 (4, 'Belle Peau',    'Esi M.',    '90000004', 'Beauté');

INSERT INTO produits (id, boutique_id, nom, prix_fcfa, categorie, stock) VALUES
 (1, 1, 'Smartphone 128 Go', 95000, 'Téléphones', 12),
 (2, 2, 'Robe en wax',       22000, 'Mode',        8),
 (3, 3, 'Ventilateur',       21000, 'Maison',      5);

INSERT INTO clients (id, nom, telephone) VALUES (1, 'Awa K.', '90111111');

-- Awa, envoyée par Mode & Style, achète un smartphone chez Tech Express.
INSERT INTO commandes (id, client_id, total_fcfa, statut) VALUES (1, 1, 95000, 'payee');
INSERT INTO lignes_commande (commande_id, produit_id, boutique_id, quantite, prix_unitaire_fcfa)
 VALUES (1, 1, 1, 1, 95000);
INSERT INTO paiements (commande_id, moyen, reference_agregateur, statut)
 VALUES (1, 't_money', 'EXEMPLE-0001', 'reussi');
INSERT INTO recommandations (client_id, boutique_source_id, boutique_cible_id,
                             produit_recherche, statut, commande_id)
 VALUES (1, 2, 1, 'Smartphone 128 Go', 'vendue', 1);
