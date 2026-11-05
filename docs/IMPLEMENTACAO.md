# Etapa de implementação

Como a solução é implantada em campo, o que ela custa e consome, e as
restrições que só apareceram quando o sistema saiu da bancada.

---

## 1. Fluxo de implementação em campo

Sete fases. Cada uma termina num **ponto de decisão**: ou o critério foi
atendido e segue, ou para ali. Subir 15 m com o sistema não validado em
bancada é o erro mais caro possível neste projeto.

### Fase 0 — Pré-requisitos (antes de ir ao colégio)

| Item | Responsável | Critério |
|---|---|---|
| Autorização de trabalho em altura, NR-35 | Manutenção do colégio | agendado, com acompanhamento |
| Ponto de rede ou cobertura Wi-Fi no local | TI do colégio | sinal melhor que −70 dBm medido no ponto |
| PC da escola disponível e ligado continuamente | TI do colégio | configuração de energia ajustada |
| Suíte automatizada passando | Grupo | 9 de 9 |
| Bancada validada | Grupo | [TESTES.md](TESTES.md), nível 2 completo |

> **Ponto de decisão 0:** sem NR-35 agendado e sem sinal confirmado no ponto,
> não se sobe. Medir o sinal exige subir uma vez só com um celular.

### Fase 1 — Montagem no gabinete (em bancada, no chão)

1. Fixar pod, ponte e regulador no gabinete, com os sensores voltados para as
   aberturas de ventilação.
2. Ajustar o LM2596 em **5,0 V** com multímetro, **antes** de ligar qualquer
   placa. Conferir sem carga e com carga.
3. Ligar o cabo do pod no conector **`USB`** da ponte. Nada no `COM`.
4. Montar a biruta e o tacômetro no mastro, acima do gabinete.
5. Passar o cabeamento com laço de gotejamento para a água não correr para
   dentro dos conectores.

> **Ponto de decisão 1:** LED verde pulsando e contagem de linhas subindo na
> página de estado. Se o pod não enumerar aqui, não vai enumerar a 15 m.

### Fase 2 — Configuração de rede (ainda no chão)

1. Ligar a estação; a ponte abre a rede `ypod-setup`.
2. Pelo celular, acessar `192.168.4.1` e escolher a rede da escola.
3. Trocar a senha do portal em `/config`. A senha de fábrica é pública.
4. Confirmar que `ypod-bridge.local` responde do PC da escola.

> **Ponto de decisão 2:** ponte visível pelo PC da escola, pela rede final — não
> por hotspot de celular, que isola clientes e esconde esse problema.

### Fase 3 — Instalação no local

> **Como está hoje:** a estação foi instalada num **terraço aberto**, local
> provisório acordado com o professor responsável do colégio, e não sobre a
> caixa d'água a ~15 m como previa a Concepção. Ver restrição 9. Os passos
> abaixo descrevem a instalação definitiva.

1. Içar o gabinete fechado. Fixar na estrutura da caixa d'água.
2. Orientar e fixar a biruta, **anotando a posição do norte geográfico**.
3. Posicionar o painel solar voltado ao norte, com inclinação próxima à latitude
   de Curitiba (~25°), livre de sombra nas horas centrais do dia.
4. Conectar bateria e painel. Conferir tensão sob carga.
5. Fechar e vedar o gabinete; conferir prensa-cabos.

> **Ponto de decisão 3:** com o gabinete fechado e só na bateria, o sistema
> segue publicando por 30 minutos.

### Fase 4 — Calibração de instalação

1. Em `/config`, com a biruta apontada ao norte, clicar em "definir norte na
   posição atual".
2. Informar o número de furos do disco do tacômetro.
3. **Calibrar o fator de velocidade** contra um anemômetro de referência — ver
   restrição 3; hoje este passo não pode ser concluído.

### Fase 5 — Servidor e publicação

1. Clonar o repositório em `C:\YPOD`, **fora de pasta sincronizada**.
2. Configurar o `iniciar_ypod.ps1` para subir com o Windows.
3. Ajustar a energia do Windows: suspensão "Nunca", desligar o gerenciamento de
   energia do adaptador de rede, religar após queda de energia.
4. Subir o servidor com `--nuvem-url` — **só neste computador**.
5. Conferir o painel público recebendo.

> **Ponto de decisão 5:** o site público mostra dado com menos de 1 minuto de
> atraso.

### Fase 6 — Vigilância e entrega

1. Cadastrar `/api/saude` num monitor externo, com alerta por e-mail.
2. Forçar uma falha (parar o servidor por 20 min) e **confirmar que o e-mail
   chega**. Alarme não testado é alarme que não existe.
3. Entregar o [manual](MANUAL.md) à direção e à manutenção.
4. Agendar a primeira calibração cruzada com o Y-Pod de referência.

---

## 2. Experiência do usuário

Está no [MANUAL.md](MANUAL.md), escrito para dois leitores: a comunidade
escolar, que só precisa do painel, e quem instala e mantém.

A decisão de projeto que mais afeta o usuário final: **o painel é público e sem
senha**. Qualquer pessoa com o endereço vê o mesmo dado que a direção. Isso
atende a prioridade declarada pelo cliente e serve à comunidade do entorno, que
não tem estação de referência pública próxima.

---

## 3. Sustentabilidade

### Econômica

O custo foi mantido baixo por três decisões, não por acaso:

| Decisão | Efeito |
|---|---|
| Usar o **Y-Pod que o colégio já tem** do programa AQ-IQ como instrumento | elimina o maior item de custo: nove sensores calibrados |
| A ponte é um ESP32-S3 de prateleira | módulo de baixo custo, substituível por qualquer similar |
| Servidor em **PC cedido pelo colégio**, não em serviço pago | sem mensalidade |
| Nuvem no **plano gratuito** da Cloudflare | R$ 0/mês, e a arquitetura foi desenhada para caber nele |

**O plano gratuito não foi sorte: foi projeto.** A cota é de 100 mil escritas e
5 milhões de leituras por dia. Gravar cada leitura crua consumiria 39 mil
escritas/dia e tornaria uma consulta de 30 dias impossível. Agregar por minuto
na borda e resumir por hora na nuvem leva o uso a **1,4 % da cota de escrita**.

Custo de operação recorrente: **energia do PC da escola e nada mais.** Não há
licença, assinatura nem serviço pago no caminho.

> **A levantar com o grupo:** valores unitários do gabinete, painel solar,
> baterias, regulador e mastro, para fechar o custo total de implantação. Os
> itens acima são a estrutura de custo, não o orçamento.

### Ambiental

**Consumo.** Estimativa a partir das especificações; **medição pendente**:

| Item | Estimativa |
|---|---|
| Ponte ESP32-S3, Wi-Fi sempre acordado, 240 MHz | ~0,6 W |
| Pod completo, com o ventilador do sensor de particulado | ~1,4 W |
| **Total** | **~2 W → ~48 Wh/dia** |

Com 2 baterias de 12 V 9 Ah (216 Wh) e 50 % de profundidade de descarga, a
autonomia sem sol é de cerca de **2 dias**. Dimensionado para o inverno de
Curitiba, que é o pior caso de irradiação.

> O firmware **poderia** economizar ~0,3 W reduzindo o clock ou ligando a
> economia de rádio. Foi testado: as duas quebram a enumeração USB. O sistema
> compila, conecta no Wi-Fi e nunca lê o pod. A economia não vale o projeto.

**Operação.** Energia solar, sem ligação à rede elétrica no ponto de medição.
Sem consumíveis, sem reagente, sem descarte periódico.

**Fim de vida.** Os sensores têm vida útil finita e são o principal resíduo:
o eletroquímico de CO dura cerca de 2 anos e o ventilador do sensor óptico de
particulado cerca de 3. São substituíveis individualmente, sem trocar o
conjunto — o que já é uma escolha de projeto a favor do descarte menor. O
descarte deve seguir a política de resíduos eletrônicos da instituição.

**Benefício ambiental.** O sistema produz informação que não existia: não há
estação pública de referência próxima ao colégio. Dado local sobre material
particulado permite decisão informada sobre atividade ao ar livre.

### Social

- **Acesso aberto.** Painel público, sem senha, em endereço fixo. A comunidade
  do entorno tem o mesmo acesso que a instituição.
- **Linguagem acessível.** O IQAr traduz seis medições numa palavra e numa cor.
  O manual explica o que fazer em cada faixa, sem exigir formação técnica.
- **Honestidade do dado.** Sensor com defeito aparece vazio, não com número
  inventado; o índice some quando o dado tem mais de 24 h. Publicar qualidade do
  ar errada é pior do que não publicar.
- **Uso pedagógico.** O dado alimenta o programa AQ-IQ e fica disponível para
  projetos dos alunos. Todo o código é aberto.
- **Segurança do trabalho.** Manutenção a 15 m é atividade sob NR-35. O manual
  deixa explícito que nenhuma tarefa justifica subir sozinho.

---

## 4. Restrições reais encontradas nesta etapa

Restrições que **não** foram levantadas na Concepção e apareceram quando o
sistema saiu da bancada.

### Restrição 1 — O pod não se reapresenta depois que a ponte reinicia

**O que é.** Um dispositivo USB auto-alimentado só sinaliza sua presença quando
**vê a tensão VBUS subir**. Reiniciar a ponte por software não derruba o VBUS,
porque ele vem do outro conector através do jumper soldado. Resultado: a ponte
reinicia, o pod continua energizado e calado, e a página de estado mostra
`esperando o pod (0 bytes)` com o Wi-Fi perfeito.

**O que afeta.** Toda reinicialização da ponte — queda de energia, atualização
de firmware, watchdog — exige alguém subir e religar o cabo. Em instalação a
15 m, isso é inaceitável.

**Dá para corrigir a tempo?** **Não por software.** A biblioteca de host USB do
núcleo não oferece função de desligar a pilha; só existe inicialização. A
correção é de hardware: uma chave eletrônica (MOSFET ou *load switch*) na linha
de 5 V do conector do pod, comandada por um GPIO, para a ponte cortar e
restabelecer o VBUS sozinha. É um componente de baixo custo e uma alteração
pequena no esquemático, mas **é alteração de hardware** e precisa entrar na
próxima revisão da placa.

**Mitigação em uso hoje:** um watchdog reinicia a ponte após 10 minutos sem
receber byte nenhum. Isso recupera o caso em que o pod caiu, mas não o caso em
que a ponte reiniciou primeiro.

### Restrição 2 — Velocidade do vento sem referência de calibração

**O que é.** O tacômetro conta pulsos e a ponte calcula rotações por minuto.
Converter rpm em m/s exige um fator que depende da geometria do anemômetro, e
esse fator só se obtém comparando com um **anemômetro de referência**, que o
grupo não possui.

**O que afeta.** O requisito da Concepção pede 0 a 30 m/s. O sistema publica
**rpm**. A direção do vento funciona e é corretamente tratada como grandeza
circular; a velocidade é relativa.

**Dá para corrigir a tempo?** Depende de conseguir um anemômetro de referência
emprestado por um laboratório da universidade. A alteração no sistema é de um
único número, ajustável pela página de configuração, sem regravar firmware. **A
restrição é de instrumentação, não de projeto.**

> Inventar um fator plausível resolveria o item no papel e produziria um dado
> que ninguém poderia usar. O sistema publica rpm e declara que é rpm.

### Restrição 3 — Infraestrutura IIoT do colégio não definida

**O que é.** A Concepção previa broker MQTT e InfluxDB no servidor do colégio.
Até agora o colégio não definiu o broker, o servidor nem a política de retenção.

**O que afeta.** O caminho previsto na arquitetura não pôde ser usado. Sem uma
alternativa, não haveria entrega nenhuma do painel.

**Como foi contornado.** Dois caminhos independentes foram implementados:

- O caminho MQTT está **escrito e testado contra broker real**, com QoS 1 e
  *Last Will* retido. Está desligado por configuração, não por falta de código.
  Quando o broker existir, é mudança de configuração.
- Em paralelo, foi construído um caminho próprio até a nuvem, que entrega o
  painel público hoje.

**Risco pendente:** se a política de retenção do InfluxDB da escola for curta, o
histórico longo passará a existir só no banco local do PC — que deixa de ser
cópia e passa a ser o original, com as consequências de backup que isso traz.

### Restrição 4 — O buffer de 24 h não cabia na memória

**O que é.** O requisito pedia 24 horas de armazenamento local. A memória
disponível comporta 20 000 linhas; a 20–30 linhas por minuto medidas, isso dá
entre **11 e 17 horas** — abaixo do requisito.

**O que afeta.** O requisito mais importante da arquitetura IIoT não seria
atendido com a abordagem direta.

**Como foi resolvido.** Guardar as 27 linhas de um minuto protege um detalhe que
o PC descarta na chegada, porque ele grava média de minuto. A ponte passou a
dividir as mesmas vagas entre detalhe recente e memória longa, chegando a
**12,7 dias**. Requisito superado em 12 vezes, sem comprar memória.

### Restrição 5 — O computador da escola desliga fora do horário

**O que é.** Em dois fins de semana a coleta parou: o PC suspendeu ou desligou
depois da tela de bloqueio. Uma das quedas durou **22 horas** e foi detectada
pelo monitor.

**O que afeta.** A coleta contínua, que é premissa do projeto. A estação continua
medindo e guardando, então o dado não se perde — mas o painel público fica
desatualizado, e é ele a prioridade declarada do cliente.

**Dá para corrigir a tempo?** Sim, é configuração: suspensão "Nunca",
gerenciamento de energia do adaptador de rede desligado, religar após queda de
energia na BIOS, horário ativo do Windows Update. Já está na lista de campo.

### Restrição 6 — Um contrato de dados em três arquivos

**O que é.** O mapa de campos existe no firmware da ponte, no servidor e no
Worker da nuvem. O envio para a nuvem é lido **por nome**, então uma chave que
falte no destino não gera erro: a origem envia e o destino descarta em silêncio.

**O que afeta.** Quatro campos ficaram cinco dias fora do histórico público,
enquanto a tela mostrava os valores certos — porque o estado ao vivo não passa
por aquelas colunas. Nenhum erro, nenhum log, nenhum sintoma.

**Como foi resolvido.** O teste de contrato comparava dois dos três arquivos.
Hoje compara os três e a mensagem de falha nomeia as chaves divergentes.

> A lição é de arquitetura, não de código: **um contrato replicado em N lugares
> precisa de um teste que compare os N**, senão a replicação é só uma forma
> elaborada de errar em silêncio.

### Restrição 7 — O esquemático elétrico está desatualizado

**O que é.** O diagrama entregue na etapa anterior identifica a placa como
"DevKitC-1" (é uma YD-ESP32-S3), não traz os sensores de vento e especifica
**3,30 V** na alimentação, quando o que o sistema exige é **5,0 V** no pino `5V`.

**O que afeta.** Quem montasse seguindo o diagrama deixaria o barramento de 5 V
sem alimentação, e o pod nunca enumeraria. O sintoma seria idêntico ao da
restrição 1 e levaria horas de diagnóstico na direção errada.

**Dá para corrigir a tempo?** Sim, é redesenho de documento, e **está em
correção**. Um diagrama errado é pior que diagrama nenhum: ele é seguido com
confiança. Até a nova versão, ele está fora do repositório de propósito.

### Restrição 8 — Ferramenta de gravação sem driver no Windows

**O que é.** A ferramenta de linha de comando do Arduino não executa a
instalação de driver do núcleo quando roda em modo não interativo. Sem ela, a
interface de gravação do UNO R4 fica sem driver e a gravação falha com um erro
que aponta para o dispositivo errado.

**O que afeta.** Bloqueou a habilitação da calibração embarcada do pod — e a
mensagem de erro sugere problema de placa ou cabo, o que leva o diagnóstico
para o caminho errado.

**Como foi resolvido.** Executar uma vez, como administrador, o instalador de
driver que acompanha o núcleo. Está documentado no [README](../README.md).

### Restrição 9 — O ponto de instalação previsto não estava disponível

**O que é.** A Concepção definiu, junto com o cliente, medição sobre a caixa
d'água a cerca de 15 m. Na implementação esse ponto não pôde ser usado, e foi
acordado com o professor responsável do colégio um **local provisório: um
terraço aberto**.

**O que afeta.** O terraço é ambiente externo e representativo do ar do
entorno, então a medição vale. O que muda é a **altura**: mais perto do solo, a
leitura sofre mais influência local — poeira ressuspensa, o próprio prédio como
obstáculo, e um padrão de vento diferente do que se mediria a 15 m. Os dados do
período provisório não são diretamente comparáveis aos que virão depois da
mudança para o ponto definitivo.

**Dá para corrigir a tempo?** Depende de autorização e de trabalho em altura sob
NR-35, não do grupo. **A mitigação é de registro:** a data da mudança de local
precisa ficar marcada na série histórica, para que ninguém compare dois períodos
medidos em alturas diferentes como se fossem o mesmo ponto.

### Restrição 10 — A proteção contra tempo está incompleta

**O que é.** O requisito era gabinete IP65+ para o conjunto. Hoje **as baterias
estão protegidas em IP65**; a caixa que abriga o ESP32 e o regulador, e o
próprio pod, **não estão**.

**O que afeta.** É a pendência de maior risco em campo. Água no regulador ou na
ponte interrompe a coleta; água no pod danifica o instrumento mais caro do
sistema, que pertence ao colégio e é insubstituível no prazo do projeto. Chuva
com vento num terraço aberto atinge exatamente esses dois pontos.

**Dá para corrigir a tempo?** **Sim, e é a pendência mais urgente em campo.**
É a compra de um gabinete IP65 com prensa-cabos e a transferência das duas
peças, sem alteração de projeto elétrico nem de firmware. Até lá, a estação não
deve ficar exposta a chuva forte sem acompanhamento.

> O pod tem entradas de ar obrigatórias para o sensor de particulado e para o
> de temperatura e umidade: a proteção dele não é uma caixa vedada, e sim um
> **abrigo ventilado** — o *radiation shield* já previsto na Concepção, que
> protege de chuva e sol direto sem bloquear a troca de ar.

---

## 5. Resumo para a banca

| # | Restrição | Afeta | Corrigível a tempo? |
|---|---|---|---|
| 1 | Pod não re-enumera | operação sem subir ao local | **Não — exige hardware** |
| 2 | Vento em rpm, não m/s | um requisito de faixa | Sim, se houver anemômetro de referência |
| 3 | Infra IIoT do colégio indefinida | caminho MQTT previsto | Já contornado; depende do colégio |
| 4 | Buffer de 24 h não cabia | requisito principal | **Resolvido e superado** |
| 5 | PC desliga fora do horário | continuidade da coleta | Sim, configuração |
| 6 | Contrato em três arquivos | integridade do histórico | **Resolvido** |
| 7 | Esquemático desatualizado | quem for montar | Sim — em correção |
| 8 | Driver de gravação | habilitar calibração | **Resolvido** |
| 9 | Ponto definitivo indisponível | altura e comparabilidade | Depende de autorização; mitigado por registro |
| 10 | **Proteção IP65 incompleta** | risco de perda do instrumento | **Sim — a pendência mais urgente em campo** |
