# Stage 1: Build dependencies 🏗️
FROM python:3.12-slim AS builder

WORKDIR /build

# Trust the company CA for pip installs against internal mirrors
COPY ca.pem /usr/local/share/ca-certificates/company-ca.crt
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && \
    update-ca-certificates && \
    rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Stage 2: Minimal runtime 🏃
FROM python:3.12-slim AS runner

# Create a non-root system group and user 👤
RUN groupadd -g 10001 appgroup && \
    useradd -u 10001 -g appgroup -s /sbin/nologin -M appuser

# Trust the company CA for outbound HTTPS calls (GitLab, Keycloak, etc.)
COPY ca.pem /usr/local/share/ca-certificates/company-ca.crt
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && \
    update-ca-certificates && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Writable ledger directory when the container runs without a volume mount.
RUN mkdir -p /data && chown 10001:10001 /data

# Copy the pre-built virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt

# Copy application assets and set ownership 📁
COPY --chown=10001:10001 main.py auth.py config.py model.py gitlab_client.py logging_utils.py releases_store.py .
COPY --chown=10001:10001 app/ ./app/
COPY --chown=10001:10001 public/ ./public/

USER 10001:10001

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]