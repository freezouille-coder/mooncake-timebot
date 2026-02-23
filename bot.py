"""
Discord Time Tracking Bot — Mooncake Edition v4
────────────────────────────────────────────────
Commandes artistes:
  /start              → Commencer ta journée
  /stop               → Terminer ta journée
  /pause / /resume    → Pauses
  /off [raison]       → Jour off
  /edit               → Correction d'heures
  /status             → Ton statut
  /myreport           → Tes heures du mois
  /mydailies          → Tes dailies du mois
  /myschedule         → Tes horaires + timezone

Daily = poster dans ton canal *-progress avec #daily

Rappels dans le canal -progress avec boutons:
  🟢 LIVE  ·  🏖️ OFF  ·  ⏰ En retard

Commandes admin:
  /today [dept]  ·  /dailies [dept]  ·  /summary [dept]  ·  /report [dept]
  /setrate  ·  /rates  ·  /pending  ·  /approve  ·  /reject
"""

import discord
from discord import app_commands
from discord.ext import commands, tasks
from discord.ui import View, Button
import sqlite3, csv, re, asyncio
from datetime import datetime, timedelta, time, timezone
from typing import Optional
import os
from pathlib import Path

# ─── Configuration ───────────────────────────────────────────────────────────
DB_PATH = Path(__file__).parent / "timetracking.db"
TOKEN = os.getenv("DISCORD_BOT_TOKEN", "YOUR_TOKEN_HERE")
ADMIN_ROLE_NAME = "Admin"
TEAM_ROLE_NAME = "DreamTeam"
DEPT_ROLE_SUFFIX = "Team"
SUMMARY_CHANNEL_NAME = "time-tracking"
ADMIN_CHANNEL_NAME = "time-tracking-admin"
PROGRESS_CHANNEL_SUFFIX = "-progress"
DAILY_KEYWORD = "#daily"
DEFAULT_SCHEDULE_START = 10
DEFAULT_SCHEDULE_END = 18
DEFAULT_TIMEZONE = "CET"
REMINDER_DAILY_OFFSET = 7           # Rappel daily = start + 7h (ex: 9h→16h)
DEFAULT_HOURLY_RATE = 0.0

# ─── Studio Roles (hiérarchie + taux par défaut) ─────────────────────────────
# Rôles détectés via les rôles Discord. L'ordre = hiérarchie (0 = plus haut).
# can_approve = peut approuver les congés de son département.

STUDIO_ROLES = {
    "Head":          {"level": 0, "emoji": "👑", "default_rate": 50.0, "can_approve": True},
    "Lead":          {"level": 1, "emoji": "⭐", "default_rate": 40.0, "can_approve": True},
    "Supervisor":    {"level": 2, "emoji": "🔷", "default_rate": 38.0, "can_approve": True},
    "Senior Artist": {"level": 3, "emoji": "💎", "default_rate": 32.0, "can_approve": False},
    "Artist":        {"level": 4, "emoji": "🎨", "default_rate": 25.0, "can_approve": False},
    "Junior Artist": {"level": 5, "emoji": "🌱", "default_rate": 20.0, "can_approve": False},
    "Testor":        {"level": 6, "emoji": "🧪", "default_rate": 22.0, "can_approve": False},
    "Intern":        {"level": 7, "emoji": "📚", "default_rate": 15.0, "can_approve": False},
}

# ─── Timezone mapping (DST-aware) ──────────────────────────────────────────
# L'artiste tape "CET" ou "EST", le bot utilise la vraie zone IANA en interne
# pour gérer automatiquement le changement d'heure été/hiver.

from zoneinfo import ZoneInfo

TZ_MAP = {
    # Europe
    "CET": "Europe/Paris", "CEST": "Europe/Paris",
    "WET": "Europe/London", "EET": "Europe/Athens",
    "GMT": "Europe/London", "UTC": "UTC",
    # Amérique du Nord
    "EST": "America/New_York", "EDT": "America/New_York",
    "CST": "America/Chicago", "CDT": "America/Chicago",
    "MST": "America/Denver", "MDT": "America/Denver",
    "PST": "America/Los_Angeles", "PDT": "America/Los_Angeles",
    # Autres
    "JST": "Asia/Tokyo", "KST": "Asia/Seoul",
    "IST": "Asia/Kolkata", "AEST": "Australia/Sydney",
    "NZST": "Pacific/Auckland", "BRT": "America/Sao_Paulo",
    "HST": "Pacific/Honolulu", "AKST": "America/Anchorage",
    "SGT": "Asia/Singapore", "HKT": "Asia/Hong_Kong",
}

def get_zoneinfo(tz_name):
    """Retourne l'objet ZoneInfo pour un acronyme timezone."""
    iana = TZ_MAP.get(tz_name.upper(), TZ_MAP.get(DEFAULT_TIMEZONE, "Europe/Paris"))
    return ZoneInfo(iana)

def tz_offset(tz_name):
    """Retourne l'offset UTC actuel (DST-aware) pour un timezone."""
    from datetime import timezone as tz_mod
    zi = get_zoneinfo(tz_name)
    now_aware = datetime.now(zi)
    return int(now_aware.utcoffset().total_seconds() / 3600)

def now_tz(tz_name):
    """Heure actuelle dans un timezone donné (DST-aware)."""
    zi = get_zoneinfo(tz_name)
    return datetime.now(zi).replace(tzinfo=None)  # Retourne naive pour compatibilité DB

# ─── Jours fériés France ────────────────────────────────────────────────────

def _easter(year):
    """Calcul de Pâques (algorithme de Butcher)."""
    a=year%19; b=year//100; c=year%100; d=b//4; e=b%4
    f=(b+8)//25; g=(b-f+1)//3; h=(19*a+b-d-g+15)%30
    i=c//4; k=c%4; l=(32+2*e+2*i-h-k)%7
    m=(a+11*h+22*l)//451; month=(h+l-7*m+114)//31; day=(h+l-7*m+114)%31+1
    return datetime(year, month, day)

def get_french_holidays(year):
    """Retourne un dict date_str -> nom du jour férié pour une année."""
    easter = _easter(year)
    holidays = {
        f"{year}-01-01": "Jour de l'An",
        f"{year}-05-01": "Fête du Travail",
        f"{year}-05-08": "Victoire 1945",
        f"{year}-07-14": "Fête Nationale",
        f"{year}-08-15": "Assomption",
        f"{year}-11-01": "Toussaint",
        f"{year}-11-11": "Armistice",
        f"{year}-12-25": "Noël",
        # Fériés mobiles (basés sur Pâques)
        (easter + timedelta(days=1)).strftime("%Y-%m-%d"): "Lundi de Pâques",
        (easter + timedelta(days=39)).strftime("%Y-%m-%d"): "Ascension",
        (easter + timedelta(days=50)).strftime("%Y-%m-%d"): "Lundi de Pentecôte",
    }
    return holidays

def is_holiday(date_str):
    """Vérifie si une date est un jour férié. Retourne le nom ou None."""
    year = int(date_str[:4])
    holidays = get_french_holidays(year)
    return holidays.get(date_str)

def is_holiday_or_vacation(conn, date_str):
    """Vérifie si c'est un jour férié OU une période de vacances collectives."""
    h = is_holiday(date_str)
    if h: return h
    v = conn.execute("SELECT reason FROM collective_holidays WHERE date=?", (date_str,)).fetchone()
    return v["reason"] if v else None

# ─── Database ────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS work_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL, username TEXT NOT NULL, date TEXT NOT NULL,
            start_time TEXT NOT NULL, end_time TEXT,
            total_pause_minutes REAL DEFAULT 0, status TEXT DEFAULT 'working',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS pauses (
            id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER NOT NULL,
            start_time TEXT NOT NULL, end_time TEXT,
            FOREIGN KEY (session_id) REFERENCES work_sessions(id)
        );
        CREATE TABLE IF NOT EXISTS days_off (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL, username TEXT NOT NULL, date TEXT NOT NULL,
            reason TEXT DEFAULT 'Jour off', created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS dailies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL, username TEXT NOT NULL, date TEXT NOT NULL,
            message TEXT NOT NULL,
            message_url TEXT DEFAULT '',
            attachments TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(user_id, date)
        );
        CREATE TABLE IF NOT EXISTS hourly_rates (
            user_id TEXT PRIMARY KEY, username TEXT NOT NULL,
            rate REAL NOT NULL DEFAULT 0, currency TEXT DEFAULT '$',
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS edit_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL, username TEXT NOT NULL,
            target_date TEXT NOT NULL, new_start TEXT NOT NULL, new_end TEXT NOT NULL,
            reason TEXT NOT NULL, status TEXT DEFAULT 'pending',
            reviewed_by TEXT, reviewed_at TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS user_schedules (
            user_id TEXT PRIMARY KEY,
            start_hour INTEGER NOT NULL DEFAULT 10,
            end_hour INTEGER NOT NULL DEFAULT 18,
            tz TEXT NOT NULL DEFAULT 'CET',
            work_days TEXT NOT NULL DEFAULT '0,1,2,3,4',
            lunch_minutes INTEGER NOT NULL DEFAULT 60,
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS snoozes (
            user_id TEXT NOT NULL,
            date TEXT NOT NULL,
            snooze_until TEXT NOT NULL,
            PRIMARY KEY (user_id, date)
        );
        CREATE TABLE IF NOT EXISTS collective_holidays (
            date TEXT NOT NULL,
            reason TEXT NOT NULL,
            created_by TEXT,
            PRIMARY KEY (date)
        );
        CREATE TABLE IF NOT EXISTS leave_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL, username TEXT NOT NULL,
            start_date TEXT NOT NULL, end_date TEXT NOT NULL,
            reason TEXT NOT NULL, status TEXT DEFAULT 'pending',
            reviewed_by TEXT, reviewed_at TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS user_channels (
            user_id TEXT PRIMARY KEY,
            channel_id TEXT NOT NULL,
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_user_date ON work_sessions(user_id, date);
        CREATE INDEX IF NOT EXISTS idx_off_user_date ON days_off(user_id, date);
        CREATE INDEX IF NOT EXISTS idx_dailies_user_date ON dailies(user_id, date);
        CREATE INDEX IF NOT EXISTS idx_edits_status ON edit_requests(status);
        CREATE INDEX IF NOT EXISTS idx_leave_status ON leave_requests(status);
    """)
    # Migration: ajouter lunch_minutes si colonne manquante (backward compat)
    try: conn.execute("ALTER TABLE user_schedules ADD COLUMN lunch_minutes INTEGER NOT NULL DEFAULT 60")
    except: pass
    conn.commit(); conn.close()

# ─── Helpers ─────────────────────────────────────────────────────────────────

MOIS_FR = ["","Janvier","Février","Mars","Avril","Mai","Juin",
           "Juillet","Août","Septembre","Octobre","Novembre","Décembre"]

import random

# ─── Messages Fun ────────────────────────────────────────────────────────────
# Le bot met la bonne vibe, pas la pression !

MSG_START = [
    "Let's gooo ! 🔥 Bonne journée !",
    "C'est parti mon kiki ! 🚀",
    "Rise and grind! ☀️ Mais à ton rythme hein.",
    "Another day, another slay 💅",
    "Le café est prêt ? Alors on y va ! ☕",
    "Mode bête de travail : activé 🐺",
    "It's showtime baby ! 🎬",
    "Allez, on va faire des trucs incroyables aujourd'hui ✨",
    "Le monde n'est pas ready pour ce que tu vas créer 🌍",
    "Tu vas tout déchirer, comme d'hab 💪",
    "Les pixels ne vont pas se pousser tout seuls ! Let's go 🎨",
    "Main character energy today ✨",
    "Loading... creativity.exe... ▓▓▓▓▓▓▓▓░░ 80% 🖥️",
    "On est là pour casser la baraque ! 🏚️💥",
    "*crack de doigts* On y va. 🫰",
    "Nouvelle journée, nouvelles œuvres d'art 🖼️",
    "Le studio a besoin de tes talents ! Go go go 🏃",
    "May the force be with you today ⚡",
    "Journée de prod, journée de légende 📜",
    "Allez hop, les pinceaux numériques sont prêts ! 🖌️",
    "Tu vas accomplir de grandes choses. Comme d'habitude. 😏",
    "Ready Player One! 🕹️",
    "Time to make art happen ✌️",
    "Café ✅ Motivation ✅ Talent ✅ On envoie ! 🚀",
]

MSG_STOP = [
    "GG WP ! 🎮 Bonne soirée !",
    "Journée bouclée, bien joué ! 🏆",
    "That's a wrap! 🎬 Repose-toi bien.",
    "Mission accomplished 🫡",
    "Tu as bien mérité ton repos ! 😴",
    "Sauvegarde effectuée 💾 À demain !",
    "Et boom, une journée de plus dans la légende 📖",
    "Ctrl+S ta journée, c'est dans la boîte ! 💾",
    "Tu peux être fier·e de toi aujourd'hui 🌟",
    "Pack it up, on se retrouve demain ! 🎒",
    "Achievement unlocked: Another productive day 🏅",
    "Le studio te remercie. Ton canapé aussi. 🛋️",
    "Time to touch some grass 🌿 (ou au moins Netflix)",
    "Beau travail ! Maintenant profite de ta soirée 🌙",
    "Session terminée, légende intacte 🐐",
    "Et voilà, encore une journée de travail bien faite ! ✨",
    "Fin de la transmission. Over and out 📻",
    "Allez, va profiter ! Tu le mérites 🍕",
    "Le bot approuve cette fin de journée. 10/10. ⭐",
    "On dit merci qui ? Merci toi ! 🫶",
]

MSG_STOP_BLOCKED = [
    "Héyyy pas si vite ! 📝 Tu as oublié ton daily ! Poste dans ton canal `-progress` avec **#daily**.",
    "Almost there! 🏃 Mais d'abord, ton daily dans ton canal `-progress` !",
    "Nope! 🙅 Daily d'abord, repos ensuite. Poste dans `-progress` avec **#daily**.",
    "On ferme pas boutique sans le daily ! 🏪 Vite un post dans `-progress` avec **#daily** !",
    "Error 403: Daily Required 🤖 Poste dans ton canal `-progress` avec **#daily** pour débloquer /stop.",
    "Le daily c'est comme les légumes, c'est obligatoire 🥦 Go poster dans `-progress` !",
    "Nuh uh 🙅 Pas de /stop sans daily. C'est la loi. Poste avec **#daily** !",
    "Tu croyais pouvoir t'échapper sans daily ? Think again 🕵️ Poste avec **#daily** !",
    "Access denied. 🔒 Le mot de passe c'est **#daily** dans ton canal progress.",
    "Oopsie ! Daily manquant 📝 Poste vite dans `-progress` avec **#daily**.",
    "Le bot est inflexible sur ce point 🤖 Daily = obligatoire. Go !",
    "Plot twist : tu dois d'abord poster ton daily ! 📝 `-progress` + **#daily**",
]

MSG_REMINDER_START = [
    "👋 Hey {name} ! Il est **{hour}h** ({tz}) et t'as pas encore pointé. Tout va bien ?",
    "👀 {name}, t'es là ? Il est **{hour}h** ({tz})... On t'attend !",
    "🫣 {name}... **{hour}h** ({tz}) et toujours pas de /start. T'as oublié ou c'est off ?",
    "☕ {name}, le café est froid là ! Il est **{hour}h** ({tz}). /start ou /off ?",
    "📡 Signal perdu pour {name}... Il est **{hour}h** ({tz}). Tout roule ?",
    "🐌 {name}, doucement mais sûrement ? Il est **{hour}h** ({tz}), on attend ton /start !",
    "🎵 Toc toc toc, {name} ! Il est **{hour}h** ({tz}). Y'a quelqu'un ? 🚪",
    "🔍 Avis de recherche : {name} ! Dernier·e vu·e... nulle part. Il est **{hour}h** ({tz}).",
    "📣 Appel à {name} ! **{hour}h** ({tz}), les autres ont déjà commencé 😬",
    "🐑 {name}... **{hour}h** ({tz}). On compte les moutons ou on /start ?",
    "🎮 {name}, respawn time! Il est **{hour}h** ({tz}). On a besoin de toi !",
    "🌅 Le soleil est levé, {name} pas encore. **{hour}h** ({tz}). Ça arrive ?",
]

MSG_REMINDER_DAILY = [
    "📝 {name}, n'oublie pas ton daily ! Poste ici avec **#daily**.",
    "📝 Hey {name}, il manque ton daily ! Un petit post avec **#daily** et c'est bon 🫶",
    "📝 {name}, ton daily attend ! Balance ton avancement avec **#daily** ✨",
    "📝 Show us what you got {name} ! Poste ton daily avec **#daily** 🎨",
    "📝 {name}, qu'est-ce que t'as fait de beau aujourd'hui ? **#daily** time !",
    "📝 {name}, un daily un daily un daily ! Poste avec **#daily** 🎵",
    "📝 Petit rappel friendly pour {name} : ton daily avec **#daily** 😊",
    "📝 {name}, montre-nous tes progrès du jour ! Poste avec **#daily** 🎬",
    "📝 Daily check pour {name} ! On veut voir ce que t'as fait avec **#daily** 👀",
    "📝 {name}, c'est l'heure de briller ! Partage ton travail avec **#daily** ⭐",
]

MSG_REMINDER_DAILY_20H = [
    "📝 {name}... il est 20h et toujours pas de daily 🥲 Poste vite avec **#daily** sinon tu pourras pas /stop !",
    "📝 Tic tac {name} ! 20h et pas de daily. Tu sais ce qu'il te reste à faire... **#daily** 🕐",
    "📝 {name}, le daily c'est comme la bise en France, on peut pas y échapper ! Poste avec **#daily** 😘",
    "📝 URGENT {name} ! 20h, pas de daily, `/stop` bloqué. Solution : poste avec **#daily** 🚨",
    "📝 {name}, le bot ne te lâchera pas 🤖 20h, daily manquant. Poste avec **#daily** et tout ira bien.",
    "📝 Last call {name} ! Il est 20h, ton daily est en retard. **#daily** dans ton canal, vite ! 🏃",
    "📝 {name}, on y est presque ! Poste ton daily avec **#daily** et tu pourras enfin /stop 🏁",
]

MSG_MIDNIGHT_CHECK = [
    "🦉 {name}... il est minuit et tu bosses encore ? T'es sûr·e que ça va ?",
    "🌙 Minuit ! {name}, t'es en mode vampire ou quoi ?",
    "🕛 Hey {name}, il est minuit passé... Tu devrais peut-être penser à toi ?",
    "🌚 {name}, même la lune dort bientôt. Tu continues vraiment ?",
    "🧛 {name}, minuit a sonné ! Le bot s'inquiète pour ton sommeil...",
    "🦇 *cris de chauve-souris* Minuit, {name} ! T'es sûr·e de vouloir continuer ?",
    "🕛 {name}, il est minuit. Ton lit est triste sans toi 🛏️",
    "🌃 Minuit passé, {name}. Tu fais des heures sup ou t'as oublié /stop ?",
]

MSG_3AM_FORCE_CLOSE = [
    "😴 **{name}**, il est 3h du mat ! Le bot a pris la décision pour toi : DODO. Ta session est fermée. {hours} de travail aujourd'hui, t'es un·e warrior mais là faut dormir ! 💤",
    "🛏️ 3h du mat, **{name}** ! Allez, on éteint tout. Le bot t'a forcé·e à aller dormir. {hours} de boulot, c'est héroïque mais ton lit t'appelle ! 😤💤",
    "⚠️ ALERTE DODO pour **{name}** ! Il est 3h, session fermée de force. {hours} aujourd'hui, bravo mais DORS. Le projet sera encore là demain, promis 🫶💤",
    "🚨 **{name}**, 3h du mat, c'est fini ! Le bot a activé le protocole repos forcé. {hours} de taf, respect. Maintenant : oreiller. Tout de suite. 🛌",
    "🤖 OVERRIDE SYSTEM : **{name}**, il est 3h. Session fermée. {hours} aujourd'hui. Le bot t'ordonne de dormir. This is not a suggestion. 😴",
    "💤 Game over pour **{name}** ! 3h du mat, session auto-fermée. {hours} de taf aujourd'hui. Tu te prends pour qui, Batman ? 🦇 Va dormir !",
]

MSG_HOLIDAY_WORKER = [
    "Oh, {name} qui bosse un jour férié ! Respect 💪 {holiday} mais toi t'as des choses à créer.",
    "Jour férié ({holiday}) mais {name} est là ! Dedication level: over 9000 🔥",
    "{name} ne connaît pas les jours fériés 😤 ({holiday} ? Connais pas.)",
    "{name} travaille pendant {holiday} ! Quand on aime, on ne compte pas 💛",
    "Le calendrier dit {holiday}, mais {name} dit TRAVAIL 🗓️💪",
]

MSG_CONGE_APPROVED = [
    "🏖️ Congé approuvé ! {name} est en vacances du **{start}** au **{end}**. Profite bien ! 🌴",
    "✅ C'est validé ! {name} est off du **{start}** au **{end}**. Repose-toi bien ! 😎",
    "🎉 Congé confirmé pour {name} ! Du **{start}** au **{end}**. Don't forget to touch grass 🌱",
    "🏖️ Approved ! {name} du **{start}** au **{end}**. See you on the other side ! 🌊",
    "✅ {name} part du **{start}** au **{end}**. Nous on est jaloux mais bon, approuvé 😤🏖️",
]

MSG_ON_LEAVE_TODAY = [
    "🏖️ **{name}** est en congé aujourd'hui ! ({reason}) — back soon ✌️",
    "😴 **{name}** profite de son congé ({reason}). Ne rien attendre de ce côté-là aujourd'hui !",
    "🌴 **{name}** est off ({reason}). Pas de panique, c'est prévu !",
    "🏖️ **{name}** est en mode vacances ({reason}). On se débrouille sans ! 💪",
    "🌞 **{name}** est off ({reason}). No stress, tout est sous contrôle.",
]

MSG_SESSION_FORGOTTEN_END = [
    "🕐 Hey {name}, il est **{hour}h** et ta session est toujours ouverte ! T'as oublié `/stop` ou tu fais des heures sup ?",
    "🕐 {name}, normalement tu finis à **{hour}h**... Session toujours ouverte ! `/stop` si t'as fini 😉",
    "🕐 {name}, ta journée devait finir à **{hour}h** et t'es encore en mode 'working'. Tu bosses encore ou t'as oublié ?",
    "🕐 Euh {name}... il est **{hour}h** et t'es toujours connecté·e. Oubli de `/stop` ? 🤔",
    "🕐 {name}, **{hour}h** passé ! Ta session est fantôme ou tu bosses encore ? `/stop` ou `/pause` ? 👻",
]

MSG_SESSION_TOO_LONG = [
    "⚠️ **{name}** a une session ouverte depuis plus de **{hours}** ! C'est sûrement un oubli de `/stop`.",
    "🚨 Session de **{name}** ouverte depuis **{hours}** ! Probablement un oubli.",
    "👀 **{name}** en mode travail depuis **{hours}**... Oubli de `/stop` ?",
    "⚠️ **{name}** : session ouverte depuis **{hours}**. À vérifier — très probablement un oubli.",
]

def pick(messages, **kwargs):
    """Choisit un message aléatoire et le formate."""
    return random.choice(messages).format(**kwargs)

def parse_date(text):
    """Parse une date flexible: YYYY-MM-DD, DD/MM/YYYY, DD-MM-YYYY, YYYY/MM/DD. Retourne str YYYY-MM-DD ou None."""
    if not text: return None
    text = text.strip()
    for fmt_str in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt_str).strftime("%Y-%m-%d")
        except: continue
    return None

# ─── Streak & Overtime helpers ───────────────────────────────────────────────

EXPECTED_WORK_HOURS = 8       # Heures normales par jour
EXPECTED_PAUSE_MINS = 90      # Pause déjeuner par défaut (1h30)
MIN_WEEKLY_HOURS = 32         # Alerte admin si artiste fait moins que ça sur la semaine

def get_streak(conn, uid):
    """Calcule le nombre de jours consécutifs avec un daily posté (en arrière depuis aujourd'hui)."""
    date = datetime.strptime(today_str(), "%Y-%m-%d")
    streak = 0
    while True:
        ds = date.strftime("%Y-%m-%d")
        row = conn.execute("SELECT id FROM dailies WHERE user_id=? AND date=?", (uid, ds)).fetchone()
        if not row:
            # Vérifier si c'était un jour off ou weekend (ne casse pas le streak)
            is_off = conn.execute("SELECT id FROM days_off WHERE user_id=? AND date=?", (uid, ds)).fetchone()
            if is_off or date.weekday() >= 5:
                date -= timedelta(days=1)
                continue
            break
        streak += 1
        date -= timedelta(days=1)
    return streak

def calc_overtime_mins(session, lunch_mins=60):
    """Calcule les minutes d'heures sup pour une session (> EXPECTED_WORK_HOURS), après lunch auto."""
    worked = calc_paid_mins(session, lunch_mins)
    expected = EXPECTED_WORK_HOURS * 60
    return max(0, worked - expected)

def get_week_dates(ref_date=None):
    """Retourne (lundi, dimanche) de la semaine contenant ref_date."""
    if ref_date is None: ref_date = datetime.strptime(today_str(), "%Y-%m-%d")
    monday = ref_date - timedelta(days=ref_date.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday

def get_week_stats(conn, uid, monday, sunday):
    """Stats de la semaine pour un artiste: heures, overtime, nb dailies."""
    sessions = conn.execute(
        "SELECT * FROM work_sessions WHERE user_id=? AND date>=? AND date<=? AND status='done'",
        (uid, monday.strftime("%Y-%m-%d"), sunday.strftime("%Y-%m-%d"))
    ).fetchall()
    lunch = get_lunch_minutes(conn, uid)
    total_mins = sum(calc_paid_mins(s, lunch) for s in sessions)
    overtime_mins = sum(calc_overtime_mins(s, lunch) for s in sessions)
    nb_dailies = conn.execute(
        "SELECT COUNT(*) as c FROM dailies WHERE user_id=? AND date>=? AND date<=?",
        (uid, monday.strftime("%Y-%m-%d"), sunday.strftime("%Y-%m-%d"))
    ).fetchone()["c"]
    nb_sessions = len(sessions)
    return {"total_mins": total_mins, "overtime_mins": overtime_mins, "nb_dailies": nb_dailies, "nb_sessions": nb_sessions}

def now_utc(): return datetime.utcnow()

def now_local(): return now_tz(DEFAULT_TIMEZONE)

# now() returns server time (CET by default) for DB storage
def now(): return now_local()
def today_str(): return now().strftime("%Y-%m-%d")

def fmt(minutes):
    if minutes is None or minutes < 0: return "0h00"
    return f"{int(minutes//60)}h{int(minutes%60):02d}"

def get_active_session(conn, uid):
    return conn.execute("SELECT * FROM work_sessions WHERE user_id=? AND date=? AND status IN ('working','paused')", (uid, today_str())).fetchone()

def get_active_pause(conn, sid):
    return conn.execute("SELECT * FROM pauses WHERE session_id=? AND end_time IS NULL", (sid,)).fetchone()

def calc_mins(s):
    """Calcule les minutes brutes travaillées (pauses manuelles déduites, SANS lunch auto)."""
    st = datetime.fromisoformat(s["start_time"])
    en = datetime.fromisoformat(s["end_time"]) if s["end_time"] else now()
    return max(0, (en-st).total_seconds()/60 - (s["total_pause_minutes"] or 0))

def calc_paid_mins(s, lunch_mins=60):
    """Calcule les minutes payables : si session > 6h ET pauses manuelles < 30min, déduit le lunch auto."""
    raw = calc_mins(s)
    manual_pause = s["total_pause_minutes"] or 0
    # Si la session brute (avant pauses) > 6h ET l'artiste n'a pas pris >= 30min de pause manuelle
    if raw > 360 and manual_pause < 30:
        return max(0, raw - lunch_mins)
    return raw

def is_admin(i):
    if i.user.guild_permissions.administrator: return True
    return any(r.name == ADMIN_ROLE_NAME for r in i.user.roles)

def get_rate(conn, uid):
    r = conn.execute("SELECT rate, currency FROM hourly_rates WHERE user_id=?", (uid,)).fetchone()
    return (r["rate"], r["currency"]) if r else (DEFAULT_HOURLY_RATE, "$")

def get_schedule(conn, uid):
    r = conn.execute("SELECT start_hour, end_hour, tz, work_days FROM user_schedules WHERE user_id=?", (uid,)).fetchone()
    if r:
        return (r["start_hour"], r["end_hour"], r["tz"], r["work_days"])
    return (DEFAULT_SCHEDULE_START, DEFAULT_SCHEDULE_END, DEFAULT_TIMEZONE, "0,1,2,3,4")

DEFAULT_LUNCH_MINUTES = 60

def get_lunch_minutes(conn, uid):
    """Retourne la durée de pause déjeuner configurée (défaut 60min)."""
    r = conn.execute("SELECT lunch_minutes FROM user_schedules WHERE user_id=?", (uid,)).fetchone()
    return r["lunch_minutes"] if r else DEFAULT_LUNCH_MINUTES

def get_work_days(conn, uid):
    """Retourne la liste des jours de travail (0=lundi ... 6=dimanche)."""
    _, _, _, wd_str = get_schedule(conn, uid)
    return [int(d.strip()) for d in wd_str.split(",") if d.strip().isdigit()]

def is_work_day(conn, uid, dt=None):
    """Vérifie si la date donnée est un jour de travail pour l'artiste."""
    if dt is None:
        _, _, user_tz, _ = get_schedule(conn, uid)
        dt = now_tz(user_tz)
    return dt.weekday() in get_work_days(conn, uid)

# Mapping jours FR <-> numéros
JOURS_FR = {"lundi":0, "mardi":1, "mercredi":2, "jeudi":3, "vendredi":4, "samedi":5, "dimanche":6}
JOURS_NAMES = ["Lundi","Mardi","Mercredi","Jeudi","Vendredi","Samedi","Dimanche"]

def parse_days(text):
    """Parse 'lundi,mardi,mercredi,jeudi' → '0,1,2,3'"""
    days = []
    for part in text.lower().replace(" ", "").split(","):
        if part in JOURS_FR:
            days.append(JOURS_FR[part])
        elif part.isdigit() and 0 <= int(part) <= 6:
            days.append(int(part))
    return sorted(set(days))

def utc_time(h, m=0):
    return time(hour=(h - tz_offset(DEFAULT_TIMEZONE)) % 24, minute=m)

# ─── Department Helpers ──────────────────────────────────────────────────────

def get_team_members(guild):
    role = discord.utils.get(guild.roles, name=TEAM_ROLE_NAME)
    return [m for m in guild.members if role and role in m.roles and not m.bot] if role else []

def get_dept_roles(guild):
    return [r for r in guild.roles if r.name.endswith(DEPT_ROLE_SUFFIX) and r.name != TEAM_ROLE_NAME]

def get_member_dept(member):
    for r in member.roles:
        if r.name.endswith(DEPT_ROLE_SUFFIX) and r.name != TEAM_ROLE_NAME:
            return r.name.replace(f" {DEPT_ROLE_SUFFIX}", "").replace(DEPT_ROLE_SUFFIX, "")
    return "Sans département"

def get_dept_members(guild, dept_name):
    if not dept_name: return get_team_members(guild)
    for r in guild.roles:
        if r.name.endswith(DEPT_ROLE_SUFFIX) and r.name != TEAM_ROLE_NAME:
            clean = r.name.replace(f" {DEPT_ROLE_SUFFIX}", "").replace(DEPT_ROLE_SUFFIX, "")
            if clean.lower() == dept_name.lower():
                return [m for m in guild.members if r in m.roles and not m.bot]
    return []

def get_dept_list(guild):
    return [r.name.replace(f" {DEPT_ROLE_SUFFIX}", "").replace(DEPT_ROLE_SUFFIX, "") for r in get_dept_roles(guild)]

def build_dept_map(guild):
    result = {}
    for m in get_team_members(guild):
        result[str(m.id)] = get_member_dept(m)
    return result

# ─── Studio Role Helpers ─────────────────────────────────────────────────────

def get_member_studio_role(member):
    """Détecte le rôle studio d'un membre via ses rôles Discord. Retourne le plus haut."""
    best = None
    for r in member.roles:
        if r.name in STUDIO_ROLES:
            info = STUDIO_ROLES[r.name]
            if best is None or info["level"] < best[1]:
                best = (r.name, info["level"])
    return best[0] if best else None

def get_role_info(role_name):
    """Retourne les infos d'un rôle studio (emoji, level, default_rate, can_approve)."""
    return STUDIO_ROLES.get(role_name, {"level": 99, "emoji": "👤", "default_rate": 0.0, "can_approve": False})

def get_role_tag(member):
    """Retourne le tag formaté du rôle studio (emoji + nom), ou chaîne vide."""
    role = get_member_studio_role(member)
    if not role: return ""
    info = get_role_info(role)
    return f"{info['emoji']}{role}"

def build_role_map(guild):
    """Construit un dict {user_id: role_name} pour tous les membres de l'équipe."""
    result = {}
    for m in get_team_members(guild):
        role = get_member_studio_role(m)
        if role: result[str(m.id)] = role
    return result

def can_approve_for(member, target_member):
    """Vérifie si member peut approuver les congés de target_member (même département, rôle supérieur)."""
    approver_role = get_member_studio_role(member)
    if not approver_role: return False
    info = get_role_info(approver_role)
    if not info["can_approve"]: return False
    # Même département ?
    return get_member_dept(member) == get_member_dept(target_member)

def get_effective_rate(conn, uid, member=None):
    """Retourne le taux horaire : custom > défaut du rôle > DEFAULT_HOURLY_RATE."""
    r = conn.execute("SELECT rate, currency FROM hourly_rates WHERE user_id=?", (uid,)).fetchone()
    if r and r["rate"] > 0: return (r["rate"], r["currency"])
    # Taux par défaut du rôle studio
    if member:
        role = get_member_studio_role(member)
        if role:
            info = get_role_info(role)
            if info["default_rate"] > 0:
                return (info["default_rate"], "$")
    return (DEFAULT_HOURLY_RATE, "$")

async def dept_autocomplete(interaction: discord.Interaction, current: str):
    if not interaction.guild: return []
    depts = get_dept_list(interaction.guild)
    return [app_commands.Choice(name=d, value=d) for d in depts if current.lower() in d.lower()][:25]

# ─── Progress Channel Helper ────────────────────────────────────────────────

def find_progress_channel(guild, member):
    """Trouve le canal -progress d'un membre. Priorité: DB > nom flexible > permissions."""
    # 1) Cherche dans la DB (lié via /mychannel ou auto-détection)
    conn = get_db()
    try:
        row = conn.execute("SELECT channel_id FROM user_channels WHERE user_id=?", (str(member.id),)).fetchone()
        if row:
            ch = guild.get_channel(int(row["channel_id"]))
            if ch: return ch
    finally: conn.close()
    # 2) Matching flexible : le nom du canal CONTIENT le username/display_name + se termine par -progress
    name_lower = member.name.lower()
    display_lower = member.display_name.lower().replace(" ", "-")
    for ch in guild.text_channels:
        ch_lower = ch.name.lower()
        if not ch_lower.endswith(PROGRESS_CHANNEL_SUFFIX):
            continue
        # Extrait la partie avant -progress, nettoie les emojis/pipes/tirets
        raw_prefix = ch_lower[:-len(PROGRESS_CHANNEL_SUFFIX)]
        # Nettoie: enlève les caractères spéciaux (emoji, |, ┃) et les tirets en trop
        import re
        clean = re.sub(r'[^\w-]', '', raw_prefix).strip('-').rstrip('-').lstrip('-')
        # Supprime les tirets multiples
        clean = re.sub(r'-+', '-', clean)
        if clean == name_lower or clean == display_lower:
            return ch
        # Fallback: le nom est CONTENU dans le canal
        if name_lower in ch_lower or display_lower in ch_lower:
            return ch
    # 3) Fallback permissions: canal privé où le membre peut écrire
    for ch in guild.text_channels:
        if ch.name.lower().endswith(PROGRESS_CHANNEL_SUFFIX):
            perms = ch.permissions_for(member)
            if not ch.permissions_for(guild.default_role).read_messages and perms.send_messages:
                return ch
    return None

def save_user_channel(user_id, channel_id):
    """Sauvegarde le lien user → canal progress."""
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO user_channels (user_id,channel_id,updated_at) VALUES (?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET channel_id=?,updated_at=?",
            (str(user_id), str(channel_id), now().isoformat(), str(channel_id), now().isoformat()))
        conn.commit()
    finally: conn.close()

def get_admin_channel(guild):
    """Retourne le canal #time-tracking-admin (ou None)."""
    return discord.utils.get(guild.text_channels, name=ADMIN_CHANNEL_NAME)

# ─── Timezone Autocomplete ──────────────────────────────────────────────────

async def tz_autocomplete(interaction: discord.Interaction, current: str):
    common = ["CET", "EST", "PST", "CST", "MST", "GMT", "UTC", "JST", "BRT", "EET", "WET", "AEST", "NZST", "IST", "SGT", "HKT", "HST"]
    return [app_commands.Choice(name=f"{t} (UTC{tz_offset(t):+d})", value=t) for t in common if current.upper() in t][:25]

# ─── Bot Setup ───────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ═══════════════════ INTERACTIVE BUTTONS ════════════════════════════════════

class ReminderStartView(View):
    """Boutons pour le rappel de /start dans le canal progress."""
    def __init__(self, user_id: str):
        super().__init__(timeout=3600*4)  # 4h timeout
        self.user_id = user_id

    @discord.ui.button(label="🟢 Je suis LIVE", style=discord.ButtonStyle.green, custom_id="reminder_live")
    async def btn_live(self, interaction: discord.Interaction, button: Button):
        if str(interaction.user.id) != self.user_id:
            return await interaction.response.send_message("⚠️ Ce bouton n'est pas pour toi.", ephemeral=True)
        conn = get_db()
        try:
            uid, name = self.user_id, interaction.user.display_name
            if get_active_session(conn, uid):
                return await interaction.response.send_message("⚠️ Déjà une session en cours !", ephemeral=True)
            cur = now()
            conn.execute("INSERT INTO work_sessions (user_id,username,date,start_time,status) VALUES (?,?,?,?,'working')",
                         (uid, name, today_str(), cur.isoformat()))
            conn.commit()
            # Désactiver les boutons
            for item in self.children: item.disabled = True
            await interaction.response.edit_message(view=self)
            dept = get_member_dept(interaction.user) if interaction.guild else ""
            dept_txt = f" · {dept}" if dept and dept != "Sans département" else ""
            e = discord.Embed(title="🟢 Journée commencée !", description=f"**{name}**{dept_txt} — {cur.strftime('%H:%M')}", color=0x2ECC71)
            await interaction.followup.send(embed=e)
        finally: conn.close()

    @discord.ui.button(label="🏖️ Je suis OFF", style=discord.ButtonStyle.secondary, custom_id="reminder_off")
    async def btn_off(self, interaction: discord.Interaction, button: Button):
        if str(interaction.user.id) != self.user_id:
            return await interaction.response.send_message("⚠️ Ce bouton n'est pas pour toi.", ephemeral=True)
        conn = get_db()
        try:
            uid, name = self.user_id, interaction.user.display_name
            if conn.execute("SELECT * FROM days_off WHERE user_id=? AND date=?", (uid, today_str())).fetchone():
                return await interaction.response.send_message("⚠️ Déjà off !", ephemeral=True)
            conn.execute("INSERT INTO days_off (user_id,username,date,reason) VALUES (?,?,?,?)",
                         (uid, name, today_str(), "Off (via bouton)"))
            conn.commit()
            for item in self.children: item.disabled = True
            await interaction.response.edit_message(view=self)
            e = discord.Embed(title="🏖️ Off", description=f"**{name}** est off aujourd'hui.", color=0x9B59B6)
            await interaction.followup.send(embed=e)
        finally: conn.close()

    @discord.ui.button(label="⏰ En retard", style=discord.ButtonStyle.primary, custom_id="reminder_snooze")
    async def btn_snooze(self, interaction: discord.Interaction, button: Button):
        if str(interaction.user.id) != self.user_id:
            return await interaction.response.send_message("⚠️ Ce bouton n'est pas pour toi.", ephemeral=True)
        conn = get_db()
        try:
            uid = self.user_id
            snooze_until = (now_utc() + timedelta(hours=1)).isoformat()
            conn.execute("INSERT INTO snoozes (user_id,date,snooze_until) VALUES (?,?,?) "
                         "ON CONFLICT(user_id,date) DO UPDATE SET snooze_until=?",
                         (uid, today_str(), snooze_until, snooze_until))
            conn.commit()
            for item in self.children: item.disabled = True
            await interaction.response.edit_message(view=self)
            await interaction.followup.send(f"⏰ OK **{interaction.user.display_name}**, je re-checke dans 1h !")
        finally: conn.close()

class MidnightView(View):
    """Boutons pour le check de minuit."""
    def __init__(self, user_id: str):
        super().__init__(timeout=3600*3)  # 3h timeout

    @discord.ui.button(label="😤 Ozef, je continue", style=discord.ButtonStyle.danger, custom_id="midnight_continue")
    async def btn_continue(self, interaction: discord.Interaction, button: Button):
        for item in self.children: item.disabled = True
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(f"🔥 **{interaction.user.display_name}** mode night owl activé ! Respect... mais pense à dormir quand même 🦉")

    @discord.ui.button(label="😴 Ok, c'est fini", style=discord.ButtonStyle.green, custom_id="midnight_stop")
    async def btn_stop(self, interaction: discord.Interaction, button: Button):
        conn = get_db()
        try:
            uid, name = str(interaction.user.id), interaction.user.display_name
            active = get_active_session(conn, uid)
            if not active:
                for item in self.children: item.disabled = True
                await interaction.response.edit_message(view=self)
                return await interaction.followup.send("✅ Pas de session active. Bonne nuit ! 💤")
            # Check daily
            has_daily = conn.execute("SELECT * FROM dailies WHERE user_id=? AND date=?", (uid, today_str())).fetchone()
            if not has_daily:
                for item in self.children: item.disabled = True
                await interaction.response.edit_message(view=self)
                return await interaction.followup.send(f"⚠️ {name}, tu dois d'abord poster ton daily avec **#daily** dans ton canal progress avant de fermer ! Courage, c'est le dernier effort 💪")
            # Fermer la session
            ap = get_active_pause(conn, active["id"])
            if ap:
                pd = (now()-datetime.fromisoformat(ap["start_time"])).total_seconds()/60
                conn.execute("UPDATE pauses SET end_time=? WHERE id=?", (now().isoformat(), ap["id"]))
                conn.execute("UPDATE work_sessions SET total_pause_minutes=total_pause_minutes+? WHERE id=?", (pd, active["id"]))
            cur = now()
            conn.execute("UPDATE work_sessions SET end_time=?, status='done' WHERE id=?", (cur.isoformat(), active["id"]))
            conn.commit()
            up = conn.execute("SELECT * FROM work_sessions WHERE id=?", (active["id"],)).fetchone()
            wm = calc_mins(up)
            for item in self.children: item.disabled = True
            await interaction.response.edit_message(view=self)
            e = discord.Embed(title="🌙 Bonne nuit !", description=f"**{name}** — {fmt(wm)} aujourd'hui\n\n{pick(MSG_STOP)}", color=0x9B59B6)
            await interaction.followup.send(embed=e)
        finally: conn.close()

# ═══════════════════ TUTORIAL / SETUP (canal temporaire) ════════════════════

SETUP_CHANNEL_PREFIX = "setup-"          # ex: #setup-maria
SETUP_CHANNEL_TIMEOUT = 600              # 10min d'inactivité → suppression auto

# ─── Helpers canal setup ─────────────────────────────────────────────────────

async def create_setup_channel(guild: discord.Guild, member: discord.Member) -> discord.TextChannel:
    """Crée un canal privé #setup-[nom] visible uniquement par l'artiste + admins."""
    channel_name = f"{SETUP_CHANNEL_PREFIX}{member.display_name.lower().replace(' ', '-')}"

    # Récupérer la catégorie du canal #time-tracking pour y mettre le setup
    ref_ch = discord.utils.get(guild.text_channels, name=SUMMARY_CHANNEL_NAME)
    category = ref_ch.category if ref_ch else None

    # Overwrites : tout le monde ne voit pas, sauf l'artiste et les admins
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        member: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
    }
    # Donner accès aux admins Discord
    admin_role = discord.utils.get(guild.roles, name=ADMIN_ROLE_NAME)
    if admin_role:
        overwrites[admin_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

    ch = await guild.create_text_channel(
        name=channel_name,
        overwrites=overwrites,
        category=category,
        topic=f"Setup guidé pour {member.display_name} — supprimé automatiquement à la fin.",
        reason="Setup guidé time tracker"
    )
    return ch


async def delete_setup_channel(channel: discord.TextChannel, delay: int = 5):
    """Supprime le canal setup après un délai (en secondes)."""
    await asyncio.sleep(delay)
    try:
        await channel.delete(reason="Setup terminé — canal temporaire supprimé")
    except Exception:
        pass


# ─── Setup Views (s'envoient dans le canal temporaire) ───────────────────────

class SetupStep1View(View):
    """Étape 1 : Choisir ses horaires."""
    def __init__(self, user_id: str, channel: discord.TextChannel):
        super().__init__(timeout=SETUP_CHANNEL_TIMEOUT)
        self.user_id = user_id
        self.channel = channel

    async def on_timeout(self):
        await delete_setup_channel(self.channel, delay=0)

    async def _apply(self, interaction: discord.Interaction, start: int, end: int, tz: str):
        if str(interaction.user.id) != self.user_id:
            return await interaction.response.send_message("⚠️ Ce canal est réservé à son propriétaire.", ephemeral=True)
        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO user_schedules (user_id,start_hour,end_hour,timezone,work_days) VALUES (?,?,?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET start_hour=?,end_hour=?,timezone=?",
                (self.user_id, start, end, tz, "lundi,mardi,mercredi,jeudi,vendredi", start, end, tz)
            )
            conn.commit()
        finally:
            conn.close()
        for item in self.children: item.disabled = True
        await interaction.response.edit_message(view=self)
        await _send_setup_step2(self.channel, interaction.user, start, end, tz)

    @discord.ui.button(label="🌅 Matin (9h–17h)", style=discord.ButtonStyle.primary)
    async def btn_matin(self, interaction: discord.Interaction, button: Button):
        await self._apply(interaction, 9, 17, "CET")

    @discord.ui.button(label="☀️ Standard (10h–18h)", style=discord.ButtonStyle.primary)
    async def btn_standard(self, interaction: discord.Interaction, button: Button):
        await self._apply(interaction, 10, 18, "CET")

    @discord.ui.button(label="🌆 Après-midi (12h–20h)", style=discord.ButtonStyle.primary)
    async def btn_apm(self, interaction: discord.Interaction, button: Button):
        await self._apply(interaction, 12, 20, "CET")

    @discord.ui.button(label="⚙️ Je configure moi-même", style=discord.ButtonStyle.secondary)
    async def btn_custom(self, interaction: discord.Interaction, button: Button):
        if str(interaction.user.id) != self.user_id:
            return await interaction.response.send_message("⚠️ Ce canal est réservé à son propriétaire.", ephemeral=True)
        for item in self.children: item.disabled = True
        await interaction.response.edit_message(view=self)
        e = discord.Embed(
            title="⚙️ Configuration manuelle",
            description=(
                "Utilise `/myschedule` sur le serveur pour définir tes horaires précis.\n\n"
                "Exemple : `/myschedule début:9 fin:18 tz:CET`\n\n"
                "*(On continue avec les valeurs par défaut 10h–18h CET pour l'instant.)*"
            ),
            color=0xF39C12
        )
        await self.channel.send(embed=e)
        await _send_setup_step2(self.channel, interaction.user, 10, 18, "CET")


class SetupStep2View(View):
    """Étape 2 : Timezone."""
    def __init__(self, user_id: str, channel: discord.TextChannel, start: int, end: int):
        super().__init__(timeout=SETUP_CHANNEL_TIMEOUT)
        self.user_id = user_id
        self.channel = channel
        self.start = start
        self.end = end

    async def on_timeout(self):
        await delete_setup_channel(self.channel, delay=0)

    async def _apply(self, interaction: discord.Interaction, new_tz: str):
        if str(interaction.user.id) != self.user_id:
            return await interaction.response.send_message("⚠️ Ce canal est réservé à son propriétaire.", ephemeral=True)
        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO user_schedules (user_id,start_hour,end_hour,timezone,work_days) VALUES (?,?,?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET timezone=?",
                (self.user_id, self.start, self.end, new_tz, "lundi,mardi,mercredi,jeudi,vendredi", new_tz)
            )
            conn.commit()
        finally:
            conn.close()
        for item in self.children: item.disabled = True
        await interaction.response.edit_message(view=self)
        await _send_setup_step3(self.channel, interaction.user, self.start, self.end, new_tz)

    @discord.ui.button(label="🇪🇺 Europe (CET/CEST)", style=discord.ButtonStyle.primary)
    async def btn_cet(self, interaction: discord.Interaction, button: Button):
        await self._apply(interaction, "CET")

    @discord.ui.button(label="🇬🇧 UK (GMT/BST)", style=discord.ButtonStyle.primary)
    async def btn_gmt(self, interaction: discord.Interaction, button: Button):
        await self._apply(interaction, "GMT")

    @discord.ui.button(label="🗽 East US (EST/EDT)", style=discord.ButtonStyle.primary)
    async def btn_est(self, interaction: discord.Interaction, button: Button):
        await self._apply(interaction, "EST")

    @discord.ui.button(label="🌴 West US (PST/PDT)", style=discord.ButtonStyle.primary)
    async def btn_pst(self, interaction: discord.Interaction, button: Button):
        await self._apply(interaction, "PST")


class SetupStep3View(View):
    """Étape 3 : Pause déjeuner."""
    def __init__(self, user_id: str, channel: discord.TextChannel, start: int, end: int, tz: str):
        super().__init__(timeout=SETUP_CHANNEL_TIMEOUT)
        self.user_id = user_id
        self.channel = channel
        self.start = start
        self.end = end
        self.tz = tz

    async def on_timeout(self):
        await delete_setup_channel(self.channel, delay=0)

    async def _apply(self, interaction: discord.Interaction, minutes: int):
        if str(interaction.user.id) != self.user_id:
            return await interaction.response.send_message("⚠️ Ce canal est réservé à son propriétaire.", ephemeral=True)
        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO user_schedules (user_id,start_hour,end_hour,timezone,work_days,lunch_minutes) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET lunch_minutes=?",
                (self.user_id, self.start, self.end, self.tz, "lundi,mardi,mercredi,jeudi,vendredi", minutes, minutes)
            )
            conn.commit()
        finally:
            conn.close()
        for item in self.children: item.disabled = True
        await interaction.response.edit_message(view=self)
        await _send_setup_step4(self.channel, interaction.user, self.start, self.end, self.tz)

    @discord.ui.button(label="🍽️ 1h (défaut)", style=discord.ButtonStyle.primary)
    async def btn_60(self, interaction: discord.Interaction, button: Button):
        await self._apply(interaction, 60)

    @discord.ui.button(label="⏱️ 45min", style=discord.ButtonStyle.secondary)
    async def btn_45(self, interaction: discord.Interaction, button: Button):
        await self._apply(interaction, 45)

    @discord.ui.button(label="⏱️ 30min", style=discord.ButtonStyle.secondary)
    async def btn_30(self, interaction: discord.Interaction, button: Button):
        await self._apply(interaction, 30)

    @discord.ui.button(label="🚫 Pas de déduction auto", style=discord.ButtonStyle.danger)
    async def btn_none(self, interaction: discord.Interaction, button: Button):
        await self._apply(interaction, 0)


class SetupStep4View(View):
    """Étape 4 : Récap + confirmation."""
    def __init__(self, user_id: str, channel: discord.TextChannel):
        super().__init__(timeout=SETUP_CHANNEL_TIMEOUT)
        self.user_id = user_id
        self.channel = channel

    async def on_timeout(self):
        await delete_setup_channel(self.channel, delay=0)

    @discord.ui.button(label="🚀 C'est parti !", style=discord.ButtonStyle.green)
    async def btn_go(self, interaction: discord.Interaction, button: Button):
        if str(interaction.user.id) != self.user_id:
            return await interaction.response.send_message("⚠️ Ce canal est réservé à son propriétaire.", ephemeral=True)
        for item in self.children: item.disabled = True
        await interaction.response.edit_message(view=self)
        e = discord.Embed(
            title="🎉 Setup terminé !",
            description=(
                "Tu es configuré·e. Retourne sur le serveur et tape `/start` pour commencer ta journée !\n\n"
                "Ce canal va disparaître dans quelques secondes. Bonne journée 🚀"
            ),
            color=0x2ECC71
        )
        await self.channel.send(embed=e)
        await delete_setup_channel(self.channel, delay=5)

    @discord.ui.button(label="🔁 Recommencer", style=discord.ButtonStyle.secondary)
    async def btn_restart(self, interaction: discord.Interaction, button: Button):
        if str(interaction.user.id) != self.user_id:
            return await interaction.response.send_message("⚠️ Ce canal est réservé à son propriétaire.", ephemeral=True)
        for item in self.children: item.disabled = True
        await interaction.response.edit_message(view=self)
        await _send_setup_step1(self.channel, interaction.user)


# ─── Step senders (vers le canal temporaire) ─────────────────────────────────

async def _send_setup_step1(channel: discord.TextChannel, member: discord.Member):
    e = discord.Embed(
        title="👋 Bienvenue sur le Time Tracker ! — Étape 1/4",
        description=(
            "Ce setup prend **1 minute** et tu n'auras plus à y revenir.\n\n"
            "**🕐 Quels sont tes horaires habituels ?**\n"
            "*(Affine avec `/myschedule` à tout moment)*"
        ),
        color=0x3498DB
    )
    e.set_footer(text="Mooncake Time Tracker · Setup guidé")
    await channel.send(member.mention, embed=e, view=SetupStep1View(str(member.id), channel))


async def _send_setup_step2(channel: discord.TextChannel, member: discord.Member, start: int, end: int, tz: str):
    e = discord.Embed(
        title="🌍 Étape 2/4 — Ta timezone",
        description=(
            f"Horaires : **{start}h → {end}h** ✅\n\n"
            "**Dans quel fuseau horaire tu travailles ?**\n"
            "*(Été/hiver géré automatiquement)*"
        ),
        color=0x3498DB
    )
    e.set_footer(text="Mooncake Time Tracker · Setup guidé")
    await channel.send(embed=e, view=SetupStep2View(str(member.id), channel, start, end))


async def _send_setup_step3(channel: discord.TextChannel, member: discord.Member, start: int, end: int, tz: str):
    e = discord.Embed(
        title="🍽️ Étape 3/4 — Pause déjeuner",
        description=(
            f"Timezone : **{tz}** ✅\n\n"
            "**Combien de temps pour ta pause déj ?**\n\n"
            "Si ta session dépasse **6h** et que tu n'as pas fait `/pause` toi-même, "
            "cette durée sera automatiquement déduite."
        ),
        color=0x3498DB
    )
    e.set_footer(text="Mooncake Time Tracker · Setup guidé")
    await channel.send(embed=e, view=SetupStep3View(str(member.id), channel, start, end, tz))


async def _send_setup_step4(channel: discord.TextChannel, member: discord.Member, start: int, end: int, tz: str):
    conn = get_db()
    try:
        sched = conn.execute("SELECT * FROM user_schedules WHERE user_id=?", (str(member.id),)).fetchone()
        lunch = sched["lunch_minutes"] if sched and sched["lunch_minutes"] is not None else 60
    finally:
        conn.close()
    lunch_txt = f"{lunch}min" if lunch > 0 else "Désactivée"
    e = discord.Embed(
        title="✅ Étape 4/4 — Récap de ta config",
        description=(
            f"🕐 Horaires : **{start}h → {end}h**\n"
            f"🌍 Timezone : **{tz}**\n"
            f"🍽️ Pause déj auto : **{lunch_txt}**\n\n"
            "**📚 Commandes essentielles :**\n"
            "▸ `/start` — Commencer ta journée\n"
            "▸ `/stop` — Terminer *(bloqué sans daily !)*\n"
            "▸ `/pause` / `/resume` — Gérer tes pauses\n"
            "▸ `/status` — Voir où tu en es\n"
            "▸ `/myreport` — Tes heures du mois\n\n"
            "**📝 Le daily :**\n"
            "Poste dans ton canal `-progress` avec **#daily** dans le message.\n"
            "Sans ça, `/stop` est bloqué.\n\n"
            "**✏️ Pour modifier ta config plus tard :**\n"
            "▸ `/myschedule` · `/mydays` · `/mylunch` · `/mychannel`"
        ),
        color=0x2ECC71
    )
    e.set_footer(text="Mooncake Time Tracker · Setup guidé 🎉")
    await channel.send(embed=e, view=SetupStep4View(str(member.id), channel))


# ─── Commande /setup ─────────────────────────────────────────────────────────

@bot.tree.command(name="setup", description="🎓 Configuration guidée (horaires, timezone, pause déj)")
async def cmd_setup(interaction: discord.Interaction):
    """Crée un canal temporaire privé et lance le tutoriel de setup."""
    guild = interaction.guild
    if not guild:
        return await interaction.response.send_message("⚠️ Cette commande doit être utilisée sur le serveur.", ephemeral=True)

    # Vérifier si un canal setup existe déjà pour cet user
    existing_name = f"{SETUP_CHANNEL_PREFIX}{interaction.user.display_name.lower().replace(' ', '-')}"
    existing = discord.utils.get(guild.text_channels, name=existing_name)
    if existing:
        return await interaction.response.send_message(
            f"📬 Ton canal de setup est déjà ouvert : {existing.mention}",
            ephemeral=True
        )

    # Créer le canal
    try:
        ch = await create_setup_channel(guild, interaction.user)
    except discord.Forbidden:
        return await interaction.response.send_message(
            "⚠️ Je n'ai pas la permission de créer des canaux (`Manage Channels`). Contacte un admin !",
            ephemeral=True
        )
    except Exception as ex:
        return await interaction.response.send_message(f"⚠️ Erreur lors de la création du canal : {ex}", ephemeral=True)

    await interaction.response.send_message(
        f"📬 Ton canal de setup est prêt : {ch.mention} — il disparaîtra automatiquement à la fin !",
        ephemeral=True
    )
    await _send_setup_step1(ch, interaction.user)


# ─── on_member_join : propose le setup automatiquement ───────────────────────

@bot.event
async def on_member_join(member: discord.Member):
    """Quand un nouveau membre DreamTeam rejoint, crée son canal setup automatiquement."""
    await asyncio.sleep(5)  # Laisser le temps aux bots de rôles d'assigner
    try:
        member = await member.guild.fetch_member(member.id)
    except Exception:
        return
    if not any(r.name == TEAM_ROLE_NAME for r in member.roles):
        return
    guild = member.guild
    try:
        ch = await create_setup_channel(guild, member)
        e = discord.Embed(
            title=f"👋 Bienvenue {member.display_name} !",
            description=(
                f"Tu viens de rejoindre **{guild.name}**.\n\n"
                "Je suis le bot de time tracking 🤖\n"
                "Je vais t'aider à tracker tes heures, poster tes dailies et gérer tes congés.\n\n"
                "**Configurons tes horaires — ça prend 1 minute !**"
            ),
            color=0x3498DB
        )
        await ch.send(member.mention, embed=e)
        await asyncio.sleep(1)
        await _send_setup_step1(ch, member)
        # Notifier l'artiste dans son canal progress si existant
        pch = find_progress_channel(guild, member)
        if pch:
            await pch.send(
                f"👋 Bienvenue {member.mention} ! Ton canal de configuration est prêt : {ch.mention} 🎓"
            )
    except discord.Forbidden:
        # Pas les perms Manage Channels — fallback canal progress
        pch = find_progress_channel(guild, member)
        if pch:
            try:
                await pch.send(
                    f"👋 Bienvenue {member.mention} ! Tape `/setup` pour configurer tes horaires.\n"
                    f"*(Le bot n'a pas la permission de créer des canaux — contacte un admin.)*"
                )
            except Exception:
                pass
    except Exception:
        pass



# ═══════════════════ REACTION-BASED CONGÉ APPROVAL ═══════════════════════════

@bot.event
async def on_raw_reaction_add(payload):
    """Approuve/rejette un congé via réaction ✅/❌ sur le message dans #time-tracking."""
    if payload.member and payload.member.bot:
        return
    emoji = str(payload.emoji)
    if emoji not in ("✅", "❌"):
        return
    guild = bot.get_guild(payload.guild_id)
    if not guild: return
    channel = guild.get_channel(payload.channel_id)
    if not channel or channel.name not in (SUMMARY_CHANNEL_NAME, ADMIN_CHANNEL_NAME):
        return
    # Vérifier que c'est un admin OU un Lead/Supervisor/Head du même département
    member = payload.member
    if not member: return
    is_global_admin = member.guild_permissions.administrator or any(r.name == ADMIN_ROLE_NAME for r in member.roles)
    # Récupérer le message
    try:
        message = await channel.fetch_message(payload.message_id)
    except: return
    if not message.embeds: return
    embed = message.embeds[0]
    if not embed.title or "Demande de congé #" not in embed.title: return
    # Extraire l'ID du congé
    try:
        rid = int(embed.title.split("#")[1])
    except: return
    conn = get_db()
    try:
        req = conn.execute("SELECT * FROM leave_requests WHERE id=? AND status='pending'", (rid,)).fetchone()
        if not req: return
        # Vérifier les permissions
        if not is_global_admin:
            # Check si le membre est Lead/Supervisor/Head du même département
            target = guild.get_member(int(req["user_id"]))
            if not target or not can_approve_for(member, target):
                return  # Pas autorisé
        if emoji == "✅":
            # Approuver
            d1 = datetime.strptime(req["start_date"], "%Y-%m-%d")
            d2 = datetime.strptime(req["end_date"], "%Y-%m-%d")
            current = d1
            while current <= d2:
                ds = current.strftime("%Y-%m-%d")
                if not conn.execute("SELECT id FROM days_off WHERE user_id=? AND date=?", (req["user_id"], ds)).fetchone():
                    conn.execute("INSERT INTO days_off (user_id,username,date,reason) VALUES (?,?,?,?)",
                                 (req["user_id"], req["username"], ds, f"🏖️ Congé: {req['reason']}"))
                current += timedelta(days=1)
            conn.execute("UPDATE leave_requests SET status='approved', reviewed_by=?, reviewed_at=? WHERE id=?",
                         (member.display_name, now().isoformat(), rid))
            conn.commit()
            msg = pick(MSG_CONGE_APPROVED, name=req["username"], start=req["start_date"], end=req["end_date"])
            e = discord.Embed(title=f"✅ Congé #{rid} approuvé", description=msg, color=0x2ECC71)
            await channel.send(embed=e)
            # Notifier l'artiste
            art = guild.get_member(int(req["user_id"]))
            if art:
                ch = find_progress_channel(guild, art)
                if ch:
                    try: await ch.send(f"✅ {art.mention} — Ton congé du **{req['start_date']}** au **{req['end_date']}** a été approuvé ! Profite bien 🏖️")
                    except: pass
        else:
            # Rejeter
            conn.execute("UPDATE leave_requests SET status='rejected', reviewed_by=?, reviewed_at=? WHERE id=?",
                         (member.display_name, now().isoformat(), rid))
            conn.commit()
            e = discord.Embed(title=f"❌ Congé #{rid} refusé", description=f"**{req['username']}** {req['start_date']}→{req['end_date']}", color=0xE74C3C)
            await channel.send(embed=e)
            art = guild.get_member(int(req["user_id"]))
            if art:
                ch = find_progress_channel(guild, art)
                if ch:
                    try: await ch.send(f"❌ {art.mention} — Ton congé du **{req['start_date']}** au **{req['end_date']}** a été refusé.")
                    except: pass
        # Retirer les réactions du message original
        try:
            await message.clear_reactions()
        except: pass
    finally: conn.close()

# ═══════════════════ #DAILY DETECTION ════════════════════════════════════════

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # ─── Protection canaux #time-tracking : réservés au bot + admins ───
    if message.channel.name in (SUMMARY_CHANNEL_NAME, ADMIN_CHANNEL_NAME):
        is_adm = message.author.guild_permissions.administrator or any(r.name == ADMIN_ROLE_NAME for r in message.author.roles)
        if not is_adm:
            try:
                await message.delete()
                await message.channel.send(
                    f"🚫 {message.author.mention} — Ce canal est réservé au bot et aux admins.\n"
                    f"📝 Pour poster ton daily → va dans ton canal **-progress** et écris avec **#daily**.\n"
                    f"💬 Pour les commandes → utilise les commandes slash (`/start`, `/stop`, etc.).",
                    delete_after=15  # Le message d'explication disparaît après 15s
                )
            except: pass
            return

    # ─── #daily detection dans les canaux -progress ───
    if (message.channel.name.endswith(PROGRESS_CHANNEL_SUFFIX)
            and DAILY_KEYWORD.lower() in message.content.lower()):
        conn = get_db()
        try:
            uid = str(message.author.id)
            name = message.author.display_name
            date = today_str()
            clean_msg = re.sub(r'#daily\b', '', message.content, flags=re.IGNORECASE).strip()
            if not clean_msg: clean_msg = "(images/vidéos)"
            attachment_urls = [a.url for a in message.attachments]
            attachments_str = " | ".join(attachment_urls) if attachment_urls else ""
            msg_url = message.jump_url
            existing = conn.execute("SELECT * FROM dailies WHERE user_id=? AND date=?", (uid, date)).fetchone()
            if existing:
                conn.execute("UPDATE dailies SET message=?, message_url=?, attachments=?, username=? WHERE user_id=? AND date=?",
                             (clean_msg, msg_url, attachments_str, name, uid, date))
                reaction = "🔄"
            else:
                conn.execute("INSERT INTO dailies (user_id,username,date,message,message_url,attachments) VALUES (?,?,?,?,?,?)",
                             (uid, name, date, clean_msg, msg_url, attachments_str))
                reaction = "✅"
            conn.commit()
            await message.add_reaction(reaction)
            # Auto-lier le canal progress à l'artiste
            save_user_channel(uid, message.channel.id)
        except Exception as ex:
            print(f"Erreur daily: {ex}")
            try: await message.add_reaction("❌")
            except: pass
        finally: conn.close()
    await bot.process_commands(message)

# ═══════════════════ ARTIST COMMANDS ═════════════════════════════════════════

@bot.tree.command(name="start", description="🟢 Commencer ta journée")
async def cmd_start(interaction: discord.Interaction):
    conn = get_db()
    try:
        uid, name = str(interaction.user.id), interaction.user.display_name
        if get_active_session(conn, uid):
            return await interaction.response.send_message("⚠️ Session déjà en cours. `/stop` d'abord.", ephemeral=True)
        if conn.execute("SELECT * FROM days_off WHERE user_id=? AND date=?", (uid, today_str())).fetchone():
            # Si c'est un off collectif (vacances/férié), laisser l'artiste override
            off_row = conn.execute("SELECT * FROM days_off WHERE user_id=? AND date=?", (uid, today_str())).fetchone()
            if off_row and off_row["reason"].startswith("🏖️"):
                conn.execute("DELETE FROM days_off WHERE id=?", (off_row["id"],)); conn.commit()
                # Continue — l'artiste veut bosser malgré le off collectif
            else:
                return await interaction.response.send_message("⚠️ Tu es off aujourd'hui. `/off` pour annuler d'abord si c'est une erreur.", ephemeral=True)
        cur = now()
        conn.execute("INSERT INTO work_sessions (user_id,username,date,start_time,status) VALUES (?,?,?,?,'working')", (uid, name, today_str(), cur.isoformat()))
        conn.commit()
        dept = get_member_dept(interaction.user) if interaction.guild else ""
        dept_txt = f" · {dept}" if dept and dept != "Sans département" else ""
        role_tag = get_role_tag(interaction.user) if interaction.guild else ""
        role_txt = f" {role_tag}" if role_tag else ""
        fun = pick(MSG_START)
        e = discord.Embed(title="🟢 Journée commencée !", description=f"**{name}**{role_txt}{dept_txt} — {cur.strftime('%H:%M')}\n\n{fun}", color=0x2ECC71, timestamp=cur)
        # Si c'est un jour férié, petit message spécial
        holiday = is_holiday(today_str())
        if holiday:
            e.add_field(name="📅 Jour férié", value=pick(MSG_HOLIDAY_WORKER, name=name, holiday=holiday), inline=False)
        await interaction.response.send_message(embed=e)
    finally: conn.close()

@bot.tree.command(name="stop", description="🔴 Terminer ta journée")
async def cmd_stop(interaction: discord.Interaction):
    conn = get_db()
    try:
        uid, name = str(interaction.user.id), interaction.user.display_name
        active = get_active_session(conn, uid)
        if not active: return await interaction.response.send_message("⚠️ Pas de session.", ephemeral=True)
        # Bloquer si pas de daily
        if not conn.execute("SELECT * FROM dailies WHERE user_id=? AND date=?", (uid, today_str())).fetchone():
            return await interaction.response.send_message(pick(MSG_STOP_BLOCKED), ephemeral=True)
        ap = get_active_pause(conn, active["id"])
        if ap:
            pd = (now()-datetime.fromisoformat(ap["start_time"])).total_seconds()/60
            conn.execute("UPDATE pauses SET end_time=? WHERE id=?", (now().isoformat(), ap["id"]))
            conn.execute("UPDATE work_sessions SET total_pause_minutes=total_pause_minutes+? WHERE id=?", (pd, active["id"]))
        cur = now()
        conn.execute("UPDATE work_sessions SET end_time=?, status='done' WHERE id=?", (cur.isoformat(), active["id"]))
        conn.commit()
        up = conn.execute("SELECT * FROM work_sessions WHERE id=?", (active["id"],)).fetchone()
        wm_raw = calc_mins(up)
        lunch = get_lunch_minutes(conn, uid)
        wm = calc_paid_mins(up, lunch)
        lunch_deducted = wm_raw - wm  # > 0 si lunch auto-déduit
        ot = max(0, wm - EXPECTED_WORK_HOURS * 60)
        fun = pick(MSG_STOP)
        e = discord.Embed(title="🔴 Journée terminée !", description=f"**{name}**\n\n{fun}", color=0xE74C3C, timestamp=cur)
        e.add_field(name="Début", value=datetime.fromisoformat(up["start_time"]).strftime("%H:%M"), inline=True)
        e.add_field(name="Fin", value=cur.strftime("%H:%M"), inline=True)
        e.add_field(name="Pauses", value=fmt(up["total_pause_minutes"]), inline=True)
        worked_txt = f"**{fmt(wm)}**"
        if lunch_deducted > 0:
            worked_txt += f" (🍽️ -{fmt(lunch_deducted)} lunch auto)"
        if ot > 0:
            worked_txt += f" (⏰ +{fmt(ot)} sup)"
        e.add_field(name="🕐 Payé", value=worked_txt, inline=False)
        # Streak
        streak = get_streak(conn, uid)
        if streak >= 2:
            streak_emoji = "🔥" if streak >= 5 else ("⭐" if streak >= 3 else "📝")
            e.add_field(name=f"{streak_emoji} Streak", value=f"**{streak} jours** de daily consécutifs !", inline=False)
        await interaction.response.send_message(embed=e)
    finally: conn.close()

@bot.tree.command(name="pause", description="⏸️ Pause")
async def cmd_pause(interaction: discord.Interaction):
    conn = get_db()
    try:
        uid = str(interaction.user.id); active = get_active_session(conn, uid)
        if not active: return await interaction.response.send_message("⚠️ Pas de session.", ephemeral=True)
        if active["status"]=="paused": return await interaction.response.send_message("⚠️ Déjà en pause.", ephemeral=True)
        cur = now()
        conn.execute("INSERT INTO pauses (session_id,start_time) VALUES (?,?)", (active["id"], cur.isoformat()))
        conn.execute("UPDATE work_sessions SET status='paused' WHERE id=?", (active["id"],)); conn.commit()
        e = discord.Embed(title="⏸️ Pause", description=f"**{interaction.user.display_name}** — {cur.strftime('%H:%M')}", color=0xF39C12)
        await interaction.response.send_message(embed=e)
    finally: conn.close()

@bot.tree.command(name="resume", description="▶️ Reprendre")
async def cmd_resume(interaction: discord.Interaction):
    conn = get_db()
    try:
        uid = str(interaction.user.id); active = get_active_session(conn, uid)
        if not active or active["status"]!="paused": return await interaction.response.send_message("⚠️ Pas en pause.", ephemeral=True)
        ap = get_active_pause(conn, active["id"])
        if ap:
            pd = (now()-datetime.fromisoformat(ap["start_time"])).total_seconds()/60
            conn.execute("UPDATE pauses SET end_time=? WHERE id=?", (now().isoformat(), ap["id"]))
            conn.execute("UPDATE work_sessions SET total_pause_minutes=total_pause_minutes+?, status='working' WHERE id=?", (pd, active["id"]))
            conn.commit()
            e = discord.Embed(title="▶️ Reprise !", description=f"**{interaction.user.display_name}** — pause: **{fmt(pd)}**", color=0x2ECC71)
            await interaction.response.send_message(embed=e)
        else:
            conn.execute("UPDATE work_sessions SET status='working' WHERE id=?", (active["id"],)); conn.commit()
            await interaction.response.send_message("▶️ Reprise !", ephemeral=True)
    finally: conn.close()

@bot.tree.command(name="off", description="🏖️ Jour off")
@app_commands.describe(raison="Raison", date="Date (ex: 25/02/2026 ou 2026-02-25)")
async def cmd_off(interaction: discord.Interaction, raison: str="Jour off", date: Optional[str]=None):
    conn = get_db()
    try:
        uid, name = str(interaction.user.id), interaction.user.display_name
        td = parse_date(date) if date else today_str()
        if not td: return await interaction.response.send_message("⚠️ Format date: `25/02/2026` ou `2026-02-25`", ephemeral=True)
        if conn.execute("SELECT * FROM days_off WHERE user_id=? AND date=?", (uid,td)).fetchone():
            return await interaction.response.send_message(f"⚠️ Déjà off le {td}.", ephemeral=True)
        if conn.execute("SELECT * FROM work_sessions WHERE user_id=? AND date=? AND status IN ('working','paused')", (uid,td)).fetchone():
            return await interaction.response.send_message("⚠️ Session en cours, `/stop` d'abord.", ephemeral=True)
        conn.execute("INSERT INTO days_off (user_id,username,date,reason) VALUES (?,?,?,?)", (uid,name,td,raison)); conn.commit()
        e = discord.Embed(title="🏖️ Off", description=f"**{name}** off le **{td}**\n{raison}", color=0x9B59B6)
        await interaction.response.send_message(embed=e)
    finally: conn.close()

@bot.tree.command(name="myschedule", description="🕐 Tes horaires + timezone")
@app_commands.describe(debut="Heure de début (ex: 9)", fin="Heure de fin (ex: 18)", timezone="Ton timezone (ex: EST, CET, PST)")
@app_commands.autocomplete(timezone=tz_autocomplete)
async def cmd_myschedule(interaction: discord.Interaction, debut: int, fin: int, timezone: str):
    if debut<0 or debut>23 or fin<0 or fin>23:
        return await interaction.response.send_message("⚠️ Heures entre 0 et 23.", ephemeral=True)
    tz_upper = timezone.upper()
    if tz_upper not in TZ_MAP:
        tz_list = ", ".join(sorted(TZ_MAP.keys()))
        return await interaction.response.send_message(f"⚠️ Timezone inconnu. Disponibles: {tz_list}", ephemeral=True)
    conn = get_db()
    try:
        uid = str(interaction.user.id)
        conn.execute(
            "INSERT INTO user_schedules (user_id,start_hour,end_hour,tz,updated_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET start_hour=?,end_hour=?,tz=?,updated_at=?",
            (uid, debut, fin, tz_upper, now().isoformat(), debut, fin, tz_upper, now().isoformat()))
        conn.commit()
        offset = tz_offset(tz_upper)
        work_days = get_work_days(conn, uid)
        days_txt = ", ".join(JOURS_NAMES[d] for d in work_days)
        e = discord.Embed(
            title="🕐 Horaires mis à jour",
            description=(
                f"**{interaction.user.display_name}**\n"
                f"📅 {debut}h → {fin}h ({tz_upper}, UTC{offset:+d})\n"
                f"📆 Jours: {days_txt}\n"
                f"⏰ Rappel à **{debut+1}h {tz_upper}** si pas pointé."
            ),
            color=0x3498DB)
        await interaction.response.send_message(embed=e, ephemeral=True)
    finally: conn.close()

@bot.tree.command(name="mydays", description="📆 Tes jours de travail")
@app_commands.describe(jours="Jours séparés par des virgules (ex: lundi,mardi,mercredi,jeudi)")
async def cmd_mydays(interaction: discord.Interaction, jours: str):
    days = parse_days(jours)
    if not days:
        return await interaction.response.send_message(
            "⚠️ Format: `lundi,mardi,mercredi,jeudi,vendredi`\nJours valides: lundi, mardi, mercredi, jeudi, vendredi, samedi, dimanche",
            ephemeral=True)
    days_str = ",".join(str(d) for d in days)
    conn = get_db()
    try:
        uid = str(interaction.user.id)
        existing = conn.execute("SELECT * FROM user_schedules WHERE user_id=?", (uid,)).fetchone()
        if existing:
            conn.execute("UPDATE user_schedules SET work_days=?, updated_at=? WHERE user_id=?",
                         (days_str, now().isoformat(), uid))
        else:
            conn.execute("INSERT INTO user_schedules (user_id,start_hour,end_hour,tz,work_days,updated_at) VALUES (?,?,?,?,?,?)",
                         (uid, DEFAULT_SCHEDULE_START, DEFAULT_SCHEDULE_END, DEFAULT_TIMEZONE, days_str, now().isoformat()))
        conn.commit()
        days_txt = ", ".join(JOURS_NAMES[d] for d in days)
        sched_start, sched_end, user_tz, _ = get_schedule(conn, uid)
        e = discord.Embed(
            title="📆 Jours de travail mis à jour",
            description=(
                f"**{interaction.user.display_name}**\n"
                f"📆 **{days_txt}** ({len(days)}j/semaine)\n"
                f"🕐 {sched_start}h → {sched_end}h ({user_tz})\n"
                f"Le bot ne t'embêtera pas les autres jours ✌️"
            ),
            color=0x3498DB)
        await interaction.response.send_message(embed=e, ephemeral=True)
    finally: conn.close()

@bot.tree.command(name="mychannel", description="📌 Lier ton canal progress")
@app_commands.describe(canal="Le canal à lier (ou laisse vide pour utiliser le canal actuel)")
async def cmd_mychannel(interaction: discord.Interaction, canal: discord.TextChannel = None):
    target = canal or interaction.channel
    if not target.name.lower().endswith(PROGRESS_CHANNEL_SUFFIX):
        return await interaction.response.send_message(f"⚠️ Ce canal ne se termine pas par `{PROGRESS_CHANNEL_SUFFIX}`. Utilise un canal progress !", ephemeral=True)
    save_user_channel(interaction.user.id, target.id)
    e = discord.Embed(title="📌 Canal progress lié !", description=f"**{interaction.user.display_name}** → {target.mention}\n\nTous les rappels iront dans ce canal.", color=0x3498DB)
    await interaction.response.send_message(embed=e, ephemeral=True)

@bot.tree.command(name="mylunch", description="🍽️ Configurer ta pause déjeuner")
@app_commands.describe(minutes="Durée en minutes (défaut: 60). Met 0 pour désactiver la déduction auto.")
async def cmd_mylunch(interaction: discord.Interaction, minutes: int = 60):
    if minutes < 0 or minutes > 180:
        return await interaction.response.send_message("⚠️ Entre 0 et 180 minutes.", ephemeral=True)
    uid = str(interaction.user.id)
    conn = get_db()
    try:
        existing = conn.execute("SELECT user_id FROM user_schedules WHERE user_id=?", (uid,)).fetchone()
        if existing:
            conn.execute("UPDATE user_schedules SET lunch_minutes=?, updated_at=? WHERE user_id=?",
                         (minutes, now().isoformat(), uid))
        else:
            conn.execute("INSERT INTO user_schedules (user_id, lunch_minutes) VALUES (?,?)", (uid, minutes))
        conn.commit()
        if minutes == 0:
            desc = "Déduction auto désactivée. Pense à faire `/pause` manuellement !"
        else:
            desc = f"**{minutes}min** de lunch auto-déduit si ta session > 6h et que tu n'as pas fait `/pause` >= 30min."
        e = discord.Embed(title="🍽️ Pause déjeuner configurée !", description=desc, color=0xF39C12)
        await interaction.response.send_message(embed=e, ephemeral=True)
    finally: conn.close()

@bot.tree.command(name="conge", description="🏖️ Demander un congé")
@app_commands.describe(debut="Date début (ex: 25/02/2026 ou 2026-02-25)", fin="Date fin (même jour si 1 jour)", raison="Raison")
async def cmd_conge(interaction: discord.Interaction, debut: str, fin: str, raison: str="Congé"):
    conn = get_db()
    try:
        uid, name = str(interaction.user.id), interaction.user.display_name
        debut = parse_date(debut); fin = parse_date(fin)
        if not debut or not fin:
            return await interaction.response.send_message("⚠️ Format date: `25/02/2026` ou `2026-02-25`", ephemeral=True)
        d1 = datetime.strptime(debut, "%Y-%m-%d"); d2 = datetime.strptime(fin, "%Y-%m-%d")
        if d2 < d1:
            return await interaction.response.send_message("⚠️ La fin doit être après le début.", ephemeral=True)
        # Pas dans le passé (mais aujourd'hui OK)
        today = datetime.strptime(today_str(), "%Y-%m-%d")
        if d1 < today:
            return await interaction.response.send_message("⚠️ Pas de congé dans le passé ! Utilise `/edit` pour corriger des jours passés.", ephemeral=True)
        # Vérifier doublon
        existing = conn.execute("SELECT * FROM leave_requests WHERE user_id=? AND start_date=? AND end_date=? AND status='pending'", (uid, debut, fin)).fetchone()
        if existing:
            return await interaction.response.send_message("⚠️ Tu as déjà une demande en attente pour ces dates.", ephemeral=True)
        nb_days = (d2 - d1).days + 1
        conn.execute("INSERT INTO leave_requests (user_id,username,start_date,end_date,reason) VALUES (?,?,?,?,?)",
                     (uid, name, debut, fin, raison)); conn.commit()
        rid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        e = discord.Embed(title="🏖️ Congé demandé", description=f"Demande **#{rid}**", color=0x9B59B6)
        e.add_field(name="Dates", value=f"**{debut}** → **{fin}** ({nb_days} jour{'s' if nb_days>1 else ''})", inline=True)
        e.add_field(name="Raison", value=raison, inline=True)
        e.set_footer(text="En attente de validation admin")
        await interaction.response.send_message(embed=e, ephemeral=True)
        # Notifier dans #time-tracking-admin (ou #time-tracking si pas de canal admin)
        for g in bot.guilds:
            ch = get_admin_channel(g) or discord.utils.get(g.text_channels, name=SUMMARY_CHANNEL_NAME)
            if ch:
                admin_role = discord.utils.get(g.roles, name=ADMIN_ROLE_NAME)
                admin_ping = admin_role.mention if admin_role else "@Admin"
                ae = discord.Embed(title=f"🏖️ Demande de congé #{rid}", description=f"**{name}**", color=0x9B59B6)
                ae.add_field(name="Dates", value=f"**{debut}** → **{fin}** ({nb_days}j)", inline=True)
                ae.add_field(name="Raison", value=raison, inline=True)
                ae.set_footer(text=f"Congé #{rid} — ✅ pour approuver, ❌ pour refuser")
                msg = await ch.send(f"{admin_ping} — Nouvelle demande de congé :", embed=ae)
                await msg.add_reaction("✅")  # Pour admin: approuver
                await msg.add_reaction("❌")  # Pour admin: refuser
    finally: conn.close()

@bot.tree.command(name="edit", description="✏️ Correction d'heures")
@app_commands.describe(date="Date (ex: 25/02/2026 ou 2026-02-25)", debut="Début HH:MM", fin="Fin HH:MM", raison="Raison")
async def cmd_edit(interaction: discord.Interaction, date: str, debut: str, fin: str, raison: str):
    conn = get_db()
    try:
        uid, name = str(interaction.user.id), interaction.user.display_name
        date = parse_date(date)
        if not date: return await interaction.response.send_message("⚠️ Format date: `25/02/2026` ou `2026-02-25`", ephemeral=True)
        try: datetime.strptime(debut, "%H:%M"); datetime.strptime(fin, "%H:%M")
        except: return await interaction.response.send_message("⚠️ Format heure: HH:MM", ephemeral=True)
        if conn.execute("SELECT * FROM edit_requests WHERE user_id=? AND target_date=? AND status='pending'", (uid,date)).fetchone():
            return await interaction.response.send_message(f"⚠️ Demande déjà en attente pour {date}.", ephemeral=True)
        conn.execute("INSERT INTO edit_requests (user_id,username,target_date,new_start,new_end,reason) VALUES (?,?,?,?,?,?)",
                     (uid,name,date,f"{date}T{debut}:00",f"{date}T{fin}:00",raison)); conn.commit()
        rid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        e = discord.Embed(title="✏️ Correction envoyée", description=f"Demande **#{rid}**", color=0xF39C12)
        e.add_field(name="Date", value=date, inline=True); e.add_field(name="Heures", value=f"{debut}→{fin}", inline=True)
        e.add_field(name="Raison", value=raison, inline=False)
        await interaction.response.send_message(embed=e, ephemeral=True)
        for g in bot.guilds:
            ch = get_admin_channel(g) or discord.utils.get(g.text_channels, name=SUMMARY_CHANNEL_NAME)
            if ch:
                ae = discord.Embed(title=f"✏️ Correction #{rid}", description=f"**{name}**", color=0xF39C12)
                ae.add_field(name="Date", value=date, inline=True); ae.add_field(name="Heures", value=f"{debut}→{fin}", inline=True)
                ae.add_field(name="Raison", value=raison, inline=False); ae.set_footer(text="/pending → /approve ou /reject")
                await ch.send(embed=ae)
    finally: conn.close()

@bot.tree.command(name="status", description="📊 Ton statut")
async def cmd_status(interaction: discord.Interaction):
    conn = get_db()
    try:
        uid, name = str(interaction.user.id), interaction.user.display_name
        off = conn.execute("SELECT * FROM days_off WHERE user_id=? AND date=?", (uid,today_str())).fetchone()
        if off:
            return await interaction.response.send_message(embed=discord.Embed(title=f"📊 {name}", description=f"🏖️ Off — {off['reason']}", color=0x9B59B6), ephemeral=True)
        active = get_active_session(conn, uid)
        if not active:
            done = conn.execute("SELECT * FROM work_sessions WHERE user_id=? AND date=? AND status='done'", (uid,today_str())).fetchone()
            desc = f"✅ Terminé — **{fmt(calc_mins(done))}**" if done else "⬜ Pas commencé"
            return await interaction.response.send_message(embed=discord.Embed(title=f"📊 {name}", description=desc, color=0x95A5A6), ephemeral=True)
        wm = calc_mins(active)
        st = "⏸️ En pause" if active["status"]=="paused" else "🟢 Au travail"
        e = discord.Embed(title=f"📊 {name}", description=st, color=0xF39C12 if active["status"]=="paused" else 0x2ECC71)
        e.add_field(name="Depuis", value=datetime.fromisoformat(active["start_time"]).strftime("%H:%M"), inline=True)
        e.add_field(name="Pauses", value=fmt(active["total_pause_minutes"]), inline=True)
        e.add_field(name="Travaillé", value=f"**{fmt(wm)}**", inline=False)
        await interaction.response.send_message(embed=e, ephemeral=True)
    finally: conn.close()

@bot.tree.command(name="myreport", description="📈 Tes heures du mois")
@app_commands.describe(mois="Mois (1-12)", annee="Année")
async def cmd_myreport(interaction: discord.Interaction, mois: Optional[int]=None, annee: Optional[int]=None):
    cur=now(); month=mois or cur.month; year=annee or cur.year
    uid, name = str(interaction.user.id), interaction.user.display_name
    conn = get_db()
    try:
        dp = f"{year}-{month:02d}"
        sessions = conn.execute("SELECT * FROM work_sessions WHERE user_id=? AND date LIKE ? AND status='done' ORDER BY date", (uid,f"{dp}%")).fetchall()
        offs = conn.execute("SELECT * FROM days_off WHERE user_id=? AND date LIKE ? ORDER BY date", (uid,f"{dp}%")).fetchall()
        total_mins=0; lines=[]
        for s in sessions:
            wm=calc_mins(s); total_mins+=wm; d=datetime.strptime(s["date"],"%Y-%m-%d").strftime("%d/%m")
            lines.append(f"`{d}` {datetime.fromisoformat(s['start_time']).strftime('%H:%M')}→{datetime.fromisoformat(s['end_time']).strftime('%H:%M')} **{fmt(wm)}** (pause:{fmt(s['total_pause_minutes'])})")
        actives = conn.execute("SELECT * FROM work_sessions WHERE user_id=? AND date LIKE ? AND status IN ('working','paused')", (uid,f"{dp}%")).fetchall()
        for s in actives:
            wm=calc_mins(s); total_mins+=wm; d=datetime.strptime(s["date"],"%Y-%m-%d").strftime("%d/%m")
            lines.append(f"`{d}` {datetime.fromisoformat(s['start_time']).strftime('%H:%M')}→en cours **{fmt(wm)}** ⏳")
        rate, cs = get_effective_rate(conn, uid, interaction.user if interaction.guild else None); th = total_mins/60
        e = discord.Embed(title=f"📈 {name} — {MOIS_FR[month]} {year}", color=0x3498DB)
        if lines:
            chunk = "\n".join(lines)
            if len(chunk)>1024:
                mid=len(lines)//2
                e.add_field(name="Journées (1/2)", value="\n".join(lines[:mid]), inline=False)
                e.add_field(name="Journées (2/2)", value="\n".join(lines[mid:]), inline=False)
            else: e.add_field(name="Journées", value=chunk, inline=False)
        if offs: e.add_field(name=f"Off ({len(offs)})", value="\n".join(f"`{datetime.strptime(o['date'],'%Y-%m-%d').strftime('%d/%m')}` 🏖️ {o['reason']}" for o in offs), inline=False)
        txt = f"**{fmt(total_mins)}** ({th:.2f}h) | {len(sessions)+len(actives)} jours | {len(offs)} off"
        if rate>0: txt += f"\n💰 **{th*rate:.2f}{cs}** ({rate:.2f}{cs}/h)"
        e.add_field(name="📊 Total", value=txt, inline=False)
        await interaction.response.send_message(embed=e, ephemeral=True)
    finally: conn.close()

@bot.tree.command(name="mydailies", description="📖 Tes dailies du mois")
@app_commands.describe(mois="Mois (1-12)", annee="Année")
async def cmd_mydailies(interaction: discord.Interaction, mois: Optional[int]=None, annee: Optional[int]=None):
    cur=now(); month=mois or cur.month; year=annee or cur.year
    uid, name = str(interaction.user.id), interaction.user.display_name
    conn = get_db()
    try:
        dp = f"{year}-{month:02d}"
        dailies = conn.execute("SELECT * FROM dailies WHERE user_id=? AND date LIKE ? ORDER BY date", (uid,f"{dp}%")).fetchall()
        wdates = {w["date"] for w in conn.execute("SELECT DISTINCT date FROM work_sessions WHERE user_id=? AND date LIKE ?", (uid,f"{dp}%")).fetchall()}
        ddates = {d["date"] for d in dailies}; missing=sorted(wdates-ddates)
        e = discord.Embed(title=f"📖 Dailies — {MOIS_FR[month]} {year}", description=f"**{name}**", color=0x1ABC9C)
        if dailies:
            for d in dailies:
                day=datetime.strptime(d["date"],"%Y-%m-%d").strftime("%d/%m")
                val = d["message"][:100]+("..." if len(d["message"])>100 else "")
                if d["message_url"]: val += f"\n[📎 Voir le post]({d['message_url']})"
                e.add_field(name=f"📝 {day}", value=val, inline=False)
        else: e.add_field(name="—", value="Aucun daily.", inline=False)
        if missing: e.add_field(name=f"⚠️ Manquants ({len(missing)})", value=", ".join(datetime.strptime(d,"%Y-%m-%d").strftime("%d/%m") for d in missing), inline=False)
        e.set_footer(text=f"📊 {len(dailies)}/{len(wdates)} dailies")
        await interaction.response.send_message(embed=e, ephemeral=True)
    finally: conn.close()

# ═══════════════════ ADMIN COMMANDS ══════════════════════════════════════════

@bot.tree.command(name="who", description="👀 Qui est en ligne ?")
async def cmd_who(interaction: discord.Interaction):
    conn = get_db()
    try:
        date = today_str()
        sessions = conn.execute("SELECT * FROM work_sessions WHERE date=? AND status IN ('working','paused') ORDER BY username", (date,)).fetchall()
        if not sessions:
            return await interaction.response.send_message("🏜️ Personne n'est en ligne en ce moment !", ephemeral=True)
        dept_map = build_dept_map(interaction.guild) if interaction.guild else {}
        role_map = build_role_map(interaction.guild) if interaction.guild else {}
        lines = []
        for s in sessions:
            st = datetime.fromisoformat(s["start_time"]).strftime("%H:%M")
            wm = calc_mins(s)
            icon = "⏸️" if s["status"]=="paused" else "🟢"
            dept = dept_map.get(s["user_id"], "")
            dept_tag = f" `{dept}`" if dept else ""
            role = role_map.get(s["user_id"], "")
            role_tag = f" {get_role_info(role)['emoji']}" if role else ""
            lines.append(f"{icon} **{s['username']}**{role_tag}{dept_tag} depuis {st} ({fmt(wm)})")
        e = discord.Embed(title=f"👀 En ligne ({len(sessions)})", description="\n".join(lines), color=0x2ECC71)
        await interaction.response.send_message(embed=e, ephemeral=True)
    finally: conn.close()

@bot.tree.command(name="today", description="📋 Résumé du jour")
@app_commands.describe(departement="Département")
@app_commands.autocomplete(departement=dept_autocomplete)
async def cmd_today(interaction: discord.Interaction, departement: Optional[str]=None):
    conn = get_db()
    try:
        date = today_str()
        sessions = conn.execute("SELECT * FROM work_sessions WHERE date=? ORDER BY username", (date,)).fetchall()
        offs = conn.execute("SELECT * FROM days_off WHERE date=? ORDER BY username", (date,)).fetchall()
        dailies = conn.execute("SELECT * FROM dailies WHERE date=? ORDER BY username", (date,)).fetchall()
        dept_map = build_dept_map(interaction.guild) if interaction.guild else {}
        role_map = build_role_map(interaction.guild) if interaction.guild else {}
        members = get_dept_members(interaction.guild, departement) if interaction.guild else []
        m_ids = {str(m.id) for m in members} if departement else None
        if m_ids is not None:
            sessions=[s for s in sessions if s["user_id"] in m_ids]
            offs=[o for o in offs if o["user_id"] in m_ids]
            dailies=[d for d in dailies if d["user_id"] in m_ids]
        s_ids={s["user_id"] for s in sessions}; o_ids={o["user_id"] for o in offs}
        not_started=[m for m in members if str(m.id) not in s_ids and str(m.id) not in o_ids] if members else []
        dept_label = f" — {departement}" if departement else ""
        e = discord.Embed(title=f"📋 {date}{dept_label}", color=0x3498DB)
        if sessions:
            lines=[]
            for s in sessions:
                lunch_m=get_lunch_minutes(conn,s["user_id"])
                wm=calc_paid_mins(s,lunch_m); st=datetime.fromisoformat(s["start_time"]).strftime("%H:%M")
                en=datetime.fromisoformat(s["end_time"]).strftime("%H:%M") if s["end_time"] else "en cours"
                icon="⏸️" if s["status"]=="paused" else ("✅" if s["status"]=="done" else "🟢")
                dept=dept_map.get(s["user_id"],""); dept_tag=f" `{dept}`" if dept and not departement else ""
                role=role_map.get(s["user_id"],""); role_e=f"{get_role_info(role)['emoji']}" if role else ""
                ot=calc_overtime_mins(s,lunch_m); ot_tag=f" (+{fmt(ot)}sup)" if ot > 0 else ""
                lines.append(f"{icon} {role_e}**{s['username']}**{dept_tag} {st}→{en} **{fmt(wm)}**{ot_tag}")
            e.add_field(name="💼 Travail", value="\n".join(lines), inline=False)
        if offs: e.add_field(name="🏖️ Off", value="\n".join(f"**{o['username']}**—{o['reason']}" for o in offs), inline=False)
        if not_started: e.add_field(name="⬜ Pas pointé", value=", ".join(f"**{a.display_name}**" for a in not_started), inline=False)
        if dailies:
            dl_lines=[]
            for d in dailies:
                link = f" [📎]({d['message_url']})" if d["message_url"] else ""
                dl_lines.append(f"**{d['username']}** — {d['message'][:60]}{'...' if len(d['message'])>60 else ''}{link}")
            e.add_field(name="📝 Dailies", value="\n".join(dl_lines), inline=False)
        d_ids={d["user_id"] for d in dailies}
        miss=list(dict.fromkeys(s["username"] for s in sessions if s["user_id"] not in d_ids and s["user_id"] not in o_ids))
        if miss: e.add_field(name="⚠️ Dailies manquants", value=", ".join(f"**{n}**" for n in miss), inline=False)
        if not sessions and not offs and not not_started: e.description = "Aucune activité."
        await interaction.response.send_message(embed=e)
    finally: conn.close()

@bot.tree.command(name="dailies", description="📋 Dailies + manquants")
@app_commands.describe(departement="Département", date="Date (ex: 25/02/2026)")
@app_commands.autocomplete(departement=dept_autocomplete)
async def cmd_dailies(interaction: discord.Interaction, departement: Optional[str]=None, date: Optional[str]=None):
    if not is_admin(interaction): return await interaction.response.send_message("⛔ Admin.", ephemeral=True)
    conn = get_db()
    try:
        td = parse_date(date) if date else today_str()
        if not td: return await interaction.response.send_message("⚠️ Format date: `25/02/2026` ou `2026-02-25`", ephemeral=True)
        dailies = conn.execute("SELECT * FROM dailies WHERE date=? ORDER BY username", (td,)).fetchall()
        workers = conn.execute("SELECT DISTINCT user_id, username FROM work_sessions WHERE date=?", (td,)).fetchall()
        offs = conn.execute("SELECT user_id, username FROM days_off WHERE date=?", (td,)).fetchall()
        members = get_dept_members(interaction.guild, departement) if interaction.guild else []
        m_ids = {str(m.id) for m in members} if departement else None
        if m_ids is not None:
            dailies=[d for d in dailies if d["user_id"] in m_ids]
            workers=[w for w in workers if w["user_id"] in m_ids]
            offs=[o for o in offs if o["user_id"] in m_ids]
        w_ids={w["user_id"] for w in workers}; o_ids={o["user_id"] for o in offs}; d_ids={d["user_id"] for d in dailies}
        dept_label = f" — {departement}" if departement else ""
        e = discord.Embed(title=f"📋 Dailies {td}{dept_label}", color=0x1ABC9C)
        if dailies:
            for d in dailies:
                val = d["message"][:150]+("..." if len(d["message"])>150 else "")
                if d["message_url"]: val += f"\n[📎 Voir le post]({d['message_url']})"
                e.add_field(name=f"✅ {d['username']}", value=val, inline=False)
        else: e.add_field(name="—", value="Aucun daily.", inline=False)
        miss_w=[w for w in workers if w["user_id"] not in d_ids and w["user_id"] not in o_ids]
        if miss_w: e.add_field(name=f"⚠️ Manquants ({len(miss_w)})", value="\n".join(f"❌ **{m['username']}**" for m in miss_w), inline=False)
        absent=[m for m in members if str(m.id) not in w_ids and str(m.id) not in o_ids] if members else []
        if absent: e.add_field(name=f"👻 Pas pointé ({len(absent)})", value="\n".join(f"⬜ **{a.display_name}**" for a in absent), inline=False)
        if offs: e.add_field(name="🏖️ Off", value=", ".join(o["username"] for o in offs), inline=False)
        if len(workers)>0: e.set_footer(text=f"📊 {len(dailies)}/{len(workers)} dailies ({int(len(dailies)/len(workers)*100)}%)")
        await interaction.response.send_message(embed=e)
    finally: conn.close()

@bot.tree.command(name="setrate", description="💰 Taux horaire (admin)")
@app_commands.describe(artiste="Artiste", taux="Taux horaire", devise="Devise")
async def cmd_setrate(interaction: discord.Interaction, artiste: discord.Member, taux: float, devise: str="$"):
    if not is_admin(interaction): return await interaction.response.send_message("⛔ Admin.", ephemeral=True)
    conn = get_db()
    try:
        conn.execute("INSERT INTO hourly_rates (user_id,username,rate,currency,updated_at) VALUES (?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET rate=?,currency=?,username=?,updated_at=?",
                     (str(artiste.id),artiste.display_name,taux,devise,now().isoformat(),taux,devise,artiste.display_name,now().isoformat())); conn.commit()
        await interaction.response.send_message(embed=discord.Embed(title="💰 OK", description=f"**{artiste.display_name}** → **{taux:.2f}{devise}/h**", color=0x2ECC71), ephemeral=True)
    finally: conn.close()

@bot.tree.command(name="rates", description="💰 Taux horaires (admin)")
async def cmd_rates(interaction: discord.Interaction):
    if not is_admin(interaction): return await interaction.response.send_message("⛔ Admin.", ephemeral=True)
    conn = get_db()
    try:
        rates = conn.execute("SELECT * FROM hourly_rates ORDER BY username").fetchall()
        e = discord.Embed(title="💰 Taux horaires", color=0xE67E22)
        if not rates: e.description = "Aucun. `/setrate`"
        else:
            for r in rates: e.add_field(name=f"👤 {r['username']}", value=f"**{r['rate']:.2f}{r['currency']}/h**", inline=True)
        await interaction.response.send_message(embed=e, ephemeral=True)
    finally: conn.close()

@bot.tree.command(name="pending", description="📋 Corrections en attente (admin)")
async def cmd_pending(interaction: discord.Interaction):
    if not is_admin(interaction): return await interaction.response.send_message("⛔ Admin.", ephemeral=True)
    conn = get_db()
    try:
        reqs = conn.execute("SELECT * FROM edit_requests WHERE status='pending' ORDER BY created_at").fetchall()
        if not reqs: return await interaction.response.send_message("✅ Rien en attente.", ephemeral=True)
        e = discord.Embed(title=f"📋 Corrections ({len(reqs)})", color=0xF39C12)
        for r in reqs:
            st=datetime.fromisoformat(r["new_start"]).strftime("%H:%M"); en=datetime.fromisoformat(r["new_end"]).strftime("%H:%M")
            e.add_field(name=f"#{r['id']}—{r['username']} ({r['target_date']})", value=f"{st}→{en}\n{r['reason']}\n`/approve {r['id']}` · `/reject {r['id']}`", inline=False)
        await interaction.response.send_message(embed=e, ephemeral=True)
    finally: conn.close()

@bot.tree.command(name="approve", description="✅ Approuver correction (admin)")
@app_commands.describe(id="Numéro")
async def cmd_approve(interaction: discord.Interaction, id: int):
    if not is_admin(interaction): return await interaction.response.send_message("⛔ Admin.", ephemeral=True)
    conn = get_db()
    try:
        req = conn.execute("SELECT * FROM edit_requests WHERE id=? AND status='pending'", (id,)).fetchone()
        if not req: return await interaction.response.send_message(f"⚠️ #{id} introuvable.", ephemeral=True)
        old = conn.execute("SELECT id FROM work_sessions WHERE user_id=? AND date=?", (req["user_id"],req["target_date"])).fetchone()
        if old:
            conn.execute("DELETE FROM pauses WHERE session_id=?", (old["id"],))
            conn.execute("DELETE FROM work_sessions WHERE id=?", (old["id"],))
        conn.execute("INSERT INTO work_sessions (user_id,username,date,start_time,end_time,total_pause_minutes,status) VALUES (?,?,?,?,?,0,'done')",
                     (req["user_id"],req["username"],req["target_date"],req["new_start"],req["new_end"]))
        conn.execute("UPDATE edit_requests SET status='approved', reviewed_by=?, reviewed_at=? WHERE id=?",
                     (interaction.user.display_name,now().isoformat(),id)); conn.commit()
        st=datetime.fromisoformat(req["new_start"]).strftime("%H:%M"); en=datetime.fromisoformat(req["new_end"]).strftime("%H:%M")
        wm=(datetime.fromisoformat(req["new_end"])-datetime.fromisoformat(req["new_start"])).total_seconds()/60
        e = discord.Embed(title=f"✅ #{id} approuvée", description=f"**{req['username']}** {req['target_date']}\n{st}→{en} (**{fmt(wm)}**)", color=0x2ECC71)
        await interaction.response.send_message(embed=e)
        try:
            m = interaction.guild.get_member(int(req["user_id"]))
            if m:
                ch = find_progress_channel(interaction.guild, m)
                if ch: await ch.send(f"✅ {m.mention} — Correction du **{req['target_date']}** approuvée ! {st}→{en}")
        except: pass
    finally: conn.close()

@bot.tree.command(name="reject", description="❌ Rejeter correction (admin)")
@app_commands.describe(id="Numéro", raison="Raison")
async def cmd_reject(interaction: discord.Interaction, id: int, raison: str=""):
    if not is_admin(interaction): return await interaction.response.send_message("⛔ Admin.", ephemeral=True)
    conn = get_db()
    try:
        req = conn.execute("SELECT * FROM edit_requests WHERE id=? AND status='pending'", (id,)).fetchone()
        if not req: return await interaction.response.send_message(f"⚠️ #{id} introuvable.", ephemeral=True)
        conn.execute("UPDATE edit_requests SET status='rejected', reviewed_by=?, reviewed_at=? WHERE id=?",
                     (interaction.user.display_name,now().isoformat(),id)); conn.commit()
        e = discord.Embed(title=f"❌ #{id} rejetée", description=f"**{req['username']}** {req['target_date']}\n{raison}", color=0xE74C3C)
        await interaction.response.send_message(embed=e)
        try:
            m = interaction.guild.get_member(int(req["user_id"]))
            if m:
                ch = find_progress_channel(interaction.guild, m)
                if ch: await ch.send(f"❌ {m.mention} — Correction du **{req['target_date']}** rejetée. {raison}")
        except: pass
    finally: conn.close()

@bot.tree.command(name="summary", description="📊 Résumé mensuel")
@app_commands.describe(mois="Mois (1-12)", annee="Année", departement="Département")
@app_commands.autocomplete(departement=dept_autocomplete)
async def cmd_summary(interaction: discord.Interaction, mois: Optional[int]=None, annee: Optional[int]=None, departement: Optional[str]=None):
    if not is_admin(interaction): return await interaction.response.send_message("⛔ Admin.", ephemeral=True)
    cur=now(); month=mois or cur.month; year=annee or cur.year
    conn = get_db()
    try:
        dp = f"{year}-{month:02d}"
        sessions = conn.execute("SELECT * FROM work_sessions WHERE date LIKE ? ORDER BY username,date", (f"{dp}%",)).fetchall()
        offs = conn.execute("SELECT * FROM days_off WHERE date LIKE ? ORDER BY username,date", (f"{dp}%",)).fetchall()
        dept_map = build_dept_map(interaction.guild) if interaction.guild else {}
        if departement and interaction.guild:
            m_ids = {str(m.id) for m in get_dept_members(interaction.guild, departement)}
            sessions=[s for s in sessions if s["user_id"] in m_ids]
            offs=[o for o in offs if o["user_id"] in m_ids]
        users={}
        for s in sessions:
            uid=s["user_id"]
            if uid not in users: users[uid]={"name":s["username"],"mins":0,"days":set(),"off":0,"dl":0,"dept":dept_map.get(uid,"?")}
            if s["status"]=="done": users[uid]["mins"]+=calc_mins(s); users[uid]["days"].add(s["date"])
        for o in offs:
            uid=o["user_id"]
            if uid not in users: users[uid]={"name":o["username"],"mins":0,"days":set(),"off":0,"dl":0,"dept":dept_map.get(uid,"?")}
            users[uid]["off"]+=1
        for dc in conn.execute("SELECT user_id, COUNT(*) as c FROM dailies WHERE date LIKE ? GROUP BY user_id", (f"{dp}%",)).fetchall():
            if dc["user_id"] in users: users[dc["user_id"]]["dl"]=dc["c"]
        dept_label = f" — {departement}" if departement else ""
        e = discord.Embed(title=f"📊 {MOIS_FR[month]} {year}{dept_label}", color=0xE67E22)
        if not users: e.add_field(name="—", value="Aucune activité.", inline=False)
        else:
            gp=0
            if not departement:
                by_dept={}
                for uid, u in users.items():
                    d=u["dept"]
                    if d not in by_dept: by_dept[d]=[]
                    by_dept[d].append((uid,u))
                for dn in sorted(by_dept.keys()):
                    du=by_dept[dn]; dl=[]; dth=0
                    for uid,u in sorted(du, key=lambda x:x[1]["name"].lower()):
                        m_obj=interaction.guild.get_member(int(uid)) if interaction.guild else None
                        th=u["mins"]/60; dw=len(u["days"]); rate,cs=get_effective_rate(conn,uid,m_obj); pay=th*rate; gp+=pay; dth+=th
                        dpct=f" ({int(u['dl']/dw*100)}%)" if dw>0 else ""
                        pt=f" · {pay:.0f}{cs}" if rate>0 else ""
                        role_e=get_role_info(get_member_studio_role(m_obj))["emoji"] if m_obj and get_member_studio_role(m_obj) else ""
                        dl.append(f"{role_e}**{u['name']}** — {fmt(u['mins'])} ({th:.1f}h){pt} · 📝{u['dl']}/{dw}{dpct}")
                    e.add_field(name=f"📂 {dn} ({len(du)}) — {dth:.1f}h", value="\n".join(dl), inline=False)
            else:
                for uid,u in sorted(users.items(), key=lambda x:x[1]["name"].lower()):
                    m_obj=interaction.guild.get_member(int(uid)) if interaction.guild else None
                    th=u["mins"]/60; dw=len(u["days"]); rate,cs=get_effective_rate(conn,uid,m_obj); pay=th*rate; gp+=pay
                    dpct=f" ({int(u['dl']/dw*100)}%)" if dw>0 else ""
                    pt=f"\n💰 **{pay:.2f}{cs}**" if rate>0 else ""
                    role_e=get_role_info(get_member_studio_role(m_obj))["emoji"]+" " if m_obj and get_member_studio_role(m_obj) else ""
                    e.add_field(name=f"{role_e}{u['name']}", value=f"🕐 **{fmt(u['mins'])}** ({th:.2f}h)\n📅 {dw}j | 🏖️ {u['off']} off\n📝 {u['dl']}/{dw}{dpct}{pt}", inline=True)
            if gp>0: e.set_footer(text=f"💰 Total: {gp:.2f}$")
        await interaction.response.send_message(embed=e)
    finally: conn.close()

@bot.tree.command(name="report", description="📑 Rapport TXT+CSV")
@app_commands.describe(mois="Mois (1-12)", annee="Année", departement="Département")
@app_commands.autocomplete(departement=dept_autocomplete)
async def cmd_report(interaction: discord.Interaction, mois: Optional[int]=None, annee: Optional[int]=None, departement: Optional[str]=None):
    if not is_admin(interaction): return await interaction.response.send_message("⛔ Admin.", ephemeral=True)
    await interaction.response.defer()
    cur=now(); month=mois or cur.month; year=annee or cur.year; dp=f"{year}-{month:02d}"
    conn = get_db()
    try:
        sessions = conn.execute("SELECT * FROM work_sessions WHERE date LIKE ? AND status='done' ORDER BY username,date", (f"{dp}%",)).fetchall()
        offs = conn.execute("SELECT * FROM days_off WHERE date LIKE ? ORDER BY username,date", (f"{dp}%",)).fetchall()
        dailies = conn.execute("SELECT * FROM dailies WHERE date LIKE ? ORDER BY user_id,date", (f"{dp}%",)).fetchall()
        dept_map = build_dept_map(interaction.guild) if interaction.guild else {}
        role_map = build_role_map(interaction.guild) if interaction.guild else {}
        if departement and interaction.guild:
            m_ids = {str(m.id) for m in get_dept_members(interaction.guild, departement)}
            sessions=[s for s in sessions if s["user_id"] in m_ids]
            offs=[o for o in offs if o["user_id"] in m_ids]
            dailies=[d for d in dailies if d["user_id"] in m_ids]
        ud={}
        for s in sessions:
            uid=s["user_id"]
            if uid not in ud: ud[uid]={"name":s["username"],"sess":[],"offs":[],"dl":[],"dept":dept_map.get(uid,"?")}
            ud[uid]["sess"].append(s)
        for o in offs:
            uid=o["user_id"]
            if uid not in ud: ud[uid]={"name":o["username"],"sess":[],"offs":[],"dl":[],"dept":dept_map.get(uid,"?")}
            ud[uid]["offs"].append(o)
        for dl in dailies:
            if dl["user_id"] in ud: ud[dl["user_id"]]["dl"].append(dl)
        dept_label = f" — {departement}" if departement else ""
        rpt=[f"{'='*60}",f"  RAPPORT — {MOIS_FR[month]} {year}{dept_label}",f"  {now().strftime('%Y-%m-%d %H:%M')}",f"{'='*60}",""]
        csv_rows=[]; gt=0; gp=0
        by_dept={}
        for uid,d in ud.items():
            dept=d["dept"]
            if dept not in by_dept: by_dept[dept]=[]
            by_dept[dept].append((uid,d))
        for dn in sorted(by_dept.keys()):
            rpt+=[f"  ══ {dn.upper()} ══",""]
            for uid,d in sorted(by_dept[dn], key=lambda x:x[1]["name"].lower()):
                m_obj=interaction.guild.get_member(int(uid)) if interaction.guild else None
                rate,cs=get_effective_rate(conn,uid,m_obj)
                role=role_map.get(uid,"")
                role_label=f"  [{role}]" if role else ""
                rpt+=[f"┌───────────────────────────────────",f"│ 👤 {d['name']}{role_label}  [{dn}]  ({rate:.2f}{cs}/h)",f"├───────────────────────────────────"]
                ut=0; wdates=set()
                for s in d["sess"]:
                    wm=calc_mins(s); ut+=wm; wdates.add(s["date"])
                    df=datetime.strptime(s["date"],"%Y-%m-%d").strftime("%d/%m/%Y")
                    st=datetime.fromisoformat(s["start_time"]).strftime("%H:%M")
                    en=datetime.fromisoformat(s["end_time"]).strftime("%H:%M")
                    hd=any(dl["date"]==s["date"] for dl in d["dl"])
                    dm_msg=next((dl["message"] for dl in d["dl"] if dl["date"]==s["date"]),"")
                    dm_url=next((dl["message_url"] for dl in d["dl"] if dl["date"]==s["date"]),"")
                    rpt.append(f"│  {df}  {st}→{en}  {fmt(wm)}  pause:{fmt(s['total_pause_minutes'])}  {'✅' if hd else '❌'}📝")
                    csv_rows.append({"Artiste":d["name"],"Rôle":role,"Département":dn,"Date":s["date"],"Début":st,"Fin":en,
                        "Pause (min)":round(s["total_pause_minutes"] or 0,1),"Heures":round(wm/60,2),
                        "Heures sup":round(max(0,wm/60-EXPECTED_WORK_HOURS),2),
                        "Taux":rate,"Montant":round(wm/60*rate,2),"Devise":cs,
                        "Daily":"Oui" if hd else "Non","Daily message":dm_msg,"Daily lien":dm_url,"Type":"Travail"})
                for o in d["offs"]:
                    df=datetime.strptime(o["date"],"%Y-%m-%d").strftime("%d/%m/%Y")
                    rpt.append(f"│  {df}  🏖️ OFF — {o['reason']}")
                    csv_rows.append({"Artiste":d["name"],"Rôle":role,"Département":dn,"Date":o["date"],"Début":"","Fin":"",
                        "Pause (min)":"","Heures":0,"Heures sup":0,
                        "Taux":rate,"Montant":0,"Devise":cs,
                        "Daily":"","Daily message":"","Daily lien":"","Type":f"Off - {o['reason']}"})
                if d["dl"]:
                    rpt+=["│","│  📝 DAILIES:"]
                    for dl in d["dl"]:
                        rpt.append(f"│    {datetime.strptime(dl['date'],'%Y-%m-%d').strftime('%d/%m')}: {dl['message'][:80]}{'...' if len(dl['message'])>80 else ''}")
                        if dl["message_url"]: rpt.append(f"│      ↳ {dl['message_url']}")
                th=ut/60; up=th*rate; gt+=ut; gp+=up
                wc=len(wdates); dc=len(d["dl"]); dpct=f" ({int(dc/wc*100)}%)" if wc>0 else ""
                rpt+=["│",f"│  TOTAL: {fmt(ut)} ({th:.2f}h)"]
                if rate>0: rpt.append(f"│  💰 À PAYER: {up:.2f}{cs}")
                rpt+=[f"│  {wc}j | {len(d['offs'])} off | {dc}/{wc} dailies{dpct}",f"└───────────────────────────────────",""]
        rpt+=[f"{'='*60}",f"  TOTAL: {fmt(gt)} ({gt/60:.2f}h)"]
        if gp>0: rpt.append(f"  💰 TOTAL: {gp:.2f}$")
        rpt.append(f"{'='*60}")
        suffix=f"_{departement}" if departement else ""
        txt_path=Path(__file__).parent/f"rapport_{MOIS_FR[month]}_{year}{suffix}.txt"
        txt_path.write_text("\n".join(rpt),encoding="utf-8")
        csv_path=Path(__file__).parent/f"rapport_{MOIS_FR[month]}_{year}{suffix}.csv"
        if csv_rows:
            with open(csv_path,"w",newline="",encoding="utf-8-sig") as f:
                w=csv.DictWriter(f,fieldnames=list(csv_rows[0].keys()),delimiter=";"); w.writeheader(); w.writerows(csv_rows)
        files=[discord.File(str(txt_path))]
        if csv_rows: files.append(discord.File(str(csv_path)))
        await interaction.followup.send(f"📑 **{MOIS_FR[month]} {year}{dept_label}** :", files=files)
        txt_path.unlink(missing_ok=True); csv_path.unlink(missing_ok=True)
    finally: conn.close()

@bot.tree.command(name="vacances", description="🏖️ Vacances collectives — tout le monde off (admin)")
@app_commands.describe(debut="Date début (ex: 25/02/2026)", fin="Date fin", raison="Raison")
async def cmd_vacances(interaction: discord.Interaction, debut: str, fin: str, raison: str="Vacances"):
    if not is_admin(interaction): return await interaction.response.send_message("⛔ Admin.", ephemeral=True)
    debut = parse_date(debut); fin = parse_date(fin)
    if not debut or not fin: return await interaction.response.send_message("⚠️ Format date: `25/02/2026` ou `2026-02-25`", ephemeral=True)
    d1 = datetime.strptime(debut, "%Y-%m-%d"); d2 = datetime.strptime(fin, "%Y-%m-%d")
    if d2 < d1: return await interaction.response.send_message("⚠️ La fin doit être après le début.", ephemeral=True)
    conn = get_db()
    try:
        days = []; current = d1
        while current <= d2:
            ds = current.strftime("%Y-%m-%d")
            conn.execute("INSERT OR REPLACE INTO collective_holidays (date,reason,created_by) VALUES (?,?,?)",
                         (ds, raison, interaction.user.display_name))
            days.append(ds)
            current += timedelta(days=1)
        # Créer les jours off pour tout le monde
        members = get_team_members(interaction.guild) if interaction.guild else []
        count = 0
        for m in members:
            uid = str(m.id)
            for ds in days:
                existing = conn.execute("SELECT id FROM days_off WHERE user_id=? AND date=?", (uid, ds)).fetchone()
                if not existing:
                    conn.execute("INSERT INTO days_off (user_id,username,date,reason) VALUES (?,?,?,?)",
                                 (uid, m.display_name, ds, f"🏖️ {raison}"))
                    count += 1
        conn.commit()
        nb_days = len(days)
        e = discord.Embed(title="🏖️ Vacances collectives", color=0x9B59B6)
        e.add_field(name="Période", value=f"**{debut}** → **{fin}** ({nb_days} jours)", inline=False)
        e.add_field(name="Raison", value=raison, inline=True)
        e.add_field(name="Artistes", value=f"{len(members)} membres mis off ({count} entrées créées)", inline=True)
        e.set_footer(text="Les artistes peuvent quand même /start s'ils veulent travailler.")
        await interaction.response.send_message(embed=e)
        # Notifier dans #time-tracking
        for g in bot.guilds:
            ch = discord.utils.get(g.text_channels, name=SUMMARY_CHANNEL_NAME)
            if ch and ch.id != interaction.channel_id:
                await ch.send(embed=e)
    finally: conn.close()

@bot.tree.command(name="cancelvacances", description="❌ Annuler des vacances collectives (admin)")
@app_commands.describe(debut="Date début (ex: 25/02/2026)", fin="Date fin")
async def cmd_cancelvacances(interaction: discord.Interaction, debut: str, fin: str):
    if not is_admin(interaction): return await interaction.response.send_message("⛔ Admin.", ephemeral=True)
    debut = parse_date(debut); fin = parse_date(fin)
    if not debut or not fin: return await interaction.response.send_message("⚠️ Format date: `25/02/2026` ou `2026-02-25`", ephemeral=True)
    d1 = datetime.strptime(debut, "%Y-%m-%d"); d2 = datetime.strptime(fin, "%Y-%m-%d")
    conn = get_db()
    try:
        current = d1; dates = []
        while current <= d2:
            dates.append(current.strftime("%Y-%m-%d")); current += timedelta(days=1)
        removed = 0
        for ds in dates:
            # Supprimer la holiday collective
            conn.execute("DELETE FROM collective_holidays WHERE date=?", (ds,))
            # Supprimer les jours off auto-créés (ceux avec "🏖️" dans la raison)
            r = conn.execute("DELETE FROM days_off WHERE date=? AND reason LIKE '🏖️%'", (ds,))
            removed += r.rowcount
        conn.commit()
        e = discord.Embed(title="❌ Vacances annulées", description=f"**{debut}** → **{fin}**\n{removed} jours off supprimés.", color=0xE74C3C)
        await interaction.response.send_message(embed=e)
    finally: conn.close()

@bot.tree.command(name="pendingconge", description="📋 Demandes de congé en attente (admin)")
async def cmd_pendingconge(interaction: discord.Interaction):
    if not is_admin(interaction): return await interaction.response.send_message("⛔ Admin.", ephemeral=True)
    conn = get_db()
    try:
        reqs = conn.execute("SELECT * FROM leave_requests WHERE status='pending' ORDER BY start_date").fetchall()
        if not reqs: return await interaction.response.send_message("✅ Aucune demande en attente.", ephemeral=True)
        e = discord.Embed(title=f"🏖️ Demandes de congé ({len(reqs)})", color=0x9B59B6)
        for r in reqs:
            nb = (datetime.strptime(r["end_date"],"%Y-%m-%d") - datetime.strptime(r["start_date"],"%Y-%m-%d")).days + 1
            e.add_field(name=f"#{r['id']} — {r['username']}", value=f"**{r['start_date']}** → **{r['end_date']}** ({nb}j)\n{r['reason']}\n`/approveconge {r['id']}` · `/rejectconge {r['id']}`", inline=False)
        await interaction.response.send_message(embed=e, ephemeral=True)
    finally: conn.close()

@bot.tree.command(name="approveconge", description="✅ Approuver un congé (admin)")
@app_commands.describe(id="Numéro de la demande")
async def cmd_approveconge(interaction: discord.Interaction, id: int):
    # Admin OU Lead/Supervisor/Head du même département
    is_adm = is_admin(interaction)
    conn = get_db()
    try:
        req = conn.execute("SELECT * FROM leave_requests WHERE id=? AND status='pending'", (id,)).fetchone()
        if not req: return await interaction.response.send_message(f"⚠️ #{id} introuvable.", ephemeral=True)
        if not is_adm:
            target = interaction.guild.get_member(int(req["user_id"])) if interaction.guild else None
            if not target or not can_approve_for(interaction.user, target):
                return await interaction.response.send_message("⛔ Tu n'as pas la permission d'approuver ce congé.", ephemeral=True)
        # Créer les jours off
        d1 = datetime.strptime(req["start_date"], "%Y-%m-%d")
        d2 = datetime.strptime(req["end_date"], "%Y-%m-%d")
        current = d1; count = 0
        while current <= d2:
            ds = current.strftime("%Y-%m-%d")
            existing = conn.execute("SELECT id FROM days_off WHERE user_id=? AND date=?", (req["user_id"], ds)).fetchone()
            if not existing:
                conn.execute("INSERT INTO days_off (user_id,username,date,reason) VALUES (?,?,?,?)",
                             (req["user_id"], req["username"], ds, f"🏖️ Congé: {req['reason']}"))
                count += 1
            current += timedelta(days=1)
        conn.execute("UPDATE leave_requests SET status='approved', reviewed_by=?, reviewed_at=? WHERE id=?",
                     (interaction.user.display_name, now().isoformat(), id))
        conn.commit()
        nb = (d2 - d1).days + 1
        msg = pick(MSG_CONGE_APPROVED, name=req["username"], start=req["start_date"], end=req["end_date"])
        e = discord.Embed(title=f"✅ Congé #{id} approuvé", description=msg, color=0x2ECC71)
        e.add_field(name="Détails", value=f"**{req['username']}** — {nb} jour{'s' if nb>1 else ''}\n{req['reason']}", inline=False)
        await interaction.response.send_message(embed=e)
        # Notifier l'artiste dans son canal progress
        try:
            m = interaction.guild.get_member(int(req["user_id"]))
            if m:
                ch = find_progress_channel(interaction.guild, m)
                if ch:
                    await ch.send(f"✅ {m.mention} — Ton congé du **{req['start_date']}** au **{req['end_date']}** a été approuvé ! Profite bien 🏖️")
        except: pass
    finally: conn.close()

@bot.tree.command(name="rejectconge", description="❌ Rejeter un congé (admin)")
@app_commands.describe(id="Numéro", raison="Raison du refus")
async def cmd_rejectconge(interaction: discord.Interaction, id: int, raison: str=""):
    is_adm = is_admin(interaction)
    conn = get_db()
    try:
        req = conn.execute("SELECT * FROM leave_requests WHERE id=? AND status='pending'", (id,)).fetchone()
        if not req: return await interaction.response.send_message(f"⚠️ #{id} introuvable.", ephemeral=True)
        if not is_adm:
            target = interaction.guild.get_member(int(req["user_id"])) if interaction.guild else None
            if not target or not can_approve_for(interaction.user, target):
                return await interaction.response.send_message("⛔ Tu n'as pas la permission de refuser ce congé.", ephemeral=True)
        conn.execute("UPDATE leave_requests SET status='rejected', reviewed_by=?, reviewed_at=? WHERE id=?",
                     (interaction.user.display_name, now().isoformat(), id))
        conn.commit()
        e = discord.Embed(title=f"❌ Congé #{id} refusé", description=f"**{req['username']}** {req['start_date']}→{req['end_date']}\n{raison}", color=0xE74C3C)
        await interaction.response.send_message(embed=e)
        try:
            m = interaction.guild.get_member(int(req["user_id"]))
            if m:
                ch = find_progress_channel(interaction.guild, m)
                if ch:
                    await ch.send(f"❌ {m.mention} — Ton congé du **{req['start_date']}** au **{req['end_date']}** a été refusé. {raison}")
        except: pass
    finally: conn.close()

# ═══════════════════ SCHEDULED TASKS ═════════════════════════════════════════

@tasks.loop(minutes=15)
async def reminder_start():
    """Vérifie toutes les 15min si quelqu'un a 1h de retard (dans son timezone)."""
    utc_now = now_utc()
    conn = get_db()
    try:
        date = today_str()
        # Pas de rappels les jours fériés ou vacances collectives
        holiday = is_holiday_or_vacation(conn, date)
        for g in bot.guilds:
            for a in get_team_members(g):
                uid = str(a.id)
                # Déjà pointé ou off ?
                if conn.execute("SELECT id FROM work_sessions WHERE user_id=? AND date=?", (uid,date)).fetchone(): continue
                if conn.execute("SELECT id FROM days_off WHERE user_id=? AND date=?", (uid,date)).fetchone(): continue
                # Pas de rappel si jour férié/vacances
                if holiday: continue
                # Check snooze
                snooze = conn.execute("SELECT snooze_until FROM snoozes WHERE user_id=? AND date=?", (uid,date)).fetchone()
                if snooze and utc_now < datetime.fromisoformat(snooze["snooze_until"]):
                    continue
                # Heure locale de l'artiste
                sched_start, _, user_tz, _ = get_schedule(conn, uid)
                user_now = now_tz(user_tz)
                user_hour = user_now.hour
                # Pas un jour de travail pour cet artiste ?
                if not is_work_day(conn, uid, user_now): continue
                # Rappel si heure locale = start + 1 (fenêtre de 15min)
                if user_hour != sched_start + 1: continue
                if user_now.minute >= 15: continue
                # Trouver le canal progress
                ch = find_progress_channel(g, a)
                if not ch: continue
                try:
                    view = ReminderStartView(uid)
                    msg = pick(MSG_REMINDER_START, name=a.display_name, hour=user_hour, tz=user_tz)
                    await ch.send(f"{a.mention}\n{msg}", view=view)
                except Exception as ex:
                    print(f"Rappel start erreur {a.display_name}: {ex}")
    finally: conn.close()

@tasks.loop(minutes=30)
async def notify_leave_today():
    """Le matin, poste un message dans le canal progress des artistes en congé."""
    current = now()
    if current.hour != DEFAULT_SCHEDULE_START or current.minute >= 30:
        return  # Seulement une fois le matin
    conn = get_db()
    try:
        date = today_str()
        offs = conn.execute("SELECT * FROM days_off WHERE date=? AND reason LIKE '🏖️ Congé:%'", (date,)).fetchall()
        for g in bot.guilds:
            for o in offs:
                m = g.get_member(int(o["user_id"]))
                if not m: continue
                ch = find_progress_channel(g, m)
                if not ch: continue
                reason = o["reason"].replace("🏖️ Congé: ", "")
                try:
                    await ch.send(pick(MSG_ON_LEAVE_TODAY, name=m.display_name, reason=reason))
                except: pass
    finally: conn.close()

_alerted_10h = set()  # In-memory: {(user_id, date)} — pour ne pas spammer l'alerte >10h

@tasks.loop(minutes=30)
async def check_forgotten_sessions():
    """Détecte les sessions oubliées: rappel à end_hour + alerte admin si >10h (une seule fois)."""
    conn = get_db()
    try:
        date = today_str()
        for g in bot.guilds:
            alert_lines = []  # Pour l'admin
            for a in get_team_members(g):
                uid = str(a.id)
                active = get_active_session(conn, uid)
                if not active: continue
                sched_start, sched_end, user_tz, _ = get_schedule(conn, uid)
                user_now = now_tz(user_tz)
                user_hour = user_now.hour
                session_mins = calc_mins(active)
                session_hours = session_mins / 60

                # 1) Rappel à end_hour: "t'as oublié /stop ?"
                if user_hour == sched_end and user_now.minute < 30:
                    ch = find_progress_channel(g, a)
                    if ch:
                        try:
                            await ch.send(f"{a.mention}\n{pick(MSG_SESSION_FORGOTTEN_END, name=a.display_name, hour=sched_end)}")
                        except: pass

                # 2) Alerte si session > 10h — UNE SEULE FOIS
                if session_hours >= 10 and (uid, date) not in _alerted_10h:
                    _alerted_10h.add((uid, date))
                    ch = find_progress_channel(g, a)
                    if ch:
                        try:
                            await ch.send(f"{a.mention}\n⚠️ Ta session est ouverte depuis **{fmt(session_mins)}** ! `/stop` si t'as fini, ou `/pause` si tu fais une pause.")
                        except: pass
                    alert_lines.append(pick(MSG_SESSION_TOO_LONG, name=a.display_name, hours=fmt(session_mins)))

            # Notifier l'admin dans #time-tracking-admin
            if alert_lines:
                ch = get_admin_channel(g) or discord.utils.get(g.text_channels, name=SUMMARY_CHANNEL_NAME)
                if ch:
                    e = discord.Embed(title="⚠️ Sessions suspectes", description="\n".join(alert_lines), color=0xE74C3C)
                    await ch.send(embed=e)
    finally: conn.close()

@tasks.loop(minutes=30)
async def reminder_daily():
    """Rappel daily dans le canal progress si l'artiste a travaillé mais pas posté."""
    utc_now = now_utc()
    conn = get_db()
    try:
        date = today_str()
        for g in bot.guilds:
            for a in get_team_members(g):
                uid = str(a.id)
                if not conn.execute("SELECT id FROM work_sessions WHERE user_id=? AND date=?", (uid,date)).fetchone(): continue
                if conn.execute("SELECT id FROM dailies WHERE user_id=? AND date=?", (uid,date)).fetchone(): continue
                # Check heure locale
                sched_start, _, user_tz, _ = get_schedule(conn, uid)
                user_now = now_tz(user_tz)
                # Rappel daily = start + REMINDER_DAILY_OFFSET heures
                daily_hour = sched_start + REMINDER_DAILY_OFFSET
                if user_now.hour != daily_hour: continue
                if user_now.minute >= 30: continue
                ch = find_progress_channel(g, a)
                if not ch: continue
                try:
                    await ch.send(f"{a.mention}\n{pick(MSG_REMINDER_DAILY, name=a.display_name)}")
                except: pass
    finally: conn.close()

@tasks.loop(time=utc_time(23,55))
async def daily_summary():
    conn = get_db()
    try:
        date = today_str()
        sessions=conn.execute("SELECT * FROM work_sessions WHERE date=? AND status='done' ORDER BY username", (date,)).fetchall()
        offs=conn.execute("SELECT * FROM days_off WHERE date=? ORDER BY username", (date,)).fetchall()
        if not sessions and not offs: return
        e = discord.Embed(title=f"📋 Fin de journée — {date}", color=0x3498DB)
        if sessions:
            lines=[]; ta=0
            for s in sessions:
                wm=calc_mins(s); ta+=wm
                lines.append(f"✅ **{s['username']}** {datetime.fromisoformat(s['start_time']).strftime('%H:%M')}→{datetime.fromisoformat(s['end_time']).strftime('%H:%M')} **{fmt(wm)}**")
            lines.append(f"\n📊 Total: **{fmt(ta)}**")
            e.add_field(name="💼 Travail", value="\n".join(lines), inline=False)
        if offs: e.add_field(name="🏖️ Off", value="\n".join(f"**{o['username']}**—{o['reason']}" for o in offs), inline=False)
        dailies=conn.execute("SELECT * FROM dailies WHERE date=? ORDER BY username", (date,)).fetchall()
        if dailies:
            dl_lines=[]
            for d in dailies:
                link = f" [📎]({d['message_url']})" if d["message_url"] else ""
                dl_lines.append(f"**{d['username']}** — {d['message'][:60]}...{link}")
            e.add_field(name="📝 Dailies", value="\n".join(dl_lines), inline=False)
        d_ids={d["user_id"] for d in dailies}; o_ids={o["user_id"] for o in offs}
        miss=list(dict.fromkeys(s["username"] for s in sessions if s["user_id"] not in d_ids and s["user_id"] not in o_ids))
        if miss: e.add_field(name=f"⚠️ Dailies manquants ({len(miss)})", value=", ".join(f"**{n}**" for n in miss), inline=False)
        for g in bot.guilds:
            ch=discord.utils.get(g.text_channels, name=SUMMARY_CHANNEL_NAME)
            if ch: await ch.send(embed=e)
    finally: conn.close()

@tasks.loop(time=utc_time(0,5))
async def auto_close():
    conn = get_db()
    try:
        yesterday=(now()-timedelta(days=1)).strftime("%Y-%m-%d")
        opens=conn.execute("SELECT * FROM work_sessions WHERE date=? AND status IN ('working','paused')", (yesterday,)).fetchall()
        for s in opens:
            ap=get_active_pause(conn, s["id"])
            if ap:
                eod=datetime.fromisoformat(s["date"]+"T23:59:00")
                pd=(eod-datetime.fromisoformat(ap["start_time"])).total_seconds()/60
                conn.execute("UPDATE pauses SET end_time=? WHERE id=?", (eod.isoformat(),ap["id"]))
                conn.execute("UPDATE work_sessions SET total_pause_minutes=total_pause_minutes+? WHERE id=?", (pd,s["id"]))
            conn.execute("UPDATE work_sessions SET end_time=?, status='done' WHERE id=?", (datetime.fromisoformat(s["date"]+"T23:59:00").isoformat(),s["id"]))
        conn.commit()
        # Nettoyer les snoozes de la veille
        conn.execute("DELETE FROM snoozes WHERE date=?", (yesterday,)); conn.commit()
        if opens:
            for g in bot.guilds:
                ch=get_admin_channel(g) or discord.utils.get(g.text_channels, name=SUMMARY_CHANNEL_NAME)
                if ch: await ch.send(f"⚠️ Sessions auto-fermées: **{', '.join(s['username'] for s in opens)}**")
    finally: conn.close()

@tasks.loop(time=utc_time(17, 0))
async def notify_holidays():
    """Prévient la veille d'un jour férié. Si vendredi et lundi férié, prévient le vendredi."""
    conn = get_db()
    try:
        today = now()
        tomorrow = today + timedelta(days=1)
        tomorrow_str = tomorrow.strftime("%Y-%m-%d")
        # Check demain
        h_tomorrow = is_holiday_or_vacation(conn, tomorrow_str)
        if h_tomorrow:
            for g in bot.guilds:
                ch = discord.utils.get(g.text_channels, name=SUMMARY_CHANNEL_NAME)
                if ch:
                    e = discord.Embed(title="📅 Rappel", description=f"Demain **{tomorrow.strftime('%d/%m')}** est **{h_tomorrow}** !\nPas de rappels — mais tu peux `/start` si tu veux bosser.", color=0x9B59B6)
                    await ch.send(embed=e)
        # Si on est vendredi, check aussi lundi
        if today.weekday() == 4:  # Vendredi
            monday = today + timedelta(days=3)
            monday_str = monday.strftime("%Y-%m-%d")
            h_monday = is_holiday_or_vacation(conn, monday_str)
            if h_monday and h_monday != h_tomorrow:  # Éviter doublon si demain=samedi férié
                for g in bot.guilds:
                    ch = discord.utils.get(g.text_channels, name=SUMMARY_CHANNEL_NAME)
                    if ch:
                        e = discord.Embed(title="📅 Bon week-end !", description=f"Lundi **{monday.strftime('%d/%m')}** est **{h_monday}** !\nOn se retrouve mardi. Bon repos ! 🎉", color=0x9B59B6)
                        await ch.send(embed=e)
    finally: conn.close()

# ─── Night Owl Tasks ─────────────────────────────────────────────────────────

@tasks.loop(time=utc_time(20, 0))
async def evening_summary_20h():
    """À 20h: résumé auto du jour dans #time-tracking + rappel daily aux retardataires."""
    conn = get_db()
    try:
        date = today_str()
        sessions = conn.execute("SELECT * FROM work_sessions WHERE date=? ORDER BY username", (date,)).fetchall()
        offs = conn.execute("SELECT * FROM days_off WHERE date=? ORDER BY username", (date,)).fetchall()
        dailies = conn.execute("SELECT * FROM dailies WHERE date=? ORDER BY username", (date,)).fetchall()

        for g in bot.guilds:
            dept_map = build_dept_map(g)
            role_map = build_role_map(g)
            members = get_team_members(g)
            s_ids = {s["user_id"] for s in sessions}; o_ids = {o["user_id"] for o in offs}; d_ids = {d["user_id"] for d in dailies}

            e = discord.Embed(title=f"📋 Résumé 20h — {date}", color=0x3498DB)
            if sessions:
                lines = []
                for s in sessions:
                    lunch_m = get_lunch_minutes(conn, s["user_id"])
                    wm = calc_paid_mins(s, lunch_m)
                    st = datetime.fromisoformat(s["start_time"]).strftime("%H:%M")
                    en = datetime.fromisoformat(s["end_time"]).strftime("%H:%M") if s["end_time"] else "en cours"
                    icon = "⏸️" if s["status"]=="paused" else ("✅" if s["status"]=="done" else "🟢")
                    dept = dept_map.get(s["user_id"], "")
                    dept_tag = f" `{dept}`" if dept else ""
                    role = role_map.get(s["user_id"], "")
                    role_e = f"{get_role_info(role)['emoji']}" if role else ""
                    has_daily = "📝" if s["user_id"] in d_ids else "❌📝"
                    ot = calc_overtime_mins(s, lunch_m)
                    ot_tag = f" (+{fmt(ot)}sup)" if ot > 0 else ""
                    lines.append(f"{icon} {role_e}**{s['username']}**{dept_tag} {st}→{en} **{fmt(wm)}**{ot_tag} {has_daily}")
                e.add_field(name="💼 Travail", value="\n".join(lines), inline=False)
            if offs:
                e.add_field(name="🏖️ Off/Congé", value="\n".join(f"**{o['username']}** — {o['reason']}" for o in offs), inline=False)
            # Pas pointé
            not_started = [m for m in members if str(m.id) not in s_ids and str(m.id) not in o_ids]
            if not_started:
                e.add_field(name=f"👻 Pas pointé ({len(not_started)})", value=", ".join(f"**{a.display_name}**" for a in not_started), inline=False)
            # Dailies avec liens
            if dailies:
                dl_lines = []
                for d in dailies:
                    link = f" [📎]({d['message_url']})" if d["message_url"] else ""
                    dl_lines.append(f"**{d['username']}** — {d['message'][:60]}{'...' if len(d['message'])>60 else ''}{link}")
                e.add_field(name="📝 Dailies", value="\n".join(dl_lines), inline=False)
            # Dailies manquants
            no_daily = [s["username"] for s in sessions if s["user_id"] not in d_ids and s["user_id"] not in o_ids]
            if no_daily:
                e.add_field(name=f"⚠️ Dailies manquants ({len(no_daily)})", value=", ".join(f"**{n}**" for n in no_daily), inline=False)

            ch = discord.utils.get(g.text_channels, name=SUMMARY_CHANNEL_NAME)
            if ch:
                await ch.send(embed=e)

            # Admin channel: infos privées (dailies manquants, taux, détails)
            admin_ch = get_admin_channel(g)
            if admin_ch and (no_daily or not_started):
                ae = discord.Embed(title=f"👑 Admin — 20h — {date}", color=0xE74C3C)
                if no_daily:
                    ae.add_field(name=f"⚠️ Dailies manquants ({len(no_daily)})", value=", ".join(f"**{n}**" for n in no_daily), inline=False)
                if not_started:
                    ae.add_field(name=f"👻 Pas pointé & pas off ({len(not_started)})", value=", ".join(f"**{a.display_name}**" for a in not_started), inline=False)
                # Sessions encore ouvertes
                still_open = [s for s in sessions if s["status"] in ("working","paused")]
                if still_open:
                    ae.add_field(name=f"🟢 Encore en cours ({len(still_open)})", value="\n".join(f"**{s['username']}** depuis {datetime.fromisoformat(s['start_time']).strftime('%H:%M')}" for s in still_open), inline=False)
                await admin_ch.send(embed=ae)

            # Rappel daily aux retardataires dans leur canal progress
            for a in members:
                uid = str(a.id)
                if uid not in s_ids: continue
                if uid in d_ids: continue
                if uid in o_ids: continue
                pch = find_progress_channel(g, a)
                if pch:
                    try:
                        await pch.send(f"{a.mention}\n{pick(MSG_REMINDER_DAILY_20H, name=a.display_name)}")
                    except: pass
    finally: conn.close()

@tasks.loop(time=utc_time(0, 1))
async def midnight_check():
    """À minuit, check les sessions encore ouvertes et demande gentiment."""
    conn = get_db()
    try:
        date = today_str()
        for g in bot.guilds:
            for a in get_team_members(g):
                uid = str(a.id)
                active = get_active_session(conn, uid)
                if not active: continue
                ch = find_progress_channel(g, a)
                if not ch: continue
                try:
                    view = MidnightView(uid)
                    msg = pick(MSG_MIDNIGHT_CHECK, name=a.display_name)
                    await ch.send(f"{a.mention}\n{msg}", view=view)
                except: pass
    finally: conn.close()

@tasks.loop(time=utc_time(3, 0))
async def force_close_3am():
    """À 3h du mat, ferme de force les sessions encore ouvertes."""
    conn = get_db()
    try:
        date = today_str()
        for g in bot.guilds:
            for a in get_team_members(g):
                uid = str(a.id)
                active = get_active_session(conn, uid)
                if not active: continue
                # Fermer la session
                ap = get_active_pause(conn, active["id"])
                if ap:
                    pd = (now()-datetime.fromisoformat(ap["start_time"])).total_seconds()/60
                    conn.execute("UPDATE pauses SET end_time=? WHERE id=?", (now().isoformat(), ap["id"]))
                    conn.execute("UPDATE work_sessions SET total_pause_minutes=total_pause_minutes+? WHERE id=?", (pd, active["id"]))
                cur = now()
                conn.execute("UPDATE work_sessions SET end_time=?, status='done' WHERE id=?", (cur.isoformat(), active["id"]))
                conn.commit()
                up = conn.execute("SELECT * FROM work_sessions WHERE id=?", (active["id"],)).fetchone()
                wm = calc_mins(up)
                # Auto-daily si pas fait (pour pas bloquer la fermeture)
                if not conn.execute("SELECT * FROM dailies WHERE user_id=? AND date=?", (uid, date)).fetchone():
                    conn.execute("INSERT OR IGNORE INTO dailies (user_id,username,date,message,message_url) VALUES (?,?,?,?,?)",
                                 (uid, a.display_name, date, "(auto — session fermée à 3h)", ""))
                    conn.commit()
                ch = find_progress_channel(g, a)
                if ch:
                    try:
                        msg = pick(MSG_3AM_FORCE_CLOSE, name=a.display_name, hours=fmt(wm))
                        await ch.send(f"{a.mention}\n{msg}")
                    except: pass
    finally: conn.close()

# ─── Weekly Digest (Vendredi soir) ────────────────────────────────────────────

@tasks.loop(time=utc_time(19, 0))
async def weekly_digest():
    """Vendredi 19h: résumé hebdo pour les artistes + dashboard admin."""
    current = now()
    if current.weekday() != 4:  # 4 = vendredi
        return
    conn = get_db()
    try:
        monday, sunday = get_week_dates()
        mon_str, sun_str = monday.strftime("%Y-%m-%d"), sunday.strftime("%Y-%m-%d")

        for g in bot.guilds:
            members = get_team_members(g)
            dept_map = build_dept_map(g)
            admin_lines = []
            low_hours_lines = []

            # ─ Résumé par artiste dans leur canal progress ─
            for a in members:
                uid = str(a.id)
                stats = get_week_stats(conn, uid, monday, sunday)
                streak = get_streak(conn, uid)
                if stats["nb_sessions"] == 0: continue

                ch = find_progress_channel(g, a)
                if ch:
                    streak_txt = ""
                    if streak >= 5: streak_txt = f"\n🔥 **Streak: {streak} jours** de daily consécutifs ! On fire !"
                    elif streak >= 3: streak_txt = f"\n⭐ **Streak: {streak} jours** de daily d'affilée !"
                    ot_txt = f"\n⏰ Dont **{fmt(stats['overtime_mins'])}** d'heures sup" if stats["overtime_mins"] > 0 else ""
                    e = discord.Embed(title=f"📊 Ta semaine — {mon_str[5:]} → {sun_str[5:]}", color=0x9B59B6)
                    e.add_field(name="🕐 Heures", value=f"**{fmt(stats['total_mins'])}**{ot_txt}", inline=True)
                    e.add_field(name="📝 Dailies", value=f"**{stats['nb_dailies']}/{stats['nb_sessions']}**", inline=True)
                    e.add_field(name="📅 Jours", value=f"**{stats['nb_sessions']}**", inline=True)
                    if streak_txt:
                        e.add_field(name="", value=streak_txt, inline=False)
                    e.set_footer(text="Bon week-end ! 🎉")
                    try: await ch.send(f"{a.mention}", embed=e)
                    except: pass

                # Collect admin data
                dept = dept_map.get(uid, "")
                dept_tag = f" `{dept}`" if dept else ""
                ot = f" (+{fmt(stats['overtime_mins'])} sup)" if stats["overtime_mins"] > 0 else ""
                admin_lines.append(f"**{a.display_name}**{dept_tag} — {fmt(stats['total_mins'])}{ot} · 📝{stats['nb_dailies']}/{stats['nb_sessions']}")

                if stats["total_mins"] / 60 < MIN_WEEKLY_HOURS and stats["nb_sessions"] >= 3:
                    low_hours_lines.append(f"⚠️ **{a.display_name}** — {fmt(stats['total_mins'])} (< {MIN_WEEKLY_HOURS}h)")

            # ─ Dashboard admin dans #time-tracking-admin ─
            if admin_lines:
                admin_ch = get_admin_channel(g) or discord.utils.get(g.text_channels, name=SUMMARY_CHANNEL_NAME)
                if admin_ch:
                    e = discord.Embed(title=f"👑 Dashboard hebdo — {mon_str[5:]} → {sun_str[5:]}", color=0xE74C3C)
                    # Grouper par département
                    by_dept = {}
                    for a in members:
                        uid = str(a.id)
                        stats = get_week_stats(conn, uid, monday, sunday)
                        if stats["nb_sessions"] == 0: continue
                        dept = dept_map.get(uid, "Sans département")
                        by_dept.setdefault(dept, []).append((a.display_name, stats))
                    for dept, artists in sorted(by_dept.items()):
                        dept_total = sum(s["total_mins"] for _, s in artists)
                        dept_ot = sum(s["overtime_mins"] for _, s in artists)
                        lines = []
                        for name, s in artists:
                            ot = f" (+{fmt(s['overtime_mins'])})" if s["overtime_mins"] > 0 else ""
                            lines.append(f"  {name} — {fmt(s['total_mins'])}{ot} · 📝{s['nb_dailies']}/{s['nb_sessions']}")
                        ot_dept = f" (+{fmt(dept_ot)} sup)" if dept_ot > 0 else ""
                        e.add_field(name=f"📂 {dept} ({len(artists)}) — {fmt(dept_total)}{ot_dept}", value="\n".join(lines), inline=False)
                    if low_hours_lines:
                        e.add_field(name=f"🚨 Heures basses (<{MIN_WEEKLY_HOURS}h)", value="\n".join(low_hours_lines), inline=False)
                    await admin_ch.send(embed=e)

            # ─ Résumé public dans #time-tracking ─
            pub_ch = discord.utils.get(g.text_channels, name=SUMMARY_CHANNEL_NAME)
            if pub_ch and admin_lines:
                e = discord.Embed(title=f"📊 Résumé de la semaine — {mon_str[5:]} → {sun_str[5:]}", description="Bon week-end ! 🎉", color=0x9B59B6)
                e.add_field(name="Équipe", value="\n".join(admin_lines[:20]), inline=False)
                await pub_ch.send(embed=e)
    finally: conn.close()

# ═══════════════════ EVENTS ══════════════════════════════════════════════════

@bot.event
async def on_ready():
    print(f"✅ {bot.user} connecté ! Serveurs: {[g.name for g in bot.guilds]}")
    try:
        synced = await bot.tree.sync()
        print(f"   {len(synced)} commandes sync")
    except Exception as ex: print(f"   Erreur: {ex}")
    for g in bot.guilds:
        depts = get_dept_list(g); team = get_team_members(g)
        print(f"   {TEAM_ROLE_NAME}: {len(team)} membres | Depts: {', '.join(depts) if depts else 'aucun'}")
        progress = [c.name for c in g.text_channels if c.name.endswith(PROGRESS_CHANNEL_SUFFIX)]
        print(f"   Canaux progress: {len(progress)} ({', '.join(progress[:5])}{'...' if len(progress)>5 else ''})")
    for t in [daily_summary, auto_close, reminder_start, notify_leave_today, check_forgotten_sessions,
              reminder_daily, notify_holidays, evening_summary_20h, midnight_check, force_close_3am,
              weekly_digest]:
        if not t.is_running(): t.start()
    print(f"   Daily: '{DAILY_KEYWORD}' dans *{PROGRESS_CHANNEL_SUFFIX} | /stop bloqué sans daily")
    print(f"   Rappels: canaux progress (DST-aware timezone par artiste)")
    print(f"   Fériés France | Night owl | Résumé 20h | Digest vendredi 19h")
    print(f"   Studio roles: {', '.join(STUDIO_ROLES.keys())}")
    print("   🎉 Prêt !")

if __name__ == "__main__":
    init_db(); bot.run(TOKEN)
