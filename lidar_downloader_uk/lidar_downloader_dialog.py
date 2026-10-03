# SPDX-FileCopyrightText: 2025-2026 Simon Stoate
# SPDX-License-Identifier: GPL-2.0-or-later
"""The plugin's panel (a dock widget). Its layout is in lidar_downloader_dialog_base.ui (edit it with Qt
Designer); the few widgets Designer can't make (the "Crop to" layer chooser, the menu buttons, the icons, the
footer) are added here."""

import html
import os

from qgis.PyQt import uic
from qgis.PyQt import QtWidgets
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.core import Qgis, QgsApplication, QgsMapLayerProxyModel
from qgis.gui import QgsMapLayerComboBox

HELP_URL = 'https://github.com/simonstoate/LidarDownloaderUK#readme'

FORM_CLASS, _ = uic.loadUiType(os.path.join(
    os.path.dirname(__file__), 'lidar_downloader_dialog_base.ui'))


def _polygon_layers():
    """The layer filter for polygon layers, across QGIS versions.

    QGIS 3.34 moved the filter enum to Qgis.LayerFilter; the old
    QgsMapLayerProxyModel.Filter is gone in QGIS 4.
    """
    if hasattr(Qgis, 'LayerFilter'):
        return Qgis.LayerFilter.PolygonLayer
    return QgsMapLayerProxyModel.Filter.PolygonLayer


def _theme_icon(*names):
    """The first of QGIS's own icons that this QGIS has (an empty icon if none)."""
    for name in names:
        icon = QgsApplication.getThemeIcon(name)
        if not icon.isNull():
            return icon
    return QgsApplication.getThemeIcon(names[-1])


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

    def setFullText(self, text, tooltip=None):
        """Show text (elided to fit), with the whole of it as the tooltip unless another is given."""
        self._full_text = text
        self.setToolTip(tooltip or text)
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

        # "Crop to" this layer: polygon layers only (a site boundary, say)
        self.cboLayer = QgsMapLayerComboBox(self)
        self.cboLayer.setFilters(_polygon_layers())
        self.cboLayer.setToolTip("The polygon layer to crop to (its selected features if it has any): ticking "
                                 "'Crop to' selects the tiles beneath it")
        self.layCrop.addWidget(self.cboLayer, 1)

        # The sections fold up (QGIS's own group boxes), each remembering whether it was folded
        for box in (self.grpTiles, self.grpDataset, self.grpDownload, self.grpUse, self.grpFolder, self.grpLegend):
            box.setSettingGroup('LidarDownloaderUK')
            box.setSaveCollapsedState(True)

        # What happens to tiles once downloaded (the plugin keeps the choice and what's possible)
        for text, key, tip in (
                ("Load each tile", 'tiles',
                 "When the download finishes, add each tile to the project as a layer of its own: download and load "
                 "in one go"),
                ("Load a temporary mosaic", 'mosaic',
                 "When the download finishes, add the tiles as one temporary mosaic: a VRT in QGIS's temporary folder, "
                 "gone when QGIS closes (\"Create mosaic...\" can save one). For two or more selected tiles that make "
                 "a square or rectangle, so the mosaic has no empty space; not for point clouds or oblique photos."),
                ("Just download", 'none',
                 "Download the tiles without adding them to the project: load them later with \"Load selected\"")):
            self.cboAfterDownload.addItem(text, key)
            self.cboAfterDownload.setItemData(self.cboAfterDownload.count() - 1, tip, Qt.ItemDataRole.ToolTipRole)
        self.cboAfterDownload.setSizeAdjustPolicy(
            QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.cboAfterDownload.setMinimumContentsLength(12)
        self.cboAfterDownload.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                                            QtWidgets.QSizePolicy.Policy.Fixed)

        # Pick tiles on the map, and deselect them, with QGIS's own selection icons
        self.btnPickTiles.setIcon(_theme_icon('/mActionSelectRectangle.svg'))
        self.btnDeselect.setIcon(_theme_icon('/mActionDeselectActiveLayer.svg', '/mActionDeselectAll.svg'))

        # "Downloaded:" (under the download folder) what's in the folder, as a menu filled in by the plugin each time
        # it opens. The grid is shaded for the one chosen; it's in step with the dataset chosen in step 2
        self.btnShaded = MenuButton(self)
        self.layShaded.addWidget(self.btnShaded, 1)

        # Scroll rather than clip the dock's contents on short screens
        contents = self.widget()
        scroll = QtWidgets.QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(contents)
        self.setWidget(scroll)

        # Dataset chooser: a drop-down menu of products (by nation across a border), resolutions and years. Each
        # dataset says what it is in a tooltip (in the menu, and on the button for the chosen one), and so does the
        # info icon beside the button, which shows it on a click too
        self.btnDataset = MenuButton(self)
        self.btnDataset.setToolTip("LIDAR product, survey year and resolution to download")
        self.btnDataset.menu().setToolTipsVisible(True)
        self.btnDatasetInfo = QtWidgets.QToolButton(self)
        self.btnDatasetInfo.setIcon(_theme_icon('/mActionPropertiesWidget.svg', '/mIconInfo.svg'))  # a blue "i"
        self.btnDatasetInfo.setAutoRaise(True)
        self.btnDatasetInfo.clicked.connect(self.show_dataset_info)
        self.layDatasetRow = QtWidgets.QHBoxLayout()
        self.layDatasetRow.addWidget(self.btnDataset, 1)
        self.layDatasetRow.addWidget(self.btnDatasetInfo)
        self.layDataset.insertLayout(0, self.layDatasetRow)

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

    def show_dataset_info(self):
        """The info icon: what the chosen dataset is, in a pop-up that stays until clicked away."""
        QtWidgets.QWhatsThis.showText(self.btnDatasetInfo.mapToGlobal(self.btnDatasetInfo.rect().bottomLeft()),
                                      self.btnDatasetInfo.toolTip(), self.btnDatasetInfo)

    def setLegendRows(self, rows):
        """Show the legend: one line per (colour, text) with a coloured square. (Which dataset it's about is in
        the "Shaded for" menu.)

        A single rich-text label (no tables, no per-row widgets): tables were mis-sized
        when wrapped, and creating/deleting row widgets crashed QGIS 3.x (PyQt5).
        """
        lines = [f'<span style="color:{colour}; font-size:large">&#9632;</span>&nbsp;{html.escape(text)}'
                 for colour, text in rows]
        self.lblLegend.setText('<br>'.join(lines))
        self.legendRows = [text for _, text in rows]
        self.lblLegend.updateGeometry()
        self.grpLegend.layout().activate()

    def closeEvent(self, event):
        self.closed.emit()
        super().closeEvent(event)
