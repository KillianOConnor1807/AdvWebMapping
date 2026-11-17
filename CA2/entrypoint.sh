#!/bin/sh
# Container start-up: bring the database schema up to date, collect static files,
# load seed data into any empty tables, then hand over to the container's command.
set -e

echo "Applying database migrations..."
python manage.py migrate --noinput
python manage.py createcachetable

echo "Collecting static files..."
python manage.py collectstatic --noinput --verbosity 0

if [ "${SEED_DATA:-true}" = "true" ]; then
    echo "Checking seed data..."
    python manage.py seed
fi

exec "$@"
