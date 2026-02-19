import nacl.bindings as sodium
import typing
import dataclasses
import oxenmq
import oxenc
import logging
import enum
import typing_extensions
import configparser

from .types import (
    SessionID,
    MessageID,
    RoomToken,
    bt_value,
    PluginInsertMessage,
    PluginHelloRequest,
    PluginUploadFileRequest,
    PluginUploadFileResponse,
    FileUploadMetadata,
    RoomAddPostRequest,
    ReactionPosted,
    MessagePosted,
    PluginDeleteMessageRequest,
    PluginDeleteMessageResponse,
)
from typing          import Callable, Dict, List, Optional, Tuple, Union, Tuple
from datetime        import timedelta
from sogs.model.post import Post

log                 = logging.getLogger('PLUGIN')
console_log_handler = logging.StreamHandler()
console_log_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s %(message)s'))

def setup_plugin_logging(ini: Optional[configparser.ConfigParser] = None, plugin_section: Optional[str] = None):
    """Configure plugin logging with optional colored output.

    Log level is determined by (in order of precedence):
      1. [plugin_<name>] section's 'log_level' field (if plugin_section provided and field exists)
      2. [log] section's 'level' field (if exists in ini)
      3. Default: INFO

    If coloredlogs is installed, colored output will be used. Otherwise falls back to
    the standard console handler.

    Args:
        ini: Parsed ConfigParser from the plugin's .ini file
        plugin_section: The plugin-specific section name (e.g., 'plugin_sogs_filter')
    """
    level = 'INFO'
    if ini:
        if plugin_section and ini.has_option(plugin_section, 'log_level'):
            level = ini.get(plugin_section, 'log_level')
        elif ini.has_option('log', 'level'):
            level = ini.get('log', 'level')

    log.setLevel(level)

    try:
        import coloredlogs
        coloredlogs.install(milliseconds=True, isatty=True, logger=log, level=level)
    except ImportError:
        log.addHandler(console_log_handler)

class FilterResponse(enum.Enum):
    Accept = "OK"     # Accept message, it will be posted and visible in the room
    Reject = "REJECT" # Reject message, message will not be posted
    Silent = "SILENT" # Accept message, it will be posted but only visible to the user that posted it

    @typing_extensions.override
    def __str__(self):
        return self.value

@dataclasses.dataclass
class FilterResult:
    status: FilterResponse
    reason: Optional[str] = None

    @staticmethod
    def accept() -> "FilterResult":
        return FilterResult(status=FilterResponse.Accept)

    @staticmethod
    def reject(reason: Optional[str] = None) -> "FilterResult":
        return FilterResult(status=FilterResponse.Reject, reason=reason)

    @staticmethod
    def silent(reason: Optional[str] = None) -> "FilterResult":
        return FilterResult(status=FilterResponse.Silent, reason=reason)

    @staticmethod
    def from_dict(src: Dict[bytes, bt_value]) -> "FilterResult":
        status_str = typing.cast(bytes, src.get(b'status', b'REJECT')).decode('utf-8')
        if status_str == str(FilterResponse.Accept):
            status = FilterResponse.Accept
        elif status_str == str(FilterResponse.Silent):
            status = FilterResponse.Silent
        else:
            status = FilterResponse.Reject

        reason: Optional[str] = None
        if b'reason' in src:
            reason = typing.cast(bytes, src[b'reason']).decode('utf-8')

        result = FilterResult(status=status, reason=reason)
        return result

    @staticmethod
    def from_bencode(data: Union[bytes, memoryview]) -> "FilterResult":
        d: Dict[bytes, bt_value] = oxenc.bt_deserialize(data)
        result                   = FilterResult.from_dict(d)
        return result

    def to_dict(self) -> Dict[bytes, bytes]:
        result: Dict[bytes, bytes] = {b'status': str(self.status).encode('utf-8')}
        if self.reason:
            result[b'reason'] = self.reason.encode('utf-8')
        return result

    def to_bencode(self) -> bytes:
        result = oxenc.bt_serialize(self.to_dict())
        return result

@dataclasses.dataclass
class RoomReadRequest:
    room_id:    int
    room_name:  str
    room_token: RoomToken
    session_id: SessionID
    user_id:    int

    @classmethod
    def from_bencode(cls, src: Dict[bytes, bt_value]):
        result = RoomReadRequest(room_id     = typing.cast(int, src[b'room_id']),
                                 room_name  = typing.cast(bytes, src[b'room_name']).decode('utf-8'),
                                 room_token = typing.cast(bytes, src[b'room_token']).decode('utf-8'),
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
    room_token:      Optional[RoomToken] = None
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
            result.room_token = typing.cast(bytes, src[b'room_token']).decode('utf-8')

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
class RegisterPluginResult:
    plugin_id:    int
    was_inserted: bool

@dataclasses.dataclass
class RegisterPluginToRoomConfig:
    room:      Union[int, bytes] # Room identifier - either the room ID (int) or token (bytes).
    approver:  bool = False      # If True, the plugin can deny/disapprove messages in this room.
    required:  bool = False      # If True, the plugin's approval is required for messages in this room.
    subscribe: bool = True       # If True, the plugin receives notifications for all new messages in this room.

@dataclasses.dataclass
class PluginConfigFromINI:
    ini:          configparser.ConfigParser = dataclasses.field(default_factory=lambda: configparser.ConfigParser(strict=False))
    success:      bool                      = False
    sogs_address: str                       = ''
    sogs_pubkey:  bytes                     = b''
    display_name: str                       = ''

@dataclasses.dataclass
class Plugin:
    # User initialised
    ed_privkey:           bytes # ed25519
    display_name:         str
    sogs_address:         str
    sogs_pubkey:          bytes

    # Default values
    running:                    bool                                                              = False
    last_post_time:             int                                                               = 0
    pre_slash_handlers:         Dict[str, typing.Callable[[RoomAddPostRequest, List[str]], bool]] = dataclasses.field(default_factory=dict)
    post_slash_handlers:        Dict[str, typing.Callable[[RoomAddPostRequest, List[str]], bool]] = dataclasses.field(default_factory=dict)
    on_request_read_handler:    Optional[typing.Callable[[RoomReadRequest], bt_value]]            = None
    on_reaction_posted_handler: Optional[typing.Callable[[oxenmq.Message, ReactionPosted], None]] = None
    on_message_posted_handler:  Optional[typing.Callable[[oxenmq.Message, MessagePosted], None]]  = None
    conn:                       Optional[oxenmq.ConnectionID]                                     = None

    # Post initialised
    ed_pubkey:            bytes               = dataclasses.field(init=False) # 32 byte ed25519 public key
    session_id:           SessionID           = dataclasses.field(init=False) # 33 byte 15-blinded x25519 pubkey  (w/  15-prefix)
    blind25_pubkey:       bytes               = dataclasses.field(init=False) # 32 byte 25-blinded x25519 pubkey  (w/o 25-prefix)
    blind25_privkey:      bytes               = dataclasses.field(init=False) # 32 byte 25-blinded x25519 privkey (w/o 25-prefix)
    blind15_pubkey:       bytes               = dataclasses.field(init=False) # 32 byte 15-blinded x25519 pubkey  (w/o 15-prefix)
    blind15_privkey:      bytes               = dataclasses.field(init=False) # 32 byte 15-blinded x25519 privkey (w/o 15-prefix)
    x_pubkey:             bytes               = dataclasses.field(init=False) # 32 byte x25519 pubkey  (non-blinded Session ID)
    x_privkey:            bytes               = dataclasses.field(init=False) # 32 byte x25519 privkey (non-blinded Session ID)
    omq:                  oxenmq.OxenMQ       = dataclasses.field(init=False)

    @staticmethod
    def load_ini_from_path(ini_path: str, default_display_name: Optional[str] = None) -> PluginConfigFromINI:
        from sogs import config as sogs_config

        # Setup and load config file from disk
        parsed_ini            = configparser.ConfigParser(strict=False)
        files_read: List[str] = parsed_ini.read(ini_path)

        if len(files_read) != 1:
            log.warning(f"Plugin .ini config file does not exist, terminating plugin. File was: {ini_path}")
            return PluginConfigFromINI()

        # Load fields common to all plugins
        sogs_address:    str           = parsed_ini.get('plugin', 'sogs_address',    fallback=sogs_config.OMQ_LISTEN)
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

        display_name = ''
        if default_display_name:
            display_name = default_display_name
        else:
            display_name = parsed_ini.get('plugin', 'display_name', fallback=f"{sogs_pubkey_hex[:4]}..{sogs_pubkey_hex[:-4]}")

        result = PluginConfigFromINI(ini          = parsed_ini,
                                     success      = True,
                                     sogs_address = sogs_address,
                                     sogs_pubkey  = sogs_pubkey,
                                     display_name = display_name)
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

    def describe_config(self) -> List[Tuple[str, str]]:
        result: List[Tuple[str, str]] = [
            ("SOGS Address (Pubkey)",      f"{self.sogs_address} ({self.sogs_pubkey.hex()})"),
            ("Display Name",               self.display_name),
            ("Ed25519 Pubkey",             self.ed_pubkey.hex()),
            ("X25519 Pubkey",              self.x_pubkey.hex()),
            ("Session Account (Blind-15)", self.session_id.hex()),
        ]
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
        self.omq = oxenmq.OxenMQ(privkey=self.x_privkey, pubkey=self.x_pubkey, log_level=oxenmq.LogLevel.warn)
        cat      = self.omq.add_category("plugin", access_level=oxenmq.AuthLevel.none)
        cat.add_request_command("filter_message",       self.filter_message)
        cat.add_command        ("message_posted",       self._on_message_posted)
        cat.add_command        ("reaction_posted",      self._on_reaction_posted)
        cat.add_request_command("pre_message_command",  self.pre_message_command)
        cat.add_request_command("post_message_command", self.post_message_command)
        cat.add_request_command("request_read",         self._on_request_read)

    def _require_conn_established(self) -> oxenmq.ConnectionID:
        assert self.conn, "Plugin misuse: Connection to SOGS not established yet, plugin.run() must be called first"
        return self.conn

    def _on_registered_after_hello(self):
        conn: oxenmq.ConnectionID = self._require_conn_established()

        # NOTE: Subscribe to the following hooks on SOGS. SOGs will call invoke this plugin via
        # OxenMQ when the commands are triggered.
        if len(self.pre_slash_handlers) or self.on_request_read_handler:
            pre_commands: List[str] = list(self.pre_slash_handlers.keys())
            if self.on_request_read_handler:
                pre_commands.append('/request_read')

            log.debug(f"Registering {len(pre_commands)} pre-command(s): {pre_commands}")
            self.omq.send(conn, "plugin.register_pre_commands", oxenc.bt_serialize({b'commands': pre_commands}))

        if len(self.post_slash_handlers):
            post_commands: List[str] = list(self.post_slash_handlers.keys())

            log.debug(f"Registering {len(post_commands)} post-command(s): {post_commands}")
            self.omq.send(conn, "plugin.register_post_commands", oxenc.bt_serialize({b'commands': list(post_commands)}))

    def _say_hello(self):
        conn: oxenmq.ConnectionID = self._require_conn_established()
        try:
            hello                       = PluginHelloRequest(session_id=self.session_id)
            future: oxenmq.ResultFuture = self.omq.request_future(conn, "plugin.hello", hello.to_bencode(), request_timeout=timedelta(seconds=10))
            resp:   bytes               = typing.cast(bytes, oxenc.bt_deserialize(future.get()[0]))
            if resp == b'OK':
                return
            elif resp == b"REGISTER":
                self._on_registered_after_hello()
                connect_str = "reconnected" if self.running else "connected"
                log.info(f"Plugin '{self.display_name}' (0x{self.ed_pubkey[:2].hex()}..{self.ed_pubkey[-2:].hex()}) {connect_str} to SOGS at {self.sogs_address} (0x{self.sogs_pubkey[:2].hex()}..{self.sogs_pubkey[-2:].hex()}) ✅")
                self.running = True
                return
            log.error(f"Unexpected response from SOGS during hello: {resp}")
        except Exception as e:
            log.error(f"Failed to complete hello handshake: {e}")

    def run(self):
        self.omq.start()
        try:
            self.conn = self.omq.connect_remote(oxenmq.Address(self.sogs_address, self.sogs_pubkey))
        except Exception as e:
            raise RuntimeError((f"!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n"
                                f"!!\n"
                                f"!! ⛔ {e} ⛔\n"
                                f"!!\n"
                                f"!!  1. Check that `sogs_address` in [plugin] is set to the correct SOGS address and is contactable\n"
                                f"!!\n"
                                f"!!       {self.sogs_address}\n"
                                f"!!\n"
                                f"!!  2. Check that `sogs_address` in [plugin] is set to the correct SOGS public key\n"
                                f"!!\n"
                                f"!!       {self.sogs_pubkey.hex()}\n"
                                f"!!\n"
                                f"!!  3. Register the plugin onto SOGS if it hasn't been already. The following command registers globally\n"
                                f"!!     across all-rooms (see --help for more information on these options)\n"
                                f"!!\n"
                                f"!!       python3 -msogs --add-plugin {self.ed_pubkey.hex()} --plugin-name '{self.display_name}' --plugin-global true --plugin-approver true --plugin-required true --plugin-subscribe true\n"
                                f"!!\n"
                                f"!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n"))

        # FIXME: there's definitely a better way to do this, but if SOGS restarts and
        #        we reconnect, this makes SOGS recognize our omq connection as this plugin.
        count = 0
        while True:
            if count == 0 or count % 60 == 0:
                self._say_hello()
            count += 1
            from time import sleep
            sleep(1)

    def register_on_request_read_handler(self, handler: Callable[[RoomReadRequest], bt_value]):
        """
        If a user attempts to read a room but has only "access" to the room, this will be called
        (if registered).

        Currently SOGS does nothing with the response from this request, but responding signals
        the plugin is done handling it.  This is so SOGS waits to respond to that user until e.g.
        the plugin has had the chance to whisper the user (so the user will see the whisper right away).
        Any return value from the handler will be ignored until SOGS has use for it.
        """
        self.on_request_read_handler = handler

        # if not running, finish_init() will do this once connected
        if self.running:
            conn: oxenmq.ConnectionID = self._require_conn_established()
            self.omq.send(conn, f"plugin.register_pre_commands", oxenc.bt_serialize({b"commands": ["request_read"]}))

    def register_on_reaction_posted_handler(self, handler: Callable[[oxenmq.Message, ReactionPosted], None]):
        self.on_reaction_posted_handler = handler

    def register_on_message_posted_handler(self, handler: Callable[[oxenmq.Message, MessagePosted], None]):
        self.on_message_posted_handler = handler

    def handle_message_command(self, m: oxenmq.Message, pre_command: bool) -> bytes:
        req = RoomAddPostRequest.from_bencode(m.dataview()[0])
        msg = Post(raw=req.message_data)

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
            log.error(f"Failed to handle slash command '{command}': {e}")
            return oxenc.bt_serialize(True)

    def pre_message_command(self, m: oxenmq.Message):
        return self.handle_message_command(m, True)

    def post_message_command(self, m: oxenmq.Message):
        return self.handle_message_command(m, False)

    def _on_request_read(self, m: oxenmq.Message):
        # Example
        #  {b'room_id': 1, b'room_name': b'foobar', b'room_token': b'foobar', b'session_id': b'1500784b7c2096f6ed811b25c53a63e551954ee6778c7ae4437cb01c4b01fb4a09', b'user_id': 3}
        req       = typing.cast(Dict[bytes, bt_value], oxenc.bt_deserialize(m.dataview()[0]))
        room_info = RoomReadRequest.from_bencode(req)

        # this should not be called by sogs if we didn't register it...
        if not self.on_request_read_handler:
            return oxenc.bt_serialize(False)
        try:
            self.on_request_read_handler(room_info)
        except Exception as e:
            import traceback
            log.error(f"Failed to handle request_read for room '{room_info.room_token}': {traceback.format_exc()}")
        return oxenc.bt_serialize(True)

    def register_command(self, command: str, handler: typing.Callable[[RoomAddPostRequest, List[str]], bool], pre_command: bool):
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

    def register_pre_command(self, command: str, handler: typing.Callable[[RoomAddPostRequest, List[str]], bool]):
        """
        Register a handler for slash commands that runs BEFORE the message is inserted.

        Handler receives (request_data, command_parts) and must return a bool. Return False to
        reject the message and prevent database insertion. Use for validation, rate limiting, or
        commands that shouldn't be stored.
        """
        self.register_command(command, handler, True)

    def register_post_command(self, command: str, handler: typing.Callable[[RoomAddPostRequest, List[str]], bool]):
        """
        Register a handler for slash commands that runs AFTER the message is inserted.

        Handler receives (request_data, command_parts). Return value is ignored since the message is
        already stored. Use for side effects like replies, reactions, logging, or triggering
        follow-up actions.
        """
        self.register_command(command, handler, False)

    def filter_message(self, m: oxenmq.Message):
        try:
            req              = RoomAddPostRequest.from_bencode(m.dataview()[0])
            result: FilterResult = self.filter(req)
            return result.to_bencode()
        except Exception as e:
            log.error(f"Failed to filter message: {e}")
            return FilterResult.reject(reason=str(e)).to_bencode()

    def filter(self, req: RoomAddPostRequest) -> FilterResult:  # pyright: ignore[reportUnusedParameter]
        """
        Users may override this function for custom filtering, or supply a callable filter object.
        Returns a FilterResult with status and optional reason for filtering.
        """
        return FilterResult.accept()

    def set_user_room_permissions(self,
                                  room:         Optional[Union[RoomToken, int]] = None,
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
        elif isinstance(room, RoomToken):
            req.room_token = room
        else:
            log.warning(f"Invalid room identifier type '{type(room).__name__}': expected bytes or int")
            return SetUserRoomPermissionsResponse.InvalidArg

        # NOTE: Set the user
        if isinstance(user, SessionID):
            if len(user) != 33:
                log.warning(f"Invalid SessionID length {len(user)}: expected 33 bytes for blinded public key")
                return SetUserRoomPermissionsResponse.InvalidArg
            req.user_session_id = user
        elif isinstance(user, int):
            req.user_id = user
        else:
            log.warning(f"Invalid user identifier type '{type(user).__name__}': expected SessionID or int")
            return SetUserRoomPermissionsResponse.InvalidArg

        # NOTE: Set permissions
        if not accessible and not read and not write:
            log.warning("No permissions specified: at least one of 'accessible', 'read', or 'write' required")
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
                log.warning(f"Invalid delay {sec_from_now}s: must be between 0 and {UPPER_BOUND}")
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
            request:   PluginDeleteMessageRequest = PluginDeleteMessageRequest(msg_ids=msg_ids)
            future:    oxenmq.ResultFuture = self.omq.request_future(conn, "plugin.delete_message", request.to_bencode())
            resp_list: List[bytes]         = future.get()
            assert len(resp_list) == 1

            response: PluginDeleteMessageResponse = PluginDeleteMessageResponse.from_bencode(resp_list[0])
            if response.status == "OK":
                result = True
        return result

    def delete_message(self, msg_id: int) -> bool:
        result = self.delete_messages([msg_id])
        return result

    def post_message(self,
                     room_token:           RoomToken,
                     body:                 str,
                     *,
                     whisper_to:           Optional[int] = None,
                     relay_to_plugins:     bool = False,
                     attachments_metadata: Optional[List[FileUploadMetadata]] = None) -> Optional[MessageID]:
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
                attachment_ids.append(attachment_meta.id)

                attachment = content.dataMessage.attachments.add()
                for key, value in attachment_meta.to_protobuf_dict().items():
                    setattr(attachment, key, value)

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
                        room_token:       RoomToken,
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
        return msg_id

    def post_reactions(self, room_token: RoomToken, msg_id: MessageID, *reactions: str) -> Dict[bytes, bt_value]:
        conn: oxenmq.ConnectionID = self._require_conn_established()
        req = {b"room_token": room_token, b"msg_id": msg_id, b"reactions": reactions}
        log.debug(f"Posting {len(reactions)} reaction(s) to message {msg_id} in room '{room_token}'")
        return oxenc.bt_deserialize(self.omq.request_future( conn, "plugin.post_reactions", oxenc.bt_serialize(req), request_timeout=timedelta(seconds=5)).get()[0])

    def remove_reactions(self, room_token: RoomToken, msg_id: MessageID, *reactions: str):
        req = {b"room_token": room_token, b"msg_id": msg_id, b"reactions": reactions}
        log.debug(f"Removing {len(reactions)} reaction(s) from message {msg_id} in room '{room_token}'")

        conn: oxenmq.ConnectionID = self._require_conn_established()
        return oxenc.bt_deserialize(
            self.omq.request_future(
                conn,
                "plugin.remove_reactions",
                oxenc.bt_serialize(req),
                request_timeout=timedelta(seconds=5),
            ).get()[0]
        )

    def upload_file(self, file_path: str, room_token: RoomToken, display_filename: Optional[str] = None) -> Optional[FileUploadMetadata]:
        try:
            from os import path
            filename = display_filename if display_filename else path.basename(file_path)

            from pathlib import Path
            file_contents = Path(file_path).read_bytes()

            req = PluginUploadFileRequest(
                filename      = filename,
                file_contents = file_contents,
                room_token    = room_token,
            )

            conn: oxenmq.ConnectionID = self._require_conn_established()
            resp = PluginUploadFileResponse.from_bencode(
                self.omq.request_future(conn, "plugin.upload_file", req.to_bencode(), request_timeout=timedelta(seconds=3)).get()[0]
            )

            import mimetypes
            from PIL import Image
            mime         = mimetypes.guess_type(file_path)
            content_type = mime[0]
            width:  Optional[int] = None
            height: Optional[int] = None
            if content_type and content_type.startswith("image"):
                img           = Image.open(file_path)
                width, height = img.size

            result = FileUploadMetadata(
                file_name    = filename,
                id           = resp.file_id,
                url          = resp.url,
                size         = len(file_contents),
                content_type = content_type,
                width        = width,
                height       = height,)
            return result
        except Exception as e:
            log.error(f"Failed to upload file '{file_path}': {e}")
            return None

    def _on_message_posted(self, m: oxenmq.Message):
        """Handle message posted events from SOGS, override this in your plugin to customise the behaviour"""
        parse = MessagePosted.from_bencode(m.dataview()[0])
        if self.on_message_posted_handler:
            self.on_message_posted_handler(m, parse)

    def _on_reaction_posted(self, m: oxenmq.Message):
        """Handle reaction posted events from SOGS, register a handler to receive these events"""
        if self.on_reaction_posted_handler:
            parse = ReactionPosted.from_bencode(m.dataview()[0])
            self.on_reaction_posted_handler(m, parse)

