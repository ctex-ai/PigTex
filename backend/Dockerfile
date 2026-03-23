FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    python -m playwright install --with-deps chromium

COPY . .

RUN useradd --create-home --shell /bin/bash pigtex && \
    mkdir -p /ms-playwright && \
    chown -R pigtex:pigtex /app /ms-playwright
USER pigtex

EXPOSE 3001

CMD ["python", "run.py"]
