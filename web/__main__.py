"""Ponto de entrada para iniciar a UI do harness via `python -m web`."""

from __future__ import annotations

import argparse
import sys
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn


def open_browser(url: str, delay_s: float = 1.2) -> None:
    """Abre o navegador padrão automaticamente após o servidor iniciar."""
    time.sleep(delay_s)
    try:
        webbrowser.open(url)
    except Exception:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Inicia o servidor e a interface visual da Candidata IA.")
    parser.add_argument("--host", default="127.0.0.1", help="Endereço de escuta (padrão: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Porta de escuta (padrão: 8000)")
    parser.add_argument("--no-browser", action="store_true", help="Não abre o navegador automaticamente.")
    args = parser.parse_args()

    url = f"http://{args.host}:{args.port}"
    print("\n" + "=" * 65)
    print("        🚀 CANDIDATA IA — PAINEL DE CONTROLE VISUAL")
    print("=" * 65)
    print(f" Interface web disponível em:  {url}")
    print(f" Documentação interativa em:    {url}/docs")
    print("-" * 65)
    print(" Pressione Ctrl+C para encerrar o painel.")
    print("=" * 65 + "\n")

    if not args.no_browser:
        threading.Thread(target=open_browser, args=(url,), daemon=True).start()

    uvicorn.run("web.app:app", host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
