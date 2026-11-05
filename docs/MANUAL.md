# Manual do usuário

Estação de monitoramento da qualidade do ar externo — Colégio Santa Maria.

Este manual tem duas partes, porque são dois leitores diferentes:

- **[Parte 1](#parte-1--para-a-comunidade-escolar)** — para quem usa a
  informação: direção, professores, alunos e famílias. Não exige nada técnico.
- **[Parte 2](#parte-2--para-quem-instala-e-mantém)** — para quem instala,
  configura e mantém a estação funcionando.

---

# Parte 1 — Para a comunidade escolar

## O que a estação faz

Mede o ar de fora, no alto da caixa d'água, e publica os valores num site aberto:

**<https://ypod.ypod-sm.workers.dev>**

A página funciona em celular e computador, não precisa de senha nem de
instalação, e atualiza sozinha.

## O que a página mostra

**Em cima, o IQAr** — o Índice de Qualidade do Ar, um número único que resume
tudo numa palavra. É o que você olha se tiver tempo para olhar uma coisa só.

| Índice | Classificação | O que significa na prática |
|---|---|---|
| 0 – 40 | **Boa** | Atividade ao ar livre normal. |
| 41 – 80 | **Moderada** | Pessoas muito sensíveis podem ter sintomas leves (tosse, irritação nos olhos). |
| 81 – 120 | **Ruim** | Grupos sensíveis — crianças com asma, pessoas com doença respiratória — devem reduzir esforço físico ao ar livre. |
| 121 – 200 | **Muito ruim** | Todos podem sentir efeitos. Vale evitar educação física e esportes ao ar livre. |
| acima de 200 | **Péssima** | Evitar atividade externa. |

As faixas seguem a **Resolução CONAMA 506/2024**, que é a norma nacional
vigente. O índice é o **pior** entre os poluentes medidos, não a média: se o
material particulado está ruim e o resto está bom, o índice mostra ruim. É
assim de propósito — o ar não fica bom porque a maioria dos poluentes está
baixa.

**Embaixo, cada medição separada**, com o valor atual e o gráfico das últimas
horas. Você pode escolher o período: última hora, último dia, última semana, ou
um intervalo exato de datas.

## O que cada medição quer dizer

| O que aparece | O que é | Por que importa aqui |
|---|---|---|
| **PM2,5** e **PM10** | Poeira fina em suspensão, em µg/m³ | O principal problema do ar externo. Vem de trânsito, queima e obra. Quanto menor, melhor. |
| **CO₂** | Gás carbônico, em ppm | Ao ar livre fica perto de 420 ppm. Serve para confirmar que o sensor está vivo. |
| **CO** | Monóxido de carbono, em ppm | Gás de escapamento. Em ar limpo o valor correto é **0**. |
| **COV** | Compostos orgânicos voláteis | Solventes, tinta, combustível. Em ar limpo o valor correto é **0**. |
| **Temperatura** e **Umidade** | °C e % | Contexto. Também corrigem a leitura dos outros sensores. |
| **Vento** | Direção e velocidade | De onde vem o ar. Ajuda a explicar por que o particulado subiu. |

### Dois valores que parecem erro e não são

**CO e COV marcando zero.** É o resultado certo em ar limpo. Esses sensores têm
resolução de 1 unidade e a equação de calibração resulta em negativo quando a
concentração é muito baixa; o firmware trava em zero. Zero quer dizer "abaixo
do que o sensor consegue distinguir", não "sensor desligado".

**Ozônio sem valor.** O sensor de ozônio deste pod está com defeito e foi
desativado no sistema, em vez de publicar número inventado. A posição continua
reservada para quando ele for trocado.

## Quando avisar o grupo responsável

Avise se alguma destas coisas acontecer:

1. **A página diz "PC da escola offline".** A coleta parou. Normalmente é o
   computador que desligou ou saiu da rede.
2. **Os valores estão congelados há horas**, com o mesmo número repetido.
3. **A data do último dado é de ontem ou antes.**
4. **Algo visivelmente quebrado** na estação, no alto: caixa aberta, cabo solto,
   antena torta, painel solar sujo ou deslocado.

Para os três primeiros casos o grupo já recebe um e-mail automático. Avisar de
qualquer jeito não atrapalha.

> **Não é necessário fazer nada no computador.** Ele religa o serviço sozinho se
> o programa cair, e a estação guarda as medições enquanto o computador estiver
> fora. Quando ele volta, o que ficou para trás entra no histórico sozinho.

---

# Parte 2 — Para quem instala e mantém

## O que compõe a estação

| Peça | O que é | Onde fica |
|---|---|---|
| **Pod YPODV6** | O instrumento. Arduino UNO R4 Minima com nove sensores. | Dentro do gabinete, no alto |
| **Ponte** | ESP32-S3 que lê o pod pela USB e manda por Wi-Fi | Mesmo gabinete |
| **Alimentação** | Painel solar → bateria → regulador LM2596 em 5,0 V | Mesmo gabinete |
| **Servidor** | Um PC comum na escola, rodando `ypod_monitor.py` | Dentro da escola |
| **Nuvem** | Worker + banco na Cloudflare, alimenta o site público | — |

## Instalar do zero

### 1. Preparar o computador da escola

```
git clone https://github.com/oliveiraguilherme0308-star/ypod-painel.git C:\YPOD
cd C:\YPOD
pip install pyserial
```

Só o Python 3 é obrigatório. O `pyserial` serve para o plano B de ligar o pod
direto no PC; o `paho-mqtt` só é usado no modo MQTT.

> **Não instale o projeto dentro do OneDrive.** O banco é SQLite e a
> sincronização pode travar o arquivo com o servidor rodando. Use `C:\YPOD` ou
> outra pasta fora de pasta sincronizada.

### 2. Preparar a ponte

Copie `firmware/ypod_bridge/segredos.exemplo.h` para `segredos.h` na mesma
pasta e preencha a senha do OTA. Sem esse arquivo a compilação para de
propósito, com mensagem dizendo o que fazer.

Compilar e gravar: veja a seção "Gravar o firmware" do
[README](../README.md).

### 3. Ligar a ponte na rede, sem cabo

Na primeira vez — ou sempre que não encontrar a rede salva por 30 s — a ponte
abre o próprio ponto de acesso:

```
rede:  ypod-setup        senha: ypod1234
depois abra:  http://192.168.4.1
```

Escolha a rede da escola na lista, digite a senha e salve. Fica gravado na
memória da placa e sobrevive a reinício e a nova gravação de firmware.

> **No celular Android**, o aparelho às vezes volta para os dados móveis ao ver
> que a rede do portal não tem internet, e aí o navegador não acha o
> `192.168.4.1`. Desligue os dados móveis por um minuto.

O portal fecha sozinho em 5 minutos e tenta a rede salva de novo, para que uma
queda curta não deixe a ponte presa nele. Para esquecer a rede salva, segure o
botão `BOOT` nos três primeiros segundos ao ligar.

### 4. Ligar o pod na ponte

O cabo do pod entra no conector marcado **`USB`** da ponte, nunca no `COM`.

> **Nunca ligue os dois conectores USB ao mesmo tempo.** Eles se realimentam e
> isso já quebrou a transferência de dados com a enumeração parecendo normal —
> sintoma confuso, causa simples.

### 5. Subir o servidor

```
python ypod_monitor.py --source net --nuvem-url https://ypod-api.ypod-sm.workers.dev
```

O `--nuvem-url` é **só para o computador da escola**. Em qualquer outra máquina,
rode sem ele: dois computadores publicando fazem os valores "ao vivo" do site
alternarem entre os dois.

Para iniciar sozinho junto com o Windows, use o `iniciar_ypod.ps1`: ele religa
o serviço se cair, grava log em `data\servidor.log` e impede duas cópias
brigando pela porta.

### 6. Calibrar o norte da biruta

Aponte a biruta para o norte geográfico e, em
<http://ypod-bridge.local/config>, clique em "definir norte na posição atual".
Fica gravado na memória da placa.

## Operar no dia a dia

### Páginas de diagnóstico

| Endereço | O que mostra | Senha |
|---|---|---|
| <http://ypod-bridge.local> | Estado da ponte: Wi-Fi, pod, buffers, vento | não |
| <http://ypod-bridge.local/config> | Ajustes: norte, tacômetro, senha, reiniciar | sim |
| <http://localhost:8000> | Painel local completo | não |
| `/api/saude` na nuvem | Estado da coleta para o monitor externo | não |

A página de estado da ponte é a mais útil: ela responde na hora se o problema é
a rede ou é o pod. **Olhe ela antes de teorizar.**

### O LED da ponte

| Cor | Significado |
|---|---|
| Azul piscando | procurando Wi-Fi |
| **Verde pulsando** | recebendo do pod — estado normal |
| Amarelo | na rede, mas sem pod |
| Roxo fixo | portal de configuração aberto |
| Vermelho | falha |

### Se a coleta parar

Nesta ordem. Cada passo só faz sentido se o anterior estiver descartado.

1. **O site diz "PC da escola offline"** → abra `data\servidor.log` naquele
   computador.
2. **O log diz "procurando a ponte"** → abra <http://ypod-bridge.local> e olhe
   o LED.
3. **A ponte está no ar mas diz `esperando o pod (0 bytes)`** → desconecte e
   reconecte o cabo USB do pod. Ele não se reapresenta sozinho depois que a
   ponte reinicia.
4. **O servidor travou** → finalize o `python.exe` no Gerenciador de Tarefas; o
   script religa em 15 segundos.

### Manutenção periódica

| Quando | O quê |
|---|---|
| Mensal | Olhar o painel solar: sujeira e folhas reduzem a geração |
| Mensal | Conferir no painel se os nove sensores têm valor |
| Semestral | Calibração cruzada com o Y-Pod de referência do colégio |
| Semestral | Inspeção do gabinete: vedação, cabos, fixação |
| Quando trocar a senha do portal | Trocar também a senha do OTA e regravar |

> Trabalho no alto (~15 m) é atividade sob **NR-35**: exige acompanhamento da
> manutenção do colégio e os equipamentos de proteção adequados. Nenhuma tarefa
> deste manual justifica subir sozinho.

## Backup

O histórico vive em três lugares: o banco local do computador da escola, o
banco na nuvem e o botão "Baixar CSV" do painel. Para cópia do banco local use
`tools/backup_banco.ps1`, que usa `VACUUM INTO` — a forma do próprio SQLite de
copiar um banco em uso.

**Não use `copy ypod.db`.** Com o servidor rodando, parte das escritas está no
arquivo `-wal` e uma cópia simples pega um banco incompleto.
