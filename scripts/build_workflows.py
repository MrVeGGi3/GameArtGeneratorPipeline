#!/usr/bin/env python3
"""Gera os JSONs importáveis do n8n a partir dos templates em html/.

    python3 scripts/build_workflows.py

Saída: workflows/game-asset-pipeline.json e workflows/mock-apis.json
Edite o HTML em html/ e rode de novo — o HTML é embutido nos nós Code.
"""
import json
import re
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = ROOT / "html"
OUT = ROOT / "workflows"

# IDs determinísticos: reimportar gera os mesmos IDs/URLs de webhook.
NS = uuid.UUID("6f1c2a52-8a57-4d8e-9c3e-2b1f6c0d9a11")


def uid(name: str) -> str:
    return str(uuid.uuid5(NS, name))


def template(file: str) -> str:
    html = (HTML / file).read_text(encoding="utf-8")
    return re.sub(r"<!--.*?-->\s*", "", html, count=1, flags=re.S)


def node(name, type_, version, pos, params, **extra):
    n = {
        "parameters": params,
        "id": uid("node:" + name),
        "name": name,
        "type": type_,
        "typeVersion": version,
        "position": pos,
    }
    n.update(extra)
    return n


def sticky(name, pos, w, h, content, color=7):
    return node(name, "n8n-nodes-base.stickyNote", 1, pos,
                {"content": content, "width": w, "height": h, "color": color})


def webhook(name, pos, path, method, response_mode, **params):
    p = {"httpMethod": method, "path": path, "responseMode": response_mode, "options": {}}
    p.update(params)
    return node(name, "n8n-nodes-base.webhook", 2, pos, p, webhookId=uid("webhook:" + path))


def set_node(name, pos, fields, include_other=False):
    assignments = [
        {"id": uid(f"{name}:{k}"), "name": k, "value": v, "type": t}
        for k, v, t in fields
    ]
    p = {"assignments": {"assignments": assignments}, "options": {}}
    if include_other:
        p["includeOtherFields"] = True
    return node(name, "n8n-nodes-base.set", 3.4, pos, p)


def code(name, pos, js):
    return node(name, "n8n-nodes-base.code", 2, pos, {"jsCode": js.strip() + "\n"})


def cond(id_, left, op_type, operation, right=None, single=False):
    c = {"id": uid(id_), "leftValue": left, "operator": {"type": op_type, "operation": operation}}
    if single:
        c["operator"]["singleValue"] = True
    else:
        c["rightValue"] = right
    return c


def conditions(*cs):
    return {
        "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose", "version": 2},
        "conditions": list(cs),
        "combinator": "or",
    }


def switch_rule(key, conds):
    return {"conditions": conditions(*conds), "renameOutput": True, "outputKey": key}


def respond_html(name, pos, body_expr):
    return node(name, "n8n-nodes-base.respondToWebhook", 1.1, pos, {
        "respondWith": "text",
        "responseBody": body_expr,
        "options": {"responseHeaders": {"entries": [
            {"name": "Content-Type", "value": "text/html; charset=utf-8"},
            {"name": "Cache-Control", "value": "no-store"},
        ]}},
    })


class Graph:
    def __init__(self):
        self.nodes, self.conns = [], {}

    def add(self, *ns):
        self.nodes.extend(ns)
        return ns[0] if len(ns) == 1 else ns

    def link(self, src, dst, out=0):
        outs = self.conns.setdefault(src["name"], {"main": []})["main"]
        while len(outs) <= out:
            outs.append([])
        outs[out].append({"node": dst["name"], "type": "main", "index": 0})

    def chain(self, *ns):
        for a, b in zip(ns, ns[1:]):
            self.link(a, b)

    def workflow(self, name, wf_id, tags=()):
        return {
            "name": name,
            "nodes": self.nodes,
            "connections": self.conns,
            "pinData": {},
            "settings": {"executionOrder": "v1", "saveManualExecutions": True,
                         "saveDataSuccessExecution": "all", "saveDataErrorExecution": "all"},
            "meta": {"templateCredsSetupCompleted": False},
            "id": wf_id,
            "active": False,
            "tags": [],
        }


# ---------------------------------------------------------------------------
# Textos do LLM
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are a senior Game Technical Artist and prompt engineer.
Convert the user's asset idea (it may be written in Portuguese) into ONE production-ready English prompt for a text-to-image model. The generated image will be fed directly into an image-to-3D generator (Meshy / Tripo3D), so it must be a clean reference sheet of a single asset.

Mandatory rules:
- Single isolated subject, fully inside the frame, centered, nothing cropped.
- Strict FRONT view, orthographic-like camera at eye level, no perspective distortion. Characters in a neutral A-pose, arms slightly away from the body.
- Pure solid white (#FFFFFF) or neutral light-gray seamless background. No floor, no environment, no props that are not part of the asset, no text, no watermark, no frame.
- Soft, even, diffuse studio lighting. No cast shadows, no dramatic rim light, no bloom, no depth of field, no motion blur.
- Clear readable silhouette and well-defined materials/colors.
- Adapt to the target 3D style:
  * Voxel: made of uniform cubic voxels, blocky Minecraft/MagicaVoxel look, flat colors per voxel, crisp grid edges.
  * Low Poly: faceted low-polygon look, flat-shaded triangles, limited color palette, stylized game asset.
  * Realista: photorealistic PBR materials, high-detail textures, physically plausible proportions, AAA game asset.
- Apply EVERY item of the feedback history. If two items conflict, the most recent one wins.

Output ONLY the final prompt: one paragraph, at most 120 words, no quotes, no labels, no explanations."""

USER_PROMPT = """={{ 'Asset idea: ' + $('Context').first().json.basePrompt + '\\n' +
'Target 3D style: ' + $('Context').first().json.style + '\\n' +
'Feedback history (oldest first):\\n' +
($('Context').first().json.feedbackHistory.length
  ? $('Context').first().json.feedbackHistory.map((f, i) => (i + 1) + '. ' + f).join('\\n')
  : 'None (first iteration).') }}"""


# ---------------------------------------------------------------------------
# Workflow principal
# ---------------------------------------------------------------------------
def build_pipeline():
    g = Graph()
    X = lambda col: 200 + col * 220  # noqa: E731
    Y1, Y2, Y3, Y4 = 300, 760, 1200, 1640

    # ---- Sticky notes -----------------------------------------------------
    g.add(
        sticky("Note: Entrada", [100, 60], 700, 420,
               "## 1 · Entrada (Form)\nO formulário nativo chama o pipeline via HTTP e redireciona o navegador "
               "para a página de revisão.\n\n> O n8n não permite *Respond to Webhook* em execuções iniciadas por "
               "Form Trigger, por isso o pipeline roda numa execução própria iniciada pelo Webhook "
               "`game-art/start`.", 6),
        sticky("Note: Prompt", [100, 520], 2700, 440,
               "## 2 · IA de Prompting → 3 · Geração 2D (Nano Banana / Gemini)\nA imagem volta em base64, vira arquivo e é gravada em `/previews/<execução>.png`. O provedor (OpenAI/Gemini) é escolhido no "
               "form. **Context** é o ponto de entrada do loop de feedback. Edite URLs/limites só no nó **Config**.", 4),
        sticky("Note: HITL", [100, 1000], 2700, 400,
               "## 4 · Human-in-the-Loop\n**Respond: Open Review** redireciona o navegador para a página HTML "
               "(Webhook `game-art/review`), que faz POST para `$execution.resumeUrl` do nó **Wait for Review**. "
               "*Modificar* volta ao **Context** com o feedback acumulado; *Aprovar* segue para o 3D.", 3),
        sticky("Note: 3D", [100, 1440], 2700, 520,
               "## 4b · Remoção de fundo (rembg) → 5 · Meshy image-to-3D (polling) → 6 · Download + Salvar\nApós aprovar: baixa a imagem, remove o fundo no rembg e envia o PNG (data URI base64) ao Meshy. Wait 15s ↔ Get Status até "
               "`SUCCEEDED` (máx. `maxPollAttempts`). O caminho do form é convertido para o caminho do container "
               "(`hostRoot` → `containerRoot`).", 5),
        sticky("Note: Review", [900, 60], 1100, 620,
               "## Página de revisão (stateless)\nRenderiza o HTML a partir da query string (imagem, prompt, "
               "resumeUrl). Tudo é HTML-escaped e o `resume` só é aceito se apontar para `/webhook-waiting/`.\n\n**Preview Image** serve `/previews/<id>.png` (só aceita nomes `^\\d+\\.png$`).", 7),
    )

    # ---- 1. Form ----------------------------------------------------------
    form = g.add(node("Form: New Asset", "n8n-nodes-base.formTrigger", 2.2, [X(0), 200], {
        "formTitle": "Game Art Generator",
        "formDescription": "Descreva o asset. Você revisará a imagem 2D antes de gerar o modelo 3D (.glb).",
        "formFields": {"values": [
            {"fieldLabel": "Prompt Base", "fieldType": "textarea", "requiredField": True,
             "placeholder": "Ex.: cavaleiro anão com machado de runas"},
            {"fieldLabel": "Caminho da Pasta Local", "requiredField": True,
             "placeholder": "C:\\Assets\\personagens  (ou /assets/personagens)"},
            {"fieldLabel": "Estilo 3D", "fieldType": "dropdown", "requiredField": True,
             "fieldOptions": {"values": [{"option": "Voxel"}, {"option": "Low Poly"}, {"option": "Realista"}]}},
            {"fieldLabel": "Provedor LLM", "fieldType": "dropdown", "requiredField": True,
             "fieldOptions": {"values": [{"option": "OpenAI"}, {"option": "Gemini"}]}},
        ]},
        "options": {"path": "game-art", "buttonLabel": "Gerar imagem 2D", "appendAttribution": False},
    }, webhookId=uid("form:game-art")))

    start_call = g.add(node("Start Pipeline Run", "n8n-nodes-base.httpRequest", 4.2, [X(1), 200], {
        "method": "POST",
        # Chamada interna: o n8n sempre escuta na 5678 dentro do container.
        "url": "http://localhost:5678/webhook/game-art/start",
        "sendBody": True,
        "specifyBody": "json",
        "jsonBody": "={{ JSON.stringify({ basePrompt: $json['Prompt Base'], outputFolder: $json['Caminho da Pasta Local'], "
                    "style: $json['Estilo 3D'], provider: $json['Provedor LLM'] }) }}",
        "options": {
            "redirect": {"redirect": {"followRedirects": False}},
            "response": {"response": {"fullResponse": True, "neverError": True}},
            "timeout": 600000,
        },
    }))

    started = g.add(node("Pipeline Started?", "n8n-nodes-base.if", 2.2, [X(2), 200], {
        "conditions": {**conditions(cond("start:ok", "={{ $json.headers?.location }}", "string", "notEmpty",
                                         single=True)), "combinator": "and"},
        "options": {},
    }))
    # Form "completion" com redirect: o n8n mantém o navegador na página de espera do form
    # e o redireciona para a página de revisão assim que este nó executa.
    redirect = g.add(node("Form: Open Review", "n8n-nodes-base.form", 1, [X(3), 100], {
        "operation": "completion",
        "respondWith": "redirect",
        "redirectUrl": "={{ $json.headers.location }}",
    }, webhookId=uid("form:open-review")))
    failed = g.add(node("Form: Start Failed", "n8n-nodes-base.form", 1, [X(3), 300], {
        "operation": "completion",
        "respondWith": "text",
        "completionTitle": "Falha ao iniciar o pipeline",
        "completionMessage": "={{ 'HTTP ' + $json.statusCode + ' — veja a aba Executions do n8n (workflow Game Art Pipeline).' }}",
    }, webhookId=uid("form:start-failed")))
    g.chain(form, start_call, started)
    g.link(started, redirect, 0)
    g.link(started, failed, 1)

    # ---- Review page (stateless) -----------------------------------------
    review_hook = g.add(webhook("Review Page", [X(4), 200], "game-art/review", "GET", "responseNode"))
    review_render = g.add(code("Render Review Page", [X(5), 200], f"""
// Renderiza html/review-page.html a partir da query string (sem estado no servidor).
const TEMPLATE = {json.dumps(template("review-page.html"), ensure_ascii=False)};

const q = $input.first().json.query ?? {{}};
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({{ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }}[c]));
// (o sandbox do Code node não expõe `URL`, por isso validação via regex)
const httpUrl = (u) => (/^https?:\\/\\/[^\\s"'<>]+$/i.test(String(u ?? '')) ? String(u) : '');

const resume = httpUrl(q.resume);
if (!/^https?:\\/\\/[^/]+\\/webhook-waiting\\//i.test(resume)) {{
  return [{{ json: {{ status: 400, html: '<h1>Link de revisão inválido ou expirado.</h1>' }} }}];
}}

const values = {{
  IMAGE_URL: httpUrl(q.img),
  PROMPT: q.prompt,
  BASE_PROMPT: q.base,
  STYLE: q.style,
  ITERATION: q.it,
  RESUME_URL: resume,
}};
const html = TEMPLATE.replace(/{{{{(\\w+)}}}}/g, (m, k) => (k in values ? esc(values[k]) : m));
return [{{ json: {{ status: 200, html }} }}];
"""))
    review_respond = g.add(node("Respond: Review HTML", "n8n-nodes-base.respondToWebhook", 1.1, [X(6), 200], {
        "respondWith": "text",
        "responseBody": "={{ $json.html }}",
        "options": {
            "responseCode": "={{ $json.status }}",
            "responseHeaders": {"entries": [
                {"name": "Content-Type", "value": "text/html; charset=utf-8"},
                {"name": "Cache-Control", "value": "no-store"},
            ]},
        },
    }))
    g.chain(review_hook, review_render, review_respond)

    # ---- Preview image (serve a prévia 2D gravada em /previews) ----------
    preview_hook = g.add(webhook("Preview Image", [X(4), 400], "game-art/preview", "GET", "responseNode"))
    preview_check = g.add(code("Validate Preview Name", [X(5), 400], r"""
// Só aceita "<executionId>.png" (evita path traversal).
// Execução separada do pipeline: o nó Config não roda aqui, então a pasta é fixa (= Config.previewsRoot).
const PREVIEWS_ROOT = '/previews';
const f = String($input.first().json.query?.f ?? '');
const ok = /^\d+\.png$/.test(f);
return [{ json: { ok, path: ok ? `${PREVIEWS_ROOT}/${f}` : '' } }];
"""))
    preview_ok = g.add(node("Valid Name?", "n8n-nodes-base.if", 2.2, [X(6), 400], {
        "conditions": {**conditions(cond("pv:ok", "={{ $json.ok }}", "boolean", "true", single=True)),
                       "combinator": "and"},
        "options": {},
    }))
    preview_read = g.add(node("Read Preview", "n8n-nodes-base.readWriteFile", 1, [X(7), 340], {
        "operation": "read",
        "fileSelector": "={{ $json.path }}",
        "options": {"dataPropertyName": "data"},
    # O seletor é um glob: arquivo inexistente = 0 itens (sem erro). alwaysOutputData garante 1 item.
    }, onError="continueErrorOutput", alwaysOutputData=True))
    preview_found = g.add(node("Found?", "n8n-nodes-base.if", 2.2, [X(8), 340], {
        "conditions": {**conditions(cond("pv:found", "={{ !!$binary?.data }}", "boolean", "true", single=True)),
                       "combinator": "and"},
        "options": {},
    }))
    preview_respond = g.add(node("Respond: Preview PNG", "n8n-nodes-base.respondToWebhook", 1.1, [X(9), 300], {
        "respondWith": "binary",
        "responseDataSource": "set",
        "inputFieldName": "data",
        "options": {"responseHeaders": {"entries": [
            {"name": "Content-Type", "value": "image/png"},
            {"name": "Cache-Control", "value": "no-store"},
        ]}},
    }))
    preview_404 = g.add(node("Respond: Preview 404", "n8n-nodes-base.respondToWebhook", 1.1, [X(9), 500], {
        "respondWith": "text",
        "responseBody": "Preview not found",
        "options": {"responseCode": 404},
    }))
    g.chain(preview_hook, preview_check, preview_ok)
    g.link(preview_ok, preview_read, 0)
    g.link(preview_ok, preview_404, 1)
    g.link(preview_read, preview_found, 0)
    g.link(preview_read, preview_404, 1)
    g.link(preview_found, preview_respond, 0)
    g.link(preview_found, preview_404, 1)

    # ---- 2. Prompting ----------------------------------------------------
    start = g.add(webhook("Start (Webhook)", [X(0), Y2], "game-art/start", "POST", "responseNode"))
    config = g.add(set_node("Config", [X(1), Y2], [
        # Nano Banana real (Gemini API, credencial "Google Gemini(PaLM) Api"). Troque o modelo na URL:
        #   gemini-2.5-flash-image          Nano Banana    (US$ 0,039/img)
        #   gemini-3.1-flash-image-preview  Nano Banana 2  (US$ 0,067/img)
        #   gemini-3-pro-image-preview      Nano Banana Pro (US$ 0,134/img)
        # Mock (workflow "Game Art - Mock APIs"): http://localhost:5678/webhook/mock/nanobanana/generate
        ("nanoBananaUrl",
         "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image:generateContent", "string"),
        # Meshy: mock. Reais: https://api.meshy.ai/openapi/v1/image-to-3d  e  .../image-to-3d/ (prefixo do GET)
        ("meshyCreateUrl", "http://localhost:5678/webhook/mock/meshy/image-to-3d", "string"),
        ("meshyStatusUrlPrefix", "http://localhost:5678/webhook/mock/meshy/status?task_id=", "string"),
        ("pollIntervalSec", 15, "number"),
        ("maxPollAttempts", 40, "number"),
        # Pasta do HOST montada no container (ASSETS_HOST_PATH) e o destino dentro do container.
        ("hostRoot", "C:\\Assets", "string"),
        ("containerRoot", "/assets", "string"),
        # rembg self-hosted (serviço "rembg" do docker-compose). Modelos: u2net, isnet-general-use,
        # isnet-anime, birefnet-general, birefnet-general-lite ...
        ("rembgUrl", "http://rembg:7000/api/remove", "string"),
        ("rembgModel", "isnet-general-use", "string"),
        # Pasta (no container) das prévias 2D exibidas na página de revisão.
        ("previewsRoot", "/previews", "string"),
    ]))
    init_ctx = g.add(set_node("Init Context", [X(2), Y2], [
        ("basePrompt", "={{ $('Start (Webhook)').first().json.body.basePrompt }}", "string"),
        ("style", "={{ $('Start (Webhook)').first().json.body.style || 'Low Poly' }}", "string"),
        ("provider", "={{ $('Start (Webhook)').first().json.body.provider || 'OpenAI' }}", "string"),
        ("outputFolder", "={{ $('Start (Webhook)').first().json.body.outputFolder }}", "string"),
        ("feedbackHistory", "={{ [] }}", "array"),
        ("iteration", 1, "number"),
    ]))
    context = g.add(node("Context", "n8n-nodes-base.noOp", 1, [X(3), Y2], {}))

    provider = g.add(node("Switch Provider", "n8n-nodes-base.switch", 3.2, [X(4), Y2], {
        "rules": {"values": [
            switch_rule("OpenAI", [cond("sp:openai", "={{ $json.provider }}", "string", "equals", "OpenAI")]),
            switch_rule("Gemini", [cond("sp:gemini", "={{ $json.provider }}", "string", "equals", "Gemini")]),
        ]},
        "options": {"fallbackOutput": 0},
    }))
    openai = g.add(node("OpenAI: Build Prompt", "@n8n/n8n-nodes-langchain.openAi", 1.8, [X(5), Y2 - 100], {
        "resource": "text",
        "operation": "message",
        "modelId": {"__rl": True, "value": "gpt-4.1-mini", "mode": "list", "cachedResultName": "GPT-4.1-MINI"},
        "messages": {"values": [
            {"content": SYSTEM_PROMPT, "role": "system"},
            {"content": USER_PROMPT},
        ]},
        "options": {"temperature": 0.7, "maxTokens": 400},
    }, retryOnFail=True, maxTries=3, waitBetweenTries=2000))
    gemini = g.add(node("Gemini: Build Prompt", "@n8n/n8n-nodes-langchain.googleGemini", 1, [X(5), Y2 + 100], {
        "resource": "text",
        "operation": "message",
        "modelId": {"__rl": True, "value": "models/gemini-2.5-flash", "mode": "list",
                    "cachedResultName": "models/gemini-2.5-flash"},
        "messages": {"values": [{"content": USER_PROMPT}]},
        "options": {"systemMessage": SYSTEM_PROMPT, "temperature": 0.7, "maxOutputTokens": 400},
    }, retryOnFail=True, maxTries=3, waitBetweenTries=2000))
    normalize = g.add(set_node("Normalize Prompt", [X(6), Y2], [
        ("imagePrompt",
         "={{ String($json.message?.content ?? $json.content?.parts?.map(p => p.text).join('') ?? $json.text ?? '')"
         ".trim().replace(/^[\"'`\\s]+|[\"'`\\s]+$/g, '') }}", "string"),
        ("negativePrompt",
         "background scenery, floor, cast shadows, text, watermark, cropped, multiple objects, perspective distortion, blur",
         "string"),
    ]))

    # ---- 3. NanoBanana (Gemini generateContent) --------------------------
    nano = g.add(node("NanoBanana: Generate 2D", "n8n-nodes-base.httpRequest", 4.2, [X(7), Y2], {
        "method": "POST",
        "url": "={{ $('Config').first().json.nanoBananaUrl }}",
        "authentication": "predefinedCredentialType",
        "nodeCredentialType": "googlePalmApi",
        "sendBody": True,
        "specifyBody": "json",
        "jsonBody": "={{ JSON.stringify({\n"
                    "  contents: [{ role: 'user', parts: [{ text: $json.imagePrompt + '\\nAvoid: ' + $json.negativePrompt + '.' }] }],\n"
                    "  generationConfig: { responseModalities: ['IMAGE'], imageConfig: { aspectRatio: '1:1' } }\n"
                    "}) }}",
        "options": {"timeout": 180000},
    }, retryOnFail=True, maxTries=3, waitBetweenTries=3000))
    extract_img = g.add(code("Extract Image", [X(8), Y2], r"""
// A API do Gemini devolve a imagem em base64: candidates[0].content.parts[].inlineData
const r = $input.first().json;
const cand = r.candidates?.[0];
const part = cand?.content?.parts?.find((p) => p.inlineData?.data);
if (!part) {
  const reason = r.promptFeedback?.blockReason ?? cand?.finishReason ?? 'desconhecido';
  const text = cand?.content?.parts?.map((p) => p.text).filter(Boolean).join(' ') ?? '';
  // Sem ':' na mensagem: o n8n corta a mensagem de erro no primeiro dois-pontos.
  const msg = `Nano Banana não retornou imagem (motivo ${reason}). ${text}`.replace(/:/g, ' -').trim();
  throw new Error(msg.slice(0, 800));
}
return [{ json: { imageBase64: part.inlineData.data, mimeType: part.inlineData.mimeType ?? 'image/png' } }];
"""))
    to_file = g.add(node("Image to File", "n8n-nodes-base.convertToFile", 1.1, [X(9), Y2], {
        "operation": "toBinary",
        "sourceProperty": "imageBase64",
        "binaryPropertyName": "data",
        "options": {"dataIsBase64": True, "mimeType": "={{ $json.mimeType }}",
                    "fileName": "={{ $execution.id + '.png' }}"},
    }))
    save_preview = g.add(node("Save Preview", "n8n-nodes-base.readWriteFile", 1, [X(10), Y2], {
        "operation": "write",
        # Um arquivo por execução, sobrescrito a cada iteração do loop de feedback.
        "fileName": "={{ $('Config').first().json.previewsRoot + '/' + $execution.id + '.png' }}",
        "dataPropertyName": "data",
        "options": {},
    }))
    link = g.add(code("Build Review Link", [X(11), Y2], r"""
// Monta o link da página de revisão. A imagem é servida pelo webhook "Preview Image".
// A origem vem do próprio resumeUrl (WEBHOOK_URL), então funciona em qualquer host/porta.
const ctx = $('Context').first().json;
const imagePrompt = $('Normalize Prompt').first().json.imagePrompt;
const resumeUrl = $execution.resumeUrl;
const origin = resumeUrl.match(/^https?:\/\/[^/]+/i)[0];
const imageUrl = `${origin}/webhook/game-art/preview?f=${$execution.id}.png&v=${ctx.iteration}`;

const params = {
  img: imageUrl,
  prompt: imagePrompt,
  base: ctx.basePrompt,
  style: ctx.style,
  it: String(ctx.iteration),
  resume: resumeUrl,
};
const qs = Object.entries(params).map(([k, v]) => `${k}=${encodeURIComponent(v ?? '')}`).join('&');
const reviewUrl = `${origin}/webhook/game-art/review?${qs}`;

return [{ json: { imageUrl, imagePrompt, iteration: ctx.iteration, reviewUrl } }];
"""))

    # ---- 4. HITL ---------------------------------------------------------
    open_review = g.add(node("Respond: Open Review", "n8n-nodes-base.respondToWebhook", 1.1, [X(1), Y3], {
        "respondWith": "redirect",
        "redirectURL": "={{ $json.reviewUrl }}",
        # 303 See Other: o navegador troca o POST do form por um GET na página de revisão.
        "options": {"responseCode": 303},
    }))
    wait_review = g.add(node("Wait for Review", "n8n-nodes-base.wait", 1.1, [X(2), Y3], {
        "resume": "webhook",
        "httpMethod": "POST",
        "responseMode": "responseNode",
        "limitWaitTime": True,
        "limitType": "afterTimeInterval",
        "resumeAmount": 24,
        "resumeUnit": "hours",
        "options": {},
    }, webhookId=uid("wait:review")))
    decision = g.add(node("Decision", "n8n-nodes-base.switch", 3.2, [X(3), Y3], {
        "rules": {"values": [
            switch_rule("Aprovar", [cond("dec:approve", "={{ $json.body?.action }}", "string", "equals", "approve")]),
            switch_rule("Modificar", [cond("dec:modify", "={{ $json.body?.action }}", "string", "equals", "modify")]),
        ]},
        "options": {},
    }))
    apply_fb = g.add(code("Apply Feedback", [X(4), Y3 + 120], """
// Acumula o feedback e volta para o "Context" (loop).
const ctx = $('Context').first().json;
const feedback = String($input.first().json.body?.feedback ?? '').trim();
const history = [...(ctx.feedbackHistory ?? [])];
if (feedback) history.push(feedback);
return [{ json: { ...ctx, feedbackHistory: history, iteration: (ctx.iteration ?? 1) + 1 } }];
"""))
    approved_render = g.add(code("Render Approved Page", [X(4), Y3 - 100], f"""
const TEMPLATE = {json.dumps(template("approved-page.html"), ensure_ascii=False)};
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({{ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }}[c]));
const ctx = $('Context').first().json;
const values = {{
  IMAGE_URL: $('Build Review Link').first().json.imageUrl,
  STYLE: ctx.style,
  OUTPUT_FOLDER: ctx.outputFolder,
  ITERATION: ctx.iteration,
}};
return [{{ json: {{ html: TEMPLATE.replace(/{{{{(\\w+)}}}}/g, (m, k) => (k in values ? esc(values[k]) : m)) }} }}];
"""))
    approved_respond = g.add(respond_html("Respond: Approved", [X(5), Y3 - 100], "={{ $json.html }}"))

    # ---- 4b. Remoção de fundo (rembg) -----------------------------------
    download_2d = g.add(code("Restore Approved Image", [X(6), Y3 - 100], """
// Recupera o binário da imagem aprovada (última iteração) salvo nos dados da execução.
return [{ json: {}, binary: $('Image to File').first().binary }];
"""))
    remove_bg = g.add(node("Remove Background", "n8n-nodes-base.httpRequest", 4.2, [X(7), Y3 - 100], {
        "method": "POST",
        "url": "={{ $('Config').first().json.rembgUrl }}",
        "sendBody": True,
        "contentType": "multipart-form-data",
        "bodyParameters": {"parameters": [
            {"parameterType": "formBinaryData", "name": "file", "inputDataFieldName": "data"},
            {"name": "model", "value": "={{ $('Config').first().json.rembgModel }}"},
        ]},
        "options": {"response": {"response": {"responseFormat": "file", "outputPropertyName": "data"}},
                    # 1ª chamada baixa o modelo (~170 MB) e pode demorar.
                    "timeout": 600000},
    }, retryOnFail=True, maxTries=2, waitBetweenTries=5000))
    to_b64 = g.add(node("PNG to Base64", "n8n-nodes-base.extractFromFile", 1.1, [X(8), Y3 - 100], {
        "operation": "binaryToPropery",
        "binaryPropertyName": "data",
        "destinationKey": "imageBase64",
        "options": {"keepSource": "json"},
    }))

    # ---- 5. Meshy + polling ---------------------------------------------
    meshy_create = g.add(node("Meshy: Create Task", "n8n-nodes-base.httpRequest", 4.2, [X(9), Y3 - 100], {
        "method": "POST",
        "url": "={{ $('Config').first().json.meshyCreateUrl }}",
        "sendBody": True,
        "specifyBody": "json",
        "jsonBody": "={{ (() => { const s = $('Context').first().json.style; return JSON.stringify({\n"
                    "  image_url: 'data:image/png;base64,' + $json.imageBase64,\n"
                    "  ai_model: 'latest',\n"
                    "  topology: s === 'Realista' ? 'quad' : 'triangle',\n"
                    "  target_polycount: { 'Voxel': 8000, 'Low Poly': 3000, 'Realista': 60000 }[s] ?? 10000,\n"
                    "  should_remesh: true,\n"
                    "  should_texture: true,\n"
                    "  enable_pbr: s === 'Realista',\n"
                    "  symmetry_mode: 'auto'\n"
                    "}); })() }}",
        "options": {"timeout": 60000},
    }, retryOnFail=True, maxTries=3, waitBetweenTries=3000))
    task = g.add(set_node("Task Info", [X(10), Y3 - 100], [
        ("taskId", "={{ $json.result ?? $json.task_id ?? $json.id }}", "string"),
    ]))
    wait_poll = g.add(node("Wait 15s", "n8n-nodes-base.wait", 1.1, [X(1), Y4], {
        "amount": "={{ $('Config').first().json.pollIntervalSec }}",
        "unit": "seconds",
    }, webhookId=uid("wait:poll")))
    get_status = g.add(node("Meshy: Get Status", "n8n-nodes-base.httpRequest", 4.2, [X(2), Y4], {
        "url": "={{ $('Config').first().json.meshyStatusUrlPrefix + $('Task Info').first().json.taskId }}",
        "options": {"timeout": 30000},
    }, retryOnFail=True, maxTries=3, waitBetweenTries=5000))
    status = g.add(node("Status", "n8n-nodes-base.switch", 3.2, [X(3), Y4], {
        "rules": {"values": [
            switch_rule("Pronto", [cond("st:ok", "={{ $json.status }}", "string", "equals", "SUCCEEDED")]),
            switch_rule("Falhou", [
                cond("st:failed", "={{ $json.status }}", "string", "equals", "FAILED"),
                cond("st:canceled", "={{ $json.status }}", "string", "equals", "CANCELED"),
                cond("st:expired", "={{ $json.status }}", "string", "equals", "EXPIRED"),
            ]),
        ]},
        "options": {"fallbackOutput": "extra", "renameFallbackOutput": "Em andamento"},
    }))
    can_poll = g.add(node("Keep Polling?", "n8n-nodes-base.if", 2.2, [X(4), Y4 + 180], {
        "conditions": {**conditions(cond(
            "poll:max", "={{ $runIndex + 1 }}", "number", "lt",
            "={{ $('Config').first().json.maxPollAttempts }}")), "combinator": "and"},
        "options": {},
    }))
    stop_failed = g.add(node("Stop: Meshy Failed", "n8n-nodes-base.stopAndError", 1, [X(4), Y4], {
        "errorMessage": "={{ 'Meshy task ' + $('Task Info').first().json.taskId + ' terminou com status ' + $json.status + ': ' + ($json.task_error?.message ?? '') }}",
    }))
    stop_timeout = g.add(node("Stop: Polling Timeout", "n8n-nodes-base.stopAndError", 1, [X(5), Y4 + 280], {
        "errorMessage": "={{ 'Meshy task ' + $('Task Info').first().json.taskId + ' não terminou após ' + $('Config').first().json.maxPollAttempts + ' tentativas.' }}",
    }))

    # ---- 6. Download + salvar -------------------------------------------
    download = g.add(node("Download GLB", "n8n-nodes-base.httpRequest", 4.2, [X(5), Y4 - 100], {
        "url": "={{ $json.model_urls.glb }}",
        "options": {"response": {"response": {"responseFormat": "file", "outputPropertyName": "data"}},
                    "timeout": 300000},
    }, retryOnFail=True, maxTries=3, waitBetweenTries=5000))
    build_path = g.add(code("Build File Path", [X(6), Y4 - 100], r"""
// Converte o caminho digitado no form (host) para o caminho dentro do container.
//   C:\Assets\herois  ->  /assets/herois     (hostRoot -> containerRoot, ver nó Config)
//   /assets/herois    ->  /assets/herois
//   herois            ->  /assets/herois     (relativo = dentro do containerRoot)
const cfg = $('Config').first().json;
const ctx = $('Context').first().json;
const norm = (p) => String(p ?? '').trim().replace(/\\/g, '/').replace(/\/+$/, '');
const under = (p, root) => root && (p.toLowerCase() === root.toLowerCase() || p.toLowerCase().startsWith(root.toLowerCase() + '/'));

const containerRoot = norm(cfg.containerRoot);
const hostRoot = norm(cfg.hostRoot);
let folder = norm(ctx.outputFolder);

if (!folder) folder = containerRoot;
else if (under(folder, containerRoot)) { /* já é caminho do container */ }
else if (under(folder, hostRoot)) folder = containerRoot + folder.slice(hostRoot.length);
else if (!folder.startsWith('/') && !/^[a-z]:/i.test(folder)) folder = `${containerRoot}/${folder}`;
else throw new Error(`A pasta "${ctx.outputFolder}" está fora da pasta montada. Use algo dentro de "${cfg.hostRoot}" (host) ou "${containerRoot}" (container).`);

if (folder.split('/').includes('..')) throw new Error('Caminho inválido: ".." não é permitido.');

const slug = (s, n) => String(s).normalize('NFD').replace(/[\u0300-\u036f]/g, '')
  .toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, n) || 'asset';
const stamp = $now.toFormat('yyyyMMdd-HHmmss'); // fuso = GENERIC_TIMEZONE
const base = `${slug(ctx.basePrompt, 40)}_${slug(ctx.style, 12)}_${stamp}`;

// Fallback caso a subpasta não exista (o nó de escrita do n8n não cria pastas):
// salva na raiz montada, prefixando o nome da subpasta.
const sub = folder.slice(containerRoot.length).replace(/^\//, '');
const prefix = sub ? slug(sub, 40) + '__' : '';

// 2 itens: modelo 3D (.glb) + imagem 2D sem fundo (.png)
const files = [
  { kind: 'glb', binary: $input.first().binary },
  { kind: 'png', binary: $('Remove Background').first().binary },
];
return files.map(({ kind, binary }) => {
  const fileName = `${base}.${kind}`;
  return { json: { kind, folder, fileName, filePath: `${folder}/${fileName}`, fallbackPath: `${containerRoot}/${prefix}${fileName}` }, binary };
});
"""))
    save = g.add(node("Save Files to Disk", "n8n-nodes-base.readWriteFile", 1, [X(7), Y4 - 100], {
        "operation": "write",
        "fileName": "={{ $json.filePath }}",
        "dataPropertyName": "data",
        "options": {},
    }, onError="continueErrorOutput"))
    fallback = g.add(code("Use Root Folder", [X(8), Y4 + 100], """
// A pasta de destino não existe dentro do container: salva na raiz montada para não perder o modelo.
return $('Build File Path').all().map((src) => ({
  json: { ...src.json, filePath: src.json.fallbackPath, warning: `Pasta ${src.json.folder} não existe; arquivos salvos na raiz.` },
  binary: src.binary,
}));
"""))
    save_root = g.add(node("Save Files to Root", "n8n-nodes-base.readWriteFile", 1, [X(9), Y4 + 100], {
        "operation": "write",
        "fileName": "={{ $json.filePath }}",
        "dataPropertyName": "data",
        "options": {},
    }))
    done = g.add(code("Done", [X(10), Y4 - 100], """
// Resumo da execução (um único item).
return [{ json: {
  savedTo: $input.all().map((i) => i.json.fileName),
  warning: $('Use Root Folder').isExecuted ? $('Use Root Folder').first().json.warning : '',
  taskId: $('Task Info').first().json.taskId,
  iterations: $('Context').first().json.iteration,
  imagePrompt: $('Build Review Link').first().json.imagePrompt,
} }];
"""))

    # ---- Conexões --------------------------------------------------------
    g.chain(start, config, init_ctx, context, provider)
    g.link(provider, openai, 0)
    g.link(provider, gemini, 1)
    g.link(openai, normalize)
    g.link(gemini, normalize)
    g.chain(normalize, nano, extract_img, to_file, save_preview, link, open_review, wait_review, decision)
    g.link(decision, approved_render, 0)
    g.link(decision, apply_fb, 1)
    g.link(apply_fb, context)                       # loop de feedback
    g.chain(approved_render, approved_respond, download_2d, remove_bg, to_b64, meshy_create, task,
            wait_poll, get_status, status)
    g.link(status, download, 0)
    g.link(status, stop_failed, 1)
    g.link(status, can_poll, 2)
    g.link(can_poll, wait_poll, 0)                   # loop de polling
    g.link(can_poll, stop_timeout, 1)
    g.chain(download, build_path, save, done)
    g.link(save, fallback, 1)
    g.chain(fallback, save_root, done)

    return g.workflow("Game Art Pipeline", "gameArtPipeline1")


# ---------------------------------------------------------------------------
# Mock APIs
# ---------------------------------------------------------------------------
def build_mock():
    g = Graph()
    g.add(sticky("Note: Mock", [60, 40], 1000, 180,
                 "## Mock APIs (Nano Banana/Gemini + Meshy)\nImitam as APIs reais (mesmo formato de resposta) para testar o pipeline sem custo. "
                 "A task do Meshy fica `IN_PROGRESS` por ~35 s e depois retorna `SUCCEEDED` com um .glb de exemplo "
                 "(Khronos Duck). Ative (Publish) este workflow antes de usar o pipeline.", 7))

    nb = g.add(webhook("Mock NanoBanana", [100, 280], "mock/nanobanana/generate", "POST", "lastNode"))
    nb_fetch = g.add(node("Fetch Placeholder", "n8n-nodes-base.httpRequest", 4.2, [320, 280], {
        # Mesmo contrato do Gemini generateContent: lê o prompt de contents[0].parts[0].text.
        "url": "={{ 'https://placehold.co/1024x1024/ffffff/333333/png?text=' + encodeURIComponent("
               "String($json.body?.contents?.[0]?.parts?.[0]?.text ?? 'asset').split(/\\s+/).slice(0, 8).join(' ')) }}",
        "options": {"response": {"response": {"responseFormat": "file", "outputPropertyName": "data"}}},
    }))
    nb_b64 = g.add(node("Placeholder to Base64", "n8n-nodes-base.extractFromFile", 1.1, [540, 280], {
        "operation": "binaryToPropery",
        "binaryPropertyName": "data",
        "destinationKey": "b64",
        "options": {},
    }))
    nb_code = g.add(code("Fake Gemini Response", [760, 280], """
// Formato da resposta real: imagem em base64 em candidates[0].content.parts[].inlineData
return [{ json: {
  candidates: [{
    content: { role: 'model', parts: [{ inlineData: { mimeType: 'image/png', data: $input.first().json.b64 } }] },
    finishReason: 'STOP',
  }],
  modelVersion: 'mock-nano-banana',
} }];
"""))
    mc = g.add(webhook("Mock Meshy Create", [100, 460], "mock/meshy/image-to-3d", "POST", "lastNode"))
    mc_code = g.add(code("Fake Task", [320, 460], """
// Mesmo formato do Meshy: { "result": "<task_id>" }. O timestamp vai no id (mock sem estado).
return [{ json: { result: 'mock-' + Date.now() } }];
"""))
    ms = g.add(webhook("Mock Meshy Status", [100, 640], "mock/meshy/status", "GET", "lastNode"))
    ms_code = g.add(code("Fake Status", [320, 640], """
const id = String($input.first().json.query?.task_id ?? '');
const started = Number(id.replace('mock-', ''));
if (!started) return [{ json: { id, status: 'FAILED', task_error: { message: 'task_id desconhecido' } } }];

const DURATION_MS = 35000;
const progress = Math.min(100, Math.round(((Date.now() - started) / DURATION_MS) * 100));
if (progress < 100) return [{ json: { id, status: progress < 5 ? 'PENDING' : 'IN_PROGRESS', progress } }];

return [{ json: {
  id,
  status: 'SUCCEEDED',
  progress: 100,
  model_urls: { glb: 'https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/Duck/glTF-Binary/Duck.glb' },
  thumbnail_url: 'https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/Duck/screenshot/screenshot.png',
} }];
"""))
    g.chain(nb, nb_fetch, nb_b64, nb_code)
    g.link(mc, mc_code)
    g.link(ms, ms_code)
    return g.workflow("Game Art - Mock APIs", "gameArtMockApis1")


def main():
    OUT.mkdir(exist_ok=True)
    for file, wf in (("game-asset-pipeline.json", build_pipeline()), ("mock-apis.json", build_mock())):
        (OUT / file).write_text(json.dumps(wf, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"workflows/{file}: {len(wf['nodes'])} nós")


if __name__ == "__main__":
    main()
