# 🕐 Discord Time Tracker — Mooncake Edition

Bot Discord complet pour tracker les heures de travail, gérer les congés, les dailies visuels, et générer des rapports pour la paie. Pensé pour les studios créatifs.

---

## Commandes

### 🎨 Artistes

| Commande | Description |
|----------|-------------|
| `/start` | 🟢 Commencer ta journée |
| `/stop` | 🔴 Terminer (⚠️ bloqué sans daily !) |
| `/pause` · `/resume` | ⏸️ Pauses (déduites auto) |
| `/off [raison] [date]` | 🏖️ Jour off |
| `/myschedule [début] [fin] [tz]` | 🕐 Horaires + timezone (ex: `/myschedule 9 18 CET`) |
| `/mydays [jours]` | 📆 Jours de travail (ex: `/mydays lundi,mardi,mercredi,jeudi`) |
| `/conge [début] [fin] [raison]` | 🏖️ Demander un congé (admin notifié) |
| `/edit [date] [début] [fin] [raison]` | ✏️ Correction d'heures (validation admin) |
| `/status` | 📊 Ton statut actuel |
| `/myreport [mois] [année]` | 📈 Tes heures + montant estimé |
| `/mydailies [mois] [année]` | 📖 Tes dailies du mois |

**Daily** = poster dans ton canal `-progress` avec `#daily` dans le message. Images, vidéos, liens acceptés.

### 👑 Admin

| Commande | Description |
|----------|-------------|
| `/today [dept]` | 📋 Résumé du jour |
| `/dailies [dept] [date]` | 📋 Dailies + manquants + absents |
| `/summary [mois] [année] [dept]` | 📊 Résumé mensuel par département |
| `/report [mois] [année] [dept]` | 📑 Export TXT + CSV pour la paie |
| `/setrate @artiste [taux] [devise]` | 💰 Taux horaire |
| `/rates` | 💰 Tous les taux |
| `/pending` | 📋 Corrections en attente |
| `/approve [id]` · `/reject [id]` | ✅❌ Traiter une correction |
| `/pendingconge` | 🏖️ Congés en attente |
| `/approveconge [id]` · `/rejectconge [id]` | ✅❌ Traiter un congé (ou emoji ✅/❌) |
| `/vacances [début] [fin] [raison]` | 🏖️ Vacances collectives |
| `/cancelvacances [début] [fin]` | ❌ Annuler vacances collectives |

---

## Fonctionnalités

### 📝 Dailies dans les canaux -progress
- Chaque artiste a son canal (`#theo-progress`, `#alice-progress`...)
- Poster avec `#daily` dans le message → bot réagit ✅
- Images, vidéos, liens capturés
- `/stop` **bloqué** tant que le daily n'est pas posté
- Liens vers les posts originaux dans tous les résumés

### 🏖️ Congés
- L'artiste demande avec `/conge` → notification avec @Admin tagué
- L'admin clique ✅ ou ❌ directement sur le message (ou commande)
- Artiste notifié dans son canal progress
- Le matin des congés, message dans le canal progress

### 📅 Jours fériés France
- 11 fériés (y compris Pâques, Ascension, Pentecôte calculés auto)
- Notification la veille à 17h (vendredi si lundi férié)
- Pas de rappels les jours fériés — un artiste peut bosser quand même

### 🔔 Rappels intelligents (canaux -progress, pas en DM)
- **+1h** — rappel /start avec boutons (🟢 LIVE · 🏖️ OFF · ⏰ En retard)
- **+7h** — rappel daily
- **20h** — résumé auto dans #time-tracking + rappel daily insistant
- **Heure de fin** — "ta session est toujours ouverte !"
- **+10h** — alerte admin si session trop longue
- Respecte les jours/horaires de chaque artiste

### 🦉 Night Owl
- **Minuit** — "tu bosses encore ?" avec boutons
- **3h du mat** — fermeture forcée + message marrant

### 💬 Messages fun (mix FR/EN)
Le bot met la bonne ambiance. Messages aléatoires modifiables dans les listes `MSG_*`.

### 💰 Paie
- Taux horaire par artiste · Export CSV (Excel/Sheets, séparateur `;`)

---

## Installation

### 1. Bot Discord
1. [discord.com/developers](https://discord.com/developers/applications) → New Application
2. Bot → Token · Intents : ✅ Server Members, ✅ Message Content
3. OAuth2 : `bot` + `applications.commands` · Permissions : Send Messages, Embeds, Attach Files, Read History, Add Reactions

### 2. Serveur Discord
- `#time-tracking` — résumés publics, dailies
- `#time-tracking-admin` — infos privées (sessions suspectes, demandes de congé, dailies manquants)
- Canaux `-progress` par artiste (format flexible : `🍕 | maria-progress` OK)
- Rôles : `Admin`, `DreamTeam`, départements optionnels (`Animation Team`...)

### 3. Railway
Push GitHub → [railway.app](https://railway.app) → Variable : `DISCORD_BOT_TOKEN`

### 4. Local
```bash
pip install -r requirements.txt
export DISCORD_BOT_TOKEN="ton-token"
python bot.py
```

## Config (bot.py)
```python
ADMIN_ROLE_NAME = "Admin"
TEAM_ROLE_NAME = "DreamTeam"
DEPT_ROLE_SUFFIX = "Team"
SUMMARY_CHANNEL_NAME = "time-tracking"
PROGRESS_CHANNEL_SUFFIX = "-progress"
DAILY_KEYWORD = "#daily"
DEFAULT_TIMEZONE = "CET"
```

## Tables DB
`work_sessions` · `pauses` · `days_off` · `dailies` · `hourly_rates` · `edit_requests` · `leave_requests` · `user_schedules` · `collective_holidays` · `snoozes`
