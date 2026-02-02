import dataclasses
import typing

from sogs.plugin import Plugin, bt_value

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
