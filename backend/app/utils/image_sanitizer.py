"""Image validation and metadata sanitisation.

This is where an uploaded file stops being arbitrary bytes and becomes something
the system is willing to store. Two jobs, in this order:

**1. Establish it is actually a supported image.** The browser's `Content-Type`
is a claim, not a fact. Magic bytes are checked first, then Pillow is asked to
decode it. A file that will not decode is not an image, whatever it is named.

**2. Destroy every scrap of metadata.** Not by asking Pillow to omit EXIF — by
constructing a new image from the decoded pixel data and discarding the original
object entirely. `Image.frombytes(mode, size, img.tobytes())` carries pixels and
nothing else: no EXIF, no GPS, no camera make or model, no software version, no
PNG text chunks, no XMP, no ICC profile, and no embedded EXIF thumbnail.

That last one is worth naming. The EXIF thumbnail is a *separate image* that does
not always match the visible one — a student who crops something out of a photo
can leave it intact in the thumbnail. A metadata-field-removal approach that
misses it leaves the cropped content in the file.

**What this does not do.** It does not touch what is visibly in the picture. A
face, a name badge, a number plate, a document on a desk, or a screenshot with
the student's own name in the header all survive sanitisation untouched, because
they are pixels. No claim to the contrary is made anywhere in this system, and
the upload UI says so to the student.
"""

from __future__ import annotations

import hashlib
import io
import logging
from dataclasses import dataclass

from PIL import Image, UnidentifiedImageError
from PIL.Image import DecompressionBombError

logger = logging.getLogger(__name__)

# Formats accepted in Phase 4B-1. Video is deliberately absent; SVG is
# deliberately absent because it is a document format that can carry script and
# remote references, and "an image that can make network requests" is not
# something to accept from an unauthenticated-ish upload path.
SUPPORTED_CONTENT_TYPES: dict[str, str] = {
    "image/jpeg": "JPEG",
    "image/png": "PNG",
    "image/webp": "WEBP",
}

# Leading bytes that must be present for a format to be considered at all.
_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
)

DEFAULT_MAX_PIXELS = 50_000_000  # ~50 MP
DEFAULT_MAX_DIMENSION = 12_000
JPEG_QUALITY = 88


class ImageRejectedError(ValueError):
    """The upload is not something this system will store.

    Carries a `reason` code so the API can map it to a specific message without
    the route parsing prose.
    """

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class SanitisedImage:
    """The result: bytes safe to store, plus what was recorded about them."""

    data: bytes
    content_type: str
    width: int
    height: int
    sha256: str
    original_sha256: str
    original_byte_size: int

    @property
    def byte_size(self) -> int:
        return len(self.data)


def _sniff_content_type(data: bytes) -> str | None:
    """Identify the format from the bytes themselves."""
    for signature, content_type in _MAGIC:
        if data.startswith(signature):
            return content_type
    # WebP: 'RIFF' .... 'WEBP'
    if len(data) >= 12 and data[0:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def sanitise_image(
    data: bytes,
    *,
    declared_content_type: str | None = None,
    max_bytes: int,
    max_pixels: int = DEFAULT_MAX_PIXELS,
    max_dimension: int = DEFAULT_MAX_DIMENSION,
) -> SanitisedImage:
    """Validate, strip all metadata, and re-encode.

    Raises :class:`ImageRejectedError` for anything not stored.
    """
    if not data:
        raise ImageRejectedError("The file is empty.", reason="empty")

    if len(data) > max_bytes:
        raise ImageRejectedError(
            f"The image is larger than the {max_bytes // (1024 * 1024)} MB limit.",
            reason="too_large",
        )

    original_sha256 = hashlib.sha256(data).hexdigest()

    # 1. Magic bytes. Before Pillow is handed anything, and regardless of what
    #    the client claimed the type was.
    sniffed = _sniff_content_type(data)
    if sniffed is None:
        raise ImageRejectedError(
            "That file is not a JPEG, PNG or WebP image.", reason="unsupported_format"
        )

    # A disagreement between the claim and the bytes is not fatal — browsers get
    # this wrong routinely, especially for HEIC converted on the fly — but the
    # bytes win, always, and the mismatch is worth a log line.
    if declared_content_type and declared_content_type.split(";")[0].strip() != sniffed:
        logger.info(
            "declared content type did not match the file contents (declared=%s actual=%s)",
            declared_content_type.split(";")[0].strip(),
            sniffed,
        )

    # 2. Decode. Guarded against decompression bombs: a 100 KB PNG can declare
    #    dimensions that expand to gigabytes of pixels.
    previous_limit = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = max_pixels
    try:
        try:
            with Image.open(io.BytesIO(data)) as probe:
                probe.verify()
        except DecompressionBombError as exc:
            # Pillow raises this from open() once the declared dimensions exceed
            # the limit, before the explicit pixel check below is reached. It
            # does not inherit from OSError or ValueError, so it needs naming:
            # uncaught, a decompression bomb would surface as a 500 rather than
            # a rejected upload.
            raise ImageRejectedError(
                "The image has too many pixels to process safely.", reason="dimensions"
            ) from exc
        except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
            raise ImageRejectedError(
                "That image could not be read. It may be corrupt.", reason="corrupt"
            ) from exc

        # verify() consumes the file object, so reopen to work with it.
        try:
            with Image.open(io.BytesIO(data)) as source:
                if source.format not in SUPPORTED_CONTENT_TYPES.values():
                    raise ImageRejectedError(
                        "That file is not a JPEG, PNG or WebP image.",
                        reason="unsupported_format",
                    )

                width, height = source.size
                if width > max_dimension or height > max_dimension:
                    raise ImageRejectedError(
                        f"The image is larger than {max_dimension}px on a side.",
                        reason="dimensions",
                    )
                if width * height > max_pixels:
                    raise ImageRejectedError(
                        "The image has too many pixels to process safely.",
                        reason="dimensions",
                    )

                target_format = SUPPORTED_CONTENT_TYPES[sniffed]
                mode = _target_mode(source.mode, target_format)
                converted = source.convert(mode)

                # THE sanitisation step. A brand-new image built from raw pixel
                # bytes: nothing but pixels can survive it. Copying the object,
                # or saving with `exif=None`, would leave format-specific
                # metadata (PNG text chunks, XMP) in place.
                stripped = Image.frombytes(mode, converted.size, converted.tobytes())
        except ImageRejectedError:
            raise
        except DecompressionBombError as exc:
            raise ImageRejectedError(
                "The image has too many pixels to process safely.", reason="dimensions"
            ) from exc
        except (OSError, ValueError, MemoryError) as exc:
            raise ImageRejectedError(
                "That image could not be processed.", reason="sanitisation_failed"
            ) from exc

        # 3. Re-encode from the stripped pixels.
        out = io.BytesIO()
        try:
            if target_format == "JPEG":
                stripped.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
            elif target_format == "PNG":
                stripped.save(out, format="PNG", optimize=True)
            else:
                stripped.save(out, format="WEBP", quality=JPEG_QUALITY, method=4)
        except (OSError, ValueError) as exc:
            raise ImageRejectedError(
                "That image could not be processed.", reason="sanitisation_failed"
            ) from exc
        finally:
            stripped.close()
    finally:
        Image.MAX_IMAGE_PIXELS = previous_limit

    sanitised = out.getvalue()
    if not sanitised:
        raise ImageRejectedError("That image could not be processed.", reason="sanitisation_failed")

    return SanitisedImage(
        data=sanitised,
        content_type=sniffed,
        width=width,
        height=height,
        sha256=hashlib.sha256(sanitised).hexdigest(),
        original_sha256=original_sha256,
        original_byte_size=len(data),
    )


def _target_mode(source_mode: str, target_format: str) -> str:
    """Pixel mode to convert to before stripping.

    JPEG has no alpha channel, so transparency is flattened rather than left to
    fail at save time. PNG and WebP keep it.
    """
    if target_format == "JPEG":
        return "RGB"
    return "RGBA" if source_mode in {"RGBA", "LA", "PA", "P"} else "RGB"


def has_metadata(data: bytes) -> bool:
    """Whether an image carries any EXIF at all.

    Used by the tests that prove sanitisation worked, rather than by the
    application.
    """
    try:
        with Image.open(io.BytesIO(data)) as image:
            exif = image.getexif()
            if exif and len(dict(exif)) > 0:
                return True
            if dict(exif.get_ifd(0x8825)):  # GPS IFD
                return True
            if getattr(image, "text", None):  # PNG text chunks
                return True
            if image.info.get("exif") or image.info.get("XML:com.adobe.xmp"):
                return True
    except Exception:
        return False
    return False
