# Procedimento de testes, verificação e validação

Três níveis, do mais barato para o mais caro. Cada nível só faz sentido depois
que o anterior passou.

| Nível | O que cobre | Precisa de hardware? | Duração |
|---|---|---|---|
| **1 — Software** | Lógica, protocolos, banco, índice | não | ~90 s |
| **2 — Bancada** | Placas, sensores, rede, energia | sim | ~40 min |
| **3 — Campo** | Sistema instalado, operação contínua | sim, no local | dias |

---

## Nível 1 — Suíte automatizada

```
python testes/todos.py           # 9 testes, ~90 s
python testes/todos.py --tudo    # inclui o teste de volume, bem mais lento
```

Roda em qualquer máquina com Python 3, sem pod e sem ponte: os testes sobem
*uma ponte falsa* que fala o mesmo protocolo, um broker MQTT local e um Worker
falso. **Critério de aceite: 9 de 9 passam.** Qualquer falha bloqueia o deploy.

| Teste | O que verifica | Verif. |
|---|---|---|
| `parser_test` | Linhas malformadas. As quatro primeiras são **linhas reais** copiadas do banco, que já gravaram 4665 °C e 2876 % de umidade. | 14 |
| `contrato_test` | O mapa de campos nos **três** arquivos que precisam concordar: firmware da ponte, servidor e Worker. Extrai o formato do próprio `.ino`. | 11 |
| `iqar_test` | A tabela da CONAMA 506/2024, trava a tabela certa e rejeita a antiga. Prova que um pico de 1 minuto não move a média de 24 h. | 12 |
| `net_test` | O caminho ponte → PC: reenvio numerado, reconexão, atraso espalhado no tempo certo. | 15 |
| `mqtt_test` | Caminho MQTT QoS 1 contra broker real local. | 11 |
| `influx_test` | Saída em *line protocol* para o InfluxDB. | 11 |
| `nuvem_test` | Envio para o Worker: lote, autenticação, idempotência. | 8 |
| `gravacao_test` | Média por minuto no banco e tempo real na tela ao mesmo tempo. | 8 |
| `vento_test` | Média circular da direção do vento. | 5 |
| `volume_test` | Seis meses de dados, importação de CSV antigo e modo USB direto. | 6 |

**Por que os testes usam dados reais.** Cada linha malformada no `parser_test`
aconteceu de verdade e está datada. Um teste escrito a partir do que
*imaginamos* que pode dar errado cobre a imaginação, não o sistema.

### Teste de contrato: o que ele pegou

Entre 25/10 e 30/10 o Worker da nuvem ficou com o mapa de campos de antes de
uma correção. Como o envio é lido **por nome**, uma chave faltando não gera
erro: o servidor mandava e a nuvem descartava em silêncio. Quatro campos
ficaram cinco dias fora do histórico público enquanto a tela mostrava os valores
certos, porque o estado ao vivo não passa por aquelas colunas.

O teste comparava **dois** dos três arquivos. Hoje compara os três, e a
mensagem de falha nomeia exatamente quais chaves divergem.

---

## Nível 2 — Bancada, com hardware

Ordem obrigatória: energia antes de tudo, porque erro de alimentação produz
sintoma que parece defeito de comunicação.

### 2.1 Alimentação

| Passo | Critério de aceite |
|---|---|
| Medir a saída do LM2596 **sem carga**, com multímetro | **5,0 V ± 0,15 V** |
| Medir de novo com a ponte e o pod ligados | não cai abaixo de 4,75 V |
| Confirmar que nada está ligado no conector `COM` da ponte | nada ligado |

> **Nunca alimente pelos dois conectores USB ao mesmo tempo.** Com o jumper
> `USB-OTG` soldado, `COM` e `USB` se realimentam. Isso já quebrou a
> transferência de dados com a enumeração aparentemente normal.

> **Nunca aplique 5 V num GPIO.** O ESP32-S3 aceita 3,6 V no máximo.

### 2.2 Enumeração do pod

| Passo | Critério de aceite |
|---|---|
| Ligar a ponte, aguardar o LED | azul piscando → amarelo |
| Conectar o cabo do pod no conector `USB` | LED passa a **verde pulsando** em até 30 s |
| Abrir <http://ypod-bridge.local> | `fonte: USB host` com contagem de bytes subindo |
| Conferir "linhas desde o boot" após 2 min | entre **40 e 60** (20 a 30 por minuto) |

**Falha conhecida:** se a ponte reiniciar com o pod já conectado, ela fica em
`esperando o pod (0 bytes)` com o Wi-Fi perfeito. Desconectar e reconectar o
cabo resolve. Ver [IMPLEMENTACAO.md](IMPLEMENTACAO.md), restrição 1.

### 2.3 Sensores do pod

Com a ponte recebendo, abrir o painel e conferir que cada campo tem valor.

| Campo | Critério de aceite em ambiente normal |
|---|---|
| Temperatura | entre 10 e 40 °C, coerente com um termômetro ao lado |
| Umidade | entre 20 e 90 % |
| CO₂ | 350 a 600 ppm ao ar livre; sobe com alguém soprando perto |
| PM1 / PM2,5 / PM10 | valor presente, `PM1 ≤ PM2,5 ≤ PM10` |
| TGS2600 / TGS2602 | contagem de A/D estável depois de 10 min de aquecimento |
| CO e COV | **0 é correto** em ar limpo |
| Ozônio | vazio — sensor com defeito neste pod |

> **Teste de resposta do particulado:** acender e apagar um fósforo a ~1 m.
> PM2,5 deve subir visivelmente em menos de 30 s e voltar em alguns minutos. Se
> não reagir, o ventilador do PMS5003 pode estar parado.

> Valores iguais a `-999`, `9999` ou acima de `60000` são códigos de falha do
> conversor A/D, não medições. O servidor já os descarta.

### 2.4 Sensores de vento

| Passo | Critério de aceite |
|---|---|
| Abrir a página de estado e ler a linha "vento" | mostra ângulo, rpm e o valor cru |
| Girar a biruta devagar uma volta completa | o ângulo percorre 0 → 360 sem saltos |
| Soprar no tacômetro | rpm sobe e volta a zero |
| Apontar a biruta ao norte e gravar em `/config` | o ângulo passa a ler ~0° |

Se o valor cru do AS5600 não mudar ao girar, o ímã está fora de alcance ou
descentralizado.

### 2.5 Rede

| Passo | Critério de aceite |
|---|---|
| Ler o sinal na página de estado | melhor que **−70 dBm** no ponto de instalação |
| `ping ypod-bridge.local` do PC | responde |
| Subir o servidor com `--source net` | acha a ponte sozinha em até 30 s |
| Conferir a origem no painel | `rede <ip>:3333 · conectado` |

Se o mDNS funciona e o TCP não, a rede está isolando clientes entre si (comum
em hotspot de celular). Sintoma: sem entrada de ARP para a ponte. Nesse caso o
caminho é MQTT com broker, ou o plano B de ligar o pod direto no PC.

### 2.6 Store-and-forward

É o teste que valida o requisito mais importante da Concepção.

| Passo | Critério de aceite |
|---|---|
| Com tudo coletando, anotar o número de linhas | — |
| Parar o servidor no PC por **10 minutos** | a ponte continua: "linhas desde o boot" segue subindo |
| Conferir a página de estado da ponte | os dois buffers acumulam |
| Religar o servidor | ele pede desde onde parou |
| Conferir o painel após 1 min | **os 10 minutos aparecem no histórico, sem buraco** |

### 2.7 Gravação de firmware pela rede (OTA)

| Passo | Critério de aceite |
|---|---|
| Compilar e enviar pelo IP da ponte | termina sem erro |
| Acompanhar a página de estado | `boot id` muda, uptime zera |
| Reconectar o cabo do pod | volta a receber |

---

## Nível 3 — Verificação contra os requisitos da Concepção

Comparação direta entre o que foi prometido na etapa de Concepção e o que está
medido hoje.

### Parâmetros e faixas

| Requisito | Alvo | Situação | Evidência |
|---|---|---|---|
| CO₂ | 400 – 5.000 ppm | **Atendido** | S-300 publicando ppm calibrado |
| CO | 0 – 50 ppm | **Atendido** | CO-B4 com equação embarcada |
| COV | índice qualitativo | **Atendido** | habilitado em 30/10 |
| MP2,5 / MP10 | 0 – 500 µg/m³ | **Atendido** | PMS5003 |
| Temperatura | −10 – 50 °C | **Atendido** | SHT25, faixa do sensor maior |
| Umidade | 0 – 100 % UR | **Atendido** | SHT25 |
| Direção do vento | 0 – 360° | **Parcial** | mede e calcula média circular; **norte não calibrado** |
| Velocidade do vento | 0 – 30 m/s | **Não atendido** | publica **rpm**, não m/s — ver restrição 3 |

### Requisitos não funcionais

| Requisito | Alvo | Situação | Evidência |
|---|---|---|---|
| Leitura a cada 1 minuto | 1 min | **Atendido** | média de 60 s, carimbo no meio do intervalo |
| Buffer local store-and-forward | 24 h | **Superado — 12,7 dias** | dois anéis em PSRAM; teste 2.6 |
| Wi-Fi 2,4 GHz | — | **Atendido** | −58 a −60 dBm medidos na escola |
| MQTT | — | **Parcial** | escrito e testado contra broker real; desligado porque o broker da escola não foi definido |
| Histórico de 30 dias no painel | 30 dias | **Atendido** | janela por data/hora; exportação até 30 dias aberta |
| Gabinete IP65+ | — | **Pendente** | confirmar com o grupo |
| Operação contínua por solar | — | **Confirmar** | projeto definido; estado da instalação a confirmar |
| Calibração cruzada com o Y-Pod | periódica | **Pendente** | protocolo definido, ainda não executado |

### Requisito superado, e por quê importa

O alvo era **24 horas** de buffer. A memória disponível guardaria 20 000 linhas
cruas, o que a 27 linhas por minuto dá entre 11 e 17 horas — **abaixo do
requisito**. A solução não foi comprar memória: foi notar que o PC descarta o
detalhe de qualquer forma, já que grava média de minuto.

A ponte passou a dividir as mesmas 20 000 vagas:

```
1 600 linhas cruas ........ ~1 hora   (queda curta volta com fidelidade total)
18 400 médias de minuto ... ~12,7 dias (queda longa volta com o que o PC guardaria)
```

De ~12 horas para ~13 dias, na mesma memória, sem mudar o protocolo.

---

## Nível 3 — Validação contra as necessidades do cliente

Os cinco pontos foram definidos em reunião com o professor responsável do
colégio e estão no slide de alinhamento da Concepção.

| O que o cliente pediu | Situação | Como se verifica |
|---|---|---|
| Medição **externa**, sobre a caixa d'água (~15 m) | **Confirmar** | inspeção do local de instalação |
| Estação **fixa**, operação contínua por solar | **Confirmar** | estação energizada sem rede elétrica por 72 h |
| Parâmetros alinhados ao Y-Pod + T/UR + vento | **Atendido** | o instrumento **é** um Y-Pod; vento acrescentado |
| Infraestrutura de rede e servidor do colégio | **Atendido** | roda em PC cedido pelo colégio, na rede da escola |
| **Painel digital** — prioridade declarada | **Atendido e superado** | painel público na internet, com IQAr, aberto a qualquer pessoa |

### Sobre a prioridade do cliente

O painel digital foi o único item que o cliente marcou como prioritário. Ele
está entregue acima do pedido: além do painel, existe um **índice IQAr**
calculado pela CONAMA 506/2024 que traduz as medições numa palavra, e o
endereço é público, sem senha — a comunidade do entorno alcança o mesmo dado
que a direção do colégio.

### Monitoria de disponibilidade

O painel responder não prova que o sistema funciona: se o computador da escola
parar, a API continua de pé e devolve dado velho. Por isso existe `/api/saude`,
que devolve **503** em três condições distintas, e um serviço externo o
consulta a cada 5 minutos e avisa por e-mail.

| Condição | HTTP | Limiar |
|---|---|---|
| coleta normal | 200 | — |
| PC parou de enviar | 503 | 15 min |
| PC no ar, pod mudo | 503 | 30 min |
| nunca recebeu nada | 503 | — |

Os quatro estados foram testados em Worker local com banco semeado, não apenas
raciocinados. **O alarme já disparou numa queda real**, de 22 horas, causada por
desligamento do computador fora do horário de aula.
