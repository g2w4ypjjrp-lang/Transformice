-- =============================================================================
-- 0004: journal entries/lines, approvals, Luca export jobs.
--
-- Accounting invariants enforced in the database (not only in Python):
--   * a line is either a debit or a credit, never both, never zero
--   * totals on the entry are maintained by trigger
--   * an entry can only become 'approved' if Σdebit = Σcredit > 0, ≥ 2 lines,
--     an approver is recorded, and the taxpayer period is not locked
--   * lines of non-draft entries cannot be changed
--   * status transitions follow a fixed state machine
-- =============================================================================

CREATE TABLE public.export_jobs (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  firm_id           uuid NOT NULL,
  taxpayer_id       uuid NOT NULL,
  export_format     text NOT NULL DEFAULT 'luca_journal_excel' CHECK (export_format IN ('luca_journal_excel')),
  template_version  text NOT NULL,
  status            text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'completed', 'failed')),
  voucher_count     integer NOT NULL DEFAULT 0 CHECK (voucher_count >= 0),
  file_count        integer NOT NULL DEFAULT 0 CHECK (file_count >= 0),
  error_code        text,
  created_by        uuid REFERENCES public.users (id) ON DELETE SET NULL,
  created_at        timestamptz NOT NULL DEFAULT now(),
  completed_at      timestamptz,
  UNIQUE (firm_id, id),
  FOREIGN KEY (firm_id, taxpayer_id) REFERENCES public.taxpayers (firm_id, id) ON DELETE RESTRICT
);
CREATE INDEX export_jobs_firm_idx ON public.export_jobs (firm_id, taxpayer_id, created_at DESC);

-- One Excel file per ≤50 vouchers (Luca import limit).
CREATE TABLE public.export_files (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  firm_id         uuid NOT NULL,
  export_job_id   uuid NOT NULL,
  file_index      integer NOT NULL CHECK (file_index > 0),
  storage_bucket  text NOT NULL DEFAULT 'exports',
  storage_path    text NOT NULL CHECK (storage_path LIKE firm_id::text || '/%'),
  voucher_count   integer NOT NULL CHECK (voucher_count BETWEEN 1 AND 50),
  sha256          char(64) NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (firm_id, id),
  UNIQUE (export_job_id, file_index),
  FOREIGN KEY (firm_id, export_job_id) REFERENCES public.export_jobs (firm_id, id) ON DELETE CASCADE
);

CREATE TABLE public.journal_entries (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  firm_id            uuid NOT NULL,
  taxpayer_id        uuid NOT NULL,
  document_id        uuid,
  extracted_data_id  uuid,
  entry_date         date NOT NULL,
  voucher_type       text NOT NULL DEFAULT 'mahsup' CHECK (voucher_type IN ('mahsup', 'tediye', 'tahsil')),
  document_number    text,
  description        text CHECK (length(description) <= 500),
  currency           char(3) NOT NULL DEFAULT 'TRY',
  exchange_rate      numeric(18, 6) NOT NULL DEFAULT 1 CHECK (exchange_rate > 0),
  status             text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'approved', 'rejected', 'exported')),
  generated_by       text NOT NULL DEFAULT 'engine' CHECK (generated_by IN ('engine', 'manual')),
  engine_version     text,
  warnings           jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(warnings) = 'array'),
  total_debit        numeric(18, 2) NOT NULL DEFAULT 0,
  total_credit       numeric(18, 2) NOT NULL DEFAULT 0,
  line_count         integer NOT NULL DEFAULT 0,
  approved_by        uuid REFERENCES public.users (id) ON DELETE SET NULL,
  approved_at        timestamptz,
  export_job_id      uuid,
  export_file_id     uuid,
  exported_at        timestamptz,
  created_by         uuid REFERENCES public.users (id) ON DELETE SET NULL,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  UNIQUE (firm_id, id),
  FOREIGN KEY (firm_id, taxpayer_id) REFERENCES public.taxpayers (firm_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (firm_id, document_id) REFERENCES public.documents (firm_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (firm_id, extracted_data_id) REFERENCES public.extracted_data (firm_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (firm_id, export_job_id) REFERENCES public.export_jobs (firm_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (firm_id, export_file_id) REFERENCES public.export_files (firm_id, id) ON DELETE RESTRICT,
  CHECK (status <> 'exported' OR (export_file_id IS NOT NULL AND exported_at IS NOT NULL)),
  CHECK (status NOT IN ('approved', 'exported') OR (approved_by IS NOT NULL AND approved_at IS NOT NULL))
);
-- One live (non-rejected) journal per document.
CREATE UNIQUE INDEX journal_entries_one_live_per_doc ON public.journal_entries (document_id)
  WHERE document_id IS NOT NULL AND status <> 'rejected';
CREATE INDEX journal_entries_firm_status_idx ON public.journal_entries (firm_id, taxpayer_id, status, entry_date);
CREATE TRIGGER journal_entries_touch BEFORE UPDATE ON public.journal_entries
  FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();

CREATE TABLE public.journal_lines (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  firm_id           uuid NOT NULL,
  journal_entry_id  uuid NOT NULL,
  line_no           integer NOT NULL CHECK (line_no > 0),
  account_code      app.account_code NOT NULL,
  description       text CHECK (length(description) <= 500),
  debit             numeric(18, 2) NOT NULL DEFAULT 0 CHECK (debit >= 0),
  credit            numeric(18, 2) NOT NULL DEFAULT 0 CHECK (credit >= 0),
  vat_rate          numeric(5, 2) CHECK (vat_rate BETWEEN 0 AND 100),
  document_item_id  uuid,
  created_at        timestamptz NOT NULL DEFAULT now(),
  UNIQUE (journal_entry_id, line_no),
  FOREIGN KEY (firm_id, journal_entry_id) REFERENCES public.journal_entries (firm_id, id) ON DELETE CASCADE,
  FOREIGN KEY (firm_id, document_item_id) REFERENCES public.document_items (firm_id, id) ON DELETE SET NULL (document_item_id),
  CONSTRAINT journal_lines_one_side CHECK ((debit > 0 AND credit = 0) OR (credit > 0 AND debit = 0))
);
CREATE INDEX journal_lines_entry_idx ON public.journal_lines (journal_entry_id);
CREATE INDEX journal_lines_firm_idx ON public.journal_lines (firm_id);

-- Lines may only change while the parent entry is a draft; keep totals in sync.
CREATE OR REPLACE FUNCTION app.journal_lines_guard_and_total() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
  v_entry uuid;
  v_status text;
BEGIN
  FOR v_entry IN
    SELECT DISTINCT e FROM unnest(ARRAY[
      CASE WHEN TG_OP <> 'INSERT' THEN OLD.journal_entry_id END,
      CASE WHEN TG_OP <> 'DELETE' THEN NEW.journal_entry_id END]) AS e
    WHERE e IS NOT NULL
  LOOP
    SELECT status INTO v_status FROM public.journal_entries WHERE id = v_entry FOR UPDATE;
    IF NOT FOUND THEN
      CONTINUE;  -- parent being deleted (cascade)
    END IF;
    IF v_status <> 'draft' THEN
      RAISE EXCEPTION 'journal entry % is %; lines are read-only', v_entry, v_status
        USING ERRCODE = 'check_violation';
    END IF;
    UPDATE public.journal_entries je
       SET total_debit  = s.d,
           total_credit = s.c,
           line_count   = s.n
      FROM (SELECT coalesce(sum(debit), 0) d, coalesce(sum(credit), 0) c, count(*) n
              FROM public.journal_lines WHERE journal_entry_id = v_entry) s
     WHERE je.id = v_entry;
  END LOOP;
  RETURN NULL;
END
$$;
CREATE TRIGGER journal_lines_totals
  AFTER INSERT OR UPDATE OR DELETE ON public.journal_lines
  FOR EACH ROW EXECUTE FUNCTION app.journal_lines_guard_and_total();

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
      (OLD.status = 'rejected' AND NEW.status = 'draft')
    ) THEN
      RAISE EXCEPTION 'invalid journal status transition % -> %', OLD.status, NEW.status
        USING ERRCODE = 'check_violation';
    END IF;

    IF NEW.status = 'approved' THEN
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
    ELSIF NEW.status = 'draft' THEN
      NEW.approved_by := NULL;
      NEW.approved_at := NULL;
    END IF;
  END IF;
  RETURN NEW;
END
$$;
CREATE TRIGGER journal_entries_state
  BEFORE INSERT OR UPDATE ON public.journal_entries
  FOR EACH ROW EXECUTE FUNCTION app.journal_entries_state_machine();

-- Approval history (append-only).
CREATE TABLE public.approvals (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  firm_id           uuid NOT NULL,
  document_id       uuid,
  journal_entry_id  uuid,
  action            text NOT NULL CHECK (action IN ('approve', 'reject', 'reopen', 'correct')),
  actor_user_id     uuid NOT NULL REFERENCES public.users (id) ON DELETE RESTRICT,
  comment           text CHECK (length(comment) <= 2000),
  created_at        timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (firm_id, document_id) REFERENCES public.documents (firm_id, id) ON DELETE CASCADE,
  FOREIGN KEY (firm_id, journal_entry_id) REFERENCES public.journal_entries (firm_id, id) ON DELETE CASCADE,
  CHECK (document_id IS NOT NULL OR journal_entry_id IS NOT NULL)
);
CREATE INDEX approvals_document_idx ON public.approvals (document_id, created_at);
CREATE INDEX approvals_entry_idx ON public.approvals (journal_entry_id, created_at);
CREATE INDEX approvals_firm_idx ON public.approvals (firm_id, created_at DESC);
CREATE TRIGGER approvals_append_only
  BEFORE UPDATE OR DELETE ON public.approvals
  FOR EACH ROW WHEN (pg_trigger_depth() = 0)
  EXECUTE FUNCTION app.forbid_mutation();
