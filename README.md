# Spy Monitor MVP

Django web portal and Telegram bot skeleton for monitoring public Facebook Pages
and Instagram public accounts for keyword matches.

## Scope and safety rules

- Monitor only public pages and public accounts.
- Optional Facebook login uses a saved browser session for an operator-owned
  account, but monitoring scope should still stay limited to public pages and
  accounts you are allowed to monitor.
- Do not use CAPTCHA solving, checkpoint bypasses, or other protection bypasses.
- Do not access private accounts, paywalled content, or content hidden behind login.
- Store only the text and short raw snapshot needed for keyword monitoring.
- Checks include random delays between accounts.

The first checker implementation is intentionally conservative. It opens the
public URL with Playwright headless Chromium, reads visible page text, stores a
bounded snapshot, and searches active keywords. It does not attempt post-level
scraping or platform-specific bypass behavior.

## Stack

- Django, Django ORM, Django admin, Django templates
- PostgreSQL in Docker Compose
- Redis, Celery worker, Celery beat
- Playwright headless Chromium
- python-telegram-bot
- Nginx reverse proxy
- Certbot service and HTTPS config example

## Local development setup

```bash
cp .env.example .env
```

For host-based development, set `DEBUG=True` and either remove `DATABASE_URL`
to use SQLite or point it at a local PostgreSQL database. Add a real
`SECRET_KEY` before running with `DEBUG=False`.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
python manage.py migrate
python manage.py runserver
```

Create the first web/admin user:

```bash
python manage.py createsuperuser
```

Open:

- Dashboard: http://127.0.0.1:8000/
- Admin: http://127.0.0.1:8000/admin/
- Health: http://127.0.0.1:8000/health/

## Docker Compose setup

Edit `.env` before first boot:

- Set `SECRET_KEY`.
- Set `POSTGRES_PASSWORD`.
- Make `DATABASE_URL` use the same PostgreSQL password.
- Set `TELEGRAM_BOT_TOKEN` if Telegram alerts or bot polling are needed.
- Facebook auth can be turned on with `FACEBOOK_AUTH_ENABLED=True`, or by saving
  active Facebook credentials in **Facebook Login** after the app is running.

Start the web stack:

```bash
docker compose up --build
```

To use the Compose-managed Nginx service in a standalone environment:

```bash
docker compose --profile nginx up --build
```

If your Docker Compose build path fails inside Bake, retry with:

```bash
COMPOSE_BAKE=false docker compose up --build
```

Create an admin user:

```bash
docker compose exec web python manage.py createsuperuser
```

Run one monitoring pass manually:

```bash
docker compose exec web python manage.py check_accounts --force
```

Run the Telegram bot service:

```bash
docker compose --profile bot up -d telegram-bot
```

Celery beat runs `check_accounts` every `MONITORING_BEAT_SECONDS` seconds and
each account still respects its own `check_interval_minutes`.

## Managing data

The dashboard supports normal CRUD for:

- `MonitoredAccount`: public Facebook Page or Instagram public account URL.
- `Keyword`: active phrase to match.

Use the dashboard to add, edit, and deactivate accounts or keywords. Deactivate
keeps historical posts, matches, and check runs intact. Django admin is still
available for full internal management, including `TelegramChat` rows.

Dashboard pages show active account count, active keyword count, total matches,
latest matches, latest check runs, and each account's last status/error.

## Manual checks

From the web portal, use **Run all active checks now** on the dashboard or
accounts page. Each account row also has **Check this account now**. The optional
`Limit to last N posts` field overrides the account's default
`max_posts_per_check` for that manual run only.

For the MVP manual checks run synchronously and may take time if several
accounts are configured.

From the command line:

```bash
python manage.py check_accounts --force
python manage.py check_accounts --force --no-telegram
python manage.py check_accounts --force --account-id 3
python manage.py check_accounts --force --account-id 3 --limit 5
```

Without `--force`, the command only checks active accounts whose
`check_interval_minutes` has elapsed. `--limit` means "process at most N posts
per account for this run" and must be between 1 and 20.

Each monitored account has:

- `check_interval_minutes`: how often scheduled checks should run.
- `max_posts_per_check`: default number of recent candidates to process, 1-20.
- `scroll_rounds`: conservative page scroll count, 0-5.

## Facebook public checker

The Facebook checker is a conservative, best-effort public Page monitor. It:

- launches headless Chromium with a desktop user agent
- optionally loads a saved Playwright Facebook browser session
- opens the configured public Facebook Page URL
- waits for visible body content
- detects login walls, CAPTCHA/checkpoints, unavailable/private pages, rate
  limits, and empty responses before parsing
- scrolls slowly for the configured `scroll_rounds`
- extracts visible post candidates from `role="article"` containers and links
  containing `/posts/`, `/permalink/`, `story_fbid=`, `/photos/`, `/videos/`,
  or `/reel/`
- normalizes Facebook post URLs and removes common tracking parameters
- creates one `Post` per stable candidate ID
- keyword-matches each post separately
- sends Telegram alerts only for newly-created post-keyword matches

Blocked statuses:

- `login_required`: Facebook showed a login wall.
- `captcha_or_checkpoint`: CAPTCHA, checkpoint, or security check detected.
- `private_or_unavailable`: private, removed, or unavailable content detected.
- `rate_limited_or_blocked`: temporary block or rate limit detected.
- `empty_response`: no usable visible text was returned.

Current limitations of non-API Facebook monitoring:

- Facebook markup changes frequently, so selectors are best-effort.
- Facebook can show different content by account, region, IP, or time.
- Saved browser sessions expire. If Facebook rejects the session, refresh it
  manually; the checker never solves CAPTCHA/checkpoint challenges.
- Timestamp parsing is opportunistic; missing timestamps do not block matching.
- Photo/video/reel links are used only when nearby visible text is available.

## Optional Facebook authenticated session

For pages that show a generic login wall to unauthenticated browsers, save
Facebook credentials in **Facebook Login** or create a Playwright storage-state
file manually.

This setting forces authenticated checks even before credentials are saved:

```bash
FACEBOOK_AUTH_ENABLED=True
FACEBOOK_AUTH_STORAGE_STATE_PATH=/app/runtime/facebook_storage_state.json
```

Credentials can be managed without editing `.env`: sign in to the web app with
a staff user, open **Facebook Login**, and save the Facebook username/password.
The password is encrypted before being stored in the database. The same model is
also available in Django admin as `PlatformCredential`. Active stored
credentials automatically enable authenticated checks; no `.env` edit is needed.

When auth is active, the checker loads the saved Playwright session. If the
session file is missing or Facebook rejects it with a login wall, CAPTCHA, or
checkpoint, the check is marked `auth_required` and sends a Telegram alert. The
scheduled checker does not attempt to solve CAPTCHA/checkpoint challenges or
keep retrying credentials.

Create or refresh the session:

```bash
python manage.py refresh_facebook_session
```

That opens a browser window. Log in normally, complete any first-party 2FA or
checkpoint prompts yourself, and the command saves the resulting browser
session.

For a server or Docker environment, use the dedicated human-assisted session
manager. Set a protected operator URL in `.env`:

```bash
FACEBOOK_SESSION_MANAGER_URL=https://spy.raccncode.com/facebook-session/vnc.html
FACEBOOK_SESSION_REQUEST_TTL_SECONDS=900
FACEBOOK_SESSION_RUN_CHECKS_AFTER_SUCCESS=True
```

Start the service:

```bash
docker compose -f docker-compose.prod.yml --profile session up -d facebook-session-manager
```

Then sign in to the web app as a staff user, open **Facebook Login**, and create
a session refresh request. The app sends the request to Telegram. Open the
session-manager browser, complete Facebook login, 2FA, or checkpoint manually,
and the session manager saves `/app/runtime/facebook_storage_state.json`.

The session manager is a separate container with write access to the Facebook
session volume. The Celery worker mounts the same volume read-only and only
uses an already-saved session. By default the noVNC port is bound to
`127.0.0.1:${SPY_FACEBOOK_SESSION_PORT:-18011}`; expose it only through a
protected reverse proxy or SSH tunnel.

## Telegram setup

Create a Telegram bot with BotFather, then put the token in `.env`:

```bash
TELEGRAM_BOT_TOKEN=123456789:replace-with-real-token
```

For local development:

```bash
python manage.py run_telegram_bot
```

For Docker Compose:

```bash
docker compose --profile bot up -d telegram-bot
```

Open a chat with the bot and send `/start`. This creates or reactivates a
`TelegramChat` row. Alerts are sent to active `TelegramChat` rows only.

If `TELEGRAM_BOT_TOKEN` is empty or no active chats exist, alert sending logs a
skip and does not crash checks.

## Telegram commands

- `/start` registers the current chat for alerts.
- `/accounts` lists active monitored accounts.
- `/keywords` lists active keywords.
- `/matches` lists the latest 5 matches.
- `/session` shows Facebook session state and recent session refresh requests.
- `/addaccount`, `/removeaccount`, `/addkeyword`, and `/removekeyword` are MVP
  placeholders. Use the dashboard or Django admin for changes.

Alerts use this format:

```text
Platform:
Account:
Keyword:
Post:
Preview:
```

## Logs and observability

Docker Compose logs:

```bash
docker compose -f docker-compose.prod.yml logs -f web
docker compose -f docker-compose.prod.yml logs -f celery-worker
docker compose -f docker-compose.prod.yml logs -f celery-beat
docker compose -f docker-compose.prod.yml --profile bot logs -f telegram-bot
```

Nginx logs on the VPS:

```bash
tail -f /var/log/nginx/spy-access.log
tail -f /var/log/nginx/spy-error.log
```

The dashboard check-run views show latest run status, posts found, new posts,
matches, and latest error per account.

## HTTPS for spy.raccncode.com

The default Nginx config serves HTTP and ACME challenges. Point DNS for
`spy.raccncode.com` at the server, then run:

```bash
docker compose --profile certbot run --rm certbot certonly \
  --webroot \
  --webroot-path /var/www/certbot \
  --email admin@raccncode.com \
  --agree-tos \
  --no-eff-email \
  -d spy.raccncode.com
```

After certificates exist, replace `nginx/default.conf` with the contents of
`nginx/ssl.conf.example`, then reload Nginx:

```bash
docker compose exec nginx nginx -s reload
```

For renewal, run:

```bash
docker compose --profile certbot run --rm certbot renew
docker compose exec nginx nginx -s reload
```

Set `SECURE_SSL_REDIRECT=True` after HTTPS is confirmed.

## Production deployment for spy.raccncode.com

Production runs from `/var/www/spy` on the VPS. Host-level Nginx owns ports 80
and 443, and proxies `spy.raccncode.com` to the Django container on
`127.0.0.1:18010`. Do not start the Compose `nginx` profile on that VPS unless
you intentionally replace the host-level reverse proxy.

Required production `.env` values:

```bash
DEBUG=False
SECRET_KEY=replace-with-strong-random-value
ALLOWED_HOSTS=spy.raccncode.com,localhost,127.0.0.1
CSRF_TRUSTED_ORIGINS=https://spy.raccncode.com
POSTGRES_DB=spy
POSTGRES_USER=spy
POSTGRES_PASSWORD=replace-with-strong-random-value
DATABASE_URL=postgres://spy:replace-with-strong-random-value@postgres:5432/spy
REDIS_URL=redis://redis:6379/0
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/0
TELEGRAM_BOT_TOKEN=
SPY_WEB_PORT=18010
SPY_FACEBOOK_SESSION_PORT=18011
FACEBOOK_SESSION_MANAGER_URL=
```

Deployment is repeatable with:

```bash
cd /var/www/spy
./deploy.sh
```

The script pulls latest code if `/var/www/spy` is a Git repo, rebuilds
containers, runs migrations, runs `collectstatic`, restarts web/Celery services,
starts the Telegram bot only when `TELEGRAM_BOT_TOKEN` is set, starts the
Facebook session manager only when `FACEBOOK_SESSION_MANAGER_URL` is set, and
prints container status.

After deployment, verify:

```bash
curl -I https://spy.raccncode.com/health/
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml exec web python manage.py check_accounts
```

## Useful commands

```bash
python manage.py check
python manage.py makemigrations
python manage.py migrate
python manage.py check_accounts --force --no-telegram
python manage.py run_telegram_bot
```
