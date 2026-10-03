FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV DATABASE_URL=sqlite:////data/sports_prediction.db

# Runs as a non-root user rather than the image's default root -- this
# process parses request bodies and fetches from third-party APIs, so a bug
# or a dependency CVE that achieves code execution shouldn't also hand over
# root inside the container. /data is created and owned here because it has
# to exist and be writable before VOLUME hands it to Docker; chowning it
# after the volume takes over wouldn't reach whatever gets mounted there.
RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /data \
    && chown -R appuser:appuser /app /data
VOLUME ["/data"]
USER appuser

# Most free hosts (Render, Koyeb, Cloud Run, Hugging Face Spaces) inject the
# port to bind as $PORT and health-check that port specifically; a hardcoded
# 8000 fails those deploys with no useful error. Defaults to 8000 so local
# docker-compose is unchanged.
ENV PORT=8000
EXPOSE 8000

# Shell form so $PORT is expanded at runtime rather than baked in at build.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
