# SPDX-FileCopyrightText: 2025-2026 Simon Stoate
# SPDX-License-Identifier: GPL-2.0-or-later
"""Map tools: pick tiles on the map, and draw an area of interest."""

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
    """Click a tile to add it to / remove it from the selection; drag a box to add every tile under it."""

    # (geometry in the canvas CRS, is_click): a click toggles one tile, a drag adds tiles
    picked = pyqtSignal(object, bool)

    def __init__(self, canvas):
        super().__init__(canvas, QColor(31, 120, 180))
        self.start = None
        self.dragging = False

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
        self.picked.emit(geometry, is_click)


class DrawAreaTool(_BandTool):
    """Drag a rectangle, or click points for a polygon (right-click or double-click to finish, Esc to cancel)."""

    drawn = pyqtSignal(object)  # QgsGeometry in the canvas CRS

    def __init__(self, canvas):
        super().__init__(canvas, QColor(227, 26, 28))
        self.points = []
        self.start = None
        self.dragging = False

    def reset(self):
        self.points = []
        self.start = None
        self.dragging = False
        self.clear()

    def _show_polygon(self, extra=None):
        points = self.points + ([extra] if extra is not None else [])
        if len(points) >= 2:
            self.show(QgsGeometry.fromPolygonXY([points]))

    def _finish_polygon(self):
        points = self.points
        self.reset()
        if len(points) >= 3:
            self.drawn.emit(QgsGeometry.fromPolygonXY([points]))

    def canvasPressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self._finish_polygon()
        elif event.button() == Qt.MouseButton.LeftButton:
            self.start = event.pos()
            self.dragging = False

    def canvasMoveEvent(self, event):
        point = QgsPointXY(self.toMapCoordinates(event.pos()))
        if self.start is not None and not self.points and \
                (event.pos() - self.start).manhattanLength() > DRAG_PIXELS:
            self.dragging = True
            self.show(QgsGeometry.fromRect(QgsRectangle(QgsPointXY(self.toMapCoordinates(self.start)), point)))
        elif self.points:
            self._show_polygon(point)

    def canvasReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self.start is None:
            return
        if self.dragging:
            rect = QgsRectangle(self.toMapCoordinates(self.start), self.toMapCoordinates(event.pos()))
            self.reset()
            self.drawn.emit(QgsGeometry.fromRect(rect))
            return
        self.start = None
        point = QgsPointXY(self.toMapCoordinates(event.pos()))
        # A double-click also sends a second press/release at the same point: don't add it twice
        if not self.points or self.points[-1] != point:
            self.points.append(point)
        self._show_polygon()

    def canvasDoubleClickEvent(self, event):
        self._finish_polygon()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.reset()

    def deactivate(self):
        self.points = []
        self.start = None
        self.dragging = False
        super().deactivate()
