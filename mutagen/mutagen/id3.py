#
# id3 support for mutagen
# Copyright (C) 2005  Michael Urman
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of version 2 of the GNU General Public License as
# published by the Free Software Foundation.
#
# $Id$
#

__all__ = ['ID3', 'Frames', 'Open']

import mutagen
from struct import unpack
from mmap import mmap

PRINT_ERRORS = True

class ID3NoHeaderError(ValueError): pass
class ID3UnsupportedVersionError(NotImplementedError): pass

class ID3(mutagen.Metadata):
    """ID3 is the mutagen.ID3 metadata class.

    It accepts a filename and a dictionary of frameid to frame handlers.
    """

    PEDANTIC = True

    def __init__(self, filename=None, known_frames=None):
        if known_frames is None: known_frames = Frames
        self.unknown_frames = []
        self.__known_frames = known_frames
        self.__filename = None
        self.__flags = 0
        self.__size = 0
        self.__readbytes = 0
        self.__padding = 0
        self.__crc = None

        if filename is not None:
            self.load(filename)

    def fullread(self, size):
        data = self.__fileobj.read(size)
        if len(data) != size: raise EOFError
        self.__readbytes += size
        return data

    def load(self, filename):
        self.__filename = filename
        self.__fileobj = file(filename, 'rb')
        try:
            try:
                self.load_header()
            except EOFError:
                from os.path import getsize
                raise ID3NoHeaderError("%s: too small (%d bytes)" %(
                    filename, getsize(filename)))
            except (ID3NoHeaderError, ID3UnsupportedVersionError), err:
                import sys
                stack = sys.exc_traceback
                try: self.__fileobj.seek(-128, 2)
                except EnvironmentError: raise err, None, stack
                else:
                    frames = ParseID3v1(self.__fileobj.read(128))
                    if frames is not None:
                        map(self.loaded_frame, frames.keys(), frames.values())
                    else: raise err, None, stack
            else:
                while self.__readbytes+self.__padding+10 < self.__size:
                    try:
                        name, tag = self.load_frame(frames=self.__known_frames)
                    except EOFError: break

                    if name != '\x00\x00\x00\x00':
                        if isinstance(tag, Frame):
                            self.loaded_frame(name, tag)
                        else:
                            self.unknown_frames.append([name, tag])
        finally:
            self.__fileobj.close()
            del self.__fileobj

    def loaded_frame(self, name, tag):
        if name == 'TXXX' or name == 'WXXX':
            name += ':' + tag.desc
        self[name] = tag

    def load_header(self):
        fn = self.__filename
        data = self.fullread(10)
        id3, vmaj, vrev, flags, size = unpack('>3sBBB4s', data)
        self.__flags = flags
        self.__size = BitPaddedInt(size)
        self.version = (2, vmaj, vrev)

        if id3 != 'ID3':
            raise ID3NoHeaderError("'%s' doesn't start with an ID3 tag" % fn)
        if vmaj not in [3, 4]:
            raise ID3UnsupportedVersionError("'%s' ID3v2.%d not supported"
                    % (fn, vmaj))

        if self.PEDANTIC:
            if (2,4,0) <= self.version and (flags & 0x0f):
                raise ValueError("'%s' has invalid flags %#02x" % (fn, flags))
            elif (2,3,0) <= self.version and (flags & 0x1f):
                raise ValueError("'%s' has invalid flags %#02x" % (fn, flags))


        if self.f_extended:
            self.__extsize = BitPaddedInt(self.fullread(4))
            self.__extdata = self.fullread(self.__extsize - 4)

    def load_frame(self, frames):
        data = self.fullread(10)
        name, size, flags = unpack('>4s4sH', data)
        size = BitPaddedInt(size)
        if name == '\x00\x00\x00\x00': return name, None
        if size == 0: return name, data
        framedata = self.fullread(size)
        try: tag = frames[name]
        except KeyError:
            return name, data + framedata
        else:
            if self.f_unsynch or flags & 0x40:
                framedata = unsynch.decode(framedata)
            tag = tag.fromData(self, flags, framedata)
        return name, tag

    f_unsynch = property(lambda s: bool(s.__flags & 0x80))
    f_extended = property(lambda s: bool(s.__flags & 0x40))
    f_experimental = property(lambda s: bool(s.__flags & 0x20))
    f_footer = property(lambda s: bool(s.__flags & 0x10))

    #f_crc = property(lambda s: bool(s.__extflags & 0x8000))

class BitPaddedInt(int):
    def __new__(cls, value, bits=7, bigendian=True):
        mask = (1<<(bits))-1
        if isinstance(value, str):
            bytes = [ord(byte) & mask for byte in value]
            if bigendian: bytes.reverse()
            numeric_value = 0
            for shift, byte in zip(range(0, len(bytes)*bits, bits), bytes):
                numeric_value += byte << shift
            return super(BitPaddedInt, cls).__new__(cls, numeric_value)
        else:
            return super(BitPaddedInt, cls).__new__(cls, value)

    def __init__(self, value, bits=7, bigendian=True):
        self.bits = bits
        self.bigendian = bigendian
        return super(BitPaddedInt, self).__init__(value)
    
    def as_str(value, bits=7, bigendian=True, width=4):
        bits = getattr(value, 'bits', bits)
        bigendian = getattr(value, 'bigendian', bigendian)
        value = int(value)
        mask = (1<<bits)-1
        bytes = []
        while value:
            bytes.append(value & mask)
            value = value >> bits
        for i in range(len(bytes), width): bytes.append(0)
        if len(bytes) != width:
            raise ValueError, 'Value too wide (%d bytes)' % len(bytes)
        if bigendian: bytes.reverse()
        return ''.join(map(chr, bytes))
    to_str = staticmethod(as_str)

class unsynch(object):
    def decode(value):
        output = []
        safe = True
        append = output.append
        for val in value:
            if safe:
                append(val)
                safe = val != '\xFF'
            else:
                if val != '\x00': raise ValueError('invalid sync-safe string')
                safe = True
        if not safe: raise ValueError('string ended unsafe')
        return ''.join(output)
    decode = staticmethod(decode)

    def encode(value):
        output = []
        safe = True
        append = output.append
        for val in value:
            if safe:
                append(val)
                if val == '\xFF': safe = False
            elif val == '\x00' or val >= '\xE0':
                append('\x00')
                append(val)
                safe = val != '\xFF'
            else:
                append(val)
                safe = True
        if not safe: append('\x00')
        return ''.join(output)
    encode = staticmethod(encode)

class Spec(object):
    def __init__(self, name): self.name = name

class ByteSpec(Spec):
    def read(self, frame, data): return ord(data[0]), data[1:]
    def write(self, frame, value): return chr(value)
    def validate(self, frame, value): return value

class EncodingSpec(ByteSpec):
    def read(self, frame, data):
        enc, data = super(EncodingSpec, self).read(frame, data)
        if enc < 16: return enc, data
        else: return 0, chr(enc)+data

    def validate(self, frame, value):
        if 0 <= value <= 3: return value
        if value is None: return None
        raise ValueError('%s: invalid encoding' % value)

class LanguageSpec(Spec):
    def read(self, frame, data): return data[:3], data[3:]
    def write(self, frame, value): return str(value)
    def validate(self, frame, value):
        if value is None: return None
        if isinstance(value, basestring) and len(value) == 3: return value
        raise ValueError('%s: invalid language' % value)

class BinaryDataSpec(Spec):
    def read(self, frame, data): return data, ''
    def write(self, frame, value): return str(value)
    def validate(self, frame, value): return str(value)

class EncodedTextSpec(Spec):
    encodings = [ ('latin1', '\x00'), ('utf16', '\x00\x00'),
                  ('utf16be', '\x00\x00'), ('utf8', '\x00') ]

    def read(self, frame, data):
        enc, term = self.encodings[frame.encoding]
        ret = ''
        if len(term) == 1:
            if term in data:
                data, ret = data.split(term, 1)
        else:
            offset = -1
            try:
                while True:
                    offset = data.index(term, offset+1)
                    if offset & 1: continue
                    data, ret = data[0:offset], data[offset+2:]; break
            except ValueError: pass

        return data.decode(enc), ret

    def write(self, frame, value):
        enc, term = self.encodings[frame.encoding]
        return value.encode(enc) + term

    def validate(self, frame, value): return unicode(value)

class EncodedMultiTextSpec(EncodedTextSpec):
    def read(self, frame, data):
        values = []
        while 1:
            value, data = super(EncodedMultiTextSpec, self).read(frame, data)
            values.append(value)
            if not data: break
        return values, data

    def write(self, frame, value):
        return super(EncodedMultiTextSpec, self).write(frame, u'\u0000'.join(value))
    def validate(self, frame, value):
        enc, term = self.encodings[frame.encoding or 0]
        if value is None: return []
        if isinstance(value, list): return value
        if isinstance(value, str): return value.decode(enc).split(u'\u0000')
        if isinstance(value, unicode): return value.split(u'\u0000')
        raise ValueError

class MultiSpec(Spec):
    def __init__(self, name, *specs):
        super(MultiSpec, self).__init__(name)
        self.specs = specs

    def read(self, frame, data):
        values = []
        while data:
            record = []
            for spec in self.specs:
                value, data = spec.read(frame, data)
                record.append(value)
            if len(self.specs) != 1: values.append(record)
            else: values.append(record[0])
        return values, data

    def write(self, frame, value):
        data = []
        if len(self.specs) == 1:
            for v in value:
                data.append(self.specs[0].write(frame, v))
        else:
            for record in value:
                for v, s in zip(record, self.specs):
                    data.append(s.write(frame, v))
        return ''.join(data)

    def validate(self, frame, value):
        if value is None: return []
        if isinstance(value, list): return value
        raise ValueError

class EncodedNumericTextSpec(EncodedTextSpec): pass
class EncodedNumericPartTextSpec(EncodedTextSpec): pass

class Latin1TextSpec(EncodedTextSpec):
    def read(self, frame, data):
        if '\x00' in data: data, ret = data.split('\x00',1)
        else: ret = ''
        return data.decode('latin1'), ret

    def write(self, data, value):
        return value.encode('latin1') + '\x00'

    def validate(self, frame, value): return unicode(value)

class Frame(object):
    FLAG23_ALTERTAG     = 0x8000
    FLAG23_ALTERFILE    = 0x4000
    FLAG23_READONLY     = 0x2000
    FLAG23_COMPRESS     = 0x0080
    FLAG23_ENCRYPT      = 0x0040
    FLAG23_GROUP        = 0x0020

    FLAG24_ALTERTAG     = 0x4000
    FLAG24_ALTERFILE    = 0x2000
    FLAG24_READONLY     = 0x1000
    FLAG24_GROUPID      = 0x0040
    FLAG24_COMPRESS     = 0x0008
    FLAG24_ENCRYPT      = 0x0004
    FLAG24_UNSYNC       = 0x0002
    FLAG24_DATALEN      = 0x0001

    def __init__(self, *args, **kwargs):
        for checker, val in zip(self._framespec, args):
            setattr(self, checker.name, checker.validate(self, val))
        for checker in self._framespec[len(args):]:
            validated = checker.validate(self, kwargs.get(checker.name, None))
            setattr(self, checker.name, validated)

    def __repr__(self):
        kw = []
        for attr in self._framespec:
            kw.append('%s=%r' % (attr.name, getattr(self, attr.name)))
        return '%s(%s)' % (type(self).__name__, ', '.join(kw))

    def _readData(self, data):
        odata = data
        for reader in self._framespec:
            value, data = reader.read(self, data)
            setattr(self, reader.name, value)
        if data.strip('\x00'):
            if PRINT_ERRORS: print 'Leftover data: %s: %r (from %r)' % (
                    type(self).__name__, data, odata)

    def _writeData(self):
        data = []
        for writer in self._framespec:
            data.append(writer.write(self, getattr(self, writer.name)))

    def fromData(cls, id3, tflags, data):

        if (2,4,0) <= id3.version:
            if tflags & Frame.FLAG24_UNSYNC and not id3.f_unsynch:
                data = unsynch.decode(data)
            if tflags & Frame.FLAG24_ENCRYPT:
                raise ID3EncryptionUnsupportedError
            if tflags & Frame.FLAG24_COMPRESS:
                data = data.decode('zlib')

        elif (2,3,0) <= id3.version:
            if tflags & Frame.FLAG24_ENCRYPT:
                raise ID3EncryptionUnsupportedError
            if tflags & Frame.FLAG23_COMPRESS:
                data = data.decode('zlib')

        frame = cls()
        frame._rawdata = data
        frame._flags = tflags
        frame._readData(data)
        return frame
    fromData = classmethod(fromData)

class TextFrame(Frame):
    _framespec = [ EncodingSpec('encoding'), EncodedTextSpec('text') ]
    def __str__(self): return self.text.encode('utf-8')
    def __unicode__(self): return self.text
    def __eq__(self, other): return self.text == other

class NumericTextFrame(TextFrame):
    _framespec = [ EncodingSpec('encoding'), EncodedNumericTextSpec('text') ]
    def __pos__(self): return int(self.text)

class NumericPartTextFrame(TextFrame):
    _framespec = [ EncodingSpec('encoding'),
        EncodedNumericPartTextSpec('text') ]
    def __pos__(self):
        t = self.text
        return int('/' in t and t[:t.find('/')] or t)

class MultiTextFrame(TextFrame):
    _framespec = [ EncodingSpec('encoding'), EncodedMultiTextSpec('text') ]
    def __str__(self): return '\u0000'.join(self.text).encode('utf-8')
    def __unicode__(self): return u'\u0000'.join(self.text)
    def __eq__(self, other):
        if isinstance(other, str): return str(self) == other
        elif isinstance(other, unicode): return u'\u0000'.join(self.text) == other
        return self.text == other
    def __getitem__(self, item): return self.text[item]
    def __iter__(self): return iter(self.text)
    def append(self, value): return self.text.append(value)
    def extend(self, value): return self.text.extend(value)

class UrlFrame(Frame):
    _framespec = [ Latin1TextSpec('url') ]
    def __str__(self): return self.url.encode('utf-8')
    def __unicode__(self): return self.url
    def __eq__(self, other): return self.url == other

class TALB(TextFrame): "Album"
class TBPM(NumericTextFrame): "Beats per minute"
class TCOM(MultiTextFrame): "Composer"
class TCON(MultiTextFrame): "Content type (Genre)"
class TCOP(MultiTextFrame): "Copyright"
class TDAT(MultiTextFrame): "Date of recording (DDMM)"
class TDLY(NumericTextFrame): "Audio Delay (ms)"
class TENC(MultiTextFrame): "Encoder"
class TEXT(MultiTextFrame): "Lyricist"
class TFLT(MultiTextFrame): "File type"
class TIME(MultiTextFrame): "Time of recording (HHMM)"
class TIT1(MultiTextFrame): "Content group description"
class TIT2(MultiTextFrame): "Title"
class TIT3(MultiTextFrame): "Subtitle/Description refinement"
class TKEY(MultiTextFrame): "Starting Key"
class TLAN(MultiTextFrame): "Audio Languages"
class TLEN(NumericTextFrame): "Audio Length (ms)"
class TMED(MultiTextFrame): "Original Media"
class TOAL(MultiTextFrame): "Original Album"
class TOFN(MultiTextFrame): "Original Filename"
class TOLY(MultiTextFrame): "Original Lyricist"
class TOPE(MultiTextFrame): "Original Artist/Performer"
class TORY(NumericTextFrame): "Original Release Year"
class TOWN(MultiTextFrame): "Owner/Licensee"
class TPE1(MultiTextFrame): "Lead Artist/Performer/Soloist/Group"
class TPE2(MultiTextFrame): "Band/Orchestra/Accompaniment"
class TPE3(MultiTextFrame): "Conductor"
class TPE4(MultiTextFrame): "Interpreter/Remixer/Modifier"
class TPOS(NumericPartTextFrame): "Track Number"
class TPUB(MultiTextFrame): "Publisher"
class TRCK(NumericPartTextFrame): "Track Number"
class TRDA(MultiTextFrame): "Recording Dates"
class TRSN(MultiTextFrame): "Internet Radio Station Name"
class TRSO(MultiTextFrame): "Internet Radio Station Owner"
class TSIZ(NumericTextFrame): "Size of audio data (bytes)"
class TSRC(MultiTextFrame): "International Standard Recording Code (ISRC)"
class TSSE(MultiTextFrame): "Encoder settings"
class TYER(NumericTextFrame): "Year of recording"

class TXXX(TextFrame):
    "User-defined Text"
    _framespec = [ EncodingSpec('encoding'), EncodedTextSpec('desc'),
        EncodedTextSpec('text') ]

class WCOM(UrlFrame): "Commercial Information"
class WCOP(UrlFrame): "Copyright Information"
class WOAF(UrlFrame): "Official File Information"
class WOAS(UrlFrame): "Official Source Information"
class WORS(UrlFrame): "Official Internet Radio Information"
class WPAY(UrlFrame): "Payment Information"
class WPUB(UrlFrame): "Official Publisher Information"

class WXXX(UrlFrame):
    "User-defined URL"
    _framespec = [ EncodingSpec('encoding'), EncodedTextSpec('desc'),
        Latin1TextSpec('url') ]

class IPLS(Frame):
    "Involved People List"
    _framespec = [ EncodingSpec('encoding'), MultiSpec('people',
            EncodedTextSpec('involvement'), EncodedTextSpec('person')) ]
    def __eq__(self, other):
        return self.people == other

class MCDI(Frame):
    "Binary dump of CD's TOC"
    _framespec = [ BinaryDataSpec('data') ]
    def __eq__(self, other): return self.data == other

# class ETCO: unsupported
# class MLLT: unsupported
# class SYTC: unsupported
# class USLT: unsupported
# class SYLT: unsupported

class COMM(TextFrame):
    "User comment"
    _framespec = [ EncodingSpec('encoding'), LanguageSpec('lang'),
        EncodedTextSpec('desc'), EncodedTextSpec('text') ]
        
# class RVAD: unsupported
# class EQUA: unsupported
# class RVRB: unsupported

class APIC(Frame):
    "Attached (or linked) Picture"
    _framespec = [ EncodingSpec('encoding'), Latin1TextSpec('mime'),
        ByteSpec('type'), EncodedTextSpec('desc'), BinaryDataSpec('data') ]
    def __eq__(self, other): return self.data == other

# class GEOB: unsupported
# class PCNT: unsupported
# class POPM: unsupported
# class GEOB: unsupported
# class RBUF: unsupported
# class AENC: unsupported
# class LINK: unsupported
# class POSS: unsupported

class USER(TextFrame):
    "Terms of use"
    _framespec = [ EncodingSpec('encoding'), LanguageSpec('lang'),
        EncodedTextSpec('text') ]

# class OWNE: unsupported
# class COMR: unsupported
# class ENCR: unsupported
# class GRID: unsupported
# class PRIV: unsupported

Frames = dict([(k,v) for (k,v) in globals().items()
        if len(k)==4 and isinstance(v, type) and issubclass(v, Frame)])

# support open(filename) as interface
Open = ID3

# ID3v1.1 support.
def ParseID3v1(string):
    from struct import error as StructError
    frames = {}
    try:
        tag, title, artist, album, year, comment, track, genre = unpack(
            "3s30s30s30s4s29sbb", string)
    except StructError: return None

    if tag != "TAG": return None
    title = title.strip("\x00").strip().decode('latin1')
    artist = artist.strip("\x00").strip().decode('latin1')
    album = album.strip("\x00").strip().decode('latin1')
    year = year.strip("\x00").strip().decode('latin1')
    comment = comment.strip("\x00").strip().decode('latin1')

    if title: frames["TIT2"] = TIT2(encoding=0, text=title)
    if artist: frames["TPE1"] = TPE1(encoding=0, text=[artist])
    if album: frames["TALB"] = TALB(encoding=0, text=album)
    # FIXME: Needs to be TDAT if 2.4 was requested (if we have a way
    # to request tag versions).
    if year: frames["TYER"] = TYER(encoding=0, text=year)
    if comment: frames["COMM"] = COMM(
        encoding=0, lang="eng", desc="ID3v1 Comment", text=comment)
    if track: frames["TRCK"] = TRCK(encoding=0, text=str(track))
    return frames
