# Build context is the repo root, so the encoder/ package copies as a unit:
#   docker build -f encoder/Dockerfile -t sizzle-encoder .
FROM python:3.11-slim

WORKDIR /app

COPY encoder/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY encoder/ ./encoder/

ENV PORT=5002
EXPOSE 5002

# One worker: the fallback path holds a Whisper model in memory, and the
# primary path is fast enough that concurrency is not the bottleneck.
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:$PORT --workers 1 --timeout 600 'encoder.service:create_app()'"]
