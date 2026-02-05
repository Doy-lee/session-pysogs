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
    Dict[Union[bytes, str], "bt_value"],
]

@dataclasses.dataclass
class MessageRequest:
    data_size:    int
    filtered:     bool
    is_mod:       bool
    message_data: bytes
    room_id:      int
    room_name:    str
    room_token:   bytes
    session_id:   SessionID  # 25-blinded x25519 pubkey as raw 33 bytes
    sig:          bytes
    user_id:      int
    whisper_mods: bool
    alt_id:       Optional[SessionID] = None # 15-blinded x25519 pubkey as raw 33 bytes
    whisper_to:   Optional[int]       = None # User ID that this message was whispered to

    @classmethod
    def from_dict(cls, src: Dict[bytes, bt_value]):
        result = MessageRequest(data_size    = typing.cast(int,   src[b'data_size']),
                                filtered     = typing.cast(bool,  src[b'filtered']),
                                is_mod       = typing.cast(bool,  src[b'is_mod']),
                                message_data = typing.cast(bytes, src[b'message_data']),
                                room_id      = typing.cast(int,   src[b'room_id']),
                                room_name    = typing.cast(bytes, src[b'room_name']).decode('utf-8'),
                                room_token   = typing.cast(bytes, src[b'room_token']),
                                session_id   = bytes.fromhex(typing.cast(bytes, src[b'session_id']).decode('utf-8')),
                                sig          = bytes.fromhex(typing.cast(bytes, src[b'sig']).decode('utf-8')),
                                user_id      = typing.cast(int,   src[b'user_id']),
                                whisper_mods = typing.cast(bool,  src[b'whisper_mods']))

        if b"alt_id" in src:
            assert isinstance(src[b"alt_id"], bytes)
            result.alt_id = bytes.fromhex(src[b'alt_id'].decode('utf-8'))

        if b"whisper_to" in src:
            assert isinstance(src[b"whisper_to"], int)
            result.whisper_to = src[b"whisper_to"]

        return result

    def to_dict(self) -> Dict[bytes, bt_value]:
        result: Dict[bytes, bt_value] = {b'data_size':     self.data_size,
                                         b'filtered':      int(self.filtered),
                                         b'is_mod':        int(self.is_mod),
                                         b'message_data':  self.message_data,
                                         b'room_id':       self.room_id,
                                         b'room_name':     self.room_name.encode('utf-8'),
                                         b'room_token':    self.room_token,
                                         b'session_id':    self.session_id.hex().encode('utf-8'),
                                         b'sig':           self.sig.hex().encode('utf-8'),
                                         b'user_id':       self.user_id,
                                         b'whisper_mods':  int(self.whisper_mods)}
        if self.alt_id:
            result[b"alt_id"] = self.alt_id
        if self.whisper_to:
            result[b"whisper_to"] = self.whisper_to
        return result

    def to_bencode(self) -> bytes:
        d      = self.to_dict()
        result = oxenc.bt_serialize(d)
        return result
