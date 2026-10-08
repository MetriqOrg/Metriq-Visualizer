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


def test_renderer_defaults_and_validation():
    assert render.ExportOptions('x.mp4').renderer == 'auto'
    with pytest.raises(ValueError, match='renderer'):
        render.ExportOptions('x.mp4', renderer='typo')


@pytest.mark.parametrize('mode,time', [('Trail fade', .05), ('Cumulative reveal', .15), ('Full static', .25)])
def test_parallel_cpu_frames_are_bit_identical(scene, mode, time):
    analysis, geometry = scene
    options = render.ExportOptions('unused.mp4', width=640, height=360, history_mode=mode, renderer='cpu')
    session = render.ExportPreviewSession(analysis, geometry, options)
    times = [0., time, .3]
    expected = [session.render_frame(t).copy() for t in times]
    session.close()
    actual = list(render.iter_export_frames(analysis, geometry, options, times, parallel=True))
    for cpu, worker in zip(expected, actual, strict=True):
        np.testing.assert_array_equal(cpu, worker)


def test_gpu_initialization_failure_falls_back_once(scene, monkeypatch, caplog):
    import metriq_visualizer_gpu_export as gpu
    def fail(*args):
        raise RuntimeError('no display')
    monkeypatch.setattr(gpu, 'GPUExportRenderer', fail)
    analysis, geometry = scene
    options = render.ExportOptions('unused.mp4', width=640, height=360, renderer='gpu')
    session = render.ExportPreviewSession(analysis, geometry, options)
    expected = render.ExportPreviewSession(analysis, geometry, replace(options, renderer='cpu'))
    try:
        np.testing.assert_array_equal(session.render_frame(.1), expected.render_frame(.1))
        session.render_frame(.2)
        assert 'falling back to CPU' in caplog.text
        assert caplog.text.count('falling back to CPU') == 1
    finally:
        session.close(); expected.close()


def test_gpu_frame_failure_falls_back_for_same_frame(scene, monkeypatch, caplog):
    import metriq_visualizer_gpu_export as gpu
    closed = []
    class BrokenGPU:
        def __init__(self, *args): pass
        def render(self, t): raise RuntimeError('lost context')
        def close(self): closed.append(True)
    monkeypatch.setattr(gpu, 'GPUExportRenderer', BrokenGPU)
    analysis, geometry = scene
    options = render.ExportOptions('unused.mp4', width=640, height=360, renderer='gpu')
    session = render.ExportPreviewSession(analysis, geometry, options)
    reference = render.ExportPreviewSession(analysis, geometry, replace(options, renderer='cpu'))
    try:
        for t in [.1, .2]:
            np.testing.assert_array_equal(session.render_frame(t), reference.render_frame(t))
        assert closed == [True]
        assert caplog.text.count('falling back to CPU') == 1
    finally:
        session.close(); reference.close()


def _stall_gpu_worker(connection, *args):
    # No startup reply: the parent must terminate this blocked process.
    connection.recv()


def test_gpu_startup_timeout_terminates_worker(scene, monkeypatch):
    import multiprocessing
    import time
    import metriq_visualizer_gpu_export as gpu
    monkeypatch.setattr(gpu, '_gpu_worker', _stall_gpu_worker)
    monkeypatch.setattr(gpu, 'GPU_STARTUP_TIMEOUT', .2)
    before = {p.pid for p in multiprocessing.active_children()}
    start = time.monotonic()
    analysis, geometry = scene
    with pytest.raises(RuntimeError, match='timed out'):
        gpu.GPUExportRenderer(analysis, geometry, render.ExportOptions('unused'))
    assert time.monotonic() - start < 3.
    assert {p.pid for p in multiprocessing.active_children()} == before


def test_worker_failure_resumes_without_lost_or_duplicate_frames(scene, monkeypatch, caplog):
    from concurrent.futures import Future
    analysis, geometry = scene
    options = render.ExportOptions('unused', width=640, height=360)
    times = [0., .1, .2, .3]
    reference = list(render.iter_export_frames(analysis, geometry, replace(options, renderer='cpu'), times))
    class Pool:
        calls = 0
        def __init__(self, **kwargs): pass
        def submit(self, fn, t):
            future = Future()
            future.set_exception(RuntimeError('worker died'))
            return future
        def shutdown(self, **kwargs): pass
    monkeypatch.setattr(render, 'ProcessPoolExecutor', Pool)
    actual = list(render.iter_export_frames(analysis, geometry, options, times, parallel=True))
    assert len(actual) == len(reference)
    for a,b in zip(actual, reference, strict=True): np.testing.assert_array_equal(a,b)
    assert 'using serial CPU' in caplog.text


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


def test_renderer_selector_and_option_persistence(scene, monkeypatch):
    from types import SimpleNamespace
    import metriq_visualizer_app as app
    selected = []
    def choose(*args):
        selected.append(args)
        return 'Fast (GPU)', True
    monkeypatch.setattr(app.QInputDialog, 'getItem', choose)
    window = SimpleNamespace(_export_renderer='auto', _schedule_autosave=lambda: None)
    assert app.MainWindow._choose_export_renderer(window)
    assert window._export_renderer == 'gpu'
    assert selected[0][3] == ('Auto (identical CPU output)', 'CPU (reference)', 'Fast (GPU)')
    monkeypatch.setattr(app.QInputDialog, 'getItem', lambda *args: ('CPU (reference)', False))
    assert not app.MainWindow._choose_export_renderer(window)
    assert window._export_renderer == 'gpu'
    # _build_export_options_from_state reads every default eagerly, so supply
    # the fallback widgets without building a GUI or changing any controls.
    class Widget:
        def value(self): return 1
        def currentText(self): return 'unused'
        def isChecked(self): return False
        def text(self): return ''
    class Window:
        _base_azimuth = 35.
        def __getattr__(self, name): return Widget()
    state = {'export': {'renderer': 'gpu', 'width': 640, 'height': 360, 'fps':30}}
    assert app.MainWindow._build_export_options_from_state(Window(),state,'unused').renderer == 'gpu'
    del state['export']['renderer']
    assert app.MainWindow._build_export_options_from_state(Window(),state,'unused').renderer == 'auto'


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
    options = render.ExportOptions('unused',width=640,height=360,renderer='cpu',render_mode='Tube + points',
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
    expected=list(render.iter_export_frames(analysis,geometry,replace(options,renderer='cpu'),times))
    session=render.ExportPreviewSession(analysis,geometry,replace(options,renderer='cpu'))
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


def test_gpu_scatter_projection_keeps_point_colors_and_sizes_with_depth(scene):
    from mpl_toolkits.mplot3d import proj3d
    import metriq_visualizer_gpu_export as gpu
    analysis, geometry = scene
    renderer = render.OffscreenGeometryRenderer(analysis,geometry,render.ExportOptions('unused',width=640,height=360))
    artist=renderer.dynamic_scatter
    xyz=np.array([[1.,-1.,1.],[-1.,1.,-1.],[0.,0.,0.]])
    colors=np.array([[1.,0.,0.,1.],[0.,1.,0.,1.],[0.,0.,1.,1.]])
    sizes=np.array([9.,25.,49.])
    renderer._set_scatter_data(artist,*xyz.T,colors,sizes)
    renderer.ax.M=renderer.ax.get_proj()
    artist.do_3d_projection()
    x,y,z=proj3d.proj_transform(*xyz.T,renderer.ax.M)
    order=np.argsort(z)[::-1]
    assert not np.array_equal(order,np.arange(3))
    positions,faces,edges,actual_sizes,widths=gpu.depth_sorted_scatter_data(artist,renderer.ax)
    np.testing.assert_allclose(positions,renderer.ax.transData.transform(np.column_stack([x,y])[order]))
    np.testing.assert_array_equal(faces,colors[order])
    np.testing.assert_array_equal(actual_sizes,sizes[order])


def test_cpu_worker_wait_is_bounded_and_stall_falls_back(scene, monkeypatch):
    from concurrent.futures import TimeoutError
    analysis,geometry=scene
    options=render.ExportOptions('unused',width=640,height=360)
    waits=[]; shutdowns=[]
    class Future:
        def result(self,timeout=None):
            waits.append(timeout)
            assert timeout is not None
            raise TimeoutError('stalled worker')
        def cancel(self): pass
    class Pool:
        def __init__(self,**kw): pass
        def submit(self,*args): return Future()
        def shutdown(self,**kw): shutdowns.append(kw)
    monkeypatch.setattr(render,'ProcessPoolExecutor',Pool)
    times=[0.,.1]
    actual=list(render.iter_export_frames(analysis,geometry,options,times,parallel=True))
    expected=list(render.iter_export_frames(analysis,geometry,replace(options,renderer='cpu'),times))
    for a,b in zip(actual,expected,strict=True): np.testing.assert_array_equal(a,b)
    assert waits and all(t==render.CPU_FRAME_TIMEOUT for t in waits)
    assert shutdowns==[{'wait':False,'cancel_futures':True}]


def _stall_after_startup(connection, *args):
    connection.send(('ok',None))
    connection.recv()  # Receive the frame request, then never signal completion.
    connection.recv()


def test_gpu_frame_timeout_terminates_worker_and_removes_buffer(scene, monkeypatch):
    import multiprocessing
    import time
    from pathlib import Path
    import metriq_visualizer_gpu_export as gpu
    monkeypatch.setattr(gpu,'_gpu_worker',_stall_after_startup)
    monkeypatch.setattr(gpu,'GPU_FRAME_TIMEOUT',.2)
    analysis,geometry=scene
    before={p.pid for p in multiprocessing.active_children()}
    renderer=gpu.GPUExportRenderer(analysis,geometry,render.ExportOptions('unused'))
    buffer=Path(renderer._buffer_path)
    assert buffer.exists()
    start=time.monotonic()
    try:
        with pytest.raises(RuntimeError,match='timed out'): renderer.render(.1)
    finally: renderer.close()
    assert time.monotonic()-start<3.
    assert not buffer.exists()
    assert {p.pid for p in multiprocessing.active_children()}==before


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
