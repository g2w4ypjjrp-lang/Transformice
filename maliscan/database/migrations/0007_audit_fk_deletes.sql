-- =============================================================================
-- 0007: allow firm/user deletion (KVKK erasure, office termination).
--
-- audit_logs is append-only, but its foreign keys use ON DELETE SET NULL. The
-- referential action is an internal UPDATE executed from a trigger (depth > 0),
-- which the append-only guard rejected — so no firm or user could be deleted.
-- Direct UPDATE/DELETE by any role (depth 0) stays forbidden.
-- =============================================================================
DROP TRIGGER IF EXISTS audit_logs_append_only ON public.audit_logs;
CREATE TRIGGER audit_logs_append_only
  BEFORE UPDATE OR DELETE ON public.audit_logs
  FOR EACH ROW WHEN (pg_trigger_depth() = 0)
  EXECUTE FUNCTION app.forbid_mutation();
