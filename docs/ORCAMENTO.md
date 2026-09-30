# Orçamento: manter o Game Art Pipeline ativo

Preços consultados em **30/09/2026**, em USD, sem impostos (IOF/ISS de cartão internacional não incluídos).
Confira as fontes antes de fechar contrato, porque os preços mudam com frequência.

## 1. O que custa em cada etapa

| Etapa | Serviço | Modelo de cobrança | Preço |
|---|---|---|---|
| Orquestração | n8n self-hosted (Community) | grátis | US$ 0 |
| Prompting | OpenAI `gpt-4.1-mini` (padrão do workflow) | por token | US$ 0,40 / 1M entrada · US$ 1,60 / 1M saída |
| Prompting | Google `gemini-2.5-flash` | por token (tem free tier) | US$ 0,30 / 1M entrada · US$ 2,50 / 1M saída |
| Imagem 2D | Nano Banana (`gemini-2.5-flash-image`) | por imagem | **US$ 0,039** |
| Imagem 2D | Nano Banana 2 (`gemini-3.1-flash-image`) | por imagem 1K | US$ 0,067 |
| Imagem 2D | Nano Banana 2 Lite | por imagem 1K | US$ 0,0336 |
| Imagem 2D | Nano Banana Pro (`gemini-3-pro-image`) | por imagem 1K/2K | US$ 0,134 |
| Remoção de fundo | rembg self-hosted | CPU local | US$ 0 |
| 3D | Meshy image-to-3D | créditos | 20 créditos (só malha) · **30** (com textura 2K/4K) · 35 (8K) |
| 3D | Meshy **Pro** (menor plano com API) | assinatura | **US$ 20/mês → 1.000 créditos** (US$ 0,02/crédito) |
| 3D | Meshy **Ultra** | assinatura | US$ 100/mês → 10.000 créditos (US$ 0,01/crédito) |
| Hospedagem (opcional) | Hetzner CX23 (2 vCPU, 4 GB, 40 GB) | mensal | € 5,49/mês (UE) |

O plano Free do Meshy **não gera chave de API**, então a assinatura Pro é o único custo fixo, e só é
obrigatória se você usar o modo **2D + 3D**. No modo **Apenas 2D**, o Meshy não é chamado.
Tasks que falham são reembolsadas em créditos.

## 2. Custo por asset

Premissa: **3 imagens 2D por asset** (1ª geração + 2 rodadas de "Modificar"). Cada chamada ao LLM usa
~600 tokens de entrada e ~150 de saída.

| Item | Cálculo | Custo |
|---|---|---|
| LLM (gpt-4.1-mini) | 3 × (600 × 0,40 + 150 × 1,60) / 1M | US$ 0,0015 |
| Imagens 2D (Nano Banana) | 3 × 0,039 | US$ 0,117 |
| Remoção de fundo | local | US$ 0 |
| Meshy com textura, plano Pro | 30 créditos × 0,02 | US$ 0,60 |
| **Total por asset** | | **≈ US$ 0,72** |

**Modo Apenas 2D:** o mesmo cálculo, sem a linha do Meshy, dá **≈ US$ 0,12 por asset**, sem custo fixo.

No modo 2D + 3D, o Meshy responde por **~83%** do custo. O LLM é desprezível: mesmo com o Gemini 2.5 Flash e os tokens de
"thinking" (~1.000 de saída), fica em ~US$ 0,01 por asset.

Variações:

- Plano Ultra: US$ 0,30 no 3D, total ≈ **US$ 0,42 por asset**.
- Nano Banana Pro nas imagens: +US$ 0,29, total ≈ US$ 1,00 por asset (Pro).
- Só malha, sem textura (20 créditos): −US$ 0,20.

## 3. Cenários mensais

| Cenário | Assets/mês | Meshy | Imagens 2D | LLM | Infra | **Total/mês** |
|---|---|---|---|---|---|---|
| **Só 2D** | 100 (Apenas 2D) | — | US$ 11,70 | US$ 0,15 | PC local | **≈ US$ 12** |
| **Parado** (3D disponível) | 0 | US$ 20 (Pro) | 0 | 0 | PC local | **US$ 20** |
| **Hobby** | 10 | US$ 20 (usa 300 de 1.000 créditos) | US$ 1,17 | US$ 0,02 | PC local | **≈ US$ 21** |
| **Indie** (limite do Pro) | 33 | US$ 20 (990 créditos) | US$ 3,86 | US$ 0,05 | PC local ou VPS € 5,49 | **≈ US$ 24** (+ € 5,49 com VPS) |
| **Estúdio** | 300 | US$ 100 (Ultra, 9.000 créditos) | US$ 35,10 | US$ 0,45 | VPS € 5,49 | **≈ US$ 136 + € 5,49** |

Na conversão para reais, use o câmbio do dia do cartão mais o IOF. Exemplo: a R$ 5,50/US$, o cenário Indie
sai por ~R$ 132/mês antes do IOF.

## 4. Infraestrutura

- **PC local (setup atual)**: custo zero além da energia. Consumo medido: rembg ~2 GB de RAM e n8n ~0,3 GB.
  O rembg só usa CPU durante a chamada (~0,5 s por imagem 1024²). Se o PC ficar ligado 24/7 só por isso,
  a conta é: `watts × 730 h ÷ 1000 × tarifa`. Exemplo: 60 W × 730 h = 43,8 kWh; a R$ 0,90/kWh dá ≈ R$ 39/mês.
- **VPS (Hetzner CX23, € 5,49)**: os 4 GB de RAM atendem rembg + n8n. Nesse caso a "Pasta Local" passa a ser
  o disco do servidor. Para os arquivos chegarem no seu PC, você precisaria de algo como Syncthing, rclone
  ou upload para S3/Drive.
- **n8n Cloud** (Starter ~US$ 20/mês, 2.500 execuções): não recomendado, porque não grava no seu disco nem roda
  o container do rembg.

## 5. Como reduzir o custo

1. **Aprovar com menos iterações.** Cada "Modificar" custa US$ 0,039, bem menos que o 3D, então vale iterar
   no 2D antes de aprovar. O caro é gerar 3D de uma imagem ruim.
2. Gerar **só a malha** (20 créditos) e texturizar depois, apenas nos assets que passarem na revisão.
3. Com mais de **~100 assets/mês**, o **Ultra** fica mais barato que o Pro com créditos extras. Os créditos
   avulsos custam quase o dobro: ~US$ 0,04 cada, então 20 + (30n − 1.000) × 0,04 > 100 quando n > 100.
   Os planos intermediários (Premium US$ 40/3.000 créditos, Studio US$ 60/4.000) podem ser uma opção, mas
   as fontes não confirmam se incluem API.
4. Usar o **Gemini no free tier** para o prompting (limite de requisições baixo, mas suficiente para uso pessoal).

## 6. Nano Banana real (resolvido)

O pipeline já chama a API real do Gemini (`gemini-2.5-flash-image`). A imagem volta em base64, é gravada em
`previews/` e exibida na revisão por um webhook. Detalhes no README, seção "Nano Banana (API do Gemini)".
Aprovar **não** gera uma nova chamada paga: o binário da última iteração é reaproveitado para o rembg e o Meshy.

## Fontes

- Google Gemini API pricing: https://ai.google.dev/gemini-api/docs/pricing
- OpenAI API pricing: https://developers.openai.com/api/docs/pricing
- Meshy API (créditos por chamada): https://docs.meshy.ai/en/api/pricing
- Meshy planos: https://costbench.com/software/ai-3d-generation/meshy/ · https://meshyiai.com/api-pricing/
- Hetzner CX23: https://costgoat.com/pricing/hetzner · https://northflank.com/blog/hetzner-cloud-server-price-increases
- n8n Cloud: https://n8n.school/blog/n8n-pricing
