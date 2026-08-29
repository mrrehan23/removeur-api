FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       libglib2.0-0 \
       libgl1 \
       libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --upgrade pip \
    && pip install -r requirements.txt

COPY main.py .

RUN useradd --create-home --shell /usr/sbin/nologin removeur \
    && chown -R removeur:removeur /app

USER removeur

CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-10000} --workers 1
