# Imagem oficial do Playwright (Ubuntu Jammy) já com Chromium e deps
FROM mcr.microsoft.com/playwright/python:v1.46.0-jammy

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Instala apenas suas libs de app (o Playwright já vem instalado na imagem)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia o código
COPY . .

# Sobe o servidor FastAPI; Render injeta $PORT
CMD bash -lc 'uvicorn server:app --host 0.0.0.0 --port ${PORT:-10000}'
