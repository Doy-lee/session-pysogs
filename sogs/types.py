from typing import Any, Dict, List, Optional, Union
import dataclasses
import typing
import oxenc


# Represents the different variants of data types that a primitive bencoded type can hold. These
# values are produced and consumed by the module oxenc's bt_serialize/bt_deserialize functions.
bt_value = Union[
    int,
    float,
    bytes,
    str,
    List["bt_value"],
    List[int], # Covered by bt_value, but LSP still gets confused
    List[str], # Covered by bt_value, but LSP still gets confused
    Dict[Union[bytes, str], "bt_value"],
]
SessionID = bytes
RoomToken = str
MessageID = int
TimestampS = float

@dataclasses.dataclass
class MessageInsert:
    """When a message is posted to a room, a request describing the post to be added is created with
    this structure and relayed to plugins for running the pre/post message hooks with this data"""
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
    room_token:   RoomToken
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
                                    room_token   = typing.cast(bytes, src[b'room_token']).decode('utf-8'),
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
                                         b'room_token':       self.room_token.encode('utf-8'),
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
    room_token:       RoomToken
    session_id:       SessionID  # 25-blinded x25519 pubkey as raw 33 bytes
    message_data:     bytes
    sig:              bytes
    whisper_mods:     bool
    alt_id:           Optional[SessionID] = None  # 15-blinded x25519 pubkey as raw 33 bytes
    whisper_to:       Optional[int]       = None  # User ID that this message was whispered to
    attachment_ids:   List[int]           = dataclasses.field(default_factory=list)
    relay_to_plugins: bool                = False

    @staticmethod
    def from_dict(src: Dict[bytes, bt_value]) -> "PluginInsertMessage":
        result = PluginInsertMessage(
            room_token       = typing.cast(bytes, src[b'room_token']).decode('utf-8'),
            session_id       = bytes.fromhex(typing.cast(bytes, src[b'session_id']).decode('utf-8')),
            message_data     = typing.cast(bytes, src[b'message_data']),
            sig              = bytes.fromhex(typing.cast(bytes, src[b'sig']).decode('utf-8')),
            whisper_mods     = typing.cast(bool, src[b'whisper_mods']),
            relay_to_plugins = typing.cast(bool, src[b'relay_to_plugins'])
        )

        if b"alt_id" in src:
            assert isinstance(src[b"alt_id"], bytes)
            result.alt_id = src[b'alt_id']

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
            b'session_id':       self.session_id.hex(),
            b'message_data':     self.message_data,
            b'sig':              self.sig.hex(),
            b'whisper_mods':     int(self.whisper_mods),
            b'relay_to_plugins': int(self.relay_to_plugins),
        }
        if self.alt_id:
            result[b"alt_id"] = self.alt_id
        if self.whisper_to:
            result[b"whisper_to"] = self.whisper_to
        if len(self.attachment_ids):
            result[b"attachment_ids"] = self.attachment_ids
        return result

    def to_bencode(self) -> bytes:
        d      = self.to_dict()
        result = oxenc.bt_serialize(d)
        return result


@dataclasses.dataclass
class ReactionPosted:
    """Event sent to plugins when a user adds a reaction to a message"""
    msg_id:     MessageID  # Message ID being reacted to
    reaction:   str        # The reaction emoji/content (1-12 unicode characters)
    user_id:    int        # User ID of the reactor
    session_id: SessionID  # 33-byte blinded Session ID (15-blinded x25519 pubkey)
    room_id:    int        # Room ID
    room_token: RoomToken  # Room token
    room_name:  str        # Room display name
    is_mod:     bool       # Whether reactor is a moderator
    is_admin:   bool       # Whether reactor is an admin

    @classmethod
    def from_dict(cls, src: Dict[bytes, bt_value]) -> "ReactionPosted":
        return cls(
            msg_id     = typing.cast(int, src[b'msg_id']),
            reaction   = typing.cast(bytes, src[b'reaction']).decode('utf-8'),
            user_id    = typing.cast(int, src[b'user_id']),
            session_id = bytes.fromhex(typing.cast(bytes, src[b'session_id']).decode('utf-8')),
            room_id    = typing.cast(int, src[b'room_id']),
            room_token = typing.cast(bytes, src[b'room_token']).decode('utf-8'),
            room_name  = typing.cast(bytes, src[b'room_name']).decode('utf-8'),
            is_mod     = bool(typing.cast(int, src[b'is_mod'])),
            is_admin   = bool(typing.cast(int, src[b'is_admin'])),
        )

    @classmethod
    def from_bencode(cls, data: Union[bytes, memoryview]) -> "ReactionPosted":
        d: Dict[bytes, bt_value] = oxenc.bt_deserialize(data)
        return cls.from_dict(d)

    def to_dict(self) -> Dict[bytes, bt_value]:
        return {
            b'msg_id':     self.msg_id,
            b'reaction':   self.reaction,
            b'user_id':    self.user_id,
            b'session_id': self.session_id.hex(),
            b'room_id':    self.room_id,
            b'room_token': self.room_token,
            b'room_name':  self.room_name,
            b'is_mod':     int(self.is_mod),
            b'is_admin':   int(self.is_admin),
        }

    def to_bencode(self) -> bytes:
        return oxenc.bt_serialize(self.to_dict())

@dataclasses.dataclass
class MessagePosted:
    """Event sent to plugins when a message is posted to a room"""
    id:              MessageID       # Message ID
    room:            int             # Room ID
    room_token:      RoomToken       # Room token
    user:            int             # User ID
    session_id:      SessionID       # 33-byte 25-blinded Session ID
    data:            bytes           # Raw message data
    data_size:       int
    signature:       bytes
    posted:          TimestampS      # Unix timestamp
    seqno:           int
    seqno_creation:  int
    seqno_data:      int
    filtered:        bool
    whisper_mods:    bool
    seqno_reactions: int
    edited:          Optional[TimestampS] = None  # Timestamp of when the message was edited
    whisper:         Optional[int]        = None  # User ID that the message was whispered to
    whisper_to:      Optional[SessionID]  = None  # 33-byte 15/25-blinded Session ID that we whispered to
    alt_id:          Optional[SessionID]  = None  # 33-byte 15-blinded Session ID
    signing_id:      Optional[SessionID]  = None  # 33-byte signing Session ID

    @staticmethod
    def from_dict(src: Dict[bytes, bt_value]) -> "MessagePosted":
        result = MessagePosted(
            id              = typing.cast(int, src[b'id']),
            room            = typing.cast(int, src[b'room']),
            room_token      = typing.cast(bytes, src[b'room_token']).decode('utf-8'),
            user            = typing.cast(int, src[b'user']),
            session_id      = typing.cast(bytes, src[b'session_id']),
            data            = typing.cast(bytes, src[b'data']),
            data_size       = typing.cast(int, src[b'data_size']),
            signature       = typing.cast(bytes, src[b'signature']),
            posted          = typing.cast(float, src[b'posted']),
            seqno           = typing.cast(int, src[b'seqno']),
            seqno_creation  = typing.cast(int, src[b'seqno_creation']),
            seqno_data      = typing.cast(int, src[b'seqno_data']),
            filtered        = typing.cast(bool, src.get(b'filtered', False)),
            whisper_mods    = typing.cast(bool, src.get(b'whisper_mods', False)),
            seqno_reactions = typing.cast(int, src.get(b'seqno_reactions', 0)),
            edited          = typing.cast(float, src[b'edited']) if b'edited' in src else None,
            whisper         = typing.cast(int, src[b'whisper']) if b'whisper' in src else None,
            alt_id          = typing.cast(bytes, src[b'alt_id']) if b'alt_id' in src else None,
            signing_id      = typing.cast(bytes, src[b'signing_id']) if b'signing_id' in src else None,
            whisper_to      = typing.cast(bytes, src[b'whisper_to']) if b'whisper_to' in src else None,
        )
        return result

    @staticmethod
    def from_bencode(data: Union[bytes, memoryview]) -> "MessagePosted":
        d: Dict[bytes, bt_value] = oxenc.bt_deserialize(data)
        result                   = MessagePosted.from_dict(d)
        return result

    def to_dict(self) -> Dict[bytes, bt_value]:
        result: Dict[bytes, bt_value] = {
            b'id':              self.id,
            b'room':            self.room,
            b'room_token':      self.room_token,
            b'user':            self.user,
            b'session_id':      self.session_id.hex(),
            b'data':            self.data,
            b'data_size':       self.data_size,
            b'signature':       self.signature,
            b'posted':          str(self.posted),
            b'seqno':           self.seqno,
            b'seqno_creation':  self.seqno_creation,
            b'seqno_data':      self.seqno_data,
            b'filtered':        int(self.filtered),
            b'whisper_mods':    int(self.whisper_mods),
            b'seqno_reactions': self.seqno_reactions,
        }
        if self.edited is not None:
            result[b'edited'] = self.edited
        if self.whisper is not None:
            result[b'whisper'] = self.whisper
        if self.alt_id is not None:
            result[b'alt_id'] = self.alt_id
        if self.signing_id is not None:
            result[b'signing_id'] = self.signing_id
        if self.whisper_to is not None:
            result[b'whisper_to'] = self.whisper_to
        return result

    def to_bencode(self) -> bytes:
        return oxenc.bt_serialize(self.to_dict())

@dataclasses.dataclass
class PluginHelloRequest:
    """Request payload sent by a plugin during the hello handshake. Contains the plugin's Session ID
    which is used to entitle the plugin to room permissions"""
    session_id: SessionID

    @staticmethod
    def from_dict(src: Dict[bytes, bt_value]) -> "PluginHelloRequest":
        result = PluginHelloRequest(session_id = bytes.fromhex(typing.cast(bytes, src[b'session_id']).decode('utf-8')),)
        return result

    @staticmethod
    def from_bencode(data: Union[bytes, memoryview]) -> "PluginHelloRequest":
        d: Dict[bytes, bt_value] = oxenc.bt_deserialize(data)
        result                   = PluginHelloRequest.from_dict(d)
        return result

    def to_dict(self) -> Dict[bytes, bt_value]:
        result: Dict[bytes, bt_value] = {b'session_id': self.session_id.hex()}
        return result

    def to_bencode(self) -> bytes:
        result = oxenc.bt_serialize(self.to_dict())
        return result

@dataclasses.dataclass
class PluginUploadFileRequest:
    filename:      str
    file_contents: bytes
    room_token:    RoomToken

    @staticmethod
    def from_dict(src: Dict[bytes, bt_value]) -> "PluginUploadFileRequest":
        result = PluginUploadFileRequest(
            filename      = typing.cast(bytes, src[b'filename']).decode('utf-8'),
            file_contents = typing.cast(bytes, src[b'file_contents']),
            room_token    = typing.cast(bytes, src[b'room_token']).decode('utf-8'),)
        return result;

    @staticmethod
    def from_bencode(data: Union[bytes, memoryview]) -> "PluginUploadFileRequest":
        d: Dict[bytes, bt_value] = oxenc.bt_deserialize(data)
        result                   = PluginUploadFileRequest.from_dict(d)
        return result

    def to_dict(self) -> Dict[bytes, bt_value]:
        result: Dict[bytes, bt_value] = {
            b'filename':      self.filename,
            b'file_contents': self.file_contents,
            b'room_token':    self.room_token,
        }
        return result

    def to_bencode(self) -> bytes:
        result = oxenc.bt_serialize(self.to_dict())
        return result

@dataclasses.dataclass
class PluginUploadFileResponse:
    """Response payload from SOGS after a file upload."""
    file_id: int
    url:     str

    @staticmethod
    def from_dict(src: Dict[bytes, bt_value]) -> "PluginUploadFileResponse":
        result = PluginUploadFileResponse(file_id = typing.cast(int, src[b'file_id']),
                                          url     = typing.cast(bytes, src[b'url']).decode('utf-8'),)
        return result

    @staticmethod
    def from_bencode(data: Union[bytes, memoryview]) -> "PluginUploadFileResponse":
        d: Dict[bytes, bt_value] = oxenc.bt_deserialize(data)
        result                   = PluginUploadFileResponse.from_dict(d)
        return result

    def to_dict(self) -> Dict[bytes, bt_value]:
        result: Dict[bytes, bt_value] = { b'file_id': self.file_id, b'url': self.url,}
        return result

    def to_bencode(self) -> bytes:
        result = oxenc.bt_serialize(self.to_dict())
        return result

@dataclasses.dataclass
class FileUploadMetadata:
    file_name:    str
    id:           int
    url:          str
    size:         int
    content_type: Optional[str] = None
    width:        Optional[int] = None  # Only for image attachments
    height:       Optional[int] = None  # Only for image attachments

    def to_protobuf_dict(self) -> Dict[str, Any]:
        """Returns a dict with camelCase keys matching protobuf AttachmentPointer field names."""
        result: Dict[str, Any] = {
            "fileName": self.file_name,
            "id":       self.id,
            "url":      self.url,
            "size":     self.size,
        }
        if self.content_type is not None:
            result["contentType"] = self.content_type
        if self.width is not None:
            result["width"] = self.width
        if self.height is not None:
            result["height"] = self.height
        return result


@dataclasses.dataclass
class PluginDeleteMessageRequest:
    """Request from plugin to SOGS to delete message(s) created by the plugin."""
    msg_ids: List[MessageID]

    @staticmethod
    def from_dict(src: Dict[bytes, bt_value]) -> "PluginDeleteMessageRequest":
        result = PluginDeleteMessageRequest(
            msg_ids = typing.cast(List[int], src[b'msg_ids'])
        )
        return result

    @staticmethod
    def from_bencode(data: Union[bytes, memoryview]) -> "PluginDeleteMessageRequest":
        d: Dict[bytes, bt_value] = oxenc.bt_deserialize(data)
        result                   = PluginDeleteMessageRequest.from_dict(d)
        return result

    def to_dict(self) -> Dict[bytes, bt_value]:
        result: Dict[bytes, bt_value] = {
            b'msg_ids': self.msg_ids,
        }
        return result

    def to_bencode(self) -> bytes:
        d      = self.to_dict()
        result = oxenc.bt_serialize(d)
        return result


@dataclasses.dataclass
class PluginDeleteMessageResponse:
    """Response from SOGS to plugin after a delete message request."""
    status: str
    error:  Optional[str] = None

    @staticmethod
    def from_dict(src: Dict[bytes, bt_value]) -> "PluginDeleteMessageResponse":
        result = PluginDeleteMessageResponse(
            status = typing.cast(bytes, src[b'status']).decode('utf-8'),
        )
        if b'error' in src:
            result.error = typing.cast(bytes, src[b'error']).decode('utf-8')
        return result

    @staticmethod
    def from_bencode(data: Union[bytes, memoryview]) -> "PluginDeleteMessageResponse":
        d: Dict[bytes, bt_value] = oxenc.bt_deserialize(data)
        result                   = PluginDeleteMessageResponse.from_dict(d)
        return result

    def to_dict(self) -> Dict[bytes, bt_value]:
        result: Dict[bytes, bt_value] = {
            b'status': self.status.encode('utf-8'),
        }
        if self.error is not None:
            result[b'error'] = self.error.encode('utf-8')
        return result

    def to_bencode(self) -> bytes:
        d      = self.to_dict()
        result = oxenc.bt_serialize(d)
        return result


@dataclasses.dataclass
class PluginReactionsRequest:
    """Request from plugin to SOGS to post or remove reactions on a message."""
    room_token: RoomToken
    msg_id:     MessageID
    reactions:  List[str]

    @staticmethod
    def from_dict(src: Dict[bytes, bt_value]) -> "PluginReactionsRequest":
        result = PluginReactionsRequest(
            room_token = typing.cast(bytes, src[b'room_token']).decode('utf-8'),
            msg_id     = typing.cast(int, src[b'msg_id']),
            reactions  = [r.decode('utf-8') for r in typing.cast(List[bytes], src[b'reactions'])],
        )
        return result

    @staticmethod
    def from_bencode(data: Union[bytes, memoryview]) -> "PluginReactionsRequest":
        d: Dict[bytes, bt_value] = oxenc.bt_deserialize(data)
        result                   = PluginReactionsRequest.from_dict(d)
        return result

    def to_dict(self) -> Dict[bytes, bt_value]:
        result: Dict[bytes, bt_value] = {
            b'room_token': self.room_token,
            b'msg_id':     self.msg_id,
            b'reactions':  self.reactions,
        }
        return result

    def to_bencode(self) -> bytes:
        d      = self.to_dict()
        result = oxenc.bt_serialize(d)
        return result


@dataclasses.dataclass
class PluginReactionsResponse:
    """Response from SOGS to plugin after a post/remove reactions request."""
    status: str
    error:  Optional[str] = None

    @staticmethod
    def from_dict(src: Dict[bytes, bt_value]) -> "PluginReactionsResponse":
        result = PluginReactionsResponse(status = typing.cast(bytes, src[b'status']).decode('utf-8'),)
        if b'error' in src:
            result.error = typing.cast(bytes, src[b'error']).decode('utf-8')
        return result

    @staticmethod
    def from_bencode(data: Union[bytes, memoryview]) -> "PluginReactionsResponse":
        d: Dict[bytes, bt_value] = oxenc.bt_deserialize(data)
        result                   = PluginReactionsResponse.from_dict(d)
        return result

    def to_dict(self) -> Dict[bytes, bt_value]:
        result: Dict[bytes, bt_value] = { b'status': self.status, }
        if self.error is not None:
            result[b'error'] = self.error.encode('utf-8')
        return result

    def to_bencode(self) -> bytes:
        d      = self.to_dict()
        result = oxenc.bt_serialize(d)
        return result
