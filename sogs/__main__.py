from argparse import ArgumentParser as AP, RawDescriptionHelpFormatter, Action
import atexit
import re
import sys
import typing
import pathlib
import sogs.plugin
import shutil
from dataclasses import dataclass
from typing import List, Tuple, Dict, Optional

from . import __version__ as version


ap = AP(
    epilog="""

Examples:

    # Add new room 'xyz':
    python3 -msogs --add-room xyz --name 'XYZ Room'

    # Add 2 admins to each of rooms 'xyz' and 'abc':
    python3 -msogs --rooms abc xyz --admin --add-moderators 050123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef 0500112233445566778899aabbccddeeff00112233445566778899aabbccddeeff

     # Add a global moderator visible as a moderator of all rooms:
    python3 -msogs --add-moderators 050123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef --rooms=+ --visible

    # Set default read/write True and upload False on all rooms
    python3 -msogs --add-perms rw --remove-perms u --rooms='*'

    # Remove overrides for user 0501234... on all rooms
    python3 -msogs --clear-perms rwua --rooms='*' --users 050123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef

     # List room info:
    python3 -msogs -L

     # List all plugins and their room configurations:
    python3 -msogs --list-plugins

    # Add a plugin with Ed25519 public key '012345...' (first example uses default name and all flags=false and a room id of 1):
    python3 -msogs --add-plugin 1
    python3 -msogs --add-plugin 0123456789abcdef... --plugin-name 'My Plugin' --plugin-global true --plugin-approver true --plugin-required true --plugin-subscribe true

     # Add a plugin to specific rooms (first example uses default flags=false and a room id of 1):
    python3 -msogs --add-room-plugin 1                   --rooms my-room other-room
    python3 -msogs --add-room-plugin 0123456789abcdef... --rooms my-room --room-plugin-approver true --room-plugin-required true --room-plugin-subscribe true

     # Remove a plugin from specific rooms by ID or Ed25519 pubkey:
    python3 -msogs --delete-room-plugin 1                  --rooms my-room other-room
    python3 -msogs --delete-room-plugin 012345789abcdef... --rooms my-room

     # Delete a plugin entirely by ID or Ed25519 pubkey (and from any room using it):
    python3 -msogs --delete-plugin 1
    python3 -msogs --delete-plugin 0123456789abcdef...

A sogs.ini will be loaded from the current directory, if one exists.  You can override this by
specifying a path to the config file to load in the SOGS_CONFIG environment variable.

""",  # noqa: E501
    formatter_class=RawDescriptionHelpFormatter,
)


class CrudeStringUnescape(Action):
    """Crude class for potentially-escaped parameters; this supports '\\\\' and '\\n'"""

    escapes = {'\\': '\\', 'n': '\n'}
    pat = re.compile(r'\\([\\n])')

    def __init__(self, option_strings, dest, nargs=None, **kwargs):
        if nargs is not None:
            raise ValueError("nargs not allowed")
        super().__init__(option_strings, dest, **kwargs)

    def __call__(self, parser, ns, value, option_string=None):
        setattr(ns, self.dest, self.pat.sub(lambda x: self.escapes[x[1]], value))


ap.add_argument('--version', '-V', action='version', version=f'PySOGS {version}')

ap.add_argument('--add-room', help="Add a room with the given token", metavar='TOKEN')
ap.add_argument(
    '--name',
    help="Set or updates a room's name (with --add-room or --rooms); if omitted when adding a "
    "room then uses the token name",
)
ap.add_argument(
    '--description',
    action=CrudeStringUnescape,
    help="Sets or updates a room's description (with --add-room or --rooms)",
)
ap.add_argument('--delete-room', help="Delete the room with the given token", metavar='TOKEN')
ap.add_argument(
    '--add-moderators',
    nargs='+',
    metavar='SESSIONID',
    help="Add the given Session ID(s) as a moderator of the room given by --rooms",
)

ap.add_argument(
    '--delete-moderators',
    nargs='+',
    metavar='SESSIONID',
    help="Delete the the given Session ID(s) as moderator and admins of the room given by --rooms",
)
ap.add_argument(
    '--users',
    help="One or more specific users to set permissions for with --add-perms, --remove-perms, "
    "--clear-perms.  If omitted then the room default permissions will be set for the given "
    "room(s) instead.",
    nargs='+',
    metavar='SESSIONID',
)
ap.add_argument(
    "--add-perms",
    help="With --add-room or --rooms, set these permissions to true; takes a string of 1-4 of "
    "the letters \"rwua\" for [r]ead, [w]rite, [u]pload, and [a]ccess.",
)
ap.add_argument(
    "--remove-perms",
    help="With --add-room or --rooms, set these permissions to false; takes the same string as "
    "--add-perms, but denies the listed permissions rather than granting them.",
)
ap.add_argument(
    "--clear-perms",
    help="With --add-room or --rooms, clear room or user overrides on these permissions, "
    "returning them to the default setting.  Takes the same argument as --add-perms.",
)
ap.add_argument(
    '--admin',
    action='store_true',
    help="Add the given moderators as admins rather than ordinary moderators",
)
ap.add_argument(
    '--rooms',
    nargs='+',
    metavar='TOKEN',
    help="Room(s) to use when adding/removing moderators/admins or when setting permissions. "
    "If a single room name of '+' is given then the user will be added/removed as a global "
    "admin/moderator. '+' is not valid for setting permissions. If a single room name "
    "of '*' is given then the changes take effect on each of the server's current rooms.",
)

# Add plugin commands
ap.add_argument('--add-plugin',
                help="Add or update a plugin's Ed25519 public key (64 hex chars). "
                "Omitted flags retain their current values for existing plugins, or use defaults for new plugins.",
                metavar='ED25519_PUBKEY')
ap.add_argument('--plugin-name',
                help="Human-readable name for the plugin (default: abbreviated hex of ed25519 pubkey)",
                metavar='NAME')
_ = ap.add_argument('--plugin-global',
                type=str,
                choices=['true', 'false'],
                help="If true, this plugin applies to all rooms (default: false)",
                metavar='true|false')
_ = ap.add_argument('--plugin-approver',
                type=str,
                choices=['true', 'false'],
                help="If true, plugin can approve/deny messages (default: false)",
                metavar='true|false')
_ = ap.add_argument('--plugin-required',
                type=str,
                choices=['true', 'false'],
                help="If true, plugin must be connected for message approval (default: false, has no effect if 'approver' is false)",
                metavar='true|false')
_ = ap.add_argument('--plugin-subscribe',
                type=str,
                choices=['true', 'false'],
                help="If true, plugin receives message notifications (default: false)",
                metavar='true|false')

# Room plugin management
_ = ap.add_argument('--add-room-plugin',
                    type=str,
                    help=("Add a plugin to specific room(s). Accepts plugin ID (numeric) or Ed25519 "
                          "public key (64 hex chars). Requires --rooms. "
                          "Omitted flags retain their current values for existing plugins, or use defaults for new plugins."),
                    metavar='PLUGIN_ID_OR_ED_KEY')
_ = ap.add_argument('--room-plugin-approver',
                    type=str,
                    choices=['true', 'false'],
                    help="If true, plugin can approve/deny messages in these rooms (default: false)",
                    metavar='true|false')
_ = ap.add_argument('--room-plugin-required',
                    type=str,
                    choices=['true', 'false'],
                    help="If true, plugin must be connected for message approval in these rooms (default: false, has no effect if 'approver' is false)",
                    metavar='true|false')
_ = ap.add_argument('--room-plugin-subscribe',
                    type=str,
                    choices=['true', 'false'],
                    help="If true, plugin receives message notifications in these rooms (default: false)",
                    metavar='true|false')

# Plugin deletion
delete_plugin_group = ap.add_mutually_exclusive_group()
_ = delete_plugin_group.add_argument('--delete-plugin',
                type=str,
                help=("Delete a plugin entirely (including all room associations). Accepts plugin "
                      "ID (numeric) or Ed25519 public key (64 hex chars)."),
                metavar='PLUGIN_ID_OR_KEY')

_ = delete_plugin_group.add_argument('--delete-room-plugin',
                type=str,
                help=("Remove a plugin from specific room(s). Accepts plugin ID (numeric) or "
                      "Ed25519 public key (64 hex chars). Requires --rooms."),
                metavar='PLUGIN_ID_OR_KEY')

vis_group = ap.add_mutually_exclusive_group()
vis_group.add_argument(
    '--visible',
    action='store_true',
    help="Make an added moderator/admins' status publicly visible. This is the default for room "
    "mods, but not for global mods",
)
vis_group.add_argument(
    '--hidden',
    action='store_true',
    help="Hide the added moderator/admins' status from public users. This is the default for "
    "global mods, but not for room mods",
)

_ = ap.add_argument("--list-rooms",       "-L",      action='store_true', help="List current rooms and basic stats")
_ = ap.add_argument('--list-global-mods', '-M',      action='store_true', help="List global moderators/admins")
_ = ap.add_argument("--list-plugins",     "-P",      action='store_true', help="List all registered plugins and their room configurations")
_ = ap.add_argument('--install-plugins',  nargs='*',                      help="List available plugins or install specific plugin(s) by install ID. " "Usage: --install-plugins [install_id ...]")


ap.add_argument(
    "--verbose",
    "-v",
    action='store_true',
    help="Show more details for some commands, such as showing moderators/admins in room details",
)
ap.add_argument(
    "--yes", action='store_true', help="Don't prompt for confirmation for some commands, just do it"
)
ap.add_argument(
    "--initialize",
    action='store_true',
    help="Initialize database and private key if they do not exist; advanced use only.",
)
ap.add_argument(
    "--upgrade",
    "-U",
    action="store_true",
    help="Perform any required database upgrades.  If database upgrades are required then other "
    "commands will exit with an error message until this flag is used.  Note that this is "
    "normally not required: database upgrades are performed automatically during sogs daemon "
    "startup.",
)
ap.add_argument(
    "--check-upgrades",
    action="store_true",
    help="Check whether database upgrades are required then exit.  The exit code is 0 if no "
    "upgrades are needed, 5 if required upgrades were detected.",
)

def _resolve_plugin_id(plugin_id_or_key: str) -> int:
    if plugin_id_or_key.isdigit():
        return int(plugin_id_or_key)

    if len(plugin_id_or_key) != 64:
        raise ValueError(
            "Invalid plugin identifier: must be numeric ID or 64-char hex Ed25519 key")

    try:
        ed_pubkey = bytes.fromhex(plugin_id_or_key)
    except ValueError:
        raise ValueError(f"Invalid hex string: '{plugin_id_or_key}'")

    if len(ed_pubkey) != 32:
        raise ValueError(
            "Invalid Ed25519 public key: must be 64 hex characters (32 bytes)")

    from .db import query
    plugin = query("SELECT id FROM plugins WHERE ed_key = :key", key=ed_pubkey).first()

    if not plugin:
        raise ValueError(
            f"No plugin found with Ed25519 public key '{plugin_id_or_key}'")
    return plugin['id']


def scan_available_plugins() -> List[sogs.plugin.InstallPluginMetadata]:
    """Scan the plugins directory for available plugins with manifest.ini and .ini.sample files."""
    import os
    import configparser
    from pathlib import Path

    plugins_dir                              = Path(__file__).parent / 'plugins'
    result: List[sogs.plugin.InstallPluginMetadata] = []

    if not plugins_dir.exists():
        return result

    for item in os.listdir(plugins_dir):
        this_plugins_dir = plugins_dir / item
        if this_plugins_dir.is_dir() and not item.startswith('_') and not item.startswith('.'):
            manifest_path: pathlib.Path = this_plugins_dir / 'manifest.ini'
            if not manifest_path.exists():
                print(f"Warning: Skipping '{item}': manifest.ini not found", file=sys.stderr)
                continue

            try:
                config = configparser.ConfigParser()
                _      = config.read(manifest_path)

                if 'info' not in config:
                    print(f"Warning: Skipping '{item}': [info] section not found in manifest.ini", file=sys.stderr)
                    continue

                startup_file                  = config['info'].get('startup_file', '')
                install_id:      str          = startup_file[:-3] if startup_file.endswith('.py') else item
                sample_ini_path: pathlib.Path = this_plugins_dir / f"{install_id}.ini.sample"

                if not sample_ini_path.exists():
                    print(f"Warning: Skipping '{item}': {install_id}.ini.sample is required but not found", file=sys.stderr)
                    continue

                result.append(sogs.plugin.InstallPluginMetadata(
                    name             = config['info'].get('name',        item),
                    description      = config['info'].get('description', '(N/A)'),
                    version          = config['info'].get('version',     '(N/A)'),
                    author           = config['info'].get('author',      '(N/A)'),
                    startup_file     = startup_file,
                    directory        = this_plugins_dir,
                    manifest_path    = manifest_path,
                    sample_ini_path  = sample_ini_path,
                    desired_ini_path = this_plugins_dir / f"{install_id}.ini",
                ))
            except Exception as e:
                print(f"Warning: Failed to parse plugin manifest for {item}: {e}", file=sys.stderr)
    return result

def print_available_plugins_table(plugins: List[sogs.plugin.InstallPluginMetadata]):
    """Print a formatted table of available plugins using tabulate."""
    from tabulate import tabulate

    if not plugins:
        print("No available plugins found.")
        return

    table_data = []
    for i, plugin in enumerate(plugins):
        table_data.append([
            i + 1,
            plugin.install_id,
            plugin.name,
            plugin.version,
            plugin.author,
            plugin.description
        ])

    print("\n" + tabulate(
        table_data,
        headers      = ['#', 'Install ID', 'Name', 'Ver', 'Author', 'Description'],
        tablefmt     = 'simple_grid',
        maxcolwidths = [None, 18, 20, 6, 12, 50]
    ))
    print("\nTo install: python3 -m sogs --install-plugins <install_id> [ <install_id> ...]")


args = ap.parse_args()

update_room = not args.add_room and (
    args.description is not None
    or args.name is not None
    or args.add_moderators
    or args.delete_moderators
    or args.add_perms
    or args.remove_perms
    or args.clear_perms
)
incompat = [
    ('--add-room',           args.add_room),
    ('--delete-room',        args.delete_room),
    ('room modifiers',       update_room),
    ('--list-rooms',         args.list_rooms),
    ('--list-global-mods',   args.list_global_mods),
    ('--list-plugins',       args.list_plugins),
    ('--install-plugins',    args.install_plugins),
    ('--initialize',         args.initialize),
    ('--upgrade',            args.upgrade),
    ('--check-upgrades',     args.check_upgrades),
    ('--add-plugin',         args.add_plugin),
    ('--add-room-plugin',    args.add_room_plugin),
    ('--delete-room-plugin', args.delete_room_plugin),
    ('--delete-plugin',      args.delete_plugin),
]
for i in range(1, len(incompat)):
    for j in range(0, i):
        if incompat[j][1] and incompat[i][1]:
            print(f"Error: {incompat[j][0]} and {incompat[i][0]} are incompatible", file=sys.stderr)
            sys.exit(1)

if args.add_room_plugin: # Validate --add-plugin companion arguments
    if not args.rooms:
        print("Error: --add-room-plugin requires --rooms", file=sys.stderr)
        sys.exit(1)
    if '+' in args.rooms or '*' in args.rooms:
        print("Error: --add-room-plugin requires specific room tokens, not '+' or '*'", file=sys.stderr)
        sys.exit(1)

if args.delete_room_plugin: # Validate --delete-room-plugin arguments
    if not args.rooms:
        print("Error: --delete-room-plugin requires --rooms", file=sys.stderr)
        sys.exit(1)
    if '+' in args.rooms or '*' in args.rooms:
        print("Error: --delete-room-plugin requires specific room tokens, not '+' or '*'", file=sys.stderr)
        sys.exit(1)

if update_room and not args.rooms:
    print(
        "A room must be specified (using --rooms) when updating permissions or room details",
        file=sys.stderr,
    )
    sys.exit(1)
if args.rooms and not update_room and not args.delete_room_plugin and not args.add_room_plugin:
    # If we have --rooms but didn't recognize any of the `update_rooms` options then that means
    # `--rooms` was specify with some action (e.g. `--initialize`) that doesn't support --rooms:
    print("Error: --rooms specified without a room modification option", file=sys.stderr)
    sys.exit(1)

# Handle --install-plugins that doesn't require database (just listing)
if args.install_plugins is not None and (not args.install_plugins or (len(args.install_plugins) == 1 and args.install_plugins[0] == '')):
    plugins = scan_available_plugins()
    print_available_plugins_table(plugins)
    sys.exit(0)

from . import config, crypto, db
from .migrations.exc import DatabaseUpgradeRequired
from sqlalchemy_utils import database_exists
db_updated = False
try:
    if not args.initialize and not database_exists(config.DB_URL):
        raise RuntimeError(f"{config.DB_URL} database does not exist")

    if args.initialize:
        crypto.persist_privkey()

    db.init_engine(sogs_skip_init=True)

    db_updated = db.database_init(create=args.initialize, upgrade=args.upgrade)

except DatabaseUpgradeRequired as e:
    print(
        f"Database upgrades are required: {e}\n\n"
        "You can attempt the upgrade using the --upgrade flag; see --help for details."
    )
    sys.exit(5)

except Exception as e:
    print(
        f"""

SOGS initialization failed: {e}.


Perhaps you need to specify a SOGS_CONFIG path or use one of the --upgrade/--initialize options?
Try --help for additional information.
"""
    )
    sys.exit(1)

from . import web
from .model.room import Room, get_rooms
from .model.user import User, SystemUser, get_all_global_moderators
from .model.exc import AlreadyExists, NoSuchRoom, NoSuchUser

web.appdb = db.get_conn()


@atexit.register
def close_conn():
    web.appdb.close()


def print_room(room: Room):
    msgs, msgs_size = room.messages_size()
    files, files_size = room.attachments_size()
    reactions = room.reactions_counts()
    r_total = sum(x[1] for x in reactions)
    reactions.sort(key=lambda x: x[1], reverse=True)

    msgs_size /= 1_000_000
    files_size /= 1_000_000

    active = [room.active_users_last(x * 86400) for x in (1, 7, 14, 30)]
    m, a, hm, ha = room.get_all_moderators()
    admins = len(a) + len(ha)
    mods = len(m) + len(hm)

    perms = "{}read, {}write, {}upload, {}accessible".format(
        "+" if room.default_read else "-",
        "+" if room.default_write else "-",
        "+" if room.default_upload else "-",
        "+" if room.default_accessible else "-",
    )

    print(
        f"""
{room.token}
{"=" * len(room.token)}
Name: {room.name}
Description: {room.description}
URL: {config.URL_BASE}/{room.token}?public_key={crypto.server_pubkey_hex}
Messages: {msgs} ({msgs_size:.1f} MB)
Attachments: {files} ({files_size:.1f} MB)
Reactions: {r_total}; top 5: {', '.join(f"{r} ({c})" for r, c in reactions[0:5])}
Active users: {active[0]} (1d), {active[1]} (7d), {active[2]} (14d), {active[3]} (30d)
Default permissions: {perms}
Moderators: {admins} admins ({len(ha)} hidden), {mods} moderators ({len(hm)} hidden)""",
        end='',
    )
    if args.verbose and any((m, a, hm, ha)):
        print(":")
        for id in a:
            print(f"    - {id} (admin)")
        for id in ha:
            print(f"    - {id} (hidden admin)")
        for id in m:
            print(f"    - {id} (moderator)")
        for id in hm:
            print(f"    - {id} (hidden moderator)")
    else:
        print()


def room_token_valid(room):
    if not re.fullmatch(r'[\w-]{1,64}', room):
        print(
            "Error: room tokens may only contain a-z, A-Z, 0-9, _, and - characters",
            file=sys.stderr,
        )
        sys.exit(1)


def perm_flag_to_word(char):
    if char == 'r':
        return "read"
    if char == 'w':
        return "write"
    if char == 'u':
        return "upload"
    if char == 'a':
        return "accessible"

    print(f"Error: invalid permission flag '{char}'", file=sys.stderr)
    sys.exit(1)


perms = {}

def install_plugin_to_db(plugin_name: str, ed_pubkey: bytes, x_pubkey: bytes, is_global: bool, is_approver: bool, is_required: bool, is_subscribe: bool):
    assert len(ed_pubkey) == 32
    assert len(x_pubkey) == 32

    from .db import query
    # Check if plugin already exists
    existing = query("SELECT id, name, global, approver, required, subscribe FROM plugins WHERE ed_key = :key", key=ed_pubkey).first()

    fields: List[Tuple[str, str]] = []
    fields.append(("Ed25519 Pubkey", f"{ed_pubkey.hex()}"))
    fields.append(("X25519 Pubkey",  f"{x_pubkey.hex()}"))
    if existing is None:
        # Create new plugin with provided or default values
        plugin_id = db.insert_and_get_pk(
            "INSERT INTO plugins (name, ed_key, x_key, global, approver, required, subscribe) "
            "VALUES (:name, :ed_key, :x_key, :is_global, :is_approver, :is_required, :is_subscribe)",
            'id',
            name         = plugin_name,
            ed_key       = ed_pubkey,
            x_key        = x_pubkey,
            is_global    = is_global,
            is_approver  = is_approver,
            is_required  = is_required,
            is_subscribe = is_subscribe,)

        # Build list of all fields
        fields.append(("Name",      f"'{plugin_name}'"))
        fields.append(("Global",    f"{is_global}"))
        fields.append(("Approver",  f"{is_approver}"))
        fields.append(("Required",  f"{is_required}"))
        fields.append(("Subscribe", f"{is_subscribe}"))
    else:
        # Plugin exists - only update explicitly provided fields
        plugin_id:     int  = existing['id']
        old_name:      str  = existing['name'] or ""
        old_global:    bool = bool(existing['global'])
        old_approver:  bool = bool(existing['approver'])
        old_required:  bool = bool(existing['required'])
        old_subscribe: bool = bool(existing['subscribe'])

        # Determine which fields to update
        update_fields = []
        update_params: Dict[str, typing.Any] = {'id': plugin_id}

        # Name: update only if explicitly provided
        if args.plugin_name is not None:
            if old_name != plugin_name:
                update_fields.append("name = :name")
                update_params['name'] = plugin_name
            fields.append(("Name", f"'{old_name}' -> '{plugin_name}'"))
        else:
            fields.append(("Name", f"'{old_name}' (unchanged)"))

        # Always update keys (these are derived from the pubkey)
        update_fields.extend(["ed_key = :ed_key", "x_key = :x_key"])
        update_params['ed_key'] = ed_pubkey
        update_params['x_key'] = x_pubkey

        # Boolean flags: update only if explicitly provided
        if args.plugin_global is not None:
            if old_global != is_global:
                update_fields.append("global = :is_global")
                update_params['is_global'] = is_global
            fields.append(("Global", f"{old_global} -> {is_global}"))
        else:
            fields.append(("Global", f"{old_global} (unchanged)"))

        if args.plugin_approver is not None:
            if old_approver != is_approver:
                update_fields.append("approver = :is_approver")
                update_params['is_approver'] = is_approver
            fields.append(("Approver", f"{old_approver} -> {is_approver}"))
        else:
            fields.append(("Approver", f"{old_approver} (unchanged)"))

        if args.plugin_required is not None:
            if old_required != is_required:
                update_fields.append("required = :is_required")
                update_params['is_required'] = is_required
            fields.append(("Required", f"{old_required} -> {is_required}"))
        else:
            fields.append(("Required", f"{old_required} (unchanged)"))

        if args.plugin_subscribe is not None:
            if old_subscribe != is_subscribe:
                update_fields.append("subscribe = :is_subscribe")
                update_params['is_subscribe'] = is_subscribe
            fields.append(("Subscribe", f"{old_subscribe} -> {is_subscribe}"))
        else:
            fields.append(("Subscribe", f"{old_subscribe} (unchanged)"))

        # Execute update only if there are changes
        if update_fields:
            query(f"UPDATE plugins SET {', '.join(update_fields)} WHERE id = :id", **update_params)

    from sogs.utils import pretty_format_key_value_list
    print(f"  Plugin '{plugin_name}' (id={plugin_id}):\n    " + "\n    ".join(pretty_format_key_value_list(fields)))

def parse_and_set_perm_flags(flags, perm_setting):
    for char in flags:
        perm_type = perm_flag_to_word(char)
        if perm_type in perms:
            print(
                f"Error: permission flag '{char}' in more than one permission set "
                "(add/remove/clear)",
                file=sys.stderr,
            )
            sys.exit(1)
        perms[perm_type] = perm_setting


if args.add_perms:
    parse_and_set_perm_flags(args.add_perms, True)
if args.remove_perms:
    parse_and_set_perm_flags(args.remove_perms, False)
if args.clear_perms:
    parse_and_set_perm_flags(args.clear_perms, None)

if args.initialize:
    print("Database schema created.")

elif args.upgrade:
    print("Database successfully upgraded." if db_updated else "No database upgrades required.")

elif args.check_upgrades:
    print("No database upgrades required.")

elif args.add_room:
    room_token_valid(args.add_room)

    try:
        room = Room.create(
            token=args.add_room, name=args.name or args.add_room, description=args.description
        )
        if "read" in perms:
            room.default_read = perms["read"]
        if "write" in perms:
            room.default_write = perms["write"]
        if "accessible" in perms:
            room.default_accessible = perms["accessible"]
        if "upload" in perms:
            room.default_upload = perms["upload"]

    except AlreadyExists:
        print(f"Error: room '{args.add_room}' already exists!", file=sys.stderr)
        sys.exit(1)
    print(f"Created room {args.add_room}:")
    print_room(room)

elif args.delete_room:
    try:
        room = Room(token=args.delete_room)
    except NoSuchRoom:
        print(f"Error: no such room '{args.delete_room}'", file=sys.stderr)
        sys.exit(1)

    print_room(room)
    if args.yes:
        res = "y"
    else:
        res = input("Are you sure you want to delete this room? [yN] ")
    if res.startswith("y") or res.startswith("Y"):
        room.delete()
        print("Room deleted.")
    else:
        print("Aborted.")
        sys.exit(2)

elif update_room:
    rooms = []
    all_rooms = False
    global_rooms = False
    if len(args.rooms) > 1 and ('*' in args.rooms or '+' in args.rooms):
        print(
            "Error: '+'/'*' arguments to --rooms cannot be used with other rooms", file=sys.stderr
        )
        sys.exit(1)

    if args.rooms == ['+']:
        global_rooms = True
    elif args.rooms == ['*']:
        rooms = get_rooms()
        all_rooms = True
    else:
        try:
            rooms = [Room(token=r) for r in args.rooms]
        except NoSuchRoom as nsr:
            print(f"No such room: '{nsr.token}'", file=sys.stderr)
            sys.exit(1)

    if not len(rooms) and not global_rooms:
        print("Error: --rooms is required when updating room settings/permissions", file=sys.stderr)
        sys.exit(1)

    if args.add_moderators:
        for a in args.add_moderators:
            if not re.fullmatch(r'[012]5[A-Fa-f0-9]{64}', a):
                print(f"Error: '{a}' is not a valid session id", file=sys.stderr)
                sys.exit(1)

        sysadmin = SystemUser()

        if global_rooms:
            for sid in args.add_moderators:
                u = User(session_id=sid)
                u.set_moderator(admin=args.admin, visible=args.visible, added_by=sysadmin)
                print(
                    "Added {} as {} global {}".format(
                        sid,
                        "visible" if args.visible else "hidden",
                        "admin" if args.admin else "moderator",
                    )
                )
        else:
            for sid in args.add_moderators:
                u = User(session_id=sid, update_last_id=True)
                for room in rooms:
                    room.set_moderator(
                        u, admin=args.admin, visible=not args.hidden, added_by=sysadmin
                    )
                    print(
                        "Added {} as {} {} of {} ({})".format(
                            u.session_id,
                            "hidden" if args.hidden else "visible",
                            "admin" if args.admin else "moderator",
                            room.name,
                            room.token,
                        )
                    )

    if args.delete_moderators:
        for a in args.delete_moderators:
            if not re.fullmatch(r'[012]5[A-Fa-f0-9]{64}', a):
                print(f"Error: '{a}' is not a valid session id", file=sys.stderr)
                sys.exit(1)

        sysadmin = SystemUser()

        if global_rooms:
            for sid in args.delete_moderators:
                try:
                    u = User(session_id=sid, autovivify=False)
                    if u.global_admin or u.global_moderator:
                        was_admin = u.global_admin
                        u.remove_moderator(removed_by=sysadmin)
                        print(
                            f"Removed {u.session_id} "
                            f"(identified by {sid}) "
                            f"as global {'admin' if was_admin else 'moderator'}"
                        )
                except NoSuchUser:
                    pass
        else:
            for sid in args.delete_moderators:
                try:
                    u = User(session_id=sid, autovivify=False)
                    for room in rooms:
                        room.remove_moderator(u, removed_by=sysadmin)
                        print(
                            f"Removed {u.session_id} "
                            f"(identified by {sid}) "
                            f"as moderator/admin of {room.name} ({room.token})"
                        )
                except NoSuchUser:
                    pass

    if args.add_perms or args.clear_perms or args.remove_perms:
        if global_rooms:
            print(
                "Error: --rooms cannot be '+' (i.e. global) when updating room permissions",
                file=sys.stderr,
            )
            sys.exit(1)

        vivify = args.add_perms or args.remove_perms
        users = []
        if args.users:
            users = [
                User(session_id=sid, autovivify=vivify, update_last_id=True) for sid in args.users
            ]

        # users not specified means set room defaults
        if not len(users):
            for room in rooms:
                if "read" in perms:
                    room.default_read = perms["read"]
                    print(
                        ('Enabled' if room.default_read else 'Disabled')
                        + f" default read permission in {room.token}"
                    )
                if "write" in perms:
                    room.default_write = perms["write"]
                    print(
                        ('Enabled' if room.default_write else 'Disabled')
                        + f" default write permission in {room.token}"
                    )
                if "accessible" in perms:
                    room.default_accessible = perms["accessible"]
                    print(
                        ('Enabled' if room.default_accessible else 'Disabled')
                        + f" default accessible permission in {room.token}"
                    )
                if "upload" in perms:
                    room.default_upload = perms["upload"]
                    print(
                        ('Enabled' if room.default_upload else 'Disabled')
                        + f" default upload permission in {room.token}"
                    )
        else:
            sysadmin = SystemUser()
            for room in rooms:
                for user in users:
                    room.set_permissions(user, mod=sysadmin, **perms)
                    print(f"Updated room permissions for {user} in {room.token}")

    if args.description is not None:
        if global_rooms or all_rooms:
            print(
                "Error: --rooms cannot be '+' or '*' (i.e. global/all) with --description",
                file=sys.stderr,
            )
            sys.exit(1)

        for room in rooms:
            room.description = None if not args.description else args.description
            print(f"Updated {room.token} description to:\n\n{room.description}\n")

    if args.name is not None:
        if global_rooms or all_rooms:
            print(
                "Error: --rooms cannot be '+' or '*' (i.e. global/all) with --name", file=sys.stderr
            )
            sys.exit(1)

        for room in rooms:
            old = room.name
            room.name = args.name
            print(f"Changed {room.token} name from '{old}' to '{room.name}'")

elif args.list_rooms:
    rooms = get_rooms()
    if rooms:
        for room in rooms:
            print_room(room)
    else:
        print("No rooms.")

elif args.list_global_mods:
    m, a, hm, ha = get_all_global_moderators()
    admins = len(a) + len(ha)
    mods = len(m) + len(hm)

    print(f"{admins} global admins ({len(ha)} hidden), {mods} moderators ({len(hm)} hidden):")
    for u in a:
        print(f"- {u.session_id} (admin)")
    for u in ha:
        print(f"- {u.session_id} (hidden admin)")
    for u in m:
        print(f"- {u.session_id} (moderator)")
    for u in hm:
        print(f"- {u.session_id} (hidden moderator)")

elif args.list_plugins:
    from .db import query

    # Get all plugins
    plugins = query("SELECT id, name, ed_key, x_key, global, approver, required, subscribe FROM plugins ORDER BY id").all()

    if not plugins:
        print("No plugins registered.")
    else:
        for index, plugin in enumerate(plugins):
            plugin_id      = typing.cast(int, plugin['id'])
            name           = typing.cast(str, plugin['name'])
            global_flag    = bool(plugin['global'])
            approver_flag  = bool(plugin['approver'])
            required_flag  = bool(plugin['required'])
            subscribe_flag = bool(plugin['subscribe'])

            # Get keys from database
            ed25519_key = typing.cast(bytes, plugin['ed_key'])
            x25519_key  = typing.cast(bytes, plugin['x_key'])

            def _verify_plugin_keys(ed_key: bytes, x_key: bytes) -> bool:
                import nacl.bindings as sodium
                expected_x_key = sodium.crypto_sign_ed25519_pk_to_curve25519(ed_key)
                return expected_x_key == x_key

            # Verify keys match
            invalid_key_warning = ""
            try:
                if not _verify_plugin_keys(ed25519_key, x25519_key):
                    invalid_key_warning = " (⛔ X25519 key does not match the derived x-key from ed25519, restart the server to auto-repair the key)"
            except Exception as e:
                invalid_key_warning = " (⛔ Ed25519 pubkey was not a valid key)"

            print(f"[{index:02d}] '{name}' (Plugin ID={plugin_id})")
            print(f"  Ed25519 Pubkey:                     {ed25519_key.hex()}{invalid_key_warning}")
            print(f"  X25519 Pubkey:                      {x25519_key.hex()}")
            print(f"  Global/Approver/Required/Subscribe: {global_flag}/{approver_flag}/{required_flag}/{subscribe_flag}")

            # Get room_plugins for this plugin
            room_plugins = query(("SELECT rp.room, r.token, r.name, rp.approver, rp.required, rp.subscribe "
                                  "FROM room_plugins rp JOIN rooms r ON rp.room = r.id "
                                  "WHERE rp.plugin = :plugin_id ORDER BY r.token"),
                                 plugin_id=plugin_id).all()

            if room_plugins:
                print(f"  Rooms ({len(room_plugins)})")
                for index, row in enumerate(room_plugins):
                    room_name    = typing.cast(str, row['name'])
                    room_id      = typing.cast(int, row['room'])
                    room_token   = typing.cast(bytes, row['token'])
                    rp_approver  = bool(typing.cast(int, row['approver']))
                    rp_required  = bool(typing.cast(int, row['required']))
                    rp_subscribe = bool(typing.cast(int, row['subscribe']))

                    print(f"    [{index:02d}] '{room_name}' (Room ID={room_id})")
                    print(f"      Room Token:                  {room_token}")
                    print(f"      Approver/Required/Subscribe: {rp_approver}/{rp_required}/{rp_subscribe}")
            else:
                print("  Rooms (0)")

            print()  # Empty line between plugins

elif typing.cast(bool, args.add_plugin):
    import nacl.bindings as sodium
    try:
        ed_pubkey = bytes.fromhex(args.add_plugin)
    except ValueError:
        print(f"Error: '{args.add_plugin}' is not a valid hex string", file=sys.stderr)
        sys.exit(1)

    if len(ed_pubkey) != sodium.crypto_sign_PUBLICKEYBYTES:
        print((f"Error: Ed25519 public key must be {sodium.crypto_sign_PUBLICKEYBYTES} bytes "
              f"({sodium.crypto_sign_PUBLICKEYBYTES * 2} hex chars), got {len(ed_pubkey)} bytes"),
              file=sys.stderr)
        sys.exit(1)

    # Generate default name: first 4 hex chars + ".." + last 4 hex chars of ed key
    default_name = f"{ed_pubkey[:2].hex()}..{ed_pubkey[-2:].hex()}"
    plugin_name = args.plugin_name if args.plugin_name is not None else default_name

    # Parse boolean flags (default to False if not specified)
    is_global    = args.plugin_global    == 'true' if args.plugin_global    is not None else False
    is_approver  = args.plugin_approver  == 'true' if args.plugin_approver  is not None else False
    is_required  = args.plugin_required  == 'true' if args.plugin_required  is not None else False
    is_subscribe = args.plugin_subscribe == 'true' if args.plugin_subscribe is not None else False

    # Derive x25519 key from ed25519 key
    x_pubkey = sodium.crypto_sign_ed25519_pk_to_curve25519(ed_pubkey)

    from .db import query
    with db.transaction():
        install_plugin_to_db(ed_pubkey, x_pubkey, is_global, is_approver, is_required, is_subscribe)

elif typing.cast(bool, args.add_room_plugin):
    # Resolve plugin identifier
    try:
        plugin_id = _resolve_plugin_id(args.add_room_plugin)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Parse boolean flags (default to False if not specified)
    room_plugin_approver  = args.room_plugin_approver == 'true' if args.room_plugin_approver  is not None else False
    room_plugin_required  = args.room_plugin_required == 'true' if args.room_plugin_required  is not None else False
    room_plugin_subscribe = args.room_plugin_subscribe == 'true' if args.room_plugin_subscribe is not None else False

    # Verify plugin exists
    from .db import query
    plugin = query("SELECT id, name FROM plugins WHERE id = :id", id=plugin_id).first()
    if not plugin:
        print(f"Error: Plugin with ID {plugin_id} not found", file=sys.stderr)
        sys.exit(1)

    # Process each room
    plugin_name: str = plugin['name'] or f"Plugin {plugin_id}"
    for room_token in args.rooms:
        try:
            room = Room(token=room_token)

            with db.transaction():
                # Check if entry already exists
                existing = query(
                    "SELECT approver, required, subscribe FROM room_plugins "
                    "WHERE plugin = :plugin_id AND room = :room_id",
                    plugin_id=plugin_id,
                    room_id=room.id
                ).first()

                fields: List[Tuple[str, str]] = []
                fields.append(("Room", f"{room_token}"))
                if existing:
                    old_approver  = bool(existing['approver'])
                    old_required  = bool(existing['required'])
                    old_subscribe = bool(existing['subscribe'])

                    # Determine which fields to update (only explicitly provided ones)
                    update_fields = []
                    update_params: dict = {'plugin_id': plugin_id, 'room_id': room.id}

                    if args.room_plugin_approver is not None:
                        if old_approver != room_plugin_approver:
                            update_fields.append("approver = :approver")
                            update_params['approver'] = room_plugin_approver
                        fields.append(("Approver", f"{old_approver} -> {room_plugin_approver}"))
                    else:
                        fields.append(("Approver", f"{old_approver} (unchanged)"))

                    if args.room_plugin_required is not None:
                        if old_required != room_plugin_required:
                            update_fields.append("required = :required")
                            update_params['required'] = room_plugin_required
                        fields.append(("Required", f"{old_required} -> {room_plugin_required}"))
                    else:
                        fields.append(("Required", f"{old_required} (unchanged)"))

                    if args.room_plugin_subscribe is not None:
                        if old_subscribe != room_plugin_subscribe:
                            update_fields.append("subscribe = :subscribe")
                            update_params['subscribe'] = room_plugin_subscribe
                        fields.append(("Subscribe", f"{old_subscribe} -> {room_plugin_subscribe}"))
                    else:
                        fields.append(("Subscribe", f"{old_subscribe} (unchanged)"))

                    # Execute update only if there are changes
                    if update_fields:
                        query(
                            f"UPDATE room_plugins SET {', '.join(update_fields)} "
                            "WHERE plugin = :plugin_id AND room = :room_id",
                            **update_params
                        )
                else:
                    # Create new room plugin entry with provided or default values
                    query(
                        "INSERT INTO room_plugins (plugin, room, approver, required, subscribe) "
                        "VALUES (:plugin_id, :room_id, :approver, :required, :subscribe)",
                        plugin_id = plugin_id,
                        room_id   = room.id,
                        approver  = room_plugin_approver,
                        required  = room_plugin_required,
                        subscribe = room_plugin_subscribe
                    )

                    fields.append(("Approver",  f"{room_plugin_approver}"))
                    fields.append(("Required",  f"{room_plugin_required}"))
                    fields.append(("Subscribe", f"{room_plugin_subscribe}"))

                from sogs.utils import pretty_format_key_value_list
                print(f"Room plugin '{plugin_name}' (id={plugin_id}):\n  " + "\n  ".join(pretty_format_key_value_list(fields)))

        except NoSuchRoom:
            print(f"Error: Room '{room_token}' not found", file=sys.stderr)

elif typing.cast(bool, args.delete_room_plugin):
    try:
        plugin_id = _resolve_plugin_id(typing.cast(str, args.delete_room_plugin))
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Verify plugin exists
    from .db import query
    plugin = query("SELECT id, name FROM plugins WHERE id = :id", id=plugin_id).first()
    if not plugin:
        print(f"Error: Plugin with ID {plugin_id} not found", file=sys.stderr)
        sys.exit(1)

    plugin_name = plugin['name'] or f"Plugin {plugin_id}"
    for room_token in args.rooms:
        try:
            room = Room(token=room_token)
            with db.transaction():
                result = query("DELETE FROM room_plugins WHERE plugin = :plugin_id AND room = :room_id", plugin_id=plugin_id, room_id=room.id)
                if result.rowcount > 0:
                    print(f"Removed '{plugin_name}' from room '{room_token}'")
                else:
                    print(f"'{plugin_name}' plugin was not configured for room '{room_token}'")
        except NoSuchRoom:
            print(f"Error: Room '{room_token}' not found", file=sys.stderr)

elif typing.cast(bool, args.delete_plugin):
    try:
        plugin_id = _resolve_plugin_id(typing.cast(str, args.delete_plugin))
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    from .db import query
    plugin = query("SELECT id, name FROM plugins WHERE id = :id", id=plugin_id).first()
    if not plugin:
        print(f"Error: Plugin with ID {plugin_id} not found", file=sys.stderr)
        sys.exit(1)

    plugin_name = plugin['name'] or f"Plugin {plugin_id}"

    # Check for room associations
    room_count = query("SELECT COUNT(*) as count FROM room_plugins WHERE plugin = :plugin_id", plugin_id=plugin_id).first()['count']

    # Delete plugin (room_plugins will be cascade deleted)
    with db.transaction():
        query("DELETE FROM plugins WHERE id = :id", id=plugin_id)

    if room_count:
        print(f"Deleted plugin '{plugin_name}' (ID={plugin_id}) and removed from {room_count} room(s)")
    else:
        print(f"Deleted plugin '{plugin_name}' (ID={plugin_id})")

elif typing.cast(Optional[List[str]], args.install_plugins) is not None:
    install_plugins                                  = typing.cast(List[str], args.install_plugins)
    plugins: List[sogs.plugin.InstallPluginMetadata] = scan_available_plugins()

    if len(plugins) == 0:
        print("No plugins available to install")
        sys.exit(0)

    if len(install_plugins) == 1 and install_plugins[0] == '':
        print_available_plugins_table(plugins)
        sys.exit(0)

    import importlib.util
    import nacl.public
    import nacl.signing
    import nacl.bindings
    import pathlib
    import configparser
    import tempfile
    import os

    import sogs.config
    import sogs.crypto

    for install_id in install_plugins:
        if not install_id:
            continue

        # Find the plugin with the specified install ID
        plugin: Optional[sogs.plugin.InstallPluginMetadata] = None
        for plugins_it in plugins:
            if plugins_it.install_id == install_id:
                plugin = plugins_it
                break

        if plugin == None:
            print(f"Unknown plugin specified '{install_id}', skipping", file=sys.stderr)
            continue

        # Load the ini file into memory
        ini_parser = configparser.ConfigParser(strict=False)
        try:
            if plugin.desired_ini_path.exists():
                _ = ini_parser.read(plugin.desired_ini_path)
            else:
                _ = ini_parser.read(plugin.sample_ini_path)
        except Exception as e:
            print("Failed to parse plugin sample .INI file ({plugin_sample_ini_path}): {e}", file=sys.stderr)
            continue

        # Setup [plugin] sogs pubkey/address
        PLUGIN_SECTION = 'plugin'
        if not ini_parser.has_section(PLUGIN_SECTION):
            ini_parser.add_section(PLUGIN_SECTION)

        _ = ini_parser.set(PLUGIN_SECTION, 'sogs_pubkey_hex', sogs.crypto.server_pubkey_hex)

        listen_addr = ""
        for it in sogs.config.OMQ_LISTEN:
            if len(listen_addr):
                listen_addr += "\n  "
            listen_addr += it
        _ = ini_parser.set(PLUGIN_SECTION, 'sogs_address', listen_addr)

        # Setup [<install_id>] default fields
        INSTALL_ID_SECTION = plugin.install_id
        if not ini_parser.has_section(INSTALL_ID_SECTION):
            ini_parser.add_section(INSTALL_ID_SECTION)

        # Load or generate Ed25519 key specified by [<install_id>].key_file
        print(f"Installing {plugin.name}...")
        key_file: str          = ini_parser[INSTALL_ID_SECTION].get('key_file', f"{plugin.install_id}_ed25519")
        key_path: pathlib.Path = plugin.directory / key_file
        if pathlib.Path(key_path).exists():
            print(f"  Loading Ed25519 key from {key_path.relative_to(os.getcwd())} ...", end=" ", flush=True)
        else:
            print(f"  Generating Ed25519 key to {key_path.relative_to(os.getcwd())} ...", end=" ", flush=True)
        ed25519_key_bytes: bytes = sogs.plugin.Plugin.get_or_make_ed25519_privkey(str(key_path))
        ed25519_key              = nacl.signing.SigningKey(ed25519_key_bytes[:nacl.bindings.crypto_sign_SEEDBYTES])
        print(f"public: {bytes(ed25519_key.verify_key).hex()}", flush=True)

        # Derive X25519 key to register into the SOGS instance plugin database
        print("  Deriving X25519 key ...", end=" ", flush=True)
        x25519_key:  nacl.public.PrivateKey = ed25519_key.to_curve25519_private_key()
        x25519_pkey: nacl.public.PublicKey  = ed25519_key.verify_key.to_curve25519_public_key()
        print(f"public: {bytes(x25519_pkey).hex()}", flush=True)

        # Run the plugin's installation hook
        startup_plugin_py_path = plugin.directory / plugin.startup_file
        if not startup_plugin_py_path.exists():
            print(f"\nError: Installation failed for {plugin.name}, plugin specified by `startup_file` does not exist: {startup_plugin_py_path}", file=sys.stderr)
            continue

        # Handle plugin installation result
        try:
            spec = importlib.util.spec_from_file_location(f"sogs.plugins.{install_id}", startup_plugin_py_path)
            if not spec or not spec.loader:
                print(f"\nError: Installation failed for {plugin.name}, `startup_file` plugin ({startup_plugin_py_path}) did not load successfully: is it a valid python file?", file=sys.stderr)
                continue

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            if not hasattr(module, 'install_hook'):
                print(f"\nError: Installation failed for {plugin.name}, `startup_file` plugin ({startup_plugin_py_path}) did not implement `install_hook(plugin: sogs.plugin.InstallPluginMetadata)`", file=sys.stderr)
                continue

            print("  Running plugin installer ...")
            install_result = module.install_hook(plugin)  # pyright: ignore[reportAny]
            if not isinstance(install_result, sogs.plugin.InstallPluginResult):
                print(f"\nError: Installation failed for {plugin.name}, `startup_file` plugin ({startup_plugin_py_path}) install hook did not return a sogs.plugin.InstallPluginResult value: returned ({typeof(install_result)})", file=sys.stderr)
                continue

            if not install_result.success:
                print(f"\nError: Installation failed for {plugin.name}, `startup_file` plugin ({startup_plugin_py_path}) install failed: {install_result.err_msg}", file=sys.stderr)
                continue

        except Exception as e:
            print(f"\nError: Installation failed for {plugin.name}, loading of `startup_file` plugin ({startup_plugin_py_path}) failed: {e}", file=sys.stderr)
            continue

        # All installation steps run successfully, we'll now commit the config files update the DB
        # in a semi-atomic manner (everything thus far has been mutating runtime memory only).
        ini_bak_path = plugin.desired_ini_path.with_suffix('.bak')
        key_bak_path = key_path.with_suffix('.bak')
        try:
            with tempfile.NamedTemporaryFile() as tmp_ini_file:
                with tempfile.NamedTemporaryFile() as tmp_ed25519_key:
                    from .db import query
                    with db.transaction():
                        # Register the plugin to the DB
                        print("  Registering plugin in SOGS instance ...", flush=True)
                        is_global    = False
                        is_approver  = False
                        is_required  = False
                        is_subscribe = False
                        install_plugin_to_db(plugin.name, bytes(ed25519_key.verify_key), bytes(x25519_pkey), is_global, is_approver, is_required, is_subscribe)

                        # Write the files we care about inside the DB transaction, an exception will
                        # rollback any changes and undo the plugin installation essentially.
                        import io
                        ini_buffer = io.StringIO()
                        _          = ini_parser.write(ini_buffer)

                        # Save .ini file and ed25519 keypair to temporary location
                        _ = tmp_ed25519_key.write(ed25519_key_bytes)
                        _ = tmp_ini_file.write(ini_buffer.getvalue().encode())

                        # Move the old files, if they exist, this doubles as a permission check that we
                        # can indeed replace the file. If these fail, an exception is raised
                        if key_path.exists():
                            _ = shutil.move(src=key_path, dst=key_bak_path)

                        if plugin.desired_ini_path.exists():
                            _ = shutil.move(src=plugin.desired_ini_path, dst=ini_bak_path)

                        # Now move the files into place
                        _ = shutil.move(src=tmp_ini_file.name, dst=plugin.desired_ini_path)
                        _ = shutil.move(src=tmp_ed25519_key.name, dst=key_path)
        except Exception as e:
            print(f"  Error: Installation failed for {plugin.name}, writing installation to disk did not succeed: {e}", file=sys.stderr)
            try:
                if ini_bak_path.exists():
                    _ = shutil.move(ini_bak_path, plugin.desired_ini_path)
                if key_bak_path.exists():
                    _ = shutil.move(key_bak_path, key_path)
                print("  Installation reverted", file=sys.stderr)
            except Exception as rollback_e:
                print(f"CRITICAL: Failed to roll back file system changes: {rollback_e}", file=sys.stderr)
                print("Please restore the .bak files ({ini_bak_path} and {key_bak_path}) manually by removing the .bak suffix from them", file=sys.stderr)
else:
    print("Error: no action given", file=sys.stderr)
    ap.print_usage()
    sys.exit(1)
