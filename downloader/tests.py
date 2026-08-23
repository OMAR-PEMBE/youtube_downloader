import os
import tempfile
from datetime import timedelta
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .forms import DownloadForm
from .models import Advertisement, DownloadJob
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
    def _advertisement(self, **overrides):
        values = {
            "name": "Test campaign",
            "placement": Advertisement.Placement.ABOVE_FORM,
            "headline": "Recommended service",
            "description": "A useful partner offer.",
            "destination_url": "https://example.com/offer",
            "is_active": True,
        }
        values.update(overrides)
        return Advertisement.objects.create(**values)

    def test_liveness_endpoint(self):
        response = self.client.get(reverse("health-live"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_readiness_endpoint(self):
        response = self.client.get(reverse("health-ready"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    @patch("downloader.views.cache.get", side_effect=RuntimeError("Redis unavailable"))
    def test_readiness_reports_dependency_failure(self, cache_get):
        response = self.client.get(reverse("health-ready"))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "unavailable"})

    def test_home_page_loads(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "YouTube Downloader")

    def test_active_advertisement_is_rendered_and_counts_impression(self):
        advertisement = self._advertisement()

        response = self.client.get(reverse("home"))

        self.assertContains(response, "Recommended service")
        self.assertContains(response, "Sponsored / Affiliate link")
        advertisement.refresh_from_db()
        self.assertEqual(advertisement.impressions, 1)

    def test_inactive_and_expired_advertisements_are_hidden(self):
        self._advertisement(name="Inactive", headline="Hidden inactive", is_active=False)
        self._advertisement(
            name="Expired",
            headline="Hidden expired",
            ends_at=timezone.now() - timedelta(seconds=1),
        )

        response = self.client.get(reverse("home"))

        self.assertNotContains(response, "Hidden inactive")
        self.assertNotContains(response, "Hidden expired")

    def test_advertisement_click_redirects_and_counts_aggregate_click(self):
        advertisement = self._advertisement()

        response = self.client.get(reverse("advertisement-click", args=[advertisement.id]))

        self.assertRedirects(
            response,
            "https://example.com/offer",
            fetch_redirect_response=False,
        )
        advertisement.refresh_from_db()
        self.assertEqual(advertisement.clicks, 1)

    def test_advertisement_content_is_escaped(self):
        self._advertisement(headline='<script>alert("x")</script>')

        response = self.client.get(reverse("home"))

        self.assertNotContains(response, '<script>alert("x")</script>', html=False)
        self.assertContains(response, "&lt;script&gt;")


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
        self.assertIn("cancel_url", response.json())

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
                for closer in response._resource_closers:
                    closer()
                response._resource_closers.clear()

            self.assertFalse(DownloadJob.objects.filter(pk=job.id).exists())
            self.assertFalse(directory.exists())

    @patch("downloader.views.current_app.control.revoke")
    def test_queued_download_can_be_cancelled(self, revoke):
        session = self.client.session
        session.save()
        job = DownloadJob.objects.create(
            session_key=session.session_key,
            url="https://youtu.be/example",
            download_type="video",
            quality="720",
            task_id="task-123",
            expires_at=timezone.now() + timedelta(hours=1),
        )

        response = self.client.post(reverse("download-cancel", args=[job.id]))

        self.assertEqual(response.status_code, 202)
        job.refresh_from_db()
        self.assertEqual(job.status, DownloadJob.Status.CANCELLED)
        self.assertEqual(job.url, "")
        revoke.assert_called_once_with("task-123", terminate=False)

    @override_settings(
        DOWNLOAD_RATE_LIMIT_COUNT=1,
        DOWNLOAD_MAX_ACTIVE_JOBS=10,
        CACHES={
            "default": {
                "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                "LOCATION": "rate-limit-test",
            }
        },
    )
    @patch("downloader.views.download_media.delay")
    def test_download_rate_limit_is_applied_to_anonymous_session(self, delay):
        delay.return_value = SimpleNamespace(id="task-123")
        payload = {
            "url": "https://youtu.be/example",
            "download_type": "video",
            "video_quality": "720",
        }

        first = self.client.post(reverse("download-start"), payload)
        second = self.client.post(reverse("download-start"), payload)

        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 429)


class EnsureAdminCommandTests(TestCase):
    @patch.dict(
        os.environ,
        {
            "DJANGO_SUPERUSER_USERNAME": "render-admin",
            "DJANGO_SUPERUSER_EMAIL": "admin@example.com",
            "DJANGO_SUPERUSER_PASSWORD": "strong-test-password",
        },
    )
    def test_creates_admin_once_without_resetting_existing_password(self):
        output = StringIO()
        call_command("ensure_admin", stdout=output)
        administrator = get_user_model().objects.get(username="render-admin")
        self.assertTrue(administrator.is_superuser)
        self.assertTrue(administrator.check_password("strong-test-password"))

        with patch.dict(os.environ, {"DJANGO_SUPERUSER_PASSWORD": "different-password"}):
            call_command("ensure_admin", stdout=output)

        administrator.refresh_from_db()
        self.assertTrue(administrator.check_password("strong-test-password"))


class YouTubeDownloaderTests(SimpleTestCase):
    def test_rejects_invalid_quality_before_creating_temp_directory(self):
        with patch("downloader.service.tempfile.mkdtemp") as make_temp_directory:
            with self.assertRaisesMessage(DownloadError, "Invalid download quality"):
                YouTubeDownloader("https://youtu.be/example", "video", "invalid")
        make_temp_directory.assert_not_called()

    def test_rejects_video_over_duration_limit(self):
        with tempfile.TemporaryDirectory() as root:
            downloader = YouTubeDownloader(
                "https://youtu.be/example",
                "video",
                "720",
                output_directory=root,
                max_duration=60,
            )

            message = downloader._match_filter({"duration": 61})
            self.assertIn("longer than", message)

    def test_aborts_when_download_exceeds_size_limit(self):
        with tempfile.TemporaryDirectory() as root:
            downloader = YouTubeDownloader(
                "https://youtu.be/example",
                "video",
                "720",
                output_directory=root,
                max_file_size=100,
            )

            with self.assertRaisesMessage(DownloadError, "exceeds"):
                downloader._progress_hook({"downloaded_bytes": 101})

    def test_rejects_oversized_finished_file(self):
        with tempfile.TemporaryDirectory() as root:
            filepath = Path(root) / "video.mp4"
            filepath.write_bytes(b"x" * 101)
            downloader = YouTubeDownloader(
                "https://youtu.be/example",
                "video",
                "720",
                output_directory=root,
                max_file_size=100,
            )

            with self.assertRaisesMessage(DownloadError, "exceeds"):
                downloader._validate_file_size(filepath)


class BackgroundTaskTests(TransactionTestCase):
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
            self.assertEqual(job.url, "")
            self.assertEqual(result["filename"], "video.mp4")

    @patch("downloader.tasks.YouTubeDownloader")
    def test_task_finishes_cancellation_without_starting_download(self, downloader_class):
        with tempfile.TemporaryDirectory() as root:
            job = self._job(status=DownloadJob.Status.CANCELLING)

            with override_settings(DOWNLOAD_ROOT=root):
                result = download_media.run(str(job.id))

            job.refresh_from_db()
            self.assertTrue(result["cancelled"])
            self.assertEqual(job.status, DownloadJob.Status.CANCELLED)
            self.assertEqual(job.url, "")
            downloader_class.assert_not_called()

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

    @override_settings(DOWNLOAD_STALE_JOB_SECONDS=60)
    def test_cleanup_removes_stale_active_job_after_worker_restart(self):
        with tempfile.TemporaryDirectory() as root:
            job = self._job(status=DownloadJob.Status.DOWNLOADING)
            DownloadJob.objects.filter(pk=job.id).update(
                updated_at=timezone.now() - timedelta(minutes=2)
            )
            directory = Path(root) / str(job.id)
            directory.mkdir()
            (directory / "partial.webm").write_bytes(b"partial")

            with override_settings(DOWNLOAD_ROOT=root):
                deleted = cleanup_expired_downloads.run()

            self.assertEqual(deleted, 1)
            self.assertFalse(DownloadJob.objects.filter(pk=job.id).exists())
            self.assertFalse(directory.exists())
