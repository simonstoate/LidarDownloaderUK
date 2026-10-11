# Changelog

All notable changes to LIDAR Downloader UK, newest first. 0.14.3 was the first public release (the changelog in `metadata.txt` starts there); earlier versions are development history.

## 0.15.0

- Go to replaces the selection with the place's tiles.
- The grid fades when zoomed out: no black block at national scale.
- All of the Welsh archive: the 25 cm surveys (2006-2011) and surveys on tiles crossing into the next 5 km tile.
- Welsh archive download sizes estimated from real files.
- Survey dates for Welsh DTMs and DSMs.
- New users: a one-time tip, and zips deleted once extracted.
- Point cloud tiles of several files show their points: converted to COPC copies (QGIS 3.44+), several at once, with an overview when zoomed out.
- Scotland: "newest for each tile" picks a survey that covers the whole tile; the menu says when one covers only part.
- Scotland: Phase 1 and 2's 10 km files offered only for the tiles they reach.
- Clearer names for Scotland's new surveys.
- The grid shows newly published surveys.
- Real plurals in messages, and a Feedback link.

## 0.14.6

- Each 5 km tile loads as one layer, read straight from the downloaded files.
- Only Create mosaic and Crop to join tiles; point clouds load as they are.
- A simpler, shorter panel: select and deselect icons, a data menu that waits for a tile, a Download button that says what it will fetch, sections that fold up.
- After downloading: load each tile, load a temporary mosaic, or just download.
- Create mosaic: a temporary mosaic, or a saved file named after the data and tiles.
- Crop to: downloads only the tiles a polygon needs, loaded as one cropped layer.
- Fixes: the data menu follows the nation, Northern Ireland links, night photos, Welsh archive converted only if you agree, downloads no longer stuck busy.

## 0.14.5

- Removed the deprecated `supportsQt6` metadata flag (plugins.qgis.org warns about it).
- Qt6 previews of QGIS 3.40 / 3.44 now treat the plugin as incompatible; QGIS 3 and 4 are unaffected.

## 0.14.4

- Passes plugins.qgis.org's Qt6 compatibility check. No change in use.

## 0.14.3

- First public release.
- Coverage shading works with up to 1,600 tiles in view (was 400).
- A note under its tick box says when to zoom in, while it's checking, if it failed, and how many tiles it shaded.

## 0.14.2

- Prepared for release but not published; same as 0.14.1.

## 0.14.1

- Tidied for publishing: licence headers, clearer comments, unused code removed.
- Fix: a postcode search server error no longer reads "no data available".
- Processing tool messages can be translated.

## 0.14.0

- Panel laid out as steps: choose area, choose data, download, use.
- The grid shows when the panel opens; the first download folder is made without asking.
- Datasets named by nation and product; "newest for each tile" replaces "latest available" and "all years and resolutions".
- Switches to the selected tiles' own nation's dataset, and says so.
- Size estimates from real file sizes where the service gives them.
- Unfinished downloads can be resumed.
- Downloaded grids get overviews.
- My downloads shows where each survey is.
- Processing: the product list follows the menu (products from 12 renumbered); unnamed VRTs named after their tiles.

## 0.13.0

- Welsh archive heights converted from millimetres to metres (they loaded 1000x too high).
- Mosaics of mixed surveys show the newest on top, at the finest cell size.
- Cropping: trimmed to the tiles, self-crossing polygons repaired, areas away from the tiles load uncropped.
- A loaded file is released (after asking) before it's replaced.
- A service counts as down only after two tiles get no answer.
- Smaller fixes: cancellable cropped point clouds, Processing threads, the grid takes over only its own layer.
- Clean security scan (Bandit, detect-secrets).

## 0.12.2

- A service that isn't answering is named once, plainly, and its other tiles skipped.

## 0.12.1

- Unticking the grid removes it in a project saved with it (no second grid).

## 0.12.0

- The plugin finds, downloads, organises and loads data; styling, exports, differences and 3D move to a companion plugin.
- New "Crop to this layer" for loaded tiles and saved mosaics; download and crop in one go.
- Tiles load as one mosaic per dataset, coloured by height.
- Create mosaic saves a VRT or GeoTIFF (point clouds: a virtual point cloud or cropped COPC).
- Clear also removes the drawn area; the buffer is gone.

## 0.11.0

- Renamed LIDAR Downloader UK: uninstall the old "LIDAR Downloader" (settings and downloads carry over).
- A service that doesn't answer no longer hides the others' results.
- Long Welsh and Scottish result lists fetched in pages.
- Partly downloaded tiles no longer count as downloaded.
- Processing: accepts non-commercial terms and survey years as shown; says when a service is down.
- Downloads stay on the official services; zips that won't fit aren't extracted.
- Places ranked cities first; wording no longer England only.

## 0.10.0

- Wales (DataMapWales): the 2020-22 national DTM / DSM and NRW's archive (1998-2015).
- Scotland (Scottish Remote Sensing Portal): Phase 1-6 DTM / DSM, point clouds and other surveys.
- 10 km files downloaded once and shared by their four tiles.
- Non-commercial surveys ask first.
- Northern Ireland: references, postcodes and places point to the official sources.
- "Official data sources" menu.

## 0.9.5

- The grid is the whole OS 5 km grid, marked where there's data: coastal and estuary tiles can be downloaded.
- New: coastal bathymetry, IRRGBN aerial photos, other oblique photos.

## 0.9.4

- Exports no longer overwrite other datasets' files with the same name.
- Downloads follow only Environment Agency links, for valid tiles.
- Licence and OS grid notice ship with the plugin.

## 0.9.3

- The Processing download tool can style its output and make contours.
- Help link in the panel and Processing tools.
- Fits narrower panels; no QGIS 4 warning from the colour stretch.

## 0.9.2

- My downloads: show, load, open or delete each survey.
- Disk space check; three tiles download at once.
- SurfZone DEM.

## 0.9.1

- River bathymetry where surveyed; can be burnt into DTM exports.

## 0.9.0

- Point clouds (QGIS 3.32+): a group's tiles load as one virtual point cloud, coloured by class, in British National Grid.
- Export terrain from point clouds (DTM or DSM) and Clip to layer; also in Processing.

## 0.8.2

- Contour smoothing: none, light or strong.
- "Use as 3D terrain".

## 0.8.1

- Difference between two surfaces (e.g. new minus old survey), with volumes; also in Processing.

## 0.8.0

- Export terrain: a clipped, resampled grid as GeoTIFF, ASC, FLT or XYZ.
- Export contours: GeoPackage, Shapefile or DXF (3D lines).
- Both also in Processing.

## 0.7.1

- Go to: a grid reference, coordinates, postcode or place.
- Choice of contour interval.
- Layers carry the Environment Agency attribution and licence; set up for the Elevation Profile tool.
- Dataset descriptions; an offer to switch to British National Grid.

## 0.7.0

- Pick on map, and Draw area (with a buffer).
- Show coverage of the chosen dataset.
- Survey dates for Composite tiles.

## 0.6.3

- Styles apply to a mosaic of the group: one colour stretch, seamless hillshade and contours.
- Real contour lines, with index contours labelled.

## 0.6.2

- Styles: hillshade, contours, elevation colours, or your own (.qml).

## 0.6.1

- The legend has its own section.
- Fixed crashes on QGIS 3.x (dataset menu, download progress).

## 0.6.0

- Choose a whole product: each tile's newest survey, with the grid shaded per survey.
- Folder layout product / resolution / year / tile; old downloads moved after asking.
- The download folder is filled in when QGIS starts.

## 0.5.5

- Dataset menu: products at the top level, resolutions and years inside.

## 0.5.4

- Dataset list grouped by product and resolution.
- Layers already in the project aren't loaded again.
- The grid moves to the top when shown.

## 0.5.3

- Hillshade and Clip to layer (Cloud-Optimised GeoTIFFs, made in the background).
- "Load tiles after downloading" option.
- Processing can clip and hillshade.

## 0.5.2

- Processing algorithm "Download LIDAR / aerial tiles" (models, batch, scripts).

## 0.5.1

- Aerial photos: vertical photos as rasters, oblique photos as points with map tips.

## 0.5.0

- Dataset list: every product, survey year and resolution for the selected tiles.
- "Latest available" option.
- Each dataset in its own folder.
- Point clouds load as point cloud layers.
- Size estimates per dataset.

## 0.4.6

- New icon.

## 0.4.5

- Deleting extracted zips is permanent, and the warning says so.

## 0.4.4

- Tile names on the grid when zoomed in.
- "Open" button for the download folder.
- "Delete extracted zips..." with a clear warning.

## 0.4.3

- Fix: QGIS 4 listed the plugin as incompatible.

## 0.4.2

- "Tidy up" deletes zips whose tiles are fully extracted.

## 0.4.1

- Option to delete zips after extracting.

## 0.4

- Redesigned panel: Choose, Download, Use.
- Select the tiles in view or under a layer.
- Download size estimate.
- Results in the message bar, not pop-ups.
- The grid ships as one GeoPackage.

## 0.3

- Downloads run in the background, with progress and Cancel.
- Uses QGIS's network settings; retries temporary errors.
- Automated tests on QGIS 3.40, 3.44 and 4.2.

## 0.2.1

- Downloads work again (the new Defra address).
- Failure reasons shown per tile.

## 0.2

- QGIS 4 (Qt6) support alongside QGIS 3.22+.
- Fixes: zipped tiles recognised, removing the grid, the grid tick box, unloading, failed downloads.

## 0.1

- Initial release.
