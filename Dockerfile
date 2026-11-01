FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ .

RUN useradd --system --uid 10001 maxmcp && mkdir -p /data && chown maxmcp /data
USER maxmcp

ENV MAX_HOST=0.0.0.0 \
    MAX_PORT=8766 \
    MAX_DIR=/data \
    PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8766

CMD ["python", "server.py"]
