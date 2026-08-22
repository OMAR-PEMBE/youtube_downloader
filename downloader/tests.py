from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import reverse

from .forms import DownloadForm
from .service import DownloadError, YouTubeDownloader


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


class HomeViewTests(SimpleTestCase):
    def test_home_page_loads(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "YouTube Downloader")

    @patch("downloader.views.YouTubeDownloader")
    def test_download_error_is_shown_to_user(self, downloader_class):
        downloader_class.return_value.download.side_effect = DownloadError("Failed")
        response = self.client.post(reverse("home"), {
            "url": "https://youtu.be/example",
            "download_type": "video",
            "video_quality": "720",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Failed")


class YouTubeDownloaderTests(SimpleTestCase):
    def test_rejects_invalid_quality_before_creating_temp_directory(self):
        with patch("downloader.service.tempfile.mkdtemp") as make_temp_directory:
            with self.assertRaisesMessage(DownloadError, "Invalid download quality"):
                YouTubeDownloader("https://youtu.be/example", "video", "invalid")
        make_temp_directory.assert_not_called()
