# Facebook browser collection

Production portal: https://spy.raccncode.com/research/

Staff operators can select **Facebook scan**, enter one search phrase or public
Facebook post/Reel URL per line (up to 12), and choose a depth. No Meta API or
remote research engine is used by this mode. It uses the saved Facebook session.
Posts and comments are now retained in a shared database corpus. Comment IDs are
unique per canonical post URL; observed text changes are recorded as revisions.
Observation times are not publication times, and revisions may reflect Facebook
translation/extraction changes, not necessarily edits by an author. The admin
contains read-only post/comment records with comment revision history.

Every discovered URL gets a durable `FacebookThreadWork` row. Visible comment
batches commit immediately; errors never delete previous evidence. **Continue
collection** resumes pending/partial/failed threads without clearing sources or
repeating successful discovery. If all threads were sampled, an explicit Continue
rechecks them. No further visible comments observed is not a completeness claim.

- Quick: up to 5 discovered links per query, post text only.
- Standard: up to 10 links per query, batches of 40 additional comments.
- Deep: up to 20 links per query, batches of 100 additional comments.
- All discovered links are queued; there is no separate total visit truncation.
- Each discussion gets up to 3 attempts per cycle. Budget grows from 45 to 150
  seconds of comment traversal per attempt, plus bounded page navigation/setup.
  After limits/errors persist, the job ends with explicit gaps and can be continued.
- Each Celery task handles one query or discussion. A 30-second beat dispatcher
  finds active DB jobs even after broker/task loss. A 300-second Redis lease and
  270-second hard task limit enforce one browser scan at a time. Abandoned running
  rows are reclaimed once the lease expires. This is queue recovery, **not** a
  schedule for repeatedly searching Facebook after a completed scan.
- Facebook has no durable DOM scroll cursor. Resume reopens and replays a thread,
  deduplicates saved comments and increases the traversal budget. Very large or
  unstable threads can still stall; remaining gaps are never called complete.
- Login challenges/rate limits pause all active browser scan jobs for operator
  review. They do not trigger automated attempts to bypass access controls.

Search is personalized and may return old posts. Only content with a public
visibility marker is retained as post/comment evidence. Login challenges or
rate limits stop collection; they are not bypassed. Comments are switched to
All/Newest where available, and only read-only expansion controls are clicked.
Every comment must have a permalink belonging to the exact target post.

PCL matching is a word/phrase flag for manual review, not sentiment analysis or
an allegation classifier. No AI model is connected in this first collector.
Video, audio, images, hidden/deleted comments and inaccessible/private sources
are not analyzed. Visible text can be automatically translated by Facebook.
A completed job is a completed bounded scan, not complete coverage of Facebook.
The report identifies unvisited links, per-source limits and extraction gaps.

## Operator login

Open `/settings/facebook-login/` as a staff user. The noVNC route requires the
same application login, including its WebSocket connection. Include the rules
in `nginx/facebook-session.conf.example` in the host TLS server configuration.
Telegram request links do not independently grant remote desktop access.

The session supervisor waits for Xvfb, VNC and websockify readiness, removes
stale display locks after restarts, and exits if a component dies. Logs are in
`/tmp/xvfb.log`, `/tmp/x11vnc.log`, `/tmp/novnc.log` inside the session container.
The image applies `scripts/patch_novnc.py` so browser-extension errors do not
open noVNC's fatal overlay; errors in noVNC itself remain visible.

Session storage is replaced atomically. The checker requires Facebook auth
cookies and a loaded Facebook page before reporting an authenticated session.
DB work invoked from Playwright's synchronous event loop uses a separate thread.

## Deployment verification (2026-09-24 UTC)

The login repair passed 32 tests and a live authenticated refresh (request #16).
External noVNC WebSocket connection and injected extension-error handling passed.
VNC survived a repeated container restart. Browser collection adds seven tests
(39 total). On the user-supplied sample Reel, a live pilot captured 9 comment and
reply records; absence of additional visible controls is not a completeness claim.

Production backups: `/var/backups/spy/login-20260924T032400Z/`.
Docker tags `before-login-fix-20260924` retain the previous image versions;
`login-fixed-20260924` retain the version before browser research was added.
The source tree at `/var/www/spy` is not a Git checkout; deploy changed files
explicitly and retain backups. No production schema migration was required.

The first search job (Research #1) discovered 73 distinct URLs over eight queries;
its original ordinary-post extraction was incomplete. The parser was corrected
for public SVG badges and post dialogs over the home feed. The follow-up job
(Research #2) verified four public sources and captured 85 comments/replies.
One source failed due to a changing dialog locator. Two large discussions hit
the configured 40-comment limit. Two explicit PCL comment matches were a project
link and “Great job PCL!”; this is not evidence of negative spillover, nor proof
that none exists beyond the collected sample. This was the baseline before the durable queue upgrade below.


## Durable queue upgrade (2026-09-24 UTC)

Migration `research.0002` adds the corpus, comment revisions and durable thread
queue; all existing Research models remain intact. Run `python manage.py
import_facebook_evidence` once after migration to import older reports. The command
is idempotent and starts no jobs. It preserves historical job status.

Backup before deployment: `/var/backups/spy/queue-20260924T041321Z/`, containing
source and a PostgreSQL custom-format dump. Previous web/worker/beat image tags:
`spy-<service>:before-durable-queue-20260924`. Schema changes are additive: rolling
back those service images does not require dropping the corpus tables.

Next stages: independent discovery via a source watchlist, recurring monitoring,
reliable alert delivery, AI relevance/risk classification and measured recall.

Validation: 54 tests passed locally and in the production image with networking
and production database access disabled. These include a real Chromium/DB
checkpoint test, hidden-dialog removal, an unrelated icon labelled “Ещё”, empty
comment extraction, interrupted-task recovery, challenge pauses, cross-job
idempotency, text revisions, legacy import and the Continue handler.

The collector only expands “See more / Ещё” when it is actual visible button
text. An icon with the same accessible label can open unrelated link information;
that panel must not replace the discussion scope. Zero captured comments are
reported as `comments_not_observed` or `comment_links_not_matched`, rather than a
successful end-of-discussion observation. These gaps get bounded retries.

The first production cycle of Research #3 retained 124 comments from a large
petition thread over three passes (42 → 82 → 124). Continuing through the portal
