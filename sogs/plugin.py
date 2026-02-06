import nacl.bindings as sodium
import typing
import dataclasses
import oxenmq
import oxenc
import logging
import enum
import typing_extensions
import datetime
import configparser

from .types import SessionID, MessageID, bt_value, PluginInsertMessage, RoomAddPostRequest, ReactionPosted
from typing          import Callable, Dict, List, Optional, Union
from datetime        import timedelta
from sogs.model.post import Post

console_log_handler = logging.StreamHandler()
console_log_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s %(message)s'))

log = logging.Logger('PLUGIN')
log.addHandler(console_log_handler)

class FilterResponse(enum.Enum):
    Accept = "OK"
    Reject = "REJECT"
    Silent = "SILENT"

    @typing_extensions.override
    def __str__(self):
        return self.value

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
    reply_formats: List[str] = dataclasses.field(default_factory=list)
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
    def from_bencode(cls, src: Dict[bytes, bt_value]):
        result = RoomReadRequest(room_id     = typing.cast(int, src[b'room_id']),
                                 room_name  = typing.cast(bytes, src[b'room_name']).decode('utf-8'),
                                 room_token = typing.cast(bytes, src[b'room_token']),
                                 session_id = bytes.fromhex(typing.cast(bytes, src[b'session_id']).decode('utf-8')),
                                 user_id    = typing.cast(int, src[b'user_id']),)
        return result

class SetUserRoomPermissionsResponse(enum.Enum):
    NoSuchRoom = 0
    NoSuchUser = 1
    Error      = 2
    InvalidArg = 3
    Ok         = 4

@dataclasses.dataclass
class SetUserRoomPermissions:
    room_id:         Optional[int]       = None
    room_token:      Optional[bytes]     = None
    user_id:         Optional[int]       = None
    user_session_id: Optional[SessionID] = None
    accessible:      Optional[bool]      = None
    read:            Optional[bool]      = None
    write:           Optional[bool]      = None
    in_s:            Optional[int]       = None

    @classmethod
    def from_bencode(cls, src: Dict[bytes, bt_value]):
        result = SetUserRoomPermissions()
        if b'room_id' in src:
            result.room_id = typing.cast(int, src[b'room_id'])
        elif b'room_token' in src:
            result.room_token = typing.cast(bytes, src[b'room_token'])

        if b'user_id' in src:
            result.user_id = typing.cast(int, src[b'user_id'])
        elif b'user_session_id' in src:
            result.user_session_id = bytes.fromhex(typing.cast(bytes, src[b'user_session_id']).decode('utf-8'))

        if b'accessible' in src:
            result.accessible = typing.cast(bool, src[b'accessible'])
        if b'read' in src:
            result.read = typing.cast(bool, src[b'read'])
        if b'write' in src:
            result.write = typing.cast(bool, src[b'write'])

        if b'in' in src:
            result.in_s = typing.cast(int, src[b'in'])
        return result

    def to_bencode(self) -> Dict[bytes, bt_value]:
        result: Dict[bytes, bt_value] = {}

        if self.room_id is not None:
            result[b'room_id'] = self.room_id
        elif self.room_token is not None:
            result[b'room_token'] = self.room_token

        if self.user_id is not None:
            result[b'user_id'] = self.user_id
        elif self.user_session_id is not None:
            result[b'user_session_id'] = self.user_session_id.hex().encode('utf-8')

        if self.accessible is not None:
            result[b'accessible'] = self.accessible
        if self.read is not None:
            result[b'read'] = self.read
        if self.write is not None:
            result[b'write'] = self.write

        if self.in_s is not None:
            result[b'in'] = self.in_s
        return result

@dataclasses.dataclass
class PluginConfigFromINI:
    ini:          configparser.ConfigParser = dataclasses.field(default_factory=lambda: configparser.ConfigParser(strict=False))
    success:      bool                      = False
    sogs_address: str                       = ''
    sogs_pubkey:  bytes                     = b''

@dataclasses.dataclass
class Plugin:
    # User initialised
    ed_privkey:           bytes # ed25519
    display_name:         str
    sogs_address:         str
    sogs_pubkey:          bytes

    # Default values
    running:              bool                                                                 = False
    last_post_time:       int                                                                  = 0
    pre_slash_handlers:   Dict[str, typing.Callable[[Dict[bytes, bt_value], List[str]], bool]] = dataclasses.field(default_factory=dict)
    post_slash_handlers:  Dict[str, typing.Callable[[Dict[bytes, bt_value], List[str]], bool]] = dataclasses.field(default_factory=dict)
    request_read_handler: Optional[typing.Callable[[RoomReadRequest], bt_value]]               = None
    conn:                 Optional[oxenmq.ConnectionID]                                        = None

    # Post initialised
    ed_pubkey:            bytes               = dataclasses.field(init=False) # 32 byte ed25519 public key
    session_id:           SessionID           = dataclasses.field(init=False) # 33 byte 15-blinded x25519 pubkey  (w/  15-prefix)
    blind25_pubkey:       bytes               = dataclasses.field(init=False) # 32 byte 25-blinded x25519 pubkey  (w/o 25-prefix)
    blind25_privkey:      bytes               = dataclasses.field(init=False) # 32 byte 25-blinded x25519 privkey (w/o 25-prefix)
    blind15_pubkey:       bytes               = dataclasses.field(init=False) # 32 byte 15-blinded x25519 pubkey  (w/o 15-prefix)
    blind15_privkey:      bytes               = dataclasses.field(init=False) # 32 byte 15-blinded x25519 privkey (w/o 15-prefix)
    x_pubkey:             bytes               = dataclasses.field(init=False) # 32 byte x25519 pubkey (non-blinded Session ID)
    x_privkey:            bytes               = dataclasses.field(init=False) # 32 byte x25519 pubkey (non-blinded Session ID)
    omq:                  oxenmq.OxenMQ       = dataclasses.field(init=False)

    @staticmethod
    def load_ini_from_path(ini_path: str) -> PluginConfigFromINI:
        from sogs import config as sogs_config

        # Setup and load config file from disk
        parsed_ini            = configparser.ConfigParser(strict=False)
        files_read: List[str] = parsed_ini.read(ini_path)

        if len(files_read) != 1:
            log.warning(f"Plugin .ini config file does not exist, terminating plugin. File was: {ini_path}")
            return PluginConfigFromINI()

        # Load fields common to all plugins
        sogs_address:    str        = parsed_ini.get('plugin', 'sogs_address',    fallback=sogs_config.OMQ_LISTEN)
        sogs_pubkey_hex: Optional[str] = parsed_ini.get('plugin', 'sogs_pubkey_hex', fallback=None)

        # Convert pubkey to bytes
        if not sogs_pubkey_hex:
            log.error(f"INI config file field 'sogs_pubkey_hex' is missing. File was: {ini_path}")
            return PluginConfigFromINI()

        if sogs_pubkey_hex.startswith("0x"):
            sogs_pubkey_hex = sogs_pubkey_hex[2:]

        try:
            sogs_pubkey: bytes = bytes.fromhex(sogs_pubkey_hex)
        except Exception:
            log.error(f"Config file field 'sogs_pubkey_hex' was not a valid hex string: {sogs_pubkey_hex}")
            return PluginConfigFromINI()

        result = PluginConfigFromINI(ini=parsed_ini, success=True, sogs_address=sogs_address, sogs_pubkey=sogs_pubkey)
        return result

    @staticmethod
    def get_or_make_ed25519_privkey(key_file: str) -> bytes:
        import pathlib
        result: bytes = b''
        dest_path     = pathlib.Path(key_file)
        try:
            result = dest_path.read_bytes()
            if len(result) == sodium.crypto_sign_SEEDBYTES:
                (_, result) = sodium.crypto_sign_seed_keypair(result)
        except FileNotFoundError:
            (_, result) = sodium.crypto_sign_keypair()
            bytes_written = dest_path.write_bytes(result)
            assert bytes_written == sodium.crypto_sign_SECRETKEYBYTES, f"Failed to write plugin key to {key_file}, aborting"
        assert len(result) == sodium.crypto_sign_SECRETKEYBYTES
        return result

    def __post_init__(self):
        """Generate the derivative keys based given the Session Account's Ed25519 key-pairing and
        sets up an OxenMQ connection to the SOGS server"""

        if len(self.ed_privkey) != 64:
            raise Exception("SOGS plugin must specify a Ed25519 64b private keypair (privkey: {len(self.privkey)}b")

        # Generate Ed25519 public key and X25519 keys
        self.ed_pubkey                                 = sodium.crypto_sign_ed25519_sk_to_pk(self.ed_privkey)
        self.x_privkey                                 = sodium.crypto_sign_ed25519_sk_to_curve25519(self.ed_privkey)
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
        cat.add_command        ("on_reaction_posted",   self.on_reaction_posted)
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
            pre_commands: List[str] = list(self.pre_slash_handlers.keys())
            if self.request_read_handler:
                pre_commands.append('/request_read')

            print(f"Registering pre-commands: {pre_commands}")
            self.omq.send(conn, "plugin.register_pre_commands", oxenc.bt_serialize({b'commands': pre_commands}))

        if len(self.post_slash_handlers):
            post_commands: List[str] = list(self.post_slash_handlers.keys())

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

    def handle_message_command(self, m: oxenmq.Message, pre_command: bool) -> bytes:
        req = oxenc.bt_deserialize(m.dataview()[0])
        msg = Post(raw=req[b"message_data"])

        command_parts = typing.cast(str, msg.text).split(' ')
        if not command_parts: # shouldn't be possible, but false just to signal it happened
            return oxenc.bt_serialize(False)

        command           = command_parts[0]
        command_container = self.pre_slash_handlers if pre_command else self.post_slash_handlers
        if not command in command_container:
            return oxenc.bt_serialize(True)

        try:
            retval: bool = command_container[command](req, command_parts)
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
        req       = typing.cast(Dict[bytes, bt_value], oxenc.bt_deserialize(m.dataview()[0]))
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

    def register_command(self, command: str, handler: typing.Callable[[Dict[bytes, bt_value], List[str]], bool], pre_command: bool):
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

    def register_pre_command(self, command: str, handler: typing.Callable[[Dict[bytes, bt_value], List[str]], bool]):
        """
        Register a handler for slash commands that runs BEFORE the message is inserted.

        Handler receives (request_data, command_parts) and must return a bool. Return False to
        reject the message and prevent database insertion. Use for validation, rate limiting, or
        commands that shouldn't be stored.
        """
        self.register_command(command, handler, True)

    def register_post_command(self, command: str, handler: typing.Callable[[Dict[bytes, bt_value], List[str]], bool]):
        """
        Register a handler for slash commands that runs AFTER the message is inserted.

        Handler receives (request_data, command_parts). Return value is ignored since the message is
        already stored. Use for side effects like replies, reactions, logging, or triggering
        follow-up actions.
        """
        self.register_command(command, handler, False)

    def filter_message(self, m: oxenmq.Message):
        try:
            req                  = RoomAddPostRequest.from_bencode(oxenc.bt_deserialize(m.dataview()[0]))
            resp: FilterResponse = self.filter(req)
            return oxenc.bt_serialize(str(resp))
        except Exception as e:
            print(f"Exception filtering message: {e}")
            return oxenc.bt_serialize(str(FilterResponse.Reject))

    def filter(self, req: RoomAddPostRequest) -> FilterResponse:  # pyright: ignore[reportUnusedParameter]
        """
        Users may override this function for custom filtering, or supply a callable filter object
        """
        return FilterResponse.Accept

    def reply(
        self,
        room_name:       bytes,
        room_token:      bytes,
        user_session_id: SessionID,
        username:        Optional[str],
        reply_settings:  ReplySettings,
    ) -> Optional[MessageID]:
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
            room_token   = room_token)

        result: Optional[MessageID] = self.post_message(room_token=room_token,
                                                        body=body,
                                                        whisper_target=None if reply_settings.public else user_session_id)
        return result;

    def set_user_room_permissions(self,
                                  room:         Optional[Union[bytes, int]]     = None,
                                  user:         Optional[Union[SessionID, int]] = None,
                                  sec_from_now: Optional[int]                   = None,
                                  accessible:   Optional[bool]                  = None,
                                  read:         Optional[bool]                  = None,
                                  write:        Optional[bool]                  = None) -> SetUserRoomPermissionsResponse:
        """Set the permission(s) of the user for the room

        The following parameters must be set, or otherwise this function returns InvalidArg:

          - Room must be set to either the room token in bytes (e.g.: b'foobar') or ID of the room.
          - User must be set to either the user's 33b blinded Session ID or the ID of the user.
          - At least one of the permissions must be set, accessible, read, or write.
        """
        req = SetUserRoomPermissions()

        # NOTE: Set the room
        if isinstance(room, int):
            req.room_id = room
        elif isinstance(room, bytes):
            req.room_token = room
        else:
            print("Room identifier (token `bytes` or id `int`) is required for permissions changes.")
            return SetUserRoomPermissionsResponse.InvalidArg

        # NOTE: Set the user
        if isinstance(user, SessionID):
            if len(user) != 33:
                print("User passed as `SessionID` must be a 33b blinded public key for permissions changes.")
                return SetUserRoomPermissionsResponse.InvalidArg
            req.user_session_id = user
        elif isinstance(user, int):
            req.user_id = user
        else:
            print("User (`SessionID` or id `int`) is required for permissions changes.")
            return SetUserRoomPermissionsResponse.InvalidArg

        # NOTE: Set permissions
        if not accessible and not read and not write:
            print("At least one permission should be specified (`accessible`, `read`, `write`) for permissions changes.")
            return SetUserRoomPermissionsResponse.InvalidArg
        if accessible is not None:
            req.accessible = accessible
        if read is not None:
            req.read = read
        if write is not None:
            req.write = write

        # NOTE: Set enqueued permission change
        if sec_from_now:
            UPPER_BOUND: int = 1_000_000_000
            if not 0 < sec_from_now < UPPER_BOUND:
                print(f"Enqueuing a permission change in the future must be bounded between [0 < {sec_from_now} < {UPPER_BOUND}]")
                return SetUserRoomPermissionsResponse.InvalidArg

            req.in_s = sec_from_now

        # NOTE: Request and response
        result                         = SetUserRoomPermissionsResponse.Error
        conn:      oxenmq.ConnectionID = self._require_conn_established();
        future:    oxenmq.ResultFuture = self.omq.request_future(conn, "plugin.set_user_room_permissions", oxenc.bt_serialize(req.to_bencode()), request_timeout=timedelta(seconds=5))
        resp_list: List[bytes]         = future.get()
        resp:      bytes               = resp_list[0]
        assert len(resp_list) == 1
        if resp == b"OK":
            result = SetUserRoomPermissionsResponse.Ok
        elif resp == b"NoSuchRoom":
            result = SetUserRoomPermissionsResponse.NoSuchRoom
        elif resp == b"NoSuchUser":
            result = SetUserRoomPermissionsResponse.NoSuchUser
        return result

    def delete_messages(self, msg_ids: List[MessageID]) -> bool:
        """Request SOGs to delete the specified message(s). The message(s) must have been created by
        this plugin.
        """
        result = True
        if len(msg_ids):
            conn:      oxenmq.ConnectionID = self._require_conn_established();
            future:    oxenmq.ResultFuture = self.omq.request_future(conn, "plugin.delete_message", oxenc.bt_serialize({b'msg_ids': msg_ids}))
            resp_list: List[bytes]         = future.get()
            assert len(resp_list) == 1

            resp = typing.cast(Dict[bytes, bt_value], oxenc.bt_deserialize(resp_list[0]))
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
                     whisper_to:           Optional[int] = None,
                     relay_to_plugins:     bool = False,
                     attachments_metadata: Optional[List[Dict[str, typing.Any]]] = None) -> Optional[MessageID]:
        from sogs import session_pb2 as protobuf
        from time import time
        self.last_post_time = max(self.last_post_time + 1, int(time() * 1000))

        content                                 = protobuf.Content()
        content.dataMessage.body                = body.encode()
        content.dataMessage.timestamp           = self.last_post_time
        content.dataMessage.profile.displayName = self.display_name

        attachment_ids: List[int] = []
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
        sig:    bytes               = blind15_sign(self.ed_privkey, self.sogs_pubkey, content)
        result: Optional[MessageID] = self._insert_message(room_token, self.session_id, content, sig, whisper_to=whisper_to, relay_to_plugins=relay_to_plugins, attachment_ids=attachment_ids)
        return result

    def _insert_message(self,
                        room_token:       bytes,
                        session_id:       SessionID,
                        message:          bytes,
                        sig:              bytes,
                        *,
                        relay_to_plugins: bool                = False,
                        whisper_mods:     bool                = False,
                        whisper_to:       Optional[int]       = None,
                        attachment_ids:   Optional[List[int]] = None,) -> Optional[MessageID]:
        req = PluginInsertMessage(room_token       = room_token,
                                  session_id       = session_id,
                                  message_data     = message,
                                  sig              = sig,
                                  whisper_mods     = whisper_mods,
                                  whisper_to       = whisper_to,
                                  attachment_ids   = attachment_ids or [],
                                  relay_to_plugins = relay_to_plugins,)
        conn: oxenmq.ConnectionID = self._require_conn_established()
        resp = oxenc.bt_deserialize(self.omq.request_future(conn, "plugin.insert_message", req.to_bencode(), request_timeout=timedelta(seconds=5)).get()[0])
        if not b'msg_id' in resp:
            return None

        msg_id = typing.cast(MessageID, resp[b'msg_id'])
        print(f"Message inserted, id: {msg_id}")
        return msg_id

    def post_reactions(self, room_token: bytes, msg_id: MessageID, *reactions: str) -> Dict[bytes, bt_value]:
        conn: oxenmq.ConnectionID = self._require_conn_established()
        req = {b"room_token": room_token, b"msg_id": msg_id, b"reactions": reactions}
        print(f"post_reactions request: {req}")
        return oxenc.bt_deserialize(self.omq.request_future( conn, "plugin.post_reactions", oxenc.bt_serialize(req), request_timeout=timedelta(seconds=5)).get()[0])

    def remove_reactions(self, room_token: bytes, msg_id: MessageID, *reactions: str):
        req = {b"room_token": room_token, b"msg_id": msg_id, b"reactions": reactions}
        print(f"post_reactions request: {req}")

        conn: oxenmq.ConnectionID = self._require_conn_established()
        return oxenc.bt_deserialize(
            self.omq.request_future(
                conn,
                "plugin.remove_reactions",
                oxenc.bt_serialize(req),
                request_timeout=timedelta(seconds=5),
            ).get()[0]
        )

    def upload_file(self, file_path: str, room_token: bytes, display_filename: Optional[str] = None):
        try:
            from os import path
            filename = display_filename if display_filename else path.basename(file_path)

            from pathlib import Path
            file_contents = Path(file_path).read_bytes()

            req: Dict[bytes, bt_value] = {
                b"filename":      filename,
                b"file_contents": file_contents,
                b"room_token":    room_token
            }

            conn: oxenmq.ConnectionID = self._require_conn_established()
            resp = oxenc.bt_deserialize(self.omq.request_future(conn, "plugin.upload_file", oxenc.bt_serialize(req), request_timeout=timedelta(seconds=3),).get()[0])

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
            mime                    = mimetypes.guess_type(file_path)
            metadata["contentType"] = mime[0]
            if typing.cast(str, mime[0]).startswith("image"):
                img                = Image.open(file_path)
                width, height      = img.size
                metadata["width"]  = width
                metadata["height"] = height

            return metadata

        except Exception as e:
            print(f"upload_file exception: {e}")
            return None

    def message_posted(self, m: oxenmq.Message):  # pyright: ignore[reportUnusedParameter]
        """Handle message posted events from SOGS, override this in your plugin to customise the behaviour"""
        pass

    def on_reaction_posted(self, m: oxenmq.Message):  # pyright: ignore[reportUnusedParameter]
        """Handle reaction posted events from SOGS, override this in your plugin to customise the behaviour"""
        _ = ReactionPosted.from_bencode(m.dataview()[0]) # Parse payload and use as needed

