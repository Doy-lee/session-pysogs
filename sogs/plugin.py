import nacl.bindings as sodium
import typing
import dataclasses
import oxenmq
import oxenc
import logging
import enum
import typing_extensions
import datetime

from typing          import Callable
from nacl.encoding   import HexEncoder
from nacl.signing    import SigningKey
from datetime        import timedelta
from time            import time
from sogs.model.post import Post

class LogFormatter(logging.Formatter):
    @typing_extensions.override
    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        dt     = datetime.datetime.fromtimestamp(record.created)
        result = dt.strftime('%y-%m-%d %H:%M:%S.%f')[:-3]
        return result

log_formatter = LogFormatter('%(asctime)s %(levelname)s %(name)s %(message)s')
console_log_handler = logging.StreamHandler()
console_log_handler.setFormatter(log_formatter)

log = logging.Logger('PLUGIN')
log.addHandler(console_log_handler)

# Represents the Session account as bytes, i.e. 1b Blinding Prefix + 32b X25519 Public Key
# This is not to be confused with the value of the Session ID that is typically returned from the
# SOGS server OMQ response which is the hex representation of the Session account but held in a
# bytes object,
# e.g.
#   OMQ Session ID response => b"15aaaa.."       (66 bytes)
#   `SessionID`             => b"\x15\xaa\xaa.." (33 bytes)
SessionID:  typing.TypeAlias = bytes
RoomToken:  typing.TypeAlias = bytes
TimestampS: typing.TypeAlias = float
MessageID:  typing.TypeAlias = int

# Represents the different variants of data types that a primitive bencoded type can hold. These
# values are produced and consumed by the module oxenc's bt_serialize/bt_deserialize functions.
bt_value: typing.TypeAlias = (
    int
    | bytes
    | str
    | list["bt_value"]
    | dict[bytes | str, "bt_value"]
)

@dataclasses.dataclass
class ReplySettings:
    """Settings controlling how the plugin replies to a filtered message.
    Attributes:
        reply_formats: List of format strings where one is chosen at random to use as the reply. In
                       the reply, the following python placeholders are supported:
                       {profile_name}, {profile_at}, {room_name}, {room_token}.

                       e.g. reply_format_str = "Hey {profile_name}! No swearing here in {room_name}"

        profile_name:  Display name for the reply
        public:        If True the reply is posted publicly; if False it is whispered to the user.
    """
    reply_formats: list[str] = dataclasses.field(default_factory=list)
    profile_name:  str       = 'SOGS'
    public:        bool      = False

@dataclasses.dataclass
class RoomReadRequest:
    room_id:    int
    room_name:  str
    room_token: bytes
    session_id: SessionID
    user_id:    int

    @classmethod
    def from_bencode(cls, src: dict[bytes, bt_value]):
        result = RoomReadRequest(room_id     = typing.cast(int, src[b'room_id']),
                                 room_name  = typing.cast(bytes, src[b'room_name']).decode('utf-8'),
                                 room_token = typing.cast(bytes, src[b'room_token']),
                                 session_id = bytes.fromhex(typing.cast(bytes, src[b'session_id']).decode('utf-8')),
                                 user_id    = typing.cast(int, src[b'user_id']),)
        return result

@dataclasses.dataclass
class FilterMessageRequest:
    alt_id:       SessionID # 15-blinded x25519 pubkey (w/ 15-prefix)
    data_size:    int
    filtered:     bool
    is_mod:       bool
    message_data: bytes
    room_id:      int
    room_name:    str
    room_token:   bytes
    session_id:   SessionID # 25-blinded x25519 pubkey (w/ 25-prefix)
    sig:          bytes
    user_id:      int
    whisper_mods: bool

    @classmethod
    def from_bencode(cls, src: dict[bytes, bt_value]):
        result = FilterMessageRequest(alt_id       = bytes.fromhex(typing.cast(bytes, src[b'alt_id']).decode('utf-8')),
                                      data_size    = typing.cast(int,   src[b'data_size']),
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
        return result


class SetUserRoomPermissionsResponse(enum.Enum):
    NoSuchRoom = 0
    NoSuchUser = 1
    Error      = 2
    InvalidArg = 3
    Ok         = 4

@dataclasses.dataclass
class Plugin:
    FILTER_ACCEPT:        typing.ClassVar[str]                  = "OK"
    FILTER_REJECT:        typing.ClassVar[str]                  = "REJECT"
    FILTER_REJECT_SILENT: typing.ClassVar[str]                  = "SILENT"
    FILTER_RESPONSES:     typing.ClassVar[tuple[str, str, str]] = (FILTER_ACCEPT, FILTER_REJECT, FILTER_REJECT_SILENT)

    # User initialised
    ed_privkey:           bytes # ed25519
    ed_pubkey:            bytes # ed25519
    display_name:         str
    sogs_address:         str
    sogs_pubkey:          bytes

    # Default values
    running:              bool                                                = False
    last_post_time:       int                                                 = 0
    pre_slash_handlers:   dict[str, typing.Callable[[str, str], None]]        = dataclasses.field(default_factory=dict)
    post_slash_handlers:  dict[str, typing.Callable[[str, str], None]]        = dataclasses.field(default_factory=dict)
    request_read_handler: typing.Callable[[RoomReadRequest], bt_value] | None = None
    conn:                 oxenmq.ConnectionID | None                          = None

    # Post initialised
    session_id:           SessionID           = dataclasses.field(init=False) # 33 byte 15-blinded x25519 pubkey  (w/  15-prefix)
    blind25_pubkey:       bytes               = dataclasses.field(init=False) # 32 byte 25-blinded x25519 pubkey  (w/o 25-prefix)
    blind25_privkey:      bytes               = dataclasses.field(init=False) # 32 byte 25-blinded x25519 privkey (w/o 25-prefix)
    blind15_pubkey:       bytes               = dataclasses.field(init=False) # 32 byte 15-blinded x25519 pubkey  (w/o 15-prefix)
    blind15_privkey:      bytes               = dataclasses.field(init=False) # 32 byte 15-blinded x25519 privkey (w/o 15-prefix)
    x_pubkey:             bytes               = dataclasses.field(init=False) # 32 byte x25519 pubkey (non-blinded Session ID)
    x_privkey:            bytes               = dataclasses.field(init=False) # 32 byte x25519 pubkey (non-blinded Session ID)
    omq:                  oxenmq.OxenMQ       = dataclasses.field(init=False)

    def __post_init__(self):
        """Generate the derivative keys based given the Session Account's Ed25519 key-pairing and
        sets up an OxenMQ connection to the SOGS server"""

        if len(self.ed_privkey) != 32 or len(self.ed_pubkey) != 32:
            raise Exception("SOGS plugin must specify a Ed25519 32b public and private keypair (pubkey was: {len(self.pubkey)}b, privkey: {len(self.privkey)}b")

        # Generate X25519 keys
        self.x_privkey                                 = sodium.crypto_sign_ed25519_sk_to_curve25519(self.ed_privkey + self.ed_pubkey)
        self.x_pubkey                                  = sodium.crypto_sign_ed25519_pk_to_curve25519(self.ed_pubkey)

        # Generate blinded keys
        import session_util.blinding
        blind25_keypair: session_util.blinding.Keypair = session_util.blinding.blind25_key_pair(self.ed_privkey, self.sogs_pubkey)
        blind15_keypair: session_util.blinding.Keypair = session_util.blinding.blind15_key_pair(self.ed_privkey, self.sogs_pubkey)
        self.blind25_pubkey                            = blind25_keypair.pubkey
        self.blind25_privkey                           = blind25_keypair.privkey
        self.blind15_pubkey                            = blind15_keypair.pubkey
        self.blind15_privkey                           = blind15_keypair.privkey
        self.session_id                                = b"\x15" + self.blind15_pubkey

        # Setup networking to SOGS via OMQ
        self.omq = oxenmq.OxenMQ(privkey=self.x_privkey, pubkey=self.x_pubkey, log_level=oxenmq.LogLevel.debug)
        cat      = self.omq.add_category("plugin", access_level=oxenmq.AuthLevel.none)
        cat.add_request_command("filter_message",       self.filter_message)
        cat.add_command        ("message_posted",       self.message_posted)
        cat.add_command        ("reaction_posted",      self.reaction_posted)
        cat.add_request_command("pre_message_command",  self.pre_message_command)
        cat.add_request_command("post_message_command", self.post_message_command)
        cat.add_request_command("request_read",         self.request_read)

    def _require_conn_established(self) -> oxenmq.ConnectionID:
        assert self.conn, "Plugin misuse: Connection to SOGS not established yet, plugin.run() must be called first"
        return self.conn

    def _on_registered_after_hello(self):
        conn: oxenmq.ConnectionID = self._require_conn_established()

        # NOTE: Subscribe to the following hooks on SOGS. SOGs will call invoke this plugin via
        # OxenMQ when the commands are triggered.
        if len(self.pre_slash_handlers) or self.request_read_handler:
            pre_commands: list[str] = list(self.pre_slash_handlers.keys())
            if self.request_read_handler:
                pre_commands.append('/request_read')

            print(f"Registering pre-commands: {pre_commands}")
            self.omq.send(conn, "plugin.register_pre_commands", oxenc.bt_serialize({b'commands': pre_commands}))

        if len(self.post_slash_handlers):
            post_commands: list[str] = list(self.post_slash_handlers.keys())

            print(f"Registering post-commands: {post_commands}")
            self.omq.send(conn, "plugin.register_post_commands", oxenc.bt_serialize({b'commands': list(post_commands)}))

    def say_hello(self):
        conn: oxenmq.ConnectionID = self._require_conn_established()
        try:
            future: oxenmq.ResultFuture = self.omq.request_future(conn, "plugin.hello", oxenc.bt_serialize(self.session_id.hex().encode()), request_timeout=timedelta(seconds=10))
            resp:   bytes               = typing.cast(bytes, oxenc.bt_deserialize(future.get()[0]))
            if resp == b'OK':
                return
            elif resp == b"REGISTER":
                self._on_registered_after_hello()
                self.running = True
                return
            print(f"Plugin hello error from sogs: {resp}")
        except Exception as e:
            print(f"Exception in plugin hello: {e}")

    def run(self):
        self.omq.start()
        self.conn = self.omq.connect_remote(oxenmq.Address(self.sogs_address, self.sogs_pubkey))

        self.say_hello()

        # FIXME: there's definitely a better way to do this, but if SOGS restarts and
        #        we reconnect, this makes SOGS recognize our omq connection as this plugin.
        count = 0
        while True:
            count += 1
            if count % 60 == 0:
                self.say_hello()
            from time import sleep

            sleep(1)

    def register_request_read_handler(self, handler: Callable[[RoomReadRequest], bt_value]):
        """
        If a user attempts to read a room but has only "access" to the room, this will be called
        (if registered).

        Currently SOGS does nothing with the response from this request, but responding signals
        the plugin is done handling it.  This is so SOGS waits to respond to that user until e.g.
        the plugin has had the chance to whisper the user (so the user will see the whisper right away).
        Any return value from the handler will be ignored until SOGS has use for it.
        """
        self.request_read_handler = handler

        # if not running, finish_init() will do this once connected
        if self.running:
            conn: oxenmq.ConnectionID = self._require_conn_established()
            self.omq.send(conn, f"plugin.register_pre_commands", oxenc.bt_serialize({b"commands": ["request_read"]}))

    def handle_message_command(self, m: oxenmq.Message, pre_command: bool):
        req = oxenc.bt_deserialize(m.dataview()[0])
        msg = Post(raw=req[b"message_data"])

        command_parts = msg.text.split(' ')
        if not command_parts: # shouldn't be possible, but false just to signal it happened
            return oxenc.bt_serialize(False)

        command           = command_parts[0]
        command_container = self.pre_slash_handlers if pre_command else self.post_slash_handlers
        if not command in command_container:
            return oxenc.bt_serialize(True)

        try:
            retval = command_container[command](req, command_parts)
            if not isinstance(retval, bool):
                print("command handlers must return True or False")
                return oxenc.bt_serialize(True)
            return oxenc.bt_serialize(retval)
        except Exception as e:
            print(f"Exception handling slash command: {e}")
            return oxenc.bt_serialize(True)

    def pre_message_command(self, m: oxenmq.Message):
        return self.handle_message_command(m, True)

    def post_message_command(self, m: oxenmq.Message):
        return self.handle_message_command(m, False)

    def request_read(self, m: oxenmq.Message):
        # Example
        #  {b'room_id': 1, b'room_name': b'foobar', b'room_token': b'foobar', b'session_id': b'1500784b7c2096f6ed811b25c53a63e551954ee6778c7ae4437cb01c4b01fb4a09', b'user_id': 3}
        req       = typing.cast(dict[bytes, bt_value], oxenc.bt_deserialize(m.dataview()[0]))
        room_info = RoomReadRequest.from_bencode(req)

        # this should not be called by sogs if we didn't register it...
        if not self.request_read_handler:
            return oxenc.bt_serialize(False)
        try:
            self.request_read_handler(room_info)
        except Exception as e:
            import traceback
            print(f"Exception in request_read handler: {traceback.format_exc()}")
        return oxenc.bt_serialize(True)

    def register_command(self, command: str, handler: typing.Callable[[str, str], None], pre_command: bool):
        """
        Registers a slash command with sogs.  `handler` will be invoked with the arguments
        from sogs as a dictionary, including "command": command.
        sogs sends commands before database insertion and after.  Use pre_message/post_message to
        indicate which you want to handle.
        Return True from your handler if sogs may continue to the next plugin and/or the next step
        in message handling, False if you handled the command and it should be considered finished
        or if you wanted to handle it but there was an error and sogs should discard it.
        """
        if pre_command:
            self.pre_slash_handlers[command] = handler
        else:
            self.post_slash_handlers[command] = handler

        # if not running, finish_init() will do this once connected
        if self.running:
            assert self.conn, "When running is set, the connection should already be established"
            command_type = "pre_commands" if pre_command else "post_commands"
            self.omq.send(self.conn, f"plugin.register_{command_type}", oxenc.bt_serialize({b"commands": [command]}))

    def register_pre_command(self, command: str, handler: typing.Callable[[str, str], None]):
        self.register_command(command, handler, True)

    def register_post_command(self, command: str, handler: typing.Callable[[str, str], None]):
        self.register_command(command, handler, False)

    def filter_message(self, m: oxenmq.Message):
        try:
            req_raw: dict[bytes, bt_value] = oxenc.bt_deserialize(m.dataview()[0])
            req                            = FilterMessageRequest.from_bencode(req_raw)

            log.debug(f"Filter message received: {req_raw}")
            # NOTE: Example
            #
            # {b'alt_id': b'15dae60d80fa50f570831ddd02345556120c7508f749fd15f24002ff2a74b436cd',
            #  b'data_size': 160,
            #  b'filtered': 0,
            #  b'is_mod': 0,
            #  b'message_data': b"\n\x1c\n\x07testttt\xaa\x06\r\n\tAnonymous\x18\x00\xd0\x06\x01`\x00h\x00x\xaf\xff\xa7\xc2\xc03\x8a\x01@Q\x13Ki\xde\xfc\x7f=\x81M\xc0\x91\x96\x82\xcc\xd7\xb0%kVC\xd5\x8aYo(\xdf\xbf\xdbj(\xbf5\xbd,5\x81\xcb\x8a\xd0O\xa2\x8b\xe8\x12'\x02:1b\xfd:B\xe3\x04\xce\xd4O\xbf=I\xe3$\x02\x80\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00",
            #  b'room_id': 3,
            #  b'room_name': b'foobar4',
            #  b'room_token': b'foobar4',
            #  b'session_id': b'25a9ae1d59778a30abb54e9a6bbbd4832a40c27bb92afbcda0f28df3094d2b55a4',
            #  b'sig': b'/Cs\xab\xea\xfb\xa29\xee\xaao\x8cJ,\xba\\\x86g#Y<\xca\xce\x9a\x99\x85\x88\xca\xd6\xb6\x96\xe2/\xf7Q\x043b!\xb9\xf7\xcc\r\xbf(\xbd\x80\xba@`\xa1\x00\x94\x10V\xf2U\xa8\xc4\xd7k\xe3\xb0\r',
            #  b'user_id': 5,
            #  b'whisper_mods': 0}

            resp = self.filter(req)
            if resp not in self.FILTER_RESPONSES:
                log.warning(f"plugin.filter() must return one of {Plugin.FILTER_RESPONSES}")
                return oxenc.bt_serialize("REJECT")

            print(f"filter_message returning '{resp}' as filter response")
            return oxenc.bt_serialize(resp)
        except Exception as e:
            print(f"Exception filtering message: {e}")
            return oxenc.bt_serialize("REJECT")

    def filter(self, req: FilterMessageRequest):  # pyright: ignore[reportUnusedParameter]
        """
        Users may override this function for custom filtering, or supply a callable filter object

        This function must return one of FILTER_ACCEPT, FILTER_REJECT, or FILTER_REJECT_SILENT
        """
        return self.FILTER_ACCEPT

    def reply(
        self,
        room_name:       bytes,
        room_token:      bytes,
        user_session_id: SessionID,
        message_data,
        username: str,
        *args,
        reply_settings: ReplySettings,
    ):
        """Call this from your filter() override when you want to reply to a user message, e.g.
        "hey no swearing here"
        """
        from random import choice
        rf = choice(reply_settings.reply_formats)

        user_session_id_hex = user_session_id.hex()
        body = rf.format(
            profile_name = user_session_id_hex if username is None else username,
            profile_at   = f"@{user_session_id_hex}",
            room_name    = room_name.decode('utf-8'),
            room_token   = room_token,
        ).encode()

        self.post_message(room_token, body, whisper_target=None if reply_settings.public else user_session_id)

    def set_user_room_permissions(
        self,
        room:         bytes     | int | None = None,
        user:         SessionID | int | None = None,
        sec_from_now: int       | None       = None,
        accessible:   bool      | None       = None,
        read:         bool      | None       = None,
        write:        bool      | None       = None,
        upload:       bool      | None       = None,
    ) -> SetUserRoomPermissionsResponse:
        """Set the permission(s) of the user for the room

        The following parameters must be set, or otherwise this function returns InvalidArg:

          - Room must be set to either the room token in bytes (e.g.: b'foobar') or ID of the room.
          - User must be set to either the user's 33b blinded Session ID or the ID of the user.
          - At least one of the permissions must be set, accessible, read, write or upload.
        """
        req: dict[bytes, bt_value] = {}

        # NOTE: Set the room
        if isinstance(room, int):
            req[b"room_id"] = room
        elif isinstance(room, bytes):
            req[b"room_token"] = room
        else:
            print("Room identifier (token `bytes` or id `int`) is required for permissions changes.")
            return SetUserRoomPermissionsResponse.InvalidArg

        # NOTE: Set the user
        if isinstance(user, SessionID):
            if len(user) != 33:
                print("User passed as `SessionID` must be a 33b blinded public key for permissions changes.")
                return SetUserRoomPermissionsResponse.InvalidArg
            req[b"user_session_id"] = user.hex()
        elif isinstance(user, int):
            req[b"user_id"] = user
        else:
            print("User (`SessionID` or id `int`) is required for permissions changes.")
            return SetUserRoomPermissionsResponse.InvalidArg

        # NOTE: Set permissions
        if not accessible and not read and not write and not upload:
            print("At least one permission should be specified (`accessible`, `read`, `write`, `upload`) for permissions changes.")
            return SetUserRoomPermissionsResponse.InvalidArg
        if accessible:
            req[b"accessible"] = accessible
        if read:
            req[b"read"] = read
        if write:
            req[b"write"] = write
        if upload:
            req[b"upload"] = upload

        # NOTE: Set enqueued permission change
        if sec_from_now:
            UPPER_BOUND: int = 1_000_000_000
            if not 0 < sec_from_now < UPPER_BOUND:
                print(f"Enqueuing a permission change in the future must be bounded between [0 < {sec_from_now} < {UPPER_BOUND}]")
                return SetUserRoomPermissionsResponse.InvalidArg

            req[b"in"] = sec_from_now

        # NOTE: Request and response
        conn:      oxenmq.ConnectionID = self._require_conn_established();
        future:    oxenmq.ResultFuture = self.omq.request_future(conn, "plugin.set_user_room_permissions", oxenc.bt_serialize(req), request_timeout=timedelta(seconds=1))
        resp_list: list[bytes]         = future.get()
        assert len(resp_list) == 1

        resp: bytes = future.get()[0]
        result = SetUserRoomPermissionsResponse.Error
        if resp == b"OK":
            result = SetUserRoomPermissionsResponse.Ok
        elif resp == b"NoSuchRoom":
            result = SetUserRoomPermissionsResponse.NoSuchRoom
        elif resp == b"NoSuchUser":
            result = SetUserRoomPermissionsResponse.NoSuchUser
        return result

    def delete_messages(self, msg_ids: list[MessageID]) -> bool:
        """Request SOGs to delete the specified message(s). The message(s) must have been created by
        this plugin.
        """
        result = True
        if len(msg_ids):
            conn:      oxenmq.ConnectionID = self._require_conn_established();
            future:    oxenmq.ResultFuture = self.omq.request_future(conn, "plugin.delete_messages", oxenc.bt_serialize(req))
            resp_list: list[bytes]         = future.get()
            assert len(resp_list) == 1

            resp: dict[bytes, bt_value] = oxenc.bt_deserialize(resp_list[0])
            if b'status' in resp and resp[b'status'] == b'OK':
                result = True
        return result

    def delete_message(self, msg_id: int) -> bool:
        result = self.delete_messages([msg_id])
        return result

    def post_message(self,
                     room_token:           bytes,
                     body:                 str,
                     *,
                     whisper_target:       SessionID | None = None,
                     no_plugins:           bool = False,
                     attachments_metadata: list[dict[str, typing.Any]] | None = None) -> MessageID | None:
        from sogs import session_pb2 as protobuf
        from time import time
        self.last_post_time = max(self.last_post_time + 1, int(time() * 1000))

        content                                 = protobuf.Content()
        content.dataMessage.body                = body
        content.dataMessage.timestamp           = self.last_post_time
        content.dataMessage.profile.displayName = self.display_name

        attachment_ids: list[int] = []
        if attachments_metadata:
            for attachment_meta in attachments_metadata:
                assert "id" in attachment_meta
                assert isinstance(attachment_meta["id"], int)
                attachment_ids.append(attachment_meta["id"])

                attachment = content.dataMessage.attachments.add()
                for key in attachment_meta:
                    _ = getattr(attachment, key)                   # Ensure the field is available in the attachment (throws if it isn't)
                    setattr(attachment, key, attachment_meta[key]) # Assign the field

        def pad_message(payload: bytes) -> bytearray:
            # NOTE: Direct port of
            # https://github.com/session-foundation/libsession-util/blob/dd5d7c006d95a138f3648e4d76a0754e3950245a/src/session_protocol.cpp#L382
            PADDING_TERMINATING_BYTE = 0x80
            SESSION_PROTOCOL_COMMUNITY_OR_1O1_MSG_PADDING = 160

            # Calculate amount of padding required
            padded_content_size = len(payload) + 1  # +1 for padding byte
            bytes_for_padding = SESSION_PROTOCOL_COMMUNITY_OR_1O1_MSG_PADDING - (padded_content_size % SESSION_PROTOCOL_COMMUNITY_OR_1O1_MSG_PADDING)
            padded_content_size += bytes_for_padding
            assert padded_content_size % SESSION_PROTOCOL_COMMUNITY_OR_1O1_MSG_PADDING == 0

            # Do the padding
            result = bytearray(padded_content_size)
            result[0:len(payload)] = payload
            result[len(payload)] = PADDING_TERMINATING_BYTE
            return result


        # NOTE: Content must be padded as per Session Protocol requirements
        content = bytes(pad_message(content.SerializeToString()))

        # FIXME: Use 25-blinding when Session is ready and deprecate 15-blinded keys
        from session_util.blinding import blind15_sign
        sig:    bytes            = blind15_sign(self.ed_privkey, self.sogs_pubkey, content)
        result: MessageID | None = self.inject_message(room_token, self.session_id, content, sig, whisper_target=whisper_target, no_plugins=no_plugins, attachment_ids=attachment_ids)
        return result

    # This can be used either to post a message from the plugin *or* to re-inject a now-approved user message
    # Pass whisper_target=session_id if the message is a whisper to a user
    # Pass whisper_mods="yes" if the message is a mod whisper
    def inject_message(
        self,
        room_token:     bytes,
        session_id:     SessionID,
        message:        bytes,
        sig:            bytes,
        *,
        whisper_target: SessionID | None = None,
        whisper_mods:   bool             = False,
        no_plugins:     bool             = False,
        attachment_ids: list[int] | None = None,
    ) -> MessageID | None:
        req: dict[bytes, typing.Any] = {
            b"room_token":   room_token,
            b"session_id":   session_id.hex(),
            b"message":      message,
            b"sig":          sig,
            b"whisper_mods": whisper_mods,
        }

        if whisper_target:
            req[b"whisper_target"] = whisper_target.hex()

        if no_plugins:
            req[b"no_plugins"] = True

        if attachment_ids:
            req[b"files"] = attachment_ids

        conn: oxenmq.ConnectionID = self._require_conn_established()
        resp = oxenc.bt_deserialize(
            self.omq.request_future(
                self.conn, "plugin.message", oxenc.bt_serialize(req), request_timeout=timedelta(seconds=5)
            ).get()[0]
        )

        if not b'msg_id' in resp:
            return None

        msg_id = typing.cast(MessageID, resp[b'msg_id'])
        print(f"Message injected, id: {msg_id}")
        return msg_id

    def post_reactions(self, room_token: bytes, msg_id: MessageID, *reactions: str) -> dict[bytes, bt_value]:
        req = {b"room_token": room_token, b"msg_id": msg_id, b"reactions": reactions}
        print(f"post_reactions request: {req}")
        return oxenc.bt_deserialize(
            self.omq.request_future(
                self.conn,
                "plugin.post_reactions",
                oxenc.bt_serialize(req),
                request_timeout=timedelta(seconds=5),
            ).get()[0]
        )

    def remove_reactions(self, room_token: bytes, msg_id: MessageID, *reactions: str):
        req = {b"room_token": room_token, b"msg_id": msg_id, b"reactions": reactions}
        print(f"post_reactions request: {req}")
        return oxenc.bt_deserialize(
            self.omq.request_future(
                self.conn,
                "plugin.remove_reactions",
                oxenc.bt_serialize(req),
                request_timeout=timedelta(seconds=5),
            ).get()[0]
        )

    def upload_file(self, file_path: str, room_token: bytes, display_filename: str | None = None):
        try:
            from os import path
            filename = display_filename if display_filename else path.basename(file_path)

            from pathlib import Path
            file_contents = Path(file_path).read_bytes()

            req = {"filename": filename, "file_contents": file_contents, "room_token": room_token}

            resp = oxenc.bt_deserialize(
                self.omq.request_future(
                    self.conn,
                    "plugin.upload_file",
                    oxenc.bt_serialize(req),
                    request_timeout=timedelta(seconds=3),
                ).get()[0]
            )

            if not (b"file_id" in resp and b"url" in resp):
                print(f"file_id or url missing from sogs response to upload_file")
                return None

            metadata = {
                "fileName": filename,
                "id": resp[b"file_id"],
                "url": resp[b"url"].decode("utf-8"),
                "size": len(file_contents)
            }

            import mimetypes
            from PIL import Image
            mime = mimetypes.guess_type(file_path)
            metadata["contentType"] = mime[0]
            if mime[0].startswith("image"):
                img = Image.open(file_path)
                width, height = img.size
                metadata["width"] = width
                metadata["height"] = height

            return metadata

        except Exception as e:
            print(f"upload_file exception: {e}")
            return None

    def message_posted(self, m: oxenmq.Message):
        print(f"message_posted called")
        try:
            msg = oxenc.bt_deserialize(m.dataview()[0])
            print(f"message: {msg}")
        except Exception as e:
            print(f"Exception: {e}")

    def reaction_posted(self, m: oxenmq.Message):
        print(f"reaction_posted called")
        try:
            reaction = oxenc.bt_deserialize(m.dataview()[0])
            print(f"reaction: {reaction}")
        except Exception as e:
            print(f"Exception: {e}")


def profanity_check(*args):
    import better_profanity

    for part in args:
        if better_profanity.profanity.contains_profanity(part):
            print(f"Profanity detected in message part: \"{part}\"")
            return True

    return False


class SogsFilterPlugin(Plugin):
    import re

    # Character ranges for different filters.  This is ordered because some are subsets of each other
    # (e.g. persian is a subset of the arabic character range).
    alphabet_filter_patterns = [
        (
            'persian',
            re.compile(
                r'[\u0621-\u0628\u062a-\u063a\u0641-\u0642\u0644-\u0648\u064e-\u0651\u0655'
                r'\u067e\u0686\u0698\u06a9\u06af\u06be\u06cc]'
            ),
        ),
        (
            'arabic',
            re.compile(r'[\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff\ufb50-\ufdff\ufe70-\ufefe]'),
        ),
        ('cyrillic', re.compile(r'[\u0400-\u04ff]')),
        ('debug', re.compile(r'debug alphabet test')),
    ]

    """
    Handles profanity filtering and alphabet detection/direction (replacing the functionality
            which was previously built into SOGS directly).

    Pass config_file=path_to_sogs.ini or config_file=True to load config from
    environment SOGS_CONFIG variable or 'sogs.ini' in pwd

    Pass reply_name to override the default Session display name of this plugin (SOGS Plugin)
    """

    def __init__(self, privkey, pubkey, *args, display_name="SOGS Plugin", config_file=None):

        self.room_settings = {}
        self.filter_mods = False

        if isinstance(config_file, str):
            import os

            os.environ['SOGS_CONFIG'] = config_file

        from sogs import config

        self.config = config
        from sogs.crypto import server_pubkey_bytes

        sogs_pubkey = server_pubkey_bytes
        sogs_address = config.OMQ_LISTEN[0].replace('*', '127.0.0.1')
        self.from_sogs_config = True
        self.load_sogs_settings()

        Plugin.__init__(self, sogs_address, sogs_pubkey, privkey, pubkey, display_name)

    def load_sogs_settings(self):
        self.filter_mods = self.config.FILTER_MODS
        settings = {
            'profanity_filter': self.config.PROFANITY_FILTER,
            'profanity_silent': self.config.PROFANITY_SILENT,
            'alphabet_filters': self.config.ALPHABET_FILTERS,
            'alphabet_silent': self.config.ALPHABET_SILENT,
            'reply_settings': None,
        }
        self.room_settings['*'] = {}
        for k in self.config.FILTER_SETTINGS:
            if (
                'profanity' in self.config.FILTER_SETTINGS[k]
                or '*' in self.config.FILTER_SETTINGS[k]
            ):
                self.room_settings[k] = {}

        for k in settings:
            for room in self.room_settings:
                self.room_settings[room][k] = settings[k]

        print(f"overrides:\n{self.config.ROOM_OVERRIDES}\n")
        for room_token in self.config.ROOM_OVERRIDES:
            self.room_settings[room_token] = {}
            for k in settings:
                self.room_settings[room_token][k] = settings[k]
            for k in (
                'profanity_filter',
                'profanity_silent',
                'alphabet_filters',
                'alphabet_silent',
            ):
                if k in self.config.ROOM_OVERRIDES[room_token]:
                    self.room_settings[room_token][k] = self.config.ROOM_OVERRIDES[room_token][k]

        print(self.room_settings)

    def get_reply_settings(self, room_token, *args, filter_type='profanity', filter_lang=None) -> ReplySettings | None:
        if not self.config.FILTER_SETTINGS:
            return None

        reply_format = None
        profile_name = 'SOGS'
        public = False

        # Precedences from least to most specific so that we load values from least specific first
        # then overwrite them if we find a value in a more specific section
        room_precedence = ('*', room_token)
        filter_precedence = ('*', filter_type, filter_lang) if filter_lang else ('*', filter_type)

        for r in room_precedence:
            s1 = self.config.FILTER_SETTINGS.get(r)
            if s1 is None:
                continue
            for f in filter_precedence:
                settings = s1.get(f)
                if settings is None:
                    continue

                rf = settings.get('reply')
                pn = settings.get('profile_name')
                pb = settings.get('public')
                if rf is not None:
                    reply_format = rf
                if pn is not None:
                    profile_name = pn
                if pb is not None:
                    public = pb

        if reply_format is None:
            return None

        return ReplySettings(reply_formats=reply_format, profile_name=profile_name, public=public)

    def filter(self, request):
        # is_mod should be "mod" but is empty if not, so just check len
        if request[b"is_mod"] and not self.filter_mods:
            return self.FILTER_ACCEPT

        if request[b"message_id"] != -1:
            print("message filter request is an edit")

        room_token = request[b"room_token"].decode('utf-8')
        print(f"filtering for room_token: {room_token}")
        if room_token in self.room_settings:
            settings = self.room_settings[room_token]
            print("filter using room-specific settings")
        else:
            settings = self.room_settings['*']
            print("filter using global settings")

        if not (settings['profanity_filter'] or settings['alphabet_filters']):
            return self.FILTER_ACCEPT

        msg = Post(raw=request[b"message_data"])

        prof_result = self.FILTER_ACCEPT
        if settings['profanity_filter'] and profanity_check(msg.text, msg.username):
            reply_settings = self.get_reply_settings(room_token, filter_type='profanity')
            if reply_settings:
                print(f"replying with format: {reply_settings}")
                self.reply(
                    request[b"room_name"],
                    request[b"room_token"],
                    bytes.fromhex(request[b"session_id"].decode()),
                    request[b"message_data"],
                    msg.username,
                    reply_settings=reply_settings,
                )
            prof_result = (
                self.FILTER_REJECT_SILENT if settings['profanity_silent'] else self.FILTER_REJECT
            )

        if not settings['alphabet_filters']:
            return prof_result

        alpha_result = self.FILTER_ACCEPT
        for lang, pattern in self.alphabet_filter_patterns:
            if lang not in settings['alphabet_filters']:
                continue

            if not pattern.search(msg.text):
                continue

            # Filter it!
            filter_type, filter_lang = 'alphabet', lang
            reply_settings = self.get_reply_settings(
                request[b"room_token"], filter_type=filter_type, filter_lang=filter_lang
            )
            if reply_settings:
                print(f"replying with format: {reply_settings}")
                self.reply(
                    request[b"room_name"],
                    request[b"room_token"],
                    bytes.fromhex(request[b"session_id"].decode()),
                    request[b"message_data"],
                    msg.username,
                    reply_settings=reply_settings,
                )

            alpha_result = (
                self.FILTER_REJECT_SILENT if settings['alphabet_silent'] else self.FILTER_REJECT
            )

            break

        if alpha_result == self.FILTER_REJECT or prof_result == self.FILTER_REJECT:
            # Example of re-injecting the message later if some other approval process succeeds:
            # msg_id = self.inject_message(room_token, user_session_id, message_data, sig, whisper_target = whisper_target, whisper_mods = whisper_mods)
            return self.FILTER_REJECT
        elif alpha_result == self.FILTER_REJECT_SILENT or prof_result == self.FILTER_REJECT_SILENT:
            return self.FILTER_REJECT_SILENT

        return self.FILTER_ACCEPT


class SlashTestPlugin(Plugin):

    def __init__(self, sogs_address, sogs_pubkey, privkey, pubkey, display_name):

        Plugin.__init__(self, sogs_address, sogs_pubkey, privkey, pubkey, display_name)
        self.register_pre_command('/test', self.handle_pre_slash)
        self.register_post_command('/test', self.handle_post_slash)
        self.register_pre_command('/test_handled', self.handle_pre_slash)
        self.register_post_command('/test_handled', self.handle_post_slash)
        self.register_pre_command('/get_file', self.handle_get_file)

    def handle_pre_slash(self, request, command_parts):
        print(f"slash pre-insertion command: {command_parts}")
        if command_parts[0] == '/test_handled':
            return False
        return True

    def handle_post_slash(self, request, command_parts):
        print(f"slash post-insertion command: {command_parts}")
        if command_parts[0] == '/test_handled':
            return False
        return True

    def handle_get_file(self, request, command_parts):
        print(f"/get_file pre-insertion command: {command_parts}")

        room_token = request[b'room_token']
        print(f"room_token for file upload: {room_token}")

        file_meta = self.upload_file("test.jpg", room_token)

        if not file_meta:
            print("file upload failed...")
            return False

        print(f"file upload success, file_meta: {file_meta}")

        msg_id = self.post_message(
            room_token,
            "Please work ffs!",
            no_plugins=False,
            attachments_metadata=[file_meta,],
        )

        print(f"Success, msg_id = {msg_id}")

        return False


class PermissionPlugin(Plugin):

    def __init__(
        self,
        sogs_address,
        sogs_pubkey,
        privkey,
        pubkey,
        display_name,
        *args,
        yes_reaction="\N{THUMBS UP SIGN}",
        no_reaction="\N{THUMBS DOWN SIGN}",
        retry_timeout=120,
        write_timeout=120,
    ):

        self.yes_reaction = yes_reaction
        self.no_reaction = no_reaction
        self.pending_requests = {}  # map {session_id : {room_token : msg_id } }
        self.retry_jail = {}
        self.retry_timeout = retry_timeout
        self.write_timeout = write_timeout

        Plugin.__init__(self, sogs_address, sogs_pubkey, privkey, pubkey, display_name)
        self.register_request_read_handler(self.handle_request_read)

    def handle_request_read(self, req):
        room_token = req[b'room_token']
        session_id = req[b'session_id']
        if session_id in self.retry_jail:
            if time() > self.retry_jail[session_id]:
                del self.retry_jail[session_id]
            else:
                return oxenc.bt_serialize("JAIL")

        if session_id in self.pending_requests and room_token in self.pending_requests[session_id]:
            return oxenc.bt_serialize("OK")
        print(f"request_read from {session_id}, id={req[b'user_id']}, room={room_token}")
        msg_id = self.post_message(
            room_token,
            "Please react with a thumbs up to agree to the room rules.",
            whisper_target=session_id,
            no_plugins=True,
        )
        if msg_id:
            react_resp = self.post_reactions(
                room_token, msg_id, self.yes_reaction, self.no_reaction
            )
            if b'error' in react_resp:
                print(f"Error adding reactions to whisper: {react_resp[b'error']}")
                return oxenc.bt_serialize("ERROR")
            if session_id not in self.pending_requests:
                self.pending_requests[session_id] = dict()
            self.pending_requests[session_id][room_token] = msg_id

        return oxenc.bt_serialize("OK")

    def reaction_posted(self, m: oxenmq.Message):
        req = oxenc.bt_deserialize(m.dataview()[0])
        print(f"reaction_posted, req = {req}")
        msg_id = req[b'msg_id']
        session_id = req[b'session_id']
        room_token = req[b'room_token']
        if (
            session_id in self.pending_requests
            and room_token in self.pending_requests[session_id]
            and msg_id == self.pending_requests[session_id][room_token]
        ):
            print(f"reaction_posted, correct session_id, room, and msg_id")
            reaction = req[b'reaction'].decode('utf-8')
            if reaction == self.yes_reaction:
                print(f"Granting read permissions to {session_id} for room with token {room_token}")
                self.set_user_room_permissions(
                    room_token=room_token, user_session_id=session_id, sec_from_now=None, read=True
                )
                self.set_user_room_permissions(
                    room_token=room_token, user_session_id=session_id, sec_from_now=120, write=True
                )
                self.post_message(
                    room_token,
                    f"You may read now.  Study up, and you may learn to write in {self.write_timeout} seconds.",
                    whisper_target=session_id,
                    no_plugins=True,
                )
            else:
                self.post_message(
                    room_token,
                    f"You chose...poorly.  You may try again in {self.retry_timeout} seconds with a new prompt.",
                    whisper_target=session_id,
                    no_plugins=True,
                )
                self.retry_jail[session_id] = time() + self.retry_timeout
            self.delete_message(msg_id)
            del self.pending_requests[session_id][room_token]
            if len(self.pending_requests[session_id]) == 0:
                del self.pending_requests[session_id]


if __name__ == '__main__':

    """
    These are test keys for convenience and if they make it into production *anywhere*, that means
    that someone did something really dumb.
    """
    # server_key_hex = b"3689294e4e49dac8842746ae7011477610e846f30a4f30bedac684fb20f28f65"
    server_key_hex = b'ef5b3bd118ffd0abcb48731b6eb8a9037ee4ed7442f4599088b55bad9d8a480a'
    plugin_privkey_hex = b'489327e8db1e9f6e05c4ad4d75b8bef6aeb8ad78ae6b3d4a74b96455b7438e79'

    from nacl.public import PublicKey

    server_key = PublicKey(HexEncoder.decode(server_key_hex))
    server_key_bytes = server_key.encode()

    privkey = SigningKey(HexEncoder.decode(plugin_privkey_hex))
    print(f"privkey: {privkey.encode(HexEncoder)}")
    privkey_bytes = privkey.encode()
    pubkey_bytes = privkey.verify_key.encode()
    print(f"pubkey: {privkey.verify_key.encode(HexEncoder)}")
    # plugin = TestPlugin(
    #     "tcp://127.0.0.1:43210", server_key_bytes, privkey_bytes, pubkey_bytes
    # )
    # plugin = SogsFilterPlugin(
    #     privkey_bytes, pubkey_bytes, config_file='sogs.ini'
    # )
    # plugin = PermissionPlugin(
    #     "tcp://127.0.0.1:43210", server_key_bytes, privkey_bytes, pubkey_bytes, "Permissions Plugin"
    # )
    plugin = SlashTestPlugin(
        "tcp://127.0.0.1:43210", server_key_bytes, privkey_bytes, pubkey_bytes, "Slash Test Plugin"
    )

    plugin.run()
