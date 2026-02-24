# 🕐 Discord Time Tracker — Mooncake Edition

Bot Discord complet pour tracker les heures de travail, gérer les congés, les dailies visuels, et générer des rapports pour la paie. Pensé pour les studios d'animation.

---

## ⚡ Démarrage rapide

1. Crée le bot sur [discord.com/developers](https://discord.com/developers/applications)
   - Intents : ✅ Server Members, ✅ Message Content, ✅ Guild Members
   - Permissions : ✅ Manage Channels, ✅ Manage Roles *(nécessaires pour le canal setup temporaire)*
2. Sur ton serveur Discord, crée :
   - `#time-tracking` — résumés publics (⚠️ les messages non-admin sont auto-supprimés)
   - `#time-tracking-admin` — privé admin (congés, sessions suspectes, alertes)
   - Un canal `-progress` par artiste (ex: `maria-progress` ou `🍕 | maria-progress`)
   - Rôles : `Admin`, `DreamTeam`, + départements (`Animation Team`, `LookDev Team`...) + rôles studio (`Lead`, `Artist`...)
3. Déploie sur [Railway](https://railway.app) avec la variable `DISCORD_BOT_TOKEN`

---

## 🎨 Commandes Artiste

| Commande | Description |
|----------|-------------|
| `/setup` | 🎓 Configuration guidée en DM (horaires, timezone, pause déj) |
| `/start` | 🟢 Commencer ta journée |
| `/stop` | 🔴 Terminer (⚠️ bloqué sans daily !) |
| `/pause` · `/resume` | ⏸️ Pauses manuelles |
| `/off [raison] [date]` | 🏖️ Jour off |
| `/conge [début] [fin] [raison]` | 🏖️ Demander un congé |
| `/edit [date] [début] [fin] [raison]` | ✏️ Correction d'heures |
| `/status` | 📊 Ton statut actuel |
| `/myreport [mois] [année]` | 📈 Tes heures + montant estimé |
| `/mydailies [mois] [année]` | 📖 Tes dailies du mois |

**Config perso :**

| Commande | Description |
|----------|-------------|
| `/myschedule [début] [fin] [tz]` | 🕐 Horaires + timezone |
| `/mydays [jours]` | 📆 Jours de travail (ex: `lundi,mardi,mercredi,jeudi,vendredi`) |
| `/mylunch [minutes]` | 🍽️ Pause déjeuner (défaut: 60min) |
| `/mychannel [canal]` | 📌 Lier ton canal progress |

> **📅 Dates** : tu peux écrire `25/02/2026`, `2026-02-25`, ou `25.02.2026` — le bot comprend tout !

### 🎓 Setup guidé (`/setup`)

Lance le tutoriel de configuration en **canal temporaire privé**. En 4 étapes avec boutons :
1. Tes horaires (Matin / Standard / Après-midi / Manuel)
2. Ta timezone (Europe, UK, East US, West US)
3. Ta pause déjeuner (30 / 45 / 60min / Désactivée)
4. Récap + aide-mémoire des commandes essentielles

Le bot crée automatiquement un canal **`#setup-[ton-nom]`** visible uniquement par toi et les admins. Il se supprime tout seul à la fin (ou après 10min d'inactivité).

> Le setup est lancé **automatiquement** quand tu rejoins le serveur avec le rôle `DreamTeam`.

### Comment poster ton daily

1. Va dans ton canal `-progress` (ex: `#maria-progress`)
2. Écris un message avec **#daily** dedans (+ images, vidéos, liens)
3. Le bot réagit ✅ — c'est enregistré !
4. Sans daily, `/stop` est bloqué

> ⚠️ **N'écris PAS dans `#time-tracking`** — ce canal est réservé au bot. Tes messages y seront supprimés automatiquement.

### 🍽️ Pause déjeuner automatique

- Par défaut : **1h** déduite automatiquement si ta session > 6h
- Si tu fais `/pause` de 30min+ toi-même → pas de déduction auto
- Configure avec `/mylunch 45` (45min) ou `/mylunch 0` (désactivé)

### 🔥 Streak & Overtime

- **Streak** = jours consécutifs de daily. Visible dans `/stop` : 📝 à 2+, ⭐ à 3+, 🔥 à 5+ jours
- **Overtime** = tout au-delà de 8h/jour (après lunch). Affiché séparément dans `/stop`, résumés, rapports

---

## 👑 Commandes Admin

| Commande | Description |
|----------|-------------|
| `/who` | 👀 Qui est en ligne maintenant |
| `/today [dept]` | 📋 Résumé du jour |
| `/dailies [dept] [date]` | 📋 Dailies + manquants |
| `/summary [mois] [année] [dept]` | 📊 Résumé mensuel |
| `/report [mois] [année] [dept]` | 📑 Export TXT + CSV pour la paie |
| `/setrate @artiste [taux] [devise]` | 💰 Taux horaire custom |
| `/rates` | 💰 Tous les taux |
| `/pending` | 📋 Corrections en attente |
| `/approve [id]` · `/reject [id]` | ✅❌ Traiter une correction |
| `/pendingconge` | 🏖️ Congés en attente |
| `/approveconge [id]` · `/rejectconge [id]` | ✅❌ Traiter un congé |
| `/vacances [début] [fin] [raison]` | 🏖️ Vacances collectives |
| `/cancelvacances [début] [fin]` | ❌ Annuler vacances |

### Approbation des congés

Deux méthodes :
1. **Emoji** : clique ✅ ou ❌ directement sur le message du bot dans `#time-tracking-admin`
2. **Commande** : `/approveconge 5` ou `/rejectconge 5`

Les **Lead**, **Supervisor** et **Head** peuvent aussi approuver les congés de leur département.

---

## 🎭 Rôles Studio

Crée ces rôles Discord et assigne-les. Le bot les détecte automatiquement :

| Rôle | Emoji | Taux défaut | Approuve congés |
|------|-------|-------------|----------------|
| Head | 👑 | 50$/h | ✅ son dept |
| Lead | ⭐ | 40$/h | ✅ son dept |
| Supervisor | 🔷 | 38$/h | ✅ son dept |
| Senior Artist | 💎 | 32$/h | ❌ |
| Artist | 🎨 | 25$/h | ❌ |
| Junior Artist | 🌱 | 20$/h | ❌ |
| Testor | 🧪 | 22$/h | ❌ |
| Intern | 📚 | 15$/h | ❌ |

- Le taux du rôle s'applique si aucun `/setrate` custom n'est défini
- Le rôle apparaît dans `/who`, `/today`, résumés, et rapports CSV (colonne `Rôle`)

---

## 🔔 Automatisations

| Quand | Quoi | Où |
|-------|------|-----|
| +1h après start_hour | Rappel `/start` avec boutons | canal progress |
| +7h après start | Rappel daily | canal progress |
| Heure de fin | "T'as oublié `/stop` ?" | canal progress |
| Session > 10h | Alerte (une seule fois) | progress + admin |
| 20h | Résumé du jour | `#time-tracking` |
| 20h | Alertes admin | `#time-tracking-admin` |
| 23h55 | Récap fin de journée | `#time-tracking` |
| Minuit | Night owl check | canal progress |
| 3h | Fermeture forcée | canal progress |
| Vendredi 19h | Digest hebdo perso | canal progress |
| Vendredi 19h | Dashboard admin | `#time-tracking-admin` |
| 17h veille de férié | Notification férié | `#time-tracking` |

### Timezone été/hiver

Automatique ! CET ↔ CEST, EST ↔ EDT, etc. L'artiste tape juste `CET` dans `/myschedule`.

---

## 📂 Canaux

| Canal | Qui écrit | Contenu |
|-------|-----------|---------|
| `#time-tracking` | 🤖 Bot + Admin | Résumés, notifications |
| `#time-tracking-admin` | 🤖 Bot + Admin | Congés, alertes |
| `#xxx-progress` | Artiste + Bot | Dailies, rappels |

> Le bot supprime les messages non-admin dans `#time-tracking` avec une explication.

---

## ⚙️ Configuration

```python
ADMIN_ROLE_NAME = "Admin"
TEAM_ROLE_NAME = "DreamTeam"
DEPT_ROLE_SUFFIX = "Team"              # "Animation Team" → dept "Animation"
SUMMARY_CHANNEL_NAME = "time-tracking"
ADMIN_CHANNEL_NAME = "time-tracking-admin"
PROGRESS_CHANNEL_SUFFIX = "-progress"
DAILY_KEYWORD = "#daily"
DEFAULT_TIMEZONE = "CET"               # DST auto
DEFAULT_LUNCH_MINUTES = 60             # Pause déj
EXPECTED_WORK_HOURS = 8                # Seuil overtime
MIN_WEEKLY_HOURS = 32                  # Alerte si moins
```


---

## 📅 Système de Meetings

### Commandes Admin

| Commande | Description |
|----------|-------------|
| `/createmeeting` | 📅 Créer un meeting (vote optionnel avec `vote:True`) |
| `/cancelmeeting [id]` | ❌ Annuler un meeting |
| `/closevote [id]` | 🗳️ Clore le vote manuellement (si vote=True) |
| `/rsvpstatus [id]` | 📊 Voir les RSVP d'un meeting |
| `/meetings` | 📋 Voir tous les meetings à venir |

### Commandes Artiste

| Commande | Description |
|----------|-------------|
| `/myagenda` | 📆 Tes meetings à venir + statut RSVP |

### Créer un meeting

```
# Cas normal — créneau fixe, RSVP direct
/createmeeting date:demain time:16h title:"Review animation" teams:"Animation Team"

# Avec vote de créneau (optionnel)
/createmeeting date:lundi title:"Sync LookDev" teams:"LookDev Team" vote:True slots:15h,16h,17h

# Paramètres disponibles
  date       : demain, lundi, 25/02/2026...
  time       : 16h, 14h30 (requis sans vote)
  title      : sujet du meeting
  teams      : rôles invités séparés par virgules
  voice      : salon vocal (optionnel)
  duration   : durée en minutes (défaut: 60)
  urgent     : true/false — 🚨 flag urgent
  recurrence : none / weekly / biweekly / monthly
  vote       : true/false — active le vote de créneau
  slots      : créneaux si vote=True (ex: 15h,16h,17h)
```

### RSVP

Chaque artiste invité reçoit dans son canal `-progress` :
- **✅ Je serai là !** — confirme sa présence
- **❌ Je ne peux pas** — se désinscrit
- **🔄 Autre moment...** — ouvre une popup pour proposer une alternative (texte libre)

Si plusieurs personnes proposent un autre moment, l'admin reçoit une alerte avec toutes les suggestions.

### Vote de créneau (optionnel — `vote:True`)

Utile quand l'heure n'est pas encore fixée :
1. Le bot crée le meeting en statut **🗳️ vote en cours**
2. Les invités reçoivent un lien vers `#meetings` pour voter
3. Dans `#meetings`, boutons par créneau — **on peut voter pour plusieurs**
4. Quand tout le monde a voté → **confirmation automatique** du créneau gagnant
5. Sinon, admin fait `/closevote [id]` pour forcer la clôture

### Récurrence

- `weekly` — nouveau meeting créé automatiquement chaque semaine
- `biweekly` — toutes les 2 semaines  
- `monthly` — chaque mois

### Automatisations

| Quand | Quoi |
|-------|------|
| 30min avant le meeting | Rappel dans le canal progress de chaque invité |
| Chaque nuit à 1h | Création de la prochaine occurrence (meetings récurrents) |

### Canaux

| Canal | Contenu |
|-------|---------|
| `#meetings` | Annonces publiques + boutons de vote *(créer le canal sur le serveur)* |

> Si `#meetings` n'existe pas, les annonces vont dans `#time-tracking`.

### Conflits

Le bot détecte automatiquement si un des membres invités a déjà un meeting au même créneau et affiche un warning. Le meeting est créé quand même — l'admin décide.

## 🗃️ Base de données

SQLite : `work_sessions` · `pauses` · `days_off` · `dailies` · `hourly_rates` · `edit_requests` · `leave_requests` · `user_schedules` · `user_channels` · `collective_holidays` · `snoozes`

## 🚀 Déploiement

```bash
# Local
pip install -r requirements.txt
export DISCORD_BOT_TOKEN="ton-token"
python bot.py

# Railway : push GitHub → railway.app → variable DISCORD_BOT_TOKEN
```
