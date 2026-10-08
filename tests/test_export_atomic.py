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


def test_legacy_render_failure_preserves_existing_export_and_cleans_staging(tmp_path, monkeypatch):
    import numpy as np
    import metriq_visualizer_render as render
    from types import SimpleNamespace
    destination=tmp_path/'existing.mp4'
    destination.write_bytes(b'previous')
    staging=tmp_path/'staging'; staging.mkdir()
    monkeypatch.setattr(render.tempfile,'mkdtemp',lambda **kw: str(staging))
    class Writer:
        def __init__(self,*args): self.released=False
        def isOpened(self): return True
        def write(self,frame): pass
        def release(self): self.released=True
    writer=Writer()
    monkeypatch.setattr(render.cv2,'VideoWriter',lambda *args: writer)
    def frames(*args,**kw):
        yield np.zeros((2,2,4),dtype=np.uint8)
        raise RuntimeError('render failed')
    monkeypatch.setattr(render,'iter_export_frames',frames)
    with pytest.raises(RuntimeError,match='render failed'):
        render._render_export_video_legacy_opencv(SimpleNamespace(audio_path='missing'),None,
            render.ExportOptions(str(destination),width=2,height=2),clip_start=0,clip_end=1,
            clip_duration=1,total_frames=2)
    assert destination.read_bytes()==b'previous'
    assert writer.released
    assert not staging.exists()
    assert not list(tmp_path.glob('*.partial'))


def test_ffmpeg_render_failure_preserves_existing_export(tmp_path, monkeypatch):
    import metriq_visualizer_render as render
    from types import SimpleNamespace
    destination=tmp_path/'existing.mp4'; destination.write_bytes(b'previous')
    staging=tmp_path/'staging'; staging.mkdir()
    monkeypatch.setattr(render.tempfile,'mkdtemp',lambda **kw: str(staging))
    process=SimpleNamespace(stdin=None,poll=lambda: None,kill=lambda: None)
    monkeypatch.setattr(render.subprocess,'Popen',lambda *args,**kwargs: process)
    monkeypatch.setattr(render,'build_ffmpeg_rawvideo_command',lambda **kwargs: ['unused'])
    def frames(*args,**kw):
        raise RuntimeError('render failed')
        yield
    monkeypatch.setattr(render,'iter_export_frames',frames)
    with pytest.raises(RuntimeError,match='render failed'):
        render._render_export_video_ffmpeg(SimpleNamespace(audio_path='missing'),None,
            render.ExportOptions(str(destination)),ffmpeg_path='unused',encoder=SimpleNamespace(label='test'),
            quality='balanced',clip_start=0,clip_end=1,clip_duration=1,total_frames=2)
    assert destination.read_bytes()==b'previous'
    assert not staging.exists()
    assert not list(tmp_path.glob('*.partial'))
