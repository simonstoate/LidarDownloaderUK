# SPDX-FileCopyrightText: 2025-2026 Simon Stoate
# SPDX-License-Identifier: GPL-2.0-or-later
"""Background tasks: searching what's available, and downloading tiles."""

import json
import os
import threading
import time
import zipfile

from qgis.PyQt.QtCore import QByteArray, QFile, QIODevice, QUrl, pyqtSignal, pyqtSlot
from qgis.PyQt.QtNetwork import QNetworkRequest
from qgis.core import (Qgis, QgsBlockingNetworkRequest, QgsFeedback, QgsMessageLog, QgsProcessingContext,
                       QgsProcessingFeedback, QgsTask)

from . import api, places, pointclouds, postprocess, sources, storage

LOG_TAG = 'LIDAR Downloader UK'  # the Log Messages tab, as the plugin names it
MAX_ATTEMPTS = 3
RETRY_DELAYS = (5, 15)  # seconds to wait before the 2nd and 3rd attempts
# Statuses the service uses for "no such tile" / "not allowed": retrying won't help
PERMANENT_HTTP_ERRORS = (400, 401, 403, 404, 500)


class ServiceHealth:
    """Which services have stopped answering, shared by parallel downloads (so their other tiles are skipped
    rather than each waiting minutes to time out). A service counts as down once two different files from it
    got no answer; any answer from it clears that."""

    def __init__(self):
        self._lock = threading.Lock()
        self._missed = {}
        self._down = set()

    def is_down(self, source):
        with self._lock:
            return source in self._down

    def missed(self, source, url):
        """Note a file the service didn't answer for. Returns True if the service now counts as down."""
        with self._lock:
            self._missed.setdefault(source, set()).add(url)
            if len(self._missed[source]) >= 2:
                self._down.add(source)
            return source in self._down

    def answered(self, source):
        with self._lock:
            self._missed.pop(source, None)
            self._down.discard(source)


class DownloadTilesTask(QgsTask):
    """Download and extract tiles without blocking the QGIS UI.

    Runs in a worker thread, so run() must not touch widgets or the project.
    Uses QgsBlockingNetworkRequest, which respects the QGIS proxy and
    authentication settings. Results are in self.results:
    {tile: None on success, or a short error string}; self.details has the technical reason for failures
    such as a service not answering.
    """

    # Emitted from the worker thread; Qt queues it to the UI thread.
    statusMessage = pyqtSignal(str)
    # A tile finished (downloaded, skipped or failed), for overall progress
    tileFinished = pyqtSignal(str)

    def __init__(self, tiles, download_dir, reuse_existing=(), delete_zips=False, on_finished=None,
                 dataset=api.DEFAULT_DATASET, urls=None, folders=None, files=None, health=None, feedback=None,
                 part_of_batch=False):
        """download_dir is the dataset folder; folders maps tile -> dataset folder where a tile's
        survey differs (e.g. "newest for each tile"). urls maps tile -> download URL (from the search
        API); tiles without one get a URL built from dataset. files maps tile -> api.FileRef tuple
        for sources that serve files rather than a zip per tile (Wales, Scotland). health is a
        ServiceHealth shared by parallel downloads. feedback is a Processing feedback when run inside a
        Processing algorithm: messages, progress and cancelling then go through it directly (no signals
        connected across threads). part_of_batch: messages name the tile only (the batch counts overall)."""
        super().__init__(f'Downloading LIDAR tiles: {api.dataset_label(dataset)}')
        self.tiles = list(tiles)
        self.download_dir = download_dir
        self.dataset = dataset
        self.urls = dict(urls or {})
        self.folders = dict(folders or {})
        self.files = dict(files or {})
        self._fetched = set()  # file URLs fetched by this task (a 10 km file serves several tiles)
        self.reuse_existing = set(reuse_existing)
        self.delete_zips = delete_zips
        self.on_finished = on_finished
        self.results = {}
        self.details = {}
        self.health = health if health is not None else ServiceHealth()
        self.feedback = feedback if feedback is not None else QgsFeedback()
        self._report = getattr(feedback, 'pushInfo', None)
        self._report_progress = getattr(feedback, 'setProgress', None)
        self.part_of_batch = part_of_batch
        self._last_detail = ''

    def cancel(self):
        # Aborts an in-flight request as well as stopping the loop
        self.feedback.cancel()
        super().cancel()

    def _stopped(self):
        return self.isCanceled() or self.feedback.isCanceled()

    def _status(self, text):
        self.statusMessage.emit(text)
        if self._report:
            self._report(text)

    def _progress(self, value):
        self.setProgress(value)
        if self._report_progress:
            self._report_progress(value)

    def run(self):
        total = len(self.tiles)
        for index, tile in enumerate(self.tiles):
            if self._stopped():
                return False
            try:
                self.results[tile] = self._process_tile(tile, index, total)
            except Exception as e:  # never let an unexpected error take QGIS down
                self.results[tile] = f'unexpected error: {e}'
            if self.results[tile] and api.NOT_RESPONDING in self.results[tile] and self._last_detail:
                self.details[tile] = self._last_detail
            self._progress(100.0 * (index + 1) / total)
            self.tileFinished.emit(tile)
        # Cancelling during the last tile must still report as cancelled
        return not self._stopped()

    def finished(self, result):
        # Called on the UI thread once run() returns (or the task is cancelled)
        if self.on_finished:
            self.on_finished(self, result)

    def _process_tile(self, tile, index, total):
        prefix = tile if self.part_of_batch else f"{tile} ({index + 1} of {total})"
        folder = self.folders.get(tile, self.download_dir)
        os.makedirs(folder, exist_ok=True)
        if not api.TILE_RE.match(tile):
            return 'invalid tile name'
        if tile in self.files:
            return self._process_files(tile, folder, self.files[tile], prefix, index, total)
        url = self.urls.get(tile)
        if url and not api.is_trusted_url(url):
            return 'download link is not on an official service'
        if not url and self.dataset.is_latest:
            return "couldn't check which surveys cover this tile"
        if not url:
            try:
                d = self.dataset
                url = api.tile_download_url(tile, d.product, d.year, d.resolution)
            except ValueError:
                return 'invalid tile name'

        zip_path = storage.zip_path(folder, tile)
        if tile not in self.reuse_existing:
            self._status(f"{prefix}: downloading...")
            error = self._download(url, zip_path, prefix, index, total)
            if error:
                return error
        elif not os.path.exists(zip_path):
            # Extracted earlier and the zip was deleted: nothing left to do
            if storage.find_data_files(folder, tile):
                return None
            return 'zip file missing and no extracted files found'

        if self._stopped():
            return 'cancelled'

        self._status(f"{prefix}: extracting...")
        try:
            storage.extract_tile(folder, tile)
        except zipfile.BadZipFile:
            return 'downloaded file was not a valid zip'
        except OSError as e:
            return f'extraction failed: {e}'

        if self.delete_zips:
            # Only after a successful extraction, so a failure keeps the zip
            storage.remove_quietly(zip_path)
        self._add_overviews(folder, tile, prefix)
        return None

    def _add_overviews(self, folder, tile, prefix):
        """Overviews for the tile's grids, so large mosaics draw quickly zoomed out (never fails the tile)."""
        if self._stopped():
            return
        self._status(f"{prefix}: preparing for fast drawing...")
        try:
            postprocess.add_overviews(storage.find_raster_files(folder, tile))
        except Exception as e:  # only a speed-up: whatever goes wrong, the tile itself is fine
            # (logging is safe from this worker thread)
            QgsMessageLog.logMessage(f"{tile}: couldn't prepare it for fast drawing ({e}); it's downloaded and loads "
                                     "as normal, but may draw slowly zoomed out.", LOG_TAG, Qgis.MessageLevel.Info)

    def _process_files(self, tile, folder, refs, prefix, index, total):
        """Fetch a tile's files (each into its tile's or 10 km square's folder), skipping ones already
        there when the tile is reused, or fetched for another tile in this task. Zips are extracted."""
        if not api.TILE_RE.match(tile):
            return 'invalid tile name'
        if any(not api.is_trusted_url(ref.url) or os.path.basename(ref.name) != ref.name for ref in refs):
            return 'download link is not on an official service'
        # A file already here in full (e.g. a 10 km file another tile brought) is only fetched again when
        # this tile was downloaded before and the user chose to download it again
        again = tile not in self.reuse_existing and storage.is_downloaded(folder, tile)
        storage.start_tile_files(folder, tile, refs)
        for number, ref in enumerate(refs, 1):
            if self._stopped():
                return 'cancelled'
            if storage.file_done(folder, ref) and (not again or ref.url in self._fetched):
                continue
            destination = storage.file_folder(folder, ref)
            os.makedirs(destination, exist_ok=True)
            path = os.path.join(destination, ref.name)
            label = f"{prefix}, file {number} of {len(refs)}" if len(refs) > 1 else prefix
            self._status(f"{label}: downloading {ref.name}...")
            error = self._download(ref.url, path, label, index, total)
            if error and (api.NOT_RESPONDING in error or api.NO_ANSWER in error):
                return error
            if error:
                return f'{ref.name}: {error}'
            self._fetched.add(ref.url)
            if ref.name.lower().endswith('.zip'):
                self._status(f"{label}: extracting...")
                try:
                    storage.extract_file_zip(path)
                    # NRW's archive grids are in millimetres: made metres, like everything else
                    postprocess.convert_mm_grids(os.path.dirname(path))
                except zipfile.BadZipFile:
                    return f'{ref.name}: downloaded file was not a valid zip'
                except (OSError, RuntimeError) as e:
                    return f'{ref.name}: extraction failed: {e}'
                if self.delete_zips:
                    storage.remove_quietly(path)
        storage.finish_tile_files(folder, tile, refs)
        self._add_overviews(folder, tile, prefix)
        return None

    def _download(self, url, dest, prefix, index, total):
        """Fetch url into dest with retries. Returns None on success, else an error string. A file the service
        doesn't answer for (timeouts, no connection, or still busy after retrying) is noted in self.health;
        once a service is down, its other files aren't tried."""
        source = api.url_source(url)
        if source and self.health.is_down(source):
            return api.not_responding(source)
        error = None
        unanswered = False
        for attempt in range(MAX_ATTEMPTS):
            if attempt:
                if source and self.health.is_down(source):
                    break
                delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS)) - 1]
                self._status(f"{prefix}: {error}; retrying in {delay}s...")
                if not self._sleep(delay):
                    return 'cancelled'

            request = QNetworkRequest(QUrl(url))
            # Tiles are 20-300 MB: keep them out of the QGIS network cache
            request.setAttribute(QNetworkRequest.Attribute.CacheSaveControlAttribute, False)
            request.setAttribute(QNetworkRequest.Attribute.CacheLoadControlAttribute,
                                 QNetworkRequest.CacheLoadControl.AlwaysNetwork)

            blocking = QgsBlockingNetworkRequest()
            # Connect to a real slot, not a lambda: a Python callable connected from this worker
            # thread gets a hidden PyQt proxy object owned by the thread-pool thread, destroyed at
            # an arbitrary moment when the idle thread exits - which crashed QGIS 3.x (PyQt5).
            self._current = (prefix, index, total)
            blocking.downloadProgress.connect(self._on_bytes)
            blocking.get(request, True, self.feedback)

            if self._stopped():
                return 'cancelled'

            reply = blocking.reply()
            status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            final_url = reply.request().url().toString()
            if status == 200 and final_url != url and not api.is_trusted_url(final_url):
                return 'download link redirected away from the official service'
            if status:
                self.health.answered(source)
            if status == 200:
                content = reply.content()
                if not content:
                    error = 'empty response'
                    continue
                try:
                    write_qbytearray_atomically(dest, content)
                except OSError as e:
                    return f'could not save file: {e}'
                return None

            if status in PERMANENT_HTTP_ERRORS:
                return api.describe_http_error(status)
            # Timeouts, connection problems, 502/503/504...: worth retrying
            self._last_detail = blocking.errorMessage() or (f'HTTP {status}' if status else 'no answer')
            error = api.describe_http_error(status) if status else self._last_detail
            unanswered = not status or status in api.UNAVAILABLE_HTTP_STATUSES

        if unanswered:
            if source and self.health.missed(source, url):
                return api.not_responding(source)
            return api.no_answer(source, error)
        return error

    @pyqtSlot('qint64', 'qint64')
    def _on_bytes(self, received, size):
        prefix, index, total = self._current
        if size and size > 0:
            self._progress(100.0 * (index + received / size) / total)
            self._status(f"{prefix}: downloading {received / 1e6:.1f} of {size / 1e6:.1f} MB")

    def _sleep(self, seconds):
        """Sleep, waking early if cancelled. Returns False if cancelled."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self._stopped():
                return False
            time.sleep(0.2)
        return True


def split_tiles(tiles, parts):
    """Share tiles between parallel downloads, keeping tiles of the same 10 km square together (a file
    covering the square is fetched once, never by two downloads at the same time)."""
    groups = {}
    for tile in tiles:
        groups.setdefault(tile[:4], []).append(tile)
    shares = [[] for _ in range(max(1, parts))]
    for group in sorted(groups.values(), key=len, reverse=True):
        min(shares, key=len).extend(group)
    return shares


def _parent_depends_on_subtask():
    """QgsTask's ParentDependsOnSubTask, wherever this QGIS version keeps it (a scoped enum in newer bindings)."""
    dependency = getattr(QgsTask, 'SubTaskDependency', QgsTask)
    return dependency.ParentDependsOnSubTask


class ParallelDownloadTask(QgsTask):
    """Download tiles a few at a time: a DownloadTilesTask per share of the tiles, run by QGIS as
    subtasks (each in its own thread), with QGIS combining their progress and cancelling them
    together. Offers the same results / details / tiles / statusMessage / on_finished as DownloadTilesTask,
    with messages counting the tiles done overall ("12 of 50 done")."""

    statusMessage = pyqtSignal(str)

    def __init__(self, tiles, download_dir, reuse_existing=(), delete_zips=False, on_finished=None,
                 dataset=api.DEFAULT_DATASET, urls=None, folders=None, parallel=3, files=None):
        super().__init__(f'Downloading LIDAR tiles: {api.dataset_label(dataset)}')
        self.tiles = list(tiles)
        self.on_finished = on_finished
        self.done = 0
        shares = split_tiles(self.tiles, parallel)
        self.parts = []
        health = ServiceHealth()  # shared: once a service isn't answering, the other parts skip it too
        for share in (s for s in shares if s):
            part = DownloadTilesTask(share, download_dir, reuse_existing, delete_zips, None, dataset, urls, folders,
                                     files, health, part_of_batch=True)
            # Connected here, on the UI thread, to real slots (never a lambda from a worker thread)
            part.statusMessage.connect(self._on_status)
            part.tileFinished.connect(self._on_tile_finished)
            self.addSubTask(part, [], _parent_depends_on_subtask())
            self.parts.append(part)

        self._results = None
        self._details = None

    @property
    def results(self):
        if self._results is not None:
            return self._results
        merged = {}
        for part in self.parts:
            merged.update(part.results)
        return merged

    @property
    def details(self):
        if self._details is not None:
            return self._details
        merged = {}
        for part in self.parts:
            merged.update(part.details)
        return merged

    @pyqtSlot(str)
    def _on_status(self, text):
        self.statusMessage.emit(f"{self.done} of {len(self.tiles)} done · {text}")

    @pyqtSlot(str)
    def _on_tile_finished(self, tile):
        self.done += 1

    def run(self):
        # Runs once every part has finished
        return not self.isCanceled()

    def finished(self, result):
        # Keep plain results and let go of the parts: QGIS deletes them with this task
        self._results = self.results
        self._details = self.details
        self.parts = []
        if self.on_finished:
            self.on_finished(self, result)


class ClipPointCloudTask(QgsTask):
    """Crop a point cloud to an area (a GeoJSON file, removed afterwards) as a COPC file, in the background. It
    runs QGIS's PDAL tool with its own Processing context (the project and map canvas belong to the UI thread)
    and a feedback that Cancel stops. self.result is the output path; self.error says why it failed."""

    def __init__(self, source, area_file, output, description, on_finished=None):
        super().__init__(description)
        self.source, self.area_file, self.output = source, area_file, output
        self.on_finished = on_finished
        self.feedback = QgsProcessingFeedback()
        self.result = None
        self.error = None

    def cancel(self):
        self.feedback.cancel()
        super().cancel()

    def run(self):
        try:
            self.result = pointclouds.clip(self.source, self.area_file, self.output, context=QgsProcessingContext(),
                                           feedback=self.feedback)
            return not self.isCanceled()
        except Exception as e:  # never let an unexpected error take QGIS down
            self.error = str(e)
            return False
        finally:
            postprocess.remove_file(self.area_file)

    def finished(self, result):
        if self.on_finished:
            self.on_finished(self, result)


def write_qbytearray_atomically(path, data):
    """Write a QByteArray via a .part file without copying it into a Python bytes
    object first (point cloud tiles are ~300 MB)."""
    part_path = path + '.part'
    f = QFile(part_path)
    if not f.open(QIODevice.OpenModeFlag.WriteOnly):
        raise OSError(f.errorString())
    written = f.write(data)
    f.close()
    if written != len(data):
        storage.remove_quietly(part_path)
        raise OSError('disk full or write error')
    try:
        os.replace(part_path, path)
    except OSError:
        storage.remove_quietly(part_path)
        raise


# The Scottish catalogue's collections, fetched once per QGIS session
_scottish_collections = None


class SearchTask(QgsTask):
    """Ask each source covering some areas which datasets they have: the Environment Agency (England),
    DataMapWales and the Scottish Remote Sensing Portal.

    areas is a list of {'square': '100 km square', 'polygon': GeoJSON Polygon in WGS84, 'bbox':
    (xmin, ymin, xmax, ymax) in British National Grid} (grid.search_areas); a bare GeoJSON Polygon is
    asked of the Environment Agency only. Results are in self.offerings (api.Offering list); self.error
    is set if a source failed, and self.failed_squares lists the 100 km squares it left unchecked (the
    others' results are still good).
    """

    def __init__(self, areas, on_finished=None, feedback=None):
        """feedback: a Processing feedback when run inside a Processing algorithm (cancelling goes through it)."""
        super().__init__('Checking available LIDAR data')
        # A bare polygon gets a stand-in square ('SU'), only used to report whether its search failed
        self.areas = [a if 'polygon' in a else {'square': 'SU', 'polygon': a, 'bbox': None} for a in areas]
        self.on_finished = on_finished
        self.offerings = []
        self.error = None
        self.error_detail = ''
        self.failed_squares = set()
        self.feedback = feedback if feedback is not None else QgsFeedback()

    def cancel(self):
        self.feedback.cancel()
        super().cancel()

    def run(self):
        try:
            return self._search()
        except Exception as e:  # never let an unexpected error take QGIS down
            self.error = f'unexpected error: {e}'
            return False

    def _stopped(self):
        return self.isCanceled() or self.feedback.isCanceled()

    def _search(self):
        for index, area in enumerate(self.areas):
            if self._stopped():
                return False
            wanted = sources.sources_for_square(area['square']) if area.get('bbox') else {'ea'}
            answers = []
            if 'ea' in wanted:
                data = self._request('Environment Agency', api.SEARCH_URL, area['polygon'], 'application/geo+json')
                answers.append(data is not None and api.parse_search_response(data))
            if 'wales' in wanted:
                for layer, parse in ((sources.WALES_NATIONAL_LAYER, sources.parse_wales_national),
                                     (sources.WALES_ARCHIVE_LAYER, sources.parse_wales_archive)):
                    data = self._pages('features', sources.WALES_PAGE, lambda start, layer=layer: self._request(
                        'DataMapWales', sources.wales_url(layer, area['bbox'], start=start), service='wales'))
                    answers.append(data is not None and parse(data))
            if 'scotland' in wanted:
                collections = self._scottish_collections()  # None if the catalogue didn't answer
                data = collections and self._pages('result', sources.SCOTLAND_PAGE, lambda start: self._request(
                    'Scottish Remote Sensing Portal', sources.SCOTLAND_SEARCH_URL,
                    sources.scotland_search_body(collections, area['polygon'], offset=start), 'application/json',
                    service='scotland'))
                answers.append(collections is not None and data is not None
                               and sources.parse_scotland_products(data or {}, collections))
            for found in answers:
                if found is False:
                    self.failed_squares.add(area['square'])
                else:
                    self.offerings.extend(found)
            self.setProgress(100.0 * (index + 1) / len(self.areas))
        return not self._stopped()

    def _pages(self, key, size, fetch, most=20):
        """Fetch pages of results (fetch(start) -> response) until a short page; {key: all items}, or None
        if a request failed or there were too many to page through."""
        items = []
        for _ in range(most):
            data = fetch(len(items))
            if data is None:
                return None
            page = (data or {}).get(key) or []
            items.extend(page)
            if len(page) < size:
                return {key: items}
        self.error = self.error or 'too many results: select a smaller area'
        return None

    def _scottish_collections(self):
        global _scottish_collections
        if _scottish_collections is None:
            data = self._request('Scottish Remote Sensing Portal', sources.SCOTLAND_COLLECTIONS_URL, service='scotland')
            if data is not None:
                _scottish_collections = sources.parse_scotland_collections(data)
        return _scottish_collections

    def _request(self, name, url, body=None, content_type=None, service='ea'):
        """GET (or POST body as JSON) and return the decoded JSON, or None after noting the error."""
        request = QNetworkRequest(QUrl(url))
        request.setRawHeader(b'Accept', b'application/json')
        blocking = QgsBlockingNetworkRequest()
        if body is None:
            blocking.get(request, True, self.feedback)
        else:
            request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, content_type)
            blocking.post(request, QByteArray(json.dumps(body).encode('utf-8')), True, self.feedback)
        if self._stopped():
            return None
        reply = blocking.reply()
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        if not status or status in api.UNAVAILABLE_HTTP_STATUSES:
            self.error = api.not_responding(service)
            self.error_detail = blocking.errorMessage() or (f'HTTP {status}' if status else 'no answer')
            return None
        if status != 200:
            self.error = f'{name}: {api.describe_http_error(status, service)}'
            return None
        try:
            return json.loads(bytes(reply.content()).decode('utf-8'))
        except ValueError as e:
            self.error = f'{name}: unexpected response: {e}'
            return None

    def finished(self, result):
        if self.on_finished:
            self.on_finished(self, result)


class GeocodeTask(QgsTask):
    """Look up a postcode or place name with postcodes.io. Results are in self.places
    (places.Place list); self.error is set if the lookup failed."""

    def __init__(self, text, on_finished=None):
        super().__init__(f"Finding '{text}'")
        self.text = text
        self.on_finished = on_finished
        self.places = []
        self.error = None
        self.feedback = QgsFeedback()

    def cancel(self):
        self.feedback.cancel()
        super().cancel()

    def run(self):
        try:
            blocking = QgsBlockingNetworkRequest()
            blocking.get(QNetworkRequest(QUrl(places.lookup_url(self.text))), True, self.feedback)
            if self.isCanceled():
                return False
            reply = blocking.reply()
            status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            if status not in (200, 404):  # a postcode that isn't found is a 404 with details
                # (a 500 here is a server fault: only the Environment Agency uses it for "no data")
                self.error = (api.describe_http_error(status, service='postcodes.io') if status
                              else blocking.errorMessage() or 'network error')
                return False
            found = places.parse_lookup_response(self.text, json.loads(bytes(reply.content()).decode('utf-8')))
            if not places.is_postcode(self.text):
                # postcodes.io's places are Great Britain only: add Northern Ireland's (OSNI gazetteer), exact
                # matches first. Several names exist in both (Acton, Newcastle...), so they're offered as a
                # choice rather than jumping straight to NI.
                exact = places.match_ni_places(self.text, exact_only=True)
                partial = [p for p in places.match_ni_places(self.text) if p not in exact]
                found = exact + found + partial
            self.places = found
            return True
        except Exception as e:  # never let an unexpected error take QGIS down
            self.error = f'unexpected error: {e}'
            return False

    def finished(self, result):
        if self.on_finished:
            self.on_finished(self, result)
