from dataclasses import replace

import numpy as np
import pytest

import metriq_visualizer_render as render
from metriq_visualizer_core import analysis_from_table_file, build_geometry


@pytest.fixture
def scene(tmp_path):
    source = tmp_path / 'scene.csv'
    source.write_text('time,a,b,c\n0,1,4,2\n0.1,2,3,5\n0.2,4,2,1\n0.3,8,1,3\n')
    analysis = analysis_from_table_file(source)
    geometry = build_geometry(analysis, 'a', 'b', 'c', 'time', 'a')
    return analysis, geometry



@pytest.mark.parametrize('mode,time', [('Trail fade', .05), ('Cumulative reveal', .15), ('Full static', .25)])
def test_serial_and_parallel_cpu_frames_are_bit_identical(scene, mode, time):
    analysis, geometry = scene
    options = render.ExportOptions('unused.mp4', width=640, height=360, history_mode=mode)
    session = render.ExportPreviewSession(analysis, geometry, options)
    times = [0., time, .3]
    expected = [session.render_frame(t).copy() for t in times]
    session.close()
    actual = list(render.iter_export_frames(analysis, geometry, options, times, parallel=True))
    for cpu, worker in zip(expected, actual, strict=True):
        np.testing.assert_array_equal(cpu, worker)




@pytest.mark.parametrize('fit', ['contain', 'fill', 'stretch'])
def test_cached_composition_matches_uncached_with_overlap_and_clipping(scene, fit):
    analysis, geometry = scene
    layout = render.default_export_layout()
    layout.geometry.fit_mode = fit
    layout.geometry.content_scale = 1.7
    layout.preview.x = layout.geometry.x
    layout.preview.y = layout.geometry.y
    options = render.ExportOptions('unused', width=640, height=360, layout=layout)
    rng = np.random.default_rng(7)
    cards = {'geometry': rng.integers(0,256,(140,180,4),dtype=np.uint8)}
    cache = {}
    for t in [0.,.2]:
        params = dict(cards=cards, output_size=(640,360),layout=layout,source_path=analysis.source_path,
                      options=options,current_time=t,analysis=analysis)
        expected = render.compose_export_frame_rgba(**params)
        actual = render.compose_export_frame_rgba(**params,static_layers=cache)
        np.testing.assert_array_equal(actual,expected)




def test_parallel_cpu_video_preview_and_detailed_scene_equality(scene, tmp_path):
    import cv2
    analysis, geometry = scene
    source = tmp_path / 'preview.avi'
    writer = cv2.VideoWriter(str(source),cv2.VideoWriter_fourcc(*'MJPG'),10.,(64,48))
    assert writer.isOpened()
    try:
        for i in range(8): writer.write(np.full((48,64,3), (i*25,80,180),dtype=np.uint8))
    finally: writer.release()
    analysis = replace(analysis,source_path=str(source))
    options = render.ExportOptions('unused',width=640,height=360,render_mode='Tube + points',
        ghost_path=True,show_colorbar=True,point_label_mode='Current point',show_watermark=True,watermark_text='test')
    times=[0.,.1,.2,.3,.5,.7]
    expected=list(render.iter_export_frames(analysis,geometry,options,times))
    actual=list(render.iter_export_frames(analysis,geometry,options,times,parallel=True))
    for a,b in zip(actual,expected,strict=True): np.testing.assert_array_equal(a,b)




@pytest.mark.parametrize('fail_at', [1,3])
def test_submission_failure_preserves_unsubmitted_frame(scene, monkeypatch, fail_at):
    from concurrent.futures import Future
    analysis,geometry=scene
    options=render.ExportOptions('unused',width=640,height=360)
    times=[0.,.1,.2,.3]
    expected=list(render.iter_export_frames(analysis,geometry,options,times))
    session=render.ExportPreviewSession(analysis,geometry,options)
    class Pool:
        calls=0
        def __init__(self,**kw): pass
        def submit(self,fn,t):
            self.calls+=1
            if self.calls==fail_at: raise RuntimeError('submit failed')
            future=Future(); future.set_result(session.render_frame(t).copy()); return future
        def shutdown(self,**kw): pass
    monkeypatch.setattr(render,'ProcessPoolExecutor',Pool)
    try: actual=list(render.iter_export_frames(analysis,geometry,options,times,parallel=True))
    finally: session.close()
    assert len(actual)==len(expected)
    for a,b in zip(actual,expected,strict=True): np.testing.assert_array_equal(a,b)




def test_auto_uses_serial_verified_path_without_spawning(monkeypatch):
    calls=[]; times=[]; closed=[]
    def forbidden_pool(**kw):
        calls.append(kw)
        raise AssertionError('Auto must use the verified serial winner')
    class Session:
        def __init__(self,*args): pass
        def render_frame(self,t): times.append(t); return np.zeros((1,1,4),dtype=np.uint8)
        def close(self): closed.append(True)
    monkeypatch.setattr(render,'ProcessPoolExecutor',forbidden_pool)
    monkeypatch.setattr(render,'ExportPreviewSession',Session)
    requests=[i/30. for i in range(91)]
    frames=list(render.iter_export_frames(None,None,render.ExportOptions('unused'),requests))
    assert len(frames)==91 and times==requests and not calls and closed==[True]


def test_legacy_renderer_state_values_load_as_normal_cpu(scene):
    import metriq_visualizer_app as app
    class Widget:
        def value(self): return 1
        def currentText(self): return 'unused'
        def isChecked(self): return False
        def text(self): return ''

    class Window:
        _base_azimuth = 35.
        def __getattr__(self, name): return Widget()

    window = Window()
    for renderer in ('gpu', 'cpu', 'auto'):
        state = {'export': {'renderer': renderer, 'width': 640, 'height': 360, 'fps': 30}}
        options = app.MainWindow._build_export_options_from_state(window, state, 'unused')
        assert options.width == 640 and not hasattr(options, 'renderer')
