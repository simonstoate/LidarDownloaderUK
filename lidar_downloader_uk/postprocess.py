# SPDX-FileCopyrightText: 2025-2026 Simon Stoate
# SPDX-License-Identifier: GPL-2.0-or-later
"""What the plugin does with downloaded tiles through GDAL: a VRT joining a tile's files into the 5 km tile,
mosaics (VRTs, optionally cropped to an area) and GeoTIFF copies, overviews for quick drawing, converting the
Welsh archive's millimetre grids to metres (when the user agrees), and finding the surveys in a tile's survey
file.

Uses the GDAL Python bindings directly (not Processing), so it can run in a QgsTask or inside a
Processing algorithm on any QGIS version. GeoTIFFs are Cloud-Optimised where the COG driver exists
(GDAL 3.1+), otherwise tiled and compressed.
"""

import glob
import hashlib
import json
import math
import os
import uuid

from osgeo import gdal, ogr, osr
from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsGeometry, QgsProject

# GDAL errors raise Python exceptions. QGIS's own Processing plugin turns this on for the whole session anyway
# (and GDAL 4 makes it the default); it's set here too so this module behaves the same without Processing.
gdal.UseExceptions()


def _output_options():
    if gdal.GetDriverByName('COG') is not None:
        return {'format': 'COG', 'creationOptions': ['COMPRESS=DEFLATE', 'PREDICTOR=YES', 'BIGTIFF=IF_SAFER']}
    return {'format': 'GTiff', 'creationOptions': ['COMPRESS=DEFLATE', 'TILED=YES', 'BIGTIFF=IF_SAFER']}


def remove_file(path):
    """Delete a file if it exists (an error deleting it is raised)."""
    if path and os.path.exists(path):
        os.remove(path)


def _progress(callback):
    """Adapt a callback(fraction) -> bool(keep going) to GDAL's progress signature."""
    if callback is None:
        return None

    def gdal_progress(complete, message, data):
        return 1 if callback(complete) is not False else 0
    return gdal_progress


def _join_options(files, black_is_data=False):
    """gdal.BuildVRT's no-data options for joining files: where none of them covers, no data. Photos without a
    no-data value: where they overlap, a photo's black outside its flight lets the photo beneath show through, but 0
    isn't declared the mosaic's no data, as a dark pixel with a band at 0 is still part of the picture (the layer shows
    only all-black as see-through); photos whose black is part of the picture (black_is_data: taken at night) are
    joined as they are. Elevation grids without one: -9999 (not a height of 0). Grids with one: theirs."""
    src = gdal.Open(files[0])
    band = src.GetRasterBand(1)
    nodata, byte = band.GetNoDataValue(), band.DataType == gdal.GDT_Byte
    band = src = None
    if nodata is not None:
        return {}
    if byte:
        return {} if black_is_data else {'srcNodata': 0, 'VRTNodata': 'None'}
    return {'VRTNodata': -9999}


def _options_key(options):
    """Recorded in a VRT's name: how its files were joined (nothing for files with their own no data)."""
    return ''.join(f'|{name}={value}' for name, value in sorted(options.items()))


def _build(path, files, options=None):
    """A VRT over the files in the order given: where they overlap, later files are drawn on top (so list the
    best survey last), at the finest cell size among them (never an average of mixed resolutions)."""
    options = _join_options(files) if options is None else options
    return gdal.BuildVRT(path, list(files), resolution='highest', **options)


def mosaic(files, black_is_data=False):
    """An in-memory VRT over the files, in the order given (best last) (GDAL path string). Call release() when
    done."""
    path = f'/vsimem/lidar_downloader_{uuid.uuid4().hex}.vrt'
    vrt = _build(path, files, _join_options(files, black_is_data))
    if vrt is None:
        raise RuntimeError('could not build a mosaic of the files')
    vrt = None  # flush to /vsimem
    return path


def release(path):
    """Free an in-memory mosaic made by mosaic()."""
    if path and path.startswith('/vsimem/'):
        gdal.Unlink(path)


def _nodata_options(source, black_is_data=False):
    """gdal.Warp options for a cropped copy's no data: the source's own value; for elevation grids without one,
    -9999. Photos: outside the area is see-through by an alpha band, so no colour has to mean "nothing"; so are
    all-black pixels (the area outside a flight: black in every band, not just one), unless black is part of the
    picture (black_is_data: taken at night)."""
    src = gdal.Open(source)
    band = src.GetRasterBand(1)
    nodata = band.GetNoDataValue()
    byte = band.DataType == gdal.GDT_Byte
    src = None
    if nodata is not None:
        return {'dstNodata': nodata}
    if byte:
        if black_is_data:
            return {'dstAlpha': True}
        return {'srcNodata': 0, 'dstAlpha': True, 'warpOptions': ['UNIFIED_SRC_NODATA=YES']}
    return {'dstNodata': -9999}


def fit_cutline(cutline_geojson, source):
    """The cutline's polygons trimmed to the raster's extent (so an area far bigger than the tiles, or several
    areas far apart, don't make a vast, mostly empty output). Raises ValueError if none of them overlaps it."""
    src = gdal.Open(source)
    x0, dx, _, y0, _, dy = src.GetGeoTransform()
    x1, y1 = x0 + dx * src.RasterXSize, y0 + dy * src.RasterYSize
    src = None
    ring = ogr.Geometry(ogr.wkbLinearRing)
    for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)):
        ring.AddPoint_2D(x, y)
    extent = ogr.Geometry(ogr.wkbPolygon)
    extent.AddGeometry(ring)
    data = json.loads(cutline_geojson)
    kept = []
    for feature in data.get('features', []):
        geometry = ogr.CreateGeometryFromJson(json.dumps(feature['geometry']))
        if geometry is None:
            continue
        if not geometry.IsValid():
            geometry = geometry.MakeValid() if hasattr(geometry, 'MakeValid') else geometry.Buffer(0)
        part = geometry.Intersection(extent) if geometry is not None else None
        if part is not None and not part.IsEmpty() and part.GetArea() > 0:
            kept.append({'type': 'Feature', 'properties': {}, 'geometry': json.loads(part.ExportToJson())})
    if not kept:
        raise ValueError("the area to crop to doesn't overlap the downloaded tiles")
    data['features'] = kept
    return json.dumps(data)


def overlaps(cutline_geojson, bounds):
    """True if any of the polygons (a GeoJSON FeatureCollection string) covers part of the box bounds (xmin, ymin,
    xmax, ymax), e.g. a 5 km tile; only touching its edge doesn't count."""
    xmin, ymin, xmax, ymax = bounds
    ring = ogr.Geometry(ogr.wkbLinearRing)
    for x, y in ((xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax), (xmin, ymin)):
        ring.AddPoint_2D(x, y)
    box = ogr.Geometry(ogr.wkbPolygon)
    box.AddGeometry(ring)
    for feature in json.loads(cutline_geojson).get('features', []):
        geometry = ogr.CreateGeometryFromJson(json.dumps(feature['geometry']))
        if geometry is None:
            continue
        if not geometry.IsValid():
            geometry = geometry.MakeValid() if hasattr(geometry, 'MakeValid') else geometry.Buffer(0)
        part = geometry.Intersection(box) if geometry is not None else None
        if part is not None and not part.IsEmpty() and part.GetArea() > 0:
            return True
    return False


def clip(source, output, cutline_geojson, progress=None, black_is_data=False):
    """Clip a raster to polygons given as a GeoJSON FeatureCollection string (with a 'crs' member).

    Pixels outside the polygons become no data. Returns the output path. Raises ValueError if the polygons
    don't overlap the raster.
    """
    cutline = f'/vsimem/lidar_downloader_cut_{uuid.uuid4().hex}.geojson'
    gdal.FileFromMemBuffer(cutline, fit_cutline(cutline_geojson, source).encode('utf-8'))
    try:
        result = gdal.Warp(output, source, cutlineDSName=cutline, cropToCutline=True,
                           **_nodata_options(source, black_is_data),
                           multithread=True, callback=_progress(progress), **_output_options())
        if result is None:
            raise RuntimeError('clipping failed')
        result = None
    finally:
        gdal.Unlink(cutline)
    return output


def file_key(files, extra=''):
    """A short key unique to the files in their order (which one is drawn on top matters) and extra (e.g. an
    area), for file names."""
    joined = '|'.join(os.path.normcase(f) for f in files) + extra
    return hashlib.blake2b(joined.encode('utf-8'), digest_size=5).hexdigest()  # a file name, not security


def unique_name(files, prefix, suffix, extra=''):
    """A file name unique to the files in their order and extra, e.g. mosaic_2_tiles_1a2b3c4d5e.vrt."""
    return f'{prefix}_{len(files)}_tiles_{file_key(files, extra)}{suffix}'


def on_pixel_grid(bounds, path):
    """bounds (xmin, ymin, xmax, ymax) moved out onto a raster's pixel grid (by less than a pixel), so a VRT with
    them reads its pixels as they are, not shifted by part of a pixel."""
    src = gdal.Open(path)
    x0, dx, _, y0, _, dy = src.GetGeoTransform()
    src = None
    dx, dy = abs(dx), abs(dy)

    def down(value, origin, size):
        return round(origin + math.floor((value - origin) / size + 1e-6) * size, 6)

    def up(value, origin, size):
        return round(origin + math.ceil((value - origin) / size - 1e-6) * size, 6)
    xmin, ymin, xmax, ymax = bounds
    return down(xmin, x0, dx), down(ymin, y0, dy), up(xmax, x0, dx), up(ymax, y0, dy)


def _empty_share(path):
    """The share of a photo that's black (0 in all its colour bands): outside its flight. From a small read of it."""
    src = gdal.Open(path)
    bands = [src.GetRasterBand(b).ReadAsArray(buf_xsize=64, buf_ysize=64)
             for b in range(1, min(src.RasterCount, 3) + 1)]
    src = None
    empty = bands[0] == 0
    for band in bands[1:]:
        empty &= band == 0
    return float(empty.mean())


def tile_vrt(files, folder, tile, bounds, black_is_data=False):
    """A VRT joining a tile's files into the 5 km tile, trimmed to its square (bounds: xmin, ymin, xmax, ymax; on the
    files' own pixel grid, which some surveys have a fraction of a pixel off it), in folder with a name unique to the
    tile and its files (made once). It only reads the files: nothing is copied or changed. Photos are drawn fullest on
    top, so where they overlap the edge of one that's mostly black (never quite black after compression) doesn't
    cover a neighbour. Returns its path."""
    os.makedirs(folder, exist_ok=True)
    options = _join_options(files, black_is_data)
    if options.get('srcNodata') == 0:  # photos: the emptiest underneath
        files = sorted(files, key=_empty_share, reverse=True)
    path = os.path.join(folder, f'{tile}_{file_key(files, _options_key(options))}.vrt')
    if not os.path.exists(path):
        vrt = gdal.BuildVRT(path, list(files), resolution='highest', outputBounds=on_pixel_grid(bounds, files[0]),
                            **options)
        if vrt is None:
            raise RuntimeError(f'could not join the files of {tile}')
        vrt = None
    return path


def mosaic_file(files, folder, black_is_data=False):
    """A VRT mosaic of the files in the order given (best last), saved in folder with a name unique to them.
    Returns its path."""
    os.makedirs(folder, exist_ok=True)
    options = _join_options(files, black_is_data)
    path = os.path.join(folder, unique_name(files, 'mosaic', '.vrt', _options_key(options)))
    if not os.path.exists(path):
        vrt = _build(path, files, options)
        if vrt is None:
            raise RuntimeError('could not build a mosaic of the tiles')
        vrt = None
    return path


# Composite LIDAR tiles list their surveys in the first; SurfZone tiles (LIDAR and sea-bed surveys) in the second
SURVEY_LAYERS = ('lidar_used_in_merging_process', 'survey_used_in_merging_process')


def _survey_layer(src):
    for name in SURVEY_LAYERS:
        layer = src.GetLayerByName(name)
        if layer is not None:
            return layer
    # Fall back to any layer with a survey year
    for i in range(src.GetLayerCount()):
        if src.GetLayer(i).GetLayerDefn().GetFieldIndex('SRVY_YEAR') >= 0:
            return src.GetLayer(i)
    return None


def survey_layer_name(path):
    """The layer of a tile's survey file (a GeoPackage) that lists its surveys with when they were flown, or
    None."""
    src = ogr.Open(path)
    layer = _survey_layer(src) if src is not None else None
    name = layer.GetName() if layer is not None else None
    layer = src = None
    return name


def geojson_feature_collection(geometries_json, epsg):
    """Wrap GeoJSON geometry strings (already in EPSG:epsg) as a FeatureCollection GDAL can read."""
    return json.dumps({
        'type': 'FeatureCollection',
        'crs': {'type': 'name', 'properties': {'name': f'urn:ogc:def:crs:EPSG::{epsg}'}},
        'features': [{'type': 'Feature', 'properties': {}, 'geometry': json.loads(g)} for g in geometries_json],
    })


def cutline_from_geometries(geometries, crs, raster_path, transform_context=None):
    """GeoJSON cutline (in the raster's CRS: British National Grid without one) from QGIS polygon geometries in
    crs, or None if there are no polygons. Invalid polygons (e.g. a drawn one that crosses itself) are repaired."""
    raster_crs = QgsCoordinateReferenceSystem()
    ds = gdal.Open(raster_path) if raster_path and os.path.exists(raster_path) else None
    if ds is not None and ds.GetProjection():
        raster_crs = QgsCoordinateReferenceSystem.fromWkt(ds.GetProjection())
    ds = None
    if not raster_crs.isValid() or not raster_crs.authid():
        raster_crs = QgsCoordinateReferenceSystem('EPSG:27700')  # all the data is British National Grid
    transform = QgsCoordinateTransform(crs, raster_crs, transform_context or QgsProject.instance().transformContext())
    polygons = []
    for geometry in geometries:
        geom = QgsGeometry(geometry)
        if geom.isEmpty():
            continue
        geom.transform(transform)
        if not geom.isGeosValid():  # e.g. a drawn polygon that crosses itself
            geom = geom.makeValid()
        if geom.isEmpty() or 'Polygon' not in geom.asJson():
            continue
        polygons.append(geom.asJson())
    if not polygons:
        return None
    return geojson_feature_collection(polygons, raster_crs.authid().split(':')[-1])


def default_output(folder, stem, suffix, extension='.tif'):
    """A not-yet-existing path like <folder>/<stem>_<suffix>.tif, or <stem>.tif with no suffix (adds _2, _3... if
    needed)."""
    base = os.path.join(folder, f'{stem}_{suffix}' if suffix else stem)
    path, n = f'{base}{extension}', 2
    while os.path.exists(path):
        path, n = f'{base}_{n}{extension}', n + 1
    return path


MM_UNITS_SUFFIX = '_mm_units'


def mm_grids(folder):
    """Natural Resources Wales' archive grids in a folder still in millimetres, as published (..._mm_units.asc)."""
    return sorted(glob.glob(os.path.join(glob.escape(folder), '**', f'*{MM_UNITS_SUFFIX}.asc'), recursive=True))


def convert_mm_grids(folder):
    """Natural Resources Wales' archive grids store heights in millimetres (files named ..._mm_units.asc):
    rewrite each as a compressed GeoTIFF in metres (British National Grid, no data kept) and remove the
    millimetre grid. Only when the user agrees: it changes the downloaded files. Returns the number converted (0 if
    there's nothing to do, e.g. already converted)."""
    converted = 0
    for path in mm_grids(folder):
        output = path[:-len(f'{MM_UNITS_SUFFIX}.asc')] + '.tif'
        src = gdal.Open(path)
        band = src.GetRasterBand(1)
        nodata = band.GetNoDataValue()
        nodata = -9999 if nodata is None else nodata
        values = band.ReadAsArray().astype('float32')
        empty = values == nodata
        values /= 1000.0
        values[empty] = nodata
        part = output + '.part.tif'
        out = gdal.GetDriverByName('GTiff').Create(part, src.RasterXSize, src.RasterYSize, 1, gdal.GDT_Float32,
                                                   ['COMPRESS=DEFLATE', 'PREDICTOR=3', 'TILED=YES'])
        out.SetGeoTransform(src.GetGeoTransform())
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(27700)
        out.SetProjection(srs.ExportToWkt())
        out_band = out.GetRasterBand(1)
        out_band.SetNoDataValue(nodata)
        out_band.WriteArray(values)
        out_band = out = band = src = None
        os.replace(part, output)
        for sidecar in (path, os.path.splitext(path)[0] + '.prj', path + '.aux.xml'):
            remove_file(sidecar)
        converted += 1
    return converted


def add_overviews(paths):
    """Overviews (smaller copies of a grid, in an .ovr file beside it) for elevation grids that don't have them, so
    a mosaic of many tiles draws quickly when zoomed out. Grids that have them (e.g. Scotland's cloud-optimised
    GeoTIFFs) or are small are left alone. So is everything with GDAL older than 3.4, which can only compress
    overviews through a setting shared by the whole of QGIS. Returns the number of files given overviews."""
    if not hasattr(gdal, 'SetThreadLocalConfigOption'):
        return 0
    built = 0
    for path in paths:
        if not path.lower().endswith(('.tif', '.tiff', '.asc')):
            continue
        ds = gdal.Open(path)
        if ds is None:
            continue
        size = max(ds.RasterXSize, ds.RasterYSize)
        if ds.GetRasterBand(1).GetOverviewCount() or size <= 1024:
            ds = None
            continue
        levels, factor = [], 2
        while size / factor >= 256:
            levels.append(factor)
            factor *= 2
        gdal.SetThreadLocalConfigOption('COMPRESS_OVERVIEW', 'DEFLATE')
        try:
            ds.BuildOverviews('AVERAGE', levels)
            built += 1
        finally:
            gdal.SetThreadLocalConfigOption('COMPRESS_OVERVIEW', None)
            ds = None
    return built


def build_vrt(files, output, black_is_data=False):
    """A VRT mosaic of the files (in the order given: best last) at output, replacing any. Returns the output
    path."""
    remove_file(output)
    vrt = _build(output, files, _join_options(files, black_is_data))
    if vrt is None:
        raise RuntimeError('could not build a mosaic of the tiles')
    vrt = None
    return output


def crop_vrt(source, output, cutline_geojson, black_is_data=False):
    """A VRT of a raster cropped to polygons (a GeoJSON FeatureCollection string with a 'crs' member): outside
    them is no data. Nothing is copied: the VRT reads source (which must be a file, e.g. a mosaic VRT) with
    the polygons stored in it. Returns the output path. Raises ValueError if the polygons don't overlap the
    raster."""
    cutline = f'/vsimem/lidar_downloader_cut_{uuid.uuid4().hex}.geojson'
    gdal.FileFromMemBuffer(cutline, fit_cutline(cutline_geojson, source).encode('utf-8'))
    try:
        remove_file(output)
        result = gdal.Warp(output, source, format='VRT', cutlineDSName=cutline, cropToCutline=True,
                           **_nodata_options(source, black_is_data))
        if result is None:
            raise RuntimeError('cropping failed')
        result = None
    finally:
        gdal.Unlink(cutline)
    return output


def cropped_mosaic_file(files, folder, cutline_geojson, black_is_data=False):
    """A VRT mosaic of the files cropped to polygons, saved in folder with a name unique to the files and the
    area (reused if it exists). Returns its path."""
    path = os.path.join(folder, unique_name(files, 'cropped', '.vrt',
                                            cutline_geojson + _options_key(_join_options(files, black_is_data))))
    if not os.path.exists(path):
        crop_vrt(mosaic_file(files, folder, black_is_data), path, cutline_geojson, black_is_data)
    return path


def save_copy(source, output, progress=None):
    """A standalone GeoTIFF copy of a raster (e.g. a mosaic VRT). Returns the output path."""
    remove_file(output)
    result = gdal.Translate(output, source, callback=_progress(progress), **_output_options())
    if result is None:
        raise RuntimeError('could not write the GeoTIFF')
    result = None
    return output
