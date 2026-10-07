FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt pyproject.toml README.md LICENSE ./
COPY pshl ./pshl
RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 8000

CMD ["python", "-m", "pshl", "serve", "--template", "/templates/template.psd", "--config", "/config/template.json", "--font", "/fonts/font.ttf", "--output", "/output", "--host", "0.0.0.0", "--port", "8000"]
