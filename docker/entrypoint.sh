#!/bin/sh
set -eu

mode="${1:-web}"

case "$mode" in
  web)
    exec gunicorn civicloop.wsgi:application \
      --bind "0.0.0.0:${PORT:-8000}" \
      --workers "${WEB_CONCURRENCY:-2}" \
      --access-logfile -
    ;;
  worker)
    exec celery -A civicloop worker \
      --loglevel "${LOG_LEVEL:-INFO}" \
      --concurrency "${CELERY_WORKER_CONCURRENCY:-1}"
    ;;
  scheduler)
    exec celery -A civicloop beat \
      --loglevel "${LOG_LEVEL:-INFO}" \
      --schedule /tmp/celerybeat-schedule
    ;;
  manage)
    shift
    exec python backend/manage.py "$@"
    ;;
  *)
    exec "$@"
    ;;
esac
