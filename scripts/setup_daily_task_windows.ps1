# Setup Windows Scheduled Task for Daily Job Hunt at 11:00 AM
$taskName = "CandidataCloudDailyJobHunt"
$pythonPath = "C:\codes\ia_candidata\.venv\Scripts\python.exe"
$workingDir = "C:\codes\ia_candidata"
$arguments = "-m core.daily_job_hunter --run-now"

Write-Host "Configurando Tarefa Agendada no Windows para às 11:00 todos os dias..." -ForegroundColor Cyan

$action = New-ScheduledTaskAction -Execute $pythonPath -Argument $arguments -WorkingDirectory $workingDir
$trigger = New-ScheduledTaskTrigger -Daily -At "11:00AM"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

# Remove existing if present
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Description "Busca ativa diária de vagas e abastecimento no Supabase da Candidata Cloud."

Write-Host "✅ Tarefa agendada com sucesso! O sistema executará a busca na web todos os dias às 11:00." -ForegroundColor Green
Write-Host "Para executar manualmente a qualquer momento, rode: .\.venv\Scripts\python.exe -m core.daily_job_hunter --run-now" -ForegroundColor Yellow
