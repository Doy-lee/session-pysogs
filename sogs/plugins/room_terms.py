r"""Room Terms Plugin

  This plugin presents users with terms of agreement when they request read access to a room.
  Users must react with the configured accept emoji (default: thumbs up) to agree to the terms
  and gain access. Reacting with any other emoji is treated as declining the terms, and the
  user must wait a configurable timeout period before they can try again.

  The plugin supports per-room configuration:
    1. Room-specific settings (`[plugin_room_terms.room.<token>]`) - highest priority
    2. Globally using room token `*` (`[plugin_room_terms.room.*]`) - default for all rooms
    3. Built-in defaults (if neither is specified)

  Each room can have its own terms message, accept emoji, retry timeout, and write timeout.

Getting Started:
  Setup the .ini config file (see the configuration section below for more details) with the
  desired parameters and then you can run the plugin standalone:

    cd session-pysogs
    python3 -m sogs.plugins.room_terms --plugin_room_terms_ini_path <path/to/plugin/config.ini>

  Alternatively you can run the plugin alongside the SOGS server as a UWSGI mule. In your UWSGI
  .ini config file, add to the [uwsgi] section:

    [uwsgi]
    mule = sogs.plugins.room_terms
    env  = PLUGIN_ROOM_TERMS_INI_PATH=<path/to/plugin/config.ini>

  Note that the plugin can be parameterized via the following methods:

    - Pass the `--plugin_room_terms_ini_path` flag to the python invocation
    - Set the PLUGIN_ROOM_TERMS_INI_PATH environment variable
    - Otherwise expects "room_terms.ini" in the current working directory if omitted

  Start the plugin via UWSGI or directly and after it has loaded the plugin will generate a
  Ed25519 keypair and output this information on startup, e.g.:

    [ROOM TERMS] Plugin loaded:
      SOGS Address (Pubkey):      tcp://127.0.0.1:22028 (cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc)
      Display Name:               Room Terms Plugin
      Ed25519 Pubkey:             aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
      X25519 Pubkey:              bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
      Session Account (Blind-15): 15xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

  The Ed25519 public key must be registered to the SOGS instance to enable the plugin to establish
  a connection to the SOGS server, authenticate and consequently receive messages from SOGS to react
  to. The plugin can be registered by invoking on the SOGS instance:

    python3 -m sogs --add-plugin       <ed25519 pubkey hex 64 chars> \
                    --plugin-name      'Room Terms Plugin' \
                    --plugin-global    true \
                    --plugin-approver  true \
                    --plugin-required  true \
                    --plugin-subscribe true

Config file (.ini):
  Configure how the Room Terms plugin behaves and how it connects to the SOGS server by adding
  the following fields into the .ini file. This can be in your SOGS .ini file or a separate
  .ini file if you wish.

  The `[plugin_room_terms]` section contains plugin-level settings (key_file, display_name,
  log_level). Room-specific settings are configured in `[plugin_room_terms.room.<token>]`
  sections, with `[plugin_room_terms.room.*]` serving as the global default for all rooms.

  See the example configuration:

```ini
[log]
; Log level for the plugin (DEBUG, INFO, WARNING, ERROR, CRITICAL)
; Can be overridden per-plugin in [plugin_room_terms] as 'log_level'
; level = INFO

[plugin]
; Configuration for the plugin connection to the SOGS server

; The public key of your SOGS. This can be found in your room URLs.
; sogs_pubkey_hex = xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

; The SOGS OMQ address which plugins will operate against.
; sogs_address = tcp://127.0.0.1:22028

[plugin_room_terms]
; The display name of the plugin that will be shown to Session clients
; display_name = Room Terms Plugin

; Path to the ed25519 private key file. A new key is generated if not found.
; key_file = plugin_room_terms_ed25519

; Log level for this plugin (overrides [log] section if set)
; log_level = INFO

[plugin_room_terms.room.*]
; Global defaults for all rooms. These settings apply to any room that does not have
; its own specific configuration section.

; The terms of agreement message shown to users.
; terms = Please react with a thumbs up to join the room.

; The emoji users must react with to accept the terms. Any other reaction is treated
; as declining the terms.
; accept_reaction = 👍

; Seconds before a user who did not accept can try again.
; retry_timeout = 120

; Seconds after accepting before user gets write permissions.
; write_timeout = 120

; Message shown when user accepts terms (reacted with accept emoji).
; reply_message = You now have read access. You will gain write access in \write seconds.

; Message shown when user declines terms (reacted with wrong emoji).
; failure_message = To enter, please react with \accept to accept the terms. You may try again in \retry seconds.

; Escape sequences in reply_message and failure_message:
;   \accept - the accept emoji (e.g. 👍)
;   \write  - write timeout in seconds
;   \retry  - retry timeout in seconds
;   \r      - room name
;   \t      - room token
;   \@      - @mention of the user
;   \p      - profile name
;   \n      - newline
;   \\     - literal backslash

[plugin_room_terms.room.myroom]
; Room-specific settings for the room with token "myroom". Any settings here override
; the wildcard defaults above.

; Custom terms for this room
; terms = Welcome to MyRoom! By accepting, you agree to be excellent to each other.

; Custom accept emoji for this room
; accept_reaction = ⭐

; Custom timeouts for this room
; retry_timeout = 60
; write_timeout = 300

; Custom reply messages for this room (see escape sequences above)
; reply_message = Welcome to MyRoom! You now have read access.
; failure_message = Sorry, you need to react with \accept to join MyRoom. Try again in \retry seconds.
```
"""

import configparser
import dataclasses
import os
import oxenc
import oxenmq
import time
import typing
import sogs.plugin
import sogs.types
import sogs.utils

from typing      import Optional, Dict, List, Tuple
from sogs.types  import bt_value, SessionID, RoomToken, MessageID
from sogs.plugin import Plugin, RoomReadRequest


def process_terms_message(
    text: str,
    accept_reaction: str,
    write_timeout: int,
    retry_timeout: int,
    room_name: str = "",
    room_token: str = "",
    user_mention: str = "",
    profile_name: str = ""
) -> str:
    result = text.replace('\\\\', '\x00')
    result = result.replace('\\accept', accept_reaction)
    result = result.replace('\\write', str(write_timeout))
    result = result.replace('\\retry', str(retry_timeout))
    result = result.replace('\\r', room_name)
    result = result.replace('\\t', room_token)
    result = result.replace('\\@', user_mention)
    result = result.replace('\\p', profile_name)
    result = result.replace('\\n', '\n')
    result = result.replace('\x00', '\\')
    return result


@dataclasses.dataclass
class RoomTermsConfig:
    terms:           Optional[str] = None # The terms of agreement message shown to users.
    accept_reaction: Optional[str] = None # Emoji users must react with to accept the terms.
    retry_timeout:   Optional[int] = None # Seconds before user can retry after not accepting.
    write_timeout:   Optional[int] = None # Seconds after accepting before user gets write permissions.
    reply_message:   Optional[str] = None # Message shown when user accepts terms.
    failure_message: Optional[str] = None # Message shown when user declines terms.

@dataclasses.dataclass
class RoomTermsPlugin(Plugin):
    """Room Terms plugin for presenting users with terms of agreement before granting access.

    When users request read access to a room, they are presented with configurable terms and
    must react with the accept emoji to agree. Any other reaction is treated as declining the
    terms, placing the user in a retry timeout before they can attempt again.

    Configuration is hierarchical: room-specific settings override wildcard ('*') settings,
    which override class-level defaults.

    Attributes:
        room_configs:     Dictionary mapping room tokens to their RoomTermsConfig.
                          Use '*' key for wildcard/global defaults.
        pending_requests: Tracks pending term acceptance requests per user/room.
        retry_jail:       Tracks users who did not accept and when they can retry.
    """

    # Class-level defaults (fallback if not specified in wildcard room config)
    DEFAULT_ACCEPT_REACTION: typing.ClassVar[str] = "\N{THUMBS UP SIGN}"
    DEFAULT_RETRY_TIMEOUT:   typing.ClassVar[int] = 120
    DEFAULT_WRITE_TIMEOUT:   typing.ClassVar[int] = 120
    DEFAULT_TERMS:           typing.ClassVar[str] = "Please react with a thumbs up to join the room."
    DEFAULT_REPLY_MESSAGE:   typing.ClassVar[str] = "You now have read access. You will gain write access in \\write seconds."
    DEFAULT_FAILURE_MESSAGE: typing.ClassVar[str] = "To enter, please react with \\accept to accept the terms. You may try again in \\retry seconds."

    room_configs:     Dict[RoomToken, RoomTermsConfig]            = dataclasses.field(default_factory=dict)
    pending_requests: Dict[RoomToken, Dict[SessionID, MessageID]] = dataclasses.field(default_factory=dict)
    retry_jail:       Dict[RoomToken, Dict[SessionID, float]]     = dataclasses.field(default_factory=dict)

    def __post_init__(self):
        super().__post_init__()
        self.register_on_request_read_handler(self.on_request_read)
        self.register_on_reaction_posted_handler(self.on_reaction_posted)

        # Print startup diagnostics
        room_configs_desc: str = ""
        for token in self.room_configs:
            if len(room_configs_desc) > 100:
                room_configs_desc += ".."
                break
            if len(room_configs_desc):
                room_configs_desc += ", "
            room_configs_desc += token

        wildcard_config                   = self.get_config_for_room('*')
        desc_lines: List[Tuple[str, str]] = self.describe_config()
        desc_lines.extend([
            ("Accept Reaction", wildcard_config.accept_reaction or self.DEFAULT_ACCEPT_REACTION),
            ("Retry Timeout",   f"{wildcard_config.retry_timeout or self.DEFAULT_RETRY_TIMEOUT}s"),
            ("Write Timeout",   f"{wildcard_config.write_timeout or self.DEFAULT_WRITE_TIMEOUT}s"),
            ("Room Configs",    f"({len(self.room_configs)}) [{room_configs_desc}]" if len(self.room_configs) else "(using defaults)"),
            ("Reply Message",   wildcard_config.reply_message or self.DEFAULT_REPLY_MESSAGE),
            ("Failure Message", wildcard_config.failure_message or self.DEFAULT_FAILURE_MESSAGE),
        ])

        import sogs.utils
        log_line: str = "Plugin loaded:\n  " + "\n  ".join(sogs.utils.pretty_format_key_value_list(desc_lines))
        sogs.plugin.log.info(log_line)

    def get_config_for_room(self, room_token: RoomToken) -> RoomTermsConfig:
        # Start with class defaults
        result = RoomTermsConfig(
            terms           = self.DEFAULT_TERMS,
            accept_reaction = self.DEFAULT_ACCEPT_REACTION,
            retry_timeout   = self.DEFAULT_RETRY_TIMEOUT,
            write_timeout   = self.DEFAULT_WRITE_TIMEOUT,
            reply_message   = self.DEFAULT_REPLY_MESSAGE,
            failure_message = self.DEFAULT_FAILURE_MESSAGE,
        )

        # Override with wildcard config
        if '*' in self.room_configs:
            wildcard = self.room_configs['*']
            if wildcard.terms is not None:
                result.terms = wildcard.terms
            if wildcard.accept_reaction is not None:
                result.accept_reaction = wildcard.accept_reaction
            if wildcard.retry_timeout is not None:
                result.retry_timeout = wildcard.retry_timeout
            if wildcard.write_timeout is not None:
                result.write_timeout = wildcard.write_timeout
            if wildcard.reply_message is not None:
                result.reply_message = wildcard.reply_message
            if wildcard.failure_message is not None:
                result.failure_message = wildcard.failure_message

        # Override with specific room config
        if room_token != '*' and room_token in self.room_configs:
            specific = self.room_configs[room_token]
            if specific.terms is not None:
                result.terms = specific.terms
            if specific.accept_reaction is not None:
                result.accept_reaction = specific.accept_reaction
            if specific.retry_timeout is not None:
                result.retry_timeout = specific.retry_timeout
            if specific.write_timeout is not None:
                result.write_timeout = specific.write_timeout
            if specific.reply_message is not None:
                result.reply_message = specific.reply_message
            if specific.failure_message is not None:
                result.failure_message = specific.failure_message

        return result

    def on_request_read(self, req: RoomReadRequest) -> bt_value:
        room_token: RoomToken = req.room_token
        session_id: SessionID = req.session_id

        if room_token in self.retry_jail and session_id in self.retry_jail[room_token]:
            if time.time() > self.retry_jail[room_token][session_id]:
                del self.retry_jail[room_token][session_id]
            else:
                return oxenc.bt_serialize("JAIL")

        if room_token in self.pending_requests and session_id in self.pending_requests[room_token]:
            return oxenc.bt_serialize("OK")

        room_config: RoomTermsConfig = self.get_config_for_room(room_token)
        sogs.plugin.log.debug(f"Read request from user {req.user_id} ({sogs.utils.fmt_bytes_trunc(session_id)}) for room '{room_token}'")

        room_terms: str = process_terms_message(
            typing.cast(str, room_config.terms),
            typing.cast(str, room_config.accept_reaction),
            typing.cast(int, room_config.write_timeout),
            typing.cast(int, room_config.retry_timeout),
            room_name=req.room_name,
            room_token=room_token,
            user_mention=f"@{req.session_id.hex()}",
            profile_name=str(req.user_id)
        )

        msg_id: Optional[MessageID] = self.post_message(room_token, room_terms, whisper_to=req.user_id, relay_to_plugins=False)
        if msg_id:
            react_resp = self.post_reactions(room_token, msg_id, typing.cast(str, room_config.accept_reaction))
            if react_resp.error:
                sogs.plugin.log.error(f"Failed to add reaction to terms message for user {req.user_id} in room '{room_token}': {react_resp.error}")
                return oxenc.bt_serialize("ERROR")
            if room_token not in self.pending_requests:
                self.pending_requests[room_token] = dict()
            self.pending_requests[room_token][session_id] = msg_id

        return oxenc.bt_serialize("OK")

    def on_reaction_posted(self, m: oxenmq.Message, req: sogs.types.ReactionPosted):  # pyright: ignore[reportUnusedParameter]
        msg_id:     MessageID = req.msg_id
        session_id: SessionID = req.session_id
        room_token: RoomToken = req.room_token

        if not (room_token in self.pending_requests and
                session_id in self.pending_requests[room_token] and
                msg_id == self.pending_requests[room_token][session_id]):
            return

        room_config:    RoomTermsConfig = self.get_config_for_room(room_token)
        accept_reaction: str = typing.cast(str, room_config.accept_reaction)
        retry_timeout:   int = typing.cast(int, room_config.retry_timeout)
        write_timeout:   int = typing.cast(int, room_config.write_timeout)

        sogs.plugin.log.debug(f"Received reaction '{req.reaction}' from user {req.user_id} (0x{session_id.hex()[:16]}...) in room '{room_token}'")

        if req.reaction == accept_reaction:
            sogs.plugin.log.info(f"Terms accepted by user {req.user_id} (0x{session_id.hex()[:16]}...) in room '{room_token}'; granting read now, write in {write_timeout}s")
            _ = self.set_user_room_permissions(room=room_token, user=session_id, sec_from_now=None, read=True)
            _ = self.set_user_room_permissions(room=room_token, user=session_id, sec_from_now=write_timeout, write=True)
            reply_msg = process_terms_message(
                typing.cast(str, room_config.reply_message),
                accept_reaction,
                write_timeout,
                retry_timeout,
                room_name=req.room_name,
                room_token=room_token,
                user_mention=f"@{req.session_id.hex()}",
                profile_name=str(req.user_id)
            )
            _ = self.post_message(room_token,
                              reply_msg,
                              whisper_to=req.user_id,
                              relay_to_plugins=False)
        else:
            sogs.plugin.log.info(f"Terms not accepted by user {req.user_id} (0x{session_id.hex()[:16]}...) in room '{room_token}'; reacted with '{req.reaction}' instead of '{accept_reaction}'; retry available in {retry_timeout}s")
            failure_msg = process_terms_message(
                typing.cast(str, room_config.failure_message),
                accept_reaction,
                write_timeout,
                retry_timeout,
                room_name=req.room_name,
                room_token=room_token,
                user_mention=f"@{req.session_id.hex()}",
                profile_name=str(req.user_id)
            )
            _ = self.post_message(room_token,
                                  failure_msg,
                                  whisper_to=req.user_id,
                                  relay_to_plugins=False)
            if room_token not in self.retry_jail:
                self.retry_jail[room_token] = dict()
            self.retry_jail[room_token][session_id] = time.time() + retry_timeout

        _ = self.delete_message(msg_id)
        del self.pending_requests[room_token][session_id]
        if len(self.pending_requests[room_token]) == 0:
            del self.pending_requests[room_token]


def entry_point():
    import argparse

    # Argument parser
    parser = argparse.ArgumentParser(description='Room Terms Plugin')
    _ = parser.add_argument('--plugin_room_terms_ini_path', type=str,
                            default=os.environ.get('PLUGIN_ROOM_TERMS_INI_PATH', 'room_terms.ini'),
                            help='Path to the configuration .ini file (default: room_terms.ini or set PLUGIN_ROOM_TERMS_INI_PATH env)')
    args     = parser.parse_args()
    ini_file = typing.cast(str, args.plugin_room_terms_ini_path)

    # Set logger name
    sogs.plugin.log.name = '[ROOM TERMS]'

    # Load common INI configuration
    config: sogs.plugin.PluginConfigFromINI = sogs.plugin.Plugin.load_ini_from_path(ini_path=ini_file, default_display_name='Room Terms Plugin')
    if not config.success:
        return

    # Configure logging with level from .ini (must be after config load)
    sogs.plugin.setup_plugin_logging(ini=config.ini, plugin_section='plugin_room_terms')
    sogs.plugin.log.info(f"Loading Room Terms plugin config from {ini_file}")

    try:
        ini_parser = configparser.RawConfigParser(strict=False)
        _          = ini_parser.read(ini_file)

        # Get plugin-level config from [plugin_room_terms] section
        key_file   = ini_parser.get('plugin_room_terms', 'key_file', fallback="plugin_room_terms_ed25519")
        ed_privkey = sogs.plugin.Plugin.get_or_make_ed25519_privkey(key_file)

        # Parse room configurations from [plugin_room_terms.room.<token>] sections
        room_configs: Dict[RoomToken, RoomTermsConfig] = {}
        for section in ini_parser.sections():
            if not section.startswith('plugin_room_terms.room.'):
                continue

            parts = section.split('.')
            if len(parts) != 3:
                raise ValueError(f"Section '{section}' has unexpected format. Expected: 'plugin_room_terms.room.<token>'")

            room_token: str = parts[2]
            if len(room_token) == 0:
                raise ValueError(f"Section '{section}' has empty room token")

            # Parse room config (all fields optional)
            room_config = RoomTermsConfig()

            if ini_parser.has_option(section, 'terms'):
                room_config.terms = ini_parser.get(section, 'terms')
            if ini_parser.has_option(section, 'accept_reaction'):
                room_config.accept_reaction = ini_parser.get(section, 'accept_reaction')
            if ini_parser.has_option(section, 'retry_timeout'):
                room_config.retry_timeout = ini_parser.getint(section, 'retry_timeout')
            if ini_parser.has_option(section, 'write_timeout'):
                room_config.write_timeout = ini_parser.getint(section, 'write_timeout')
            if ini_parser.has_option(section, 'reply_message'):
                room_config.reply_message = ini_parser.get(section, 'reply_message')
            if ini_parser.has_option(section, 'failure_message'):
                room_config.failure_message = ini_parser.get(section, 'failure_message')

            room_configs[room_token] = room_config

        # Instantiate plugin
        plugin = RoomTermsPlugin(sogs_address = config.sogs_address,
                                 sogs_pubkey  = config.sogs_pubkey,
                                 ed_privkey   = ed_privkey,
                                 display_name = config.display_name,
                                 room_configs = room_configs)
        plugin.run()

    except Exception as e:
        sogs.plugin.log.error(f"Exception raised in plugin. Terminating:\n{e}")


if __name__ == "__main__":
    entry_point()
