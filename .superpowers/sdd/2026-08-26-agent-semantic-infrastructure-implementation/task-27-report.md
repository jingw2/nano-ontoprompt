# Task 27 report — Runtime and refresh operator surfaces

## Delivered

- Added `/api/v2`-client-relative Runtime and refresh adapters, typed REST
  shapes, protected Runtime routes, and operator pages for investigation,
  action-plan review/approval/execution, sandbox, reconciliation/rollback,
  and refresh operations.
- Backend workflow statuses are rendered uppercase in the UI while request
  values remain server-owned. The refresh page now also renders the durable
  replay state returned by `RefreshRunView`.
- The UI never constructs cursor, broker, credential, selector, SQL, write
  target, risk, authorization, or plan-hash values.

## TDD evidence

The inherited focused unit suite initially passed (14 tests). A new
`RefreshOperationsPage` test for the existing backend field
`latest_run.replay_status` failed as intended because
`data-testid="refresh-replay-state"` did not exist. The minimal rendering was
then added and the same focused test passed.

## Fresh verification

Executed from `frontend`:

```text
npm run test:unit -- src/api/runtime.test.ts src/api/refresh.test.ts src/pages/runtime/RuntimeInvestigationPage.test.tsx src/pages/runtime/ActionPlanPage.test.tsx src/pages/runtime/RefreshOperationsPage.test.tsx
5 test files passed; 14 tests passed; 0 failed.

npm run build
tsc -b && vite build exited 0.

npx playwright test src/test/e2e/runtime-governance.spec.ts
3 skipped; 0 failed.
```

The production build emitted only pre-existing bundle-size/dynamic-import
warnings and completed successfully.

## Real-API E2E constraint and handling

The direct ORM seed script inherited with this task was removed: it could
seed governed Runtime rows but violated the requirement to use authenticated
real APIs. The browser spec reads the real refresh IDs from
`test_data/runtime/playwright_seed.json` and is deliberately opt-in only
when `AGENT_E2E_RUNTIME_GOVERNANCE_READY=1` and the API base resolves to a
loopback host. A local/test runner must provision any Runtime plan and
reconciliation IDs through supported authenticated API flows and supply
`AGENT_E2E_RUNTIME_PLAN_ID` / `AGENT_E2E_RUNTIME_RECONCILIATION_ID`.

This environment has no supported REST endpoint that mints the delegated
Runtime credential required by `/api/v2/runtime/*`; therefore the normal
browser run self-skips instead of faking a browser credential, directly
inserting database rows, or contacting a production system.

`CONFIGURATION_DRIFT` remains intentionally untested as a rendered status:
the backend raises it only inside worker paths and does not persist it on a
queryable refresh-run response. No frontend mock or synthetic state was
introduced for it.

## Fix round 1 — usable browser Runtime credentials

The initial operator UI used the ordinary browser session bearer for
`/api/v2/runtime/*`, but those routes correctly require Task 13's persisted
`runtime_delegated` credential. The previous E2E guard therefore masked a
real usability bug.

Added two authenticated Runtime endpoints:

- `GET /api/v2/runtime/delegation-agents` lists only active agents configured
  for the Runtime REST audience.
- `POST /api/v2/runtime/delegations` lets the current signed-in user delegate
  only their own identity to a selected registered agent for an allowlisted
  scope. It calls the existing Task 13 issuer, persists only the token hash,
  and returns the short-lived bearer once.

The frontend now keeps this delegated token in a separate memory-only store.
Runtime requests use that token, while a Runtime 401/403 does not refresh or
log out the normal browser session. The route gate makes agent selection and
delegation explicit before loading a Runtime operator page.

The direct transport test is non-vacuous: it seeds the already-governed
runtime fixture, authenticates a session user, exchanges through
`POST /api/v2/runtime/delegations`, then calls the real
`POST /api/v2/runtime/investigate` with the returned token and receives
`ALLOW`.

Refresh schedule saving now retains and renders the persisted
`RefreshScheduleView.timezone` and `business_calendar`, rather than showing
the form's default UTC after a successful save.

Fresh fix-round verification:

```text
(cd backend && .venv/bin/python -m pytest tests/runtime/test_runtime_api.py -q)
16 passed.

(cd frontend && npm run test:unit -- src/api/runtime.test.ts src/api/refresh.test.ts src/pages/runtime/RuntimeInvestigationPage.test.tsx src/pages/runtime/ActionPlanPage.test.tsx src/pages/runtime/RefreshOperationsPage.test.tsx)
5 test files passed; 17 tests passed.

(cd frontend && npm run build)
tsc -b && vite build exited 0.

(cd frontend && npx playwright test src/test/e2e/runtime-governance.spec.ts)
3 skipped; no local backend advertised the required APIs.
```

The browser suite is no longer gated by a blanket readiness environment flag:
it runs only against a loopback API and self-skips when the local API is down
or lacks the required endpoint. Full plan approval/execute and refresh
mutation browser flows still require a local fixture provisioned through the
new authenticated delegation flow; this repository has no public API that
creates the governed snapshot/action/binding prerequisites from an empty
database. The backend transport test above proves the credential exchange and
authorized Runtime request without a database bypass.

## Fix round 2 — domain isolation and persisted schedules

- Runtime delegation-agent discovery now filters on the authenticated user's
  `security_domain_id`. Issuance repeats that filter before the Task 13
  issuer; a cross-domain or unknown agent gets the same generic
  `AGENT_INACTIVE` denial, preventing metadata enumeration. The transport
  test verifies both omission and denial.
- Added authenticated `GET /api/v2/refresh/sources/{source_id}/schedule`.
  Refresh Operations reads its durable `RefreshScheduleView` on mount, so a
  reload shows the persisted timezone and business calendar.
- The local Playwright action-plan flow now selects an agent and starts the
  Runtime delegation gate before expecting the authorized plan request.

```text
(cd backend && .venv/bin/python -m pytest tests/runtime/test_runtime_api.py tests/v2/incremental/test_refresh_api.py -q -k 'cross_domain or reads_persisted')
2 passed.

(cd frontend && npm run test:unit -- src/api/refresh.test.ts src/pages/runtime/RefreshOperationsPage.test.tsx src/api/runtime.test.ts)
3 test files passed; 13 tests passed.

(cd frontend && npm run build)
tsc -b && vite build exited 0.
```
