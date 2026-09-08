/**
 * Wire types for the durable refresh operations REST surface (Tasks 6-10,
 * `backend/app/routers/v2/refresh.py` +
 * `backend/app/services/v2/incremental/operations.py`). These mirror
 * `RefreshRunView`/`RefreshStatus`/`RefreshScheduleView` field-for-field —
 * this file carries no logic, only shape.
 */

export interface RefreshRunView {
  run_id: string
  source_id: string
  resource: string
  policy: string
  trigger?: string | null
  status: string
  dispatch_state?: string | null
  dispatch_queue?: string | null
  config_version: number
  cursor_contract: string
  cursor_before?: Record<string, unknown> | null
  cursor_after?: Record<string, unknown> | null
  input_dataset_version_ids: string[]
  pipeline_run_id?: string | null
  lag_seconds?: number | null
  duplicate_count: number
  late_count: number
  retry_count: number
  retry_reason?: string | null
  dead_letter_id?: string | null
  replay_status?: string | null
  cancel_requested_at?: string | null
  cancel_requested_by?: string | null
  cancel_reason?: string | null
  terminal_at?: string | null
  already_terminal: boolean
}

export interface RefreshStatus {
  source_id: string
  resource: string
  policy: string
  config_version: number
  cursor_contract: string
  cursor?: Record<string, unknown> | null
  cursor_observed_at?: string | null
  fencing_token: number
  latest_run: RefreshRunView | null
  input_dataset_version_id?: string | null
  pipeline_run_id?: string | null
  lag_seconds?: number | null
  freshness_lag_seconds?: number | null
  duplicate_count: number
  late_count: number
  retry_count: number
  dlq_count: number
  next_schedule_at?: string | null
  sla_status: string
  backfill_window_seconds: number
}

export interface RefreshScheduleView {
  id: string
  target_type: string
  target_id: string
  cron_expr: string
  timezone: string
  business_calendar: string[]
  sla_seconds: number
  retry_policy?: Record<string, unknown> | null
  backfill_window_seconds: number
  max_pending_runs: number
  enabled: boolean
  next_due_at?: string | null
}

export interface RefreshScheduleRequest {
  cron_expr: string
  timezone?: string
  business_calendar?: string[]
  sla_seconds?: number
  retry_policy?: Record<string, unknown> | null
  backfill_window_seconds?: number
  max_pending_runs?: number
  enabled?: boolean
}

export interface RefreshTriggerRequest {
  mode: string
  backfill_from?: string | null
  backfill_to?: string | null
}

/** `POST /runs/{run_id}/cancel` returns a full `RefreshRunView` (202) when
 * the request took effect, or this plain, terminal shape (200) when the run
 * had already finished before the request arrived — never a distinct error. */
export interface RefreshAlreadyTerminal {
  status: string
  already_terminal: true
}

export type RefreshCancelResult = RefreshRunView | RefreshAlreadyTerminal
