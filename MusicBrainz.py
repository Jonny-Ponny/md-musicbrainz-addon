# MusicBrainz.py
# Example addon implementation using the MusicBrainz API

import os
import threading
import time
from typing import List, Dict, Any, Optional

import requests
from addon_base import MetadataFetcher

# Supported fields:
# album
# albumArtist
# artist
# comment
# composer
# date
# description
# diskNumber
# genre
# lyrics
# picture
# publisher
# releaseType
# title
# trackNumber
# unsyncedLyrics
#
# extraInfo is display-only and is never written as a tag.
# coverart is used for search results, picture for fetched metadata.
# Other fields returned as is

class MusicBrainz(MetadataFetcher):
    name = "MusicBrainz"
    id = "musicbrainz"
    description = "Fetch song and album metadata from MusicBrainz database"
    version = "1.1.0"
    github = "https://github.com/Jonny-Ponny/md-musicbrainz-addon"

    # Base URL for addon
    BASE_URL = "https://musicbrainz.org/ws/2/"
    COVER_ART_URL = "https://coverartarchive.org/release/{release_id}"

    # Recommended to have a meaningful user-agent with contact information
    USER_AGENT = (
        f"metadata-docker-musicbrainz-addon/{version} "
        "(https://github.com/Jonny-Ponny/md-musicbrainz-addon)"
    )

    required_env_vars = ["MD_MUSICBRAINZ_FETCH_COVER"]

    # Get setting from environment
    FETCH_COVERART = os.getenv("MD_MUSICBRAINZ_FETCH_COVER", "False").lower() in ('true', '1', 'yes', 'on')

    REQUEST_TIMEOUT = 20
    RATE_LIMIT_SECONDS = 1.0

    # MusicBrainz allows one request per second. The app creates a new fetcher
    # instance per API request, so the rate-limit state must live on the class
    # and be protected by a lock to work across requests.
    _rate_lock = threading.Lock()
    _last_request_time = 0.0

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": self.USER_AGENT
        })

    def _rate_limit(self):
        cls = type(self)
        with cls._rate_lock:
            now = time.monotonic()
            elapsed = now - cls._last_request_time
            if elapsed < self.RATE_LIMIT_SECONDS:
                time.sleep(self.RATE_LIMIT_SECONDS - elapsed)
            cls._last_request_time = time.monotonic()

    def _get(self, endpoint: str, params: Dict[str, Any]) -> Dict[str, Any]:
        url = self.BASE_URL + endpoint
        request_params = dict(params)
        request_params.setdefault("fmt", "json")

        # MusicBrainz occasionally returns 429/503 when busy. Retry a couple of
        # times with the server-provided delay before giving up.
        for attempt in range(3):
            self._rate_limit()
            resp = self.session.get(
                url,
                params=request_params,
                timeout=self.REQUEST_TIMEOUT,
            )
            if resp.status_code in (429, 503) and attempt < 2:
                retry_after = resp.headers.get("Retry-After")
                try:
                    delay = max(self.RATE_LIMIT_SECONDS, float(retry_after))
                except (TypeError, ValueError):
                    delay = self.RATE_LIMIT_SECONDS * (attempt + 2)
                time.sleep(delay)
                continue

            resp.raise_for_status()
            return resp.json()

    @staticmethod
    def _artist_string(artist_credit: List[Dict]) -> str:
        """Join an artist credit using MusicBrainz's join phrases."""
        if not artist_credit:
            return "Unknown Artist"

        parts = []
        for index, credit in enumerate(artist_credit):
            artist = credit.get("artist") or {}
            name = artist.get("name") or credit.get("name")
            if not name:
                continue
            parts.append(name)
            if index < len(artist_credit) - 1:
                parts.append(credit.get("joinphrase") or "; ")

        return "".join(parts).strip() or "Unknown Artist"

    @staticmethod
    def _first_artist_id(artist_credit: List[Dict]) -> Optional[str]:
        """Return the MusicBrainz ID of the first artist in the credit list."""
        for credit in artist_credit:
            if "artist" in credit and "id" in credit["artist"]:
                return credit["artist"]["id"]
        return None

    @staticmethod
    def _format_duration(length_ms: Any) -> str:
        """Format a MusicBrainz length in milliseconds as m:ss."""
        if not isinstance(length_ms, (int, float)) or length_ms <= 0:
            return ""
        total_seconds = int(round(length_ms / 1000))
        return f"{total_seconds // 60}:{total_seconds % 60:02d}"

    @staticmethod
    def _flatten_artist(artist_data: Any) -> str:
        if isinstance(artist_data, str):
            return artist_data
        if isinstance(artist_data, dict) and "name" in artist_data:
            return artist_data["name"]
        if isinstance(artist_data, list):
            return MusicBrainz._artist_string(artist_data)
        return "Unknown Artist"

    @staticmethod
    def _format_genres(genres_data: List[Dict]) -> str:
        """Extract unique genre names, capitalize each word, and join with '; '."""
        if not genres_data:
            return ""

        seen = set()
        formatted_genres = []
        for genre in genres_data:
            name = (genre.get("name") or "").strip()
            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())
            formatted_genres.append(name.title())
        return "; ".join(formatted_genres)

    def _get_cover_art_url(self, release_id: str) -> Optional[str]:
        """
        Query the Cover Art Archive for the front cover image URL.
        Returns the URL as a string, or None if not found.
        """
        self._rate_limit()
        url = self.COVER_ART_URL.format(release_id=release_id)
        try:
            resp = self.session.get(url, timeout=self.REQUEST_TIMEOUT)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            data = resp.json()
            for img in data.get("images", []):
                if img.get("front"):
                    return img.get("image")
        except requests.RequestException:
            # Cover art is optional, so a failure must not break metadata fetch.
            pass
        return None

    def _build_track_metadata(
        self,
        recording: Dict,
        release: Optional[Dict] = None,
        track_info: Optional[Dict] = None,
        medium_info: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        Build a flat track metadata dict. Always includes:
          - musicbrainz_trackid
          - musicbrainz_albumid (if release provided)
          - musicbrainz_artistid (first artist from recording)
          - title, artist, album, albumArtist, date, length, comment,
            publisher, releaseType, trackNumber, diskNumber
        """
        track = {}

        # Recording basics
        track["musicbrainz_trackid"] = recording.get("id")
        track["title"] = recording.get("title", "Unknown Title")
        track["artist"] = self._flatten_artist(recording.get("artist-credit", []))
        track["musicbrainz_artistid"] = self._first_artist_id(recording.get("artist-credit", []))
        track["length"] = recording.get("length")
        track["video"] = bool(recording.get("video"))

        # Disambiguation as comment
        if recording.get("disambiguation"):
            track["comment"] = recording["disambiguation"]

        # Release context
        if release:
            track["album"] = release.get("title", "Unknown Album")
            track["albumArtist"] = self._flatten_artist(release.get("artist-credit", []))
            track["musicbrainz_albumid"] = release.get("id")
            track["musicbrainz_albumartistid"] = self._first_artist_id(
                release.get("artist-credit", [])
            )

            if release.get("date"):
                track["date"] = release["date"]

            # Publisher
            if "label-info" in release and release["label-info"]:
                label = release["label-info"][0].get("label")
                if label:
                    track["publisher"] = label.get("name")

            # Release type, including secondary types (e.g. "Album + Live")
            rg = release.get("release-group") or {}
            primary_type = rg.get("primary-type")
            if primary_type:
                secondary_types = rg.get("secondary-types") or []
                track["releaseType"] = " + ".join([primary_type, *secondary_types])

            # Release disambiguation (if not already set)
            if release.get("disambiguation") and "comment" not in track:
                track["comment"] = release["disambiguation"]

        # Fall back to the recording's first release date when no release was supplied
        if "date" not in track and recording.get("first-release-date"):
            track["date"] = recording["first-release-date"]

        # Track and disc numbers
        if track_info:
            track["trackNumber"] = track_info.get("number")
        if medium_info:
            track["diskNumber"] = medium_info.get("position")

        # Recording genres are not included in release lookups, so fall back to
        # the release genres there.
        genres_data = recording.get("genres") or (release.get("genres") if release else [])
        track["genre"] = self._format_genres(genres_data or [])

        duration = self._format_duration(recording.get("length"))
        if duration:
            track["extraInfo"] = duration

        # Add any other primitive fields from recording, but skip internal
        # search/bookkeeping fields that should not become custom tags.
        skip_fields = {"id", "score", "artist-credit", "releases", "genres", "tags",
                       "disambiguation", "first-release-date"}
        for key, value in recording.items():
            if key in track or key in skip_fields or isinstance(value, (dict, list)):
                continue
            track[key] = value

        return track

    # ------------------ Search Methods ------------------

    @staticmethod
    def _clamp_limit(limit: int) -> int:
        try:
            value = int(limit)
        except (TypeError, ValueError):
            return 5
        return max(1, min(value, 100))

    def search_songs(self, query: str, limit: int = 5, include_coverart: Optional[bool] = None) -> List[Dict[str, Any]]:
        """
        Search for recordings (songs). Results include:
          - id, title, artist, album, date, coverart, extraInfo
        """
        try:
            if include_coverart is None:
                include_coverart = self.FETCH_COVERART
            params = {"query": query, "limit": self._clamp_limit(limit)}
            data = self._get("recording", params)
            results = []
            for rec in data.get("recordings", []):
                releases = rec.get("releases") or []
                release = releases[0] if releases else None

                entry = {
                    "id": rec.get("id"),
                    "title": rec.get("title", "Unknown"),
                    "artist": self._flatten_artist(rec.get("artist-credit", [])),
                }

                if release:
                    if release.get("title"):
                        entry["album"] = release["title"]
                    if release.get("date"):
                        entry["date"] = release["date"]
                if "date" not in entry and rec.get("first-release-date"):
                    entry["date"] = rec["first-release-date"]

                extra = []
                duration = self._format_duration(rec.get("length"))
                if duration:
                    extra.append(duration)
                if release and release.get("title"):
                    extra.append(release["title"])
                if extra:
                    entry["extraInfo"] = " · ".join(extra)

                if include_coverart and release and release.get("id"):
                    entry["coverart"] = self._get_cover_art_url(release["id"])

                results.append(entry)
            return results
        except Exception:
            return []

    def search_albums(self, query: str, limit: int = 5, include_coverart: Optional[bool] = None) -> List[Dict[str, Any]]:
        """
        Search for releases (albums). Results include:
          - id, title, artist, date, coverart, extraInfo
        """
        try:
            if include_coverart is None:
                include_coverart = self.FETCH_COVERART
            params = {"query": query, "limit": self._clamp_limit(limit)}
            data = self._get("release", params)
            results = []
            for rel in data.get("releases", []):
                release_id = rel.get("id")
                entry = {
                    "id": release_id,
                    "title": rel.get("title", "Unknown"),
                    "artist": self._flatten_artist(rel.get("artist-credit", [])),
                    "date": rel.get("date"),
                }

                release_group = rel.get("release-group") or {}
                extra = []
                if release_group.get("primary-type"):
                    extra.append(release_group["primary-type"])
                track_count = rel.get("track-count")
                if track_count:
                    extra.append(f"{track_count} track" + ("" if track_count == 1 else "s"))
                if extra:
                    entry["extraInfo"] = " · ".join(extra)

                if include_coverart and release_id:
                    entry["coverart"] = self._get_cover_art_url(release_id)

                results.append(entry)
            return results
        except Exception:
            return []

    # ------------------ Fetch Metadata ------------------

    def fetch_song_metadata(self, song_id: str) -> Dict[str, Any]:
        """
        Fetch detailed metadata for a single recording.
        Returns a flat dict with musicbrainz_trackid, musicbrainz_albumid (if available),
        musicbrainz_artistid, and all other fields.
        """
        params = {"inc": "artist-credits+releases+genres"}
        data = self._get(f"recording/{song_id}", params)
        recording = data

        release = None
        if "releases" in recording and recording["releases"]:
            release = recording["releases"][0]

        track = self._build_track_metadata(recording, release)

        # Add cover art URL as "picture"
        if self.FETCH_COVERART and release and release.get("id"):
            track["picture"] = self._get_cover_art_url(release["id"])
        else:
            track["picture"] = None

        return track

    def fetch_album_metadata(self, album_id: str) -> List[Dict[str, Any]]:
        """
        Fetch all tracks for a release (album).
        Returns a list of flat track dicts, each including:
          - musicbrainz_trackid, musicbrainz_albumid, musicbrainz_artistid,
            plus all other fields.
        """
        params = {"inc": "recordings+artist-credits+release-groups+labels+genres"}
        data = self._get(f"release/{album_id}", params)
        release = data

        # Get cover art URL once for the whole album
        cover_url = self._get_cover_art_url(album_id) if self.FETCH_COVERART else None

        tracks = []
        for medium in release.get("media", []):
            medium_pos = medium.get("position")
            for track_info in medium.get("tracks", []):
                recording = track_info.get("recording", {})
                if not recording:
                    continue
                track = self._build_track_metadata(
                    recording,
                    release,
                    track_info=track_info,
                    medium_info={"position": medium_pos}
                )
                track["picture"] = cover_url  # same for all tracks
                tracks.append(track)

        return tracks