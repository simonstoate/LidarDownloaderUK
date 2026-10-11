# SPDX-FileCopyrightText: 2025-2026 Simon Stoate
# SPDX-License-Identifier: GPL-2.0-or-later
"""Point clouds load as downloaded, never joined into a mosaic or cropped: a tile of several files loads as
one layer through a virtual point cloud (VPC).

QGIS draws the points of a VPC only from COPC (cloud-optimised) files; of other files, such as the Environment
Agency's LAZ, it draws just their outlines. So the first time a tile of several files is loaded, each is converted
to a COPC copy, and the VPC lists the copies, with an overview (thinned points) for zoomed-out views. They're kept
apart from the downloads, which stay as they are, in the dataset's vpc folder:

    <dataset>/SJ90SE/                    the files as downloaded
    <dataset>/vpc/SJ90SE.vpc             the tile as one layer
    <dataset>/vpc/SJ90SE-overview.copc.laz
    <dataset>/vpc/copc/<file>.copc.laz   the COPC copies

Uses pdal_wrench, the program QGIS's PDAL tools run (QGIS 3.32+), directly: through Processing, the tools open
their inputs as layers, and run in the background, a COPC input's layer works out its statistics and hands them
back to the tool's thread when done, which crashed QGIS while the tool was still running (0.15 testing, a tile of
28 files). Environment Agency point clouds often have no coordinate system recorded, so the VPC is given British
National Grid.
"""

import glob
import json
import os
import shutil
import sys
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from qgis.core import (Qgis, QgsApplication, QgsBlockingProcess, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
                       QgsCoordinateTransformContext, QgsFeedback, QgsPointCloudClassifiedRenderer, QgsRectangle)
from qgis.PyQt.QtCore import QThread

BNG = 'EPSG:27700'
VPC_FOLDER = 'vpc'
COPC_SUFFIX = '.copc.laz'
# Converting needs about as much space again as the files (the COPC copies), and a margin
CONVERT_SPACE = 1.1
CONVERT_MARGIN_MB = 500
# Several files are converted at once (pdal_wrench converts a file on about one core), as many as there are cores
# to spare and memory for: PDAL holds a file's points while it writes the copy, about 110 bytes each (2.6 GB for a
# 25-million-point file), and some of what's free is left for QGIS and the rest
CONVERT_AT_ONCE = 4
CONVERT_BYTES_PER_POINT = 110
CONVERT_MEMORY_SHARE = 0.7


class NoSpace(RuntimeError):
    """Not enough disk space to convert a tile's files to COPC."""


def wrench_path():
    """The pdal_wrench program QGIS's PDAL tools run (QGIS 3.32 or later with PDAL), or None."""
    for folder in (QgsApplication.libexecPath(), QgsApplication.applicationDirPath()):
        for name in ('pdal_wrench', 'pdal_wrench.exe'):
            if os.path.exists(os.path.join(folder, name)):
                return os.path.join(folder, name)
    return None


def available():
    """True if QGIS's PDAL tools are installed (QGIS 3.32 or later with PDAL): the algorithms and the
    pdal_wrench program they run (some Linux packages register the algorithms without it)."""
    if QgsApplication.processingRegistry().algorithmById('pdal:virtualpointcloud') is None:
        return False
    return wrench_path() is not None


def can_convert():
    """True if QGIS's PDAL tools can write COPC (QGIS 3.44 and later: the pdal_wrench QGIS 3.32 to 3.42 ships
    can't). Before that, a tile of several files loads as a layer per file, which QGIS indexes itself."""
    return available() and Qgis.QGIS_VERSION_INT >= 34400


def is_copc(path):
    """True if a file is COPC: a LAS file whose first record (after the 375-byte LAS 1.4 header) is COPC's."""
    try:
        with open(path, 'rb') as f:
            head = f.read(381)
    except OSError:
        return False
    return head[:4] == b'LASF' and head[377:381] == b'copc'


def point_count(path):
    """How many points a LAS / LAZ file has, from its header (0 if it can't be read)."""
    try:
        with open(path, 'rb') as f:
            head = f.read(255)
    except OSError:
        return 0
    if head[:4] != b'LASF' or len(head) < 111:
        return 0
    if head[25] >= 4 and len(head) >= 255:  # LAS 1.4: a 64-bit count
        return int.from_bytes(head[247:255], 'little')
    return int.from_bytes(head[107:111], 'little')


def free_memory():
    """Bytes of memory free (on macOS, a quarter of all of it), or 0 if unknown."""
    try:
        if sys.platform == 'win32':
            import ctypes

            class Status(ctypes.Structure):
                _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong)] + [
                    (name, ctypes.c_ulonglong) for name in ('total', 'available', 'total_page', 'available_page',
                                                            'total_virtual', 'available_virtual', 'extended')]
            status = Status()
            status.length = ctypes.sizeof(Status)
            return status.available if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)) else 0
        if os.path.exists('/proc/meminfo'):
            with open('/proc/meminfo') as f:
                for line in f:
                    if line.startswith('MemAvailable:'):
                        return int(line.split()[1]) * 1024
            return 0
        return os.sysconf('SC_PHYS_PAGES') * os.sysconf('SC_PAGE_SIZE') // 4
    except (OSError, ValueError, AttributeError):
        return 0


def _bng():
    return QgsCoordinateReferenceSystem(BNG)


def _wrench(arguments, output, keep_going=None, feedback=None, threads=None):
    """Run pdal_wrench (in the background too: it opens no layers) to write output, with threads (all the cores
    by default). keep_going() -> False, or cancelling feedback (from another thread too), stops it: returns False.
    Raises RuntimeError if it didn't write output, with what it said."""
    said = []
    feedback = feedback if feedback is not None else QgsFeedback()

    def read(data):
        said.append(bytes(data).decode('utf-8', 'replace'))
        if keep_going is not None and not keep_going():
            feedback.cancel()

    threads = threads or max(1, QThread.idealThreadCount())
    process = QgsBlockingProcess(wrench_path(), arguments + [f'--threads={threads}'])
    process.setStdOutHandler(read)  # (QGIS needs both, and keeps no reference to read: it's alive until we return)
    process.setStdErrHandler(read)
    code = process.run(feedback)
    if feedback.isCanceled():
        return False
    if code != 0 or not os.path.exists(output):
        lines = [line.strip() for line in ''.join(said).splitlines() if line.strip()]
        raise RuntimeError(f"QGIS's PDAL tools couldn't write {os.path.basename(output)}"
                           + (f': {lines[-1]}' if lines else f' (pdal_wrench {arguments[0]} ended with {code})'))
    return True


def _fix_crs(data):
    """Give a VPC's items without a coordinate system British National Grid (and WGS84 bounds, which
    the format expects once a CRS is known). Returns True if anything changed."""
    bng = _bng()
    # The WKT variant enum became scoped (QgsCoordinateReferenceSystem.WktVariant) in newer bindings
    variants = getattr(QgsCoordinateReferenceSystem, 'WktVariant', QgsCoordinateReferenceSystem)
    variant = variants.WKT2_2019_SIMPLIFIED
    wkt = bng.toWkt(variant)
    to_wgs84 = QgsCoordinateTransform(bng, QgsCoordinateReferenceSystem('EPSG:4326'), QgsCoordinateTransformContext())
    changed = False
    for item in data.get('features', []):
        properties = item.get('properties', {})
        if properties.get('proj:wkt2') or len(properties.get('proj:bbox') or []) != 6:
            continue
        x0, y0, z0, x1, y1, z1 = properties['proj:bbox']
        box = to_wgs84.transformBoundingBox(QgsRectangle(x0, y0, x1, y1))
        properties['proj:wkt2'] = wkt
        item['bbox'] = [box.xMinimum(), box.yMinimum(), z0, box.xMaximum(), box.yMaximum(), z1]
        ring = [[box.xMinimum(), box.yMinimum(), z0], [box.xMinimum(), box.yMaximum(), z0],
                [box.xMaximum(), box.yMaximum(), z1], [box.xMaximum(), box.yMinimum(), z1],
                [box.xMinimum(), box.yMinimum(), z0]]
        item['geometry'] = {'type': 'Polygon', 'coordinates': [ring]}
        changed = True
    return changed


def vpc_path(folder, name):
    """Where a tile's VPC is kept: <dataset folder>/vpc/<tile>.vpc."""
    return os.path.join(folder, VPC_FOLDER, f'{name}.vpc')


def copc_path(folder, source):
    """Where a downloaded file's COPC copy is kept: <dataset folder>/vpc/copc/<file>.copc.laz."""
    stem = os.path.splitext(os.path.basename(source))[0]
    return os.path.join(folder, VPC_FOLDER, 'copc', stem + COPC_SUFFIX)


def _data_hrefs(path):
    """The files a VPC lists (its items' data), as given in it."""
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    return [item.get('assets', {}).get('data', {}).get('href', '') for item in data.get('features', [])]


def vpc_ready(folder, name, files):
    """True if the tile's VPC is there and lists the COPC copies of exactly these files, all there too."""
    path = vpc_path(folder, name)
    if not os.path.exists(path):
        return False
    try:
        listed = {os.path.basename(href.replace('\\', '/')) for href in _data_hrefs(path)}
    except (OSError, ValueError, AttributeError):
        return False
    wanted = {copc_path(folder, f) for f in files}
    return listed == {os.path.basename(p) for p in wanted} and all(os.path.exists(p) for p in wanted)


def _finish_vpc(part, output):
    """Move a VPC just written at part to output, with British National Grid for items without a coordinate
    system, its links written with '/' (pdal_wrench mixes in '\\' on Windows), and its overview renamed with it
    (<tile>-overview.copc.laz, the name QGIS also looks for)."""
    with open(part, encoding='utf-8') as f:
        data = json.load(f)
    _fix_crs(data)
    folder = os.path.dirname(part)
    old, new = (os.path.basename(p)[:-4] + '-overview' + COPC_SUFFIX for p in (part, output))
    for item in data.get('features', []):
        for asset in item.get('assets', {}).values():
            href = asset.get('href')
            if isinstance(href, str):
                href = href.replace('\\', '/')
                asset['href'] = href[:-len(old)] + new if os.path.basename(href) == old else href
    if os.path.exists(os.path.join(folder, old)):
        os.replace(os.path.join(folder, old), os.path.join(folder, new))
    with open(part, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=1)
    os.replace(part, output)


def old_vpcs(folder, name):
    """A tile's VPCs from before 0.15 (in the dataset's mosaics folder, over the LAZ files): build_tile_vpc
    replaces them."""
    return glob.glob(os.path.join(glob.escape(os.path.join(folder, 'mosaics')), f'{name}_*.vpc'))


def _convert(folder, files, keep_going):
    """Convert files to COPC copies (<dataset>/vpc/copc), several at once: as many as there are cores to spare and
    memory for, the biggest first. keep_going(how many done) -> False stops them. Returns False if stopped; raises
    RuntimeError (after stopping the others) if one couldn't be converted."""
    at_once = max(1, min(CONVERT_AT_ONCE, QThread.idealThreadCount() // 2))
    threads = max(1, QThread.idealThreadCount() // at_once)  # (shared between them)
    memory = free_memory() * CONVERT_MEMORY_SHARE
    waiting = sorted(files, key=point_count, reverse=True)
    running = {}  # {conversion: (its feedback, the memory it needs)}
    failed, stopped, done = [], False, 0

    def convert(source, feedback):
        target = copc_path(folder, source)
        part = target[:-len(COPC_SUFFIX)] + '_part' + COPC_SUFFIX
        if not _wrench(['translate', f'--input={source}', f'--output={part}'], part, feedback=feedback,
                       threads=threads):
            if os.path.exists(part):
                os.remove(part)
            return
        if not is_copc(part):
            os.remove(part)
            raise RuntimeError(f"QGIS's PDAL tools didn't convert {os.path.basename(source)} to COPC")
        os.replace(part, target)

    with ThreadPoolExecutor(at_once) as pool:
        while waiting or running:
            using = sum(need for _, need in running.values())
            while waiting and len(running) < at_once:
                need = point_count(waiting[0]) * CONVERT_BYTES_PER_POINT
                if running and using + need > memory:  # (one at a time if that's all there's room for)
                    break
                feedback = QgsFeedback()
                running[pool.submit(convert, waiting.pop(0), feedback)] = (feedback, need)
                using += need
            finished, _ = wait(running, timeout=0.5, return_when=FIRST_COMPLETED)
            for conversion in finished:
                running.pop(conversion)
                try:
                    conversion.result()
                    done += 1
                except Exception as e:  # (stop the others, then say why)
                    failed.append(e)
            if not stopped and (failed or not keep_going(done)):
                stopped = True
                waiting.clear()
                for feedback, _ in running.values():
                    feedback.cancel()
    if failed:
        raise failed[0]
    return not stopped


def build_tile_vpc(folder, name, files, progress=None):
    """A tile of several point cloud files as one layer QGIS can draw: each file converted to a COPC copy (unless
    it has one; several at once), then a VPC over the copies with an overview. Slow for a big tile: run it in
    the background.
    progress(fraction) -> keep going. Returns the VPC's path, or None if stopped. Raises NoSpace if there isn't
    the disk space, RuntimeError if a PDAL tool failed or didn't write COPC. VPCs from before 0.15 (in the
    dataset's mosaics folder, over the LAZ files: only their outlines showed) are removed."""
    pending = [f for f in sorted(files) if not os.path.exists(copc_path(folder, f))]
    needed = sum(os.path.getsize(f) for f in pending) / 1e6 * CONVERT_SPACE + CONVERT_MARGIN_MB
    os.makedirs(os.path.join(folder, VPC_FOLDER, 'copc'), exist_ok=True)
    free = shutil.disk_usage(os.path.join(folder, VPC_FOLDER)).free / 1e6
    if pending and free < needed:
        raise NoSpace(f'converting needs about {needed / 1000:.1f} GB of disk space; {free / 1000:.1f} GB is free')
    steps = len(pending) + 1
    if pending and not _convert(folder, pending, lambda done: progress is None or progress(done / steps)):
        return None
    if progress is not None and not progress(len(pending) / steps):
        return None
    output = vpc_path(folder, name)
    part = output[:-4] + '_part.vpc'
    listing = output[:-4] + '_files.txt'  # (a long list of files could be too long for a command line)
    with open(listing, 'w', encoding='utf-8') as f:
        f.write(''.join(copc_path(folder, source) + '\n' for source in sorted(files)))
    try:
        if not _wrench(['build_vpc', f'--output={part}', '--overview', f'--input-file-list={listing}'], part,
                       lambda: progress is None or progress(len(pending) / steps)):
            return None
    finally:
        os.remove(listing)
    _finish_vpc(part, output)
    for old in old_vpcs(folder, name):
        os.remove(old)
    return output


def style_classified(layer):
    """Colour points by their classification (ground, vegetation, buildings, water...)."""
    names = [a.name() for a in layer.attributes().attributes()] if layer.attributes() else []
    if 'Classification' not in names:
        return
    renderer = QgsPointCloudClassifiedRenderer('Classification', QgsPointCloudClassifiedRenderer.defaultCategories())
    renderer.setPointSize(2)
    layer.setRenderer(renderer)
    layer.triggerRepaint()


def show_overview(layer):
    """Zoomed out, a VPC shows its overview (thinned points) rather than just its files' outlines, QGIS's default
    (QGIS 3.36+: before that, its outlines only)."""
    renderer = layer.renderer()
    behaviour = getattr(Qgis, 'PointCloudZoomOutRenderBehavior', None)
    if renderer is not None and behaviour is not None and hasattr(renderer, 'setZoomOutBehavior'):
        renderer.setZoomOutBehavior(behaviour.RenderOverview)


def ensure_crs(layer):
    """Point clouds without a coordinate system are British National Grid."""
    if not layer.crs().isValid():
        layer.setCrs(_bng())
