# The Tapestry server: one container, the embedding model built in (PLAN.md stage 6).
FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir '.[server]' \
 && TAPESTRY_MODEL_DIR=/opt/tapestry-model python -c "from tapestry import embed; embed.fetch_model(__import__('pathlib').Path('/opt/tapestry-model'))"

ENV TAPESTRY_MODEL_DIR=/opt/tapestry-model \
    TAPESTRY_SERVER_HOME=/data \
    TAPESTRY_HOME=/data \
    PYTHONUNBUFFERED=1
RUN useradd --system --uid 10001 --home /data tapestry && mkdir -p /data && chown tapestry /data
USER tapestry
VOLUME /data
EXPOSE 8650
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8650/v1/health', timeout=3)"
CMD ["tapestry", "server", "run", "--host", "0.0.0.0", "--port", "8650"]
