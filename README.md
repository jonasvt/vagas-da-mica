# Localizador de Vagas da Mica 💖

Um site carinhoso onde a Mica entra quando quiser, vê as vagas novas uma por uma e "zera" a caixinha. Um robô no GitHub busca as vagas a cada 2 horas e publica o site no GitHub Pages — **o seu PC pode ficar desligado**.

Endereço: **https://jonasvt.github.io/vagas-da-mica/**

**Regras fixas**
- Só entram vagas **remotas**. Presenciais e híbridas são descartadas.
- Só entram vagas que aceitam quem está no Brasil: worldwide, Brasil, América Latina, "Americas" ou sem local informado. Uma vaga é descartada **só** quando diz explicitamente que é para outro país ("US only", "Europe", "must be based in the UK").

## Como ela usa

- **💌 Caixinha:** as vagas novas com as palavras dela no título, uma de cada vez: *Quero aplicar*, *Já apliquei* ou *Não é pra mim* (tem *desfazer*). Barra de progresso e, ao chegar em 100%, comemoração com o desenho dela e contador de dias seguidos zerando.
- **📝 Minha lista:** as vagas que ela quer aplicar, para marcar *Apliquei* até zerar também.
- **🔎 Buscar:** procura palavra ou frase exata (aceita plural e ignora o ponto que o celular coloca sozinho). Primeiro vêm as vagas com isso no título; depois as que só citam no texto, com o trecho destacado (ex.: "Discord", "YouTube").
- **💌 Pro Jonas** (só na Buscar): copia título + link e abre uma janelinha mostrando o que foi copiado (no celular também tem "Compartilhar"). Não mexe na lista dela; fica no histórico "Mandei pro Jonas".
- **Tirar:** na Minha lista (pendentes e já aplicadas) e no "Mandei pro Jonas", com "desfazer" por alguns segundos.
- **⚙️:** ela muda as palavras, o que esconder (ex.: senior) e quantos dias mostrar.

O site busca vagas novas sozinho quando ela volta para a aba (se passou mais de 15 minutos) e avisa "✨ N vagas novas chegaram". O progresso fica guardado no aparelho dela (celular ou computador). Se ela trocar de aparelho, começa do zero.

## Personalizar (você)

Edite [site/mica.json](site/mica.json): título, assinatura, **bilhetinhos** (um por dia), mensagens de comemoração e as palavras de busca. As palavras desse arquivo também guiam a busca do robô no Himalayas. Fontes e intervalos: [config/site.json](config/site.json).

## Como fica no ar

- Repositório **público** `jonasvt/vagas-da-mica` (código + site). `data/`, `.env`, `docs/` e `.claude/` ficam só no PC (`.gitignore`).
- O robô (`.github/workflows/vagas-da-mica.yml`) roda a cada 2 horas, a cada mudança enviada para a `main` e quando você clicar em **Actions → Vagas da Mica → Run workflow**. Ele publica a pasta `site` na branch `gh-pages` (sempre um commit só).
- GitHub Pages: **Settings → Pages → Branch `gh-pages` / `(root)`**.
- Tudo grátis (repositório público tem minutos de Actions ilimitados). O site não aparece no Google (`noindex`).
- Para mudar algo (bilhetes, palavras…): edite, faça commit e `git push` — o robô republica sozinho.

Se o GitHub mandar e-mail dizendo que desativou o robô por falta de atividade, é só entrar em **Actions → Vagas da Mica → Enable workflow**.

Para testar no PC antes: `python -m vagas site --force` e abra `python -m http.server 8090 --directory site` → http://127.0.0.1:8090.

---

# Bot de Vagas (Discord) — alternativa

Um bot do Discord que procura **vagas remotas** pelas palavras que cada pessoa escolher e posta as vagas novas **no canal do servidor** onde o alerta foi criado (sem mensagem privada e sem marcar ninguém). Precisa do PC (ou de um servidor) ligado.

Guia de uso do bot: [GUIA-RAPIDO.md](GUIA-RAPIDO.md)

## Onde o bot procura

| Fonte | Como | Para quem |
|---|---|---|
| Himalayas, Remote OK, Remotive, Jobicy, Working Nomads, We Work Remotely | APIs/feeds oficiais | qualquer área |
| Vagas BR no GitHub (backend-br, frontendbr, datascience-br, qa-brasil) | API do GitHub | tecnologia no Brasil |
| Freelancer.com | API oficial | projetos freelance |
| Reddit (7 subs de editores + r/forhire, r/hiring, r/jobbit, r/freelance_forhire) | RSS | edição de vídeo, social media, freelas (nos subs gerais só entra post de contratação que diz "remote") |
| Vaga encaminhada ou colada no bot | você manda | Discord, LinkedIn, Upwork, YT Jobs… |
| E-mails de alerta do **LinkedIn** e do **Upwork** | lê sua caixa de entrada | opcional, ver abaixo |
| Gupy | **desligada** | os termos da Gupy vedam "agregar" o conteúdo; ligar em `config/bot.json` é decisão sua |

**LinkedIn e Upwork** proíbem robôs nos termos de uso, então o bot **não acessa esses sites**. Duas formas permitidas:
1. **Posts do LinkedIn:** cole o link do post + o texto na conversa com o bot. Ele só guarda se tiver certeza: o post precisa estar contratando, ter contato (e-mail, link de candidatura ou "me mande mensagem") e dizer que é remoto.
2. **Vagas do LinkedIn/Upwork por e-mail:** crie alertas de vaga no LinkedIn (filtro "Remoto") e no Upwork. Os e-mails chegam na sua caixa e o bot lê, sem marcar como lido nem apagar nada. Para ligar, preencha `IMAP_USER` e `IMAP_PASSWORD` no `.env` (no Gmail, use uma "senha de app"). Ainda não testei com e-mails reais: o primeiro e-mail pode precisar de ajuste.

## Como ligar (uma vez, uns 10 minutos)

1. **Instalar:** `pip install -r requirements.txt`
2. **Criar o bot no Discord**
   - Entre em https://discord.com/developers/applications → **New Application** → nome "Vagas".
   - Menu **Bot** → **Reset Token** → copie o token.
   - Copie `.env.example` para `.env` e cole o token em `DISCORD_TOKEN=`.
3. **Colocar o bot no seu servidor**
   - Se ainda não tem um, crie no Discord: **+** → **Criar meu próprio** → **Para mim e meus amigos**. Convide ela.
   - No portal: **OAuth2** → **URL Generator** → marque `bot` e `applications.commands`; em permissões marque **Send Messages** e **Embed Links**. Abra o link gerado e escolha o seu servidor.
   - (Opcional, faz os comandos aparecerem na hora) No Discord: Configurações → Avançado → **Modo desenvolvedor**; clique com o botão direito no servidor → **Copiar ID** → cole em `DISCORD_GUILD_ID=` no `.env`.
4. **Ligar:** dê dois cliques em `iniciar-bot.bat` (ou `python -m vagas bot`). O PC precisa ficar ligado. Quando religar, o bot volta a procurar sozinho.

## Comandos

| Comando | O que faz |
|---|---|
| `/vagas python, backend` | Procura agora (campo opcional **sem**: palavras que não quer no título) |
| `/alerta python, cientista de dados` | Use **dentro do canal** onde quer receber: as vagas novas passam a ser postadas nele |
| `/alertas` · `/apagar 3` | Lista ou apaga alertas |
| `/ajuda` | Explica tudo isso |
| Encaminhar/colar uma vaga na conversa com o bot | Guarda a vaga e posta nos canais cujo alerta combina |

Para o bot postar num canal privado, adicione o bot nas permissões do canal com **Ver canal**, **Enviar mensagens** e **Inserir links**.

Vírgula separa as opções (`python, dados` = uma **ou** outra). Acento e hífen não importam (`backend` acha "Back-end").

## Testar sem Discord

```bash
python -m vagas procurar "python, cientista de dados" --sem "senior, lead"
```

Mostra no terminal o que o `/vagas` responderia.

## Manutenção

| Comando | Para quê |
|---|---|
| `python -m vagas backup` | Cópia do banco em `data/backups/` |
| `python -m vagas restore ARQUIVO` | Restaura um backup |
| `python -m vagas limpar` | Reaplica as regras (só remoto + aceita o Brasil) no banco todo, com backup antes |
| `python -m unittest discover -s tests -t .` | Testes automáticos |

Configuração das fontes e intervalos: [config/bot.json](config/bot.json). O `.env` guarda os segredos e nunca deve ir para o Git.

## O painel antigo

O painel web de editor de vídeo (`python -m vagas serve`) continua na pasta, mas está desligado. Pesquisa e documentação dele: [docs/](docs/).
