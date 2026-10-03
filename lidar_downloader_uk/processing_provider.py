# SPDX-FileCopyrightText: 2025-2026 Simon Stoate
# SPDX-License-Identifier: GPL-2.0-or-later
"""Processing Toolbox support: "Download LIDAR / aerial tiles", for models, batch jobs and scripts.

It shares the dock's download folder layout, so tiles downloaded by either are reused by the other.
"""

import os

from qgis.PyQt.QtCore import QCoreApplication, QSettings, QStandardPaths
from qgis.PyQt.QtGui import QIcon
from qgis.core import (
    Qgis, QgsGeometry, QgsProcessing, QgsProcessingAlgorithm, QgsProcessingContext, QgsProcessingException,
    QgsProcessingOutputFolder, QgsProcessingOutputNumber, QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum, QgsProcessingParameterExtent, QgsProcessingParameterFeatureSource,
    QgsProcessingParameterFileDestination, QgsProcessingParameterFolderDestination, QgsProcessingParameterString,
    QgsProcessingProvider)

from . import api, postprocess, storage
from .grid import intersecting_features, open_grid, search_areas
from .tasks import DownloadTilesTask, SearchTask

# Same setting the dock uses, so both default to the same folder
SETTINGS_KEY_DIR = 'LidarDownloader/download_dir'
# Refuse areas bigger than this (about 7,000 km2 / roughly 1 TB of 1m DTM)
MAX_TILES = 300
HELP_URL = 'https://github.com/simonstoate/LidarDownloaderUK#readme'


def default_folder():
    """The dock's download folder, or (the panel never opened) Documents/LiDAR Data: never a temporary folder."""
    return QSettings().value(SETTINGS_KEY_DIR) or os.path.join(
        QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation), 'LiDAR Data')


def _any_geometry_type():
    # The source type enum moved to Qgis.ProcessingSourceType in QGIS 3.36
    if hasattr(Qgis, 'ProcessingSourceType'):
        return Qgis.ProcessingSourceType.VectorAnyGeometry
    return QgsProcessing.SourceType.TypeVectorAnyGeometry


class LidarDownloaderProvider(QgsProcessingProvider):
    """The plugin's group in the Processing Toolbox (the provider id 'lidardownloader' is kept from before the
    plugin was renamed, so saved models still find the algorithm)."""

    def __init__(self, plugin_dir):
        super().__init__()
        self.plugin_dir = plugin_dir

    def id(self):
        return 'lidardownloader'

    def name(self):
        return 'LIDAR Downloader UK'

    def icon(self):
        return QIcon(os.path.join(self.plugin_dir, 'icon.svg'))

    def loadAlgorithms(self):
        self.addAlgorithm(DownloadTilesAlgorithm(self.plugin_dir))


class DownloadTilesAlgorithm(QgsProcessingAlgorithm):
    """Download a dataset for the tiles covering an area, the way the panel does, and optionally make a (cropped)
    VRT mosaic or a clipped GeoTIFF of them. Runs in Processing's worker thread: nothing here touches the UI."""

    INPUT = 'INPUT'
    EXTENT = 'EXTENT'
    PRODUCT = 'PRODUCT'
    YEAR = 'YEAR'
    RESOLUTION = 'RESOLUTION'
    FOLDER = 'FOLDER'
    OVERWRITE = 'OVERWRITE'
    ACCEPT_TERMS = 'ACCEPT_TERMS'
    DELETE_ZIPS = 'DELETE_ZIPS'
    CONVERT_MM = 'CONVERT_MM'
    BUILD_VRT = 'BUILD_VRT'
    OUTPUT_VRT = 'OUTPUT_VRT'
    CROP_VRT = 'CROP_VRT'
    OUTPUT_CLIPPED = 'OUTPUT_CLIPPED'
    DOWNLOADED = 'DOWNLOADED'
    FAILED = 'FAILED'
    UNAVAILABLE = 'UNAVAILABLE'

    def __init__(self, plugin_dir):
        super().__init__()
        self.plugin_dir = plugin_dir

    def tr(self, text):
        return QCoreApplication.translate('DownloadTilesAlgorithm', text)

    def createInstance(self):
        return DownloadTilesAlgorithm(self.plugin_dir)

    def name(self):
        return 'downloadtiles'

    def displayName(self):
        return self.tr('Download LIDAR / aerial tiles')

    def helpUrl(self):
        return HELP_URL

    def shortHelpString(self):
        return self.tr(
            'Downloads LIDAR data (and aerial photography) for the 5 km OSGB grid tiles covering an area: England '
            'from the Environment Agency, Wales from DataMapWales and Scotland from the Scottish Remote Sensing '
            'Portal. Some Scottish surveys are licensed for non-commercial use only: check the licence in the '
            'layers\' metadata.\n\n'
            'Area: a layer (tick "Selected features only" to use a selection) and/or a map extent.\n'
            'Dataset: the product, a survey year or "latest" (each tile\'s newest survey), and a resolution in '
            'metres (e.g. 1, 0.5 or 50cm), or blank for the finest available.\n\n'
            'Tiles go into the download folder (the dock\'s, unless you choose another) using the same layout as '
            'the LIDAR Downloader UK dock, so tiles already downloaded are reused unless "Download again" is '
            'ticked.\n\n'
            'Raster and imagery datasets can be combined into a VRT mosaic (a small file that reads the tiles), '
            'which is loaded when the algorithm finishes. Tick "Crop the VRT" to crop it to the input layer\'s '
            'polygons: download and crop in one go. Optionally, also save the tiles clipped to the input layer\'s '
            'polygons as a standalone GeoTIFF copy (Cloud-Optimised).')

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.INPUT, self.tr('Area: tiles intersecting this layer'), [_any_geometry_type()], optional=True))
        self.addParameter(QgsProcessingParameterExtent(
            self.EXTENT, self.tr('Area: tiles intersecting this extent'), optional=True))
        self.addParameter(QgsProcessingParameterEnum(
            self.PRODUCT, self.tr('Product'), options=[api.product_label(p) for p in api.PRODUCTS], defaultValue=0))
        self.addParameter(QgsProcessingParameterString(
            self.YEAR, self.tr('Survey year ("latest" for each tile\'s newest)'), defaultValue=api.LATEST))
        self.addParameter(QgsProcessingParameterString(
            self.RESOLUTION, self.tr('Resolution in metres (blank for the finest available)'),
            defaultValue='', optional=True))
        self.addParameter(QgsProcessingParameterFolderDestination(
            self.FOLDER, self.tr('Download folder'), defaultValue=default_folder()))
        self.addParameter(QgsProcessingParameterBoolean(
            self.OVERWRITE, self.tr('Download again tiles that are already downloaded'), defaultValue=False))
        self.addParameter(QgsProcessingParameterBoolean(
            self.DELETE_ZIPS, self.tr('Delete zip files after extracting'), defaultValue=False))
        self.addParameter(QgsProcessingParameterBoolean(
            self.CONVERT_MM, self.tr('Convert Welsh archive heights from millimetres to metres (rewrites the '
                                     'files; NRW published them in millimetres)'), defaultValue=False))
        self.addParameter(QgsProcessingParameterBoolean(
            self.ACCEPT_TERMS, self.tr('Accept the licence terms of surveys for non-commercial use only '
                                       '(some Scottish surveys)'), defaultValue=False))
        self.addParameter(QgsProcessingParameterBoolean(
            self.BUILD_VRT, self.tr('Build a VRT mosaic (rasters and imagery)'), defaultValue=True))
        self.addParameter(QgsProcessingParameterFileDestination(
            self.OUTPUT_VRT, self.tr('VRT mosaic'), fileFilter='VRT files (*.vrt)', optional=True,
            createByDefault=False))
        self.addParameter(QgsProcessingParameterBoolean(
            self.CROP_VRT, self.tr('Crop the VRT to the input layer\'s polygons'), defaultValue=False))
        self.addParameter(QgsProcessingParameterFileDestination(
            self.OUTPUT_CLIPPED, self.tr('Clipped to the input layer (GeoTIFF copy)'), fileFilter='GeoTIFF (*.tif)',
            optional=True, createByDefault=False))
        self.addOutput(QgsProcessingOutputFolder(self.FOLDER, self.tr('Dataset folder')))
        self.addOutput(QgsProcessingOutputNumber(self.DOWNLOADED, self.tr('Tiles downloaded or reused')))
        self.addOutput(QgsProcessingOutputNumber(self.FAILED, self.tr('Tiles that failed')))
        self.addOutput(QgsProcessingOutputNumber(self.UNAVAILABLE, self.tr('Tiles without this dataset')))

    # ---------------------------------------------------------------- run

    def processAlgorithm(self, parameters, context, feedback):
        grid_crs, extents = self._tiles_in_area(parameters, context, feedback)
        wanted = self._wanted_dataset(parameters, context)
        feedback.pushInfo(self.tr('Dataset: {}').format(api.dataset_label(wanted)))
        found = self._available(wanted, grid_crs, extents, parameters, context, feedback)
        if found is None:  # cancelled
            return {}
        wanted, available, unavailable = found

        # Each tile goes into the folder of the survey it comes from (<product>/<res>/<year>),
        # the same layout the dock uses, so tiles downloaded by either are reused.
        root = self.parameterAsString(parameters, self.FOLDER, context)
        folders = {t: os.path.join(root, api.dataset_folder(o.dataset)) for t, o in available.items()}
        for tile, offering in available.items():
            if offering.rights:  # kept with the download, for the layers' metadata
                storage.write_survey_info(folders[tile], {
                    'dataset': api.dataset_label(offering.dataset), 'rights': offering.rights})
        resolved = {o.dataset for o in available.values()}
        folder_dataset = next(iter(resolved)) if len(resolved) == 1 else wanted
        dataset_dir = os.path.join(root, api.dataset_folder(folder_dataset))
        os.makedirs(dataset_dir, exist_ok=True)
        feedback.pushInfo(self.tr('Folder: {}').format(dataset_dir))

        overwrite = self.parameterAsBoolean(parameters, self.OVERWRITE, context)
        reuse = set() if overwrite else {t for t in available if storage.is_downloaded(folders[t], t)}
        if reuse:
            feedback.pushInfo(self.tr('Reusing {} tile(s) already downloaded.').format(len(reuse)))
        download = DownloadTilesTask(
            sorted(available), dataset_dir, reuse,
            delete_zips=self.parameterAsBoolean(parameters, self.DELETE_ZIPS, context),
            dataset=folder_dataset, urls={t: o.url for t, o in available.items() if o.url}, folders=folders,
            files={t: o.files for t, o in available.items() if o.files}, feedback=feedback)
        download.run()
        if feedback.isCanceled():
            return {}

        succeeded = sorted(t for t, err in download.results.items() if err is None)
        failed = [f'{t}: {err}' + (f' ({download.details[t]})' if t in download.details else '')
                  for t, err in sorted(download.results.items()) if err]
        for message in failed:
            feedback.reportError(message, fatalError=False)
        feedback.pushInfo(self.tr('{} tile(s) ready, {} failed.').format(len(succeeded), len(failed)))

        if self.parameterAsBoolean(parameters, self.CONVERT_MM, context):  # only if asked: it rewrites the files
            for tile in succeeded:
                if available[tile].dataset.product.startswith('wales_lidar_archive'):
                    postprocess.convert_mm_grids(folders[tile])
        # Worst survey first, best last: in the mosaic the best is drawn on top, the others fill its gaps
        ranked = sorted((api.survey_rank(available[t].dataset), path) for t in succeeded
                        for path in storage.find_raster_files(folders[t], t))
        files = list(dict.fromkeys(path for _, path in ranked))
        results = {self.FOLDER: dataset_dir, self.DOWNLOADED: len(succeeded), self.FAILED: len(failed),
                   self.UNAVAILABLE: len(unavailable), self.OUTPUT_VRT: None}
        if self.parameterAsBoolean(parameters, self.BUILD_VRT, context) and succeeded:
            results[self.OUTPUT_VRT] = self._build_vrt(parameters, context, feedback, dataset_dir, files,
                                                       folder_dataset)
        results[self.OUTPUT_CLIPPED] = self._clip(parameters, context, feedback, files, folder_dataset)
        return results

    def _wanted_dataset(self, parameters, context):
        """The dataset asked for. Its year is as typed until _available matches it to the surveys found."""
        product = list(api.PRODUCTS)[self.parameterAsEnum(parameters, self.PRODUCT, context)]
        year = (self.parameterAsString(parameters, self.YEAR, context) or api.LATEST).strip().lower()
        typed = self.parameterAsString(parameters, self.RESOLUTION, context)
        try:
            resolution = api.parse_resolution(typed)  # '1', '1.0', '1 m', '50cm'...
        except ValueError:
            raise QgsProcessingException(self.tr(
                "Resolution '{}' is not a number of metres (e.g. 1, 0.5 or 50cm); leave it blank for the finest "
                "available.").format(typed)) from None
        return api.Dataset(product, year, resolution)

    def _available(self, wanted, grid_crs, extents, parameters, context, feedback):
        """Ask the services which of the tiles have the dataset. Returns (wanted, {tile: Offering}, [tiles without
        it]), with wanted's survey year matched to the surveys found ('2020-22' or '2011' typed); None if
        cancelled. Raises QgsProcessingException if there's nothing to download, or licence terms to accept."""
        # The feedback is passed in (not connected to signals): this runs in Processing's worker thread
        search = SearchTask(search_areas(extents, grid_crs, context.transformContext()), feedback=feedback)
        feedback.pushInfo(self.tr('Checking which tiles have this dataset...'))
        search.run()
        if feedback.isCanceled():
            return None
        if search.error and not search.offerings:
            raise QgsProcessingException(self.tr("Couldn't check what's available: {}").format(search.error))
        unchecked = sorted(t for t in extents if t[:2] in search.failed_squares)
        if search.error:
            feedback.pushWarning(self.tr("One of the services didn't answer ({}), so {} tile(s) couldn't be "
                                         "checked: {}").format(search.error, len(unchecked), ', '.join(unchecked)))
        if not wanted.is_latest:
            year, choices = api.resolve_year(wanted.product, wanted.year, [o.dataset for o in search.offerings])
            if year is None and len(choices) > 1:
                raise QgsProcessingException(self.tr(
                    'Several {} surveys match "{}": {}. Type one of these as the survey year.').format(
                    api.product_label(wanted.product), wanted.year, ', '.join(choices)))
            if year:
                wanted = api.Dataset(wanted.product, year, wanted.resolution)

        by_tile = {}
        for offering in search.offerings:
            by_tile.setdefault(offering.tile, []).append(offering)
        chosen = {t: api.pick_offering(by_tile.get(t, []), wanted) for t in extents if t not in unchecked}
        available = {t: o for t, o in chosen.items() if o}
        unavailable = sorted(t for t, o in chosen.items() if not o)
        if unavailable:
            feedback.pushWarning(self.tr('{} tile(s) have no {}: {}').format(
                len(unavailable), api.dataset_label(wanted), ', '.join(unavailable)))
        if not available and unchecked:
            raise QgsProcessingException(self.tr("Couldn't check what's available: {}").format(search.error))
        if not available:
            found = api.sorted_datasets(api.summarise(search.offerings, list(extents)))
            hint = '; '.join(f'{api.dataset_label(d)} (year "{d.year}")' for d in found[:8])
            raise QgsProcessingException(
                self.tr('None of the {} tile(s) have {}.').format(len(extents), api.dataset_label(wanted))
                + (self.tr(' Available here: {}').format(hint) if hint else ''))

        restricted = {o.dataset: o.rights for o in available.values() if api.is_non_commercial(o.rights)}
        if restricted and not self.parameterAsBoolean(parameters, self.ACCEPT_TERMS, context):
            terms = '\n\n'.join(f'{api.dataset_label(d)}:\n{text}' for d, text in restricted.items())
            raise QgsProcessingException(self.tr(
                'These surveys are licensed for non-commercial use only. If your use is allowed, tick "Accept '
                'the licence terms..." and run again.\n\n') + terms)
        return wanted, available, unavailable

    def _cutline(self, parameters, context, sample_file):
        """The input layer's polygons as a GeoJSON cutline, or None if it has none."""
        source = self.parameterAsSource(parameters, self.INPUT, context)
        if source is None:
            return None
        return postprocess.cutline_from_geometries(
            [f.geometry() for f in source.getFeatures() if f.hasGeometry()], source.sourceCrs(), sample_file,
            context.transformContext())

    def _clip(self, parameters, context, feedback, files, dataset):
        """The optional GeoTIFF copy of the tiles clipped to the input layer's polygons: its path, or None."""
        clipped_path = self.parameterAsFileOutput(parameters, self.OUTPUT_CLIPPED, context)
        if not clipped_path:
            return None
        if api.product_kind(dataset.product) not in (api.RASTER, api.IMAGERY) or not files:
            feedback.pushWarning(self.tr('No clipped output: it needs downloaded rasters or imagery.'))
            return None
        cutline = self._cutline(parameters, context, files[0])
        if cutline is None:
            feedback.pushWarning(self.tr('No clipped output: the input layer has no polygons.'))
            return None
        feedback.pushInfo(self.tr('Clipping to the input layer...'))
        black = api.black_is_data(dataset.product)
        mosaic = postprocess.mosaic(files, black)
        try:
            postprocess.clip(mosaic, clipped_path, cutline, progress=self._progress(feedback), black_is_data=black)
        finally:
            postprocess.release(mosaic)
        self._load_on_completion(context, clipped_path, f'{api.dataset_label(dataset)} clipped', self.OUTPUT_CLIPPED)
        return clipped_path

    @staticmethod
    def _progress(feedback):
        """A progress callback (fraction done -> keep going?) reporting to the Processing feedback."""
        def progress(fraction):
            feedback.setProgress(100 * fraction)
            return not feedback.isCanceled()
        return progress

    @staticmethod
    def _load_on_completion(context, path, name, output_name):
        """Add an output to the project when the algorithm finishes (or refresh it if it's already loaded)."""
        project = context.project()
        if project is None:
            return
        target = os.path.normcase(os.path.abspath(path))
        for layer in project.mapLayers().values():
            if os.path.normcase(os.path.abspath(layer.source().split('|')[0])) == target:
                layer.reload()  # already loaded: refresh it rather than add a duplicate
                layer.triggerRepaint()
                return
        context.addLayerToLoadOnCompletion(path, QgsProcessingContext.LayerDetails(name, project, output_name))

    def _tiles_in_area(self, parameters, context, feedback):
        """Tiles with data intersecting the input layer and/or extent: (grid CRS, {tile: extent})."""
        grid = open_grid(self.plugin_dir)
        if not grid.isValid():
            raise QgsProcessingException(self.tr('Could not open the bundled OSGB grid.'))

        features = []
        source = self.parameterAsSource(parameters, self.INPUT, context)
        if source is not None:
            geometries = [f.geometry() for f in source.getFeatures() if f.hasGeometry()]
            features += intersecting_features(grid, geometries, source.sourceCrs(),
                                              transform_context=context.transformContext())
        if parameters.get(self.EXTENT):
            extent = self.parameterAsExtent(parameters, self.EXTENT, context)
            crs = self.parameterAsExtentCrs(parameters, self.EXTENT, context)
            if not extent.isEmpty():
                features += intersecting_features(grid, [QgsGeometry.fromRect(extent)], crs,
                                                  transform_context=context.transformContext())
        if source is None and not parameters.get(self.EXTENT):
            raise QgsProcessingException(self.tr('Choose an area: an input layer and/or an extent.'))

        extents = {f['TILE_NAME']: f.geometry().boundingBox() for f in features}
        if not extents:
            raise QgsProcessingException(self.tr('The area has no tiles with LIDAR data (England, Wales, Scotland and '
                                                 'their coasts).'))
        if len(extents) > MAX_TILES:
            raise QgsProcessingException(self.tr(
                'The area covers {} tiles; the limit is {}. Use a smaller area.').format(len(extents), MAX_TILES))
        feedback.pushInfo(self.tr('{} tile(s) in the area: {}').format(len(extents), ', '.join(sorted(extents))))
        return grid.crs(), extents

    def _build_vrt(self, parameters, context, feedback, dataset_dir, files, dataset):
        """The optional VRT mosaic of the tiles (cropped to the input layer if asked): its path, or None."""
        if api.product_kind(dataset.product) not in (api.RASTER, api.IMAGERY):
            feedback.pushInfo(self.tr('No VRT: point clouds and photos can\'t be mosaicked.'))
            return None
        if not files:
            return None
        output = self.parameterAsFileOutput(parameters, self.OUTPUT_VRT, context)
        if not output:
            # Named after the tiles too, so each row of a batch run gets its own
            stem = api.dataset_folder(dataset).replace(os.sep, '_')
            output = os.path.join(dataset_dir, postprocess.unique_name(files, stem, '.vrt'))
        cutline = None
        if self.parameterAsBoolean(parameters, self.CROP_VRT, context):
            cutline = self._cutline(parameters, context, files[0])
            if cutline is None:
                feedback.pushWarning(self.tr('The VRT is not cropped: the input layer has no polygons.'))
        black = api.black_is_data(dataset.product)  # (night photos: black is part of the picture)
        feedback.pushInfo(self.tr('Building VRT from {} file(s)').format(len(files))
                          + (', cropped...' if cutline else '...'))
        try:
            if cutline:
                # The cropped VRT reads a mosaic VRT of the tiles, kept with the dataset's other mosaics
                source = postprocess.mosaic_file(files, os.path.join(dataset_dir, 'mosaics'), black)
                postprocess.crop_vrt(source, output, cutline, black)
            else:
                postprocess.build_vrt(files, output, black)
        except RuntimeError as e:
            raise QgsProcessingException(self.tr('Could not build the VRT: {}').format(e)) from e
        name = api.dataset_label(dataset) + (' (cropped)' if cutline else '')
        self._load_on_completion(context, output, name, self.OUTPUT_VRT)
        return output
