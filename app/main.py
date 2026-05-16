from fastapi import FastAPI, HTTPException, Request
from contextlib import asynccontextmanager
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional
from watchdog.observers.polling import PollingObserver
from watchdog.events import FileSystemEventHandler
import json
import os
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import glob
import re
import shutil
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SHRYNC_VERSION = os.environ.get("SHRYNC_VERSION", "0.57")

@asynccontextmanager
async def lifespan(app: FastAPI):
    global worker_running, _sub_dispatcher_running
    worker_running = True  # zet vóór initial_startup zodat workers meteen actief zijn
    threading.Thread(target=initial_startup, daemon=True).start()
    yield
    # Shutdown
    worker_running = False
    _sub_dispatcher_running = False
    for obs in _observers:
        try:
            obs.stop()
            obs.join()
        except:
            pass

app = FastAPI(title="Shrync", version=SHRYNC_VERSION, lifespan=lifespan)

BASE_DIR = Path(__file__).parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

DB_PATH = "/config/shrync.db"
os.makedirs("/config", exist_ok=True)

# ── Database ──────────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS libraries (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        path TEXT NOT NULL,
        enabled INTEGER DEFAULT 1,
        scan_interval INTEGER DEFAULT 3600,
        video_codec TEXT DEFAULT 'hevc_nvenc',  -- wordt automatisch libx265 als GPU_MODE=cpu
        audio_codec TEXT DEFAULT 'copy',
        quality TEXT DEFAULT '28',
        preset TEXT DEFAULT 'p7',
        last_scan TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        exclude_patterns TEXT DEFAULT '',
        subtitle_quality TEXT DEFAULT 'normal'
    )""")
    # Migratie: voeg kolommen toe aan bestaande databases
    for col, dflt in [('exclude_patterns', "''"), ('subtitle_quality', "'normal'")]:
        try:
            c.execute(f"ALTER TABLE libraries ADD COLUMN {col} TEXT DEFAULT {dflt}")
        except Exception:
            pass  # kolom bestaat al
    c.execute("""CREATE TABLE IF NOT EXISTS queue (
        id TEXT PRIMARY KEY,
        library_id TEXT,
        file_path TEXT NOT NULL,
        file_size INTEGER DEFAULT 0,
        status TEXT DEFAULT 'pending',
        progress INTEGER DEFAULT 0,
        fps REAL DEFAULT 0,
        eta TEXT DEFAULT '',
        added_at TEXT DEFAULT CURRENT_TIMESTAMP,
        started_at TEXT,
        finished_at TEXT,
        error_msg TEXT,
        original_size INTEGER DEFAULT 0,
        new_size INTEGER DEFAULT 0,
        profile_id TEXT DEFAULT ''
    )""")
    try:
        c.execute("ALTER TABLE queue ADD COLUMN profile_id TEXT DEFAULT ''")
    except Exception:
        pass
    c.execute("""CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )""")
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('max_workers', '1')")
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('language', 'en')")
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('conversion_profile', 'nvenc_max')")
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('audio_codec', 'copy')")
    c.execute("""CREATE TABLE IF NOT EXISTS history (
        id TEXT PRIMARY KEY,
        library_id TEXT,
        file_path TEXT NOT NULL,
        original_size INTEGER DEFAULT 0,
        new_size INTEGER DEFAULT 0,
        duration_seconds INTEGER DEFAULT 0,
        status TEXT DEFAULT 'success',
        error_msg TEXT,
        finished_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")

    # ── Ondertiteling tabellen ────────────────────────────────────────────────
    c.execute("""CREATE TABLE IF NOT EXISTS subtitle_queue (
        id TEXT PRIMARY KEY,
        library_id TEXT,
        file_path TEXT NOT NULL,
        file_size INTEGER DEFAULT 0,
        source_lang TEXT DEFAULT 'eng',
        target_lang TEXT DEFAULT 'nld',
        subtitle_track_index INTEGER DEFAULT -1,
        status TEXT DEFAULT 'pending',
        progress INTEGER DEFAULT 0,
        lines_total INTEGER DEFAULT 0,
        lines_done INTEGER DEFAULT 0,
        added_at TEXT DEFAULT CURRENT_TIMESTAMP,
        started_at TEXT,
        finished_at TEXT,
        error_msg TEXT,
        output_path TEXT,
        model_used TEXT DEFAULT ''
    )""")
    try:
        c.execute("ALTER TABLE subtitle_queue ADD COLUMN model_used TEXT DEFAULT ''")
    except Exception:
        pass
    c.execute("""CREATE TABLE IF NOT EXISTS subtitle_history (
        id TEXT PRIMARY KEY,
        library_id TEXT,
        file_path TEXT NOT NULL,
        output_path TEXT,
        source_lang TEXT DEFAULT 'eng',
        target_lang TEXT DEFAULT 'nld',
        lines_translated INTEGER DEFAULT 0,
        model_used TEXT,
        status TEXT DEFAULT 'success',
        error_msg TEXT,
        finished_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")

    # Ondertiteling instellingen
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('subtitle_enabled', '0')")
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('ollama_host', 'http://localhost:11434')")
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('ollama_model', '')")
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('subtitle_auto', '1')")
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('subtitle_source_lang', 'eng')")
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('subtitle_target_lang', 'nld')")
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('theme', 'dark')")

    conn.commit()
    conn.close()

init_db()

# ── Cleanup stale conversions on startup ──────────────────────────────────────
def cleanup_stale_conversions():
    """Ruim loshangende tijdelijke bestanden op en zet taken terug naar pending."""
    conn = get_db()
    stale_jobs = conn.execute("SELECT * FROM queue WHERE status='processing'").fetchall()
    for job in stale_jobs:
        # Tijdelijk bestand altijd naast het bronbestand — zoek op shryncing-*.mkv
        tmp_dir = str(Path(job["file_path"]).parent)
        for tmp_file in glob.glob(os.path.join(tmp_dir, "shryncing-*.mkv")):
            try:
                os.remove(tmp_file)
                logger.info(f"Opruimen: tijdelijk bestand verwijderd: {tmp_file}")
            except Exception as e:
                logger.warning(f"Kon tijdelijk bestand niet verwijderen: {tmp_file} — {e}")
        conn.execute(
            "UPDATE queue SET status='pending', progress=0, fps=0, eta='', started_at=NULL WHERE id=?",
            (job["id"],)
        )
        logger.info(f"Opruimen: taak teruggezet naar pending: {job['file_path']}")
    if stale_jobs:
        logger.info(f"Opruimen voltooid: {len(stale_jobs)} onderbroken taak/taken hersteld.")
    else:
        logger.info("Opruimen: geen loshangende bestanden gevonden.")
    conn.commit()
    conn.close()

cleanup_stale_conversions()

def cleanup_stale_subtitles():
    """
    Bij herstart: zet onderbroken subtitle jobs terug naar pending,
    en verwijder fout-items uit de wachtrij (horen in geschiedenis thuis).
    """
    conn = get_db()
    stale = conn.execute(
        "SELECT id, file_path FROM subtitle_queue WHERE status='processing'"
    ).fetchall()
    for job in stale:
        conn.execute(
            "UPDATE subtitle_queue SET status='pending', progress=0, lines_done=0, started_at=NULL WHERE id=?",
            (job["id"],)
        )
        logger.info(f"Ondertitel opruimen: teruggezet naar pending: {Path(job['file_path']).name}")
    errors = conn.execute(
        "SELECT COUNT(*) as c FROM subtitle_queue WHERE status='error'"
    ).fetchone()["c"]
    if errors:
        conn.execute("DELETE FROM subtitle_queue WHERE status='error'")
        logger.info(f"Ondertitel opruimen: {errors} fout-item(s) verwijderd uit wachtrij")
    conn.commit()
    conn.close()
    if stale:
        logger.info(f"Ondertitel opruimen: {len(stale)} onderbroken taak/taken hersteld.")

cleanup_stale_subtitles()

# ── State ─────────────────────────────────────────────────────────────────────
active_jobs = {}        # slot_name -> {"id": job_id, "process": process}
active_jobs_lock = threading.Lock()
worker_threads = []
worker_running = False
workers_paused = False  # pauze flag: workers slaan taken over als True
scan_status = {}        # library_id -> {status, scanned, added, skipped, already_converted, current_file, error}
_observers = []
_sub_dispatcher_running = False  # hier gedefinieerd zodat lifespan er zeker bij kan

# ── Models ────────────────────────────────────────────────────────────────────
class LibraryCreate(BaseModel):
    name: str
    path: str
    exclude_patterns: Optional[str] = ""
    subtitle_quality: Optional[str] = "normal"

class LibraryUpdate(LibraryCreate):
    enabled: Optional[bool] = True

# ── Settings helper ───────────────────────────────────────────────────────────
def get_global_setting(key: str, default: str = '') -> str:
    try:
        conn = get_db()
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        conn.close()
        return row["value"] if row else default
    except:
        return default

def get_max_workers() -> int:
    try:
        v = get_global_setting('max_workers', '1')
        return max(1, min(3, int(v)))
    except:
        return 1

# ── Helper: check if file needs conversion ────────────────────────────────────
VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".ts", ".wmv", ".flv"}

# ── Conversion profiles ───────────────────────────────────────────────────────
# profile_id -> (video_codec, nvenc_preset_or_cpu_preset, cq_or_crf)
# Voor NVENC: preset = p4/p5/p6/p7 (niet slow/medium/fast)
# Voor CPU:   preset = slow/medium/fast + crf
# ── Encoder profielen ─────────────────────────────────────────────────────────
# Formaat: (codec, preset, quality, encoder_type)
# encoder_type: "nvidia" | "amd" | "intel" | "cpu"
PROFILES = {
    # Nvidia NVENC (GTX 900+)
    "nvenc_max":      ("hevc_nvenc", "p7", "19", "nvidia"),
    "nvenc_high":     ("hevc_nvenc", "p5", "22", "nvidia"),
    "nvenc_balanced": ("hevc_nvenc", "p4", "26", "nvidia"),
    "h264_nvenc":     ("h264_nvenc", "p5", "20", "nvidia"),
    # AMD AMF (RX 400+, via /dev/dri)
    "amf_max":        ("hevc_amf",   "quality",  "19", "amd"),
    "amf_balanced":   ("hevc_amf",   "balanced", "26", "amd"),
    "h264_amf":       ("h264_amf",   "quality",  "20", "amd"),
    # Intel QSV (Gen 6+, via /dev/dri)
    "qsv_max":        ("hevc_qsv",   "veryslow", "19", "intel"),
    "qsv_balanced":   ("hevc_qsv",   "medium",   "26", "intel"),
    "h264_qsv":       ("h264_qsv",   "medium",   "20", "intel"),
    # CPU fallback
    "cpu_slow":       ("libx265",    "slow",     "20", "cpu"),
    "cpu_medium":     ("libx265",    "medium",   "23", "cpu"),
    "cpu_fast":       ("libx265",    "fast",     "26", "cpu"),
    "h264_cpu":       ("libx264",    "medium",   "22", "cpu"),
}

def profile_to_ffmpeg(profile_id: str):
    """Returns (codec, preset, quality, encoder_type) for a given profile."""
    return PROFILES.get(profile_id, PROFILES["nvenc_max"])

# ── HDR metadata detectie ──────────────────────────────────────────────────────
def detect_hdr(src: str) -> dict:
    """
    Detecteert HDR type via ffprobe.
    Returnt dict met: hdr_type (None|"hdr10"|"hdr10plus"|"hlg"|"dolby_vision"),
    color_space, color_transfer, color_primaries, has_dv_rpu.
    """
    result = {"hdr_type": None, "color_space": None, "color_transfer": None,
              "color_primaries": None, "has_dv_rpu": False, "pix_fmt": "yuv420p"}
    try:
        import json as _json
        probe = subprocess.run([
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_streams", "-show_format", src
        ], capture_output=True, text=True, timeout=30)
        if probe.returncode != 0:
            return result
        data = _json.loads(probe.stdout)
        video_stream = next(
            (s for s in data.get("streams", []) if s.get("codec_type") == "video"), None
        )
        if not video_stream:
            return result

        ct  = video_stream.get("color_transfer", "")
        cp  = video_stream.get("color_primaries", "")
        cs  = video_stream.get("color_space", "")
        pf  = video_stream.get("pix_fmt", "yuv420p")

        result["color_transfer"]  = ct
        result["color_primaries"] = cp
        result["color_space"]     = cs
        result["pix_fmt"]         = pf

        # Dolby Vision: check side_data_list
        for side in video_stream.get("side_data_list", []):
            if "Dolby Vision" in side.get("side_data_type", ""):
                result["hdr_type"]    = "dolby_vision"
                result["has_dv_rpu"]  = True
                return result

        # HDR10+ check
        for side in video_stream.get("side_data_list", []):
            if "HDR10+" in side.get("side_data_type", ""):
                result["hdr_type"] = "hdr10plus"
                return result

        # HLG
        if ct == "arib-std-b67":
            result["hdr_type"] = "hlg"
            return result

        # HDR10 (PQ + BT.2020)
        if ct in ("smpte2084", "smpte428") and cp in ("bt2020", "bt2020nc", "bt2020c"):
            result["hdr_type"] = "hdr10"
            return result

    except Exception as e:
        logger.warning(f"HDR detectie mislukt voor {src}: {e}")
    return result

def _hdr_video_flags(hdr: dict) -> list:
    """
    Geeft de extra ffmpeg video-flags terug om HDR metadata te bewaren.
    Alleen van toepassing als hdr_type niet None is.
    """
    if not hdr.get("hdr_type"):
        return []
    flags = [
        "-colorspace",       hdr["color_space"]     or "bt2020nc",
        "-color_primaries",  hdr["color_primaries"]  or "bt2020",
        "-color_trc",        hdr["color_transfer"]   or "smpte2084",
    ]
    return flags

# ── ffmpeg command builders ───────────────────────────────────────────────────

def build_nvenc_cmd(src, tmp_out, codec, preset, cq, audio_codec, hdr: dict = None):
    """
    Universele NVENC command — GTX 900 (Maxwell) t/m RTX 40 (Ada Lovelace).
    - constqp + bf 0 + pix_fmt yuv420p voor maximale compatibiliteit
    - HDR metadata flags worden toegevoegd als het bronbestand HDR is
    """
    hdr = hdr or {}
    # Kies pixel format op basis van HDR type
    if hdr.get("hdr_type") in ("hdr10", "hdr10plus", "hlg"):
        pix_fmt = "p010le"   # 10-bit voor HDR bewaring
    elif hdr.get("hdr_type") == "dolby_vision":
        pix_fmt = "yuv420p10le"
    else:
        pix_fmt = "yuv420p"

    cmd = [
        "ffmpeg", "-y",
        "-i", src,
        "-map", "0",          # alle streams uit input: video, audio, subs, bijlagen
        "-c:v", codec,
        "-preset", preset,
        "-rc", "constqp",
        "-qp", cq,
        "-bf", "0",           # verplicht Maxwell/Pascal HEVC
        "-pix_fmt", pix_fmt,
    ]
    cmd += _hdr_video_flags(hdr)
    cmd += [
        "-c:a", audio_codec,
        "-c:s", "copy",
    ]
    cmd += [
        "-max_muxing_queue_size", "4096",
        "-progress", "pipe:1",
        "-nostats",
        "-loglevel", "warning",
        tmp_out
    ]
    return cmd

def build_amf_cmd(src, tmp_out, codec, preset, qp, audio_codec, hdr: dict = None):
    """
    AMD AMF encoder command (hevc_amf / h264_amf).
    Vereist: /dev/dri device mount in Docker.
    Kwaliteitsparameter via -qp_i / -qp_p (AMF gebruikt geen globale -crf).
    """
    hdr = hdr or {}
    pix_fmt = "p010le" if hdr.get("hdr_type") else "yuv420p"
    cmd = [
        "ffmpeg", "-y",
        "-i", src,
        "-map", "0",
        "-c:v", codec,
        "-quality", preset,   # quality | balanced | speed
        "-qp_i", qp,
        "-qp_p", qp,
        "-pix_fmt", pix_fmt,
    ]
    cmd += _hdr_video_flags(hdr)
    cmd += [
        "-c:a", audio_codec,
        "-c:s", "copy",
    ]
    cmd += [
        "-max_muxing_queue_size", "4096",
        "-progress", "pipe:1",
        "-nostats",
        "-loglevel", "warning",
        tmp_out
    ]
    return cmd

def build_qsv_cmd(src, tmp_out, codec, preset, q, audio_codec, hdr: dict = None):
    """
    Intel QSV encoder command (hevc_qsv / h264_qsv).
    Vereist: /dev/dri device mount + Intel iGPU/Arc in Docker.
    Kwaliteitsparameter via -global_quality (ICQ mode).
    """
    hdr = hdr or {}
    pix_fmt = "p010le" if hdr.get("hdr_type") else "yuv420p"
    cmd = [
        "ffmpeg", "-y",
        "-hwaccel", "qsv",
        "-i", src,
        "-map", "0",
        "-c:v", codec,
        "-preset", preset,
        "-global_quality", q,
        "-look_ahead", "1",
        "-pix_fmt", pix_fmt,
    ]
    cmd += _hdr_video_flags(hdr)
    cmd += [
        "-c:a", audio_codec,
        "-c:s", "copy",
    ]
    cmd += [
        "-max_muxing_queue_size", "4096",
        "-progress", "pipe:1",
        "-nostats",
        "-loglevel", "warning",
        tmp_out
    ]
    return cmd

def build_cpu_cmd(src, tmp_out, codec, preset, crf, audio_codec, hdr: dict = None):
    """CPU ffmpeg command (libx265/libx264) met optionele HDR metadata."""
    hdr = hdr or {}
    pix_fmt = "yuv420p10le" if hdr.get("hdr_type") else "yuv420p"
    cmd = [
        "ffmpeg", "-y",
        "-i", src,
        "-map", "0",
        "-c:v", codec,
        "-preset", preset,
        "-crf", crf,
        "-pix_fmt", pix_fmt,
    ]
    cmd += _hdr_video_flags(hdr)
    cmd += [
        "-c:a", audio_codec,
        "-c:s", "copy",
    ]
    cmd += [
        "-max_muxing_queue_size", "4096",
        "-progress", "pipe:1",
        "-nostats",
        "-loglevel", "warning",
        tmp_out
    ]
    return cmd


def needs_conversion(file_path: str, target_codec: str) -> bool:
    """
    Controleert via ffprobe of het bestand al de doelcodec heeft.
    Gebruikt -select_streams v:0 en een korte timeout voor snelle scan.
    """
    try:
        result = subprocess.run([
            "ffprobe", "-v", "quiet",
            "-select_streams", "v:0",
            "-show_entries", "stream=codec_name",
            "-print_format", "json",
            file_path
        ], capture_output=True, text=True, timeout=5)
        if result.returncode != 0 or not result.stdout.strip():
            return True  # Kan bestand niet lezen — voeg toe voor de zekerheid
        info = json.loads(result.stdout)
        for stream in info.get("streams", []):
            current = stream.get("codec_name", "")
            if "hevc" in target_codec and current in ("hevc", "h265"):
                return False
            if "h264" in target_codec and current == "h264":
                return False
        return True
    except subprocess.TimeoutExpired:
        logger.warning(f"ffprobe timeout bij {Path(file_path).name} — overgeslagen")
        return False  # Timeout = sla over, niet toevoegen (voorkomt vastlopen)
    except Exception as e:
        logger.warning(f"ffprobe fout bij {file_path}: {e}")
        return True

# ── Scanner ───────────────────────────────────────────────────────────────────
def scan_library(library_id: str):
    global scan_status
    conn = get_db()
    lib = conn.execute("SELECT * FROM libraries WHERE id=?", (library_id,)).fetchone()
    if not lib:
        conn.close()
        return

    path = lib["path"]
    scan_status[library_id] = {
        "status": "scanning", "scanned": 0, "added": 0,
        "skipped": 0, "already_converted": 0,
        "current_file": "", "path": path, "error": None
    }

    if not os.path.isdir(path):
        scan_status[library_id]["status"] = "error"
        scan_status[library_id]["error"] = f"Map niet gevonden: {path}"
        logger.error(f"Scan {library_id}: map niet gevonden: {path}")
        conn.close()
        return

    # Log map contents for diagnostics
    try:
        top_entries = os.listdir(path)
        logger.info(f"Scan {library_id}: map '{path}' heeft {len(top_entries)} items: {top_entries[:10]}")
    except Exception as e:
        scan_status[library_id]["status"] = "error"
        scan_status[library_id]["error"] = f"Kan map niet lezen: {e}"
        logger.error(f"Scan {library_id}: kan map niet lezen: {e}")
        conn.close()
        return

    added = skipped = already_converted = scanned = 0

    for root, dirs, files in os.walk(path):
        # Skip hidden directories
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for fname in files:
          try:
            ext = Path(fname).suffix.lower()
            if ext not in VIDEO_EXTENSIONS:
                continue
            fpath = os.path.join(root, fname)
            # Sla tijdelijke Shrync-bestanden over (shryncing-*.mkv)
            if fname.startswith("shryncing-"):
                continue
            # Uitsluitingspatronen toepassen (per bibliotheek)
            # lib is een sqlite3.Row — gebruik try/except voor kolommen
            # die mogelijk niet bestaan in oudere databases
            try:
                exclude_raw = lib["exclude_patterns"] or ""
            except (IndexError, KeyError):
                exclude_raw = ""
            excluded = False
            for pat in [p.strip() for p in exclude_raw.splitlines() if p.strip()]:
                try:
                    if re.search(pat, fname, re.IGNORECASE):
                        excluded = True
                        scan_status[library_id]["skipped"] = scan_status[library_id].get("skipped", 0) + 1
                        scan_status[library_id]["last_skip"] = {"file": fname, "reason": "excluded_pattern", "pattern": pat}
                        logger.debug(f"Scan: uitgesloten door patroon '{pat}': {fname}")
                        break
                except re.error as e:
                    logger.warning(f"Ongeldig uitsluitingspatroon '{pat}': {e} — patroon genegeerd")
            if excluded:
                continue
            scanned += 1
            scan_status[library_id]["scanned"] = scanned
            scan_status[library_id]["current_file"] = fname
            logger.debug(f"Scan: gevonden: {fpath}")

            existing = conn.execute(
                "SELECT id FROM queue WHERE file_path=? AND status IN ('pending','processing')", (fpath,)
            ).fetchone()
            if existing:
                skipped += 1
                scan_status[library_id]["skipped"] = skipped
                scan_status[library_id]["last_skip"] = {"file": fname, "reason": "already_queued"}
                continue

            done = conn.execute(
                "SELECT id FROM history WHERE file_path=? AND status IN ('success','skipped')", (fpath,)
            ).fetchone()
            if done:
                skipped += 1
                scan_status[library_id]["skipped"] = skipped
                scan_status[library_id]["last_skip"] = {"file": fname, "reason": "already_converted"}
                continue

            # Mislukte conversie: verwijder oude foutmelding en voeg opnieuw toe
            # (skipped wordt al hierboven afgehandeld — hier alleen errors)
            failed = conn.execute(
                "SELECT id FROM history WHERE file_path=? AND status='error'", (fpath,)
            ).fetchone()
            if failed:
                conn.execute("DELETE FROM history WHERE file_path=? AND status='error'", (fpath,))
                conn.commit()
                logger.info(f"Scan: mislukte conversie opnieuw toegevoegd: {fpath}")

            profile_id = get_global_setting('conversion_profile', 'nvenc_max')
            global_codec, _, _, _ = profile_to_ffmpeg(profile_id)
            if not needs_conversion(fpath, global_codec):
                already_converted += 1
                scan_status[library_id]["already_converted"] = already_converted
                scan_status[library_id]["last_skip"] = {"file": fname, "reason": "codec_match", "codec": global_codec}
                continue

            fsize = os.path.getsize(fpath)
            jid = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO queue (id,library_id,file_path,file_size,status) VALUES (?,?,?,?,'pending')",
                (jid, library_id, fpath, fsize)
            )
            conn.commit()   # direct committen zodat de worker het meteen oppakt
            added += 1
            scan_status[library_id]["added"] = added
          except Exception as _scan_exc:
            logger.warning(f"Scan: fout bij verwerken {fname}: {_scan_exc}")

    conn.execute("UPDATE libraries SET last_scan=? WHERE id=?", (datetime.now(timezone.utc).isoformat(), library_id))
    conn.commit()
    conn.close()
    scan_status[library_id]["status"] = "done"
    scan_status[library_id]["current_file"] = ""
    logger.info(f"Scan {library_id}: {scanned} gescand, {added} toegevoegd, {skipped} overgeslagen, {already_converted} al H.265")

# ── Conversion ────────────────────────────────────────────────────────────────
def run_conversion(job_id: str):
    conn = get_db()
    job = conn.execute(
        "SELECT q.* FROM queue q WHERE q.id=?",
        (job_id,)
    ).fetchone()
    if not job:
        conn.close()
        return

    src = job["file_path"]
    if not os.path.exists(src):
        conn.execute("UPDATE queue SET status='error', error_msg='Bestand niet gevonden', finished_at=? WHERE id=?",
                     (datetime.now(timezone.utc).isoformat(), job_id))
        conn.commit()
        conn.close()
        return

    # Tijdelijk bestand altijd naast het bronbestand — zelfde map, willekeurige naam
    _src_dir = str(Path(src).parent)
    _rand    = str(uuid.uuid4()).replace("-", "")[:12]
    tmp_out  = os.path.join(_src_dir, f"shryncing-{_rand}.mkv")
    logger.info(f"Tijdelijk bestand: {tmp_out}")
    # Lees conversie-instellingen uit globale settings (niet uit bibliotheek)
    profile = get_global_setting('conversion_profile', 'nvenc_max')
    video_codec, preset, quality, _ = profile_to_ffmpeg(profile)
    audio_codec = get_global_setting('audio_codec', 'copy')

    # Lees filmduur via ffprobe — alleen format sectie, snelle header read
    duration = 0
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet",
             "-show_entries", "format=duration",
             "-print_format", "json", src],
            capture_output=True, text=True, timeout=10
        )
        info = json.loads(r.stdout)
        duration = float(info.get("format", {}).get("duration", 0))
    except:
        pass

    gpu_mode = os.environ.get("GPU_MODE", "cpu").lower()

    # Detecteer HDR metadata in bronbestand
    hdr_info = detect_hdr(src)
    if hdr_info.get("hdr_type"):
        logger.info(f"HDR gedetecteerd: {hdr_info['hdr_type']} — metadata wordt bewaard")

    # Bepaal encoder_type uit profiel (4e veld)
    profile_data = PROFILES.get(profile, None)
    encoder_type = profile_data[3] if profile_data else "cpu"

    # Fallback logica: als gevraagde encoder niet overeenkomt met GPU_MODE
    effective_codec = video_codec
    if encoder_type == "nvidia" and gpu_mode != "nvidia":
        effective_codec = "libx265" if "hevc" in video_codec else "libx264"
        encoder_type = "cpu"
        logger.warning(f"Profiel vraagt NVENC maar GPU_MODE={gpu_mode} — valt terug op CPU: {effective_codec}")
    elif encoder_type == "amd" and gpu_mode != "amd":
        effective_codec = "libx265" if "hevc" in video_codec else "libx264"
        encoder_type = "cpu"
        logger.warning(f"Profiel vraagt AMD AMF maar GPU_MODE={gpu_mode} — valt terug op CPU: {effective_codec}")
    elif encoder_type == "intel" and gpu_mode != "intel":
        effective_codec = "libx265" if "hevc" in video_codec else "libx264"
        encoder_type = "cpu"
        logger.warning(f"Profiel vraagt Intel QSV maar GPU_MODE={gpu_mode} — valt terug op CPU: {effective_codec}")

    # Bouw het juiste ffmpeg command
    if encoder_type == "nvidia":
        cmd = build_nvenc_cmd(src, tmp_out, effective_codec, preset, quality, audio_codec, hdr_info)
    elif encoder_type == "amd":
        cmd = build_amf_cmd(src, tmp_out, effective_codec, preset, quality, audio_codec, hdr_info)
    elif encoder_type == "intel":
        cmd = build_qsv_cmd(src, tmp_out, effective_codec, preset, quality, audio_codec, hdr_info)
    else:
        cmd = build_cpu_cmd(src, tmp_out, effective_codec, preset, quality, audio_codec, hdr_info)

    original_size = os.path.getsize(src)
    conn.execute("UPDATE queue SET status='processing', started_at=?, original_size=?, profile_id=? WHERE id=?",
                 (datetime.now(timezone.utc).isoformat(), original_size, profile, job_id))
    conn.commit()

    start_time = time.time()
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)

    slot_id = threading.current_thread().name
    with active_jobs_lock:
        active_jobs[slot_id] = {"id": job_id, "process": process}

    stderr_lines = []

    # Lees stderr in aparte thread zodat de pipe-buffer niet blokkeert
    def read_stderr():
        for line in process.stderr:
            stderr_lines.append(line)
    stderr_thread = threading.Thread(target=read_stderr, daemon=True)
    stderr_thread.start()

    try:
        # Buffer per progress-blok: ffmpeg schrijft keys in vaste volgorde,
        # eindigend met "progress=". Pas na het complete blok berekenen we
        # ETA zodat out_time_us en fps altijd van hetzelfde moment zijn.
        blok: dict = {}
        for line in process.stdout:
            line = line.strip()
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            blok[key] = val
            if key == "progress":
                # Volledig blok ontvangen — verwerk
                try:
                    out_time_us = int(blok.get("out_time_us", 0))
                    fps_val     = float(blok.get("fps", 0))
                    progress    = 0
                    eta         = ""
                    if duration > 0 and out_time_us > 0:
                        current_sec = out_time_us / 1_000_000
                        progress = min(int((current_sec / duration) * 100), 99)
                        if fps_val > 0:
                            remaining_sec = int((duration - current_sec) / fps_val)
                            eta = f"{remaining_sec // 60}m{remaining_sec % 60}s"
                    conn2 = get_db()
                    conn2.execute("UPDATE queue SET progress=?, fps=?, eta=? WHERE id=?",
                                  (progress, fps_val, eta, job_id))
                    conn2.commit()
                    conn2.close()
                except:
                    pass
                blok = {}  # reset voor volgend blok
        process.wait()
    except Exception as e:
        process.kill()
        logger.error(f"Conversie fout: {e}")

    stderr_thread.join(timeout=30)
    stderr_out = "".join(stderr_lines)

    with active_jobs_lock:
        active_jobs.pop(slot_id, None)

    elapsed = int(time.time() - start_time)
    now = datetime.now(timezone.utc).isoformat()

    if process.returncode == 0 and os.path.exists(tmp_out):
        new_size = os.path.getsize(tmp_out)

        # Als geconverteerd bestand groter is dan origineel:
        # - bij .mp4/.avi/.ts/.wmv/.flv/.mov: remux naar MKV zonder hercodering
        #   (alleen containerwijziging, geen kwaliteitsverlies, geen hercodering)
        # - bij .mkv: origineel behouden, als skipped markeren
        src_ext = Path(src).suffix.lower()
        non_mkv_extensions = {".mp4", ".avi", ".ts", ".wmv", ".flv", ".mov", ".m4v"}

        if new_size >= original_size and src_ext in non_mkv_extensions:
            # Remux naar MKV — alleen containerwissel, geen hercodering
            try: os.remove(tmp_out)
            except: pass
            mkv_out = str(Path(src).with_suffix(".mkv"))
            tmp_remux = os.path.join(str(Path(src).parent), f"remux-{_rand}.mkv")
            logger.info(f"Geconverteerd groter dan origineel — remux {src_ext} → MKV: {Path(src).name}")
            remux_cmd = [
                "ffmpeg", "-y", "-i", src,
                "-map", "0",
                "-c", "copy",
                "-loglevel", "warning",
                tmp_remux
            ]
            remux_result = subprocess.run(remux_cmd, capture_output=True, text=True, timeout=300)
            if remux_result.returncode == 0 and os.path.exists(tmp_remux):
                try:
                    os.remove(src)
                    os.rename(tmp_remux, mkv_out)
                    remux_size = os.path.getsize(mkv_out)
                    logger.info(f"Remux geslaagd: {mkv_out} ({original_size} → {remux_size} bytes)")
                    conn.execute(
                        "INSERT INTO history (id,library_id,file_path,original_size,new_size,duration_seconds,status,finished_at) "
                        "VALUES (?,?,?,?,?,?,'success',?)",
                        (str(uuid.uuid4()), job["library_id"], mkv_out, original_size, remux_size, elapsed, now)
                    )
                    conn.execute("DELETE FROM queue WHERE id=?", (job_id,))
                    conn.commit()
                    conn.close()
                    maybe_queue_subtitle(mkv_out, job["library_id"])
                    return
                except Exception as re:
                    if os.path.exists(tmp_remux):
                        try: os.remove(tmp_remux)
                        except: pass
                    logger.error(f"Remux verplaatsen mislukt: {re}")
            else:
                if os.path.exists(tmp_remux):
                    try: os.remove(tmp_remux)
                    except: pass
                logger.error(f"Remux mislukt: {remux_result.stderr[-500:]}")

        if new_size >= original_size:
            try: os.remove(tmp_out)
            except: pass
            warn_msg = f"Geconverteerd bestand ({new_size} bytes) is groter dan origineel ({original_size} bytes) — origineel behouden"
            logger.warning(warn_msg)
            conn.execute(
                "INSERT INTO history (id,library_id,file_path,original_size,new_size,duration_seconds,status,error_msg,finished_at) "
                "VALUES (?,?,?,?,?,?,'skipped',?,?)",
                (str(uuid.uuid4()), job["library_id"], src, original_size, new_size, elapsed, warn_msg, now)
            )
            conn.execute("DELETE FROM queue WHERE id=?", (job_id,))
            conn.commit()
            conn.close()
            return

        try:
            os.remove(src)
            os.rename(tmp_out, src)
        except Exception as e:
            if os.path.exists(tmp_out):
                try: os.remove(tmp_out)
                except: pass
            err_msg = f"Bestand verplaatsen mislukt: {e}"
            logger.error(err_msg)
            conn.execute(
                "INSERT INTO history (id,library_id,file_path,original_size,new_size,duration_seconds,status,error_msg,finished_at) "
                "VALUES (?,?,?,?,0,?,'error',?,?)",
                (str(uuid.uuid4()), job["library_id"], src, original_size, elapsed, err_msg, now)
            )
            conn.execute("DELETE FROM queue WHERE id=?", (job_id,))
            conn.commit()
            conn.close()
            return

        conn.execute(
            "INSERT INTO history (id,library_id,file_path,original_size,new_size,duration_seconds,status,finished_at) "
            "VALUES (?,?,?,?,?,?,'success',?)",
            (str(uuid.uuid4()), job["library_id"], src, original_size, new_size, elapsed, now)
        )
        conn.execute("DELETE FROM queue WHERE id=?", (job_id,))
        conn.commit()
        conn.close()
        logger.info(f"Conversie geslaagd: {src} ({original_size} -> {new_size} bytes, {elapsed}s)")
        # Voeg toe aan ondertitelwachtrij als functie ingeschakeld is
        maybe_queue_subtitle(src, job["library_id"])
        return
    else:
        # Meest relevante foutmelding uit stderr — laatste 1000 tekens
        error_detail = stderr_out[-1000:].strip() if stderr_out.strip() else f"ffmpeg returncode: {process.returncode}"
        logger.error(f"Conversie mislukt returncode={process.returncode}: {src}")
        if os.path.exists(tmp_out):
            try: os.remove(tmp_out)
            except: pass
        conn.execute(
            "INSERT INTO history (id,library_id,file_path,original_size,new_size,duration_seconds,status,error_msg,finished_at) "
            "VALUES (?,?,?,?,0,?,'error',?,?)",
            (str(uuid.uuid4()), job["library_id"], src, original_size, elapsed, error_detail, now)
        )
        conn.execute("DELETE FROM queue WHERE id=?", (job_id,))

    conn.commit()
    conn.close()


# ── Ondertiteling engine ──────────────────────────────────────────────────────

def get_subtitle_setting(key: str, default: str = '') -> str:
    conn = get_db()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default

def detect_subtitle_streams(file_path: str) -> list:
    """
    Detecteert alle ondertitelsporen in een mediabestand via ffprobe.
    Geeft gesorteerde lijst terug: Engels zonder SDH eerst.
    """
    try:
        result = subprocess.run([
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_streams",
            "-select_streams", "s",
            file_path
        ], capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            return []
        data = json.loads(result.stdout)
        streams = []
        for s in data.get("streams", []):
            tags = s.get("tags", {})
            lang = (tags.get("language") or tags.get("LANGUAGE") or "").lower()
            title = (tags.get("title") or tags.get("TITLE") or "").lower()
            idx = s.get("index", -1)
            codec = s.get("codec_name", "")
            streams.append({
                "index": idx,
                "lang": lang,
                "title": title,
                "codec": codec,
                "is_sdh": any(x in title for x in ["sdh", "hearing", "cc", "forced"]),
            })
        return streams
    except Exception as e:
        logger.warning(f"Ondertitel detectie mislukt voor {file_path}: {e}")
        return []

def pick_best_english_stream(streams: list) -> dict | None:
    """Wrapper voor achterwaartse compatibiliteit."""
    return pick_best_source_stream(streams, "eng")

def pick_best_source_stream(streams: list, source_lang: str = "eng") -> dict | None:
    """
    Kiest het beste ondertitelspoor voor de opgegeven brontaal.
    Voorkeur: niet-SDH. Fallback: elk spoor van die taal.
    """
    # Normaliseer taalcodes (iso 639-2 → varianten)
    lang_variants = {
        "eng": ("eng", "en"), "nld": ("nld", "dut", "nl"),
        "deu": ("deu", "ger", "de"), "fra": ("fra", "fre", "fr"),
        "spa": ("spa", "es"),        "ita": ("ita", "it"),
        "por": ("por", "pt"),        "rus": ("rus", "ru"),
        "jpn": ("jpn", "ja"),        "zho": ("zho", "chi", "zh"),
        "kor": ("kor", "ko"),        "ara": ("ara", "ar"),
        "pol": ("pol", "pl"),        "swe": ("swe", "sv"),
        "nor": ("nor", "nb", "nn"),  "dan": ("dan", "da"),
        "fin": ("fin", "fi"),        "tur": ("tur", "tr"),
    }
    variants = lang_variants.get(source_lang, (source_lang,))
    matches = [s for s in streams if s["lang"] in variants]
    if not matches:
        return None
    normal = [s for s in matches if not s["is_sdh"]]
    return normal[0] if normal else matches[0]

def has_dutch_subtitle(file_path: str) -> bool:
    """
    Controleert of er al een ondertitelbestand aanwezig is voor dit mediabestand.
    Sloeg over als:
    - Er een gelabeld NL bestand naast staat (.nl.srt, .nld.srt, etc.)
    - Er een extern SRT/ASS/SSA bestand naast staat met dezelfde bestandsnaam
      (ongeacht taalcode — extern bestand = al aanwezig, niet opnieuw aanmaken)
    - Er een NL ondertitelstream in het MKV zelf zit
    """
    base   = Path(file_path).stem
    parent = Path(file_path).parent

    # 1. Gelabelde NL bestanden
    for ext in (".nl.srt", ".nld.srt", ".dut.srt", ".nl.forced.srt",
                ".nl.sdh.srt", ".nld.forced.srt"):
        if (parent / (base + ext)).exists():
            logger.debug(f"Ondertitel: gelabeld NL bestand gevonden voor {base}")
            return True

    # 2. Elk extern ondertitelbestand met dezelfde bestandsnaam (zonder taalcode)
    #    bijv. "Film.srt", "Film.ass", "Film.ssa" — als het naast de mediafile staat
    #    beschouwen we het als al aanwezig, ongeacht de taalcode
    for sub_ext in (".srt", ".ass", ".ssa", ".vtt", ".sub"):
        candidate = parent / (base + sub_ext)
        if candidate.exists():
            logger.debug(f"Ondertitel: extern bestand gevonden ({candidate.name}) — overslaan")
            return True
        # Ook bestanden met een taalcode tussenin: Film.en.srt, Film.eng.srt, etc.
        # glob op base + ".*" + sub_ext
        import glob as _glob
        matches = _glob.glob(str(parent / (base + ".*" + sub_ext[1:])))
        if matches:
            logger.debug(f"Ondertitel: extern bestand met taalcode gevonden ({matches[0]}) — overslaan")
            return True

    # 3. NL ondertitelstream in het MKV zelf
    try:
        result = subprocess.run([
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_streams", "-select_streams", "s", file_path
        ], capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            data = json.loads(result.stdout)
            for s in data.get("streams", []):
                tags = s.get("tags", {})
                lang = (tags.get("language") or tags.get("LANGUAGE") or "").lower()
                if lang in ("nld", "dut", "nl"):
                    return True
    except:
        pass

    return False

def extract_srt(file_path: str, stream_index: int) -> str | None:
    """Extraheert een ondertitelspoor als tijdelijk SRT bestand."""
    tmp_path = f"/tmp/shrync_sub_{uuid.uuid4().hex[:8]}.srt"
    try:
        result = subprocess.run([
            "ffmpeg", "-y", "-i", file_path,
            "-map", f"0:{stream_index}",
            "-c:s", "srt",
            tmp_path
        ], capture_output=True, text=True, timeout=60)
        if result.returncode == 0 and os.path.exists(tmp_path):
            return tmp_path
        logger.warning(f"SRT extractie mislukt: {result.stderr[-300:]}")
        return None
    except Exception as e:
        logger.warning(f"SRT extractie fout: {e}")
        return None

def parse_srt(srt_path: str) -> list:
    """
    Parseert een SRT bestand naar een lijst van blokken:
    [{"index": "1", "timing": "00:00:01,000 --> 00:00:03,000", "text": "Hello"}]
    """
    blocks = []
    try:
        with open(srt_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        # Split op lege regels
        raw_blocks = content.strip().split("\n\n")
        for block in raw_blocks:
            lines = block.strip().splitlines()
            if len(lines) < 3:
                continue
            idx_line = lines[0].strip()
            timing_line = lines[1].strip()
            text_lines = lines[2:]
            if "-->" not in timing_line:
                continue
            blocks.append({
                "index": idx_line,
                "timing": timing_line,
                "text": "\n".join(text_lines),
            })
    except Exception as e:
        logger.warning(f"SRT parse fout: {e}")
    return blocks

def write_srt(blocks: list, output_path: str):
    """Schrijft vertaalde blokken terug als SRT bestand."""
    with open(output_path, "w", encoding="utf-8") as f:
        for i, block in enumerate(blocks, 1):
            f.write(f"{i}\n")
            f.write(f"{block['timing']}\n")
            f.write(f"{block['text']}\n\n")

def translate_blocks_ollama(blocks: list, model: str, host: str,
                             job_id: str, update_cb,
                             quality: str = "normal") -> list:
    """
    Vertaalt ondertitelblokken via Ollama.
    Gebruikt unieke ###N### markers als scheiding zodat punten, haakjes en
    dubbele punten in de vertaalde tekst nooit de parsing kunnen verstoren.
    Timecodes worden NOOIT aangepast — alleen de tekst wordt vertaald.

    Verbeteringen tov vorige versie:
    - BATCH_SIZE verlaagd van 20 naar 10: kleinere batches zijn betrouwbaarder
      voor lokale modellen (7B-13B) en verminderen context-verlies
    - num_predict verhoogd van 2048 naar 4096: voorkomt afkappen van de respons
      halverwege een batch, wat onvertaalde zinnen midden in een bestand gaf
    - Retry-logica: als een batch onvolledig terugkomt (> 50% markers mist),
      wordt de batch opnieuw gestuurd als losse blokken van 1 — zo worden
      toch alle blokken vertaald ipv terugvallen op origineel
    """
    # Kwaliteitsprofielen:
    # fast      — batch 5,  num_ctx 4096  — snel, minder context, goed voor series
    # normal    — batch 10, num_ctx 8192  — standaard
    # thorough  — batch 6,  num_ctx 16384 — meer context per blok, beter voor films
    _quality_profiles = {
        "fast":      {"batch": 5,  "num_ctx": 4096},
        "normal":    {"batch": 10, "num_ctx": 8192},
        "thorough":  {"batch": 6,  "num_ctx": 16384},
    }
    _qp = _quality_profiles.get(quality, _quality_profiles["normal"])
    BATCH_SIZE = _qp["batch"]
    _num_ctx   = _qp["num_ctx"]
    translated = []

    source_lang = get_subtitle_setting("subtitle_source_lang", "eng")
    target_lang = get_subtitle_setting("subtitle_target_lang", "nld")

    lang_names = {
        "eng": ("English","Engels"), "nld": ("Dutch","Nederlands"),
        "deu": ("German","Duits"),   "fra": ("French","Frans"),
        "spa": ("Spanish","Spaans"), "ita": ("Italian","Italiaans"),
        "por": ("Portuguese","Portugees"), "rus": ("Russian","Russisch"),
        "jpn": ("Japanese","Japans"), "zho": ("Chinese","Chinees"),
        "kor": ("Korean","Koreaans"), "ara": ("Arabic","Arabisch"),
        "pol": ("Polish","Pools"),   "swe": ("Swedish","Zweeds"),
        "nor": ("Norwegian","Noors"), "dan": ("Danish","Deens"),
        "fin": ("Finnish","Fins"),   "tur": ("Turkish","Turks"),
    }
    src_name = lang_names.get(source_lang, (source_lang, source_lang))[0]
    tgt_name = lang_names.get(target_lang, (target_lang, target_lang))[0]

    system_prompt = (
        f"You are a professional {src_name}-to-{tgt_name} subtitle translator for films and series. "
        f"Your goal is natural, fluent {tgt_name} as it would appear in a professional cinema release — "
        f"NOT a word-for-word translation. Prioritize how native {tgt_name} speakers actually speak."
        f"\n\nRules:"
        f"\n- Adapt idioms and expressions to natural {tgt_name} equivalents"
        f"\n- Restructure sentences when needed for natural flow"
        f"\n- Keep the same register: casual stays casual, formal stays formal, humor stays funny"
        f"\n- Preserve swearing, slang and intensity — do not sanitize"
        f"\n- Contractions and informal speech are preferred over stiff formal language"
        f"\n- Short punchy lines stay short — do not pad with filler words"
        f"\n- Never translate proper names, brand names or untranslatable terms"
        f"\n\nFormat rules:"
        f"\n- Each subtitle is wrapped in ###N### markers"
        f"\n- Output EVERY ###N### marker exactly as given, same order, translated text on the next line"
        f"\n- Do NOT add, remove, merge or reorder any ###N### markers"
        f"\n- Reply ONLY with markers and translated text, no explanation, no notes"
        f"\n\nExamples of good vs bad translation:"
        f"\nBAD: 'Ik heb er geen idee van wat je bedoelt te zeggen.' "
        f"GOOD: 'Ik snap niet wat je bedoelt.'"
        f"\nBAD: 'Dat is niet iets wat ik zou willen doen.' "
        f"GOOD: 'Dat doe ik liever niet.'"
        f"\nBAD: 'Je hebt het goed gedaan, man.' "
        f"GOOD: 'Goed gedaan, man.'"
    )

    marker_pattern = re.compile(r"###(\d+)###")

    def call_ollama(prompt_text: str, timeout_sec: int = 120) -> str:
        """Roep Ollama aan en geef de ruwe respons terug."""
        response = subprocess.run([
            "curl", "-s", "-X", "POST",
            f"{host}/api/generate",
            "-H", "Content-Type: application/json",
            "-d", json.dumps({
                "model": model,
                "system": system_prompt,
                "prompt": prompt_text,
                "stream": False,
                "options": {
                    "temperature": 0.3,
                    "num_predict": min(_num_ctx, 4096),  # max = helft van context window
                    "num_ctx": _num_ctx   # Dynamisch op basis van quality instelling
                }
            })
        ], capture_output=True, text=True, timeout=timeout_sec)
        if response.returncode != 0:
            raise Exception(f"curl fout: {response.stderr[:200]}")
        return json.loads(response.stdout).get("response", "").strip()

    def parse_markers(resp_text: str, batch_size: int) -> dict:
        """Parseer ###N### markers uit de respons. Geeft dict {num: tekst}."""
        result = {}
        parts = marker_pattern.split(resp_text)
        i = 1
        while i < len(parts) - 1:
            try:
                num = int(parts[i])
                text = parts[i+1].strip().replace(" | ", "\n")
                if 1 <= num <= batch_size:
                    result[num] = text
            except (ValueError, IndexError):
                pass
            i += 2
        return result

    def translate_single(block: dict) -> str:
        """Vertaal één enkel blok — fallback als batch mislukt."""
        try:
            text_flat = block["text"].replace("\n", " | ")
            resp = call_ollama(f"###1###\n{text_flat}", timeout_sec=60)
            parsed = parse_markers(resp, 1)
            return parsed.get(1, block["text"])
        except Exception:
            return block["text"]

    for batch_start in range(0, len(blocks), BATCH_SIZE):
        batch = blocks[batch_start:batch_start + BATCH_SIZE]

        # Bouw prompt met unieke ###N### markers
        prompt_lines = []
        for i, block in enumerate(batch):
            text_flat = block["text"].replace("\n", " | ")
            prompt_lines.append(f"###{i+1}###\n{text_flat}")
        prompt = "\n".join(prompt_lines)

        try:
            resp_text = call_ollama(prompt)
            result_lines = parse_markers(resp_text, len(batch))

            found    = len(result_lines)
            expected = len(batch)

            if found < expected * 0.5:
                # Te weinig markers — stuur elk blok individueel opnieuw
                logger.warning(
                    f"Batch {batch_start//BATCH_SIZE + 1}: slechts {found}/{expected} "
                    f"markers ontvangen — elk blok individueel vertalen"
                )
                for i, block in enumerate(batch):
                    translated_text = translate_single(block)
                    translated.append({
                        "index": block["index"],
                        "timing": block["timing"],
                        "text": translated_text,
                    })
            else:
                # Normaal pad: gebruik batch resultaat, val per ontbrekend blok
                # terug op individuele vertaling ipv onvertaald origineel
                for i, block in enumerate(batch):
                    if i+1 in result_lines:
                        translated_text = result_lines[i+1]
                    else:
                        # Dit specifieke blok miste een marker — individueel vertalen
                        logger.debug(f"Marker {i+1} ontbrak — individueel vertalen")
                        translated_text = translate_single(block)
                    translated.append({
                        "index": block["index"],
                        "timing": block["timing"],
                        "text": translated_text,
                    })

        except Exception as e:
            logger.warning(f"Ollama vertaling batch fout: {e} — origineel behouden")
            for block in batch:
                translated.append(block)

        # Voortgang bijwerken
        done = min(batch_start + BATCH_SIZE, len(blocks))
        update_cb(done, len(blocks))

    return translated

def run_subtitle_translation(job_id: str):
    """Voert één ondertitelvertaling uit — draait in eigen thread."""
    conn = get_db()
    job = conn.execute("SELECT * FROM subtitle_queue WHERE id=?", (job_id,)).fetchone()
    if not job:
        conn.close()
        return

    file_path = job["file_path"]
    logger.info(f"Ondertitel vertaling gestart: {Path(file_path).name}")

    conn.execute(
        "UPDATE subtitle_queue SET status='processing', started_at=? WHERE id=?",
        (datetime.now(timezone.utc).isoformat(), job_id)
    )
    conn.commit()

    host        = get_subtitle_setting("ollama_host", "http://localhost:11434")
    model       = get_subtitle_setting("ollama_model", "")
    source_lang = get_subtitle_setting("subtitle_source_lang", "eng")
    target_lang = get_subtitle_setting("subtitle_target_lang", "nld")

    # Sla model op in queue zodat de UI het kan tonen bij actieve jobs
    conn.execute("UPDATE subtitle_queue SET model_used=? WHERE id=?", (model, job_id))
    conn.commit()

    if not model:
        conn.execute(
            "UPDATE subtitle_queue SET status='error', error_msg='Geen Ollama model ingesteld', finished_at=? WHERE id=?",
            (datetime.now(timezone.utc).isoformat(), job_id)
        )
        conn.commit()
        conn.close()
        return

    try:
        # Detecteer ondertitelsporen
        streams = detect_subtitle_streams(file_path)
        best = pick_best_source_stream(streams, source_lang)
        if not best:
            raise Exception(f"Geen {source_lang} ondertitelstream gevonden in dit bestand")

        logger.info(f"Ondertitelspoor gekozen: index {best['index']} ({best['lang']}, SDH={best['is_sdh']})")

        # Extraheer naar tijdelijk SRT
        tmp_srt = extract_srt(file_path, best["index"])
        if not tmp_srt:
            raise Exception("SRT extractie mislukt")

        # Parseer SRT
        blocks = parse_srt(tmp_srt)
        os.remove(tmp_srt)

        if not blocks:
            raise Exception("SRT bestand is leeg of onleesbaar")

        conn.execute(
            "UPDATE subtitle_queue SET lines_total=? WHERE id=?",
            (len(blocks), job_id)
        )
        conn.commit()

        # Voortgang callback
        def update_progress(done: int, total: int):
            pct = int((done / total) * 100) if total > 0 else 0
            c = get_db()
            c.execute(
                "UPDATE subtitle_queue SET progress=?, lines_done=? WHERE id=?",
                (pct, done, job_id)
            )
            c.commit()
            c.close()

        # Vertaal via Ollama
        # Haal subtitle_quality op uit de bibliotheek-instelling
        lib_row = conn.execute("SELECT subtitle_quality FROM libraries WHERE id=?",
                               (job["library_id"] if job["library_id"] else "",)).fetchone()
        try:
            sub_quality = lib_row["subtitle_quality"] if lib_row else "normal"
            if not sub_quality:
                sub_quality = "normal"
        except (IndexError, KeyError):
            sub_quality = "normal"
        translated = translate_blocks_ollama(blocks, model, host, job_id, update_progress,
                                             quality=sub_quality)

        # Schrijf output SRT naast het mediabestand
        base   = Path(file_path).stem
        parent = Path(file_path).parent
        # ISO 639-1 code voor output bestandsnaam
        iso1_map = {"nld":"nl","eng":"en","deu":"de","fra":"fr","spa":"es","ita":"it",
                    "por":"pt","rus":"ru","jpn":"ja","zho":"zh","kor":"ko","ara":"ar",
                    "pol":"pl","swe":"sv","nor":"no","dan":"da","fin":"fi","tur":"tr"}
        tgt_iso1 = iso1_map.get(target_lang, target_lang[:2])
        out_path = str(parent / f"{base}.{tgt_iso1}.srt")
        write_srt(translated, out_path)

        logger.info(f"Ondertitel opgeslagen: {out_path} ({len(translated)} regels)")

        # Geschiedenis opslaan
        hist_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO subtitle_history (id,library_id,file_path,output_path,source_lang,target_lang,"
            "lines_translated,model_used,status,finished_at) VALUES (?,?,?,?,?,?,?,?,'success',?)",
            (hist_id, job["library_id"], file_path, out_path,
             job["source_lang"], job["target_lang"],
             len(translated), model, datetime.now(timezone.utc).isoformat())
        )
        conn.execute("DELETE FROM subtitle_queue WHERE id=?", (job_id,))
        conn.commit()

    except Exception as e:
        logger.error(f"Ondertitel vertaling mislukt: {file_path}: {e}")
        # Fout opslaan in geschiedenis
        hist_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO subtitle_history (id,library_id,file_path,source_lang,target_lang,"
            "model_used,status,error_msg,finished_at) VALUES (?,?,?,?,?,?,'error',?,?)",
            (hist_id, job["library_id"], file_path,
             job["source_lang"], job["target_lang"],
             get_subtitle_setting("ollama_model",""), str(e)[:500],
             datetime.now(timezone.utc).isoformat())
        )
        # Verwijder uit wachtrij — fouten horen in geschiedenis, niet in wachtrij
        conn.execute("DELETE FROM subtitle_queue WHERE id=?", (job_id,))
        conn.commit()
    finally:
        conn.close()

def maybe_queue_subtitle(file_path: str, library_id: str):
    """
    Wordt aangeroepen na een succesvolle conversie.
    Voegt het bestand toe aan de ondertitelwachtrij als:
    - Ondertiteling is ingeschakeld
    - Er geen Nederlandse ondertitel bestaat
    - Er een Engelse ondertitelstream aanwezig is
    """
    if get_subtitle_setting("subtitle_enabled", "0") != "1":
        return
    if has_dutch_subtitle(file_path):
        logger.info(f"Ondertitel: NL al aanwezig voor {Path(file_path).name}")
        return
    source_lang = get_subtitle_setting("subtitle_source_lang", "eng")
    streams = detect_subtitle_streams(file_path)
    best = pick_best_source_stream(streams, source_lang)
    if not best:
        logger.info(f"Ondertitel: geen stream gevonden ({source_lang}) in {Path(file_path).name}")
        return
    conn = get_db()
    existing = conn.execute(
        "SELECT id FROM subtitle_queue WHERE file_path=? AND status IN ('pending','processing')",
        (file_path,)
    ).fetchone()
    if existing:
        conn.close()
        return
    failed = conn.execute(
        "SELECT id FROM subtitle_history WHERE file_path=? AND status='error'",
        (file_path,)
    ).fetchone()
    if failed:
        logger.info(f"Ondertitel: eerder mislukt, wordt overgeslagen voor {Path(file_path).name}")
        conn.close()
        return
    jid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO subtitle_queue (id,library_id,file_path,file_size,subtitle_track_index,status) "
        "VALUES (?,?,?,?,?,'pending')",
        (jid, library_id, file_path, os.path.getsize(file_path), best["index"])
    )
    conn.commit()
    conn.close()
    logger.info(f"Ondertitel wachtrij: {Path(file_path).name} toegevoegd")

# ── Ondertitel dispatcher ─────────────────────────────────────────────────────
_sub_semaphore = threading.Semaphore(1)  # altijd 1 tegelijk — Ollama is single-threaded

def sub_job_thread(job_id: str):
    try:
        run_subtitle_translation(job_id)
    finally:
        _sub_semaphore.release()

def subtitle_dispatcher_loop():
    """
    Wacht tot de conversiewachtrij leeg is voordat ondertitels worden verwerkt.
    Verwerkt altijd maximaal 1 ondertiteling tegelijk.
    """
    logger.info("Ondertitel dispatcher gestart.")
    while _sub_dispatcher_running:
        if get_subtitle_setting("subtitle_enabled", "0") != "1":
            time.sleep(5)
            continue
        try:
            conn = get_db()
            # Wacht als er nog conversies bezig zijn
            active_conversions = conn.execute(
                "SELECT COUNT(*) as c FROM queue WHERE status IN ('pending','processing')"
            ).fetchone()["c"]

            if active_conversions > 0:
                conn.close()
                time.sleep(10)
                continue

            # Haal volgende ondertiteljob op
            job = conn.execute(
                "SELECT id FROM subtitle_queue WHERE status='pending' ORDER BY added_at ASC LIMIT 1"
            ).fetchone()
            conn.close()

            if job:
                acquired = _sub_semaphore.acquire(blocking=False)
                if acquired:
                    c2 = get_db()
                    c2.execute(
                        "UPDATE subtitle_queue SET status='processing' WHERE id=? AND status='pending'",
                        (job["id"],)
                    )
                    c2.commit()
                    c2.close()
                    t = threading.Thread(
                        target=sub_job_thread,
                        args=(job["id"],),
                        name=f"Subtitle-{job['id'][:8]}",
                        daemon=True
                    )
                    t.start()
                else:
                    time.sleep(5)
            else:
                time.sleep(10)
        except Exception as e:
            logger.error(f"Ondertitel dispatcher fout: {e}")
            time.sleep(10)
    logger.info("Ondertitel dispatcher gestopt.")

# ── File watcher ──────────────────────────────────────────────────────────────
class LibraryWatcher(FileSystemEventHandler):
    def __init__(self, library_id: str, library_path: str):
        self.library_id = library_id
        self.library_path = library_path
        self._pending = {}
        self._lock = threading.Lock()

    def on_created(self, event):
        if event.is_directory:
            return
        self._handle(event.src_path)

    def on_moved(self, event):
        if event.is_directory:
            return
        self._handle(event.dest_path)

    def _handle(self, fpath: str):
        if Path(fpath).suffix.lower() not in VIDEO_EXTENSIONS:
            return
        # Bestanden in de cache map negeren
        if Path(fpath).name.startswith("shryncing-"):
            return
        with self._lock:
            self._pending[fpath] = time.time()
        threading.Thread(target=self._delayed_queue, args=(fpath,), daemon=True).start()
        logger.info(f"Watcher: nieuw bestand gedetecteerd: {fpath}")

    def _delayed_queue(self, fpath: str):
        # Wacht tot het bestand stabiel is (klaar met kopiëren)
        # Maximaal 30 minuten wachten, check elke 15 seconden
        max_wait = 30 * 60
        waited = 0
        time.sleep(10)  # eerste wacht
        while waited < max_wait:
            with self._lock:
                if fpath not in self._pending:
                    return  # geannuleerd door een nieuwer event
            try:
                size1 = os.path.getsize(fpath)
                time.sleep(15)
                size2 = os.path.getsize(fpath)
                if size1 == size2 and size1 > 0:
                    break  # bestand is stabiel
                waited += 15
                logger.debug(f"Watcher: bestand nog bezig ({size1} → {size2}): {fpath}")
            except Exception as e:
                logger.warning(f"Watcher: bestand niet leesbaar: {fpath} — {e}")
                return
        else:
            logger.warning(f"Watcher: timeout wachten op stabiel bestand: {fpath}")
            return

        with self._lock:
            if fpath not in self._pending:
                return
            del self._pending[fpath]

        conn = get_db()
        existing = conn.execute(
            "SELECT id FROM queue WHERE file_path=? AND status IN ('pending','processing')", (fpath,)
        ).fetchone()
        done = conn.execute(
            "SELECT id FROM history WHERE file_path=? AND status='success'", (fpath,)
        ).fetchone()
        if existing or done:
            conn.close()
            return
        lib = conn.execute("SELECT * FROM libraries WHERE id=?", (self.library_id,)).fetchone()
        global_codec, _, _, _ = profile_to_ffmpeg(get_global_setting('conversion_profile', 'nvenc_max'))
        if not lib or not needs_conversion(fpath, global_codec):
            conn.close()
            return
        fsize = os.path.getsize(fpath)
        jid = str(uuid.uuid4())
        conn.execute("INSERT INTO queue (id,library_id,file_path,file_size,status) VALUES (?,?,?,?,'pending')",
                     (jid, self.library_id, fpath, fsize))
        conn.commit()
        conn.close()
        logger.info(f"Watcher: bestand toegevoegd aan wachtrij: {fpath}")


def start_watchers():
    global _observers
    for obs in _observers:
        try:
            obs.stop()
        except:
            pass
    _observers = []
    conn = get_db()
    libs = conn.execute("SELECT * FROM libraries WHERE enabled=1").fetchall()
    conn.close()
    for lib in libs:
        if not os.path.isdir(lib["path"]):
            logger.warning(f"Watcher: map niet gevonden: {lib['path']}")
            continue
        handler = LibraryWatcher(lib["id"], lib["path"])
        observer = PollingObserver(timeout=10)  # controleer elke 10 seconden
        observer.schedule(handler, lib["path"], recursive=True)
        observer.start()
        _observers.append(observer)
        logger.info(f"Polling watcher gestart: {lib['path']}")

# ── Dynamische worker dispatcher ─────────────────────────────────────────────
# Geen vaste worker-threads — een dispatcher controleert de wachtrij en start
# per job een losse thread, maximaal get_max_workers() gelijktijdig.
# Threads leven alleen zolang de conversie duurt en stoppen daarna automatisch.

_dispatcher_running = False
_job_semaphore = threading.Semaphore(1)  # wordt bijgewerkt via update_semaphore()
_semaphore_lock = threading.Lock()

def update_semaphore():
    """Pas semaphore aan op de huidige max_workers instelling."""
    global _job_semaphore
    n = get_max_workers()
    with _semaphore_lock:
        _job_semaphore = threading.Semaphore(n)
    logger.info(f"Worker limiet ingesteld op {n}")

def run_job_thread(job_id: str):
    """Voert één conversie uit en geeft de semaphore daarna vrij."""
    try:
        run_conversion(job_id)
    finally:
        _job_semaphore.release()
        logger.debug(f"Job {job_id[:8]} klaar — slot vrijgegeven")

def dispatcher_loop():
    """
    Centrale dispatcher — draait als één achtergrond-thread.
    Polt de wachtrij en start per beschikbare job een losse thread,
    begrensd door de semaphore (= max_workers).
    """
    logger.info("Dispatcher gestart.")
    while _dispatcher_running:
        if workers_paused:
            time.sleep(1)
            continue
        try:
            # Haal alle job-ids op die al actief zijn (in geheugen én in DB)
            with active_jobs_lock:
                active_ids = [v["id"] for v in active_jobs.values()]

            conn2 = get_db()
            # Sluit zowel 'processing' (DB) als actieve in-memory jobs uit
            skip_ids = active_ids[:]
            processing_in_db = [r["id"] for r in conn2.execute(
                "SELECT id FROM queue WHERE status='processing'"
            ).fetchall()]
            for pid in processing_in_db:
                if pid not in skip_ids:
                    skip_ids.append(pid)

            if skip_ids:
                placeholders = ",".join("?" * len(skip_ids))
                job = conn2.execute(
                    f"SELECT id FROM queue WHERE status='pending' AND id NOT IN ({placeholders}) "
                    f"ORDER BY added_at ASC LIMIT 1", skip_ids
                ).fetchone()
            else:
                job = conn2.execute(
                    "SELECT id FROM queue WHERE status='pending' ORDER BY added_at ASC LIMIT 1"
                ).fetchone()
            conn2.close()

            if job:
                # Probeer een slot te bemachtigen (non-blocking)
                acquired = _job_semaphore.acquire(blocking=False)
                if acquired:
                    # Zet status meteen op 'processing' in DB zodat de dispatcher
                    # dit item niet nogmaals oppakt vóór de thread gestart is
                    conn3 = get_db()
                    conn3.execute(
                        "UPDATE queue SET status='processing' WHERE id=? AND status='pending'",
                        (job["id"],)
                    )
                    conn3.commit()
                    conn3.close()
                    logger.info(f"Dispatcher: start conversie {job['id'][:8]}")
                    t = threading.Thread(
                        target=run_job_thread,
                        args=(job["id"],),
                        name=f"Conversie-{job['id'][:8]}",
                        daemon=True
                    )
                    t.start()
                else:
                    # Alle slots bezet — wacht tot er een vrijkomt
                    time.sleep(1)
            else:
                # Geen werk — rustig wachten
                time.sleep(3)
        except Exception as e:
            logger.error(f"Dispatcher fout: {e}")
            time.sleep(5)
    logger.info("Dispatcher gestopt.")


def start_workers():
    global _dispatcher_running, worker_threads, worker_running
    # Stop eventuele oude dispatcher
    _dispatcher_running = False
    worker_running = False
    for t in worker_threads:
        t.join(timeout=2)
    worker_threads = []

    # Semaphore instellen op huidige max_workers
    update_semaphore()

    # Start één dispatcher thread
    _dispatcher_running = True
    worker_running = True
    t = threading.Thread(target=dispatcher_loop, name="Dispatcher", daemon=True)
    t.start()
    worker_threads = [t]
    logger.info(f"Dispatcher actief (max {get_max_workers()} gelijktijdige conversie(s))")


def watcher_monitor():
    """Herstart observers die gestopt zijn (bijv. na een fout)."""
    while worker_running:
        time.sleep(30)
        try:
            dead = [obs for obs in _observers if not obs.is_alive()]
            if dead:
                logger.warning(f"Watcher monitor: {len(dead)} observer(s) gestopt — herstart...")
                start_watchers()
        except Exception as e:
            logger.error(f"Watcher monitor fout: {e}")


def scan_existing_subtitles():
    """
    Controleert bij opstart alle bestanden in actieve bibliotheken:
    voegt toe aan ondertitelwachtrij als er geen NL ondertitel is
    maar wel een Engelse stream aanwezig is.
    Alleen als subtitle_enabled=1.
    """
    if get_subtitle_setting("subtitle_enabled", "0") != "1":
        return
    logger.info("Ondertitel opstartscan gestart...")
    conn = get_db()
    libs = conn.execute("SELECT * FROM libraries WHERE enabled=1").fetchall()
    conn.close()
    added = 0
    for lib in libs:
        path = lib["path"]
        if not os.path.isdir(path):
            continue
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for fname in files:
                if Path(fname).suffix.lower() not in VIDEO_EXTENSIONS:
                    continue
                if fname.startswith("shryncing-"):
                    continue
                fpath = os.path.join(root, fname)
                # Sla over als al in wachtrij
                c = get_db()
                existing = c.execute(
                    "SELECT id FROM subtitle_queue WHERE file_path=? AND status IN ('pending','processing')",
                    (fpath,)
                ).fetchone()
                already_done = c.execute(
                    "SELECT id FROM subtitle_history WHERE file_path=? AND status='success'",
                    (fpath,)
                ).fetchone()
                already_failed = c.execute(
                    "SELECT id FROM subtitle_history WHERE file_path=? AND status='error'",
                    (fpath,)
                ).fetchone()
                c.close()
                if existing or already_done or already_failed:
                    continue
                if has_dutch_subtitle(fpath):
                    continue
                source_lang = get_subtitle_setting("subtitle_source_lang", "eng")
                streams = detect_subtitle_streams(fpath)
                best = pick_best_source_stream(streams, source_lang)
                if not best:
                    continue
                c = get_db()
                jid = str(uuid.uuid4())
                c.execute(
                    "INSERT INTO subtitle_queue (id,library_id,file_path,file_size,subtitle_track_index,status) "
                    "VALUES (?,?,?,?,?,'pending')",
                    (jid, lib["id"], fpath, os.path.getsize(fpath), best["index"])
                )
                c.commit()
                c.close()
                added += 1
    logger.info(f"Ondertitel opstartscan klaar: {added} bestand(en) toegevoegd aan wachtrij")

def start_subtitle_dispatcher():
    global _sub_dispatcher_running
    _sub_dispatcher_running = True
    t = threading.Thread(target=subtitle_dispatcher_loop, name="SubtitleDispatcher", daemon=True)
    t.start()
    logger.info("Ondertitel dispatcher actief.")

def initial_startup():
    logger.info("Shrync opstart — workers en scans starten...")
    # Start workers EERST zodat ze meteen kunnen beginnen zodra items in de wachtrij komen
    start_workers()
    start_subtitle_dispatcher()
    # Scans parallel uitvoeren — één thread per bibliotheek zodat ze
    # de worker-start niet blokkeren
    conn = get_db()
    libs = conn.execute("SELECT * FROM libraries WHERE enabled=1").fetchall()
    conn.close()
    for lib in libs:
        threading.Thread(
            target=scan_library,
            args=(lib["id"],),
            name=f"Scan-{lib['id'][:8]}",
            daemon=True
        ).start()
    start_watchers()
    threading.Thread(target=watcher_monitor, daemon=True).start()
    logger.info("Live monitoring actief.")
    # Ondertitel opstartscan in aparte thread
    threading.Thread(target=scan_existing_subtitles, daemon=True).start()

# ── App lifecycle via lifespan context (zie boven) ───────────────────────────

# ── API Routes ────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")

@app.get("/api/stats")
def api_stats():
    conn = get_db()
    pending    = conn.execute("SELECT COUNT(*) as c FROM queue WHERE status='pending'").fetchone()["c"]
    processing = conn.execute("SELECT COUNT(*) as c FROM queue WHERE status='processing'").fetchone()["c"]
    done_today = conn.execute("SELECT COUNT(*) as c FROM history WHERE status='success' AND date(finished_at)=date('now')").fetchone()["c"]
    errors     = conn.execute("SELECT COUNT(*) as c FROM history WHERE status='error'").fetchone()["c"]
    saved      = conn.execute("SELECT SUM(original_size-new_size) as s FROM history WHERE status='success'").fetchone()["s"] or 0
    libs       = conn.execute("SELECT COUNT(*) as c FROM libraries WHERE enabled=1").fetchone()["c"]

    # Geschatte eindtijd — gemiddelde conversieduur van laatste 20 voltooide jobs
    eta_seconds = None
    if pending > 0:
        recent = conn.execute(
            "SELECT duration_seconds FROM history WHERE status='success' AND duration_seconds > 0 "
            "ORDER BY finished_at DESC LIMIT 20"
        ).fetchall()
        if recent:
            avg_dur = sum(r["duration_seconds"] for r in recent) / len(recent)
            max_workers = int(conn.execute("SELECT value FROM settings WHERE key='max_workers'").fetchone()["value"] or 1)
            eta_seconds = int((pending * avg_dur) / max(max_workers, 1))

    conn.close()
    return {"pending": pending, "processing": processing, "done_today": done_today,
            "errors": errors, "saved_bytes": saved, "active_libraries": libs,
            "eta_seconds": eta_seconds}

@app.get("/api/recent")
def api_recent():
    """Laatste 5 succesvol verwerkte bestanden voor het dashboard."""
    conn = get_db()
    rows = conn.execute(
        "SELECT h.*, l.name as library_name FROM history h "
        "LEFT JOIN libraries l ON h.library_id = l.id "
        "WHERE h.status = 'success' "
        "ORDER BY h.finished_at DESC LIMIT 5"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.get("/api/savings")
def api_savings():
    conn = get_db()

    # Haal alle succesvolle history items op — zelfde bron als geschiedenis pagina
    rows = conn.execute(
        "SELECT h.*, l.name as library_name FROM history h "
        "LEFT JOIN libraries l ON h.library_id = l.id "
        "WHERE h.status = 'success'"
    ).fetchall()

    conn.close()

    if not rows:
        return {"totals": {}, "per_library": [], "daily": []}

    # Bereken totalen in Python — betrouwbaarder dan SQL aggregates op lege sets
    total_files    = len(rows)
    total_original = sum(r["original_size"] or 0 for r in rows)
    total_new      = sum(r["new_size"] or 0 for r in rows)
    total_saved    = total_original - total_new

    totals = {
        "total_files":    total_files,
        "total_original": total_original,
        "total_new":      total_new,
        "total_saved":    total_saved,
    }

    # Per bibliotheek
    libs_map = {}
    for r in rows:
        key = r["library_id"] or "onbekend"
        if key not in libs_map:
            libs_map[key] = {
                "library_name": r["library_name"] or "Onbekend",
                "files": 0,
                "original": 0,
                "new_size": 0,
                "saved": 0,
            }
        libs_map[key]["files"]    += 1
        libs_map[key]["original"] += r["original_size"] or 0
        libs_map[key]["new_size"] += r["new_size"] or 0
        libs_map[key]["saved"]    += (r["original_size"] or 0) - (r["new_size"] or 0)

    per_library = sorted(libs_map.values(), key=lambda x: x["saved"], reverse=True)

    # Per dag (laatste 30 dagen)
    daily_map = {}
    for r in rows:
        if not r["finished_at"]:
            continue
        day = r["finished_at"][:10]  # YYYY-MM-DD
        if day not in daily_map:
            daily_map[day] = {"day": day, "files": 0, "saved": 0}
        daily_map[day]["files"] += 1
        daily_map[day]["saved"] += (r["original_size"] or 0) - (r["new_size"] or 0)

    daily = sorted(daily_map.values(), key=lambda x: x["day"])

    return {"totals": totals, "per_library": per_library, "daily": daily}

@app.get("/api/libraries")
def api_get_libraries():
    conn = get_db()
    libs = conn.execute("SELECT * FROM libraries ORDER BY name").fetchall()
    conn.close()
    return [dict(l) for l in libs]

@app.post("/api/libraries")
def api_create_library(lib: LibraryCreate):
    lid = str(uuid.uuid4())
    conn = get_db()
    conn.execute(
        "INSERT INTO libraries (id,name,path,exclude_patterns,subtitle_quality) VALUES (?,?,?,?,?)",
        (lid, lib.name, lib.path,
         lib.exclude_patterns or "", lib.subtitle_quality or "normal")
    )
    conn.commit()
    conn.close()
    threading.Thread(target=scan_library, args=(lid,), daemon=True).start()
    threading.Thread(target=start_watchers, daemon=True).start()
    return {"id": lid}

@app.put("/api/libraries/{lid}")
def api_update_library(lid: str, lib: LibraryUpdate):
    conn = get_db()
    conn.execute(
        "UPDATE libraries SET name=?,path=?,enabled=?,exclude_patterns=?,subtitle_quality=? WHERE id=?",
        (lib.name, lib.path, 1 if lib.enabled else 0,
         lib.exclude_patterns or "", lib.subtitle_quality or "normal", lid)
    )
    conn.commit()
    conn.close()
    threading.Thread(target=start_watchers, daemon=True).start()
    return {"ok": True}

@app.delete("/api/libraries/{lid}")
def api_delete_library(lid: str):
    conn = get_db()
    conn.execute("DELETE FROM libraries WHERE id=?", (lid,))
    conn.commit()
    conn.close()
    return {"ok": True}

@app.post("/api/libraries/{lid}/scan")
def api_scan_library(lid: str):
    threading.Thread(target=scan_library, args=(lid,), daemon=True).start()
    return {"ok": True}

@app.get("/api/libraries/{lid}/scan-status")
def api_scan_status_single(lid: str):
    return scan_status.get(lid, {"status": "idle"})

@app.get("/api/scan-status")
def api_all_scan_status():
    return scan_status

@app.get("/api/libraries/{lid}/skipped")
def api_skipped_files(lid: str):
    """
    Geeft de bestanden terug die de laatste scan heeft overgeslagen voor deze bibliotheek.
    Bron: history (al geconverteerd) + queue (al in wachtrij).
    Dit zijn de bestanden die de scan bewust heeft overgeslagen — niet de codec_match bestanden.
    """
    conn = get_db()
    lib = conn.execute("SELECT * FROM libraries WHERE id=?", (lid,)).fetchone()
    if not lib:
        conn.close()
        raise HTTPException(404, "Bibliotheek niet gevonden")

    skipped = []

    # Bestanden die al succesvol geconverteerd zijn (in history)
    history_rows = conn.execute(
        "SELECT file_path, finished_at FROM history WHERE library_id=? AND status='success' ORDER BY finished_at DESC",
        (lid,)
    ).fetchall()
    for row in history_rows:
        fpath = row["file_path"]
        fname = Path(fpath).name
        skipped.append({
            "file": fname,
            "path": fpath,
            "reason": "already_converted",
            "date": row["finished_at"],
        })

    # Bestanden die al in de wachtrij staan
    queue_rows = conn.execute(
        "SELECT file_path, status, added_at FROM queue WHERE library_id=? ORDER BY added_at DESC",
        (lid,)
    ).fetchall()
    for row in queue_rows:
        fpath = row["file_path"]
        fname = Path(fpath).name
        skipped.append({
            "file": fname,
            "path": fpath,
            "reason": "already_queued",
            "status": row["status"],
            "date": row["added_at"],
        })

    conn.close()
    return {"library": dict(lib), "skipped": skipped, "total": len(skipped)}

@app.get("/api/savings/chart")
def api_savings_chart():
    """Wekelijkse besparingsdata voor de afgelopen 8 weken."""
    conn = get_db()
    rows = conn.execute("""
        SELECT
            strftime('%W-%Y', finished_at) as week_key,
            strftime('%d/%m', MIN(finished_at)) as week_label,
            SUM(original_size - new_size) as saved_bytes,
            COUNT(*) as files
        FROM history
        WHERE status='success'
          AND finished_at >= datetime('now', '-56 days')
        GROUP BY week_key
        ORDER BY week_key ASC
        LIMIT 8
    """).fetchall()
    conn.close()
    if not rows:
        return {"labels": [], "values_gb": [], "files": []}
    return {
        "labels":    [r["week_label"] for r in rows],
        "values_gb": [round((r["saved_bytes"] or 0) / (1024**3), 2) for r in rows],
        "files":     [r["files"] for r in rows],
    }

@app.get("/api/queue")
def api_queue(status: Optional[str] = None):
    conn = get_db()
    if status:
        rows = conn.execute(
            "SELECT q.*, l.name as library_name FROM queue q LEFT JOIN libraries l ON q.library_id=l.id "
            "WHERE q.status=? ORDER BY q.added_at DESC LIMIT 100", (status,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT q.*, l.name as library_name FROM queue q LEFT JOIN libraries l ON q.library_id=l.id "
            "WHERE q.status IN ('pending','processing','error') ORDER BY q.status DESC, q.added_at ASC LIMIT 200"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.delete("/api/queue/{jid}")
def api_remove_queue(jid: str):
    conn = get_db()
    job = conn.execute("SELECT * FROM queue WHERE id=?", (jid,)).fetchone()
    if job and job["status"] == "processing":
        with active_jobs_lock:
            for slot in active_jobs.values():
                if slot["id"] == jid and slot["process"]:
                    slot["process"].kill()
    conn.execute("DELETE FROM queue WHERE id=?", (jid,))
    conn.commit()
    conn.close()
    return {"ok": True}

@app.post("/api/queue/add")
def api_add_to_queue(data: dict):
    file_path = data.get("file_path", "")
    library_id = data.get("library_id")
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(400, "Bestand niet gevonden")
    conn = get_db()
    existing = conn.execute(
        "SELECT id FROM queue WHERE file_path=? AND status IN ('pending','processing')", (file_path,)
    ).fetchone()
    if existing:
        conn.close()
        raise HTTPException(400, "Al in wachtrij")
    jid = str(uuid.uuid4())
    fsize = os.path.getsize(file_path)
    conn.execute("INSERT INTO queue (id,library_id,file_path,file_size) VALUES (?,?,?,?)",
                 (jid, library_id, file_path, fsize))
    conn.commit()
    conn.close()
    return {"id": jid}

@app.get("/api/settings")
def api_get_settings():
    conn = get_db()
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    conn.close()
    return {r["key"]: r["value"] for r in rows}

@app.post("/api/settings")
def api_save_settings(data: dict):
    conn = get_db()
    for key, value in data.items():
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, str(value)))
    conn.commit()
    conn.close()
    if "max_workers" in data:
        # Semaphore bijwerken — loopt direct door zonder workers te herstarten
        threading.Thread(target=update_semaphore, daemon=True).start()
    return {"ok": True}

@app.post("/api/workers/pause")
def api_pause_workers():
    global workers_paused
    workers_paused = True
    # Stuur SIGSTOP naar alle actieve ffmpeg-processen
    import signal
    with active_jobs_lock:
        for slot in active_jobs.values():
            proc = slot.get("process")
            if proc and proc.poll() is None:
                try:
                    os.kill(proc.pid, signal.SIGSTOP)
                except Exception:
                    pass
    return {"paused": True}

@app.post("/api/workers/resume")
def api_resume_workers():
    global workers_paused
    workers_paused = False
    # Stuur SIGCONT naar alle gepauzeerde ffmpeg-processen
    import signal
    with active_jobs_lock:
        for slot in active_jobs.values():
            proc = slot.get("process")
            if proc and proc.poll() is None:
                try:
                    os.kill(proc.pid, signal.SIGCONT)
                except Exception:
                    pass
    return {"paused": False}

@app.get("/api/workers/status")
def api_workers_status():
    with active_jobs_lock:
        active = len(active_jobs)
    return {"paused": workers_paused, "active": active, "running": worker_running}

@app.get("/api/diagnostics")
def api_diagnostics():
    """Toont wat de container ziet — handig voor probleemoplossing."""
    conn = get_db()
    libs = conn.execute("SELECT * FROM libraries").fetchall()
    conn.close()

    results = []
    for lib in libs:
        path = lib["path"]
        info = {"id": lib["id"], "name": lib["name"], "path": path}

        if not os.path.exists(path):
            info["error"] = f"Pad bestaat NIET in container: {path}"
            info["files"] = []
            results.append(info)
            continue

        if not os.path.isdir(path):
            info["error"] = f"Pad is geen map: {path}"
            info["files"] = []
            results.append(info)
            continue

        # List top-level contents
        try:
            top = os.listdir(path)
            info["top_level_count"] = len(top)
            info["top_level_sample"] = top[:20]
        except Exception as e:
            info["error"] = f"Kan map niet lezen: {e}"
            results.append(info)
            continue

        # Count video files recursively
        video_count = 0
        video_sample = []
        try:
            for root, dirs, files in os.walk(path):
                dirs[:] = [d for d in dirs if not d.startswith('.')]
                for fname in files:
                    if Path(fname).suffix.lower() in VIDEO_EXTENSIONS:
                        video_count += 1
                        if len(video_sample) < 5:
                            video_sample.append(os.path.join(root, fname))
        except Exception as e:
            info["walk_error"] = str(e)

        info["video_files_found"] = video_count
        info["video_sample"] = video_sample
        results.append(info)

    # Also show /media contents
    media_root = {}
    if os.path.exists("/media"):
        try:
            media_root["contents"] = os.listdir("/media")
        except Exception as e:
            media_root["error"] = str(e)
    else:
        media_root["error"] = "/media bestaat niet"

    return {"libraries": results, "media_root": media_root}

@app.get("/api/config")
def api_config():
    """Geeft runtime configuratie terug zodat de UI weet welke functies beschikbaar zijn."""
    gpu_mode = os.environ.get("GPU_MODE", "cpu").lower()
    return {
        "gpu_available": gpu_mode in ("nvidia", "amd", "intel"),
        "gpu_mode":      gpu_mode,
        "version":       SHRYNC_VERSION,
    }


@app.get("/api/gpu-monitor")
def api_gpu_monitor():
    """
    Live GPU statistieken voor het dashboard.
    Ondersteunt Nvidia (nvidia-smi), AMD en Intel (/sys/class/drm).
    Returnt altijd een geldig object — nooit een error response.
    """
    gpu_mode = os.environ.get("GPU_MODE", "cpu").lower()
    result = {"mode": gpu_mode, "available": False, "gpus": []}

    if gpu_mode == "nvidia":
        try:
            # Stap 1: GPU stats + encoder utilization (werkt op alle driver versies)
            out = subprocess.run([
                "nvidia-smi",
                "--query-gpu=name,utilization.gpu,utilization.memory,"
                "memory.used,memory.total,temperature.gpu,utilization.encoder",
                "--format=csv,noheader,nounits"
            ], capture_output=True, text=True, timeout=5)
            if out.returncode == 0:
                for line in out.stdout.strip().splitlines():
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) >= 6:
                        enc_util = int(parts[6]) if len(parts) > 6 and parts[6].isdigit() else 0
                        result["gpus"].append({
                            "name":         parts[0],
                            "gpu_util":     int(parts[1]) if parts[1].isdigit() else 0,
                            "mem_util":     int(parts[2]) if parts[2].isdigit() else 0,
                            "mem_used_mb":  int(parts[3]) if parts[3].isdigit() else 0,
                            "mem_total_mb": int(parts[4]) if parts[4].isdigit() else 0,
                            "temperature":  int(parts[5]) if parts[5].isdigit() else 0,
                            "enc_util":     enc_util,
                            "enc_sessions": 0,  # wordt hieronder ingevuld
                        })
                result["available"] = len(result["gpus"]) > 0

            # Stap 2: encoder sessies ophalen via aparte query (niet altijd beschikbaar)
            if result["available"]:
                out2 = subprocess.run([
                    "nvidia-smi",
                    "--query-accounted-apps=gpu_name",
                    "--format=csv,noheader"
                ], capture_output=True, text=True, timeout=3)
                # Teller: actieve ffmpeg processen tellen als betrouwbaardere bron
                import subprocess as _sp
                ps = _sp.run(["pgrep", "-c", "ffmpeg"], capture_output=True, text=True)
                ffmpeg_count = int(ps.stdout.strip()) if ps.returncode == 0 and ps.stdout.strip().isdigit() else 0
                for gpu in result["gpus"]:
                    gpu["enc_sessions"] = ffmpeg_count

        except Exception as e:
            logger.debug(f"nvidia-smi fout: {e}")

    elif gpu_mode in ("amd", "intel"):
        # AMD en Intel: basis info via /sys/class/drm
        try:
            import glob as _glob
            gpu_entries = []
            for card in sorted(_glob.glob("/sys/class/drm/card[0-9]")):
                name_path = f"{card}/device/product_name"
                util_path = f"{card}/device/gpu_busy_percent"
                name = open(name_path).read().strip() if os.path.exists(name_path) else (
                    "AMD GPU" if gpu_mode == "amd" else "Intel GPU"
                )
                util = 0
                if os.path.exists(util_path):
                    try: util = int(open(util_path).read().strip())
                    except: pass
                gpu_entries.append({"name": name, "gpu_util": util,
                                    "mem_util": 0, "mem_used_mb": 0,
                                    "mem_total_mb": 0, "temperature": 0,
                                    "enc_sessions": 0})
            if gpu_entries:
                result["gpus"]      = gpu_entries
                result["available"] = True
        except Exception as e:
            logger.debug(f"DRM GPU info fout: {e}")

    return result


@app.get("/api/profiles")
def api_get_profiles():
    """Geeft alle beschikbare encoder profielen terug met encoder_type label."""
    return [
        # ── Nvidia NVENC ──────────────────────────────────────────────────────
        {"id": "nvenc_max",      "label": "NVENC H.265 — Max kwaliteit",    "codec": "hevc_nvenc", "encoder": "nvidia"},
        {"id": "nvenc_high",     "label": "NVENC H.265 — Hoge kwaliteit",   "codec": "hevc_nvenc", "encoder": "nvidia"},
        {"id": "nvenc_balanced", "label": "NVENC H.265 — Gebalanceerd",     "codec": "hevc_nvenc", "encoder": "nvidia"},
        {"id": "h264_nvenc",     "label": "NVENC H.264 — Hoge kwaliteit",   "codec": "h264_nvenc", "encoder": "nvidia"},
        # ── AMD AMF ───────────────────────────────────────────────────────────
        {"id": "amf_max",        "label": "AMF H.265 — Max kwaliteit",      "codec": "hevc_amf",   "encoder": "amd"},
        {"id": "amf_balanced",   "label": "AMF H.265 — Gebalanceerd",       "codec": "hevc_amf",   "encoder": "amd"},
        {"id": "h264_amf",       "label": "AMF H.264 — Hoge kwaliteit",     "codec": "h264_amf",   "encoder": "amd"},
        # ── Intel QSV ─────────────────────────────────────────────────────────
        {"id": "qsv_max",        "label": "QSV H.265 — Max kwaliteit",      "codec": "hevc_qsv",   "encoder": "intel"},
        {"id": "qsv_balanced",   "label": "QSV H.265 — Gebalanceerd",       "codec": "hevc_qsv",   "encoder": "intel"},
        {"id": "h264_qsv",       "label": "QSV H.264 — Hoge kwaliteit",     "codec": "h264_qsv",   "encoder": "intel"},
        # ── CPU ───────────────────────────────────────────────────────────────
        {"id": "cpu_slow",       "label": "CPU H.265 — Max kwaliteit",      "codec": "libx265",    "encoder": "cpu"},
        {"id": "cpu_medium",     "label": "CPU H.265 — Gebalanceerd",       "codec": "libx265",    "encoder": "cpu"},
        {"id": "cpu_fast",       "label": "CPU H.265 — Snel",               "codec": "libx265",    "encoder": "cpu"},
        {"id": "h264_cpu",       "label": "CPU H.264 — Gebalanceerd",       "codec": "libx264",    "encoder": "cpu"},
    ]

@app.get("/api/history")
def api_history(page: int = 1, per_page: int = 50, search: str = "",
                sort: str = "finished_at", dir: str = "desc"):
    offset = (page - 1) * per_page
    # Toegestane sorteerkolommen (SQL injection preventie)
    allowed_sort = {"file_path","library_name","finished_at","status"}
    sort_col = sort if sort in allowed_sort else "finished_at"
    sort_dir = "DESC" if dir.lower() == "desc" else "ASC"
    conn = get_db()
    if search:
        like = f"%{search}%"
        total = conn.execute(
            "SELECT COUNT(*) as c FROM history h LEFT JOIN libraries l ON h.library_id=l.id "
            "WHERE h.file_path LIKE ? OR l.name LIKE ?", (like, like)
        ).fetchone()["c"]
        rows = conn.execute(
            f"SELECT h.*, l.name as library_name FROM history h LEFT JOIN libraries l ON h.library_id=l.id "
            f"WHERE h.file_path LIKE ? OR l.name LIKE ? "
            f"ORDER BY {sort_col} {sort_dir} LIMIT ? OFFSET ?",
            (like, like, per_page, offset)
        ).fetchall()
    else:
        total = conn.execute("SELECT COUNT(*) as c FROM history").fetchone()["c"]
        rows = conn.execute(
            f"SELECT h.*, l.name as library_name FROM history h LEFT JOIN libraries l ON h.library_id=l.id "
            f"ORDER BY {sort_col} {sort_dir} LIMIT ? OFFSET ?", (per_page, offset)
        ).fetchall()
    conn.close()
    return {"total": total, "page": page, "items": [dict(r) for r in rows]}

@app.delete("/api/history/{hid}")
def api_delete_history_item(hid: str):
    conn = get_db()
    conn.execute("DELETE FROM history WHERE id=?", (hid,))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.post("/api/history/{hid}/retry")
def api_retry_history(hid: str):
    """Zet een mislukte conversie opnieuw in de wachtrij."""
    conn = get_db()
    item = conn.execute("SELECT * FROM history WHERE id=? AND status='error'", (hid,)).fetchone()
    if not item:
        conn.close()
        raise HTTPException(404, "Niet gevonden of niet mislukt")
    file_path = item["file_path"]
    if not os.path.exists(file_path):
        conn.close()
        raise HTTPException(400, "Bronbestand bestaat niet meer")
    # Controleer of al in wachtrij
    existing = conn.execute(
        "SELECT id FROM queue WHERE file_path=? AND status IN ('pending','processing')", (file_path,)
    ).fetchone()
    if existing:
        conn.close()
        raise HTTPException(400, "Al in wachtrij")
    # Verwijder oude foutmelding uit geschiedenis
    conn.execute("DELETE FROM history WHERE id=?", (hid,))
    # Toevoegen aan wachtrij
    jid = str(uuid.uuid4())
    fsize = os.path.getsize(file_path)
    conn.execute(
        "INSERT INTO queue (id,library_id,file_path,file_size,status) VALUES (?,?,?,?,'pending')",
        (jid, item["library_id"], file_path, fsize)
    )
    conn.commit()
    conn.close()
    return {"id": jid}

@app.delete("/api/history")
def api_clear_history():
    conn = get_db()
    conn.execute("DELETE FROM history")
    conn.commit()
    conn.close()
    return {"ok": True}


# ══════════════════════════════════════════════════════════════════════════════
# ── Ondertiteling API routes ──────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/subtitle/stats")
def api_subtitle_stats():
    """Dashboard tegel data voor ondertiteling."""
    conn = get_db()
    pending    = conn.execute("SELECT COUNT(*) as c FROM subtitle_queue WHERE status='pending'").fetchone()["c"]
    processing = conn.execute("SELECT COUNT(*) as c FROM subtitle_queue WHERE status='processing'").fetchone()["c"]
    done_today = conn.execute(
        "SELECT COUNT(*) as c FROM subtitle_history WHERE status='success' AND date(finished_at)=date('now')"
    ).fetchone()["c"]
    errors = conn.execute("SELECT COUNT(*) as c FROM subtitle_history WHERE status='error'").fetchone()["c"]
    total  = conn.execute("SELECT COUNT(*) as c FROM subtitle_history WHERE status='success'").fetchone()["c"]
    conn.close()
    return {
        "pending": pending, "processing": processing,
        "done_today": done_today, "errors": errors, "total": total,
        "enabled": get_subtitle_setting("subtitle_enabled", "0") == "1"
    }

@app.get("/api/subtitle/queue")
def api_subtitle_queue(page: int = 1, per_page: int = 50):
    offset = (page - 1) * per_page
    conn = get_db()
    total = conn.execute(
        "SELECT COUNT(*) as c FROM subtitle_queue WHERE status IN ('pending','processing','error')"
    ).fetchone()["c"]
    rows = conn.execute(
        "SELECT sq.*, l.name as library_name FROM subtitle_queue sq "
        "LEFT JOIN libraries l ON sq.library_id=l.id "
        "WHERE sq.status IN ('pending','processing','error') "
        "ORDER BY sq.status DESC, sq.added_at ASC LIMIT ? OFFSET ?",
        (per_page, offset)
    ).fetchall()
    conn.close()
    return {"total": total, "page": page, "per_page": per_page, "items": [dict(r) for r in rows]}

@app.get("/api/subtitle/history")
def api_subtitle_history(page: int = 1, per_page: int = 50):
    offset = (page - 1) * per_page
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) as c FROM subtitle_history").fetchone()["c"]
    rows = conn.execute(
        "SELECT sh.*, l.name as library_name FROM subtitle_history sh "
        "LEFT JOIN libraries l ON sh.library_id=l.id "
        "ORDER BY sh.finished_at DESC LIMIT ? OFFSET ?",
        (per_page, offset)
    ).fetchall()
    conn.close()
    return {"total": total, "page": page, "items": [dict(r) for r in rows]}

@app.delete("/api/subtitle/queue/{jid}")
def api_subtitle_queue_remove(jid: str):
    conn = get_db()
    conn.execute("DELETE FROM subtitle_queue WHERE id=?", (jid,))
    conn.commit()
    conn.close()
    return {"ok": True}

@app.delete("/api/subtitle/queue")
def api_subtitle_queue_clear():
    """Verwijdert alle pending items uit de ondertitelwachtrij. Lopende jobs blijven."""
    conn = get_db()
    deleted = conn.execute(
        "DELETE FROM subtitle_queue WHERE status='pending'"
    ).rowcount
    conn.commit()
    conn.close()
    return {"ok": True, "deleted": deleted}

@app.post("/api/subtitle/retry/{hid}")
def api_subtitle_retry(hid: str):
    conn = get_db()
    item = conn.execute(
        "SELECT * FROM subtitle_history WHERE id=? AND status='error'", (hid,)
    ).fetchone()
    if not item:
        conn.close()
        raise HTTPException(404, "Niet gevonden of niet mislukt")
    if not os.path.exists(item["file_path"]):
        conn.close()
        raise HTTPException(400, "Bronbestand bestaat niet meer")
    streams = detect_subtitle_streams(item["file_path"])
    best = pick_best_english_stream(streams)
    if not best:
        conn.close()
        raise HTTPException(400, "Geen Engelse ondertitelstream gevonden")
    jid = str(uuid.uuid4())
    conn.execute("DELETE FROM subtitle_history WHERE id=?", (hid,))
    conn.execute(
        "INSERT INTO subtitle_queue (id,library_id,file_path,file_size,subtitle_track_index,status) "
        "VALUES (?,?,?,?,?,'pending')",
        (jid, item["library_id"], item["file_path"],
         os.path.getsize(item["file_path"]), best["index"])
    )
    conn.commit()
    conn.close()
    return {"id": jid}

@app.delete("/api/subtitle/history")
def api_subtitle_clear_history():
    conn = get_db()
    conn.execute("DELETE FROM subtitle_history WHERE status='error'")
    conn.commit()
    conn.close()
    return {"ok": True}

class BulkHistoryAction(BaseModel):
    ids: list
    action: str  # "delete_srt" | "requeue"

@app.post("/api/subtitle/history/bulk")
def api_subtitle_history_bulk(body: BulkHistoryAction):
    """
    Bulk actie op subtitle history items.
    action="delete_srt"  → verwijder het .srt bestand van schijf + history record
    action="requeue"     → voeg opnieuw toe aan vertaalwachtrij (ook voor succesvolle items)
    """
    if not body.ids:
        raise HTTPException(400, "Geen items opgegeven")
    if body.action not in ("delete_srt", "requeue"):
        raise HTTPException(400, "Ongeldige actie")

    conn = get_db()
    results = {"ok": 0, "failed": 0, "errors": []}

    placeholders = ",".join("?" * len(body.ids))
    items = conn.execute(
        f"SELECT * FROM subtitle_history WHERE id IN ({placeholders})",
        body.ids
    ).fetchall()

    for item in items:
        try:
            if body.action == "delete_srt":
                # Verwijder het SRT bestand van schijf als het bestaat
                srt_path = item["output_path"]
                if srt_path and os.path.exists(srt_path):
                    os.remove(srt_path)
                    logger.info(f"SRT verwijderd: {srt_path}")
                # Verwijder history record
                conn.execute("DELETE FROM subtitle_history WHERE id=?", (item["id"],))
                results["ok"] += 1

            elif body.action == "requeue":
                # Controleer of bronbestand nog bestaat
                if not os.path.exists(item["file_path"]):
                    results["failed"] += 1
                    results["errors"].append(f"{Path(item['file_path']).name}: bronbestand niet gevonden")
                    continue
                # Verwijder eventueel bestaand SRT bestand zodat het opnieuw aangemaakt wordt
                srt_path = item["output_path"]
                if srt_path and os.path.exists(srt_path):
                    try:
                        os.remove(srt_path)
                        logger.info(f"Oud SRT verwijderd voor herverwerking: {srt_path}")
                    except Exception as e:
                        logger.warning(f"Kon oud SRT niet verwijderen: {srt_path} — {e}")
                # Detecteer subtitle streams
                streams = detect_subtitle_streams(item["file_path"])
                source_lang = get_subtitle_setting("subtitle_source_lang", "eng")
                best = pick_best_source_stream(streams, source_lang)
                if not best:
                    results["failed"] += 1
                    results["errors"].append(f"{Path(item['file_path']).name}: geen geschikte ondertitelstream gevonden")
                    continue
                jid = str(uuid.uuid4())
                conn.execute("DELETE FROM subtitle_history WHERE id=?", (item["id"],))
                conn.execute(
                    "INSERT INTO subtitle_queue (id,library_id,file_path,file_size,subtitle_track_index,status) "
                    "VALUES (?,?,?,?,?,'pending')",
                    (jid, item["library_id"], item["file_path"],
                     os.path.getsize(item["file_path"]), best["index"])
                )
                results["ok"] += 1

        except Exception as e:
            results["failed"] += 1
            results["errors"].append(f"{Path(item['file_path']).name}: {str(e)[:100]}")
            logger.warning(f"Bulk subtitle actie fout voor {item['file_path']}: {e}")

    conn.commit()
    conn.close()
    return results

@app.get("/api/subtitle/active")
def api_subtitle_active():
    """Live voortgang van de actieve ondertiteljob."""
    conn = get_db()
    job = conn.execute(
        "SELECT sq.*, l.name as library_name FROM subtitle_queue sq "
        "LEFT JOIN libraries l ON sq.library_id=l.id "
        "WHERE sq.status='processing' LIMIT 1"
    ).fetchone()
    conn.close()
    if not job:
        return None
    return dict(job)

@app.get("/api/ollama/models")
def api_ollama_models():
    """Haalt beschikbare Ollama modellen op van de geconfigureerde host."""
    host = get_subtitle_setting("ollama_host", "http://localhost:11434")
    try:
        result = subprocess.run(
            ["curl", "-s", "--connect-timeout", "4", f"{host}/api/tags"],
            capture_output=True, text=True, timeout=6
        )
        if result.returncode != 0:
            return {"models": [], "error": "Ollama niet bereikbaar"}
        data = json.loads(result.stdout)
        models = [m["name"] for m in data.get("models", [])]
        return {"models": models}
    except Exception as e:
        return {"models": [], "error": str(e)[:200]}

@app.post("/api/subtitle/queue/add")
def api_subtitle_add(data: dict):
    """Voegt een bestand handmatig toe aan de ondertitelwachtrij."""
    file_path = data.get("file_path", "")
    library_id = data.get("library_id")
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(400, "Bestand niet gevonden")
    if has_dutch_subtitle(file_path):
        raise HTTPException(400, "Nederlandse ondertitel al aanwezig")
    streams = detect_subtitle_streams(file_path)
    best = pick_best_english_stream(streams)
    if not best:
        raise HTTPException(400, "Geen Engelse ondertitelstream gevonden in dit bestand")
    conn = get_db()
    existing = conn.execute(
        "SELECT id FROM subtitle_queue WHERE file_path=? AND status IN ('pending','processing')",
        (file_path,)
    ).fetchone()
    if existing:
        conn.close()
        raise HTTPException(400, "Al in ondertitelwachtrij")
    jid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO subtitle_queue (id,library_id,file_path,file_size,subtitle_track_index,status) "
        "VALUES (?,?,?,?,?,'pending')",
        (jid, library_id, file_path, os.path.getsize(file_path), best["index"])
    )
    conn.commit()
    conn.close()
    return {"id": jid}


# ── Handmatige ondertiteling scan per bibliotheek ─────────────────────────────

@app.post("/api/libraries/{lid}/scan-subtitles")
def api_scan_subtitles_library(lid: str):
    """
    Scant één bibliotheek op bestanden zonder ondertitel en
    voegt ze toe aan de ondertitelwachtrij.
    """
    conn = get_db()
    lib = conn.execute("SELECT * FROM libraries WHERE id=?", (lid,)).fetchone()
    conn.close()
    if not lib:
        raise HTTPException(404, "Bibliotheek niet gevonden")

    def do_scan():
        source_lang = get_subtitle_setting("subtitle_source_lang", "eng")
        added = 0
        path = lib["path"]
        if not os.path.isdir(path):
            return
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for fname in files:
                if Path(fname).suffix.lower() not in VIDEO_EXTENSIONS:
                    continue
                if fname.startswith("shryncing-"):
                    continue
                fpath = os.path.join(root, fname)
                c = get_db()
                existing = c.execute(
                    "SELECT id FROM subtitle_queue WHERE file_path=? AND status IN ('pending','processing')",
                    (fpath,)
                ).fetchone()
                done = c.execute(
                    "SELECT id FROM subtitle_history WHERE file_path=? AND status='success'",
                    (fpath,)
                ).fetchone()
                failed = c.execute(
                    "SELECT id FROM subtitle_history WHERE file_path=? AND status='error'",
                    (fpath,)
                ).fetchone()
                c.close()
                if existing or done or failed:
                    continue
                if has_dutch_subtitle(fpath):
                    continue
                streams = detect_subtitle_streams(fpath)
                best = pick_best_source_stream(streams, source_lang)
                if not best:
                    continue
                c = get_db()
                jid = str(uuid.uuid4())
                c.execute(
                    "INSERT INTO subtitle_queue (id,library_id,file_path,file_size,subtitle_track_index,status) "
                    "VALUES (?,?,?,?,?,'pending')",
                    (jid, lid, fpath, os.path.getsize(fpath), best["index"])
                )
                c.commit()
                c.close()
                added += 1
        logger.info(f"Handmatige ondertitel scan bibliotheek {lib['name']}: {added} toegevoegd")

    threading.Thread(target=do_scan, daemon=True).start()
    return {"ok": True, "message": f"Scan gestart voor '{lib['name']}'"}

# ── Vertaalkwaliteit test ──────────────────────────────────────────────────────

@app.post("/api/subtitle/test-translation")
def api_test_translation(data: dict):
    """
    Vertaalt een kleine stuk testtekst via Ollama en geeft het resultaat terug.
    Gebruikt de geconfigureerde host, model en taalinstellingen.
    """
    host   = get_subtitle_setting("ollama_host", "http://localhost:11434")
    model  = get_subtitle_setting("ollama_model", "")
    source_lang = data.get("source_lang") or get_subtitle_setting("subtitle_source_lang", "eng")
    target_lang = data.get("target_lang") or get_subtitle_setting("subtitle_target_lang", "nld")
    test_text   = data.get("text", "").strip()

    if not model:
        raise HTTPException(400, "Geen Ollama model ingesteld")
    if not test_text:
        raise HTTPException(400, "Geen testtekst opgegeven")

    lang_names = {
        "eng":"English","nld":"Dutch","deu":"German","fra":"French",
        "spa":"Spanish","ita":"Italian","por":"Portuguese","rus":"Russian",
        "jpn":"Japanese","zho":"Chinese","kor":"Korean","ara":"Arabic",
        "pol":"Polish","swe":"Swedish","nor":"Norwegian","dan":"Danish",
        "fin":"Finnish","tur":"Turkish",
    }
    src_name = lang_names.get(source_lang, source_lang)
    tgt_name = lang_names.get(target_lang, target_lang)

    prompt = (
        f"Translate this {src_name} subtitle line to natural, fluent {tgt_name} as used in professional cinema subtitles. Adapt idioms, do NOT translate word for word. "
        "Return ONLY the translation, nothing else.\n\n" + test_text
    )

    try:
        result = subprocess.run([
            "curl", "-s", "-X", "POST", f"{host}/api/generate",
            "-H", "Content-Type: application/json",
            "-d", json.dumps({
                "model": model, "prompt": prompt, "stream": False,
                "options": {"temperature": 0.3, "num_predict": 1024}
            })
        ], capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            raise Exception(f"curl fout: {result.stderr[:200]}")
        resp = json.loads(result.stdout)
        translation = resp.get("response", "").strip()
        return {"ok": True, "translation": translation, "model": model,
                "source_lang": source_lang, "target_lang": target_lang}
    except Exception as e:
        raise HTTPException(500, str(e)[:300])

