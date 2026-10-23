FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml ./
COPY pbs_mcp ./pbs_mcp

ENV MCP_HOST=0.0.0.0 \
    MCP_PORT=8000

# Git-Commit und Version des Builds (von GitHub Actions gesetzt) - get_version
# gibt beides zurueck, damit nach einem Redeploy pruefbar ist, welcher Stand wirklich laeuft.
ARG GIT_SHA=unbekannt
ARG APP_VERSION=
ENV GIT_SHA=$GIT_SHA \
    APP_VERSION=$APP_VERSION

# Nicht als root laufen
RUN useradd --system --uid 10001 --no-create-home app
USER app

EXPOSE 8000

# /healthz ist ohne Auth erreichbar und prueft nur, ob der Prozess antwortet
# (nicht die PBS-API). Kein curl/wget im Image, darum per Python/urllib.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('MCP_PORT', '8000') + '/healthz', timeout=4)"]

CMD ["python", "-m", "pbs_mcp"]
