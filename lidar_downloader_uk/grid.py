# SPDX-FileCopyrightText: 2025-2026 Simon Stoate
# SPDX-License-Identifier: GPL-2.0-or-later
"""The OSGB 5km grid layer: loading, styling, selection."""

import math
import os

from qgis.PyQt.QtCore import QObject, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QColor
from qgis.core import (
    Qgis, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsFeatureRequest, QgsFillSymbol, QgsGeometry,
    QgsPalLayerSettings, QgsProject, QgsProperty, QgsRectangle, QgsRuleBasedRenderer, QgsSymbolLayer,
    QgsTextBufferSettings, QgsTextFormat, QgsVectorLayer, QgsVectorLayerSimpleLabeling)

LAYER_NAME = 'OSGB Grid'
GRID_FILE = os.path.join('data', 'osgb_grid_5km.gpkg')
# Marks the plugin's own grid layer (saved with the project), so it never mistakes a user's layer for it
GRID_PROPERTY = 'lidar_downloader/grid'
# Shading for downloaded tiles: one colour for a single dataset, a palette when
# several datasets (years / resolutions) of a product are shown together.
DOWNLOADED_COLOUR = '#00c800'
DATASET_COLOURS = ('#1b9e77', '#d95f02', '#7570b3', '#e7298a', '#66a61e', '#e6ab02', '#a6761d', '#1f78b4',
                   '#b15928', '#6a3d9a')
# The grid is Ordnance Survey open data (see data/LICENSE-DATA.txt)
GRID_ATTRIBUTION = 'Contains OS data \u00a9 Crown copyright and database right.'
GRID_LICENCE = ('Open Government Licence v3.0: '
                'https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/')
# 't' if any source (Environment Agency, DataMapWales, Scottish portal) had data for the square when the grid
# was built (tools/build_grid.py).
# Used for shading and the bulk selection helpers; what can be downloaded is always checked live.
HAS_DATA_FIELD = 'HAS_DATA'
TILE_NAME_FIELD = 'TILE_NAME'
# Hide tile labels when zoomed out further than this (1:500,000): at national
# scale 10,000+ labels would just be clutter.
LABELS_MAX_SCALE = 500000
# Zoomed out beyond 1:1,000,000 the squares' outlines fade, to faint by 1:3,000,000, so the grid isn't a black block
# at national scale (the shading of tiles stays)
FADE_SCALES = (1000000, 3000000)


def _stroke_colour_key():
    """The symbol layer property for the outline colour, looked up by name as it's spelt differently across versions:
    Property.StrokeColor from QGIS 3.36, PropertyStrokeColor before. None if neither is there."""
    enum = getattr(QgsSymbolLayer, 'Property', None)
    for owner, name in ((enum, 'StrokeColor'), (enum, 'PropertyStrokeColor'), (QgsSymbolLayer, 'PropertyStrokeColor')):
        if owner is not None and hasattr(owner, name):
            return getattr(owner, name)
    return None


STROKE_COLOR = _stroke_colour_key()


def fading_outline(symbol, colour, faintest):
    """Make a fill symbol's outline fade as the map zooms out (FADE_SCALES): colour ('r,g,b') at 1:1,000,000 and
    closer, its opacity (0-255) down to faintest by 1:3,000,000. Returns the symbol."""
    if STROKE_COLOR is not None:  # (otherwise the outline just doesn't fade)
        near, far = FADE_SCALES
        symbol.symbolLayer(0).setDataDefinedProperty(STROKE_COLOR, QgsProperty.fromExpression(
            f'color_rgba({colour}, scale_linear(@map_scale, {near}, {far}, 255, {faintest}))'))
    return symbol


def open_grid(plugin_dir, name=LAYER_NAME):
    """The bundled OSGB 5km grid as a (not yet added) layer."""
    path = os.path.join(plugin_dir, GRID_FILE)
    return QgsVectorLayer(f'{path}|layername=osgb_grid_5km', name, 'ogr')


def intersecting_features(layer, geometries, crs, data_only=True, transform_context=None, touching=True,
                          known=()):
    """Grid features intersecting any of the geometries (given in crs); with data_only, only squares with data
    from one of the sources (HAS_DATA, or among known: tiles a search has found data for since); touching=False
    leaves out squares that only touch them (along an edge, as with a site boundary drawn along a tile's edge)."""
    context = transform_context or QgsProject.instance().transformContext()
    transform = QgsCoordinateTransform(crs, layer.crs(), context)
    found = {}
    for geometry in geometries:
        if geometry is None or geometry.isEmpty():
            continue
        geom = QgsGeometry(geometry)
        geom.transform(transform)
        engine = QgsGeometry.createGeometryEngine(geom.constGet())
        engine.prepareGeometry()
        # The GeoPackage spatial index makes the bounding box filter cheap
        for feature in layer.getFeatures(QgsFeatureRequest().setFilterRect(geom.boundingBox())):
            if feature.id() in found:
                continue
            if data_only and feature[HAS_DATA_FIELD] != 't' and feature[TILE_NAME_FIELD] not in known:
                continue
            square = feature.geometry().constGet()
            if engine.intersects(square) and (touching or not engine.touches(square)):
                found[feature.id()] = feature
    return list(found.values())


def search_areas(tile_extents, crs, transform_context=None):
    """The areas to search, one per 100 km square: [{'square', 'polygon', 'bbox'}] (tasks.SearchTask).

    tile_extents is {tile name: QgsRectangle in crs} (the grid's British National Grid). polygon is
    the tiles' bounding box as a GeoJSON Polygon in WGS84 (the Environment Agency's search takes one
    polygon); bbox is the same box in British National Grid (DataMapWales); squares is the box widened
    to whole 10 km squares, in WGS84 (the Scottish search: a survey's files in a square come together,
    see sources.parse_scotland_products).
    """
    groups = {}
    for tile, extent in tile_extents.items():
        rect = QgsRectangle(extent)
        if tile[:2] in groups:
            groups[tile[:2]].combineExtentWith(rect)
        else:
            groups[tile[:2]] = rect
    context = transform_context or QgsProject.instance().transformContext()
    to_wgs84 = QgsCoordinateTransform(crs, QgsCoordinateReferenceSystem('EPSG:4326'), context)
    to_bng = QgsCoordinateTransform(crs, QgsCoordinateReferenceSystem('EPSG:27700'), context)
    bng_to_wgs84 = QgsCoordinateTransform(QgsCoordinateReferenceSystem('EPSG:27700'),
                                          QgsCoordinateReferenceSystem('EPSG:4326'), context)

    def wgs84_ring(box, transform):
        r = transform.transformBoundingBox(box)
        return [[r.xMinimum(), r.yMinimum()], [r.xMaximum(), r.yMinimum()], [r.xMaximum(), r.yMaximum()],
                [r.xMinimum(), r.yMaximum()], [r.xMinimum(), r.yMinimum()]]

    areas = []
    for square, rect in groups.items():
        b = to_bng.transformBoundingBox(rect)
        tens = QgsRectangle(math.floor(b.xMinimum() / 10000 + 1e-6) * 10000,
                            math.floor(b.yMinimum() / 10000 + 1e-6) * 10000,
                            math.ceil(b.xMaximum() / 10000 - 1e-6) * 10000,
                            math.ceil(b.yMaximum() / 10000 - 1e-6) * 10000)
        areas.append({'square': square,
                      'polygon': {'type': 'Polygon', 'coordinates': [wgs84_ring(rect, to_wgs84)]},
                      'bbox': (b.xMinimum(), b.yMinimum(), b.xMaximum(), b.yMaximum()),
                      'squares': {'type': 'Polygon', 'coordinates': [wgs84_ring(tens, bng_to_wgs84)]}})
    return areas


class GridLayer(QObject):
    """Owns the grid layer in the current project.

    The layer is tracked by id rather than by object: if the user removes it
    from the Layers panel, the Python wrapper points at a deleted C++ object
    and any call on it raises RuntimeError.
    """

    selectionCountChanged = pyqtSignal(int)
    # The user removed the layer themselves (not via remove())
    removedExternally = pyqtSignal()

    def __init__(self, iface, plugin_dir, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.path = os.path.join(plugin_dir, GRID_FILE)
        self.layer_id = None
        # Tiles a search has found data for (HAS_DATA is a snapshot from when the grid was built: a survey
        # published since isn't in it)
        self.known_data = set()
        self.no_data = None  # the grid's squares without data (see mark_data)
        QgsProject.instance().layersWillBeRemoved.connect(self._on_layers_will_be_removed)

    def cleanup(self):
        try:
            QgsProject.instance().layersWillBeRemoved.disconnect(self._on_layers_will_be_removed)
        except (TypeError, RuntimeError):
            pass
        self.remove()

    def layer(self):
        """Return the grid layer if it is still in the project, else None."""
        if not self.layer_id:
            return None
        layer = QgsProject.instance().mapLayer(self.layer_id)
        if layer is None or not layer.isValid():
            return None
        return layer

    @staticmethod
    def _is_grid(layer):
        """True for the plugin's own grid layer, e.g. one saved in a project: marked as the plugin's, or (saved
        before layers were marked) named OSGB Grid and reading the grid file inside a copy of this plugin's folder
        (perhaps the older lidar_downloader one). Never a user's own layer, whatever its file is called."""
        if layer.customProperty(GRID_PROPERTY):
            return True
        path, _, options = layer.source().partition('|')
        folders = os.path.normcase(os.path.abspath(path)).split(os.sep)
        return (layer.name() == LAYER_NAME and 'layername=osgb_grid_5km' in options and len(folders) >= 3
                and folders[-1] == 'osgb_grid_5km.gpkg' and folders[-2] == 'data'
                and folders[-3].startswith('lidar_downloader'))

    def saved_grids(self):
        """Grid layers in the project that this session didn't add (e.g. a project saved with the grid, reopened)."""
        return [lyr for lyr in QgsProject.instance().mapLayers().values()
                if lyr.id() != self.layer_id and self._is_grid(lyr)]

    @staticmethod
    def _describe(layer):
        metadata = layer.metadata()
        metadata.setTitle('OSGB 5 km grid')
        metadata.setAbstract('The Ordnance Survey 5 km British National Grid tiles, marked where there is LIDAR data.')
        metadata.setRights([GRID_ATTRIBUTION])
        metadata.setLicenses([GRID_LICENCE])
        layer.setMetadata(metadata)

    def _adopt(self, layer, downloaded_tiles):
        """Take over a grid already in the project, pointed at this plugin's grid file. Returns False if it can't
        be read."""
        source = f'{self.path}|layername=osgb_grid_5km'
        if not layer.isValid() or os.path.normcase(os.path.abspath(layer.source().split('|')[0])) != \
                os.path.normcase(os.path.abspath(self.path)):
            layer.setDataSource(source, LAYER_NAME, 'ogr')
        if not layer.isValid():
            return False
        self.layer_id = layer.id()
        self._describe(layer)
        layer.setCustomProperty(GRID_PROPERTY, True)
        layer.selectionChanged.connect(self._on_selection_changed)
        node = QgsProject.instance().layerTreeRoot().findLayer(layer.id())
        if node:
            node.setItemVisibilityChecked(True)
        self.apply_style(downloaded_tiles)
        self.apply_labels()
        return True

    def load(self, downloaded_tiles=()):
        """Add the grid to the project (or make it visible). Returns False if it can't be loaded."""
        existing = self.layer()
        if existing is not None:
            node = QgsProject.instance().layerTreeRoot().findLayer(existing.id())
            if node:
                node.setItemVisibilityChecked(True)
            return True

        # A grid saved in the project: take it over rather than adding a second one
        saved = self.saved_grids()
        if saved:
            QgsProject.instance().removeMapLayers([lyr.id() for lyr in saved[1:]])
            return self._adopt(saved[0], downloaded_tiles)

        layer = open_grid(os.path.dirname(os.path.dirname(self.path)))
        if not layer.isValid():
            return False

        # Adding a layer can make QGIS zoom to it; restore the current view
        canvas = self.iface.mapCanvas()
        prev_extent = QgsRectangle(canvas.extent())

        # Add it at the top of the Layers panel
        self._describe(layer)
        layer.setCustomProperty(GRID_PROPERTY, True)
        QgsProject.instance().addMapLayer(layer, False)
        QgsProject.instance().layerTreeRoot().insertLayer(0, layer)
        self.layer_id = layer.id()
        layer.selectionChanged.connect(self._on_selection_changed)
        self.apply_style(downloaded_tiles)
        self.apply_labels()

        if not prev_extent.isEmpty():
            def restore_view():
                canvas.setExtent(prev_extent)
                canvas.refresh()
            QTimer.singleShot(50, restore_view)
        return True

    def move_to_top(self):
        """Put the grid at the top of the Layers panel (outside any group)."""
        layer = self.layer()
        if layer is None:
            return
        root = QgsProject.instance().layerTreeRoot()
        node = root.findLayer(layer.id())
        if node is None or (node.parent() is root and root.children().index(node) == 0):
            return
        parent = node.parent()
        root.insertChildNode(0, node.clone())
        parent.removeChildNode(node)

    def is_on_top(self):
        """True if the grid is the top layer of the Layers panel (new layers then go just below it)."""
        layer = self.layer()
        children = QgsProject.instance().layerTreeRoot().children()
        return layer is not None and bool(children) and getattr(children[0], 'layerId', lambda: None)() == layer.id()

    def remove(self):
        """Remove the grid, and any other copy of it in the project (e.g. saved with it)."""
        layer = self.layer()
        self.layer_id = None
        ids = ([layer.id()] if layer is not None else []) + [lyr.id() for lyr in self.saved_grids()]
        if ids:
            QgsProject.instance().removeMapLayers(ids)
            self.iface.mapCanvas().refresh()

    def apply_style(self, shading):
        """Hollow squares; downloaded tiles filled; squares with no data from any source faint (most are sea).
        Zoomed out, the outlines fade.

        shading is a list of (label, colour, tile names), one per dataset, e.g. from
        shading_entries(); a plain list of tile names is shaded as "Downloaded".
        """
        layer = self.layer()
        if layer is None:
            return
        if shading and isinstance(shading[0], str):
            shading = [('Downloaded', DOWNLOADED_COLOUR, shading)]

        base_symbol = fading_outline(QgsFillSymbol.createSimple(
            {'color': '0,0,0,0', 'outline_color': 'black', 'outline_width': '0.26', 'style': 'no'}), '0,0,0', 30)
        outside_symbol = fading_outline(QgsFillSymbol.createSimple(
            {'color': '0,0,0,0', 'style': 'no', 'outline_color': '190,190,190', 'outline_width': '0.1'}),
            '190,190,190', 0)

        root_rule = QgsRuleBasedRenderer.Rule(None)
        for label, colour, tiles in shading or []:
            if not tiles:
                continue
            c = QColor(colour)
            symbol = fading_outline(QgsFillSymbol.createSimple(
                {'color': f'{c.red()},{c.green()},{c.blue()},110', 'outline_color': 'black',
                 'outline_style': 'solid', 'outline_width': '0.26', 'style': 'solid'}), '0,0,0', 30)
            tile_list = ",".join(f"'{t}'" for t in sorted(tiles))
            rule = QgsRuleBasedRenderer.Rule(symbol)
            rule.setFilterExpression(f'"TILE_NAME" IN ({tile_list})')
            rule.setLabel(label)
            root_rule.appendChild(rule)

        rule_outside = QgsRuleBasedRenderer.Rule(outside_symbol)
        known = ",".join(f"'{t}'" for t in sorted(self.known_data))
        rule_outside.setFilterExpression(f"\"{HAS_DATA_FIELD}\" = 'f'"
                                         + (f" AND \"{TILE_NAME_FIELD}\" NOT IN ({known})" if known else ''))
        rule_outside.setLabel('No data')
        root_rule.appendChild(rule_outside)

        rule_others = QgsRuleBasedRenderer.Rule(base_symbol)
        rule_others.setFilterExpression('ELSE')
        rule_others.setLabel('Available')
        root_rule.appendChild(rule_others)

        layer.setRenderer(QgsRuleBasedRenderer(root_rule))
        layer.triggerRepaint()
        self.iface.layerTreeView().refreshLayerSymbology(layer.id())

    def apply_labels(self):
        """Label each square with its tile name, centred, with a white halo."""
        layer = self.layer()
        if layer is None:
            return

        text_format = QgsTextFormat()
        text_format.setSize(8)
        text_format.setColor(QColor(40, 40, 40))
        buffer = QgsTextBufferSettings()
        buffer.setEnabled(True)
        buffer.setSize(0.8)
        buffer.setColor(QColor(255, 255, 255))
        text_format.setBuffer(buffer)

        settings = QgsPalLayerSettings()
        settings.fieldName = TILE_NAME_FIELD
        settings.setFormat(text_format)
        # Centred on the square (the enum moved to Qgis.LabelPlacement in QGIS 3.26)
        if hasattr(Qgis, 'LabelPlacement'):
            settings.placement = Qgis.LabelPlacement.OverPoint
        else:
            settings.placement = QgsPalLayerSettings.Placement.OverPoint
        settings.scaleVisibility = True
        settings.minimumScale = LABELS_MAX_SCALE  # "minimum" = most zoomed-out scale

        layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
        layer.setLabelsEnabled(True)
        layer.triggerRepaint()

    def selected_tile_names(self):
        return [name for name, _ in self.selected_tiles()]

    def selected_tile_extents(self):
        """Return {tile name: QgsRectangle} for the selected tiles, in the grid's CRS."""
        layer = self.layer()
        if layer is None:
            return {}
        return {f[TILE_NAME_FIELD]: f.geometry().boundingBox() for f in layer.selectedFeatures()}

    def selected_tiles(self):
        """Return [(tile name, has data)] for the selected tiles."""
        layer = self.layer()
        if layer is None:
            return []
        return [(f[TILE_NAME_FIELD], f[HAS_DATA_FIELD] == 't') for f in layer.selectedFeatures()]

    def select_intersecting(self, geometries, crs, data_only=True, mode='replace', touching=True):
        """Select the tiles intersecting any of the geometries (given in crs).

        mode 'replace' replaces the current selection, 'add' adds to it, 'remove' takes
        the tiles out of it, 'intersect' keeps only the selected tiles among them, and
        'toggle' adds the tiles that aren't selected and removes those that are. Returns
        the number of tiles found, or None if the grid isn't loaded. touching=False leaves out tiles that only
        touch the geometries along an edge.
        """
        layer = self.layer()
        if layer is None:
            return None
        ids = [f.id() for f in intersecting_features(layer, geometries, crs, data_only, touching=touching,
                                                     known=self.known_data)]
        selected = set(layer.selectedFeatureIds())
        if mode == 'toggle':
            layer.selectByIds(list(selected.symmetric_difference(ids)))
        elif mode == 'add':
            layer.selectByIds(list(selected | set(ids)))
        elif mode == 'remove':
            layer.selectByIds(list(selected - set(ids)))
        elif mode == 'intersect':
            layer.selectByIds(list(selected & set(ids)))
        else:
            layer.selectByIds(ids)
        return len(ids)

    def tiles_in(self, rect, crs, limit):
        """{tile name: QgsRectangle in the grid's CRS} for the tiles in rect (given in crs), squares without data
        too (a survey may have been published since the grid was built), or None if more than limit of them have
        data (or the grid isn't loaded)."""
        layer = self.layer()
        if layer is None:
            return None
        transform = QgsCoordinateTransform(crs, layer.crs(), QgsProject.instance().transformContext())
        try:
            box = transform.transformBoundingBox(rect)
        except Exception:  # e.g. a view of the whole world in a projection that can't hold it
            return None
        if box.width() * box.height() > limit * 5000 * 5000 * 4:
            return None  # far too big: don't even read the features
        tiles, with_data = {}, 0
        for feature in layer.getFeatures(QgsFeatureRequest().setFilterRect(box)):
            tiles[feature[TILE_NAME_FIELD]] = feature.geometry().boundingBox()
            with_data += feature[HAS_DATA_FIELD] == 't' or feature[TILE_NAME_FIELD] in self.known_data
            if with_data > limit:
                return None
        return tiles

    def mark_data(self, tiles):
        """Tiles a search found data for count as having data from now on (only those the grid has as without data
        are kept: the rest have it already). Returns True if that's news."""
        if self.no_data is None:  # (read once: the squares without data, most of them sea)
            grid = open_grid(os.path.dirname(os.path.dirname(self.path)))
            request = QgsFeatureRequest().setFilterExpression(f"\"{HAS_DATA_FIELD}\" != 't'")
            self.no_data = {f[TILE_NAME_FIELD] for f in grid.getFeatures(request)}
            grid = None
        new = (set(tiles) & self.no_data) - self.known_data
        self.known_data |= new
        return bool(new)

    def select_names(self, names):
        """Select exactly these tiles (by name). Returns the number found."""
        layer = self.layer()
        if layer is None:
            return 0
        wanted = set(names)
        request = QgsFeatureRequest().setFilterExpression(
            f'"{TILE_NAME_FIELD}" IN (' + ','.join(f"'{n}'" for n in sorted(wanted)) + ')') if wanted else None
        ids = [f.id() for f in layer.getFeatures(request)] if request is not None else []
        layer.selectByIds(ids)
        return len(ids)

    def clear_selection(self):
        layer = self.layer()
        if layer is not None:
            layer.removeSelection()

    def _on_selection_changed(self):
        layer = self.layer()
        self.selectionCountChanged.emit(layer.selectedFeatureCount() if layer else 0)

    def _on_layers_will_be_removed(self, layer_ids):
        if self.layer_id and self.layer_id in layer_ids:
            self.layer_id = None
            self.removedExternally.emit()
