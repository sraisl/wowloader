#!/usr/bin/env python3
"""Lädt neue Folgen einer Radio-Wien-Sendung (Standard: "Extra WOW", WXWOW) herunter.

Die ORF-Audio-API listet die Sendungen der letzten 7 Tage. Bereits geladene
Folgen werden übersprungen, verpasste Tage werden nachgeholt.
"""

import argparse
import json
import logging
import os
import re
import ssl
import sys
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

API = "https://audioapi.orf.at/wie/api/json/4.0"
LOOPSTREAM = "https://loopstream01.apa.at/?channel=oe2w&shoutcast=0&id={}"
USER_AGENT = "wow-downloader/1.0"
CHUNK = 256 * 1024
SEGMENT = 4 * 1024 * 1024  # Teilstück für parallele Downloads und Fortsetzen
PARALLEL = 4  # gleichzeitige Verbindungen pro Folge
RETRIES = 3  # Versuche pro Segment
# Untergrenze für die Plausibilitätsprüfung: halbe Datenrate von 128 kbit/s
# (tatsächlich liefert der ORF ~192 kbit/s).
MIN_BYTES_PER_SECOND = 128_000 // 8 // 2
DELETED_FILE = ".deleted.json"

log = logging.getLogger("wow")

# python.org-Python bringt ohne "Install Certificates.command" keine CA-Liste mit;
# dann den macOS-Systemstore verwenden.
SSL_CONTEXT = ssl.create_default_context()
if not SSL_CONTEXT.cert_store_stats()["x509_ca"] and Path("/etc/ssl/cert.pem").exists():
    SSL_CONTEXT.load_verify_locations("/etc/ssl/cert.pem")


def setup_logging():
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%d %H:%M:%S"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)


def open_url(url, timeout, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    return urllib.request.urlopen(req, timeout=timeout, context=SSL_CONTEXT)


def fetch_json(url):
    with open_url(url, 30) as resp:
        return json.load(resp)


def list_online(program):
    """Alle on-demand verfügbaren Sendungen von `program`, nach Tag sortiert."""
    days = fetch_json(f"{API}/broadcasts")
    return sorted(
        (
            {"day": b["broadcastDay"], "title": b.get("title"), "start": b.get("startISO")}
            for day in days
            for b in day["broadcasts"]
            if b["programKey"] == program and b.get("isOnDemand")
        ),
        key=lambda b: b["day"],
    )


def find_episodes(program):
    return [b["day"] for b in list_online(program)]


def day_to_date(day):
    return datetime.strptime(str(day), "%Y%m%d").date()


def load_deleted(dest):
    try:
        return set(json.loads((dest / DELETED_FILE).read_text(encoding="utf-8")))
    except FileNotFoundError:
        return set()


def _save_deleted(dest, deleted):
    (dest / DELETED_FILE).write_text(json.dumps(sorted(deleted)), encoding="utf-8")


def mark_deleted(dest, date):
    _save_deleted(dest, load_deleted(dest) | {str(date)})


def unmark_deleted(dest, date):
    _save_deleted(dest, load_deleted(dest) - {str(date)})


def check_size(stream, size):
    if stream.get("start") and stream.get("end"):
        minimum = (stream["end"] - stream["start"]) / 1000 * MIN_BYTES_PER_SECOND
        if size < minimum:
            raise IOError(f"Stream zu kurz: {size} Bytes, erwartet mind. {minimum:.0f}")


def probe_size(url):
    """Größe des Streams per Range-Anfrage; None, wenn der Server keine Teil-Downloads kann."""
    with open_url(url, 30, {"Range": "bytes=0-0"}) as resp:
        m = re.fullmatch(r"bytes 0-0/(\d+)", resp.headers.get("Content-Range") or "")
        return int(m.group(1)) if resp.status == 206 and m else None


def download(streams, target, on_progress=None, parallel=PARALLEL):
    """Lädt die Streams in Segmenten über `parallel` Verbindungen nach `target`.

    Der ORF drosselt jede Verbindung auf ~270 KB/s, parallele Segmente vervielfachen das.
    Fertige Segmente stehen in `<target>.part.state`; ein abgebrochener Download wird beim
    nächsten Aufruf fortgesetzt. `on_progress(bytes, total)` wird laufend aufgerufen."""
    urls = [LOOPSTREAM.format(s["loopStreamId"]) for s in streams]
    sizes = [probe_size(u) for u in urls]
    if None in sizes:
        log.info("Server unterstützt keine Teil-Downloads, lade sequenziell")
        return download_sequential(streams, target, on_progress)
    for s, size in zip(streams, sizes):
        check_size(s, size)

    # Segment = (URL, Start im Stream, Länge, Offset in der Zieldatei)
    segments = []
    total = 0
    for url, size in zip(urls, sizes):
        segments += [(url, start, min(SEGMENT, size - start), total + start) for start in range(0, size, SEGMENT)]
        total += size

    part = target.with_name(target.name + ".part")
    state_file = target.with_name(target.name + ".part.state")
    key = {"streams": [s["loopStreamId"] for s in streams], "sizes": sizes, "segment": SEGMENT}
    done = set()
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
        if state.get("key") == key and part.exists():
            done = set(state["done"])
    except (FileNotFoundError, ValueError):
        pass
    if not done:
        part.unlink(missing_ok=True)

    lock = threading.Lock()
    stop = threading.Event()
    loaded = [sum(segments[i][2] for i in done)]
    if done:
        log.info(f"setze fort bei {loaded[0] / 1e6:.1f} von {total / 1e6:.1f} MB")

    def report(n):
        with lock:
            loaded[0] += n
            current = loaded[0]
        if on_progress:
            on_progress(current, total)

    def mark_done(i):
        with lock:
            done.add(i)
            tmp = state_file.with_name(state_file.name + ".tmp")
            tmp.write_text(json.dumps({"key": key, "done": sorted(done)}), encoding="utf-8")
            tmp.replace(state_file)

    def fetch(i):
        url, start, length, offset = segments[i]
        for attempt in range(1, RETRIES + 1):
            got = 0
            try:
                with open_url(url, 60, {"Range": f"bytes={start}-{start + length - 1}"}) as resp:
                    if resp.status != 206 or not (resp.headers.get("Content-Range") or "").startswith(f"bytes {start}-"):
                        raise IOError("Server ignoriert die Range-Anfrage")
                    while got < length and (chunk := resp.read(min(CHUNK, length - got))):
                        if stop.is_set():
                            raise IOError("abgebrochen")
                        os.pwrite(fd, chunk, offset + got)
                        got += len(chunk)
                        report(len(chunk))
                if got != length:
                    raise IOError(f"Segment unvollständig: {got} von {length} Bytes")
                os.fsync(fd)
                mark_done(i)
                return
            except Exception as e:
                report(-got)
                if stop.is_set() or attempt == RETRIES:
                    raise
                log.warning(f"Segment {i + 1}/{len(segments)}: {e} – Versuch {attempt + 1} von {RETRIES}")
                stop.wait(2 * attempt)

    fd = os.open(part, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        os.ftruncate(fd, total)
        report(0)
        with ThreadPoolExecutor(max_workers=parallel) as pool:
            futures = [pool.submit(fetch, i) for i in range(len(segments)) if i not in done]
            try:
                for f in as_completed(futures):
                    f.result()
            except BaseException:
                stop.set()
                for f in futures:
                    f.cancel()
                raise
    finally:
        os.close(fd)

    if part.stat().st_size != total or len(done) != len(segments):
        raise IOError("Download unvollständig")
    part.rename(target)
    state_file.unlink(missing_ok=True)


def download_sequential(streams, target, on_progress=None):
    """Fallback: Streams nacheinander über eine Verbindung laden."""
    part = target.with_name(target.name + ".part")
    target.with_name(target.name + ".part.state").unlink(missing_ok=True)
    done = 0
    total = 0
    try:
        with open(part, "wb") as out:
            for s in streams:
                got = 0
                with open_url(LOOPSTREAM.format(s["loopStreamId"]), 60) as resp:
                    expected = resp.headers.get("Content-Length")
                    total = total + int(expected) if expected and total is not None else None
                    while chunk := resp.read(CHUNK):
                        out.write(chunk)
                        got += len(chunk)
                        done += len(chunk)
                        if on_progress:
                            on_progress(done, total)
                if expected and got != int(expected):
                    raise IOError(f"Stream unvollständig: {got} von {expected} Bytes")
                check_size(s, got)
        part.rename(target)
    except BaseException:
        part.unlink(missing_ok=True)
        raise


def process(program, day, dest, dry_run, on_download=None, on_progress=None, parallel=PARALLEL):
    date = day_to_date(day)
    existing = list(dest.glob(f"{date} *.mp3"))
    if existing:
        log.info(f"{date}: bereits vorhanden ({existing[0].name})")
        return
    if str(date) in load_deleted(dest):
        log.info(f"{date}: gelöscht, übersprungen")
        return

    info = fetch_json(f"{API}/broadcast/{program}/{day}")
    streams = [s for s in info.get("streams", []) if s.get("loopStreamId")]
    if not streams:
        log.info(f"{date}: noch kein Stream verfügbar")
        return

    title = re.sub(r'[/\\:*?"<>|]', "-", info.get("title") or program).strip(" .")
    target = dest / f"{date} {title}.mp3"
    if dry_run:
        log.info(f"{date}: würde laden -> {target.name}")
        return

    log.info(f"{date}: lade {target.name} ...")
    if on_download:
        on_download(target.name)
    download(streams, target, on_progress, parallel)
    meta = {k: info.get(k) for k in ("title", "subtitle", "description", "moderator", "startISO", "endISO", "url")}
    target.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(f"{date}: fertig ({target.stat().st_size / 1e6:.1f} MB)")


def run(program, dest, dry_run=False, on_download=None, on_progress=None, parallel=PARALLEL):
    """Lädt alle fehlenden Folgen. Gibt True zurück, wenn alles geklappt hat."""
    dest.mkdir(parents=True, exist_ok=True)
    try:
        episodes = find_episodes(program)
    except Exception as e:
        log.error(f"FEHLER beim Abrufen der Sendungsliste: {e}")
        return False
    if not dry_run:
        # Abgebrochene Downloads werden fortgesetzt; nur Reste von Folgen, die nicht mehr
        # online sind, wegräumen.
        online = {str(day_to_date(d)) for d in episodes}
        for leftover in dest.glob("*.mp3.part*"):
            if leftover.name[:10] not in online:
                leftover.unlink()
    log.info(f"{len(episodes)} Folge(n) von {program} online")
    ok = True
    for day in episodes:
        try:
            process(program, day, dest, dry_run, on_download, on_progress, parallel)
        except Exception as e:
            log.error(f"{day}: FEHLER {e}")
            ok = False
    return ok


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", type=Path, default=Path.home() / "Music" / "Extra WOW", help="Zielordner")
    parser.add_argument("--program", default="WXWOW", help="ORF programKey der Sendung")
    parser.add_argument("--dry-run", action="store_true", help="nur anzeigen, nichts laden")
    parser.add_argument("--parallel", type=int, default=PARALLEL, help="gleichzeitige Verbindungen pro Folge")
    args = parser.parse_args()

    setup_logging()
    return 0 if run(args.program, args.dest.expanduser(), args.dry_run, parallel=max(1, args.parallel)) else 1


if __name__ == "__main__":
    sys.exit(main())
