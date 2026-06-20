FROM python:3.12-slim

# Non-root user; serial access is granted by the device passthrough + cgroup,
# but we still add 'dialout' in case the host nodes are group-owned that way.
RUN useradd --create-home --uid 1000 app \
    && usermod -aG dialout app

WORKDIR /app

# Install dependencies first for layer caching.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

USER app

ENV CONFIG=/app/config.yaml \
    HOST=0.0.0.0 \
    PORT=8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import os,urllib.request,sys; sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/health').status==200 else 1)"

CMD ["stage-api"]
