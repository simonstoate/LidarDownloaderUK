# SPDX-FileCopyrightText: 2025-2026 Simon Stoate
# SPDX-License-Identifier: GPL-2.0-or-later
"""The "My downloads" window: what's in the download folder, how much space it takes, and actions per survey."""

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt import QtWidgets


def format_bytes(size):
    """A size in bytes for people, e.g. '1.2 GB' ('unknown' for None)."""
    if size is None:
        return 'unknown'
    for unit, scale in (('GB', 1e9), ('MB', 1e6), ('KB', 1e3)):
        if size >= scale:
            return f'{size / scale:.1f} {unit}'
    return f'{size} bytes'


class DownloadsDialog(QtWidgets.QDialog):
    """Lists every survey downloaded (one row each). The plugin fills it with set_rows() and acts on the
    requests: each signal carries the row's index in the list given to set_rows()."""

    showRequested = pyqtSignal(int)
    loadRequested = pyqtSignal(int)
    openRequested = pyqtSignal(int)
    deleteRequested = pyqtSignal(int)
    refreshRequested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('My Downloads - LIDAR Downloader UK')
        self.resize(640, 420)
        layout = QtWidgets.QVBoxLayout(self)
        self.lblSummary = QtWidgets.QLabel(self)
        self.lblSummary.setWordWrap(True)
        layout.addWidget(self.lblSummary)
        self.tree = QtWidgets.QTreeWidget(self)
        self.tree.setHeaderLabels(['Dataset', 'Tiles', 'Size', 'Where (10 km squares)'])
        self.tree.setRootIsDecorated(False)
        self.tree.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setSortingEnabled(False)
        self.tree.itemSelectionChanged.connect(self._update_buttons)
        self.tree.itemDoubleClicked.connect(lambda item, column: self._request(self.showRequested))
        layout.addWidget(self.tree, 1)
        # The zip files: deleted after extracting (a setting the plugin keeps), or tidied up now
        zips = QtWidgets.QHBoxLayout()
        self.chkDeleteZips = QtWidgets.QCheckBox('Delete zips after extracting', self)
        self.chkDeleteZips.setToolTip('Permanently deletes each zip once its tile has extracted successfully. Saves '
                                      'disk space: each zip is about the same size as its extracted tile. Tiles whose '
                                      'zip was deleted still count as downloaded.')
        self.btnTidyZips = QtWidgets.QPushButton('Delete extracted zips...', self)
        self.btnTidyZips.setToolTip('Lists the zip files in the download folder whose tiles are fully extracted, and '
                                    'asks before permanently deleting them')
        zips.addWidget(self.chkDeleteZips)
        zips.addStretch(1)
        zips.addWidget(self.btnTidyZips)
        layout.addLayout(zips)
        buttons = QtWidgets.QHBoxLayout()
        self.btnShow = QtWidgets.QPushButton('Show on grid', self)
        self.btnShow.setToolTip('Choose this dataset and select its tiles on the grid')
        self.btnLoad = QtWidgets.QPushButton('Load', self)
        self.btnLoad.setToolTip('Choose this dataset and load its tiles into the project')
        self.btnOpen = QtWidgets.QPushButton('Open folder', self)
        self.btnDelete = QtWidgets.QPushButton('Delete...', self)
        self.btnDelete.setToolTip('Permanently delete this survey\'s folder (asks first)')
        self.btnRefresh = QtWidgets.QPushButton('Refresh', self)
        close = QtWidgets.QPushButton('Close', self)
        for button, signal in ((self.btnShow, self.showRequested), (self.btnLoad, self.loadRequested),
                               (self.btnOpen, self.openRequested), (self.btnDelete, self.deleteRequested)):
            button.clicked.connect(lambda _checked=False, s=signal: self._request(s))
            buttons.addWidget(button)
        buttons.addStretch(1)
        self.btnRefresh.clicked.connect(self.refreshRequested)
        close.clicked.connect(self.close)
        buttons.addWidget(self.btnRefresh)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self._update_buttons()

    def set_rows(self, rows, folder, total, free):
        """rows: [(label, tile count, bytes, where, tile names)]. Keeps the selected row where it can."""
        selected = self.selected_row()
        self.tree.clear()
        for index, (label, tiles, size, where, names) in enumerate(rows):
            item = QtWidgets.QTreeWidgetItem([label, str(tiles), format_bytes(size), where])
            item.setToolTip(3, ', '.join(names))
            item.setData(0, Qt.ItemDataRole.UserRole, index)
            item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            item.setTextAlignment(2, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.tree.addTopLevelItem(item)
        for column in range(4):
            self.tree.resizeColumnToContents(column)
        if selected is not None and selected < len(rows):
            self.tree.setCurrentItem(self.tree.topLevelItem(selected))
        self.lblSummary.setText(
            f'{len(rows)} survey(s), {format_bytes(total)} in {folder}. Free on this drive: {format_bytes(free)}.'
            if rows else f'Nothing downloaded yet in {folder}. Free on this drive: {format_bytes(free)}.')
        self._update_buttons()

    def selected_row(self):
        """The index (in set_rows' list) of the selected row, or None."""
        items = self.tree.selectedItems()
        return items[0].data(0, Qt.ItemDataRole.UserRole) if items else None

    def select_row(self, index):
        """Select the row at index (in set_rows' list)."""
        if 0 <= index < self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(index))

    def _request(self, signal):
        row = self.selected_row()
        if row is not None:
            signal.emit(row)

    def _update_buttons(self):
        has = self.selected_row() is not None
        for button in (self.btnShow, self.btnLoad, self.btnOpen, self.btnDelete):
            button.setEnabled(has)
