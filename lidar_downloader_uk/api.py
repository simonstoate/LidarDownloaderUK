# SPDX-FileCopyrightText: 2025-2026 Simon Stoate
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Products, datasets and OSGB tile helpers, and the Environment Agency's Defra Survey Data
Download service (England). Wales and Scotland are in sources.py.

Kept free of QGIS/Qt imports so it can be unit tested with plain pytest.

Both endpoints are the ones used by https://environment.data.gov.uk/survey
(the old api.agrimetrics.co.uk host was retired in 2026). If downloads break
again, this is the file to change.

    POST {BASE_URL}/search                    body: a GeoJSON Polygon in WGS84
        -> {"count": n, "results": [{product, year, resolution, tile, uri}, ...]}
    GET  {BASE_URL}/{product}/{year}/{resolution}/{tile id}   -> zip
"""

import os
import re
from dataclasses import dataclass

BASE_URL = 'https://environment.data.gov.uk/tiles/collections/survey'
# Download links from the search services are only followed if they point to one of these
TRUSTED_DOWNLOAD_PREFIX = BASE_URL + '/'
TRUSTED_DOWNLOAD_PREFIXES = (
    TRUSTED_DOWNLOAD_PREFIX,
    'https://dmwproductionblob.blob.core.windows.net/lidar-zips/',   # Wales, 2020-22 national survey
    'https://lle.blob.core.windows.net/lidar/',                      # Wales, archive surveys
    'https://srsp-open-data.s3.eu-west-2.amazonaws.com/',            # Scotland (both spellings of the host)
    'https://srsp-open-data.s3-eu-west-2.amazonaws.com/',
)
# A 10 km square such as SU12 (Welsh archive and some Scottish files cover one)
SQUARE_10KM_RE = re.compile(r'^[A-HJ-Z]{2}\d{2}$')
# A 5 km tile such as SU12NE (also keeps names from the service safe to use as folder names)
TILE_RE = re.compile(r'^[A-HJ-Z]{2}\d{2}(NE|NW|SE|SW)$')
# Survey years and product ids as the services write them ('2022', '2020-2022', '2011-phase-1'; 'lidar_composite_dtm').
# Both become folder names, so anything else is refused.
YEAR_RE = re.compile(r'^\d{4}(-[a-z0-9-]+)?$')
PRODUCT_RE = re.compile(r'^[a-z0-9_]+$')
SEARCH_URL = f'{BASE_URL}/search'

# Default dataset: the 2022 Composite DTM at 1m (what v0.1-0.4 downloaded)
PRODUCT = 'lidar_composite_dtm'
YEAR = '2022'
RESOLUTION = '1'

# Typical zip size of a full 5km Composite DTM tile at 1m
APPROX_TILE_MB = 70

RASTER = 'raster'            # elevation grids (DTM, DSM, ...)
POINT_CLOUD = 'point_cloud'  # .laz
IMAGERY = 'imagery'          # vertical aerial orthophotos (.ecw)
PHOTOS = 'photos'            # oblique aerial photos (.jpg with GPS positions)

# Pseudo-year: the most recent survey available for each tile (with no resolution: then its finest resolution)
LATEST = 'latest'
# How menus and messages name LATEST
NEWEST = 'newest for each tile'

# Products, in display order (and the order of the Processing tool's Product list):
# id -> (label, kind, approx zip MB per 5km tile: elevation rasters at 1m, others as-is).
# Measured Sept 2026; single-survey products and imagery vary with how much of the
# tile was flown. Labels start with the nation (menus drop it inside a nation's submenu).
PRODUCTS = {
    # England (the Environment Agency's survey data service)
    'lidar_composite_dtm': ('England Composite DTM', RASTER, 70),
    'lidar_composite_first_return_dsm': ('England Composite DSM, first return', RASTER, 74),
    'lidar_composite_last_return_dsm': ('England Composite DSM, last return', RASTER, 74),
    'national_lidar_programme_dtm': ('England National LIDAR Programme DTM', RASTER, 69),
    'national_lidar_programme_dsm': ('England National LIDAR Programme DSM', RASTER, 70),
    'national_lidar_programme_first_return_dsm': ('England National LIDAR Programme DSM, first return', RASTER, 70),
    'national_lidar_programme_intensity': ('England National LIDAR Programme intensity', RASTER, 40),
    'national_lidar_programme_vom': ('England vegetation height (VOM)', RASTER, 38),
    'national_lidar_programme_point_cloud': ('England National LIDAR Programme point cloud', POINT_CLOUD, 285),
    'lidar_tiles_dtm': ('England DTM (individual surveys)', RASTER, 40),
    'lidar_tiles_dsm': ('England DSM (individual surveys)', RASTER, 40),
    'lidar_point_cloud': ('England point cloud (individual surveys)', POINT_CLOUD, 60),
    # The coast from land to sea: LiDAR merged with sea-bed surveys
    'surfzone_dem_2019': ('England SurfZone DEM (coast: land and sea bed)', RASTER, 7),
    # River beds and the sea bed surveyed by boat (multibeam sonar), where LiDAR can't see through the water
    'bathymetry_riverine_multibeam': ('England river bathymetry (river beds)', RASTER, 1),
    'bathymetry_coastal_multibeam': ('England coastal bathymetry (sea bed)', RASTER, 12),
    # Wales (DataMapWales: Welsh Government and Natural Resources Wales), see sources.py
    'wales_lidar_dtm': ('Wales DTM (national survey)', RASTER, 36),
    'wales_lidar_dsm': ('Wales DSM (national survey)', RASTER, 38),
    'wales_lidar_archive_dtm': ('Wales DTM (archive surveys)', RASTER, 11),
    'wales_lidar_archive_dsm': ('Wales DSM (archive surveys)', RASTER, 11),
    # Scotland (Scottish Remote Sensing Portal: Scottish Government and JNCC), see sources.py
    'scotland_lidar_dtm': ('Scotland DTM', RASTER, 70),
    'scotland_lidar_dsm': ('Scotland DSM', RASTER, 70),
    'scotland_lidar_point_cloud': ('Scotland point cloud', POINT_CLOUD, 200),
    # England's aerial photography: patchy coverage from individual flights
    'vertical_aerial_photography_tiles_rgb': ('England aerial photos, colour (RGB)', IMAGERY, 150),
    'vertical_aerial_photography_tiles_rgbn': ('England aerial photos, colour + near-infrared (RGBN)', IMAGERY, 150),
    'vertical_aerial_photography_tiles_irrgb': ('England aerial photos, false-colour infrared (IRRGB)', IMAGERY, 150),
    'vertical_aerial_photography_tiles_irrgbn': ('England aerial photos, colour + infrared bands (IRRGBN)', IMAGERY,
                                                 150),
    'vertical_aerial_photography_tiles_night_time': ('England aerial photos, at night', IMAGERY, 150),
    'oblique_aerial_photography_tiles_incident_response': ('England oblique photos (incident response)', PHOTOS, 25),
    'oblique_aerial_photography_tiles_other_survey': ('England oblique photos (other surveys)', PHOTOS, 80),
}
# Typical size (MB) of one file, for sources that list files without sizes: a Welsh 2020-22 1 km GeoTIFF, and a
# Welsh archive 10 km zip at 1 m (scaled by the resolution: 2 m is about a quarter)
FILE_MB = {'wales_lidar_dtm': 1.45, 'wales_lidar_dsm': 1.5, 'wales_lidar_archive_dtm': 42,
           'wales_lidar_archive_dsm': 42}
NATION_NAMES = {'ea': 'England', 'wales': 'Wales', 'scotland': 'Scotland'}
# Where each product comes from: 'ea' (England), 'wales' or 'scotland'
SOURCE_PREFIXES = (('wales_', 'wales'), ('scotland_', 'scotland'))
# Each source's service, as named in messages
SERVICE_NAMES = {'ea': "The Environment Agency's survey data service", 'wales': 'DataMapWales',
                 'scotland': 'The Scottish Remote Sensing Portal'}
# Download links on each source's storage
_URL_SOURCES = ((TRUSTED_DOWNLOAD_PREFIX, 'ea'), (SEARCH_URL, 'ea'),
                ('https://dmwproductionblob.blob.core.windows.net/', 'wales'),
                ('https://lle.blob.core.windows.net/', 'wales'),
                ('https://datamap.gov.wales/', 'wales'), ('https://srsp-open-data.', 'scotland'),
                ('https://api.remotesensing.data.gov.scot/', 'scotland'))
# Part of the messages for a service that isn't answering at all, and for a file it didn't answer for
NOT_RESPONDING = "isn't responding at the moment"
NO_ANSWER = "didn't answer"
# HTTP statuses meaning the service is overloaded or down (after retrying)
UNAVAILABLE_HTTP_STATUSES = (502, 503, 504)


def url_source(url):
    """'ea', 'wales' or 'scotland' for a link on one of the services, else ''."""
    return next((source for prefix, source in _URL_SOURCES if (url or '').startswith(prefix)), '')


def not_responding(source):
    """The message for a service that isn't answering (several files timed out, no connection, or 'busy' after
    retrying)."""
    name = SERVICE_NAMES.get(source, 'The download service')
    return f"{name} {NOT_RESPONDING}. Try again later, or check your internet connection."


def no_answer(source, detail):
    """The message for one file the service didn't answer for (while its other files may be fine)."""
    name = SERVICE_NAMES.get(source, 'The download service')
    return f"{name} {NO_ANSWER} for this tile ({detail}): try it again later"


# What each product is, in a sentence, for the dock. Heights are metres above Ordnance Datum Newlyn.
_BARE_EARTH = 'Ground heights with buildings and vegetation removed (bare earth)'
_SURFACE = 'Surface heights including buildings, trees and other objects'
PRODUCT_DESCRIPTIONS = {
    'lidar_composite_dtm': f'{_BARE_EARTH}, merged from all surveys to give the most complete coverage. '
                           'Survey dates shows when each part was flown.',
    'lidar_composite_first_return_dsm': f'{_SURFACE} (first laser return: tree canopies and roofs), merged from '
                                        'all surveys.',
    'lidar_composite_last_return_dsm': f'{_SURFACE} (last laser return: solid structures, less vegetation), '
                                       'merged from all surveys.',
    'national_lidar_programme_dtm': f'{_BARE_EARTH}, from the National LIDAR Programme: all of England flown to '
                                    'one specification since 2016.',
    'national_lidar_programme_dsm': f'{_SURFACE}, from the National LIDAR Programme.',
    'national_lidar_programme_first_return_dsm': f'{_SURFACE} (first laser return: tree canopies and roofs), from '
                                                 'the National LIDAR Programme.',
    'national_lidar_programme_intensity': 'How strongly the ground reflected the laser (not heights): shows surface '
                                          'materials, road markings and water.',
    'national_lidar_programme_vom': 'Vegetation Object Model: the height of vegetation above the ground, in metres.',
    'national_lidar_programme_point_cloud': 'The classified 3D laser points (LAZ) the National LIDAR Programme grids '
                                            'are made from.',
    'lidar_tiles_dtm': f'{_BARE_EARTH}, from individual surveys since 1998 (often flown for flood risk); '
                       'coverage varies by year.',
    'lidar_tiles_dsm': f'{_SURFACE}, from individual surveys since 1998; coverage varies by year.',
    'lidar_point_cloud': 'The 3D laser points (LAZ) from individual surveys.',
    'surfzone_dem_2019': 'A seamless coastal surface from the land out across the sea bed: LIDAR merged with '
                         'boat surveys of the nearshore (2 m, heights above Ordnance Datum). Survey dates shows which '
                         'survey each part comes from.',
    'bathymetry_coastal_multibeam': 'Sea-bed heights near the coast surveyed by boat with multibeam sonar, as 0.5 m '
                                    'grids covering just the surveyed area.',
    'vertical_aerial_photography_tiles_irrgbn': 'Orthophotos with colour and infrared bands, for vegetation and '
                                                'water mapping.',
    'oblique_aerial_photography_tiles_other_survey': 'Angled photographs from other flights (not incidents), placed '
                                                     'where the camera was.',
    'wales_lidar_dtm': f'{_BARE_EARTH}: the Welsh Government\'s 2020-22 survey of all of Wales at 1 m. Not made '
                       'specifically for flood modelling, so check and edit it before using it for that.',
    'wales_lidar_dsm': f'{_SURFACE}: the Welsh Government\'s 2020-22 survey of all of Wales at 1 m.',
    'wales_lidar_archive_dtm': f'{_BARE_EARTH}, from Natural Resources Wales\' earlier surveys (1998-2015, 25 cm to '
                               '2 m); coverage varies by year.',
    'wales_lidar_archive_dsm': f'{_SURFACE}, from Natural Resources Wales\' earlier surveys (1998-2015).',
    'scotland_lidar_dtm': f'{_BARE_EARTH}, from the Scottish public sector surveys (Phases 1-6, the national '
                          'programme and others, 25 cm to 2 m). Some older surveys have their own licence terms.',
    'scotland_lidar_dsm': f'{_SURFACE}, from the Scottish public sector surveys.',
    'scotland_lidar_point_cloud': 'The 3D laser points (LAZ) from the Scottish public sector surveys. Some '
                                  '(e.g. Phase II) are for non-commercial use only.',
    'bathymetry_riverine_multibeam': 'River-bed heights surveyed by boat with multibeam sonar, where LIDAR can\'t see '
                                     'through the water: only the river channel has data.',
    'vertical_aerial_photography_tiles_rgb': 'Colour aerial photographs looking straight down (orthophotos).',
    'vertical_aerial_photography_tiles_rgbn': 'Colour plus near-infrared orthophotos (four bands).',
    'vertical_aerial_photography_tiles_irrgb': 'False-colour infrared orthophotos: healthy vegetation shows red.',
    'vertical_aerial_photography_tiles_night_time': 'Aerial photographs taken at night (orthophotos).',
    'oblique_aerial_photography_tiles_incident_response': 'Angled photographs taken during incidents such as '
                                                          'floods, placed where the camera was.',
}
# Elevation grids (heights of the ground or surface), as opposed to intensity or vegetation height
ELEVATION_PRODUCTS = {
    'lidar_composite_dtm', 'lidar_composite_first_return_dsm', 'lidar_composite_last_return_dsm',
    'national_lidar_programme_dtm', 'national_lidar_programme_dsm', 'national_lidar_programme_first_return_dsm',
    'lidar_tiles_dtm', 'lidar_tiles_dsm', 'bathymetry_riverine_multibeam', 'surfzone_dem_2019',
    'bathymetry_coastal_multibeam', 'wales_lidar_dtm', 'wales_lidar_dsm', 'wales_lidar_archive_dtm',
    'wales_lidar_archive_dsm', 'scotland_lidar_dtm', 'scotland_lidar_dsm',
}
LICENCE = 'Open Government Licence v3.0: https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/'
# Products whose tiles come with a survey file: the surveys merged into each tile, with when they were flown
SURVEY_DATE_PRODUCTS = {'lidar_composite_dtm', 'lidar_composite_first_return_dsm', 'lidar_composite_last_return_dsm',
                        'surfzone_dem_2019'}


def has_survey_dates(product):
    return product in SURVEY_DATE_PRODUCTS


# Photos whose black is part of the picture (taken at night), not the empty area outside the flight
BLACK_IS_DATA_PRODUCTS = {'vertical_aerial_photography_tiles_night_time'}


def black_is_data(product):
    return product in BLACK_IS_DATA_PRODUCTS


def is_non_commercial(rights):
    """True if a survey's licence statement limits it to non-commercial use."""
    rights = (rights or '').lower()
    return 'non-commercial' in rights or 'non commercial' in rights


def licence_for(rights):
    """The licence to record for a survey: its own terms if they restrict use, else the OGL."""
    return rights if is_non_commercial(rights) else LICENCE


def resolve_year(product, year, datasets):
    """The survey year string meaning `year` for a product, as typed in Processing: exact ('2011-phase-1'),
    as shown in the Dataset menu ('2020-22', '2011 (Phase 1)') or a bare first year ('2011'). Returns
    (year, choices): year is None if nothing matches (no choices) or several do (choices lists them)."""
    years = sorted({d.year for d in datasets if d.product == product})
    typed = (year or '').strip().lower()
    for test in (lambda y: y == typed, lambda y: year_label(Dataset(product, y, '')).lower() == typed,
                 lambda y: typed.isdigit() and year_number(y) == int(typed)):
        found = [y for y in years if test(y)]
        if found:
            return (found[0] if len(found) == 1 else None), found
    return None, []


@dataclass(frozen=True)
class Dataset:
    """What the user chooses to download: a product, year and resolution."""
    product: str = PRODUCT
    year: str = YEAR
    resolution: str = RESOLUTION

    @property
    def is_latest(self):
        return self.year == LATEST

    @property
    def is_concrete(self):
        """A real survey (one year, one resolution), as listed by the search API and stored on disk."""
        return not self.is_latest and self.resolution != ''

    @property
    def is_all(self):
        """The whole product: every year and resolution (newest, then finest, per tile)."""
        return self.is_latest and self.resolution == ''

    def matches(self, other):
        """True if a concrete dataset (from the search API) satisfies this choice.

        Year 'latest' matches any year; an empty resolution matches any resolution.
        """
        if self.product != other.product:
            return False
        if not self.is_latest and self.year != other.year:
            return False
        return self.resolution in ('', other.resolution)


DEFAULT_DATASET = Dataset()

# Each nation's main DTM, DSM and point cloud: what the panel switches to when the chosen dataset covers none of
# the selected tiles (e.g. England's Composite DTM chosen, Welsh tiles selected)
EQUIVALENTS = {
    ('ea', 'dtm'): Dataset('lidar_composite_dtm', '2022', '1'),
    ('ea', 'dsm'): Dataset('lidar_composite_first_return_dsm', '2022', '1'),
    ('ea', 'cloud'): Dataset('national_lidar_programme_point_cloud', LATEST, ''),
    ('wales', 'dtm'): Dataset('wales_lidar_dtm', '2020-2022', '1'),
    ('wales', 'dsm'): Dataset('wales_lidar_dsm', '2020-2022', '1'),
    ('scotland', 'dtm'): Dataset('scotland_lidar_dtm', LATEST, ''),
    ('scotland', 'dsm'): Dataset('scotland_lidar_dsm', LATEST, ''),
    ('scotland', 'cloud'): Dataset('scotland_lidar_point_cloud', LATEST, ''),
}

# The Composite products cover all of England with a single 2022 edition, so
# they're always offered. Other products vary by area and survey year (e.g.
# the National LIDAR Programme is 2018-2023 depending on where you look) and
# are listed from the search API for the selected tiles.
ALWAYS_LISTED = [Dataset(product, '2022', res)
                 for product in ('lidar_composite_dtm', 'lidar_composite_first_return_dsm',
                                 'lidar_composite_last_return_dsm')
                 for res in ('1', '2')]


@dataclass(frozen=True)
class FileRef:
    """One file to download for a tile (Wales and Scotland serve files, not a zip per tile).

    square is the grid square the file covers: its tile ('SH28SW') for files within a 5 km tile, or
    a 10 km square ('ST16') for files shared by that square's four tiles, which are kept once in a
    folder named after it. A .zip is extracted there."""
    url: str
    name: str
    square: str
    size: int = 0


@dataclass(frozen=True)
class Offering:
    """One downloadable tile of one dataset, as listed by a search service."""
    dataset: Dataset
    tile: str        # 5km tile name, e.g. 'SU12NE'
    tile_id: str     # API tile id, e.g. 'SU1525'
    url: str         # the tile's zip (Environment Agency); '' where files lists what to download
    files: tuple = ()
    # Licence or attribution statement for this survey, where the source gives one
    rights: str = ''


def tile_to_grid(tile):
    """Convert a 5km tile name (e.g. 'SU12NE') to the API tile id (e.g. 'SU1525').

    The id is the 5km square's south-west corner as a 1km grid reference.
    """
    tile = tile.upper()
    prefix = tile[:2]
    digits = tile[2:4]
    quadrant = tile[4:]

    if not prefix.isalpha() or len(digits) != 2 or not digits.isdigit() or len(quadrant) != 2:
        raise ValueError(f"Invalid tile format: {tile}")

    easting = int(digits[0]) * 10
    northing = int(digits[1]) * 10

    if quadrant == 'NW':
        northing += 5
    elif quadrant == 'NE':
        easting += 5
        northing += 5
    elif quadrant == 'SE':
        easting += 5
    elif quadrant == 'SW':
        pass
    else:
        raise ValueError(f"Unknown quadrant: {quadrant}")

    return f"{prefix}{easting:02d}{northing:02d}"


def tile_download_url(tile, product=PRODUCT, year=YEAR, resolution=RESOLUTION):
    """Return the zip download URL for a 5km tile name such as 'SU12NE'."""
    return f"{BASE_URL}/{product}/{year}/{resolution}/{tile_to_grid(tile)}"


def describe_http_error(status_code, service='ea'):
    """Short, user-facing reason for a failed request. The Environment Agency's service answers 500
    for tiles it has no data for; from the other services a 500 is a server fault."""
    if status_code == 404 or (status_code == 500 and service == 'ea'):
        return f"no data available (HTTP {status_code})"
    if status_code == 500:
        return f"server error, try again later (HTTP {status_code})"
    if status_code == 403:
        return "access refused by server (HTTP 403)"
    if status_code in (502, 503, 504):
        return f"server busy, try again later (HTTP {status_code})"
    return f"HTTP {status_code}"


# ------------------------------------------------------------------ products

def is_supported_product(product):
    """True for the products the plugin knows, and for any new LIDAR or aerial photography product the
    Environment Agency adds (it also lists products the plugin doesn't handle, e.g. CASI imagery)."""
    return product in PRODUCTS or 'lidar' in product or 'aerial_photography' in product


def is_imagery(product):
    return product_kind(product) in (IMAGERY, PHOTOS)


def product_label(product):
    return PRODUCTS[product][0] if product in PRODUCTS else product.replace('_', ' ').title()


def product_description(product):
    return PRODUCT_DESCRIPTIONS.get(product, '')


def is_elevation(product):
    return product in ELEVATION_PRODUCTS


def attribution(dataset, rights=''):
    """The copyright / attribution statement for data from a dataset: the survey's own statement if its
    source gave one (rights), else the source's usual wording (Open Government Licence)."""
    if rights:
        return rights
    source = product_source(dataset.product) if dataset is not None else 'ea'
    if source == 'wales':
        return ('Contains public sector information licensed under the Open Government Licence v3.0: Welsh '
                'Government / Natural Resources Wales LIDAR.')
    if source == 'scotland':
        return ('Contains public sector information licensed under the Open Government Licence v3.0: Scottish '
                'Government LIDAR (Scottish Remote Sensing Portal).')
    year = year_number(dataset.year) if dataset is not None else 0
    return f'© Environment Agency copyright and/or database right{f" {year}" if year else ""}. All rights reserved.'


def product_kind(product):
    if product in PRODUCTS:
        return PRODUCTS[product][1]
    if 'point_cloud' in product:
        return POINT_CLOUD
    if 'oblique' in product:
        return PHOTOS
    if 'aerial_photography' in product:
        return IMAGERY
    return RASTER


def product_sort_key(product):
    order = list(PRODUCTS)
    # Unknown LiDAR products go after the known LiDAR ones, unknown imagery at the end
    fallback = len(order) + (1 if is_imagery(product) else 0)
    return (is_imagery(product), order.index(product) if product in order else fallback, product)


def resolution_label(resolution):
    """The resolution as used in folder names: '1' -> '1m', '0.5' -> '0.5m'; point clouds have none ('NaN' ->
    'N/A'). Messages and menus use resolution_text ('1 m')."""
    try:
        float(resolution)
    except ValueError:
        return 'N/A'
    if resolution.lower() == 'nan':
        return 'N/A'
    return f"{resolution}m"


def resolution_text(resolution):
    """The resolution for people: '1' -> '1 m', '0.5' -> '0.5 m'; '' for none (point clouds, photos)."""
    res = resolution_label(resolution) if resolution else 'N/A'
    return '' if res == 'N/A' else f"{res[:-1]} m"


def dataset_label(dataset, within_nation=False):
    """E.g. 'England Composite DTM, 2022, 1 m' or 'Scotland DTM, newest for each tile' (within_nation: without
    the nation, as in a nation's submenu)."""
    product = menu_label(dataset.product) if within_nation else product_label(dataset.product)
    if dataset.is_all:
        return f"{product}, {NEWEST}"
    if dataset.resolution == '':
        year = NEWEST if dataset.is_latest else dataset.year
        return f"{product}, {year}, finest resolution"
    return _join(product, year_label(dataset), resolution_text(dataset.resolution))


def mosaic_name(dataset, tiles, cropped=False):
    """A file name (no extension) for a mosaic: the dataset, then its tiles (a short reference such as
    places.tiles_ref makes), e.g. England_Composite_DTM_2022_1m_SU12NE_SU12NW; ending _cropped when cut to an area."""
    label = dataset_label(dataset).replace(NEWEST, 'newest').replace('finest resolution', 'finest')
    words = re.findall(r'[A-Za-z0-9.\-]+', re.sub(r'(\d) m\b', r'\1m', label))
    return '_'.join(words + ([tiles] if tiles else []) + (['cropped'] if cropped else []))


def menu_label(product):
    """A product's label without its nation, e.g. 'Composite DTM' (inside the England submenu)."""
    label = product_label(product)
    nation = NATION_NAMES.get(product_source(product), '') + ' '
    if label.startswith(nation) and len(label) > len(nation):
        label = label[len(nation)].upper() + label[len(nation) + 1:]
    return label


def short_dataset_label(dataset):
    """Year and resolution only, e.g. '2022, 1 m' (for legends within one product)."""
    return _join(year_label(dataset), resolution_text(dataset.resolution))


def is_safe_dataset(dataset):
    """True if a dataset read from a server makes a plain folder name (no separators or '..'): its product,
    year and resolution become folders in the download folder."""
    try:
        float(dataset.resolution)
    except (TypeError, ValueError):
        return False
    return bool(PRODUCT_RE.match(dataset.product) and YEAR_RE.match(dataset.year)
                and re.match(r'^[0-9A-Za-z.]+$', dataset.resolution) and '..' not in dataset.resolution)


def year_number(year):
    """The (first) survey year of a dataset year such as '2022', '2020-2022' or '2011-phase-1', or 0."""
    return int(year[:4]) if year and year[:4].isdigit() else 0


def survey_name(year):
    """'2011-phase-1' -> 'Phase 1', '2020-2022' -> '' (no name, just years)."""
    if not year or not YEAR_RE.match(year) or len(year) == 4:
        return ''
    rest = year[5:]
    if rest.isdigit():
        return ''
    words = {'hes': 'HES', 'lidar': 'LIDAR', 'dsm': 'DSM', 'dtm': 'DTM', 'outerheb': 'Outer Hebrides',
             '2010s10': 'Scottish Ten', '2017sp3': 'SP3'}
    return ' '.join(words.get(w, w.capitalize())
                    for w in rest.split('-'))


def product_source(product):
    """'ea', 'wales' or 'scotland'."""
    return next((source for prefix, source in SOURCE_PREFIXES if product.startswith(prefix)), 'ea')


def dataset_key(dataset):
    """'product|year|resolution', e.g. for settings and menu action data."""
    return '|'.join((dataset.product, dataset.year, dataset.resolution))


def year_label(dataset):
    """'2022', '2020-22', '2011 (Phase 1)' or 'newest for each tile'."""
    if dataset.is_latest:
        return NEWEST
    year = dataset.year
    if len(year) == 9 and year[4] == '-' and year[5:].isdigit():
        return f'{year[:4]}-{year[7:]}'  # 2020-2022 -> 2020-22
    name = survey_name(year)
    return f'{year[:4]} ({name})' if name else year


def _join(*parts):
    return ', '.join(p for p in parts if p)


def dataset_tree(datasets):
    """Datasets as a menu tree. Returns a list of (label, child) nodes in display order: child is a Dataset
    for a choice, or a list of nodes for a submenu.

    Each product is a submenu of its choices: "newest for each tile" (where it has several surveys), then each
    resolution and survey year (a resolution with several years gets its own submenu). A product with a single
    choice is that choice, e.g. "England SurfZone DEM (coast: land and sea bed), 2019, 2 m". When the datasets
    come from more than one nation (a selection across a border), each nation is a submenu, with its products
    named without it, and England's aerial photos are a submenu of their own. For example:

        England Composite DTM             -> 2022, 1 m | 2022, 2 m
        England DTM (individual surveys)  -> newest for each tile | 2010, 0.5 m | 1 m -> newest for each tile | 2012 ...
        England SurfZone DEM (coast: land and sea bed), 2019, 2 m
    """
    by_product = {}
    for dataset in sorted_datasets(datasets):
        by_product.setdefault(dataset.product, {}).setdefault(dataset.resolution, []).append(dataset)

    nodes = []  # (product, node)
    for product in sorted(by_product, key=product_sort_key):
        resolutions = by_product[product]
        whole = resolutions.pop('', [])  # "newest for each tile" over all resolutions (year 'latest', no resolution)
        children = [(NEWEST, d) for d in whole if d.is_all]
        if len(resolutions) == 1:
            # One resolution: its own "newest for each tile" would duplicate the product's
            only = next(iter(resolutions))
            if children:
                resolutions[only] = [d for d in resolutions[only] if not d.is_latest]
        for resolution in sorted(resolutions, key=_resolution_value):
            members = resolutions[resolution]
            res = resolution_text(resolution)
            if len(members) == 1 or len(resolutions) == 1:
                # Direct choices: "2022, 1 m" (or just the year when there's no resolution)
                children += [(_join(year_label(d), res), d) for d in members]
            else:
                children.append((res, [(year_label(d), d) for d in members]))
        nodes.append((product, children))

    def node_for(product, children, within_nation):
        label = menu_label(product) if within_nation else product_label(product)
        if len(children) == 1 and isinstance(children[0][1], Dataset):
            return (_join(label, children[0][0]), children[0][1])  # a single choice: no submenu
        return (label, children)

    sources = {product_source(product) for product, _ in nodes}
    if len(sources) <= 1:
        return [node_for(product, children, False) for product, children in nodes]
    tree = []
    for source in ('ea', 'wales', 'scotland'):
        members = [(product, children) for product, children in nodes
                   if product_source(product) == source and not is_imagery(product)]
        if members:
            tree.append((NATION_NAMES[source], [node_for(p, c, True) for p, c in members]))
    photos = [(product, children) for product, children in nodes if is_imagery(product)]
    if photos:
        tree.append(('England aerial photos', [node_for(p, c, True) for p, c in photos]))
    return tree


def tree_datasets(node):
    """All Datasets under a tree node."""
    _, child = node
    if isinstance(child, Dataset):
        return [child]
    return [d for sub in child for d in tree_datasets(sub)]


def estimate_tile_mb(dataset):
    """Rough zip size of one full 5km tile, for download estimates."""
    product = dataset.product
    base = PRODUCTS[product][2] if product in PRODUCTS else APPROX_TILE_MB
    if product_kind(product) != RASTER:
        # Point clouds and imagery don't scale simply with the resolution
        return base
    try:
        res = float(dataset.resolution)
    except ValueError:
        return base
    if res != res or res <= 0:  # NaN
        return base
    return base / (res * res)


def _resolution_folder(resolution):
    res = resolution_label(resolution) if resolution else ''
    return '' if res == 'N/A' else res


def dataset_folder(dataset):
    """Sub-folder of the download folder for a dataset, following the Dataset menu:
    product / resolution / year, e.g. 'lidar_composite_dtm/1m/2022' or
    'lidar_point_cloud/2012' (no resolution level for point clouds and photos).

    For "newest for each tile" choices, which have no single folder, it's the
    product (or product/resolution) folder above the survey folders.
    """
    parts = [dataset.product]
    res = _resolution_folder(dataset.resolution)
    if res:
        parts.append(res)
    if not dataset.is_latest:
        parts.append(dataset.year)
    return os.path.join(*parts)


def dataset_from_folder(product, parts):
    """The concrete Dataset stored in <product>/<parts...> (the inverse of dataset_folder), or None."""
    if len(parts) == 2 and parts[0].endswith('m') and YEAR_RE.match(parts[1]):
        resolution = parts[0][:-1]
        try:
            float(resolution)
        except ValueError:
            return None
        return Dataset(product, parts[1], resolution)
    if len(parts) == 1 and YEAR_RE.match(parts[0]):
        return Dataset(product, parts[0], 'NaN')
    return None


def download_mb(offering, counted=None):
    """Rough download size (MB) of one tile's offering: the file sizes where the service lists them (Scotland),
    else a typical size (per file for Wales, per tile for England). Files shared by several tiles (10 km files)
    count once when the same `counted` set is passed for all of them."""
    if not offering.files:
        return estimate_tile_mb(offering.dataset)
    total = 0.0
    for ref in offering.files:
        if counted is not None:
            if ref.url in counted:
                continue
            counted.add(ref.url)
        if ref.size:
            total += ref.size / 1e6
        else:
            typical = FILE_MB.get(offering.dataset.product, estimate_tile_mb(offering.dataset))
            if 'archive' in offering.dataset.product:
                typical = typical / max(_resolution_value(offering.dataset.resolution), 0.25) ** 2
            total += typical
    return total


def disk_mb(download, zipped, delete_zips=False):
    """Disk space (MB) a download needs: zips extract to about the same again (the zips go afterwards if
    they're deleted); other files are used as they are."""
    return download * (1 if delete_zips or not zipped else 2)


def parse_resolution(text):
    """A resolution typed by a person ('1', '1.0', '1 m', '50cm', '0.5m') as the service writes it ('1', '0.5'),
    or '' for none (finest available). Raises ValueError for anything else."""
    value = (text or '').strip().lower().replace(' ', '')
    if not value:
        return ''
    scale = 1.0
    if value.endswith('cm'):
        value, scale = value[:-2], 0.01
    elif value.endswith('m'):
        value = value[:-1]
    number = float(value) * scale
    if not 0 < number < 100:
        raise ValueError(text)
    return f'{number:g}'


def survey_rank(dataset):
    """Sort key: higher is better (newest survey year, then finest resolution)."""
    return year_number(dataset.year), -_resolution_value(dataset.resolution), dataset.year


def best_dataset(datasets):
    """Newest survey year, then finest resolution."""
    return max(datasets, key=survey_rank)


# ------------------------------------------------------------------ search

def is_trusted_url(url):
    """True for download links on the official services' own storage (HTTPS)."""
    return isinstance(url, str) and url.startswith(TRUSTED_DOWNLOAD_PREFIXES) and '..' not in url


def square_tiles(ref):
    """(folder square, [5 km tiles]) for a grid reference naming a 1, 5 or 10 km square:
    'SH2281' -> ('SH28SW', ['SH28SW']), 'NN70SE' -> ('NN70SE', ['NN70SE']),
    'ST16' -> ('ST16', ['ST16NE', 'ST16NW', 'ST16SE', 'ST16SW']). None if it isn't one."""
    ref = (ref or '').strip().upper().replace(' ', '')
    if TILE_RE.match(ref):
        return ref, [ref]
    if SQUARE_10KM_RE.match(ref):
        return ref, [ref + q for q in ('NE', 'NW', 'SE', 'SW')]
    if re.match(r'^[A-HJ-Z]{2}\d{4}$', ref):
        tile = f"{ref[:2]}{ref[2]}{ref[4]}{'N' if int(ref[5]) >= 5 else 'S'}{'E' if int(ref[3]) >= 5 else 'W'}"
        return tile, [tile]
    return None


def squares_text(tiles, most=4):
    """Where tiles are, as their 10 km squares: 'SU12, SU22, ST16 +2 more'."""
    squares = sorted({tile[:4] for tile in tiles})
    return ', '.join(squares[:most]) + (f' +{len(squares) - most} more' if len(squares) > most else '')


def tile_label_to_name(label):
    """API tile label 'SU12ne' -> grid tile name 'SU12NE'."""
    return label.upper()


def parse_search_response(data):
    """Turn a search API response into Offerings (LiDAR and aerial photography)."""
    offerings = []
    for result in data.get('results', []):
        try:
            product = result['product']['id']
            if not is_supported_product(product):
                continue
            dataset = Dataset(product, str(result['year']['id']), str(result['resolution']['id']))
            tile_id = result['tile']['id']
            tile = tile_label_to_name(result['tile']['label'])
            url = result.get('uri') or f"{BASE_URL}/{product}/{dataset.year}/{dataset.resolution}/{tile_id}"
        except (KeyError, TypeError, AttributeError):
            continue
        if not TILE_RE.match(tile) or not is_trusted_url(url) or not is_safe_dataset(dataset):
            continue  # not a tile name, a link away from the Environment Agency's service, or odd names
        offerings.append(Offering(dataset, tile, tile_id, url))
    return offerings


def summarise(offerings, tiles):
    """Which datasets cover which of the given tiles.

    Returns {Dataset: set of tile names}, restricted to the given tiles. Products
    surveyed in more than one year also get a "newest for each tile" entry.
    """
    wanted = set(tiles)
    coverage = {}
    for offering in offerings:
        if offering.tile in wanted:
            coverage.setdefault(offering.dataset, set()).add(offering.tile)

    years = {}
    for dataset in coverage:
        years.setdefault((dataset.product, dataset.resolution), set()).add(dataset.year)
    for (product, resolution), found in years.items():
        if len(found) > 1:
            latest = Dataset(product, LATEST, resolution)
            coverage[latest] = set().union(*(tiles_ for d, tiles_ in coverage.items() if latest.matches(d)
                                             and not d.is_latest))

    # The whole product ("newest for each tile", over every resolution) where it has more than one survey
    concrete = {}
    for dataset in coverage:
        if dataset.is_concrete:
            concrete.setdefault(dataset.product, []).append(dataset)
    for product, datasets in concrete.items():
        if len(datasets) > 1:
            coverage[Dataset(product, LATEST, '')] = set().union(*(coverage[d] for d in datasets))
    return coverage


def pick_offering(offerings, dataset):
    """The offering to download for a tile: the newest matching year, then the finest resolution."""
    matching = [o for o in offerings if dataset.matches(o.dataset)]
    if not matching:
        return None
    best = best_dataset([o.dataset for o in matching])
    return next(o for o in matching if o.dataset == best)


def _resolution_value(resolution):
    """Numeric resolution for sorting; no resolution (point clouds, photos) sorts last."""
    try:
        value = float(resolution)
    except ValueError:
        return 999.0
    return value if value == value else 999.0  # NaN


def sorted_datasets(datasets):
    """Order datasets for display: product order, newest year first, finest resolution first."""
    def res_value(dataset):
        return _resolution_value(dataset.resolution)

    def year_value(dataset):
        # "newest for each tile" first, then the newest year first
        return (-9999, '') if dataset.is_latest else (-year_number(dataset.year), dataset.year)

    return sorted(datasets, key=lambda d: (product_sort_key(d.product), year_value(d), res_value(d)))
