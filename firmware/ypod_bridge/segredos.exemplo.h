/*
 * Segredos da ponte -- MODELO
 * ---------------------------
 * Copie este arquivo para `segredos.h` (na mesma pasta) e preencha. O
 * `segredos.h` esta no .gitignore e nunca vai para o repositorio; este modelo
 * vai, para quem clonar saber o que precisa criar.
 *
 *     copy segredos.exemplo.h segredos.h        (Windows)
 *     cp   segredos.exemplo.h segredos.h        (Linux/macOS)
 *
 * Sem o `segredos.h` a compilacao para com uma mensagem dizendo o que fazer --
 * de proposito. E melhor quebrar o build do que compilar em silencio com a
 * senha de outra pessoa.
 *
 * O que NAO fica aqui:
 *   - a senha do Wi-Fi: ela e escolhida no portal e vive na NVS da placa
 *   - a senha do portal: e padrao de fabrica (ponte.h) e se troca na pagina
 *     de configuracao, ficando gravada na NVS
 *   - o token da nuvem: e secret do Worker e variavel de ambiente no PC
 */
#pragma once

// Senha para gravar firmware pela rede (ArduinoOTA). Quem souber isto e
// estiver na mesma rede pode gravar o que quiser na placa -- por isso ela nao
// mora no repositorio. Use "" para desligar a protecao (nao recomendado).
#define OTA_SENHA "troque-esta-senha"
