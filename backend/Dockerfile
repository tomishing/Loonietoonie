FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Exact, tested versions (requirements.lock), so a rebuild never picks up a new major release.
COPY requirements.lock .
RUN pip install -r requirements.lock

COPY app ./app

RUN useradd --create-home appuser
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

EXPOSE 8000
# The entrypoint fixes the data folder's owner, then drops to appuser.
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
