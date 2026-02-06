import dataclasses
import json
import typing
from sogs.plugin import *
from sogs.types import bt_value

@dataclasses.dataclass
class SlashTestPlugin(Plugin):

    def __post_init__(self):
        super().__post_init__()
        self.register_pre_command('/test', self.handle_pre_slash)
        self.register_post_command('/test', self.handle_post_slash)
        self.register_pre_command('/test_handled', self.handle_pre_slash)
        self.register_post_command('/test_handled', self.handle_post_slash)
        self.register_pre_command('/get_file', self.handle_get_file)
        log.info("Plugin initialised: x25519 pubkey {}".format(self.x_pubkey.hex()))

    def handle_pre_slash(self, request: Dict[bytes, bt_value], command_parts: List[str]) -> bool:
        print(f"slash pre-insertion command: {json.dumps(request, indent=1)} {command_parts}")
        if command_parts[0] == '/test_handled':
            return False
        return True

    def handle_post_slash(self, request: Dict[bytes, bt_value], command_parts: List[str]) -> bool:
        print(f"slash post-insertion command: {json.dumps(request, indent=1)} {command_parts}")
        if command_parts[0] == '/test_handled':
            return False
        return True

    def handle_get_file(self, request: Dict[bytes, bt_value], command_parts: List[str]) -> bool:
        print(f"/get_file pre-insertion command: {command_parts}")

        room_token = typing.cast(bytes, request[b'room_token'])
        print(f"room_token for file upload: {room_token}")

        file_meta = self.upload_file("test.jpg", room_token)

        if not file_meta:
            print("file upload failed...")
            return False

        print(f"file upload success, file_meta: {file_meta}")

        msg_id = self.post_message(
            room_token,
            "Please work ffs!",
            relay_to_plugins=True,
            attachments_metadata=[file_meta,],
        )

        print(f"Success, msg_id = {msg_id}")

        return False

def entry_point(ini_path: str = 'slash_test.ini'):
    import argparse
    import os
    import traceback

    # Argument parser
    parser = argparse.ArgumentParser(description='Slash Test Plugin for SOGS')
    _ = parser.add_argument('--plugin_slash_test_ini_path', type=str,
                            default=os.environ.get('PLUGIN_SLASH_TEST_INI_PATH', 'slash_test.ini'),
                            help='Path to the configuration .ini file (default: slash_test.ini or set PLUGIN_SLASH_TEST_INI_PATH env)')
    args     = parser.parse_args()
    ini_path = typing.cast(str, args.plugin_slash_test_ini_path)

    # Load common INI configuration
    log.info(f"Loading Slash Test plugin config from {ini_path}")
    log.name                    = 'SLASH_TEST'
    config: PluginConfigFromINI = Plugin.load_ini_from_path(ini_path)
    if not config.success:
        return

    # Plugin specific fields from INI
    key_file:     str   = config.ini.get('plugin_slash_test', 'key_file',     fallback="slash_test_ed25519")
    display_name: str   = config.ini.get('plugin_slash_test', 'display_name', fallback="Slash Test Plugin")
    ed_privkey:   bytes = Plugin.get_or_make_ed25519_privkey(key_file)

    try:
        # Instantiate the plugin
        plugin = SlashTestPlugin(sogs_address=config.sogs_address, sogs_pubkey=config.sogs_pubkey, ed_privkey=ed_privkey, display_name=display_name)

        from sogs.web import app
        with app.app_context():
            import sogs.db
            with sogs.db.transaction():
                sogs.db.query("INSERT OR IGNORE INTO plugins (name, auth_key, global, approver, subscribe) VALUES ('Slash Test', :key, 1, 1, 1)", key=plugin.x_pubkey)

        plugin.run()
    except Exception:
        log.error("Exception raised in plugin. Terminating:\n{}".format(traceback.format_exc()))
