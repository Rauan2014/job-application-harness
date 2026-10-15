import sys
from pathlib import Path

# Garante que a raiz do projeto esteja no sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from storage.drive_client import build_service, build_sheets_service  # noqa: E402
from storage.sheet_loader import download_google_sheet  # noqa: E402


def main() -> None:
    print("=" * 60)
    print(" INICIANDO AUTENTICAÇÃO GOOGLE VIA OAUTH (client_secret.json)")
    print("=" * 60)
    print("Uma janela do seu navegador será aberta para autorizar o acesso.")
    print("Escolha a sua conta Google que tem acesso à planilha.")
    print("-" * 60)

    try:
        service = build_service(
            service_account_file=Path("./service_account.json"),
            oauth_client_file=Path("./client_secret.json"),
        )
        print("\n Autenticação concluída com sucesso! Arquivo token.json gerado.")
    except Exception as exc:
        print(f"\n Falha na autenticação: {exc}")
        sys.exit(1)

    # Testa o download da planilha do usuário via Drive
    sheet_id = "1L8fZdLj9YHMu62oFMRg77g-_BePl6UoZ16tGIYFA6TI"
    print(f"\nTestando acesso e exportação da planilha {sheet_id}...")
    try:
        dest = Path("./cache/vagas_sheets_latest.xlsx")
        download_google_sheet(sheet_id, dest_path=dest, drive_service=service)
        print(f" Sucesso! Planilha baixada e salva em: {dest.resolve()} ({dest.stat().st_size} bytes)")
    except Exception as exc:
        print(f" Aviso ao exportar planilha: {exc}")

    # Testa a API do Google Sheets
    print("\nTestando Google Sheets API v4...")
    try:
        sheets_svc = build_sheets_service(
            service_account_file=Path("./service_account.json"),
            oauth_client_file=Path("./client_secret.json"),
        )
        meta = sheets_svc.spreadsheets().get(spreadsheetId=sheet_id).execute()
        print(f" Sucesso! Conectado ao Google Sheets: {meta.get('properties', {}).get('title')}")
    except Exception as exc:
        print(f" Aviso na Google Sheets API: {exc}")
        print(" -> Lembre-se de ativar a 'Google Sheets API' em seu projeto Google Cloud se for usar escrita online.")

if __name__ == "__main__":
    main()
