"""File categories by extension."""

from __future__ import annotations

from pathlib import Path

CATEGORY_EXTENSIONS: dict[str, frozenset[str]] = {
    "image": frozenset(
        "jpg jpeg jpe jfif png gif webp heic heif avif tif tiff bmp jxl "
        "dng cr2 cr3 crw nef nrw arw srf sr2 orf rw2 raf pef srw x3f 3fr erf kdc mrw".split()
    ),
    "video": frozenset(
        "mp4 m4v mov mkv avi wmv asf flv f4v webm 3gp 3g2 mts m2ts ts mpg mpeg mpe vob "
        "ogv divx xvid rm rmvb".split()
    ),
    "audio": frozenset(
        "mp3 flac ogg oga opus m4a m4b aac wav wma aiff aif alac ape wv mka dsf".split()
    ),
    "document": frozenset(
        "pdf doc docx odt ods odp odg xls xlsx xlsm ppt pptx rtf txt md tex epub mobi djvu "
        "csv tsv pages numbers key".split()
    ),
    "archive": frozenset(
        "zip tar gz tgz bz2 tbz2 xz txz zst 7z rar iso img dmg deb rpm cab lz lzma "
        "appimage".split()
    ),
}

CATEGORY_LABELS = {
    "image": "Photo",
    "video": "Video",
    "audio": "Music",
    "document": "Document",
    "archive": "Archive",
    "other": "Other",
}


def extension(path: Path) -> str:
    """Lower-case extension without the dot (``""`` if none)."""
    return path.suffix[1:].lower()


def category_of(path: Path) -> str:
    ext = extension(path)
    for category, extensions in CATEGORY_EXTENSIONS.items():
        if ext in extensions:
            return category
    return "other"
