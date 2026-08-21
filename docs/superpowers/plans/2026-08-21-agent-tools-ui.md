# Agent Module Tool-Selection UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an Agent editor choose an Ontology (with its Logic/Action tool categories) and bind live external-tool connections (Search / Playwright / External MCP) both during Agent creation and afterwards on the detail page's Tool tab — replacing the fully-hardcoded "Available later" `ExternalToolCard` placeholder with a working bind/unbind UI, and extending `AgentCreateWizard` (currently name/description/model only) to offer the same selection inline at creation time.

**Architecture:** Two new backend GET endpoints expose (a) which approved, active `tool_connection_versions` of kind `search|playwright|external_mcp` an Agent may bind to, and (b) which are currently bound to a given Agent version — closing the gap that the existing P7D bind/unbind write endpoints (`POST/DELETE /agents/{id}/versions/{vid}/external-tools[/{alias}]`) have had since they were built with no way to list state. On the frontend, the existing ontology-binding logic embedded in `ToolConfigTab.tsx` is extracted into a shared hook + presentational component so `AgentCreateWizard.tsx` can reuse it verbatim; a rewritten `ExternalToolCard.tsx` becomes a props-driven bind/unbind list reused identically by both the detail tab (real API calls) and the wizard (local pending-state, applied via sequential bind calls after Agent creation succeeds).

**Tech Stack:** FastAPI + SQLAlchemy Core (`text()` queries) on the backend; React + TypeScript + `react-i18next` + Vitest/Testing Library/MSW on the frontend. No new database tables or migrations — this plan only adds read routes over existing tables (`tool_providers`, `tool_connections`, `tool_connection_versions`, `agent_external_tool_bindings`).

**Spec:** No standalone spec document exists for this feature; it is a direct UI/API-completion follow-up to the already-merged P7D (`agent_external_tool_bindings` write API) and P7-UI (`tool_connections` admin console) plans. This plan is self-contained; grounding facts (file:line citations) are embedded in each task below in place of a separate spec doc.

## Global Constraints

- New routes live under the existing `/api/v1/agents` path prefix, which `backend/app/services/agent/policy.py:134` (`_PLATFORM_PREFIXES`) already covers — do not add new prefixes to `policy.py`.
- Any task that adds or changes a backend route MUST, as its last step before committing, run `cd backend && python -m scripts.generate_openapi` and commit the regenerated `backend/openapi-agent.json` in the same commit, then run `pytest backend/tests/agent/test_core_route_registration.py backend/tests/agent/test_agent_policy.py backend/tests/agent/test_role_unification.py -q` and confirm all pass. This project has hit "stale pinned manifest caught only later" three times before (OAuth PKCE plan, Ontology MCP Server plan, P7-UI plan) — do not repeat it.
- New/changed GET responses must never include `credential_reference` (P7-UI's final review caught a real plaintext-credential-exposure bug on this exact table; `backend/app/services/tool_connections.py:306-312`'s `list_connection_versions` already excludes it — match that precedent, do not regress it).
- Alias validation stays exactly `^[A-Za-z0-9_-]{1,55}$`, `max_length=55` (`backend/app/schemas/agents.py:154`) — this plan reuses the existing `BindExternalToolRequest`/`bind_external_tool`/`unbind_external_tool` write path unchanged; no new write endpoint or new alias rule is introduced.
- Scope is `search`, `playwright`, `external_mcp` provider kinds only (`LIVE_PROVIDER_KINDS` in `frontend/src/api/toolConnections.ts:49`). The `skill`/`ontology_mcp` provider kinds, and the entirely separate Signed-Skills data path (`skill_packages`/`skill_versions`/`agent_skill_bindings`/`bind_skill`/`unbind_skill`), are explicitly OUT of scope — confirmed with the user. The "Signed Skills" placeholder card in `ExternalToolCard.tsx` must remain visually present, unchanged, still reading "Available later".
- The Ontology's own inbound `enabled_categories` `'mcp'` self-exposure toggle (already working, in `ToolConfigTab.tsx`'s category checkboxes) is untouched by this plan. "External MCP" in this plan means only `agent_external_tool_bindings` rows pointing at a `tool_connection_versions` row whose provider `kind = 'external_mcp'` — a different concept the user explicitly disambiguated via clarifying question.
- Existing ontology-binding behavior (one Ontology per Agent, category toggles cascade to per-descriptor checkboxes, `enabled_categories` legacy-null handling) must not regress. Tasks that extract this logic into shared code prove it by re-running the pre-existing test suite unchanged and getting the same pass count.

---

### Task 1: Backend — external-tool catalog + current-bindings read endpoints

**Files:**
- Modify: `backend/app/services/agent/catalog.py` (add `agent_external_tool_catalog`)
- Modify: `backend/app/services/agent/configuration.py` (add `list_external_tool_bindings`)
- Modify: `backend/app/routers/agents.py` (add two GET routes + imports)
- Test: `backend/tests/agent/test_agent_external_tool_binding.py` (add fixture helper + 4 tests)
- Modify: `backend/openapi-agent.json` (regenerated, not hand-edited)

**Interfaces:**
- Consumes: `bind_external_tool`'s approval-check shape (`tool_connection_versions.approval_status`), `tool_connections.active_version_id`, `tool_providers.kind` — all read-only, no schema changes.
- Produces: `agent_external_tool_catalog(db: Session) -> list[dict]` (each dict: `tool_connection_version_id, connection_id, version_no, provider_id, provider_name, provider_kind, health_status`) and `list_external_tool_bindings(db: Session, *, agent_version_id: str) -> list[dict]` (each dict: `id, alias, tool_connection_version_id, connection_id, version_no, provider_name, provider_kind, approval_status, health_status`). Task 3's frontend API client mirrors these exact field names — do not rename.

- [ ] **Step 1: Write the failing backend tests**

Open `backend/tests/agent/test_agent_external_tool_binding.py`. Add this helper right after the existing `_approved_version` function (after line 83):

```python
def _active_version(session, kind: str = "search") -> dict:
    """An approved AND activated connection version — what the new catalog
    endpoint should surface as bindable."""
    from app.services.tool_connections import (
        activate_connection_version, approve_connection_version,
        create_connection, create_connection_version, create_provider,
    )
    provider = create_provider(session, actor_id="u-1", name=f"{kind}-provider", kind=kind)
    connection = create_connection(session, actor_id="u-1", provider_id=provider["id"])
    version = create_connection_version(session, actor_id="u-1", connection_id=connection["id"],
                                        endpoint="https://example.com/v1")
    approve_connection_version(session, actor_id="u-1", version_id=version["id"])
    activate_connection_version(session, actor_id="u-1", connection_id=connection["id"],
                                version_id=version["id"])
    return {"connection_id": connection["id"], "version_id": version["id"],
            "provider_name": provider["name"], "provider_kind": kind}
```

Then append these four tests at the end of the file:

```python
def test_external_tool_catalog_lists_only_active_approved_live_kinds(session):
    from app.services.agent.catalog import agent_external_tool_catalog
    from app.services.tool_connections import create_connection, create_connection_version, create_provider
    active = _active_version(session, kind="external_mcp")
    # approved connection version that was never activated -> excluded
    provider2 = create_provider(session, actor_id="u-1", name="unactivated", kind="playwright")
    connection2 = create_connection(session, actor_id="u-1", provider_id=provider2["id"])
    create_connection_version(session, actor_id="u-1", connection_id=connection2["id"])
    # active+approved but a non-live kind -> excluded
    _active_version(session, kind="skill")
    items = agent_external_tool_catalog(session)
    assert [i["tool_connection_version_id"] for i in items] == [active["version_id"]]
    assert items[0]["provider_kind"] == "external_mcp"
    assert items[0]["provider_name"] == active["provider_name"]
    assert "credential_reference" not in items[0]


def test_catalog_route_requires_authentication(session):
    from fastapi.testclient import TestClient
    from app.deps import get_db
    from app.main import app

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            r = client.get("/api/v1/agents/catalog/external-tools")
            assert r.status_code == 401, r.text
    finally:
        app.dependency_overrides.clear()


def test_list_bindings_route_returns_joined_metadata(session):
    from fastapi.testclient import TestClient
    from app.deps import get_db
    from app.main import app
    from app.services.agent.configuration import bind_external_tool
    from app.services.auth_service import create_access_token

    active = _active_version(session, kind="search")
    bind_external_tool(session, actor_id="u-1", agent_version_id="av-1",
                       tool_connection_version_id=active["version_id"], alias="search")
    session.execute(text(
        "INSERT INTO agent_access_grants (id, agent_id, user_id, capabilities, status, created_by) "
        "VALUES ('aag-1', 'ag-1', 'u-1', '[\"view_config\"]'::json, 'active', 'u-1')"
    ))
    session.commit()

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            headers = {"Authorization": f"Bearer {create_access_token({'sub': 'u-1', 'role': 'admin'})}"}
            r = client.get("/api/v1/agents/ag-1/versions/av-1/external-tools", headers=headers)
            assert r.status_code == 200, r.text
            items = r.json()["data"]["items"]
            assert len(items) == 1
            assert items[0]["alias"] == "search"
            assert items[0]["provider_kind"] == "search"
            assert items[0]["approval_status"] == "approved"
    finally:
        app.dependency_overrides.clear()


def test_list_bindings_route_hides_cross_agent_version(session):
    """A grant on agent A must not reach agent B's version — same
    existence-hiding contract as the write endpoints (test_cross_agent_version_rejected above)."""
    from fastapi.testclient import TestClient
    from app.deps import get_db
    from app.main import app
    from app.services.auth_service import create_access_token

    app_schema_version_id = session.execute(text(
        "SELECT active_version_id FROM application_state_schema_registries WHERE application_key = 'chat-v1'"
    )).scalar_one()
    session.execute(text(
        "INSERT INTO agents (id,visibility,status,owner_id,created_at,updated_at) "
        "VALUES ('ag-2','private','active','u-1',now(),now())"
    ))
    session.execute(text(
        "INSERT INTO agent_versions (id, agent_id, version_no, name, default_model_config_version_id, "
        "default_model_name, system_prompt, application_state_schema_version_id, config_hash, created_by, created_at) "
        "VALUES ('av-2', 'ag-2', 1, 'test-version', 'mcv-1', 'test-model', '', :svid, 'h', 'u-1', now())"
    ), {"svid": app_schema_version_id})
    session.execute(text(
        "INSERT INTO agent_access_grants (id, agent_id, user_id, capabilities, status, created_by) "
        "VALUES ('aag-1', 'ag-1', 'u-1', '[\"view_config\"]'::json, 'active', 'u-1')"
    ))
    session.commit()

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            headers = {"Authorization": f"Bearer {create_access_token({'sub': 'u-1', 'role': 'admin'})}"}
            r = client.get("/api/v1/agents/ag-1/versions/av-2/external-tools", headers=headers)
            assert r.status_code == 404, r.text
    finally:
        app.dependency_overrides.clear()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && TEST_DATABASE_URL=<your test db url> pytest tests/agent/test_agent_external_tool_binding.py -v -k "catalog or list_bindings"`
Expected: FAIL — `agent_external_tool_catalog`/`list_external_tool_bindings` don't exist yet, and the two routes 404.

- [ ] **Step 3: Implement `agent_external_tool_catalog`**

In `backend/app/services/agent/catalog.py`, add this function near `agent_catalog_ontologies`/`agent_catalog_models`:

```python
def agent_external_tool_catalog(db: Session) -> list[dict]:
    """Search/Playwright/External-MCP connections an Agent may bind to: the
    active version of each active connection, already admin-approved via
    P7-UI — matches bind_external_tool's own approval check
    (configuration.py:379-385) so nothing shown here can fail to bind."""
    rows = db.execute(text(
        "SELECT tcv.id AS tool_connection_version_id, tcv.connection_id, tcv.version_no, "
        "tp.id AS provider_id, tp.name AS provider_name, tp.kind AS provider_kind, "
        "tcv.health_status "
        "FROM tool_connections tc "
        "JOIN tool_connection_versions tcv ON tcv.id = tc.active_version_id "
        "JOIN tool_providers tp ON tp.id = tc.provider_id "
        "WHERE tc.status = 'active' AND tcv.approval_status = 'approved' "
        "AND tp.kind IN ('search', 'playwright', 'external_mcp') "
        "ORDER BY tp.name, tc.id"
    )).mappings().all()
    return [dict(r) for r in rows]
```

- [ ] **Step 4: Implement `list_external_tool_bindings`**

In `backend/app/services/agent/configuration.py`, add this function near `bind_external_tool`/`unbind_external_tool`:

```python
def list_external_tool_bindings(db: Session, *, agent_version_id: str) -> list[dict]:
    """Current external-tool bindings for one Agent version, joined with the
    connection/provider metadata the UI needs to render them (no listing
    function existed before this — bind/unbind were write-only)."""
    rows = db.execute(text(
        "SELECT aetb.id, aetb.alias, aetb.tool_connection_version_id, tcv.connection_id, "
        "tcv.version_no, tp.name AS provider_name, tp.kind AS provider_kind, "
        "tcv.approval_status, tcv.health_status "
        "FROM agent_external_tool_bindings aetb "
        "JOIN tool_connection_versions tcv ON tcv.id = aetb.tool_connection_version_id "
        "JOIN tool_connections tc ON tc.id = tcv.connection_id "
        "JOIN tool_providers tp ON tp.id = tc.provider_id "
        "WHERE aetb.agent_version_id = :id ORDER BY aetb.alias"
    ), {"id": agent_version_id}).mappings().all()
    return [dict(r) for r in rows]
```

- [ ] **Step 5: Add the two routes**

Routes in this router return plain dicts through the `{"data": {"items": [...]}}` envelope with no `response_model` — every existing catalog/list route here does the same (`catalog_ontologies` at `agents.py:314-316`, and `list_providers_route`/`list_connections_route` in `backend/app/routers/tool_connections.py:39-63`). Follow that precedent; do not add new Pydantic response schemas for these two read-only list endpoints — they would have no other consumer and FastAPI doesn't need one to serialize the dicts.

In `backend/app/routers/agents.py`, add `agent_external_tool_catalog` to the `from app.services.agent.catalog import (...)` block and `list_external_tool_bindings` to the `from app.services.agent.configuration import (...)` block.

Add these two routes immediately after `catalog_models` (after line 321, before `validate_agent_tool_bindings`):

```python
@router.get("/catalog/external-tools")
def catalog_external_tools(db: Session = Depends(get_db), current_user: User = Depends(require_editor)):
    return {"data": {"items": agent_external_tool_catalog(db)}}
```

Add this route immediately after `unbind_skill_route` (after line 509, before `agent_access_grants_list`):

```python
@router.get("/{agent_id}/versions/{version_id}/external-tools")
def list_external_tools_route(agent_id: str, version_id: str, db: Session = Depends(get_db),
                              current_user: User = Depends(get_current_user)):
    _require_agent_grant(db, current_user.id, agent_id, "view_config")
    _require_agent_version_owned(db, agent_id, version_id)
    return {"data": {"items": list_external_tool_bindings(db, agent_version_id=version_id)}}
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd backend && TEST_DATABASE_URL=<your test db url> pytest tests/agent/test_agent_external_tool_binding.py -v`
Expected: all tests pass, including the 4 new ones and every pre-existing test in the file unchanged.

- [ ] **Step 7: Regenerate the OpenAPI manifest and verify the route-governance suite**

Run: `cd backend && python -m scripts.generate_openapi`
Then run: `pytest tests/agent/test_core_route_registration.py tests/agent/test_agent_policy.py tests/agent/test_role_unification.py -q`
Expected: all pass, including `test_openapi_manifest_is_deterministic` and `test_operation_map_covers_all_registered_routes` (the new routes inherit the `/api/v1/agents` platform-plane prefix automatically — no `policy.py` edit should be needed; if `test_operation_map_covers_all_registered_routes` fails, that means this assumption was wrong and `policy.py` genuinely needs a prefix/entry — investigate before adding one).

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/agent/catalog.py backend/app/services/agent/configuration.py \
  backend/app/routers/agents.py \
  backend/tests/agent/test_agent_external_tool_binding.py backend/openapi-agent.json
git commit -m "feat(agent): add external-tool catalog and current-bindings read endpoints"
```

---

### Task 2: Frontend — extract shared Ontology/Tool selection into a reusable hook + component

**Files:**
- Create: `frontend/src/pages/agents/shared/useOntologyToolSelection.ts`
- Create: `frontend/src/pages/agents/shared/OntologyToolSelector.tsx`
- Create: `frontend/src/pages/agents/shared/OntologyToolSelector.test.tsx`
- Modify: `frontend/src/pages/agents/detail/ToolConfigTab.tsx` (use the extracted hook/component instead of inline state/JSX)

**Interfaces:**
- Consumes: `agentToolsApi`, `TOOL_CATEGORIES`, `TOOL_CAPABILITY_GROUPS`, `OntologyBinding`, `PublishedOntology`, `ToolCategory`, `ToolDescriptor` from `@/api/agentTools` (all pre-existing, unchanged).
- Produces: `useOntologyToolSelection(ontologies: { id: string }[])` returning `{ bindings, setBindings, toolsByOntology, error, setError, bindOntology, unbindOntology, toggleCategory, toggleTool }`, and `<OntologyToolSelector ontologies bindings toolsByOntology canEdit onBind onUnbind onToggleCategory onToggleTool />`. Task 4 (wizard) imports both directly from `@/pages/agents/shared/useOntologyToolSelection` and `@/pages/agents/shared/OntologyToolSelector`.

- [ ] **Step 1: Capture the regression baseline**

Run: `cd frontend && npx vitest run src/pages/agents/detail/ToolConfigTab.test.tsx`
Expected: all tests currently pass (baseline pass count — write it down; Step 6 must match it exactly).

- [ ] **Step 2: Create the hook**

Write `frontend/src/pages/agents/shared/useOntologyToolSelection.ts`:

```ts
import { useCallback, useEffect, useState } from 'react'
import {
  agentToolsApi, TOOL_CATEGORIES,
  type OntologyBinding, type ToolCategory, type ToolDescriptor,
} from '@/api/agentTools'

export const BASE_CAPABILITIES = ['read_schema', 'read_instances', 'traverse_relations']

export function categoryOf(d: ToolDescriptor): ToolCategory {
  if (d.category) return d.category
  if (d.source_kind === 'builtin') return 'query'
  if (d.source_kind === 'logic') return 'logic'
  if (d.source_kind === 'action') return 'action'
  return 'mcp'
}

/** Categories in effect for a binding: explicit list when stored, otherwise
 * ALL (the legacy default keeps every category enabled). */
export function effectiveCategories(b: OntologyBinding): ToolCategory[] {
  return b.enabled_categories ?? TOOL_CATEGORIES
}

/** Capabilities accumulate across every selected tool's capability. */
export function deriveCapabilities(tools: ToolDescriptor[], selected: Set<string>): string[] {
  const extra = [...selected]
    .map(id => tools.find(d => d.descriptor_id === id)?.capability)
    .filter((c): c is string => Boolean(c))
  return [...new Set([...BASE_CAPABILITIES, ...extra])]
}

export function useOntologyToolSelection(ontologies: { id: string }[]) {
  const [bindings, setBindings] = useState<OntologyBinding[]>([])
  const [toolsByOntology, setToolsByOntology] = useState<Record<string, ToolDescriptor[]>>({})
  const [error, setError] = useState('')

  const loadTools = useCallback((ontologyId: string) => {
    agentToolsApi.listOntologyTools(ontologyId)
      .then(res => {
        const tools = Array.isArray(res.tools) ? res.tools : []
        setToolsByOntology(prev => ({ ...prev, [ontologyId]: tools }))
        setBindings(prev => prev.map(b => {
          if (b.ontology_id !== ontologyId || b.enabled_categories === undefined || b.enabled_categories === null) return b
          const selected = new Set(
            tools.filter(d => effectiveCategories(b).includes(categoryOf(d))).map(d => d.descriptor_id),
          )
          return { ...b, selected_tools: [...selected], capabilities: deriveCapabilities(tools, selected) }
        }))
      })
      .catch(() => setError('AGENTS_TOOLS_LOAD_FAILED'))
  }, [])

  // auto-load the tool list for every bound ontology so category state is visible
  useEffect(() => {
    bindings.forEach(b => {
      if (b.ontology_id && !toolsByOntology[b.ontology_id]) loadTools(b.ontology_id)
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bindings.map(b => b.ontology_id).join(',')])

  // an Agent binds at most one Ontology: picking one REPLACES any existing binding
  const bindOntology = useCallback((ontologyId: string) => {
    const ontology = ontologies.find(o => o.id === ontologyId)
    if (!ontology) return
    setBindings([{
      ontology_id: ontology.id,
      capabilities: [...BASE_CAPABILITIES],
      allowlists: {},
      selected_tools: [],
      enabled_categories: [...TOOL_CATEGORIES],
    }])
    loadTools(ontology.id)
  }, [ontologies, loadTools])

  const unbindOntology = useCallback((ontologyId: string) => {
    setBindings(prev => prev.filter(b => b.ontology_id !== ontologyId))
  }, [])

  const toggleCategory = useCallback((ontologyId: string, category: ToolCategory, on: boolean) => {
    setBindings(prev => prev.map(b => {
      if (b.ontology_id !== ontologyId) return b
      const cats = new Set(effectiveCategories(b))
      if (on) cats.add(category)
      else cats.delete(category)
      const tools = toolsByOntology[ontologyId] ?? []
      const selected = new Set(b.selected_tools)
      for (const d of tools) {
        if (categoryOf(d) === category) {
          if (on) selected.add(d.descriptor_id)
          else selected.delete(d.descriptor_id)
        }
      }
      return { ...b, enabled_categories: [...cats],
               selected_tools: [...selected], capabilities: deriveCapabilities(tools, selected) }
    }))
  }, [toolsByOntology])

  const toggleTool = useCallback((ontologyId: string, descriptorId: string, on: boolean) => {
    setBindings(prev => prev.map(b => {
      if (b.ontology_id !== ontologyId) return b
      const selected = new Set(b.selected_tools)
      if (on) selected.add(descriptorId)
      else selected.delete(descriptorId)
      const known = toolsByOntology[ontologyId] ?? []
      return { ...b, selected_tools: [...selected],
               capabilities: deriveCapabilities(known, selected) }
    }))
  }, [toolsByOntology])

  return {
    bindings, setBindings, toolsByOntology, setToolsByOntology, error, setError,
    bindOntology, unbindOntology, toggleCategory, toggleTool,
  }
}
```

This is a line-for-line move of the logic currently at `frontend/src/pages/agents/detail/ToolConfigTab.tsx:25-56,94-168` — no behavior change.

- [ ] **Step 3: Create the presentational component**

Write `frontend/src/pages/agents/shared/OntologyToolSelector.tsx`:

```tsx
import { useTranslation } from 'react-i18next'
import {
  TOOL_CATEGORIES, TOOL_CAPABILITY_GROUPS,
  type OntologyBinding, type PublishedOntology, type ToolCategory, type ToolDescriptor,
} from '@/api/agentTools'
import { categoryOf, effectiveCategories } from './useOntologyToolSelection'

const CATEGORY_LABELS: Record<ToolCategory, { label: string; fallback: string }> = {
  mcp: { label: 'agent.tools.category_mcp', fallback: 'MCP 外部工具' },
  query: { label: 'agent.tools.category_query', fallback: '查询' },
  write: { label: 'agent.tools.category_write', fallback: '写入' },
  logic: { label: 'agent.tools.category_logic', fallback: 'Logic 规则' },
  action: { label: 'agent.tools.category_action', fallback: '实例 Action' },
}

interface Props {
  ontologies: PublishedOntology[]
  bindings: OntologyBinding[]
  toolsByOntology: Record<string, ToolDescriptor[]>
  canEdit: boolean
  onBind: (ontologyId: string) => void
  onUnbind: (ontologyId: string) => void
  onToggleCategory: (ontologyId: string, category: ToolCategory, on: boolean) => void
  onToggleTool: (ontologyId: string, descriptorId: string, on: boolean) => void
}

export default function OntologyToolSelector({
  ontologies, bindings, toolsByOntology, canEdit, onBind, onUnbind, onToggleCategory, onToggleTool,
}: Props) {
  const { t } = useTranslation()
  // one Agent binds at most one Ontology: once bound, the picker is disabled —
  // unbind first to switch to a different published ontology
  const pickable = bindings.length > 0 ? [] : ontologies

  return (
    <div>
      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <select
            data-testid="ontology-picker"
            disabled={!canEdit || bindings.length > 0 || pickable.length === 0}
            value=""
            onChange={e => { if (e.target.value) onBind(e.target.value) }}
            className="border rounded-lg px-3 py-2 text-sm bg-white"
          >
            <option value="">{t('agent.tools.select_ontology', '选择要绑定的已发布本体…')}</option>
            {pickable.map(o => (
              <option key={o.id} value={o.id}>{o.name} ({o.id})</option>
            ))}
          </select>
          <span className="text-xs text-gray-400">{t('agent.tools.picker_note', '每个 Agent 仅能绑定一个已发布本体；绑定后默认启用全部工具类别，如需更换请先解绑')}</span>
        </div>
        {ontologies.length === 0 && (
          <p className="text-sm text-gray-400">{t('agent.tools.no_ontologies', '没有可绑定的已发布本体')}</p>
        )}
      </div>

      <div className="space-y-4 mt-4" data-testid="bound-ontology-panels">
        {bindings.map(binding => {
          const ontology = ontologies.find(o => o.id === binding.ontology_id)
          const cats = effectiveCategories(binding)
          const tools = toolsByOntology[binding.ontology_id] ?? []
          return (
            <div key={binding.ontology_id} className="border rounded-lg p-4" data-testid={`ontology-tools-${binding.ontology_id}`}>
              <div className="flex items-center justify-between mb-2">
                <div>
                  <p className="text-sm font-medium">{ontology?.name ?? binding.ontology_id}</p>
                  <p className="text-xs text-gray-500 font-mono mt-0.5">{binding.ontology_id}</p>
                </div>
                <button type="button" disabled={!canEdit} onClick={() => onUnbind(binding.ontology_id)}
                  className="px-3 py-1.5 text-xs rounded-lg border hover:bg-gray-50 disabled:opacity-40">
                  {t('agent.tools.unbind', '解绑')}
                </button>
              </div>
              <p className="text-xs text-gray-500 mb-1 mt-2">{t('agent.tools.categories', '工具类别')}</p>
              <div className="flex flex-wrap gap-3 mb-2" data-testid={`category-toggles-${binding.ontology_id}`}>
                {TOOL_CATEGORIES.map(cat => (
                  <label key={cat} className="flex items-center gap-1.5 text-xs">
                    <input type="checkbox" data-testid={`category-${binding.ontology_id}-${cat}`} disabled={!canEdit}
                      checked={cats.includes(cat)}
                      onChange={e => onToggleCategory(binding.ontology_id, cat, e.target.checked)}
                      className="mt-0.5" />
                    {t(CATEGORY_LABELS[cat].label, CATEGORY_LABELS[cat].fallback)}
                  </label>
                ))}
              </div>
              <p className="text-xs text-gray-400 mb-2">{t('agent.tools.category_tools_note', '勾选的类别默认全部启用')}</p>
              {tools.map(d => {
                const dCat = categoryOf(d)
                const catOn = cats.includes(dCat)
                return (
                  <label key={d.descriptor_id} className="flex items-start gap-2 py-1.5 text-sm">
                    <input type="checkbox" disabled={!canEdit || !catOn}
                      checked={catOn && binding.selected_tools.includes(d.descriptor_id)}
                      onChange={e => onToggleTool(binding.ontology_id, d.descriptor_id, e.target.checked)}
                      className="mt-1" />
                    <span>
                      <span className="font-medium">
                        {t(TOOL_CAPABILITY_GROUPS[dCat]?.label ?? 'agent.tools.tool_other',
                           TOOL_CAPABILITY_GROUPS[dCat]?.fallback ?? d.source_kind)}
                        {d.source_kind !== 'builtin' && ` · ${d.source_id.slice(0, 8)}`}
                      </span>
                      <span className="text-xs text-gray-400 ml-2 font-mono">{d.capability}</span>
                    </span>
                  </label>
                )
              })}
              {tools.length === 0 && (
                <p className="text-xs text-gray-400">{t('agent.tools.no_tools', '该本体暂无可用工具（需先发布）')}</p>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
```

This is a line-for-line move of the JSX currently at `ToolConfigTab.tsx:227-251,254-310` (the `<h3>本体绑定</h3>` heading stays in `ToolConfigTab.tsx` itself since it wraps the whole section, not just the picker — see Step 5).

- [ ] **Step 4: Write a focused test for the extracted component**

Write `frontend/src/pages/agents/shared/OntologyToolSelector.test.tsx`:

```tsx
import '@/i18n'
import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import OntologyToolSelector from './OntologyToolSelector'
import type { OntologyBinding, PublishedOntology, ToolDescriptor } from '@/api/agentTools'

const ONTOLOGIES: PublishedOntology[] = [{ id: 'o-1', name: 'Supply Ontology', status: 'published' }]
const TOOLS: ToolDescriptor[] = [
  { descriptor_id: 'logic:rule-1', version: 1, source_kind: 'logic', source_id: 'rule-1',
    capability: 'execute_read_logic', timeout_ms: 10000, result_limit: 1, descriptor_hash: 'h' + '0'.repeat(63) },
]

describe('OntologyToolSelector', () => {
  it('lists pickable ontologies and calls onBind when one is chosen', async () => {
    const onBind = vi.fn()
    render(<OntologyToolSelector ontologies={ONTOLOGIES} bindings={[]} toolsByOntology={{}} canEdit
      onBind={onBind} onUnbind={vi.fn()} onToggleCategory={vi.fn()} onToggleTool={vi.fn()} />)
    await userEvent.selectOptions(screen.getByTestId('ontology-picker'), 'o-1')
    expect(onBind).toHaveBeenCalledWith('o-1')
  })

  it('renders a bound ontology panel and toggles a category', async () => {
    const onToggleCategory = vi.fn()
    const binding: OntologyBinding = {
      ontology_id: 'o-1', capabilities: [], allowlists: {}, selected_tools: [], enabled_categories: ['logic'],
    }
    render(<OntologyToolSelector ontologies={ONTOLOGIES} bindings={[binding]}
      toolsByOntology={{ 'o-1': TOOLS }} canEdit
      onBind={vi.fn()} onUnbind={vi.fn()} onToggleCategory={onToggleCategory} onToggleTool={vi.fn()} />)
    expect(screen.getByTestId('ontology-tools-o-1')).toBeTruthy()
    await userEvent.click(screen.getByTestId('category-o-1-write'))
    expect(onToggleCategory).toHaveBeenCalledWith('o-1', 'write', true)
  })

  it('disables the picker and unbind button when canEdit is false', () => {
    const binding: OntologyBinding = {
      ontology_id: 'o-1', capabilities: [], allowlists: {}, selected_tools: [], enabled_categories: null,
    }
    render(<OntologyToolSelector ontologies={ONTOLOGIES} bindings={[binding]} toolsByOntology={{}} canEdit={false}
      onBind={vi.fn()} onUnbind={vi.fn()} onToggleCategory={vi.fn()} onToggleTool={vi.fn()} />)
    expect((screen.getByTestId('ontology-picker') as HTMLSelectElement).disabled).toBe(true)
    expect((screen.getByText('解绑') as HTMLButtonElement).disabled).toBe(true)
  })
})
```

Run: `cd frontend && npx vitest run src/pages/agents/shared/OntologyToolSelector.test.tsx`
Expected: all 3 pass.

- [ ] **Step 5: Wire `ToolConfigTab.tsx` to the extracted hook + component**

In `frontend/src/pages/agents/detail/ToolConfigTab.tsx`:
- Remove the local `BASE_CAPABILITIES`, `CATEGORY_LABELS`, `categoryOf`, `effectiveCategories`, `deriveCapabilities` definitions (now imported).
- Remove the `bindings`/`toolsByOntology` `useState` calls and the `loadTools`/`bindOntology`/`unbindOntology`/`toggleCategory`/`toggleTool` `useCallback`s and the auto-load `useEffect` (all now come from the hook).
- Add: `import { useOntologyToolSelection } from '@/pages/agents/shared/useOntologyToolSelection'` and `import OntologyToolSelector from '@/pages/agents/shared/OntologyToolSelector'`.
- Add near the top of the component body: `const { bindings, setBindings, toolsByOntology, error, setError, bindOntology, unbindOntology, toggleCategory, toggleTool } = useOntologyToolSelection(ontologies)`.
- Keep every other piece of state (`ontologies`, `validation`, `drawerOpen`, `saving`) and every other effect (fetching ontologies, seeding `bindings` from `activeVersion.ontology_bindings` via `setBindings`, the tool-validation effect, `persistedBindings`/`dirty`, `save`) exactly as-is — they now just reference `bindings` from the hook instead of local state.
- Replace the JSX block that currently renders the ontology-picker `<select>` and the `bound-ontology-panels` div (the content between the `<h3>本体绑定</h3>` heading and the `{validation && (...)}` block) with:
  ```tsx
  <OntologyToolSelector ontologies={ontologies} bindings={bindings} toolsByOntology={toolsByOntology}
    canEdit={canEdit} onBind={bindOntology} onUnbind={unbindOntology}
    onToggleCategory={toggleCategory} onToggleTool={toggleTool} />
  ```
  Keep the `<h3>` heading and the `{error && ...}` line above it exactly where they are (they wrap the whole ontology-bindings section, not just the extracted piece).
- Leave the `ExternalToolCard` rendering, validation panel, save button, and everything else in the file untouched (Task 3 touches those).

- [ ] **Step 6: Re-run the regression baseline**

Run: `cd frontend && npx vitest run src/pages/agents/detail/ToolConfigTab.test.tsx`
Expected: same pass count as Step 1 — zero behavior change. If any test fails, the extraction introduced a behavior difference; fix `OntologyToolSelector.tsx`/`useOntologyToolSelection.ts` (not the test) until it matches.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/pages/agents/shared/useOntologyToolSelection.ts \
  frontend/src/pages/agents/shared/OntologyToolSelector.tsx \
  frontend/src/pages/agents/shared/OntologyToolSelector.test.tsx \
  frontend/src/pages/agents/detail/ToolConfigTab.tsx
git commit -m "refactor(agent-ui): extract ontology/tool selection into a shared hook and component"
```

---

### Task 3: Frontend — external-tool API client + working `ExternalToolCard` + wire into `ToolConfigTab`

**Files:**
- Create: `frontend/src/api/agentExternalTools.ts`
- Modify: `frontend/src/pages/agents/detail/ExternalToolCard.tsx` (full rewrite)
- Modify: `frontend/src/pages/agents/detail/ToolConfigTab.tsx` (add external-binding state, pass real props to `ExternalToolCard`)
- Modify: `frontend/src/pages/agents/detail/ToolConfigTab.test.tsx` (register the two new endpoints as default handlers; replace the now-obsolete "issues zero P7 requests" test)

**Interfaces:**
- Consumes: Task 1's `GET /agents/catalog/external-tools` and `GET /agents/{agent_id}/versions/{version_id}/external-tools`, plus the pre-existing `POST/DELETE /agents/{agent_id}/versions/{version_id}/external-tools[/{alias}]` (`backend/app/routers/agents.py:452-479`, already implemented, never had a frontend caller).
- Produces: `agentExternalToolsApi` (`listCatalog`, `listBindings`, `bind`, `unbind`), the `ExternalToolCatalogItem`/`ExternalToolBinding` types, and a rewritten `<ExternalToolCard bindings canEdit onBind onUnbind bindError? />` whose `bindings` prop shape (`{ alias, tool_connection_version_id, provider_name, provider_kind }[]`) Task 4's wizard reuses verbatim for its local pending-selection state.

- [ ] **Step 1: Write the API client**

Create `frontend/src/api/agentExternalTools.ts`:

```ts
import { apiClient } from './client'
import { newAgentIdempotencyKey } from './agentDetail'

export interface ExternalToolCatalogItem {
  tool_connection_version_id: string
  connection_id: string
  version_no: number
  provider_id: string
  provider_name: string
  provider_kind: 'search' | 'playwright' | 'external_mcp'
  health_status: 'healthy' | 'unhealthy' | 'unknown'
}

export interface ExternalToolBinding {
  id: string
  alias: string
  tool_connection_version_id: string
  connection_id: string
  version_no: number
  provider_name: string
  provider_kind: string
  approval_status: string
  health_status: string
}

export interface BindExternalToolPayload {
  tool_connection_version_id: string
  alias: string
}

export const agentExternalToolsApi = {
  listCatalog: () => apiClient.get<{ items: ExternalToolCatalogItem[] }>('/agents/catalog/external-tools'),
  listBindings: (agentId: string, versionId: string) =>
    apiClient.get<{ items: ExternalToolBinding[] }>(`/agents/${agentId}/versions/${versionId}/external-tools`),
  bind: (agentId: string, versionId: string, body: BindExternalToolPayload) =>
    apiClient.post<{ id: string; alias: string; tool_connection_version_id: string }>(
      `/agents/${agentId}/versions/${versionId}/external-tools`, body,
      { headers: { 'Idempotency-Key': newAgentIdempotencyKey() } },
    ),
  unbind: (agentId: string, versionId: string, alias: string) =>
    apiClient.delete<{ released: boolean }>(
      `/agents/${agentId}/versions/${versionId}/external-tools/${encodeURIComponent(alias)}`,
    ),
}
```

- [ ] **Step 2: Rewrite `ExternalToolCard.tsx`**

Replace the entire contents of `frontend/src/pages/agents/detail/ExternalToolCard.tsx`:

```tsx
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { agentExternalToolsApi, type ExternalToolCatalogItem } from '@/api/agentExternalTools'

export interface BoundExternalTool {
  alias: string
  tool_connection_version_id: string
  provider_name: string
  provider_kind: string
}

interface Props {
  bindings: BoundExternalTool[]
  canEdit: boolean
  onBind: (item: ExternalToolCatalogItem, alias: string) => void | Promise<void>
  onUnbind: (alias: string) => void | Promise<void>
  bindError?: string
}

const KIND_LABELS: Record<string, { label: string; fallback: string }> = {
  search: { label: 'agent.tools.name_search', fallback: 'Search' },
  playwright: { label: 'agent.tools.name_playwright', fallback: 'Playwright' },
  external_mcp: { label: 'agent.tools.name_mcp_connections', fallback: 'MCP Connections' },
}

const LIVE_KINDS = ['search', 'playwright', 'external_mcp'] as const

/** Default alias from a provider name, de-duplicated against currently
 * bound/pending aliases; still editable by the user before binding. */
export function slugifyAlias(providerName: string, existing: string[]): string {
  const base = providerName.toLowerCase().replace(/[^a-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 50) || 'tool'
  let candidate = base
  let n = 2
  while (existing.includes(candidate)) {
    candidate = `${base}-${n}`.slice(0, 55)
    n += 1
  }
  return candidate
}

export default function ExternalToolCard({ bindings, canEdit, onBind, onUnbind, bindError }: Props) {
  const { t } = useTranslation()
  const [catalog, setCatalog] = useState<ExternalToolCatalogItem[]>([])
  const [error, setError] = useState('')
  const [aliasDrafts, setAliasDrafts] = useState<Record<string, string>>({})

  useEffect(() => {
    let cancelled = false
    agentExternalToolsApi.listCatalog()
      .then(res => { if (!cancelled) setCatalog(Array.isArray(res.items) ? res.items : []) })
      .catch(() => { if (!cancelled) setError('AGENTS_EXTERNAL_TOOLS_CATALOG_FAILED') })
    return () => { cancelled = true }
  }, [])

  const boundVersionIds = new Set(bindings.map(b => b.tool_connection_version_id))
  const aliases = bindings.map(b => b.alias)

  const draftFor = (item: ExternalToolCatalogItem) =>
    aliasDrafts[item.tool_connection_version_id] ?? slugifyAlias(item.provider_name, aliases)

  return (
    <div data-testid="external-tool-cards" className="space-y-4">
      {error && <p className="text-sm text-red-500">{error}</p>}
      {bindError && <p className="text-sm text-red-500">{bindError}</p>}
      {LIVE_KINDS.map(kind => {
        const kindCatalog = catalog.filter(i => i.provider_kind === kind)
        const kindBindings = bindings.filter(b => b.provider_kind === kind)
        return (
          <div key={kind} className="border rounded-lg p-4" data-testid={`external-kind-${kind}`}>
            <p className="text-sm font-medium mb-2">{t(KIND_LABELS[kind].label, KIND_LABELS[kind].fallback)}</p>
            {kindBindings.map(b => (
              <div key={b.alias} className="flex items-center justify-between py-1.5 text-sm">
                <span>{b.provider_name} · <span className="font-mono text-xs">{b.alias}</span></span>
                <button type="button" disabled={!canEdit} onClick={() => onUnbind(b.alias)}
                  className="px-2 py-1 text-xs rounded border hover:bg-gray-50 disabled:opacity-40">
                  {t('agent.tools.unbind', '解绑')}
                </button>
              </div>
            ))}
            {kindCatalog.filter(i => !boundVersionIds.has(i.tool_connection_version_id)).map(item => (
              <div key={item.tool_connection_version_id} className="flex items-center gap-2 py-1.5 text-sm">
                <span className="flex-1">{item.provider_name}</span>
                <input
                  data-testid={`alias-input-${item.tool_connection_version_id}`}
                  disabled={!canEdit}
                  value={draftFor(item)}
                  onChange={e => setAliasDrafts(prev => ({ ...prev, [item.tool_connection_version_id]: e.target.value }))}
                  className="border rounded px-2 py-1 text-xs font-mono w-32"
                />
                <button type="button" disabled={!canEdit}
                  data-testid={`bind-${item.tool_connection_version_id}`}
                  onClick={() => onBind(item, draftFor(item))}
                  className="px-2 py-1 text-xs rounded border hover:bg-gray-50 disabled:opacity-40">
                  {t('agent.tools.bind', '绑定')}
                </button>
              </div>
            ))}
            {kindCatalog.length === 0 && (
              <p className="text-xs text-gray-400">{t('agent.tools.no_external_connections', '暂无可绑定的已激活连接，请先在工具连接管理中配置并激活')}</p>
            )}
          </div>
        )
      })}
      <div className="border rounded-lg p-4 bg-gray-50 opacity-70" data-testid="external-tool-card">
        <div className="flex items-center justify-between">
          <p className="text-sm font-medium">{t('agent.tools.name_signed_skills', 'Signed Skills')}</p>
          <span className="px-2 py-0.5 rounded text-xs bg-gray-200 text-gray-600">
            {t('agent.tools.available_later', 'Available later')}
          </span>
        </div>
        <p className="text-xs text-gray-500 mt-1">{t('agent.tools.external_later', '外部工具将在后续版本提供')}</p>
      </div>
    </div>
  )
}
```

Note: the raw error code is rendered directly with no `t()` wrapper, matching `ToolConfigTab.tsx`'s own existing convention for its `error` state (`{error && <p ...>{error}</p>}` at `ToolConfigTab.tsx:229`, never translated) — stay consistent with that rather than inventing a new i18n key.

- [ ] **Step 3: Wire real bind/unbind state into `ToolConfigTab.tsx`**

In `frontend/src/pages/agents/detail/ToolConfigTab.tsx`, add the import `import { agentExternalToolsApi, type ExternalToolCatalogItem } from '@/api/agentExternalTools'`. Add this state and these callbacks alongside the existing ones:

```tsx
const [externalBindings, setExternalBindings] = useState<BoundExternalTool[]>([])
const [externalError, setExternalError] = useState('')

const loadExternalBindings = useCallback(() => {
  if (!activeVersion) return
  agentExternalToolsApi.listBindings(agentId, activeVersion.id)
    .then(res => setExternalBindings(Array.isArray(res.items) ? res.items : []))
    .catch(() => setExternalError('AGENTS_EXTERNAL_BINDINGS_LOAD_FAILED'))
}, [agentId, activeVersion])

useEffect(() => { loadExternalBindings() }, [loadExternalBindings])

const bindExternal = useCallback(async (item: ExternalToolCatalogItem, alias: string) => {
  if (!activeVersion) return
  setExternalError('')
  try {
    await agentExternalToolsApi.bind(agentId, activeVersion.id,
      { tool_connection_version_id: item.tool_connection_version_id, alias })
    loadExternalBindings()
  } catch {
    setExternalError(t('agent.tools.bind_failed', '绑定失败（别名可能已被占用，或所选连接已失效）'))
  }
}, [agentId, activeVersion, loadExternalBindings, t])

const unbindExternal = useCallback(async (alias: string) => {
  if (!activeVersion) return
  setExternalError('')
  try {
    await agentExternalToolsApi.unbind(agentId, activeVersion.id, alias)
    loadExternalBindings()
  } catch {
    setExternalError(t('agent.tools.unbind_failed', '解绑失败'))
  }
}, [agentId, activeVersion, loadExternalBindings, t])
```

Add `import type { BoundExternalTool } from './ExternalToolCard'` and change the existing `<ExternalToolCard />` render (currently `ToolConfigTab.tsx:337`, no props) to:

```tsx
<ExternalToolCard bindings={externalBindings} canEdit={canEdit}
  onBind={bindExternal} onUnbind={unbindExternal} bindError={externalError} />
```

- [ ] **Step 4: Update `ToolConfigTab.test.tsx`**

`ToolConfigTab` now issues two more requests on *every* mount, unconditionally (the catalog fetch inside `ExternalToolCard` and the new `loadExternalBindings` effect) — but `defaultHandlers()` is not called by every test (six of the file's tests call it via `toolsHandlers()`; the `'shows external tool cards...'` test at line 249 calls neither and relies on `onUnhandledRequest: 'error'` to prove no extra requests happen). Adding the two new handlers only inside `defaultHandlers()` would leave every test that skips it failing on an unhandled request. Register them globally instead, in a `beforeEach` added right after the existing `afterEach(() => server.resetHandlers())` (so it re-registers fresh before every single test, independent of which per-test helper functions run):

```ts
beforeEach(() => {
  server.use(
    http.get('*/api/v1/agents/catalog/external-tools', () =>
      HttpResponse.json({ data: { items: [] }, message: 'ok' })),
    http.get('*/api/v1/agents/a-1/versions/v-1/external-tools', () =>
      HttpResponse.json({ data: { items: [] }, message: 'ok' })),
  )
})
```

(Add `beforeEach` to the existing `import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'` line.) Read the whole file first to check whether any test renders `ToolConfigTab` with an `activeVersion` whose `id` is NOT `'v-1'` (e.g. the `versionWithBinding` variant around line 238) — if one exists, add a matching `GET .../versions/<that id>/external-tools` handler for it too, either in this same `beforeEach` or via that test's own `server.use()` override (empty items is fine as the default; only add a populated response if a specific test's assertions need it). Individual tests that need populated catalog/bindings data override these two defaults with their own `server.use()` call, same as every other per-test override in this file.

Replace the test `'shows external tool cards as unavailable and issues zero P7 requests'` (currently lines 249-260) — its premise ("zero P7 requests, everything unavailable") is exactly what this task changes — with:

```tsx
  it('lists a bindable catalog entry and binds it', async () => {
    server.use(
      http.get('*/api/v1/agents/catalog/ontologies', () =>
        HttpResponse.json({ data: { items: [], next_cursor: null, has_more: false }, message: 'ok' })),
      http.get('*/api/v1/agents/catalog/external-tools', () =>
        HttpResponse.json({ data: { items: [
          { tool_connection_version_id: 'tcv-1', connection_id: 'c-1', version_no: 1,
            provider_id: 'p-1', provider_name: 'Web Search', provider_kind: 'search', health_status: 'healthy' },
        ] }, message: 'ok' })),
    )
    let bindBody: Record<string, unknown> | null = null
    server.use(
      http.post('*/api/v1/agents/a-1/versions/v-1/external-tools', async ({ request }) => {
        bindBody = await request.json() as Record<string, unknown>
        return HttpResponse.json({ data: { id: 'aetb-1', alias: bindBody.alias, tool_connection_version_id: 'tcv-1' }, message: 'ok' }, { status: 201 })
      }),
    )
    renderTab()
    await waitFor(() => expect(screen.getByTestId('bind-tcv-1')).toBeTruthy())
    await userEvent.click(screen.getByTestId('bind-tcv-1'))
    await waitFor(() => expect(bindBody).not.toBeNull())
    expect(bindBody).toMatchObject({ tool_connection_version_id: 'tcv-1' })
  })

  it('unbinds a currently bound external tool', async () => {
    server.use(
      http.get('*/api/v1/agents/catalog/ontologies', () =>
        HttpResponse.json({ data: { items: [], next_cursor: null, has_more: false }, message: 'ok' })),
      http.get('*/api/v1/agents/a-1/versions/v-1/external-tools', () =>
        HttpResponse.json({ data: { items: [
          { id: 'aetb-1', alias: 'search', tool_connection_version_id: 'tcv-1', connection_id: 'c-1',
            version_no: 1, provider_name: 'Web Search', provider_kind: 'search',
            approval_status: 'approved', health_status: 'healthy' },
        ] }, message: 'ok' })),
    )
    let unbound = false
    server.use(
      http.delete('*/api/v1/agents/a-1/versions/v-1/external-tools/search', () => {
        unbound = true
        return HttpResponse.json({ data: { released: true }, message: 'ok' })
      }),
    )
    renderTab()
    await screen.findByText('search', { exact: false })
    await userEvent.click(screen.getByText('解绑'))
    await waitFor(() => expect(unbound).toBe(true))
  })

  it('keeps the Signed Skills card as a static, unavailable placeholder', async () => {
    server.use(
      http.get('*/api/v1/agents/catalog/ontologies', () =>
        HttpResponse.json({ data: { items: [], next_cursor: null, has_more: false }, message: 'ok' })),
    )
    renderTab()
    await waitFor(() => expect(screen.getByTestId('external-tool-cards')).toBeTruthy())
    expect(screen.getAllByTestId('external-tool-card').length).toBe(1)
    expect(screen.getByText('后续提供')).toBeTruthy()
  })
```

Run: `cd frontend && npx vitest run src/pages/agents/detail/ToolConfigTab.test.tsx`
Expected: all pass, including every pre-existing test.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/agentExternalTools.ts frontend/src/pages/agents/detail/ExternalToolCard.tsx \
  frontend/src/pages/agents/detail/ToolConfigTab.tsx frontend/src/pages/agents/detail/ToolConfigTab.test.tsx
git commit -m "feat(agent-ui): wire ExternalToolCard to real bind/unbind for Search/Playwright/External MCP"
```

---

### Task 4: Frontend — ontology + external-tool selection in `AgentCreateWizard`

**Files:**
- Modify: `frontend/src/pages/agents/new/AgentCreateWizard.tsx`
- Modify: `frontend/src/api/agentDetail.ts` (extend `create()`'s body type with `ontology_bindings`)
- Modify: `frontend/src/pages/agents/new/AgentCreateWizard.test.tsx`

**Interfaces:**
- Consumes: `useOntologyToolSelection`/`OntologyToolSelector` (Task 2), `ExternalToolCard`/`BoundExternalTool`/`agentExternalToolsApi` (Task 3), the pre-existing `AgentCreateRequest.ontology_bindings` field (`backend/app/schemas/agents.py:28`, already accepted by `create_agent`/`POST /agents` but never sent by the frontend before this task).
- Produces: no new exported interfaces — this is the final consumer task.

- [ ] **Step 1: Extend the create-agent request type**

In `frontend/src/api/agentDetail.ts`, change the `create` method's body parameter type (around line 95-103) to add `ontology_bindings?: OntologyBinding[]`:

```ts
  create: (body: {
    name: string
    description?: string | null
    default_model_config_version_id: string
    default_model_name: string
    system_prompt?: string | null
    memory_settings?: Record<string, unknown>
    ontology_bindings?: OntologyBinding[]
  }) =>
    apiClient.post<CreateAgentResult>('/agents', body, { headers: { 'Idempotency-Key': newAgentIdempotencyKey() } }),
```

(`OntologyBinding` is already imported at the top of this file — no new import needed.)

- [ ] **Step 2: Write the failing wizard tests**

In `frontend/src/pages/agents/new/AgentCreateWizard.test.tsx`, the two existing tests use `screen.getByRole('combobox')` to mean "the model select" — once this task adds an ontology-picker `<select>` too, that query becomes ambiguous. First fix both existing tests to target the model select unambiguously: replace `(screen.getByRole('combobox') as HTMLSelectElement)` at line 47 and line 69 with `(screen.getByLabelText('模型') as HTMLSelectElement)`.

The wizard now issues two more unconditional requests on every render (the wizard's own new `listPublishedOntologies` effect, added in Step 3, and the catalog fetch inside `ExternalToolCard`) — every test in this file, not just the new ones, needs a handler for both or it fails under `onUnhandledRequest: 'error'` (set at line 11). This file has no existing shared-handler helper (unlike `ToolConfigTab.test.tsx`), so add a `beforeEach` right after the existing `afterEach(() => server.resetHandlers())` that registers safe empty defaults for every test, overridden per-test via `server.use()` exactly like `catalog/models` already is:

```ts
beforeEach(() => {
  server.use(
    http.get('*/api/v1/agents/catalog/ontologies', () =>
      HttpResponse.json({ data: { items: [], next_cursor: null, has_more: false }, message: 'ok' })),
    http.get('*/api/v1/agents/catalog/external-tools', () =>
      HttpResponse.json({ data: { items: [] }, message: 'ok' })),
  )
})
```

(Add `beforeEach` to the existing `import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'` line.)

Then add these three new tests:

```tsx
  it('binds a chosen ontology inline on create', async () => {
    let createdBody: Record<string, unknown> | null = null
    server.use(
      http.get('*/api/v1/agents/catalog/models', () =>
        HttpResponse.json({ data: { items: [{ id: 'm-1', name: 'gpt-4o', version_no: 1 }], next_cursor: null, has_more: false }, message: 'ok' })),
      http.get('*/api/v1/agents/catalog/ontologies', () =>
        HttpResponse.json({ data: { items: [{ id: 'o-1', name: 'Supply Ontology', status: 'published' }], next_cursor: null, has_more: false }, message: 'ok' })),
      http.get('*/api/v1/agents/catalog/external-tools', () =>
        HttpResponse.json({ data: { items: [] }, message: 'ok' })),
      http.get('*/api/v1/ontologies/o-1/tools', () =>
        HttpResponse.json({ data: { ontology_id: 'o-1', published: true, release_id: 'r-1', tools: [] }, message: 'ok' })),
      http.post('*/api/v1/agents', async ({ request }) => {
        createdBody = await request.json() as Record<string, unknown>
        return HttpResponse.json({ data: { agent_id: 'a-new', version_id: 'v-1', version_no: 1, config_hash: 'c' + '0'.repeat(63) }, message: 'ok' }, { status: 201 })
      }),
    )
    await renderWizard()
    await waitFor(() => expect((screen.getByLabelText('模型') as HTMLSelectElement).options.length).toBe(2))
    await userEvent.type(screen.getByLabelText('名称'), 'New Agent')
    await userEvent.selectOptions(screen.getByLabelText('模型'), 'm-1')
    await userEvent.selectOptions(screen.getByTestId('ontology-picker'), 'o-1')
    await userEvent.click(screen.getByRole('button', { name: '创建' }))
    await waitFor(() => expect(createdBody).not.toBeNull())
    expect(createdBody!.ontology_bindings).toMatchObject([{ ontology_id: 'o-1' }])
  })

  it('binds pending external tools after create and navigates on full success', async () => {
    let bindCalls: string[] = []
    server.use(
      http.get('*/api/v1/agents/catalog/models', () =>
        HttpResponse.json({ data: { items: [{ id: 'm-1', name: 'gpt-4o', version_no: 1 }], next_cursor: null, has_more: false }, message: 'ok' })),
      http.get('*/api/v1/agents/catalog/ontologies', () =>
        HttpResponse.json({ data: { items: [], next_cursor: null, has_more: false }, message: 'ok' })),
      http.get('*/api/v1/agents/catalog/external-tools', () =>
        HttpResponse.json({ data: { items: [
          { tool_connection_version_id: 'tcv-1', connection_id: 'c-1', version_no: 1,
            provider_id: 'p-1', provider_name: 'Web Search', provider_kind: 'search', health_status: 'healthy' },
        ] }, message: 'ok' })),
      http.post('*/api/v1/agents', () =>
        HttpResponse.json({ data: { agent_id: 'a-new', version_id: 'v-1', version_no: 1, config_hash: 'c' + '0'.repeat(63) }, message: 'ok' }, { status: 201 })),
      http.post('*/api/v1/agents/a-new/versions/v-1/external-tools', async ({ request }) => {
        const body = await request.json() as { alias: string }
        bindCalls.push(body.alias)
        return HttpResponse.json({ data: { id: 'aetb-1', alias: body.alias, tool_connection_version_id: 'tcv-1' }, message: 'ok' }, { status: 201 })
      }),
    )
    await renderWizard()
    await waitFor(() => expect((screen.getByLabelText('模型') as HTMLSelectElement).options.length).toBe(2))
    await userEvent.type(screen.getByLabelText('名称'), 'New Agent')
    await userEvent.selectOptions(screen.getByLabelText('模型'), 'm-1')
    await waitFor(() => expect(screen.getByTestId('bind-tcv-1')).toBeTruthy())
    await userEvent.click(screen.getByTestId('bind-tcv-1'))
    await userEvent.click(screen.getByRole('button', { name: '创建' }))
    await waitFor(() => expect(bindCalls.length).toBe(1))
    expect(screen.getByText('DETAIL:a-new')).toBeTruthy()
  })

  it('shows a recovery banner instead of auto-navigating when an external-tool bind fails', async () => {
    server.use(
      http.get('*/api/v1/agents/catalog/models', () =>
        HttpResponse.json({ data: { items: [{ id: 'm-1', name: 'gpt-4o', version_no: 1 }], next_cursor: null, has_more: false }, message: 'ok' })),
      http.get('*/api/v1/agents/catalog/ontologies', () =>
        HttpResponse.json({ data: { items: [], next_cursor: null, has_more: false }, message: 'ok' })),
      http.get('*/api/v1/agents/catalog/external-tools', () =>
        HttpResponse.json({ data: { items: [
          { tool_connection_version_id: 'tcv-1', connection_id: 'c-1', version_no: 1,
            provider_id: 'p-1', provider_name: 'Web Search', provider_kind: 'search', health_status: 'healthy' },
        ] }, message: 'ok' })),
      http.post('*/api/v1/agents', () =>
        HttpResponse.json({ data: { agent_id: 'a-new', version_id: 'v-1', version_no: 1, config_hash: 'c' + '0'.repeat(63) }, message: 'ok' }, { status: 201 })),
      http.post('*/api/v1/agents/a-new/versions/v-1/external-tools', () =>
        HttpResponse.json({ error: { code: 'EXTERNAL_TOOL_VERSION_NOT_APPROVED' } }, { status: 422 })),
    )
    await renderWizard()
    await waitFor(() => expect((screen.getByLabelText('模型') as HTMLSelectElement).options.length).toBe(2))
    await userEvent.type(screen.getByLabelText('名称'), 'New Agent')
    await userEvent.selectOptions(screen.getByLabelText('模型'), 'm-1')
    await waitFor(() => expect(screen.getByTestId('bind-tcv-1')).toBeTruthy())
    await userEvent.click(screen.getByTestId('bind-tcv-1'))
    await userEvent.click(screen.getByRole('button', { name: '创建' }))
    await waitFor(() => expect(screen.getByText('前往详情页处理')).toBeTruthy())
    expect(screen.queryByText('DETAIL:a-new')).toBeNull()
    await userEvent.click(screen.getByText('前往详情页处理'))
    expect(screen.getByText('DETAIL:a-new')).toBeTruthy()
  })
```

Run: `cd frontend && npx vitest run src/pages/agents/new/AgentCreateWizard.test.tsx`
Expected: FAIL — `AgentCreateWizard.tsx` doesn't render `ontology-picker`/`bind-tcv-1`/the recovery banner yet.

- [ ] **Step 3: Implement the wizard changes**

Rewrite `frontend/src/pages/agents/new/AgentCreateWizard.tsx`:

```tsx
import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { agentDetailApi, type CatalogModel } from '@/api/agentDetail'
import { agentExternalToolsApi, type ExternalToolCatalogItem } from '@/api/agentExternalTools'
import { agentToolsApi, type PublishedOntology } from '@/api/agentTools'
import { useOntologyToolSelection } from '@/pages/agents/shared/useOntologyToolSelection'
import OntologyToolSelector from '@/pages/agents/shared/OntologyToolSelector'
import ExternalToolCard, { type BoundExternalTool } from '@/pages/agents/detail/ExternalToolCard'

export default function AgentCreateWizard() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [models, setModels] = useState<CatalogModel[]>([])
  const [ontologies, setOntologies] = useState<PublishedOntology[]>([])
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [modelId, setModelId] = useState('')
  const [systemPrompt, setSystemPrompt] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [pendingExternalTools, setPendingExternalTools] = useState<BoundExternalTool[]>([])
  const [bindFailures, setBindFailures] = useState<string[]>([])
  const [createdAgentId, setCreatedAgentId] = useState<string | null>(null)

  const { bindings, toolsByOntology, bindOntology, unbindOntology, toggleCategory, toggleTool } =
    useOntologyToolSelection(ontologies)

  useEffect(() => {
    let cancelled = false
    agentDetailApi.catalogModels()
      .then(res => { if (!cancelled) setModels(Array.isArray(res.items) ? res.items : []) })
      .catch(() => { if (!cancelled) setError('AGENTS_CATALOG_FAILED') })
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    let cancelled = false
    agentToolsApi.listPublishedOntologies()
      .then(res => { if (!cancelled) setOntologies(Array.isArray(res.items) ? res.items : []) })
      .catch(() => undefined)
    return () => { cancelled = true }
  }, [])

  const bindPendingExternal = useCallback((item: ExternalToolCatalogItem, alias: string) => {
    setPendingExternalTools(prev => [...prev, {
      alias, tool_connection_version_id: item.tool_connection_version_id,
      provider_name: item.provider_name, provider_kind: item.provider_kind,
    }])
  }, [])

  const unbindPendingExternal = useCallback((alias: string) => {
    setPendingExternalTools(prev => prev.filter(p => p.alias !== alias))
  }, [])

  const submit = useCallback(async (event: React.FormEvent) => {
    event.preventDefault()
    const model = models.find(m => m.id === modelId)
    if (!name.trim() || !model) return
    setSaving(true)
    setError('')
    setBindFailures([])
    try {
      const result = await agentDetailApi.create({
        name: name.trim(),
        description: description.trim() || null,
        default_model_config_version_id: model.id,
        default_model_name: model.name,
        system_prompt: systemPrompt || null,
        memory_settings: {},
        ontology_bindings: bindings,
      })
      const failures: string[] = []
      for (const pick of pendingExternalTools) {
        try {
          await agentExternalToolsApi.bind(result.agent_id, result.version_id,
            { tool_connection_version_id: pick.tool_connection_version_id, alias: pick.alias })
        } catch {
          failures.push(pick.alias)
        }
      }
      if (failures.length > 0) {
        setCreatedAgentId(result.agent_id)
        setBindFailures(failures)
      } else {
        navigate(`/agents/${result.agent_id}`)
      }
    } catch {
      setError(t('agent.create.failed', '创建失败'))
    } finally {
      setSaving(false)
    }
  }, [name, description, modelId, models, systemPrompt, bindings, pendingExternalTools, navigate, t])

  return (
    <div className="max-w-2xl">
      <h2 className="text-xl font-semibold mb-4">{t('agent.create.title', '新建 Agent')}</h2>
      <form onSubmit={submit} className="bg-white border rounded-lg p-6 space-y-4" data-testid="agent-create-wizard">
        <div>
          <label className="block text-sm text-gray-600 mb-1" htmlFor="agent-name">{t('agent.create.name', '名称')}</label>
          <input id="agent-name" value={name} onChange={e => setName(e.target.value)}
            className="w-full border rounded-lg px-3 py-2 text-sm" />
        </div>
        <div>
          <label className="block text-sm text-gray-600 mb-1" htmlFor="agent-description">{t('agent.create.description', '描述')}</label>
          <textarea id="agent-description" value={description} onChange={e => setDescription(e.target.value)}
            className="w-full border rounded-lg px-3 py-2 text-sm" rows={3} />
        </div>
        <div>
          <label className="block text-sm text-gray-600 mb-1" htmlFor="agent-model">{t('agent.create.model', '模型')}</label>
          <select id="agent-model" value={modelId} onChange={e => setModelId(e.target.value)}
            className="w-full border rounded-lg px-3 py-2 text-sm">
            <option value="">{t('agent.create.select_model', '选择模型…')}</option>
            {models.map(m => (
              <option key={m.id} value={m.id}>{m.name} · v{m.version_no ?? '—'}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="block text-sm text-gray-600 mb-1" htmlFor="agent-prompt">{t('agent.create.initial_prompt', '初始系统提示词')}</label>
          <textarea id="agent-prompt" value={systemPrompt} onChange={e => setSystemPrompt(e.target.value)}
            className="w-full border rounded-lg px-3 py-2 text-sm font-mono" rows={5}
            placeholder={t('agent.create.prompt_placeholder', '可选 — 可在详情页继续编辑')} />
        </div>
        <div>
          <h3 className="text-sm font-medium text-gray-700 mb-2">{t('agent.tools.ontology_bindings', '本体绑定')}</h3>
          <OntologyToolSelector ontologies={ontologies} bindings={bindings} toolsByOntology={toolsByOntology}
            canEdit onBind={bindOntology} onUnbind={unbindOntology}
            onToggleCategory={toggleCategory} onToggleTool={toggleTool} />
        </div>
        <div>
          <h3 className="text-sm font-medium text-gray-700 mb-2">{t('agent.tools.external', '外部工具')}</h3>
          <ExternalToolCard bindings={pendingExternalTools} canEdit
            onBind={bindPendingExternal} onUnbind={unbindPendingExternal} />
        </div>
        {error && <p className="text-sm text-red-500">{error}</p>}
        {bindFailures.length > 0 && createdAgentId && (
          <div className="border border-amber-300 bg-amber-50 rounded-lg p-3 text-sm text-amber-700">
            <p>{t('agent.create.partial_tool_bind_failure', 'Agent 已创建，但以下外部工具绑定失败：')} {bindFailures.join(', ')}</p>
            <button type="button" onClick={() => navigate(`/agents/${createdAgentId}`)}
              className="mt-2 px-3 py-1 text-xs border border-current rounded hover:opacity-80">
              {t('agent.create.go_to_detail', '前往详情页处理')}
            </button>
          </div>
        )}
        <div className="flex gap-3">
          <button type="submit" disabled={saving || !name.trim() || !modelId}
            className="bg-black text-white rounded-lg px-4 py-2 text-sm font-medium hover:bg-gray-800 disabled:opacity-40">
            {saving ? t('agent.create.saving', '创建中…') : t('agent.create.submit', '创建')}
          </button>
          <button type="button" onClick={() => navigate('/agents')}
            className="px-4 py-2 text-sm border rounded-lg hover:bg-gray-50">
            {t('agent.create.cancel', '取消')}
          </button>
        </div>
      </form>
    </div>
  )
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd frontend && npx vitest run src/pages/agents/new/AgentCreateWizard.test.tsx`
Expected: all pass, including the two pre-existing tests (now fixed to use `getByLabelText('模型')`) and the three new ones.

- [ ] **Step 5: Full frontend regression pass**

Run: `cd frontend && npx vitest run src/pages/agents`
Expected: every test under `src/pages/agents/**` passes — this catches any cross-file breakage from the shared-component extraction (Task 2) or the `ExternalToolCard` prop-shape change (Task 3) that a single-file run wouldn't surface (e.g. `AgentDetailPage.test.tsx` rendering the full detail page including `ToolConfigTab`).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/agents/new/AgentCreateWizard.tsx frontend/src/pages/agents/new/AgentCreateWizard.test.tsx \
  frontend/src/api/agentDetail.ts
git commit -m "feat(agent-ui): add ontology and external-tool selection to the Agent creation wizard"
```
