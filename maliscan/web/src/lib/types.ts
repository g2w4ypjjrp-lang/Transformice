export type Role = "owner" | "admin" | "accountant" | "clerk" | "viewer";
export interface Membership { firm_id: string; firm_name: string; role: Role }
export interface Me { id: string; email: string; full_name: string | null; locale: string; is_super_admin: boolean; memberships: Membership[] }
export interface Taxpayer {
  id: string; taxpayer_type: "company" | "individual"; title: string; tax_number: string; tax_office: string | null;
  bookkeeping_basis: string; fiscal_year_start_month: number; locked_until: string | null; default_payment_method: string;
  default_cash_account: string | null; default_bank_account: string | null; default_credit_card_account: string | null;
  default_supplier_account: string | null; default_customer_account: string | null; default_input_vat_account: string | null;
  default_output_vat_account: string | null; default_expense_account: string | null; is_active: boolean;
  email: string | null; phone: string | null; address: string | null; settings: Record<string, unknown>;
  stats?: { documents: number; pending_review: number } | null; warnings?: string[];
}
export interface DocumentSummary {
  id: string; taxpayer_id: string; taxpayer_title: string | null; source: string; file_kind: string;
  document_type: string | null; direction: string | null; status: string; error_code: string | null;
  supplier_tax_number: string | null; supplier_name: string | null; document_number: string | null;
  document_date: string | null; total_amount: string | null; currency: string | null;
  overall_confidence: string | null; duplicate_of: string | null; original_filename: string | null; created_at: string;
}
export interface JournalLine { line_no?: number; account_code: string; account_name?: string | null; description: string | null; debit: string; credit: string; vat_rate?: string | null; source_note?: string | null }
export interface Journal { id: string; entry_date: string; description: string | null; document_number: string | null; status: string; warnings: { code: string; message: string }[]; total_debit: string; total_credit: string; lines: JournalLine[]; voucher_type: string }
export interface Issue { severity: "error" | "warning" | "info"; code: string; message: string; field: string | null }
export interface Account { id: string; code: string; name: string; account_class: string; is_postable: boolean; is_active: boolean }
