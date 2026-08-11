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
