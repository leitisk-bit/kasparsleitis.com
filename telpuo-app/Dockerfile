FROM python:3.12-slim

# poppler renders the map PDFs the same way they print
RUN apt-get update \
 && apt-get install -y --no-install-recommends poppler-utils \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py ./
COPY oreplay ./oreplay
COPY static ./static

ENV OREPLAY_DATA=/data
VOLUME /data
EXPOSE 8000
CMD ["gunicorn", "-w", "1", "--threads", "8", "--timeout", "600", "-b", "0.0.0.0:8000", "app:app"]
