# The integrations service. Build from the repository's root:
#   podman build -t integrations .
# It holds no credentials: a person's token comes with each request.
FROM node:22-slim AS docs
WORKDIR /build/clients/js
COPY clients/js/package.json clients/js/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY openapi/ /build/openapi/
RUN npm run --silent docs

FROM python:3.14-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PORT=8080 INTEGRATIONS_DOCS_DIR=/app/docs
WORKDIR /app
COPY service-requirements.lock pyproject.toml README.md LICENSE /tmp/build/
COPY src/ /tmp/build/src/
RUN pip install --require-hashes -r /tmp/build/service-requirements.lock \
    && pip install --no-deps /tmp/build && rm -rf /tmp/build
COPY --from=docs /build/clients/js/docs-site/ docs/
RUN useradd --create-home --uid 1000 app
USER app
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn integrations.service.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*' --no-server-header"]
