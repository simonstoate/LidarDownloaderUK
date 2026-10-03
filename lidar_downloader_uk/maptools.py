# SPDX-FileCopyrightText: 2025-2026 Simon Stoate
# SPDX-License-Identifier: GPL-2.0-or-later
"""The map tool for picking tiles on the map."""

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor
from qgis.core import Qgis, QgsGeometry, QgsPointXY, QgsRectangle, QgsWkbTypes
from qgis.gui import QgsMapTool, QgsRubberBand

DRAG_PIXELS = 4  # movement before a press counts as a drag


def _polygon_type():
    # The geometry type enum moved to Qgis.GeometryType in QGIS 3.30
    return Qgis.GeometryType.Polygon if hasattr(Qgis, 'GeometryType') else QgsWkbTypes.GeometryType.PolygonGeometry


class _BandTool(QgsMapTool):
    """A map tool that sketches with a rubber band (removed from the canvas by release())."""

    def __init__(self, canvas, colour):
        super().__init__(canvas)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.band = QgsRubberBand(canvas, _polygon_type())
        self.band.setColor(colour)
        fill = QColor(colour)
        fill.setAlpha(40)
        self.band.setFillColor(fill)
        self.band.setWidth(2)

    def show(self, geometry):
        if self.band is not None:
            self.band.setToGeometry(geometry, None)

    def clear(self):
        if self.band is not None:
            self.band.reset(_polygon_type())

    def release(self):
        """Remove the rubber band from the canvas (call before the plugin unloads)."""
        if self.band is not None:
            self.clear()
            self.canvas().scene().removeItem(self.band)
            self.band = None

    def deactivate(self):
        self.clear()
        super().deactivate()


class TileSelectTool(_BandTool):
    """Click a tile to select it, or drag a box to select every tile under it, the way QGIS's own Select Features
    tool works: on their own they replace the selection; with Shift or Ctrl held they change it (selection_mode)."""

    # (geometry in the canvas CRS, is_click, mode): mode is how the tiles under it change the selection
    picked = pyqtSignal(object, bool, str)

    def __init__(self, canvas):
        super().__init__(canvas, QColor(31, 120, 180))
        self.start = None
        self.dragging = False

    @staticmethod
    def selection_mode(modifiers, is_click):
        """How a click or a dragged box changes the selection, as QGIS's Select Features tool does it
        (QgsMapToolSelectUtils): 'replace' with no key held. A click with Shift or Ctrl is 'toggle': it adds the
        tile, or removes it if it's selected. A box is 'add' with Shift, 'remove' with Ctrl, and with both
        'intersect' (keeps the selected tiles under it)."""
        shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        ctrl = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        if is_click:
            return 'toggle' if shift or ctrl else 'replace'
        if shift and ctrl:
            return 'intersect'
        return 'add' if shift else 'remove' if ctrl else 'replace'

    def canvasPressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.start = event.pos()
            self.dragging = False

    def canvasMoveEvent(self, event):
        if self.start is None:
            return
        if (event.pos() - self.start).manhattanLength() > DRAG_PIXELS:
            self.dragging = True
            self.show(QgsGeometry.fromRect(QgsRectangle(self.toMapCoordinates(self.start),
                                                        self.toMapCoordinates(event.pos()))))

    def canvasReleaseEvent(self, event):
        if self.start is None or event.button() != Qt.MouseButton.LeftButton:
            return
        if self.dragging:
            rect = QgsRectangle(self.toMapCoordinates(self.start), self.toMapCoordinates(event.pos()))
            geometry, is_click = QgsGeometry.fromRect(rect), False
        else:
            geometry, is_click = QgsGeometry.fromPointXY(QgsPointXY(self.toMapCoordinates(event.pos()))), True
        self.start = None
        self.dragging = False
        self.clear()
        self.picked.emit(geometry, is_click, self.selection_mode(event.modifiers(), is_click))
