"""Unit tests for storage.py (no QGIS needed)."""

import os
import zipfile

import pytest

from lidar_downloader_uk import api, storage


def make_zip(path, files):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with zipfile.ZipFile(path, 'w') as zf:
        for name, data in files.items():
            zf.writestr(name, data)


def touch(path, data=b''):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(data)


def test_scan_empty_or_missing(tmp_path):
    assert storage.scan_downloaded_tiles(str(tmp_path)) == []
    assert storage.scan_downloaded_tiles(str(tmp_path / 'missing')) == []


def test_scan_finds_zips_and_tile_folders(tmp_path):
    d = tmp_path
    touch(str(d / 'SU12NE.zip'))
    touch(str(d / 'su12nw.zip'))                       # case-insensitive
    touch(str(d / 'combined.zip'))                     # not a tile name
    touch(str(d / 'SU12NE.zip.part'))                  # incomplete download
    touch(str(d / 'TQ09SW' / 'TQ09sw_DTM_1m.tif'))     # zip deleted after extraction
    (d / 'TQ09SE').mkdir()                             # empty: failed extraction
    (d / 'notes').mkdir()
    assert storage.scan_downloaded_tiles(str(d)) == ['SU12NE', 'SU12NW', 'TQ09SW']


def test_is_downloaded(tmp_path):
    d = str(tmp_path)
    assert not storage.is_downloaded(d, 'SU12NE')
    touch(os.path.join(d, 'SU12NE.zip'))
    assert storage.is_downloaded(d, 'SU12NE')                      # zip only
    os.makedirs(os.path.join(d, 'TQ09SW'))
    assert not storage.is_downloaded(d, 'TQ09SW')                  # empty folder
    touch(os.path.join(d, 'TQ09SW', 'TQ09sw_DTM_1m.tif'))
    assert storage.is_downloaded(d, 'TQ09SW')                      # rasters only


def test_extract_and_find_rasters(tmp_path):
    d = str(tmp_path)
    make_zip(storage.zip_path(d, 'SU12NE'), {
        'SU12ne_DTM_1m.tif': b'tif', 'SU12ne_DTM_1m.tif.xml': b'<xml/>', 'sub/other.asc': b'asc'})
    storage.extract_tile(d, 'SU12NE')
    assert storage.extract_path(d, 'SU12NE') == os.path.join(d, 'SU12NE')
    found = [os.path.relpath(f, storage.extract_path(d, 'SU12NE')) for f in storage.find_raster_files(d, 'SU12NE')]
    assert sorted(found) == sorted(['SU12ne_DTM_1m.tif', os.path.join('sub', 'other.asc')])
    assert storage.find_raster_files(d, 'TQ09SW') == []


def test_grids_without_prj_get_british_national_grid(tmp_path):
    d = str(tmp_path)
    make_zip(storage.zip_path(d, 'TQ16NW'), {
        'tq1468_20100907mb.asc': b'asc', 'tq1068_20100907mb.asc': b'asc', 'tq1068_20100907mb.prj': b'already'})
    storage.extract_tile(d, 'TQ16NW')
    folder = storage.extract_path(d, 'TQ16NW')
    with open(os.path.join(folder, 'tq1468_20100907mb.prj')) as f:
        assert 'British_National_Grid' in f.read()
    with open(os.path.join(folder, 'tq1068_20100907mb.prj')) as f:
        assert f.read() == 'already'
    assert storage.zip_fully_extracted(d, 'TQ16NW')


def test_corrupt_zip_is_deleted(tmp_path):
    d = str(tmp_path)
    path = storage.zip_path(d, 'SU12NE')
    touch(path, b'<html>not a zip</html>')
    with pytest.raises(zipfile.BadZipFile):
        storage.extract_tile(d, 'SU12NE')
    assert not os.path.exists(path)


def test_write_file_atomically(tmp_path):
    path = str(tmp_path / 'a.zip')
    storage.write_file_atomically(path, b'data')
    assert open(path, 'rb').read() == b'data'
    assert not os.path.exists(path + '.part')


def test_dataset_dirs_and_local_datasets(tmp_path):
    root = str(tmp_path)
    dtm = os.path.join(root, api.dataset_folder(api.DEFAULT_DATASET))
    dtm2 = os.path.join(root, api.dataset_folder(api.Dataset('lidar_composite_dtm', '2022', '2')))
    cloud = os.path.join(root, api.dataset_folder(api.Dataset('lidar_point_cloud', '2012', 'NaN')))
    touch(os.path.join(dtm, 'SU12NE', 'SU12ne_DTM_1m.tif'))
    touch(os.path.join(dtm2, 'SU12NE.zip'))
    touch(os.path.join(cloud, 'SU12NE', 'a.laz'))
    touch(os.path.join(root, 'notes', 'readme.txt'))
    assert storage.dataset_dirs(root) == sorted([dtm, dtm2, cloud])
    assert sorted(storage.local_datasets(root, 'lidar_composite_dtm'), key=str) == sorted([
        (api.DEFAULT_DATASET, dtm), (api.Dataset('lidar_composite_dtm', '2022', '2'), dtm2)], key=str)
    assert storage.local_datasets(root, 'lidar_point_cloud') == [
        (api.Dataset('lidar_point_cloud', '2012', 'NaN'), cloud)]
    assert storage.find_point_cloud_files(cloud, 'SU12NE') and not storage.find_raster_files(cloud, 'SU12NE')


def test_find_redundant_zips(tmp_path):
    root = str(tmp_path)
    d = os.path.join(root, api.dataset_folder(api.DEFAULT_DATASET))
    files = {'SU12ne_DTM_1m.tif': b'tif-data', 'meta/SU12ne.xml': b'<xml/>'}

    make_zip(storage.zip_path(d, 'SU12NE'), files)       # fully extracted -> redundant
    storage.extract_tile(d, 'SU12NE')
    make_zip(storage.zip_path(d, 'SU12NW'), files)       # a file removed -> kept
    storage.extract_tile(d, 'SU12NW')
    os.remove(os.path.join(storage.extract_path(d, 'SU12NW'), 'meta', 'SU12ne.xml'))
    make_zip(storage.zip_path(d, 'SU12SE'), files)       # truncated -> kept
    storage.extract_tile(d, 'SU12SE')
    with open(os.path.join(storage.extract_path(d, 'SU12SE'), 'SU12ne_DTM_1m.tif'), 'wb') as f:
        f.write(b'tif')
    make_zip(storage.zip_path(d, 'SU12SW'), files)       # never extracted -> kept
    touch(os.path.join(d, 'SU22NW.zip'), b'not a zip')   # corrupt -> kept
    make_zip(os.path.join(d, 'combined.zip'), files)     # not a tile -> ignored

    other = os.path.join(root, api.dataset_folder(api.Dataset('national_lidar_programme_dtm', '2019', '1')))
    make_zip(storage.zip_path(other, 'SU12NE'), files)   # another dataset folder -> redundant
    storage.extract_tile(other, 'SU12NE')

    redundant, kept = storage.find_redundant_zips(root)
    assert sorted(p for _, p, _ in redundant) == sorted([storage.zip_path(d, 'SU12NE'),
                                                         storage.zip_path(other, 'SU12NE')])
    assert sorted(t for t, _, _ in kept) == ['SU12NW', 'SU12SE', 'SU12SW', 'SU22NW']
    listing = storage.format_file_list(redundant, base_dir=root)
    assert os.path.join('national_lidar_programme_dtm', '1m', '2019', 'SU12NE.zip') in listing
    assert storage.find_redundant_zips(str(tmp_path / 'missing')) == ([], [])


def test_imagery_and_photo_files(tmp_path):
    d = str(tmp_path)
    touch(os.path.join(d, 'SU12NE', 'Ortho_RGBN_P1_20cm_res.ecw'))
    touch(os.path.join(d, 'SU12NE', 'Obliques_S23_016.jpg'))
    assert [os.path.basename(f) for f in storage.find_raster_files(d, 'SU12NE')] == ['Ortho_RGBN_P1_20cm_res.ecw']
    assert [os.path.basename(f) for f in storage.find_photo_files(d, 'SU12NE')] == ['Obliques_S23_016.jpg']
    assert len(storage.find_data_files(d, 'SU12NE')) == 2


def test_metadata_files(tmp_path):
    d = str(tmp_path)
    touch(os.path.join(d, 'SU12NE', 'SU12ne_DTM_1m_Metadata.gpkg'))
    touch(os.path.join(d, 'SU12NE', 'SU12ne_DTM_1m.tif'))
    assert [os.path.basename(f) for f in storage.find_metadata_files(d, 'SU12NE')] == ['SU12ne_DTM_1m_Metadata.gpkg']
    assert storage.find_metadata_files(d, 'SU12NW') == []
    # Metadata alone doesn't count as data (only the raster does)
    assert [os.path.basename(f) for f in storage.find_data_files(d, 'SU12NE')] == ['SU12ne_DTM_1m.tif']


def test_download_summary_and_delete(tmp_path):
    root = str(tmp_path)
    dtm = os.path.join(root, 'lidar_composite_dtm', '1m', '2022')
    old = os.path.join(root, 'lidar_tiles_dtm', '1m', '2010')
    touch(os.path.join(dtm, 'SU12NE', 'a.tif'), b'x' * 1000)
    touch(os.path.join(dtm, 'SU12NW.zip'), b'x' * 500)
    touch(os.path.join(dtm, 'mosaics', 'mosaic_2_tiles_abc.vrt'), b'x' * 10)
    touch(os.path.join(old, 'SU12NE', 'b.asc'), b'x' * 200)
    touch(os.path.join(root, 'notes.txt'), b'keep')
    rows = storage.download_summary(root)
    assert [(api.dataset_label(d), sorted(tiles), size) for d, _, tiles, size in rows] == [
        ('England Composite DTM, 2022, 1 m', ['SU12NE', 'SU12NW'], 1510),
        ('England DTM (individual surveys), 2010, 1 m', ['SU12NE'], 200)]
    assert storage.free_space(os.path.join(root, 'not', 'made', 'yet')) > 0

    # Only survey folders inside the download folder can be deleted
    for bad in (root, os.path.join(root, 'lidar_composite_dtm'), os.path.join(dtm, 'SU12NE'), str(tmp_path.parent)):
        with pytest.raises(ValueError):
            storage.delete_dataset(root, bad)
    freed, errors = storage.delete_dataset(root, old)
    assert freed == 200 and errors == []
    assert not os.path.exists(os.path.join(root, 'lidar_tiles_dtm'))  # empty parents tidied away
    assert os.path.exists(dtm) and os.path.exists(os.path.join(root, 'notes.txt'))
    assert [d.product for d, _, _, _ in storage.download_summary(root)] == ['lidar_composite_dtm']


def test_files_shared_by_a_10km_square(tmp_path):
    d = str(tmp_path)
    touch(os.path.join(d, 'NS79', 'NS79_1M_DTM_PHASE1.tif'), b'x')      # a 10 km file (Scotland Phase I)
    touch(os.path.join(d, 'NS79SE', 'NS7993_50CM_DTM_HES2010.tif'), b'x')
    assert storage.scan_downloaded_tiles(d) == ['NS79NE', 'NS79NW', 'NS79SE', 'NS79SW']
    assert [os.path.basename(f) for f in storage.find_raster_files(d, 'NS79NW')] == ['NS79_1M_DTM_PHASE1.tif']
    assert sorted(os.path.basename(f) for f in storage.find_raster_files(d, 'NS79SE')) == [
        'NS7993_50CM_DTM_HES2010.tif', 'NS79_1M_DTM_PHASE1.tif']
    assert storage.is_downloaded(d, 'NS79NE') and not storage.is_downloaded(d, 'NS89SW')
    # The dataset folder is recognised, and its square folders aren't mistaken for datasets
    assert storage.dataset_dirs(d) == [d]


def test_per_file_downloads(tmp_path):
    d = str(tmp_path)
    tif = api.FileRef('https://srsp-open-data.s3.eu-west-2.amazonaws.com/x/NS79_1M_DTM.tif', 'NS79_1M_DTM.tif',
                      'NS79', size=3)
    assert storage.file_folder(d, tif) == os.path.join(d, 'NS79') and not storage.file_done(d, tif)
    touch(os.path.join(d, 'NS79', 'NS79_1M_DTM.tif'), b'xx')
    assert not storage.file_done(d, tif)                       # wrong size: a broken download
    touch(os.path.join(d, 'NS79', 'NS79_1M_DTM.tif'), b'xyz')
    assert storage.file_done(d, tif)
    zipped = api.FileRef('https://lle.blob.core.windows.net/lidar/2m_res_ST16_1998_dtm.zip',
                         '2m_res_ST16_1998_dtm.zip', 'ST16')
    make_zip(os.path.join(d, 'ST16', zipped.name), {'st1666_dtm.asc': b'asc'})
    assert not storage.file_done(d, zipped)
    storage.extract_file_zip(os.path.join(d, 'ST16', zipped.name))
    assert storage.file_done(d, zipped)
    assert os.path.exists(os.path.join(d, 'ST16', 'st1666_dtm.prj'))  # British National Grid added
    assert [os.path.basename(f) for f in storage.find_raster_files(d, 'ST16SW')] == ['st1666_dtm.asc']


def test_tile_markers_for_per_file_downloads(tmp_path):
    d = str(tmp_path)
    # A 10 km zip listed for ST16NE only: the square's other tiles don't count as downloaded
    zipped = api.FileRef('https://lle.blob.core.windows.net/lidar/2m_res_ST16_1998_dtm.zip',
                         '2m_res_ST16_1998_dtm.zip', 'ST16')
    storage.start_tile_files(d, 'ST16NE', [zipped])
    make_zip(os.path.join(d, 'ST16', zipped.name), {'st1666_dtm.asc': b'asc'})
    storage.extract_file_zip(os.path.join(d, 'ST16', zipped.name))
    assert storage.scan_downloaded_tiles(d) == []              # still coming
    storage.finish_tile_files(d, 'ST16NE', [zipped])
    assert storage.scan_downloaded_tiles(d) == ['ST16NE']
    assert storage.find_raster_files(d, 'ST16SW') == [] and storage.find_raster_files(d, 'ST16NE')
    # 1 km files in a tile's folder: a tile whose download stopped part-way isn't downloaded
    one = api.FileRef('https://dmwproductionblob.blob.core.windows.net/lidar-zips/SH2080_DTM_1m.tif',
                      'SH2080_DTM_1m.tif', 'SH28SW')
    storage.start_tile_files(d, 'SH28SW', [one])
    touch(os.path.join(d, 'SH28SW', one.name), b'x')
    assert not storage.is_downloaded(d, 'SH28SW') and 'SH28SW' not in storage.scan_downloaded_tiles(d)
    storage.finish_tile_files(d, 'SH28SW', [one])
    assert storage.is_downloaded(d, 'SH28SW') and 'SH28SW' in storage.scan_downloaded_tiles(d)


def test_zip_too_big_for_the_drive(tmp_path, monkeypatch):
    path = os.path.join(str(tmp_path), 'ST16', 'a.zip')
    make_zip(path, {'st1666_dtm.asc': b'x' * 1000})
    monkeypatch.setattr(storage, 'free_space', lambda _path: 10)
    with pytest.raises(OSError, match='not enough disk space'):
        storage.extract_file_zip(path)
    assert not os.path.exists(os.path.join(str(tmp_path), 'ST16', 'st1666_dtm.asc'))
