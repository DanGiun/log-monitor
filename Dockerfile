FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY log_viewer ./log_viewer
RUN python -m pip install --no-cache-dir .
EXPOSE 8765
CMD ["log-viewer", "--host", "0.0.0.0", "--port", "8765", "--no-browser"]
