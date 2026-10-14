FROM python:3.13-alpine

# Alpine statt Debian-slim: deutlich weniger (ungefixte) CVEs im Basis-Image.
# Paketversionen nicht pinnen: Sicherheitsupdates kommen über das Basis-Image (Dependabot)
# hadolint ignore=DL3018
RUN apk add --no-cache tzdata \
    && adduser -D -u 1000 wow

WORKDIR /app
COPY requirements.txt .
# pip/ensurepip werden zur Laufzeit nicht gebraucht; ihre gebündelten Pakete erzeugen sonst CVE-Funde
RUN pip install --no-cache-dir -r requirements.txt \
    && pip uninstall -y pip setuptools wheel \
    && rm -rf /usr/local/lib/python3.13/ensurepip
COPY wow_downloader.py app.py ./
COPY static ./static

ENV DEST=/data \
    TZ=Europe/Vienna \
    PYTHONUNBUFFERED=1
RUN mkdir /data && chown 1000:1000 /data
VOLUME /data
USER 1000:1000
EXPOSE 8000

HEALTHCHECK --interval=60s --timeout=5s \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/status', timeout=4)"]

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
