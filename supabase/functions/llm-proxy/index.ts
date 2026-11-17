import { createClient } from "https://esm.sh/@supabase/supabase-js@2.49.4";

const CORS_HEADERS: Record<string, string> = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Expose-Headers": "X-Quota-Used-Pct, X-Quota-Remaining-Pct, X-Quota-Reset-At",
};

interface ModelSpec {
  openrouterId: string;
  label: string;
  inputPer1M: number;
  outputPer1M: number;
  featured?: boolean;
}

const MODEL_REGISTRY: Record<string, ModelSpec> = {
  "deepseek/deepseek-v4.1-flash": {
    openrouterId: "deepseek/deepseek-v4.1-flash",
    label: "DeepSeek V4.1 Flash",
    inputPer1M: 0.051,
    outputPer1M: 0.203,
    featured: true,
  },
  "deepseek-v4.1-flash": {
    openrouterId: "deepseek/deepseek-v4.1-flash",
    label: "DeepSeek V4.1 Flash",
    inputPer1M: 0.051,
    outputPer1M: 0.203,
  },
  "openai/gpt-6-luna": {
    openrouterId: "openai/gpt-6-luna",
    label: "GPT-6 Luna",
    inputPer1M: 0.10,
    outputPer1M: 0.50,
    featured: true,
  },
  "gpt-6-luna": {
    openrouterId: "openai/gpt-6-luna",
    label: "GPT-6 Luna",
    inputPer1M: 0.10,
    outputPer1M: 0.50,
  },
  "anthropic/claude-haiku-5.5": {
    openrouterId: "anthropic/claude-haiku-5.5",
    label: "Claude Haiku 5.5",
    inputPer1M: 0.10,
    outputPer1M: 0.50,
    featured: true,
  },
  "claude-haiku-5.5": {
    openrouterId: "anthropic/claude-haiku-5.5",
    label: "Claude Haiku 5.5",
    inputPer1M: 0.10,
    outputPer1M: 0.50,
  },
  "google/gemini-3.5-flash-lite": {
    openrouterId: "google/gemini-3.5-flash-lite",
    label: "Gemini 3.5 Flash-Lite",
    inputPer1M: 0.30,
    outputPer1M: 2.50,
    featured: true,
  },
  "gemini-3.5-flash-lite": {
    openrouterId: "google/gemini-3.5-flash-lite",
    label: "Gemini 3.5 Flash-Lite",
    inputPer1M: 0.30,
    outputPer1M: 2.50,
  },
  "google/gemini-3.8-flash": {
    openrouterId: "google/gemini-3.8-flash",
    label: "Gemini 3.8 Flash",
    inputPer1M: 0.825,
    outputPer1M: 4.125,
    featured: true,
  },
  "gemini-3.8-flash": {
    openrouterId: "google/gemini-3.8-flash",
    label: "Gemini 3.8 Flash",
    inputPer1M: 0.825,
    outputPer1M: 4.125,
  },
  "qwen/qwen3.8-flash": {
    openrouterId: "qwen/qwen3.8-flash",
    label: "Qwen 3.8 Flash",
    inputPer1M: 0.15,
    outputPer1M: 0.47,
    featured: true,
  },
  "qwen/qwen3.7-flash": {
    openrouterId: "qwen/qwen3.7-flash",
    label: "Qwen 3.7 Flash",
    inputPer1M: 0.03,
    outputPer1M: 0.13,
    featured: true,
  },
  "minimax/minimax-m3.1-flash": {
    openrouterId: "minimax/minimax-m3.1-flash",
    label: "MiniMax M3.1 Flash",
    inputPer1M: 0.075,
    outputPer1M: 0.30,
    featured: true,
  },
  "minimax-m3.1-flash": {
    openrouterId: "minimax/minimax-m3.1-flash",
    label: "MiniMax M3.1 Flash",
    inputPer1M: 0.075,
    outputPer1M: 0.30,
  },
  "minimax/minimax-m3": {
    openrouterId: "minimax/minimax-m3",
    label: "MiniMax M3",
    inputPer1M: 0.30,
    outputPer1M: 1.20,
    featured: true,
  },
  "minimax-m3": {
    openrouterId: "minimax/minimax-m3",
    label: "MiniMax M3",
    inputPer1M: 0.30,
    outputPer1M: 1.20,
  },
  // Compatibilidade com configurações anteriores
  "openai/gpt-5.4-nano": {
    openrouterId: "openai/gpt-5.4-nano",
    label: "GPT-5.4 Nano",
    inputPer1M: 0.20,
    outputPer1M: 1.25,
  },
  "openai/gpt-5-nano": {
    openrouterId: "openai/gpt-5-nano",
    label: "GPT-5 Nano",
    inputPer1M: 0.05,
    outputPer1M: 0.40,
  },
  "google/gemini-2.5-flash": {
    openrouterId: "google/gemini-2.5-flash",
    label: "Gemini 2.5 Flash (Legado)",
    inputPer1M: 0.30,
    outputPer1M: 2.50,
  },
  "gemini-2.5-flash": {
    openrouterId: "google/gemini-2.5-flash",
    label: "Gemini 2.5 Flash (Legado)",
    inputPer1M: 0.30,
    outputPer1M: 2.50,
  },
  "google/gemini-2.5-flash-lite": {
    openrouterId: "google/gemini-2.5-flash-lite",
    label: "Gemini 2.5 Flash-Lite (Legado)",
    inputPer1M: 0.10,
    outputPer1M: 0.40,
  },
};

const DEFAULT_MODEL_KEY = "deepseek/deepseek-v4.1-flash";

function jsonResponse(
  body: Record<string, unknown>,
  status = 200,
  extraHeaders: Record<string, string> = {},
): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      ...CORS_HEADERS,
      "Content-Type": "application/json; charset=utf-8",
      ...extraHeaders,
    },
  });
}

function getPublicModels(): Array<{ id: string; label: string }> {
  const seen = new Set<string>();
  const models: Array<{ id: string; label: string }> = [];
  for (const spec of Object.values(MODEL_REGISTRY)) {
    if (!spec.featured || seen.has(spec.openrouterId)) continue;
    seen.add(spec.openrouterId);
    models.push({ id: spec.openrouterId, label: spec.label });
  }
  return models;
}

function extractRoute(pathname: string): string {
  const cleaned = pathname.replace(/\/+$/, "");
  if (cleaned.endsWith("/chat/completions")) return "/chat/completions";
  if (cleaned.endsWith("/models")) return "/models";
  if (cleaned.endsWith("/quota")) return "/quota";
  if (cleaned.endsWith("/llm-proxy")) return "/chat/completions";
  return cleaned;
}

function extractBearerToken(req: Request): string | null {
  const authHeader = req.headers.get("authorization") || "";
  const match = authHeader.match(/^Bearer\s+(.+)$/i);
  return match ? match[1].trim() : null;
}

function computeEstimatedCostUsd(
  spec: ModelSpec,
  promptTokens: number,
  completionTokens: number,
  providerCostUsd?: unknown,
): number {
  if (typeof providerCostUsd === "number" && Number.isFinite(providerCostUsd) && providerCostUsd > 0) {
    return Number(providerCostUsd.toFixed(6));
  }
  const inputCost = (Math.max(0, promptTokens) / 1_000_000) * spec.inputPer1M;
  const outputCost = (Math.max(0, completionTokens) / 1_000_000) * spec.outputPer1M;
  const total = inputCost + outputCost;
  if (total <= 0 && (promptTokens > 0 || completionTokens > 0)) {
    return 0.00001;
  }
  return Number(total.toFixed(6));
}

Deno.serve(async (req: Request): Promise<Response> => {
  if (req.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: CORS_HEADERS });
  }

  const url = new URL(req.url);
  const route = extractRoute(url.pathname);

  if (req.method === "GET" && route === "/models") {
    return jsonResponse({ object: "list", data: getPublicModels() });
  }

  const supabaseUrl = Deno.env.get("SUPABASE_URL") ?? "";
  const serviceRoleKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";
  if (!supabaseUrl || !serviceRoleKey) {
    return jsonResponse(
      { error: { code: "supabase_env_missing", message: "Configuração do Supabase ausente na Edge Function." } },
      500,
    );
  }

  const token = extractBearerToken(req);
  if (!token) {
    return jsonResponse(
      { error: { code: "unauthorized", message: "Token de autenticação ausente. Faça login na sua conta." } },
      401,
    );
  }

  const adminClient = createClient(supabaseUrl, serviceRoleKey, {
    auth: { persistSession: false, autoRefreshToken: false },
  });

  const { data: userData, error: userErr } = await adminClient.auth.getUser(token);
  if (userErr || !userData?.user) {
    return jsonResponse(
      { error: { code: "invalid_token", message: "Sessão expirada ou inválida. Faça login novamente." } },
      401,
    );
  }

  const user = userData.user;

  if (req.method === "GET" && route === "/quota") {
    const { data: quotaData, error: quotaErr } = await adminClient.rpc("check_and_reserve_llm_quota", {
      p_user_id: user.id,
    });
    if (quotaErr || !quotaData) {
      return jsonResponse(
        { error: { code: "quota_lookup_failed", message: "Não foi possível consultar sua cota agora." } },
        500,
      );
    }
    const usedPct = Number(quotaData.used_pct ?? (quotaData.allowed ? 0 : 100));
    const remainingPct = Math.max(0, Number((100 - usedPct).toFixed(2)));
    return jsonResponse({
      plan: quotaData.plan ?? "free_trial",
      subscription_status: quotaData.subscription_status ?? "inactive",
      used_pct: usedPct,
      remaining_pct: remainingPct,
      cycle_reset_at: quotaData.cycle_reset_at ?? null,
      is_blocked: !quotaData.allowed,
      reason: quotaData.reason ?? null,
    });
  }

  if (req.method !== "POST" || route !== "/chat/completions") {
    return jsonResponse(
      { error: { code: "not_found", message: `Rota não suportada: ${req.method} ${route}` } },
      404,
    );
  }

  let body: Record<string, unknown>;
  try {
    body = await req.json();
  } catch {
    return jsonResponse(
      { error: { code: "invalid_json", message: "Corpo JSON inválido." } },
      400,
    );
  }

  const requestedModel = String(body.model || DEFAULT_MODEL_KEY).trim().toLowerCase();
  const modelSpec = MODEL_REGISTRY[requestedModel];
  if (!modelSpec) {
    return jsonResponse(
      {
        error: {
          code: "model_not_allowed",
          message: `Modelo '${requestedModel}' não está incluído no plano gerenciado. Use um dos modelos rápidos disponíveis.`,
          allowed_models: getPublicModels().map((m) => m.id),
        },
      },
      400,
    );
  }

  const { data: quota, error: quotaErr } = await adminClient.rpc("check_and_reserve_llm_quota", {
    p_user_id: user.id,
  });

  if (quotaErr || !quota) {
    return jsonResponse(
      { error: { code: "quota_check_failed", message: "Falha ao validar cota de IA no servidor." } },
      500,
    );
  }

  if (!quota.allowed) {
    const reason = String(quota.reason || "quota_exceeded");
    const status = reason === "subscription_inactive" ? 403 : 402;
    const usedPct = Number(quota.used_pct ?? 100);
    const message = reason === "subscription_inactive"
      ? "Sua assinatura não está ativa. Ative o plano Pro ou use sua própria chave (BYOK) nas configurações."
      : "Você atingiu 100% da cota mensal de IA deste ciclo. Aguarde a renovação, recarregue ou altere para BYOK.";
    return jsonResponse(
      {
        error: {
          code: reason,
          message,
          used_pct: usedPct,
          remaining_pct: 0,
          cycle_reset_at: quota.cycle_reset_at ?? null,
        },
      },
      status,
      {
        "X-Quota-Used-Pct": String(usedPct),
        "X-Quota-Remaining-Pct": "0",
        "X-Quota-Reset-At": String(quota.cycle_reset_at ?? ""),
      },
    );
  }

  const openrouterKey = Deno.env.get("OPENROUTER_API_KEY") ?? "";
  if (!openrouterKey) {
    return jsonResponse(
      {
        error: {
          code: "proxy_not_configured",
          message: "Provedor gerenciado ainda não configurado (OPENROUTER_API_KEY ausente no Supabase).",
        },
      },
      503,
    );
  }

  const upstreamPayload: Record<string, unknown> = {
    model: modelSpec.openrouterId,
    messages: body.messages,
    temperature: typeof body.temperature === "number" ? body.temperature : 0,
    stream: false,
  };
  if (body.response_format) {
    upstreamPayload.response_format = body.response_format;
  }
  const requestedMaxTokens = typeof body.max_tokens === "number" ? body.max_tokens : 4096;
  upstreamPayload.max_tokens = Math.max(64, Math.min(4096, Math.trunc(requestedMaxTokens)));

  const openrouterUrl = Deno.env.get("OPENROUTER_BASE_URL") || "https://openrouter.ai/api/v1/chat/completions";
  const upstreamResp = await fetch(openrouterUrl, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${openrouterKey}`,
      "Content-Type": "application/json",
      "HTTP-Referer": "https://ia-candidata.app",
      "X-Title": "IA Candidata",
    },
    body: JSON.stringify(upstreamPayload),
  });

  const upstreamText = await upstreamResp.text();
  if (!upstreamResp.ok) {
    return new Response(upstreamText, {
      status: upstreamResp.status,
      headers: {
        ...CORS_HEADERS,
        "Content-Type": "application/json; charset=utf-8",
      },
    });
  }

  let upstreamJson: Record<string, unknown>;
  try {
    upstreamJson = JSON.parse(upstreamText);
  } catch {
    return jsonResponse(
      { error: { code: "upstream_invalid_json", message: "Resposta inválida do provedor de IA." } },
      502,
    );
  }

  const usage = (upstreamJson.usage ?? {}) as Record<string, unknown>;
  const promptTokens = Number(usage.prompt_tokens ?? 0) || 0;
  const completionTokens = Number(usage.completion_tokens ?? 0) || 0;
  const costUsd = computeEstimatedCostUsd(modelSpec, promptTokens, completionTokens, usage.cost);

  const { data: recorded } = await adminClient.rpc("record_llm_usage", {
    p_workspace_id: quota.workspace_id,
    p_user_id: user.id,
    p_model: modelSpec.openrouterId,
    p_prompt_tokens: promptTokens,
    p_completion_tokens: completionTokens,
    p_cost_usd: costUsd,
  });

  const usedPct = Number(recorded?.used_pct ?? quota.used_pct ?? 0);
  const remainingPct = Number(recorded?.remaining_pct ?? Math.max(0, 100 - usedPct));
  const resetAt = String(recorded?.cycle_reset_at ?? quota.cycle_reset_at ?? "");

  return new Response(JSON.stringify(upstreamJson), {
    status: 200,
    headers: {
      ...CORS_HEADERS,
      "Content-Type": "application/json; charset=utf-8",
      "X-Quota-Used-Pct": String(usedPct),
      "X-Quota-Remaining-Pct": String(remainingPct),
      "X-Quota-Reset-At": resetAt,
    },
  });
});
