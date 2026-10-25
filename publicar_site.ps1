# Publica o painel (static/dashboard.html) em https://ypod.ypod-sm.workers.dev
#
#   powershell -ExecutionPolicy Bypass -File publicar_site.ps1
#
# Antes, confira a mudanca no painel local (http://127.0.0.1:8000): ele le o
# mesmo arquivo a cada atualizacao da pagina, entao basta dar F5.
# Precisa do login na Cloudflare feito uma vez (wrangler login).

$ErrorActionPreference = "Stop"
$API = "https://ypod-api.ypod-sm.workers.dev"
Set-Location $PSScriptRoot

$node = Get-ChildItem (Join-Path $env:LOCALAPPDATA "Programs\node-v*-win-x64") -Directory -ErrorAction SilentlyContinue |
    Sort-Object Name -Descending | Select-Object -First 1 -ExpandProperty FullName
if ($node) { $env:Path = "$node;$env:Path" }
$python = Get-ChildItem (Join-Path $env:LOCALAPPDATA "Programs\Python\Python3*\python.exe") -ErrorAction SilentlyContinue |
    Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
if (-not $python) { $python = "python" }
$env:WRANGLER_SEND_METRICS = "false"

& $python tools\gerar_site.py $API
if ($LASTEXITCODE -ne 0) { throw "gerar_site.py falhou" }

wrangler deploy -c wrangler.site.jsonc
if ($LASTEXITCODE -ne 0) { throw "wrangler deploy falhou (fez o 'wrangler login'?)" }

Remove-Item -Recurse -Force .wrangler -ErrorAction SilentlyContinue
Write-Host "`nPublicado: https://ypod.ypod-sm.workers.dev (Ctrl+F5 no navegador para ver)"
