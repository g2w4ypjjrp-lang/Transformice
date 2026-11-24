-- =============================================================================
-- 0002: taxpayers (mükellefler), chart of accounts, account mappings, rules.
--
-- Every tenant-owned table carries firm_id. Child tables reference their parent
-- through a composite (firm_id, id) foreign key, so a row can never point to a
-- parent belonging to another firm — enforced by PostgreSQL itself.
-- =============================================================================

-- Account code format used by Luca / Tek Düzen Hesap Planı: 3-digit main
-- account followed by optional dot-separated sub accounts (e.g. 770.01.003).
CREATE DOMAIN app.account_code AS text
  CHECK (VALUE ~ '^[0-9]{3}(\.[0-9A-Za-z]{1,10}){0,5}$');

CREATE TABLE public.taxpayers (
  id                         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  firm_id                    uuid NOT NULL REFERENCES public.firms (id) ON DELETE CASCADE,
  taxpayer_type              text NOT NULL CHECK (taxpayer_type IN ('company', 'individual')),
  title                      text NOT NULL CHECK (length(btrim(title)) BETWEEN 2 AND 250),
  -- VKN (10 digits) for companies, TCKN (11 digits) for individuals.
  tax_number                 text NOT NULL,
  tax_office                 text,
  trade_registry_number      text,
  email                      text,
  phone                      text,
  address                    text,
  -- Bilanço esası (balance sheet) vs. işletme hesabı esası (operating account book).
  bookkeeping_basis          text NOT NULL DEFAULT 'balance_sheet'
                               CHECK (bookkeeping_basis IN ('balance_sheet', 'operating_account')),
  fiscal_year_start_month    smallint NOT NULL DEFAULT 1 CHECK (fiscal_year_start_month BETWEEN 1 AND 12),
  -- Accounting periods on or before this date are closed: no new approvals.
  locked_until               date,
  default_payment_method     text NOT NULL DEFAULT 'cash'
                               CHECK (default_payment_method IN ('cash', 'bank', 'credit_card', 'supplier_credit')),
  default_cash_account       app.account_code DEFAULT '100.01',
  default_bank_account       app.account_code DEFAULT '102.01',
  default_credit_card_account app.account_code DEFAULT '300.01',
  default_supplier_account   app.account_code DEFAULT '320.01',
  default_customer_account   app.account_code DEFAULT '120.01',
  default_input_vat_account  app.account_code DEFAULT '191.01',
  default_output_vat_account app.account_code DEFAULT '391.01',
  default_expense_account    app.account_code DEFAULT '770.01',
  settings                   jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(settings) = 'object'),
  is_active                  boolean NOT NULL DEFAULT true,
  created_by                 uuid REFERENCES public.users (id) ON DELETE SET NULL,
  created_at                 timestamptz NOT NULL DEFAULT now(),
  updated_at                 timestamptz NOT NULL DEFAULT now(),
  deleted_at                 timestamptz,
  UNIQUE (firm_id, id),
  CONSTRAINT taxpayers_tax_number_format CHECK (
    (taxpayer_type = 'company'    AND tax_number ~ '^[0-9]{10}$') OR
    (taxpayer_type = 'individual' AND tax_number ~ '^[1-9][0-9]{10}$')
  )
);
CREATE UNIQUE INDEX taxpayers_firm_tax_number_uq ON public.taxpayers (firm_id, tax_number)
  WHERE deleted_at IS NULL;
CREATE INDEX taxpayers_firm_title_idx ON public.taxpayers (firm_id, lower(title));
CREATE TRIGGER taxpayers_touch BEFORE UPDATE ON public.taxpayers
  FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();

CREATE TABLE public.chart_of_accounts (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  firm_id         uuid NOT NULL,
  taxpayer_id     uuid NOT NULL,
  code            app.account_code NOT NULL,
  name            text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 250),
  parent_code     app.account_code,
  account_class   text NOT NULL CHECK (account_class IN
                    ('asset', 'liability', 'equity', 'income', 'expense', 'cost', 'memo')),
  normal_balance  text NOT NULL CHECK (normal_balance IN ('debit', 'credit')),
  is_postable     boolean NOT NULL DEFAULT true,  -- only leaf accounts receive journal lines
  is_active       boolean NOT NULL DEFAULT true,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (firm_id, taxpayer_id) REFERENCES public.taxpayers (firm_id, id) ON DELETE CASCADE,
  UNIQUE (taxpayer_id, code),
  CHECK (parent_code IS NULL OR code LIKE parent_code || '.%')
);
CREATE INDEX chart_of_accounts_firm_idx ON public.chart_of_accounts (firm_id);
CREATE TRIGGER chart_of_accounts_touch BEFORE UPDATE ON public.chart_of_accounts
  FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();

-- Learned / configured mapping from a document attribute to an account.
-- taxpayer_id NULL = firm-wide default applied to all taxpayers.
CREATE TABLE public.account_mappings (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  firm_id             uuid NOT NULL REFERENCES public.firms (id) ON DELETE CASCADE,
  taxpayer_id         uuid,
  match_field         text NOT NULL CHECK (match_field IN
                        ('supplier_tax_number', 'supplier_name', 'item_keyword', 'expense_category', 'document_type')),
  match_value         text NOT NULL CHECK (length(btrim(match_value)) > 0),
  account_code        app.account_code NOT NULL,
  vat_account_code    app.account_code,
  is_non_deductible   boolean NOT NULL DEFAULT false,  -- KKEG
  vat_deductible      boolean NOT NULL DEFAULT true,   -- false → VAT goes to expense/KKEG
  priority            integer NOT NULL DEFAULT 100,
  source              text NOT NULL DEFAULT 'manual' CHECK (source IN ('manual', 'learned', 'import')),
  is_active           boolean NOT NULL DEFAULT true,
  created_by          uuid REFERENCES public.users (id) ON DELETE SET NULL,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (firm_id, taxpayer_id) REFERENCES public.taxpayers (firm_id, id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX account_mappings_uq ON public.account_mappings
  (firm_id, taxpayer_id, match_field, lower(match_value)) NULLS NOT DISTINCT;
CREATE INDEX account_mappings_lookup_idx ON public.account_mappings (firm_id, taxpayer_id, match_field)
  WHERE is_active;
CREATE TRIGGER account_mappings_touch BEFORE UPDATE ON public.account_mappings
  FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();

-- Declarative rules evaluated by the deterministic accounting engine.
-- conditions: {"all":[{"field":"supplier_tax_number","op":"eq","value":"..."}]}
-- actions:    {"expense_account":"770.01.003","non_deductible":true,...}
CREATE TABLE public.accounting_rules (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  firm_id           uuid NOT NULL REFERENCES public.firms (id) ON DELETE CASCADE,
  taxpayer_id       uuid,
  name              text NOT NULL CHECK (length(btrim(name)) BETWEEN 2 AND 200),
  description       text,
  priority          integer NOT NULL DEFAULT 100,
  conditions        jsonb NOT NULL CHECK (jsonb_typeof(conditions) = 'object'),
  actions           jsonb NOT NULL CHECK (jsonb_typeof(actions) = 'object'),
  stop_processing   boolean NOT NULL DEFAULT true,
  is_active         boolean NOT NULL DEFAULT true,
  created_by        uuid REFERENCES public.users (id) ON DELETE SET NULL,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (firm_id, taxpayer_id) REFERENCES public.taxpayers (firm_id, id) ON DELETE CASCADE
);
CREATE INDEX accounting_rules_lookup_idx ON public.accounting_rules (firm_id, taxpayer_id, priority)
  WHERE is_active;
CREATE TRIGGER accounting_rules_touch BEFORE UPDATE ON public.accounting_rules
  FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at();
