import uuid
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone


class AdvertisementQuerySet(models.QuerySet):
    def active(self):
        now = timezone.now()
        return self.filter(is_active=True).filter(
            Q(starts_at__isnull=True) | Q(starts_at__lte=now),
            Q(ends_at__isnull=True) | Q(ends_at__gt=now),
        )


class Advertisement(models.Model):
    class Placement(models.TextChoices):
        PAGE_TOP = "page_top", "Top of page"
        ABOVE_FORM = "above_form", "Above download form"
        BELOW_FORM = "below_form", "Below download form"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, help_text="Internal campaign name")
    placement = models.CharField(max_length=20, choices=Placement.choices, db_index=True)
    headline = models.CharField(max_length=120)
    description = models.CharField(max_length=280, blank=True)
    image_url = models.URLField(
        max_length=1000,
        blank=True,
        validators=[URLValidator(schemes=("http", "https"))],
    )
    destination_url = models.URLField(
        max_length=1000,
        validators=[URLValidator(schemes=("http", "https"))],
    )
    disclosure = models.CharField(max_length=80, default="Sponsored / Affiliate link")
    is_active = models.BooleanField(default=False, db_index=True)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    priority = models.PositiveSmallIntegerField(default=0)
    impressions = models.PositiveBigIntegerField(default=0, editable=False)
    clicks = models.PositiveBigIntegerField(default=0, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = AdvertisementQuerySet.as_manager()

    class Meta:
        ordering = ["-priority", "-created_at"]

    def clean(self):
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValidationError({"ends_at": "End time must be later than start time."})

    def __str__(self):
        return self.name


class DownloadJob(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        DOWNLOADING = "downloading", "Downloading"
        PROCESSING = "processing", "Processing"
        CANCELLING = "cancelling", "Cancelling"
        CANCELLED = "cancelled", "Cancelled"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"
        EXPIRED = "expired", "Expired"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session_key = models.CharField(max_length=40, db_index=True)
    task_id = models.CharField(max_length=255, blank=True)
    url = models.URLField(max_length=500, blank=True)
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
