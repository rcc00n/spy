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

