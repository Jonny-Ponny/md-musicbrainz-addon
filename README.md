# md-musicbrainz-addon
Example addon for [metadata-docker](https://github.com/Jonny-Ponny/metadata-docker). Addon uses MusicBrainz API to retrieve song and album metadata from the MusicBrainz database.

## Addon structure

### Base Class Inheritance
Every plugin must define a class that inherits from ```MetadataFetcher``` (provided in ```addon_base.py```).

The class must set the following attributes:

1. ```name = "MusicBrainz"```   - Human‑readable name (shown in UI)
2. ```id = "musicbrainz"```     - Unique identifier (used in API routes)
3. ```description = "..."```    - Optional short description
4. ```version = "1.1.0"```      - Optional version string (shown in Settings)
5. ```github = "..."```         - Optional GitHub URL (shown as a link in Settings)
6. ```required_env_vars = []``` - List of env var names needed (optional)

### Implementing the Four Core Methods
All 4 methods are not required. For any unsupported method the base class will raise ```NotImplementedError``` automatically. The ```/api/addons``` endpoint will detect which methods are actually overridden using the ```_default_implementation``` marker.
Method|Purpose|Return Type
|---|---|---|
```search_songs(query, limit)```|Search for individual tracks|```List[Dict]``` with at ```least id```, ```title```, ```artist```
```fetch_song_metadata(song_id)```|Get full details of a single track|```Dict``` with all metadata fields
```search_albums(query, limit)```|Search for releases/albums|```List[Dict]``` with at least ```id```, ```title```, ```artist```, ```date```
```fetch_album_metadata(album_id)```|Get all tracks of an album|```List[Dict]``` - each track is a full metadata dict

### Implementation details for each method:

```search_songs```
- Queries the MusicBrainz recording endpoint.
- Returns a list of short result objects (id, title, artist, album, date, coverart).
- Includes a display-only ```extraInfo``` value with the track duration and release title.

```fetch_song_metadata```
- Fetches a single recording using its MusicBrainz ID.
- Includes releases in the response to get album context.
- Returns a flat dictionary with all fields (see Metadata Fields below).
- The helper _build_track_metadata() consolidates the response.

```search_albums```
- Queries the MusicBrainz release endpoint.
- Returns a list of short result objects (id, title, artist, date, coverart).
- Includes a display-only ```extraInfo``` value with the release type and track count.

```fetch_album_metadata```
- Fetches a full release, including all tracks.
- Iterates over media and tracks to build a list of track metadata dicts.
- Each track inherits album‑level fields (album title, artist, date, etc.) automatically.

### Metadata fields
Plugin should return a flat dictionary (or list of dictionaries) that includes the fields expected by the app.
Supported fields:
- album
- albumArtist
- artist
- comment
- composer
- date
- description
- diskNumber
- genre
- lyrics
- picture
- publisher
- releaseType
- title
- trackNumber
- unsyncedLyrics
- coverart (for search results only)
- picture (cover art as a metadata field)
- extraInfo (display-only, never written as a tag)

All other fields will be used as is, using field name received from addon

MusicBrainz IDs are returned as custom fields: ```musicbrainz_trackid```, ```musicbrainz_albumid```, ```musicbrainz_artistid```, and ```musicbrainz_albumartistid```.

### Helper Methods & Best Practices

Rate Limiting: Plugin uses a class‑level ```_last_request_time``` and a ```_rate_limit()``` method called before every API call. The lock and timestamp are class‑level because the app creates a new fetcher instance for each request.

User‑Agent: Recommended to set a unique ```User‑Agent``` header to identify your application to the service (MusicBrainz requires this). This addon includes its version and GitHub URL in the header.

Environment Variables: MusicBrainz is open and does not require an API key. If your plugin needs keys, list them in ```required_env_vars``` and read them via ```os.getenv()``` from docker-compose.yml.

This addon uses ```MD_MUSICBRAINZ_FETCH_COVER``` environment variable to allow fetching of cover art in searches and metadata fetches. Since MusicBrainz and Cover Art Archive implement rate limiting, all requests are limited to 1 per second. Using searches with cover art fetching enabled will increase response time (~1 second per search result). If you need to fetch cover art use ```True```, if you want faster response time use ```False``` to disable cover art fetching.

## Using This Plugin as a Template

To create your own addon:

1. Copy ```MusicBrainz.py``` and rename it (e.g., ```MyService.py```).
2. Change the class name, id, description, version, and GitHub URL.
3. Implement the methods you want to support.
4. Add any external dependencies to a ```*_requirements.txt``` file (or folder ```*requirements.txt```).
5. Place the python file and requirements file in ```/addons```(or create separate subfolder inside ```/addons```).
6. Restart the container - the plugin will be discovered automatically.

## Troubleshooting & Logs

- Missing imports - If your plugin uses a library not in the base image, add it to the requirements file. The entrypoint will install it on container start.
- Method not detected - Ensure you have overridden the method. The detection mechanism checks for the _default_implementation marker.
- Rate limiting - If you exceed the limit, MusicBrainz returns a 503. The plugin’s rate‑limiter prevents this in normal use.
