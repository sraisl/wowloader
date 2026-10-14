#!/usr/bin/env python3
"""Lädt neue Folgen einer Radio-Wien-Sendung (Standard: "Extra WOW", WXWOW) herunter.

Die ORF-Audio-API listet die Sendungen der letzten 7 Tage. Bereits geladene
Folgen werden übersprungen, verpasste Tage werden nachgeholt.
"""

import argparse
import json
import re
import ssl
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

API = "https://audioapi.orf.at/wie/api/json/4.0"
LOOPSTREAM = "https://loopstream01.apa.at/?channel=oe2w&shoutcast=0&id={}"
USER_AGENT = "wow-downloader/1.0"
CHUNK = 1024 * 1024

# python.org-Python bringt ohne "Install Certificates.command" keine CA-Liste mit;
# dann den macOS-Systemstore verwenden.
SSL_CONTEXT = ssl.create_default_context()
if not SSL_CONTEXT.cert_store_stats()["x509_ca"] and Path("/etc/ssl/cert.pem").exists():
    SSL_CONTEXT.load_verify_locations("/etc/ssl/cert.pem")


def open_url(url, timeout):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(req, timeout=timeout, context=SSL_CONTEXT)


def log(msg):
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}", flush=True)


def fetch_json(url):
    with open_url(url, 30) as resp:
        return json.load(resp)


def find_episodes(program):
    days = fetch_json(f"{API}/broadcasts")
    return sorted(
        b["broadcastDay"]
        for day in days
        for b in day["broadcasts"]
        if b["programKey"] == program and b.get("isOnDemand")
    )


def download(stream_ids, target):
    part = target.with_name(target.name + ".part")
    try:
        with open(part, "wb") as out:
            for sid in stream_ids:
                with open_url(LOOPSTREAM.format(sid), 60) as resp:
                    while chunk := resp.read(CHUNK):
                        out.write(chunk)
        part.rename(target)
    except BaseException:
        part.unlink(missing_ok=True)
        raise


def process(program, day, dest, dry_run):
    date = datetime.strptime(str(day), "%Y%m%d").date()
    existing = list(dest.glob(f"{date} *.mp3"))
    if existing:
        log(f"{date}: bereits vorhanden ({existing[0].name})")
        return

    info = fetch_json(f"{API}/broadcast/{program}/{day}")
    stream_ids = [s["loopStreamId"] for s in info.get("streams", []) if s.get("loopStreamId")]
    if not stream_ids:
        log(f"{date}: noch kein Stream verfügbar")
        return

    title = re.sub(r'[/\\:*?"<>|]', "-", info.get("title") or program).strip(" .")
    target = dest / f"{date} {title}.mp3"
    if dry_run:
        log(f"{date}: würde laden -> {target.name}")
        return

    log(f"{date}: lade {target.name} ...")
    download(stream_ids, target)
    meta = {k: info.get(k) for k in ("title", "subtitle", "description", "moderator", "startISO", "endISO", "url")}
    target.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"{date}: fertig ({target.stat().st_size / 1e6:.1f} MB)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", type=Path, default=Path.home() / "Music" / "Extra WOW", help="Zielordner")
    parser.add_argument("--program", default="WXWOW", help="ORF programKey der Sendung")
    parser.add_argument("--dry-run", action="store_true", help="nur anzeigen, nichts laden")
    args = parser.parse_args()

    dest = args.dest.expanduser()
    dest.mkdir(parents=True, exist_ok=True)

    episodes = find_episodes(args.program)
    log(f"{len(episodes)} Folge(n) von {args.program} online")
    failed = False
    for day in episodes:
        try:
            process(args.program, day, dest, args.dry_run)
        except Exception as e:
            log(f"{day}: FEHLER {e}")
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
