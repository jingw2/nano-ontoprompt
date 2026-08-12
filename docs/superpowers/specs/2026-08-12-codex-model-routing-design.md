# Codex model routing design

## Goal

Configure this repository so GPT-5.6 Sol plans and reviews with medium reasoning, while a dedicated executor implements approved plans with a separately configurable model. The initial executor model is DeepSeek V4 Flash with maximum reasoning.

## Scope

The repository owns workflow and agent-role configuration. User-level Codex files only register the DeepSeek provider and its model metadata because Codex does not allow project configuration to define providers or authentication routing. No API key is stored in the repository or written into a configuration file.

## Files

### `.codex/config.toml`

- Select `gpt-5.6-sol` as the primary model.
- Set primary and Plan-mode reasoning to `medium`.
- Select `gpt-5.6-sol` as the `/review` model.
- Register project-scoped `executor` and `reviewer` custom agents.
- Leave default subagent model settings unset so each role controls its own model.

### `.codex/agents/executor.toml`

- Default to provider `deepseek`, model `deepseek-v4-flash`, and reasoning effort `max`.
- Reference the DeepSeek-specific model catalog.
- Instruct the executor to implement only an approved plan, keep changes narrow, run relevant checks, and report results.
- Include concise comments showing which fields must change together when switching to GPT-5.6 Luna or another registered provider.

### `.codex/agents/reviewer.toml`

- Use `gpt-5.6-sol` with reasoning effort `medium`.
- Operate read-only and report correctness, regression, security, and test findings.
- Do not edit implementation files.

### `AGENTS.md`

Append a repository workflow requiring the primary Sol agent to plan first, delegate implementation to `executor`, delegate review to `reviewer`, send substantive fixes back to `executor`, and verify the final result.

### `~/.codex/config.toml`

Append a `deepseek` provider registration without changing the user's default model or existing settings:

- Base URL: `https://api.deepseek.com/`
- Wire API: `responses`
- Authentication source: environment variable `DEEPSEEK_API_KEY`

The user will set `DEEPSEEK_API_KEY`; the value will not be requested, printed, or stored by this change.

### `~/.codex/models-deepseek.json`

Store the official Codex model metadata for `deepseek-v4-flash`, including its 1,048,576-token context window and supported `low`, `high`, and `max` reasoning levels. Only the DeepSeek executor references this catalog, so the primary Sol session continues using OpenAI's dynamically supplied model catalog.

## Execution flow

1. The primary GPT-5.6 Sol agent analyzes the request and prepares a plan with medium reasoning.
2. After approval, the primary delegates the plan and acceptance criteria to `executor`.
3. The executor uses DeepSeek V4 Flash with maximum reasoning by default and performs the implementation and checks.
4. The primary delegates the resulting diff to the Sol-based `reviewer` with medium reasoning.
5. Substantive review fixes return to `executor`; the reviewer checks the revised diff.
6. The primary reports the final verification evidence.

## Compatibility and failure behavior

- The desktop application's bundled Codex version is new enough for the official DeepSeek model catalog. The separately installed Homebrew Codex 0.134.0 is below DeepSeek's declared minimum client version 0.144.0 and is outside this change's supported execution path.
- If `DEEPSEEK_API_KEY` is absent, only DeepSeek executor calls fail authentication; Sol planning and review remain unaffected.
- If the executor is switched to Luna, its `model`, `model_provider`, and model-catalog setting must be changed together as documented in the file.

## Verification

- Parse every TOML file and the DeepSeek JSON catalog.
- Confirm project paths resolve from `.codex/config.toml`.
- Confirm the provider registration references `DEEPSEEK_API_KEY` and contains no credential value.
- Confirm Plan and reviewer use Sol with medium reasoning.
- Confirm executor uses `deepseek-v4-flash` with maximum reasoning.
- Confirm unrelated working-tree changes remain untouched.
