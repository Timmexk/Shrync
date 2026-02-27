from fastapi import FastAPI, HTTPException, Request
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
from datetime import datetime
from pathlib import Path
import sqlite3
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SHRYNC_VERSION = os.environ.get("SHRYNC_VERSION", "0.02")

app = FastAPI(title="Shrync", version=SHRYNC_VERSION)

BASE_DIR = Path(__file__).parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

DB_PATH = "/config/shrync.db"
_CACHE_DIR_ENV = os.environ.get("CACHE_DIR", "").strip()
# Als CACHE_DIR leeg is: gebruik de map van het bronbestand (ingesteld per conversie)
CACHE_DIR = _CACHE_DIR_ENV if _CACHE_DIR_ENV else ""
os.makedirs("/config", exist_ok=True)
if CACHE_DIR:
    os.makedirs(CACHE_DIR, exist_ok=True)

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
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")
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
        new_size INTEGER DEFAULT 0
    )""")
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
    conn.commit()
    conn.close()

init_db()

# ── Cleanup stale conversions on startup ──────────────────────────────────────
def cleanup_stale_conversions():
    """Ruim loshangende .converting.mkv bestanden op en zet taken terug naar pending."""
    conn = get_db()
    stale_jobs = conn.execute("SELECT * FROM queue WHERE status='processing'").fetchall()
    for job in stale_jobs:
        src_name = Path(job["file_path"]).stem + "_" + job["id"][:8] + ".mkv"
        tmp_file = os.path.join(CACHE_DIR, src_name)
        if os.path.exists(tmp_file):
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

# ── State ─────────────────────────────────────────────────────────────────────
active_jobs = {}        # slot_name -> {"id": job_id, "process": process}
active_jobs_lock = threading.Lock()
worker_threads = []
worker_running = False
workers_paused = False  # pauze flag: workers slaan taken over als True
scan_status = {}        # library_id -> {status, scanned, added, skipped, already_converted, current_file, error}
_observers = []

# ── Models ────────────────────────────────────────────────────────────────────
class LibraryCreate(BaseModel):
    name: str
    path: str
    scan_interval: int = 3600

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
# profile_id -> (video_codec, preset, crf/cq)
PROFILES = {
    "nvenc_max":    ("hevc_nvenc", "p7", "19"),   # NVENC H.265 max quality
    "nvenc_high":   ("hevc_nvenc", "p6", "23"),   # NVENC H.265 high quality
    "nvenc_balanced":("hevc_nvenc","p4", "26"),   # NVENC H.265 balanced
    "cpu_slow":     ("libx265",   "slow","22"),   # CPU H.265 max quality
    "cpu_medium":   ("libx265",   "medium","24"), # CPU H.265 balanced
    "cpu_fast":     ("libx265",   "fast","26"),   # CPU H.265 fast
    "h264_nvenc":   ("h264_nvenc","p6", "20"),    # NVENC H.264 high quality
    "h264_cpu":     ("libx264",   "medium","22"), # CPU H.264 balanced
}

def profile_to_ffmpeg(profile_id: str):
    """Returns (video_codec, preset, quality_str) for a given profile."""
    return PROFILES.get(profile_id, PROFILES["nvenc_max"])

def needs_conversion(file_path: str, target_codec: str) -> bool:
    try:
        result = subprocess.run([
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_streams", file_path
        ], capture_output=True, text=True, timeout=30)
        info = json.loads(result.stdout)
        for stream in info.get("streams", []):
            if stream.get("codec_type") == "video":
                current = stream.get("codec_name", "")
                if "hevc" in target_codec and current in ("hevc", "h265"):
                    return False
                if "h264" in target_codec and current == "h264":
                    return False
        return True
    except Exception as e:
        logger.warning(f"ffprobe error for {file_path}: {e}")
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
            ext = Path(fname).suffix.lower()
            if ext not in VIDEO_EXTENSIONS:
                continue
            fpath = os.path.join(root, fname)
            # Sla tijdelijke Shrync-bestanden over (herkenbaar aan _shrync_ in naam)
            if "_shrync_" in fname:
                continue
            # Sla bestanden in de cache map over (als geconfigureerd)
            if CACHE_DIR and CACHE_DIR in fpath:
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
                continue

            done = conn.execute(
                "SELECT id FROM history WHERE file_path=? AND status='success'", (fpath,)
            ).fetchone()
            if done:
                skipped += 1
                scan_status[library_id]["skipped"] = skipped
                continue

            profile_id = get_global_setting('conversion_profile', 'nvenc_max')
            global_codec, _, _ = profile_to_ffmpeg(profile_id)
            if not needs_conversion(fpath, global_codec):
                already_converted += 1
                scan_status[library_id]["already_converted"] = already_converted
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

    conn.execute("UPDATE libraries SET last_scan=? WHERE id=?", (datetime.utcnow().isoformat(), library_id))
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
                     (datetime.utcnow().isoformat(), job_id))
        conn.commit()
        conn.close()
        return

    # Tijdelijk bestand in de cache map (niet naast het origineel)
    src_name = Path(src).stem + "_shrync_" + job_id[:8] + ".mkv"
    # Als geen aparte cache map: gebruik map van bronbestand
    _cache = CACHE_DIR if CACHE_DIR else str(Path(src).parent)
    tmp_out = os.path.join(_cache, src_name)
    # Lees conversie-instellingen uit globale settings (niet uit bibliotheek)
    profile = get_global_setting('conversion_profile', 'nvenc_max')
    video_codec, preset, quality = profile_to_ffmpeg(profile)
    audio_codec = get_global_setting('audio_codec', 'copy')

    # Get duration via ffprobe
    duration = 0
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", src],
            capture_output=True, text=True, timeout=30
        )
        info = json.loads(r.stdout)
        duration = float(info.get("format", {}).get("duration", 0))
    except:
        pass

    gpu_mode = os.environ.get("GPU_MODE", "cpu").lower()
    is_nvenc = "nvenc" in video_codec and gpu_mode == "nvidia"

    # Als nvenc codec gekozen maar GPU_MODE=cpu, val terug op CPU equivalent
    effective_codec = video_codec
    if "nvenc" in video_codec and gpu_mode != "nvidia":
        effective_codec = "libx265" if "hevc" in video_codec else "libx264"
        logger.warning(f"GPU_MODE is niet 'nvidia' — valt terug op CPU codec: {effective_codec}")

    if is_nvenc:
        cmd = [
            "ffmpeg", "-y",
            "-i", src,
            "-c:v", effective_codec,
            "-preset", preset,
            "-rc", "constqp",
            "-qp", quality,
            "-b:v", "0",
            "-c:a", audio_codec,
            "-c:s", "copy",
            "-progress", "pipe:1",
            "-nostats",
            tmp_out
        ]
    else:
        cmd = [
            "ffmpeg", "-y",
            "-i", src,
            "-c:v", effective_codec,
            "-preset", preset,
            "-crf", quality,
            "-c:a", audio_codec,
            "-c:s", "copy",
            "-progress", "pipe:1",
            "-nostats",
            tmp_out
        ]

    original_size = os.path.getsize(src)
    conn.execute("UPDATE queue SET status='processing', started_at=?, original_size=? WHERE id=?",
                 (datetime.utcnow().isoformat(), original_size, job_id))
    conn.commit()

    start_time = time.time()
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)

    slot_id = threading.current_thread().name
    with active_jobs_lock:
        active_jobs[slot_id] = {"id": job_id, "process": process}

    out_time_us = 0
    stderr_lines = []

    # Lees stderr in aparte thread zodat de pipe-buffer niet blokkeert
    def read_stderr():
        for line in process.stderr:
            stderr_lines.append(line)
    stderr_thread = threading.Thread(target=read_stderr, daemon=True)
    stderr_thread.start()

    try:
        for line in process.stdout:
            line = line.strip()
            if line.startswith("out_time_us="):
                try:
                    out_time_us = int(line.split("=")[1])
                except:
                    pass
            elif line.startswith("fps="):
                try:
                    fps_val = float(line.split("=")[1])
                    progress = 0
                    eta = ""
                    if duration > 0:
                        current_sec = out_time_us / 1_000_000
                        progress = min(int((current_sec / duration) * 100), 99)
                        if fps_val > 0:
                            remaining_sec = int((duration - current_sec) / fps_val * 25)
                            eta = f"{remaining_sec // 60}m{remaining_sec % 60}s"
                    conn2 = get_db()
                    conn2.execute("UPDATE queue SET progress=?, fps=?, eta=? WHERE id=?",
                                  (progress, fps_val, eta, job_id))
                    conn2.commit()
                    conn2.close()
                except:
                    pass
        process.wait()
    except Exception as e:
        process.kill()
        logger.error(f"Conversie fout: {e}")

    stderr_thread.join(timeout=5)
    stderr_out = "".join(stderr_lines)

    with active_jobs_lock:
        active_jobs.pop(slot_id, None)

    elapsed = int(time.time() - start_time)
    now = datetime.utcnow().isoformat()

    if process.returncode == 0 and os.path.exists(tmp_out):
        new_size = os.path.getsize(tmp_out)
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
        logger.info(f"Conversie geslaagd: {src} ({original_size} -> {new_size} bytes, {elapsed}s)")
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
        if "_shrync_" in Path(fpath).name:
            return
        if CACHE_DIR and CACHE_DIR in fpath:
            return
        with self._lock:
            self._pending[fpath] = time.time()
        threading.Thread(target=self._delayed_queue, args=(fpath,), daemon=True).start()
        logger.info(f"Watcher: nieuw bestand gedetecteerd: {fpath}")

    def _delayed_queue(self, fpath: str):
        time.sleep(10)
        with self._lock:
            if fpath not in self._pending:
                return
            del self._pending[fpath]
        # Check file is stable (wacht tot bestand klaar is met kopiëren)
        try:
            size1 = os.path.getsize(fpath)
            time.sleep(10)
            size2 = os.path.getsize(fpath)
            if size1 != size2:
                logger.info(f"Watcher: bestand nog bezig met kopiëren: {fpath}")
                return
        except:
            return
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
        global_codec, _, _ = profile_to_ffmpeg(get_global_setting('conversion_profile', 'nvenc_max'))
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

# ── Worker pool ───────────────────────────────────────────────────────────────
def worker_loop(slot_name: str):
    logger.info(f"{slot_name} gereed.")
    while worker_running:
        if workers_paused:
            time.sleep(1)
            continue
        try:
            conn2 = get_db()
            with active_jobs_lock:
                active_ids = [v["id"] for v in active_jobs.values()]
            if active_ids:
                placeholders = ",".join("?" * len(active_ids))
                job = conn2.execute(
                    f"SELECT id FROM queue WHERE status='pending' AND id NOT IN ({placeholders}) "
                    f"ORDER BY added_at ASC LIMIT 1", active_ids
                ).fetchone()
            else:
                job = conn2.execute(
                    "SELECT id FROM queue WHERE status='pending' ORDER BY added_at ASC LIMIT 1"
                ).fetchone()
            conn2.close()
            if job:
                run_conversion(job["id"])
            else:
                time.sleep(3)
        except Exception as e:
            logger.error(f"{slot_name} fout: {e}")
            time.sleep(5)


def start_workers():
    global worker_threads, worker_running
    # Stop bestaande workers
    worker_running = False
    for t in worker_threads:
        t.join(timeout=2)
    worker_threads = []
    # Start nieuwe workers met opgeslagen instelling
    worker_running = True
    n = get_max_workers()
    logger.info(f"Starten met {n} worker(s)...")
    for i in range(n):
        t = threading.Thread(
            target=worker_loop,
            args=(f"Worker-{i+1}",),
            name=f"Worker-{i+1}",
            daemon=True
        )
        t.start()
        worker_threads.append(t)


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


def initial_startup():
    logger.info("Shrync opstart — eenmalige scan van alle bibliotheken...")
    conn = get_db()
    libs = conn.execute("SELECT * FROM libraries WHERE enabled=1").fetchall()
    conn.close()
    for lib in libs:
        scan_library(lib["id"])
    start_watchers()
    threading.Thread(target=watcher_monitor, daemon=True).start()
    logger.info("Live monitoring actief.")
    start_workers()

# ── App lifecycle ─────────────────────────────────────────────────────────────
@app.on_event("startup")
def startup():
    global worker_running
    worker_running = True  # zet vóór initial_startup zodat workers meteen actief zijn
    n = get_max_workers()
    logger.info(f"Opgeslagen instelling: {n} worker(s)")
    threading.Thread(target=initial_startup, daemon=True).start()

@app.on_event("shutdown")
def shutdown():
    global worker_running
    worker_running = False
    for obs in _observers:
        try:
            obs.stop()
            obs.join()
        except:
            pass

# ── API Routes ────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/api/stats")
def api_stats():
    conn = get_db()
    pending    = conn.execute("SELECT COUNT(*) as c FROM queue WHERE status='pending'").fetchone()["c"]
    processing = conn.execute("SELECT COUNT(*) as c FROM queue WHERE status='processing'").fetchone()["c"]
    done_today = conn.execute("SELECT COUNT(*) as c FROM history WHERE status='success' AND date(finished_at)=date('now')").fetchone()["c"]
    errors     = conn.execute("SELECT COUNT(*) as c FROM history WHERE status='error'").fetchone()["c"]
    saved      = conn.execute("SELECT SUM(original_size-new_size) as s FROM history WHERE status='success'").fetchone()["s"] or 0
    libs       = conn.execute("SELECT COUNT(*) as c FROM libraries WHERE enabled=1").fetchone()["c"]
    conn.close()
    return {"pending": pending, "processing": processing, "done_today": done_today,
            "errors": errors, "saved_bytes": saved, "active_libraries": libs}

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
        "INSERT INTO libraries (id,name,path,scan_interval) VALUES (?,?,?,?)",
        (lid, lib.name, lib.path, lib.scan_interval)
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
        "UPDATE libraries SET name=?,path=?,scan_interval=?,enabled=? WHERE id=?",
        (lib.name, lib.path, lib.scan_interval, 1 if lib.enabled else 0, lid)
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
        threading.Thread(target=start_workers, daemon=True).start()
    return {"ok": True}

@app.post("/api/workers/pause")
def api_pause_workers():
    global workers_paused
    workers_paused = True
    return {"paused": True}

@app.post("/api/workers/resume")
def api_resume_workers():
    global workers_paused
    workers_paused = False
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

    return {"libraries": results, "media_root": media_root, "cache_dir": CACHE_DIR}

@app.get("/api/config")
def api_config():
    """Geeft runtime configuratie terug zodat de UI weet welke functies beschikbaar zijn."""
    gpu_mode = os.environ.get("GPU_MODE", "cpu").lower()
    # Controleer ook of nvidia-smi beschikbaar is als extra check
    gpu_available = gpu_mode == "nvidia"
    return {
        "gpu_available": gpu_available,
        "gpu_mode": gpu_mode,
        "cache_dir": CACHE_DIR or "(naast bronbestand)",
        "version": SHRYNC_VERSION,
    }


@app.get("/api/profiles")
def api_get_profiles():
    return [
        {"id": "nvenc_max",      "label": "NVENC H.265 — Max kwaliteit",    "codec": "hevc_nvenc", "gpu": True},
        {"id": "nvenc_high",     "label": "NVENC H.265 — Hoge kwaliteit",   "codec": "hevc_nvenc", "gpu": True},
        {"id": "nvenc_balanced", "label": "NVENC H.265 — Gebalanceerd",     "codec": "hevc_nvenc", "gpu": True},
        {"id": "h264_nvenc",     "label": "NVENC H.264 — Hoge kwaliteit",   "codec": "h264_nvenc", "gpu": True},
        {"id": "cpu_slow",       "label": "CPU H.265 — Max kwaliteit",      "codec": "libx265",    "gpu": False},
        {"id": "cpu_medium",     "label": "CPU H.265 — Gebalanceerd",       "codec": "libx265",    "gpu": False},
        {"id": "cpu_fast",       "label": "CPU H.265 — Snel",               "codec": "libx265",    "gpu": False},
        {"id": "h264_cpu",       "label": "CPU H.264 — Gebalanceerd",       "codec": "libx264",    "gpu": False},
    ]

@app.get("/api/history")
def api_history(page: int = 1, per_page: int = 50):
    offset = (page - 1) * per_page
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) as c FROM history").fetchone()["c"]
    rows = conn.execute(
        "SELECT h.*, l.name as library_name FROM history h LEFT JOIN libraries l ON h.library_id=l.id "
        "ORDER BY h.finished_at DESC LIMIT ? OFFSET ?", (per_page, offset)
    ).fetchall()
    conn.close()
    return {"total": total, "page": page, "items": [dict(r) for r in rows]}

@app.delete("/api/history")
def api_clear_history():
    conn = get_db()
    conn.execute("DELETE FROM history")
    conn.commit()
    conn.close()
    return {"ok": True}
