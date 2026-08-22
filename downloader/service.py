import os
import shutil
import tempfile
from pathlib import Path

import yt_dlp


def format_duration(seconds):
    if not seconds:
        return "Unknown"

    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    seconds = seconds % 60

    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"

    return f"{minutes}:{seconds:02d}"


def get_video_info(url):
    """
    Fetch basic YouTube video information without downloading it.
    """

    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
    }

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(
                url,
                download=False
            )

        resolutions = set()

        for fmt in info.get("formats", []):
            height = fmt.get("height")

            if height:
                resolutions.add(height)

        # Keep only useful/common resolutions
        allowed_resolutions = [
            2160,
            1440,
            1080,
            720,
            480,
            360,
            240,
            144,
        ]

        available_resolutions = [
            resolution
            for resolution in allowed_resolutions
            if resolution in resolutions
        ]

        return {
            "title": info.get("title"),
            "thumbnail": info.get("thumbnail"),
            "duration": format_duration(
                info.get("duration")
            ),
            "uploader": info.get("uploader"),
            "webpage_url": info.get("webpage_url"),
            "resolutions": available_resolutions,
        }

    except yt_dlp.utils.DownloadError as error:
        raise DownloadError(
            f"Could not retrieve video information: {error}"
        )
    
class DownloadError(Exception):
    pass


class DeleteOnClose:

    def __init__(self, file_object, directory):
        self.file_object = file_object
        self.directory = directory

    def read(self, *args, **kwargs):
        return self.file_object.read(*args, **kwargs)

    def close(self):
        try:
            self.file_object.close()
        finally:
            shutil.rmtree(
                self.directory,
                ignore_errors=True
            )

    def __getattr__(self, name):
        return getattr(self.file_object, name)


class YouTubeDownloader:

    VIDEO_QUALITIES = {
        "2160",
        "1440",
        "1080",
        "720",
        "480",
        "360",
        "240",
        "144",
    }
    AUDIO_QUALITIES = {"320", "192", "128"}

    def __init__(self, url, download_type, quality):

        allowed_qualities = (
            self.VIDEO_QUALITIES
            if download_type == "video"
            else self.AUDIO_QUALITIES
            if download_type == "audio"
            else set()
        )

        if download_type not in {"video", "audio"}:
            raise DownloadError("Invalid download type.")

        if quality not in allowed_qualities:
            raise DownloadError("Invalid download quality.")

        self.url = url
        self.download_type = download_type
        self.quality = quality

        self.temp_directory = tempfile.mkdtemp(
            prefix="youtube_download_"
        )

    def download(self):

        try:

            if self.download_type == "video":
                return self._download_video()

            if self.download_type == "audio":
                return self._download_audio()

        except yt_dlp.utils.DownloadError:

            self.cleanup()

            raise DownloadError(
                "The video could not be downloaded."
            )

        except Exception:

            self.cleanup()
            raise

    def _base_options(self):

        return {
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "restrictfilenames": True,
            "retries": 3,

            "outtmpl": os.path.join(
                self.temp_directory,
                "%(title).150B-%(id)s.%(ext)s"
            ),
        }

    def _download_video(self):

        height = int(self.quality)

        options = self._base_options()

        options.update(
            {
                "format": (
                    f"bestvideo[height<={height}]"
                    f"+bestaudio/"
                    f"best[height<={height}]"
                ),

                "merge_output_format": "mp4",
            }
        )

        self._execute_download(options)

        filepath = self._find_final_file()

        return self._prepare_file(filepath)

    def _download_audio(self):

        options = self._base_options()

        options.update(
            {
                "format": "bestaudio/best",

                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality":
                            self.quality,
                    }
                ],
            }
        )

        self._execute_download(options)

        filepath = self._find_final_file(
            ".mp3"
        )

        return self._prepare_file(filepath)

    def _execute_download(self, options):

        with yt_dlp.YoutubeDL(options) as ydl:

            ydl.extract_info(
                self.url,
                download=True
            )

    def _find_final_file(
        self,
        required_extension=None
    ):

        directory = Path(
            self.temp_directory
        )

        files = []

        for file in directory.iterdir():

            if not file.is_file():
                continue

            if file.suffix in {
                ".part",
                ".ytdl",
            }:
                continue

            if required_extension:

                if (
                    file.suffix.lower()
                    != required_extension.lower()
                ):
                    continue

            files.append(file)

        if not files:

            raise DownloadError(
                "Downloaded file could not be found."
            )

        return max(
            files,
            key=lambda file:
                file.stat().st_mtime
        )

    def _prepare_file(self, filepath):

        file_object = open(
            filepath,
            "rb"
        )

        wrapped_file = DeleteOnClose(
            file_object,
            self.temp_directory
        )

        return wrapped_file, filepath.name

    def cleanup(self):

        shutil.rmtree(
            self.temp_directory,
            ignore_errors=True
        )
