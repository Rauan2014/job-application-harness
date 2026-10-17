#!/usr/bin/env bash
set -e

# Candidata IA — Script de Inicialização do Painel Visual para Linux / macOS
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
cd "$DIR"

echo "==============================================================="
echo "    🚀 INICIANDO CANDIDATA IA — PAINEL DE CONTROLE VISUAL"
echo "==============================================================="
echo "Abrindo seu navegador automaticamente em instantes..."
echo ""

if [ -f ".venv/bin/python" ]; then
    .venv/bin/python -m web
elif command -v python3 >/dev/null 2>&1; then
    python3 -m web
elif command -v python >/dev/null 2>&1; then
    python -m web
else
    echo "Erro: Python 3 não foi encontrado. Por favor, instale o Python e configure seu ambiente virtual."
    exit 1
fi
