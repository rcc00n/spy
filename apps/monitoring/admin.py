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
        "platform",
        "external_post_id",
        "published_at",
        "first_seen_at",
    )
    list_filter = ("platform", "first_seen_at")
    search_fields = ("external_post_id", "post_url", "text")
    readonly_fields = ("first_seen_at",)


@admin.register(PostKeywordMatch)
class PostKeywordMatchAdmin(admin.ModelAdmin):
    list_display = ("keyword", "post", "created_at", "telegram_sent")
    list_filter = ("telegram_sent", "created_at", "keyword")
    search_fields = ("keyword__phrase", "post__text", "matched_text_preview")
    readonly_fields = ("created_at",)


class CheckRunPostInline(admin.TabularInline):
    model = CheckRunPost
    extra = 0
    fields = (
        "sequence",
        "status",
        "is_new",
        "source_type",
        "post_url",
        "skip_reason",
    )
    readonly_fields = fields
    can_delete = False
    show_change_link = True


@admin.register(CheckRun)
class CheckRunAdmin(admin.ModelAdmin):
    list_display = (
        "monitored_account",
        "status",
        "started_at",
        "finished_at",
        "posts_found",
        "new_posts_found",
        "matches_found",
        "facebook_state",
        "final_url",
    )
    list_filter = ("status", "started_at", "monitored_account__platform")
    search_fields = ("monitored_account__account_name", "error_message")
    readonly_fields = (
        "started_at",
        "finished_at",
        "status",
        "error_message",
        "posts_found",
        "new_posts_found",
        "matches_found",
        "final_url",
        "route_url",
        "page_title",
        "http_status_code",
        "facebook_state",
        "article_count",
        "link_count",
        "post_link_count",
        "diagnostic_text",
        "diagnostic_html_snapshot",
        "screenshot_path",
    )
    inlines = (CheckRunPostInline,)


@admin.register(CheckRunPost)
class CheckRunPostAdmin(admin.ModelAdmin):
    list_display = (
        "check_run",
        "sequence",
        "status",
        "is_new",
        "source_type",
        "post_url",
        "observed_at",
    )
    list_filter = ("status", "is_new", "source_type", "observed_at")
