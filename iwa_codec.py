"""Lossless protobuf + IWA codec for Apple iWork files.

Every layer round-trips byte-for-byte, which is what makes writing safe:
  tokenize/emit        protobuf wire format, order and encoding preserved
  archives/pack        TSP.ArchiveInfo framing, MessageInfo lengths rewritten
  iwa_decode/encode    Apple's chunk framing + Snappy

Verified against all 7 .iwa files of the manuscript.
"""

def read_varint(buf, pos):
    val = shift = 0
    while True:
        b = buf[pos]; pos += 1
        val |= (b & 0x7F) << shift
        if not b & 0x80:
            return val, pos
        shift += 7

def write_varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)

def tokenize(buf):
    """-> [(field_no, wire_type, value_bytes)] preserving order."""
    out, pos = [], 0
    while pos < len(buf):
        key, pos = read_varint(buf, pos)
        num, wire = key >> 3, key & 0x07
        if wire == 0:
            start = pos
            _, pos = read_varint(buf, pos)
            val = buf[start:pos]
        elif wire == 2:
            n, pos = read_varint(buf, pos)
            val = buf[pos:pos + n]; pos += n
        elif wire == 5:
            val = buf[pos:pos + 4]; pos += 4
        elif wire == 1:
            val = buf[pos:pos + 8]; pos += 8
        else:
            raise ValueError(f"wire {wire}")
        out.append((num, wire, val))
    return out

def emit(tokens):
    out = bytearray()
    for num, wire, val in tokens:
        out += write_varint((num << 3) | wire)
        if wire == 2:
            out += write_varint(len(val))
        out += val
    return bytes(out)

# -- IWA container ---------------------------------------------------------
def snappy_decompress(data):
    out, pos = bytearray(), 0
    while data[pos] & 0x80:
        pos += 1
    pos += 1
    while pos < len(data):
        tag = data[pos]; pos += 1
        kind = tag & 0x03
        if kind == 0:
            n = tag >> 2
            if n >= 60:
                extra = n - 59
                n = int.from_bytes(data[pos:pos + extra], "little"); pos += extra
            n += 1
            out += data[pos:pos + n]; pos += n
            continue
        if kind == 1:
            length = 4 + ((tag >> 2) & 0x07)
            offset = ((tag >> 5) << 8) | data[pos]; pos += 1
        elif kind == 2:
            length = (tag >> 2) + 1
            offset = int.from_bytes(data[pos:pos + 2], "little"); pos += 2
        else:
            length = (tag >> 2) + 1
            offset = int.from_bytes(data[pos:pos + 4], "little"); pos += 4
        start = len(out) - offset
        if offset >= length:           # no self-overlap: copy in one slice
            out += out[start:start + length]
        else:                          # overlapping run: byte by byte
            for i in range(length):
                out.append(out[start + i])
    return bytes(out)

def _emit_literal(out, data, start, end):
    n = end - start
    if n <= 0:
        return
    if n <= 60:
        out.append((n - 1) << 2)
    else:
        extra = ((n - 1).bit_length() + 7) // 8
        out.append((59 + extra) << 2)
        out += (n - 1).to_bytes(extra, "little")
    out += data[start:end]


def snappy_compress(block):
    """Snappy for one block (<= 64 KB). Hash-chained LZ77, greedy matching."""
    out = bytearray(write_varint(len(block)))
    n = len(block)
    table = {}
    pos = lit = 0
    while pos + 4 <= n:
        key = block[pos:pos + 4]
        cand = table.get(key, -1)
        table[key] = pos
        if cand < 0:
            pos += 1
            continue
        offset = pos - cand
        if offset > 65535:
            pos += 1
            continue
        length = 4
        while (pos + length < n and length < 64
               and block[cand + length] == block[pos + length]):
            length += 1
        _emit_literal(out, block, lit, pos)
        if length <= 11 and offset <= 2047:          # 1-byte offset copy
            out.append(0x01 | ((length - 4) << 2) | ((offset >> 8) << 5))
            out.append(offset & 0xFF)
        else:                                        # 2-byte offset copy
            out.append(0x02 | ((length - 1) << 2))
            out += offset.to_bytes(2, "little")
        pos += length
        lit = pos
    _emit_literal(out, block, lit, n)
    return bytes(out)


def iwa_decode(data):
    if not data:
        return b""
    out, pos = bytearray(), 0
    while pos < len(data):
        flag = data[pos]
        n = int.from_bytes(data[pos + 1:pos + 4], "little")
        block = data[pos + 4:pos + 4 + n]
        pos += 4 + n
        out += block if flag else snappy_decompress(block)
    return bytes(out)

def iwa_encode(payload):
    """Re-frame a payload into IWA chunks (64 KB uncompressed each)."""
    out = bytearray()
    for i in range(0, len(payload), 0x10000):
        block = snappy_compress(payload[i:i + 0x10000])
        out += bytes([0]) + len(block).to_bytes(3, "little") + block
    return bytes(out)

# -- archive framing -------------------------------------------------------
def archives(payload):
    """-> [(info_bytes, [(msg_type, msg_bytes)])] in file order."""
    out, pos = [], 0
    while pos < len(payload):
        n, pos = read_varint(payload, pos)
        info_bytes = payload[pos:pos + n]; pos += n
        msgs = []
        for num, wire, val in tokenize(info_bytes):
            if num != 2:
                continue
            mt, length = None, 0
            for n2, w2, v2 in tokenize(val):
                if n2 == 1:
                    mt = read_varint(v2, 0)[0]
                elif n2 == 3:
                    length = read_varint(v2, 0)[0]
            msgs.append([mt, payload[pos:pos + length]])
            pos += length
        out.append([info_bytes, msgs])
    return out

def pack_archives(arcs):
    """Inverse of archives(), rewriting each MessageInfo length."""
    out = bytearray()
    for info_bytes, msgs in arcs:
        toks, k = tokenize(info_bytes), 0
        new = []
        for num, wire, val in toks:
            if num == 2:
                inner = [(n2, w2, write_varint(len(msgs[k][1])) if n2 == 3 else v2)
                         for n2, w2, v2 in tokenize(val)]
                val = emit(inner)
                k += 1
            new.append((num, wire, val))
        packed = emit(new)
        out += write_varint(len(packed)) + packed
        for _, msg in msgs:
            out += msg
    return bytes(out)
