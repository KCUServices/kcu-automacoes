# Regista a tarefa agendada "Melhorias-IA" (de 5 em 5 minutos, com a tua sessao, sem janela).
# Ficheiro so com ASCII (o PowerShell 5.1 le mal os acentos).
#   powershell -ExecutionPolicy Bypass -File instalar_tarefa.ps1
#   powershell -ExecutionPolicy Bypass -File instalar_tarefa.ps1 -Python "C:\Python313\pythonw.exe" -Minutos 10
#   powershell -ExecutionPolicy Bypass -File instalar_tarefa.ps1 -Remover
param(
    [string]$Python = '',
    [int]$Minutos = 5,
    [string]$Nome = 'Melhorias-IA',
    [switch]$Remover
)
$ErrorActionPreference = 'Stop'

if ($Remover) {
    Unregister-ScheduledTask -TaskName $Nome -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output "Tarefa $Nome removida."
    return
}

$pasta = $PSScriptRoot
$script = Join-Path $pasta 'melhorias.py'
if (-not (Test-Path (Join-Path $pasta 'config.json'))) { throw 'Falta config.json (copia config.exemplo.json e preenche).' }
if (-not $Python) {
    $cmd = Get-Command pythonw.exe -ErrorAction SilentlyContinue
    if (-not $cmd) { throw 'Nao encontrei o pythonw.exe no PATH. Usa -Python "C:\caminho\pythonw.exe".' }
    $Python = $cmd.Source
}
if (-not [Environment]::GetEnvironmentVariable('APROVACOES_TOKEN', 'User')) {
    Write-Output 'Aviso: falta a variavel de utilizador APROVACOES_TOKEN (ver README).'
}

$acao = New-ScheduledTaskAction -Execute $Python -Argument "`"$script`"" -WorkingDirectory $pasta
$t1 = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes $Minutos)
$t2 = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$def = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 1) -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries
$quem = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $Nome -Action $acao -Trigger @($t1, $t2) -Settings $def -Principal $quem -Description 'Analisa issues e ideias novas e prepara PRs para aprovacao no Discord' -Force | Out-Null
Get-ScheduledTask -TaskName $Nome | Select-Object TaskName, State
