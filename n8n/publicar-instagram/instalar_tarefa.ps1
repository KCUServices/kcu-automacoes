# Regista a tarefa agendada "Publicar-Pasta" (de 2 em 2 minutos, com a tua sessao, sem janela).
# Ficheiro so com ASCII (o PowerShell 5.1 le mal os acentos).
#   powershell -ExecutionPolicy Bypass -File instalar_tarefa.ps1 -Pasta "D:\Publicar"
#   powershell -ExecutionPolicy Bypass -File instalar_tarefa.ps1 -Remover
# Antes: define as variaveis de utilizador PUBLICAR_N8N e PUBLICAR_TOKEN (ver README).
param(
    [string]$Pasta = '',
    [string]$Python = '',
    [int]$Minutos = 2,
    [string]$Nome = 'Publicar-Pasta',
    [switch]$Remover
)
$ErrorActionPreference = 'Stop'

if ($Remover) {
    Unregister-ScheduledTask -TaskName $Nome -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output "Tarefa $Nome removida."
    return
}

$script = Join-Path $PSScriptRoot 'pasta_para_aprovar.py'
if (-not $Python) {
    $cmd = Get-Command pythonw.exe -ErrorAction SilentlyContinue
    if (-not $cmd) { throw 'Nao encontrei o pythonw.exe no PATH. Usa -Python "C:\caminho\pythonw.exe".' }
    $Python = $cmd.Source
}
if ($Pasta) {
    New-Item -ItemType Directory -Force -Path $Pasta | Out-Null
    [Environment]::SetEnvironmentVariable('PUBLICAR_PASTA', $Pasta, 'User')
}
foreach ($v in @('PUBLICAR_N8N', 'PUBLICAR_TOKEN')) {
    if (-not [Environment]::GetEnvironmentVariable($v, 'User')) { Write-Output "Aviso: falta a variavel de utilizador $v." }
}

$acao = New-ScheduledTaskAction -Execute $Python -Argument "`"$script`"" -WorkingDirectory $PSScriptRoot
$t1 = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes $Minutos)
$t2 = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$def = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -StartWhenAvailable -AllowStartIfOnBatteries
$quem = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $Nome -Action $acao -Trigger @($t1, $t2) -Settings $def -Principal $quem -Description 'Manda as imagens da pasta para aprovacao no Discord (via n8n)' -Force | Out-Null
Get-ScheduledTask -TaskName $Nome | Select-Object TaskName, State
