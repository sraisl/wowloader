FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 wow

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY wow_downloader.py app.py ./
COPY static ./static

ENV DEST=/data \
    TZ=Europe/Vienna \
    PYTHONUNBUFFERED=1
RUN mkdir /data && chown wow /data
VOLUME /data
USER wow
EXPOSE 8000

HEALTHCHECK --interval=60s --timeout=5s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/status', timeout=4)"

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
