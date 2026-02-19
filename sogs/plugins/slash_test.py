import configparser
import dataclasses
import os
import typing
import sogs.plugin

from typing     import Dict, List, Tuple
from sogs.plugin import Plugin
from sogs.types  import bt_value, RoomAddPostRequest

@dataclasses.dataclass
class SlashTestPlugin(Plugin):

    def __post_init__(self):
        super().__post_init__()
        self.register_pre_command('/test', self.handle_pre_slash)
        self.register_post_command('/test', self.handle_post_slash)
        self.register_pre_command('/test_handled', self.handle_pre_slash)
        self.register_post_command('/test_handled', self.handle_post_slash)
        self.register_pre_command('/get_file', self.handle_get_file)

        # Print startup diagnostics
        desc_lines: List[Tuple[str, str]] = self.describe_config()

        import sogs.utils
        log_line: str = "Plugin loaded:\n  " + "\n  ".join(sogs.utils.pretty_format_key_value_list(desc_lines))
        sogs.plugin.log.info(log_line)


    def handle_pre_slash(self, request: RoomAddPostRequest, command_parts: List[str]) -> bool:  # pyright: ignore[reportUnusedParameter]
        if command_parts[0] == '/test_handled':
            return False
        return True

    def handle_post_slash(self, request: RoomAddPostRequest, command_parts: List[str]) -> bool:  # pyright: ignore[reportUnusedParameter]
        if command_parts[0] == '/test_handled':
            return False
        return True

    def handle_get_file(self, request: RoomAddPostRequest, command_parts: List[str]) -> bool:
        sogs.plugin.log.debug(f"/get_file pre-insertion command: {command_parts}")

        room_token = request.room_token
        sogs.plugin.log.debug(f"room_token for file upload: {room_token}")

        file_meta = self.upload_file("test.jpg", room_token)

        if not file_meta:
            sogs.plugin.log.warning("file upload failed...")
            return False

        sogs.plugin.log.debug(f"file upload success, file_meta: {file_meta}")

        msg_id = self.post_message(
            room_token,
            "Please work ffs!",
            relay_to_plugins=True,
            attachments_metadata=[file_meta,],
        )

        sogs.plugin.log.debug(f"Success, msg_id = {msg_id}")

        return False

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
