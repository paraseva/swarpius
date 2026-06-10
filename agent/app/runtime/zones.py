"""Zone-subsystem container for ``RuntimeState``.

Bundles the zone-domain and the image-byte cache used by browser
artwork requests, exposing the handles that test code pokes at.
"""

from __future__ import annotations

from typing import Any

from app.roon.zone_artwork_service import ZoneArtworkCache
from app.roon.zone_domain import ZoneDomain


class ZoneSubsystem:

    def __init__(self, domain: ZoneDomain, artwork: ZoneArtworkCache) -> None:
        self.domain = domain
        self.artwork = artwork

    @property
    def state_lock(self) -> Any:
        return self.domain.zone_state_lock

    @property
    def image_base64_cache(self) -> Any:
        return self.artwork.image_base64_cache

    @property
    def artwork_lock(self) -> Any:
        return self.artwork.lock

    def replace_artwork(self, new_artwork: ZoneArtworkCache) -> None:
        self.artwork = new_artwork
