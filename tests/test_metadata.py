"""Check metadata.txt is valid for plugins.qgis.org and allows QGIS 3.22 to 4.x."""

import configparser
import os
import re

METADATA = os.path.join(os.path.dirname(__file__), os.pardir, 'lidar_downloader_uk', 'metadata.txt')


def read_metadata():
    parser = configparser.ConfigParser()
    parser.optionxform = str
    with open(METADATA, encoding='utf-8') as f:
        parser.read_file(f)  # raises on malformed lines
    return dict(parser.items('general'))


def test_required_fields():
    meta = read_metadata()
    for key in ('name', 'qgisMinimumVersion', 'description', 'about', 'version',
                'author', 'email', 'repository', 'tracker'):
        assert meta.get(key), key


def test_no_supports_qt6_flag():
    # Deprecated: QGIS 4 ignores it and plugins.qgis.org warns about it on upload.
    # QGIS 4 support comes from qgisMaximumVersion alone (see the next test).
    assert 'supportsQt6' not in read_metadata()


def test_allowed_on_qgis_3_and_4():
    # QGIS's plugin manager treats a missing qgisMaximumVersion as
    # "<major of minimum>.99", i.e. 3.99, which blocks the plugin on QGIS 4.
    meta = read_metadata()
    maximum = meta.get('qgisMaximumVersion') or meta['qgisMinimumVersion'][0] + '.99'
    assert tuple(int(p) for p in maximum.split('.')) >= (4, 99)
    assert meta['qgisMinimumVersion'].startswith('3.')


def test_version_format():
    assert re.fullmatch(r'\d+\.\d+(\.\d+)?', read_metadata()['version'])


def test_changelog_mentions_current_version():
    meta = read_metadata()
    assert meta['version'] in meta['changelog']
