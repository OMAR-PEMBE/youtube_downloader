from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .forms import DownloadForm
from .models import DownloadJob
from .service import DownloadError, get_video_info
from .tasks import download_media


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


def home(request):
    video_info = None
    form = DownloadForm(request.POST or None)

    if request.method == "POST" and request.POST.get("action") == "preview":
        if form.is_valid():
            try:
                video_info = get_video_info(form.cleaned_data["url"])
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
    active_jobs = DownloadJob.objects.filter(
        session_key=session_key,
        status__in=[
            DownloadJob.Status.QUEUED,
            DownloadJob.Status.DOWNLOADING,
            DownloadJob.Status.PROCESSING,
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
        job.error = "The download service is unavailable. Ensure Redis and the Celery worker are running."
        job.save(update_fields=["status", "stage", "error", "updated_at"])
        return JsonResponse({"error": job.error}, status=503)

    job.task_id = result.id
    job.save(update_fields=["task_id", "updated_at"])
    return JsonResponse(
        {
            "job_id": str(job.id),
            "progress_url": reverse("download-progress", args=[job.id]),
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

    return JsonResponse(payload)


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

    return FileResponse(
        filepath.open("rb"),
        as_attachment=True,
        filename=job.filename,
    )
