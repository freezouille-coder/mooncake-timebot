"""
Discord Time Tracking Bot
─────────────────────────
Commandes artistes:
  /start              → Commencer ta journée de travail
  /stop               → Terminer ta journée de travail
  /pause              → Prendre une pause
  /resume             → Reprendre après une pause
  /off [raison]       → Déclarer un jour off
  /mydaily [message]  → Publier ton daily (résumé de ta journée)
  /status             → Voir ton statut actuel
  /myreport           → Voir tes propres heures du mois
  /mydailies          → Voir tes propres dailies du mois
  /edit               → Demander une correction d'heures

Commandes admin:
  /today              → Résumé de la journée (tout le monde)
  /dailies            → Voir les dailies du jour + qui manque
  /summary            → Résumé mensuel de toute l'équipe
  /report             → Rapport mensuel détaillé (TXT + CSV)
  /setrate            → Définir le taux horaire d'un artiste
  /rates              → Voir tous les taux horaires
  /pending            → Voir les corrections en attente
  /approve            → Approuver une correction
  /reject             → Rejeter une correction
"""

import discord
from discord import app_commands
from discord.ext import commands, tasks
import sqlite3
import csv
from datetime import datetime, timedelta, time
from typing import Optional
import os
from pathlib import Path

# ─── Configuration ───────────────────────────────────────────────────────────
DB_PATH = Path(__file__).parent / "timetracking.db"
TOKEN = os.getenv("DISCORD_BOT_TOKEN", "YOUR_TOKEN_HERE")
ADMIN_ROLE_NAME = "Admin"           # Rôle pour accéder aux rapports et approuver
ARTIST_ROLE_NAME = "Artiste"        # Rôle qui identifie les artistes (pour dailies/rappels)
SUMMARY_CHANNEL_NAME = "time-tracking"
TIMEZONE_OFFSET = -5                # UTC offset (ex: -5 pour EST, +1 pour CET)
REMINDER_HOUR_START = 10            # Heure locale pour rappel "tu n'as pas /start"
REMINDER_HOUR_DAILY = 17            # Heure locale pour rappel "daily manquant"
DEFAULT_HOURLY_RATE = 0.0           # Taux horaire par défaut (0 = non défini)

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
            message TEXT NOT NULL, created_at TEXT DEFAULT (datetime('now')),
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
        CREATE INDEX IF NOT EXISTS idx_sessions_user_date ON work_sessions(user_id, date);
        CREATE INDEX IF NOT EXISTS idx_off_user_date ON days_off(user_id, date);
        CREATE INDEX IF NOT EXISTS idx_dailies_user_date ON dailies(user_id, date);
        CREATE INDEX IF NOT EXISTS idx_edits_status ON edit_requests(status);
    """)
    conn.commit()
    conn.close()

# ─── Helpers ─────────────────────────────────────────────────────────────────

MOIS_FR = ["", "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
           "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre"]

def now():
    return datetime.utcnow() + timedelta(hours=TIMEZONE_OFFSET)

def today_str():
    return now().strftime("%Y-%m-%d")

def fmt(minutes):
    if minutes is None or minutes < 0: return "0h00"
    return f"{int(minutes // 60)}h{int(minutes % 60):02d}"

def get_active_session(conn, user_id):
    return conn.execute(
        "SELECT * FROM work_sessions WHERE user_id=? AND date=? AND status IN ('working','paused')",
        (user_id, today_str())).fetchone()

def get_active_pause(conn, session_id):
    return conn.execute("SELECT * FROM pauses WHERE session_id=? AND end_time IS NULL", (session_id,)).fetchone()

def calc_mins(session):
    start = datetime.fromisoformat(session["start_time"])
    end = datetime.fromisoformat(session["end_time"]) if session["end_time"] else now()
    return max(0, (end - start).total_seconds() / 60 - (session["total_pause_minutes"] or 0))

def is_admin(interaction):
    if interaction.user.guild_permissions.administrator: return True
    return any(r.name == ADMIN_ROLE_NAME for r in interaction.user.roles)

def get_artists(guild):
    role = discord.utils.get(guild.roles, name=ARTIST_ROLE_NAME)
    return [m for m in guild.members if role and role in m.roles and not m.bot] if role else []

def get_rate(conn, user_id):
    r = conn.execute("SELECT rate, currency FROM hourly_rates WHERE user_id=?", (user_id,)).fetchone()
    return (r["rate"], r["currency"]) if r else (DEFAULT_HOURLY_RATE, "$")

def utc_time(local_h, m=0):
    return time(hour=(local_h - TIMEZONE_OFFSET) % 24, minute=m)

# ─── Bot Setup ───────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ═══════════════════ ARTIST COMMANDS ═════════════════════════════════════════

@bot.tree.command(name="start", description="🟢 Commencer ta journée de travail")
async def cmd_start(interaction: discord.Interaction):
    conn = get_db()
    try:
        uid, name = str(interaction.user.id), interaction.user.display_name
        if get_active_session(conn, uid):
            return await interaction.response.send_message("⚠️ Tu as déjà une session en cours. `/stop` d'abord.", ephemeral=True)
        if conn.execute("SELECT * FROM days_off WHERE user_id=? AND date=?", (uid, today_str())).fetchone():
            return await interaction.response.send_message("⚠️ Tu es off aujourd'hui.", ephemeral=True)
        cur = now()
        conn.execute("INSERT INTO work_sessions (user_id,username,date,start_time,status) VALUES (?,?,?,?,'working')",
                     (uid, name, today_str(), cur.isoformat()))
        conn.commit()
        e = discord.Embed(title="🟢 Journée commencée !", description=f"**{name}** — {cur.strftime('%H:%M')}", color=0x2ECC71, timestamp=cur)
        await interaction.response.send_message(embed=e)
    finally: conn.close()

@bot.tree.command(name="stop", description="🔴 Terminer ta journée de travail")
async def cmd_stop(interaction: discord.Interaction):
    conn = get_db()
    try:
        uid, name = str(interaction.user.id), interaction.user.display_name
        active = get_active_session(conn, uid)
        if not active:
            return await interaction.response.send_message("⚠️ Pas de session en cours.", ephemeral=True)
        ap = get_active_pause(conn, active["id"])
        if ap:
            pd = (now() - datetime.fromisoformat(ap["start_time"])).total_seconds() / 60
            conn.execute("UPDATE pauses SET end_time=? WHERE id=?", (now().isoformat(), ap["id"]))
            conn.execute("UPDATE work_sessions SET total_pause_minutes=total_pause_minutes+? WHERE id=?", (pd, active["id"]))
        cur = now()
        conn.execute("UPDATE work_sessions SET end_time=?, status='done' WHERE id=?", (cur.isoformat(), active["id"]))
        conn.commit()
        updated = conn.execute("SELECT * FROM work_sessions WHERE id=?", (active["id"],)).fetchone()
        wm = calc_mins(updated)
        st = datetime.fromisoformat(updated["start_time"]).strftime("%H:%M")
        e = discord.Embed(title="🔴 Journée terminée !", description=f"**{name}**", color=0xE74C3C, timestamp=cur)
        e.add_field(name="Début", value=st, inline=True)
        e.add_field(name="Fin", value=cur.strftime("%H:%M"), inline=True)
        e.add_field(name="Pauses", value=fmt(updated["total_pause_minutes"]), inline=True)
        e.add_field(name="🕐 Travaillé", value=f"**{fmt(wm)}**", inline=False)
        if not conn.execute("SELECT * FROM dailies WHERE user_id=? AND date=?", (uid, today_str())).fetchone():
            e.add_field(name="📝 Daily manquant !", value="N'oublie pas `/mydaily` !", inline=False)
        await interaction.response.send_message(embed=e)
    finally: conn.close()

@bot.tree.command(name="pause", description="⏸️ Prendre une pause")
async def cmd_pause(interaction: discord.Interaction):
    conn = get_db()
    try:
        uid = str(interaction.user.id)
        active = get_active_session(conn, uid)
        if not active: return await interaction.response.send_message("⚠️ Pas de session.", ephemeral=True)
        if active["status"] == "paused": return await interaction.response.send_message("⚠️ Déjà en pause.", ephemeral=True)
        cur = now()
        conn.execute("INSERT INTO pauses (session_id, start_time) VALUES (?,?)", (active["id"], cur.isoformat()))
        conn.execute("UPDATE work_sessions SET status='paused' WHERE id=?", (active["id"],))
        conn.commit()
        e = discord.Embed(title="⏸️ Pause", description=f"**{interaction.user.display_name}** — {cur.strftime('%H:%M')}", color=0xF39C12)
        await interaction.response.send_message(embed=e)
    finally: conn.close()

@bot.tree.command(name="resume", description="▶️ Reprendre après une pause")
async def cmd_resume(interaction: discord.Interaction):
    conn = get_db()
    try:
        uid = str(interaction.user.id)
        active = get_active_session(conn, uid)
        if not active or active["status"] != "paused":
            return await interaction.response.send_message("⚠️ Tu n'es pas en pause.", ephemeral=True)
        ap = get_active_pause(conn, active["id"])
        if ap:
            pd = (now() - datetime.fromisoformat(ap["start_time"])).total_seconds() / 60
            conn.execute("UPDATE pauses SET end_time=? WHERE id=?", (now().isoformat(), ap["id"]))
            conn.execute("UPDATE work_sessions SET total_pause_minutes=total_pause_minutes+?, status='working' WHERE id=?", (pd, active["id"]))
            conn.commit()
            e = discord.Embed(title="▶️ Reprise !", description=f"**{interaction.user.display_name}** — pause: **{fmt(pd)}**", color=0x2ECC71)
            await interaction.response.send_message(embed=e)
        else:
            conn.execute("UPDATE work_sessions SET status='working' WHERE id=?", (active["id"],))
            conn.commit()
            await interaction.response.send_message("▶️ Reprise !", ephemeral=True)
    finally: conn.close()

@bot.tree.command(name="off", description="🏖️ Déclarer un jour off")
@app_commands.describe(raison="Raison (optionnel)", date="Date YYYY-MM-DD (optionnel)")
async def cmd_off(interaction: discord.Interaction, raison: str = "Jour off", date: Optional[str] = None):
    conn = get_db()
    try:
        uid, name = str(interaction.user.id), interaction.user.display_name
        td = date or today_str()
        try: datetime.strptime(td, "%Y-%m-%d")
        except: return await interaction.response.send_message("⚠️ Format: YYYY-MM-DD", ephemeral=True)
        if conn.execute("SELECT * FROM days_off WHERE user_id=? AND date=?", (uid, td)).fetchone():
            return await interaction.response.send_message(f"⚠️ Déjà off le {td}.", ephemeral=True)
        if conn.execute("SELECT * FROM work_sessions WHERE user_id=? AND date=? AND status IN ('working','paused')", (uid, td)).fetchone():
            return await interaction.response.send_message("⚠️ Session en cours, `/stop` d'abord.", ephemeral=True)
        conn.execute("INSERT INTO days_off (user_id,username,date,reason) VALUES (?,?,?,?)", (uid, name, td, raison))
        conn.commit()
        e = discord.Embed(title="🏖️ Jour off", description=f"**{name}** off le **{td}**\n{raison}", color=0x9B59B6)
        await interaction.response.send_message(embed=e)
    finally: conn.close()

@bot.tree.command(name="mydaily", description="📝 Publier ton daily")
@app_commands.describe(message="Décris ce que tu as fait aujourd'hui")
async def cmd_mydaily(interaction: discord.Interaction, message: str):
    conn = get_db()
    try:
        uid, name, date = str(interaction.user.id), interaction.user.display_name, today_str()
        existing = conn.execute("SELECT * FROM dailies WHERE user_id=? AND date=?", (uid, date)).fetchone()
        if existing:
            conn.execute("UPDATE dailies SET message=?, username=? WHERE user_id=? AND date=?", (message, name, uid, date))
            title = "📝 Daily mis à jour !"
        else:
            conn.execute("INSERT INTO dailies (user_id,username,date,message) VALUES (?,?,?,?)", (uid, name, date, message))
            title = "📝 Daily publié !"
        conn.commit()
        e = discord.Embed(title=title, description=f"**{name}** — {date}", color=0x1ABC9C)
        e.add_field(name="Contenu", value=message[:1024], inline=False)
        await interaction.response.send_message(embed=e)
    finally: conn.close()

@bot.tree.command(name="edit", description="✏️ Demander une correction d'heures")
@app_commands.describe(date="Date (YYYY-MM-DD)", debut="Début (HH:MM)", fin="Fin (HH:MM)", raison="Raison")
async def cmd_edit(interaction: discord.Interaction, date: str, debut: str, fin: str, raison: str):
    conn = get_db()
    try:
        uid, name = str(interaction.user.id), interaction.user.display_name
        try: datetime.strptime(date, "%Y-%m-%d")
        except: return await interaction.response.send_message("⚠️ Format date: YYYY-MM-DD", ephemeral=True)
        try: datetime.strptime(debut, "%H:%M"); datetime.strptime(fin, "%H:%M")
        except: return await interaction.response.send_message("⚠️ Format heure: HH:MM", ephemeral=True)
        if conn.execute("SELECT * FROM edit_requests WHERE user_id=? AND target_date=? AND status='pending'", (uid, date)).fetchone():
            return await interaction.response.send_message(f"⚠️ Demande déjà en attente pour {date}.", ephemeral=True)
        conn.execute("INSERT INTO edit_requests (user_id,username,target_date,new_start,new_end,reason) VALUES (?,?,?,?,?,?)",
                     (uid, name, date, f"{date}T{debut}:00", f"{date}T{fin}:00", raison))
        conn.commit()
        rid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        e = discord.Embed(title="✏️ Correction envoyée", description=f"Demande **#{rid}**", color=0xF39C12)
        e.add_field(name="Date", value=date, inline=True)
        e.add_field(name="Heures", value=f"{debut} → {fin}", inline=True)
        e.add_field(name="Raison", value=raison, inline=False)
        await interaction.response.send_message(embed=e, ephemeral=True)
        for g in bot.guilds:
            ch = discord.utils.get(g.text_channels, name=SUMMARY_CHANNEL_NAME)
            if ch:
                ae = discord.Embed(title=f"✏️ Correction #{rid}", description=f"**{name}**", color=0xF39C12)
                ae.add_field(name="Date", value=date, inline=True)
                ae.add_field(name="Heures", value=f"{debut} → {fin}", inline=True)
                ae.add_field(name="Raison", value=raison, inline=False)
                ae.set_footer(text="/pending → /approve ou /reject")
                await ch.send(embed=ae)
    finally: conn.close()

@bot.tree.command(name="status", description="📊 Ton statut actuel")
async def cmd_status(interaction: discord.Interaction):
    conn = get_db()
    try:
        uid, name = str(interaction.user.id), interaction.user.display_name
        off = conn.execute("SELECT * FROM days_off WHERE user_id=? AND date=?", (uid, today_str())).fetchone()
        if off:
            e = discord.Embed(title=f"📊 {name}", description=f"🏖️ Off — {off['reason']}", color=0x9B59B6)
            return await interaction.response.send_message(embed=e, ephemeral=True)
        active = get_active_session(conn, uid)
        if not active:
            done = conn.execute("SELECT * FROM work_sessions WHERE user_id=? AND date=? AND status='done'", (uid, today_str())).fetchone()
            if done:
                e = discord.Embed(title=f"📊 {name}", description=f"✅ Terminé — **{fmt(calc_mins(done))}**", color=0x95A5A6)
            else:
                e = discord.Embed(title=f"📊 {name}", description="⬜ Pas commencé", color=0x95A5A6)
            return await interaction.response.send_message(embed=e, ephemeral=True)
        wm = calc_mins(active)
        st = "⏸️ En pause" if active["status"] == "paused" else "🟢 Au travail"
        e = discord.Embed(title=f"📊 {name}", description=st, color=0xF39C12 if active["status"]=="paused" else 0x2ECC71)
        e.add_field(name="Depuis", value=datetime.fromisoformat(active["start_time"]).strftime("%H:%M"), inline=True)
        e.add_field(name="Pauses", value=fmt(active["total_pause_minutes"]), inline=True)
        e.add_field(name="Travaillé", value=f"**{fmt(wm)}**", inline=False)
        await interaction.response.send_message(embed=e, ephemeral=True)
    finally: conn.close()

@bot.tree.command(name="myreport", description="📈 Tes heures du mois")
@app_commands.describe(mois="Mois (1-12)", annee="Année")
async def cmd_myreport(interaction: discord.Interaction, mois: Optional[int] = None, annee: Optional[int] = None):
    cur = now(); month = mois or cur.month; year = annee or cur.year
    uid, name = str(interaction.user.id), interaction.user.display_name
    conn = get_db()
    try:
        dp = f"{year}-{month:02d}"
        sessions = conn.execute("SELECT * FROM work_sessions WHERE user_id=? AND date LIKE ? AND status='done' ORDER BY date", (uid, f"{dp}%")).fetchall()
        offs = conn.execute("SELECT * FROM days_off WHERE user_id=? AND date LIKE ? ORDER BY date", (uid, f"{dp}%")).fetchall()
        total_mins = 0; lines = []
        for s in sessions:
            wm = calc_mins(s); total_mins += wm
            d = datetime.strptime(s["date"], "%Y-%m-%d").strftime("%d/%m")
            lines.append(f"`{d}` {datetime.fromisoformat(s['start_time']).strftime('%H:%M')}→{datetime.fromisoformat(s['end_time']).strftime('%H:%M')} — **{fmt(wm)}** (pause: {fmt(s['total_pause_minutes'])})")
        actives = conn.execute("SELECT * FROM work_sessions WHERE user_id=? AND date LIKE ? AND status IN ('working','paused')", (uid, f"{dp}%")).fetchall()
        for s in actives:
            wm = calc_mins(s); total_mins += wm
            d = datetime.strptime(s["date"], "%Y-%m-%d").strftime("%d/%m")
            lines.append(f"`{d}` {datetime.fromisoformat(s['start_time']).strftime('%H:%M')}→en cours — **{fmt(wm)}** ⏳")
        rate, cur_s = get_rate(conn, uid); th = total_mins / 60
        e = discord.Embed(title=f"📈 {name} — {MOIS_FR[month]} {year}", color=0x3498DB)
        if lines:
            chunk = "\n".join(lines)
            if len(chunk) > 1024:
                mid = len(lines)//2
                e.add_field(name="Journées (1/2)", value="\n".join(lines[:mid]), inline=False)
                e.add_field(name="Journées (2/2)", value="\n".join(lines[mid:]), inline=False)
            else: e.add_field(name="Journées", value=chunk, inline=False)
        if offs:
            e.add_field(name=f"Off ({len(offs)})", value="\n".join(f"`{datetime.strptime(o['date'],'%Y-%m-%d').strftime('%d/%m')}` 🏖️ {o['reason']}" for o in offs), inline=False)
        txt = f"**{fmt(total_mins)}** ({th:.2f}h) | {len(sessions)+len(actives)} jours | {len(offs)} off"
        if rate > 0: txt += f"\n💰 **{th*rate:.2f}{cur_s}** ({rate:.2f}{cur_s}/h)"
        e.add_field(name="📊 Total", value=txt, inline=False)
        await interaction.response.send_message(embed=e, ephemeral=True)
    finally: conn.close()

@bot.tree.command(name="mydailies", description="📖 Tes dailies du mois")
@app_commands.describe(mois="Mois (1-12)", annee="Année")
async def cmd_mydailies(interaction: discord.Interaction, mois: Optional[int] = None, annee: Optional[int] = None):
    cur = now(); month = mois or cur.month; year = annee or cur.year
    uid, name = str(interaction.user.id), interaction.user.display_name
    conn = get_db()
    try:
        dp = f"{year}-{month:02d}"
        dailies = conn.execute("SELECT * FROM dailies WHERE user_id=? AND date LIKE ? ORDER BY date", (uid, f"{dp}%")).fetchall()
        work_dates = {w["date"] for w in conn.execute("SELECT DISTINCT date FROM work_sessions WHERE user_id=? AND date LIKE ?", (uid, f"{dp}%")).fetchall()}
        daily_dates = {d["date"] for d in dailies}; missing = sorted(work_dates - daily_dates)
        e = discord.Embed(title=f"📖 Dailies — {MOIS_FR[month]} {year}", description=f"**{name}**", color=0x1ABC9C)
        if dailies:
            for d in dailies:
                day = datetime.strptime(d["date"], "%Y-%m-%d").strftime("%d/%m")
                e.add_field(name=f"📝 {day}", value=d["message"][:150]+("..." if len(d["message"])>150 else ""), inline=False)
        else: e.add_field(name="—", value="Aucun daily ce mois.", inline=False)
        if missing: e.add_field(name=f"⚠️ Manquants ({len(missing)})", value=", ".join(datetime.strptime(d,"%Y-%m-%d").strftime("%d/%m") for d in missing), inline=False)
        e.set_footer(text=f"📊 {len(dailies)}/{len(work_dates)} dailies")
        await interaction.response.send_message(embed=e, ephemeral=True)
    finally: conn.close()

# ═══════════════════ ADMIN COMMANDS ══════════════════════════════════════════

@bot.tree.command(name="today", description="📋 Résumé de la journée")
async def cmd_today(interaction: discord.Interaction):
    conn = get_db()
    try:
        date = today_str()
        sessions = conn.execute("SELECT * FROM work_sessions WHERE date=? ORDER BY username", (date,)).fetchall()
        offs = conn.execute("SELECT * FROM days_off WHERE date=? ORDER BY username", (date,)).fetchall()
        artists = get_artists(interaction.guild) if interaction.guild else []
        s_ids = {s["user_id"] for s in sessions}; o_ids = {o["user_id"] for o in offs}
        not_started = [a for a in artists if str(a.id) not in s_ids and str(a.id) not in o_ids]
        if not sessions and not offs and not not_started:
            return await interaction.response.send_message("📋 Rien aujourd'hui.", ephemeral=True)
        e = discord.Embed(title=f"📋 {date}", color=0x3498DB)
        if sessions:
            lines = []
            for s in sessions:
                wm = calc_mins(s); st = datetime.fromisoformat(s["start_time"]).strftime("%H:%M")
                end = datetime.fromisoformat(s["end_time"]).strftime("%H:%M") if s["end_time"] else "en cours"
                icon = "⏸️" if s["status"]=="paused" else ("✅" if s["status"]=="done" else "🟢")
                lines.append(f"{icon} **{s['username']}** {st}→{end} **{fmt(wm)}** (pause:{fmt(s['total_pause_minutes'])})")
            e.add_field(name="💼 Travail", value="\n".join(lines), inline=False)
        if offs: e.add_field(name="🏖️ Off", value="\n".join(f"**{o['username']}** — {o['reason']}" for o in offs), inline=False)
        if not_started: e.add_field(name="⬜ Pas pointé", value=", ".join(f"**{a.display_name}**" for a in not_started), inline=False)
        dailies = conn.execute("SELECT * FROM dailies WHERE date=? ORDER BY username", (date,)).fetchall()
        if dailies: e.add_field(name="📝 Dailies", value="\n".join(f"**{d['username']}** — {d['message'][:80]}{'...' if len(d['message'])>80 else ''}" for d in dailies), inline=False)
        d_ids = {d["user_id"] for d in dailies}
        miss = []; seen = set()
        for s in sessions:
            if s["user_id"] not in d_ids and s["user_id"] not in o_ids and s["user_id"] not in seen:
                seen.add(s["user_id"]); miss.append(s["username"])
        if miss: e.add_field(name="⚠️ Dailies manquants", value=", ".join(f"**{n}**" for n in miss), inline=False)
        await interaction.response.send_message(embed=e)
    finally: conn.close()

@bot.tree.command(name="dailies", description="📋 Dailies du jour + manquants (admin)")
@app_commands.describe(date="Date YYYY-MM-DD (optionnel)")
async def cmd_dailies(interaction: discord.Interaction, date: Optional[str] = None):
    if not is_admin(interaction): return await interaction.response.send_message("⛔ Admin only.", ephemeral=True)
    conn = get_db()
    try:
        td = date or today_str()
        try: datetime.strptime(td, "%Y-%m-%d")
        except: return await interaction.response.send_message("⚠️ Format: YYYY-MM-DD", ephemeral=True)
        dailies = conn.execute("SELECT * FROM dailies WHERE date=? ORDER BY username", (td,)).fetchall()
        workers = conn.execute("SELECT DISTINCT user_id, username FROM work_sessions WHERE date=?", (td,)).fetchall()
        offs = conn.execute("SELECT user_id, username FROM days_off WHERE date=?", (td,)).fetchall()
        artists = get_artists(interaction.guild) if interaction.guild else []
        w_ids={w["user_id"] for w in workers}; o_ids={o["user_id"] for o in offs}; d_ids={d["user_id"] for d in dailies}
        e = discord.Embed(title=f"📋 Dailies — {td}", color=0x1ABC9C)
        if dailies:
            for d in dailies: e.add_field(name=f"✅ {d['username']}", value=d["message"][:200]+("..." if len(d["message"])>200 else ""), inline=False)
        else: e.add_field(name="—", value="Aucun daily publié.", inline=False)
        miss_w = [w for w in workers if w["user_id"] not in d_ids and w["user_id"] not in o_ids]
        if miss_w: e.add_field(name=f"⚠️ Manquants ({len(miss_w)})", value="\n".join(f"❌ **{m['username']}**" for m in miss_w), inline=False)
        absent = [a for a in artists if str(a.id) not in w_ids and str(a.id) not in o_ids]
        if absent: e.add_field(name=f"👻 Pas pointé ({len(absent)})", value="\n".join(f"⬜ **{a.display_name}**" for a in absent), inline=False)
        if offs: e.add_field(name="🏖️ Off", value=", ".join(o["username"] for o in offs), inline=False)
        if len(workers)>0: e.set_footer(text=f"📊 {len(dailies)}/{len(workers)} dailies ({int(len(dailies)/len(workers)*100)}%)")
        await interaction.response.send_message(embed=e)
    finally: conn.close()

@bot.tree.command(name="setrate", description="💰 Taux horaire d'un artiste (admin)")
@app_commands.describe(artiste="L'artiste", taux="Taux horaire (ex: 25.00)", devise="Devise (défaut: $)")
async def cmd_setrate(interaction: discord.Interaction, artiste: discord.Member, taux: float, devise: str = "$"):
    if not is_admin(interaction): return await interaction.response.send_message("⛔ Admin only.", ephemeral=True)
    conn = get_db()
    try:
        conn.execute("INSERT INTO hourly_rates (user_id,username,rate,currency,updated_at) VALUES (?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET rate=?,currency=?,username=?,updated_at=?",
                     (str(artiste.id), artiste.display_name, taux, devise, now().isoformat(), taux, devise, artiste.display_name, now().isoformat()))
        conn.commit()
        e = discord.Embed(title="💰 Taux mis à jour", description=f"**{artiste.display_name}** → **{taux:.2f}{devise}/h**", color=0x2ECC71)
        await interaction.response.send_message(embed=e, ephemeral=True)
    finally: conn.close()

@bot.tree.command(name="rates", description="💰 Tous les taux horaires (admin)")
async def cmd_rates(interaction: discord.Interaction):
    if not is_admin(interaction): return await interaction.response.send_message("⛔ Admin only.", ephemeral=True)
    conn = get_db()
    try:
        rates = conn.execute("SELECT * FROM hourly_rates ORDER BY username").fetchall()
        e = discord.Embed(title="💰 Taux horaires", color=0xE67E22)
        if not rates: e.description = "Aucun taux. Utilisez `/setrate`."
        else:
            for r in rates: e.add_field(name=f"👤 {r['username']}", value=f"**{r['rate']:.2f}{r['currency']}/h**", inline=True)
        await interaction.response.send_message(embed=e, ephemeral=True)
    finally: conn.close()

@bot.tree.command(name="pending", description="📋 Corrections en attente (admin)")
async def cmd_pending(interaction: discord.Interaction):
    if not is_admin(interaction): return await interaction.response.send_message("⛔ Admin only.", ephemeral=True)
    conn = get_db()
    try:
        reqs = conn.execute("SELECT * FROM edit_requests WHERE status='pending' ORDER BY created_at").fetchall()
        if not reqs: return await interaction.response.send_message("✅ Aucune correction en attente.", ephemeral=True)
        e = discord.Embed(title=f"📋 Corrections ({len(reqs)})", color=0xF39C12)
        for r in reqs:
            st = datetime.fromisoformat(r["new_start"]).strftime("%H:%M"); en = datetime.fromisoformat(r["new_end"]).strftime("%H:%M")
            e.add_field(name=f"#{r['id']} — {r['username']} ({r['target_date']})", value=f"{st}→{en}\n{r['reason']}\n`/approve {r['id']}` · `/reject {r['id']}`", inline=False)
        await interaction.response.send_message(embed=e, ephemeral=True)
    finally: conn.close()

@bot.tree.command(name="approve", description="✅ Approuver une correction (admin)")
@app_commands.describe(id="Numéro de la demande")
async def cmd_approve(interaction: discord.Interaction, id: int):
    if not is_admin(interaction): return await interaction.response.send_message("⛔ Admin only.", ephemeral=True)
    conn = get_db()
    try:
        req = conn.execute("SELECT * FROM edit_requests WHERE id=? AND status='pending'", (id,)).fetchone()
        if not req: return await interaction.response.send_message(f"⚠️ #{id} introuvable.", ephemeral=True)
        # Supprimer ancienne session
        old = conn.execute("SELECT id FROM work_sessions WHERE user_id=? AND date=?", (req["user_id"], req["target_date"])).fetchone()
        if old:
            conn.execute("DELETE FROM pauses WHERE session_id=?", (old["id"],))
            conn.execute("DELETE FROM work_sessions WHERE id=?", (old["id"],))
        conn.execute("INSERT INTO work_sessions (user_id,username,date,start_time,end_time,total_pause_minutes,status) VALUES (?,?,?,?,?,0,'done')",
                     (req["user_id"], req["username"], req["target_date"], req["new_start"], req["new_end"]))
        conn.execute("UPDATE edit_requests SET status='approved', reviewed_by=?, reviewed_at=? WHERE id=?",
                     (interaction.user.display_name, now().isoformat(), id))
        conn.commit()
        st = datetime.fromisoformat(req["new_start"]).strftime("%H:%M"); en = datetime.fromisoformat(req["new_end"]).strftime("%H:%M")
        wm = (datetime.fromisoformat(req["new_end"]) - datetime.fromisoformat(req["new_start"])).total_seconds() / 60
        e = discord.Embed(title=f"✅ #{id} approuvée", description=f"**{req['username']}** — {req['target_date']}\n{st}→{en} (**{fmt(wm)}**)", color=0x2ECC71)
        e.set_footer(text=f"Par {interaction.user.display_name}")
        await interaction.response.send_message(embed=e)
        try:
            member = interaction.guild.get_member(int(req["user_id"]))
            if member: await member.send(f"✅ Correction du **{req['target_date']}** approuvée ! {st}→{en}")
        except: pass
    finally: conn.close()

@bot.tree.command(name="reject", description="❌ Rejeter une correction (admin)")
@app_commands.describe(id="Numéro de la demande", raison="Raison du rejet")
async def cmd_reject(interaction: discord.Interaction, id: int, raison: str = ""):
    if not is_admin(interaction): return await interaction.response.send_message("⛔ Admin only.", ephemeral=True)
    conn = get_db()
    try:
        req = conn.execute("SELECT * FROM edit_requests WHERE id=? AND status='pending'", (id,)).fetchone()
        if not req: return await interaction.response.send_message(f"⚠️ #{id} introuvable.", ephemeral=True)
        conn.execute("UPDATE edit_requests SET status='rejected', reviewed_by=?, reviewed_at=? WHERE id=?",
                     (interaction.user.display_name, now().isoformat(), id))
        conn.commit()
        e = discord.Embed(title=f"❌ #{id} rejetée", description=f"**{req['username']}** — {req['target_date']}\n{raison}", color=0xE74C3C)
        await interaction.response.send_message(embed=e)
        try:
            member = interaction.guild.get_member(int(req["user_id"]))
            if member: await member.send(f"❌ Correction du **{req['target_date']}** rejetée. {raison}")
        except: pass
    finally: conn.close()

@bot.tree.command(name="summary", description="📊 Résumé mensuel équipe (admin)")
@app_commands.describe(mois="Mois (1-12)", annee="Année")
async def cmd_summary(interaction: discord.Interaction, mois: Optional[int] = None, annee: Optional[int] = None):
    if not is_admin(interaction): return await interaction.response.send_message("⛔ Admin only.", ephemeral=True)
    cur = now(); month = mois or cur.month; year = annee or cur.year
    conn = get_db()
    try:
        dp = f"{year}-{month:02d}"
        sessions = conn.execute("SELECT * FROM work_sessions WHERE date LIKE ? ORDER BY username,date", (f"{dp}%",)).fetchall()
        offs = conn.execute("SELECT * FROM days_off WHERE date LIKE ? ORDER BY username,date", (f"{dp}%",)).fetchall()
        users = {}
        for s in sessions:
            uid = s["user_id"]
            if uid not in users: users[uid] = {"name": s["username"], "mins": 0, "days": set(), "off": 0, "dl": 0}
            if s["status"] == "done": users[uid]["mins"] += calc_mins(s); users[uid]["days"].add(s["date"])
        for o in offs:
            uid = o["user_id"]
            if uid not in users: users[uid] = {"name": o["username"], "mins": 0, "days": set(), "off": 0, "dl": 0}
            users[uid]["off"] += 1
        for dc in conn.execute("SELECT user_id, COUNT(*) as c FROM dailies WHERE date LIKE ? GROUP BY user_id", (f"{dp}%",)).fetchall():
            if dc["user_id"] in users: users[dc["user_id"]]["dl"] = dc["c"]
        e = discord.Embed(title=f"📊 Équipe — {MOIS_FR[month]} {year}", description="Heures et montants à payer", color=0xE67E22)
        if not users: e.add_field(name="—", value="Aucune activité.", inline=False)
        else:
            gp = 0
            for uid, u in sorted(users.items(), key=lambda x: x[1]["name"].lower()):
                th = u["mins"]/60; dw = len(u["days"]); dpct = f" ({int(u['dl']/dw*100)}%)" if dw>0 else ""
                rate, cs = get_rate(conn, uid); pay = th * rate; gp += pay
                pt = f"\n💰 **{pay:.2f}{cs}** ({rate:.2f}{cs}/h)" if rate>0 else ""
                e.add_field(name=f"👤 {u['name']}", value=f"🕐 **{fmt(u['mins'])}** ({th:.2f}h)\n📅 {dw}j | 🏖️ {u['off']} off\n📝 {u['dl']}/{dw}{dpct}{pt}", inline=True)
            if gp > 0: e.set_footer(text=f"💰 Total: {gp:.2f}$")
        await interaction.response.send_message(embed=e)
    finally: conn.close()

@bot.tree.command(name="report", description="📑 Rapport mensuel TXT + CSV (admin)")
@app_commands.describe(mois="Mois (1-12)", annee="Année")
async def cmd_report(interaction: discord.Interaction, mois: Optional[int] = None, annee: Optional[int] = None):
    if not is_admin(interaction): return await interaction.response.send_message("⛔ Admin only.", ephemeral=True)
    await interaction.response.defer()
    cur = now(); month = mois or cur.month; year = annee or cur.year; dp = f"{year}-{month:02d}"
    conn = get_db()
    try:
        sessions = conn.execute("SELECT * FROM work_sessions WHERE date LIKE ? AND status='done' ORDER BY username,date", (f"{dp}%",)).fetchall()
        offs = conn.execute("SELECT * FROM days_off WHERE date LIKE ? ORDER BY username,date", (f"{dp}%",)).fetchall()
        dailies = conn.execute("SELECT * FROM dailies WHERE date LIKE ? ORDER BY user_id,date", (f"{dp}%",)).fetchall()
        ud = {}
        for s in sessions:
            uid = s["user_id"]
            if uid not in ud: ud[uid] = {"name": s["username"], "sess": [], "offs": [], "dl": []}
            ud[uid]["sess"].append(s)
        for o in offs:
            uid = o["user_id"]
            if uid not in ud: ud[uid] = {"name": o["username"], "sess": [], "offs": [], "dl": []}
            ud[uid]["offs"].append(o)
        for dl in dailies:
            if dl["user_id"] in ud: ud[dl["user_id"]]["dl"].append(dl)

        rpt = [f"{'='*60}", f"  RAPPORT — {MOIS_FR[month]} {year}", f"  {now().strftime('%Y-%m-%d %H:%M')}", f"{'='*60}", ""]
        csv_rows = []; gt = 0; gp = 0

        for uid, d in sorted(ud.items(), key=lambda x: x[1]["name"].lower()):
            rate, cs = get_rate(conn, uid)
            rpt += [f"┌───────────────────────────────────", f"│ 👤 {d['name']}  ({rate:.2f}{cs}/h)", f"├───────────────────────────────────"]
            ut = 0; wdates = set()
            for s in d["sess"]:
                wm = calc_mins(s); ut += wm; wdates.add(s["date"])
                df = datetime.strptime(s["date"],"%Y-%m-%d").strftime("%d/%m/%Y")
                st = datetime.fromisoformat(s["start_time"]).strftime("%H:%M")
                en = datetime.fromisoformat(s["end_time"]).strftime("%H:%M")
                hd = any(dl["date"]==s["date"] for dl in d["dl"])
                dm_msg = next((dl["message"] for dl in d["dl"] if dl["date"]==s["date"]), "")
                rpt.append(f"│  {df}  {st}→{en}  {fmt(wm)}  pause:{fmt(s['total_pause_minutes'])}  {'✅' if hd else '❌'}📝")
                csv_rows.append({"Artiste": d["name"], "Date": s["date"], "Début": st, "Fin": en,
                    "Pause (min)": round(s["total_pause_minutes"] or 0, 1), "Heures": round(wm/60, 2),
                    "Taux": rate, "Montant": round(wm/60*rate, 2), "Devise": cs,
                    "Daily": "Oui" if hd else "Non", "Message daily": dm_msg, "Type": "Travail"})
            for o in d["offs"]:
                df = datetime.strptime(o["date"],"%Y-%m-%d").strftime("%d/%m/%Y")
                rpt.append(f"│  {df}  🏖️ OFF — {o['reason']}")
                csv_rows.append({"Artiste": d["name"], "Date": o["date"], "Début": "", "Fin": "",
                    "Pause (min)": "", "Heures": 0, "Taux": rate, "Montant": 0, "Devise": cs,
                    "Daily": "", "Message daily": "", "Type": f"Off - {o['reason']}"})
            if d["dl"]:
                rpt += ["│", "│  📝 DAILIES:"]
                for dl in d["dl"]:
                    rpt.append(f"│    {datetime.strptime(dl['date'],'%Y-%m-%d').strftime('%d/%m')}: {dl['message'][:80]}{'...' if len(dl['message'])>80 else ''}")
            th = ut/60; up = th*rate; gt += ut; gp += up
            wc = len(wdates); dc = len(d["dl"]); dpct = f" ({int(dc/wc*100)}%)" if wc>0 else ""
            rpt += ["│", f"│  TOTAL: {fmt(ut)} ({th:.2f}h)"]
            if rate > 0: rpt.append(f"│  💰 À PAYER: {up:.2f}{cs}")
            rpt += [f"│  {wc}j travaillés | {len(d['offs'])} off | {dc}/{wc} dailies{dpct}", f"└───────────────────────────────────", ""]

        rpt += [f"{'='*60}", f"  TOTAL: {fmt(gt)} ({gt/60:.2f}h)"]
        if gp > 0: rpt.append(f"  💰 TOTAL À PAYER: {gp:.2f}$")
        rpt.append(f"{'='*60}")

        txt_path = Path(__file__).parent / f"rapport_{MOIS_FR[month]}_{year}.txt"
        txt_path.write_text("\n".join(rpt), encoding="utf-8")
        csv_path = Path(__file__).parent / f"rapport_{MOIS_FR[month]}_{year}.csv"
        if csv_rows:
            with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=list(csv_rows[0].keys()), delimiter=";")
                w.writeheader(); w.writerows(csv_rows)
        files = [discord.File(str(txt_path))]
        if csv_rows: files.append(discord.File(str(csv_path)))
        await interaction.followup.send(f"📑 **{MOIS_FR[month]} {year}** :", files=files)
        txt_path.unlink(missing_ok=True); csv_path.unlink(missing_ok=True)
    finally: conn.close()

# ═══════════════════ SCHEDULED TASKS ═════════════════════════════════════════

@tasks.loop(time=utc_time(REMINDER_HOUR_START))
async def reminder_start():
    """DM les artistes qui n'ont pas /start."""
    if now().weekday() >= 5: return
    conn = get_db()
    try:
        date = today_str()
        for guild in bot.guilds:
            for artist in get_artists(guild):
                uid = str(artist.id)
                if conn.execute("SELECT id FROM work_sessions WHERE user_id=? AND date=?", (uid, date)).fetchone(): continue
                if conn.execute("SELECT id FROM days_off WHERE user_id=? AND date=?", (uid, date)).fetchone(): continue
                try: await artist.send(f"👋 Il est **{REMINDER_HOUR_START}h** et tu n'as pas encore pointé !\n`/start` ou `/off` si tu ne travailles pas.")
                except: pass
    finally: conn.close()

@tasks.loop(time=utc_time(REMINDER_HOUR_DAILY))
async def reminder_daily():
    """DM les artistes qui n'ont pas fait /mydaily."""
    conn = get_db()
    try:
        date = today_str()
        for guild in bot.guilds:
            for artist in get_artists(guild):
                uid = str(artist.id)
                if not conn.execute("SELECT id FROM work_sessions WHERE user_id=? AND date=?", (uid, date)).fetchone(): continue
                if conn.execute("SELECT id FROM dailies WHERE user_id=? AND date=?", (uid, date)).fetchone(): continue
                try: await artist.send("📝 Rappel : tu n'as pas publié ton daily ! Utilise `/mydaily`.")
                except: pass
    finally: conn.close()

@tasks.loop(time=utc_time(23, 55))
async def daily_summary():
    conn = get_db()
    try:
        date = today_str()
        sessions = conn.execute("SELECT * FROM work_sessions WHERE date=? AND status='done' ORDER BY username", (date,)).fetchall()
        offs = conn.execute("SELECT * FROM days_off WHERE date=? ORDER BY username", (date,)).fetchall()
        if not sessions and not offs: return
        e = discord.Embed(title=f"📋 Fin de journée — {date}", color=0x3498DB)
        if sessions:
            lines = []; ta = 0
            for s in sessions:
                wm = calc_mins(s); ta += wm
                lines.append(f"✅ **{s['username']}** {datetime.fromisoformat(s['start_time']).strftime('%H:%M')}→{datetime.fromisoformat(s['end_time']).strftime('%H:%M')} **{fmt(wm)}**")
            lines.append(f"\n📊 Total: **{fmt(ta)}**")
            e.add_field(name="💼 Travail", value="\n".join(lines), inline=False)
        if offs: e.add_field(name="🏖️ Off", value="\n".join(f"**{o['username']}** — {o['reason']}" for o in offs), inline=False)
        dailies = conn.execute("SELECT * FROM dailies WHERE date=? ORDER BY username", (date,)).fetchall()
        if dailies: e.add_field(name="📝 Dailies", value="\n".join(f"**{d['username']}** — {d['message'][:80]}..." for d in dailies), inline=False)
        d_ids = {d["user_id"] for d in dailies}; o_ids = {o["user_id"] for o in offs}
        miss = list(dict.fromkeys(s["username"] for s in sessions if s["user_id"] not in d_ids and s["user_id"] not in o_ids))
        if miss: e.add_field(name=f"⚠️ Dailies manquants ({len(miss)})", value=", ".join(f"**{n}**" for n in miss), inline=False)
        for g in bot.guilds:
            ch = discord.utils.get(g.text_channels, name=SUMMARY_CHANNEL_NAME)
            if ch: await ch.send(embed=e)
    finally: conn.close()

@tasks.loop(time=utc_time(0, 5))
async def auto_close():
    conn = get_db()
    try:
        yesterday = (now() - timedelta(days=1)).strftime("%Y-%m-%d")
        opens = conn.execute("SELECT * FROM work_sessions WHERE date=? AND status IN ('working','paused')", (yesterday,)).fetchall()
        for s in opens:
            ap = get_active_pause(conn, s["id"])
            if ap:
                eod = datetime.fromisoformat(s["date"] + "T23:59:00")
                pd = (eod - datetime.fromisoformat(ap["start_time"])).total_seconds() / 60
                conn.execute("UPDATE pauses SET end_time=? WHERE id=?", (eod.isoformat(), ap["id"]))
                conn.execute("UPDATE work_sessions SET total_pause_minutes=total_pause_minutes+? WHERE id=?", (pd, s["id"]))
            conn.execute("UPDATE work_sessions SET end_time=?, status='done' WHERE id=?", (datetime.fromisoformat(s["date"]+"T23:59:00").isoformat(), s["id"]))
        conn.commit()
        if opens:
            for g in bot.guilds:
                ch = discord.utils.get(g.text_channels, name=SUMMARY_CHANNEL_NAME)
                if ch: await ch.send(f"⚠️ Sessions auto-fermées: **{', '.join(s['username'] for s in opens)}**")
    finally: conn.close()

# ═══════════════════ EVENTS ══════════════════════════════════════════════════

@bot.event
async def on_ready():
    print(f"✅ {bot.user} connecté ! Serveurs: {[g.name for g in bot.guilds]}")
    try:
        synced = await bot.tree.sync()
        print(f"   {len(synced)} commandes sync")
    except Exception as ex: print(f"   Erreur: {ex}")
    for t in [daily_summary, auto_close, reminder_start, reminder_daily]:
        if not t.is_running(): t.start()
    print(f"   Rôle artiste: '{ARTIST_ROLE_NAME}' | Rappels: start={REMINDER_HOUR_START}h, daily={REMINDER_HOUR_DAILY}h")
    print("   🎉 Prêt !")

if __name__ == "__main__":
    init_db()
    bot.run(TOKEN)
