# Game Art Generator Pipeline (n8n)

Pipeline local no n8n: **ideia → prompt técnico (OpenAI/Gemini) → imagem 2D (Nano Banana, via API do Gemini) → revisão humana
com loop de feedback → remoção de fundo (rembg self-hosted) → [opcional] image-to-3D (Meshy) com polling →
`.png` sem fundo (+ `.glb`) salvos no disco**.

No form, o campo **Tipo de Geração** escolhe a saída:

- **Apenas 2D**: depois de aprovar, remove o fundo e salva só o `.png`. Não usa o Meshy.
- **2D + 3D**: remove o fundo, gera o modelo no Meshy e salva o `.glb` e o `.png`.

Custo operacional: **≈ US$ 0,12 por asset 2D** (sem custo fixo) ou **≈ US$ 0,72 por asset 3D** + US$ 20/mês (Meshy Pro). Veja [Custo operacional](#custo-operacional).

Testado no n8n **2.27.4** (Docker).

```
workflows/game-asset-pipeline.json   workflow principal  (Import from text / Import from file)
workflows/mock-apis.json             mocks de Nano Banana (formato Gemini) + Meshy, para testar sem custo
html/review-page.html                página de revisão (HTML + CSS inline)
html/approved-page.html              página exibida após "Aprovar"
docker/docker-compose.yml, .env.example
scripts/build_workflows.py           gera os JSONs a partir de html/ (rode após editar o HTML)
previews/                            prévias 2D da página de revisão (1 PNG por execução)
```

## Fluxo

```
Form: New Asset ─► Start Pipeline Run (HTTP) ─► Form: Open Review (redirect do navegador)
                                   │
                                   ▼
Start (Webhook) ─► Config ─► Init Context ─► Context ◄──────────────────────────┐
                                               │                                 │
                     Switch Provider ─► OpenAI | Gemini ─► Normalize Prompt       │
                                               │                                 │
     NanoBanana: Generate 2D ─► Extract Image ─► Image to File ─► Save Preview  │
                                               │        (/previews/<exec>.png)   │
                                        Build Review Link                         │
                                               │                                 │
                     Respond: Open Review (303) ─► Wait for Review ─► Decision ──┤ Modificar
                                                                        │  (Apply Feedback)
                                                                     Aprovar
                                                                        ▼
   Render Approved Page ─► Respond: Approved ─► Restore Approved Image ─► Remove Background (rembg)
                                                                                ▼
                                                     Generate 3D? ─ Apenas 2D ─► Build File Path (só .png)
                                                          │ 2D + 3D
                                                          ▼
                                  Task Info ◄─ Meshy: Create Task ◄─ PNG to Base64
                                      ▼
                 ┌──► Wait 15s ─► Meshy: Get Status ─► Status ─ SUCCEEDED ─► Download GLB
                 │                                        │ FAILED ─► Stop     ▼
                 └──── Keep Polling? ◄── em andamento ────┘       Build File Path ─► Save Files ─► Done
                                                                   (.glb + .png ou só .png)

Review Page (Webhook GET) ─► Render Review Page (Code) ─► Respond: Review HTML
Preview Image (Webhook GET) ─► Validate Preview Name ─► Read Preview ─► Respond: Preview PNG | 404
```

### Nano Banana (API do Gemini)

O nó **NanoBanana: Generate 2D** chama `POST .../v1beta/models/<modelo>:generateContent` com
`responseModalities: ['IMAGE']` e `aspectRatio: '1:1'`, usando a credencial *Google Gemini (PaLM) API*.
A API **devolve a imagem em base64** (`candidates[0].content.parts[].inlineData`), e não uma URL. Por isso:

1. **Extract Image** pega o base64. Se a resposta não trouxer imagem (ex.: bloqueio de segurança), ele falha com o
   motivo (`finishReason` / `blockReason`).
2. **Image to File** converte em binário, e **Save Preview** grava `/previews/<execução>.png`, sobrescrito a cada
   iteração.
3. A página de revisão mostra a imagem pelo webhook `GET /webhook/game-art/preview?f=<execução>.png`. Esse webhook
   só aceita nomes `^\d+\.png$`; qualquer outro nome, ou arquivo inexistente, retorna 404.
4. Após aprovar, **Restore Approved Image** reaproveita o binário da última iteração, sem nova chamada paga.

Usei o HTTP Request em vez do nó nativo "Gemini → Image → Generate" porque o nativo (n8n 2.27) não aceita
aspect ratio e, quando a resposta não tem imagem, devolve um arquivo vazio sem acusar erro.

A pasta `previews/` acumula 1 PNG (~1–2 MB) por execução. Apague o conteúdo quando quiser, mas não durante uma
revisão em andamento.

### Por que o form chama um Webhook (e não roda tudo direto)?

O n8n 2.x **não aceita** o nó *Respond to Webhook* em execuções iniciadas por Form Trigger
(erro: `The "Respond to Webhook" node is not supported in workflows initiated by the "n8n Form Trigger"`).
Por isso o form só dispara o pipeline via HTTP (`/webhook/game-art/start`). Com isso o pipeline roda
numa execução própria e pode usar *Respond to Webhook* + *Wait*. Quando a imagem fica pronta, o form
redireciona o navegador para a página de revisão. Tudo continua num único workflow.

### Página de revisão (Human-in-the-Loop)

- O Webhook `GET /webhook/game-art/review` renderiza `html/review-page.html` no nó Code **Render Review Page**.
  A página não guarda estado: imagem, prompt, iteração e `resumeUrl` chegam pela query string. Todos os
  valores passam por HTML-escape, e o `resume` só é aceito se apontar para `/webhook-waiting/`.
- O `<form>` faz `POST` para `$execution.resumeUrl` do nó **Wait for Review**, que já inclui a assinatura
  exigida pelo n8n 2.x. Campos enviados: `feedback` e `action=approve|modify`.
- **Modificar**: o **Apply Feedback** acumula o texto em `feedbackHistory` e volta ao **Context**. O LLM recebe
  todo o histórico. A nova imagem é entregue como resposta 303 ao POST, e o navegador cai na nova página de revisão.
- **Aprovar**: responde na hora com a página "Aprovado" (evita timeout do navegador) e segue para a remoção de fundo e o 3D.
- Sem resposta em 24 h, a execução termina sozinha.

### Remoção de fundo (rembg)

Depois da aprovação, a imagem 2D aprovada é enviada ao serviço `rembg` do compose
(`POST http://rembg:7000/api/remove`, multipart `file` + `model`). O PNG transparente vai ao Meshy como
data URI base64 (`data:image/png;base64,...`), formato que a API do Meshy aceita em `image_url`, e também
é salvo ao lado do `.glb`.

- Modelo padrão: `isnet-general-use` (campo `rembgModel` no Config). Para personagens em estilo anime, use
  `isnet-anime`. `birefnet-general` tem mais qualidade, mas é bem mais pesado na CPU.
- A **1ª chamada** de cada modelo baixa os pesos (~170 MB; aqui levou ~80 s). Eles ficam no volume `rembg_models`.
  Depois disso, cada imagem 1024×1024 levou ~0,5 s na CPU.
- Consumo medido: ~2 GB de RAM no rembg e ~0,3 GB no n8n.
- Para um n8n que não usa este compose, rode `docker run -d -p 7000:7000 danielgatis/rembg s --host 0.0.0.0 --port 7000`
  e troque `rembgUrl` para `http://host.docker.internal:7000/api/remove` (ou para o IP do host).

## Custo operacional

Preços de 30/09/2026, em USD, sem IOF. Detalhes, fontes e contas em [docs/ORCAMENTO.md](docs/ORCAMENTO.md).

**Quanto custa cada ação na página de revisão:**

| Ação | O que é cobrado | Custo |
|---|---|---|
| Gerar / **Modificar** | 1 prompt no LLM + 1 imagem Nano Banana (`gemini-2.5-flash-image`) | ≈ US$ 0,04 |
| **Aprovar** (Apenas 2D) | rembg (local, grátis) | US$ 0 |
| **Aprovar** (2D + 3D) | rembg + 1 task Meshy com textura (30 créditos) | US$ 0,60 no Pro · US$ 0,30 no Ultra |

**Por asset** (premissa: 3 imagens até aprovar):

| Item | Apenas 2D | 2D + 3D |
|---|---|---|
| LLM (`gpt-4.1-mini`, 3 chamadas) | US$ 0,0015 | US$ 0,0015 |
| Imagens 2D (3 × US$ 0,039) | US$ 0,117 | US$ 0,117 |
| Remoção de fundo (rembg self-hosted) | US$ 0 | US$ 0 |
| Meshy image-to-3D com textura (plano Pro) | — | US$ 0,60 |
| **Total** | **≈ US$ 0,12** | **≈ US$ 0,72** (≈ US$ 0,42 no Ultra) |

**Por mês:**

| Cenário | Assets/mês | Total/mês |
|---|---|---|
| Só 2D | 100 | **≈ US$ 12**, sem custo fixo |
| Parado com 3D disponível | 0 | **US$ 20** (assinatura Meshy Pro) |
| Hobby 3D | 10 | ≈ US$ 21 |
| Indie 3D (limite do Pro: 1.000 créditos) | 33 | ≈ US$ 24 |
| Estúdio 3D (Meshy Ultra) | 300 | ≈ US$ 136 + VPS € 5,49 |

Os cenários se somam. Por exemplo, 33 assets 3D + 100 assets só 2D ≈ US$ 24 + US$ 12 = US$ 36/mês.

**O que você precisa saber:**

- **Custo fixo:** só existe se você usar o 3D. É a assinatura Meshy **Pro (US$ 20/mês)**, o menor plano com API
  (o Free não gera chave). Usando só **Apenas 2D**, o custo é 100% por uso e dá para ficar sem a assinatura.
- **Custo zero:** n8n self-hosted e rembg. A infraestrutura também sai de graça no PC local (só energia).
  Um VPS equivalente (Hetzner CX23, 4 GB) custa € 5,49/mês.
- **Onde vai o dinheiro:** o Meshy é **~83%** do custo por asset. Cada "Modificar" custa ~15× menos que um
  "Aprovar", então vale iterar no 2D antes de aprovar.
- **Aprovar não gera nova imagem:** a imagem da última iteração é reaproveitada.
- **Modelo de imagem:** trocar para Nano Banana 2 (US$ 0,067) ou Pro (US$ 0,134) muda o custo por iteração, no campo `nanoBananaUrl` do Config.
- **Mais de ~100 assets/mês:** o plano Meshy Ultra (US$ 100 / 10.000 créditos) passa a compensar.
- **Economia no 3D:** gerar só a malha (20 créditos) economiza US$ 0,20 por asset.
- **Testes sem custo:** use o workflow de mock (`mock-apis.json`).

## Setup

### 1. Subir o n8n

```bash
cd docker
cp .env.example .env        # ajuste ASSETS_HOST_PATH, PUID/PGID
docker compose up -d        # http://localhost:5679
```

Variáveis que liberam a escrita no disco (já estão no compose):

| Variável | Valor | Motivo |
|---|---|---|
| volume `${ASSETS_HOST_PATH}:/assets` | pasta do host | o container só enxerga o disco montado |
| volume `${PREVIEWS_HOST_PATH}:/previews` | `../previews` | prévias 2D (a pasta precisa existir; o repo já traz `previews/`) |
| `N8N_RESTRICT_FILE_ACCESS_TO` | `/assets;/previews` | no n8n 2.x, os nós de arquivo só podem acessar os diretórios listados aqui (separe vários com `;`) |
| `N8N_BLOCK_FILE_ACCESS_TO_N8N_FILES` | `true` | mantém bloqueado o acesso a `~/.n8n` (não desligue) |
| `user: PUID:PGID` | `1000:1000` | os arquivos saem com o seu dono no Linux |
| `WEBHOOK_URL` | `http://localhost:5679/` | base do `resumeUrl` e dos links que o navegador abre |

Sem compose, o equivalente é:

```bash
docker run -d --name game-art-n8n -p 5679:5678 -u 1000:1000 \
  -e WEBHOOK_URL=http://localhost:5679/ -e N8N_SECURE_COOKIE=false \
  -e 'N8N_RESTRICT_FILE_ACCESS_TO=/assets;/previews' -e N8N_BLOCK_FILE_ACCESS_TO_N8N_FILES=true \
  -e GENERIC_TIMEZONE=America/Sao_Paulo \
  -v n8n_data:/home/node/.n8n -v /home/voce/GameAssets:/assets -v /home/voce/previews:/previews \
  docker.n8n.io/n8nio/n8n
# Windows (PowerShell): -v C:\Assets:/assets
```

Para usar um n8n que já existe, adicione os dois volumes e inclua `/assets` e `/previews` em
`N8N_RESTRICT_FILE_ACCESS_TO`, por exemplo `/files;/scripts;/assets;/previews`. Depois recrie o container.

### 2. Importar

No n8n: **Create workflow → ⋯ → Import from file** (ou cole o JSON com Ctrl+V no canvas).
Importe `mock-apis.json` e `game-asset-pipeline.json`.

### 3. Credenciais

- **NanoBanana: Generate 2D** (obrigatória): credencial *Google Gemini (PaLM) API* com uma chave do
  [Google AI Studio](https://aistudio.google.com/apikey). A geração de imagem **não tem free tier**, então a
  chave precisa de faturamento ativo.
- Prompting (a do provedor escolhido no form):
  - **OpenAI: Build Prompt**: credencial *OpenAI* (modelo padrão `gpt-4.1-mini`).
  - **Gemini: Build Prompt**: a mesma credencial do Gemini (modelo padrão `gemini-2.5-flash`).

### 4. Nó Config

| Campo | Padrão | Observação |
|---|---|---|
| `nanoBananaUrl` | `…/models/gemini-2.5-flash-image:generateContent` | troque o modelo na URL: `gemini-3.1-flash-image-preview` (Nano Banana 2) ou `gemini-3-pro-image-preview` (Pro). Mock: `http://localhost:5678/webhook/mock/nanobanana/generate` |
| `meshyCreateUrl` | mock | real: `https://api.meshy.ai/openapi/v1/image-to-3d` |
| `meshyStatusUrlPrefix` | mock | real: `https://api.meshy.ai/openapi/v1/image-to-3d/` (o `task_id` é anexado) |
| `pollIntervalSec` / `maxPollAttempts` | 15 / 40 | ~10 min de polling no máximo |
| `hostRoot` / `containerRoot` | `C:\Assets` / `/assets` | `hostRoot` deve ser a pasta que você montou (`ASSETS_HOST_PATH`) |
| `rembgUrl` / `rembgModel` | `http://rembg:7000/api/remove` / `isnet-general-use` | serviço self-hosted de remoção de fundo |
| `previewsRoot` | `/previews` | pasta das prévias no container |

Para o Meshy real, abra **Meshy: Create Task** e **Meshy: Get Status** e escolha
*Authentication → Generic → Header Auth* (`Authorization: Bearer <API_KEY>`).

Para testar sem custo, cole a URL do mock em `nanoBananaUrl`. O mock devolve a resposta no mesmo formato do Gemini.
Mesmo com o mock, o nó exige uma credencial Gemini selecionada, mas uma chave qualquer serve.

### 5. Ativar e usar

1. Clique em **Publish** nos dois workflows (mock e pipeline).
2. Abra `http://localhost:5679/form/game-art`.
3. Preencha o form. Em **Tipo de Geração**, escolha **Apenas 2D** ou **2D + 3D**. Em **Caminho da Pasta Local**, use `C:\Assets\personagens` (convertido para
   `/assets/personagens`), `/assets/personagens` ou `personagens` (caminho relativo).

> O nó de escrita do n8n **não cria pastas**. Crie a subpasta no host antes. Se ela não existir, os arquivos
> não se perdem: são salvos na raiz (`/assets/<subpasta>__<arquivo>`) e o nó **Done** avisa em `warning`.

Arquivos gerados: `<prompt-slug>_<estilo>_<AAAAMMDD-HHmmss>.png` (imagem 2D sem fundo) e, no modo **2D + 3D**, o `.glb` com o mesmo nome.

## Editar o HTML

Edite `html/review-page.html` ou `html/approved-page.html` e rode:

```bash
python3 scripts/build_workflows.py
```

Depois reimporte o JSON. Os tokens `{{IMAGE_URL}}`, `{{PROMPT}}`, `{{RESUME_URL}}`… são substituídos pelo nó Code.

## Testes realizados (mock)

Rodado no n8n 2.27.4, com o nó OpenAI trocado por um stub (sem chave de API) e o Nano Banana apontando para o mock
no formato Gemini:

- form → página de espera → redirect para a revisão; a `<img>` carrega de `/webhook/game-art/preview` (PNG 200);
- `/preview?f=../x`, `f=999.png` (inexistente) e sem `f` → 404;
- resposta sem imagem → erro claro no **Extract Image** (`motivo …`);
- API real do Google com chave falsa → `API key not valid`, o que confirma URL, modelo e autenticação;
- **Modificar** com feedback → 303 → nova página (iteração #2, feedback aplicado ao prompt);
- **Apenas 2D**: Aprovar → rembg → só o `.png` salvo, em segundos; nenhum nó do Meshy executa (`taskId: null` no **Done**);
- **2D + 3D**: Aprovar → página "Aprovado" → rembg real (PNG RGBA, fundo 97% transparente) → Meshy mock recebe o data URI;
- 3 polls (IN_PROGRESS 43% → 86% → SUCCEEDED);
- `.glb` válido (glTF binary v2) + `.png` RGBA salvos em `/assets/props/`, e fallback para a raiz quando a subpasta não existia.

Não testado: geração real de imagem no Gemini, prompting real (OpenAI/Gemini) e o Meshy real, porque faltam chaves.
