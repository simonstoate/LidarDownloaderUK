# SPDX-FileCopyrightText: 2025-2026 Simon Stoate
# SPDX-License-Identifier: GPL-2.0-or-later
r"""Build the plugin's 5 km grid (lidar_downloader_uk/data/osgb_grid_5km.gpkg).

The squares are Ordnance Survey's British National Grid (osbng-grids, OGL v3.0): every 5 km square of
the grid, sea included. Each is marked HAS_DATA = 't' if any source had LIDAR for it when the grid was
built, else 'f': the Environment Agency's survey service (one search per 100 km square), DataMapWales'
tile catalogues and the Scottish Remote Sensing Portal's DTM / DSM collections. The plugin uses the flag
for shading and for the "tiles in view / under layer" helpers; what's downloadable is always checked
live, so re-running this only refreshes the shading.

Run with the Python that ships with QGIS (it needs GDAL's Parquet driver), e.g. on Windows:
    "C:\Program Files\QGIS 4.2.1\bin\python-qgis.bat" tools\build_grid.py
"""

import datetime
import json
import os
import sys
import tempfile
import time
import urllib.request

from osgeo import gdal, ogr, osr

gdal.UseExceptions()
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from lidar_downloader_uk import api, sources  # noqa: E402  (pure Python: no QGIS needed)
OUTPUT = os.path.join(REPO, 'lidar_downloader_uk', 'data', 'osgb_grid_5km.gpkg')
SOURCE_URL = ('https://raw.githubusercontent.com/OrdnanceSurvey/osbng-grids/main/'
              'osbng-grids/data/geoparquet/osbng_grid_5km.parquet')
SEARCH_URL = 'https://environment.data.gov.uk/tiles/collections/survey/search'
# England and its coastal waters lie within these 100 km squares' bounds (eastings / northings in km)
SCAN_EAST, SCAN_NORTH = range(0, 700, 100), range(0, 700, 100)


def download_source(folder):
    path = os.path.join(folder, 'osbng_grid_5km.parquet')
    print('Downloading', SOURCE_URL)
    urllib.request.urlretrieve(SOURCE_URL, path)
    return path


def request_json(url, body=None):
    headers = {'User-Agent': 'LIDAR Downloader UK grid builder', 'Accept': 'application/json'}
    data = None
    if body is not None:
        headers['Content-Type'] = 'application/json'
        data = json.dumps(body).encode()
    for attempt in range(3):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers),
                                                    timeout=300))
        except Exception as e:  # retry temporary failures, then give up loudly
            if attempt == 2:
                raise
            print('  retrying after', e)
            time.sleep(10)


def wales_tiles():
    """Tiles with data in DataMapWales' 2020-22 and archive catalogues (all features, no area filter)."""
    found = set()
    for layer, field in ((sources.WALES_NATIONAL_LAYER, 'british_gr'), (sources.WALES_ARCHIVE_LAYER, 'gb_ng')):
        url = (f'{sources.WALES_WFS}?service=WFS&version=2.0.0&request=GetFeature&typeNames={layer}'
               f'&outputFormat=application/json&count=100000&propertyName={field}')
        data = request_json(url)
        for feature in data.get('features', []):
            squares = api.square_tiles((feature.get('properties') or {}).get(field))
            if squares:
                found.update(squares[1])
        print(f'  Wales {layer}: {len(data.get("features", []))} squares')
    return found


def scotland_tiles(wgs84_ring_for):
    """Tiles with Scottish DTM / DSM products (one catalogue search per 100 km square of Scotland)."""
    collections = sources.parse_scotland_collections(request_json(sources.SCOTLAND_COLLECTIONS_URL))
    wanted = {name: c for name, c in collections.items() if c.product != 'scotland_lidar_point_cloud'}
    found = set()
    for east in range(0, 700, 100):
        for north in range(500, 1300, 100):
            polygon = {'type': 'Polygon', 'coordinates': [wgs84_ring_for(east * 1000, north * 1000)]}
            data = request_json(sources.SCOTLAND_SEARCH_URL, sources.scotland_search_body(wanted, polygon, 20000))
            offerings = sources.parse_scotland_products(data, wanted)
            found.update(o.tile for o in offerings)
            if offerings:
                print(f'  Scotland {east:3},{north:4} km: {len(data.get("result", []))} products')
    return found


def tiles_with_data():
    """Tile names (e.g. SU12NE) any source lists data for."""
    bng, wgs = osr.SpatialReference(), osr.SpatialReference()
    bng.ImportFromEPSG(27700)
    wgs.ImportFromEPSG(4326)
    for srs in (bng, wgs):
        srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    to_wgs = osr.CoordinateTransformation(bng, wgs)

    def wgs84_ring(e, n):
        corners = [to_wgs.TransformPoint(x, y)[:2]
                   for x, y in ((e, n), (e + 100000, n), (e + 100000, n + 100000), (e, n + 100000))]
        lons, lats = [c[0] for c in corners], [c[1] for c in corners]
        return [[min(lons), min(lats)], [max(lons), min(lats)], [max(lons), max(lats)], [min(lons), max(lats)],
                [min(lons), min(lats)]]

    print('Environment Agency...')
    found = set()
    for east in SCAN_EAST:
        for north in SCAN_NORTH:
            e, n = east * 1000, north * 1000
            corners = [to_wgs.TransformPoint(x, y)[:2]
                       for x, y in ((e, n), (e + 100000, n), (e + 100000, n + 100000), (e, n + 100000))]
            lons, lats = [c[0] for c in corners], [c[1] for c in corners]
            ring = [[min(lons), min(lats)], [max(lons), min(lats)], [max(lons), max(lats)], [min(lons), max(lats)],
                    [min(lons), min(lats)]]
            body = json.dumps({'type': 'Polygon', 'coordinates': [ring]}).encode()
            for attempt in range(3):
                try:
                    request = urllib.request.Request(SEARCH_URL, data=body,
                                                     headers={'Content-Type': 'application/geo+json'})
                    data = json.load(urllib.request.urlopen(request, timeout=180))
                    break
                except Exception as e:  # retry temporary failures, then give up loudly
                    if attempt == 2:
                        raise
                    print('  retrying after', e)
                    time.sleep(10)
            if data.get('count') not in (None, len(data['results'])):
                raise RuntimeError(f'incomplete search result at {east},{north} km')
            tiles = {r['tile']['label'].upper() for r in data['results'] if r.get('tile', {}).get('label')}
            found |= tiles
            print(f'  {east:3},{north:3} km: {len(tiles):4} tiles with data')
    print('Wales...')
    found |= wales_tiles()
    print('Scotland...')
    found |= scotland_tiles(wgs84_ring)
    return found


def build(source, with_data, output):
    src = ogr.Open(source)
    layer = src.GetLayer(0)
    part = output + '.part.gpkg'
    if os.path.exists(part):
        os.remove(part)
    out = ogr.GetDriverByName('GPKG').CreateDataSource(part)
    out_layer = out.CreateLayer('osgb_grid_5km', srs=layer.GetSpatialRef(), geom_type=ogr.wkbPolygon,
                                options=['SPATIAL_INDEX=YES', 'DESCRIPTION=Ordnance Survey 5 km British National Grid; '
                                         'HAS_DATA from the Environment Agency, DataMapWales and Scottish Remote '
                                         f'Sensing Portal catalogues, {datetime.date.today()}'])
    out_layer.CreateField(ogr.FieldDefn('TILE_NAME', ogr.OFTString))
    out_layer.CreateField(ogr.FieldDefn('HAS_DATA', ogr.OFTString))
    out.StartTransaction()
    count = marked = 0
    for feature in layer:
        name = feature.GetField('bng_ref')
        new = ogr.Feature(out_layer.GetLayerDefn())
        new.SetField('TILE_NAME', name)
        flag = 't' if name in with_data else 'f'
        new.SetField('HAS_DATA', flag)
        new.SetGeometry(feature.GetGeometryRef().Clone())
        out_layer.CreateFeature(new)
        count += 1
        marked += flag == 't'
    out.CommitTransaction()
    out_layer = out = None
    src = None
    gdal.VectorTranslate(output + '.tmp.gpkg', part, options='-f GPKG')  # compact
    os.replace(output + '.tmp.gpkg', output)
    os.remove(part)
    return count, marked


def main():
    folder = tempfile.mkdtemp()
    source = sys.argv[1] if len(sys.argv) > 1 else download_source(folder)
    print('Asking the Environment Agency, DataMapWales and the Scottish Remote Sensing Portal which tiles have data...')
    with_data = tiles_with_data()
    count, marked = build(source, with_data, OUTPUT)
    print(f'{count} squares written to {OUTPUT}; {marked} with LIDAR data '
          f'({os.path.getsize(OUTPUT) / 1e6:.1f} MB)')


if __name__ == '__main__':
    main()
