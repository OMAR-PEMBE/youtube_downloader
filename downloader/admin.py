import shutil
from pathlib import Path

from celery import current_app
from django.conf import settings
from django.contrib import admin

from .models import Advertisement, DownloadJob


@admin.register(Advertisement)
class AdvertisementAdmin(admin.ModelAdmin):
    list_display = (
        "name", "placement", "is_active", "priority", "starts_at", "ends_at",
        "impressions", "clicks",
    )
    list_filter = ("is_active", "placement")
    search_fields = ("name", "headline")
    list_editable = ("is_active", "priority")
    readonly_fields = ("id", "impressions", "clicks", "created_at", "updated_at")
    fieldsets = (
        ("Campaign", {"fields": ("id", "name", "placement", "is_active", "priority")}),
        ("Creative", {"fields": ("headline", "description", "image_url", "destination_url", "disclosure")}),
        ("Schedule", {"fields": ("starts_at", "ends_at")}),
        ("Aggregate performance", {"fields": ("impressions", "clicks", "created_at", "updated_at")}),
    )


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
    readonly_fields = (
        "id", "task_id", "download_type", "quality", "status", "stage",
        "progress", "downloaded_bytes", "total_bytes", "speed", "eta",
        "filename", "error", "created_at", "updated_at", "expires_at",
    )
    exclude = ("session_key", "url", "file_path")
    search_fields = ("id", "filename", "task_id")
    actions = ("cancel_selected_jobs",)

    def has_add_permission(self, request):
        return False

    @admin.action(description="Cancel selected active downloads")
    def cancel_selected_jobs(self, request, queryset):
        active_statuses = {
            DownloadJob.Status.QUEUED,
            DownloadJob.Status.DOWNLOADING,
            DownloadJob.Status.PROCESSING,
            DownloadJob.Status.CANCELLING,
        }
        cancelled = 0
        for job in queryset.filter(status__in=active_statuses):
            if job.task_id:
                try:
                    current_app.control.revoke(job.task_id, terminate=False)
                except Exception:
                    pass
            if job.status == DownloadJob.Status.QUEUED:
                job.status = DownloadJob.Status.CANCELLED
                job.stage = "Download cancelled by administrator"
                shutil.rmtree(Path(settings.DOWNLOAD_ROOT) / str(job.id), ignore_errors=True)
            else:
                job.status = DownloadJob.Status.CANCELLING
                job.stage = "Stopping download safely"
            job.url = ""
            job.save(update_fields=("status", "stage", "url", "updated_at"))
            cancelled += 1
        self.message_user(request, f"Requested cancellation for {cancelled} download(s).")
