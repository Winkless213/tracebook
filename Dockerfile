FROM node:24-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim-bookworm
ARG APP_RELEASE=dev
LABEL org.opencontainers.image.title="Tracebook" org.opencontainers.image.revision=$APP_RELEASE
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 DATA_DIR=/data APP_RELEASE=$APP_RELEASE
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home tracebook
COPY devrag/ ./devrag/
COPY scripts/backup.py ./scripts/backup.py
COPY --from=frontend /build/dist ./frontend/dist/
RUN mkdir /data && chown tracebook:tracebook /data
USER tracebook
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=3)"
CMD ["uvicorn", "devrag.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
