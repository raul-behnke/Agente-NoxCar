FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# deps first (layer cache)
COPY requirements.txt .
RUN pip install -r requirements.txt gunicorn

COPY . .

EXPOSE 8000

# gunicorn + uvicorn workers (async). Tune workers to CPU.
CMD ["gunicorn", "app:api", \
     "-k", "uvicorn.workers.UvicornWorker", \
     "-w", "2", "-b", "0.0.0.0:8000", \
     "--timeout", "60", "--access-logfile", "-"]
