import uuid
from pathlib import Path

from django.db import models


class DownloadJob(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        DOWNLOADING = "downloading", "Downloading"
        PROCESSING = "processing", "Processing"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"
        EXPIRED = "expired", "Expired"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session_key = models.CharField(max_length=40, db_index=True)
    task_id = models.CharField(max_length=255, blank=True)
    url = models.URLField(max_length=500)
    download_type = models.CharField(max_length=10)
    quality = models.CharField(max_length=10)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.QUEUED,
        db_index=True,
    )
    stage = models.CharField(max_length=100, default="Waiting for a worker")
    progress = models.PositiveSmallIntegerField(default=0)
    downloaded_bytes = models.PositiveBigIntegerField(default=0)
    total_bytes = models.PositiveBigIntegerField(null=True, blank=True)
    speed = models.FloatField(null=True, blank=True)
    eta = models.PositiveIntegerField(null=True, blank=True)
    filename = models.CharField(max_length=255, blank=True)
    file_path = models.TextField(blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField(db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.id} ({self.status})"

    @property
    def file_exists(self):
        return bool(self.file_path and Path(self.file_path).is_file())
