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
