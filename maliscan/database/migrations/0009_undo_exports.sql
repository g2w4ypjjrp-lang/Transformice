-- =============================================================================
-- 0009: exports can be undone by owner/admin (file deleted, e.g. Luca rejected it):
--   journal entries exported -> approved (export links cleared), export jobs/files deletable.
-- =============================================================================
CREATE OR REPLACE FUNCTION app.journal_entries_state_machine() RETURNS trigger
LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
DECLARE
  v_locked date;
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF NEW.status <> 'draft' THEN
      RAISE EXCEPTION 'journal entries must be created as draft' USING ERRCODE = 'check_violation';
    END IF;
    NEW.total_debit := 0; NEW.total_credit := 0; NEW.line_count := 0;
    RETURN NEW;
  END IF;

  -- Non-status edits are only allowed on drafts (totals are trigger-maintained).
  IF OLD.status <> 'draft' AND NEW.status = OLD.status
     AND (NEW.entry_date, NEW.description, NEW.document_number, NEW.voucher_type, NEW.currency, NEW.exchange_rate, NEW.taxpayer_id)
         IS DISTINCT FROM
         (OLD.entry_date, OLD.description, OLD.document_number, OLD.voucher_type, OLD.currency, OLD.exchange_rate, OLD.taxpayer_id) THEN
    RAISE EXCEPTION 'only draft journal entries can be edited' USING ERRCODE = 'check_violation';
  END IF;

  IF NEW.status IS DISTINCT FROM OLD.status THEN
    IF NOT (
      (OLD.status = 'draft'    AND NEW.status IN ('approved', 'rejected')) OR
      (OLD.status = 'approved' AND NEW.status IN ('draft', 'exported')) OR
      (OLD.status = 'exported' AND NEW.status = 'approved') OR
      (OLD.status = 'rejected' AND NEW.status = 'draft')
    ) THEN
      RAISE EXCEPTION 'invalid journal status transition % -> %', OLD.status, NEW.status
        USING ERRCODE = 'check_violation';
    END IF;

    IF NEW.status = 'approved' AND OLD.status <> 'exported' THEN
      IF NEW.line_count < 2 OR NEW.total_debit <= 0 OR NEW.total_debit <> NEW.total_credit THEN
        RAISE EXCEPTION 'journal entry is not balanced (debit %, credit %, lines %)',
          NEW.total_debit, NEW.total_credit, NEW.line_count USING ERRCODE = 'check_violation';
      END IF;
      SELECT locked_until INTO v_locked FROM public.taxpayers WHERE id = NEW.taxpayer_id;
      IF v_locked IS NOT NULL AND NEW.entry_date <= v_locked THEN
        RAISE EXCEPTION 'accounting period is locked until %', v_locked USING ERRCODE = 'check_violation';
      END IF;
      IF auth.uid() IS NOT NULL THEN
        IF NOT app.has_firm_role(NEW.firm_id, ARRAY['owner', 'admin', 'accountant']) THEN
          RAISE EXCEPTION 'approval requires accountant role' USING ERRCODE = 'insufficient_privilege';
        END IF;
        NEW.approved_by := auth.uid();
      END IF;
      NEW.approved_at := coalesce(NEW.approved_at, now());
    ELSIF NEW.status = 'approved' AND OLD.status = 'exported' THEN
      -- export undone (file deleted / not imported into Luca): back to approved, unlinked
      IF auth.uid() IS NOT NULL AND NOT app.has_firm_role(NEW.firm_id, ARRAY['owner', 'admin']) THEN
        RAISE EXCEPTION 'undoing an export requires owner/admin role' USING ERRCODE = 'insufficient_privilege';
      END IF;
      NEW.export_job_id := NULL; NEW.export_file_id := NULL; NEW.exported_at := NULL;
    ELSIF NEW.status = 'draft' THEN
      NEW.approved_by := NULL;
      NEW.approved_at := NULL;
    END IF;
  END IF;
  RETURN NEW;
END
$$;

GRANT DELETE ON public.export_jobs, public.export_files TO authenticated;
CREATE POLICY export_jobs_delete ON public.export_jobs FOR DELETE TO authenticated
  USING (app.has_firm_role(firm_id, ARRAY['owner','admin']));
CREATE POLICY export_files_delete ON public.export_files FOR DELETE TO authenticated
  USING (app.has_firm_role(firm_id, ARRAY['owner','admin']));
