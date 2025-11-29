from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RegulatoryStandard:
    fm_pr: dict[int, float] = None
    tv_pr: dict[int, float] = None

    def __post_init__(self):
        object.__setattr__(
            self,
            "fm_pr",
            {
                0: 45.0,
                200: 6.0,
                400: -20.0,
                600: -40.0,
            },
        )
        object.__setattr__(
            self,
            "tv_pr",
            {
                0: 23.0,
                -6: -28.0,
                6: -27.0,
            },
        )

    def get_required_pr(self, service_type: str, freq_offset: float) -> float:
        """Return protection ratio (dB) for the given service and offset."""
        service = service_type.upper()
        if service == "FM":
            offset = int(round(abs(freq_offset)))
            if offset not in self.fm_pr:
                raise ValueError(f"Unsupported FM offset: {freq_offset} kHz")
            return self.fm_pr[offset]
        if service == "TV":
            offset = int(round(freq_offset))
            if offset not in self.tv_pr:
                raise ValueError(f"Unsupported TV channel offset: {freq_offset} MHz")
            return self.tv_pr[offset]
        raise ValueError(f"Unknown service type {service_type}")
