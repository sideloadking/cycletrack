"""Minimal TIFF reader.

Reads the subset of TIFF used by elevation rasters (single or multi-band,
8/16/32-bit integer or 32/64-bit float, strip- or tile-based, uncompressed or
Deflate/LZW) and returns a numpy array. No rasterio/tifffile/GDAL required.
"""

import io
import struct
import zlib

import numpy as np

TAG_WIDTH = 256
TAG_HEIGHT = 257
TAG_BITS = 258
TAG_COMPRESSION = 259
TAG_PHOTOMETRIC = 262
TAG_STRIP_OFFSETS = 273
TAG_SAMPLES = 277
TAG_ROWS_PER_STRIP = 278
TAG_STRIP_COUNTS = 279
TAG_PLANAR = 284
TAG_SAMPLE_FORMAT = 339
TAG_TILE_WIDTH = 322
TAG_TILE_LENGTH = 323
TAG_TILE_OFFSETS = 324
TAG_TILE_COUNTS = 325

TYPE_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8,
              11: 4, 12: 8, 16: 8, 17: 8, 18: 8}


class TiffDecodeError(ValueError):
    pass


def _lzw_decode(data):
    """TIFF LZW decoder (MSB-first bit order, 9-bit minimum code width)."""
    out = bytearray()
    if not data:
        return bytes(out)

    bit = 0
    def next_code(width):
        nonlocal bit
        code = 0
        for _ in range(width):
            if bit >> 3 >= len(data):
                return 257  # end of information
            code = (code << 1) | ((data[bit >> 3] >> (7 - (bit & 7))) & 1)
            bit += 1
        return code

    table = {i: bytes([i]) for i in range(256)}
    table[256] = b""          # clear code
    table[257] = b""          # EOI (unused in decode)

    code_width = 9
    prev = b""
    first = True
    next_free = 258

    while True:
        code = next_code(code_width)
        if code == 256:  # clear
            table = {i: bytes([i]) for i in range(256)}
            table[256] = b""
            table[257] = b""
            code_width = 9
            next_free = 258
            first = True
            continue
        if code == 257:
            break

        if first:
            if code >= 256:
                break
            entry = table[code]
            prev = entry
            first = False
            out.extend(entry)
            continue

        if code in table:
            entry = table[code]
        elif code == next_free:
            entry = prev + prev[:1]
        else:
            break

        out.extend(entry)
        if next_free < 4096:
            table[next_free] = prev + entry[:1]
            next_free += 1
            # TIFF LZW uses the "early change" convention: the code width
            # increases one entry sooner than mathematically necessary
            # (511 -> 10 bits, 1023 -> 11, 2047 -> 12). GIF-style timing
            # (512/1024/2048) misdecodes every code past the first width
            # change and shears the raster.
            if next_free >= (1 << code_width) - 1 and code_width < 12:
                code_width += 1
        prev = entry

    return bytes(out)


def _read_values(buf, entry_type, entry_count, offset, endian="<"):
    size = TYPE_SIZES.get(entry_type)
    if not size:
        raise TiffDecodeError(f"Unsupported TIFF entry type {entry_type}")
    total = size * entry_count
    data = buf[offset:offset + total]
    if len(data) < total:
        data = data + b"\x00" * (total - len(data))
    if entry_type in (1, 6, 7):
        return list(data[:entry_count])
    if entry_type == 2:
        return data[:entry_count].rstrip(b"\x00").decode("utf-8", "replace")
    if entry_type == 3:
        return list(struct.unpack(f"{endian}{entry_count}H", data[:entry_count * 2]))
    if entry_type == 8:
        return list(struct.unpack(f"{endian}{entry_count}h", data[:entry_count * 2]))
    if entry_type == 4:
        return list(struct.unpack(f"{endian}{entry_count}I", data[:entry_count * 4]))
    if entry_type == 9:
        return list(struct.unpack(f"{endian}{entry_count}i", data[:entry_count * 4]))
    if entry_type == 16:
        return list(struct.unpack(f"{endian}{entry_count}Q", data[:entry_count * 8]))
    if entry_type == 17:
        return list(struct.unpack(f"{endian}{entry_count}q", data[:entry_count * 8]))
    if entry_type == 11:
        return list(struct.unpack(f"{endian}{entry_count}f", data[:entry_count * 4]))
    if entry_type == 12:
        return list(struct.unpack(f"{endian}{entry_count}d", data[:entry_count * 8]))
    raise TiffDecodeError(f"Unsupported TIFF entry type {entry_type}")


def read_tiff(data):
    """Decode a TIFF byte string into a 2D numpy array (samples, rows, cols).

    Returns (array, width, height). The first axis is the sample/band.
    """
    if isinstance(data, str):
        data = open(data, "rb").read()
    data = bytes(data)
    if len(data) < 8:
        raise TiffDecodeError("Truncated TIFF")

    if data[:2] == b"II":
        endian = "<"
    elif data[:2] == b"MM":
        endian = ">"
    else:
        raise TiffDecodeError("Not a TIFF (bad byte order)")

    if struct.unpack(endian + "H", data[2:4])[0] != 42:
        raise TiffDecodeError("Not a TIFF (bad magic)")

    ifd_off = struct.unpack(endian + "I", data[4:8])[0]
    entries = _read_ifd(data, ifd_off, endian)

    width = entries.get(TAG_WIDTH, 1)
    height = entries.get(TAG_HEIGHT, 1)
    bits = entries.get(TAG_BITS, [8])
    if isinstance(bits, list):
        bits = bits[0] if bits else 8
    compression = entries.get(TAG_COMPRESSION, 1)
    samples = entries.get(TAG_SAMPLES, 1)
    sample_format = entries.get(TAG_SAMPLE_FORMAT, [1])
    if isinstance(sample_format, list):
        sample_format = sample_format[0] if sample_format else 1
    planar = entries.get(TAG_PLANAR, 1)
    photometric = entries.get(TAG_PHOTOMETRIC, 1)

    def _as_list(v):
        return v if isinstance(v, list) else ([v] if v is not None else [])

    if TAG_TILE_OFFSETS in entries:
        offsets = _as_list(entries[TAG_TILE_OFFSETS])
        counts = _as_list(entries.get(TAG_TILE_COUNTS))
        tile_w = entries.get(TAG_TILE_WIDTH, width)
        tile_h = entries.get(TAG_TILE_LENGTH, height)
        is_tiled = True
    else:
        offsets = _as_list(entries.get(TAG_STRIP_OFFSETS))
        counts = _as_list(entries.get(TAG_STRIP_COUNTS))
        rows_per_strip = entries.get(TAG_ROWS_PER_STRIP, height)
        tile_w, tile_h = width, rows_per_strip
        is_tiled = False

    if not offsets:
        raise TiffDecodeError("No strip/tile offsets")

    ncols = width
    nrows = height

    if bits == 8:
        dtype = np.uint8
    elif bits == 16:
        dtype = np.uint16 if sample_format in (1, 4) else np.int16
    elif bits == 32:
        if sample_format == 3:
            dtype = np.float32
        elif sample_format == 2:
            dtype = np.int32
        else:
            dtype = np.uint32
    elif bits == 64:
        dtype = np.float64 if sample_format == 3 else np.int64
    else:
        raise TiffDecodeError(f"Unsupported bit depth {bits}")

    # Honour the file's byte order for the pixel data (native order is
    # little-endian on the platforms we run on).
    if endian == ">":
        dtype = np.dtype(dtype).newbyteorder(">")

    bytes_per_sample = bits // 8

    if compression == 1:
        decompress = lambda b: b
    elif compression in (8, 32946):
        decompress = zlib.decompress
    elif compression == 5:
        decompress = _lzw_decode
    else:
        raise TiffDecodeError(f"Unsupported compression {compression}")

    if is_tiled:
        tiles_across = (ncols + tile_w - 1) // tile_w
        tiles_down = (nrows + tile_h - 1) // tile_h
        out = np.zeros((samples, nrows, ncols), dtype=dtype)
        for i, off in enumerate(offsets):
            if counts and i < len(counts):
                raw = decompress(data[off:off + counts[i]])
            else:
                raw = decompress(data[off:])
            tx = (i % tiles_across) * tile_w
            ty = (i // tiles_across) * tile_h
            w = min(tile_w, ncols - tx)
            h = min(tile_h, nrows - ty)
            arr = np.frombuffer(raw, dtype=dtype)
            # A compliant writer stores every tile at its full tile_w x tile_h
            # size, padding the right/bottom edge tiles; the valid region is
            # the top-left w x h corner. Reshaping straight to (h, w) here
            # would ignore the full-tile row stride and shear the image, so
            # reshape to the full tile first and slice. A few writers emit
            # only the valid sub-region instead; fall back to that shape.
            full = tile_w * tile_h * samples
            if arr.size >= full:
                arr = arr[:full]
                if planar == 2:
                    tile = arr.reshape(samples, tile_h, tile_w)
                else:
                    tile = arr.reshape(tile_h, tile_w, samples).transpose(2, 0, 1)
                tile = tile[:, :h, :w]
            else:
                need = w * h * samples
                if arr.size < need:
                    arr = np.pad(arr, (0, need - arr.size))
                arr = arr[:need]
                if planar == 2:
                    tile = arr.reshape(samples, h, w)
                else:
                    tile = arr.reshape(h, w, samples).transpose(2, 0, 1)
            out[:, ty:ty + h, tx:tx + w] = tile
        return out, width, height

    # Strips.
    strip_heights = []
    remaining = nrows
    idx = 0
    while remaining > 0:
        sh = min(rows_per_strip, remaining)
        strip_heights.append(sh)
        remaining -= sh
        idx += 1

    out = np.zeros((samples, nrows, ncols), dtype=dtype)
    row = 0
    for i, off in enumerate(offsets):
        raw = decompress(data[off:off + counts[i]]) if i < len(counts) else decompress(data[off:])
        h = strip_heights[i] if i < len(strip_heights) else rows_per_strip
        need = ncols * h * samples
        arr = np.frombuffer(raw, dtype=dtype)
        if arr.size < need:
            arr = np.pad(arr, (0, need - arr.size))
        arr = arr[:need]
        if planar == 2:
            arr = arr.reshape(samples, h, ncols)
        else:
            arr = arr.reshape(h, ncols, samples).transpose(2, 0, 1)
        out[:, row:row + h, :] = arr
        row += h
    return out, width, height


def _read_ifd(buf, ifd_off, endian):
    entries = {}
    try:
        n = struct.unpack_from(endian + "H", buf, ifd_off)[0]
    except Exception:
        return entries
    pos = ifd_off + 2
    for _ in range(n):
        tag, etype, ecount = struct.unpack_from(endian + "HHI", buf, pos)
        try:
            size = TYPE_SIZES.get(etype, 0)
            total = size * ecount
            if total <= 4:
                # Value stored inline in the 4-byte field, left-justified in
                # the low-address bytes for both byte orders.
                raw = buf[pos + 8:pos + 12]
                vals = _read_values(raw[:total], etype, ecount, 0, endian)
            else:
                off = struct.unpack_from(endian + "I", buf, pos + 8)[0]
                vals = _read_values(buf, etype, ecount, off, endian)
        except (TiffDecodeError, struct.error, ValueError, IndexError):
            pos += 12
            continue
        if isinstance(vals, list) and ecount == 1 and etype not in (1, 6, 7):
            entries[tag] = vals[0]
        else:
            entries[tag] = vals
        pos += 12
    return entries
