import configparser
import dataclasses
import os
import typing
import sogs.plugin
import typing_extensions

from typing          import List, Tuple, Any, Set, Optional
from sogs.plugin     import Plugin, RoomReadRequest, FilterResult
from sogs.types      import bt_value, RoomAddPostRequest, MessagePosted, ReactionPosted, RoomToken
from sogs.model.post import Post

@dataclasses.dataclass
class SlashTestPlugin(Plugin):
    request_read_posted_once_per_room: Set[RoomToken] = dataclasses.field(default_factory=set)

    def __post_init__(self):
        super().__post_init__()

        # Register debug command handlers
        self.register_pre_command('/debug_pre_command', self.handle_debug_pre)
        self.register_post_command('/debug_post_command', self.handle_debug_post)

        # Register help command
        self.register_post_command('/help', self.handle_help)

        # Subscribe to message events for debugging
        self.register_on_message_posted_handler(self.on_message_posted)
        self.register_on_reaction_posted_handler(self.on_reaction_posted)
        self.register_on_request_read_handler(self.on_request_read)

        # Print startup diagnostics
        desc_lines: List[Tuple[str, str]] = self.describe_config()

        import sogs.utils
        log_line: str = "Plugin loaded:\n  " + "\n  ".join(sogs.utils.pretty_format_key_value_list(desc_lines))
        sogs.plugin.log.info(log_line)

    def _whisper_payload(self,
                         room_token:  str,
                         user_id:     int,
                         endpoint:    str,
                         payload:     Any,
                         session_id:  Optional[bytes] = None,
                         timestamp:   Optional[float] = None,
                         description: str             = "") -> None:
        """Format and whisper payload details to user for debugging."""
        try:
            import pprint
            from datetime import datetime
            class HexBytesPrinter(pprint.PrettyPrinter):
                def _format(self, object, stream, indent, allowance, context, level):  # pyright: ignore[reportMissingParameterType, reportImplicitOverride]
                    if isinstance(object, bytes):
                        _ = stream.write(f'0x{object.hex()}')
                        return
                    super()._format(object, stream, indent, allowance, context, level)

            # Format session_id to hex string
            session_id_str = session_id.hex() if session_id else "N/A"

            # Format timestamp to local time
            local_time_str = "N/A"
            if timestamp is not None:
                try:
                    local_time = datetime.fromtimestamp(timestamp)
                    local_time_str = local_time.strftime("%Y-%m-%d %H:%M:%S %Z")
                except (ValueError, TypeError):
                    local_time_str = str(timestamp)

            printer = HexBytesPrinter(width=100)
            body    = f"Event: {endpoint}\nDescription: {description}\nSession ID: {session_id_str}\nTimestamp: {local_time_str}\nPayload:\n{printer.pformat(payload)}"
            _       = self.post_message(room_token=room_token, body=body, whisper_to=user_id, relay_to_plugins=False)
        except Exception as e:
            sogs.plugin.log.error(f"Failed to whisper payload: {e}")

    @typing_extensions.override
    def filter(self, req: RoomAddPostRequest) -> FilterResult:
        """Observe filter requests without rejecting - whisper the payload to the user."""
        msg = Post(raw=req.message_data)
        if '/debug_' in msg.text:
            self._whisper_payload(req.room_token,
                                  req.user_id,
                                  "Filter Message",
                                  req,
                                  session_id=req.session_id,
                                  timestamp=None,
                                  description="Triggered before any message is inserted - shows raw message data before processing")
        return FilterResult.accept()

    def on_message_posted(self, m: Any, posted: MessagePosted) -> None:  # pyright: ignore[reportAny]
        sogs.plugin.log.debug(f"Message posted: {posted.id} in room {posted.room_token}")
        self._whisper_payload(posted.room_token,
                              posted.user,
                              "Message Posted",
                              posted,
                              session_id  = posted.session_id,
                              timestamp   = posted.posted,
                              description = "Triggered when any user posts a message to the room")

    def on_reaction_posted(self, m: Any, reaction: ReactionPosted) -> None:  # pyright: ignore[reportAny, reportUnusedParameter]
        sogs.plugin.log.debug(f"Reaction posted: {reaction.reaction} on msg {reaction.msg_id}")
        self._whisper_payload(reaction.room_token,
                              reaction.user_id,
                              "Reaction Posted",
                              reaction,
                              session_id  = reaction.session_id,
                              timestamp   = None,
                              description = "Triggered when any user adds a reaction to a message")

    def on_request_read(self, req: RoomReadRequest) -> bt_value:
        if req.room_token not in self.request_read_posted_once_per_room:
            self.request_read_posted_once_per_room.add(req.room_token)
            self._whisper_payload(req.room_token,
                                  req.user_id,
                                  "Request Read",
                                  req,
                                  session_id  = req.session_id,
                                  timestamp   = None,
                                  description = "Triggered when you open/read a room (once per room session)")

        return b"OK"

    def handle_debug_pre(self, request: RoomAddPostRequest, command_parts: List[str]) -> bool:
        """Debug handler for pre-command phase."""
        sogs.plugin.log.debug(f"Debug pre-command: {command_parts}")

        # Parse boolean argument from command_parts[1], default to True
        result: Optional[bool] = None
        if len(command_parts) > 1:
            arg = command_parts[1].lower()
            if arg == "false":
                result = False
            elif arg == "true":
                result = True

        if result != None:
            self._whisper_payload(
                request.room_token,
                request.user_id,
                "Pre Message Commands",
                request,
                session_id=request.session_id,
                timestamp=None,
                description=f"Triggered before the message is posted. Returning {result} to {'accept' if result else 'reject'} the message"
            )
        else:
            raise RuntimeError("Invalid option provided to /debug_pre_command")
        return result

    def handle_debug_post(self, request: RoomAddPostRequest, command_parts: List[str]) -> bool:
        """Debug handler for post-command phase."""
        sogs.plugin.log.debug(f"Debug post-command: {command_parts}")
        self._whisper_payload(
            request.room_token,
            request.user_id,
            "Post Message Commands",
            request,
            session_id=request.session_id,
            timestamp=None,
            description="Triggered after the message is posted"
        )
        return True

    def handle_help(self, request: RoomAddPostRequest, command_parts: List[str]) -> bool:
        """Show help message with available commands and debug capabilities."""
        help_text = """🛠️ Slash Test Plugin - Help Guide

This plugin helps you explore and debug the SOGS plugin API by showing you exactly what data is received at each endpoint.

Available Commands:
  🔹/debug_pre_command <true|false> - Triggers debug output for pre-command phase. Returns true/false to accept/reject the message.
  🔹/debug_post_command - Triggers debug output for post-command phase
  🔹/help - Shows this help message

Events:
  Events that occur in a room will be reflected into the room:
  📨 Message Posted 👍 Reaction Posted 📖 Request Read 🔍 Filter ⚡ Pre/Post Message Command

How to Use:
  1. Send any message to see the message_posted payload
  2. Add a reaction to see the reaction_posted payload
  3. Use /debug_pre_command true or /debug_pre_command false to control whether the message is accepted or rejected

All payloads are whispered to you privately with descriptions of when they were triggered!"""

        _ = self.post_message(
            room_token       = request.room_token,
            body             = help_text,
            whisper_to       = request.user_id,
            relay_to_plugins = False
        )
        return True # No-op for post commands

def entry_point():
    import argparse

    # Argument parser
    parser = argparse.ArgumentParser(description='Slash Test Plugin for SOGS')
    _ = parser.add_argument('--plugin_slash_test_ini_path', type=str,
                            default=os.environ.get('PLUGIN_SLASH_TEST_INI_PATH', 'slash_test.ini'),
                            help='Path to the configuration .ini file (default: slash_test.ini or set PLUGIN_SLASH_TEST_INI_PATH env)')
    args     = parser.parse_args()
    ini_path = typing.cast(str, args.plugin_slash_test_ini_path)

    # Set logger name
    sogs.plugin.log.name = '[SLASH TEST]'

    # Load common INI configuration
    config: sogs.plugin.PluginConfigFromINI = sogs.plugin.Plugin.load_ini_from_path(ini_path=ini_path, default_display_name='Slash Test Plugin')
    if not config.success:
        return

    # Configure logging with level from .ini (must be after config load)
    sogs.plugin.setup_plugin_logging(ini=config.ini, plugin_section='plugin_slash_test')
    sogs.plugin.log.info(f"Loading Slash Test plugin config from {ini_path}")

    try:
        ini_parser = configparser.RawConfigParser(strict=False)
        _          = ini_parser.read(ini_path)

        # Plugin specific fields from INI
        key_file:   str   = ini_parser.get('plugin_slash_test', 'key_file', fallback="slash_test_ed25519")
        ed_privkey: bytes = Plugin.get_or_make_ed25519_privkey(key_file)

        # Instantiate the plugin
        plugin = SlashTestPlugin(sogs_address = config.sogs_address,
                                 sogs_pubkey  = config.sogs_pubkey,
                                 ed_privkey   = ed_privkey,
                                 display_name = config.display_name)
        plugin.run()

    except Exception as e:
        sogs.plugin.log.error(f"Exception raised in plugin. Terminating:\n{e}")


if __name__ == "__main__":
    entry_point()
