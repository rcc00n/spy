FROM mcr.microsoft.com/playwright/python:v1.55.0-noble

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_BREAK_SYSTEM_PACKAGES=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        fluxbox \
        netcat-openbsd \
