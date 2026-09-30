"""Unit tests for places.py (no QGIS needed)."""

from lidar_downloader_uk import places


def test_square_origin():
    assert places.square_origin('SU') == (400000, 100000)
    assert places.square_origin('TQ') == (500000, 100000)
    assert places.square_origin('NT') == (300000, 600000)
    assert places.square_origin('HP') == (400000, 1200000)
    assert places.square_origin('SV') == (0, 0)
    assert places.square_origin('XX') is None   # off the grid
    assert places.square_origin('SI') is None   # no I


def test_grid_references():
    p = places.parse_grid_reference('SU')
    assert p.extent == (400000, 100000, 500000, 200000)
    p = places.parse_grid_reference('su12')
    assert p.extent == (410000, 120000, 420000, 130000) and (p.easting, p.northing) == (415000, 125000)
    assert places.parse_grid_reference('SU 14 27').extent == (414000, 127000, 415000, 128000)
    p = places.parse_grid_reference('SU 14567 27890')
    assert p.extent == (414567, 127890, 414568, 127891) and p.label == 'SU1456727890'
    assert places.parse_grid_reference('SU12NE').extent == (415000, 125000, 420000, 130000)
    assert places.parse_grid_reference('SU12sw').extent == (410000, 120000, 415000, 125000)


def test_numeric_references():
    p = places.parse_grid_reference('414360, 130135')
    assert (p.easting, p.northing, p.extent) == (414360, 130135, None)
    assert places.parse_grid_reference('414360 130135').easting == 414360
    assert places.parse_grid_reference('900000, 130135') is None  # off the grid


def test_not_grid_references():
    for text in ('', 'Salisbury', 'SU123', 'SU1NE', 'SW1A 1AA', 'XX12', 'SU123456789012'):
        assert places.parse_grid_reference(text) is None, text


def test_postcodes_and_urls():
    assert places.is_postcode('SP1 2EP') and places.is_postcode('sw1a1aa') and places.is_postcode('M1 1AE')
    assert not places.is_postcode('Salisbury') and not places.is_postcode('SP1')
    assert places.lookup_url(' sp1 2ep ') == 'https://api.postcodes.io/postcodes/SP1%202EP'
    assert places.lookup_url('Bishops Cannings') == 'https://api.postcodes.io/places?q=Bishops%20Cannings&limit=10'


def test_parse_postcode():
    data = {'status': 200, 'result': {'postcode': 'SP1 3QQ', 'eastings': 414500, 'northings': 130100,
                                      'admin_district': 'Wiltshire', 'country': 'England'}}
    assert places.parse_postcode_response(data) == [places.Place('SP1 3QQ (Wiltshire, England)', 414500, 130100)]
    terminated = {'status': 404, 'error': 'Postcode not found',
                  'terminated': {'postcode': 'SP1 2EP', 'longitude': -1.80003, 'latitude': 51.063094}}
    [p] = places.parse_postcode_response(terminated)
    assert p.easting is None and (p.lon, p.lat) == (-1.80003, 51.063094) and 'no longer in use' in p.label
    assert places.parse_postcode_response({'status': 404, 'error': 'Invalid postcode'}) == []


def test_parse_places():
    data = {'status': 200, 'result': [
        {'name_1': 'Salisbury', 'local_type': 'Hamlet', 'country': 'Wales', 'county_unitary': 'Powys',
         'eastings': 300000, 'northings': 250000},
        {'name_1': 'Salisbury', 'local_type': 'City', 'country': 'England', 'county_unitary': 'Wiltshire',
         'eastings': 414360, 'northings': 130135, 'min_eastings': 411247, 'min_northings': 127969,
         'max_eastings': 417064, 'max_northings': 133461},
        {'name_1': 'No position', 'country': 'England'},
    ]}
    found = places.parse_places_response(data)
    assert [p.label for p in found] == ['Salisbury (City, Wiltshire)', 'Salisbury (Hamlet, Powys)']
    assert found[0].extent == (411247, 127969, 417064, 133461) and found[1].extent is None
    assert places.parse_places_response({'status': 200, 'result': None}) == []
    assert places.parse_lookup_response('Salisbury', data) == found


def test_bt_postcodes_use_lat_lon():
    # postcodes.io gives BT postcodes' eastings / northings in Irish Grid: they must not be read as British
    data = {'status': 200, 'result': {'postcode': 'BT1 5GS', 'eastings': 333832, 'northings': 374014,
                                      'country': 'Northern Ireland', 'admin_district': 'Belfast',
                                      'longitude': -5.930077, 'latitude': 54.596633}}
    [p] = places.parse_postcode_response(data)
    assert p.easting is None and (p.lon, p.lat) == (-5.930077, 54.596633)


def test_irish_grid_references():
    p = places.parse_irish_grid_reference('J 341 743')   # Belfast
    assert p.crs == 'EPSG:29902' and p.extent == (334100, 374300, 334200, 374400)
    assert places.parse_irish_grid_reference('h235439').extent == (223500, 343900, 223600, 344000)  # Enniskillen
    for text in ('SU 14 27', 'J', 'J3', 'I 12 34', 'Belfast'):
        assert places.parse_irish_grid_reference(text) is None, text
    assert places.parse_grid_reference('J 341 743') is None   # British references have two letters


def test_is_in_ireland():
    assert places.is_in_ireland(54.597, -5.930)       # Belfast
    assert places.is_in_ireland(54.344, -7.639)       # Enniskillen
    assert places.is_in_ireland(53.35, -6.26)         # Dublin (the island, not just NI)
    assert not places.is_in_ireland(55.30, -5.80)     # Mull of Kintyre, Scotland
    assert not places.is_in_ireland(51.50, -0.12)     # London
    assert not places.is_in_ireland(55.86, -4.25)     # Glasgow
    assert not places.is_in_ireland(51.73, -5.48)     # Grassholm, Wales
    assert places.is_in_ireland(52.17, -6.37)         # Carnsore Point


def test_ni_place_names():
    assert len(places.ni_places()) == 336
    [enniskillen] = places.match_ni_places('enniskillen')
    assert enniskillen.label.startswith('Enniskillen (Fermanagh and Omagh') and enniskillen.lon < -7
    assert places.match_ni_places("helens bay") and places.match_ni_places('Bally')
    assert places.match_ni_places('Bally', exact_only=True) == []
    assert places.match_ni_places('') == []
