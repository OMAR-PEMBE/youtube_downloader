import shutil
import time
from datetime import timedelta
from pathlib import Path

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from .models import DownloadJob
from .service import DownloadCancelled, DownloadError, YouTubeDownloader


def _safe_job_directory(job_id):
    root = Path(settings.DOWNLOAD_ROOT).resolve()
    directory = (root / str(job_id)).resolve()
    if root not in directory.parents:
        raise ValueError("Invalid download directory")
    return directory


@shared_task(bind=True, name="downloader.tasks.download_media")
def download_media(self, job_id):
    job = DownloadJob.objects.get(pk=job_id)
    directory = _safe_job_directory(job.id)
    directory.mkdir(parents=True, exist_ok=True)
    last_update = 0.0

    if job.status in {DownloadJob.Status.CANCELLING, DownloadJob.Status.CANCELLED}:
        DownloadJob.objects.filter(pk=job.id).update(
            status=DownloadJob.Status.CANCELLED,
            stage="Download cancelled",
            url="",
            error="",
            updated_at=timezone.now(),
        )
        shutil.rmtree(directory, ignore_errors=True)
        return {"job_id": str(job.id), "cancelled": True}

    url = job.url
    DownloadJob.objects.filter(pk=job.id).update(url="")
    job.url = ""

    def report_progress(phase, data):
        nonlocal last_update
        now = time.monotonic()
        status = data.get("status")

        current_status = DownloadJob.objects.filter(pk=job.id).values_list(
            "status", flat=True
        ).first()
        if current_status in {DownloadJob.Status.CANCELLING, DownloadJob.Status.CANCELLED}:
            raise DownloadCancelled("Download cancelled by the user.")

        if phase == "processing":
            job_status = DownloadJob.Status.PROCESSING
            stage = "Processing and merging media"
            percent = 95 if status == "started" else 99
        else:
            job_status = DownloadJob.Status.DOWNLOADING
            stage = "Downloading media"
            downloaded = int(data.get("downloaded_bytes") or 0)
            total = int(data.get("total_bytes") or data.get("total_bytes_estimate") or 0)
            calculated = min(94, int(downloaded * 94 / total)) if total else job.progress
            percent = max(job.progress, calculated)

        if now - last_update < 0.5 and status not in {"finished", "error"}:
            return

        downloaded = int(data.get("downloaded_bytes") or job.downloaded_bytes or 0)
        total = int(data.get("total_bytes") or data.get("total_bytes_estimate") or job.total_bytes or 0)
        speed = data.get("speed")
        eta = data.get("eta")

        DownloadJob.objects.filter(pk=job.id).update(
            status=job_status,
            stage=stage,
            progress=percent,
            downloaded_bytes=downloaded,
            total_bytes=total or None,
            speed=float(speed) if speed else None,
            eta=max(0, int(eta)) if eta is not None else None,
            updated_at=timezone.now(),
        )
        job.status = job_status
        job.stage = stage
        job.progress = percent
        job.downloaded_bytes = downloaded
        job.total_bytes = total or None
        last_update = now

        try:
            self.update_state(
                state="PROGRESS",
                meta={"progress": percent, "stage": stage},
            )
        except Exception:
            pass

    DownloadJob.objects.filter(pk=job.id).update(
        status=DownloadJob.Status.DOWNLOADING,
        stage="Starting download",
        progress=1,
        error="",
    )

    try:
        downloader = YouTubeDownloader(
            url,
            job.download_type,
            job.quality,
            output_directory=directory,
            progress_callback=report_progress,
            max_duration=settings.DOWNLOAD_MAX_DURATION_SECONDS,
            max_file_size=settings.DOWNLOAD_MAX_FILE_SIZE_BYTES,
        )
        filepath = downloader.download_to_path()
        expires_at = timezone.now() + timedelta(seconds=settings.DOWNLOAD_JOB_TTL_SECONDS)
        DownloadJob.objects.filter(pk=job.id).update(
            status=DownloadJob.Status.READY,
            stage="Download ready",
            progress=100,
            filename=filepath.name,
            file_path=str(filepath),
            error="",
            eta=None,
            expires_at=expires_at,
            updated_at=timezone.now(),
        )
        return {"job_id": str(job.id), "filename": filepath.name}
    except DownloadCancelled:
        shutil.rmtree(directory, ignore_errors=True)
        DownloadJob.objects.filter(pk=job.id).update(
            status=DownloadJob.Status.CANCELLED,
            stage="Download cancelled",
            progress=0,
            url="",
            error="",
            eta=None,
            updated_at=timezone.now(),
        )
        return {"job_id": str(job.id), "cancelled": True}
    except Exception as error:
        shutil.rmtree(directory, ignore_errors=True)
        message = str(error) if isinstance(error, DownloadError) else "The download could not be completed."
        DownloadJob.objects.filter(pk=job.id).update(
            status=DownloadJob.Status.FAILED,
            stage="Download failed",
            url="",
            error=message[:1000],
            eta=None,
            updated_at=timezone.now(),
        )
        raise


@shared_task(name="downloader.tasks.cleanup_expired_downloads")
def cleanup_expired_downloads():
    stale_before = timezone.now() - timedelta(seconds=settings.DOWNLOAD_STALE_JOB_SECONDS)
    stale_jobs = DownloadJob.objects.filter(
        status__in=[
            DownloadJob.Status.DOWNLOADING,
            DownloadJob.Status.PROCESSING,
            DownloadJob.Status.CANCELLING,
        ],
        updated_at__lt=stale_before,
    )
    deleted = 0

    for job in stale_jobs.iterator():
        directory = _safe_job_directory(job.id)
        shutil.rmtree(directory, ignore_errors=True)
        job.delete()
        deleted += 1

    expired_jobs = DownloadJob.objects.filter(
        expires_at__lte=timezone.now()
    ).exclude(
        status__in=[
            DownloadJob.Status.DOWNLOADING,
            DownloadJob.Status.PROCESSING,
            DownloadJob.Status.CANCELLING,
        ]
    )
    for job in expired_jobs.iterator():
        directory = _safe_job_directory(job.id)
        shutil.rmtree(directory, ignore_errors=True)
        job.delete()
        deleted += 1

    return deleted
