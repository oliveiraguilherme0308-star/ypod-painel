<#
    Copia segura do banco, com o servidor rodando.

        powershell -ExecutionPolicy Bypass -File tools\backup_banco.ps1 -Destino D:\backup-ypod
        ... -Manter 12        quantas copias guardar (padrao 8)
        ... -Verificar        confere a integridade da copia recem-feita

    POR QUE NAO E "copy ypod.db":
    o banco usa WAL. Enquanto o servidor roda, parte das escritas esta no
    arquivo -wal e ainda nao foi para o .db. Uma copia simples pega um banco
    sem essas escritas -- ou pior, pega o .db no meio de uma gravacao. O
    VACUUM INTO do proprio SQLite faz uma copia consistente de um banco em uso,
    e de quebra ja sai compactada.

    POR QUE ISTO IMPORTA:
    o historico completo mora em um lugar so -- este arquivo. O D1 na nuvem e
    copia, mas fica numa conta gratis com teto de 5 GB. Um HD falhando leva o
    projeto inteiro; nenhuma das falhas de seguranca que mapeamos faz isso.
#>
param(
    [Parameter(Mandatory = $true)][string] $Destino,
    [int] $Manter = 8,
    [switch] $Verificar
)

$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $PSScriptRoot
$banco = Join-Path $raiz "data\ypod.db"

function Erro($m) { Write-Host "  $m" -ForegroundColor Red; exit 1 }

if (-not (Test-Path $banco)) { Erro "Nao achei o banco em $banco" }
if (-not (Test-Path $Destino)) { New-Item -ItemType Directory -Force $Destino | Out-Null }

# o python da instalacao real, nao o atalho da Microsoft Store
$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py -or $py -like "*WindowsApps*") {
    $py = @(Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe" -EA SilentlyContinue |
            Sort-Object FullName -Descending)[0].FullName
}
if (-not $py) { Erro "Nao achei um Python de verdade (o atalho da Store nao serve)." }

# O VACUUM INTO recusa arquivo existente -- e isso e bom: backup nunca deve
# sobrescrever backup em silencio. Mas duas execucoes seguidas caem no mesmo
# segundo (o script roda em menos de um), entao em vez de carimbo mais fino,
# que sempre tera uma resolucao que colide, a colisao e tratada.
$carimbo = Get-Date -Format "yyyy-MM-dd_HHmmss"
$saida = Join-Path $Destino "ypod_$carimbo.db"
$sufixo = 2
while (Test-Path $saida) {
    $saida = Join-Path $Destino "ypod_${carimbo}-$sufixo.db"
    $sufixo++
}

# VACUUM INTO nao aceita arquivo existente, e e isso que a gente quer: nunca
# sobrescrever um backup anterior por acidente.
$script = @"
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
c.execute("VACUUM INTO ?", (sys.argv[2],))
c.close()
"@
$tmp = Join-Path $env:TEMP "ypod_vacuum.py"
Set-Content -Encoding UTF8 $tmp $script

& $py -I $tmp $banco $saida
if ($LASTEXITCODE -ne 0) { Erro "O VACUUM INTO falhou. Nada foi gravado." }

$kb = [math]::Round((Get-Item $saida).Length / 1KB)
Write-Host "  copia: $saida  ($kb KB)" -ForegroundColor Green

if ($Verificar) {
    $v = @"
import sqlite3, sys
c = sqlite3.connect("file:%s?mode=ro" % sys.argv[1], uri=True)
print(c.execute("PRAGMA integrity_check").fetchone()[0],
      c.execute("SELECT COUNT(*) FROM readings").fetchone()[0])
"@
    $tmp2 = Join-Path $env:TEMP "ypod_check.py"
    Set-Content -Encoding UTF8 $tmp2 $v
    $r = (& $py -I $tmp2 $saida) -split "\s+"
    if ($r[0] -ne "ok") { Erro "A copia saiu corrompida: $($r[0])" }
    Write-Host "  integridade ok, $($r[1]) leituras" -ForegroundColor Green
}

# Guarda as N mais novas. Rotacionar importa tanto quanto copiar: backup que
# enche o disco para de rodar, e ai nao ha backup nenhum.
#
# Ordena pela DATA do arquivo, nao pelo nome: com os sufixos de colisao
# ("-2", "-3"), ordem alfabetica nao e ordem cronologica, e a rotacao apagaria
# a copia errada -- possivelmente a mais nova.
$antigas = @(Get-ChildItem $Destino -Filter "ypod_*.db" |
             Sort-Object LastWriteTime -Descending | Select-Object -Skip $Manter)
foreach ($a in $antigas) {
    Remove-Item $a.FullName -Force
    Write-Host "  removida copia antiga: $($a.Name)" -ForegroundColor DarkGray
}

$n = @(Get-ChildItem $Destino -Filter "ypod_*.db").Count
Write-Host "  $n copias em $Destino" -ForegroundColor DarkGray
