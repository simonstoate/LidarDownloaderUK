# SPDX-FileCopyrightText: 2025-2026 Simon Stoate
# SPDX-License-Identifier: GPL-2.0-or-later
"""The plugin's panel (a dock widget). Its layout is in lidar_downloader_dialog_base.ui (edit it with Qt
Designer); the few widgets Designer can't make (the layer chooser, the dataset menu button, the footer) are
added here."""

import html
import os

from qgis.PyQt import uic
from qgis.PyQt import QtWidgets
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.core import Qgis, QgsMapLayerProxyModel
from qgis.gui import QgsMapLayerComboBox

HELP_URL = 'https://github.com/simonstoate/LidarDownloaderUK#readme'

FORM_CLASS, _ = uic.loadUiType(os.path.join(
    os.path.dirname(__file__), 'lidar_downloader_dialog_base.ui'))


def _layer_filters():
    """Vector + raster layer filter, across QGIS versions.

    QGIS 3.34 moved the filter enum to Qgis.LayerFilter; the old
    QgsMapLayerProxyModel.Filter is gone in QGIS 4.
    """
    if hasattr(Qgis, 'LayerFilter'):
        return Qgis.LayerFilter.VectorLayer | Qgis.LayerFilter.RasterLayer
    return QgsMapLayerProxyModel.Filter.VectorLayer | QgsMapLayerProxyModel.Filter.RasterLayer


class MenuButton(QtWidgets.QToolButton):
    """A drop-down button whose menu can nest (e.g. years under a product), showing its
    text elided to the available width."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(80)
        self.setMenu(QtWidgets.QMenu(self))
        self._full_text = ''

    def fullText(self):
        return self._full_text

    def setFullText(self, text):
        self._full_text = text
        self.setToolTip(text)
        self._elide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()

    def _elide(self):
        width = max(20, self.width() - 30)  # room for the arrow
        self.setText(self.fontMetrics().elidedText(self._full_text, Qt.TextElideMode.ElideRight, width))


class LidarDownloaderDockWidget(QtWidgets.QDockWidget, FORM_CLASS):
    """The panel. The plugin (lidar_downloader.py) connects its widgets and fills in what they show."""

    # Emitted when the user closes the dock (not when it's tabbed behind another dock)
    closed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setupUi(self)
        self.legendRows = []  # the legend's lines as text (what setLegendRows last showed)

        self.cboLayer = QgsMapLayerComboBox(self)
        self.cboLayer.setFilters(_layer_filters())
        self.cboLayer.setToolTip("Layer to select tiles under, and to crop to")
        self.layLayerSelect.insertWidget(0, self.cboLayer, 1)

        # Scroll rather than clip the dock's contents on short screens
        contents = self.widget()
        scroll = QtWidgets.QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(contents)
        self.setWidget(scroll)

        # Dataset chooser: a drop-down menu of products (by nation across a border), resolutions and years
        self.btnDataset = MenuButton(self)
        self.btnDataset.setToolTip("LIDAR product, survey year and resolution to download")
        self.layDataset.insertWidget(0, self.btnDataset)
        # What the chosen product is, in a sentence
        self.lblDatasetInfo = QtWidgets.QLabel(self)
        self.lblDatasetInfo.setWordWrap(True)
        self.layDataset.insertWidget(1, self.lblDatasetInfo)

        # Help and the official data sources, at the foot of the dock
        self.lblHelp = QtWidgets.QLabel(f'<a href="{HELP_URL}">Help and guide</a>', self)
        self.lblHelp.setOpenExternalLinks(True)
        self.lblHelp.setToolTip(HELP_URL)
        self.btnSources = QtWidgets.QToolButton(self)
        self.btnSources.setText("Official data sources")
        self.btnSources.setToolTip("Each nation's official LIDAR portal, and where to find Northern Ireland's data")
        self.btnSources.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        self.btnSources.setAutoRaise(True)
        self.btnSources.setMenu(QtWidgets.QMenu(self.btnSources))
        foot = QtWidgets.QHBoxLayout()
        foot.addWidget(self.lblHelp)
        foot.addStretch(1)
        foot.addWidget(self.btnSources)
        self.layMain.addLayout(foot)

    def setLegendRows(self, title, rows):
        """Show the legend: a title, then one line per (colour, text) with a coloured square.

        A single rich-text label (no tables, no per-row widgets): tables were mis-sized
        when wrapped, and creating/deleting row widgets crashed QGIS 3.x (PyQt5).
        """
        lines = [f'<span style="color:{colour}; font-size:large">&#9632;</span>&nbsp;{html.escape(text)}'
                 for colour, text in rows]
        self.lblLegend.setText(f'<b>{html.escape(title)}</b><br>' + '<br>'.join(lines))
        self.legendRows = [text for _, text in rows]
        self.lblLegend.updateGeometry()
        self.grpLegend.layout().activate()

    def closeEvent(self, event):
        self.closed.emit()
        super().closeEvent(event)
