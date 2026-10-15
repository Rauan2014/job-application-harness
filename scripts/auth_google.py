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

    # Tenta obter o ID da planilha via argumento ou via .env (GOOGLE_SHEET_URL)
    sheet_id: str | None = None
    if len(sys.argv) > 1:
        sheet_id = sys.argv[1].strip()
    else:
        import os
        from storage.sheet_loader import extract_spreadsheet_id
        raw_url = os.getenv("GOOGLE_SHEET_URL") or ""
        if not raw_url and Path(".env").exists():
            for line in Path(".env").read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("GOOGLE_SHEET_URL="):
                    raw_url = line.split("=", 1)[1].strip().strip("'\"")
                    break
        if raw_url:
            sheet_id = extract_spreadsheet_id(raw_url)

    if not sheet_id:
        print("\n Nenhuma planilha informada. Para testar o download, defina GOOGLE_SHEET_URL no .env ou passe o ID como argumento:")
        print("   python scripts/auth_google.py <SPREADSHEET_ID>")
        return

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
