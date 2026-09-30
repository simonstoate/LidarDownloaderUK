# SPDX-FileCopyrightText: 2025-2026 Simon Stoate
# SPDX-License-Identifier: GPL-2.0-or-later
"""LIDAR Downloader UK: find, download, organise and load LIDAR data for England, Wales and Scotland in QGIS."""


def classFactory(iface):
    """Called by QGIS to create the plugin (the name is QGIS's). iface is the running QGIS's QgsInterface."""
    from .lidar_downloader import LidarDownloader
    return LidarDownloader(iface)
