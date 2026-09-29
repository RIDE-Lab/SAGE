param([switch]$InitEnvOnly)
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
    Write-Host 'Created .env from .env.example; model endpoints can be edited before startup.'
}
if ($InitEnvOnly) { return }
$project = if ($env:SAGE_COMPOSE_PROJECT) { $env:SAGE_COMPOSE_PROJECT } else { "sage-icpp-demo-local" }
docker load -i artifacts/sage-icpp-demo-20260926-cpu-full.tar
if ($LASTEXITCODE -ne 0) { throw 'Docker image import failed.' }
docker compose -p $project -f compose.local.yaml up -d --pull never --no-build --wait
if ($LASTEXITCODE -ne 0) { throw 'Demo startup failed.' }
$address = docker compose -p $project -f compose.local.yaml port demo 18400
if ($LASTEXITCODE -ne 0) { throw 'Could not determine the published port.' }
Write-Host "Open http://$address/ui/"
