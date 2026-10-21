# Shared image for ACHAL Python services. Build context = repo root.
FROM python:3.12-slim
ARG SERVICE
WORKDIR /app
ENV PYTHONUNBUFFERED=1 ACHAL_CONFIG_DIR=/app/config
COPY libs/achal-common /app/libs/achal-common
COPY services/${SERVICE}/requirements.txt /app/services/${SERVICE}/requirements.txt
RUN cd /app/services/${SERVICE} && pip install --no-cache-dir -r requirements.txt
COPY config /app/config
COPY schemas /app/schemas
COPY services/${SERVICE}/src /app/services/${SERVICE}/src
WORKDIR /app/services/${SERVICE}/src
