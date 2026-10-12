FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY pshl ./pshl
RUN pip install --no-cache-dir . && useradd --uid 10001 --create-home renderer
ENV PSHL_STATE_DIR=/home/renderer/.pshl-state
RUN mkdir /home/renderer/.pshl-state && chown renderer:renderer /home/renderer/.pshl-state
USER renderer
EXPOSE 8000
CMD ["python", "-m", "pshl", "serve", "--templates", "/templates", "--output", "/output", "--host", "0.0.0.0"]
