"""Emby-compatible NFO metadata and poster/fanart logic."""

import xml.etree.ElementTree as ET
from xml.dom import minidom
from pathlib import Path
from typing import Optional
import re


def sanitize_filename(name: str, max_len: int = 80) -> str:
    """Remove characters unsafe for filenames and truncate if too long."""
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    # Truncate aggressively to avoid filesystem errors (max 255 on most FS)
    if len(name) > max_len:
        name = name[:max_len].rstrip()
    return name


def build_emby_path(
    output_dir: Path,
    site_name: str,
    title: str,
    video_id: str,
    organize_by_site: bool = True,
) -> Path:
    """Build the Emby-compatible directory path for a video."""
    safe_title = sanitize_filename(title) or f"video_{video_id}"
    if organize_by_site:
        folder = output_dir / sanitize_filename(site_name) / safe_title
    else:
        folder = output_dir / f"{sanitize_filename(site_name)} - {safe_title}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def determine_poster_fanart(
    covers: list[str],
) -> tuple[Optional[str], Optional[str]]:
    """
    Determine which image to use as poster vs fanart.

    Logic:
    - If only horizontal covers: use as both poster and fanart
    - If only vertical covers: use as both poster and fanart
    - If both horizontal and vertical: vertical = poster, horizontal = fanart

    Heuristic: vertical images have aspect ratio height > width,
    horizontal images have width > height.
    We don't know dimensions from URLs alone, so:
    - coverImg[] = horizontal (from docs)
    - verticalImg[] = vertical (from docs, e.g. ACFAN)
    - For single cover: assume horizontal unless it looks vertical
    """
    horizontal = []
    vertical = []

    for url in covers:
        url_lower = url.lower()
        # Known vertical indicators in URL
        if any(v in url_lower for v in ["vertical", "poster", "vthumb"]):
            vertical.append(url)
        # Known horizontal indicators
        elif any(h in url_lower for h in ["horizontal", "fanart", "thumb", "landscape"]):
            horizontal.append(url)
        else:
            # Default to horizontal for unknown
            horizontal.append(url)

    poster = None
    fanart = None

    if vertical and horizontal:
        # Vertical as poster, horizontal as fanart
        poster = vertical[0]
        fanart = horizontal[0]
    elif vertical:
        # Only vertical - use as both
        poster = vertical[0]
        fanart = vertical[0]
    elif horizontal:
        # Only horizontal - use as both
        poster = horizontal[0]
        fanart = horizontal[0]

    return poster, fanart


def generate_nfo(
    title: str,
    year: str = "",
    plot: str = "",
    tags: Optional[list[str]] = None,
    genres: Optional[list[str]] = None,
    studio: str = "",
    rating: float = 0.0,
    runtime_seconds: int = 0,
    actors: Optional[list[str]] = None,
    video_id: str = "",
    source_url: str = "",
) -> str:
    """Generate Emby-compatible movie.nfo XML."""
    tags = tags or []
    genres = genres or []
    actors = actors or []

    movie = ET.Element("movie")

    title_el = ET.SubElement(movie, "title")
    title_el.text = title

    if year:
        year_el = ET.SubElement(movie, "year")
        year_el.text = year

    if plot:
        plot_el = ET.SubElement(movie, "plot")
        plot_el.text = plot

    if studio:
        studio_el = ET.SubElement(movie, "studio")
        studio_el.text = studio

    if rating > 0:
        rating_el = ET.SubElement(movie, "rating")
        rating_el.text = str(rating)

    if runtime_seconds > 0:
        runtime_el = ET.SubElement(movie, "runtime")
        runtime_el.text = str(runtime_seconds)

    if source_url:
        source_el = ET.SubElement(movie, "sourceurl")
        source_el.text = source_url

    if video_id:
        id_el = ET.SubElement(movie, "id")
        id_el.text = video_id

    for tag in tags:
        tag_el = ET.SubElement(movie, "tag")
        tag_el.text = tag

    for genre in genres:
        genre_el = ET.SubElement(movie, "genre")
        genre_el.text = genre

    for actor_name in actors:
        actor_el = ET.SubElement(movie, "actor")
        name_el = ET.SubElement(actor_el, "name")
        name_el.text = actor_name

    rough = ET.tostring(movie, encoding="unicode")
    dom = minidom.parseString(rough)
    return dom.toprettyxml(indent="  ")
