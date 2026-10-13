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
RUN mkdir -p /data
VOLUME /data
EXPOSE 8000
CMD ["python", "-m", "api", "--host", "0.0.0.0", "--port", "8000", "--db", "/data/faultline.db"]
