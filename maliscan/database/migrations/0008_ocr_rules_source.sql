-- =============================================================================
-- 0008: extraction source for the free on-server OCR + rule parser.
-- =============================================================================
ALTER TABLE public.extracted_data DROP CONSTRAINT IF EXISTS extracted_data_source_check;
ALTER TABLE public.extracted_data ADD CONSTRAINT extracted_data_source_check
  CHECK (source IN ('ubl_xml', 'pdf_text', 'ocr_ai', 'ocr_rules', 'manual'));
