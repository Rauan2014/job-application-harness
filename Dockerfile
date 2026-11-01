# ==============================================================================
# CANDIDATA CLOUD — MOTOR DE AUTOMAÇÃO (ia_candidata worker)
# Imagem de produção otimizada para VPS Hetzner Cloud (3 vCPU / 4GB RAM)
# ==============================================================================

FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

# Define diretório de trabalho e variáveis de ambiente
WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive \
    HEADLESS=true \
    USE_CAMOUFOX=true

# Instala dependências extras do sistema (Gecko/Firefox libraries, fontes e ferramentas de rede)
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-liberation \
    fonts-noto-color-emoji \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    xvfb \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copia arquivos de requisitos e instala dependências Python
COPY requirements.txt requirements-laya.txt /app/
RUN pip install --upgrade pip setuptools wheel && \
    pip install -r requirements.txt

# Inicializa o cache de binários do Camoufox / Playwright
RUN python -c "from camoufox.async_api import AsyncCamoufox; print('Verificando Camoufox engine...')" || true

# Copia código-fonte da aplicação
COPY . /app/

# Cria diretórios de cache e artefatos
RUN mkdir -p /app/cache/resumes /app/artifacts/screenshots

# Executa o daemon de produção
CMD ["python", "cloud_worker.py"]
