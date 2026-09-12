"""P2B-TOOLS categories: descriptor -> category derivation and display-name
enrichment (`app.services.agent.tool_categories`)."""


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeDb:
    """Mimics the two `db.execute(text(...), {"ids": [...]}).all()` calls
    `_live_names` makes — one for logic rules, one for actions — keyed by
    which table name appears in the query text, so a test can seed each
    table's current rows independently of the other."""

    def __init__(self, logic_names: dict[str, str], action_names: dict[str, str]):
        self.logic_names = logic_names
        self.action_names = action_names

    def execute(self, statement, params):
        sql = str(statement)
        ids = params["ids"]
        if "v2_ontology_logic_rules" in sql:
            return _FakeResult([(i, self.logic_names[i]) for i in ids if i in self.logic_names])
        if "v2_ontology_action_types" in sql:
            return _FakeResult([(i, self.action_names[i]) for i in ids if i in self.action_names])
        raise AssertionError(f"unexpected query: {sql}")


def test_tool_category_buckets_by_source_kind_and_id_prefix():
    from app.services.agent.tool_categories import tool_category

    assert tool_category({"source_kind": "builtin", "descriptor_id": "query:o-1"}) == "query"
    assert tool_category({"source_kind": "logic", "descriptor_id": "logic:r-1"}) == "logic"
    assert tool_category({"source_kind": "action", "descriptor_id": "action:a-1"}) == "action"
    assert tool_category({"source_kind": "action", "descriptor_id": "action:a-1", "write": True}) == "write"
    assert tool_category({"source_kind": "mcp", "descriptor_id": "mcp:x"}) == "mcp"


def test_enrich_overrides_a_stale_frozen_name_with_the_live_one():
    """A release's tool_descriptors are frozen at publish time; a rule
    renamed afterward must show its CURRENT name, not the name baked into
    the manifest."""
    from app.services.agent.tool_categories import enrich_tool_descriptors

    tools = [{"descriptor_id": "logic:r-1", "source_kind": "logic", "source_id": "r-1",
              "name": "Old Name At Publish Time"}]
    db = _FakeDb(logic_names={"r-1": "Renamed Rule"}, action_names={})
    enriched = enrich_tool_descriptors(tools, db)
    assert enriched[0]["name"] == "Renamed Rule"
    assert enriched[0]["category"] == "logic"


def test_enrich_fills_in_a_name_missing_from_a_pre_migration_manifest():
    """A release published before `name` existed on tool_descriptors at all
    has no `name` key — the live lookup must still supply one."""
    from app.services.agent.tool_categories import enrich_tool_descriptors

    tools = [{"descriptor_id": "action:a-1", "source_kind": "action", "source_id": "a-1"}]
    db = _FakeDb(logic_names={}, action_names={"a-1": "Create Order"})
    enriched = enrich_tool_descriptors(tools, db)
    assert enriched[0]["name"] == "Create Order"


def test_enrich_falls_back_to_a_readable_id_when_the_row_is_gone():
    """The rule/action was deleted since the release was published (or the
    lookup otherwise misses) and the frozen manifest has no name either —
    falls back to a readable `kind:id-prefix` label instead of leaving
    `name` unset."""
    from app.services.agent.tool_categories import enrich_tool_descriptors

    tools = [{"descriptor_id": "logic:deleted-rule-id", "source_kind": "logic", "source_id": "deleted-rule-id"}]
    enriched = enrich_tool_descriptors(tools, _FakeDb(logic_names={}, action_names={}))
    assert enriched[0]["name"] == "logic:deleted-"


def test_enrich_without_a_db_session_keeps_category_only_enrichment():
    from app.services.agent.tool_categories import enrich_tool_descriptors

    tools = [{"descriptor_id": "logic:r-1", "source_kind": "logic", "source_id": "r-1",
              "name": "Whatever Was Frozen"}]
    enriched = enrich_tool_descriptors(tools)
    assert enriched[0]["name"] == "Whatever Was Frozen"
    assert enriched[0]["category"] == "logic"


def test_enrich_leaves_builtin_and_mcp_descriptors_untouched_by_name_lookup():
    from app.services.agent.tool_categories import enrich_tool_descriptors

    tools = [
        {"descriptor_id": "query:o-1", "source_kind": "builtin", "source_id": "query", "name": "本体查询"},
        {"descriptor_id": "mcp:tool1", "source_kind": "mcp", "source_id": "tool1"},
    ]
    enriched = enrich_tool_descriptors(tools, _FakeDb(logic_names={}, action_names={}))
    assert enriched[0]["name"] == "本体查询"
    assert "name" not in enriched[1]
