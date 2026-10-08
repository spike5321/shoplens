FROM python:3.13.12-slim-bookworm AS dependencies
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/srv/backend
WORKDIR /srv
COPY backend/requirements.lock.txt /tmp/requirements.lock.txt
RUN pip install --no-cache-dir -r /tmp/requirements.lock.txt

FROM dependencies AS api
COPY backend/app backend/app
COPY data/metrics.yaml data/metrics.yaml
USER 10001:10001
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]

FROM dependencies AS init
COPY backend/app backend/app
COPY backend/migrations backend/migrations
COPY alembic.ini alembic.ini
COPY data data
COPY scripts/setup_db.py scripts/setup_checkpoints.py scripts/seed_data.py scripts/check_reference.py scripts/container_init.py scripts/
USER 10001:10001
CMD ["python", "scripts/container_init.py"]

FROM init AS checks
COPY tests tests
COPY scripts scripts
COPY evals evals
COPY pyproject.toml pyproject.toml
CMD ["python", "-m", "pytest", "-m", "integration", "-p", "no:cacheprovider"]
