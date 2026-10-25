<#
    Compila o firmware e grava na ponte, num comando.

        powershell -ExecutionPolicy Bypass -File tools\gravar_ponte.ps1
        ... -Alvo 192.168.0.50          grava num IP, se o nome nao resolver
        ... -Porta COM7                 grava por cabo em vez de pela rede
        ... -SoCompilar                 so confere que compila, nao grava

    Os onze arquivos de firmware/ypod_bridge/ sao UM sketch: o compilador junta
    todos e produz um binario so. E esse binario que vai para a placa -- nunca
    os arquivos separados.

    A senha do OTA e lida de segredos.h, entao voce nao digita nada. Quem
    autentica e o firmware que JA ESTA RODANDO na placa: trocar a senha no
    segredos.h so passa a valer depois desta gravacao.
#>
param(
    [string] $Alvo = "ypod-bridge.local",
    [string] $Porta = "",
    [switch] $SoCompilar
)

$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $PSScriptRoot
$sketch = Join-Path $raiz "firmware\ypod_bridge"
$build = Join-Path $raiz "firmware\ypod_bridge\build"

$FQBN = "esp32:esp32:esp32s3:FlashSize=16M,PartitionScheme=app3M_fat9M_16MB,PSRAM=opi,CDCOnBoot=default,USBMode=hwcdc"

function Erro($msg) { Write-Host "`n  $msg`n" -ForegroundColor Red; exit 1 }
function Passo($msg) { Write-Host "`n== $msg" -ForegroundColor Cyan }

# --- 1. o segredo -----------------------------------------------------------
$segredos = Join-Path $sketch "segredos.h"
if (-not (Test-Path $segredos)) {
    Erro ("Falta o segredos.h. Ele nao vem no repositorio (tem a senha do OTA).`n" +
          "  Copie o modelo e preencha:`n`n" +
          "    copy firmware\ypod_bridge\segredos.exemplo.h firmware\ypod_bridge\segredos.h")
}
$senha = ""
foreach ($linha in Get-Content $segredos) {
    if ($linha -match '^\s*#define\s+OTA_SENHA\s+"(.*)"') { $senha = $Matches[1] }
}
if ($senha -eq "troque-esta-senha") {
    Erro "O segredos.h ainda esta com o texto do modelo. Ponha a senha de verdade."
}

# --- 2. o arduino-cli -------------------------------------------------------
$acli = $null
foreach ($c in @((Get-Command arduino-cli -ErrorAction SilentlyContinue).Source,
                 (Join-Path $raiz "ferramentas\arduino-cli.exe"))) {
    if ($c -and (Test-Path $c)) { $acli = $c; break }
}
if (-not $acli) {
    Erro ("Nao achei o arduino-cli. Ele nao fica instalado de forma permanente.`n" +
          "  Baixe o .zip do Windows em https://arduino.github.io/arduino-cli/latest/installation/`n" +
          "  e ponha o arduino-cli.exe em:  $raiz\ferramentas\")
}

# --- 3. compilar ------------------------------------------------------------
Passo "Compilando os 11 arquivos num binario so"
& $acli compile --fqbn $FQBN --build-path $build $sketch
if ($LASTEXITCODE -ne 0) { Erro "A compilacao falhou. Nada foi gravado." }

$bin = Join-Path $build "ypod_bridge.ino.bin"
if (-not (Test-Path $bin)) { Erro "Compilou mas nao achei o .bin em $build" }
$kb = [math]::Round((Get-Item $bin).Length / 1024)
Write-Host "   binario: $kb KB" -ForegroundColor DarkGray

if ($SoCompilar) { Write-Host "`n  Compila. Nada gravado (-SoCompilar).`n" -ForegroundColor Green; exit 0 }

# --- 4. gravar --------------------------------------------------------------
if ($Porta) {
    Passo "Gravando por cabo em $Porta"
    & $acli upload --fqbn $FQBN --port $Porta --input-dir $build $sketch
    if ($LASTEXITCODE -ne 0) { Erro "A gravacao por cabo falhou." }
} else {
    # espota.py vem junto com o core do ESP32
    $core = Join-Path $env:LOCALAPPDATA "Arduino15\packages\esp32\hardware\esp32"
    $espota = Get-ChildItem -Path $core -Filter "espota.py" -Recurse -ErrorAction SilentlyContinue |
              Sort-Object FullName -Descending | Select-Object -First 1
    if (-not $espota) { Erro "Nao achei o espota.py em $core" }

    Passo "Gravando pela rede em $Alvo"
    Write-Host "   (a placa precisa estar na mesma rede que este PC)" -ForegroundColor DarkGray
    & python $espota.FullName -i $Alvo -p 3232 -a $senha -f $bin
    if ($LASTEXITCODE -ne 0) {
        Erro ("A gravacao pela rede falhou. Causas comuns, em ordem:`n" +
              "   1. voce nao esta na mesma rede da ponte (abra http://$Alvo)`n" +
              "   2. a senha do OTA mudou: quem autentica e o firmware ANTIGO,`n" +
              "      que esta rodando agora, nao o que voce acabou de compilar`n" +
              "   3. a ponte esta no portal de configuracao (LED roxo): ali o OTA nao sobe")
    }
}

Write-Host "`n  Gravado. A ponte reinicia sozinha em alguns segundos." -ForegroundColor Green
Write-Host "  Confira em http://$Alvo -- e lembre que o pod nao re-enumera" -ForegroundColor DarkGray
Write-Host "  sozinho depois do boot: pode precisar replugar a USB dele.`n" -ForegroundColor DarkGray
