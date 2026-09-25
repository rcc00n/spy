"""Run synchronous ORM work outside Playwright's running event loop."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from django.db import close_old_connections


def call_sync_db(callback, *args, **kwargs):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return callback(*args, **kwargs)

    def run():
        close_old_connections()
        try:
            return callback(*args, **kwargs)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(run).result()
