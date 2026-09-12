FROM python:3.11-slim

# UTF-8 stdio so emoji log lines never raise UnicodeEncodeError; unbuffered so
# logs stream promptly under docker.
ENV PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1

WORKDIR /app

# Copy requirements and install them
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Run as a non-root user
RUN useradd --create-home --uid 1000 appuser
USER appuser

# Informational only: ports are per-camera in config.yaml and the recommended
# compose uses network_mode: host. 8080 is the default when a camera omits `port`.
EXPOSE 8080

CMD ["python", "app.py"]
