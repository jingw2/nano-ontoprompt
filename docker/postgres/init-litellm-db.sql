-- Separate database for LiteLLM proxy (Admin UI / virtual keys).
-- Runs only on first Postgres volume init.
CREATE DATABASE litellm;
GRANT ALL PRIVILEGES ON DATABASE litellm TO ontoprompt;
