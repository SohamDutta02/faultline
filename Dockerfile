# Build the web UI, then serve it with the API, toolserver and proxy in one process.
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.13-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
COPY --from=web /web/dist ./web/dist
RUN useradd --system --uid 10001 --no-create-home faultline \
    && mkdir -p /data && chown faultline /data
USER faultline
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"
CMD ["python", "-m", "api", "--host", "0.0.0.0", "--port", "8000", "--db", "/data/faultline.db"]
