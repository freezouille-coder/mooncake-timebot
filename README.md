# 🕐 Bot Discord — Suivi des heures & gestion d'équipe

Bot Discord complet pour tracker les heures de travail de ton équipe d'artistes, gérer les jours off, les dailies, les taux horaires, et générer des rapports mensuels pour la paie.

---

## Commandes

### 🎨 Artistes

| Commande | Description |
|----------|-------------|
| `/start` | Commencer sa journée |
| `/stop` | Terminer sa journée |
| `/pause` | Prendre une pause |
| `/resume` | Reprendre après une pause |
| `/off [raison] [date]` | Déclarer un jour off |
| `/mydaily [message]` | Publier son daily (ce qu'on a fait aujourd'hui) |
| `/edit [date] [début] [fin] [raison]` | Demander une correction d'heures |
| `/status` | Voir son statut actuel (privé) |
| `/myreport [mois] [année]` | Voir ses heures du mois + montant estimé (privé) |
| `/mydailies [mois] [année]` | Voir ses dailies du mois (privé) |

### 👑 Admin

| Commande | Description |
|----------|-------------|
| `/today` | Résumé de la journée : qui travaille, off, pas pointé |
| `/dailies [date]` | Dailies publiés + qui manque + qui n'a pas pointé |
| `/summary [mois] [année]` | Résumé mensuel : heures, montants, dailies par artiste |
| `/report [mois] [année]` | Rapport détaillé exporté en **TXT + CSV** |
| `/setrate [artiste] [taux] [devise]` | Définir le taux horaire d'un artiste |
| `/rates` | Voir tous les taux horaires |
| `/pending` | Voir les demandes de correction en attente |
| `/approve [id]` | Approuver une correction (notifie l'artiste en DM) |
| `/reject [id] [raison]` | Rejeter une correction (notifie l'artiste en DM) |

---

## Fonctionnalités clés

### 💰 Taux horaires & calcul de paie
- Définis un taux par artiste avec `/setrate @Alice 25.00 $`
- Le `/summary` et `/report` calculent automatiquement les montants à payer
- Les artistes voient leur estimation dans `/myreport`
- Export CSV avec colonnes Taux, Montant, Devise pour import dans Excel/Sheets

### 📝 Dailies obligatoires
- Le bot identifie les artistes via le **rôle Discord "Artiste"**
- `/dailies` montre qui a publié, qui manque, qui n'a pas pointé du tout
- Rappel automatique en DM à 17h si le daily n'est pas fait
- Statistiques dailies dans les rapports mensuels (ex: "18/20 — 90%")

### ✏️ Corrections d'heures
- L'artiste fait `/edit 2026-02-10 09:00 17:30 "J'ai oublié de /start"`
- La demande est postée dans `#time-tracking` et visible via `/pending`
- L'admin approuve (`/approve 3`) ou rejette (`/reject 3 "heures incorrectes"`)
- L'artiste reçoit un DM avec la décision

### 🔔 Rappels automatiques en DM
- **10h** — "Tu n'as pas encore pointé !" (seulement les jours de semaine)
- **17h** — "Tu n'as pas publié ton daily !"
- Les rappels ne touchent que les membres avec le rôle **Artiste**
- Les DM respectent les paramètres de confidentialité (silencieux si DMs fermés)

### 📊 Export CSV
Le `/report` génère deux fichiers :
- **TXT** — rapport lisible avec bordures, dailies, totaux
- **CSV** — une ligne par session, séparateur `;`, colonnes :
  `Artiste, Date, Début, Fin, Pause (min), Heures, Taux, Montant, Devise, Daily, Message daily, Type`

Le CSV s'ouvre directement dans Excel ou Google Sheets pour la paie.

---

## Installation

### 1. Créer le bot Discord

1. https://discord.com/developers/applications → **New Application**
2. **Bot** → **Reset Token** → copie le token
3. Active les intents : ✅ Server Members, ✅ Message Content
4. **OAuth2 → URL Generator** : `bot` + `applications.commands`
   - Permissions : Send Messages, Embed Links, Attach Files, Read Message History
5. Invite le bot avec l'URL

### 2. Configurer le serveur Discord

- Crée un canal `#time-tracking`
- Crée un rôle `Admin` (pour toi et tes managers)
- Crée un rôle `Artiste` et assigne-le à tous tes artistes

### 3. Lancer le bot

```bash
pip install -r requirements.txt
export DISCORD_BOT_TOKEN="ton-token"
python bot.py
```

### 4. Configuration (dans bot.py)

```python
ADMIN_ROLE_NAME = "Admin"        # Rôle admin
ARTIST_ROLE_NAME = "Artiste"     # Rôle artiste (pour dailies et rappels)
TIMEZONE_OFFSET = -5             # Fuseau horaire (EST=-5, CET=+1)
REMINDER_HOUR_START = 10         # Heure du rappel /start
REMINDER_HOUR_DAILY = 17         # Heure du rappel daily
```

---

## Structure

```
timebot/
├── bot.py              # Le bot
├── requirements.txt    # discord.py
├── timetracking.db     # Base SQLite (créée auto)
└── README.md
```

### Tables
- `work_sessions` — sessions de travail
- `pauses` — détail des pauses
- `days_off` — jours off
- `dailies` — publications quotidiennes
- `hourly_rates` — taux horaires par artiste
- `edit_requests` — demandes de correction (pending/approved/rejected)
