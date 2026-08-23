from django.contrib import admin

from .models import DownloadJob


@admin.register(DownloadJob)
class DownloadJobAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "download_type",
        "quality",
        "status",
        "progress",
        "created_at",
        "expires_at",
    )
    list_filter = ("status", "download_type")
    readonly_fields = ("id", "task_id", "created_at", "updated_at")
    search_fields = ("id", "url", "filename", "task_id")
