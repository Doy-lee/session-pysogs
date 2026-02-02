import dataclasses
import json
import typing
from sogs.plugin import *

@dataclasses.dataclass
class SlashTestPlugin(Plugin):

    def __post_init__(self):
        super().__post_init__()
        self.register_pre_command('/test', self.handle_pre_slash)
        self.register_post_command('/test', self.handle_post_slash)
        self.register_pre_command('/test_handled', self.handle_pre_slash)
        self.register_post_command('/test_handled', self.handle_post_slash)
        self.register_pre_command('/get_file', self.handle_get_file)

    def handle_pre_slash(self, request: dict[bytes, bt_value], command_parts: list[str]) -> bool:
        print(f"slash pre-insertion command: {json.dumps(request, indent=1)} {command_parts}")
        if command_parts[0] == '/test_handled':
            return False
        return True

    def handle_post_slash(self, request: dict[bytes, bt_value], command_parts: list[str]) -> bool:
        print(f"slash post-insertion command: {json.dumps(request, indent=1)} {command_parts}")
        if command_parts[0] == '/test_handled':
            return False
        return True

    def handle_get_file(self, request: dict[bytes, bt_value], command_parts: list[str]) -> bool:
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
            no_plugins=False,
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
    log.name                        = 'SLASH_TEST'
    plugin_ini: PluginConfigFromINI = Plugin.load_ini_from_path(ini_path)
    if not plugin_ini.success:
        return

    # Plugin specific fields from INI
    key_file:     str = plugin_ini.ini.get('plugin_slash_test', 'key_file',     fallback="slash_test_ed25519")
    display_name: str = plugin_ini.ini.get('plugin_slash_test', 'display_name', fallback="Slash Test Plugin")
    ed_privkey:   bytes = Plugin.get_or_make_ed25519_privkey(key_file)

    try:
        # Instantiate the plugin
        plugin = SlashTestPlugin(sogs_address=plugin_ini.sogs_address, sogs_pubkey=plugin_ini.sogs_pubkey, ed_privkey=ed_privkey, display_name=display_name)

        # Register the plugin to the DB. SOGS uses this DB to authenticate incoming requests as long
        # as they are signed by the x25519 key stored here. This table also contains permissions for
        # the SOGS to further discriminate the types of requests the plugin is allowed to make.
        #
        # This step is optional! If you wanted to run plugins on a separate network and to remotely
        # communicate with SOGS then you could imagine manually authorising the plugin by inserting
        # the key into the DB out-of-band.
        #
        # In this example we are running the CAPTCHA plugin on a DB that is local to the application
        # and is trusted so we authorise ourselves directly into the plugins table thus making this
        # plugin completely standalone.
        from sogs.web import app
        with app.app_context():
            import sogs.db
            with sogs.db.transaction():
                sogs.db.query("INSERT OR IGNORE INTO plugins (name, auth_key, global, approver, subscribe) VALUES ('Slash Test', :key, 1, 1, 1)", key=plugin.x_pubkey)

        plugin.run()
    except Exception:
        log.error("Exception raised in plugin. Terminating:\n{}".format(traceback.format_exc()))
