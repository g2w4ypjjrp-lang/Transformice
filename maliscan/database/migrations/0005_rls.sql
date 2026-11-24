-- =============================================================================
-- 0005: Row Level Security + grants.
--
-- The API connects as the database owner but runs every user request inside a
-- transaction with `SET LOCAL ROLE authenticated` and `request.jwt.claims`
-- set (identical to Supabase PostgREST). Therefore these policies apply to
-- every API query. `anon` gets nothing. Background workers use `service_role`
-- (BYPASSRLS) and must always filter by firm_id explicitly.
-- =============================================================================

REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon, authenticated;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA app FROM PUBLIC;
REVOKE ALL ON SCHEMA app FROM PUBLIC;

GRANT USAGE ON SCHEMA public, app TO authenticated, service_role;
GRANT EXECUTE ON FUNCTION
  app.is_super_admin(), app.firm_role(uuid), app.is_firm_member(uuid),
  app.has_firm_role(uuid, text[]), app.shares_firm_with(uuid),
  app.write_audit_log(uuid, text, text, uuid, jsonb, inet, text),
  app.create_firm(text, text)
TO authenticated, service_role;

GRANT ALL ON ALL TABLES IN SCHEMA public TO service_role;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO service_role;

-- Convenience: role sets used in policies.
--   managers = owner, admin
--   bookers  = owner, admin, accountant          (master data, journals, export)
--   writers  = owner, admin, accountant, clerk   (documents, OCR corrections)

-- ---------------------------------------------------------------- users
ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;
GRANT SELECT ON public.users TO authenticated;
GRANT UPDATE (full_name, phone, locale, last_seen_at) ON public.users TO authenticated;
CREATE POLICY users_select ON public.users FOR SELECT TO authenticated
  USING (id = auth.uid() OR app.shares_firm_with(id) OR app.is_super_admin());
CREATE POLICY users_update_self ON public.users FOR UPDATE TO authenticated
  USING (id = auth.uid()) WITH CHECK (id = auth.uid());

-- ---------------------------------------------------------------- firms
ALTER TABLE public.firms ENABLE ROW LEVEL SECURITY;
GRANT SELECT ON public.firms TO authenticated;
GRANT UPDATE (name, tax_number, tax_office, email, phone, address, settings) ON public.firms TO authenticated;
CREATE POLICY firms_select ON public.firms FOR SELECT TO authenticated
  USING (app.is_firm_member(id) OR app.is_super_admin());
CREATE POLICY firms_update ON public.firms FOR UPDATE TO authenticated
  USING (app.has_firm_role(id, ARRAY['owner', 'admin']))
  WITH CHECK (app.has_firm_role(id, ARRAY['owner', 'admin']));
-- INSERT happens only through app.create_firm(); DELETE only by service_role.

-- ---------------------------------------------------------------- firm_members
ALTER TABLE public.firm_members ENABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT, DELETE ON public.firm_members TO authenticated;
GRANT UPDATE (role, status) ON public.firm_members TO authenticated;
CREATE POLICY firm_members_select ON public.firm_members FOR SELECT TO authenticated
  USING (app.is_firm_member(firm_id) OR app.is_super_admin());
-- Admins manage non-owner members; only owners may grant or touch the owner role.
CREATE POLICY firm_members_insert ON public.firm_members FOR INSERT TO authenticated
  WITH CHECK (app.has_firm_role(firm_id, ARRAY['owner', 'admin'])
              AND (role <> 'owner' OR app.has_firm_role(firm_id, ARRAY['owner'])));
CREATE POLICY firm_members_update ON public.firm_members FOR UPDATE TO authenticated
  USING (app.has_firm_role(firm_id, ARRAY['owner', 'admin'])
         AND (role <> 'owner' OR app.has_firm_role(firm_id, ARRAY['owner'])))
  WITH CHECK (app.has_firm_role(firm_id, ARRAY['owner', 'admin'])
              AND (role <> 'owner' OR app.has_firm_role(firm_id, ARRAY['owner'])));
CREATE POLICY firm_members_delete ON public.firm_members FOR DELETE TO authenticated
  USING (app.has_firm_role(firm_id, ARRAY['owner', 'admin'])
         AND (role <> 'owner' OR app.has_firm_role(firm_id, ARRAY['owner'])));

-- ---------------------------------------------------------------- firm_invitations
ALTER TABLE public.firm_invitations ENABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT ON public.firm_invitations TO authenticated;
GRANT UPDATE (revoked_at) ON public.firm_invitations TO authenticated;
CREATE POLICY firm_invitations_select ON public.firm_invitations FOR SELECT TO authenticated
  USING (app.has_firm_role(firm_id, ARRAY['owner', 'admin']));
CREATE POLICY firm_invitations_insert ON public.firm_invitations FOR INSERT TO authenticated
  WITH CHECK (app.has_firm_role(firm_id, ARRAY['owner', 'admin']) AND invited_by = auth.uid());
CREATE POLICY firm_invitations_revoke ON public.firm_invitations FOR UPDATE TO authenticated
  USING (app.has_firm_role(firm_id, ARRAY['owner', 'admin']))
  WITH CHECK (app.has_firm_role(firm_id, ARRAY['owner', 'admin']));

-- ---------------------------------------------------------------- subscriptions
ALTER TABLE public.subscription_plans ENABLE ROW LEVEL SECURITY;
GRANT SELECT ON public.subscription_plans TO authenticated;
CREATE POLICY subscription_plans_select ON public.subscription_plans FOR SELECT TO authenticated
  USING (is_active OR app.is_super_admin());

ALTER TABLE public.subscriptions ENABLE ROW LEVEL SECURITY;
GRANT SELECT ON public.subscriptions TO authenticated;
CREATE POLICY subscriptions_select ON public.subscriptions FOR SELECT TO authenticated
  USING (app.is_firm_member(firm_id) OR app.is_super_admin());

-- ---------------------------------------------------------------- audit_logs
ALTER TABLE public.audit_logs ENABLE ROW LEVEL SECURITY;
GRANT SELECT ON public.audit_logs TO authenticated;
CREATE POLICY audit_logs_select ON public.audit_logs FOR SELECT TO authenticated
  USING ((firm_id IS NOT NULL AND app.has_firm_role(firm_id, ARRAY['owner', 'admin']))
         OR app.is_super_admin());
-- Inserts only through app.write_audit_log().

-- ---------------------------------------------------------------- platform_config
ALTER TABLE public.platform_config ENABLE ROW LEVEL SECURITY;
GRANT SELECT ON public.platform_config TO authenticated;
CREATE POLICY platform_config_select ON public.platform_config FOR SELECT TO authenticated
  USING (app.is_super_admin());

-- ---------------------------------------------------------------- master data
DO $$
DECLARE
  t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['taxpayers', 'chart_of_accounts', 'account_mappings', 'accounting_rules'] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON public.%I TO authenticated', t);
    EXECUTE format($p$CREATE POLICY %1$s_select ON public.%1$I FOR SELECT TO authenticated
                     USING (app.is_firm_member(firm_id) OR app.is_super_admin())$p$, t);
    EXECUTE format($p$CREATE POLICY %1$s_insert ON public.%1$I FOR INSERT TO authenticated
                     WITH CHECK (app.has_firm_role(firm_id, ARRAY['owner','admin','accountant']))$p$, t);
    EXECUTE format($p$CREATE POLICY %1$s_update ON public.%1$I FOR UPDATE TO authenticated
                     USING (app.has_firm_role(firm_id, ARRAY['owner','admin','accountant']))
                     WITH CHECK (app.has_firm_role(firm_id, ARRAY['owner','admin','accountant']))$p$, t);
    EXECUTE format($p$CREATE POLICY %1$s_delete ON public.%1$I FOR DELETE TO authenticated
                     USING (app.has_firm_role(firm_id, ARRAY['owner','admin']))$p$, t);
  END LOOP;
END
$$;

-- ---------------------------------------------------------------- documents & extraction
ALTER TABLE public.documents ENABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT, UPDATE ON public.documents TO authenticated;
CREATE POLICY documents_select ON public.documents FOR SELECT TO authenticated
  USING (app.is_firm_member(firm_id) OR app.is_super_admin());
CREATE POLICY documents_insert ON public.documents FOR INSERT TO authenticated
  WITH CHECK (app.has_firm_role(firm_id, ARRAY['owner','admin','accountant','clerk'])
              AND uploaded_by = auth.uid());
CREATE POLICY documents_update ON public.documents FOR UPDATE TO authenticated
  USING (app.has_firm_role(firm_id, ARRAY['owner','admin','accountant','clerk']))
  WITH CHECK (app.has_firm_role(firm_id, ARRAY['owner','admin','accountant','clerk']));
-- Hard deletes go through service_role retention jobs; users soft-delete via deleted_at.

DO $$
DECLARE
  t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['extracted_data', 'document_items'] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE ON public.%I TO authenticated', t);
    EXECUTE format($p$CREATE POLICY %1$s_select ON public.%1$I FOR SELECT TO authenticated
                     USING (app.is_firm_member(firm_id) OR app.is_super_admin())$p$, t);
    EXECUTE format($p$CREATE POLICY %1$s_insert ON public.%1$I FOR INSERT TO authenticated
                     WITH CHECK (app.has_firm_role(firm_id, ARRAY['owner','admin','accountant','clerk']))$p$, t);
    EXECUTE format($p$CREATE POLICY %1$s_update ON public.%1$I FOR UPDATE TO authenticated
                     USING (app.has_firm_role(firm_id, ARRAY['owner','admin','accountant','clerk']))
                     WITH CHECK (app.has_firm_role(firm_id, ARRAY['owner','admin','accountant','clerk']))$p$, t);
  END LOOP;
END
$$;

ALTER TABLE public.processing_jobs ENABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT ON public.processing_jobs TO authenticated;
CREATE POLICY processing_jobs_select ON public.processing_jobs FOR SELECT TO authenticated
  USING (app.is_firm_member(firm_id) OR app.is_super_admin());
CREATE POLICY processing_jobs_insert ON public.processing_jobs FOR INSERT TO authenticated
  WITH CHECK (app.has_firm_role(firm_id, ARRAY['owner','admin','accountant','clerk'])
              AND status = 'queued' AND attempts = 0 AND created_by = auth.uid());

-- ---------------------------------------------------------------- journals
ALTER TABLE public.journal_entries ENABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.journal_entries TO authenticated;
CREATE POLICY journal_entries_select ON public.journal_entries FOR SELECT TO authenticated
  USING (app.is_firm_member(firm_id) OR app.is_super_admin());
CREATE POLICY journal_entries_insert ON public.journal_entries FOR INSERT TO authenticated
  WITH CHECK (app.has_firm_role(firm_id, ARRAY['owner','admin','accountant']));
CREATE POLICY journal_entries_update ON public.journal_entries FOR UPDATE TO authenticated
  USING (app.has_firm_role(firm_id, ARRAY['owner','admin','accountant']))
  WITH CHECK (app.has_firm_role(firm_id, ARRAY['owner','admin','accountant']));
CREATE POLICY journal_entries_delete ON public.journal_entries FOR DELETE TO authenticated
  USING (status = 'draft' AND app.has_firm_role(firm_id, ARRAY['owner','admin','accountant']));

ALTER TABLE public.journal_lines ENABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.journal_lines TO authenticated;
CREATE POLICY journal_lines_select ON public.journal_lines FOR SELECT TO authenticated
  USING (app.is_firm_member(firm_id) OR app.is_super_admin());
CREATE POLICY journal_lines_write ON public.journal_lines FOR ALL TO authenticated
  USING (app.has_firm_role(firm_id, ARRAY['owner','admin','accountant']))
  WITH CHECK (app.has_firm_role(firm_id, ARRAY['owner','admin','accountant']));

ALTER TABLE public.approvals ENABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT ON public.approvals TO authenticated;
CREATE POLICY approvals_select ON public.approvals FOR SELECT TO authenticated
  USING (app.is_firm_member(firm_id) OR app.is_super_admin());
CREATE POLICY approvals_insert ON public.approvals FOR INSERT TO authenticated
  WITH CHECK (actor_user_id = auth.uid() AND (
    (action = 'correct' AND app.has_firm_role(firm_id, ARRAY['owner','admin','accountant','clerk'])) OR
    (action <> 'correct' AND app.has_firm_role(firm_id, ARRAY['owner','admin','accountant']))));

-- ---------------------------------------------------------------- exports
DO $$
DECLARE
  t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['export_jobs', 'export_files'] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE ON public.%I TO authenticated', t);
    EXECUTE format($p$CREATE POLICY %1$s_select ON public.%1$I FOR SELECT TO authenticated
                     USING (app.is_firm_member(firm_id) OR app.is_super_admin())$p$, t);
    EXECUTE format($p$CREATE POLICY %1$s_insert ON public.%1$I FOR INSERT TO authenticated
                     WITH CHECK (app.has_firm_role(firm_id, ARRAY['owner','admin','accountant']))$p$, t);
    EXECUTE format($p$CREATE POLICY %1$s_update ON public.%1$I FOR UPDATE TO authenticated
                     USING (app.has_firm_role(firm_id, ARRAY['owner','admin','accountant']))
                     WITH CHECK (app.has_firm_role(firm_id, ARRAY['owner','admin','accountant']))$p$, t);
  END LOOP;
END
$$;

-- Safety net: fail the migration if any public table was left without RLS.
DO $$
DECLARE
  v_missing text;
BEGIN
  SELECT string_agg(c.relname, ', ') INTO v_missing
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE n.nspname = 'public' AND c.relkind = 'r' AND NOT c.relrowsecurity
    AND c.relname <> 'schema_migrations';
  IF v_missing IS NOT NULL THEN
    RAISE EXCEPTION 'tables without RLS: %', v_missing;
  END IF;
END
$$;
