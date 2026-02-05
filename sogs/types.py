import dataclasses
import typing
import oxenc

from typing import Dict, List, Union, Optional

# Represents the Session account as bytes, i.e. 1b Blinding Prefix + 32b X25519 Public Key
# This is not to be confused with the value of the Session ID that is typically returned from the
# SOGS server OMQ response which is the hex representation of the Session account but held in a
# bytes object,
# e.g.
#   OMQ Session ID response => b"15aaaa.."       (66 bytes)
#   `SessionID`             => b"\x15\xaa\xaa.." (33 bytes)
SessionID  = bytes
RoomToken  = bytes
TimestampS = float
MessageID  = int

# Represents the different variants of data types that a primitive bencoded type can hold. These
# values are produced and consumed by the module oxenc's bt_serialize/bt_deserialize functions.
bt_value = Union[
    int,
    bytes,
    str,
    List["bt_value"],
    List[int], # Covered by bt_value, but LSP still gets confused
    Dict[Union[bytes, str], "bt_value"],
]

@dataclasses.dataclass
class MessageInsert:
    unpadded_data:    bytes
    padded_data_size: int
    filtered:         bool
    user_id:          int
    whisper_mods:     bool
    sig:              bytes
    alt_id:           Optional[SessionID] = None # 15-blinded x25519 pubkey as raw 33 bytes
    whisper_to:       Optional[int]       = None # User ID that this message was whispered to

@dataclasses.dataclass
class RoomAddPostRequest:
    """When a message is posted to a room, a request describing the post to be added is created with
    this structure and relayed to plugins for running the pre/post message hooks with this data"""
    room_id:      int
    room_token:   bytes
    room_name:    str
    user_id:      int
    session_id:   SessionID
    message_data: bytes
    data_size:    int
    sig:          bytes
    filtered:     bool
    is_mod:       bool
    whisper_mods: bool
    whisper_to:   Optional[int]       = None # User ID that this message was whispered to
    alt_id:       Optional[SessionID] = None # 15-blinded x25519 pubkey as raw 33 bytes

    @staticmethod
    def from_dict(src: Dict[bytes, bt_value]) -> "RoomAddPostRequest":
        result = RoomAddPostRequest(room_id      = typing.cast(int,   src[b'room_id']),
                                    room_name    = typing.cast(bytes, src[b'room_name']).decode('utf-8'),
                                    room_token   = typing.cast(bytes, src[b'room_token']),
                                    user_id      = typing.cast(int,   src[b'user_id']),
                                    session_id   = bytes.fromhex(typing.cast(bytes, src[b'session_id']).decode('utf-8')),
                                    message_data = typing.cast(bytes, src[b'message_data']),
                                    data_size    = typing.cast(int,   src[b'data_size']),
                                    sig          = bytes.fromhex(typing.cast(bytes, src[b'sig']).decode('utf-8')),
                                    filtered     = typing.cast(bool,  src[b'filtered']),
                                    is_mod       = typing.cast(bool,  src[b'is_mod']),
                                    whisper_mods = typing.cast(bool,  src[b'whisper_mods']))

        if b"alt_id" in src:
            assert isinstance(src[b"alt_id"], bytes)
            result.alt_id = src[b'alt_id']

        if b"whisper_to" in src:
            assert isinstance(src[b"whisper_to"], int)
            result.whisper_to = src[b"whisper_to"]

        return result

    @staticmethod
    def from_bencode(data: Union[bytes, memoryview]) -> "RoomAddPostRequest":
        d: Dict[bytes, bt_value] = oxenc.bt_deserialize(data)
        result                   = RoomAddPostRequest.from_dict(d)
        return result

    def to_dict(self) -> Dict[bytes, bt_value]:
        result: Dict[bytes, bt_value] = {b'room_id':          self.room_id,
                                         b'room_name':        self.room_name.encode('utf-8'),
                                         b'room_token':       self.room_token,
                                         b'user_id':          self.user_id,
                                         b'session_id':       self.session_id.hex().encode('utf-8'),
                                         b'message_data':     self.message_data,
                                         b'data_size':        self.data_size,
                                         b'sig':              self.sig.hex().encode('utf-8'),
                                         b'filtered':         int(self.filtered),
                                         b'is_mod':           int(self.is_mod),
                                         b'whisper_mods':     int(self.whisper_mods)}
        if self.alt_id:
            result[b"alt_id"] = self.alt_id
        if self.whisper_to:
            result[b"whisper_to"] = self.whisper_to
        return result

    def to_bencode(self) -> bytes:
        d      = self.to_dict()
        result = oxenc.bt_serialize(d)
        return result

@dataclasses.dataclass
class PluginInsertMessage:
    """Message insertion request from plugin to SOGS. Minimal set of fields needed for plugin-inserted messages."""
    room_token:       bytes
    session_id:       SessionID  # 25-blinded x25519 pubkey as raw 33 bytes
    message_data:     bytes
    sig:              bytes
    whisper_mods:     bool
    whisper_to:       Optional[int] = None  # User ID that this message was whispered to
    attachment_ids:   List[int]     = dataclasses.field(default_factory=list)
    relay_to_plugins: bool          = False

    @staticmethod
    def from_dict(src: Dict[bytes, bt_value]) -> "PluginInsertMessage":
        result = PluginInsertMessage(
            room_token       = typing.cast(bytes, src[b'room_token']),
            session_id       = bytes.fromhex(typing.cast(bytes, src[b'session_id']).decode('utf-8')),
            message_data     = typing.cast(bytes, src[b'message_data']),
            sig              = bytes.fromhex(typing.cast(bytes, src[b'sig']).decode('utf-8')),
            whisper_mods     = typing.cast(bool, src[b'whisper_mods']),
            relay_to_plugins = typing.cast(bool, src[b'relay_to_plugins'])
        )

        if b"whisper_to" in src:
            assert isinstance(src[b"whisper_to"], int)
            result.whisper_to = src[b"whisper_to"]

        if b"attachment_ids" in src:
            assert isinstance(src[b"attachment_ids"], List)
            result.attachment_ids = typing.cast(List[int], src[b"attachment_ids"])

        return result

    @staticmethod
    def from_bencode(data: Union[bytes, memoryview]) -> "PluginInsertMessage":
        d: Dict[bytes, bt_value] = oxenc.bt_deserialize(data)
        result                   = PluginInsertMessage.from_dict(d)
        return result

    def to_dict(self) -> Dict[bytes, bt_value]:
        result: Dict[bytes, bt_value] = {
            b'room_token':       self.room_token,
            b'session_id':       self.session_id.hex().encode('utf-8'),
            b'message_data':     self.message_data,
            b'sig':              self.sig.hex().encode('utf-8'),
            b'whisper_mods':     int(self.whisper_mods),
            b'relay_to_plugins': int(self.relay_to_plugins),
        }
        if self.whisper_to:
            result[b"whisper_to"] = self.whisper_to
        if len(self.attachment_ids):
            result[b"attachment_ids"] = self.attachment_ids
        return result

    def to_bencode(self) -> bytes:
        d      = self.to_dict()
        result = oxenc.bt_serialize(d)
        return result
