# SPDX-FileCopyrightText: 2025-2026 Simon Stoate
# SPDX-License-Identifier: GPL-2.0-or-later
"""The plugin: its panel, map tools and messages, and the background tasks it starts.

The panel is laid out in steps (lidar_downloader_dialog_base.ui): 1. choose the area (tiles on the OSGB 5 km
grid), 2. choose the data, 3. download, 4. use the downloaded tiles. The sections of LidarDownloader below
follow the same order, after the setup code.
"""

# All Qt imports go through qgis.PyQt and use fully scoped enums
# (e.g. Qt.DockWidgetArea.RightDockWidgetArea) so the plugin runs on both
# Qt5 (QGIS 3.x) and Qt6 (QGIS 4.x) builds.
import html
import json
import math
import os

from qgis.PyQt.QtCore import QSettings, QTimer, Qt, QStandardPaths, QUrl
from qgis.PyQt.QtGui import QDesktopServices, QIcon
from qgis.PyQt.QtWidgets import QAction, QActionGroup, QMenu, QMessageBox, QFileDialog, QPushButton
from qgis.core import (
    Qgis, QgsApplication, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsCsException, QgsField,
    QgsGeometry, QgsMarkerSymbol, QgsMessageLog, QgsPointCloudLayer, QgsPointXY, QgsProcessingUtils, QgsProject,
    QgsProperty, QgsRasterLayer, QgsRasterTransparency, QgsRectangle, QgsSingleSymbolRenderer, QgsTask,
    QgsVectorLayer)

from . import api, migrate, places, pointclouds, postprocess, sources, storage, styles
from .grid import DATASET_COLOURS, DOWNLOADED_COLOUR, GridLayer, intersecting_features, search_areas
from .downloads_dialog import DownloadsDialog, format_bytes
from .lidar_downloader_dialog import LidarDownloaderDockWidget
from .maptools import TileSelectTool
from .processing_provider import LidarDownloaderProvider
from .tasks import DownloadTilesTask, GeocodeTask, ParallelDownloadTask, SearchTask

MB = QMessageBox.StandardButton
try:  # QgsField takes a QMetaType.Type on QGIS 3.38+ (and QGIS 4); a QVariant.Type before that
    from qgis.PyQt.QtCore import QMetaType
    QVariantInt, QVariantDouble = QMetaType.Type.Int, QMetaType.Type.Double
    QgsField('test', QVariantInt)
except (ImportError, AttributeError, TypeError):
    from qgis.PyQt.QtCore import QVariant
    QVariantInt, QVariantDouble = QVariant.Int, QVariant.Double
TITLE = 'LIDAR Downloader UK'
SETTINGS_KEY_DIR = 'LidarDownloader/download_dir'
SETTINGS_KEY_DELETE_ZIPS = 'LidarDownloader/delete_zips'
SETTINGS_KEY_DATASET = 'LidarDownloader/dataset'
# What happens to tiles once downloaded: 'tiles' (each loaded), 'mosaic' (loaded as one temporary mosaic) or 'none'.
# Up to 0.14.5 it was a tick box: load them or not (SETTINGS_KEY_LOAD_AFTER)
SETTINGS_KEY_AFTER_DOWNLOAD = 'LidarDownloader/after_download'
AFTER_DOWNLOAD_CHOICES = ('tiles', 'mosaic', 'none')
SETTINGS_KEY_LOAD_AFTER = 'LidarDownloader/load_after_download'
SETTINGS_KEY_COVERAGE = 'LidarDownloader/show_coverage'
# Tiles of the last download that didn't finish (cancelled, failed, QGIS closed): offered to resume
SETTINGS_KEY_UNFINISHED = 'LidarDownloader/unfinished'
# On the elevation layers "Load" adds: their colours as the plugin set them ('' until it has: see
# fit_group_colours), and their lowest and highest heights ("low,high", measured once)
COLOURS_PROPERTY = 'lidar_downloader/colours'
HEIGHTS_PROPERTY = 'lidar_downloader/heights'
# Wait this long after the selection changes before asking what's available
SEARCH_DELAY_MS = 600
# Don't check availability for huge selections (one request per 100 km square)
MAX_SEARCH_TILES = 2000
# Ask before downloading more than this many tiles (~70 MB each)
CONFIRM_ABOVE_TILES = 10
# Download this many tiles at once (kind to the Environment Agency's service)
PARALLEL_DOWNLOADS = 3
# Coverage preview: tiles in view that have the chosen dataset, checked once the map stops moving. With more tiles
# than this in view (about 250 x 160 km on a wide screen) it asks to zoom in: each 100 km square is a search.
COVERAGE_DELAY_MS = 800
MAX_COVERAGE_TILES = 1600
COVERAGE_COLOUR = '#6baed6'
# Step 2's menu button with no tiles selected: what can be downloaded depends on the tiles
SELECT_TILES_PROMPT = 'Select a tile to see the data available'
# Survey dates: the group they're loaded into, and on each layer, the tile it's for
SURVEY_DATES_GROUP = 'Survey dates'
SURVEY_DATES_PROPERTY = 'lidar_downloader/survey_dates_of'
SURVEY_COLOURS_PROPERTY = 'lidar_downloader/survey_colours'  # as COLOURS_PROPERTY, for survey dates
# "Go to" adds the tiles covering a place to the selection, unless that's more than this many
MAX_GO_TO_TILES = 16
BNG = 'EPSG:27700'


def format_size(megabytes):
    return f"{megabytes / 1000:.1f} GB" if megabytes >= 1000 else f"{megabytes:.0f} MB"


def plural(count, word):
    """E.g. '1 layer', '3 layers'."""
    return f"{count:,} {word}{'' if count == 1 else 's'}"


class LoadOutcome:
    """What one "Load" did, for its message."""

    def __init__(self):
        self.added = 0        # layers added to the project
        self.already = 0      # tiles (or cropped areas) that were in the project already
        self.failed = []      # layers QGIS couldn't open
        self.problems = []    # cropped mosaics that couldn't be made
        self.unjoined = []    # tiles whose files couldn't be joined into the 5 km tile
        self.loaded = set()   # tiles in the project now (added, or there already)
        self.outside = []     # selected tiles outside the "Crop to" area
        self.cropped = False  # a layer cropped to the area was added (or was there already)
        self.missed = False   # the "Crop to" area missed the tiles: they loaded as downloaded
        self.clouds = False   # point clouds were loaded (they always load whole)


def choose_place(parent, labels):
    """A menu of places under the widget. Returns the chosen index, or None."""
    menu = QMenu(parent)
    actions = [menu.addAction(label) for label in labels]
    chosen = menu.exec(parent.mapToGlobal(parent.rect().bottomLeft()))
    return actions.index(chosen) if chosen in actions else None


def ask_choice(parent, title, text, informative, choices, default=0, warning=False, details=None):
    """Modal question with custom buttons. Returns the chosen index, or None for Cancel.

    With warning=True the box shows a warning icon, the choices are marked
    destructive and Cancel is the default button (Enter does nothing harmful).
    """
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setIcon(QMessageBox.Icon.Warning if warning else QMessageBox.Icon.Question)
    box.setText(text)
    box.setInformativeText(informative)
    if details:
        box.setDetailedText(details)
    role = QMessageBox.ButtonRole.DestructiveRole if warning else QMessageBox.ButtonRole.AcceptRole
    buttons = [box.addButton(label, role) for label in choices]
    cancel = box.addButton(MB.Cancel)
    box.setEscapeButton(cancel)
    box.setDefaultButton(cancel if warning else buttons[default])
    box.exec()
    clicked = box.clickedButton()
    return buttons.index(clicked) if clicked in buttons else None


class LidarDownloader:
    """The plugin, as QGIS sees it (initGui, unload) and as the panel drives it.

    It keeps what the services said is available for each tile (offerings_by_tile), the chosen dataset, and the
    background tasks it has running (a download, a search, a mosaic being made); the tasks call back here, on the
    UI thread, when they finish.
    """

    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.actions = []
        self.menu = '&LIDAR Downloader UK'
        self.dock = None
        self.grid = None
        self.task = None
        self.dataset = self.load_dataset_setting()
        # Availability from the search API, cached per tile
        self.offerings_by_tile = {}
        self.searched_tiles = set()
        self.search_task = None
        self.search_timer = None
        self.provider = None
        self.post_task = None
        # Download folders already checked for an old folder layout, and for millimetre grids, this session
        self.migration_checked = set()
        self.units_checked = set()
        self.pick_tool = None
        # Coverage preview: tiles in view (None when zoomed out too far), its own search, and why that last failed
        self.coverage_view = set()
        self.coverage_task = None
        self.coverage_error = ''
        self.coverage_timer = None
        self.geocode_task = None
        # Licence / attribution statements from the search services, per survey
        self.survey_rights = {}
        self.ni_notice_shown = False
        self.downloads_dialog = None
        self.download_rows = []
        self.bng_offered = False
        self.bng_item = None
        # The dataset of the download running, and a resume waiting for its tiles' availability check
        self.download_dataset = None
        self.resume_pending = False
        # The tiles selected when the download (or a mosaic made in the background) started: deselected when it's
        # loaded, unless the selection has changed meanwhile
        self.download_selection = set()
        # Step 3's choice of what happens once tiles are downloaded (one of AFTER_DOWNLOAD_CHOICES)
        self.after_download = 'tiles'
        # With "Crop to" ticked (and a layer with polygons): the tiles the crop needs, which are the only ones that
        # can be downloaded; None otherwise. The crop layer watched for changes, and the timer that gathers them.
        self.crop_tiles = None
        self.watched_crop_layer = None
        self.crop_timer = None
        self.pick_hint = ''
        self.tiles_tooltip = ''
        self.coverage_tooltip = ''
        # Said under the menu after switching to the nation's dataset, until the selection changes
        self.switch_note = ''
        # The selection has changed and switching to its nation's dataset hasn't been tried for it yet (that waits
        # until the services have said what its tiles have)
        self.switch_pending = False
        # Set while the plugin selects a chosen dataset's own tiles ("Show on grid", Resume): the dataset stays
        self.keep_dataset = False
        # The dataset menu's actions (made by refresh_dataset_menu)
        self.dataset_actions = None

    def add_action(self, icon_path, text, callback, parent=None):
        """A toolbar button and Web menu entry for the plugin."""
        action = QAction(QIcon(icon_path), text, parent)
        action.triggered.connect(callback)
        self.iface.addToolBarIcon(action)
        self.iface.addPluginToWebMenu(self.menu, action)
        self.actions.append(action)
        return action

    # ------------------------------------------------------------------ setup

    def initProcessing(self):
        """Add the Processing Toolbox algorithms (QGIS calls this because metadata has hasProcessingProvider=yes)."""
        if self.provider is not None:
            return
        registry = QgsApplication.processingRegistry()
        if registry.providerById('lidardownloader') is not None:
            # Another copy of the plugin (e.g. "LIDAR Downloader", before it was renamed) has the tools
            QgsMessageLog.logMessage(
                "Another copy of this plugin is installed (e.g. the older \"LIDAR Downloader\"), so its Processing "
                "tools are used. Uninstall it in Plugins > Manage and Install Plugins.", TITLE,
                Qgis.MessageLevel.Warning)
            return
        self.provider = LidarDownloaderProvider(self.plugin_dir)
        registry.addProvider(self.provider)

    def initGui(self):
        """Build the panel (hidden until the toolbar button is clicked), its map tools, and the toolbar button and
        Web menu entry."""
        # Settings of features the plugin no longer has (styles, the drawn-area buffer, contours, crop memory)
        for key in ('styles', 'area_buffer', 'contour_interval', 'contour_smoothing', 'crop_to_layer'):
            QSettings().remove(f'LidarDownloader/{key}')
        self.initProcessing()
        self.dock = LidarDownloaderDockWidget()
        self.dock.setObjectName("LidarDownloaderDockWidget")
        self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock)
        self.dock.hide()
        self.dock.btnCancel.setEnabled(False)
        self.dock.progressBar.setMaximum(100)
        self.dock.closed.connect(self.on_dock_closed)
        canvas = self.iface.mapCanvas()

        # 1. Choose your area: the grid, Go to, picking tiles on the map, and "Crop to" a layer
        self.grid = GridLayer(self.iface, self.plugin_dir)
        self.grid.selectionCountChanged.connect(self.on_selection_count_changed)
        self.grid.removedExternally.connect(self.on_grid_removed_externally)
        QgsProject.instance().readProject.connect(self.sync_grid_checkbox)
        self.dock.chkShowGrid.toggled.connect(self.toggle_grid_layer)
        self.dock.leGoTo.returnPressed.connect(self.go_to)
        self.dock.btnGoTo.clicked.connect(self.go_to)
        self.pick_tool = TileSelectTool(canvas)
        self.pick_tool.picked.connect(self.on_tiles_picked)
        self.dock.btnPickTiles.clicked.connect(self.toggle_pick_tool)
        self.dock.btnDeselect.clicked.connect(self.deselect_tiles)
        canvas.mapToolSet.connect(self.on_map_tool_set)
        # 'Crop to' isn't remembered between sessions: next time a different layer may come first in the list
        self.dock.chkCrop.toggled.connect(self.on_crop_toggled)
        self.dock.cboLayer.layerChanged.connect(self.on_crop_layer_changed)
        self.crop_timer = QTimer()
        self.crop_timer.setSingleShot(True)
        self.crop_timer.setInterval(0)  # (gathers the signals of one edit or selection into one update)
        self.crop_timer.timeout.connect(self.on_crop_changed)
        self.pick_hint = self.dock.lblPickHint.text()
        self.coverage_tooltip = self.dock.chkCoverage.toolTip()
        self.tiles_tooltip = self.dock.cboAfterDownload.itemData(0, Qt.ItemDataRole.ToolTipRole)
        self.coverage_timer = QTimer()
        self.coverage_timer.setSingleShot(True)
        self.coverage_timer.setInterval(COVERAGE_DELAY_MS)
        self.coverage_timer.timeout.connect(self.update_coverage)
        self.dock.chkCoverage.setChecked(QSettings().value(SETTINGS_KEY_COVERAGE, True, type=bool))
        self.dock.chkCoverage.toggled.connect(self.toggle_coverage)
        self.dock.btnShaded.menu().aboutToShow.connect(self.refresh_shaded_menu)
        canvas.extentsChanged.connect(self.on_extents_changed)

        # 2. Choose the data: what the selected tiles have is asked for once the selection settles
        self.search_timer = QTimer()
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(SEARCH_DELAY_MS)
        self.search_timer.timeout.connect(self.start_search)

        # 3. Download
        self.dock.btnDownloadTiles.clicked.connect(self.download_selected_tiles)
        self.dock.btnResume.clicked.connect(self.resume_download)
        self.dock.btnCancel.clicked.connect(self.cancel_download)
        choice = QSettings().value(SETTINGS_KEY_AFTER_DOWNLOAD, '')
        if choice not in AFTER_DOWNLOAD_CHOICES:  # a new user, or a choice saved before 0.14.6, as a tick box
            choice = 'tiles' if QSettings().value(SETTINGS_KEY_LOAD_AFTER, True, type=bool) else 'none'
        self.after_download = choice
        self.dock.cboAfterDownload.setCurrentIndex(self.dock.cboAfterDownload.findData(choice))
        self.dock.cboAfterDownload.currentIndexChanged.connect(self.on_after_download_chosen)

        # 4. Use downloaded tiles
        self.dock.btnLoadSelected.clicked.connect(self.load_selected_tiles)
        self.dock.btnCreateVRT.clicked.connect(self.create_mosaic_from_selected)
        self.dock.btnSurveyDates.clicked.connect(self.toggle_survey_dates)
        self.dock.btnMyDownloads.clicked.connect(self.show_downloads)

        # Download folder, and the links at the foot of the panel
        self.dock.leDownloadDir.setText(self.saved_download_dir())
        self.dock.leDownloadDir.editingFinished.connect(self.on_download_dir_changed)
        self.dock.btnBrowse.clicked.connect(self.select_download_dir)
        self.dock.btnOpenFolder.clicked.connect(self.open_download_dir)
        self.build_sources_menu()

        self.refresh_dataset_menu()
        self.update_shaded_button()
        self.update_download_button()  # nothing selected yet
        self.update_after_download_choices()
        self.update_use_buttons()
        self.add_action(os.path.join(self.plugin_dir, 'icon.svg'), 'LIDAR Downloader UK', self.run,
                        parent=self.iface.mainWindow())

    def unload(self):
        """Undo everything initGui and the session added: tasks, map tools, signals, the grid, the panel."""
        self.watch_crop_layer(None)
        if self.crop_timer is not None:
            self.crop_timer.stop()
        if self.provider is not None:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None
        if self.search_timer is not None:
            self.search_timer.stop()
            self.search_timer = None
        if self.coverage_timer is not None:
            self.coverage_timer.stop()
            self.coverage_timer = None
        canvas = self.iface.mapCanvas()
        for signal, slot in ((canvas.mapToolSet, self.on_map_tool_set),
                             (canvas.extentsChanged, self.on_extents_changed),
                             (QgsProject.instance().readProject, self.sync_grid_checkbox)):
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        if self.pick_tool is not None:
            if canvas.mapTool() is self.pick_tool:
                canvas.unsetMapTool(self.pick_tool)
            self.pick_tool.release()
        self.pick_tool = None
        for task in [self.task, self.search_task, self.post_task, self.coverage_task, self.geocode_task]:
            if task is not None:
                task.on_finished = None
                task.cancel()
        self.task = None
        self.search_task = None
        self.post_task = None
        self.coverage_task = None
        self.geocode_task = None

        if self.grid is not None:
            self.grid.cleanup()
            self.grid = None

        for action in self.actions:
            self.iface.removePluginWebMenu(self.menu, action)
            self.iface.removeToolBarIcon(action)
        self.actions = []

        if self.downloads_dialog is not None:
            self.downloads_dialog.close()
            self.downloads_dialog.deleteLater()
            self.downloads_dialog = None
        if self.dock is not None:
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None

    def run(self):
        """The toolbar button: show the panel, with the grid on the map."""
        download_dir = self.resolve_download_dir()
        if download_dir:
            self.dock.leDownloadDir.setText(download_dir)
        self.dock.show()
        self.dock.raise_()
        if download_dir:
            self.check_migration(download_dir)
        self.sync_grid_checkbox()  # e.g. a project opened before the plugin started, saved with the grid
        if self.grid.layer() is None:
            self.dock.chkShowGrid.setChecked(True)  # the tiles are what everything is chosen by
        self.refresh_grid_style()
        self.update_selection_label()
        self.update_resume_button()

    def resolve_download_dir(self):
        """Return the saved download folder, setting up Documents/LiDAR Data if needed."""
        settings = QSettings()
        docs_path = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
        default_lidar_path = os.path.join(docs_path, "LiDAR Data")

        saved_dir = self.saved_download_dir()
        if saved_dir and os.path.exists(saved_dir):
            return saved_dir

        # First run (or the saved folder has gone): Documents/LiDAR Data, made without asking; it can be changed in
        # the panel at any time
        download_dir = None
        try:
            os.makedirs(default_lidar_path, exist_ok=True)
            download_dir = default_lidar_path
            if saved_dir:
                self.notify(f"The download folder {saved_dir} isn't there (a drive not connected?), so downloads go "
                            f"to {default_lidar_path} for now. Change it under 'Download folder'.",
                            Qgis.MessageLevel.Warning)
        except OSError as e:
            QMessageBox.warning(self.iface.mainWindow(), "Download Folder",
                                f"Could not create {default_lidar_path}:\n{e}\n\nPlease choose a folder for downloads.")
            download_dir = QFileDialog.getExistingDirectory(
                self.iface.mainWindow(), "Select Download Directory", docs_path,
                QFileDialog.Option.ShowDirsOnly | QFileDialog.Option.DontResolveSymlinks) or None

        if download_dir:
            settings.setValue(SETTINGS_KEY_DIR, download_dir)
        return download_dir

    def check_migration(self, download_dir):
        """Offer (once per session per folder) to move downloads from an earlier folder layout."""
        if not download_dir or download_dir in self.migration_checked or not os.path.isdir(download_dir):
            return
        self.migration_checked.add(download_dir)
        moves, skipped = migrate.plan(download_dir)
        for path, reason in skipped:
            QgsMessageLog.logMessage(f"Not reorganised: {path} ({reason})", TITLE, Qgis.MessageLevel.Info)
        if not moves:
            return

        lines = [f"  {n} tile(s) of {api.dataset_label(d)}  \u2192  {api.dataset_folder(d).replace(os.sep, '/')}/"
                 for d, n in sorted(migrate.summary(moves).items(), key=lambda item: api.dataset_key(item[0]))]
        skipped_note = (f"\n\n{len(skipped)} item(s) can't be moved automatically and are left where they are "
                        "(see the Log Messages panel)." if skipped else "")
        choice = ask_choice(
            self.dock, "Reorganise Downloads?",
            f"Downloads in {download_dir} use the folder layout of an earlier version of the plugin. "
            "Move them into product / resolution / year folders?",
            "\n".join(lines) + "\n\nFiles are moved within the folder, not copied or deleted. VRT files in the "
            "folder, and layers in this project, that use the moved files are updated to the new locations. "
            "Other saved QGIS projects using these files will need their layer paths updating (QGIS offers to "
            "do this when it opens a project with missing files).\n\nUntil they're moved, the plugin won't see "
            "these downloads." + skipped_note,
            ["Reorganise"],
            details="\n".join(f"{m.src}  ->  {m.dst}" for m in moves))
        if choice is None:
            self.notify("Downloads not reorganised; you'll be asked again next time.", Qgis.MessageLevel.Info)
            self.migration_checked.discard(download_dir)
            return

        # Layers using files that will move keep them open (Windows won't rename open files):
        # detach them for the move, then point them at the new (or, if a move failed, old) location.
        held = self.detach_layers(moves)
        try:
            done, errors = migrate.apply(download_dir, moves)
        finally:
            repointed = self.reattach_layers(held, done)
        changed_vrts = migrate.rewrite_vrts(download_dir, done)
        repointed += self.reload_layers(changed_vrts)
        self.refresh_grid_style()
        self.update_selection_label()
        text = (f"Reorganised {len({(m.dataset, m.tile) for m in done})} tile(s); updated {len(changed_vrts)} VRT(s) "
                f"and {repointed} layer(s) in this project.")
        if errors:
            self.notify(f"{text} {len(errors)} item(s) couldn't be moved.", Qgis.MessageLevel.Warning,
                        "\n".join(f"{src}: {err}" for src, err in errors))
        else:
            self.notify(text, Qgis.MessageLevel.Success)

    @staticmethod
    def detach_layers(moves):
        """Release project layers using files that are about to move. Returns what's needed to reattach them."""
        held = []
        for layer in QgsProject.instance().mapLayers().values():
            path, sep, rest = layer.source().partition('|')
            if migrate.new_path(path, moves) is not None:
                held.append((layer, path, sep, rest))
                layer.setDataSource('', layer.name(), layer.providerType())
        return held

    @staticmethod
    def reattach_layers(held, done):
        """Point detached layers at their files' new locations (or old ones, if a move failed)."""
        count = 0
        for layer, path, sep, rest in held:
            new = migrate.new_path(path, done)
            target = new if new is not None else path
            if not os.path.exists(target):
                QgsProject.instance().removeMapLayer(layer.id())  # a generated file (photo points), rebuilt on load
                continue
            layer.setDataSource(target + sep + rest, layer.name(), layer.providerType())
            count += new is not None
        return count

    @staticmethod
    def reload_layers(changed_files):
        """Reload project layers whose files (e.g. rewritten VRTs) changed. Returns the count."""
        changed = {os.path.normcase(os.path.normpath(p)) for p in changed_files}
        count = 0
        for layer in QgsProject.instance().mapLayers().values():
            if os.path.normcase(os.path.normpath(layer.source().partition('|')[0])) in changed:
                layer.reload()
                layer.triggerRepaint()
                count += 1
        return count

    # ---------------------------------------------------------- notifications

    def notify(self, text, level=Qgis.MessageLevel.Info, details=None, duration=None):
        """Show a message in the QGIS message bar and record it in the log panel. duration: seconds on screen (0:
        until closed); QGIS's usual time when None."""
        bar = self.iface.messageBar()
        args = (TITLE, text, details, level) if details else (TITLE, text, level)
        bar.pushMessage(*(args + ((duration,) if duration is not None else ())))
        QgsMessageLog.logMessage(f"{text}\n{details}" if details else text, TITLE, level)

    # --------------------------------------------------------------- UI events

    def on_dock_closed(self):
        # Only a real close removes the grid; tabbing to another dock doesn't
        canvas = self.iface.mapCanvas()
        if canvas.mapTool() is not None and canvas.mapTool() is self.pick_tool:
            canvas.unsetMapTool(self.pick_tool)
        self.dock.chkShowGrid.setChecked(False)

    def sync_grid_checkbox(self, *args):
        """A project opened with the grid saved in it: take the grid over and tick 'Show OSGB 5 km grid' (without
        adding a second grid), so unticking removes it."""
        if self.dock is None or self.grid is None or self.grid.layer() is not None or not self.grid.saved_grids():
            return
        loaded = self.grid.load(self.shading_entries())
        self.dock.chkShowGrid.blockSignals(True)
        self.dock.chkShowGrid.setChecked(loaded)
        self.dock.chkShowGrid.blockSignals(False)
        if loaded:
            self.dock.cboLayer.setExceptedLayerList([self.grid.layer()])
        self.update_legend()
        self.update_selection_label()

    def on_grid_removed_externally(self):
        # The user removed the grid from the Layers panel: untick without re-triggering
        self.dock.chkShowGrid.blockSignals(True)
        self.dock.chkShowGrid.setChecked(False)
        self.dock.chkShowGrid.blockSignals(False)
        self.put_pick_tool_away()
        self.dock.cboLayer.setExceptedLayerList([])
        self.update_selection_label()
        self.refresh_dataset_menu()

    def on_selection_count_changed(self, count):
        self.switch_note = ''
        self.switch_pending = not self.keep_dataset
        self.update_selection_label()
        self.schedule_search()
        if count > 0:
            self.dock.show()

    def select_download_dir(self):
        dir_path = QFileDialog.getExistingDirectory(
            self.dock, "Select Download Directory", self.dock.leDownloadDir.text(),
            QFileDialog.Option.ShowDirsOnly | QFileDialog.Option.DontResolveSymlinks)
        if dir_path:
            self.dock.leDownloadDir.setText(dir_path)
            self.on_download_dir_changed()

    def on_download_dir_changed(self):
        download_dir = self.dock.leDownloadDir.text().strip()
        if download_dir:
            QSettings().setValue(SETTINGS_KEY_DIR, download_dir)
            self.check_migration(download_dir)
        self.refresh_grid_style()
        self.update_selection_label()

    def put_pick_tool_away(self):
        canvas = self.iface.mapCanvas()
        if canvas.mapTool() is self.pick_tool:
            canvas.unsetMapTool(self.pick_tool)

    def toggle_grid_layer(self, checked):
        if not checked:
            self.put_pick_tool_away()  # (nothing to pick without the grid)
            self.grid.remove()
            self.dock.cboLayer.setExceptedLayerList([])
        elif self.grid.load(self.shading_entries()):
            self.grid.move_to_top()
            self.dock.cboLayer.setExceptedLayerList([self.grid.layer()])
            if self.dock.chkCoverage.isChecked():
                self.coverage_timer.start()
            self.offer_bng()
        else:
            self.notify(f"Failed to load the grid from {self.grid.path}", Qgis.MessageLevel.Critical)
            self.dock.chkShowGrid.setChecked(False)
        self.update_legend()
        self.update_selection_label()

    def ensure_grid(self):
        """Load the grid if needed. Returns False if it can't be loaded."""
        if self.grid.layer() is None:
            self.dock.chkShowGrid.setChecked(True)
        return self.grid.layer() is not None

    def deselect_tiles(self):
        """The deselect icon: no tiles selected."""
        if self.grid is not None:
            self.grid.clear_selection()

    def on_crop_toggled(self, checked):
        """Ticking \"Crop to\" selects the tiles beneath its layer: the ones the crop will be cut from, and while it's
        ticked the only ones that can be downloaded. They follow the crop (the layer chosen, its selected features,
        its shapes) rather than being picked by hand."""
        if checked:
            self.watch_crop_layer(self.dock.cboLayer.currentLayer())
            self.select_tiles_under_crop_layer()
        else:
            self.watch_crop_layer(None)
            self.crop_tiles = None
            self.update_crop_controls()
            self.update_selection_label()

    def on_crop_layer_changed(self, *args):
        if self.dock is not None and self.dock.chkCrop.isChecked():
            self.watch_crop_layer(self.dock.cboLayer.currentLayer())
            self.select_tiles_under_crop_layer()

    def watch_crop_layer(self, layer):
        """Follow the crop layer's selection and edits (so the tiles follow the crop), and stop following the last."""
        old, self.watched_crop_layer = self.watched_crop_layer, None
        for signal in ('selectionChanged', 'layerModified', 'dataChanged'):
            try:
                getattr(old, signal).disconnect(self.on_crop_layer_edited)
            except (AttributeError, TypeError, RuntimeError):  # none watched, or QGIS has deleted the layer
                pass
        if isinstance(layer, QgsVectorLayer):
            for signal in ('selectionChanged', 'layerModified', 'dataChanged'):
                getattr(layer, signal).connect(self.on_crop_layer_edited)
            self.watched_crop_layer = layer

    def on_crop_layer_edited(self, *args):
        self.crop_timer.start()

    def on_crop_changed(self):
        if self.dock is not None and self.dock.chkCrop.isChecked():
            self.select_tiles_under_crop_layer(quiet=True)

    def update_crop_controls(self):
        """While the tiles follow the crop they aren't picked or deselected by hand: the icons are greyed out, and
        the hint beside them says why."""
        follows = self.crop_tiles is not None
        self.dock.btnPickTiles.setEnabled(not follows)
        self.dock.btnDeselect.setEnabled(not follows)
        self.dock.lblPickHint.setText("Tiles follow the crop" if follows else self.pick_hint)
        self.dock.lblPickHint.setToolTip("While 'Crop to' is ticked, the tiles are the ones the crop needs: untick "
                                         "it to pick tiles by hand" if follows else '')
        canvas = self.iface.mapCanvas()
        if follows and canvas.mapTool() is self.pick_tool:
            canvas.unsetMapTool(self.pick_tool)

    def select_tiles_under_crop_layer(self, quiet=False):
        """Select the tiles with data beneath the \"Crop to\" layer's polygons (its selected features if it has any,
        otherwise all of them), in place of what was selected: the tiles the crop needs (crop_tiles), one for a crop
        inside a tile. A layer without polygons leaves the selection as it is, and the tiles free to pick."""
        self.crop_tiles = None
        layer = self.dock.cboLayer.currentLayer()
        features = ((layer.selectedFeatures() if layer.selectedFeatureCount() else layer.getFeatures())
                    if isinstance(layer, QgsVectorLayer) else [])
        geometries = [f.geometry() for f in features if f.hasGeometry()]
        if not isinstance(layer, QgsVectorLayer):
            if not quiet:
                self.notify("To crop, choose a polygon layer beside 'Crop to' (a site boundary, say).",
                            Qgis.MessageLevel.Warning)
        elif not geometries:
            if not quiet:
                self.notify(f"'{layer.name()}' has no polygons to crop to.", Qgis.MessageLevel.Warning)
        elif self.ensure_grid():
            if self.grid.select_intersecting(geometries, layer.crs(), touching=False) == 0 and not quiet:
                self.notify(f"No tiles with data beneath '{layer.name()}'.", Qgis.MessageLevel.Warning)
            self.crop_tiles = set(self.grid.selected_tile_names())
        self.update_crop_controls()
        self.update_selection_label()

    # ------------------------------------------------------------------ go to

    def go_to(self):
        """Go to a grid reference (worked out here) or a postcode / place (looked up in the background)."""
        text = self.dock.leGoTo.text().strip()
        if not text:
            return
        place = places.parse_grid_reference(text) or places.parse_irish_grid_reference(text)
        if place is not None:
            self.go_to_place(place)
            return
        if self.geocode_task is not None:
            return
        self.geocode_task = GeocodeTask(text, on_finished=self.on_geocode_finished)
        self.dock.btnGoTo.setEnabled(False)
        QgsApplication.taskManager().addTask(self.geocode_task)

    def on_geocode_finished(self, task, ok):
        self.geocode_task = None
        if self.dock is None:
            return
        self.dock.btnGoTo.setEnabled(True)
        if task.error:
            self.notify(f"Couldn't look up '{task.text}': {task.error}", Qgis.MessageLevel.Warning)
            return
        if not ok:
            return
        if not task.places:
            self.notify(f"Nothing found for '{task.text}'. Try a postcode, a town or village, or a grid "
                        "reference such as SU 14 27.", Qgis.MessageLevel.Warning)
            return
        index = 0 if len(task.places) == 1 else choose_place(self.dock.leGoTo, [p.label for p in task.places])
        if index is not None:
            self.go_to_place(task.places[index])

    def go_to_place(self, place):
        """Zoom to a place and add the tiles covering it to the selection (if there aren't too many)."""
        bng = QgsCoordinateReferenceSystem(BNG)
        context = QgsProject.instance().transformContext()
        area = QgsRectangle(*place.extent) if place.extent else None
        if place.easting is None:
            point = QgsCoordinateTransform(QgsCoordinateReferenceSystem('EPSG:4326'), bng, context).transform(
                QgsPointXY(place.lon, place.lat))
        elif place.crs:  # e.g. an Irish Grid reference
            to_bng = QgsCoordinateTransform(QgsCoordinateReferenceSystem(place.crs), bng, context)
            point = to_bng.transform(QgsPointXY(place.easting, place.northing))
            area = to_bng.transformBoundingBox(area) if area else None
        else:
            point = QgsPointXY(place.easting, place.northing)
        view = QgsRectangle(area) if area else QgsRectangle(point.x() - 750, point.y() - 750,
                                                            point.x() + 750, point.y() + 750)
        view.scale(1.2)
        canvas = self.iface.mapCanvas()
        canvas.setExtent(QgsCoordinateTransform(bng, canvas.mapSettings().destinationCrs(), context)
                         .transformBoundingBox(view))
        canvas.refresh()
        wgs84 = QgsCoordinateTransform(bng, QgsCoordinateReferenceSystem('EPSG:4326'), context).transform(point)
        if places.is_in_ireland(wgs84.y(), wgs84.x()):
            self.show_ni_notice(place.label)
            return
        if self.crop_tiles is not None:
            self.notify(f"Went to {place.label}. 'Crop to' is ticked, so the tiles follow the crop: untick it to "
                        "select tiles here.", Qgis.MessageLevel.Info)
            return
        if not self.ensure_grid():
            return
        # Just inside a grid square, so the neighbouring squares' tiles aren't included
        target = (QgsGeometry.fromRect(area.buffered(-1)) if area and area.width() > 2
                  else QgsGeometry.fromPointXY(point))
        count = len(intersecting_features(self.grid.layer(), [target], bng))
        if count == 0:
            self.notify(f"Went to {place.label}: no tiles with data there (the plugin covers England, Wales, "
                        "Scotland and their coasts).", Qgis.MessageLevel.Warning)
        elif count > MAX_GO_TO_TILES:
            self.notify(f"Went to {place.label}. It covers {count} tiles, so none were selected: pick the ones "
                        "you want on the map.", Qgis.MessageLevel.Info)
        else:
            self.grid.select_intersecting([target], bng, mode='add')
            hint = " To keep to your site, tick Crop to and choose its boundary layer." if count > 1 else ""
            self.notify(f"Went to {place.label}: added {count} tile(s) to the selection.{hint}",
                        Qgis.MessageLevel.Success)

    def show_ni_notice(self, place=''):
        """Northern Ireland isn't downloadable through the plugin yet: point to the official sources."""
        self.ni_notice_shown = True
        links = " \u00b7 ".join(f'<a href="{url}">{label}</a>' for label, url in sources.NI_LINKS)
        where = f"{place}: " if place else ""
        self.notify(where + "Northern Ireland's LIDAR can't be downloaded through the plugin yet (it's published "
                    "as area downloads in Irish Grid), and the Republic of Ireland isn't covered. Official sources "
                    "for Northern Ireland: " + links, Qgis.MessageLevel.Info, duration=0)  # stays, for its links

    def build_sources_menu(self):
        """The "Official data sources" menu at the foot of the panel."""
        menu = self.dock.btnSources.menu()
        menu.clear()
        for label, url in sources.OFFICIAL_SOURCES:
            menu.addAction(label).triggered.connect(lambda _checked=False, u=url: self.open_url(u))
        ni = menu.addMenu("Northern Ireland (not downloadable yet)")
        for label, url in sources.NI_LINKS:
            ni.addAction(label).triggered.connect(lambda _checked=False, u=url: self.open_url(u))

    @staticmethod
    def open_url(url):
        QDesktopServices.openUrl(QUrl(url))

    def offer_bng(self):
        """Once a session: if the project isn't in British National Grid (the data's CRS), offer to switch."""
        if self.bng_offered or QgsProject.instance().crs().authid() == BNG:
            return
        self.bng_offered = True
        bar = self.iface.messageBar()
        self.bng_item = bar.createMessage(
            TITLE, "The map isn't in British National Grid (EPSG:27700), the coordinate system of the data.")
        button = QPushButton("Switch the map to British National Grid")
        button.clicked.connect(self.set_project_bng)
        self.bng_item.layout().addWidget(button)
        bar.pushWidget(self.bng_item, Qgis.MessageLevel.Info)

    def set_project_bng(self):
        QgsProject.instance().setCrs(QgsCoordinateReferenceSystem(BNG))
        item, self.bng_item = self.bng_item, None
        if item is not None:
            # Not while its button's clicked signal is still running
            QTimer.singleShot(0, lambda: self.iface.messageBar().popWidget(item))

    # --------------------------------------------------------------- map tools

    def toggle_pick_tool(self, checked):
        self._use_map_tool(self.pick_tool, checked)

    def _use_map_tool(self, tool, checked):
        canvas = self.iface.mapCanvas()
        if checked:
            if not self.ensure_grid():
                self.on_map_tool_set(canvas.mapTool(), None)
                return
            canvas.setMapTool(tool)
        elif canvas.mapTool() is tool:
            canvas.unsetMapTool(tool)

    def on_map_tool_set(self, new_tool, old_tool=None):
        """Keep the buttons in step with the active map tool (the user may pick another QGIS tool)."""
        if self.dock is None:
            return
        self.dock.btnPickTiles.setChecked(new_tool is not None and new_tool is self.pick_tool)

    def on_tiles_picked(self, geometry, is_click, mode):
        """A click selects the tile under it, a dragged box the tiles (with data) under it: instead of what was
        selected, unless Shift or Ctrl was held (mode: see TileSelectTool.selection_mode)."""
        if not self.ensure_grid():
            return
        crs = self.iface.mapCanvas().mapSettings().destinationCrs()
        count = self.grid.select_intersecting([geometry], crs, data_only=not is_click, mode=mode)
        if count == 0 and not is_click:
            self.notify("No tiles with data there.", Qgis.MessageLevel.Warning)

    # ---------------------------------------------------------------- coverage

    def toggle_coverage(self, checked):
        QSettings().setValue(SETTINGS_KEY_COVERAGE, checked)
        if checked and self.ensure_grid():
            self.coverage_timer.start()
        else:
            self.coverage_view = set()
            self.coverage_timer.stop()
            self.refresh_grid_style()

    def on_extents_changed(self):
        if not self.ni_notice_shown and self.dock is not None and self.dock.isVisible():
            canvas = self.iface.mapCanvas()
            centre = canvas.extent().center()
            try:
                wgs84 = QgsCoordinateTransform(canvas.mapSettings().destinationCrs(),
                                               QgsCoordinateReferenceSystem('EPSG:4326'),
                                               QgsProject.instance().transformContext()).transform(centre)
            except QgsCsException:  # a view the transform can't handle: nothing to say
                wgs84 = None
            if wgs84 is not None and places.is_in_ireland(wgs84.y(), wgs84.x()) and canvas.scale() < 2_000_000:
                self.show_ni_notice()
        if self.coverage_timer is not None and self.dock.chkCoverage.isChecked() and self.grid.layer() is not None:
            self.coverage_timer.start()

    def update_coverage(self):
        """Find the tiles in view, check what the unchecked ones offer, then shade the grid."""
        if self.dock is None or self.grid.layer() is None or not self.dock.chkCoverage.isChecked():
            return
        canvas = self.iface.mapCanvas()
        tiles = self.grid.tiles_in(canvas.extent(), canvas.mapSettings().destinationCrs(), MAX_COVERAGE_TILES)
        self.coverage_view = None if tiles is None else set(tiles)
        unchecked = [t for t in (tiles or {}) if t not in self.searched_tiles]
        if self.coverage_task is None:  # (one running reports its own outcome)
            self.coverage_error = ''
            if unchecked:
                areas = search_areas({t: tiles[t] for t in unchecked}, self.grid.layer().crs())
                self.coverage_task = SearchTask(areas, on_finished=self.on_coverage_search_finished)
                self.coverage_task.requested_tiles = unchecked
                QgsApplication.taskManager().addTask(self.coverage_task)
        self.refresh_grid_style()

    def on_coverage_search_finished(self, task, ok):
        self.coverage_task = None
        if self.dock is None:
            return
        self.coverage_error = task.error or ''
        if ok:
            checked = [t for t in task.requested_tiles if t[:2] not in task.failed_squares]
            self.record_offerings(checked, [o for o in task.offerings if o.tile[:2] not in task.failed_squares])
            self.switch_if_needed()  # the selected tiles may have been among those checked
            self.refresh_dataset_menu()
            self.update_selection_label()
        if task.error:
            QgsMessageLog.logMessage(f"Coverage check failed: {task.error} {task.error_detail}".strip(), TITLE,
                                     Qgis.MessageLevel.Warning)
        self.refresh_grid_style()
        # The view may have moved on while we were searching
        if ok and not task.error and any(t not in self.searched_tiles for t in (self.coverage_view or ())):
            self.coverage_timer.start()

    def coverage_tiles(self, local):
        """Tiles in view that have the chosen dataset but aren't downloaded (None if zoomed out too far)."""
        if not self.dock.chkCoverage.isChecked():
            return set()
        if self.coverage_view is None:
            return None
        return {t for t in self.coverage_view if t in self.searched_tiles and t not in local
                and api.pick_offering(self.offerings_by_tile.get(t, []), self.dataset)}

    # ------------------------------------------------------------ survey dates

    @staticmethod
    def survey_date_layers():
        """{tile: [layers]}: the survey dates the plugin has loaded, by tile."""
        found = {}
        for layer in QgsProject.instance().mapLayers().values():
            tile = layer.customProperty(SURVEY_DATES_PROPERTY)
            if tile:
                found.setdefault(str(tile), []).append(layer)
        return found

    def survey_date_tiles(self):
        """The tiles the Survey dates button switches: the selected tiles downloaded of the chosen dataset, when it's
        one that comes with survey dates (England's Composite DTM / DSM, the SurfZone DEM)."""
        if not api.has_survey_dates(self.dataset.product):
            return []
        local = self.local_tiles()
        return [tile for tile in self.selected_names() if tile in local]

    def toggle_survey_dates(self, checked):
        """The Survey dates button. On: each selected tile's survey dates (the surveys merged into it, with when they
        were flown) as a layer of their own, read from the survey file that came with the tile. Off: those layers are
        removed."""
        tiles = self.survey_date_tiles()
        loaded = self.survey_date_layers()
        if not checked:
            layers = [layer for tile in tiles for layer in loaded.get(tile, [])]
            QgsProject.instance().removeMapLayers([layer.id() for layer in layers])
            layers = loaded = None  # don't keep Python wrappers of the layers QGIS has just deleted
            group = QgsProject.instance().layerTreeRoot().findGroup(SURVEY_DATES_GROUP)
            if group is not None and not group.findLayers():
                QgsProject.instance().layerTreeRoot().removeChildNode(group)
            self.fit_survey_colours()
            self.update_selection_label()
            return
        local = self.local_tiles()
        added, missing = 0, []
        for tile in sorted(tiles):
            if tile in loaded:
                continue
            layer = self.survey_dates_layer(tile, *local[tile])
            if layer is None:
                missing.append(tile)
                continue
            QgsProject.instance().addMapLayer(layer, False)
            self.layer_group(SURVEY_DATES_GROUP).addLayer(layer)
            added += 1
        self.fit_survey_colours()
        if added:
            self.deselect_after_loading()  # (to switch them off later, select the tiles again)
        text = (f"Survey dates for {plural(added, 'tile')}: hover over an area (with map tips on) to see when it was "
                "flown." if added else "No survey dates added.")
        if missing:
            text += f" {plural(len(missing), 'tile')} came without survey dates."
        self.notify(text, Qgis.MessageLevel.Warning if missing else Qgis.MessageLevel.Success,
                    ", ".join(missing) or None)
        self.update_selection_label()

    def fit_survey_colours(self):
        """Colour the survey dates of every tile shown over one span of years, from the oldest survey among them
        to the newest, so a year is the same colour in each tile (re-fitted as tiles are switched on or off). A
        layer whose colours have been changed since the plugin set them is left as it is."""
        mine = [layer for layers in self.survey_date_layers().values() for layer in layers
                if layer.customProperty(SURVEY_COLOURS_PROPERTY) in ('', styles.survey_signature(layer))]
        years = [year for layer in mine for year in styles.survey_years(layer)]
        if not years:
            return
        for layer in mine:
            styles.style_survey_dates(layer, (min(years), max(years)))
            layer.setCustomProperty(SURVEY_COLOURS_PROPERTY, styles.survey_signature(layer))

    def survey_dates_layer(self, tile, dataset, folder):
        """A tile's survey dates: the surveys in the survey file (a GeoPackage) that came with it, as it is, styled by
        the year flown; None if the tile came without one."""
        for path in storage.find_metadata_files(folder, tile):
            try:
                name = postprocess.survey_layer_name(path)
            except Exception:  # a survey file GDAL can't read: as if there were none
                name = None
            if not name:
                continue
            layer = QgsVectorLayer(f"{path}|layername={name}", f"{tile} survey dates", 'ogr')
            if not layer.isValid():
                continue
            styles.style_survey_dates(layer)
            layer.setCustomProperty(SURVEY_COLOURS_PROPERTY, '')  # coloured by the plugin, to be fitted
            self.describe_layer(layer, dataset)
            metadata = layer.metadata()
            metadata.setTitle(f"{tile}: when it was flown ({api.dataset_label(dataset)})")
            metadata.setAbstract("The surveys merged into the tile, with when each was flown, from the survey file "
                                 "that came with it.")
            layer.setMetadata(metadata)
            layer.setCustomProperty(SURVEY_DATES_PROPERTY, tile)
            return layer
        return None

    # ----------------------------------------------------------------- helpers

    @staticmethod
    def default_download_dir():
        """Documents/LiDAR Data (the folder name early versions used, kept so existing downloads are found)."""
        docs = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
        return os.path.join(docs, "LiDAR Data")

    def saved_download_dir(self):
        """The saved download folder ('' if none). Very early versions used a folder inside the
        plugin, which is ignored."""
        saved = QSettings().value(SETTINGS_KEY_DIR) or ''
        if saved and self.plugin_dir in os.path.abspath(saved):
            return ''
        return saved

    def get_download_dir(self):
        """The download folder shown in the panel (never a folder inside the plugin: plugin updates would wipe
        it)."""
        return self.dock.leDownloadDir.text().strip() or self.saved_download_dir() or self.default_download_dir()

    def dataset_dir(self):
        """Folder for the chosen dataset: <product>/<res>/<year>, or for "newest for each tile" the product (or
        product/res) folder above its survey folders."""
        return os.path.join(self.get_download_dir(), api.dataset_folder(self.dataset))

    def local_tiles(self):
        """{tile: (Dataset, folder)}: the downloaded version of each tile for the chosen dataset.

        For a single survey that's its folder; for "newest for each tile" it's the newest (then finest) survey
        downloaded for each tile.
        """
        if self.dataset.is_concrete:
            folder = self.dataset_dir()
            return {t: (self.dataset, folder) for t in storage.scan_downloaded_tiles(folder)}
        best = {}
        for dataset, folder in storage.local_datasets(self.get_download_dir(), self.dataset.product):
            if not self.dataset.matches(dataset):
                continue
            for tile in storage.scan_downloaded_tiles(folder):
                if tile not in best or api.best_dataset([best[tile][0], dataset]) == dataset:
                    best[tile] = (dataset, folder)
        return best

    def tile_surveys(self):
        """{tile: [(Dataset, folder)]}: the downloaded survey to load for each tile (see local_tiles). A list per
        tile, worst first and best last, so that when a tile draws on more than one survey the best is drawn on top
        of a mosaic."""
        return {tile: [(dataset, folder)] for tile, (dataset, folder) in self.local_tiles().items()}

    def offer_metres(self, tile_names):
        """Natural Resources Wales published its archive surveys with heights in millimetres. Before tiles of them
        are loaded (or made into a mosaic), ask once a session per survey whether to convert them to metres, which
        rewrites the files, or use them as published. Returns False if the user cancelled."""
        local = self.local_tiles()
        folders = sorted({local[t][1] for t in tile_names if t in local
                          and local[t][0].product.startswith('wales_lidar_archive')
                          and local[t][1] not in self.units_checked and postprocess.mm_grids(local[t][1])})
        if not folders:
            return True
        grids = [path for folder in folders for path in postprocess.mm_grids(folder)]
        loaded = [layer for folder in folders for layer in self.loaded_layers_under(folder)]
        choice = ask_choice(
            self.dock, "Heights in Millimetres",
            "These Welsh archive surveys were published with heights in millimetres, 1,000 times the heights in "
            "metres of other LIDAR.",
            "Convert them to metres? That rewrites each grid as a GeoTIFF in metres and deletes the millimetre file "
            "(the tiles can be downloaded again)" + (f"; {plural(len(loaded), 'layer')} of them in the project "
                                                     "will be removed first" if loaded else "")
            + ". Or keep them as published, in millimetres.",
            ["Convert to metres", "Keep millimetres"], details="\n".join(grids))
        if choice is None:
            return False
        self.units_checked.update(folders)
        if choice == 0:
            if loaded:
                # Windows won't delete a file that's open, and a removed layer lets go of its file only later: detach
                # them from their files first (as reorganising downloads does), then remove them
                for layer in loaded:
                    layer.setDataSource('', layer.name(), layer.providerType())
                QgsProject.instance().removeMapLayers([layer.id() for layer in loaded])
                loaded = None  # don't keep Python wrappers of the layers QGIS has just deleted
            for folder in folders:
                try:
                    postprocess.convert_mm_grids(folder)
                except (OSError, RuntimeError) as e:
                    self.notify(f"Couldn't convert {folder} to metres: {e}", Qgis.MessageLevel.Warning)
        return True

    def target_for(self, tile):
        """(Dataset, folder, url, offering) a download of the tile would use, or None if that isn't known yet.
        (offering is None when the tile hasn't been searched; url '' for sources that serve files.)"""
        offering = api.pick_offering(self.offerings_by_tile.get(tile, []), self.dataset)
        if offering is not None:
            folder = os.path.join(self.get_download_dir(), api.dataset_folder(offering.dataset))
            return offering.dataset, folder, offering.url, offering
        if self.dataset.is_concrete and api.product_source(self.dataset.product) == 'ea':
            return self.dataset, self.dataset_dir(), None, None
        return None

    def shading_entries(self):
        """[(label, colour, tiles)] for the grid: one colour per downloaded survey of the chosen dataset,
        then (with the coverage preview on) the tiles in view that have it but aren't downloaded."""
        local = self.local_tiles()
        by_dataset = {}
        for tile, (dataset, _) in local.items():
            by_dataset.setdefault(dataset, set()).add(tile)
        if self.dataset.is_concrete:
            entries = [('Downloaded', DOWNLOADED_COLOUR, by_dataset.get(self.dataset, set()))]
        else:
            entries = []
            for index, dataset in enumerate(api.sorted_datasets(by_dataset)):
                label = api.short_dataset_label(dataset)
                entries.append((label, DATASET_COLOURS[index % len(DATASET_COLOURS)], by_dataset[dataset]))
        coverage = self.coverage_tiles(local) if self.dock is not None else set()
        if coverage:
            entries.append(('Available to download (in view)', COVERAGE_COLOUR, coverage))
        return entries

    def refresh_grid_style(self):
        entries = self.shading_entries()
        self.grid.apply_style(entries)
        self.update_legend(entries)

    def update_legend(self, entries=None):
        """What the grid's shading means: the "Shaded for" button (the dataset it's about), the colour key in its
        own section at the bottom of the dock, and the note under 'Also shade tiles available to download'."""
        if entries is None:
            entries = self.shading_entries()
        self.update_shaded_button(entries)
        if self.grid.layer() is None:
            self.dock.grpLegend.setVisible(False)
            self.update_coverage_note(None)
            return
        self.update_coverage_note(next((tiles for _, colour, tiles in entries if colour == COVERAGE_COLOUR), set()))
        rows = [(colour, f"{text} ({len(tiles):,} tile{'s' if len(tiles) != 1 else ''})")
                for text, colour, tiles in entries if tiles]
        if not any(tiles for text, colour, tiles in entries if colour != COVERAGE_COLOUR):
            rows.insert(0, ('#ffffff', 'None downloaded yet'))
        rows.append(('#bebebe', 'No data (faint outline)'))
        # Show the section before setting the text, so the label's wrapped height is measured at its real width
        self.dock.grpLegend.setVisible(True)
        self.dock.setLegendRows(rows)

    @staticmethod
    def shaded_label(dataset, count):
        """A dataset with how many tiles of it are downloaded, e.g. 'England Composite DTM, 2022, 1 m (4 tiles)'."""
        tiles = f"{count:,} tile{'s' if count != 1 else ''}" if count else "none downloaded yet"
        return f"{api.dataset_label(dataset)} ({tiles})"

    def update_shaded_button(self, entries=None):
        """Name, on the "Shaded for" button, the dataset the grid's shading is about (the chosen one); its tooltip
        adds how many tiles of it are downloaded (as the legend and the button's menu do)."""
        if entries is None:
            entries = self.shading_entries()
        downloaded = set().union(*(tiles for _, colour, tiles in entries if colour != COVERAGE_COLOUR))
        self.dock.btnShaded.setFullText(
            api.dataset_label(self.dataset),
            f"{self.shaded_label(self.dataset, len(downloaded))}: the grid is shaded for it. Choose another of "
            "your downloads here; it also follows the dataset chosen in step 2.")

    def refresh_shaded_menu(self):
        """Fill the "Shaded for" menu as it opens: every survey in the download folder with its tile count (in the
        Dataset menu's order), and the chosen dataset if it isn't one of them (nothing of it downloaded yet, or
        "newest for each tile", which shades a product's surveys together). Choosing one makes it the chosen
        dataset, as choosing in step 2 does."""
        menu = self.dock.btnShaded.menu()
        menu.clear()
        group = QActionGroup(menu)
        group.setExclusive(True)
        entries = [(dataset, len(tiles))
                   for dataset, _, tiles in storage.downloaded_datasets(self.get_download_dir()) if tiles]
        if self.dataset not in [dataset for dataset, _ in entries]:
            entries.insert(0, (self.dataset, len(self.local_tiles())))
        for dataset, count in entries:
            action = menu.addAction(self.shaded_label(dataset, count))
            action.setCheckable(True)
            action.setChecked(dataset == self.dataset)
            group.addAction(action)
            action.triggered.connect(lambda _checked=False, d=dataset: self.set_dataset(d))

    def update_coverage_note(self, shaded):
        """Say under 'Also shade tiles available to download' what it's doing: waiting for the map to be zoomed in,
        checking, or how many tiles it shaded (shaded: those tiles; None when there's no grid)."""
        text = ''
        if shaded is not None and self.dock.chkCoverage.isChecked():
            if self.coverage_view is None:
                text = f"Zoom in to see which tiles have it: it works with up to {MAX_COVERAGE_TILES:,} tiles in view."
            elif self.coverage_task is not None:
                text = "Checking the tiles in view..."
            elif self.coverage_error:
                text = f"Couldn't check all the tiles in view: {self.coverage_error}"
            elif shaded:
                text = (f"{len(shaded):,} tile{'s' if len(shaded) != 1 else ''} in view shaded blue: they have it and "
                        "aren't downloaded yet.")
            else:
                text = "No tiles in view have it that aren't downloaded already."
        # In the tick box's tooltip, to save room; in view only when it says why nothing is shaded
        self.dock.chkCoverage.setToolTip(self.coverage_tooltip + (f"\n\n{text}" if text else ''))
        shown = text if (self.coverage_view is None or self.coverage_error) and shaded is not None else ''
        self.dock.lblCoverageNote.setText(shown)
        self.dock.lblCoverageNote.setVisible(bool(shown))

    def update_selection_label(self):
        tiles = self.grid.selected_tiles() if self.grid.layer() is not None else []
        outside = []
        if self.crop_tiles is not None:  # only the tiles the crop needs count
            outside = [name for name, _ in tiles if name not in self.crop_tiles]
            tiles = [(name, data) for name, data in tiles if name in self.crop_tiles]
        if not tiles:
            self.dock.lblTileCount.setText("None of the selected tiles are under the crop" if outside
                                           else "No tiles selected yet")
            self.update_download_button()
            self.update_after_download_choices()
            self.update_use_buttons()
            return

        available, unavailable, _ = self.availability([name for name, _ in tiles])
        local = self.local_tiles()
        to_fetch, already = [], []
        for tile in available:
            target = self.target_for(tile)
            if target is not None:
                (already if storage.is_downloaded(target[1], tile) else to_fetch).append(tile)
            else:
                (already if tile in local else to_fetch).append(tile)

        # One line (what will be downloaded, and its size, is on the Download button)
        parts = []
        if already:
            parts.append(f"{len(already)} downloaded")
        if unavailable:
            parts.append(f"{len(unavailable)} without this data")
        if outside:
            parts.append(f"{len(outside)} outside the crop")
        self.dock.lblTileCount.setText(f"{plural(len(tiles), 'tile')} selected"
                                       + (f": {', '.join(parts)}" if parts else ''))
        self.update_download_button(to_fetch, already)
        self.update_after_download_choices([name for name, _ in tiles])
        self.update_use_buttons([name for name, _ in tiles if name in local])

    def update_download_button(self, to_fetch=(), already=()):
        """Say on the Download button what it will do (the tiles to fetch, or with none new, the tiles it would
        fetch again after asking), and grey it out when there's nothing it can do or a download is running."""
        count = len(to_fetch) or len(already)
        tiles = f"{count:,} tile{'s' if count != 1 else ''}"
        if to_fetch:
            text = f"Download {tiles} (~{format_size(self.download_size_mb(to_fetch)[0])})"
        elif already:
            text = f"Download {tiles} again..."
        else:
            text = "Download selected tiles"
        self.dock.btnDownloadTiles.setText(text)
        self.dock.btnDownloadTiles.setEnabled(bool(count) and self.task is None)

    def on_after_download_chosen(self, index):
        combo = self.dock.cboAfterDownload
        key = combo.itemData(index)
        if key in AFTER_DOWNLOAD_CHOICES:
            self.after_download = key
            QSettings().setValue(SETTINGS_KEY_AFTER_DOWNLOAD, key)
        combo.setToolTip(combo.itemData(index, Qt.ItemDataRole.ToolTipRole))

    def update_after_download_choices(self, tiles=()):
        """Step 3's choice of what happens once the tiles are downloaded. A temporary mosaic is for two or more
        selected tiles that make a square or rectangle (so it has no empty space), of data that can be joined (not
        point clouds or oblique photos). When it can't be used the tiles are loaded individually instead; it's
        chosen again when it can."""
        joinable = api.product_kind(self.dataset.product) in (api.RASTER, api.IMAGERY)
        crop = joinable and self.crop_tiles is not None  # loading gives the crop: the only way to load
        possible = joinable and not crop and places.fills_rectangle(tiles)
        combo = self.dock.cboAfterDownload
        tiles_row, mosaic_row = combo.findData('tiles'), combo.findData('mosaic')
        combo.setItemText(tiles_row, "Load the crop" if crop else "Load each tile")
        combo.setItemData(tiles_row, "When the download finishes, add your area as one layer: the tiles under the "
                          "crop joined and cut to its shape (the tiles themselves are downloaded whole)" if crop
                          else self.tiles_tooltip, Qt.ItemDataRole.ToolTipRole)
        combo.view().setRowHidden(mosaic_row, crop)
        combo.model().item(mosaic_row).setEnabled(possible)
        shown = 'tiles' if self.after_download == 'mosaic' and not possible else self.after_download
        combo.blockSignals(True)  # (not the user's choice)
        combo.setCurrentIndex(combo.findData(shown))
        combo.blockSignals(False)
        combo.setToolTip(combo.itemData(combo.currentIndex(), Qt.ItemDataRole.ToolTipRole))

    def after_download_choice(self):
        """What to do with tiles once downloaded, as step 3 shows it: 'tiles', 'mosaic' or 'none'."""
        return self.dock.cboAfterDownload.currentData() or 'tiles'

    def update_use_buttons(self, downloaded=()):
        """Step 4's buttons act on the selected tiles that are downloaded (of the chosen dataset), so they're greyed
        out when there are none, or while a download or a mosaic is being made. Create mosaic isn't for point clouds or
        oblique photos (they're only ever loaded as downloaded). Survey dates is only for products that come with
        them, and shows as on while the survey dates of any of the selected tiles are shown."""
        if self.dock is None:
            return
        busy = self.task is not None
        kind = api.product_kind(self.dataset.product)
        self.dock.btnLoadSelected.setEnabled(bool(downloaded) and not busy)
        self.dock.btnLoadSelected.setText("Load crop" if self.crop_tiles is not None
                                          and kind in (api.RASTER, api.IMAGERY) else "Load selected")
        self.dock.btnCreateVRT.setEnabled(bool(downloaded) and kind not in (api.PHOTOS, api.POINT_CLOUD) and not busy
                                          and self.post_task is None)
        dated = list(downloaded) if api.has_survey_dates(self.dataset.product) else []
        loaded = self.survey_date_layers() if dated else {}
        self.dock.btnSurveyDates.setEnabled(bool(dated))
        self.dock.btnSurveyDates.blockSignals(True)
        self.dock.btnSurveyDates.setChecked(any(tile in loaded for tile in dated))
        self.dock.btnSurveyDates.blockSignals(False)

    # ------------------------------------------------------------- datasets

    def download_size_mb(self, tiles):
        """(download MB, disk MB) for downloading the tiles in the chosen dataset: the files' listed sizes where the
        service gives them (Scotland), else typical sizes; shared 10 km files counted once; zips count twice on disk
        (they extract to about the same again) unless they're deleted after extracting."""
        counted, download, disk = set(), 0.0, 0.0
        delete = self.delete_zips()
        for tile in tiles:
            target = self.target_for(tile)
            offering = target[3] if target else None
            size = api.download_mb(offering, counted) if offering else api.estimate_tile_mb(self.dataset)
            zipped = offering is None or not offering.files or any(f.name.lower().endswith('.zip')
                                                                   for f in offering.files)
            download += size
            disk += api.disk_mb(size, zipped, delete)
        return download, disk

    def load_dataset_setting(self):
        """The dataset chosen last time (England's Composite DTM the first time)."""
        value = QSettings().value(SETTINGS_KEY_DATASET, '') or ''
        parts = value.split('|')
        return api.Dataset(*parts) if len(parts) == 3 and all(parts) else api.DEFAULT_DATASET

    def selected_names(self):
        """The selected tiles' names, [] if none (or no grid). See selected_tile_names for actions that need
        some."""
        if self.grid is None or self.grid.layer() is None:
            return []
        return [name for name, _ in self.grid.selected_tiles()]

    def availability(self, tiles):
        """Split tiles into (available, unavailable, unknown) for the chosen dataset.

        Unknown tiles haven't been checked with the search API (yet); they're
        treated as available and the download itself reports any gaps.
        """
        available, unavailable, unknown = [], [], []
        for tile in tiles:
            if tile not in self.searched_tiles:
                unknown.append(tile)
                available.append(tile)
            elif api.pick_offering(self.offerings_by_tile.get(tile, []), self.dataset):
                available.append(tile)
            else:
                unavailable.append(tile)
        return available, unavailable, unknown

    def schedule_search(self):
        """Check availability for newly selected tiles, once the selection settles."""
        if self.search_timer is None:
            return
        tiles = self.selected_names()
        if any(t not in self.searched_tiles for t in tiles):
            self.refresh_dataset_menu()  # the chosen dataset, and that it's checking what the tiles have
            self.search_timer.start()
        else:
            # Already known (selected before, or checked by the coverage shading): no search to wait for
            self.switch_if_needed()
            self.refresh_dataset_menu()

    def start_search(self):
        if self.search_task is not None:
            return  # on_search_finished checks again
        tiles = [t for t in self.selected_names() if t not in self.searched_tiles]
        if not tiles:
            self.refresh_dataset_menu()
            return
        if len(self.selected_names()) > MAX_SEARCH_TILES:
            self.refresh_dataset_menu()
            return

        extents = self.grid.selected_tile_extents()
        areas = search_areas({t: extents[t] for t in tiles}, self.grid.layer().crs())

        self.search_task = SearchTask(areas, on_finished=self.on_search_finished)
        self.search_task.requested_tiles = tiles
        QgsApplication.taskManager().addTask(self.search_task)

    def on_search_finished(self, task, ok):
        self.search_task = None
        if self.dock is None:
            return
        if ok:
            # Keep what the services that answered found; tiles a failed service covers stay unchecked
            checked = [t for t in task.requested_tiles if t[:2] not in task.failed_squares]
            self.record_offerings(checked, [o for o in task.offerings if o.tile[:2] not in task.failed_squares])
        if task.error:
            QgsMessageLog.logMessage(f"Availability check failed: {task.error} {task.error_detail}".strip(), TITLE,
                                     Qgis.MessageLevel.Warning)
        if ok:
            self.switch_if_needed()
        self.refresh_dataset_menu(search_error=task.error)
        self.update_selection_label()
        if self.dock.chkCoverage.isChecked():
            self.refresh_grid_style()
        # The selection may have changed while we were searching
        if ok and not task.error and any(t not in self.searched_tiles for t in self.selected_names()):
            self.search_timer.start()
        elif self.resume_pending:
            self.resume_pending = False
            # Once QGIS has finished with this task: a task added from inside one's finished() is now and then lost
            # by QGIS's task manager (see tasks.ParallelDownloadTask._part_finished)
            QTimer.singleShot(0, self.download_selected_tiles)

    def switch_if_needed(self):
        """Once for each change of selection, as soon as the services have said what all its tiles have (a search
        just finished, one earlier this session, or the coverage shading's check): if the chosen dataset covers
        none of them, switch to their nation's own and say so under the menu."""
        tiles = self.selected_names()
        if not self.switch_pending or not tiles or any(t not in self.searched_tiles for t in tiles):
            return
        self.switch_pending = False
        switched = self.switch_to_nation_dataset()
        if switched:
            self.switch_note = f"The tiles are in {switched}, so the dataset is now {api.dataset_label(self.dataset)}."

    def switch_to_nation_dataset(self):
        """The chosen dataset covers none of the selected tiles (and none of them is downloaded for it), but the
        nation they're in has its equivalent (e.g. England's Composite DTM chosen, Scottish tiles selected): switch to
        it (the nation with most of the tiles). Returns the nation's name if it switched, else None."""
        tiles = self.selected_names()
        known = [t for t in tiles if t in self.searched_tiles]
        offerings = [o for t in known for o in self.offerings_by_tile.get(t, [])]
        if not known or len(known) < len(tiles) or not offerings \
                or any(self.dataset.matches(o.dataset) for o in offerings):
            return None
        if any(tile in tiles for tile in self.local_tiles()):
            return None  # downloaded for some of them (a survey no longer listed, say): still the one to load
        coverage = api.summarise(offerings, known)
        product = self.dataset.product
        kind = ('cloud' if api.product_kind(product) == api.POINT_CLOUD else 'dsm' if 'dsm' in product else 'dtm')
        tiles_by_source = {}
        for offering in offerings:
            tiles_by_source.setdefault(api.product_source(offering.dataset.product), set()).add(offering.tile)
        for source in sorted(tiles_by_source, key=lambda s: -len(tiles_by_source[s])):
            wanted = api.EQUIVALENTS.get((source, kind)) or api.EQUIVALENTS.get((source, 'dtm'))
            if wanted is None:
                continue
            # The equivalent as offered here: itself, else its product's best survey for these tiles
            surveys = [d for d in coverage if d.product == wanted.product and d.is_concrete]
            choice = wanted if wanted in coverage else (api.best_dataset(surveys) if surveys else None)
            if choice is not None:
                self.set_dataset(choice)
                return api.NATION_NAMES[source]
        return None

    def record_offerings(self, tiles, offerings):
        """Cache what the search found for these tiles (tiles with nothing found have no data)."""
        requested = set(tiles)
        for tile in requested:
            self.offerings_by_tile[tile] = []
        for offering in offerings:
            if offering.tile in requested:
                self.offerings_by_tile[offering.tile].append(offering)
            if offering.rights:
                self.survey_rights[offering.dataset] = offering.rights
        self.searched_tiles.update(requested)

    def refresh_dataset_menu(self, search_error=None):
        """List the datasets available for the selected tiles, keeping the current choice."""
        if self.dock is None:  # may run from a timer after the plugin was unloaded
            return
        tiles = self.selected_names()
        known = [t for t in tiles if t in self.searched_tiles]
        offerings = [o for t in known for o in self.offerings_by_tile.get(t, [])]
        coverage = api.summarise(offerings, known)
        in_england = len(known) < len(tiles) or not known or any(
            api.product_source(o.dataset.product) == 'ea' for t in known for o in self.offerings_by_tile.get(t, []))
        always = set(api.ALWAYS_LISTED) if in_england else set()
        datasets = api.sorted_datasets(set(coverage) | always | {self.dataset})

        def counts(dataset):
            return f"  ({len(coverage.get(dataset, ()))}/{len(known)} tiles)" if known else ''

        def add_nodes(menu, nodes):
            for label, child in nodes:
                if isinstance(child, api.Dataset):
                    licence = " (non-commercial)" if api.is_non_commercial(self.survey_rights.get(child, '')) else ""
                    action = menu.addAction(label + licence + counts(child))
                    action.setCheckable(True)
                    action.setChecked(child == self.dataset)
                    action.setData(api.dataset_key(child))
                    action.setToolTip(self.dataset_tooltip(child))
                    self.dataset_actions.addAction(action)
                    action.triggered.connect(lambda _checked=False, d=child: self.set_dataset(d))
                else:
                    members = api.tree_datasets((label, child))
                    covered = set().union(*(coverage.get(d, set()) for d in members))
                    sub = menu.addMenu(label + (f"  ({len(covered)}/{len(known)} tiles)" if known else ''))
                    sub.setToolTipsVisible(True)
                    if len({d.product for d in members}) == 1:  # a product: what it is
                        sub.menuAction().setToolTip(self.dataset_tooltip(members[0], product_only=True))
                    if self.dataset in members:
                        font = sub.menuAction().font()
                        font.setBold(True)  # show which branch holds the current choice
                        sub.menuAction().setFont(font)
                    add_nodes(sub, child)

        # The menu (see api.dataset_tree): each product with its surveys, by nation across a border; LIDAR first,
        # then aerial photos after a separator
        menu = self.dock.btnDataset.menu()
        menu.clear()
        self.dataset_actions = QActionGroup(menu)
        self.dataset_actions.setExclusive(True)
        if tiles:  # (with none selected the button is a prompt to select some: see show_chosen_dataset)
            tree = api.dataset_tree(datasets)
            lidar = [n for n in tree if not api.is_imagery(api.tree_datasets(n)[0].product)]
            imagery = [n for n in tree if api.is_imagery(api.tree_datasets(n)[0].product)]
            add_nodes(menu, lidar)
            if lidar and imagery:
                menu.addSeparator()
            add_nodes(menu, imagery)
        self.show_chosen_dataset(counts(self.dataset))

        if search_error and api.NOT_RESPONDING in search_error:
            text = f"Couldn't check what's available. {search_error}"
        elif search_error and search_error.startswith(('DataMapWales', 'Scottish')):
            text = (f"Couldn't check what's available ({search_error}). England's downloads still work; "
                    "for Wales and Scotland, try again in a moment.")
        elif search_error:
            text = f"Couldn't check what's available ({search_error}). Try again in a moment."
        elif len(tiles) > MAX_SEARCH_TILES:
            text = f"Select {MAX_SEARCH_TILES} tiles or fewer to see which datasets cover them."
        elif not tiles:
            text = "Select tiles to see which datasets cover them."
        elif len(known) < len(tiles):
            text = "Checking what the tiles have..."
        elif coverage and not any(self.dataset.matches(d) for d in coverage):
            text = (f"Not here: choose from the {plural(sum(1 for d in coverage if d.is_concrete), 'dataset')} in "
                    "the menu.")
        else:
            surveys = [d for d in coverage if d.is_concrete]
            photos = sum(1 for d in surveys if api.is_imagery(d.product))
            text = (plural(len(surveys) - photos, 'LIDAR dataset')
                    + (f" + {plural(photos, 'aerial photo set')}" if photos else ''))
        if self.switch_note and not search_error:
            text = f"{self.switch_note} {text}"
        self.dock.lblAvailability.setText(text)

    def show_chosen_dataset(self, counts=''):
        """Step 2's menu button and the line under it: the chosen dataset, or with no tiles selected a prompt to
        select some (what can be downloaded depends on the tiles; the "Downloaded" button names the dataset
        meanwhile). What the dataset is shows in the button's tooltip and the info icon beside it."""
        has_tiles = bool(self.selected_names())
        tooltip = self.dataset_tooltip(self.dataset, counts)
        self.dock.btnDataset.setEnabled(has_tiles)
        self.dock.btnDataset.setFullText(api.dataset_label(self.dataset) + counts if has_tiles
                                         else SELECT_TILES_PROMPT, tooltip if has_tiles else None)
        self.dock.btnDatasetInfo.setToolTip(tooltip)
        self.dock.btnDatasetInfo.setEnabled(has_tiles)
        self.dock.lblAvailability.setVisible(has_tiles)

    @staticmethod
    def dataset_tooltip(dataset, counts='', product_only=False):
        """What a dataset is, for tooltips: its name in bold (the product's alone for product_only), then the
        sentence describing the product."""
        name = api.product_label(dataset.product) if product_only else api.dataset_label(dataset) + counts
        return (f"<b>{html.escape(name.strip())}</b><br>"
                f"{html.escape(api.product_description(dataset.product))}")

    def set_dataset(self, dataset):
        self.dataset = dataset
        QSettings().setValue(SETTINGS_KEY_DATASET, api.dataset_key(dataset))
        # Called from a menu action's triggered signal: rebuilding the menu here would delete
        # that action while its signal is still running (crashed QGIS 3.x). Qt has already
        # checked the action; update the button now and rebuild the menu once control returns.
        self.show_chosen_dataset()
        QTimer.singleShot(0, self.refresh_dataset_menu)
        self.refresh_grid_style()
        self.update_selection_label()

    def selected_tile_names(self):
        """The selected tiles' names for an action that needs some, or None (after telling the user why) if there
        are none."""
        if self.grid.layer() is None:
            self.notify("Tick 'Show OSGB 5 km grid' and select some tiles first.", Qgis.MessageLevel.Warning)
            return None
        tiles = self.grid.selected_tile_names()
        if not tiles:
            self.notify("No tiles selected.", Qgis.MessageLevel.Warning)
            return None
        return tiles

    def set_status(self, text):
        """The line under step 3: there only while it has something to say."""
        if self.dock is not None:
            self.dock.lblStatus.setText(text)
            self.dock.lblStatus.setVisible(bool(text))

    def set_busy(self, busy):
        for widget in (self.dock.btnBrowse, self.dock.leDownloadDir, self.dock.btnResume):
            widget.setEnabled(not busy)
        if self.downloads_dialog is not None:  # (the zip tools: not while a download runs)
            self.downloads_dialog.chkDeleteZips.setEnabled(not busy)
            self.downloads_dialog.btnTidyZips.setEnabled(not busy)
        self.dock.btnCancel.setEnabled(busy)
        self.dock.progressBar.setVisible(busy)
        self.update_selection_label()  # the Download and step 4 buttons: greyed out while a download runs

    # --------------------------------------------------------------- download

    def confirm_download(self, tiles, existing):
        """Ask about re-downloading existing tiles and about large downloads.

        Returns the set of tiles to reuse from disk, or None if the user cancelled.
        """
        new = [t for t in tiles if t not in existing]
        if existing:
            choice = ask_choice(
                self.dock, "Already Downloaded",
                f"{len(existing)} of the {len(tiles)} tile(s) are already downloaded.",
                f"Skipping them leaves {len(new)} to download "
                f"(about {format_size(self.download_size_mb(new)[0])}).",
                ["Skip existing", "Download all again"])
            if choice is None:
                return None
            return set(existing) if choice == 0 else set()

        if len(new) > CONFIRM_ABOVE_TILES:
            choice = ask_choice(
                self.dock, "Large Download",
                f"Download {len(new)} tiles of {api.dataset_label(self.dataset)}?",
                f"That's about {format_size(self.download_size_mb(new)[0])}.",
                ["Download"])
            if choice is None:
                return None
        return set()

    def accept_licences(self, offerings):
        """Surveys some sources license for non-commercial use only (e.g. Scotland's Phase II point clouds):
        say so, and ask, before downloading them. Returns False if the user cancelled."""
        restricted = {o.dataset: o.rights for o in offerings if api.is_non_commercial(o.rights)}
        if not restricted:
            return True
        details = "\n\n".join(f"{api.dataset_label(d)}:\n{text}" for d, text in restricted.items())
        return ask_choice(
            self.dock, "Licence Terms",
            "Some of these surveys are licensed for non-commercial use only.",
            "The terms are below (Show Details). Only download them if your use is allowed.",
            ["Accept and download"], details=details) is not None

    def enough_space(self, new_tiles):
        """Check the drive has room for downloading new_tiles (and what they extract to); if not, ask.
        Returns False if the user cancelled."""
        needed = self.download_size_mb(new_tiles)[1] * 1e6
        free = storage.free_space(self.get_download_dir())
        if not needed or free is None or needed <= free:
            return True
        return ask_choice(
            self.dock, "Not Enough Disk Space?",
            f"These downloads need about {format_bytes(needed)} (the zips and what they extract to), but the drive "
            f"holding {self.get_download_dir()} has {format_bytes(free)} free.",
            "Downloads that don't fit will fail part-way. Free some space (My downloads... shows what's taking it), "
            "tick \"Delete zips after extracting\", or choose fewer tiles.",
            ["Download anyway"], warning=True) is not None

    def download_selected_tiles(self):
        """Download the chosen dataset for the selected tiles. Tiles without it are skipped; the user is asked
        about tiles already downloaded, large downloads, disk space and non-commercial licences; then the tiles
        download in the background (PARALLEL_DOWNLOADS at a time), into the folder of the survey each comes
        from."""
        if self.task is not None or self.dock is None:  # (busy, or unloaded before a Resume started it)
            return
        if self.grid.layer() is None:
            self.selected_tile_names()  # shows the "load the grid first" message
            return
        selected = self.grid.selected_tiles()
        if not selected:
            self.notify("No tiles selected.", Qgis.MessageLevel.Warning)
            return

        tile_names = [name for name, _ in selected]
        if self.crop_tiles is not None:  # with "Crop to" ticked: only the tiles the crop needs
            outside = [name for name in tile_names if name not in self.crop_tiles]
            tile_names = [name for name in tile_names if name in self.crop_tiles]
            if outside:
                self.notify(f"{plural(len(outside), 'selected tile')} left out: 'Crop to' is ticked, so only the tiles "
                            "the crop needs are downloaded.", Qgis.MessageLevel.Info, ", ".join(outside))
            if not tile_names:
                return
        tile_names, unavailable, _ = self.availability(tile_names)
        if unavailable:
            self.notify(f"Skipping {len(unavailable)} tile(s) with no {api.dataset_label(self.dataset)} data.",
                        Qgis.MessageLevel.Info, ", ".join(unavailable))
        if not tile_names:
            return

        # Each tile goes into the folder of the survey it comes from
        targets = {t: self.target_for(t) for t in tile_names}
        unknown = [t for t, target in targets.items() if target is None]
        if unknown:
            self.notify(f"Skipping {len(unknown)} tile(s): couldn't check which surveys cover them yet. "
                        "Try again in a moment.", Qgis.MessageLevel.Warning, ", ".join(unknown))
            tile_names = [t for t in tile_names if targets[t] is not None]
            if not tile_names:
                return

        QSettings().setValue(SETTINGS_KEY_DIR, self.get_download_dir())
        folders = {t: targets[t][1] for t in tile_names}
        try:
            for folder in set(folders.values()):
                os.makedirs(folder, exist_ok=True)
        except OSError as e:
            self.notify(f"Could not create download folder: {e}", Qgis.MessageLevel.Critical)
            return

        existing = [t for t in tile_names if storage.is_downloaded(folders[t], t)]
        reuse = self.confirm_download(tile_names, existing)
        if reuse is None:
            return
        if not self.enough_space([t for t in tile_names if t not in reuse]):
            return
        offerings = {t: targets[t][3] for t in tile_names if targets[t][3] is not None}
        if not self.accept_licences(offerings.values()):
            return
        for offering in offerings.values():
            if offering.rights:  # kept with the download, for the layers' metadata
                storage.write_survey_info(folders[offering.tile], {
                    'dataset': api.dataset_label(offering.dataset), 'rights': offering.rights})

        urls = {t: targets[t][2] for t in tile_names if targets[t][2]}
        files = {t: o.files for t, o in offerings.items() if o.files}
        options = dict(delete_zips=self.delete_zips(), on_finished=self.on_download_finished,
                       dataset=self.dataset, urls=urls, folders=folders, files=files)
        if len(tile_names) > 1 and PARALLEL_DOWNLOADS > 1:
            self.task = ParallelDownloadTask(tile_names, self.dataset_dir(), reuse, parallel=PARALLEL_DOWNLOADS,
                                             **options)
        else:
            self.task = DownloadTilesTask(tile_names, self.dataset_dir(), reuse, **options)
        self.task.statusMessage.connect(self.set_status)
        self.task.progressChanged.connect(lambda p: self.dock.progressBar.setValue(int(p)))

        self.dock.progressBar.setValue(0)
        self.set_status("Starting download...")
        self.set_busy(True)
        self.download_dataset = self.dataset
        self.download_selection = set(self.grid.selected_tile_names())
        self.save_unfinished(self.dataset, tile_names)  # until they're done (QGIS may close, or a tile fail)
        QgsApplication.taskManager().addTask(self.task)

    def save_unfinished(self, dataset, tiles):
        """Remember tiles still to download (a download running, cancelled or partly failed) to offer resuming."""
        if tiles:
            QSettings().setValue(SETTINGS_KEY_UNFINISHED, json.dumps({
                'dataset': api.dataset_key(dataset), 'tiles': sorted(tiles), 'folder': self.get_download_dir()}))
        else:
            QSettings().remove(SETTINGS_KEY_UNFINISHED)
        self.update_resume_button()

    def unfinished(self):
        """(Dataset, tiles, download folder) of the last download that didn't finish, or None."""
        try:
            data = json.loads(QSettings().value(SETTINGS_KEY_UNFINISHED, '') or '{}')
        except (TypeError, ValueError):
            return None
        parts = str(data.get('dataset') or '').split('|')
        tiles = [t for t in data.get('tiles') or [] if api.TILE_RE.match(str(t))]
        if len(parts) != 3 or not tiles:
            return None
        return api.Dataset(*parts), tiles, str(data.get('folder') or '')

    def update_resume_button(self):
        if self.dock is None:
            return
        unfinished = self.unfinished() if self.task is None else None
        self.dock.btnResume.setVisible(unfinished is not None)
        if unfinished is not None:
            dataset, tiles, _ = unfinished
            self.dock.btnResume.setText(f"Resume: {len(tiles)} tile{'s' if len(tiles) != 1 else ''} not downloaded yet")
            self.dock.btnResume.setToolTip(f"{api.dataset_label(dataset)}: {', '.join(tiles)}")

    def resume_download(self):
        """Select the unfinished download's tiles (zoomed to them), in its dataset and folder, and download them
        once their availability is known."""
        unfinished = self.unfinished()
        if unfinished is None or self.task is not None or not self.ensure_grid():
            return
        dataset, tiles, folder = unfinished
        if folder and os.path.isdir(folder) and folder != self.get_download_dir():
            self.dock.leDownloadDir.setText(folder)
            QSettings().setValue(SETTINGS_KEY_DIR, folder)
        self.select_dataset_tiles(dataset, tiles)
        if all(t in self.searched_tiles for t in tiles):
            self.download_selected_tiles()
        else:
            self.resume_pending = True  # on_search_finished starts it
            self.set_status("Checking the unfinished tiles, then resuming...")

    @staticmethod
    def delete_zips():
        """Whether zips are deleted once extracted (set in My downloads)."""
        return QSettings().value(SETTINGS_KEY_DELETE_ZIPS, False, type=bool)

    def show_downloads(self):
        if self.downloads_dialog is None:
            self.downloads_dialog = DownloadsDialog(self.iface.mainWindow())
            self.downloads_dialog.showRequested.connect(self.show_download_on_grid)
            self.downloads_dialog.loadRequested.connect(self.load_download)
            self.downloads_dialog.openRequested.connect(self.open_download_folder)
            self.downloads_dialog.deleteRequested.connect(self.delete_download)
            self.downloads_dialog.refreshRequested.connect(self.refresh_downloads)
            self.downloads_dialog.chkDeleteZips.setChecked(self.delete_zips())
            self.downloads_dialog.chkDeleteZips.toggled.connect(
                lambda checked: QSettings().setValue(SETTINGS_KEY_DELETE_ZIPS, checked))
            self.downloads_dialog.btnTidyZips.clicked.connect(self.tidy_up_zips)
        busy = self.task is not None
        self.downloads_dialog.chkDeleteZips.setEnabled(not busy)
        self.downloads_dialog.btnTidyZips.setEnabled(not busy)
        self.refresh_downloads()
        self.downloads_dialog.show()
        self.downloads_dialog.raise_()

    def refresh_downloads(self):
        if self.downloads_dialog is None:
            return
        root = self.get_download_dir()
        self.download_rows = storage.download_summary(root)
        self.downloads_dialog.set_rows([(api.dataset_label(d), len(tiles), size, api.squares_text(tiles), tiles)
                                        for d, _, tiles, size in self.download_rows],
                                       root, sum(row[3] for row in self.download_rows), storage.free_space(root))

    def show_download_on_grid(self, index):
        """Choose a downloaded survey and select its tiles on the grid (zoomed to them)."""
        if not 0 <= index < len(self.download_rows) or not self.ensure_grid():
            return False
        dataset, _, tiles, _ = self.download_rows[index]
        self.select_dataset_tiles(dataset, tiles)
        return True

    def select_dataset_tiles(self, dataset, tiles):
        """Choose a dataset and select its tiles (zoomed to them), for "Show on grid" and Resume. The dataset was
        asked for by name, so it stays chosen even if the services don't list it for those tiles (any more)."""
        self.set_dataset(dataset)
        self.keep_dataset = True
        try:
            self.grid.select_names(tiles)
        finally:
            self.keep_dataset = False
        self.switch_pending = False  # (also when the same tiles were selected already, so nothing changed)
        self.iface.mapCanvas().zoomToSelected(self.grid.layer())

    def load_download(self, index):
        if self.show_download_on_grid(index):
            self.load_selected_tiles()

    def open_download_folder(self, index):
        if 0 <= index < len(self.download_rows):
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.download_rows[index][1]))

    def delete_download(self, index):
        """Permanently delete one survey's folder, after a warning listing what goes (and project layers using it)."""
        if not 0 <= index < len(self.download_rows):
            return
        dataset, folder, tiles, size = self.download_rows[index]
        inside = os.path.normcase(os.path.abspath(folder)) + os.sep
        using = [layer for layer in QgsProject.instance().mapLayers().values()
                 if os.path.exists(layer.source().split('|')[0])
                 and os.path.normcase(os.path.abspath(layer.source().split('|')[0])).startswith(inside)]
        note = (f"\n\n{len(using)} layer(s) in this project use these files and will be removed from the project."
                if using else "")
        choice = ask_choice(
            self.downloads_dialog, "Delete Download",
            f"Permanently delete {api.dataset_label(dataset)}: {len(tiles)} tile(s), {format_bytes(size)}?",
            f"This deletes the folder {folder} and everything in it: the tiles, their zips, and the mosaics "
            "and point cloud indexes made from them. Files are not moved to the Recycle Bin, but the tiles can be "
            f"downloaded again.{note}",
            [f"Permanently delete {format_bytes(size)}"], warning=True,
            details="\n".join(sorted(tiles)))
        if choice is None:
            return
        if using:
            QgsProject.instance().removeMapLayers([layer.id() for layer in using])
            using = None  # don't keep Python wrappers of the layers QGIS has just deleted
        try:
            freed, errors = storage.delete_dataset(self.get_download_dir(), folder)
        except (OSError, ValueError) as e:
            self.notify(f"Couldn't delete {folder}: {e}", Qgis.MessageLevel.Critical)
            return
        if errors:
            self.notify(f"Deleted {format_bytes(freed)}; {len(errors)} file(s) couldn't be deleted (in use?).",
                        Qgis.MessageLevel.Warning, "\n".join(f"{p}: {e}" for p, e in errors))
        else:
            self.notify(f"Deleted {api.dataset_label(dataset)}: {format_bytes(freed)} freed.",
                        Qgis.MessageLevel.Success)
        # Not while the dialog's button signal is still running
        QTimer.singleShot(0, self.refresh_downloads)
        self.refresh_grid_style()
        self.update_selection_label()

    def open_download_dir(self):
        download_dir = self.dataset_dir() if os.path.isdir(self.dataset_dir()) else self.get_download_dir()
        if not os.path.isdir(download_dir):
            self.notify(f"The download folder doesn't exist yet (it's created on the first download): {download_dir}",
                        Qgis.MessageLevel.Warning)
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(download_dir))

    def tidy_up_zips(self):
        """Delete zips whose tiles are fully extracted, after an explicit warning."""
        download_dir = self.get_download_dir()
        redundant, kept = storage.find_redundant_zips(download_dir)
        kept_text = ""
        if kept:
            kept_text = (f"\n\nThese {len(kept)} zip file(s) will NOT be touched, because their tiles "
                         f"aren't fully extracted:\n{storage.format_file_list(kept, 5, download_dir)}")
        if not redundant:
            self.notify(f"No zip files to delete: no fully extracted tiles have a zip in {download_dir}."
                        + (f" {len(kept)} zip file(s) aren't fully extracted and were left alone." if kept else ""),
                        Qgis.MessageLevel.Info)
            return

        size_mb = sum(size for _, _, size in redundant) / 1e6
        choice = ask_choice(
            self.dock, "Permanently Delete Zip Files?",
            f"Permanently delete {len(redundant)} zip file(s) ({format_size(size_mb)}) from:\n{download_dir}",
            f"{storage.format_file_list(redundant, base_dir=download_dir)}\n\n"
            "These files will be permanently deleted, not moved to the Recycle Bin. "
            "You can download the tiles again if you need the zips.\n\n"
            f"The tiles are fully extracted: their rasters are kept and still count as downloaded.{kept_text}",
            [f"Permanently delete {len(redundant)} zip file(s)"],
            warning=True,
            details="\n".join(path for _, path, _ in redundant))
        if choice is None:
            return

        failed = []
        for _, path, _ in redundant:
            try:
                os.remove(path)
            except OSError as e:
                failed.append(f"{os.path.basename(path)}: {e}")

        self.refresh_grid_style()
        self.update_selection_label()
        done = len(redundant) - len(failed)
        if failed:
            self.notify(f"{done} zip file(s) deleted; {len(failed)} could not be deleted.",
                        Qgis.MessageLevel.Warning, "\n".join(failed))
        else:
            self.notify(f"{done} zip file(s) deleted, freeing {format_size(size_mb)}.", Qgis.MessageLevel.Success)

    def cancel_download(self):
        if self.task is not None:
            self.set_status("Cancelling...")
            self.task.cancel()

    def on_download_finished(self, task, completed):
        self.task = None
        if self.dock is None:
            return
        self.set_busy(False)
        self.save_unfinished(self.download_dataset or self.dataset,
                             [t for t in task.tiles if t not in task.results or task.results[t] is not None])

        succeeded = [t for t, err in task.results.items() if err is None]
        errors = {t: err for t, err in task.results.items() if err and err != 'cancelled'}
        details = getattr(task, 'details', {})
        failed = [f"{t}: {err}" + (f" ({details[t]})" if t in details else '') for t, err in errors.items()]
        # A service that isn't answering: said once, plainly, rather than as a failure per tile
        unanswered = {}
        for tile, err in errors.items():
            if api.NOT_RESPONDING in err:
                unanswered.setdefault(err, []).append(tile)
        for tile, err in errors.items():
            # A tile that went unanswered before its service counted as down belongs with the others
            service = err.split(f' {api.NO_ANSWER}')[0] if api.NO_ANSWER in err else None
            down = next((message for message in unanswered if service and message.startswith(service)), None)
            if down:
                unanswered[down].append(tile)
        other_failures = len(failed) - sum(len(tiles) for tiles in unanswered.values())
        not_done = [t for t in task.tiles if task.results.get(t, 'cancelled') == 'cancelled']

        self.refresh_grid_style()
        if completed:
            # The selection stays, so the tiles can be loaded (or downloaded again) straight away; loading them
            # (below, or later) deselects them
            self.dock.progressBar.setValue(100)
        self.update_selection_label()

        parts = []
        if succeeded:
            parts.append(f"Downloaded {len(succeeded)} tile(s).")
        for message, tiles in unanswered.items():
            parts.append(f"{len(tiles)} tile(s) not downloaded. {message}")
        if other_failures:
            parts.append(f"{other_failures} failed.")
        if not completed and not_done:
            parts.append(f"Cancelled: {len(not_done)} tile(s) not downloaded.")
        text = " ".join(parts) or "Nothing to do."

        if failed:
            level = Qgis.MessageLevel.Warning
        elif completed:
            level = Qgis.MessageLevel.Success
        else:
            level = Qgis.MessageLevel.Info
        self.set_status(text)
        self.notify(text, level, "\n".join(failed) if failed else None)
        choice = self.after_download_choice()
        # (with "Crop to" ticked, loading gives one layer of the area anyway: the tiles joined, then cropped)
        mosaic = choice == 'mosaic' and not self.dock.chkCrop.isChecked()
        if succeeded and mosaic and places.fills_rectangle(succeeded):
            if self.offer_metres(succeeded) and self.load_temporary_mosaic(succeeded):
                self.deselect_after_loading(self.download_selection)
        elif succeeded and choice != 'none':
            # A rectangle of tiles that came back with a gap (a tile failed, or had no data) loads tile by tile
            note = ("Loaded individually, not as a mosaic: the tiles downloaded don't fill a square or rectangle."
                    if mosaic else '')
            self.deselect_loaded(self.load_tiles(succeeded, note), self.download_selection)

    # ------------------------------------------------------------ load / mosaics

    def load_selected_tiles(self):
        tile_names = self.selected_tile_names()
        if tile_names:
            self.deselect_loaded(self.load_tiles(tile_names))

    def deselect_loaded(self, outcome, selection=None):
        """After loading tiles: if any are in the project now, deselect the grid (see deselect_after_loading)."""
        if outcome is not None and outcome.loaded:
            self.deselect_after_loading(selection)

    def deselect_after_loading(self, selection=None):
        """Whenever the plugin has loaded something (tiles, a mosaic, survey dates), the grid's tiles are deselected,
        so the selection doesn't cover what's been loaded. After a job that ran in the background (a download, a
        GeoTIFF), only if the selection is still what it was when it started (selection): the user may have moved
        on."""
        if self.grid is None or self.grid.layer() is None:
            return
        if selection is None or set(self.grid.selected_tile_names()) == set(selection):
            self.grid.clear_selection()

    def load_tiles(self, tile_names, note=''):
        """Add tiles to the project, one layer per 5 km tile, in their dataset's group (see load_files): the files
        are only read, never changed. "Create mosaic..." joins tiles; so does "Crop to": with it ticked, rasters
        and imagery load as one layer of the area, the tiles under it made into a mosaic cropped to its shape.
        Elevation layers in a group are drawn on one colour scale, so neighbouring tiles match at their edges;
        oblique photos load as points. note is added to the message. Returns what it did (a LoadOutcome), or None if
        the user cancelled."""
        if not self.offer_metres(tile_names):
            return None
        crop, crop_note = None, ''
        if self.dock.chkCrop.isChecked() and api.product_kind(self.dataset.product) in (api.RASTER, api.IMAGERY):
            crop = self.crop_cutline(quiet=True)
            if crop is None:  # never stop loading over it: load as downloaded and say why
                layer = self.dock.cboLayer.currentLayer()
                crop_note = (f"Loaded uncropped: '{layer.name()}' has no polygons to crop to." if layer is not None
                             else "Loaded uncropped: there's no layer to crop to (choose one beside Crop to).")
        # {dataset's label: (Dataset, {tile: (folder, raster files, point cloud files, photos)})}
        by_group, missing = {}, []
        surveys = self.tile_surveys()
        for tile in sorted(tile_names):
            found = False
            for dataset, folder in surveys.get(tile, []):
                files = (storage.find_raster_files(folder, tile), storage.find_point_cloud_files(folder, tile),
                         storage.find_photo_files(folder, tile))
                if any(files):
                    found = True
                    by_group.setdefault(api.dataset_label(dataset), (dataset, {}))[1][tile] = (folder,) + files
            if not found:
                missing.append(tile)
        outcome = LoadOutcome()
        for group_name, (dataset, tiles) in by_group.items():
            kind = api.product_kind(dataset.product)
            if crop is None or kind not in (api.RASTER, api.IMAGERY) \
                    or not self.load_cropped(group_name, dataset, kind, tiles, crop, outcome):
                self.load_files(group_name, dataset, kind, tiles, outcome)
            group = QgsProject.instance().layerTreeRoot().findGroup(group_name)
            if group is not None and kind == api.RASTER and api.is_elevation(dataset.product):
                self.fit_group_colours(group)
        self.report_load(outcome, crop, crop_note, missing, note)
        return outcome

    def load_files(self, group_name, dataset, kind, tiles, outcome):
        """Add each tile as one layer named after it, in the dataset's group: the 5 km tile is the unit. A tile that
        came as one file of its own is that file; one that came as several (aerial photos, Wales' 1 km grids), or as
        part of a 10 km file, is a VRT joining them into the 5 km tile, trimmed to its square (photos reach over its
        edges). A VRT only reads the files: the downloads themselves aren't changed. Point clouds: the tile's file, or
        a virtual point cloud of its files. Tiles already in the project are left as they are."""
        done = {}  # {path: in the project}: a 10 km square's point cloud serves up to four tiles, added once
        for tile, (folder, rasters, clouds, photos) in sorted(tiles.items()):
            sources = []  # [(path, layer name, provider)]
            try:
                if rasters:
                    sources.append((self.tile_raster_source(tile, folder, kind, rasters,
                                                            api.black_is_data(dataset.product)), tile, 'gdal'))
                if clouds:
                    outcome.clouds = True
                    sources += self.tile_cloud_sources(tile, folder, clouds)
            except Exception as e:  # files that couldn't be joined: say so, and carry on with the other tiles
                outcome.unjoined.append(f"{tile}: {e}")
            if photos:
                sources.append((self.photo_points_path(folder, tile), f"{tile} photos", 'photos'))
            for path, name, provider in sources:
                key = os.path.normcase(path)
                if key not in done:
                    if self.loaded_layer_for(path) is not None:  # (checked before opening: point clouds are slow)
                        outcome.already += 1
                        done[key] = True
                    else:
                        if provider == 'gdal':
                            layer = QgsRasterLayer(path, name)
                        elif provider == 'photos':
                            layer = self.photo_points_layer(folder, tile)
                        else:
                            layer = QgsPointCloudLayer(path, name, provider)
                        done[key] = self.add_layer(layer, self.layer_group(group_name), dataset, kind, outcome)
                if done[key]:
                    outcome.loaded.add(tile)

    @staticmethod
    def tile_raster_source(tile, folder, kind, files, black_is_data=False):
        """What a tile's raster layer reads: the tile's own file where it came as just one (an elevation tile), else
        a VRT joining its files into the 5 km tile, trimmed to its square, in the dataset's 'mosaics' folder."""
        own = os.path.normcase(storage.extract_path(folder, tile)) + os.sep
        if len(files) == 1 and kind != api.IMAGERY and os.path.normcase(files[0]).startswith(own):
            return files[0]
        return postprocess.tile_vrt(files, os.path.join(folder, 'mosaics'), tile,
                                    places.parse_grid_reference(tile).extent, black_is_data)

    @staticmethod
    def tile_cloud_sources(tile, folder, files):
        """[(path, layer name, provider)] for a tile's point clouds: its file, or a virtual point cloud joining its
        files (QGIS 3.32+ with PDAL; without, each file, named after it). A 10 km square's file is named after the
        square: point clouds can't be trimmed to the tile without being copied."""
        own = os.path.normcase(storage.extract_path(folder, tile)) + os.sep
        name = tile if any(os.path.normcase(f).startswith(own) for f in files) else tile[:4]
        if len(files) == 1:
            return [(files[0], name, 'pdal')]
        if not pointclouds.available():
            return [(f, os.path.splitext(os.path.basename(f))[0], 'pdal') for f in files]
        path = os.path.join(folder, 'mosaics', f'{name}_{postprocess.file_key(files)}.vpc')
        if not os.path.exists(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            pointclouds.build_vpc(files, path)
        return [(path, name, 'vpc')]

    def load_cropped(self, group_name, dataset, kind, tiles, crop, outcome):
        """With "Crop to" ticked: the tiles under the area (crop: (its name, GeoJSON)) as one layer, their files made
        into a mosaic cropped to its shape: a VRT in the dataset's 'mosaics' folder that reads the files (nothing is
        copied). Returns False if the area misses the tiles, which then load as downloaded."""
        area, cutline = crop
        under = [t for t in sorted(tiles) if postprocess.overlaps(cutline, places.parse_grid_reference(t).extent)]
        files = list(dict.fromkeys(f for tile in under for f in tiles[tile][1]))
        if not files:
            outcome.missed = True
            return False
        try:
            path = postprocess.cropped_mosaic_file(files, os.path.join(tiles[under[0]][0], 'mosaics'), cutline,
                                                   api.black_is_data(dataset.product))
        except ValueError:  # the files don't reach the area (a survey covering part of its tiles)
            outcome.missed = True
            return False
        except Exception as e:
            outcome.problems.append(f"{group_name}: {e}")
            return True
        outcome.outside += [t for t in sorted(tiles) if t not in under]
        outcome.cropped = True
        if self.loaded_layer_for(path) is not None:
            outcome.already += 1
            outcome.loaded.update(under)
            return True
        name = f"{group_name} ({plural(len(under), 'tile')}, cropped to {area})"
        if self.add_layer(QgsRasterLayer(path, name), self.layer_group(group_name), dataset, kind, outcome):
            outcome.loaded.update(under)
        return True

    def add_layer(self, layer, parent, dataset, kind, outcome):
        """Add a layer to a group, with its dataset's attribution and the plugin's display settings (only how it's
        drawn: the file isn't changed). Elevation is coloured afterwards, with its group (fit_group_colours).
        Returns True if it was added (False if QGIS couldn't open it)."""
        if not layer.isValid():
            outcome.failed.append(f"{layer.name()}: {layer.source().split('|')[0]}")
            return False
        elevation = kind == api.RASTER and api.is_elevation(dataset.product)
        if isinstance(layer, QgsPointCloudLayer):
            pointclouds.ensure_crs(layer)
            pointclouds.style_classified(layer)
        elif kind == api.IMAGERY and not api.black_is_data(dataset.product):
            self.hide_black_borders(layer)
        if elevation:
            layer.setCustomProperty(COLOURS_PROPERTY, '')
        self.describe_layer(layer, dataset, elevation=elevation)
        QgsProject.instance().addMapLayer(layer, False)
        parent.addLayer(layer)
        outcome.added += 1
        return True

    def fit_group_colours(self, group):
        """Draw a group's elevation layers on one colour scale, from the lowest to the highest height among them, so
        neighbouring tiles match at their edges (the files aren't changed). It's re-fitted as layers are added. A
        layer whose colours have been changed since the plugin set them is left as it is, and doesn't stretch the
        scale."""
        mine = [node.layer() for node in group.findLayers()
                if isinstance(node.layer(), QgsRasterLayer)
                and node.layer().customProperty(COLOURS_PROPERTY) in ('', styles.colour_signature(node.layer()))]
        ranges = [r for r in (self.layer_heights(layer) for layer in mine) if r is not None]
        if not ranges:
            return
        scale = (min(low for low, _ in ranges), max(high for _, high in ranges))
        for layer in mine:
            if layer.customProperty(COLOURS_PROPERTY) and styles.colour_range(layer) == scale:
                continue  # drawn on this scale already
            try:
                styles.elevation_colours(layer, scale)
            except Exception as e:  # a style problem shouldn't stop the layer loading
                QgsMessageLog.logMessage(f"Couldn't colour {layer.name()}: {e}", TITLE, Qgis.MessageLevel.Warning)
                continue
            layer.setCustomProperty(COLOURS_PROPERTY, styles.colour_signature(layer))
            layer.triggerRepaint()

    @staticmethod
    def layer_heights(layer):
        """(lowest, highest) height in an elevation layer, measured once and kept with it; None if it has none."""
        try:
            low, high = (float(value) for value in str(layer.customProperty(HEIGHTS_PROPERTY)).split(','))
        except ValueError:
            try:
                low, high = styles.height_range(layer)
            except Exception:  # an unreadable file: it isn't coloured with the others
                return None
            layer.setCustomProperty(HEIGHTS_PROPERTY, f'{low!r},{high!r}')
        return (low, high) if math.isfinite(low) and math.isfinite(high) and high >= low else None

    def report_load(self, outcome, crop, crop_note, missing, note=''):
        """Say what a "Load" did, in the message bar."""
        text = f"Loaded {plural(outcome.added, 'layer')}."
        area = crop[0] if crop else ''
        if outcome.cropped:
            text += f" Cropped to '{area}'."
        if outcome.outside:
            one = len(outcome.outside) == 1
            text += (f" {plural(len(outcome.outside), 'selected tile')} {'is' if one else 'are'} outside '{area}', so "
                     f"{'it is' if one else 'they are'} not in the cropped layer.")
        if outcome.missed:
            text += f" '{area}' doesn't overlap these tiles, so they're loaded as downloaded."
        if crop_note:
            text += f" {crop_note}"
        if note:
            text += f" {note}"
        if outcome.clouds and self.dock.chkCrop.isChecked():
            text += " Point clouds load whole: use 'Create mosaic...' to save a cropped copy."
        if outcome.already:
            text += f" {outcome.already} already in the project."
        if outcome.problems:
            self.notify(f"{text} Couldn't crop {plural(len(outcome.problems), 'dataset')}.",
                        Qgis.MessageLevel.Warning, "\n".join(outcome.problems))
        elif outcome.unjoined or outcome.failed:
            parts = []
            if outcome.unjoined:
                parts.append(f"{plural(len(outcome.unjoined), 'tile')} couldn't be loaded: GDAL couldn't join "
                             f"{'its' if len(outcome.unjoined) == 1 else 'their'} files.")
            if outcome.failed:
                parts.append(f"{plural(len(outcome.failed), 'layer')} couldn't be opened.")
            self.notify(f"{text} {' '.join(parts)} (Point clouds need QGIS's PDAL support, ECW imagery needs "
                        "QGIS's ECW support.)", Qgis.MessageLevel.Warning,
                        "\n".join(outcome.unjoined + outcome.failed))
        elif missing:
            self.notify(f"{text} {plural(len(missing), 'tile')} not downloaded yet.", Qgis.MessageLevel.Warning,
                        ", ".join(missing))
        else:
            warn = crop_note or outcome.missed
            self.notify(text, Qgis.MessageLevel.Warning if warn else Qgis.MessageLevel.Success)

    def crop_cutline(self, quiet=False):
        """(layer name, GeoJSON cutline) for "Crop to": the polygons of the layer chosen in section 1 (its selected
        features if it has any), in British National Grid. None if it has none (saying why, unless quiet)."""
        layer = self.dock.cboLayer.currentLayer()
        cutline = None
        if isinstance(layer, QgsVectorLayer):
            features = layer.selectedFeatures() if layer.selectedFeatureCount() else list(layer.getFeatures())
            cutline = postprocess.cutline_from_geometries(
                [f.geometry() for f in features if f.hasGeometry()], layer.crs(), None)
        if cutline is None:
            if not quiet:
                self.notify("'Crop to' is ticked, but there's no area to crop to: choose a layer with "
                            "polygons, in '1. Choose your area'.", Qgis.MessageLevel.Warning)
            return None
        return layer.name(), cutline

    @staticmethod
    def output_path(path, chosen_filter, extensions, default):
        """(path, extension added): the path the user chose, with an extension from the chosen filter if they
        didn't type one of extensions (the first one of a filter's extensions otherwise: default)."""
        lower = path.lower()
        if lower.endswith(extensions):
            return path, False
        for extension in extensions:
            if f'*{extension}' in (chosen_filter or ''):
                return path + extension, True
        return path + default, True

    def release_output(self, path, added_extension):
        """Before (re)writing a file: if it's loaded in the project, ask, then remove it from the project (Windows
        keeps a loaded file locked); if the save dialog couldn't ask about replacing it (the extension was added
        afterwards), ask. Returns False if the user cancelled."""
        target = os.path.normcase(os.path.abspath(path))
        loaded = [lyr for lyr in QgsProject.instance().mapLayers().values()
                  if lyr.source().split('|')[0]
                  and os.path.normcase(os.path.abspath(lyr.source().split('|')[0])) == target]
        if loaded:
            if ask_choice(self.dock, "Replace File?", f"'{os.path.basename(path)}' is loaded in this project.",
                          "It will be removed from the project and replaced.", ["Replace"]) is None:
                return False
            QgsProject.instance().removeMapLayers([lyr.id() for lyr in loaded])
        elif added_extension and os.path.exists(path):
            if ask_choice(self.dock, "Replace File?", f"'{os.path.basename(path)}' already exists.",
                          "Replace it?", ["Replace"]) is None:
                return False
        return True

    def create_mosaic_from_selected(self):
        """Save the selected tiles of the chosen dataset (rasters or imagery) as one file: a VRT (reads the tiles) or
        a GeoTIFF copy, cropped to the chosen layer when "Crop to" is ticked. Point clouds and oblique photos are only
        ever loaded as downloaded."""
        if self.post_task is not None:
            self.notify("Wait for the current mosaic to finish.", Qgis.MessageLevel.Info)
            return
        tile_names = self.selected_tile_names()
        if not tile_names:
            return
        kind = api.product_kind(self.dataset.product)
        if kind == api.PHOTOS:
            self.notify("Oblique photos can't be combined into a mosaic: use 'Load selected'.",
                        Qgis.MessageLevel.Warning)
            return
        if kind == api.POINT_CLOUD:
            self.notify("Point clouds aren't joined into a mosaic or cropped: use 'Load selected' to load them as "
                        "downloaded.", Qgis.MessageLevel.Warning)
            return
        crop = None
        if self.dock.chkCrop.isChecked():
            crop = self.crop_cutline()
            if crop is None:
                return
        if kind == api.RASTER and not self.offer_metres(tile_names):
            return
        self.create_raster_mosaic(tile_names, crop)

    def create_raster_mosaic(self, tile_names, crop):
        """Join raster or imagery tiles into a mosaic, cropped to crop ((name, GeoJSON cutline)) if given, and load it:
        a temporary one (see load_temporary_mosaic), or saved as a VRT (made straight away: it only points at the
        tiles) or a GeoTIFF copy (made in the background), with a file name suggested from the dataset and the tiles.
        The tiles are deselected once it's loaded."""
        files, missing = self.raster_files(tile_names)
        if not files:
            self.notify("None of the selected tiles have been downloaded yet.", Qgis.MessageLevel.Warning)
            return
        joined = [tile for tile in tile_names if tile not in missing]
        choice = ask_choice(
            self.dock, "Create Mosaic",
            f"Join {plural(len(joined), 'tile')} into one mosaic" + (f", cropped to '{crop[0]}'" if crop else '') + "?",
            "A temporary mosaic is a quick look: it's loaded straight away and goes when QGIS closes. Save as... keeps "
            "one: a VRT (a small file that reads the tiles) or a GeoTIFF copy (standalone, to share).",
            ["Temporary mosaic", "Save as..."])
        if choice is None:
            return
        if choice == 0:
            if self.load_temporary_mosaic(tile_names, crop):
                self.deselect_after_loading()
            return
        dataset, dataset_dir = self.dataset, self.dataset_dir()
        os.makedirs(dataset_dir, exist_ok=True)
        stem = api.mosaic_name(dataset, places.tiles_ref(joined), cropped=bool(crop))
        suggested = postprocess.default_output(dataset_dir, stem, '', '.vrt')
        output, chosen = QFileDialog.getSaveFileName(self.dock, "Save Mosaic", suggested,
                                                     "VRT mosaic (*.vrt);;GeoTIFF copy (*.tif)")
        if not output:
            return
        output, added = self.output_path(output, chosen, ('.vrt', '.tif', '.tiff'), '.vrt')
        is_vrt = output.lower().endswith('.vrt')
        if is_vrt:
            # A loaded VRT is simply refreshed; only ask if the dialog couldn't (the extension was added afterwards)
            if added and os.path.exists(output) and ask_choice(
                    self.dock, "Replace File?", f"'{os.path.basename(output)}' already exists.", "Replace it?",
                    ["Replace"]) is None:
                return
        elif not self.release_output(output, added):  # a loaded GeoTIFF must be let go of before it's rewritten
            return
        cutline = crop[1] if crop else None
        name = os.path.splitext(os.path.basename(output))[0]
        black = api.black_is_data(dataset.product)  # (night photos: black is part of the picture)

        if is_vrt:
            # A VRT only points at the tiles: quick, so made straight away
            try:
                if cutline:
                    source = postprocess.mosaic_file(files, os.path.join(dataset_dir, 'mosaics'), black)
                    postprocess.crop_vrt(source, output, cutline, black)
                else:
                    postprocess.build_vrt(files, output, black)
            except ValueError as e:
                self.notify(f"Nothing to save: {e}.", Qgis.MessageLevel.Warning)
                return
            except Exception as e:
                self.notify(f"Couldn't create {os.path.basename(output)}: {e}", Qgis.MessageLevel.Critical)
                return
            if self.mosaic_created(output, name, dataset, missing):
                self.deselect_after_loading()
            return

        selection = set(self.grid.selected_tile_names())

        def work(task):
            def progress(fraction):
                task.setProgress(100 * fraction)
                return not task.isCanceled()
            source = postprocess.mosaic(files, black)
            try:
                if cutline:
                    return postprocess.clip(source, output, cutline, progress=progress, black_is_data=black)
                return postprocess.save_copy(source, output, progress=progress)
            finally:
                postprocess.release(source)

        def done(exception, result=None):
            self.post_task = None
            if self.dock is None:
                return
            self.update_selection_label()  # (the Create mosaic button)
            self.set_status('')
            if isinstance(exception, ValueError):
                self.notify(f"Nothing to save: {exception}.", Qgis.MessageLevel.Warning)
                return
            if exception is not None or not result:
                self.notify(f"Creating {os.path.basename(output)} failed: {exception}", Qgis.MessageLevel.Critical)
                return
            if self.mosaic_created(result, name, dataset, missing):
                self.deselect_after_loading(selection)

        # A GeoTIFF is a copy of the data: made in the background
        self.post_task = QgsTask.fromFunction(f"LIDAR Downloader UK: {name}", work, on_finished=done)
        self.dock.btnCreateVRT.setEnabled(False)
        self.set_status(f"Creating {os.path.basename(output)}...")
        QgsApplication.taskManager().addTask(self.post_task)

    def load_temporary_mosaic(self, tile_names, crop=None):
        """Load tiles as one temporary mosaic: a VRT joining their files (cropped to crop, (name, GeoJSON), if given)
        in QGIS's temporary folder, which goes when QGIS closes ("Create mosaic... > Save as..." keeps one). Named
        after the dataset and the tiles. Returns True if it was loaded."""
        files, missing = self.raster_files(tile_names)
        if not files:
            self.notify("None of the tiles have been downloaded yet.", Qgis.MessageLevel.Warning)
            return False
        dataset = self.dataset
        ref = places.tiles_ref([tile for tile in tile_names if tile not in missing])
        path = postprocess.default_output(QgsProcessingUtils.tempFolder(),
                                          api.mosaic_name(dataset, ref, cropped=bool(crop)), '', '.vrt')
        try:
            black = api.black_is_data(dataset.product)
            if crop:
                # (the cropped VRT reads a mosaic VRT of the tiles, kept with the dataset's other mosaics)
                source = postprocess.mosaic_file(files, os.path.join(self.dataset_dir(), 'mosaics'), black)
                postprocess.crop_vrt(source, path, crop[1], black)
            else:
                postprocess.build_vrt(files, path, black)
        except ValueError as e:
            self.notify(f"Nothing to show: {e}.", Qgis.MessageLevel.Warning)
            return False
        except Exception as e:
            self.notify(f"Couldn't make the temporary mosaic: {e}", Qgis.MessageLevel.Critical)
            return False
        name = ref + (f", cropped to {crop[0]}" if crop else '')  # (QGIS marks a layer in its temporary folder)
        return self.mosaic_created(path, name, dataset, missing, temporary=True)

    def mosaic_created(self, path, name, dataset, missing, temporary=False):
        """Load a mosaic just made into its dataset's group and say so. Returns True if it's in the project."""
        added, already, errors = self.add_raster(path, name, api.dataset_label(dataset),
                                                 api.product_kind(dataset.product), dataset, position=0)
        if errors:
            self.notify(f"Created {path}, but it could not be loaded.", Qgis.MessageLevel.Warning, "\n".join(errors))
            return False
        if temporary:
            what = (f"Loaded '{name}' as a temporary mosaic: it goes when QGIS closes (\"Create mosaic...\" can "
                    "save one).")
        else:
            what = f"Created {os.path.basename(path)}" + (
                " (it was already loaded, so it was refreshed)." if already else ".")
        if missing:
            self.notify(f"{what} {len(missing)} selected tile(s) not downloaded yet were left out.",
                        Qgis.MessageLevel.Warning, ", ".join(missing))
        else:
            self.notify(what, Qgis.MessageLevel.Success)
        return True

    @staticmethod
    def hide_black_borders(layer):
        """Aerial photos fill the area outside the flight with black: show the pixels that are black in every band
        as see-through (only how the layer is drawn). A dark pixel with only some bands at 0 is still part of the
        picture, and shown."""
        renderer = layer.renderer()
        if renderer is None:
            return
        transparency = QgsRasterTransparency()
        if layer.bandCount() >= 3:
            try:  # QGIS 3.30+: (red, green, blue, opacity)
                pixel = QgsRasterTransparency.TransparentThreeValuePixel(0, 0, 0, 0)
            except TypeError:  # before: a struct to fill in
                pixel = QgsRasterTransparency.TransparentThreeValuePixel()
                pixel.red = pixel.green = pixel.blue = 0
                pixel.percentTransparent = 100
            transparency.setTransparentThreeValuePixelList([pixel])
        else:
            try:  # QGIS 3.30+: (min, max, opacity)
                pixel = QgsRasterTransparency.TransparentSingleValuePixel(0, 0, 0)
            except TypeError:
                pixel = QgsRasterTransparency.TransparentSingleValuePixel()
                pixel.min = pixel.max = 0
                pixel.percentTransparent = 100
            transparency.setTransparentSingleValuePixelList([pixel])
        renderer.setRasterTransparency(transparency)
        layer.triggerRepaint()

    @staticmethod
    def photo_points_path(download_dir, tile):
        return os.path.join(storage.extract_path(download_dir, tile), 'photo_locations.gpkg')

    def photo_points_layer(self, download_dir, tile):
        """Oblique photos as points (from their GPS tags): arrows show the camera
        direction and the map tip shows the photo."""
        folder = storage.extract_path(download_dir, tile)
        output = self.photo_points_path(download_dir, tile)
        if not os.path.exists(output):
            try:
                import processing  # here, not at the top: the Processing plugin may load after this one
                processing.run('native:importphotos', {'FOLDER': folder, 'RECURSIVE': True, 'OUTPUT': output})
            except Exception as e:  # reported as a file that couldn't be loaded
                QgsMessageLog.logMessage(f"Importing photos for {tile} failed: {e}", TITLE, Qgis.MessageLevel.Warning)
        layer = QgsVectorLayer(output, f"{tile} photos", 'ogr')
        if layer.isValid():
            symbol = QgsMarkerSymbol.createSimple({'name': 'arrow', 'color': '255,140,0', 'size': '4',
                                                   'outline_color': '60,30,0'})
            # setDataDefinedAngle works on every QGIS version (QgsSymbolLayer.Property.Angle is 3.36+)
            symbol.setDataDefinedAngle(QgsProperty.fromExpression('coalesce("direction", 0)'))
            layer.setRenderer(QgsSingleSymbolRenderer(symbol))
            layer.setMapTipTemplate('<b>[% "filename" %]</b><br>[% "timestamp" %]<br>'
                                    '<img src="file:///[% "photo" %]" width="360">')
        return layer

    def raster_files(self, tile_names):
        """(raster files, tiles not downloaded) of each tile's downloaded surveys, worst first and best last (for a
        mosaic: the best survey drawn on top, older or coarser ones filling its gaps)."""
        surveys = self.tile_surveys()
        ranked, missing = [], []
        for tile in tile_names:
            found = [(api.survey_rank(dataset), path) for dataset, folder in surveys.get(tile, [])
                     for path in storage.find_raster_files(folder, tile)]
            ranked.extend(found)
            if not found:
                missing.append(tile)
        # A 10 km file serves up to four tiles: list it once
        return list(dict.fromkeys(path for _, path in sorted(ranked))), missing

    def layer_group(self, name):
        """The layer tree group for a dataset, created at the top (just below the grid) if needed."""
        root = QgsProject.instance().layerTreeRoot()
        return root.findGroup(name) or root.insertGroup(1 if self.grid.is_on_top() else 0, name)

    @staticmethod
    def loaded_layers_for(path):
        """The project layers showing this file."""
        target = os.path.normcase(os.path.abspath(path))
        return [layer for layer in QgsProject.instance().mapLayers().values()
                if layer.source().split('|')[0]
                and os.path.normcase(os.path.abspath(layer.source().split('|')[0])) == target]

    @staticmethod
    def loaded_layers_under(folder):
        """The project layers showing files in a folder or below it (e.g. a survey's grids, and the VRTs in its
        'mosaics' folder that read them)."""
        root = os.path.normcase(os.path.abspath(folder)) + os.sep
        return [layer for layer in QgsProject.instance().mapLayers().values()
                if layer.source().split('|')[0]
                and os.path.normcase(os.path.abspath(layer.source().split('|')[0])).startswith(root)]

    @classmethod
    def loaded_layer_for(cls, path):
        """The project layer already showing this file, or None."""
        found = cls.loaded_layers_for(path)
        return found[0] if found else None

    def style_raster(self, layer, kind, dataset):
        """Imagery: the black outside the flight made transparent. Elevation (DTM / DSM): coloured by height."""
        if kind == api.IMAGERY:
            if not api.black_is_data(dataset.product):  # (night photos: black is part of the picture)
                self.hide_black_borders(layer)
        elif kind == api.RASTER and api.is_elevation(dataset.product):
            try:
                styles.elevation_colours(layer)
            except Exception as e:  # a style problem shouldn't stop the layer loading
                QgsMessageLog.logMessage(f"Couldn't colour {layer.name()}: {e}", TITLE, Qgis.MessageLevel.Warning)
        layer.triggerRepaint()

    def add_raster(self, path, name, group_name, kind, dataset, position=None):
        """Load a raster into its dataset's group, styled, with the dataset's attribution. A file already loaded
        is refreshed instead. Returns (added, already, errors)."""
        existing = self.loaded_layer_for(path)
        if existing is not None:
            existing.reload()
            existing.triggerRepaint()
            return 0, 1, []
        layer = QgsRasterLayer(path, name)
        if not layer.isValid():
            return 0, 0, [os.path.basename(path)]
        self.style_raster(layer, kind, dataset)
        self.describe_layer(layer, dataset, elevation=kind == api.RASTER and api.is_elevation(dataset.product))
        QgsProject.instance().addMapLayer(layer, False)
        group = self.layer_group(group_name)
        if position is None:
            group.addLayer(layer)
        else:
            group.insertLayer(position, layer)
        return 1, 0, []

    def rights_for(self, dataset):
        """The survey's own licence / attribution statement: from this session's searches, or saved with the
        download ('' if its source gave none)."""
        if dataset is None:
            return ''
        if dataset in self.survey_rights:
            return self.survey_rights[dataset]
        info = storage.read_survey_info(os.path.join(self.get_download_dir(), api.dataset_folder(dataset)))
        return info.get('rights', '')

    def describe_layer(self, layer, dataset, elevation=False):
        """Record where a layer's data came from (title, description, copyright / attribution, licence) in
        its metadata; optionally make it an elevation surface for the Elevation Profile tool (QGIS 3.26+)."""
        metadata = layer.metadata()
        if dataset is not None:
            metadata.setTitle(api.dataset_label(dataset))
            metadata.setAbstract(api.product_description(dataset.product))
        rights = self.rights_for(dataset)
        metadata.setRights([api.attribution(dataset, rights)])
        metadata.setLicenses([api.licence_for(rights)])
        layer.setMetadata(metadata)
        if elevation:
            properties = layer.elevationProperties() if hasattr(layer, 'elevationProperties') else None
            if properties is not None and hasattr(properties, 'setEnabled'):
                properties.setEnabled(True)
