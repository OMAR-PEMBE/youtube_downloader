import os
import shutil
import tempfile
from pathlib import Path

import imageio_ffmpeg
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


def format_file_size(size):
    if size >= 1024**3:
        return f"{size / 1024**3:.1f} GB"
    return f"{size / 1024**2:.0f} MB"


def get_video_info(url, max_duration=None):
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

        duration = info.get("duration")
        if max_duration and duration and duration > max_duration:
            raise DownloadError(
                f"This video is longer than the {format_duration(max_duration)} limit."
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
            "duration": format_duration(duration),
            "duration_seconds": duration,
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

    def __init__(
        self,
        url,
        download_type,
        quality,
        output_directory=None,
        progress_callback=None,
        max_duration=None,
        max_file_size=None,
    ):

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

        self.progress_callback = progress_callback
        self.max_duration = max_duration
        self.max_file_size = max_file_size
        self.limit_error = ""
        self.owns_directory = output_directory is None
        self.temp_directory = (
            tempfile.mkdtemp(prefix="youtube_download_")
            if self.owns_directory
            else str(Path(output_directory).resolve())
        )
        Path(self.temp_directory).mkdir(parents=True, exist_ok=True)

    def download(self):

        filepath = self.download_to_path()
        return self._prepare_file(filepath)

    def download_to_path(self):

        try:

            if self.download_type == "video":
                return self._download_video()

            if self.download_type == "audio":
                return self._download_audio()

        except yt_dlp.utils.DownloadError:

            self.cleanup()

            if self.limit_error:
                raise DownloadError(self.limit_error)

            raise DownloadError(
                "The video could not be downloaded."
            )

        except Exception:

            self.cleanup()
            raise

    def _base_options(self):

        return {
            "ffmpeg_location": imageio_ffmpeg.get_ffmpeg_exe(),
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "restrictfilenames": True,
            "retries": 3,
            "socket_timeout": 30,
            "max_filesize": self.max_file_size,
            "match_filter": self._match_filter,

            "progress_hooks": [self._progress_hook],
            "postprocessor_hooks": [self._postprocessor_hook],

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

        self._validate_file_size(filepath)

        return filepath

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

        self._validate_file_size(filepath)

        return filepath

    def _match_filter(self, info, *, incomplete=False):

        duration = info.get("duration")
        if self.max_duration and duration and duration > self.max_duration:
            self.limit_error = (
                f"This video is longer than the "
                f"{format_duration(self.max_duration)} limit."
            )
            return self.limit_error

        return None

    def _validate_file_size(self, filepath):

        if self.max_file_size and filepath.stat().st_size > self.max_file_size:
            raise DownloadError(
                f"The finished file exceeds the "
                f"{format_file_size(self.max_file_size)} limit."
            )

    def _progress_hook(self, progress):

        downloaded = int(progress.get("downloaded_bytes") or 0)
        if self.max_file_size and downloaded > self.max_file_size:
            raise DownloadError(
                f"The download exceeds the "
                f"{format_file_size(self.max_file_size)} limit."
            )

        if self.progress_callback:
            self.progress_callback("download", progress)

    def _postprocessor_hook(self, progress):

        if self.progress_callback:
            self.progress_callback("processing", progress)

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
