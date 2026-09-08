"""The one production DeepSeek vision caller for business-journey completions.

``DeepSeekVisionCaller`` is shared, unmodified, by the ontology extractor
and the Agent Runtime: same exact model, same exact origin, same ledger.
Its constructor takes no transport parameter, so the real gate can never
accept an injected one -- only ``evals.business_journeys`` unit tests reach
into a caller's private ``_client`` attribute to swap in a fake transport.
"""
from __future__ import annotations

import dataclasses
from typing import Mapping, Sequence

from evals.business_journeys.contracts import (
    MODEL_ID,
    OFFICIAL_ORIGIN,
    DeepSeekRetryExhausted,
    ImmutableModelConfigVersion,
    InputPart,
    JourneyModelContext,
    ModelCallLedger,
    ModelConfigurationError,
    ModelProbe,
    ModelResponse,
)
from evals.business_journeys.deepseek_client import DeepSeekVisionClient


class DeepSeekVisionCaller:
    """Delegates transport to ``DeepSeekVisionClient``; no registry fallback."""

    def __init__(
        self,
        api_key: str,
        model_config: ImmutableModelConfigVersion,
        ledger: ModelCallLedger,
        *,
        timeout_seconds: float = 180.0,
    ) -> None:
        self._api_key = api_key
        self._model_config = model_config
        self._ledger = ledger
        self._timeout_seconds = timeout_seconds
        self._client = DeepSeekVisionClient(api_key, timeout_seconds=timeout_seconds)

    def _validate_config(self, context: JourneyModelContext) -> None:
        cfg = self._model_config
        if getattr(cfg, "provider", None) != "deepseek":
            raise ModelConfigurationError(f"PROVIDER_MISMATCH: {getattr(cfg, 'provider', None)!r}")
        if getattr(cfg, "model_id", None) != MODEL_ID:
            raise ModelConfigurationError(f"MODEL_ID_MISMATCH: {getattr(cfg, 'model_id', None)!r}")
        if getattr(cfg, "origin", None) != OFFICIAL_ORIGIN:
            raise ModelConfigurationError(f"ORIGIN_MISMATCH: {getattr(cfg, 'origin', None)!r}")
        if context.model_id != MODEL_ID:
            raise ModelConfigurationError(f"MODEL_ID_MISMATCH: context {context.model_id!r}")
        if context.model_config_version_id != getattr(cfg, "version_id", None):
            raise ModelConfigurationError(f"CONFIG_VERSION_MISMATCH: {context.model_config_version_id!r}")

    def preflight(self, context: JourneyModelContext) -> ModelProbe:
        self._validate_config(context)
        probe = self._client.verify_model()
        self._ledger.record_preflight(context, probe.observed_model)
        return probe

    def complete(
        self,
        context: JourneyModelContext,
        parts: Sequence[InputPart],
        *,
        response_schema: Mapping[str, object],
        system_instruction: str | None = None,
    ) -> ModelResponse:
        """``system_instruction`` rides the SAME completion as a leading
        ``system`` message (see ``DeepSeekVisionClient.complete``); it is
        one logical call and one ledger lease either way."""
        self._validate_config(context)
        lease = self._ledger.begin_logical_call(context)

        try:
            response = self._client.complete(
                parts, response_schema=response_schema, correlation_id=context.correlation_id,
                system_instruction=system_instruction,
                temperature=self._model_config.temperature, seed=self._model_config.seed,
            )
        except DeepSeekRetryExhausted as exc:
            for attempt_no in range(1, getattr(exc, "http_attempts", 2) + 1):
                self._ledger.record_http_attempt(lease, attempt_no)
            raise

        for attempt_no in range(1, response.http_attempts + 1):
            self._ledger.record_http_attempt(lease, attempt_no)

        if response.model != MODEL_ID:
            raise ModelConfigurationError(f"MODEL_ID_MISMATCH: observed {response.model!r}")

        self._ledger.finish_logical_call(lease, observed_model=response.model)
        return dataclasses.replace(response, logical_call_id=lease.logical_call_id)
