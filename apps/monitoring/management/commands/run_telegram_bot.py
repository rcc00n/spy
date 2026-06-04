from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from asgiref.sync import sync_to_async

from apps.monitoring.models import (
    FacebookSessionRefreshRequest,
    Keyword,
    MonitoredAccount,
    PlatformCredential,
    PostKeywordMatch,
    TelegramChat,
)
from apps.monitoring.services.facebook_auth import storage_state_path


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
        def register_chat(chat_id):
            TelegramChat.objects.update_or_create(
                chat_id=chat_id,
                defaults={"is_active": True},
            )

        @sync_to_async
        def list_accounts():
            return list(
                MonitoredAccount.objects.filter(is_active=True).order_by(
                    "platform",
                    "account_name",
                )[:20]
            )

        @sync_to_async
        def list_keywords():
            return list(Keyword.objects.filter(is_active=True).order_by("phrase")[:50])

        @sync_to_async
        def list_matches():
            return list(
                PostKeywordMatch.objects.select_related(
                    "keyword",
                    "post",
                    "post__monitored_account",
                ).order_by("-created_at")[:5]
            )

        @sync_to_async
        def facebook_session_status():
            credential = PlatformCredential.objects.filter(
                platform=PlatformCredential.Platform.FACEBOOK,
                is_active=True,
            ).first()
            requests = list(FacebookSessionRefreshRequest.objects.order_by("-requested_at")[:5])
            return credential, storage_state_path().exists(), requests

        async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
            chat = update.effective_chat
            await register_chat(chat.id)
            await update.message.reply_text(
                "Monitoring alerts are enabled for this chat. Use /accounts, "
                "/keywords, and /matches to inspect the current configuration."
            )

        async def accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
            rows = await list_accounts()
            if not rows:
                await update.message.reply_text("No active monitored accounts.")
                return
            text = "\n".join(
                f"- {row.get_platform_display()}: {row.account_name}" for row in rows
            )
            await update.message.reply_text(text)

        async def keywords(update: Update, context: ContextTypes.DEFAULT_TYPE):
            rows = await list_keywords()
            if not rows:
                await update.message.reply_text("No active keywords.")
                return
            await update.message.reply_text("\n".join(f"- {row.phrase}" for row in rows))

        async def matches(update: Update, context: ContextTypes.DEFAULT_TYPE):
            rows = await list_matches()
            if not rows:
                await update.message.reply_text("No matches yet.")
                return
            lines = []
            for match in rows:
                account = match.post.monitored_account
                lines.append(
                    f"Platform: {account.get_platform_display()}\n"
                    f"Account: {account.account_name}\n"
                    f"Keyword: {match.keyword.phrase}\n"
                    f"Post: {match.post.post_url or account.account_url}\n"
                    f"Preview: {match.matched_text_preview[:300]}"
                )
            await update.message.reply_text("\n\n".join(lines))

        async def session(update: Update, context: ContextTypes.DEFAULT_TYPE):
            credential, session_exists, requests = await facebook_session_status()
            lines = [
                f"Saved session: {'present' if session_exists else 'missing'}",
                f"Credential: {'active' if credential else 'missing'}",
            ]
            if credential and credential.last_error:
                lines.append(f"Last error: {credential.last_error[:300]}")
            if requests:
                lines.append("Recent requests:")
                for request in requests:
                    lines.append(
                        f"#{request.pk} {request.status} requested={request.requested_at:%Y-%m-%d %H:%M}"
                    )
            await update.message.reply_text("\n".join(lines))

        async def placeholder(update: Update, context: ContextTypes.DEFAULT_TYPE):
            await update.message.reply_text(
                "Management commands are placeholders for the MVP. Use Django admin "
                "to add or remove accounts and keywords."
            )

        app = ApplicationBuilder().token(settings.TELEGRAM_BOT_TOKEN).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("accounts", accounts))
        app.add_handler(CommandHandler("keywords", keywords))
        app.add_handler(CommandHandler("matches", matches))
        app.add_handler(CommandHandler("session", session))
        app.add_handler(CommandHandler("addaccount", placeholder))
        app.add_handler(CommandHandler("removeaccount", placeholder))
        app.add_handler(CommandHandler("addkeyword", placeholder))
        app.add_handler(CommandHandler("removekeyword", placeholder))

        self.stdout.write("Telegram bot polling started.")
        app.run_polling()
