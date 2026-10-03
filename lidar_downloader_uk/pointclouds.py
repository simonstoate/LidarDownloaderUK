# SPDX-FileCopyrightText: 2025-2026 Simon Stoate
# SPDX-License-Identifier: GPL-2.0-or-later
"""Point clouds load as downloaded, never joined into a mosaic or cropped: a tile of several files loads as
one layer through a virtual point cloud (VPC), which only reads them.

Uses QGIS's PDAL tools (QGIS 3.32+). Environment Agency point clouds often have no coordinate
system recorded, so the VPC is given British National Grid.
"""

import json
import os

from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
                       QgsPointCloudClassifiedRenderer, QgsProject, QgsRectangle)

BNG = 'EPSG:27700'


def available():
    """True if QGIS's PDAL tools are installed (QGIS 3.32 or later with PDAL): the algorithms and the
    pdal_wrench program they run (some Linux packages register the algorithms without it)."""
    if QgsApplication.processingRegistry().algorithmById('pdal:virtualpointcloud') is None:
        return False
    return any(os.path.exists(os.path.join(folder, name))
               for folder in (QgsApplication.libexecPath(), QgsApplication.applicationDirPath())
               for name in ('pdal_wrench', 'pdal_wrench.exe'))


def _bng():
    return QgsCoordinateReferenceSystem(BNG)


def _run(algorithm, parameters, feedback=None):
    """Run a PDAL tool. Returns its output path; raises RuntimeError if it didn't write one (some QGIS
    builds report success when pdal_wrench refused the command)."""
    import processing  # here, not at the top: the Processing plugin may load after this one
    kwargs = {'feedback': feedback} if feedback is not None else {}
    result = processing.run(algorithm, parameters, **kwargs)
    output = result.get('OUTPUT')
    if not output or not os.path.exists(output):
        raise RuntimeError(f'{algorithm} did not produce {parameters.get("OUTPUT")}')
    return output


def fix_vpc_crs(path):
    """Give a VPC's items without a coordinate system British National Grid (and WGS84 bounds, which
    the format expects once a CRS is known). Returns True if anything changed."""
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    bng = _bng()
    # The WKT variant enum became scoped (QgsCoordinateReferenceSystem.WktVariant) in newer bindings
    variants = getattr(QgsCoordinateReferenceSystem, 'WktVariant', QgsCoordinateReferenceSystem)
    variant = variants.WKT2_2019_SIMPLIFIED
    wkt = bng.toWkt(variant)
    to_wgs84 = QgsCoordinateTransform(bng, QgsCoordinateReferenceSystem('EPSG:4326'), QgsProject.instance())
    changed = False
    for item in data.get('features', []):
        properties = item.get('properties', {})
        if properties.get('proj:wkt2') or len(properties.get('proj:bbox') or []) != 6:
            continue
        x0, y0, z0, x1, y1, z1 = properties['proj:bbox']
        box = to_wgs84.transformBoundingBox(QgsRectangle(x0, y0, x1, y1))
        properties['proj:wkt2'] = wkt
        item['bbox'] = [box.xMinimum(), box.yMinimum(), z0, box.xMaximum(), box.yMaximum(), z1]
        ring = [[box.xMinimum(), box.yMinimum(), z0], [box.xMinimum(), box.yMaximum(), z0],
                [box.xMaximum(), box.yMaximum(), z1], [box.xMaximum(), box.yMinimum(), z1],
                [box.xMinimum(), box.yMinimum(), z0]]
        item['geometry'] = {'type': 'Polygon', 'coordinates': [ring]}
        changed = True
    if changed:
        part = path + '.part'
        with open(part, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=1)
        os.replace(part, path)
    return changed


def build_vpc(files, output, feedback=None):
    """A virtual point cloud over the files at output (replacing any), in British National Grid. Returns output."""
    part = output[:-4] + '_part.vpc'
    _run('pdal:virtualpointcloud', {'LAYERS': sorted(files), 'BOUNDARY': False, 'STATISTICS': False,
                                    'OVERVIEW': False, 'CONVERT_COPC': False, 'OUTPUT': part}, feedback=feedback)
    fix_vpc_crs(part)
    os.replace(part, output)
    return output


def style_classified(layer):
    """Colour points by their classification (ground, vegetation, buildings, water...)."""
    names = [a.name() for a in layer.attributes().attributes()] if layer.attributes() else []
    if 'Classification' not in names:
        return
    renderer = QgsPointCloudClassifiedRenderer('Classification', QgsPointCloudClassifiedRenderer.defaultCategories())
    renderer.setPointSize(2)
    layer.setRenderer(renderer)
    layer.triggerRepaint()


def ensure_crs(layer):
    """Point clouds without a coordinate system are British National Grid."""
    if not layer.crs().isValid():
        layer.setCrs(_bng())
