param([switch]$InitEnvOnly)
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
    Write-Host 'Created .env from .env.example; edit it to configure optional models.'
}
if ($InitEnvOnly) { return }
docker compose -p sage-icpp-demo-hub -f compose.hub.yaml pull
if ($LASTEXITCODE -ne 0) { throw 'Image pull failed.' }
docker compose -p sage-icpp-demo-hub -f compose.hub.yaml up -d --pull never --no-build --wait
if ($LASTEXITCODE -ne 0) { throw 'Demo startup failed.' }
$address = docker compose -p sage-icpp-demo-hub -f compose.hub.yaml port demo 18400
if ($LASTEXITCODE -ne 0) { throw 'Could not determine the published port.' }
Write-Host "Open http://$address/ui/"
