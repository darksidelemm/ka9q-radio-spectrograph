"""ka9q-radio spectrum protocol and indexed binary archive (Python 3.10+)."""
import ipaddress
import json
import math
import secrets
import socket
import sqlite3
import struct
import time
from contextlib import closing

import numpy as np

# Wire IDs from references/ka9q-radio/src/status.h; never renumber.
COMMAND_TAG = 1
OUTPUT_SSRC = 18
RADIO_FREQUENCY = 33
DEMOD_TYPE = 48
RESOLUTION_BW = 93
SPECTRUM_SHAPE = 91
BIN_COUNT = 94
BIN_DATA = 96
WINDOW_TYPE = 14
SPECTRUM_AVG = 12
LIFETIME = 117
RF_LEVEL_CAL = 110
NOISE_BW = 15
INPUT_SAMPRATE = 10
FE_LOW_EDGE = 100
FE_HIGH_EDGE = 101
FIRST_LO_FREQUENCY = 34
IF_POWER = 45  # A/D input level, already encoded as dBFS by radiod
RF_ATTEN = 97
RF_GAIN = 98  # RX888 VGA gain

# enum window_type in ka9q-radio/src/window.h
WINDOW_TYPES = {'kaiser': 0, 'rectangular': 1, 'hann': 5, 'hamming': 6, 'blackman-harris': 7}
KAISER_BETA = 7.0


def tlv(kind, value):
    size = len(value)
    length = bytes([size]) if size < 128 else b'\x82' + size.to_bytes(2, 'big')
    return bytes([kind]) + length + value


def integer(kind, value):
    return tlv(kind, value.to_bytes(max(1, (value.bit_length()+7)//8), 'big'))


def floating(kind, value, double=False):
    return tlv(kind, struct.pack('>d' if double else '>f', value))


def fields(packet):
    """Decode bounded TLVs, including extended lengths and compressed scalars."""
    result = {}
    pos = 1
    while pos < len(packet):
        kind = packet[pos]
        pos += 1
        if kind == 0:
            return result
        if pos >= len(packet):
            raise ValueError('truncated TLV length')
        size = packet[pos]
        pos += 1
        if size & 128:
            count = size & 127
            if not 1 <= count <= 4 or pos + count > len(packet):
                raise ValueError('invalid extended TLV length')
            size = int.from_bytes(packet[pos:pos+count], 'big')
            pos += count
        if pos + size > len(packet):
            raise ValueError('truncated TLV payload')
        result[kind] = packet[pos:pos+size]
        pos += size
    raise ValueError('missing TLV terminator')


def number(data, double=False):
    width = 8 if double else 4
    if len(data) > width:
        raise ValueError('invalid floating point width')
    return struct.unpack('>d' if double else '>f', data.rjust(width, b'\0'))[0]


class Radio:
    def __init__(self, host, port=5006, interface='0.0.0.0', timeout=3):
        self.group = socket.gethostbyname(host)
        if not ipaddress.ip_address(self.group).is_multicast:
            raise ValueError(f'{host} resolved to {self.group}, which is not multicast')
        self.timeout = timeout
        self.ssrc = secrets.randbelow(0xfffffffe) + 1
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4*1024*1024)
            self.sock.bind(('', port))
            self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                                 socket.inet_aton(self.group)+socket.inet_aton(interface))
            self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(interface))
            self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
            self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        except Exception:
            self.sock.close()
            raise
        self.destination = (self.group, port)

    def close(self):
        self.sock.close()

    def request(self, settings=b''):
        tag = secrets.randbits(32)
        packet = (b'\x01' + integer(OUTPUT_SSRC, self.ssrc) + integer(COMMAND_TAG, tag)
                  + settings + b'\0')
        self.sock.sendto(packet, self.destination)
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            self.sock.settimeout(max(.001, deadline-time.monotonic()))
            try:
                data, source = self.sock.recvfrom(65535)
            except socket.timeout:
                break
            if not data or data[0] != 0:
                continue
            try:
                result = fields(data)
            except ValueError:
                continue
            if (int.from_bytes(result.get(OUTPUT_SSRC, b''), 'big') == self.ssrc
                    and int.from_bytes(result.get(COMMAND_TAG, b''), 'big') == tag):
                return result, source[0]
        raise TimeoutError('No matching radiod response; check hf.local, interface, multicast and radiod data= configuration')


def capture(args):
    window_type = WINDOW_TYPES[args.window]
    count = math.floor((args.max_mhz-args.min_mhz)*1e6/args.rbw) + 1
    # Even count keeps the float-vector FFT rotation unambiguous; do not exceed upper limit.
    count -= count % 2
    if not 2 <= count <= 14000:
        raise ValueError('range/RBW must give 2..14000 bins (UDP float-vector limit); narrow the range or increase RBW')
    center = args.min_mhz*1e6 + (count//2)*args.rbw
    settings = (integer(DEMOD_TYPE, 3) + floating(RADIO_FREQUENCY, center, True)
                + floating(RESOLUTION_BW, args.rbw) + integer(BIN_COUNT, count)
                + integer(WINDOW_TYPE, window_type) + integer(SPECTRUM_AVG, 1)
                + integer(LIFETIME, 500))
    if args.window == 'kaiser':
        settings += floating(SPECTRUM_SHAPE, KAISER_BETA)
    with closing(Radio(args.radio, args.port, args.interface, args.timeout)) as radio:
        # Initial response can predate FFT setup. Wait and poll before recording.
        radio.request(settings)
        time.sleep(args.settle)
        samples = []
        receiver_samples = []
        started = time.time()
        deadline = time.monotonic() + args.duration
        metadata = None
        while time.monotonic() < deadline:
            tick = time.monotonic()
            status, source = radio.request()
            if (int.from_bytes(status.get(DEMOD_TYPE, b''), 'big') != 3
                    or WINDOW_TYPE not in status
                    or int.from_bytes(status[WINDOW_TYPE], 'big') != window_type
                    or int.from_bytes(status.get(BIN_COUNT, b''), 'big') != count
                    or not math.isclose(number(status.get(RESOLUTION_BW, b'')), args.rbw, rel_tol=1e-5)
                    or not math.isclose(number(status.get(RADIO_FREQUENCY, b''), True), center, abs_tol=1)):
                raise ValueError('radiod returned unexpected spectrum settings')
            if args.window == 'kaiser' and (
                    SPECTRUM_SHAPE not in status or not math.isclose(
                        number(status[SPECTRUM_SHAPE]), KAISER_BETA, abs_tol=1e-5)):
                raise ValueError('radiod returned unexpected Kaiser beta')
            if RF_LEVEL_CAL not in status:
                raise ValueError('radiod did not report RF_LEVEL_CAL; cannot label uncalibrated powers dBm')
            lo = number(status[FIRST_LO_FREQUENCY], True)
            if not (lo + number(status[FE_LOW_EDGE]) <= args.min_mhz*1e6
                    and center + (count//2-1)*args.rbw <= lo + number(status[FE_HIGH_EDGE])):
                raise ValueError('requested frequencies extend beyond the receiver passband')
            power = np.frombuffer(status.get(BIN_DATA, b''), dtype='>f4')
            if power.size != count or not np.all(np.isfinite(power)) or np.any(power <= 0):
                raise ValueError('missing, zero or invalid spectrum bins; check receiver coverage and radiod version')
            samples.append(np.fft.fftshift(power).astype(np.float32))
            receiver = {'elapsed_seconds': time.time() - started}
            for field, name in ((IF_POWER, 'ad_level_dbfs'),
                                (RF_ATTEN, 'attenuation_db'), (RF_GAIN, 'vga_gain_db')):
                if field not in status:
                    raise ValueError(f'radiod did not report {name}')
                value = number(status[field])
                if not math.isfinite(value):
                    raise ValueError(f'radiod reported non-finite {name}')
                receiver[name] = value
            receiver_samples.append(receiver)
            current = {'radio': args.radio, 'source': source, 'first_hz': args.min_mhz*1e6,
                       'rbw_hz': args.rbw, 'bins': count, 'window': args.window,
                       'noise_bw_hz': number(status[NOISE_BW]),
                       'rf_level_cal': number(status[RF_LEVEL_CAL]), 'offset_db': args.offset_db,
                       'units': 'dBm', 'poll_seconds': args.interval}
            if args.window == 'kaiser':
                current['kaiser_beta'] = KAISER_BETA
            if metadata is not None and current != metadata:
                raise ValueError('receiver calibration or spectrum metadata changed during capture')
            metadata = current
            remaining = deadline-time.monotonic()
            if remaining > 0:
                time.sleep(min(remaining, max(0, args.interval-(time.monotonic()-tick))))
        if len(samples) < 2:
            raise ValueError('capture needs at least two spectra; increase duration')
        powers = np.stack(samples)
        median = 10*np.log10(np.median(powers, axis=0)) + args.offset_db
        peak = 10*np.log10(np.max(powers, axis=0)) + args.offset_db
        metadata['receiver_samples'] = receiver_samples
        return started, time.time()-started, len(samples), metadata, median, peak


def connect(path):
    db = sqlite3.connect(path, timeout=30)
    db.execute('''CREATE TABLE IF NOT EXISTS captures (
        id INTEGER PRIMARY KEY, started REAL NOT NULL, duration REAL NOT NULL,
        spectra INTEGER NOT NULL, metadata TEXT NOT NULL,
        median BLOB NOT NULL, peak BLOB NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS captures_started ON captures(started)')
    return db


def append(path, result):
    start, duration, spectra, metadata, median, peak = result
    with closing(connect(path)) as db, db:
        db.execute('INSERT INTO captures(started,duration,spectra,metadata,median,peak) VALUES (?,?,?,?,?,?)',
                   (start, duration, spectra, json.dumps(metadata, sort_keys=True),
                    median.astype('<f4').tobytes(), peak.astype('<f4').tobytes()))


def read_since(path, since):
    """Stream rows in UTC epoch order; only requested rows/arrays enter memory."""
    with closing(sqlite3.connect(f'file:{path}?mode=ro', uri=True)) as db:
        for start, duration, spectra, metadata, median, peak in db.execute(
                'SELECT started,duration,spectra,metadata,median,peak FROM captures WHERE started>=? ORDER BY started', (since,)):
            yield (start, duration, spectra, json.loads(metadata),
                   np.frombuffer(median, dtype='<f4').copy(), np.frombuffer(peak, dtype='<f4').copy())
