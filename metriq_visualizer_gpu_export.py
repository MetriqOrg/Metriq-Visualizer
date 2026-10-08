# Copyright (c) Metriq Foundation, Inc.
# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
# If a copy of the MPL was not distributed with this file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Explicit opt-in GL geometry rasterization, isolated from the GUI/export process.

Agg still owns the projection, axes, labels and HUD. An export-sized framebuffer
rasterizes projected scene primitives; PIL composites the usual cards afterwards.
Qt/driver crashes and stalls cannot take down the export: every IPC wait is bounded.
No GUI widgets or GUI-thread context sharing are involved.
"""
from __future__ import annotations

import multiprocessing
import tempfile
from pathlib import Path

import numpy as np

GPU_STARTUP_TIMEOUT = 20.0
GPU_FRAME_TIMEOUT = 15.0


class GPUExportRenderer:
    """A single GL worker. Construction/render failures are handled by the CPU owner."""
    def __init__(self, analysis, geom, options):
        context = multiprocessing.get_context("spawn")
        self._connection, child = context.Pipe()
        # Large frame data stays out of the pipe: poll() only bounds the first
        # byte of recv(), so a partially sent array could otherwise stall forever.
        capacity = (max(int(options.width), 1400)+1) * (max(int(options.height), 980)+1) * 4
        self._temp_root = tempfile.TemporaryDirectory(prefix="metriq_gpu_frame_")
        self._buffer_path = str(Path(self._temp_root.name) / "frame.rgba")
        self._buffer = np.memmap(self._buffer_path, dtype=np.uint8, mode="w+", shape=(capacity,))
        self._process = context.Process(target=_gpu_worker,
            args=(child, analysis, geom, options, self._buffer_path, capacity), daemon=True)
        self._closed = False
        try:
            self._process.start()
            child.close()
            self._receive(GPU_STARTUP_TIMEOUT)
        except BaseException:
            child.close()
            self.close()
            raise

    def _receive(self, timeout):
        if not self._connection.poll(timeout):
            raise RuntimeError(f"OpenGL worker timed out after {timeout:g}s")
        try:
            status, payload = self._connection.recv()
        except EOFError as exc:
            raise RuntimeError("OpenGL worker exited (display/context unavailable)") from exc
        if status != "ok":
            raise RuntimeError(payload)
        return payload

    def render(self, current_time):
        self._connection.send(float(current_time))
        shape = self._receive(GPU_FRAME_TIMEOUT)
        return np.ndarray(shape, dtype=np.uint8, buffer=self._buffer).copy()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._connection.close()
        if self._process.pid is not None:
            if self._process.is_alive():
                self._process.terminate()
            self._process.join(timeout=1.0)
            if self._process.is_alive():
                self._process.kill()
                self._process.join(timeout=1.0)
            self._process.close()
        self._buffer._mmap.close()
        self._temp_root.cleanup()


def _gpu_worker(connection, analysis, geom, options, buffer_path, capacity):
    renderer = None
    buffer = np.memmap(buffer_path, dtype=np.uint8, mode="r+", shape=(capacity,))
    try:
        renderer = _create_gl_renderer(analysis, geom, options)
        # Smoke-test a real framebuffer draw before declaring the context usable.
        renderer.render(float(options.start_time))
        connection.send(("ok", None))
        while True:
            try:
                t = connection.recv()
            except EOFError:
                break
            frame = renderer.render(t)
            target = np.ndarray(frame.shape, dtype=np.uint8, buffer=buffer)
            np.copyto(target, frame)
            connection.send(("ok", frame.shape))
    except Exception as exc:
        try:
            connection.send(("error", f"{type(exc).__name__}: {exc}"))
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        if renderer is not None:
            renderer.close()
        buffer._mmap.close()
        connection.close()


def depth_sorted_scatter_data(artist, axes):
    # Newer mplot3d stores draw offsets separately; colors/sizes returned by
    # get_* are already depth sorted. Older versions sort get_offsets directly.
    offsets = getattr(artist, "_offset_zordered", None)
    if offsets is None:
        offsets = artist.get_offsets()
    positions = axes.transData.transform(offsets)
    return positions, artist.get_facecolor(), artist.get_edgecolor(), artist.get_sizes(), artist.get_linewidths()


def _create_gl_renderer(analysis, geom, options):
    # Import only in the worker: CPU exports need neither Qt nor PyOpenGL.
    from PySide6.QtGui import QGuiApplication, QImage, QOffscreenSurface, QOpenGLContext, QSurfaceFormat
    from PySide6.QtOpenGL import QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat
    from OpenGL import GL
    from matplotlib.collections import Collection
    from mpl_toolkits.mplot3d import proj3d
    from metriq_visualizer_render import OffscreenGeometryRenderer, _figure_to_rgba

    class GLGeometryRenderer(OffscreenGeometryRenderer):
        def __init__(self):
            self.app = QGuiApplication.instance() or QGuiApplication(["metriq-gpu-export"])
            fmt = QSurfaceFormat()
            fmt.setRenderableType(QSurfaceFormat.OpenGL)
            fmt.setVersion(2, 1)
            fmt.setProfile(QSurfaceFormat.CompatibilityProfile)
            self.surface = QOffscreenSurface()
            self.surface.setFormat(fmt)
            self.surface.create()
            self.context = QOpenGLContext()
            self.context.setFormat(fmt)
            if not self.surface.isValid() or not self.context.create() or not self.context.makeCurrent(self.surface):
                raise RuntimeError("Could not open an OpenGL context in this display session")
            if not GL.glGetString(GL.GL_VERSION):
                raise RuntimeError("OpenGL context has no version string")
            super().__init__(analysis, geom, options)
            # Preserve Agg's reference canvas/projection. Rasterize at export size;
            # map back to the reference card size before the existing composition.
            self.gl_width, self.gl_height = int(options.width), int(options.height)
            fbo_format = QOpenGLFramebufferObjectFormat()
            fbo_format.setAttachment(QOpenGLFramebufferObject.CombinedDepthStencil)
            fbo_format.setSamples(4 if QOpenGLFramebufferObject.hasOpenGLFramebufferBlit() else 0)
            self.fbo = QOpenGLFramebufferObject(self.gl_width, self.gl_height, fbo_format)
            if not self.fbo.isValid():
                raise RuntimeError("Could not allocate the export framebuffer")

        def _draw_frame(self):
            from PIL import Image
            artists = [self.tube_collection, self.trail_collection, self.comet_collection,
                       self.dynamic_scatter, self.head_halo, self.head_scatter, self.head_flash, self.ghost_line]
            visible = [a for a in artists if a.get_visible()]
            visibility = [a.get_visible() for a in artists]
            for a in artists:
                a.set_visible(False)
            try:
                self.canvas.draw()  # unchanged Agg axes, point labels, colorbar and HUD
                decorations = _figure_to_rgba(self.canvas)
            finally:
                for a, shown in zip(artists, visibility):
                    a.set_visible(shown)
            self.ax.M = self.ax.get_proj()
            # Match mplot3d's painter ordering at collection level; GL depth testing
            # would otherwise change the transparent trails' blending/order.
            collections = [a for a in visible if isinstance(a, Collection)]
            collections = sorted(collections, key=lambda a: a.do_3d_projection(), reverse=True)
            if not self.context.makeCurrent(self.surface) or not self.fbo.bind():
                raise RuntimeError("Lost the OpenGL export context")
            GL.glViewport(0, 0, self.gl_width, self.gl_height)
            GL.glDisable(GL.GL_DEPTH_TEST)
            GL.glEnable(GL.GL_BLEND)
            GL.glBlendFuncSeparate(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA,
                                   GL.GL_ONE, GL.GL_ONE_MINUS_SRC_ALPHA)
            GL.glClearColor(0., 0., 0., 0.)
            GL.glClear(GL.GL_COLOR_BUFFER_BIT)
            GL.glMatrixMode(GL.GL_PROJECTION)
            GL.glLoadIdentity()
            cw, ch = self.canvas.get_width_height()
            GL.glOrtho(0., float(cw), 0., float(ch), -1., 1.)
            GL.glMatrixMode(GL.GL_MODELVIEW)
            GL.glLoadIdentity()
            # Clip to the same axes rectangle as the Agg collections.
            x, y, w, h = self.ax.bbox.bounds
            sx, sy = self.gl_width / cw, self.gl_height / ch
            GL.glEnable(GL.GL_SCISSOR_TEST)
            GL.glScissor(int(x*sx), int(y*sy), int(w*sx), int(h*sy))
            if self.ghost_line in visible:
                gx, gy, gz = self.ghost_line.get_data_3d()
                px, py, _ = proj3d.proj_transform(gx, gy, gz, self.ax.M)
                positions = self.ax.transData.transform(np.column_stack([px, py]))
                self._line(positions, self.ghost_line.get_color(), self.ghost_line.get_linewidth(), strip=True)
            for a in collections:
                if a in (self.trail_collection, self.comet_collection):
                    for pts, color, width in zip(a.get_segments(), a.get_colors(), a.get_linewidths()):
                        self._line(self.ax.transData.transform(pts), color, width)
                elif a is self.tube_collection:
                    for path, color in zip(a.get_paths(), a.get_facecolor()):
                        GL.glColor4f(*color)
                        GL.glBegin(GL.GL_POLYGON)
                        for px, py in self.ax.transData.transform(path.vertices):
                            GL.glVertex2f(float(px), float(py))
                        GL.glEnd()
                else:
                    positions, faces, edges, sizes, widths = depth_sorted_scatter_data(a, self.ax)
                    for i, pos in enumerate(positions):
                        radius = np.sqrt(sizes[i % len(sizes)]) * self.fig.dpi / 144.
                        edge_width = widths[i % len(widths)] * self.fig.dpi / 72.
                        if edge_width > 0:
                            self._disc(pos, radius + edge_width / 2., edges[i % len(edges)])
                            radius = max(0., radius - edge_width / 2.)
                        self._disc(pos, radius, faces[i % len(faces)])
            GL.glDisable(GL.GL_SCISSOR_TEST)
            GL.glFinish()
            image = self.fbo.toImage().convertToFormat(QImage.Format_RGBA8888)
            self.fbo.release()
            layer = np.frombuffer(image.constBits(), dtype=np.uint8).reshape(self.gl_height, image.bytesPerLine())
            layer = layer[:, :self.gl_width*4].reshape(self.gl_height, self.gl_width, 4).copy()
            # toImage() returns premultiplied RGBA; Qt's conversion above
            # unpremultiplies it for PIL. Do not divide by alpha a second time.
            overlay = Image.fromarray(layer).resize((cw, ch), Image.Resampling.LANCZOS)
            return np.asarray(Image.alpha_composite(Image.fromarray(decorations), overlay)).copy()

        def _line(self, points, color, width, strip=False):
            if len(points) < 2:
                return
            GL.glColor4f(*color)
            GL.glLineWidth(max(1., float(width) * self.fig.dpi / 72. * self.gl_width / self.canvas.get_width_height()[0]))
            GL.glBegin(GL.GL_LINE_STRIP if strip else GL.GL_LINES)
            for x, y in points:
                GL.glVertex2f(float(x), float(y))
            GL.glEnd()

        def _disc(self, position, radius, color):
            if radius <= 0 or color[3] <= 0:
                return
            GL.glColor4f(*color)
            GL.glBegin(GL.GL_TRIANGLE_FAN)
            GL.glVertex2f(float(position[0]), float(position[1]))
            for angle in np.linspace(0., 2.*np.pi, 33):
                GL.glVertex2f(float(position[0] + radius*np.cos(angle)), float(position[1] + radius*np.sin(angle)))
            GL.glEnd()

        def close(self):
            self.context.makeCurrent(self.surface)
            self.fbo = None
            self.context.doneCurrent()
            self.surface.destroy()

    return GLGeometryRenderer()
