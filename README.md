# spy · Facebook monitoring

A private Django workspace for collecting and reviewing public Facebook posts
and comments. Staff land directly in **Findings**, with a PCL-focused feed,
search, saved discussions and a side-by-side reader. The interface is English.

## Workspace

- `/research/facebook/monitor/` — findings and unique collection totals.
- `/research/facebook/sources/` — pages, public groups and search phrases.
- `/research/facebook/settings/` — collection schedule, pause/resume and activity.
- `/research/facebook/new/` — optional one-time scans.

Posts read counts public posts with saved text; links found includes unread or
unavailable links. Comments read counts saved comment/reply records once across
repeat scans. Search and date filters narrow the feed, not the global totals.
PCL matches are keyword matches, not a negative-sentiment assessment. Read marks
and bookmarks are personal to each operator and do not change collected evidence.

Collection uses durable queues, deduplicated evidence, per-source discovery and
bounded recurring passes. See [collector operations](docs/facebook-scan.md) for
budgets, recovery, migrations and deployment notes. Private or inaccessible
content, full historical coverage and video analysis are outside current scope.

The older account-monitoring dashboard remains at `/?legacy=1`; remote research
jobs remain at `/research/`. Notification delivery and AI classification are not
part of the findings workspace. The next product stage is source expansion and
AI-assisted relevance analysis.

## Scope and safety rules

- Monitor only public pages and public accounts.
- Optional Facebook login uses a saved browser session for an operator-owned
  account, but monitoring scope should still stay limited to public pages and
  accounts you are allowed to monitor.
- Do not use CAPTCHA solving, checkpoint bypasses, or other protection bypasses.
- Do not access private accounts, paywalled content, or content hidden behind login.
- Store only the text and short raw snapshot needed for keyword monitoring.
- Checks include random delays between accounts.

The legacy account checker is intentionally conservative. It opens the
public URL with Playwright headless Chromium, reads visible page text, stores a
bounded snapshot, and searches active keywords. Its bounded account snapshots are separate from the durable Facebook discussion
collector described above.

## Stack

- Django, Django ORM, Django admin, Django templates
- PostgreSQL in Docker Compose
- Redis, Celery worker, Celery beat
- Remote research engine API for the large language model and critic loop
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

- Research portal: http://127.0.0.1:8000/research/
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
