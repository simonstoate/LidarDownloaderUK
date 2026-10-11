# SPDX-FileCopyrightText: 2025-2026 Simon Stoate
# SPDX-License-Identifier: GPL-2.0-or-later
r"""Build the plugin's 5 km grid (lidar_downloader_uk/data/osgb_grid_5km.gpkg).

The squares are Ordnance Survey's British National Grid (osbng-grids, OGL v3.0): every 5 km square of
the grid, sea included. Each is marked HAS_DATA = 't' if any source had LIDAR for it when the grid was
built, else 'f': the Environment Agency's survey service (one search per 100 km square), DataMapWales'
tile catalogues and the Scottish Remote Sensing Portal's collections, placed as the plugin places them.
The plugin draws squares without data faintly and leaves them out when picking with a box or going to a
place, until a search finds data in one; what's downloadable is always checked live, so re-running this
refreshes the snapshot (e.g. after a nation publishes a new survey).

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
from lidar_downloader_uk import sources  # noqa: E402  (pure Python: no QGIS needed)
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
    """Tiles with data in DataMapWales' 2020-22 and archive catalogues (all features, no area filter), placed as the
    plugin places them (the archive by its tiles' outlines)."""
    found = set()
    for layer, parse in ((sources.WALES_NATIONAL_LAYER, sources.parse_wales_national),
                         (sources.WALES_ARCHIVE_LAYER, sources.parse_wales_archive)):
        url = (f'{sources.WALES_WFS}?service=WFS&version=2.0.0&request=GetFeature&typeNames={layer}'
               f'&outputFormat=application/json&count=100000')
        data = request_json(url)
        tiles = {o.tile for o in parse(data)}
        found |= tiles
        print(f'  Wales {layer}: {len(data.get("features", []))} catalogue tiles, {len(tiles)} 5 km tiles')
    return found


def scotland_tiles():
    """Tiles with Scottish products: every collection (point clouds too: they show where a survey's files of 5 and
    10 km squares have data), searched over the whole country a page at a time, placed as the plugin places them."""
    collections = sources.parse_scotland_collections(request_json(sources.SCOTLAND_COLLECTIONS_URL))
    scotland = {'type': 'Polygon', 'coordinates': [[[-8.8, 54.5], [-0.5, 54.5], [-0.5, 61.0], [-8.8, 61.0],
                                                    [-8.8, 54.5]]]}
    items = []
    for name in sorted(collections):
        start = len(items)
        while True:
            body = sources.scotland_search_body([name], scotland, limit=sources.SCOTLAND_PAGE,
                                                offset=len(items) - start)
            page = request_json(sources.SCOTLAND_SEARCH_URL, body).get('result', [])
            items += page
            if len(page) < sources.SCOTLAND_PAGE:
                break
        print(f'  Scotland {name}: {len(items) - start} products')
    found = {o.tile for o in sources.parse_scotland_products({'result': items}, collections)}
    print(f'  Scotland: {len(found)} 5 km tiles')
    return found


def tiles_with_data():
    """Tile names (e.g. SU12NE) any source lists data for."""
    bng, wgs = osr.SpatialReference(), osr.SpatialReference()
    bng.ImportFromEPSG(27700)
    wgs.ImportFromEPSG(4326)
    for srs in (bng, wgs):
        srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    to_wgs = osr.CoordinateTransformation(bng, wgs)

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
    found |= scotland_tiles()
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
