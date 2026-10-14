"""Web-UI, RSS-Feed und Scheduler für den Extra-WOW-Downloader.

Konfiguration über Umgebungsvariablen: DEST, PROGRAM, CHECK_INTERVAL_MINUTES, BASE_URL.
"""

import json
import logging
import os
import re
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import quote
from xml.sax.saxutils import escape

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response

import wow_downloader as wow

DEST = Path(os.environ.get("DEST", "/data"))
PROGRAM = os.environ.get("PROGRAM", "WXWOW")
INTERVAL = int(os.environ.get("CHECK_INTERVAL_MINUTES", "60")) * 60
PARALLEL = max(1, int(os.environ.get("PARALLEL", str(wow.PARALLEL))))
BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")
STATIC = Path(__file__).parent / "static"


class RingHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = deque(maxlen=300)

    def emit(self, record):
        self.lines.append(self.format(record))


ring = RingHandler()
ring.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%d %H:%M:%S"))
wow.setup_logging()
wow.log.addHandler(ring)

run_lock = threading.Lock()
pending = threading.Event()
status = {"running": False, "current": None, "last_run": None, "last_ok": None, "next_run": None}
progress = {"started": 0, "base": None, "done": 0, "total": None}


def on_download(name):
    progress.update(started=time.time(), base=None, done=0, total=None)
    status["current"] = name


def on_progress(done, total):
    if progress["base"] is None:
        # Beim Fortsetzen bereits vorhandene Bytes nicht in die Geschwindigkeit einrechnen
        progress.update(base=done, started=time.time())
    progress.update(done=done, total=total)


def now_iso(offset=0):
    return datetime.fromtimestamp(time.time() + offset).astimezone().isoformat(timespec="seconds")


def do_run():
    """Arbeitet angeforderte Läufe ab; nie zwei gleichzeitig. Anfragen während eines Laufs lösen
    danach einen weiteren Lauf aus."""
    while pending.is_set():
        if not run_lock.acquire(blocking=False):
            return
        try:
            status["running"] = True
            while pending.is_set():
                pending.clear()
                ok = wow.run(PROGRAM, DEST, on_download=on_download, on_progress=on_progress, parallel=PARALLEL)
                status.update(last_run=now_iso(), last_ok=ok, current=None)
        finally:
            status.update(running=False, current=None)
            run_lock.release()


def request_run():
    pending.set()
    threading.Thread(target=do_run, daemon=True).start()


def scheduler():
    while True:
        pending.set()
        do_run()
        status["next_run"] = now_iso(INTERVAL)
        time.sleep(INTERVAL)


@asynccontextmanager
async def lifespan(app):
    DEST.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=scheduler, daemon=True).start()
    yield


app = FastAPI(title="Extra WOW Downloader", lifespan=lifespan)


def episode_path(name):
    path = (DEST / name).resolve()
    if path.parent != DEST.resolve() or path.suffix != ".mp3" or not path.is_file():
        raise HTTPException(404, "Folge nicht gefunden")
    return path


def list_episodes():
    items = []
    for mp3 in sorted(DEST.glob("*.mp3"), reverse=True):
        try:
            meta = json.loads(mp3.with_suffix(".json").read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            meta = {}
        items.append({
            "name": mp3.name,
            "date": mp3.name[:10],
            "title": meta.get("title") or mp3.stem[11:],
            "subtitle": meta.get("subtitle"),
            "description": meta.get("description"),
            "moderator": meta.get("moderator"),
            "start": meta.get("startISO"),
            "end": meta.get("endISO"),
            "size": mp3.stat().st_size,
            "mtime": mp3.stat().st_mtime,
        })
    return items


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/episodes")
def episodes():
    return list_episodes()


@app.api_route("/api/episodes/{name}/audio", methods=["GET", "HEAD"])
def audio(name: str, download: bool = False):
    path = episode_path(name)
    if download:
        return FileResponse(path, media_type="audio/mpeg", filename=path.name)
    return FileResponse(path, media_type="audio/mpeg")


@app.delete("/api/episodes/{name}")
def delete(name: str):
    path = episode_path(name)
    path.unlink()
    path.with_suffix(".json").unlink(missing_ok=True)
    if re.match(r"\d{4}-\d{2}-\d{2} ", name):
        wow.mark_deleted(DEST, name[:10])
    wow.log.info(f"{name[:10]}: über das UI gelöscht")
    return {"deleted": name}


@app.get("/api/online")
def online():
    try:
        items = wow.list_online(PROGRAM)
    except Exception as e:
        raise HTTPException(502, f"ORF-API nicht erreichbar: {e}")
    deleted = wow.load_deleted(DEST)
    for b in items:
        date = str(wow.day_to_date(b["day"]))
        b["date"] = date
        if any(DEST.glob(f"{date} *.mp3")):
            b["state"] = "vorhanden"
        elif date in deleted:
            b["state"] = "gelöscht"
        else:
            b["state"] = "ausstehend"
    return items


@app.post("/api/online/{day}/restore")
def restore(day: int):
    try:
        date = wow.day_to_date(day)
    except ValueError:
        raise HTTPException(400, "Ungültiger Tag")
    wow.unmark_deleted(DEST, date)
    request_run()
    return {"restored": str(date)}


@app.post("/api/run")
def trigger_run():
    request_run()
    return {"requested": True}


@app.get("/api/status")
def get_status():
    dl = None
    if status["current"]:
        elapsed = time.time() - progress["started"]
        speed = (progress["done"] - (progress["base"] or 0)) / elapsed if elapsed > 0 else 0
        total = progress["total"]
        eta = (total - progress["done"]) / speed if total and speed else None
        dl = {"done": progress["done"], "total": total, "speed": speed, "eta": eta}
    return {**status, "progress": dl, "program": PROGRAM, "interval_minutes": INTERVAL // 60, "log": list(ring.lines)[-150:]}


@app.get("/feed.xml")
def feed(request: Request):
    base = BASE_URL or str(request.base_url).rstrip("/")
    items = []
    for ep in list_episodes():
        if ep["start"]:
            pub = datetime.fromisoformat(ep["start"])
        else:
            pub = datetime.fromtimestamp(ep["mtime"]).astimezone()
        duration = ""
        if ep["start"] and ep["end"]:
            seconds = int((datetime.fromisoformat(ep["end"]) - pub).total_seconds())
            duration = f"<itunes:duration>{seconds}</itunes:duration>"
        text = " ".join(t for t in (ep["subtitle"], ep["description"]) if t)
        items.append(
            "<item>"
            f"<title>{escape(ep['date'] + ' – ' + ep['title'])}</title>"
            f"<description>{escape(text)}</description>"
            f'<enclosure url="{escape(base + "/api/episodes/" + quote(ep["name"]) + "/audio")}"'
            f' length="{ep["size"]}" type="audio/mpeg"/>'
            f'<guid isPermaLink="false">{escape(ep["name"])}</guid>'
            f"<pubDate>{format_datetime(pub)}</pubDate>"
            f"{duration}"
            "</item>"
        )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">'
        "<channel>"
        "<title>Extra WOW – Die 2 um 2.</title>"
        f"<link>{escape(base)}/</link>"
        "<description>Radio-Wien-Sendung, automatisch archiviert</description>"
        "<language>de-at</language>"
        "<itunes:author>ORF Radio Wien</itunes:author>"
        + "".join(items)
        + "</channel></rss>"
    )
    return Response(xml, media_type="application/rss+xml; charset=utf-8")
