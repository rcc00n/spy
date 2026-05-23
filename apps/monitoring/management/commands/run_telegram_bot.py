import logging

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.monitoring.models import (
    CheckRun,
    FacebookSessionRefreshRequest,
    Keyword,
    PlatformCredential,
    PostKeywordMatch,
    TelegramChat,
)
from apps.monitoring.services.facebook_auth import storage_state_path
from apps.monitoring.services.facebook_session_requests import (
    create_facebook_session_request,
)
from apps.monitoring.services.runner import (
    accounts_for_check,
    format_check_summary,
    run_account_checks,
)
from apps.monitoring.services.telegram_templates import render_telegram_template


logger = logging.getLogger(__name__)

HELP_TEXT = """Commands:
/help - show this help.
/keywords - list keywords with IDs.
/addkeyword <phrase> - add or reactivate a keyword.
/pausekeyword <id|phrase> - deactivate a keyword.
/resumekeyword <id|phrase> - reactivate a keyword.
/check [account_id] [limit] - run checks now. Limit must be 1-20.
/matches [limit] - show latest keyword matches.
/runs [limit] - show latest check runs.
/session - show Facebook session status.
/refreshsession - create a human-assisted Facebook session refresh request.
"""


class BotCommandError(ValueError):
    pass


def command_tail(context) -> str:
    return " ".join(context.args or []).strip()


def parse_limit(value: str | None, *, default: int = 5, maximum: int = 20) -> int:
    if not value:
        return default
    try:
        limit = int(value)
    except ValueError as exc:
        raise BotCommandError("Limit must be a number.") from exc
    if not 1 <= limit <= maximum:
        raise BotCommandError(f"Limit must be between 1 and {maximum}.")
    return limit


def normalize_keyword_phrase(value: str) -> str:
    phrase = " ".join((value or "").split())
    if not phrase:
        raise BotCommandError("Keyword phrase is required.")
    return phrase


def keyword_label(keyword: Keyword) -> str:
    active = "active" if keyword.is_active else "paused"
    return f"#{keyword.pk} {active}: {keyword.phrase}"


def keyword_lookup(identifier: str) -> Keyword:
    normalized = normalize_keyword_phrase(identifier)
    if normalized.isdigit():
        try:
            return Keyword.objects.get(pk=int(normalized))
        except Keyword.DoesNotExist as exc:
            raise BotCommandError(f"Keyword #{normalized} was not found.") from exc

    try:
        return Keyword.objects.get(phrase__iexact=normalized)
    except Keyword.DoesNotExist as exc:
        raise BotCommandError(f"Keyword '{normalized}' was not found.") from exc


class Command(BaseCommand):
    help = "Run the Telegram bot polling loop."

    def handle(self, *args, **options):
        if not settings.TELEGRAM_BOT_TOKEN:
            raise CommandError("TELEGRAM_BOT_TOKEN is not configured.")

        try:
            from telegram import Update
            from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
        except ImportError as exc:
            raise CommandError("python-telegram-bot is not installed.") from exc

        @sync_to_async
        def render_message(key: str, default: str, context: dict | None = None) -> str:
            return render_telegram_template(key, context=context, default=default)

        async def send_text(update: Update, text: str) -> None:
            if not update.message:
                return
            text = text or ""
            for start in range(0, max(len(text), 1), 3900):
                await update.message.reply_text(text[start : start + 3900] or " ")

        async def reply_template(
            update: Update,
            key: str,
            default: str,
            context: dict | None = None,
        ) -> None:
            await send_text(update, await render_message(key, default, context))

        async def reply_error(update: Update, error: str) -> None:
            await reply_template(
                update,
                "bot_error",
                "Error: {error}",
                {"error": error},
            )

        @sync_to_async
        def register_chat(chat_id):
            TelegramChat.objects.update_or_create(
                chat_id=chat_id,
                defaults={"is_active": True},
            )

        async def ensure_chat(update: Update) -> None:
            chat = update.effective_chat
            if chat:
                await register_chat(chat.id)

        async def guarded(update: Update, handler):
            await ensure_chat(update)
            try:
                await handler()
            except BotCommandError as exc:
                await reply_error(update, str(exc))
            except Exception as exc:
                logger.exception("Telegram bot command failed")
                await reply_error(update, f"Unexpected error: {exc}")

        async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
            await ensure_chat(update)
            help_text = await render_message("bot_help", HELP_TEXT)
            await reply_template(
                update,
                "bot_start",
                "Monitoring alerts are enabled for this chat.\n\n{help_text}",
                {"help_text": help_text},
            )

        async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
            await ensure_chat(update)
            await reply_template(update, "bot_help", HELP_TEXT)

        @sync_to_async
        def list_keywords_text() -> str:
            keywords = list(Keyword.objects.order_by("phrase")[:100])
            if not keywords:
                return render_telegram_template(
                    "bot_keywords_empty",
                    default="No keywords are configured.",
                )
            rows = "\n".join(keyword_label(keyword) for keyword in keywords)
            return render_telegram_template(
                "bot_keywords",
                context={"keywords": rows, "keyword_count": len(keywords)},
                default="{keywords}",
            )

        async def keywords(update: Update, context: ContextTypes.DEFAULT_TYPE):
            async def action():
                await send_text(update, await list_keywords_text())

            await guarded(update, action)

        @sync_to_async
        def add_keyword_text(text: str) -> str:
            phrase = normalize_keyword_phrase(text)
            keyword = Keyword.objects.filter(phrase__iexact=phrase).first()
            created = False
            if not keyword:
                keyword = Keyword.objects.create(phrase=phrase, is_active=True)
                created = True
            elif not keyword.is_active:
                keyword.is_active = True
                keyword.save(update_fields=["is_active"])
            action = "added" if created else "reactivated"
            return render_telegram_template(
                "bot_keyword_saved",
                context={
                    "keyword_id": keyword.pk,
                    "keyword": keyword.phrase,
                    "action": action,
                },
                default="Keyword {action}: #{keyword_id} {keyword}",
            )

        async def addkeyword(update: Update, context: ContextTypes.DEFAULT_TYPE):
            async def action():
                await send_text(update, await add_keyword_text(command_tail(context)))

            await guarded(update, action)

        @sync_to_async
        def set_keyword_active_text(text: str, *, active: bool) -> str:
            keyword = keyword_lookup(text)
            keyword.is_active = active
            keyword.save(update_fields=["is_active"])
            return render_telegram_template(
                "bot_keyword_status_changed",
                context={
                    "keyword_id": keyword.pk,
                    "keyword": keyword.phrase,
                    "status": "active" if active else "paused",
                },
                default="Keyword #{keyword_id} {keyword} is now {status}.",
            )

        async def pausekeyword(update: Update, context: ContextTypes.DEFAULT_TYPE):
            async def action():
                await send_text(
                    update,
                    await set_keyword_active_text(command_tail(context), active=False),
                )

            await guarded(update, action)

        async def resumekeyword(update: Update, context: ContextTypes.DEFAULT_TYPE):
            async def action():
                await send_text(
                    update,
                    await set_keyword_active_text(command_tail(context), active=True),
                )

            await guarded(update, action)

        @sync_to_async
        def check_text(args) -> str:
            account_id = None
            limit = None
            if len(args) > 2:
                raise BotCommandError("Usage: /check [account_id] [limit]")
            if args:
                if args[0].lower() != "all":
                    try:
                        account_id = int(args[0])
                    except ValueError as exc:
                        raise BotCommandError("Account ID must be a number.") from exc
            if len(args) == 2:
                limit = parse_limit(args[1], default=5, maximum=20)

            accounts = accounts_for_check(force=True, account_id=account_id)
            if not accounts:
                return render_telegram_template(
                    "bot_check_no_accounts",
                    default="No active monitored accounts were found.",
                )

            summary = run_account_checks(accounts, post_limit=limit)
            return render_telegram_template(
                "bot_check_finished",
                context={
                    "summary": format_check_summary(summary),
                    "accounts_checked": summary["accounts_checked"],
                    "posts_found": summary["posts_found"],
                    "new_posts_found": summary["new_posts_found"],
                    "matches_found": summary["matches_found"],
                    "errors": summary["errors"],
                    "auth_required": summary.get("auth_required", 0),
                },
                default="Check finished: {summary}",
            )

        async def check(update: Update, context: ContextTypes.DEFAULT_TYPE):
            async def action():
                await send_text(update, await check_text(context.args))

            await guarded(update, action)

        @sync_to_async
        def matches_text(args) -> str:
            limit = parse_limit(args[0] if args else None, default=5, maximum=20)
            matches = list(
                PostKeywordMatch.objects.select_related(
                    "keyword",
                    "post",
                    "post__monitored_account",
                ).order_by("-created_at")[:limit]
            )
            if not matches:
                return render_telegram_template(
                    "bot_matches_empty",
                    default="No matches yet.",
                )

            rows = []
            for match in matches:
                account = match.post.monitored_account
                rows.append(
                    "\n".join(
                        [
                            f"#{match.pk} {match.created_at:%Y-%m-%d %H:%M}",
                            f"Account: {account.account_name}",
                            f"Keyword: {match.keyword.phrase}",
                            f"Post: {match.post.post_url or account.account_url}",
                            f"Preview: {match.matched_text_preview[:300]}",
                        ]
                    )
                )
            return render_telegram_template(
                "bot_matches",
                context={"matches": "\n\n".join(rows), "match_count": len(matches)},
                default="{matches}",
            )

        async def matches(update: Update, context: ContextTypes.DEFAULT_TYPE):
            async def action():
                await send_text(update, await matches_text(context.args))

            await guarded(update, action)

        @sync_to_async
        def runs_text(args) -> str:
            limit = parse_limit(args[0] if args else None, default=5, maximum=20)
            runs = list(
                CheckRun.objects.select_related("monitored_account").order_by("-started_at")[
                    :limit
                ]
            )
            if not runs:
                return render_telegram_template(
                    "bot_runs_empty",
                    default="No check runs yet.",
                )
            rows = []
            for run in runs:
                rows.append(
                    "\n".join(
                        [
                            f"#{run.pk} {run.started_at:%Y-%m-%d %H:%M} {run.status}",
                            f"Account: {run.monitored_account.account_name}",
                            f"Posts: {run.posts_found}, new: {run.new_posts_found}, matches: {run.matches_found}",
                            f"State: {run.facebook_state or 'n/a'}",
                            f"Error: {(run.error_message or '')[:180] or 'none'}",
                        ]
                    )
                )
            return render_telegram_template(
                "bot_runs",
                context={"runs": "\n\n".join(rows), "run_count": len(runs)},
                default="{runs}",
            )

        async def runs(update: Update, context: ContextTypes.DEFAULT_TYPE):
            async def action():
                await send_text(update, await runs_text(context.args))

            await guarded(update, action)

        @sync_to_async
        def session_text() -> str:
            credential = PlatformCredential.objects.filter(
                platform=PlatformCredential.Platform.FACEBOOK,
                is_active=True,
            ).first()
            requests = list(FacebookSessionRefreshRequest.objects.order_by("-requested_at")[:5])
            request_lines = []
            for request in requests:
                request_lines.append(
                    f"#{request.pk} {request.status} requested={request.requested_at:%Y-%m-%d %H:%M}"
                )
            if not request_lines:
                request_lines.append("No session refresh requests yet.")
            return render_telegram_template(
                "bot_session",
                context={
                    "session_status": "present" if storage_state_path().exists() else "missing",
                    "credential_status": "active" if credential else "missing",
                    "credential_username": credential.username if credential else "",
                    "last_error": credential.last_error if credential else "",
                    "recent_requests": "\n".join(request_lines),
                },
                default=(
                    "Saved session: {session_status}\n"
                    "Credential: {credential_status} {credential_username}\n"
                    "Last error: {last_error}\n"
                    "Recent requests:\n{recent_requests}"
                ),
            )

        async def session(update: Update, context: ContextTypes.DEFAULT_TYPE):
            async def action():
                await send_text(update, await session_text())

            await guarded(update, action)

        @sync_to_async
        def refresh_session_text() -> str:
            request, _raw_token = create_facebook_session_request()
            return render_telegram_template(
                "bot_session_request_created",
                context={
                    "request_id": request.pk,
                    "operator_url": request.operator_url,
                    "expires_at": f"{request.expires_at:%Y-%m-%d %H:%M:%S %Z}",
                    "token_hint": request.token_hint,
                },
                default=(
                    "Facebook session refresh request created: #{request_id}\n"
                    "Expires: {expires_at}\n"
