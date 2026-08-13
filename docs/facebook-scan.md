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
handler preserved all three sources and 127 comments in the job, without repeating
discovery or its already sampled PCL discussion. This verifies increasing depth
and retention; it is not a claim to have read the entire petition discussion.

After the parser correction and Continue, the live job reached 164 petition
comments and 82 comments on the previously failing Oilers post, plus 3 on the PCL
post (249 unique comments in Research #3 at that checkpoint). Collection continues
in the background within its bounded retry cycle. Production health, detail/status
views and all service containers were checked successfully after deployment.

## Source discovery upgrade (2026-09-24 UTC)

Operators manage the watchlist at `/research/facebook/sources/`: pages, public
groups and search phrases can be added, edited or disabled. New scans snapshot
selected source settings; later edits affect future scans only. Up to 30 sources
plus 12 manual phrases/page/group/post URLs can be selected. Empty requests and
non-Facebook URLs are rejected. Existing browser scans keep their original engine.

`FacebookDiscoveryRun` records each independent discovery attempt and its post
links. A page feed and a search can find the same post: both origins are retained,
while only one discussion work item is queued per job. Partial discovery commits
survive a browser error. Continue retries unfinished discoveries; successful
source passes are not repeated. Blank output is a visible gap, not proof that a
source contains no discussions. Selected feeds collect broadly; relevance and
sentiment classification remain a later stage.

Feed discovery accepts source-owned post URLs and generic Reel links with an
owner link in the same post card. Unrelated recommendations are excluded. Group
public visibility is verified in the header outside user posts/feed content before
discovery and again before each group post read. The collector never joins groups.
Page/post visibility and access-state checks from the previous collector remain.
Discovery uses bounded scroll/link budgets; feed order can be ranked or pinned.
Dates/backfill and full historical coverage are not provided by this stage.

`python manage.py seed_facebook_sources` adds the initial PCL/Oilers watchlist:
5 pages, 3 public fan groups and 8 search variants. It is idempotent, does not
re-enable existing disabled entries and does not start scans. Seed pages and group
headers were checked in the production browser on 2026-09-24. Old unavailable
OilersNation/EO7 URLs were not included. Enabled does not mean continuously watched:
recurring monitoring is the next stage.

Productive tasks publish the next queue step with a 5-second delay after releasing
the browser lock. The existing 30-second dispatcher recovers missed publications
and expired leases. One browser collection task still holds the global lock at a
time. This accelerates a submitted scan; it does not schedule new recurring scans.

Migration: `research.0003`. Backup: `/var/backups/spy/discovery-20260924T044829Z/`.
Previous service images: `spy-<service>:before-discovery-20260924` for web, worker
and beat. The schema additions are compatible with rollback to the prior images;
do not drop corpus or discovery tables to roll back application code.

Stage 2 validation: 69 tests passed in the final production image with networking
and production database access disabled. The live watchlist form renders 16
selected sources; an actual Chromium check confirmed each checkbox changes
independently. The watchlist, scan detail and health endpoint returned HTTP 200.

Research #4 (`/research/4/`) completed all 16 discovery passes: 5 pages, 3 public
groups and 8 searches. It retained 78 distinct canonical post links with their
origins. The quick discovery limit is 5 links per source; this is a bounded pilot,
not a census of those feeds. Subsequent public-post reading continues through the
durable queue. Research #5 completed a separate group-post check and stored 15
comments/replies after fresh verification of the group's public header.

Next step (3): recurring monitoring of new posts and active comment threads,
plus controlled continuation/backfill of older discussions. Steps 4–6 remain:
reliable alert delivery, AI relevance/risk classification, and measured coverage.

## Recurring monitor (stage 3, 2026-09-24 UTC)

`/research/facebook/monitor/` controls one site-wide schedule (migration 0004).
The default schedule finds posts every 6 hours using all enabled sources, checks
fresh comments in batches of 12 discussions every 2 hours, and continues batches
of 4 unfinished discussions every 12 hours. Discovery uses the standard source
budget (10 links, 4 scrolls per source) then saves post captions. Refresh prefers
Newest, falling back to All comments when necessary; the actual sort is retained
in the evidence. It uses 40 visible comments / 45 seconds of comment expansion.
Continuation seeds prior saved comments and attempt depth, then replays with a
larger budget (up to 150 seconds of comment expansion). Refresh and continuation
perform one attempt per thread per cycle; any limits remain explicit gaps.

Refresh rotates through all known, previously verified public posts, including
manual scans. Continuation rotates through known unfinished threads, including
unread links and failed attempts. A successful deep-read sample is not reset by a
later caption or refresh pass. Threads with active manual work are skipped by
continuation. Observation order, not publication dates, drives rotation. This is
continuation of the known corpus, not historical discovery by publication date.

Intervals are queue targets, not per-post guarantees: 120 verified discussions
with a refresh batch of 12 require at least 10 cycles to rotate once. The UI exposes
batch sizes and intervals. Changes reset due times but do not rewrite the active
cycle. Source changes affect the next discovery cycle. No keyword relevance
filter or sentiment classifier is applied at this stage.

The existing 30-second dispatcher locks the singleton schedule in PostgreSQL and
creates at most one unfinished scheduled cycle at a time, choosing the oldest due
lane. Missed intervals are coalesced, never expanded into a catch-up backlog. Jobs
and post rotation timestamps commit together before broker publication. Broker
loss, worker restart and interrupted browser work retain their durable recovery
paths; the global Redis lease still allows only one collector browser at a time.

Pause holds scheduled queue work, including already-published tasks, at the next
browser-step boundary; an in-flight step can finish saving its checkpoint. Resume
retains that cycle and evidence. Manual scans are separate. Access challenges or
rate limits pause the monitor and stop active Facebook jobs; unexpected scheduled
collector failures also pause the monitor. Nothing automatically re-enables it.
Controls require staff authorization and CSRF-protected POST. Scheduled cycles
cannot be restarted via the manual-job retry endpoint. No Telegram notifications
are introduced by stage 3.

For rollback, first pause the monitor and let the in-flight browser step finish.
Stop the worker and beat before restoring the previous service images. Old code
has no monitor gate, so also leave any unfinished scheduled jobs cancelled when
rolling back; do not remove corpus/schema tables or saved evidence.

Next (stage 4): durable alert delivery with deduplication and failure notices.
Then stage 5 adds relevance and PCL-risk classification, and stage 6 adds coverage
metrics and gap controls. None can guarantee 100% coverage of Facebook.

Stage 3 deployment validation: 83 tests passed in the final production image
(`ff1623d7fbefd8cfc2e672315421cad4a57470aadc81b6e4db0e91a2fc1aed6f`),
