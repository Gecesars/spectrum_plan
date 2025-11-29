from __future__ import annotations

import numpy as np

from app.core.terrain import ElevationProvider


def test_elevation_profile_from_mock_hgt(tmp_path):
    data = np.array(
        [
            [900, 901, 902],
            [800, 801, 802],
            [700, 701, 702],
        ],
        dtype=">i2",
    )
    tile_path = tmp_path / "N10W001.hgt"
    data.tofile(tile_path)

    provider = ElevationProvider(srtm_root=tmp_path, cache_size=2)
    elevations = provider.get_elevation_profile([10.9, 10.0], [-0.9, -0.1])

    # Point near the northwest corner should read the first value.
    assert elevations[0] == 900
    # Point near the southeast corner should read the bottom-right value.
    assert elevations[1] == 702
