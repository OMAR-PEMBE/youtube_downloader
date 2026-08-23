# YouTube Downloader

The application uses Django for HTTP requests, Celery for background downloads,
Redis for task dispatch/progress state, yt-dlp for media retrieval, and FFmpeg
for merging and conversion.

Only download media you own or have permission to download.

## Configure the environment

Create your private environment file before starting the stack:

```powershell
Copy-Item .env.example .env
```

Open `.env` and replace both placeholder values:

- `DJANGO_SECRET_KEY` with a long random value.
- `POSTGRES_PASSWORD` with a private database password.

Generate a Django secret with:

```powershell
.\venv\Scripts\python.exe -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

The `.env` file is ignored by Git and Docker's build context so credentials are
not committed or copied into an image.

## Recommended local setup

Celery does not support native Windows workers. Install Docker Desktop, then run:

```powershell
docker compose up --build
```

Open <http://localhost:8000>. The Compose stack starts Django, PostgreSQL,
Redis, a Celery worker, and Celery Beat for expired-file cleanup.

Stop the stack with:

```powershell
docker compose down
```

Finished files are available for one hour by default. Override this with the
`DOWNLOAD_JOB_TTL_SECONDS` environment variable. Anonymous browser sessions are
limited to two active jobs by default; configure `DOWNLOAD_MAX_ACTIVE_JOBS` to
change that limit.

The application intentionally has no user accounts or download history. A URL
is cleared from the database as soon as the worker claims its job. The finished
file and its job record are deleted after the first browser delivery; the
one-hour cleanup remains a fallback for abandoned downloads. Users can also
cancel queued or running jobs from the progress panel.

Anonymous sessions may start 10 downloads per hour by default. Configure
`DOWNLOAD_RATE_LIMIT_COUNT` and `DOWNLOAD_RATE_LIMIT_WINDOW_SECONDS` to tune
this temporary Redis-backed limit. It is abuse protection, not permanent user
tracking, and disappears when the Redis key expires.

Downloads are limited to three hours and 2 GB by default. Configure
`DOWNLOAD_MAX_DURATION_SECONDS` and `DOWNLOAD_MAX_FILE_SIZE_BYTES` in `.env` to
change those limits. The size check is enforced during transfer and on the
finished file because streaming formats do not always publish an accurate size
before download.

PostgreSQL data is stored in the `postgres-data` Docker volume. Normal
`docker compose down` does not remove it. Running `docker compose down -v`
deletes PostgreSQL and Redis data permanently and should only be used when you
intend to reset the application.

## Tests

```powershell
.\venv\Scripts\python.exe manage.py test
```
