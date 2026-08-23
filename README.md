# YouTube Downloader

The application uses Django for HTTP requests, Celery for background downloads,
Redis for task dispatch/progress state, yt-dlp for media retrieval, and FFmpeg
for merging and conversion.

Only download media you own or have permission to download.

## Recommended local setup

Celery does not support native Windows workers. Install Docker Desktop, then run:

```powershell
docker compose up --build
```

Open <http://localhost:8000>. The Compose stack starts Django, Redis, a Celery
worker, and Celery Beat for expired-file cleanup.

Stop the stack with:

```powershell
docker compose down
```

Finished files are available for one hour by default. Override this with the
`DOWNLOAD_JOB_TTL_SECONDS` environment variable. Anonymous browser sessions are
limited to two active jobs by default; configure `DOWNLOAD_MAX_ACTIVE_JOBS` to
change that limit.

## Tests

```powershell
.\venv\Scripts\python.exe manage.py test
```
