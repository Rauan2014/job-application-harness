#!/usr/bin/env bash
# ==============================================================================
# CANDIDATA CLOUD — SCRIPT DE DEPLOY AUTOMATIZADO PARA VPS HETZNER CLOUD
# Sistema Operacional Alvo: Ubuntu 22.04 LTS / 24.04 LTS
# ==============================================================================

set -euo pipefail

echo "=========================================================="
echo "  Iniciando Instalação do Worker da Candidata Cloud       "
echo "=========================================================="

# 1. Garante privilégios de root
if [ "$EUID" -ne 0 ]; then
  echo "[-] Por favor, execute este script como root (sudo ./deploy_vps.sh)"
  exit 1
fi

# 2. Configura Swap de 2GB (Essencial para estabilidade em VPS de 4GB)
if [ ! -f /swapfile ]; then
  echo "[+] Configurando 2GB de memória Swap para absorver picos de navegação..."
  fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
  echo "[✓] Swap ativado com sucesso."
else
  echo "[i] Swap já configurado."
fi

# 3. Atualiza repositórios e instala dependências básicas
echo "[+] Atualizando pacotes do sistema..."
apt-get update -qq
apt-get install -y -qq ca-certificates curl gnupg lsb-release ufw htop git

# 4. Instala Docker Engine e Docker Compose Plugin
if ! command -v docker &> /dev/null; then
  echo "[+] Instalando Docker Engine oficial..."
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg

  echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
    $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null

  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
  echo "[✓] Docker instalado com sucesso."
else
  echo "[i] Docker já está instalado."
fi

# 5. Configura Firewall básico (apenas SSH liberado)
echo "[+] Configurando UFW Firewall..."
ufw allow 22/tcp || true
ufw --force enable || true

# 6. Verifica configuração de ambiente (.env)
if [ ! -f .env ]; then
  if [ -f .env.worker.example ]; then
    echo "[!] Arquivo .env não encontrado. Criando cópia a partir de .env.worker.example..."
    cp .env.worker.example .env
    echo "=========================================================="
    echo "ATENÇÃO: Edite o arquivo .env com suas credenciais do Supabase e Gemini:"
    echo "  nano .env"
    echo "E em seguida execute novamente: docker compose up -d"
    echo "=========================================================="
    exit 0
  fi
fi

# 7. Compila e inicia o container em segundo plano
echo "[+] Compilando imagem do Worker com Camoufox e Playwright..."
docker compose build

echo "[+] Iniciando o Candidata Worker em segundo plano..."
docker compose up -d

echo ""
echo "=========================================================="
echo "  Worker da Candidata Cloud instalado e rodando!          "
echo "=========================================================="
echo "Para acompanhar os logs em tempo real, execute:"
echo "  docker compose logs -f"
echo ""
echo "Para verificar o consumo de CPU/Memória:"
echo "  docker stats candidata-worker"
echo "=========================================================="
