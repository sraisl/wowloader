# Extra WOW Downloader

Lädt die Radio-Wien-Sendung „Extra WOW – Die 2 um 2." (Mo–Fr 14–15 Uhr) als MP3 herunter.
Der ORF hält Sendungen 7 Tage online; jeder Lauf holt alle noch fehlenden Folgen dieses Zeitraums.

## Manuell

```sh
python3 wow_downloader.py --dry-run          # nur anzeigen
python3 wow_downloader.py                    # nach ~/Music/Extra WOW laden
python3 wow_downloader.py --dest ~/Downloads/wow
```

Neben jeder MP3 liegt eine `.json` mit Titel und Beschreibung.

## Server / Docker

Der Container prüft regelmäßig auf neue Folgen und stellt auf Port 8000 ein Web-UI bereit. Dort kann man:
- die Folgen abspielen, herunterladen und löschen,
- einen Lauf sofort auslösen und Status und Log ansehen,
- gelöschte Folgen erneut laden, solange sie beim ORF noch online sind.

Unter `/feed.xml` gibt es einen Podcast-Feed.

```sh
docker compose up -d --build            # Zielordner: ./downloads (siehe compose.yaml)
# oder
docker build -t wowloader .
docker run -d -p 8000:8000 -v /pfad/zum/ziel:/data wowloader
```

| Variable | Standard | Bedeutung |
|---|---|---|
| `DEST` | `/data` | Zielverzeichnis im Container (als Volume mounten) |
| `PROGRAM` | `WXWOW` | ORF-`programKey` |
| `CHECK_INTERVAL_MINUTES` | `60` | Abstand der automatischen Prüfungen |
| `PARALLEL` | `4` | gleichzeitige Verbindungen pro Folge (der ORF drosselt jede auf ~270 KB/s) |
| `BASE_URL` | – | öffentliche URL für Feed-Links, z. B. hinter einem Reverse Proxy |

Downloads laufen in 4-MB-Stücken über mehrere Verbindungen; ein abgebrochener Download (z. B. nach
einem Container-Neustart) wird beim nächsten Lauf fortgesetzt, einzelne fehlgeschlagene Stücke werden
bis zu dreimal wiederholt.

Im UI gelöschte Folgen werden in `.deleted.json` im Zielordner vermerkt, damit sie nicht automatisch
neu geladen werden. Das UI hat keine Authentifizierung: Betreibe es nur im LAN oder hinter einem Proxy mit
Login. Der Container läuft als UID 1000; der gemountete Ordner muss für diese UID beschreibbar sein
(oder `user:` in `compose.yaml` setzen).

## Automatisch (launchd)

```sh
./install.sh
```

Läuft Mo–Fr um 16:00 sowie beim Login (falls der Mac um 16 Uhr schlief).
Log: `~/Library/Logs/wow-downloader.log`. Sofort auslösen:
`launchctl kickstart gui/$(id -u)/at.orf.wow-downloader`

Deinstallieren:

```sh
launchctl bootout gui/$(id -u)/at.orf.wow-downloader
rm ~/Library/LaunchAgents/at.orf.wow-downloader.plist
```
