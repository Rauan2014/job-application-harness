@echo off
chcp 65001 >nul
title Candidata IA — Painel Visual
echo ===============================================================
echo     🚀 INICIANDO CANDIDATA IA — PAINEL DE CONTROLE VISUAL
echo ===============================================================
echo Abrindo seu navegador automaticamente em instantes...
echo.

cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m web
) else (
    python -m web
)

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Ocorreu um problema ao iniciar o servidor. Verifique se o Python esta instalado.
    pause
)
