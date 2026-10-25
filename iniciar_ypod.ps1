# Sobe o servidor do YPOD e o mantem no ar: se o python cair, religa em 15 s.
#
# Chamado pelo atalho "YPOD servidor" na pasta Inicializar do Windows, entao
# sobe sozinho quando este usuario entra no PC. Para rodar na mao:
#   powershell -ExecutionPolicy Bypass -File iniciar_ypod.ps1
#
# Log em data\servidor.log. O token de envio vem da variavel de ambiente do
# usuario YPOD_NUVEM_TOKEN (nunca escrito em arquivo).
#
# --source net: procura a ponte na rede o tempo todo, inclusive se ela trocar
# de IP ou estiver desligada quando o PC liga. (Sem isso, nao achando a ponte
# no inicio, o servidor cai para o USB e fica la.)

$NUVEM = "https://ypod-api.ypod-sm.workers.dev"

# Uma instancia so: duas brigariam pela porta 8000 e mandariam tudo em dobro.
$criado = $false
$trava = New-Object System.Threading.Mutex($true, "Local\ypod-servidor", [ref]$criado)
if (-not $criado) { exit 0 }

$pasta = $PSScriptRoot
$log = Join-Path $pasta "data\servidor.log"
New-Item -ItemType Directory -Force (Join-Path $pasta "data") | Out-Null

# A instalacao de verdade primeiro. O PATH pode estar velho ou apontar para o
# "python.exe" falso da Microsoft Store (WindowsApps), que so abre a loja.
$python = Get-ChildItem (Join-Path $env:LOCALAPPDATA "Programs\Python\Python3*\python.exe") -ErrorAction SilentlyContinue |
    Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
if (-not $python) {
    $python = Get-Command python.exe -All -ErrorAction SilentlyContinue |
        Where-Object { $_.Source -notmatch "WindowsApps" } |
        Select-Object -First 1 -ExpandProperty Source
}
if (-not $python) {
    Add-Content -Encoding UTF8 $log ("==== {0} python nao encontrado; instale o Python 3" -f (Get-Date -Format s))
    exit 1
}
if (-not $env:YPOD_NUVEM_TOKEN) {
    $env:YPOD_NUVEM_TOKEN = [Environment]::GetEnvironmentVariable("YPOD_NUVEM_TOKEN", "User")
}
$env:PYTHONIOENCODING = "utf-8"
$script = Join-Path $pasta "ypod_monitor.py"

while ($true) {
    # log passou de 5 MB: guarda o anterior e comeca outro
    if ((Test-Path $log) -and (Get-Item $log).Length -gt 5MB) {
        Move-Item -Force $log "$log.1"
    }
    Add-Content -Encoding UTF8 $log ("==== {0} iniciando" -f (Get-Date -Format s))
    & cmd.exe /c "`"$python`" -u `"$script`" --source net --no-browser --nuvem-url $NUVEM >> `"$log`" 2>&1"
    Add-Content -Encoding UTF8 $log ("==== {0} parou (codigo {1}), religando em 15 s" -f (Get-Date -Format s), $LASTEXITCODE)
    Start-Sleep -Seconds 15
}
