# YPOD Monitor

Mini servidor que recebe os dados do YPOD, guarda tudo num banco e mostra num
painel web — valores atuais, janela histórica e qualquer intervalo de data/hora.

```
YPOD ──USB──> ESP32-S3 ──Wi-Fi──┐
             ("a ponte")        ├──> ypod_monitor.py ──> navegador
YPOD ─────── USB (COM5) ────────┘        (PC)
```

Os dois caminhos funcionam; o ESP32 só serve para o pod não precisar ficar preso
ao PC por cabo. O servidor descobre sozinho qual está disponível.

O ESP32-S3 é **host USB** da porta do pod: ele lê a mesma serial que um PC
leria, sem tocar no firmware do pod. (Há também um caminho por UART em
`fonte_uart.cpp`, mas ele exige regravar o pod — no UNO R4 a USB não passa por
D0/D1, então com `Serial.print()` o pino fica mudo.)

> **Antes de compilar o firmware:** copie `firmware/ypod_bridge/segredos.exemplo.h`
> para `segredos.h` na mesma pasta e preencha a senha do OTA. Esse arquivo está
> no `.gitignore` e não vem no repositório; sem ele a compilação para com uma
> mensagem dizendo isso.

## Rodar o servidor

```
pip install pyserial
python ypod_monitor.py
```

Abre em <http://127.0.0.1:8000>. Sem argumento nenhum ele procura a ponte ESP32
na rede por 4 segundos; se não achar, usa a USB (`COM5`).

```
python ypod_monitor.py --source net             # só rede (TCP direto)
python ypod_monitor.py --host 192.168.0.42      # ponte num IP fixo
python ypod_monitor.py --broker mqtt://192.168.0.10:1883   # via MQTT
python ypod_monitor.py --source serial --port COM7
python ypod_monitor.py --list                   # portas seriais da máquina
python ypod_monitor.py --echo                   # ver as linhas cruas no terminal
```

Três transportes, mesmo banco e mesmo painel: **MQTT** (quando `--broker` é dado),
**TCP direto** com a ponte na mesma rede, ou **USB** no PC. Veja [MQTT](#mqtt).

O painel escuta em todas as interfaces: outro PC ou o celular na mesma rede
abrem em `http://<ip-do-seu-pc>:8000`.

**No modo USB, feche o Serial Monitor do VSCode** — no Windows só um programa por
vez abre a COM.

## Montar a ponte ESP32

Firmware em [firmware/ypod_bridge/](firmware/ypod_bridge/). Toda a configuração
fica em `ponte.h`; os outros arquivos você só mexe para mudar comportamento.

**Não preencha `WIFI_SSID`/`WIFI_PASS`** — eles são padrão de fábrica e o certo
é deixar vazios. A rede que vale é escolhida num portal e gravada na placa.

### Configuração da IDE (ou do FQBN)

Compila igual com a configuração errada e só quebra depois, no hardware:

```
Placa .............. ESP32S3 Dev Module
Flash Size ......... 16MB (128Mb)
PSRAM .............. OPI PSRAM       ← sem isto, buffer de 400 linhas, não 20.000
Partition Scheme ... 16M Flash (3MB APP/9.9MB FATFS)
USB Mode ........... Hardware CDC and JTAG
USB CDC On Boot .... Disabled
```

O core precisa ser **esp32 3.3.0 ou mais novo**: é de onde vem a biblioteca de
USB host. Em versão anterior o projeto nem compila.

### Nunca

- **Não alimente pelas duas USB ao mesmo tempo.** Com o jumper `USB-OTG`
  fechado, `COM` e `USB` se realimentam. Já quebrou a transferência de dados
  com a enumeração parecendo normal — sintoma confuso, causa boba.
- **Não resete a placa por DTR/RTS via pyserial.** Trava esta placa. Use
  `esptool --after hard_reset` ou o próprio upload.
- **Não ponha sinal de 5 V num GPIO.** O ESP32-S3 aceita 3,6 V no máximo, e o
  pod é 5 V.
- **Não mexa em `CPU_MHZ` nem em `WIFI_ECONOMIA`.** 160 MHz ou economia de
  rádio **quebram a enumeração USB**: compila, conecta no Wi-Fi, e o pod nunca
  monta. Economiza 0,3 W e custa a leitura do pod.

### Os sensores de vento

São da ponte, não do pod, e entram no fim da mesma linha:

| | |
|---|---|
| Direção | **AS5600**, I²C 0x36, SDA `GPIO8`, SCL `GPIO9`, **3,3 V** |
| Velocidade | tacômetro óptico, D0 em `GPIO5`, contado por hardware (PCNT) |

Contado por PCNT e não por interrupção de software porque o periférico não
perde pulso quando a CPU está ocupada com USB host e Wi-Fi ao mesmo tempo.

### O pod entra pela USB do ESP32

O pod é um **Arduino UNO R4 Minima** (identificado pelo USB: VID 2341 / PID
0069, o id oficial dessa placa no `boards.txt` da Arduino). Isso descarta o
caminho de pendurar um fio no pino TX: no R4 a USB é nativa do chip RA4M1 e
**não passa por D0/D1** — isso só valia no UNO R3, que tinha um ATmega16U2
fazendo a ponte. Hoje `Serial` vai só para a USB-C e `Serial1` só para os pinos;
se o firmware do pod usa `Serial.print()`, o D1 fica mudo.

Então a ponte lê o pod **pela USB**, exatamente como o PC faz hoje: o ESP32-S3
tem controlador USB OTG e funciona como host. De quebra, some a questão dos 5 V.

**Ligação na YD-ESP32-S3** (a placa com dois USB-C marcados `COM` e `USB`).
Cuidado para não confundir: a ESP32-S3-DevKitC-1 *oficial* tem dois **micro**-USB
e nenhum jumper. Duas USB-C com essa serigrafia é a YD-ESP32-S3 (VCC-GND
Studio), um clone.

O conector `USB` é o OTG nativo (GPIO19 = D−, GPIO20 = D+) e funciona como host.
O único detalhe: de fábrica ele não *entrega* os 5 V — entre o VBUS do conector
e o rail de 5 V tem um diodo, então energia entra por ali mas não sai. E é o
VBUS que avisa ao pod que existe um host: um device USB só liga o pull-up de D+
quando sente VBUS, mesmo tendo fonte própria. Sem isso o pod nem aparece, e o
sintoma é silêncio total — não erro.

A YD tem três jumpers de solda, **todos abertos de fábrica**:


| jumper | fechado, faz |
|---|---|
| `USB-OTG` | **liga os VBUS dos dois conectores USB-C** ← é esse que você quer |
| `IN-OUT` | leva o VBUS da USB até o 5Vin (caminho contrário; a documentação da placa avisa que, com fonte externa no 5Vin, ela pode ser realimentada pela USB) |
| `RGB` | nada a ver |

> **Como está montado hoje (24/10):** `USB-OTG` **soldado** e `IN-OUT`
> **fechado**. Em campo a placa é alimentada por um **LM2596 ajustado em 5,0 V**
> ligado ao pino `5V` do header — **nada no conector `COM`**. O texto abaixo
> descreve a montagem de bancada, para gravar com cabo.

Então: alimente e grave a placa pelo conector `COM`, e feche o `USB-OTG`. Esses
5 V passam para o conector `USB` e o pod enxerga o host. Com o jumper fechado os
dois VBUS ficam em paralelo — sem conflito aqui, porque o pod é *device* e só
consome; mas não plugue a porta `USB` em carregador ou hub ativo nesse estado.

**Não quer soldar?** Um conector USB-A fêmea avulso (4 pinos) resolve igual:

```
USB-A fêmea                placa
  VBUS(pino 1) ─────────→  pino 5V do header
  D−  (pino 2) ─────────→  GPIO19
  D+  (pino 3) ─────────→  GPIO20
  GND (pino 4) ─────────→  GND
```

e um cabo USB-A → USB-C daí até o pod. O pod puxa uns 40 mA, sobra folga.

O pod pode continuar na fonte dele: o UNO R4 faz a seleção entre a energia da
USB e a própria, e aí o VBUS serve só de aviso de presença.

**Opções da IDE** para o módulo ESP32-S3-N16R8 (16 MB flash, 8 MB PSRAM OPI):

```
Placa .............. ESP32S3 Dev Module
USB CDC On Boot .... Disabled          ← senão o core disputa o OTG
USB Mode ........... Hardware CDC and JTAG
Flash Size ......... 16MB (128Mb)
PSRAM .............. OPI PSRAM
Partition Scheme ... 16M Flash (3MB APP/9.9MB FATFS)
```

Módulo diferente (N8, N8R2…)? Ajuste Flash Size e PSRAM conforme o blindado —
marcar OPI PSRAM onde não existe impede o boot.

### Se preferir o fio no TX

Dá para trocar a fonte em `ponte.h` (`FONTE_DO_POD = FONTE_UART`), mas só
funciona se você puder acrescentar `Serial1.begin(9600)` ao firmware do pod e
espelhar os prints. Nesse caso o pod é 5 V (datasheet ABX00080: 5 V em todos os
pinos exceto o 3V3) e o ESP32-S3 aceita no máximo VDD + 0,3 V = 3,6 V na
entrada, então é obrigatório um divisor 10k/20k entre D1 e o GPIO18.

Gravado e ligado, `http://ypod-bridge.local` mostra uma página de diagnóstico
(última linha recebida, quantos PCs conectados, sinal Wi-Fi, buffer).

Como o PC acha a ponte sem você descobrir IP: ela manda um aviso UDP na rede a
cada 3 s, e também responde por mDNS. Qualquer um dos dois resolve.

**Se o PC ficar desligado**, a ponte segura as leituras num buffer circular e
reenvia quando o PC volta. Numa placa com 8 MB de PSRAM são 20 000 vagas: a
última hora linha por linha e o resto como média de minuto, o que cobre cerca
de 13 dias. Sem PSRAM, caem para 400 linhas
(~20 min). Cada linha é numerada, e
o PC pede "continue do número tal" — não perde nem duplica. Fora dessa janela, o
que passou se perde: a ponte não tem cartão SD.

### Gravar o firmware

Os arquivos de `firmware/ypod_bridge/` são **um sketch só**: o compilador junta
todos e produz um binário, e é ele que vai para a placa.

```
powershell -ExecutionPolicy Bypass -File tools\gravar_ponte.ps1
```

Compila e grava pela rede, na placa que atender por `ypod-bridge.local`. A senha
do OTA sai do `segredos.h`, então não se digita nada. Outras formas:

```
... -Alvo 192.168.0.50     grava num IP, se o nome não resolver
... -Porta COM7            grava por cabo, em vez de pela rede
... -SoCompilar            só confere que compila
```

O script é PowerShell. Fora do Windows, ou para entender o que ele faz, são
dois comandos:

```
arduino-cli compile --fqbn esp32:esp32:esp32s3:FlashSize=16M,PartitionScheme=app3M_fat9M_16MB,PSRAM=opi,CDCOnBoot=default,USBMode=hwcdc --build-path BUILD firmware/ypod_bridge

python <core>/tools/espota.py -i ypod-bridge.local -p 3232 -a <senha> -f BUILD/ypod_bridge.ino.bin
```

O `espota.py` vem junto com o core do ESP32 (em `~/.arduino15/packages/esp32/`
no Linux, `%LOCALAPPDATA%\Arduino15\packages\esp32\` no Windows). Para gravar
por cabo, troque o segundo comando por `arduino-cli upload --fqbn <o mesmo>
--port COM5 --input-dir BUILD firmware/ypod_bridge`.

**A primeira gravação de uma placa nova é sempre por cabo** — o OTA só existe
porque já há um firmware rodando que sabe receber. E o cabo vai no conector
`COM`, não no `USB`: o `USB` é a porta OTG, por onde entra o pod.

Quem autentica o OTA é o firmware **que já está na placa**. Trocar a senha no
`segredos.h` só passa a valer depois da gravação seguinte.

### Escolher a rede, sem cabo

A ponte não tem credenciais fixas. Se não entrar em nenhuma rede em 30 s — ou
se ficar 5 minutos caída depois de já estar no ar — ela levanta um ponto de
acesso:

```
rede: ypod-setup    senha: ypod1234    →    http://192.168.4.1
```

Escolha a rede, digite a senha, e ela reinicia já conectada. O portal fecha
sozinho em 5 min e tenta a rede salva de novo, para uma queda curta não deixar
a ponte presa nele. **A coleta do pod continua rodando em todos esses estados.**
Para esquecer a rede salva, segure `BOOT` nos 3 primeiros segundos ao ligar.

No Android, o celular às vezes volta para os dados móveis ao ver que a rede não
tem internet, e aí o navegador não acha o `192.168.4.1`. Desligar os dados
móveis por um minuto resolve.

**Duas coisas no portal parecem melhoráveis e não são:** o AP sobe em modo AP
puro, não `AP_STA` (senão a parte cliente troca de canal tentando reconectar e
derruba o celular), e a varredura de redes acontece uma vez antes de o AP
subir, nunca dentro da página. No `loop()`, ler o pod vem antes de qualquer
coisa de rede — a primeira versão retornava cedo durante o portal e desligava a
coleta justo quando o buffer mais importa.

### Ajustar a ponte depois de instalada

`http://ypod-bridge.local/config`, protegida pela senha do portal (usuário
`ypod`). O que se ajusta ali fica na **NVS** e sobrevive a reinício e a nova
gravação de firmware — os `#define` de `ponte.h` são só o padrão de fábrica:

| ajuste | para quê |
|---|---|
| norte da biruta | aponte ao norte e clique; o ângulo atual vira o zero |
| furos do disco | tem que bater com o disco de verdade |
| fator `k` | converte rpm em m/s; `0` publica rpm cru |
| senha do portal | a de fábrica está publicada aqui, então troque |

A página de status (`/`) continua aberta e **só de leitura**: é ela que responde
"o problema é a rede ou é o pod?", e pôr senha ali atrapalharia o diagnóstico.

### Testar sem o hardware pronto

```
python tools/ponte_falsa.py        # num terminal
python ypod_monitor.py             # no outro
```

A ponte falsa finge ser o ESP32 (mesmo protocolo, dados plausíveis). Dá para
matá-la no Ctrl+C e subir de novo para ver a retomada funcionando.

## MQTT

Para o projeto sair da sua rede e falar com um servidor de verdade, o TCP direto
não serve: ele exige que o servidor alcance a ponte, e ninguém vai abrir porta no
roteador da escola pra você. Com MQTT a ponte faz conexão **de saída** para um
broker e quem quiser assina — atravessa NAT sem pedir nada.

Ligue os dois lados no mesmo broker e tópico:

```c
// firmware/ypod_bridge/ponte.h
#define MQTT_BROKER       "mqtt://192.168.0.10:1883"
#define MQTT_TOPICO_BASE  "ypod/pod1"
#define MQTT_USUARIO      ""
#define MQTT_SENHA        ""
```

```
pip install paho-mqtt
python ypod_monitor.py --broker mqtt://192.168.0.10:1883 --topic ypod/pod1
```

Broker vazio = a ponte não publica e só o TCP funciona. Os dois convivem: o
painel local continua de pé mesmo com o broker fora do ar.

**Tópicos:**

| tópico | o quê |
|---|---|
| `<base>/raw` | uma leitura, QoS 1 |
| `<base>/status` | `online` / `offline` (retained, via Last Will) |

**Formato** de `<base>/raw`:

```json
{"boot":"a1b2c3d4","seq":1234,"age":2500,"line":"2026-...,,,YPODV6,..."}
```

`age` é há quantos **ms** a linha foi lida, não um horário — assim os dois
relógios não precisam estar sincronizados (a ponte nem tem relógio) e o backlog
chega com a hora certa. `boot`+`seq` identificam a leitura.

**Por que QoS 1 e não QoS 0.** Quase todo tutorial usa a lib PubSubClient, cujo
`publish()` é só QoS 0: manda e esquece. Com o Wi-Fi oscilando você perde
leituras em silêncio e só descobre meses depois, num gráfico furado. Aqui a
ponte usa o cliente MQTT que já vem no core do ESP32, com QoS 1: o broker
confirma cada mensagem e só então a linha sai da fila. Se a conexão cair, o
ponteiro volta para a última confirmada e ela republica dali.

QoS 1 é "pelo menos uma vez" e duplica de propósito — o índice único em
(`boot`, `seq`) descarta repetido. Testado: republicar as 200 mensagens não move
o banco em uma linha.

**Uma coisa pro seu professor:** MQTT é transporte, não armazenamento. O broker
não guarda histórico (fora a última mensagem retained). Quem guarda é este
servidor — hoje o SQLite em `data/`, amanhã o da escola.

## InfluxDB + Grafana

Quando o servidor da escola entrar, o caminho é:

```
pod ──USB──> ESP32 ──MQTT──> broker ──> ypod_monitor.py ──┬─> SQLite local
                                                          └─> InfluxDB ──> Grafana
```

```
python ypod_monitor.py --broker mqtt://...        --influx-url http://servidor:8086        --influx-token <token> --influx-org escola --influx-bucket ypod
```

InfluxDB 1.x: troque `--influx-bucket` por `--influx-db`, com `--influx-user` /
`--influx-pass` se houver autenticação. Todos os parâmetros também saem de
variáveis de ambiente (`YPOD_INFLUX_URL`, `YPOD_INFLUX_TOKEN`…), que é o jeito
certo de não deixar token em linha de comando.

**O banco local é a fila.** O escritor lê as leituras em ordem de inserção e
guarda até onde já mandou. Se o servidor da escola cair — manutenção, rede,
feriado — nada se perde: quando ele voltar, recebe tudo o que ficou para trás,
com os horários originais. Testado derrubando o Influx no meio: 500 leituras
ficaram no banco e entraram sozinhas na volta, sem furo e sem duplicata.

Isso é o que você **não** ganha mandando do ESP32 direto para o Influx, nem com
o Telegraf consumindo o MQTT: nos dois casos o buffer é pequeno e volátil.

**Schema no Influx** (para montar o painel no Grafana):

| | |
|---|---|
| measurement | `ypod` (muda com `--influx-measurement`) |
| tags | `pod_id`, `firmware` |
| fields | `temp`, `rh`, `co2`, `pm1`, `pm25`, `pm10`, `fig2600`, `fig2602`, `co`, `aux` |
| timestamp | o da leitura, precisão de ms |

Sensor sem leitura (o `65535` de desconectado) não vira campo — fica buraco no
gráfico, que é o correto, em vez de um pico de 65 mil.

## O painel

- **Cards** — valor mais recente de cada sensor, variação nos últimos 5 min,
  sparkline e o mín/máx **do intervalo que estiver na tela**. Sensor que reporta
  `65535` (desconectado/saturado) aparece como "sem dado" e some dos gráficos.
- **Janelas rápidas** — 15 min a 30 dias, acompanhando o tempo.
- **Intervalo…** — início e fim com data e hora, para olhar um período específico
  (mais os atalhos Hoje / Ontem / Últimos 7 d / Tudo). Nesse modo os gráficos
  ficam parados no período escolhido; os cards continuam mostrando o valor atual.
- **Baixar CSV** — exporta exatamente o intervalo que está na tela.
- **Tabela** — as leituras do intervalo, uma a uma.

Os gráficos são médias por minuto (ou mais, em janelas largas — o painel diz o
passo). A tabela e o CSV são leitura a leitura, sem média.

Um gráfico por unidade, nunca dois eixos y no mesmo gráfico. Clique na legenda
para esconder uma série.

## O painel público (Cloudflare)

O painel local serve quem está na mesma rede. Para ele ficar acessível de
qualquer lugar, o PC **empurra** os dados para uma API na Cloudflare — a nuvem
nunca lê o PC, que está atrás do roteador.

```
ypod_monitor.py --POST /api/push a cada 15 s--> Worker ypod-api --> banco D1
site https://ypod.ypod-sm.workers.dev ----GET /api/*----------------^
```

A API responde **no mesmo formato** do servidor local — por isso o site usa o
mesmo [static/dashboard.html](static/dashboard.html), mudando só a meta tag
`ypod-api`. Mexeu num, mexa no outro; o `contrato_test.py` não cobre essa parte.

- **Fila:** o banco local. O ponteiro `nuvem_id` na tabela `state` marca até
  onde já subiu, e só avança depois do `200`. Internet fora por dias não perde
  nada: quando volta, sobe o atraso em lotes de mil.
- **Escrita** exige o secret `INGEST_TOKEN`; a leitura é pública.
- **Publicar a API:** `wrangler deploy` dentro de `worker/`.
- **Publicar o site:** `python tools/gerar_site.py <url-da-api>` e depois
  `wrangler deploy -c wrangler.site.jsonc`. Só a pasta `site/` sobe, e ela é
  gerada — não edite nada lá dentro.
- **Cota grátis do D1:** 100 mil linhas escritas por dia. Por isso só sobem
  médias de minuto (`--gravacao 0` estoura) e janelas acima de 2 dias leem a
  tabela `hora`, que resume por hora.

> **Só um PC alimenta o site.** Dois enviando fazem os valores "ao vivo"
> alternarem entre eles, e com a ponte falsa isso publica dado inventado.

Armadilhas que já custaram tempo aqui: `wrangler dev` falha com
`SQLITE_CANTOPEN` em caminho longo (acima de 260 caracteres — teste numa pasta
curta); `wrangler secret put` lendo de pipe do PowerShell grava o `\r\n` junto
e devolve 401 depois (use `wrangler secret bulk arquivo.json`); e o subdomínio
`workers.dev` só se troca pelo painel, nunca pela API — trocar quebra todas as
URLs publicadas.

## Os dados

Tudo em `data/ypod.db` (SQLite). Uma tabela com as leituras cruas e um resumo por
minuto que é o que faz consultar meses ser instantâneo — com 6 meses gravados
(5,2 milhões de leituras, ~500 MB) qualquer janela responde em menos de 1 s.

O banco é a fonte: dá para abrir no DB Browser for SQLite, no pandas
(`pd.read_sql("select * from readings", sqlite3.connect("data/ypod.db"))`) ou
baixar o pedaço que interessa em CSV pelo painel.

Os CSVs da versão anterior (`data/ypod_AAAA-MM-DD.csv`) são importados sozinhos
na primeira execução e não são reimportados depois.

## Ajustar os sensores

Os rótulos saem do mapa `FIELDS`, no topo de [ypod_monitor.py](ypod_monitor.py):

```python
#   índice na linha, chave, rótulo, unidade, grupo (= gráfico), cor
    (16, "co2", "CO₂", "ppm", "co2", 2),
```

Os índices vieram da sua linha de exemplo. `7`/`8` (temp/umidade), `16` (CO₂) e
`17-19` (PM) são seguros; os MOx (`10`, `11`, `12`, `14`, `15`) estão com nomes
genéricos — se você tiver a ordem certa dos Figaro/e2v do seu firmware, troque a
string ali. Campo novo vira coluna nova no banco sozinho, sem perder o histórico.

A hora usada é a **do PC**, não a do pod: o RTC do seu pod está uns 2 meses
atrasado (manda `2026-07-30`). As duas ficam gravadas. Nas linhas que chegam
atrasadas pelo buffer da ponte, o horário é reconstruído a partir do relógio
interno dela, não da hora em que o PC recebeu.

### Backup

O histórico completo mora num arquivo só. O D1 na nuvem é cópia, mas fica numa
conta grátis com teto de 5 GB. Um HD falhando leva o projeto inteiro.

```
powershell -ExecutionPolicy Bypass -File tools\backup_banco.ps1 -Destino D:\backup-ypod -Verificar
```

Guarda as 8 cópias mais recentes (`-Manter N` muda). Para rodar sozinho,
agende no Agendador de Tarefas do Windows.

**Não use `copy ypod.db`.** O banco usa WAL: com o servidor rodando, parte das
escritas está no arquivo `-wal` e ainda não foi para o `.db`. Uma cópia simples
pega um banco sem elas — ou pior, pega o `.db` no meio de uma gravação. O
script usa `VACUUM INTO`, que é a forma do próprio SQLite de copiar um banco em
uso, e de quebra já sai compactado.

### O que o servidor descarta, e por quê

O parser lê os campos **por posição**, então linha fora do formato não é lida
"com um campo errado" — ela é lida **toda errada**. Três camadas de defesa, e
nenhuma delas é paranoia: as quatro situações abaixo aconteceram de verdade.

1. **Contagem de campos.** A linha do pod tem 21 campos exatos (22 com o vento
   que a ponte acrescenta). Menos que isso, descarta a linha inteira. Já chegou
   linha truncada, e o `4665` que é o ADC do sensor de CO virou 4665 °C no
   banco; e já chegou linha com a vírgula entre `pod_id` e firmware corrompida
   num `Y`, o que fundiu dois campos e gravou a umidade como temperatura.
2. **Primeiro campo.** Tem que ser data ISO ou vazio. Se vier número ali, a
   linha está deslocada e não dá para adivinhar de quanto.
3. **Faixa física por sensor.** Temperatura fora de −40 a 85 °C, umidade fora
   de 0 a 100% — descarta **o valor**, não a linha: os outros sensores da mesma
   linha podem estar bons.

Além disso, `-999` e `9999` são sentinelas de inválido. E tudo acima de
`60000` tem significado conhecido — os valores saem do `read_raw()` do firmware
do pod, não são aleatórios:

| valor | o que é |
|---|---|
| **64537** | o conversor ADS1115 não inicializou. A função devolve `-999` de um `uint16_t`, e `-999` em 16 bits sem sinal é 64537. Aparece nos cinco canais de gás ao mesmo tempo |
| **65535** | a leitura deu `-1` em `int16`: entrada aberta lê um pouco abaixo de zero |
| **65534** | a leitura deu `-2`: o mesmo |

Filtrar só o 65535 deixa passar picos de 65 mil que achatam a escala de todos
os gráficos de gás.

**Neste pod, o canal de ozônio está morto.** O MiCS-2611 lê negativo em 99,2%
das leituras, enquanto os outros quatro canais do mesmo ADS1115 vão bem em
99,8%. Isso é o sensor, não o conversor — e ozônio não está entre os parâmetros
que o projeto exige, então não bloqueia nada.

Data impossível (`2000-84-90T00:03:00`) derruba a linha inteira: não é relógio
desacertado, é sinal de que o pod estava em mau estado quando montou a linha.

### Direção do vento não é média aritmética

Direção é grandeza **circular**: a média entre 350° e 10° é 0°, não 180°. E o
erro não seria aleatório — ele aparece exatamente quando o vento vem do norte,
que é onde a escala dá a volta. O servidor soma os vetores unitários e tira
`atan2(soma dos senos, soma dos cossenos)`, e mínimo e máximo de direção vêm
vazios porque não significam nada num círculo.

A mesma conta está na ponte (`compactar.cpp`) e no Worker. Se corrigir num
lugar só, os dados ficam certos com o PC ligado e errados depois de uma queda.

## Se a coleta parar

Nesta ordem — cada passo só faz sentido se o anterior foi descartado:

1. **O site diz "PC da escola offline"** → abra `data/servidor.log` naquele PC.
2. **O log diz "procurando a ponte"** → abra <http://ypod-bridge.local> e olhe
   o LED da ponte.
3. **A ponte responde mas diz `esperando o pod (0 bytes)`** → desconecte e
   reconecte a USB do pod. Ele não re-enumera sozinho depois que a ponte
   reinicia; é a pendência mais chata que resta.
4. **O servidor travou** → Gerenciador de Tarefas, finalize o `python.exe`. O
   `iniciar_ypod.ps1` religa em 15 s. Se o próprio script parou, dois cliques
   no atalho "YPOD servidor" (`Win+R` → `shell:startup`).

O LED responde duas perguntas independentes sem misturar. A **cor de fundo** é
sobre o pod: verde pulsando a cada leitura, amarelo quando ele sumiu. A
**piscada rápida por cima** é sobre a rede: azul procurando, roxo no portal,
nenhuma quando está tudo certo. Verde pulsando com piscada roxa quer dizer
"coletando normalmente, esperando você configurar a rede".

**Antes de teorizar, abra a página de status.** Ela diz em uma linha se o
problema é rede ou é o pod. Três vezes neste projeto a causa foi o pod plugado
no PC em vez da ponte, e em todas se perdeu tempo inventando teoria sobre cabo,
contato e laço de terra antes de olhar.
