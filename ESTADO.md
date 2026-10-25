# Estado do projeto YPOD — 24 de outubro de 2026

Documento de continuidade. O resumo operacional está no [CLAUDE.md](CLAUDE.md);
este arquivo é o histórico com as decisões e o porquê
delas. Como rodar cada parte: [README.md](README.md).

> **A camada de nuvem foi construída no PC da escola na tarde de 24/10.** Painel
> público em <https://ypod.ypod-sm.workers.dev>, API em
> `ypod-api.ypod-sm.workers.dev` sobre um banco D1; o detalhe de operação está
> na seção "Painel público" do [CLAUDE.md](CLAUDE.md). O que está abaixo
> descreve a parte local, que continua sendo a base: a nuvem é alimentada
> por ela.

---

## Resumo de uma linha

**O sistema funciona ponta a ponta com o hardware real.** O que falta é
calibração dos sensores de vento, o endereço do servidor da escola, e uma
irritação conhecida: o pod precisa ser replugado quando o ESP reinicia.

```
pod ──USB──> ESP32-S3 ──Wi-Fi──> PC ──┬─> SQLite + painel local
                                       └─> MQTT ──> InfluxDB ──> Grafana (escola)
```

---

## O que está pronto e verificado em hardware

### Servidor no PC (`ypod_monitor.py`)

- Lê o pod por **TCP** (a ponte), **MQTT** ou **USB direto**; parseia 13 campos;
  grava em SQLite.
- Painel web com valores atuais, janela histórica, **intervalo por data/hora**,
  export CSV e tabela.
- **Gravação por minuto**: a tela é tempo real, o banco recebe a média de 60 s
  com timestamp no meio do intervalo. Requisito do projeto, não detalhe de
  implementação.
- Resumo por minuto (`roll_min`) que faz consulta de 6 meses responder em menos
  de 1 s — testado com 5,2 milhões de leituras, banco de 509 MB.
- Saída para InfluxDB com fila durável: se o Influx cair, nada se perde.
- Inserção idempotente por `src_key` (`boot:seq` ou `min:<epoch>`): reconexão não
  duplica linha.

Leitura real, 22/10:

```
SERVIDOR: lidas 70 | gravadas 3 | descartadas 0
Temperatura 19,68 °C | Umidade 80,26 % | CO₂ 367 ppm | MOx 3: sem dado
3.134 registros no banco
```

### Firmware da ponte (`firmware/ypod_bridge/`)

- **USB host lendo o pod** pela biblioteca oficial do core (ver abaixo).
- PSRAM de 8 MB reconhecida → 20 000 vagas, divididas em **1 600 linhas cruas
  (~1 h de detalhe) + 18 400 médias de minuto (~12,7 dias)**. Se a alocação
  falhar, cai sozinho para 400 linhas cruas, sem compactação.
- TCP com reenvio numerado: o PC pede "a partir da linha N" e não perde nem
  duplica. Validado com hardware — a ponte entregou 23 linhas de buffer e depois
  passou a tempo real.
- MQTT **QoS 1 com confirmação** (PUBACK), pelo cliente do ESP-IDF.
- Portal de configuração de rede com abertura automática e revezamento.
- OTA (`espota.py`) — usado com sucesso várias vezes, sem tocar na placa.
- Watchdog em dois níveis: o do chip (loop travado, 30 s) e os de aplicação
  (10 min sem pod, 15 min sem Wi-Fi). O de aplicação **não** reinicia enquanto o
  portal está aberto: ali estar fora da rede é o estado esperado, e reiniciar só
  fecharia o portal na cara de quem está configurando.
- Sensores de vento (AS5600 + tacômetro por PCNT), acrescentados ao fim da linha
  do pod.

### Testes automatizados (`testes/`)

Rodam sem hardware. Todos passando:

| teste | cobre |
|---|---|
| `contrato_test.py` | o formato que o firmware emite vs. o que o servidor espera (lê os `printf` do próprio `.ino`) |
| `net_test.py` | TCP, backlog, dedupe, reconexão |
| `mqtt_test.py` | MQTT com broker real, QoS 1, dedupe, queda do broker |
| `influx_test.py` | line protocol, backfill quando o Influx cai |
| `volume_test.py` | 5,2 M leituras, modo USB, import de CSV |
| `gravacao_test.py` | acumulador por minuto: média, timestamp no meio, `--gravacao 0` |

`testes/voltest` guarda ~574 MB de banco gerado pelo teste de volume. É
descartável — o teste refaz.

---

## Decisões e o porquê

**Por que host USB, e não fio no TX do pod.** No UNO R4 Minima a USB **não** passa
por D0/D1. Com `Serial.print()` no firmware do pod, o pino D1 fica mudo. Ler pela
UART exigiria regravar o pod (`Serial1.begin(9600)` espelhando os prints) — o que
ninguém quer fazer num equipamento em uso. Host USB lê exatamente a mesma porta
que o PC lê, sem tocar no pod.

**Por que o timestamp é "há quantos ms".** A ponte não tem RTC. Em vez de mandar
uma hora que ela não sabe, manda a idade da linha em milissegundos; o PC converte
usando o próprio relógio. Isso mantém correto o horário das linhas que ficaram
horas no buffer durante uma queda de rede.

**Por que o cliente MQTT do ESP-IDF e não o PubSubClient.** O PubSubClient dos
tutoriais só publica em QoS 0, sem confirmação: numa rede instável você perde
linha e nunca sabe. O cliente do IDF já vem no core e faz QoS 1.

**Por que a gravação é por minuto.** Pedido explícito. Uma leitura por segundo
durante meses é volume sem informação para este uso; a média de 60 s mantém o
banco utilizável e o painel continua em tempo real porque a tela lê do buffer
vivo, não do banco.

**Por que o vento vai no fim da linha.** O servidor lê por posição. Campo novo no
fim é a única mudança que não exige mexer no parser nem no que já está gravado.

**Por que PCNT e não interrupção.** O contador de pulsos é periférico de
hardware: não perde pulso quando a CPU está ocupada com USB host e Wi-Fi ao mesmo
tempo. Por interrupção de software perderia, e a velocidade sairia baixa justo
nos momentos de mais carga.

**Por que `TACO_K` = 0.** Converter rpm em m/s sem calibrar contra um anemômetro
de referência produz um número que *parece* velocidade sem ser. Melhor publicar
rpm cru e deixar claro que falta calibrar.

---

## O USB host — resolvido, e como

O ESP lê o pod pela USB usando a biblioteca **`USBHost`/`USBHostSerial` que vem no
core do ESP32 desde a 3.3.0**. O `fonte_usb.cpp` tem ~40 linhas.

```
=== USB host oficial: lendo o YPOD ===
>>> POD MONTADO! baud=115200
[YPOD 1] 2026-07-31T10:53:37,,,YPODV6,YPOD_V4.2.1,,,28.84,61.27,,2577,468,65535,,4496,2868,1390.00,16,28,28,
```

**Por que demorou dias:** uma busca na web disse que "o core Arduino não suporta
USB host no ESP32-S3". Era verdade até a 3.2 e estava desatualizada na 3.3. Foram
escritas ~300 linhas de driver manual em ESP-IDF, para nada — o exemplo
`USBHostSerial.ino` estava instalado no disco o tempo todo, em
`~/AppData/Local/Arduino15/packages/esp32/hardware/esp32/3.3.12/libraries/USB/examples/`.

**Lição:** antes de escrever driver, olhar os exemplos do core instalado. E
desconfiar de resultado de busca sobre suporte de biblioteca sem conferir a
versão que está na máquina.

### O requisito de VBUS

Dispositivo USB **autoalimentado** só pode puxar o pull-up de D+ depois de
detectar VBUS (Cypress AN15813). É por isso que o jumper `USB-OTG` existe nesta
placa: ele liga o VBUS do conector `COM` ao do conector `USB`, dando os 5 V que o
pod espera ver para se apresentar.

Consequência: com o jumper soldado, **nunca** ligar `COM` e `USB` em
computadores ao mesmo tempo. Já aconteceu, e o sintoma foi confuso — a enumeração
funcionava e a transferência de dados não.

---

## Armadilhas que custaram tempo (não repetir)

1. **Resetar o ESP por DTR/RTS via pyserial trava esta placa.** Use o `esptool`
   (`--after hard_reset`) ou o próprio upload. Horas perdidas achando que a placa
   estava morta quando era o reset que a derrubava.
2. **`LINE_MAX` é macro do POSIX** (`limits.h`). Está renomeado para `LINHA_MAX`.
3. **Módulos de um sketch devem ser `.cpp`, não `.ino`.** O pré-processador do
   Arduino gera protótipos antes dos `#include` dos arquivos secundários.
4. **`CPU_MHZ` 160 e `WIFI_ECONOMIA` 1 quebram a enumeração USB.** Compila,
   conecta no Wi-Fi, e o pod nunca monta. Economiza 0,3 W e custa a leitura.
   Testado em hardware. Está documentado dentro do `ponte.h` com aviso.
5. **O sensor desconectado manda 65535, 65534 *e* 64537.** O filtro é `>= 60000`.
   Filtrar só o 65535 deixava passar picos de 65 mil no gráfico.
6. **O pod emite datas impossíveis** (`2000-84-90T00:03:00`) quando está em mau
   estado. O parser rejeita a linha em vez de gravar lixo.
7. **O RTC do pod atrasa ~2 meses.** O servidor usa a hora do PC e grava as duas.
8. **Portal AP instável:** estava em `AP_STA` e varria as redes dentro do handler
   da página. A parte cliente trocava de canal e derrubava o celular a cada
   tentativa de reconexão. Hoje é **AP puro** e a varredura acontece **uma vez,
   antes** de o AP subir.
9. **A primeira versão do portal bloqueava a coleta** — o loop retornava cedo e
   desligava a leitura do pod, justo quando o buffer mais importa. Hoje
   `vento_loop()` e `fonte_loop()` vêm **antes** de qualquer coisa de rede.
10. **Hotspot de celular isola os clientes.** Sintoma: mDNS funciona (multicast),
    TCP não (unicast), ARP sem entrada para o ESP. Com MQTT e broker isso deixa
    de importar.
11. **O pod plugado no PC em vez do ESP** aconteceu três vezes, e em todas eu
    inventei teoria (cabo, contato, laço de terra) em vez de abrir a página de
    status, que diz em uma linha se o pod está montado.
12. **Mudei PSRAM e Partition Scheme ao mesmo tempo** e concluí, errado, que a
    PSRAM estava quebrada. Uma variável por vez.
13. **O usuário não vê a saída do comando enquanto ele roda** — só no fim. Peça a
    ação física *antes* de disparar o comando, nunca durante.

---

## Em aberto

### 1. O pod não re-enumera depois que o ESP reinicia

Sintoma, na página de status, com o Wi-Fi perfeito:

```
status do pod ..... sem dados
fonte ............. USB host - esperando o pod (0 bytes)
sinal Wi-Fi ....... -54 dBm
```

Hoje a solução é desconectar e reconectar a USB do pod (só o cabo do pod, com o
ESP já ligado). Isso aparece sempre que a placa reinicia — e salvar a rede no
portal reinicia.

**Correção pendente**, já desenhada e não implementada: contar o tempo sem byte
nenhum e, passados N minutos, derrubar e subir a pilha do host USB
(`USBHost.end()` / `begin()`) em vez de esperar o watchdog. Resolveria sem
ninguém ir até o equipamento.

### 2. Calibração dos sensores de vento

- **O ímã do AS5600 já é detectado** (323° no teste de 24/10). O bit MD (status
  0x0B, bit 5) em zero é o que faz o servidor receber o campo vazio — de
  propósito: ângulo sem ímã não significa nada.
- **Norte não definido.** Aponte a biruta para o norte e clique "definir norte
  na posição atual" em <http://ypod-bridge.local/config>. Grava na NVS (chave
  `norte`) e sobrevive a reboot e a nova gravação de firmware.
- **Fator `k` = 0**, publicando rpm cru. Precisa de um anemômetro de referência.
  Hoje se ajusta na mesma página, sem recompilar (NVS, chave `tacok`).
- **Confira os furos do disco.** Com o número errado a velocidade sai
  proporcional, mas com a escala toda errada. Também na página de configuração
  (NVS, chave `furos`); os `#define` de `ponte.h` são só o padrão de fábrica.

### 3. Watchdog do `loopTask`

Disparou duas vezes, causa não identificada. A placa se recupera sozinha e a
coleta volta. Como o buffer se perde no reinício, o servidor vê um buraco de
alguns segundos. Não foi investigado.

### 4. Servidor da escola

A rede da escola **não** isola clientes: mDNS e TCP PC↔ponte funcionam (testado
em 24/10). Só o beacon UDP não chega, provavelmente barrado pelo firewall do
Windows — não atrapalha, porque o mDNS acha a ponte.

O "servidor da escola" é um PC comum. Toda a coleta depende de ele ficar ligado,
e o buffer da ponte cobre ~13 dias desde a compactação de 24/10 (antes eram
12 h): um fim de semana de PC desligado deixou de ser um buraco.

Falta definir broker MQTT e InfluxDB. **Perguntar a política de retenção do
bucket**: se for curta, o histórico longo só existe no SQLite local e no D1, e
isso muda o que se pode prometer na apresentação.

### 5. Diagrama elétrico

`Diagrama_Esquematico_Grupo5 (7).pdf` está desatualizado:

- diz **DevKitC-1**, mas o que existe é um **YD-ESP32-S3** (os jumpers `USB-OTG`
  e `IN-OUT` são dele, a DevKitC não tem);
- a seção de alimentação precisa dizer **LM2596 em 5,0 V → pino `5V`**, com o
  `IN-OUT` fechado e nada no `COM`;
- os dois sensores de vento precisam aparecer com os pinos reais (GPIO8/9 e
  GPIO5) e a nota de que são **3,3 V**, nunca 5 V.

---

## Arquivos

```
CLAUDE.md                    contexto carregado automaticamente em cada sessão
ESTADO.md                    este arquivo: histórico e decisões
README.md                    como rodar cada parte

ypod_monitor.py              servidor: fontes, banco, API, Influx
static/dashboard.html        painel
static/chart.umd.min.js      Chart.js local (painel funciona offline)
data/ypod.db                 banco SQLite

firmware/ypod_bridge/
  ponte.h                      TODA a configuração: rede, MQTT, pinos, limites
  ypod_bridge.ino              Wi-Fi, buffer, TCP, página de status, OTA, watchdog
  fonte_usb.cpp                USB host (biblioteca oficial do core)
  fonte_uart.cpp               alternativa pelo pino TX (exige regravar o pod)
  saida_mqtt.cpp               publicação MQTT QoS 1
  config_rede.cpp              portal de rede, NVS, abertura automática
  pagina_config.cpp            /config: norte, calibração, senha, reiniciar
  compactar.cpp                média de minuto para o buffer longo
  sensores_vento.cpp           AS5600 + tacômetro (PCNT)
  led_status.cpp               LED RGB de estado
  segredos.exemplo.h           modelo; copie para segredos.h (fora do git)
firmware/YPOD_USB_Leitor/    sketch de teste de USB host (de outra sessão)

worker/                      a API na Cloudflare
  src/index.js                 Worker: /api/* no mesmo formato do servidor local
  schema.sql                   banco D1 (resumo por hora, contadores)
  wrangler.toml                configuração do deploy
site/                        o painel estático publicado (gerado, não editar)
tools/gerar_site.py          monta site/ a partir de static/dashboard.html
publicar_site.ps1            publica site/ na Cloudflare
iniciar_ypod.ps1             sobe o servidor e religa se cair (PC da escola)
wrangler.site.jsonc          deploy do Worker de arquivos estáticos

tools/ponte_falsa.py         simula a ponte, para testar o PC sem hardware
tools/broker_local.py        broker MQTT local, para testar sem o da escola
testes/                      testes automatizados (sem hardware)
```

**O painel é um arquivo só:** `static/dashboard.html` serve tanto o servidor
local quanto o site público — a diferença é a meta tag `ypod-api`, preenchida
pelo `gerar_site.py`. Editou o painel, rode o gerador e publique, senão o site
público fica atrasado em relação ao local.

### O que não precisa ser copiado para outra máquina

- `testes/voltest` (~574 MB) — banco gerado pelo teste de volume
- `firmware/ypod_bridge/build` (~51 MB) — compilado, refeito onde for compilar
- `__pycache__`

Isso é 625 dos 650 MB da pasta. O projeto de verdade são ~25 MB.
