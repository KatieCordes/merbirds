#!/usr/bin/env python3
"""
Fetches photos from Flickr albums, matches each
one to a species by its title, and writes photos.json for the app to
fetch as a plain static file.

Runs inside GitHub Actions, where FLICKR_API_KEY is a repo secret and
never touches the deployed site or a visitor's browser. This script is
the only place that ever sees the real key.
"""
import json
import os
import re
import sys
import urllib.parse
import urllib.request

FLICKR_API_KEY = os.environ.get("FLICKR_API_KEY", "")
FLICKR_USER_URL = "https://www.flickr.com/photos/katiecordes/"
FLICKR_PHOTOSET_IDS = [
    "72157720146899842",
    "72177720335780867",
]

# A photo's title only matches a species if it normalises to an exact
# name. This covers older/alternate common names used in some titles
# that don't match the current eBird name. Keep this in sync with the
# same map if it's ever duplicated elsewhere.
TITLE_ALIASES = {
    "crestedshriketit": "Eastern Shrike-tit",
    "rufousfantail": "Australian Rufous Fantail",
}

HTML_PATH = "index.html"
OUTPUT_PATH = "photos.json"


def normalise(s):
    return re.sub(r"[^a-z]", "", s.lower())


def flickr_call(method, **params):
    query = {"method": method, "api_key": FLICKR_API_KEY, "format": "json", "nojsoncallback": "1"}
    query.update(params)
    url = "https://api.flickr.com/services/rest/?" + urllib.parse.urlencode(query)
    with urllib.request.urlopen(url, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def load_species_names():
    with open(HTML_PATH, encoding="utf-8") as f:
        html = f.read()
    m = re.search(r'<script id="bird-data" type="application/json">(.*?)</script>', html, re.S)
    if not m:
        print("Could not find embedded species data in index.html", file=sys.stderr)
        sys.exit(1)
    data = json.loads(m.group(1))
    return [sp["c"] for sp in data["species"]]


def match_title_to_species(title, species_by_norm):
    prefix_match = re.match(r"^[A-Za-z\-\s']+", title or "")
    prefix = prefix_match.group(0).strip() if prefix_match else ""
    norm = normalise(prefix)
    if not norm:
        return None
    aliased = TITLE_ALIASES.get(norm)
    if aliased:
        return aliased
    return species_by_norm.get(norm)


def main():
    if not FLICKR_API_KEY:
        print("FLICKR_API_KEY not set, skipping photo update.", file=sys.stderr)
        sys.exit(1)

    species_names = load_species_names()
    species_by_norm = {normalise(name): name for name in species_names}

    lookup = flickr_call("flickr.urls.lookupUser", url=FLICKR_USER_URL)
    nsid = lookup.get("user", {}).get("id")
    if not nsid:
        print("Could not resolve Flickr user id.", file=sys.stderr)
        sys.exit(1)

    photo_list = []
    seen_photo_ids = set()
    for photoset_id in FLICKR_PHOTOSET_IDS:
        try:
            page = 1
            while True:
                photos_data = flickr_call(
                    "flickr.photosets.getPhotos",
                    photoset_id=photoset_id,
                    user_id=nsid,
                    extras="url_q,url_m,owner_name",
                    per_page="500",
                    page=page,
                )
                if photos_data.get("stat") == "fail":
                    raise RuntimeError(photos_data.get("message", "Flickr API request failed"))
                photoset = photos_data.get("photoset", {})
                for photo in photoset.get("photo", []):
                    photo_id = photo.get("id")
                    if photo_id is None or photo_id not in seen_photo_ids:
                        if photo_id is not None:
                            seen_photo_ids.add(photo_id)
                        photo_list.append(photo)
                if page >= photoset.get("pages", 1):
                    break
                page += 1
        except Exception as exc:
            print(f"Warning: could not load Flickr album {photoset_id}: {exc}", file=sys.stderr)

    result = {}
    matched = 0
    for p in photo_list:
        # Belt-and-braces: only accept photos actually owned by this
        # account, even though photoset_id should already scope this.
        if p.get("owner") and p["owner"] != nsid:
            continue
        title = p.get("title", "")
        species_name = match_title_to_species(title, species_by_norm)
        if not species_name or not p.get("url_q"):
            continue
        result.setdefault(species_name, []).append({
            "id": p.get("id"),
            "title": title,
            "thumb": p.get("url_q"),
            "medium": p.get("url_m") or p.get("url_q"),
        })
        matched += 1

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, separators=(",", ":"))

    print(f"Matched {matched} of {len(photo_list)} photos across {len(result)} species.")


if __name__ == "__main__":
    main()
