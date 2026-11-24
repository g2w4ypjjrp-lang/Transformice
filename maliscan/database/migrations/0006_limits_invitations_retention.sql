-- =============================================================================
-- 0006: subscription limit enforcement, invitation acceptance, retention,
-- document review helpers.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Live subscription + limits for a firm (NULL limit = unlimited).
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION app.firm_plan(p_firm_id uuid)
RETURNS TABLE (status text, period_end timestamptz, max_users int, max_taxpayers int, max_documents_per_month int)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
  SELECT s.status, s.current_period_end, p.max_users, p.max_taxpayers, p.max_documents_per_month
  FROM public.subscriptions s JOIN public.subscription_plans p ON p.id = s.plan_id
  WHERE s.firm_id = p_firm_id AND s.status IN ('trialing', 'active', 'past_due')
$$;

CREATE OR REPLACE FUNCTION app.enforce_plan_limits() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
  v record;
  v_count int;
BEGIN
  SELECT * INTO v FROM app.firm_plan(NEW.firm_id);
  IF NOT FOUND THEN
    RAISE EXCEPTION 'plan_limit:no_active_subscription' USING ERRCODE = 'check_violation';
  END IF;
  IF v.status = 'trialing' AND v.period_end IS NOT NULL AND v.period_end < now() THEN
    RAISE EXCEPTION 'plan_limit:trial_expired' USING ERRCODE = 'check_violation';
  END IF;

  IF TG_TABLE_NAME = 'taxpayers' AND v.max_taxpayers IS NOT NULL THEN
    SELECT count(*) INTO v_count FROM public.taxpayers WHERE firm_id = NEW.firm_id AND deleted_at IS NULL;
    IF v_count >= v.max_taxpayers THEN
      RAISE EXCEPTION 'plan_limit:max_taxpayers' USING ERRCODE = 'check_violation';
    END IF;
  ELSIF TG_TABLE_NAME = 'firm_members' AND v.max_users IS NOT NULL AND NEW.status = 'active' THEN
    SELECT count(*) INTO v_count FROM public.firm_members WHERE firm_id = NEW.firm_id AND status = 'active';
    IF v_count >= v.max_users THEN
      RAISE EXCEPTION 'plan_limit:max_users' USING ERRCODE = 'check_violation';
    END IF;
  ELSIF TG_TABLE_NAME = 'documents' AND v.max_documents_per_month IS NOT NULL THEN
    SELECT count(*) INTO v_count FROM public.documents
     WHERE firm_id = NEW.firm_id AND created_at >= date_trunc('month', now());
    IF v_count >= v.max_documents_per_month THEN
      RAISE EXCEPTION 'plan_limit:max_documents_per_month' USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  RETURN NEW;
END
$$;

CREATE TRIGGER taxpayers_plan_limit BEFORE INSERT ON public.taxpayers
  FOR EACH ROW EXECUTE FUNCTION app.enforce_plan_limits();
CREATE TRIGGER documents_plan_limit BEFORE INSERT ON public.documents
  FOR EACH ROW EXECUTE FUNCTION app.enforce_plan_limits();
-- The firm creator's own owner row is inserted before the subscription exists.
CREATE TRIGGER firm_members_plan_limit BEFORE INSERT ON public.firm_members
  FOR EACH ROW WHEN (NEW.role <> 'owner' OR NEW.invited_by IS NOT NULL)
  EXECUTE FUNCTION app.enforce_plan_limits();

-- -----------------------------------------------------------------------------
-- Invitation acceptance (token is hashed client-side by the API; SHA-256 hex).
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION app.accept_invitation(p_token_hash text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
  v_inv public.firm_invitations%ROWTYPE;
  v_email text;
BEGIN
  IF auth.uid() IS NULL THEN
    RAISE EXCEPTION 'authentication required' USING ERRCODE = 'insufficient_privilege';
  END IF;
  SELECT * INTO v_inv FROM public.firm_invitations WHERE token_hash = p_token_hash FOR UPDATE;
  IF NOT FOUND OR v_inv.revoked_at IS NOT NULL OR v_inv.accepted_at IS NOT NULL OR v_inv.expires_at < now() THEN
    RAISE EXCEPTION 'invitation_invalid' USING ERRCODE = 'no_data_found';
  END IF;
  SELECT email INTO v_email FROM public.users WHERE id = auth.uid();
  IF lower(v_email) <> lower(v_inv.email) THEN
    RAISE EXCEPTION 'invitation_email_mismatch' USING ERRCODE = 'insufficient_privilege';
  END IF;

  INSERT INTO public.firm_members (firm_id, user_id, role, invited_by)
  VALUES (v_inv.firm_id, auth.uid(), v_inv.role, v_inv.invited_by)
  ON CONFLICT (firm_id, user_id) DO UPDATE SET role = EXCLUDED.role, status = 'active';

  UPDATE public.firm_invitations SET accepted_at = now(), accepted_by = auth.uid() WHERE id = v_inv.id;
  PERFORM app.write_audit_log(v_inv.firm_id, 'member.joined', 'user', auth.uid(),
                              jsonb_build_object('role', v_inv.role));
  RETURN v_inv.firm_id;
END
$$;
GRANT EXECUTE ON FUNCTION app.accept_invitation(text), app.firm_plan(uuid) TO authenticated, service_role;

-- -----------------------------------------------------------------------------
-- Retention: documents are kept for the legal retention period (VUK md. 253:
-- 5 years; TTK md. 82: 10 years → 10 years default, configurable).
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION app.set_document_retention() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
  v_years int;
BEGIN
  IF NEW.retention_until IS NULL THEN
    SELECT coalesce((value #>> '{}')::int, 10) INTO v_years
      FROM public.platform_config WHERE key = 'retention.document_years';
    NEW.retention_until := (date_trunc('year', now()) + make_interval(years => coalesce(v_years, 10) + 1))::date - 1;
  END IF;
  RETURN NEW;
END
$$;
CREATE TRIGGER documents_retention BEFORE INSERT ON public.documents
  FOR EACH ROW EXECUTE FUNCTION app.set_document_retention();

-- Accounting engine metadata on generated journal lines.
ALTER TABLE public.journal_lines ADD COLUMN IF NOT EXISTS source_note text CHECK (length(source_note) <= 200);

-- Mark learned mappings created on approval.
CREATE INDEX IF NOT EXISTS documents_needs_review_idx ON public.documents (firm_id, taxpayer_id)
  WHERE status = 'needs_review' AND deleted_at IS NULL;
