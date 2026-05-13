from django.contrib import admin

from apps.monitoring.forms import PlatformCredentialForm

from .models import (
    CheckRun,
    CheckRunPost,
    FacebookSessionRefreshRequest,
    Keyword,
    ManualCheckJob,
    MonitoredAccount,
    PlatformCredential,
    Post,
    PostKeywordMatch,
    TelegramChat,
    TelegramMessageTemplate,
)


@admin.register(MonitoredAccount)
class MonitoredAccountAdmin(admin.ModelAdmin):
    list_display = (
        "account_name",
        "platform",
        "is_active",
        "check_interval_minutes",
        "max_posts_per_check",
        "scroll_rounds",
        "last_checked_at",
        "last_status",
    )
    list_filter = ("platform", "is_active", "last_status")
    search_fields = ("account_name", "account_url")
    readonly_fields = ("last_checked_at", "last_status", "last_error", "created_at", "updated_at")


@admin.register(Keyword)
class KeywordAdmin(admin.ModelAdmin):
    list_display = ("phrase", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("phrase",)
    readonly_fields = ("created_at",)


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = (
        "monitored_account",
