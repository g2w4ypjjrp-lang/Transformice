-- =============================================================================
-- 0001_core: schemas, shared helpers, firms, users, membership, subscriptions,
-- audit log and platform configuration.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS app;
COMMENT ON SCHEMA app IS 'MaliScan internal helper functions (authorization, triggers).';

-- -----------------------------------------------------------------------------
-- Generic helpers
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION app.touch_updated_at() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END
$$;

CREATE OR REPLACE FUNCTION app.forbid_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% on % is not allowed (append-only table)', TG_OP, TG_TABLE_NAME
    USING ERRCODE = 'insufficient_privilege';
END
$$;

-- -----------------------------------------------------------------------------
-- Users (profile mirror of auth.users)
-- -----------------------------------------------------------------------------
CREATE TABLE public.users (
  id              uuid PRIMARY KEY REFERENCES auth.users (id) ON DELETE CASCADE,
  email           text NOT NULL,
  full_name       text,
  phone           text,
  locale          text NOT NULL DEFAULT 'tr' CHECK (locale IN ('tr', 'en')),
  is_super_admin  boolean NOT NULL DEFAULT false,
  is_active       boolean NOT NULL DEFAULT true,
  last_seen_at    timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX users_email_lower_uq ON public.users (lower(email));
CREATE TRIGGER users_touch BEFORE UPDATE ON public.users
  FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();

-- Mirror new / changed auth users into public.users.
CREATE OR REPLACE FUNCTION app.sync_auth_user() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
  INSERT INTO public.users (id, email, full_name)
  VALUES (NEW.id, coalesce(NEW.email, NEW.id::text || '@invalid.local'),
          nullif(NEW.raw_user_meta_data ->> 'full_name', ''))
  ON CONFLICT (id) DO UPDATE SET email = EXCLUDED.email;
  RETURN NEW;
END
$$;

CREATE TRIGGER on_auth_user_synced
  AFTER INSERT OR UPDATE OF email ON auth.users
  FOR EACH ROW EXECUTE FUNCTION app.sync_auth_user();

-- -----------------------------------------------------------------------------
-- Firms (accounting offices = tenants)
-- -----------------------------------------------------------------------------
CREATE TABLE public.firms (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name         text NOT NULL CHECK (length(btrim(name)) BETWEEN 2 AND 200),
  tax_number   text CHECK (tax_number ~ '^[0-9]{10,11}$'),
  tax_office   text,
  email        text,
  phone        text,
  address      text,
  settings     jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(settings) = 'object'),
  status       text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended', 'closed')),
  created_by   uuid REFERENCES public.users (id) ON DELETE SET NULL,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  deleted_at   timestamptz
);
CREATE TRIGGER firms_touch BEFORE UPDATE ON public.firms
  FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();

CREATE TABLE public.firm_members (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  firm_id     uuid NOT NULL REFERENCES public.firms (id) ON DELETE CASCADE,
  user_id     uuid NOT NULL REFERENCES public.users (id) ON DELETE CASCADE,
  role        text NOT NULL CHECK (role IN ('owner', 'admin', 'accountant', 'clerk', 'viewer')),
  status      text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
  invited_by  uuid REFERENCES public.users (id) ON DELETE SET NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (firm_id, user_id)
);
CREATE INDEX firm_members_user_idx ON public.firm_members (user_id) WHERE status = 'active';
CREATE TRIGGER firm_members_touch BEFORE UPDATE ON public.firm_members
  FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();

CREATE TABLE public.firm_invitations (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  firm_id      uuid NOT NULL REFERENCES public.firms (id) ON DELETE CASCADE,
  email        text NOT NULL CHECK (position('@' IN email) > 1),
  role         text NOT NULL CHECK (role IN ('admin', 'accountant', 'clerk', 'viewer')),
  token_hash   text NOT NULL UNIQUE,  -- SHA-256 of the emailed token; raw token never stored
  invited_by   uuid REFERENCES public.users (id) ON DELETE SET NULL,
  expires_at   timestamptz NOT NULL,
  accepted_at  timestamptz,
  accepted_by  uuid REFERENCES public.users (id) ON DELETE SET NULL,
  revoked_at   timestamptz,
  created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX firm_invitations_pending_uq ON public.firm_invitations (firm_id, lower(email))
  WHERE accepted_at IS NULL AND revoked_at IS NULL;

-- -----------------------------------------------------------------------------
-- Authorization helpers (SECURITY DEFINER to avoid RLS recursion)
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION app.is_super_admin() RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
  SELECT coalesce(
    (SELECT u.is_super_admin AND u.is_active FROM public.users u WHERE u.id = auth.uid()),
    false)
$$;

CREATE OR REPLACE FUNCTION app.firm_role(p_firm_id uuid) RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
  SELECT m.role
  FROM public.firm_members m
  JOIN public.firms f ON f.id = m.firm_id
  JOIN public.users u ON u.id = m.user_id
  WHERE m.firm_id = p_firm_id
    AND m.user_id = auth.uid()
    AND m.status = 'active'
    AND u.is_active
    AND f.status = 'active'
    AND f.deleted_at IS NULL
$$;

CREATE OR REPLACE FUNCTION app.is_firm_member(p_firm_id uuid) RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT app.firm_role(p_firm_id) IS NOT NULL
$$;

CREATE OR REPLACE FUNCTION app.has_firm_role(p_firm_id uuid, p_roles text[]) RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT coalesce(app.firm_role(p_firm_id) = ANY (p_roles), false)
$$;

-- Users that share at least one firm with the caller (for member lists).
CREATE OR REPLACE FUNCTION app.shares_firm_with(p_user_id uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
  SELECT EXISTS (
    SELECT 1
    FROM public.firm_members me
    JOIN public.firm_members other ON other.firm_id = me.firm_id
    WHERE me.user_id = auth.uid() AND me.status = 'active'
      AND other.user_id = p_user_id
  )
$$;

-- A firm must always keep at least one active owner.
CREATE OR REPLACE FUNCTION app.guard_last_owner() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
  v_firm uuid := coalesce(OLD.firm_id, NEW.firm_id);
BEGIN
  IF OLD.role = 'owner' AND OLD.status = 'active'
     AND (TG_OP = 'DELETE' OR NEW.role <> 'owner' OR NEW.status <> 'active') THEN
    IF NOT EXISTS (
      SELECT 1 FROM public.firm_members
      WHERE firm_id = v_firm AND role = 'owner' AND status = 'active' AND id <> OLD.id
    ) AND EXISTS (SELECT 1 FROM public.firms WHERE id = v_firm) THEN
      RAISE EXCEPTION 'firm must keep at least one active owner'
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  RETURN coalesce(NEW, OLD);
END
$$;
CREATE TRIGGER firm_members_last_owner
  BEFORE UPDATE OR DELETE ON public.firm_members
  FOR EACH ROW EXECUTE FUNCTION app.guard_last_owner();

-- -----------------------------------------------------------------------------
-- Subscriptions
-- -----------------------------------------------------------------------------
CREATE TABLE public.subscription_plans (
  id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  code                     text NOT NULL UNIQUE CHECK (code ~ '^[a-z0-9_]{2,40}$'),
  name                     text NOT NULL,
  price_monthly            numeric(12, 2) NOT NULL DEFAULT 0 CHECK (price_monthly >= 0),
  currency                 char(3) NOT NULL DEFAULT 'TRY',
  max_users                integer CHECK (max_users > 0),               -- NULL = unlimited
  max_taxpayers            integer CHECK (max_taxpayers > 0),
  max_documents_per_month  integer CHECK (max_documents_per_month > 0),
  trial_days               integer NOT NULL DEFAULT 0 CHECK (trial_days >= 0),
  features                 jsonb NOT NULL DEFAULT '{}'::jsonb,
  is_active                boolean NOT NULL DEFAULT true,
  is_default_trial         boolean NOT NULL DEFAULT false,
  created_at               timestamptz NOT NULL DEFAULT now(),
  updated_at               timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX subscription_plans_one_default_trial
  ON public.subscription_plans (is_default_trial) WHERE is_default_trial;
CREATE TRIGGER subscription_plans_touch BEFORE UPDATE ON public.subscription_plans
  FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();

CREATE TABLE public.subscriptions (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  firm_id               uuid NOT NULL REFERENCES public.firms (id) ON DELETE CASCADE,
  plan_id               uuid NOT NULL REFERENCES public.subscription_plans (id),
  status                text NOT NULL CHECK (status IN ('trialing', 'active', 'past_due', 'canceled', 'expired')),
  current_period_start  timestamptz NOT NULL DEFAULT now(),
  current_period_end    timestamptz,
  canceled_at           timestamptz,
  created_at            timestamptz NOT NULL DEFAULT now(),
  updated_at            timestamptz NOT NULL DEFAULT now(),
  CHECK (current_period_end IS NULL OR current_period_end > current_period_start)
);
-- Exactly one live subscription per firm.
CREATE UNIQUE INDEX subscriptions_one_live_per_firm ON public.subscriptions (firm_id)
  WHERE status IN ('trialing', 'active', 'past_due');
CREATE TRIGGER subscriptions_touch BEFORE UPDATE ON public.subscriptions
  FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();

INSERT INTO public.subscription_plans
  (code, name, price_monthly, max_users, max_taxpayers, max_documents_per_month, trial_days, is_default_trial)
VALUES
  ('trial',        'Deneme',      0,    3,   10,   200, 14, true),
  ('starter',      'Başlangıç',   0,    3,   50,  1000,  0, false),
  ('professional', 'Profesyonel', 0,   10,  250,  5000,  0, false),
  ('enterprise',   'Kurumsal',    0, NULL, NULL,  NULL,  0, false);
-- Prices are intentionally 0 until the business defines them (see IMPLEMENTATION_PLAN.md §6).

-- -----------------------------------------------------------------------------
-- Audit log (append-only; never store financial values here)
-- -----------------------------------------------------------------------------
CREATE TABLE public.audit_logs (
  id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  firm_id        uuid REFERENCES public.firms (id) ON DELETE SET NULL,
  actor_user_id  uuid REFERENCES public.users (id) ON DELETE SET NULL,
  action         text NOT NULL CHECK (action ~ '^[a-z_]+\.[a-z_]+$'),
  entity_type    text,
  entity_id      uuid,
  metadata       jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(metadata) = 'object'),
  ip_address     inet,
  request_id     text,
  created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX audit_logs_firm_created_idx ON public.audit_logs (firm_id, created_at DESC);
CREATE INDEX audit_logs_entity_idx ON public.audit_logs (entity_type, entity_id);
CREATE TRIGGER audit_logs_append_only
  BEFORE UPDATE OR DELETE ON public.audit_logs
  FOR EACH ROW EXECUTE FUNCTION app.forbid_mutation();

CREATE OR REPLACE FUNCTION app.write_audit_log(
  p_firm_id uuid, p_action text, p_entity_type text, p_entity_id uuid,
  p_metadata jsonb DEFAULT '{}'::jsonb, p_ip inet DEFAULT NULL, p_request_id text DEFAULT NULL
) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
  v_id bigint;
BEGIN
  -- Callers may only log against firms they belong to (or platform-level with NULL).
  IF p_firm_id IS NOT NULL AND auth.uid() IS NOT NULL
     AND NOT app.is_firm_member(p_firm_id) AND NOT app.is_super_admin() THEN
    RAISE EXCEPTION 'not a member of firm' USING ERRCODE = 'insufficient_privilege';
  END IF;
  INSERT INTO public.audit_logs (firm_id, actor_user_id, action, entity_type, entity_id, metadata, ip_address, request_id)
  VALUES (p_firm_id, auth.uid(), p_action, p_entity_type, p_entity_id, coalesce(p_metadata, '{}'::jsonb), p_ip, p_request_id)
  RETURNING id INTO v_id;
  RETURN v_id;
END
$$;

-- -----------------------------------------------------------------------------
-- Platform configuration (super admin only)
-- -----------------------------------------------------------------------------
CREATE TABLE public.platform_config (
  key         text PRIMARY KEY CHECK (key ~ '^[a-z0-9_.]{2,80}$'),
  value       jsonb NOT NULL,
  updated_by  uuid REFERENCES public.users (id) ON DELETE SET NULL,
  updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER platform_config_touch BEFORE UPDATE ON public.platform_config
  FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();

INSERT INTO public.platform_config (key, value) VALUES
  ('uploads.max_file_bytes', '15728640'::jsonb),
  ('uploads.allowed_mime_types', '["image/jpeg","image/png","image/webp","image/heic","application/pdf","application/xml","text/xml"]'::jsonb),
  ('luca.max_vouchers_per_file', '50'::jsonb),
  ('retention.document_years', '10'::jsonb);

-- -----------------------------------------------------------------------------
-- Firm bootstrap: creates firm + owner membership + trial subscription atomically.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION app.create_firm(p_name text, p_tax_number text DEFAULT NULL)
RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
  v_uid  uuid := auth.uid();
  v_firm uuid;
  v_plan public.subscription_plans%ROWTYPE;
BEGIN
  IF v_uid IS NULL THEN
    RAISE EXCEPTION 'authentication required' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM public.users WHERE id = v_uid AND is_active) THEN
    RAISE EXCEPTION 'user profile missing or inactive' USING ERRCODE = 'insufficient_privilege';
  END IF;

  INSERT INTO public.firms (name, tax_number, created_by)
  VALUES (btrim(p_name), nullif(btrim(p_tax_number), ''), v_uid)
  RETURNING id INTO v_firm;

  INSERT INTO public.firm_members (firm_id, user_id, role) VALUES (v_firm, v_uid, 'owner');

  SELECT * INTO v_plan FROM public.subscription_plans WHERE is_default_trial AND is_active;
  IF FOUND THEN
    INSERT INTO public.subscriptions (firm_id, plan_id, status, current_period_end)
    VALUES (v_firm, v_plan.id, 'trialing', now() + make_interval(days => greatest(v_plan.trial_days, 1)));
  END IF;

  PERFORM app.write_audit_log(v_firm, 'firm.created', 'firm', v_firm, '{}'::jsonb);
  RETURN v_firm;
END
$$;
