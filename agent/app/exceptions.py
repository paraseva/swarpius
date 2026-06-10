from __future__ import annotations

from typing import Literal


class SwarpiusError(Exception):
    """Base exception for runtime-specific failures."""


class RoonConnectionUnavailableError(SwarpiusError, RuntimeError):
    """Raised when a runtime operation needs an active Roon connection."""


class ZoneLookupError(SwarpiusError, LookupError):
    """Raised when a requested zone/output/alias cannot be resolved."""


class ToolSchemaMismatchError(SwarpiusError, TypeError):
    """Raised when tool parameters do not match the selected skill schema."""


class ToolMappingNotFoundError(SwarpiusError, LookupError):
    """Raised when no tool implementation exists for a schema type."""


class ToolConfigurationError(SwarpiusError, RuntimeError):
    """Raised when a tool is initialised with an invalid runtime configuration."""


class UnsupportedActionError(SwarpiusError, ValueError):
    """Raised when an action name is unsupported for an operation."""


class ExternalServiceError(SwarpiusError, ConnectionError):
    """Raised when an external service call fails unexpectedly."""


class RequestInterrupted(SwarpiusError):
    """Raised when the active request should stop due to a superseding user message."""


class FixedVolumeError(SwarpiusError, ValueError):
    """Raised when a volume operation targets an output with fixed volume."""


class CategoryCorrectionFailed(SwarpiusError):
    """Raised when category reconciliation tried to recover the intended
    container (album/playlist) for a track-shaped reference and couldn't.
    Distinguishes "tried and failed" from "no correction needed" (which
    returns ``None``).

    ``failure_mode`` discriminates the two ways correction can fail:

    * ``"no_category"`` — the re-search produced no ``Albums``/
      ``Playlists`` category at all. Often means the search terms don't
      match any container of that kind (e.g. searching ``"Voices Russ
      Ballard"`` won't surface a playlist titled ``"Voices"`` because
      the playlist has no ``"Russ Ballard"`` token).
    * ``"no_match"`` — the category was present but contained no item
      whose title strict-equalled the reference's identity. The library
      genuinely lacks that container. The retry hint differs between the
      two: ``no_category`` should re-search with different terms;
      ``no_match`` can drill the existing category directly.
    """

    def __init__(
        self,
        ref_id: str,
        title: str,
        intended_category: str,
        category_name: str,
        failure_mode: Literal["no_category", "no_match"],
    ) -> None:
        super().__init__(
            f"category correction failed: ref={ref_id} title={title!r} "
            f"intended={intended_category} category={category_name} "
            f"mode={failure_mode}"
        )
        self.ref_id = ref_id
        self.title = title
        self.intended_category = intended_category
        self.category_name = category_name
        self.failure_mode = failure_mode
