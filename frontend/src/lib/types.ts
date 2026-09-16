/**
 * Response shapes of the API this interface talks to.
 *
 * Hand-written from the FastAPI schemas (app/schemas/*). Kept narrow: only the
 * fields a screen reads. When the OpenAPI document is generated into types
 * (`openapi-typescript`), this file is what it replaces.
 */

export interface Principal {
  user_id: string;
  organization_id: string;
  email: string;
  display_name: string;
  roles: string[];
}

export interface Vendor {
  id: string;
  code: string;
  name: string;
  status: string;
  is_active: boolean;
  currency: string | null;
  contact_email: string | null;
  minimum_order_quantity: number | null;
  minimum_order_value: string | null;
  created_at: string;
  updated_at: string;
}

export interface ColumnMapping {
  target: string;
  source: string | number;
  required: boolean;
}

export type RuleColumn =
  | "column_map"
  | "normalization_rules"
  | "availability_rules"
  | "quantity_semantics"
  | "price_semantics"
  | "pack_size_handling";

export interface ImportProfile {
  id: string;
  vendor_id: string;
  name: string;
  version: number;
  is_active: boolean;
  file_format: "CSV" | "XLSX";
  encoding: string | null;
  delimiter: string | null;
  quote_char: string | null;
  header_row_index: number;
  skip_rows: number;
  sheet_name: string | null;
  sheet_index: number | null;
  column_map: { columns: ColumnMapping[] };
  normalization_rules: Record<string, unknown>;
  availability_rules: Record<string, unknown>;
  quantity_semantics: Record<string, unknown>;
  price_semantics: Record<string, unknown>;
  pack_size_handling: Record<string, unknown>;
  header_signature: string | null;
  created_at: string;
}

export type JsonSchema = Record<string, unknown>;

export interface RuleSchemas {
  schemas: Record<string, JsonSchema>;
}

export interface Issue {
  code: string;
  severity: string;
  field: string;
  message: string;
}

export interface PreviewRow {
  row_number: number;
  status: string;
  raw: Record<string, string>;
  values: Record<string, unknown>;
  issues: Issue[];
}

export interface PreviewColumn {
  target: string;
  source: string | number;
  index: number | null;
  header: string | null;
  required: boolean;
}

export interface ValidationPreview {
  file_name: string | null;
  encoding: string | null;
  sheet: string | null;
  truncated: boolean;
  headers: string[];
  header_signature: string;
  expected_signature: string | null;
  signature_matches: boolean | null;
  header_ok: boolean;
  columns: PreviewColumn[];
  issues: string[];
  rows: PreviewRow[];
  notes: string[];
}

export interface ImportFile {
  id: string;
  original_filename: string;
  sha256: string;
  size_bytes: number;
  detected_encoding: string | null;
  uploaded_at: string;
}

export interface ImportJob {
  id: string;
  vendor_id: string;
  import_file: ImportFile;
  vendor_import_profile_id: string | null;
  profile_version: number | null;
  status: string;
  current_stage: string | null;
  total_rows: number;
  processed_rows: number;
  matched_rows: number;
  exception_rows: number;
  error_rows: number;
  skipped_rows: number;
  error_message: string | null;
  error_details: Record<string, unknown>;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
}

export interface ImportUpload {
  job: ImportJob;
  duplicate: boolean;
}

export interface ImportReport {
  job_id: string;
  status: string;
  current_stage: string | null;
  counters: Record<string, number>;
  by_error_code: Record<string, number>;
  issues_by_code: Record<string, number>;
  summary: string;
  error_message: string | null;
  error_details: Record<string, unknown>;
}

export interface ImportRow {
  id: string;
  row_number: number;
  status: string;
  error_code: string | null;
  error_message: string | null;
  raw_data: Record<string, string>;
  normalized_data: Record<string, unknown>;
  vendor_sku: string | null;
  normalized_upc: string | null;
  description: string | null;
  quantity: string | null;
  unit_cost: string | null;
  availability_status: string | null;
}

export interface ProductSummary {
  id: string;
  catalog_item_number: string;
  name: string;
  brand: string | null;
  is_active: boolean;
}

export interface Candidate {
  product_id: string;
  rule: string;
  priority: number;
  identifier: string | null;
  score: number | null;
  reason: string | null;
  product: ProductSummary | null;
}

export interface MappingException {
  id: string;
  reason: string;
  status: string;
  vendor_id: string | null;
  vendor_product_id: string | null;
  import_job_id: string | null;
  import_job_row_id: string | null;
  suggested_product_id: string | null;
  suggestion_score: string | null;
  deferred_until: string | null;
  resolved_product_id: string | null;
  resolved_by_user_id: string | null;
  resolved_at: string | null;
  resolution_note: string | null;
  created_at: string;
  vendor_sku: string | null;
  description: string | null;
  row_number: number | null;
  age_hours: number | null;
}

export interface RuleEvaluation {
  rule: string;
  priority: number;
  input: string | null;
  outcome: string;
  candidates: string[];
  note: string | null;
}

export interface VendorLine {
  id: string;
  vendor_sku: string;
  vendor_description: string | null;
  normalized_upc: string | null;
  product_id: string | null;
  mapping_status: string;
}

export interface MappingExceptionDetail extends MappingException {
  candidates: Candidate[];
  match_evaluations: { source?: string; rules?: RuleEvaluation[] };
  source_row: ImportRow | null;
  vendor_line: VendorLine | null;
  suggested_product: ProductSummary | null;
  resolved_product: ProductSummary | null;
}

export interface Resolution {
  exception: MappingExceptionDetail;
  superseded_product_id: string | null;
}

export interface WatchlistEntry {
  id: string;
  product_id: string;
  product: { id: string; catalog_item_number: string; name: string };
  vendor_id: string | null;
  reason: string | null;
  priority: string;
  desired_quantity: string | null;
  max_unit_cost: string | null;
  current_status: string;
  current_status_changed_at: string | null;
  is_active: boolean;
  added_at: string;
  deactivated_at: string | null;
  notes: string | null;
}

export interface StatusHistory {
  id: string;
  previous_status: string | null;
  new_status: string;
  vendor_id: string | null;
  changed_at: string;
}

export interface AvailabilityEvent {
  id: string;
  vendor_id: string;
  vendor_product_id: string;
  product_id: string | null;
  oos_watchlist_id: string | null;
  event_type: string;
  previous_status: string | null;
  new_status: string;
  previous_quantity: string | null;
  new_quantity: string | null;
  over_max_unit_cost: boolean | null;
  detected_at: string;
  vendor_sku: string | null;
  product_name: string | null;
  catalog_item_number: string | null;
}

export interface AuditEvent {
  id: string;
  actor_type: string;
  actor_user_id: string | null;
  actor_label: string | null;
  action: string;
  entity_type: string;
  entity_id: string | null;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  changed_fields: string[] | null;
  request_id: string | null;
  summary: string | null;
  occurred_at: string;
}

export interface AuditPage {
  items: AuditEvent[];
  page: number;
  page_size: number;
  total: number;
  entity_types: string[];
  actions: string[];
}

export interface SyncSummary {
  job_type: string | null;
  status: string | null;
  started_at: string | null;
  completed_at: string | null;
  error_message: string | null;
}

export interface LastImport {
  vendor_id: string;
  vendor_code: string;
  vendor_name: string;
  job_id: string | null;
  status: string | null;
  completed_at: string | null;
  created_at: string | null;
  total_rows: number | null;
  exception_rows: number | null;
}

export interface Dashboard {
  open_exceptions: number;
  watchlist_active: number;
  watchlist_in_stock: number;
  watchlist_newly_available_7d: number;
  imports_running: number;
  last_imports: LastImport[];
  amazon_syncs: SyncSummary[];
  nineyard_sync: SyncSummary | null;
  generated_at: string;
}
