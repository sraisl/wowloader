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
