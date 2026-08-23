import tempfile
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .forms import DownloadForm
from .models import DownloadJob
from .service import DownloadError, YouTubeDownloader
from .tasks import cleanup_expired_downloads, download_media


class DownloadFormTests(SimpleTestCase):
    def test_normalizes_youtube_url_without_scheme(self):
        form = DownloadForm({"url": "youtu.be/example", "download_type": "video", "video_quality": "720"})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["url"], "https://youtu.be/example")

    def test_rejects_non_youtube_host(self):
        form = DownloadForm({"url": "https://example.com/video", "download_type": "video", "video_quality": "720"})
        self.assertFalse(form.is_valid())
        self.assertIn("url", form.errors)

    def test_requires_quality_for_selected_type(self):
        form = DownloadForm({"url": "https://youtu.be/example", "download_type": "audio"})
        self.assertFalse(form.is_valid())
        self.assertIn("audio_quality", form.errors)


class HomeViewTests(TestCase):
    def test_home_page_loads(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "YouTube Downloader")

    @patch("downloader.views.get_video_info")
    def test_preview_error_is_shown_to_user(self, get_video_info):
        get_video_info.side_effect = DownloadError("Failed")
        response = self.client.post(reverse("home"), {
            "url": "https://youtu.be/example",
            "download_type": "video",
            "video_quality": "720",
            "action": "preview",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Failed")

    @patch("downloader.views.download_media.delay")
    def test_start_download_creates_background_job(self, delay):
        delay.return_value = SimpleNamespace(id="task-123")
        response = self.client.post(reverse("download-start"), {
            "url": "https://youtu.be/example",
            "download_type": "video",
            "video_quality": "720",
        })

        self.assertEqual(response.status_code, 202)
        job = DownloadJob.objects.get()
        self.assertEqual(job.task_id, "task-123")
        self.assertEqual(job.status, DownloadJob.Status.QUEUED)

    def test_progress_is_private_to_the_creating_session(self):
        session = self.client.session
        session.save()
        job = DownloadJob.objects.create(
            session_key=session.session_key,
            url="https://youtu.be/example",
            download_type="video",
            quality="720",
            expires_at=timezone.now() + timedelta(hours=1),
        )

        response = self.client.get(reverse("download-progress", args=[job.id]))
        self.assertEqual(response.status_code, 200)

        other_client_response = self.client_class().get(
            reverse("download-progress", args=[job.id])
        )
        self.assertEqual(other_client_response.status_code, 404)

    @override_settings(DOWNLOAD_MAX_ACTIVE_JOBS=1)
    def test_start_download_limits_active_jobs_per_session(self):
        session = self.client.session
        session.save()
        DownloadJob.objects.create(
            session_key=session.session_key,
            url="https://youtu.be/example",
            download_type="video",
            quality="720",
            expires_at=timezone.now() + timedelta(hours=1),
        )

        response = self.client.post(reverse("download-start"), {
            "url": "https://youtu.be/example",
            "download_type": "video",
            "video_quality": "720",
        })
        self.assertEqual(response.status_code, 429)

    def test_ready_file_is_returned_as_browser_attachment(self):
        with tempfile.TemporaryDirectory() as root:
            session = self.client.session
            session.save()
            job = DownloadJob.objects.create(
                session_key=session.session_key,
                url="https://youtu.be/example",
                download_type="video",
                quality="720",
                status=DownloadJob.Status.READY,
                stage="Download ready",
                progress=100,
                filename="video.mp4",
                expires_at=timezone.now() + timedelta(hours=1),
            )
            directory = Path(root) / str(job.id)
            directory.mkdir()
            filepath = directory / job.filename
            filepath.write_bytes(b"video")
            job.file_path = str(filepath)
            job.save(update_fields=["file_path"])

            with override_settings(DOWNLOAD_ROOT=root):
                response = self.client.get(reverse("download-file", args=[job.id]))
                self.assertEqual(response.status_code, 200)
                self.assertIn("attachment", response.headers["Content-Disposition"])
                response.close()


class YouTubeDownloaderTests(SimpleTestCase):
    def test_rejects_invalid_quality_before_creating_temp_directory(self):
        with patch("downloader.service.tempfile.mkdtemp") as make_temp_directory:
            with self.assertRaisesMessage(DownloadError, "Invalid download quality"):
                YouTubeDownloader("https://youtu.be/example", "video", "invalid")
        make_temp_directory.assert_not_called()


class BackgroundTaskTests(TestCase):
    def _job(self, **overrides):
        values = {
            "session_key": "test-session",
            "url": "https://youtu.be/example",
            "download_type": "video",
            "quality": "720",
            "expires_at": timezone.now() + timedelta(hours=1),
        }
        values.update(overrides)
        return DownloadJob.objects.create(**values)

    @patch("downloader.tasks.YouTubeDownloader")
    def test_download_task_marks_job_ready(self, downloader_class):
        with tempfile.TemporaryDirectory() as root:
            job = self._job()
            directory = Path(root) / str(job.id)
            directory.mkdir()
            filepath = directory / "video.mp4"
            filepath.write_bytes(b"video")
            downloader_class.return_value.download_to_path.return_value = filepath

            with override_settings(DOWNLOAD_ROOT=root):
                result = download_media.run(str(job.id))

            job.refresh_from_db()
            self.assertEqual(job.status, DownloadJob.Status.READY)
            self.assertEqual(job.progress, 100)
            self.assertEqual(result["filename"], "video.mp4")

    def test_cleanup_removes_expired_job_and_file(self):
        with tempfile.TemporaryDirectory() as root:
            job = self._job(
                status=DownloadJob.Status.READY,
                expires_at=timezone.now() - timedelta(seconds=1),
            )
            directory = Path(root) / str(job.id)
            directory.mkdir()
            filepath = directory / "video.mp4"
            filepath.write_bytes(b"video")
            job.file_path = str(filepath)
            job.filename = filepath.name
            job.save(update_fields=["file_path", "filename"])

            with override_settings(DOWNLOAD_ROOT=root):
                deleted = cleanup_expired_downloads.run()

            self.assertEqual(deleted, 1)
            self.assertFalse(DownloadJob.objects.filter(pk=job.id).exists())
            self.assertFalse(directory.exists())
