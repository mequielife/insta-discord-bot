FROM python:3.11-slim

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# deps Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright + Chromium + dependências do SO
RUN python -m playwright install --with-deps chromium

# copia seu código
COPY . .

# Render injeta a variável PORT; subimos o servidor FastAPI
CMD bash -lc 'uvicorn server:app --host 0.0.0.0 --port ${PORT:-10000}'
