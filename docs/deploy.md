# Deploying faultline

The app is one long-running Python process (the API, the UI, and the
toolserver and fault proxy in background threads) that streams live trial
events and writes a SQLite file. It needs a server where you can run your own
process or a Docker container. Static or PHP-only web hosting can't run it.

The repository ships a production setup in `compose.yaml`:

- **app**: the image built from the `Dockerfile`, running as a non-root user,
  with its database in the `faultline-data` volume. It is not published on any
  host port, so the only way in is through Caddy.
- **caddy**: a reverse proxy that gets and renews a free HTTPS certificate for
  your domain on its own, asks for a username and password, compresses
  responses, and passes the live event streams through unbuffered.

The password matters: the app has no accounts, and anyone who can open it can
start trials and sweeps, which spend your model quota when a key is set.

Resource use is small. Running, the two containers take about 80 MB of memory
together; the image is about 270 MB. Building the image on the server needs a
few hundred MB more for a minute.

<!-- HOSTINGER -->

## Deploy on any Linux server with Docker

1. **Point a domain at the server.** In your DNS provider, add an `A` record
   for the name you want (for example `faultline.yourdomain.com`) with the
   server's IPv4 address, and an `AAAA` record if the server has IPv6. Remove
   any other `A`/`AAAA` record on that name. Wait until
   `ping faultline.yourdomain.com` answers from the server's IP.

2. **Open ports 80 and 443** (TCP, plus UDP 443 for HTTP/3) in the server's
   firewall. Caddy needs port 80 to prove it owns the domain.

3. **Get the code and configure it** (over SSH on the server):

   ```bash
   git clone https://github.com/SohamDutta02/faultline.git
   cd faultline
   cp deploy/.env.example .env
   docker run --rm caddy:2 caddy hash-password --plaintext 'choose-a-password'
   nano .env
   ```

   In `.env`, set `DOMAIN`, `BASIC_AUTH_USER`, and `BASIC_AUTH_HASH` (paste the
   hash inside single quotes; it contains `$` signs). Add a model key
   (`GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, or `OPENAI_API_KEY`/`OPENAI_BASE_URL`)
   to enable the model-driven harnesses.

   If the repository is private, clone it with a GitHub personal access token
   or a deploy key instead.

4. **Start it:**

   ```bash
   docker compose up -d --build
   docker compose logs -f caddy     # watch for "certificate obtained successfully"
   ```

   Open `https://faultline.yourdomain.com` and log in.

### Day to day

| task | command (in the `faultline` directory) |
|---|---|
| update to the latest code | `git pull && docker compose up -d --build` |
| see logs | `docker compose logs -f app` |
| restart | `docker compose restart` |
| stop | `docker compose down` (data is kept in the volumes) |
| back up the database | see below |
| change the password | update `BASIC_AUTH_HASH` in `.env`, then `docker compose up -d` |

Both containers restart on their own after a crash or a server reboot.

To back up the database while the app runs, let SQLite make a consistent copy
inside the container, then copy it out:

```bash
docker compose exec app python -c "import sqlite3; sqlite3.connect('/data/faultline.db').backup(sqlite3.connect('/data/backup.db'))"
docker compose cp app:/data/backup.db ./faultline-backup.db
```

### Without a domain

For a quick look, set `DOMAIN=:80` in `.env` and open `http://SERVER_IP`.
There is no HTTPS in this mode, so the password travels unencrypted; switch
to a real domain before sharing the link.

### Troubleshooting

- **Caddy can't get a certificate**: the DNS record doesn't point at this
  server yet, an old `A` record is still in place, or port 80 is closed.
  `docker compose logs caddy` names the reason.
- **The trace doesn't update live**: something between the browser and Caddy
  is buffering responses (for example a CDN or proxy in front of the domain).
  Turn it off for this name; Caddy itself streams events unbuffered.
- **"model not configured" in the header**: no model key is set in `.env`
  (or only `OPENAI_BASE_URL`, with no `FAULTLINE_MODEL`); set one and run
  `docker compose up -d`.
