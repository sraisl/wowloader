# Extra WOW Downloader – `just` ohne Argument listet alle Befehle.

port := "8000"
dev_dest := "./downloads"
label := "at.orf.wow-downloader"

default:
    @just --list

# --- CLI ---------------------------------------------------------------

# Zeigt an, welche Folgen geladen würden
dry-run dest="~/Music/Extra WOW":
    python3 wow_downloader.py --dry-run --dest "{{dest}}"

# Lädt fehlende Folgen einmalig über die Kommandozeile
fetch dest="~/Music/Extra WOW":
    python3 wow_downloader.py --dest "{{dest}}"

# --- Server lokal (ohne Docker) ---------------------------------------

# Startet das Web-UI lokal mit Auto-Reload (benötigt uv)
dev dest=dev_dest:
    DEST="{{dest}}" uv run --python 3.13 --with-requirements requirements.txt \
        uvicorn app:app --reload --port {{port}}

# Prüft Python- und JavaScript-Syntax
check:
    python3 -m py_compile wow_downloader.py app.py
    python3 -c "import re; print('\n'.join(re.findall(r'<script>(.*?)</script>', open('static/index.html').read(), re.S)))" \
        | node --check --input-type=commonjs
    @echo "OK"

# --- Docker ------------------------------------------------------------

# Baut das Image
build:
    docker compose build

# Startet den Container im Hintergrund (Ziel: ./downloads)
up:
    docker compose up -d --build
    @echo "UI: http://localhost:{{port}}"

# Stoppt und entfernt den Container
down:
    docker compose down

# Startet den Container neu
restart:
    docker compose restart

# Folgt dem Container-Log
logs:
    docker compose logs -f

# Zeigt den Status des laufenden Servers
status:
    @python3 -c "import json, urllib.request; s = json.load(urllib.request.urlopen('http://localhost:{{port}}/api/status')); s.pop('log'); print(json.dumps(s, indent=2, ensure_ascii=False))"

# Löst sofort eine Prüfung auf neue Folgen aus
run-now:
    @python3 -c "import urllib.request; urllib.request.urlopen(urllib.request.Request('http://localhost:{{port}}/api/run', method='POST')); print('Lauf angestoßen')"

# --- macOS launchd -----------------------------------------------------

# Installiert den launchd-Job (Mo–Fr 16:00 und beim Login)
install:
    ./install.sh

# Entfernt den launchd-Job
uninstall:
    -launchctl bootout gui/$(id -u)/{{label}}
    rm -f ~/Library/LaunchAgents/{{label}}.plist

# Startet den launchd-Job sofort
kick:
    launchctl kickstart gui/$(id -u)/{{label}}

# Folgt dem launchd-Log
launchd-log:
    tail -f ~/Library/Logs/wow-downloader.log
