# YPOD — monitoramento de qualidade do ar (Grupo 5, Internet Industrial das Coisas, PUCPR)

Resumo operacional do projeto: o mínimo para continuar sem recomeçar.
(Assistentes de código leem este arquivo sozinhos no início da sessão.) O histórico longo está no
[ESTADO.md](ESTADO.md); como rodar cada parte, no [README.md](README.md).

## O que o sistema é

```
YPOD ──USB──> ESP32-S3 ──Wi-Fi──> PC ──┬─> SQLite + painel web (hoje)
(sensores)     ("a ponte")              └─> MQTT ──> InfluxDB ──> Grafana (servidor da escola)
```

O pod mede e cospe uma linha CSV por segundo na própria USB. O ESP32-S3 é **host
USB** daquela porta: ele lê a mesma serial que um PC leria, acrescenta os dois
sensores de vento que são dele, guarda num buffer e entrega pela rede. O PC grava
em SQLite e desenha o painel. Depois isso vai para um servidor da escola (Docker
+ InfluxDB + Grafana) pelo caminho MQTT, que já está escrito e testado.

**O ESP existe para o pod não ficar preso por cabo ao PC.** É a razão de toda a
complexidade de host USB; se alguém sugerir "só liga o pod no PC", isso desfaz o
objetivo (mas funciona como plano B — ver `--source serial`).

## Como rodar

No PC da escola (desde 24/10) o projeto esta na Área de Trabalho do usuário da
máquina, em `...\PROJETO YPOD\teste` (dentro do OneDrive institucional),
e o servidor sobe pelo `iniciar_ypod.ps1` (religa se cair, log em
`data\servidor.log`, uma instancia so). Na mao:

```
python ypod_monitor.py --source net --nuvem-url https://ypod-api.ypod-sm.workers.dev
```

Use `--source net` sempre que a ponte puder estar desligada no inicio: sem ele,
nao achando a ponte, o servidor cai para o USB e fica la.

> **Em qualquer outro PC, rode SEM `--nuvem-url`:**
>
> ```
> python ypod_monitor.py --source net
> ```
>
> Só o PC da escola alimenta o site público. Dois PCs enviando fazem os valores
> "ao vivo" do site alternarem entre os dois, e com a ponte falsa (`tools/
> ponte_falsa.py`) isso publicaria dado inventado no ar.

Ela acha a ponte sozinha: beacon UDP ou, se o firewall barrar o beacon (é o caso
na rede da escola), mDNS. Painel em <http://localhost:8000>. Só
precisa de Python 3 — `pyserial` e `paho-mqtt` são opcionais (modo USB direto e
modo MQTT). O Chart.js já está em `static/`, então o painel funciona sem internet.

Flags que importam:

| flag | para quê |
|---|---|
| `--source net\|mqtt\|serial` | de onde vem o dado (`auto` é o padrão) |
| `--host IP` | ponte num IP fixo, se a descoberta falhar |
| `--broker mqtt://host:1883` | assina o tópico em vez de ler o TCP |
| `--gravacao SEG` | intervalo de gravação no banco (padrão 60; `0` grava tudo) |
| `--list` | lista as portas COM |

**A gravação é por minuto, de propósito.** A tela mostra tempo real, mas o banco
recebe a média de 60 s, com timestamp no meio do intervalo. Foi pedido assim. Se
alguém "otimizar" isso para gravar cada leitura, quebrou o requisito.

Diagnóstico da ponte, sem cabo: <http://ypod-bridge.local>. Essa página diz na
hora se o problema é rede ou é o pod — **olhe ela antes de teorizar.**

## Hardware exato

| | |
|---|---|
| Pod | **Arduino UNO R4 Minima** — USB `2341:0069`, 9600 baud, fw `YPOD_V4.2.1`, pod `YPODV6` |
| Ponte | **YD-ESP32-S3** (clone VCC-GND da DevKitC-1), módulo **N16R8**: 16 MB flash, 8 MB PSRAM OPI |
| | porta `COM` = CH343 (`1A86:55D3`) → gravação e monitor serial |
| | porta `USB` = OTG nativa (GPIO19/20) → **é aqui que o pod entra** |
| Jumper `USB-OTG` | soldado (liga os VBUS dos dois conectores) |
| Jumper `IN-OUT` | **fechado** |
| Alimentação de campo | LM2596 ajustado em **5,0 V** → pino `5V` da placa. Nada no `COM`. |
| Direção do vento | **AS5600**, I²C 0x36, SDA=GPIO8, SCL=GPIO9, 3,3 V |
| Velocidade do vento | tacômetro óptico, D0 em **GPIO5**, contado por PCNT, **12 furos** |

O diagrama elétrico (`Diagrama_Esquematico_Grupo5 (7).pdf`) **está
desatualizado**: diz "DevKitC-1" e descreve a alimentação errada. Corrigir.

### Nunca

- **Não alimente pelas duas USB ao mesmo tempo.** Com o `USB-OTG` soldado, `COM`
  e `USB` se realimentam. Já quebrou a transferência de dados com a enumeração
  aparentemente normal — sintoma confuso, causa boba.
- **Não resete a placa por DTR/RTS via pyserial.** Trava esta placa. Use
  `esptool --after hard_reset` ou o próprio upload.
- **Não ponha sinal de 5 V num GPIO.** O S3 aceita 3,6 V no máximo.
- **Não mexa em `CPU_MHZ` nem em `WIFI_ECONOMIA`** (`ponte.h`). 160 MHz ou
  economia de rádio **quebram a enumeração USB**: compila, conecta no Wi-Fi, e o
  pod nunca monta. Testado em hardware. Economiza 0,3 W e custa o projeto.

## Painel publico (Cloudflare)

```
ypod_monitor.py --POST /api/push a cada 15 s--> Worker ypod-api --> D1 db-ypod_sm
site https://ypod.ypod-sm.workers.dev ----GET /api/*----------^
```

- `worker/`: API. Responde **o mesmo formato** do servidor local, por isso o
  mesmo `static/dashboard.html` serve nos dois (a URL da API vai na meta tag
  `ypod-api`). Mudou um, mude o outro. Publicar: `wrangler deploy` em `worker/`.
- O PC manda as medias de minuto (fila = banco local, ponteiro `nuvem_id` na
  tabela `state`) + um retrato do `/api/live`. Escrita exige o secret
  `INGEST_TOKEN`, cujo valor esta na variavel de usuario `YPOD_NUVEM_TOKEN`.
- Site: `python tools/gerar_site.py https://ypod-api.ypod-sm.workers.dev` e
  `wrangler deploy -c wrangler.site.jsonc`. So `site/` e publicado.
- Cota gratis do D1: 100 mil linhas escritas/dia. Por isso so medias de minuto
  (`--gravacao 0` estoura) e janelas > 2 dias leem a tabela `hora`.
- Node portatil (sem admin) em `%LOCALAPPDATA%\Programs\node-v24.21.0-win-x64`,
  com `wrangler` 4.149 global. Login: `wrangler login`.
- `wrangler dev`/D1 local falha com `SQLITE_CANTOPEN` em caminho longo (>260
  chars, como esta pasta do OneDrive): teste local numa pasta curta.
- `wrangler secret put` lendo de pipe do PowerShell grava `\r\n` junto (401).
  Use `wrangler secret bulk arquivo.json`.
- O subdominio `workers.dev` a Cloudflare cria sozinha com o nome da conta; foi
  trocado para `ypod-sm` **pelo painel** (a API nao deixa trocar). Trocar de
  novo quebra todas as URLs publicadas.
- `wrangler login` deu erro de CSRF na primeira vez: terminal normal (nao
  admin), com uma aba so, resolveu.
- Scripts que chamam `python` podem pegar o atalho falso da Microsoft Store, que
  nao e um Python. O `iniciar_ypod.ps1` aponta direto para a instalacao real.

### Se a coleta parar (ordem de diagnostico)

1. **Site diz "PC da escola offline"** → abrir `data/servidor.log` naquele PC.
2. **Log diz "procurando a ponte"** → abrir <http://ypod-bridge.local> e olhar
   o LED da ponte.
3. **Ponte no ar mas `esperando o pod (0 bytes)`** → desconectar e reconectar a
   USB do pod (ele nao re-enumera sozinho depois que a ponte reinicia).
4. **Servidor travado** → Gerenciador de Tarefas, finalizar o `python.exe`; o
   `iniciar_ypod.ps1` religa em 15 s. Se o proprio script parou, dois cliques no
   atalho "YPOD servidor" (`Win+R` → `shell:startup`).

Nessa ordem: cada passo so faz sentido se o anterior estiver descartado.

## Firmware da ponte (`firmware/ypod_bridge/`)

`ponte.h` concentra toda a configuração. Módulos são **`.cpp`, nunca `.ino`** (o
pré-processador do Arduino gera protótipos antes dos `#include` dos secundários).
`LINHA_MAX` tem esse nome porque `LINE_MAX` é macro do POSIX e não compila.

Compilar e gravar pelo ar (a placa não precisa estar na mesa):

```
arduino-cli compile --fqbn esp32:esp32:esp32s3:FlashSize=16M,PartitionScheme=app3M_fat9M_16MB,PSRAM=opi,CDCOnBoot=default,USBMode=hwcdc --build-path BUILD firmware/ypod_bridge

python %LOCALAPPDATA%\Arduino15\packages\esp32\hardware\esp32\3.3.12\tools\espota.py -i IP-DA-PONTE -p 3232 -a ypod -f BUILD\ypod_bridge.ino.bin
```

O core `esp32:esp32@3.3.12` está em `~/AppData/Local/Arduino15`. O `arduino-cli`
**não** está instalado de forma permanente — baixe de novo se precisar.

O USB host usa a biblioteca `USBHost`/`USBHostSerial` **que já vem no core desde
a 3.3.0**. Se a busca na web disser que "o core Arduino não suporta USB host no
ESP32-S3", está desatualizada (era verdade até a 3.2) — e foi essa informação que
custou ~300 linhas de driver manual escrito à mão, pra nada. Antes de escrever
driver, olhe `libraries/USB/examples/` do core instalado.

### Buffer: detalhe recente + memória longa

O pod manda ~27 linhas por minuto; o PC guarda **só a média do minuto**. Guardar
as 27 no buffer seria proteger um detalhe que será descartado na chegada. Então
a ponte divide as mesmas 20 000 vagas de PSRAM:

```
ring (detalhe) ....... 1 600 linhas cruas ........ ~1 hora
anelMin (minutos) .... 18 400 médias de minuto ... ~12,7 dias
```

Queda curta (PC reiniciou, Wi-Fi piscou) volta com fidelidade total; queda longa
volta com o que o PC guardaria de qualquer jeito. **De ~12 h para ~13 dias na
mesma memória.** Desliga em `ponte.h` com `COMPACTAR_BUFFER 0`.

A entrada compactada ocupa o número da **última linha do minuto** que ela
resume; os números intermediários deixam de existir. Isso não exigiu mudar o
protocolo: TCP e MQTT já pulavam número ausente, que é o que acontece com linha
que caiu do buffer.

**A ponte agora conhece os índices dos campos** (`CAMPOS_MEDIA` em `ponte.h`),
porque precisa deles para tirar a média — antes ela só repassava texto. São dois
lugares que precisam concordar com `FIELDS` do servidor, e o `contrato_test.py`
compara os dois. A direção do vento é somada como vetor também aqui, pelo mesmo
motivo que no servidor.

### Segredos

A senha do OTA **não está no repositório** — ela vem de
`firmware/ypod_bridge/segredos.h`, que está no `.gitignore`. Quem clonar copia
`segredos.exemplo.h` para `segredos.h` e preenche; sem isso a compilação para
com uma mensagem dizendo o que fazer, de propósito. A senha do Wi-Fi nunca
esteve em arquivo nenhum (portal + NVS), e o token da nuvem é secret do Worker.

### Página de configuração

<http://ypod-bridge.local/config> — protegida pela senha do portal (usuário
`ypod`). O que se ajusta ali fica na **NVS** e sobrevive a reinício e a nova
gravação de firmware; os `#define` de `ponte.h` são só padrão de fábrica:

| ajuste | chave na NVS |
|---|---|
| norte da biruta | `norte` |
| furos do disco do tacômetro | `furos` |
| fator `k` (rpm → m/s) | `tacok` |
| senha do portal | `apsenha` |

Mais os botões de abrir o portal agora e reiniciar. A página de status (`/`)
continua aberta e **só de leitura** — é ela que responde "o problema é a rede ou
é o pod?", e pôr senha ali atrapalharia o diagnóstico.

### Rede sem cabo

A ponte não tem credenciais fixas: as de `ponte.h` são só padrão de fábrica, e o
que vale é o que está na NVS. Sem conseguir entrar em nenhuma rede por 30 s — ou
depois de 5 min caída — ela abre um portal:

```
rede: ypod-setup    senha: ypod1234    ->    http://192.168.4.1
```

O portal fecha sozinho em 5 min e tenta a rede salva de novo (revezamento: uma
queda curta não deixa a ponte presa no portal). **A coleta do pod continua
rodando em todos esses estados** — o buffer existe exatamente para isso.
Para esquecer a rede salva: segure `BOOT` nos 3 primeiros segundos ao ligar.

No celular, o Android às vezes volta para os dados móveis por ver que a rede do
portal não tem internet; aí o navegador não acha o `192.168.4.1`. Desligar os
dados móveis por um minuto resolve.

LED: azul piscando = procurando Wi-Fi · verde pulsando = recebendo do pod ·
amarelo = na rede mas sem pod · roxo fixo = portal · vermelho = falha.

## Formato da linha

O servidor lê os campos **por posição** (`FIELDS` em `ypod_monitor.py`), por isso
o vento entra no fim da linha do pod — campo novo no final é natural para ele.

Campos usados: 7 temp · 8 rh · 10 fig2600 · 11 fig2602 · 12 mox3 · 14 co ·
15 aux/O3 · 16 co2 · 17 pm1 · 18 pm25 · 19 pm10 · **20 vento_vel** · **21 vento_dir**.

Lixo que o pod emite e que o servidor filtra (não "conserte" removendo):

- `-999` e `9999` = inválido
- `>= 60000` = sensor desconectado. Ele manda **65535, 65534 e 64537**,
  alternando; filtrar só o 65535 deixa passar picos de 65 mil.
- datas impossíveis (`2000-84-90T00:03:00`) quando o pod está em mau estado
- o RTC do pod atrasa ~2 meses. O servidor grava a hora do PC **e** a do pod.

## Estado em 24/10/2026

Funcionando e verificado com hardware real: USB host lendo o pod, ponte → PC por
TCP com reenvio numerado, SQLite com resumo por minuto, painel com janela
histórica e intervalo por data/hora, MQTT QoS 1 contra broker real, portal de
rede, OTA, e o **painel público na Cloudflare** alimentado pelo PC da escola. Os
testes em `testes/` passam sem hardware.

Em aberto:

1. **O pod não re-enumera depois que o ESP reinicia.** Hoje exige desconectar e
   reconectar a USB do pod. Sintoma na página de status: `esperando o pod
   (0 bytes)` com o Wi-Fi perfeito. Correção pendente: reiniciar a pilha USB host
   sozinha depois de N minutos sem byte nenhum.
2. **Norte do vento não calibrado.** O ímã do AS5600 **já é detectado** (323° no
   teste de 24/10). Falta apontar a biruta para o norte e clicar "definir norte
   na posição atual" em <http://ypod-bridge.local/config> (grava na NVS).
3. **`TACO_K` = 0**, publicando rpm cru. Converter para m/s exige calibrar contra
   um anemômetro de referência; fator inventado só esconde que o dado não vale.
4. **Watchdog do `loopTask` disparou duas vezes**, causa não identificada. A
   placa se recupera sozinha.
5. **Broker e InfluxDB da escola ainda não definidos.** Perguntar a política de
   retenção do bucket: se for curta, o histórico longo só existe no SQLite local.
   (A rede da escola **não** isola clientes: TCP PC↔ponte funciona.)
6. **Banco SQLite dentro do OneDrive.** Sincronizar um SQLite aberto pode
   travar o arquivo; para uso continuo, mover o projeto para fora (ex. `C:\YPOD`).
