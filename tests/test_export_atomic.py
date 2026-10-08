from pathlib import Path

import pytest

from metriq_visualizer_export_engine import atomic_export_destination


def test_export_replaces_destination_only_after_complete_encode(tmp_path: Path) -> None:
    destination = tmp_path / "movie.mp4"
    destination.write_bytes(b"previous export")
    with pytest.raises(RuntimeError):
        with atomic_export_destination(destination) as partial:
            partial.write_bytes(b"partial export")
            raise RuntimeError("cancelled")
    assert destination.read_bytes() == b"previous export"
    assert list(tmp_path.glob("*.partial")) == []
    with atomic_export_destination(destination) as partial:
        partial.write_bytes(b"completed export")
    assert destination.read_bytes() == b"completed export"
