r"""Headless smoke test of the plugin inside a real QGIS install (Qt5 or Qt6).

Run with the Python that ships with QGIS, e.g. on Windows:
    "C:\Program Files\QGIS 4.2.1\bin\python-qgis.bat" "<repo>\tests\smoke_test_qgis.py"
(python-qgis.bat changes directory, so pass the full path.) In Docker / Linux:
    python3 tests/smoke_test_qgis.py

The network is faked, so nothing is downloaded. Set LIDAR_LIVE=1 to also run
one real download from environment.data.gov.uk (~70 MB).
"""
import faulthandler
import io
import json
import os
import shutil
import sys
import tempfile
import time
import traceback
import zipfile
from unittest import mock

faulthandler.enable()  # print a Python traceback if QGIS segfaults
TEST_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(TEST_DIR))  # repo root, for the lidar_downloader_uk package
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qgis.core import (  # noqa: E402
    Qgis, QgsApplication, QgsCoordinateReferenceSystem, QgsExpression, QgsFeature, QgsGeometry, QgsPointXY,
    QgsProject, QgsRectangle, QgsVectorLayer)
from qgis.gui import QgsMapCanvas  # noqa: E402
from qgis.PyQt.QtCore import QCoreApplication, QObject, QSettings, QT_VERSION_STR, pyqtSignal  # noqa: E402
from qgis.PyQt.QtWidgets import QMainWindow  # noqa: E402

app = QgsApplication([], True)
app.initQgis()
# Keep the plugin's QSettings writes out of the real QGIS profile
QCoreApplication.setOrganizationName('LidarDownloaderSmokeTest')
QCoreApplication.setApplicationName('smoke_test')
QSettings().clear()
sys.path.append(os.path.join(QgsApplication.pkgDataPath(), "python", "plugins"))
from processing.core.Processing import Processing  # noqa: E402
Processing.initialize()

import lidar_downloader_uk as pkg  # noqa: E402
from lidar_downloader_uk import lidar_downloader as mod, storage, tasks  # noqa: E402

failures = []


def check(cond, label):
    print(("PASS " if cond else "FAIL ") + label, flush=True)
    if not cond:
        failures.append(label)


def tile_origin(tile):
    """South-west corner (BNG metres) of a 5km tile such as 'SU12NE'."""
    l1, l2 = ord(tile[0]) - 65, ord(tile[1]) - 65
    l1, l2 = l1 - (l1 > 7), l2 - (l2 > 7)  # the grid letters skip I
    east = ((l1 - 2) % 5) * 5 + l2 % 5
    north = (19 - (l1 // 5) * 5) - l2 // 5
    e = east * 100000 + int(tile[2]) * 10000 + (5000 if tile[5] == 'E' else 0)
    n = north * 100000 + int(tile[3]) * 10000 + (5000 if tile[4] == 'N' else 0)
    return e, n


def tile_zip_bytes(tile):
    """A zip shaped like the real service's: a raster named after the tile, in British National Grid
    at the tile's true position (50 x 50 cells of 100 m, sloping)."""
    from osgeo import gdal, osr
    e, n = tile_origin(tile)
    mem = gdal.GetDriverByName('MEM').Create('', 50, 50, 1, gdal.GDT_Float32)
    mem.SetGeoTransform((e, 100, 0, n + 5000, 0, -100))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(27700)
    mem.SetProjection(srs.ExportToWkt())
    band = mem.GetRasterBand(1)
    band.WriteRaster(0, 0, 50, 50, bytes(bytearray(
        b''.join(__import__('struct').pack('f', float(x + y) + (e % 100000) / 100 + 0.37)
                 for y in range(50) for x in range(50)))))
    stem = f'{tile[:4]}{tile[4:].lower()}_DTM_1m'
    path = f'/vsimem/{stem}.asc'
    gdal.GetDriverByName('AAIGrid').CreateCopy(path, mem)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        for ext in ('asc', 'prj'):
            vpath = f'/vsimem/{stem}.{ext}'
            f = gdal.VSIFOpenL(vpath, 'rb')
            gdal.VSIFSeekL(f, 0, 2)
            size = gdal.VSIFTellL(f)
            gdal.VSIFSeekL(f, 0, 0)
            zf.writestr(f'{stem}.{ext}', gdal.VSIFReadL(1, size, f))
            gdal.VSIFCloseL(f)
            gdal.Unlink(vpath)
    return buf.getvalue()


def square_raster_bytes(ref):
    """A small GeoTIFF (10 x 10 cells, sloping) covering a grid square such as 'SH2281', 'NS79SE' or 'NS79'."""
    from osgeo import gdal, osr
    from lidar_downloader_uk import places as places_module
    ref = ref.upper()
    e0, n0 = places_module.square_origin(ref[:2])
    if len(ref) == 6 and ref[4:].isalpha():                       # 5 km tile
        e, n = tile_origin(ref)
        size = 5000
    elif len(ref) == 6:                                            # 1 km square
        e, n, size = e0 + int(ref[2:4]) * 1000, n0 + int(ref[4:6]) * 1000, 1000
    else:                                                          # 10 km square
        e, n, size = e0 + int(ref[2]) * 10000, n0 + int(ref[3]) * 10000, 10000
    path = f'/vsimem/{ref}_{time.monotonic_ns()}.tif'
    ds = gdal.GetDriverByName('GTiff').Create(path, 10, 10, 1, gdal.GDT_Float32)
    ds.SetGeoTransform((e, size / 10, 0, n + size, 0, -size / 10))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(27700)
    ds.SetProjection(srs.ExportToWkt())
    ds.GetRasterBand(1).WriteRaster(0, 0, 10, 10, __import__('struct').pack('100f', *[float(i) for i in range(100)]))
    ds = None
    f = gdal.VSIFOpenL(path, 'rb')
    gdal.VSIFSeekL(f, 0, 2)
    length = gdal.VSIFTellL(f)
    gdal.VSIFSeekL(f, 0, 0)
    data = gdal.VSIFReadL(1, length, f)
    gdal.VSIFCloseL(f)
    gdal.Unlink(path)
    return data


BASE = 'https://environment.data.gov.uk/tiles/collections/survey'
DEFAULT = ('lidar_composite_dtm', '2022', '1')
NLP_DTM = ('national_lidar_programme_dtm', '2019', '1')
NLP_CLOUD = ('national_lidar_programme_point_cloud', '2019', '1')
NLP_DTM_2020 = ('national_lidar_programme_dtm', '2020', '1')
NLP_DTM_ALL = ('national_lidar_programme_dtm', 'latest', '')  # the whole product: all years and resolutions
RGBN = ('vertical_aerial_photography_tiles_rgbn', '2014', '0.2')
OBLIQUE = ('oblique_aerial_photography_tiles_incident_response', '2023', 'NaN')


class FakeServer:
    """Scripted responses per tile id: a list of (status, body) consumed one request at a time.

    Searches (POST) answer from self.catalogue: {tile name: [(product, year, resolution), ...]}.
    """

    def __init__(self):
        self.responses = {}
        self.requests = []
        self.urls = []
        self.searches = 0
        self.search_status = 200
        # postcodes.io lookups: {text in the URL: (status, JSON body)}
        self.geocode = {}
        self.geocode_urls = []
        self.delay = 0
        # DataMapWales WFS answers, the Scottish catalogue's, and files on their storage: {url: bytes}
        self.wales = {'national': {'features': []}, 'archive': {'features': []}}
        self.scotland_collections = {'result': []}
        self.scotland_products = {'result': []}
        self.files = {}
        self.file_requests = []
        self.active = self.max_active = 0
        self.lock = __import__('threading').Lock()
        england = ['SU12NE', 'SU12NW', 'SU12SE', 'SU12SW', 'SU22NW', 'SU32NW', 'TQ09SW']
        self.catalogue = {tile: [DEFAULT] for tile in england}
        self.catalogue['SU12NE'] += [NLP_DTM, NLP_CLOUD, RGBN, OBLIQUE]
        self.catalogue['SU12NW'] += [NLP_DTM_2020]

    def search_response(self):
        from lidar_downloader_uk import api
        results = []
        for tile, datasets in self.catalogue.items():
            tile_id = api.tile_to_grid(tile)
            for product, year, res in datasets:
                results.append({'product': {'id': product, 'label': product}, 'year': {'id': year, 'label': year},
                                'resolution': {'id': res, 'label': res + 'm'},
                                'tile': {'id': tile_id, 'label': tile[:4] + tile[4:].lower()},
                                'uri': f'{BASE}/{product}/{year}/{res}/{tile_id}'})
        # A product the plugin doesn't support, which must be ignored
        results.append({'product': {'id': 'casi_multispectral_imagery', 'label': 'x'},
                        'year': {'id': '2014', 'label': '2014'}, 'resolution': {'id': '0.5', 'label': '0.5m'},
                        'tile': {'id': 'SU1525', 'label': 'SU12ne'}, 'uri': f'{BASE}/casi/SU1525'})
        return json.dumps({'count': len(results), 'results': results}).encode()

    def request_class(self):
        server = self

        class FakeReply:
            def __init__(self, status, body):
                self.status, self.body = status, body

            def attribute(self, _attr):
                return self.status

            def content(self):
                return self.body

            def request(self):
                return self.sent  # the request actually answered (after any redirects)

        class FakeBlockingRequest(QObject):
            downloadProgress = pyqtSignal('qint64', 'qint64')

            def post(self, request, data, force_refresh=False, feedback=None):
                self._sent = request
                if 'remotesensing.data.gov.scot' in request.url().toString():
                    self._reply = FakeReply(200, json.dumps(server.scotland_products).encode())
                    return 0
                server.searches += 1
                ok = server.search_status == 200
                self._reply = FakeReply(server.search_status, server.search_response() if ok else b'')
                return 0

            def get(self, request, force_refresh=False, feedback=None):
                self._sent = request
                url = request.url().toString()
                if 'datamap.gov.wales' in url:
                    layer = 'national' if 'welsh_government' in url else 'archive'
                    self._reply = FakeReply(200, json.dumps(server.wales[layer]).encode())
                    return 0
                if 'remotesensing.data.gov.scot' in url:
                    self._reply = FakeReply(200, json.dumps(server.scotland_collections).encode())
                    return 0
                if url in server.files:
                    server.file_requests.append(url)
                    body = server.files[url]
                    self.downloadProgress.emit(len(body), len(body))
                    self._reply = FakeReply(200, body)
                    return 0
                if 'api.postcodes.io' in url:
                    server.geocode_urls.append(url)
                    status, body = next(((st, b) for key, (st, b) in server.geocode.items() if key in url), (404, {}))
                    self._reply = FakeReply(status, json.dumps(body).encode())
                    return 0
                server.urls.append(request.url().toString())
                with server.lock:
                    server.active += 1
                    server.max_active = max(server.max_active, server.active)
                try:
                    time.sleep(server.delay)
                finally:
                    with server.lock:
                        server.active -= 1
                tile_id = request.url().toString().rsplit('/', 1)[-1]
                server.requests.append(tile_id)
                queue = server.responses.get(tile_id, [(500, b'')])
                status, body = queue.pop(0) if len(queue) > 1 else queue[0]
                if status == 'SLOW':  # hang until cancelled
                    while not (feedback and feedback.isCanceled()):
                        time.sleep(0.05)
                    status, body = None, b''
                self.downloadProgress.emit(len(body), len(body))
                self._reply = FakeReply(status, body)
                return 0

            def reply(self):
                self._reply.sent = self._sent
                return self._reply

            def errorMessage(self):
                return 'network error' if self._reply.status is None else ''

        return FakeBlockingRequest


def plugin_manager_accepts_metadata():
    """Run QGIS's own Plugin Manager compatibility check against metadata.txt."""
    import configparser
    sys.path.append(os.path.join(QgsApplication.pkgDataPath(), "python"))
    from pyplugin_installer.version_compare import isCompatible, pyQgisVersion

    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read(os.path.join(os.path.dirname(TEST_DIR), 'lidar_downloader_uk', 'metadata.txt'), encoding='utf-8')
    meta = dict(parser.items('general'))
    minimum = meta['qgisMinimumVersion']
    maximum = meta.get('qgisMaximumVersion') or minimum[0] + '.99'  # same default QGIS applies
    return isCompatible(pyQgisVersion(), minimum, maximum)


def style_rules(layer):
    """{label: (filter expression, is ELSE, colour name)} for the layer's style rules.

    Plain values only: holding Python wrappers of rules from a renderer that QGIS later
    replaces (and deletes) risks memory corruption on older PyQt5-based QGIS.
    """
    rules = {}
    for rule in layer.renderer().rootRule().children():
        symbol = rule.symbol()
        rules[rule.label()] = (rule.filterExpression(), rule.isElse(), symbol.color().name() if symbol else None)
    return rules


def wait_for_search(plugin, timeout=10):
    """Wait for the debounced availability search to run and finish."""
    end = time.monotonic() + timeout
    while (plugin.search_task is not None or plugin.search_timer.isActive()) and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()
    return plugin.search_task is None


def wait_for_coverage(plugin, timeout=10):
    """Wait for the debounced coverage check (and its search) to finish."""
    end = time.monotonic() + timeout
    while (plugin.coverage_task is not None or plugin.coverage_timer.isActive()) and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()
    return plugin.coverage_task is None


def write_survey_metadata(path, surveys, layer_name='lidar_used_in_merging_process', survey_type=None):
    """A metadata GeoPackage like the Composite DTM's (or SurfZone's, with a layer name and survey type):
    surveys is [(name, year, xmin, ymin, xmax, ymax)]."""
    from osgeo import ogr, osr
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(27700)
    ds = ogr.GetDriverByName('GPKG').CreateDataSource(path)
    lyr = ds.CreateLayer(layer_name, srs, ogr.wkbMultiPolygon)
    for name, kind in (('FILENAME', ogr.OFTString), ('RESOLUTION', ogr.OFTReal), ('SD_FLOWN', ogr.OFTDateTime),
                       ('ED_FLOWN', ogr.OFTDateTime), ('SRVY_YEAR', ogr.OFTInteger), ('SRVY_TYPE', ogr.OFTString)):
        lyr.CreateField(ogr.FieldDefn(name, kind))
    for name, year, x0, y0, x1, y1 in surveys:
        f = ogr.Feature(lyr.GetLayerDefn())
        f.SetField('FILENAME', name)
        f.SetField('RESOLUTION', 1.0)
        f.SetField('SD_FLOWN', f'{year}/01/29 00:00:00')
        f.SetField('ED_FLOWN', f'{year}/02/07 00:00:00')
        f.SetField('SRVY_YEAR', year)
        if survey_type:
            f.SetField('SRVY_TYPE', survey_type)
        f.SetGeometry(ogr.CreateGeometryFromWkt(f'MULTIPOLYGON((({x0} {y0},{x1} {y0},{x1} {y1},{x0} {y1},{x0} {y0})))'))
        lyr.CreateFeature(f)
    lyr = None
    ds = None


def wait_for_geocode(plugin, timeout=10):
    end = time.monotonic() + timeout
    while plugin.geocode_task is not None and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()
    return plugin.geocode_task is None


def write_las(path, tile, spacing=50):
    """A small uncompressed LAS 1.2 point cloud over a 5 km tile, with no coordinate system (like many
    Environment Agency surveys): sloping ground (class 2, z = 10 + x/1000) and a 500 m square "building"
    (class 6, 20 m higher) in the middle of the tile."""
    import struct
    e, n = tile_origin(tile)
    points = []
    for i in range(5000 // spacing):
        for j in range(5000 // spacing):
            x, y = e + (i + 0.5) * spacing, n + (j + 0.5) * spacing
            z = 10 + (x - e) / 1000
            building = 2250 < x - e < 2750 and 2250 < y - n < 2750
            points.append((x, y, z + 20 if building else z, 6 if building else 2))
    scale = 0.01
    xs, ys, zs = [p[0] for p in points], [p[1] for p in points], [p[2] for p in points]
    header = struct.pack(
        '<4sHHIHH8sBB32s32sHHHIIBHI5I3d3d6d', b'LASF', 0, 0, 0, 0, 0, b'\0' * 8, 1, 2, b'smoke test'.ljust(32, b'\0'),
        b'smoke test'.ljust(32, b'\0'), 1, 2026, 227, 227, 0, 0, 20, len(points), len(points), 0, 0, 0, 0,
        scale, scale, scale, 0.0, 0.0, 0.0, max(xs), min(xs), max(ys), min(ys), max(zs), min(zs))
    assert len(header) == 227
    with open(path, 'wb') as f:
        f.write(header)
        for x, y, z, cls in points:
            f.write(struct.pack('<iiiHBBbBH', round(x / scale), round(y / scale), round(z / scale), 100,
                                0b00001001, cls, 0, 0, 1))  # return 1 of 1


def wait_for_task(plugin, timeout=30):
    end = time.monotonic() + timeout
    while plugin.task is not None and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()
    return plugin.task is None


def main():
    print("QGIS", Qgis.version(), "Qt", QT_VERSION_STR, flush=True)
    check(plugin_manager_accepts_metadata(), "QGIS plugin manager would accept metadata.txt on this version")

    win = QMainWindow()
    canvas = QgsMapCanvas(win)
    canvas.resize(400, 400)
    iface = mock.MagicMock()
    iface.mainWindow.return_value = win
    iface.mapCanvas.return_value = canvas
    # Like the real Layers panel: a view with a model (older QGIS crashes without one)
    # The plugin only asks the Layers panel to refresh legend icons. A real, never-shown QgsLayerTreeView
    # here segfaulted QGIS 3.x (Qt5) intermittently: its header's resize timer drew legend icons outside
    # any QGIS window (QgsProject::createExpressionContextScope). A stand-in avoids that.
    iface.layerTreeView.return_value = mock.MagicMock()

    # Message bar notifications, recorded as (text, level, details)
    msgs = []

    durations = []  # seconds each message should stay (None: QGIS's usual; 0: until closed)

    def push_message(title, text, *rest):
        level = next(r for r in rest if isinstance(r, type(Qgis.MessageLevel.Info)))
        details = next((r for r in rest if isinstance(r, str)), None)
        after = rest[rest.index(level) + 1:]
        msgs.append((text, level, details))
        durations.append(after[0] if after else None)
    iface.messageBar.return_value.pushMessage.side_effect = push_message

    answers = []
    asked = []  # (args, kwargs) of each question

    def fake_ask_choice(*args, **kwargs):
        asked.append((args, kwargs))
        return answers.pop(0)
    mod.ask_choice = fake_ask_choice

    server = FakeServer()
    real_request_class = tasks.QgsBlockingNetworkRequest
    tasks.QgsBlockingNetworkRequest = server.request_class()
    tasks.RETRY_DELAYS = (0.1, 0.1)

    tmp = tempfile.mkdtemp()
    try:
        QSettings().setValue(mod.SETTINGS_KEY_DIR, tmp)
        plugin = pkg.classFactory(iface)
        plugin.initGui()
        dock = plugin.dock
        check(dock.leDownloadDir.text() == tmp, "download folder filled in from settings when the plugin starts")
        DTM = os.path.join(tmp, 'lidar_composite_dtm', '1m', '2022')  # where the default dataset now lives
        check(dock is not None and len(plugin.actions) == 1, "initGui creates dock + action")
        icon_image = plugin.actions[0].icon().pixmap(24, 24).toImage()
        check(not icon_image.isNull() and icon_image.pixelColor(12, 12).alpha() > 0,
              "toolbar icon (SVG) renders at 24 px")
        check(not dock.btnCancel.isEnabled(), "cancel disabled when idle")
        check(dock.chkLoadAfterDownload.isChecked(), "'Load tiles after downloading' is on for a new user")
        dock.chkLoadAfterDownload.setChecked(False)  # the checks below load tiles themselves

        dock.leDownloadDir.setText(tmp)
        dock.leDownloadDir.editingFinished.emit()
        check(QSettings().value(mod.SETTINGS_KEY_DIR) == tmp, "download folder saved when edited")
        dock.show()

        # --- selection helpers (they load the grid on demand)
        canvas.setDestinationCrs(QgsCoordinateReferenceSystem('EPSG:27700'))
        canvas.setExtent(QgsRectangle(417300, 127300, 417700, 127700))  # small view in the middle of SU12NE
        plugin.select_tiles_in_view()
        layer = plugin.grid.layer()
        check(layer is not None and layer.featureCount() == 36400 and dock.chkShowGrid.isChecked(),
              "'Tiles in map view' loads the grid (the whole OS 5 km grid, sea included)")
        check(plugin.grid.selected_tile_names() == ['SU12NE'], "'Tiles in map view' selects the tile under the view")
        check("1 to download, about 70 MB" in dock.lblTileCount.text(), "selection label shows size estimate")
        rules = style_rules(layer)
        check('Available' in rules and rules['Available'][1], "'Available' is the ELSE rule")
        outside = rules.get('No data', ('', False, None))[0]
        no_data = sum(1 for _ in layer.getFeatures(outside)) if outside else 0
        check(outside == "\"HAS_DATA\" = 'f'" and not QgsExpression(outside).hasParserError()
              and 20000 < no_data < 36400,
              f"squares without data styled separately ({no_data} of them)")
        check(layer.labelsEnabled() and layer.labeling().settings().fieldName == 'TILE_NAME'
              and layer.labeling().settings().scaleVisibility, "grid labelled with tile names (scale-limited)")
        plugin.grid.load()
        check(len(QgsProject.instance().mapLayersByName("OSGB Grid")) == 1, "reloading grid doesn't duplicate")

        canvas.setExtent(QgsRectangle(600000, 1000000, 605000, 1005000))  # the North Sea
        msgs.clear()
        plugin.select_tiles_in_view()
        check(plugin.grid.selected_tile_names() == [] and msgs and msgs[-1][1] == Qgis.MessageLevel.Warning,
              "no tiles with data in view -> warning")

        # A WGS84 point layer exercises the CRS transform: one point in SU12NE, one in SU12NW
        points = QgsVectorLayer('Point?crs=EPSG:4326', 'sites', 'memory')
        feats = []
        for x, y in ((-1.745, 51.05), (-1.80, 51.05)):
            f = QgsFeature()
            f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, y)))
            feats.append(f)
        points.dataProvider().addFeatures(feats)
        QgsProject.instance().addMapLayer(points)
        dock.cboLayer.setLayer(points)
        plugin.select_tiles_under_layer()
        check(sorted(plugin.grid.selected_tile_names()) == ['SU12NE', 'SU12NW'],
              "'Tiles under layer' selects intersecting tiles (with CRS transform)")
        picker_ids = [dock.cboLayer.layer(i).id() for i in range(dock.cboLayer.count())]
        check(layer.id() not in picker_ids and points.id() in picker_ids, "grid layer excluded from the layer picker")

        def select(*tiles):
            names = ",".join(f"'{t}'" for t in tiles)
            layer.selectByIds([f.id() for f in layer.getFeatures(f'"TILE_NAME" IN ({names})')])

        # --- download: one good tile, one retried then good, one with no data, one without the dataset (Scotland)
        server.responses = {
            'SU1525': [(200, tile_zip_bytes('SU12NE'))],               # SU12NE
            'SU1025': [(503, b''), (200, tile_zip_bytes('SU12NW'))],   # SU12NW: retried
            'SU1020': [(500, b'')],                                    # SU12SW: pretend no data
        }
        select('SU12NE', 'SU12NW', 'SU12SW', 'NT27NE')
        wait_for_search(plugin)
        check("1 with no data for this dataset" in dock.lblTileCount.text(),
              "the live check finds the square without the dataset")
        msgs.clear()
        plugin.download_selected_tiles()
        check(plugin.task is not None and dock.btnCancel.isEnabled()
              and not dock.btnDownloadTiles.isEnabled(), "download runs as background task; UI busy")
        check(wait_for_task(plugin), "task finishes")
        check(any("Skipping 1 tile(s) with no" in m[0] and "NT27NE" in (m[2] or '') for m in msgs),
              "a tile without the dataset is skipped and reported")
        check('NT2575' not in server.requests, "a tile without the dataset is not requested")
        text, level, details = msgs[-1]
        check("Downloaded 2 tile(s)" in text and "1 failed" in text and level == Qgis.MessageLevel.Warning
              and "SU12SW: no data available (HTTP 500)" in (details or ''), "message bar summary + failure details")
        check(server.requests.count('SU1025') == 2, "503 is retried")
        check(server.requests.count('SU1020') == 1, "500 (no data) is not retried")
        check(dock.progressBar.value() == 100 and dock.btnDownloadTiles.isEnabled(), "progress 100% and UI re-enabled")
        check(sorted(os.listdir(DTM)) == ['SU12NE', 'SU12NE.zip', 'SU12NW', 'SU12NW.zip'],
              "tiles saved under product/resolution/year (lidar_composite_dtm/1m/2022), no .part files left")
        labels = list(style_rules(layer))
        check('Downloaded' in labels, "grid restyled with downloaded tiles")

        # --- a service that doesn't answer: said once, plainly; its other tiles aren't each left to time out
        server.responses = {tile_id: [(None, b'')] for tile_id in ('SU2025', 'SU3025', 'SU1520', 'SU1020', 'TQ0090')}
        server.requests.clear()
        select('SU22NW', 'SU32NW', 'SU12SE', 'SU12SW', 'TQ09SW')  # more tiles than parallel downloads
        wait_for_search(plugin)
        msgs.clear()
        plugin.download_selected_tiles()
        wait_for_task(plugin)
        text, level, details = msgs[-1] if msgs else ('', None, None)
        check(text.startswith("5 tile(s) not downloaded. The Environment Agency's survey data service isn't responding")
              and "Try again later" in text and "failed" not in text and level == Qgis.MessageLevel.Warning
              and "network error" in (details or ''), "a service that doesn't answer: one plain message naming it")
        check(len(server.requests) < 15, f"... tiles after the service stopped answering aren't all tried "
                                         f"({len(server.requests)} requests for 5 tiles, not 15)")
        # In one download (no parallel timing): once two tiles got no answer, the next isn't tried at all
        server.requests.clear()
        direct = tasks.DownloadTilesTask(['SU22NW', 'SU32NW', 'TQ09SW'], os.path.join(tmp, 'direct'))
        direct.run()
        check(server.requests.count('TQ0090') == 0 and mod.api.NO_ANSWER in direct.results['SU22NW']
              and mod.api.NOT_RESPONDING in direct.results['SU32NW']
              and mod.api.NOT_RESPONDING in direct.results['TQ09SW'],
              "one tile unanswered: that tile fails; a second: the service counts as down and its next tile is skipped")
        server.responses = {'SU2025': [(None, b''), (200, tile_zip_bytes('SU22NW'))]}
        slow = tasks.DownloadTilesTask(['SU22NW'], os.path.join(tmp, 'direct2'))
        slow.run()
        check(slow.results['SU22NW'] is None, "a tile that answers on a second try downloads normally")
        direct = slow = None
        shutil.rmtree(os.path.join(tmp, 'direct'), ignore_errors=True)
        shutil.rmtree(os.path.join(tmp, 'direct2'), ignore_errors=True)
        server.search_status = None
        select('SU32NE')
        wait_for_search(plugin)
        check("Couldn't check what's available. The Environment Agency's survey data service isn't responding"
              in dock.lblAvailability.text(), "a search that gets no answer says which service isn't responding")
        server.search_status = 200

        # --- already downloaded: "Skip existing" reuses the files without requesting them
        server.requests.clear()
        select('SU12NE')
        check("1 already downloaded" in dock.lblTileCount.text(), "label counts already-downloaded tiles")
        answers.append(0)
        plugin.download_selected_tiles()
        wait_for_task(plugin)
        check(server.requests == [], "existing tiles reused with 'Skip existing'")

        # --- "Delete zip files after extracting"
        dock.chkDeleteZips.setChecked(True)
        check(QSettings().value(mod.SETTINGS_KEY_DELETE_ZIPS, False, type=bool), "delete-zips option saved")
        server.responses = {'SU2025': [(200, tile_zip_bytes('SU22NW'))]}  # SU22NW
        server.requests.clear()
        select('SU22NW')
        msgs.clear()
        plugin.download_selected_tiles()
        wait_for_task(plugin)
        check(not os.path.exists(os.path.join(DTM, 'SU22NW.zip'))
              and os.path.exists(os.path.join(DTM, 'SU22NW', 'SU22nw_DTM_1m.asc'))
              and "Downloaded 1 tile(s)" in msgs[-1][0], "zip deleted after successful extraction")
        select('SU22NW', 'SU12NE')
        check("2 already downloaded" in dock.lblTileCount.text(), "tile without zip still counts as downloaded")
        server.requests.clear()
        answers.append(0)  # Skip existing
        msgs.clear()
        plugin.download_selected_tiles()
        wait_for_task(plugin)
        check(server.requests == [] and msgs and "Downloaded 2 tile(s)" in msgs[-1][0],
              "re-using a tile whose zip was deleted works without downloading")
        check(not os.path.exists(os.path.join(DTM, 'SU12NE.zip'))
              and storage.find_raster_files(DTM, 'SU12NE'), "re-using a tile tidies away its old zip")
        dock.chkDeleteZips.setChecked(False)

        # --- "Delete extracted zips...": SU12NW.zip is fully extracted; SU32NW.zip was never extracted
        with open(os.path.join(DTM, 'SU32NW.zip'), 'wb') as f:
            f.write(tile_zip_bytes('SU32NW'))
        answers.append(None)  # Cancel
        asked.clear()
        plugin.tidy_up_zips()
        check(os.path.exists(os.path.join(DTM, 'SU12NW.zip')), "delete zips: cancel deletes nothing")
        args, kwargs = asked[-1]
        text, informative = args[2], args[3]
        check(kwargs.get('warning') is True and args[4] == ["Permanently delete 1 zip file(s)"],
              "delete zips: warning dialog, explicit button")
        check(tmp in text and "SU12NW.zip" in informative and "SU32NW.zip" in informative
              and "will NOT be touched" in informative,
              "delete zips: dialog lists folder, files to delete and files kept")
        check("permanently delete" in text.lower() and "permanently deleted" in informative
              and "download the tiles again" in informative, "delete zips: dialog warns it's permanent")
        answers.append(0)  # Delete
        msgs.clear()
        plugin.tidy_up_zips()
        check(not os.path.exists(os.path.join(DTM, 'SU12NW.zip')) and os.path.exists(os.path.join(DTM, 'SU32NW.zip'))
              and storage.find_raster_files(DTM, 'SU12NW') and msgs and msgs[-1][0].startswith("1 zip file(s)"),
              "delete zips removes only fully extracted zips")
        msgs.clear()
        plugin.tidy_up_zips()
        check(msgs and "No zip files to delete" in msgs[-1][0]
              and "1 zip file(s) aren't fully extracted" in msgs[-1][0],
              "delete zips with nothing to do says so")
        os.remove(os.path.join(DTM, 'SU32NW.zip'))

        # --- "Open" download folder
        with mock.patch.object(mod.QDesktopServices, 'openUrl') as open_url:
            plugin.open_download_dir()
            opened = open_url.call_args[0][0].toLocalFile() if open_url.call_args else ''
            check(open_url.call_count == 1 and os.path.normcase(os.path.normpath(opened))
                  == os.path.normcase(os.path.normpath(DTM)), "Open button opens the chosen dataset's folder")
            dock.leDownloadDir.setText(os.path.join(tmp, 'missing'))
            msgs.clear()
            plugin.open_download_dir()
            check(open_url.call_count == 1 and msgs and msgs[-1][1] == Qgis.MessageLevel.Warning,
                  "Open with a missing folder warns instead")
            dock.leDownloadDir.setText(tmp)

        # --- load (one mosaic per dataset group) + Create mosaic
        select('SU12NE', 'SU12NW', 'TQ09SW')
        msgs.clear()
        plugin.load_selected_tiles()
        check(msgs and "Loaded 1 layer" in msgs[-1][0] and "TQ09SW" in (msgs[-1][2] or ''),
              "load selected tiles (TQ09SW not downloaded yet)")
        mosaics = [lyr for lyr in QgsProject.instance().mapLayers().values()
                   if lyr.customProperty(mod.MOSAIC_PROPERTY) == 'England Composite DTM, 2022, 1 m']
        check(len(mosaics) == 1 and mosaics[0].name() == 'England Composite DTM, 2022, 1 m (2 tiles)'
              and 'mosaic_2_tiles' in mosaics[0].source()
              and os.sep + 'mosaics' + os.sep in mosaics[0].source(),
              "the group's tiles load as one mosaic (a VRT in the dataset's 'mosaics' folder)")
        shader = mosaics[0].renderer().shader() if mosaics and mosaics[0].renderer().type() == 'singlebandpseudocolor' \
            else None
        check(shader is not None and shader.minimumValue() < 101 and shader.maximumValue() > 247,
              "elevation colours stretched over the whole group (both tiles' min and max)")
        mosaics = shader = None
        vrt = os.path.join(tmp, "out.vrt")
        with mock.patch.object(mod.QFileDialog, "getSaveFileName", return_value=(vrt, "")):
            msgs.clear()
            plugin.create_mosaic_from_selected()
        check(os.path.exists(vrt) and msgs and "Created out.vrt" in msgs[-1][0], "Create mosaic: a VRT of the tiles")

        # --- v0.5: choosing a dataset
        def menu_entries(menu=None, path=()):
            """[(path of menu texts, action)] for every dataset in the (nested) dataset menu."""
            menu = menu or dock.btnDataset.menu()
            entries = []
            for action in menu.actions():
                if action.menu():
                    entries += menu_entries(action.menu(), path + (action.text(),))
                elif not action.isSeparator():
                    entries.append((path + (action.text(),), action))
            return entries

        def entry_for(dataset):
            return next(((p, a) for p, a in menu_entries() if a.data() == '|'.join(dataset)), (None, None))

        def choose(dataset):
            _, action = entry_for(dataset)
            if action is not None:
                action.trigger()
            return action is not None

        select('SU12NE', 'SU12NW')
        check(wait_for_search(plugin), "availability search runs after selecting tiles")
        nlp_path, _ = entry_for(NLP_DTM)
        default_path, _ = entry_for(DEFAULT)
        check(nlp_path == ('England National LIDAR Programme DTM  (2/2 tiles)', '2019, 1 m  (1/2 tiles)'),
              "product at the top level; resolution and year listed inside it")
        check(default_path == ('England Composite DTM  (2/2 tiles)', '2022, 1 m  (2/2 tiles)'),
              "several resolutions: each listed inside the product with its year and tile count")
        check(all(a.menu() or a.data() for a in dock.btnDataset.menu().actions() if not a.isSeparator()),
              "only products at the top level of the menu (a product with one choice is that choice)")
        check(not any('casi' in ' '.join(p).lower() for p, _ in menu_entries()), "unsupported products not listed")
        check("LIDAR dataset(s)" in dock.lblAvailability.text(), "availability label")
        searches = server.searches
        select('SU12NW', 'SU12NE')
        wait_for_search(plugin)
        check(server.searches == searches, "availability is cached per tile (no repeat search)")

        check(choose(NLP_DTM) >= 0 and plugin.dataset == mod.api.Dataset(*NLP_DTM), "choosing a dataset")
        check(QSettings().value(mod.SETTINGS_KEY_DATASET) == '|'.join(NLP_DTM), "dataset choice saved")
        check("1 with no data for this dataset" in dock.lblTileCount.text()
              and "1 to download, about 69 MB" in dock.lblTileCount.text(), "label reflects the chosen dataset")
        nlp_dir = os.path.join(tmp, 'national_lidar_programme_dtm', '1m', '2019')
        server.responses = {'SU1525': [(200, tile_zip_bytes('SU12NE'))]}
        server.urls.clear()
        msgs.clear()
        plugin.download_selected_tiles()
        wait_for_task(plugin)
        check(server.urls == [f'{BASE}/national_lidar_programme_dtm/2019/1/SU1525'],
              "downloads the chosen dataset's URL only")
        check(any("no England National LIDAR Programme DTM" in m[0] and "SU12NW" in (m[2] or '') for m in msgs),
              "tile without the dataset skipped and reported")
        check(storage.find_raster_files(nlp_dir, 'SU12NE') and storage.find_raster_files(DTM, 'SU12NE'),
              "dataset downloaded into its own folder; default folder untouched")
        downloaded = style_rules(layer).get('Downloaded', ('',))[0]
        check("'SU12NE'" in downloaded and "'SU12NW'" not in downloaded, "grid shading follows the chosen dataset")
        select('SU12NE')
        msgs.clear()
        plugin.load_selected_tiles()
        group = QgsProject.instance().layerTreeRoot().findGroup('England National LIDAR Programme DTM, 2019, 1 m')
        check(group is not None and len(group.findLayers()) == 1, "loaded tiles grouped under the dataset name")

        # The product itself ("newest for each tile"): each tile's newest survey, into that survey's folder
        select('SU12NE', 'SU12NW')
        wait_for_search(plugin)
        all_path, _ = entry_for(NLP_DTM_ALL)
        check(all_path is not None and all_path[1] == 'newest for each tile  (2/2 tiles)',
              "the product itself can be chosen ('newest for each tile')")
        texts = next(([sub.text() for sub in a.menu().actions()] for a in dock.btnDataset.menu().actions()
                      if a.menu() and a.text().startswith('England National LIDAR Programme DTM')), [])
        check(texts and texts[0].startswith('newest for each tile') and sum('newest' in t for t in texts) == 1,
              "'newest for each tile' listed first (not twice for a single resolution)")
        choose(NLP_DTM_ALL)
        check(dock.grpLegend.isVisible() and any(r.startswith('2019, 1 m') for r in dock.legendRows),
              "legend shows the product's downloaded surveys")
        main_layout = dock.layMain
        check(main_layout.indexOf(dock.grpLegend) > main_layout.indexOf(dock.grpUse),
              "legend is in its own section at the bottom of the panel")
        server.responses = {'SU1525': [(200, tile_zip_bytes('SU12NE'))], 'SU1025': [(200, tile_zip_bytes('SU12NW'))]}
        server.urls.clear()
        check("1 already downloaded" in dock.lblTileCount.text() and "1 to download" in dock.lblTileCount.text(),
              "label: SU12NE's newest survey (2019) already downloaded, SU12NW's (2020) not")
        answers.append(0)  # Skip existing
        plugin.download_selected_tiles()
        wait_for_task(plugin)
        check(server.urls == [f'{BASE}/national_lidar_programme_dtm/2020/1/SU1025'],
              "whole product: downloads each tile's newest survey (skipping what's already there)")
        check(storage.scan_downloaded_tiles(os.path.join(tmp, 'national_lidar_programme_dtm', '1m', '2020'))
              == ['SU12NW'],
              "each tile saved in its own survey-year folder")
        rules = style_rules(layer)
        colours = {rules[label][2] for label in ('2019, 1 m', '2020, 1 m') if label in rules}
        check('2019, 1 m' in rules and '2020, 1 m' in rules and len(colours) == 2,
              "grid shaded in a different colour per survey year")
        legend = dock.legendRows
        check('2019, 1 m (1 tile)' in legend and '2020, 1 m (1 tile)' in legend
              and 'No data (faint outline)' in legend,
              "legend lists each survey with its tile count")

        # Point clouds: nothing to mosaic before downloading; .laz found after download
        select('SU12NE')  # a completed download clears the selection
        wait_for_search(plugin)
        choose(NLP_CLOUD)
        msgs.clear()
        plugin.create_mosaic_from_selected()
        check(msgs and msgs[-1][1] == Qgis.MessageLevel.Warning
              and ("downloaded yet" in msgs[-1][0] or "3.32" in msgs[-1][0]),
              "Create mosaic on point clouds not downloaded yet: says why")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            zf.writestr('SU12ne.laz', b'not really a laz file')
        server.responses = {'SU1525': [(200, buf.getvalue())]}
        plugin.download_selected_tiles()
        wait_for_task(plugin)
        cloud_dir = os.path.join(tmp, 'national_lidar_programme_point_cloud', '1m', '2019')
        select('SU12NE')  # a completed download clears the selection
        check(storage.find_point_cloud_files(cloud_dir, 'SU12NE')
              and 'SU12NE' in storage.scan_downloaded_tiles(cloud_dir),
              "point cloud tile downloaded and recognised")
        msgs.clear()
        plugin.load_selected_tiles()
        check(msgs and msgs[-1][1] == Qgis.MessageLevel.Warning and "couldn't be opened" in msgs[-1][0],
              "an invalid point cloud file is reported, not a crash")

        # --- Aerial imagery: listed after the LiDAR, behind a separator
        wait_for_search(plugin)
        top_level = [('' if a.isSeparator() else a.text()) for a in dock.btnDataset.menu().actions()]
        separators = [i for i, t in enumerate(top_level) if t == '']
        rgbn_top = next(i for i, t in enumerate(top_level)
                        if t.startswith('England aerial photos, colour + near-infrared'))
        lidar_top = [i for i, t in enumerate(top_level) if 'LIDAR' in t]
        check(len(separators) == 1 and max(lidar_top) < separators[0] < rgbn_top,
              "aerial imagery listed after the LiDAR datasets, behind a separator")
        choose(RGBN)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:  # ECW in reality; any GDAL raster will do here
            zf.write(os.path.join(TEST_DIR, 'data', 'tenbytenraster.asc'), 'Ortho_RGBN_P1_20cm_res.asc')
            zf.write(os.path.join(TEST_DIR, 'data', 'tenbytenraster.prj'), 'Ortho_RGBN_P1_20cm_res.prj')
        server.responses = {'SU1525': [(200, buf.getvalue())]}
        server.urls.clear()
        plugin.download_selected_tiles()
        wait_for_task(plugin)
        check(server.urls == [f'{BASE}/vertical_aerial_photography_tiles_rgbn/2014/0.2/SU1525'],
              "imagery tile downloaded")
        select('SU12NE')
        msgs.clear()
        plugin.load_selected_tiles()
        group = QgsProject.instance().layerTreeRoot().findGroup(
            'England aerial photos, colour + near-infrared (RGBN), 2014, 0.2 m')
        check(group is not None and len(group.findLayers()) == 1 and "Loaded 1 layer" in msgs[-1][0],
              "imagery loads as a raster")
        imagery = group.findLayers()[0].layer() if group else None
        check(imagery is not None
              and any(r.min() == 0 and r.max() == 0 for r in imagery.dataProvider().userNoDataValues(1)),
              "imagery black borders set to transparent (no data = 0)")

        # --- Oblique photos: GPS-tagged JPEGs become direction arrows with photo map tips
        from osgeo import gdal
        photo_zip = io.BytesIO()
        jpg_path = os.path.join(tmp, 'Obliques_test.jpg')
        mem = gdal.GetDriverByName('MEM').Create('', 8, 8, 3)
        mem.SetMetadata({'EXIF_GPSLatitude': '(51) (3) (0)', 'EXIF_GPSLatitudeRef': 'N',
                         'EXIF_GPSLongitude': '(1) (45) (0)', 'EXIF_GPSLongitudeRef': 'W',
                         'EXIF_GPSImgDirection': '(120)', 'EXIF_GPSVersionID': '0x02 0x03 0x00 0x00'})
        gdal.GetDriverByName('JPEG').CreateCopy(jpg_path, mem, options=['WRITE_EXIF_METADATA=YES'])
        mem = None
        with zipfile.ZipFile(photo_zip, 'w') as zf:
            zf.write(jpg_path, 'Obliques_test.jpg')
        os.remove(jpg_path)
        choose(OBLIQUE)
        server.responses = {'SU1525': [(200, photo_zip.getvalue())]}
        plugin.download_selected_tiles()
        wait_for_task(plugin)
        select('SU12NE')
        msgs.clear()
        plugin.create_mosaic_from_selected()
        check(msgs and "photos" in msgs[-1][0], "Create mosaic refuses oblique photos")
        msgs.clear()
        plugin.load_selected_tiles()
        photos = QgsProject.instance().mapLayersByName('SU12NE photos')
        check(len(photos) == 1 and photos[0].featureCount() == 1 and '<img' in photos[0].mapTipTemplate()
              and "Loaded 1 layer" in msgs[-1][0], "oblique photos load as points with photo map tips")
        check(photos and abs(next(photos[0].getFeatures()).geometry().asPoint().y() - 51.05) < 0.001,
              "photo placed from its GPS tag")

        # With nothing selected, the Composite datasets are still offered
        layer.removeSelection()
        items = [a.data() for _, a in menu_entries()]
        check('|'.join(DEFAULT) in items and 'lidar_composite_first_return_dsm|2022|2' in items,
              "Composite datasets always listed, even with no selection")
        check(dock.btnDataset.fullText().startswith(mod.api.dataset_label(plugin.dataset)),
              "dataset button shows the chosen dataset")

        # Search failure: say so, and downloads still fall back to the built URL
        choose(DEFAULT)
        server.search_status = 503
        select('SU42NW')  # not searched before
        wait_for_search(plugin)
        check("Couldn't check" in dock.lblAvailability.text(), "search failure shown in the dock")
        server.search_status = 200
        check(plugin.dataset == mod.api.DEFAULT_DATASET, "back to the default dataset")

        # --- Processing Toolbox algorithm
        import processing
        from qgis.core import QgsProcessingException
        check(QgsApplication.processingRegistry().algorithmById('lidardownloader:downloadtiles') is not None,
              "Processing algorithm registered")
        proc_dir = tempfile.mkdtemp()
        server.responses = {'SU1525': [(200, tile_zip_bytes('SU12NE'))], 'SU1025': [(200, tile_zip_bytes('SU12NW'))]}
        server.urls.clear()
        params = {'EXTENT': '416000,419000,126000,129000 [EPSG:27700]', 'INPUT': points, 'PRODUCT': 0, 'YEAR': 'latest',
                  'RESOLUTION': '', 'FOLDER': proc_dir, 'OVERWRITE': False, 'DELETE_ZIPS': False, 'BUILD_VRT': True,
                  'OUTPUT_VRT': os.path.join(proc_dir, 'mosaic.vrt')}
        result = processing.run('lidardownloader:downloadtiles', params)
        check(result['DOWNLOADED'] == 2 and result['FAILED'] == 0 and os.path.exists(result['OUTPUT_VRT'])
              and os.path.normpath(result['FOLDER']) == os.path.join(proc_dir, 'lidar_composite_dtm', '1m', '2022'),
              "Processing: layer + extent -> 2 tiles downloaded into the survey folder, VRT built")
        check(sorted(server.urls) == [f'{BASE}/lidar_composite_dtm/2022/1/SU1025',
                                      f'{BASE}/lidar_composite_dtm/2022/1/SU1525'],
              "Processing: 'latest' + blank resolution resolves to the API's URLs")
        server.urls.clear()
        result = processing.run('lidardownloader:downloadtiles', params)
        check(server.urls == [] and result['DOWNLOADED'] == 2, "Processing: already-downloaded tiles reused")
        nlp = dict(params, PRODUCT=list(mod.api.PRODUCTS).index('national_lidar_programme_dtm'), BUILD_VRT=False)
        server.responses = {'SU1525': [(200, tile_zip_bytes('SU12NE'))], 'SU1025': [(200, tile_zip_bytes('SU12NW'))]}
        result = processing.run('lidardownloader:downloadtiles', nlp)
        nlp_root = os.path.join(proc_dir, 'national_lidar_programme_dtm', '1m')
        check(result['DOWNLOADED'] == 2 and result['UNAVAILABLE'] == 0
              and os.path.normpath(result['FOLDER']) == os.path.join(proc_dir, 'national_lidar_programme_dtm')
              and storage.scan_downloaded_tiles(os.path.join(nlp_root, '2019')) == ['SU12NE']
              and storage.scan_downloaded_tiles(os.path.join(nlp_root, '2020')) == ['SU12NW'],
              "Processing: tiles from different survey years go into their own year folders")
        try:
            scotland = dict(params, INPUT=None, EXTENT='600000,605000,1000000,1005000 [EPSG:27700]')  # sea
            processing.run('lidardownloader:downloadtiles', scotland)
            check(False, "Processing: area outside England raises an error")
        except QgsProcessingException as e:
            check('no tiles with LIDAR data' in str(e), "Processing: area without LIDAR raises an error")
        try:
            cloud_index = list(mod.api.PRODUCTS).index('lidar_point_cloud')
            processing.run('lidardownloader:downloadtiles', dict(params, PRODUCT=cloud_index))
            check(False, "Processing: unavailable dataset raises an error listing what's available")
        except QgsProcessingException as e:
            check('Available here' in str(e) and 'Composite DTM' in str(e),
                  "Processing: unavailable dataset raises an error listing what's available")
        shutil.rmtree(proc_dir, ignore_errors=True)

        # --- No duplicate layers; grid kept on top; dataset groups below it
        choose(DEFAULT)
        select('SU12NE')
        plugin.load_selected_tiles()
        before = len(QgsProject.instance().mapLayers())
        msgs.clear()
        plugin.load_selected_tiles()
        check(len(QgsProject.instance().mapLayers()) == before and msgs and "Loaded 0 layer" in msgs[-1][0]
              and "1 already in the project" in msgs[-1][0], "loading again doesn't add duplicate layers")
        vrt_again = os.path.join(tmp, 'dup.vrt')
        with mock.patch.object(mod.QFileDialog, "getSaveFileName", return_value=(vrt_again, "")):
            plugin.create_mosaic_from_selected()
            before = len(QgsProject.instance().mapLayers())
            msgs.clear()
            plugin.create_mosaic_from_selected()
        check(len(QgsProject.instance().mapLayers()) == before and "already loaded" in msgs[-1][0],
              "re-creating a VRT that's loaded refreshes it instead of adding a duplicate")
        root = QgsProject.instance().layerTreeRoot()
        other = QgsVectorLayer('Point?crs=EPSG:27700', 'someone else', 'memory')
        QgsProject.instance().addMapLayer(other, False)
        root.insertLayer(0, other)
        check(not plugin.grid.is_on_top(), "(another layer added above the grid)")
        dock.chkShowGrid.setChecked(False)
        dock.chkShowGrid.setChecked(True)
        layer = plugin.grid.layer()
        check(plugin.grid.is_on_top(), "ticking 'Show OSGB grid' puts the grid at the top of the Layers panel")
        group_node = root.findGroup('England Composite DTM, 2022, 1 m')
        for name in ('England National LIDAR Programme DTM, 2019, 1 m',
                     'England National LIDAR Programme DTM, 2020, 1 m'):
            if root.findGroup(name):
                root.removeChildNode(root.findGroup(name))
        select('SU12NE', 'SU12NW')  # both tiles, so the whole product is offered
        wait_for_search(plugin)
        check(choose(NLP_DTM_ALL), "(chose the whole NLP DTM product)")
        select('SU12NE', 'SU12NW')
        plugin.load_selected_tiles()
        new_groups = [root.findGroup(f'England National LIDAR Programme DTM, {y}, 1 m') for y in ('2019', '2020')]
        check(all(new_groups) and sorted(root.children().index(g) for g in new_groups) == [1, 2]
              and plugin.grid.is_on_top(), "loaded tiles grouped per survey, just below the grid")
        check(group_node is not None, "(default dataset group exists)")
        groups = (dock.grpTiles, dock.grpDataset, dock.grpDownload, dock.grpUse, dock.grpFolder, dock.grpLegend)
        sections = [dock.layMain.indexOf(g) for g in groups]
        check(sections == sorted(sections) and dock.layDownload.indexOf(dock.chkLoadAfterDownload) == 1
              and dock.layDataset.indexOf(dock.btnDataset) == 0,
              "panel in task order: area, data, download (with 'load them' under the button), use, folder, legend")
        check(not dock.progressBar.isVisible(), "no progress bar while nothing is downloading")
        QgsProject.instance().removeMapLayer(other.id())

        # --- Create mosaic as a GeoTIFF copy (in the background); crop to a layer (loading, saving, Processing)
        def wait_post(timeout=60):
            end = time.monotonic() + timeout
            while plugin.post_task is not None and time.monotonic() < end:
                app.processEvents()
                time.sleep(0.02)
            app.processEvents()

        choose(DEFAULT)
        select('SU12NE')
        copy_path = os.path.join(tmp, 'copy_test.tif')
        with mock.patch.object(mod.QFileDialog, "getSaveFileName", return_value=(copy_path, "")):
            msgs.clear()
            plugin.create_mosaic_from_selected()
            wait_post()
        tile_raster = storage.find_raster_files(DTM, 'SU12NE')[0]
        rl = mod.QgsRasterLayer(tile_raster, 'r')
        copied = mod.QgsRasterLayer(copy_path, 'copy')
        check(copied.isValid() and copied.width() == rl.width() and msgs and "Created copy_test.tif" in msgs[-1][0],
              "Create mosaic: a GeoTIFF copy, made in the background and loaded")
        copied = None
        # Saving over a GeoTIFF that's loaded (Windows locks it): asks, removes it from the project, replaces it
        answers.append(0)  # Replace
        asked.clear()
        with mock.patch.object(mod.QFileDialog, "getSaveFileName", return_value=(copy_path, "")):
            msgs.clear()
            plugin.create_mosaic_from_selected()
            wait_post()
        loaded_copies = [lyr for lyr in QgsProject.instance().mapLayers().values()
                         if os.path.normcase(lyr.source()) == os.path.normcase(copy_path)]
        check(asked and asked[-1][0][1] == 'Replace File?' and msgs and "Created copy_test.tif" in msgs[-1][0]
              and len(loaded_copies) == 1, "saving over a loaded GeoTIFF asks first, then replaces it")
        loaded_copies = None
        # A polygon layer drawn in the (test) raster's CRS, covering its left half
        e = rl.extent()
        half = QgsVectorLayer(f'Polygon?crs={rl.crs().authid() or "EPSG:4326"}', 'half', 'memory')
        f = QgsFeature()
        f.setGeometry(QgsGeometry.fromRect(QgsRectangle(e.xMinimum(), e.yMinimum(), e.center().x(), e.yMaximum())))
        half.dataProvider().addFeatures([f])
        QgsProject.instance().addMapLayer(half)
        dock.cboLayer.setLayer(half)
        dock.chkCrop.setChecked(True)
        check(dock.chkCrop.text() == 'Crop to: half', "'Crop to' names the layer it will use")
        clip_path = os.path.join(tmp, 'clip_test.tif')
        with mock.patch.object(mod.QFileDialog, "getSaveFileName", return_value=(clip_path, "")):
            plugin.create_mosaic_from_selected()
            wait_post()
        clipped = mod.QgsRasterLayer(clip_path, 'c')
        check(clipped.isValid() and clipped.width() == rl.width() // 2 and clipped.height() == rl.height(),
              "Create mosaic, cropped: a GeoTIFF cut to the polygon")
        cropped_vrt = os.path.join(tmp, 'cropped_test.vrt')
        with mock.patch.object(mod.QFileDialog, "getSaveFileName", return_value=(cropped_vrt, "")):
            plugin.create_mosaic_from_selected()
        cropped = mod.QgsRasterLayer(cropped_vrt, 'cv')
        with open(cropped_vrt, encoding='utf-8') as fh:
            vrt_text = fh.read()
        check(cropped.isValid() and cropped.width() == rl.width() // 2 and '<Cutline>' in vrt_text
              and os.path.getsize(cropped_vrt) < 20000, "Create mosaic, cropped: a small VRT cut to the polygon")
        clipped = cropped = None
        msgs.clear()
        plugin.load_selected_tiles()
        group_mosaic = [lyr for lyr in QgsProject.instance().mapLayers().values()
                        if lyr.customProperty(mod.MOSAIC_PROPERTY) == 'England Composite DTM, 2022, 1 m']
        check(len(group_mosaic) == 1 and group_mosaic[0].name().endswith('cropped to half)')
              and os.path.basename(group_mosaic[0].source()).startswith('cropped_')
              and group_mosaic[0].width() == rl.width() // 2 and "Cropped to 'half'" in msgs[-1][0],
              "Load selected with 'Crop to this layer': the group's mosaic is cropped")
        group_mosaic = None

        def crop_layer(name, *wkts):
            crop = QgsVectorLayer('Polygon?crs=EPSG:27700', name, 'memory')
            for wkt in wkts:
                feat = QgsFeature()
                feat.setGeometry(QgsGeometry.fromWkt(wkt))
                crop.dataProvider().addFeatures([feat])
            QgsProject.instance().addMapLayer(crop)
            dock.cboLayer.setLayer(crop)
            return crop

        def group_mosaic_layer():
            found = [lyr for lyr in QgsProject.instance().mapLayers().values()
                     if lyr.customProperty(mod.MOSAIC_PROPERTY) == 'England Composite DTM, 2022, 1 m']
            return found[0] if len(found) == 1 else None

        from osgeo import gdal as _gdal
        # Two sites 100 km apart: the crop is trimmed to the tiles, not the box around both sites
        x0, y0 = tile_origin('SU12NE')
        far = crop_layer('far sites',
                         f'POLYGON(({x0} {y0}, {x0 + 2500} {y0}, {x0 + 2500} {y0 + 5000}, '
                         f'{x0} {y0 + 5000}, {x0} {y0}))',
                         'POLYGON((517000 227000, 518000 227000, 518000 228000, 517000 227000))')
        plugin.load_selected_tiles()
        layer_now = group_mosaic_layer()
        check(layer_now is not None and layer_now.width() == 25 and layer_now.height() == 50,
              "a crop layer with far-apart sites: the crop is trimmed to the tiles (not a vast, empty grid)")
        # A drawn polygon that crosses itself is repaired, not refused
        bowtie = crop_layer('bow tie', f'POLYGON(({x0} {y0}, {x0 + 5000} {y0 + 5000}, {x0 + 5000} {y0}, '
                                       f'{x0} {y0 + 5000}, {x0} {y0}))')
        msgs.clear()
        plugin.load_selected_tiles()
        check(msgs and "Cropped to 'bow tie'" in msgs[-1][0] and group_mosaic_layer() is not None,
              "a self-crossing polygon is repaired and used for the crop")
        # An area away from the tiles: they load uncropped, and the message says why
        away = crop_layer('away', 'POLYGON((517000 227000, 518000 227000, 518000 228000, 517000 227000))')
        msgs.clear()
        plugin.load_selected_tiles()
        layer_now = group_mosaic_layer()
        check(msgs and "'away' doesn't overlap these tiles" in msgs[-1][0] and layer_now is not None
              and 'cropped' not in layer_now.name(),
              "a crop area away from the tiles: loaded uncropped, with the reason")
        layer_now = None
        QgsProject.instance().removeMapLayers([far.id(), bowtie.id(), away.id()])
        far = bowtie = away = None
        dock.cboLayer.setLayer(half)
        # Aerial photos cropped: real black pixels stay black (no data), not turned into 1s that show
        photo = os.path.join(tmp, 'photo.tif')
        pds = _gdal.GetDriverByName('GTiff').Create(photo, 4, 4, 3, _gdal.GDT_Byte)
        pds.SetGeoTransform((x0, 1, 0, y0 + 4, 0, -1))
        pds.SetProjection(QgsCoordinateReferenceSystem('EPSG:27700').toWkt())
        for band in range(1, 4):
            pds.GetRasterBand(band).Fill(120)
            pds.GetRasterBand(band).WriteRaster(1, 1, 1, 1, bytes(1))
        pds = None
        cut = mod.postprocess.cutline_from_geometries(
            [QgsGeometry.fromRect(QgsRectangle(x0, y0, x0 + 4, y0 + 4))], QgsCoordinateReferenceSystem('EPSG:27700'),
            photo)
        cropped_photo = mod.postprocess.crop_vrt(photo, os.path.join(tmp, 'photo_crop.vrt'), cut)
        pds = _gdal.Open(cropped_photo)
        black = pds.GetRasterBand(1).ReadAsArray(1, 1, 1, 1)[0][0]
        pds = None
        check(black == 0, f"cropped aerial photos keep black as no data (value {black})")
        # Same via the Processing tool: area = the half-tile polygon; a cropped VRT and a clipped GeoTIFF
        import processing
        proc_dir = tempfile.mkdtemp()
        server.responses = {'SU1525': [(200, tile_zip_bytes('SU12NE'))]}
        clip_out, vrt_out = os.path.join(proc_dir, 'clip.tif'), os.path.join(proc_dir, 'site.vrt')
        result = processing.run('lidardownloader:downloadtiles', {
            'INPUT': half, 'PRODUCT': 0, 'YEAR': 'latest', 'RESOLUTION': '', 'FOLDER': proc_dir, 'BUILD_VRT': True,
            'OUTPUT_VRT': vrt_out, 'CROP_VRT': True, 'OUTPUT_CLIPPED': clip_out})
        clipped_p, vrt_p = mod.QgsRasterLayer(clip_out, 'c'), mod.QgsRasterLayer(vrt_out, 'v')
        check(result['OUTPUT_CLIPPED'] == clip_out and result['OUTPUT_VRT'] == vrt_out
              and clipped_p.width() == 25 and clipped_p.height() == 50 and vrt_p.width() == 25,
              "Processing: download and crop in one go (a cropped VRT, and a clipped GeoTIFF copy)")
        clipped_p = vrt_p = None
        shutil.rmtree(proc_dir, ignore_errors=True)

        dock.cboLayer.setLayer(points)
        msgs.clear()
        plugin.load_selected_tiles()
        check(msgs and msgs[-1][1] == Qgis.MessageLevel.Warning and "Loaded uncropped: 'sites' has no polygons"
              in msgs[-1][0], "'Crop to' a layer without polygons never stops loading: loads uncropped, says why")
        dock.cboLayer.setLayer(half)

        # --- "Load tiles after downloading": with 'Crop to this layer', download and crop in one go
        dock.chkLoadAfterDownload.setChecked(True)
        check(QSettings().value(mod.SETTINGS_KEY_LOAD_AFTER, False, type=bool), "load-after-download option saved")
        root = QgsProject.instance().layerTreeRoot()
        if root.findGroup('England Composite DTM, 2022, 1 m'):
            root.removeChildNode(root.findGroup('England Composite DTM, 2022, 1 m'))
        server.responses = {'SU1525': [(200, tile_zip_bytes('SU12NE'))]}
        select('SU12NE')
        answers.append(1)  # Download again
        msgs.clear()
        plugin.download_selected_tiles()
        wait_for_task(plugin)
        group_mosaic = [lyr for lyr in QgsProject.instance().mapLayers().values()
                        if lyr.customProperty(mod.MOSAIC_PROPERTY) == 'England Composite DTM, 2022, 1 m']
        check(len(group_mosaic) == 1 and group_mosaic[0].name().endswith('cropped to half)') and msgs
              and "Loaded 1 layer" in msgs[-1][0], "download and crop in one go: loads cropped after downloading")
        group_mosaic = None
        dock.chkCrop.setChecked(False)
        QgsProject.instance().removeMapLayer(half.id())
        half = f = rl = None
        server.responses = {'TQ0090': [(200, tile_zip_bytes('TQ09SW'))]}
        select('TQ09SW')
        msgs.clear()
        plugin.download_selected_tiles()
        wait_for_task(plugin)
        group_mosaic = [lyr for lyr in QgsProject.instance().mapLayers().values()
                        if lyr.customProperty(mod.MOSAIC_PROPERTY) == 'England Composite DTM, 2022, 1 m']
        check(len(group_mosaic) == 1 and group_mosaic[0].name().endswith('(2 tiles)')
              and 'mosaic_2_tiles' in group_mosaic[0].source()
              and msgs and "Loaded 1 layer" in msgs[-1][0],
              "tiles load automatically after downloading, into the group's (uncropped) mosaic")
        group_mosaic = None
        dock.chkLoadAfterDownload.setChecked(False)
        before = len(QgsProject.instance().mapLayers())
        select('SU12NE', 'TQ09SW')
        plugin.load_selected_tiles()
        check(len(QgsProject.instance().mapLayers()) == before, "reloading the same tiles adds nothing")

        # --- v0.7: pick tiles on the map, draw an area, coverage preview, survey dates
        from qgis.gui import QgsMapMouseEvent
        from qgis.PyQt.QtCore import QEvent, QPoint, QPointF, Qt
        choose(DEFAULT)
        canvas.setExtent(QgsRectangle(410000, 120000, 425000, 135000))  # SU12 and its neighbours
        handlers = {'press': (QEvent.Type.MouseButtonPress, 'canvasPressEvent'),
                    'move': (QEvent.Type.MouseMove, 'canvasMoveEvent'),
                    'release': (QEvent.Type.MouseButtonRelease, 'canvasReleaseEvent'),
                    'double': (QEvent.Type.MouseButtonDblClick, 'canvasDoubleClickEvent')}

        def mouse(tool, kind, x, y):
            """Send a left-button mouse event at map position (x, y) to a map tool."""
            p = canvas.getCoordinateTransform().transform(x, y)
            event_type, handler = handlers[kind]
            button = Qt.MouseButton.LeftButton
            try:
                event = QgsMapMouseEvent(canvas, event_type, QPoint(round(p.x()), round(p.y())), button, button)
            except TypeError:  # QPointF positions on newer QGIS
                event = QgsMapMouseEvent(canvas, event_type, QPointF(round(p.x()), round(p.y())), button, button)
            getattr(tool, handler)(event)

        def click(tool, x, y):
            mouse(tool, 'press', x, y)
            mouse(tool, 'release', x, y)

        def drag(tool, x0, y0, x1, y1):
            mouse(tool, 'press', x0, y0)
            mouse(tool, 'move', (x0 + x1) / 2, (y0 + y1) / 2)
            mouse(tool, 'move', x1, y1)
            mouse(tool, 'release', x1, y1)

        dock.btnPickTiles.click()
        check(canvas.mapTool() is plugin.pick_tool and dock.btnPickTiles.isChecked(),
              "'Pick on map' activates the tool")
        plugin.grid.clear_selection()
        click(plugin.pick_tool, 417500, 127500)
        check(plugin.grid.selected_tile_names() == ['SU12NE'], "clicking a tile selects it")
        click(plugin.pick_tool, 412500, 127500)
        check(sorted(plugin.grid.selected_tile_names()) == ['SU12NE', 'SU12NW'], "clicking another tile adds it")
        click(plugin.pick_tool, 417500, 127500)
        check(plugin.grid.selected_tile_names() == ['SU12NW'], "clicking a selected tile removes it")
        drag(plugin.pick_tool, 412000, 122000, 418000, 128000)
        check(sorted(plugin.grid.selected_tile_names()) == ['SU12NE', 'SU12NW', 'SU12SE', 'SU12SW'],
              "dragging a box adds the tiles under it")

        dock.btnDrawArea.click()
        check(canvas.mapTool() is plugin.draw_tool and dock.btnDrawArea.isChecked()
              and not dock.btnPickTiles.isChecked(), "'Draw area' swaps tools; the buttons follow")
        drag(plugin.draw_tool, 416000, 126000, 419000, 129000)
        areas = QgsProject.instance().mapLayersByName(mod.AREA_LAYER_NAME)
        check(plugin.grid.selected_tile_names() == ['SU12NE'], "a drawn rectangle selects the tiles under it")
        check(len(areas) == 1 and areas[0].featureCount() == 1 and dock.cboLayer.currentLayer() is areas[0],
              "the drawn area is kept as a layer, chosen for 'Tiles under layer' / 'Crop to'")
        check(dock.chkCrop.isChecked() and dock.chkCrop.text() == 'Crop to: Drawn area',
              "drawing an area ticks 'Crop to', naming the drawn area")
        # A triangle inside SU12NW. Zoomed in, so clicks land within a few metres of these points.
        canvas.setExtent(QgsRectangle(411000, 124000, 416000, 129000))
        for x, y in ((413000, 126000), (414000, 126000)):
            click(plugin.draw_tool, x, y)
        click(plugin.draw_tool, 414000, 127000)
        mouse(plugin.draw_tool, 'double', 414000, 127000)
        areas = QgsProject.instance().mapLayersByName(mod.AREA_LAYER_NAME)
        feature = next(areas[0].getFeatures()) if areas else None
        check(plugin.grid.selected_tile_names() == ['SU12NW'], "a drawn polygon selects the tiles under it")
        check(len(areas) == 1 and areas[0].featureCount() == 1
              and abs(feature['area_ha'] - feature.geometry().area() / 1e4) < 0.01,
              "drawing again replaces the drawn area (one layer, one area, its size recorded)")
        feature = None
        dock.btnDrawArea.click()
        check(canvas.mapTool() is not plugin.draw_tool and not dock.btnDrawArea.isChecked(),
              "unticking 'Draw area' puts the tool away")
        areas = None
        dock.btnClearSelection.click()
        check(plugin.grid.selected_tile_names() == []
              and not QgsProject.instance().mapLayersByName(mod.AREA_LAYER_NAME),
              "Clear: clears the selection and removes the drawn area")
        check(not dock.chkCrop.isChecked(), "... and unticks 'Crop to' (nothing left to crop to)")
        canvas.setExtent(QgsRectangle(410000, 120000, 425000, 135000))

        # Coverage: tiles in view that have the chosen dataset but aren't downloaded
        searches = server.searches
        dock.chkCoverage.setChecked(True)
        check(wait_for_coverage(plugin) and server.searches > searches, "coverage check runs for the tiles in view")
        searches = server.searches
        coverage_rule = style_rules(layer).get('Available to download (in view)', ('',))[0]
        check("'SU12SE'" in coverage_rule and "'SU12SW'" in coverage_rule and "'SU12NE'" not in coverage_rule
              and "'SU13SW'" not in coverage_rule, "tiles with the dataset, not yet downloaded, are shaded")
        check(any(r.startswith('Available to download (in view) (') for r in dock.legendRows),
              "coverage listed in the legend")
        note = dock.lblCoverageNote
        shaded = coverage_rule.count("'") // 2
        check(not note.isHidden() and note.text().startswith(f"{shaded:,} tile{'s' if shaded != 1 else ''} in view "
                                                             "shaded blue"),
              f"the note under the tick box says how many tiles are shaded ({note.text()!r})")
        canvas.setExtent(QgsRectangle(0, 0, 700000, 1300000))
        wait_for_coverage(plugin)
        check('Available to download (in view)' not in style_rules(layer) and note.text().startswith('Zoom in')
              and '1,600 tiles' in note.text() and not any(r.startswith('Zoom in') for r in dock.legendRows),
              "zoomed out too far: the note under the tick box asks to zoom in (not the legend)")
        canvas.setExtent(QgsRectangle(410000, 120000, 425000, 135000))
        wait_for_coverage(plugin)
        check(server.searches == searches and 'Available to download (in view)' in style_rules(layer),
              "coverage comes back from the cache without searching again")
        plugin.coverage_error = "The Environment Agency's survey data service isn't responding at the moment."
        plugin.refresh_grid_style()
        check(note.text().startswith("Couldn't check all the tiles in view: The Environment Agency"),
              "a failed coverage check is said under the tick box")
        plugin.coverage_timer.start()
        wait_for_coverage(plugin)
        check(plugin.coverage_error == '' and 'shaded blue' in note.text(), "... and cleared by the next check")
        dock.chkCoverage.setChecked(False)
        check('Available to download (in view)' not in style_rules(layer) and note.isHidden()
              and not QSettings().value(mod.SETTINGS_KEY_COVERAGE, type=bool), "unticking removes the coverage")

        # Survey dates from the Composite DTM's metadata
        write_survey_metadata(os.path.join(DTM, 'SU12NE', 'SU12ne_DTM_1m_Metadata.gpkg'),
                              [('F_DTM_P_10781', 2018, 415000, 125000, 417500, 130000),
                               ('F0211608', 2020, 417500, 125000, 420000, 130000)])
        select('SU12NE', 'SU12NW')
        msgs.clear()
        plugin.show_survey_dates()
        surveys = QgsProject.instance().mapLayersByName('Survey dates (1 tile)')
        check(len(surveys) == 1 and surveys[0].featureCount() == 2, "survey dates: one layer of the tile's surveys")
        survey = surveys[0] if surveys else None
        values = sorted((f['year'], f['survey'], str(f['flown_from'].toString('yyyy-MM-dd')))
                        for f in survey.getFeatures())
        check(values == [(2018, 'F_DTM_P_10781', '2018-01-29'), (2020, 'F0211608', '2020-01-29')],
              "survey dates: survey, year and flown dates read from the metadata")
        check(survey.renderer().type() == 'categorizedSymbol'
              and [c.label() for c in survey.renderer().categories()] == ['2018', '2020']
              and survey.labelsEnabled() and 'flown_from' in survey.mapTipTemplate(),
              "survey dates coloured and labelled by year, with flown dates in the map tip")
        check(msgs and "1 tile(s) have no survey metadata" in msgs[-1][0], "tiles without metadata are reported")
        plugin.show_survey_dates()
        check(len(QgsProject.instance().mapLayersByName('Survey dates (1 tile)')) == 1, "survey dates not loaded twice")
        select('SU22NW')
        msgs.clear()
        plugin.show_survey_dates()
        check(msgs and msgs[-1][1] == Qgis.MessageLevel.Warning and "Composite" in msgs[-1][0],
              "no metadata: says where survey dates come from")
        QgsProject.instance().removeMapLayer(survey.id())
        survey = surveys = None

        # --- v0.7.1: go to a grid reference, postcode or place
        def go(text):
            plugin.grid.clear_selection()
            dock.leGoTo.setText(text)
            msgs.clear()
            dock.btnGoTo.click()
            wait_for_geocode(plugin)
            return sorted(plugin.grid.selected_tile_names())

        check(go('SU 14 27') == ['SU12NW'] and msgs and 'added 1 tile' in msgs[-1][0]
              and canvas.extent().contains(QgsRectangle(414000, 127000, 415000, 128000)),
              "go to a grid reference: zooms there and adds its tile")
        check(go('su12') == ['SU12NE', 'SU12NW', 'SU12SE', 'SU12SW'], "go to a 10 km square: adds its four tiles")
        check(go('SU12NE') == ['SU12NE'], "go to a tile name")
        check(go('SU') == [] and msgs and 'none were selected' in msgs[-1][0],
              "go to a 100 km square: zooms, but doesn't select 400 tiles")
        check(go('600000, 1000000') == [] and msgs and msgs[-1][1] == Qgis.MessageLevel.Warning,
              "go to somewhere without data (the North Sea): says so")
        server.geocode = {'SP1': (200, {'status': 200, 'result': {
            'postcode': 'SP1 3QQ', 'eastings': 417500, 'northings': 127500, 'admin_district': 'Wiltshire'}})}
        check(go('sp1 3qq') == ['SU12NE'] and 'postcodes/SP1' in server.geocode_urls[-1],
              "go to a postcode (looked up in the background)")
        server.geocode = {'places': (200, {'status': 200, 'result': [
            {'name_1': 'Salisbury', 'local_type': 'City', 'country': 'England', 'county_unitary': 'Wiltshire',
             'eastings': 412500, 'northings': 127500},
            {'name_1': 'Salisbury Plain', 'local_type': 'Other', 'country': 'England', 'county_unitary': 'Wiltshire',
             'eastings': 417000, 'northings': 125000, 'min_eastings': 416000, 'min_northings': 122000,
             'max_eastings': 418000, 'max_northings': 128000}]})}
        with mock.patch.object(mod, 'choose_place', return_value=1) as chooser:
            chosen = go('Salisbury')
        check(chooser.call_args and chooser.call_args[0][1][1] == 'Salisbury Plain (Other, Wiltshire)'
              and chosen == ['SU12NE', 'SU12SE'], "several places: offers a list; the chosen place's area is selected")
        server.geocode = {'places': (200, {'status': 200, 'result': []})}
        check(go('Nowhere') == [] and msgs and 'Nothing found' in msgs[-1][0], "place not found: says so")
        server.geocode = {'places': (503, {})}
        check(go('Salisbury') == [] and msgs and "Couldn't look up" in msgs[-1][0] and dock.btnGoTo.isEnabled(),
              "lookup failure: warns, Go works again")
        dock.leGoTo.clear()

        # Dataset descriptions
        select('SU12NE')  # which has National LIDAR Programme data
        wait_for_search(plugin)
        check(choose(NLP_DTM), "NLP DTM listed for SU12NE")
        nlp_text = dock.lblDatasetInfo.text()
        choose(DEFAULT)
        check('National LIDAR Programme' in nlp_text and 'bare earth' in dock.lblDatasetInfo.text(),
              "the chosen dataset is described under the menu")

        # Attribution and elevation surfaces
        select('SU12NE', 'SU12NW')
        plugin.load_selected_tiles()
        colours = [lyr for lyr in QgsProject.instance().mapLayers().values()
                   if lyr.customProperty(mod.MOSAIC_PROPERTY) == 'England Composite DTM, 2022, 1 m']
        rights = colours[0].metadata().rights() if colours else []
        check(rights == ['© Environment Agency copyright and/or database right 2022. All rights reserved.']
              and 'Open Government Licence' in colours[0].metadata().licenses()[0]
              and 'bare earth' in colours[0].metadata().abstract(),
              "layers carry the Environment Agency attribution and the Open Government Licence")
        if hasattr(colours[0], 'elevationProperties') and hasattr(colours[0].elevationProperties(), 'setEnabled'):
            check(colours[0].elevationProperties().isEnabled(),
                  "elevation data is set up for the Elevation Profile tool")
        colours = None

        # Offer to switch the map to British National Grid (once)
        QgsProject.instance().setCrs(QgsCoordinateReferenceSystem('EPSG:3857'))
        plugin.bng_offered = False
        bar = iface.messageBar.return_value
        bar.pushWidget.reset_mock()
        plugin.offer_bng()
        plugin.offer_bng()
        check(bar.pushWidget.call_count == 1 and plugin.bng_item is not None, "offers British National Grid once")
        plugin.set_project_bng()
        app.processEvents()
        check(QgsProject.instance().crs().authid() == 'EPSG:27700' and bar.popWidget.called,
              "the offer switches the project to EPSG:27700 and closes")

        # --- v0.9.0: point clouds as one virtual point cloud; saved as one, or cropped (QGIS 3.32+ with PDAL)
        if mod.pointclouds.available():
            cloud_dataset = mod.api.Dataset('lidar_point_cloud', '2015', 'NaN')
            for tile in ('SU12NE', 'SU12NW'):
                folder = os.path.join(tmp, 'lidar_point_cloud', '2015', tile)
                os.makedirs(folder, exist_ok=True)
                write_las(os.path.join(folder, f'{tile}_P_1_20150101.las'), tile)
            plugin.set_dataset(cloud_dataset)
            app.processEvents()
            select('SU12NE')
            plugin.load_selected_tiles()
            clouds = [lyr for lyr in QgsProject.instance().mapLayers().values()
                      if isinstance(lyr, mod.QgsPointCloudLayer) and lyr.name().startswith('Point cloud')]
            check(len(clouds) == 1 and clouds[0].providerType() == 'vpc' and clouds[0].name() == 'Point cloud (1 tile)'
                  and clouds[0].crs().authid() == 'EPSG:27700' and clouds[0].pointCount() == 10000
                  and clouds[0].renderer().type() == 'classified',
                  "point clouds load as one virtual point cloud, in British National Grid, coloured by class")
            select('SU12NE', 'SU12NW')
            plugin.load_selected_tiles()
            clouds = [lyr for lyr in QgsProject.instance().mapLayers().values()
                      if isinstance(lyr, mod.QgsPointCloudLayer) and lyr.name().startswith('Point cloud')]
            check(len(clouds) == 1 and clouds[0].name() == 'Point cloud (2 tiles)' and clouds[0].pointCount() == 20000,
                  "loading more tiles extends the same virtual point cloud")
            clouds = None

            vpc_out = os.path.join(tmp, 'saved_cloud.vpc')
            with mock.patch.object(mod.QFileDialog, "getSaveFileName", return_value=(vpc_out, "")):
                msgs.clear()
                plugin.create_mosaic_from_selected()
            saved = QgsProject.instance().mapLayersByName('saved_cloud')
            check(os.path.exists(vpc_out) and len(saved) == 1 and saved[0].pointCount() == 20000
                  and msgs and "Created saved_cloud.vpc" in msgs[-1][0],
                  "Create mosaic on point clouds: a virtual point cloud")
            QgsProject.instance().removeMapLayer(saved[0].id())
            saved = None
            patch = QgsVectorLayer('Polygon?crs=EPSG:27700', 'patch', 'memory')
            feature = QgsFeature()
            feature.setGeometry(QgsGeometry.fromWkt('POLYGON((416000 126000, 418000 126000, 418000 128000, '
                                                    '416000 128000, 416000 126000))'))
            patch.dataProvider().addFeatures([feature])
            QgsProject.instance().addMapLayer(patch)
            dock.cboLayer.setLayer(patch)
            dock.chkCrop.setChecked(True)
            copc_out = os.path.join(tmp, 'cloud_crop.copc.laz')
            with mock.patch.object(mod.QFileDialog, "getSaveFileName", return_value=(copc_out, "")):
                plugin.create_mosaic_from_selected()
                wait_post(120)
            clipped = mod.QgsPointCloudLayer(copc_out, 'clip', 'copc')
            check(clipped.isValid() and clipped.crs().authid() == 'EPSG:27700' and 0 < clipped.pointCount() < 20000
                  and QgsProject.instance().mapLayersByName('cloud_crop'),
                  "Create mosaic on point clouds, cropped: a COPC file in British National Grid, loaded")
            check(not os.path.exists(copc_out[:-len('.copc.laz')] + '.copc.copc.laz'),
                  "... loaded as COPC, so QGIS doesn't make a second copy of it")
            clipped = None
            dock.chkCrop.setChecked(False)
            for crop_layer in QgsProject.instance().mapLayersByName('cloud_crop'):
                QgsProject.instance().removeMapLayer(crop_layer.id())
            QgsProject.instance().removeMapLayer(patch.id())
            patch = feature = None  # QGIS deleted the layer: keep no wrapper
            plugin.set_dataset(mod.api.DEFAULT_DATASET)
            app.processEvents()
        else:
            print("SKIP point clouds: no PDAL tools (or pdal_wrench) in this QGIS:", QgsApplication.libexecPath(),
                  flush=True)

        # --- v0.9.1: river bathymetry
        # A river 100 m wide (x 417000-417100) with its bed at 0 m, as 0.5 m grids with no .prj (like the real ones)
        river_dir = os.path.join(tmp, 'bathymetry_riverine_multibeam', '0.5m', '2014')
        os.makedirs(river_dir, exist_ok=True)
        rows = ['ncols 400', 'nrows 400', 'xllcorner 416900', 'yllcorner 127000', 'cellsize 0.5', 'NODATA_value -9999']
        line = ' '.join('0.0' if 417000 <= 416900 + (i + 0.5) * 0.5 < 417100 else '-9999' for i in range(400))
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            zf.writestr('su1627_20140101mb.asc', '\n'.join(rows + [line] * 400) + '\n')
        with open(storage.zip_path(river_dir, 'SU12NE'), 'wb') as fh:
            fh.write(buf.getvalue())
        storage.extract_tile(river_dir, 'SU12NE')
        check(os.path.exists(os.path.join(river_dir, 'SU12NE', 'su1627_20140101mb.prj')),
              "bathymetry grids get a British National Grid .prj when extracted")

        # --- v0.9.2: parallel downloads, the disk space check, My downloads, SurfZone
        choose(DEFAULT)
        server.responses = {'SU1525': [(200, tile_zip_bytes('SU12NE'))], 'SU1025': [(200, tile_zip_bytes('SU12NW'))],
                            'SU2025': [(200, tile_zip_bytes('SU22NW'))]}
        server.requests.clear()
        server.delay, server.max_active = 0.3, 0
        select('SU12NE', 'SU12NW', 'SU22NW')
        answers.append(1)  # Download all again
        msgs.clear()
        plugin.download_selected_tiles()
        check(isinstance(plugin.task, mod.ParallelDownloadTask), "several tiles download in parallel")
        check(wait_for_task(plugin) and msgs and "Downloaded 3 tile(s)" in msgs[-1][0]
              and sorted(server.requests) == ['SU1025', 'SU1525', 'SU2025'] and server.max_active >= 2,
              f"tiles downloaded at the same time ({server.max_active} at once), all reported")
        server.delay = 0

        select('SU12NE', 'SU12NW', 'SU22NW')  # a finished download clears the selection
        with mock.patch.object(mod.storage, 'free_space', return_value=1000):
            answers.append(1)  # Download all again
            answers.append(None)  # ... but cancel at the disk space warning
            asked.clear()
            plugin.download_selected_tiles()
        check(plugin.task is None and asked and asked[-1][0][1] == 'Not Enough Disk Space?'
              and asked[-1][1].get('warning'), "not enough disk space: warns first (Cancel stops)")

        # My downloads: a throwaway survey, loaded in the project, then deleted
        spare = os.path.join(tmp, 'lidar_tiles_dsm', '1m', '2011', 'SU12NE')
        os.makedirs(spare)
        shutil.copy(storage.find_raster_files(DTM, 'SU12NE')[0], os.path.join(spare, 'su1525_DSM_1m.asc'))
        spare_layer = mod.QgsRasterLayer(os.path.join(spare, 'su1525_DSM_1m.asc'), 'spare')
        QgsProject.instance().addMapLayer(spare_layer)
        spare_id = spare_layer.id()
        spare_layer = None
        plugin.show_downloads()
        dialog = plugin.downloads_dialog
        labels = [dialog.tree.topLevelItem(i).text(0) for i in range(dialog.tree.topLevelItemCount())]
        dtm_row = (labels.index('England Composite DTM, 2022, 1 m')
                   if 'England Composite DTM, 2022, 1 m' in labels else -1)
        spare_row = labels.index('England DSM (individual surveys), 2011, 1 m') \
            if 'England DSM (individual surveys), 2011, 1 m' in labels else -1
        check(dialog.isVisible() and dtm_row >= 0 and spare_row >= 0
              and dialog.tree.topLevelItem(dtm_row).text(1) == str(len(plugin.download_rows[dtm_row][2]))
              and 'Free on this drive' in dialog.lblSummary.text(), "My downloads lists each survey with its size")
        choose(NLP_DTM)
        dialog.select_row(dtm_row)
        dialog.btnShow.click()
        check(plugin.dataset == mod.api.DEFAULT_DATASET and sorted(plugin.grid.selected_tile_names())
              == sorted(plugin.download_rows[dtm_row][2]), "Show on grid chooses the dataset and selects its tiles")
        dialog.select_row(spare_row)
        answers.append(None)
        dialog.btnDelete.click()
        check(os.path.exists(spare) and QgsProject.instance().mapLayer(spare_id) is not None, "Delete: Cancel keeps it")
        answers.append(0)
        asked.clear()
        dialog.select_row(spare_row)
        dialog.btnDelete.click()
        app.processEvents()
        labels = [dialog.tree.topLevelItem(i).text(0) for i in range(dialog.tree.topLevelItemCount())]
        check(asked and asked[-1][1].get('warning') and '1 layer(s) in this project' in asked[-1][0][3]
              and not os.path.exists(os.path.join(tmp, 'lidar_tiles_dsm'))
              and QgsProject.instance().mapLayer(spare_id) is None
              and 'England DSM (individual surveys), 2011, 1 m' not in labels,
              "Delete: warns (naming project layers using it), removes the layers and the folder, updates the list")
        dialog.close()
        dialog = None

        # SurfZone: its survey metadata has a different layer, and survey types
        surf = os.path.join(tmp, 'surfzone_dem_2019', '2m', '2019', 'SU12NE')
        os.makedirs(surf)
        shutil.copy(storage.find_raster_files(DTM, 'SU12NE')[0], os.path.join(surf, 'SurfZone_DEM_2019_SU12ne_2m.asc'))
        write_survey_metadata(os.path.join(surf, 'SurfZone_DEM_2019_SU12ne_2m_Metadata.gpkg'),
                              [('SurfZone_DEM_2019_SU12ne_2m', 2016, 415000, 125000, 420000, 130000)],
                              layer_name='survey_used_in_merging_process', survey_type='Multibeam')
        plugin.set_dataset(mod.api.Dataset('surfzone_dem_2019', '2019', '2'))
        app.processEvents()
        select('SU12NE')
        plugin.show_survey_dates()
        surveys = [lyr for lyr in QgsProject.instance().mapLayers().values() if lyr.name().startswith('Survey dates')]
        types = [f['type'] for f in surveys[0].getFeatures()] if surveys else []
        check(len(surveys) == 1 and types == ['Multibeam'] and 'coastal' in dock.lblDatasetInfo.text(),
              "SurfZone: described, and its survey dates include the survey type")
        for survey_layer in surveys:
            QgsProject.instance().removeMapLayer(survey_layer.id())
        surveys = None
        plugin.set_dataset(mod.api.DEFAULT_DATASET)
        app.processEvents()

        # --- v0.9.4 (audit): the grid credits Ordnance Survey; licences ship with the plugin
        from lidar_downloader_uk import grid as pkg_grid
        check(layer.metadata().rights() == [pkg_grid.GRID_ATTRIBUTION]
              and 'Open Government' in layer.metadata().licenses()[0],
              "the grid credits Ordnance Survey (OGL)")
        check(os.path.exists(os.path.join(os.path.dirname(mod.__file__), 'LICENSE'))
              and os.path.exists(os.path.join(os.path.dirname(mod.__file__), 'data', 'LICENSE-DATA.txt')),
              "licence and data notice ship inside the plugin folder")

        # --- v0.9.5: the full OS grid; coastal squares (missing from the old grid) can be picked; new products
        check(storage.TILE_NAME_RE.match('TG14NW') and layer.getFeature(next(layer.getFeatures(
            "\"TILE_NAME\" = 'TG14NW'")).id())['HAS_DATA'] == 't', "coastal square TG14NW is in the grid, with data")
        for product, kind in (('bathymetry_coastal_multibeam', mod.api.RASTER),
                              ('vertical_aerial_photography_tiles_irrgbn', mod.api.IMAGERY),
                              ('oblique_aerial_photography_tiles_other_survey', mod.api.PHOTOS)):
            check(mod.api.is_supported_product(product) and mod.api.product_kind(product) == kind
                  and mod.api.product_description(product), f"{product} supported")

        # --- v0.10: Wales and Scotland as sources; Northern Ireland signposted
        def fixture(name):
            with open(os.path.join(TEST_DIR, 'data', name), encoding='utf-8') as fh:
                return json.load(fh)

        def grid_flag(tile):
            return next(layer.getFeatures(f"\"TILE_NAME\" = '{tile}'"))['HAS_DATA']
        check(grid_flag('SH28SW') == 't' and grid_flag('NS79NE') == 't' and grid_flag('NN17NW') == 't',
              "the grid marks Welsh and Scottish squares with data")

        # Wales: the 2020-22 national survey, a GeoTIFF per 1 km square
        wales = fixture('wales_national_SH28.json')
        server.wales['national'] = wales
        for feature in wales['features']:
            props = feature['properties']
            for field in ('dtm_link', 'dsm_link'):
                server.files['https://' + props[field]] = square_raster_bytes(props['british_gr'])
        wales_dtm = ('wales_lidar_dtm', '2020-2022', '1')
        plugin.set_dataset(mod.api.DEFAULT_DATASET)  # England's Composite DTM chosen...
        app.processEvents()
        select('SH28SW')  # ... and a Welsh tile selected
        check(wait_for_search(plugin) and entry_for(wales_dtm)[1] is not None
              and not any('Composite DSM' in ' '.join(p) for p, _ in menu_entries()),
              "Welsh surveys listed for a Welsh tile (and England's other Composite products aren't)")
        check(plugin.dataset == mod.api.Dataset(*wales_dtm) and 'The tiles are in Wales' in dock.lblAvailability.text(),
              "the England dataset covers none of the tiles: switched to Wales' equivalent, and says so")
        welsh_files = len(plugin.target_for('SH28SW')[3].files)
        check(f"1 to download, about {mod.format_size(welsh_files * 1.45)}" in dock.lblTileCount.text(),
              f"the size estimate counts the tile's Welsh files ({welsh_files} of about 1.45 MB)")
        choose(wales_dtm)
        check('2020-22 survey of all of Wales' in dock.lblDatasetInfo.text(), "the Welsh survey is described")
        server.file_requests.clear()
        msgs.clear()
        plugin.download_selected_tiles()
        check(wait_for_task(plugin) and msgs and 'Downloaded 1 tile(s)' in msgs[-1][0], "Welsh tile downloaded")
        wales_dir = os.path.join(tmp, 'wales_lidar_dtm', '1m', '2020-2022')
        wales_files = storage.find_raster_files(wales_dir, 'SH28SW')
        check(len(wales_files) == 10 and len(server.file_requests) == 10,
              "its ten 1 km GeoTIFFs saved in the tile's folder")
        server.file_requests.clear()
        select('SH28SW')  # a finished download clears the selection
        answers.append(0)  # Skip existing
        asked.clear()
        msgs.clear()
        plugin.download_selected_tiles()
        wait_for_task(plugin)
        check(asked and asked[-1][0][1] == 'Already Downloaded' and server.file_requests == []
              and msgs and 'Downloaded 1 tile(s)' in msgs[-1][0], "files already downloaded aren't fetched again")
        select('SH28SW')
        plugin.load_selected_tiles()
        wales_group = root.findGroup(mod.api.dataset_label(mod.api.Dataset(*wales_dtm)))
        wales_layers = [n.layer() for n in wales_group.findLayers()] if wales_group else []
        check(len(wales_layers) == 1 and wales_layers[0].isValid()
              and 'Welsh Government' in wales_layers[0].metadata().rights()[0],
              "Welsh tiles load, credited to the Welsh Government / NRW")
        wales_layers = None
        root.removeChildNode(wales_group)
        wales_group = None

        # Welsh archive surveys: NRW's grids are in millimetres (named ..._mm_units.asc): converted to metres
        from osgeo import gdal as _gdal
        archive_zip = 'https://lle.blob.core.windows.net/lidar/2m_res_ST16_1998_dtm.zip'
        mm_grid = ('ncols 2\nnrows 2\nxllcorner 316000\nyllcorner 168000\ncellsize 2\nNODATA_value -9999\n'
                   '12345 -9999\n-4850 47044\n')
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            zf.writestr('dtm_F0000878_19980328_19980328_mm_units.asc', mm_grid)
        server.files[archive_zip] = buf.getvalue()
        archive_dir = os.path.join(tmp, 'wales_lidar_archive_dtm', '2m', '1998')
        archive = tasks.DownloadTilesTask(['ST16NE'], archive_dir, files={'ST16NE': (mod.api.FileRef(
            archive_zip, '2m_res_ST16_1998_dtm.zip', 'ST16'),)})
        archive.run()
        grids = storage.find_raster_files(archive_dir, 'ST16NE')
        grid_ds = _gdal.Open(grids[0]) if len(grids) == 1 else None
        values = grid_ds.GetRasterBand(1).ReadAsArray().tolist() if grid_ds else []
        grid_ds = None
        check(archive.results['ST16NE'] is None and [os.path.basename(g) for g in grids]
              == ['dtm_F0000878_19980328_19980328.tif'] and values == [[12.345000267028809, -9999.0],
                                                                       [-4.849999904632568, 47.04399871826172]],
              "Welsh archive grids in millimetres are converted to metres (no data kept, no millimetre copy left)")
        archive = grids = values = None

        # Mosaics of mixed surveys: the newest drawn on top, at the finest cell size (not an average)
        def grid_file(path, x0, size, cell, value):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            ds = _gdal.GetDriverByName('GTiff').Create(path, int(size / cell), int(size / cell), 1, _gdal.GDT_Float32)
            ds.SetGeoTransform((x0, cell, 0, 700000, 0, -cell))
            ds.SetProjection(QgsCoordinateReferenceSystem('EPSG:27700').toWkt())
            ds.GetRasterBand(1).SetNoDataValue(-9999)
            ds.GetRasterBand(1).Fill(value)
            ds = None
            return path
        older = grid_file(os.path.join(tmp, 'mix', 'b_old.tif'), 270000, 10000, 1, 2011)   # a 10 km file, 1 m
        newer = grid_file(os.path.join(tmp, 'mix', 'a_new.tif'), 275000, 5000, 0.5, 2021)  # one tile, 0.5 m
        mixed = mod.postprocess.build_vrt([older, newer], os.path.join(tmp, 'mix', 'mix.vrt'))
        ds = _gdal.Open(mixed)
        gt = ds.GetGeoTransform()
        newest = ds.GetRasterBand(1).ReadAsArray(int((277500 - gt[0]) / gt[1]), int((gt[3] - 697500) / -gt[5]), 1, 1)
        check(gt[1] == 0.5 and newest[0][0] == 2021, "a mosaic of mixed surveys shows the newest where they overlap, "
                                                     "at the finest cell size")
        # Overviews, so big mosaics draw quickly zoomed out: built for large grids without them, not small ones
        big = grid_file(os.path.join(tmp, 'mix', 'big.tif'), 270000, 2000, 1, 5)
        small = grid_file(os.path.join(tmp, 'mix', 'small.tif'), 270000, 500, 1, 5)
        built = mod.postprocess.add_overviews([big, small])
        big_ds = _gdal.Open(big)
        if hasattr(_gdal, 'SetThreadLocalConfigOption'):
            check(built == 1 and big_ds.GetRasterBand(1).GetOverviewCount() >= 2
                  and mod.postprocess.add_overviews([big]) == 0,
                  "overviews built once for big grids, not for small ones")
        else:
            check(built == 0 and big_ds.GetRasterBand(1).GetOverviewCount() == 0,
                  f"no overviews with GDAL {_gdal.__version__} (before 3.4), and no error")
        big_ds = None
        # A failure while adding overviews never fails the tile (they only make drawing quicker)
        real_add_overviews = mod.postprocess.add_overviews

        def broken_overviews(paths):
            raise AttributeError('simulated')
        mod.postprocess.add_overviews = broken_overviews
        try:
            fine = tasks.DownloadTilesTask(['ST16NE'], archive_dir, files={'ST16NE': (mod.api.FileRef(
                archive_zip, '2m_res_ST16_1998_dtm.zip', 'ST16'),)})
            fine.run()
        finally:
            mod.postprocess.add_overviews = real_add_overviews
        check(fine.results.get('ST16NE') is None, "a failure adding overviews doesn't fail the tile")
        fine = None
        ds = None
        shutil.rmtree(os.path.join(tmp, 'mix'), ignore_errors=True)

        # Scotland: catalogue products; Phase I's 10 km DTM serves four tiles
        mod_tasks = __import__('lidar_downloader_uk.tasks', fromlist=['x'])
        mod_tasks._scottish_collections = None
        server.scotland_collections = fixture('scotland_collections.json')
        products = fixture('scotland_products_stirling.json')
        for product in products['result']:
            http = product['data']['product']['http']
            if http['url'].endswith('.tif'):
                data = square_raster_bytes(product['properties']['osgbGridRef'])
                server.files[http['url']] = data
                http['size'] = len(data)
        server.scotland_products = products
        phase1 = ('scotland_lidar_dtm', '2011-phase-1', '1')
        plugin.set_dataset(mod.api.DEFAULT_DATASET)
        app.processEvents()
        select('NS79NE', 'NS79SE')
        check(wait_for_search(plugin) and plugin.dataset.product == 'scotland_lidar_dtm'
              and 'The tiles are in Scotland' in dock.lblAvailability.text(),
              "Scottish tiles with England's dataset chosen: switched to Scotland's DTM")
        check(entry_for(phase1)[1] is not None
              and entry_for(('scotland_lidar_dtm', '2021-phase-5', '0.5'))[1] is not None,
              "Scottish surveys listed (Phase I, Phase V...)")
        choose(phase1)
        server.file_requests.clear()
        msgs.clear()
        plugin.download_selected_tiles()
        check(wait_for_task(plugin) and msgs and 'Downloaded 2 tile(s)' in msgs[-1][0], "Scottish tiles downloaded")
        scot_dir = os.path.join(tmp, 'scotland_lidar_dtm', '1m', '2011-phase-1')
        check(server.file_requests == [u for u in server.files if u.endswith('NS79_1M_DTM_PHASE1.tif')]
              and os.path.exists(os.path.join(scot_dir, 'NS79', 'NS79_1M_DTM_PHASE1.tif'))
              and storage.scan_downloaded_tiles(scot_dir) == ['NS79NE', 'NS79SE'],
              "the 10 km file is fetched once, kept in NS79, and serves the tiles downloaded")
        select('NS79NW')
        wait_for_search(plugin)
        server.file_requests.clear()
        plugin.download_selected_tiles()
        check(wait_for_task(plugin) and server.file_requests == []
              and 'NS79NW' in storage.scan_downloaded_tiles(scot_dir),
              "another tile of the square reuses the 10 km file already there")
        select('NS79NE', 'NS79SE')
        plugin.load_selected_tiles()
        scot_group = root.findGroup(mod.api.dataset_label(mod.api.Dataset(*phase1)))
        scot_layers = [n.layer() for n in scot_group.findLayers()] if scot_group else []
        check(len(scot_layers) == 1 and 'attribution' in scot_layers[0].metadata().rights()[0].lower(),
              "the shared file loads once, with the survey's own licence statement")
        scot_layers = None
        root.removeChildNode(scot_group)
        scot_group = None
        with open(os.path.join(scot_dir, 'survey.json'), encoding='utf-8') as fh:
            check('rights' in json.load(fh), "the survey's licence is kept with the download")
        select('NS79SE')
        wait_for_search(plugin)
        hes_path, _ = entry_for(('scotland_lidar_dtm', '2010-hes', '0.5'))
        check(hes_path and '(non-commercial)' in hes_path[-1], "a non-commercial survey is marked in the menu")
        check(choose(('scotland_lidar_dtm', '2010-hes', '0.5')), "HES 2010 listed for NS79SE")
        answers.append(None)  # Cancel at the licence terms
        asked.clear()
        plugin.download_selected_tiles()
        check(plugin.task is None and asked and asked[-1][0][1] == 'Licence Terms',
              "a non-commercial survey asks before downloading (Cancel stops)")
        plugin.set_dataset(mod.api.DEFAULT_DATASET)
        app.processEvents()

        # Northern Ireland: Go to places it, and says where its data is
        def ni_go(text):
            plugin.grid.clear_selection()
            dock.leGoTo.setText(text)
            msgs.clear()
            dock.btnGoTo.click()
            wait_for_geocode(plugin)
            centre = canvas.extent().center()
            return msgs[-1][0] if msgs else '', plugin.grid.selected_tile_names(), (centre.x(), centre.y())
        server.geocode = {'BT1': (200, {'status': 200, 'result': {
            'postcode': 'BT1 5GS', 'eastings': 333832, 'northings': 374014, 'country': 'Northern Ireland',
            'admin_district': 'Belfast', 'longitude': -5.930077, 'latitude': 54.596633}})}
        text, selected, (x, y) = ni_go('BT1 5GS')
        check(durations and durations[-1] == 0, "the Northern Ireland notice (with its links) stays until closed")
        check('Northern Ireland' in text and 'opendatani' in text and selected == []
              and abs(x - 146588) < 3000 and abs(y - 529842) < 3000,
              "a BT postcode goes to Belfast (not Greater Manchester) and points to NI's official sources")
        text, _, (x, y) = ni_go('J 341 743')
        check('Northern Ireland' in text and abs(x - 146588) < 3000 and abs(y - 529842) < 3000,
              "an Irish Grid reference goes to Belfast")
        server.geocode = {'places': (200, {'status': 200, 'result': []})}
        text, _, _ = ni_go('Enniskillen')
        check('Enniskillen' in text and 'Northern Ireland' in text, "an NI town (OSNI gazetteer) is found")
        sources_menu = dock.btnSources.menu()
        labels = [a.text() for a in sources_menu.actions()]
        ni_menu = next((a.menu() for a in sources_menu.actions() if a.menu()), None)
        check(any(t.startswith('Wales') for t in labels) and any(t.startswith('Scotland') for t in labels)
              and ni_menu is not None and len(ni_menu.actions()) == len(mod.sources.NI_LINKS),
              "Official data sources menu: each nation's portal, and NI's sources")
        plugin.ni_notice_shown = False
        msgs.clear()
        canvas.setExtent(QgsRectangle(140000, 525000, 150000, 535000))  # over Belfast
        app.processEvents()
        check(plugin.ni_notice_shown and msgs and 'Northern Ireland' in msgs[-1][0],
              "moving the map over Northern Ireland says where its data is, once")
        dock.leGoTo.clear()

        # --- cancel a download in progress
        server.responses = {'SU1520': [('SLOW', b'')]}  # SU12SE
        select('SU12SE')
        msgs.clear()
        plugin.download_selected_tiles()
        time.sleep(0.3)
        app.processEvents()
        plugin.cancel_download()
        check(wait_for_task(plugin, 10), "cancel stops the task promptly")
        check(msgs and "Cancelled" in msgs[-1][0] and not os.path.exists(os.path.join(DTM, 'SU12SE.zip')),
              "cancel reported; nothing saved")
        check(dock.btnResume.isVisible() and dock.btnResume.text() == 'Resume: 1 tile not downloaded yet'
              and plugin.unfinished()[1] == ['SU12SE'], "a cancelled download is offered to resume")
        server.responses = {'SU1520': [(200, tile_zip_bytes('SU12SE'))]}
        layer.removeSelection()
        dock.btnResume.click()
        wait_for_search(plugin)
        wait_for_task(plugin)
        check(storage.find_raster_files(DTM, 'SU12SE') and not dock.btnResume.isVisible()
              and plugin.unfinished() is None
              and plugin.grid.selected_tile_names() == ['SU12SE'], "Resume selects the tiles again and downloads them")

        # --- Downloads in an earlier version's layout are offered for reorganising, once
        old_root = tempfile.mkdtemp()
        old_tile = os.path.join(old_root, 'extracted', 'SU12NE')
        with zipfile.ZipFile(io.BytesIO(tile_zip_bytes('SU12NE'))) as zf:
            zf.extractall(old_tile)
        old_raster = storage.find_raster_files(os.path.join(old_root, 'extracted'), 'SU12NE')[0]
        with open(os.path.join(old_root, 'old.vrt'), 'w', encoding='utf-8') as f:
            f.write('<VRTDataset><VRTRasterBand><SimpleSource><SourceFilename relativeToVRT="1">'
                    f'extracted/SU12NE/{os.path.basename(old_raster)}</SourceFilename></SimpleSource>'
                    '</VRTRasterBand></VRTDataset>')
        old_layer = mod.QgsRasterLayer(old_raster, 'old tile')
        QgsProject.instance().addMapLayer(old_layer)
        choose(DEFAULT)
        asked.clear()
        answers.append(0)  # Reorganise
        dock.leDownloadDir.setText(old_root)
        dock.leDownloadDir.editingFinished.emit()
        new_tile = os.path.join(old_root, 'lidar_composite_dtm', '1m', '2022', 'SU12NE')
        check(len(asked) == 1 and 'earlier version' in asked[-1][0][2] and os.path.isdir(new_tile)
              and not os.path.exists(old_tile), "old layout: asked, then tiles moved to product/resolution/year")
        vrt_text = open(os.path.join(old_root, 'old.vrt'), encoding='utf-8').read()
        check('lidar_composite_dtm/1m/2022/SU12NE/' in vrt_text, "VRTs in the folder updated to the new locations")
        check(os.path.normcase(old_layer.source()).startswith(os.path.normcase(new_tile)) and old_layer.isValid(),
              "layers in the project repointed to the moved files")
        dock.leDownloadDir.editingFinished.emit()
        check(len(asked) == 1, "not asked again once reorganised")
        QgsProject.instance().removeMapLayer(old_layer.id())
        dock.leDownloadDir.setText(tmp)
        dock.leDownloadDir.editingFinished.emit()
        shutil.rmtree(old_root, ignore_errors=True)

        # --- unticking removes the grid; a project saved with the grid, reopened, isn't doubled up
        def grid_layers():
            return [lyr for lyr in QgsProject.instance().mapLayers().values() if lyr.name() == 'OSGB Grid']

        dock.chkShowGrid.setChecked(False)
        check(not grid_layers() and plugin.grid.layer() is None, "unticking 'Show OSGB grid' removes the grid")
        saved_grid = pkg_grid.open_grid(os.path.dirname(mod.__file__))  # as if read from a saved project
        QgsProject.instance().addMapLayer(saved_grid)
        saved_grid = None
        plugin.sync_grid_checkbox()  # what opening a project does
        check(dock.chkShowGrid.isChecked() and len(grid_layers()) == 1 and plugin.grid.layer() is not None,
              "a project saved with the grid: the grid is taken over and the box ticked (no second grid)")
        dock.chkShowGrid.setChecked(False)
        check(not grid_layers(), "... and unticking removes it")
        QgsProject.instance().addMapLayer(pkg_grid.open_grid(os.path.dirname(mod.__file__)))
        dock.chkShowGrid.setChecked(True)
        check(len(grid_layers()) == 1, "ticking with a saved grid in the project doesn't add a second one")
        # A user's own layer from a file that happens to be called osgb_grid_5km.gpkg is never taken for the grid
        own_dir = os.path.join(tmp, 'client')
        os.makedirs(own_dir, exist_ok=True)
        shutil.copy(os.path.join(os.path.dirname(mod.__file__), 'data', 'osgb_grid_5km.gpkg'), own_dir)
        own = QgsVectorLayer(os.path.join(own_dir, 'osgb_grid_5km.gpkg') + '|layername=osgb_grid_5km', 'Client sites',
                             'ogr')
        QgsProject.instance().addMapLayer(own)
        own_id = own.id()
        own = None
        dock.chkShowGrid.setChecked(False)
        dock.chkShowGrid.setChecked(True)
        kept = QgsProject.instance().mapLayer(own_id)
        check(kept is not None and kept.name() == 'Client sites'
              and os.path.normcase(own_dir) in os.path.normcase(kept.source()) and len(grid_layers()) == 1,
              "a user's own layer is never taken for the grid, whatever its file is called")
        kept = None
        QgsProject.instance().removeMapLayer(own_id)
        layer = plugin.grid.layer()

        # --- the user removes the grid layer themselves
        QgsProject.instance().removeMapLayer(layer.id())
        check(not dock.chkShowGrid.isChecked() and plugin.grid.layer() is None, "manual removal syncs checkbox")
        msgs.clear()
        plugin.download_selected_tiles()
        check(msgs and msgs[-1][1] == Qgis.MessageLevel.Warning,
              "actions after manual removal warn instead of crashing")

        # --- hiding (e.g. tabbed behind another dock) keeps the grid; closing removes it
        dock.chkShowGrid.setChecked(True)
        dock.hide()
        check(plugin.grid.layer() is not None and dock.chkShowGrid.isChecked(), "tabbing away keeps the grid")
        dock.show()
        dock.close()
        check(plugin.grid.layer() is None and not dock.chkShowGrid.isChecked(), "closing dock removes grid")

        if os.environ.get("LIDAR_LIVE") == "1":
            tasks.QgsBlockingNetworkRequest = real_request_class
            live_dir = tempfile.mkdtemp()
            dock.leDownloadDir.setText(live_dir)
            dock.show()
            dock.chkShowGrid.setChecked(True)
            layer = plugin.grid.layer()
            select('SU12NE')
            plugin.download_selected_tiles()
            seen = []
            plugin.task.statusMessage.connect(seen.append)
            wait_for_task(plugin, 600)
            check(os.path.getsize(os.path.join(live_dir, 'SU12NE.zip')) > 10_000_000, "LIVE: real tile downloaded")
            check(any(" MB" in m for m in seen), f"LIVE: byte progress reported ({len(seen)} updates)")
            shutil.rmtree(live_dir, ignore_errors=True)

        dock.show()
        dock.chkShowGrid.setChecked(True)
        plugin.unload()
        check(not QgsProject.instance().mapLayersByName("OSGB Grid") and plugin.dock is None, "unload cleans up")
    except Exception:
        # To stdout, flushed: the exit below skips flushing, and stderr is buffered on older Pythons (lost in CI)
        traceback.print_exc(file=sys.stdout)
        sys.stdout.flush()
        failures.append("exception")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        QSettings().clear()


main()
print("RESULT:", "ALL PASSED" if not failures else f"{len(failures)} FAILED: {failures}", flush=True)
# Skip interpreter teardown: widgets left over from the mocked iface can
# outlive QGIS shutdown and segfault after the results are already known.
sys.stdout.flush()
faulthandler.disable()  # a crash in Qt's own exit-time cleanup isn't a test result
os._exit(1 if failures else 0)
