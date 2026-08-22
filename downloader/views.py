from django.http import FileResponse
from django.shortcuts import render

from .forms import DownloadForm
from .service import (
    DownloadError,
    YouTubeDownloader,
)


def home(request):

    if request.method == "POST":

        form = DownloadForm(
            request.POST
        )

        if form.is_valid():

            url = form.cleaned_data[
                "url"
            ]

            download_type = (
                form.cleaned_data[
                    "download_type"
                ]
            )

            if download_type == "video":

                quality = (
                    form.cleaned_data[
                        "video_quality"
                    ]
                )

            else:

                quality = (
                    form.cleaned_data[
                        "audio_quality"
                    ]
                )

            downloader = YouTubeDownloader(
                url,
                download_type,
                quality
            )

            try:

                file_object, filename = (
                    downloader.download()
                )

                return FileResponse(
                    file_object,
                    as_attachment=True,
                    filename=filename
                )

            except DownloadError as error:

                form.add_error(
                    None,
                    str(error)
                )

    else:

        form = DownloadForm()

    return render(
        request,
        "downloader/index.html",
        {
            "form": form
        }
    )
