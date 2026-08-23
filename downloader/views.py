import shutil
from datetime import timedelta
from pathlib import Path

from celery import current_app
from django.conf import settings
from django.core.cache import cache
from django.http import FileResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .forms import DownloadForm
from .models import DownloadJob
from .service import DownloadError, get_video_info
from .tasks import download_media


class DeleteJobOnClose:
    def __init__(self, file_object, job_id, directory):
        self.file_object = file_object
        self.job_id = job_id
        self.directory = directory
        self.closed = False

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.file_object.close()
        finally:
            shutil.rmtree(self.directory, ignore_errors=True)
            try:
                DownloadJob.objects.filter(pk=self.job_id).delete()
            except Exception:
                # The response has already been delivered. The scheduled cleanup
                # remains a fallback if the database is temporarily unavailable.
                pass

    def __getattr__(self, name):
        return getattr(self.file_object, name)


def _session_key(request):
    if not request.session.session_key:
        request.session.create()
    return request.session.session_key


def _owned_job(request, job_id):
    return get_object_or_404(
        DownloadJob,
        pk=job_id,
        session_key=_session_key(request),
    )


def _within_rate_limit(session_key):
    key = f"download-rate:{session_key}"
    if cache.add(key, 1, timeout=settings.DOWNLOAD_RATE_LIMIT_WINDOW_SECONDS):
        return True

    try:
        attempts = cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=settings.DOWNLOAD_RATE_LIMIT_WINDOW_SECONDS)
        attempts = 1

    return attempts <= settings.DOWNLOAD_RATE_LIMIT_COUNT


def home(request):
    video_info = None
    form = DownloadForm(request.POST or None)

    if request.method == "POST" and request.POST.get("action") == "preview":
        if form.is_valid():
            try:
                video_info = get_video_info(
                    form.cleaned_data["url"],
                    max_duration=settings.DOWNLOAD_MAX_DURATION_SECONDS,
                )
            except DownloadError as error:
                form.add_error(None, str(error))

    return render(
        request,
        "downloader/index.html",
        {"form": form, "video_info": video_info},
    )


@require_POST
def start_download(request):
    form = DownloadForm(request.POST)
    if not form.is_valid():
        return JsonResponse(
            {
                "error": "Please correct the form and try again.",
                "fields": form.errors.get_json_data(),
            },
            status=400,
        )

    session_key = _session_key(request)
    if not _within_rate_limit(session_key):
        return JsonResponse(
            {"error": "Download limit reached. Please try again later."},
            status=429,
        )

    active_jobs = DownloadJob.objects.filter(
        session_key=session_key,
        status__in=[
            DownloadJob.Status.QUEUED,
            DownloadJob.Status.DOWNLOADING,
            DownloadJob.Status.PROCESSING,
            DownloadJob.Status.CANCELLING,
        ],
    ).count()
    if active_jobs >= settings.DOWNLOAD_MAX_ACTIVE_JOBS:
        return JsonResponse(
            {"error": "Too many active downloads. Wait for a current job to finish."},
            status=429,
        )

    download_type = form.cleaned_data["download_type"]
    quality = form.cleaned_data[
        "video_quality" if download_type == "video" else "audio_quality"
    ]
    job = DownloadJob.objects.create(
        session_key=session_key,
        url=form.cleaned_data["url"],
        download_type=download_type,
        quality=quality,
        expires_at=timezone.now() + timedelta(seconds=settings.DOWNLOAD_JOB_TTL_SECONDS),
    )

    try:
        result = download_media.delay(str(job.id))
    except Exception:
        job.status = DownloadJob.Status.FAILED
        job.stage = "Background worker unavailable"
        job.url = ""
        job.error = "The download service is unavailable. Ensure Redis and the Celery worker are running."
        job.save(update_fields=["status", "stage", "url", "error", "updated_at"])
        return JsonResponse({"error": job.error}, status=503)

    job.task_id = result.id
    job.save(update_fields=["task_id", "updated_at"])
    return JsonResponse(
        {
            "job_id": str(job.id),
            "progress_url": reverse("download-progress", args=[job.id]),
            "cancel_url": reverse("download-cancel", args=[job.id]),
        },
        status=202,
    )


@require_GET
def download_progress(request, job_id):
    job = _owned_job(request, job_id)
    payload = {
        "job_id": str(job.id),
        "status": job.status,
        "stage": job.stage,
        "progress": job.progress,
        "downloaded_bytes": job.downloaded_bytes,
        "total_bytes": job.total_bytes,
        "speed": job.speed,
        "eta": job.eta,
        "error": job.error,
    }

    if job.status == DownloadJob.Status.READY and job.file_exists:
        payload["download_url"] = reverse("download-file", args=[job.id])
    elif job.status == DownloadJob.Status.READY:
        payload.update(
            status=DownloadJob.Status.FAILED,
            stage="Download file is missing",
            error="The finished file is no longer available.",
        )

    if job.status in {
        DownloadJob.Status.QUEUED,
        DownloadJob.Status.DOWNLOADING,
        DownloadJob.Status.PROCESSING,
        DownloadJob.Status.CANCELLING,
    }:
        payload["cancel_url"] = reverse("download-cancel", args=[job.id])

    return JsonResponse(payload)


@require_POST
def cancel_download(request, job_id):
    job = _owned_job(request, job_id)

    if job.status in {
        DownloadJob.Status.READY,
        DownloadJob.Status.FAILED,
        DownloadJob.Status.CANCELLED,
        DownloadJob.Status.EXPIRED,
    }:
        return JsonResponse(
            {"status": job.status, "stage": job.stage},
            status=409,
        )

    if job.task_id:
        try:
            current_app.control.revoke(job.task_id, terminate=False)
        except Exception:
            pass

    if job.status == DownloadJob.Status.QUEUED:
        job.status = DownloadJob.Status.CANCELLED
        job.stage = "Download cancelled"
        directory = (Path(settings.DOWNLOAD_ROOT).resolve() / str(job.id)).resolve()
        shutil.rmtree(directory, ignore_errors=True)
    else:
        job.status = DownloadJob.Status.CANCELLING
        job.stage = "Stopping download safely"

    job.url = ""
    job.error = ""
    job.save(update_fields=["status", "stage", "url", "error", "updated_at"])
    return JsonResponse({"status": job.status, "stage": job.stage}, status=202)


@require_GET
def download_file(request, job_id):
    job = _owned_job(request, job_id)
    if job.status != DownloadJob.Status.READY or not job.file_exists:
        return JsonResponse({"error": "The file is not ready or has expired."}, status=404)

    root = Path(settings.DOWNLOAD_ROOT).resolve()
    filepath = Path(job.file_path).resolve()
    expected_directory = (root / str(job.id)).resolve()
    if expected_directory not in filepath.parents:
        return JsonResponse({"error": "Invalid download file."}, status=404)

    wrapped_file = DeleteJobOnClose(
        filepath.open("rb"),
        job.id,
        expected_directory,
    )
    return FileResponse(
        wrapped_file,
        as_attachment=True,
        filename=job.filename,
    )
