# MusicBrainz.py
# Example addon implementataion using MusicBrainz API

import requests
import time
from typing import List, Dict, Any, Optional
from addon_base import MetadataFetcher

# Supported fields:
# album
# albumArtist
# artist
# comment
# composer
# description
# disk
# genre
# lyrics
# picture
# publisher
# releaseType
# title
# track
# unsyncedLyrics
# year

# coverart for search
# picture for fetches

# Other fields returned as is

class MusicBrainz(MetadataFetcher):
    name = "MusicBrainz"
    id = "musicbrainz"
    description = "Fetch song and album metadata from MusicBrainz database"

    # Base URL for addon
    BASE_URL = "https://musicbrainz.org/ws/2/"

    # Recommended to have meaningful user-agent
    USER_AGENT = "metadata-docker-musicbrainz-addon/1.0.0"

    _last_request_time = 0.0

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": self.USER_AGENT
        })

    def _rate_limit(self):
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < 1.0:
            time.sleep(1.0 - elapsed)
        self._last_request_time = time.time()

    def _get(self, endpoint: str, params: Dict[str, Any]) -> Dict[str, Any]:
        self._rate_limit()
        url = self.BASE_URL + endpoint
        params.setdefault("fmt", "json")
        resp = self.session.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _artist_string(artist_credit: List[Dict]) -> str:
        names = []
        for credit in artist_credit:
            if "artist" in credit:
                names.append(credit["artist"]["name"])
            elif "name" in credit:
                names.append(credit["name"])
        
        if not names:
            return "Various Artists"
        if len(names) == 1:
            return names[0]
        return "; ".join(names)

    @staticmethod
    def _first_artist_id(artist_credit: List[Dict]) -> Optional[str]:
        """Return the MusicBrainz ID of the first artist in the credit list."""
        for credit in artist_credit:
            if "artist" in credit and "id" in credit["artist"]:
                return credit["artist"]["id"]
        return None

    @staticmethod
    def _extract_year(date_str: Optional[str]) -> Optional[str]:
        if date_str and len(date_str) >= 4:
            return date_str[:4]
        return None

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
        """Extract genre names, capitalize each word, and join with '; '."""
        if not genres_data:
            return ""
        
        names = [genre.get("name", "") for genre in genres_data if genre.get("name")]
        formatted_genres = [name.title() for name in names]
        return "; ".join(formatted_genres)

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
          - title, artist, album, albumArtist, year, length, video, comment,
            publisher, releaseType, track, disk
        """
        track = {}

        # Recording basics
        track["musicbrainz_trackid"] = recording.get("id")
        track["title"] = recording.get("title", "Unknown Title")
        track["artist"] = self._flatten_artist(recording.get("artist-credit", []))
        track["musicbrainz_artistid"] = self._first_artist_id(recording.get("artist-credit", []))
        track["length"] = recording.get("length")
        track["video"] = recording.get("video", False)

        # Disambiguation as comment
        if recording.get("disambiguation"):
            track["comment"] = recording["disambiguation"]

        # Release context
        if release:
            track["album"] = release.get("title", "Unknown Album")
            track["albumArtist"] = self._flatten_artist(release.get("artist-credit", []))
            track["musicbrainz_albumid"] = release.get("id")

            # Date handling – keep full date, also store year_short
            if "date" in release:
                track["year"] = release["date"]        # full date string (e.g., "1988-11-01")
                track["first-release-date"] = release["date"]
                track["year_short"] = self._extract_year(release["date"])
            else:
                track["year"] = None
                track["first-release-date"] = None
                track["year_short"] = None

            # Publisher
            if "label-info" in release and release["label-info"]:
                label = release["label-info"][0].get("label")
                if label:
                    track["publisher"] = label.get("name")

            # Release type
            if "release-group" in release:
                rg = release["release-group"]
                if "primary-type" in rg:
                    track["releaseType"] = rg["primary-type"]

            # Release disambiguation (if not already set)
            if release.get("disambiguation") and "comment" not in track:
                track["comment"] = release["disambiguation"]

        # Track and disc numbers
        if track_info:
            track["track"] = track_info.get("number")
        if medium_info:
            track["disk"] = medium_info.get("position")
        
        genres_data = recording.get("genres", [])
        track["genre"] = self._format_genres(genres_data)

        # coveart - base64 encoded(optional)
        # track["picture"] = ""

        # Add any other primitive fields from recording (optional)
        for key, value in recording.items():
            if key not in track and not isinstance(value, (dict, list)):
                track[key] = value

        return track

    # ------------------ Search Methods ------------------

    def search_songs(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """
        Search for recordings (songs). Results include:
          - musicbrainz_trackid, title, artist
        """
        params = {"query": query, "limit": min(limit, 100)}
        data = self._get("recording", params)
        results = []
        for rec in data.get("recordings", []):
            results.append({
                "id": rec.get("id"),
                "title": rec.get("title", "Unknown"),
                "artist": self._flatten_artist(rec.get("artist-credit", [])),
                # base64 encoded coveart(optional)
                # "coverart": "" 
            })
        return results[:limit]

    def search_albums(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """
        Search for releases (albums). Results include:
          - musicbrainz_albumid, title, artist, year (from release date)
        """
        params = {"query": query, "limit": min(limit, 100)}
        data = self._get("release", params)
        results = []
        for rel in data.get("releases", []):
            results.append({
                "id": rel.get("id"),
                "title": rel.get("title", "Unknown"),
                "artist": self._flatten_artist(rel.get("artist-credit", [])),
                "year": rel.get("date")  # full date string (or None)
            })
        return results[:limit]

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

        return self._build_track_metadata(recording, release)

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
                tracks.append(track)

        return tracks