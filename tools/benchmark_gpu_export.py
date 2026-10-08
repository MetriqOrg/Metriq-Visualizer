"""Source-bound export benchmark and frame parity evidence (no GUI changes).

Run from a desktop Terminal for actual GL verification; --require-gpu fails
explicitly if the GPU request falls back. Output and cache stay outside the repo.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import contextmanager
import json
import hashlib
import logging
from pathlib import Path
import subprocess
import sys
import time
import types

import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import metriq_visualizer_render as render
from metriq_visualizer_core import analyze_media, build_geometry


def reference_module(ref):
    source = subprocess.run(['git', 'show', f'{ref}:metriq_visualizer_render.py'], cwd=ROOT,
                            check=True, capture_output=True, text=True).stdout
    module = types.ModuleType('export_cpu_reference')
    sys.modules[module.__name__] = module
    exec(compile(source, f'{ref}/metriq_visualizer_render.py', 'exec'), module.__dict__)
    return module


@contextmanager
def profile(module):
    stages, counts, originals = defaultdict(float), Counter(), []
    def wrap(owner, name, stage):
        fn = getattr(owner, name)
        originals.append((owner, name, fn))
        def timed(*args, **kwargs):
            start = time.perf_counter()
            try:
                return fn(*args, **kwargs)
            finally:
                stages[stage] += time.perf_counter() - start
                counts[stage] += 1
        setattr(owner, name, timed)
    wrap(module.OffscreenGeometryRenderer, 'render', 'geometry_total')
    wrap(module.Axes3D, 'draw', 'scene_draw')
    wrap(module.FigureCanvasAgg, 'draw', 'agg_draw')
    wrap(module, '_figure_to_rgba', 'canvas_to_array')
    wrap(module.ExportPreviewSession, '__init__', 'session_init')
    wrap(module.AnalysisCardRenderer, 'render_card', 'panels_cursor')
    wrap(module, 'compose_export_frame_rgba', 'composite_HUD')
    wrap(module, '_draw_project_overlay', 'project_HUD')
    original_popen = subprocess.Popen
    class Pipe:
        def __init__(self, pipe): self.pipe = pipe
        def __getattr__(self, name): return getattr(self.pipe, name)
        def write(self, data):
            start = time.perf_counter()
            try: return self.pipe.write(data)
            finally: stages['pipe_write'] += time.perf_counter() - start
    class Popen(original_popen):
        def __init__(self, cmd, *args, **kwargs):
            super().__init__(cmd, *args, **kwargs)
            if 'rawvideo' in cmd:
                stages['encoder'] = cmd[cmd.index('-c:v') + 1]
            if self.stdin is not None: self.stdin = Pipe(self.stdin)
        def wait(self, *args, **kwargs):
            start = time.perf_counter()
            try: return super().wait(*args, **kwargs)
            finally: stages['encoder_drain'] += time.perf_counter() - start
    subprocess.Popen = Popen
    try:
        yield stages, counts
    finally:
        subprocess.Popen = original_popen
        for owner, name, fn in reversed(originals): setattr(owner, name, fn)


def pixel_metrics(a, b):
    # Local RGB SSIM, Gaussian 11x11 window, sigma 1.5, population covariance.
    # Exclude a five-pixel border. Alpha is also covered by the exact diff.
    x, y = a[..., :3].astype(np.float64), b[..., :3].astype(np.float64)
    def blur(v): return gaussian_filter(v, sigma=(1.5, 1.5, 0), truncate=3.5)
    ux, uy = blur(x), blur(y)
    vx, vy = blur(x*x) - ux*ux, blur(y*y) - uy*uy
    cov = blur(x*y) - ux*uy
    c1, c2 = (0.01*255)**2, (0.03*255)**2
    score = ((2*ux*uy+c1)*(2*cov+c2))/((ux*ux+uy*uy+c1)*(vx+vy+c2))
    delta = np.abs(a.astype(np.int16)-b.astype(np.int16))
    return {'ssim': float(score[5:-5, 5:-5].mean()),
            'changed_pixels': int(np.any(delta != 0, axis=2).sum()),
            'changed_pixel_percent': float(np.any(delta != 0, axis=2).mean()*100),
            'mean_absolute_channel_diff': float(delta.mean()), 'max_channel_diff': int(delta.max()),
            'p99_channel_diff': float(np.percentile(delta, 99)), 'bit_identical': bool(np.array_equal(a,b))}


def encode_only(analysis, options, output):
    from metriq_visualizer_export_engine import (find_ffmpeg, encoder_candidates_for_engine,
        probe_encoder, build_ffmpeg_rawvideo_command)
    ffmpeg = find_ffmpeg()
    if not ffmpeg: return {'unavailable': 'FFmpeg not found'}
    candidate = next((e for e in encoder_candidates_for_engine('auto', ffmpeg)
                      if probe_encoder(ffmpeg, e.name, 'balanced')), None)
    if candidate is None: return {'unavailable': 'No usable encoder'}
    frame = np.full((options.height, options.width, 3), 32, dtype=np.uint8).tobytes()
    cmd = build_ffmpeg_rawvideo_command(ffmpeg_path=ffmpeg, encoder=candidate, output_path=str(output),
        width=options.width, height=options.height, fps=options.fps, quality='balanced',
        bitrate_mbps=0, audio_path=analysis.audio_path, audio_start_time=0, audio_duration=3.)
    start = time.perf_counter()
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        for _ in range(91): proc.stdin.write(frame)
        proc.stdin.close()
        code = proc.wait()
        error = proc.stderr.read()
        if code: raise RuntimeError(error.decode(errors='replace'))
    finally:
        if proc.poll() is None: proc.kill(); proc.wait()
        proc.stderr.close()
    return {'seconds': time.perf_counter()-start, 'encoder': candidate.name, 'frames': 91,
            'note': 'Uniform prepacked frame, includes input transfer, audio and encode; no drawing. Overlaps rendering in normal export.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--reference-ref', default='release/1.13-rollback')
    parser.add_argument('--benchmark', action='store_true')
    parser.add_argument('--repeats', type=int, default=1)
    parser.add_argument('--parity', action='store_true')
    parser.add_argument('--gpu', action='store_true')
    parser.add_argument('--require-gpu', action='store_true')
    parser.add_argument('--heights', type=int, nargs='+', default=[720,1080])
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    reference = reference_module(args.reference_ref)
    analysis = analyze_media(args.fixture, cache_root=args.output/'analysis-cache')
    features = sorted(analysis.features)[:3]
    geometry = build_geometry(analysis, *features, 'time', 'rms', max_points=2000)
    report = {'reference_ref': args.reference_ref, 'source_commit': subprocess.run(
        ['git','rev-parse','HEAD'], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip(),
        'fixture': str(args.fixture), 'xyz_features': features, 'color': 'time', 'size': 'rms',
        'source_files_sha256': {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in
            ['metriq_visualizer_render.py','metriq_visualizer_gpu_export.py','metriq_visualizer_app.py']},
        'benchmark': [], 'parity': []}
    gpu_unavailable = False
    if args.benchmark:
        for repetition in range(max(1, args.repeats)):
            for height in args.heights:
                width = height*16//9
                cases = [('before',reference,None), ('cpu',render,'cpu'), ('auto',render,'auto')]
                if args.gpu: cases.append(('gpu',render,'gpu'))
                if repetition % 2: cases.reverse()
                for label, module, renderer in cases:
                    kwargs = {'renderer': renderer} if renderer else {}
                    options = module.ExportOptions(str(args.output/f'{label}-{height}-{repetition}.mp4'),
                        width=width, height=height, fps=30, end_time=3., **kwargs)
                    messages, diagnostics = [], []
                    class Diagnostics(logging.Handler):
                        def emit(self, record): diagnostics.append(record.getMessage())
                    handler = Diagnostics()
                    logger = logging.getLogger('metriq_visualizer_render')
                    logger.addHandler(handler)
                    try:
                        with profile(module) as (stages, counts):
                            start = time.perf_counter()
                            module.render_export_video(analysis, geometry, options, lambda p,m: messages.append(m))
                            elapsed = time.perf_counter()-start
                    finally:
                        logger.removeHandler(handler)
                    row = {'repetition':repetition,'renderer':label,'height':height,'frames':91,'seconds':elapsed,'fps':91/elapsed,
                           'stage_seconds':dict(stages),'counts':dict(counts),'progress_messages':messages,
                           'diagnostics':diagnostics}
                    if label == 'gpu':
                        fallback = any('GPU renderer' in m and 'falling back to CPU' in m for m in diagnostics)
                        row['gpu_fallback_observed'] = fallback
                        row['gpu_performance_verified'] = not fallback
                    report['benchmark'].append(row)
                    print(json.dumps(row), flush=True)
                    (args.output/'results.json').write_text(json.dumps(report, indent=2))
                options = render.ExportOptions('unused', width=width, height=height, end_time=3.)
                report.setdefault('encode_only',{})[height] = encode_only(analysis,options,args.output/f'encode-only-{height}.mp4')
    if args.parity:
        cases = [('Trail fade', .25, 'Points + line'), ('Cumulative reveal', 1.5, 'Points + line'),
                 ('Full static', 2.75, 'Tube + points')]
        for height in args.heights:
            width = height*16//9
            for idx, (mode, t, shape) in enumerate(cases):
                common = dict(width=width,height=height,history_mode=mode,render_mode=shape,end_time=3.)
                baseline = reference.ExportPreviewSession(analysis, geometry, reference.ExportOptions('unused',**common))
                cpu = render.ExportPreviewSession(analysis, geometry, render.ExportOptions('unused',renderer='cpu',**common))
                try:
                    # Exercise reuse of static layers as well as the first frame.
                    baseline.render_frame(0.); cpu.render_frame(0.)
                    expected = baseline.render_frame(t).copy()
                    current = cpu.render_frame(t).copy()
                finally: baseline.close(); cpu.close()
                worker = next(render.iter_export_frames(analysis, geometry,
                    render.ExportOptions('unused',renderer='auto',**common),[t],parallel=True))
                for label, frame in [('cpu',current),('auto-worker',worker)]:
                    metrics = pixel_metrics(expected,frame)
                    row = dict(height=height,time=t,history_mode=mode,render_mode=shape,renderer=label,**metrics)
                    report['parity'].append(row)
                    print(json.dumps(row),flush=True)
                    side = np.concatenate((expected,frame),axis=1)
                    Image.fromarray(side).save(args.output/f'parity-{height}-{idx}-{label}.png')
                    assert metrics['bit_identical'], row
                if args.gpu or args.require_gpu:
                    gpu = render.ExportPreviewSession(analysis, geometry, render.ExportOptions('unused',renderer='gpu',**common))
                    try:
                        actual = gpu.render_frame(t).copy()
                        active = gpu.active_renderer
                    finally: gpu.close()
                    row = dict(height=height,time=t,history_mode=mode,render_mode=shape,
                               renderer='gpu',active_renderer=active)
                    if active == 'gpu':
                        row.update(pixel_metrics(expected,actual))
                        Image.fromarray(np.concatenate((expected,actual),axis=1)).save(args.output/f'parity-{height}-{idx}-gpu.png')
                    else:
                        gpu_unavailable = True
                        row['unverified'] = 'GPU context failed; actual frame is CPU fallback, not a GPU parity measurement.'
                        Image.fromarray(np.concatenate((expected,actual),axis=1)).save(args.output/f'parity-{height}-{idx}-gpu-fallback.png')
                    report['parity'].append(row)
                    print(json.dumps(row),flush=True)
    (args.output/'results.json').write_text(json.dumps(report, indent=2))
    if args.require_gpu:
        if gpu_unavailable or not any(r.get('active_renderer')=='gpu' for r in report['parity']):
            return 2
        if any(r.get('ssim',1.) < .97 for r in report['parity'] if r.get('renderer')=='gpu'):
            return 3
    return 0


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    raise SystemExit(main())
