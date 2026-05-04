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

## Research portal

Use **Research** in the sidebar to create and track long-running research jobs.
The Django server is the portal/orchestrator; the large model and critic should
run on another machine behind `RESEARCH_ENGINE_URL`.

When a user creates a research job:

- Django stores a `ResearchJob` row with query, depth, social-source preference,
  status, plan, final Markdown report, and remote job id.
- Celery sends the request to the remote engine.
- Celery polls the remote engine and records status events, sources, errors, and
  the final report.
- The job detail page polls `/research/<id>/status/` every 5 seconds.

Configure the remote engine in `.env`:

```bash
RESEARCH_ENGINE_URL=http://model-server:9000
RESEARCH_ENGINE_TOKEN=replace-with-shared-secret
RESEARCH_ENGINE_TIMEOUT_SECONDS=30
RESEARCH_ENGINE_POLL_SECONDS=5
RESEARCH_ENGINE_MAX_POLLS=2160
RESEARCH_TASK_TIME_LIMIT_SECONDS=21600
RESEARCH_TASK_SOFT_TIME_LIMIT_SECONDS=21000
RESEARCH_PORTAL_PUBLIC_BASE_URL=http://144.202.24.190
RESEARCH_SOCIAL_TOOL_BASE_URL=http://144.202.24.190
RUNPOD_API_KEY=
RUNPOD_POD_ID=
RUNPOD_ENGINE_PORT=8000
RUNPOD_AUTOSTART_ENABLED=False
RUNPOD_AUTOSTOP_AFTER_JOB=False
```

If the portal is exposed only by IP, include it in `ALLOWED_HOSTS`, for example:

```bash
ALLOWED_HOSTS=144.202.24.190,localhost,127.0.0.1
```

Remote engine API contract:

- `POST /v1/research/jobs`
- `GET /v1/research/jobs/{external_job_id}`

The submit response must either return a finished report or a remote job id:

```json
{
  "external_job_id": "research-123",
  "status": "planning",
  "message": "Research accepted"
}
```

Polling responses can include any of these fields:

```json
{
  "status": "critiquing",
  "plan": {"steps": ["web search", "social scan", "critic report"]},
  "sources": [
    {
      "source_type": "web",
      "title": "Source title",
      "url": "https://example.com",
      "excerpt": "Short evidence excerpt"
    }
  ],
  "report_markdown": "# Final report",
  "error_message": ""
}
```

Known remote statuses are normalized into portal states: `queued`, `planning`,
`collecting`, `social_collecting`, `critiquing`, `completed`, `failed`, and
`cancelled`.

### RunPod lifecycle mode

For the cheapest Pod-based workflow, keep the GPU Pod stopped when no research
is running. The portal supports two modes:

- Manual mode: leave `RUNPOD_AUTOSTART_ENABLED=False`. Start the RunPod pod
  yourself, then set `RESEARCH_ENGINE_URL=https://<pod-id>-8000.proxy.runpod.net`.
- Automatic mode: set `RUNPOD_API_KEY`, `RUNPOD_POD_ID`,
  `RUNPOD_AUTOSTART_ENABLED=True`, and optionally
  `RUNPOD_AUTOSTOP_AFTER_JOB=True`. The Celery worker resumes the pod, waits for
  `/health`, runs the research job, and stops the pod when no other research job
  is active.

If `RESEARCH_ENGINE_URL` is empty but `RUNPOD_POD_ID` and `RUNPOD_ENGINE_PORT`
are set, the portal derives the proxy URL as:

```text
https://<RUNPOD_POD_ID>-<RUNPOD_ENGINE_PORT>.proxy.runpod.net
```

RunPod API keys are secrets. Store `RUNPOD_API_KEY` only in the production
`.env` or secret manager, never in source control.

## Social monitoring data

The dashboard supports normal CRUD for:

- `MonitoredAccount`: public Facebook Page or Instagram public account URL.
- `Keyword`: active phrase to match.

Use the dashboard to add, edit, and deactivate accounts or keywords. Deactivate
keeps historical posts, matches, and check runs intact. Django admin is still
available for full internal management, including `TelegramChat` rows.

Dashboard pages show active account count, active keyword count, total matches,
latest matches, latest check runs, and each account's last status/error.
