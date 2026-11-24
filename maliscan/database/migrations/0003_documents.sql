-- =============================================================================
-- 0003: documents, extracted data (versioned), line items, processing queue.
-- =============================================================================

CREATE TABLE public.documents (
  id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  firm_id                uuid NOT NULL,
  taxpayer_id            uuid NOT NULL,
  uploaded_by            uuid REFERENCES public.users (id) ON DELETE SET NULL,
  source                 text NOT NULL CHECK (source IN ('camera', 'upload', 'email', 'api')),
  file_kind              text NOT NULL CHECK (file_kind IN ('image', 'pdf', 'xml')),
  document_type          text CHECK (document_type IN (
                           'thermal_receipt', 'retail_receipt', 'purchase_invoice', 'sales_invoice',
                           'e_invoice', 'e_archive_invoice', 'expense_slip', 'other')),
  direction              text CHECK (direction IN ('purchase', 'sales')),
  storage_bucket         text NOT NULL DEFAULT 'documents',
  storage_path           text NOT NULL,
  original_filename      text,
  mime_type              text NOT NULL,
  file_size              bigint NOT NULL CHECK (file_size > 0),
  sha256                 char(64) NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  page_count             integer CHECK (page_count > 0),
  capture_metadata       jsonb NOT NULL DEFAULT '{}'::jsonb,  -- device/crop info, no PII
  status                 text NOT NULL DEFAULT 'uploaded' CHECK (status IN (
                           'uploaded', 'queued', 'processing', 'needs_review',
                           'approved', 'rejected', 'exported', 'failed')),
  error_code             text,
  -- Denormalised key fields of the current extraction (listing, dedupe).
  supplier_tax_number    text,
  document_number        text,
  document_date          date,
  total_amount           numeric(18, 2),
  currency               char(3),
  overall_confidence     numeric(4, 3) CHECK (overall_confidence BETWEEN 0 AND 1),
  -- Business-level duplicate detection (same supplier + number + date + total).
  duplicate_fingerprint  text,
  duplicate_of           uuid,
  retention_until        date,
  created_at             timestamptz NOT NULL DEFAULT now(),
  updated_at             timestamptz NOT NULL DEFAULT now(),
  deleted_at             timestamptz,
  UNIQUE (firm_id, id),
  UNIQUE (storage_bucket, storage_path),
  FOREIGN KEY (firm_id, taxpayer_id) REFERENCES public.taxpayers (firm_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (firm_id, duplicate_of) REFERENCES public.documents (firm_id, id) ON DELETE SET NULL (duplicate_of),
  CHECK (duplicate_of IS NULL OR duplicate_of <> id),
  -- Storage paths are always namespaced by firm to make storage policies trivial.
  CHECK (storage_path LIKE firm_id::text || '/%')
);
-- Exact same file uploaded twice for the same taxpayer is rejected.
CREATE UNIQUE INDEX documents_file_dedupe_uq ON public.documents (firm_id, taxpayer_id, sha256)
  WHERE deleted_at IS NULL;
CREATE INDEX documents_firm_status_idx ON public.documents (firm_id, status, created_at DESC)
  WHERE deleted_at IS NULL;
CREATE INDEX documents_taxpayer_idx ON public.documents (firm_id, taxpayer_id, created_at DESC)
  WHERE deleted_at IS NULL;
CREATE INDEX documents_fingerprint_idx ON public.documents (firm_id, taxpayer_id, duplicate_fingerprint)
  WHERE duplicate_fingerprint IS NOT NULL AND deleted_at IS NULL;
CREATE TRIGGER documents_touch BEFORE UPDATE ON public.documents
  FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();

-- Each extraction or human correction creates a new version; exactly one is current.
CREATE TABLE public.extracted_data (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  firm_id             uuid NOT NULL,
  document_id         uuid NOT NULL,
  version             integer NOT NULL CHECK (version > 0),
  source              text NOT NULL CHECK (source IN ('ubl_xml', 'pdf_text', 'ocr_ai', 'manual')),
  ocr_provider        text,
  ai_model            text,
  schema_version      text NOT NULL DEFAULT '1',
  data                jsonb NOT NULL CHECK (jsonb_typeof(data) = 'object'),
  field_confidence    jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(field_confidence) = 'object'),
  overall_confidence  numeric(4, 3) CHECK (overall_confidence BETWEEN 0 AND 1),
  validation_issues   jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(validation_issues) = 'array'),
  raw_text            text,  -- preserved original OCR / XML-derived text
  is_current          boolean NOT NULL DEFAULT true,
  created_by          uuid REFERENCES public.users (id) ON DELETE SET NULL,  -- NULL = system
  created_at          timestamptz NOT NULL DEFAULT now(),
  UNIQUE (firm_id, id),
  UNIQUE (document_id, version),
  FOREIGN KEY (firm_id, document_id) REFERENCES public.documents (firm_id, id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX extracted_data_one_current ON public.extracted_data (document_id) WHERE is_current;
CREATE INDEX extracted_data_firm_idx ON public.extracted_data (firm_id);
CREATE TRIGGER extracted_data_immutable
  BEFORE UPDATE OF data, field_confidence, raw_text, version, document_id, firm_id ON public.extracted_data
  FOR EACH ROW EXECUTE FUNCTION app.forbid_mutation();

CREATE TABLE public.document_items (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  firm_id              uuid NOT NULL,
  document_id          uuid NOT NULL,
  extracted_data_id    uuid NOT NULL,
  line_no              integer NOT NULL CHECK (line_no > 0),
  description          text,
  quantity             numeric(18, 4),
  unit                 text,
  unit_price           numeric(18, 4),
  discount_amount      numeric(18, 2) CHECK (discount_amount >= 0),
  net_amount           numeric(18, 2),
  vat_rate             numeric(5, 2) CHECK (vat_rate BETWEEN 0 AND 100),
  vat_amount           numeric(18, 2),
  withholding_code     text,              -- KDV tevkifat kodu (e.g. 601..627)
  withholding_ratio    numeric(5, 4) CHECK (withholding_ratio BETWEEN 0 AND 1),  -- 5/10 → 0.5
  withholding_amount   numeric(18, 2) CHECK (withholding_amount >= 0),
  total_amount         numeric(18, 2),
  suggested_account    app.account_code,
  created_at           timestamptz NOT NULL DEFAULT now(),
  UNIQUE (firm_id, id),
  UNIQUE (extracted_data_id, line_no),
  FOREIGN KEY (firm_id, document_id) REFERENCES public.documents (firm_id, id) ON DELETE CASCADE,
  FOREIGN KEY (firm_id, extracted_data_id) REFERENCES public.extracted_data (firm_id, id) ON DELETE CASCADE
);
CREATE INDEX document_items_document_idx ON public.document_items (document_id);

-- PostgreSQL-backed job queue (workers use FOR UPDATE SKIP LOCKED).
-- payload holds identifiers only — never document content.
CREATE TABLE public.processing_jobs (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  firm_id          uuid NOT NULL REFERENCES public.firms (id) ON DELETE CASCADE,
  document_id      uuid,
  job_type         text NOT NULL CHECK (job_type IN ('extract_document', 'generate_journal', 'export_luca')),
  status           text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'dead')),
  priority         smallint NOT NULL DEFAULT 100,
  attempts         integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  max_attempts     integer NOT NULL DEFAULT 3 CHECK (max_attempts > 0),
  run_after        timestamptz NOT NULL DEFAULT now(),
  locked_by        text,
  locked_at        timestamptz,
  last_error_code  text,
  payload          jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_by       uuid REFERENCES public.users (id) ON DELETE SET NULL,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  finished_at      timestamptz,
  FOREIGN KEY (firm_id, document_id) REFERENCES public.documents (firm_id, id) ON DELETE CASCADE
);
CREATE INDEX processing_jobs_pick_idx ON public.processing_jobs (priority, run_after)
  WHERE status = 'queued';
CREATE INDEX processing_jobs_firm_idx ON public.processing_jobs (firm_id, created_at DESC);
-- Only one active extraction per document.
CREATE UNIQUE INDEX processing_jobs_one_active_per_doc ON public.processing_jobs (document_id, job_type)
  WHERE status IN ('queued', 'running');
CREATE TRIGGER processing_jobs_touch BEFORE UPDATE ON public.processing_jobs
  FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();
