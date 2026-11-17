-- 0006_llm_proxy_budget_quota.sql
-- Extensão para Proxy LLM com teto de orçamento ($3.00/mês), proteção contra race condition e log de uso.

ALTER TABLE public.workspaces
  ADD COLUMN IF NOT EXISTS llm_budget_usd NUMERIC(10, 4) NOT NULL DEFAULT 0.1500 CHECK (llm_budget_usd >= 0),
  ADD COLUMN IF NOT EXISTS llm_spent_usd NUMERIC(10, 4) NOT NULL DEFAULT 0.0000 CHECK (llm_spent_usd >= 0),
  ADD COLUMN IF NOT EXISTS llm_cycle_reset_at TIMESTAMPTZ NOT NULL DEFAULT (now() + interval '30 days');

CREATE TABLE IF NOT EXISTS public.llm_usage_logs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES public.workspaces(id) ON DELETE CASCADE,
  user_id UUID REFERENCES auth.users(id) ON DELETE SET NULL,
  model TEXT NOT NULL,
  prompt_tokens INTEGER NOT NULL DEFAULT 0 CHECK (prompt_tokens >= 0),
  completion_tokens INTEGER NOT NULL DEFAULT 0 CHECK (completion_tokens >= 0),
  cost_usd NUMERIC(10, 6) NOT NULL DEFAULT 0 CHECK (cost_usd >= 0),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_llm_usage_logs_ws_created
  ON public.llm_usage_logs (workspace_id, created_at DESC);

ALTER TABLE public.llm_usage_logs ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Workspace members can view own llm usage" ON public.llm_usage_logs;
CREATE POLICY "Workspace members can view own llm usage"
  ON public.llm_usage_logs
  FOR SELECT
  TO authenticated
  USING (public.is_workspace_member(workspace_id));

-- Protege também as novas colunas de orçamento contra adulteração direta via REST API pelo cliente
CREATE OR REPLACE FUNCTION public.protect_workspace_billing_columns()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public', 'auth'
AS $function$
BEGIN
  IF auth.role() <> 'service_role' THEN
    IF NEW.plan IS DISTINCT FROM OLD.plan OR
       NEW.subscription_status IS DISTINCT FROM OLD.subscription_status OR
       NEW.monthly_credit_limit IS DISTINCT FROM OLD.monthly_credit_limit OR
       NEW.used_credits_this_month IS DISTINCT FROM OLD.used_credits_this_month OR
       NEW.llm_budget_usd IS DISTINCT FROM OLD.llm_budget_usd OR
       NEW.llm_spent_usd IS DISTINCT FROM OLD.llm_spent_usd OR
       NEW.llm_cycle_reset_at IS DISTINCT FROM OLD.llm_cycle_reset_at THEN
      RAISE EXCEPTION 'Apenas o sistema de cobrança pode alterar plano, créditos e cota de IA.';
    END IF;
  END IF;
  RETURN NEW;
END;
$function$;

-- Atualiza apply_verified_payment para liberar teto de $3.00 USD no plano Pro e zerar consumo do ciclo
CREATE OR REPLACE FUNCTION public.apply_verified_payment(
  p_workspace_id uuid,
  p_user_id uuid,
  p_provider text,
  p_provider_session_id text,
  p_provider_payment_id text,
  p_plan_tier text,
  p_amount_cents integer,
  p_currency text DEFAULT 'BRL'::text,
  p_pix_qr_code text DEFAULT NULL::text,
  p_pix_copy_paste text DEFAULT NULL::text,
  p_metadata jsonb DEFAULT '{}'::jsonb
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public', 'auth'
AS $function$
DECLARE
  v_existing RECORD;
  v_ws RECORD;
  v_credits_to_add INT := 0;
  v_tx_id UUID;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtext(p_workspace_id::text));

  SELECT * INTO v_ws
  FROM public.workspaces
  WHERE id = p_workspace_id
  FOR UPDATE;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'Workspace não encontrado: %', p_workspace_id;
  END IF;

  IF p_provider_session_id IS NOT NULL THEN
    SELECT * INTO v_existing
    FROM public.billing_transactions
    WHERE provider_session_id = p_provider_session_id;

    IF FOUND AND v_existing.status = 'paid' THEN
      RETURN jsonb_build_object(
        'success', true,
        'already_processed', true,
        'transaction_id', v_existing.id,
        'workspace_id', p_workspace_id,
        'plan', v_ws.plan,
        'credits_added', v_existing.credits_added,
        'new_monthly_limit', v_ws.monthly_credit_limit,
        'used_credits_this_month', v_ws.used_credits_this_month,
        'subscription_status', v_ws.subscription_status,
        'llm_budget_usd', v_ws.llm_budget_usd,
        'llm_spent_usd', v_ws.llm_spent_usd
      );
    END IF;
  END IF;

  IF p_plan_tier = 'pix_pack' THEN
    v_credits_to_add := 50;
    UPDATE public.workspaces
    SET llm_budget_usd = llm_budget_usd + 1.5000,
        updated_at = now()
    WHERE id = p_workspace_id
    RETURNING * INTO v_ws;

  ELSIF p_plan_tier = 'pro_cloud' THEN
    v_credits_to_add := 300;
    UPDATE public.workspaces
    SET plan = 'pro_cloud',
        monthly_credit_limit = 300,
        used_credits_this_month = 0,
        subscription_status = 'active',
        llm_budget_usd = 3.0000,
        llm_spent_usd = 0.0000,
        llm_cycle_reset_at = now() + interval '30 days',
        updated_at = now()
    WHERE id = p_workspace_id
    RETURNING * INTO v_ws;

  ELSIF p_plan_tier = 'agency_b2b' THEN
    v_credits_to_add := 1200;
    UPDATE public.workspaces
    SET plan = 'agency_b2b',
        monthly_credit_limit = 1200,
        used_credits_this_month = 0,
        subscription_status = 'active',
        llm_budget_usd = 12.0000,
        llm_spent_usd = 0.0000,
        llm_cycle_reset_at = now() + interval '30 days',
        updated_at = now()
    WHERE id = p_workspace_id
    RETURNING * INTO v_ws;

  ELSIF p_plan_tier = 'free_trial' THEN
    v_credits_to_add := 5;
    UPDATE public.workspaces
    SET plan = 'free_trial',
        monthly_credit_limit = 5,
        used_credits_this_month = 0,
        subscription_status = 'trialing',
        llm_budget_usd = 0.1500,
        llm_spent_usd = 0.0000,
        llm_cycle_reset_at = now() + interval '30 days',
        updated_at = now()
    WHERE id = p_workspace_id
    RETURNING * INTO v_ws;

  ELSE
    RAISE EXCEPTION 'Plano desconhecido: %', p_plan_tier;
  END IF;

  INSERT INTO public.billing_transactions (
    workspace_id, user_id, provider, provider_session_id, provider_payment_id,
    plan_tier, amount_cents, currency, credits_added, status,
    pix_qr_code, pix_copy_paste, metadata, paid_at
  ) VALUES (
    p_workspace_id, p_user_id, p_provider, p_provider_session_id, p_provider_payment_id,
    p_plan_tier, p_amount_cents, UPPER(COALESCE(p_currency, 'BRL')), v_credits_to_add, 'paid',
    p_pix_qr_code, p_pix_copy_paste, p_metadata, now()
  )
  ON CONFLICT (provider_session_id) DO UPDATE SET
    status = 'paid',
    paid_at = now(),
    provider_payment_id = COALESCE(EXCLUDED.provider_payment_id, billing_transactions.provider_payment_id),
    credits_added = v_credits_to_add,
    currency = UPPER(COALESCE(EXCLUDED.currency, billing_transactions.currency)),
    metadata = EXCLUDED.metadata
  RETURNING id INTO v_tx_id;

  RETURN jsonb_build_object(
    'success', true,
    'already_processed', false,
    'transaction_id', v_tx_id,
    'workspace_id', p_workspace_id,
    'plan', v_ws.plan,
    'credits_added', v_credits_to_add,
    'new_monthly_limit', v_ws.monthly_credit_limit,
    'used_credits_this_month', v_ws.used_credits_this_month,
    'subscription_status', v_ws.subscription_status,
    'llm_budget_usd', v_ws.llm_budget_usd,
    'llm_spent_usd', v_ws.llm_spent_usd
  );
END;
$function$;

-- Consulta segura do consumo em % para o usuário autenticado (sem expor lógica interna de custo em USD)
CREATE OR REPLACE FUNCTION public.get_my_llm_quota()
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public', 'auth'
AS $function$
DECLARE
  v_uid UUID := auth.uid();
  v_ws RECORD;
  v_used_pct NUMERIC(5, 2) := 0;
BEGIN
  IF v_uid IS NULL THEN
    RETURN jsonb_build_object('authenticated', false);
  END IF;

  SELECT w.*
  INTO v_ws
  FROM public.workspaces w
  JOIN public.workspace_members wm ON wm.workspace_id = w.id
  WHERE wm.user_id = v_uid
  ORDER BY wm.created_at ASC
  LIMIT 1;

  IF NOT FOUND THEN
    RETURN jsonb_build_object('authenticated', true, 'has_workspace', false);
  END IF;

  IF v_ws.llm_budget_usd <= 0 THEN
    v_used_pct := 100.0;
  ELSE
    v_used_pct := LEAST(100.0, ROUND((v_ws.llm_spent_usd / v_ws.llm_budget_usd) * 100.0, 2));
  END IF;

  RETURN jsonb_build_object(
    'authenticated', true,
    'has_workspace', true,
    'workspace_id', v_ws.id,
    'plan', v_ws.plan,
    'subscription_status', v_ws.subscription_status,
    'used_pct', v_used_pct,
    'remaining_pct', GREATEST(0.0, 100.0 - v_used_pct),
    'cycle_reset_at', v_ws.llm_cycle_reset_at,
    'is_blocked', (
      v_ws.subscription_status NOT IN ('active', 'trialing')
      OR v_ws.llm_spent_usd >= v_ws.llm_budget_usd
    )
  );
END;
$function$;

REVOKE ALL ON FUNCTION public.get_my_llm_quota() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.get_my_llm_quota() TO authenticated;

-- RPC exclusiva para a Edge Function llm-proxy validar cota antes da chamada ao provedor
CREATE OR REPLACE FUNCTION public.check_and_reserve_llm_quota(p_user_id uuid)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public', 'auth'
AS $function$
DECLARE
  v_ws RECORD;
  v_used_pct NUMERIC(5, 2) := 0;
BEGIN
  SELECT w.*
  INTO v_ws
  FROM public.workspaces w
  JOIN public.workspace_members wm ON wm.workspace_id = w.id
  WHERE wm.user_id = p_user_id
  ORDER BY wm.created_at ASC
  LIMIT 1
  FOR UPDATE OF w;

  IF NOT FOUND THEN
    RETURN jsonb_build_object('allowed', false, 'reason', 'workspace_not_found');
  END IF;

  -- Renova o ciclo automaticamente se assinatura ativa e a data de reset venceu
  IF v_ws.subscription_status = 'active' AND v_ws.llm_cycle_reset_at <= now() THEN
    UPDATE public.workspaces
    SET llm_spent_usd = 0.0000,
        used_credits_this_month = 0,
        llm_cycle_reset_at = now() + interval '30 days',
        updated_at = now()
    WHERE id = v_ws.id
    RETURNING * INTO v_ws;
  END IF;

  IF v_ws.subscription_status NOT IN ('active', 'trialing') THEN
    RETURN jsonb_build_object(
      'allowed', false,
      'reason', 'subscription_inactive',
      'subscription_status', v_ws.subscription_status,
      'plan', v_ws.plan
    );
  END IF;

  IF v_ws.llm_budget_usd <= 0 OR v_ws.llm_spent_usd >= v_ws.llm_budget_usd THEN
    RETURN jsonb_build_object(
      'allowed', false,
      'reason', 'quota_exceeded',
      'used_pct', 100.0,
      'cycle_reset_at', v_ws.llm_cycle_reset_at,
      'plan', v_ws.plan
    );
  END IF;

  v_used_pct := LEAST(100.0, ROUND((v_ws.llm_spent_usd / v_ws.llm_budget_usd) * 100.0, 2));

  RETURN jsonb_build_object(
    'allowed', true,
    'workspace_id', v_ws.id,
    'plan', v_ws.plan,
    'subscription_status', v_ws.subscription_status,
    'budget_usd', v_ws.llm_budget_usd,
    'spent_usd', v_ws.llm_spent_usd,
    'used_pct', v_used_pct,
    'cycle_reset_at', v_ws.llm_cycle_reset_at
  );
END;
$function$;

REVOKE ALL ON FUNCTION public.check_and_reserve_llm_quota(uuid) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.check_and_reserve_llm_quota(uuid) TO service_role;

-- Incremento atômico pós-resposta da IA + gravação de auditoria
CREATE OR REPLACE FUNCTION public.record_llm_usage(
  p_workspace_id uuid,
  p_user_id uuid,
  p_model text,
  p_prompt_tokens integer,
  p_completion_tokens integer,
  p_cost_usd numeric
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public', 'auth'
AS $function$
DECLARE
  v_ws RECORD;
  v_safe_cost NUMERIC(10, 6) := GREATEST(COALESCE(p_cost_usd, 0), 0);
  v_used_pct NUMERIC(5, 2) := 0;
BEGIN
  UPDATE public.workspaces
  SET llm_spent_usd = llm_spent_usd + ROUND(v_safe_cost, 4),
      used_credits_this_month = used_credits_this_month + 1,
      updated_at = now()
  WHERE id = p_workspace_id
  RETURNING * INTO v_ws;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'Workspace não encontrado: %', p_workspace_id;
  END IF;

  INSERT INTO public.llm_usage_logs (
    workspace_id, user_id, model, prompt_tokens, completion_tokens, cost_usd
  ) VALUES (
    p_workspace_id,
    p_user_id,
    p_model,
    GREATEST(COALESCE(p_prompt_tokens, 0), 0),
    GREATEST(COALESCE(p_completion_tokens, 0), 0),
    v_safe_cost
  );

  IF v_ws.llm_budget_usd <= 0 THEN
    v_used_pct := 100.0;
  ELSE
    v_used_pct := LEAST(100.0, ROUND((v_ws.llm_spent_usd / v_ws.llm_budget_usd) * 100.0, 2));
  END IF;

  RETURN jsonb_build_object(
    'workspace_id', v_ws.id,
    'used_pct', v_used_pct,
    'remaining_pct', GREATEST(0.0, 100.0 - v_used_pct),
    'cycle_reset_at', v_ws.llm_cycle_reset_at
  );
END;
$function$;

REVOKE ALL ON FUNCTION public.record_llm_usage(uuid, uuid, text, integer, integer, numeric) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.record_llm_usage(uuid, uuid, text, integer, integer, numeric) TO service_role;
