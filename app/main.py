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
import shutil
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SHRYNC_VERSION = os.environ.get("SHRYNC_VERSION", "0.25")

app = FastAPI(title="Shrync", version=SHRYNC_VERSION)

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
    """Ruim loshangende tijdelijke bestanden op en zet taken terug naar pending."""
    conn = get_db()
    stale_jobs = conn.execute("SELECT * FROM queue WHERE status='processing'").fetchall()
    for job in stale_jobs:
        # Tijdelijk bestand altijd naast het bronbestand — zoek op shryncing-*.mkv
        import glob
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
        "-max_muxing_queue_size", "4096",
        "-progress", "pipe:1",
        "-nostats",
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
        "-max_muxing_queue_size", "4096",
        "-progress", "pipe:1",
        "-nostats",
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
        "-max_muxing_queue_size", "4096",
        "-progress", "pipe:1",
        "-nostats",
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
        "-c:v", codec,
        "-preset", preset,
        "-crf", crf,
        "-pix_fmt", pix_fmt,
    ]
    cmd += _hdr_video_flags(hdr)
    cmd += [
        "-c:a", audio_codec,
        "-c:s", "copy",
        "-max_muxing_queue_size", "4096",
        "-progress", "pipe:1",
        "-nostats",
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
        ], capture_output=True, text=True, timeout=8)
        info = json.loads(result.stdout)
        for stream in info.get("streams", []):
            current = stream.get("codec_name", "")
            if "hevc" in target_codec and current in ("hevc", "h265"):
                return False
            if "h264" in target_codec and current == "h264":
                return False
        return True
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
            ext = Path(fname).suffix.lower()
            if ext not in VIDEO_EXTENSIONS:
                continue
            fpath = os.path.join(root, fname)
            # Sla tijdelijke Shrync-bestanden over (shryncing-*.mkv)
            if fname.startswith("shryncing-"):
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

            # Mislukte conversie: verwijder oude foutmelding en voeg opnieuw toe
            failed = conn.execute(
                "SELECT id FROM history WHERE file_path=? AND status='error'", (fpath,)
            ).fetchone()
            if failed:
                conn.execute("DELETE FROM history WHERE file_path=? AND status='error'", (fpath,))
                conn.commit()
                logger.info(f"Scan: mislukte conversie opnieuw toegevoegd: {fpath}")

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

    # Tijdelijk bestand altijd naast het bronbestand — zelfde map, willekeurige naam
    _src_dir = str(Path(src).parent)
    _rand    = str(uuid.uuid4()).replace("-", "")[:12]
    tmp_out  = os.path.join(_src_dir, f"shryncing-{_rand}.mkv")
    logger.info(f"Tijdelijk bestand: {tmp_out}")
    # Lees conversie-instellingen uit globale settings (niet uit bibliotheek)
    profile = get_global_setting('conversion_profile', 'nvenc_max')
    video_codec, preset, quality = profile_to_ffmpeg(profile)
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
    profile_data = PROFILES.get(profile_id, None)
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
        "INSERT INTO libraries (id,name,path) VALUES (?,?,?)",
        (lid, lib.name, lib.path)
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
        "UPDATE libraries SET name=?,path=?,enabled=? WHERE id=?",
        (lib.name, lib.path, 1 if lib.enabled else 0, lid)
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
            out = subprocess.run([
                "nvidia-smi",
                "--query-gpu=name,utilization.gpu,utilization.memory,"
                "memory.used,memory.total,temperature.gpu,encoder.stats.sessionCount",
                "--format=csv,noheader,nounits"
            ], capture_output=True, text=True, timeout=5)
            if out.returncode == 0:
                for line in out.stdout.strip().splitlines():
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) >= 6:
                        result["gpus"].append({
                            "name":          parts[0],
                            "gpu_util":      int(parts[1]) if parts[1].isdigit() else 0,
                            "mem_util":      int(parts[2]) if parts[2].isdigit() else 0,
                            "mem_used_mb":   int(parts[3]) if parts[3].isdigit() else 0,
                            "mem_total_mb":  int(parts[4]) if parts[4].isdigit() else 0,
                            "temperature":   int(parts[5]) if parts[5].isdigit() else 0,
                            "enc_sessions":  int(parts[6]) if len(parts) > 6 and parts[6].isdigit() else 0,
                        })
                result["available"] = len(result["gpus"]) > 0
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
