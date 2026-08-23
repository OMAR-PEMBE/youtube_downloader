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

The web container uses Gunicorn rather than Django's development server. Docker
checks `/health/ready/` to confirm that Django, PostgreSQL, and Redis are ready
before treating the web service as healthy. `/health/live/` is a lightweight
process liveness endpoint for hosting platforms.

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

## Administration and advertising

Create the administrator account once:

```powershell
docker compose exec web python manage.py createsuperuser
```

Sign in at <http://localhost:8000/admin/>. The admin provides operational
download-job monitoring and structured advertisement management. Campaigns can
be enabled, scheduled, prioritized, and assigned to the top of the page, above
the form, or below the form. Advertisement content is escaped and cannot inject
raw HTML or JavaScript.

Impressions and clicks are aggregate counters only; they are not linked to IP
addresses, sessions, submitted URLs, or download history. Affiliate links are
rendered with a visible disclosure. Use `DJANGO_ADMIN_PATH` to choose a
different admin URL before deployment; a changed URL is an extra precaution,
not a replacement for a strong password and HTTPS.

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

## Free Render prototype deployment

The repository includes `render.yaml` for a no-cost demonstration deployment.
It provisions one free web service, one free Render Postgres database, and one
free Render Key Value instance. Because free background workers are not
available, the web service starts Gunicorn, a single Celery worker, and Celery
Beat together. This arrangement is for evaluation only, not production.

Before pushing, rotate the local `DJANGO_SECRET_KEY` and `POSTGRES_PASSWORD` in
`.env`. Never commit `.env`; it is already ignored by Git.

Deployment steps:

1. Commit the project and push the `main` branch to GitHub.
2. Sign in to Render and choose **New > Blueprint**.
3. Connect the GitHub repository and select its `render.yaml` Blueprint.
4. When prompted for `DJANGO_ADMIN_PATH`, enter a private path ending in `/`,
   such as `control-your-random-words/`.
5. Apply the Blueprint and wait for the health check to pass.
6. Open the Render Shell for the web service and run
   `python manage.py createsuperuser`.

Render automatically supplies the external hostname, generated Django secret,
PostgreSQL URL, and private Key Value URL. Do not copy local database credentials
into Render.

Free-tier limitations are significant: the web service sleeps when idle, local
download files are ephemeral, Redis data can disappear on restart, and the free
PostgreSQL database expires after 30 days without backups. Upgrade to separate
web/worker services, persistent storage, and paid PostgreSQL before treating the
site as a production service.
