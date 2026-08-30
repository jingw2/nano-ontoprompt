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
