from django.contrib import admin

from apps.research.models import ResearchEvent, ResearchJob, ResearchSource


class ResearchEventInline(admin.TabularInline):
    model = ResearchEvent
    extra = 0
    readonly_fields = ["level", "message", "payload", "created_at"]
    can_delete = False


class ResearchSourceInline(admin.TabularInline):
    model = ResearchSource
    extra = 0
    readonly_fields = ["source_type", "title", "url", "excerpt", "payload", "created_at"]
    can_delete = False


@admin.register(ResearchJob)
class ResearchJobAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "display_title",
