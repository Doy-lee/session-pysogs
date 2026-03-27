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

    # 1. List available plugins that can be installed:
    # 2. Install a plugin by specifying the plugin id "api_debug" with optional flags (applies to all installed plugins):
    # 3. Uninstall the 'api_debug' plugin entirely (and from any room using it):
    python3 -msogs --install-plugins
    python3 -msogs --install-plugins   api_debug --install-plugin-global true --install-plugin-approver true --install-plugin-required true --install-plugin-subscribe true
    python3 -msogs --uninstall-plugins api_debug

    # 1. Add a plugin to specific rooms
    # 2. Remove the 'api_debug' plugin from specific rooms by install_id:
    python3 -msogs --add-room-plugin    api_debug --rooms my-room other-room --room-plugin-approver false --room-plugin-required false --room-plugin-subscribe false
    python3 -msogs --delete-room-plugin api_debug --rooms my-room other-room


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
_ = ap.add_argument('--install-plugin-global',
                type=str,
                choices=['true', 'false'],
                help="If true, this plugin applies to all rooms (default: false). Applies to all plugins specified in --install-plugins.",
                metavar='true|false')
_ = ap.add_argument('--install-plugin-approver',
                type=str,
                choices=['true', 'false'],
                help="If true, plugin can approve/deny messages (default: false). Applies to all plugins specified in --install-plugins.",
                metavar='true|false')
_ = ap.add_argument('--install-plugin-required',
                type=str,
                choices=['true', 'false'],
                help="If true, plugin must be connected for message approval (default: false). Applies to all plugins specified in --install-plugins.",
                metavar='true|false')
_ = ap.add_argument('--install-plugin-subscribe',
                type=str,
                choices=['true', 'false'],
                help="If true, plugin receives message notifications (default: false). Applies to all plugins specified in --install-plugins.",
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
_ = delete_plugin_group.add_argument('--uninstall-plugins',
                nargs='*',
                help="Uninstall one or more plugins by install_id. Removes plugin and all room associations. With no arguments, lists installed plugins.",
                metavar='INSTALL_ID')

_ = delete_plugin_group.add_argument('--delete-room-plugin',
                nargs='+',
                help="Remove one or more plugins from specific room(s) by install_id. Requires --rooms.",
                metavar='INSTALL_ID')

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
_ = ap.add_argument('--install-plugins',  nargs='*',                      help="List available plugins or install specific plugin(s) by install ID")


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

def _resolve_plugin_by_install_id(install_id: str) -> int:
    """Resolve a plugin by its install_id."""
    from .db import query
    plugin = query("SELECT id FROM plugins WHERE install_id = :install_id", install_id=install_id).first()
    if not plugin:
        raise ValueError(f"No plugin found with install_id '{install_id}'")
    return plugin[0]


def print_plugin_listings(available_plugins: List[sogs.plugin.InstallPluginMetadata]):
    """Print installed, broken, and available plugins in formatted tables."""
    from .db import query
    from tabulate import tabulate
    import pathlib
    import configparser

    installed_plugins = query("SELECT id, name, install_id, ed_key, global, approver, required, subscribe FROM plugins ORDER BY id").all()
    available_plugin_ids = {p.install_id for p in available_plugins}
    plugins_dir = pathlib.Path(__file__).parent / 'plugins'

    installed_table = []
    broken_table = []
    uninstalled_table = []

    for idx, db_plugin in enumerate(installed_plugins):
        db_install_id: str = db_plugin[2]
        db_name: str       = db_plugin[1]
        ed_key_bytes       = db_plugin[3]
        is_global          = "✓" if db_plugin[4] else "✗"
        is_approver        = "✓" if db_plugin[5] else "✗"
        is_required        = "✓" if db_plugin[6] else "✗"
        is_subscribe       = "✓" if db_plugin[7] else "✗"

        # Format Ed25519 key: first 3 bytes (6 hex) + ... + last 3 bytes (6 hex)
        if ed_key_bytes and len(ed_key_bytes) == 32:
            ed_key_hex = ed_key_bytes.hex()
            formatted_key = f"{ed_key_hex[:6]}...{ed_key_hex[-6:]}"
        else:
            formatted_key = "-"

        if db_install_id in available_plugin_ids:
            installed_table.append([
                idx + 1,
                db_install_id,
                db_name,
                formatted_key,
                is_global,
                is_approver,
                is_required,
                is_subscribe,
                next((p.version for p in available_plugins if p.install_id == db_install_id), ""),
                next((p.author for p in available_plugins if p.install_id == db_install_id), ""),
            ])
        else:
            # Check what's broken for this plugin
            missing_parts = []
            plugin_dir = plugins_dir / db_install_id
            
            if not plugin_dir.exists():
                missing_parts.append(f"Plugin directory missing: sogs/plugins/{db_install_id}/")
            else:
                manifest_path = plugin_dir / "manifest.ini"
                if not manifest_path.exists():
                    missing_parts.append("manifest.ini not found in plugin directory")
                else:
                    try:
                        manifest_config = configparser.ConfigParser()
                        manifest_config.read(manifest_path)
                        if 'info' in manifest_config:
                            startup_file = manifest_config['info'].get('startup_file', '')
                            if startup_file and not (plugin_dir / startup_file).exists():
                                missing_parts.append(f"Startup file '{startup_file}' not found (specified in manifest.ini)")
                        else:
                            missing_parts.append("manifest.ini missing [info] section")
                    except Exception:
                        missing_parts.append("manifest.ini is corrupted or unreadable")
            
            broken_table.append([
                idx + 1,
                db_install_id,
                db_name,
                formatted_key,
                is_global,
                is_approver,
                is_required,
                is_subscribe,
                "; ".join(missing_parts) if missing_parts else "Plugin source files missing",
            ])

    for idx, plugin in enumerate(available_plugins):
        if plugin.install_id not in {p[2] for p in installed_plugins}:
            uninstalled_table.append([
                idx + 1,
                plugin.install_id,
                plugin.name,
                plugin.version,
                plugin.author,
            ])

    if installed_table:
        print(f"\nInstalled Plugins ({len(installed_table)})")
        print(tabulate(
            installed_table,
            headers=['#', 'Install ID', 'Name', 'Ed25519 Key', 'Global', 'Approver', 'Required', 'Subscribe', 'Ver', 'Author'],
            tablefmt='simple_grid',
            maxcolwidths=[None, 18, 18, 15, 8, 9, 9, 10, 6, 12]
        ))

    if broken_table:
        print(f"\nBroken Plugins ({len(broken_table)} plugin installation(s) have issues)")
        print(tabulate(
            broken_table,
            headers=['#', 'Install ID', 'Name', 'Ed25519 Key', 'Global', 'Approver', 'Required', 'Subscribe', 'Missing'],
            tablefmt='simple_grid',
            maxcolwidths=[None, 18, 18, 15, 8, 9, 9, 10, 50]
        ))
        print("\nTo remove a broken plugin, run:")
        print("  python3 -m sogs --uninstall-plugins <install_id> [<install_id> ...]")

    if uninstalled_table:
        print(f"\nUninstalled Plugins ({len(uninstalled_table)})")
        print(tabulate(
            uninstalled_table,
            headers=['#', 'Install ID', 'Name', 'Ver', 'Author'],
            tablefmt='simple_grid',
            maxcolwidths=[None, 18, 20, 6, 12]
        ))

    if not installed_table and not broken_table and not uninstalled_table:
        print("No plugins available.")

    print("\nTo install: python3 -m sogs --install-plugins <install_id> [<install_id> ...] [--install-plugin-global true] [--install-plugin-approver true] ...")
    print("To uninstall: python3 -m sogs --uninstall-plugins <install_id> [<install_id> ...]")


def scan_available_plugins() -> List[sogs.plugin.InstallPluginMetadata]:
    """Scan the plugins directory for available plugins with manifest.ini and .ini.sample files."""
    import os
    import configparser
    from pathlib import Path
    from . import config

    plugins_dir                                     = Path(__file__).parent / 'plugins'
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
                manifest_config = configparser.ConfigParser()
                _      = manifest_config.read(manifest_path)

                if 'info' not in manifest_config:
                    print(f"Warning: Skipping '{item}': [info] section not found in manifest.ini", file=sys.stderr)
                    continue

                startup_file                  = manifest_config['info'].get('startup_file', '')
                install_id:      str          = startup_file[:-3] if startup_file.endswith('.py') else item
                sample_ini_path: pathlib.Path = this_plugins_dir / f"{install_id}.ini.sample"

                if not sample_ini_path.exists():
                    print(f"Warning: Skipping '{item}': {install_id}.ini.sample is required but not found", file=sys.stderr)
                    continue

                # Calculate data directory path
                data_dir = pathlib.Path(config.DATA_DIR) / 'plugins' / install_id

                result.append(sogs.plugin.InstallPluginMetadata(
                    name             = manifest_config['info'].get('name',        item),
                    description      = manifest_config['info'].get('description', '(N/A)'),
                    version          = manifest_config['info'].get('version',     '(N/A)'),
                    author           = manifest_config['info'].get('author',      '(N/A)'),
                    startup_file     = startup_file,
                    directory        = this_plugins_dir,
                    manifest_path    = manifest_path,
                    sample_ini_path  = sample_ini_path,
                    data_dir         = data_dir,
                ))
            except Exception as e:
                print(f"Warning: Failed to parse plugin manifest for {item}: {e}", file=sys.stderr)
    return result


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
    ('--install-plugins',    args.install_plugins),
    ('--initialize',         args.initialize),
    ('--upgrade',            args.upgrade),
    ('--check-upgrades',     args.check_upgrades),
    ('--add-room-plugin',    args.add_room_plugin),
    ('--delete-room-plugin', args.delete_room_plugin),
    ('--uninstall-plugins',  args.uninstall_plugins),
]
for i in range(1, len(incompat)):
    for j in range(0, i):
        if incompat[j][1] and incompat[i][1]:
            print(f"Error: {incompat[j][0]} and {incompat[i][0]} are incompatible", file=sys.stderr)
            sys.exit(1)

if args.add_room_plugin: # Validate --add-room-plugin companion arguments
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

elif typing.cast(bool, args.add_room_plugin):
    # Resolve plugin identifier
    try:
        plugin_id = _resolve_plugin_by_install_id(args.add_room_plugin)
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
    plugin_name: str = plugin[1] or f"Plugin {plugin_id}"
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
                    else:
                        fields.append(("Approver", f"{old_approver} (unchanged)"))

                    if args.room_plugin_required is not None:
                        if old_required != room_plugin_required:
                            update_fields.append("required = :required")
                            update_params['required'] = room_plugin_required
                            fields.append(("Required", f"{old_required} -> {room_plugin_required}"))
                        else:
                            fields.append(("Required", f"{old_required} (unchanged)"))
                    else:
                        fields.append(("Required", f"{old_required} (unchanged)"))

                    if args.room_plugin_subscribe is not None:
                        if old_subscribe != room_plugin_subscribe:
                            update_fields.append("subscribe = :subscribe")
                            update_params['subscribe'] = room_plugin_subscribe
                            fields.append(("Subscribe", f"{old_subscribe} -> {room_plugin_subscribe}"))
                        else:
                            fields.append(("Subscribe", f"{old_subscribe} (unchanged)"))
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
    from .db import query
    for install_id in args.delete_room_plugin:
        try:
            plugin_id = _resolve_plugin_by_install_id(install_id)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            continue

        plugin = query("SELECT id, name FROM plugins WHERE id = :id", id=plugin_id).first()
        if not plugin:
            print(f"Error: Plugin with install_id '{install_id}' not found", file=sys.stderr)
            continue

        plugin_name = plugin[1] or f"Plugin {plugin_id}"
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

elif typing.cast(Optional[List[str]], args.uninstall_plugins) is not None:
    from .db import query

    uninstall_plugins = typing.cast(List[str], args.uninstall_plugins)

    if len(uninstall_plugins) == 0:
        plugins = scan_available_plugins()
        print_plugin_listings(plugins)
        sys.exit(0)

    for install_id in uninstall_plugins:
        try:
            plugin_id = _resolve_plugin_by_install_id(install_id)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            continue

        plugin = query("SELECT id, name FROM plugins WHERE id = :id", id=plugin_id).first()
        if not plugin:
            print(f"Error: Plugin with install_id '{install_id}' not found", file=sys.stderr)
            continue

        plugin_name = plugin[1] or f"Plugin {plugin_id}"
        room_count = query("SELECT COUNT(*) as count FROM room_plugins WHERE plugin = :plugin_id", plugin_id=plugin_id).first()[0]
        query("DELETE FROM plugins WHERE id = :id", id=plugin_id)

        if room_count:
            print(f"Uninstalled plugin '{plugin_name}' (removed from {room_count} room(s))")
        else:
            print(f"Uninstalled plugin '{plugin_name}'")

elif typing.cast(Optional[List[str]], args.install_plugins) is not None:
    install_plugins = typing.cast(List[str], args.install_plugins)
    plugins: List[sogs.plugin.InstallPluginMetadata] = scan_available_plugins()

    if len(install_plugins) == 0:
        print_plugin_listings(plugins)
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
        INSTALL_ID_SECTION = f"plugin_{plugin.install_id}"
        if not ini_parser.has_section(INSTALL_ID_SECTION):
            ini_parser.add_section(INSTALL_ID_SECTION)

        # Create data directory with verification
        try:
            plugin.data_dir.mkdir(parents=True, exist_ok=True)
        except (OSError, PermissionError) as e:
            print(f"Error: Failed to create plugin data directory {plugin.data_dir}: {e}", file=sys.stderr)
            sys.exit(1)

        # Verify directory exists and is writable
        if not plugin.data_dir.exists():
            print(f"Error: Plugin data directory {plugin.data_dir} does not exist after creation attempt", file=sys.stderr)
            sys.exit(1)

        if not os.access(plugin.data_dir, os.W_OK):
            print(f"Error: Plugin data directory {plugin.data_dir} is not writable", file=sys.stderr)
            sys.exit(1)

        # Load or generate Ed25519 key
        print(f"Installing {plugin.name}...")
        ed25519_key_bytes: bytes = b''
        if plugin.ed_key_path.exists():
            print(f"  Loading Ed25519 key from {plugin.ed_key_path} ...", end=" ", flush=True)
            ed25519_key_bytes = plugin.ed_key_path.read_bytes()
            if len(ed25519_key_bytes) != nacl.bindings.crypto_sign_SECRETKEYBYTES and len(ed25519_key_bytes) != nacl.bindings.crypto_box_SEEDBYTES:
                print(f"\nError: Installation failed for {plugin.name}, key file was not a valid secret key, expected {nacl.bindings.crypto_sign_SECRETKEYBYTES}b or {nacl.bindings.crypto_sign_SEEDBYTES}b received: {len(ed25519_key_bytes)}b", file=sys.stderr)
                continue
        else:
            print(f"  Generating Ed25519 key to {plugin.ed_key_path} ...", end=" ", flush=True)
            (_, ed25519_key_bytes) = nacl.bindings.crypto_sign_keypair()

        ed25519_key = nacl.signing.SigningKey(ed25519_key_bytes[:nacl.bindings.crypto_sign_SEEDBYTES])
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
        ini_bak_path = plugin.desired_ini_path.with_suffix('.ini.bak')
        key_bak_path = plugin.ed_key_path.with_suffix('.bak')
        try:
            with tempfile.NamedTemporaryFile() as tmp_ini_file:
                with tempfile.NamedTemporaryFile() as tmp_ed25519_key:
                    from .db import query
                    from . import config

                    print("  Registering plugin in SOGS instance ...", flush=True)

                    is_global    = args.install_plugin_global    == 'true' if args.install_plugin_global    is not None else False
                    is_approver  = args.install_plugin_approver  == 'true' if args.install_plugin_approver  is not None else False
                    is_required  = args.install_plugin_required  == 'true' if args.install_plugin_required  is not None else False
                    is_subscribe = args.install_plugin_subscribe == 'true' if args.install_plugin_subscribe is not None else False

                    # Check if plugin already existed (for reconfiguration message)
                    fields: List[Tuple[str, str]] = []
                    new_ed_key = bytes(ed25519_key.verify_key)
                    new_x_key = bytes(x25519_pkey)

                    existing = query("SELECT name, ed_key, x_key, global, approver, required, subscribe FROM plugins WHERE install_id = :install_id", install_id=plugin.install_id).first()

                    if existing:
                        old_ed_key = existing[1]
                        old_x_key = existing[2]

                        if old_ed_key == new_ed_key:
                            fields.append(("Ed25519 Pubkey", f"{new_ed_key.hex()} (unchanged)"))
                        else:
                            fields.append(("Ed25519 Pubkey", f"{old_ed_key.hex()} -> {new_ed_key.hex()}"))

                        if old_x_key == new_x_key:
                            fields.append(("X25519 Pubkey", f"{new_x_key.hex()} (unchanged)"))
                        else:
                            fields.append(("X25519 Pubkey", f"{old_x_key.hex()} -> {new_x_key.hex()}"))

                        old_name       = typing.cast(str, existing[0]) or ""
                        old_global     = bool(existing[3])
                        old_approver   = bool(existing[4])
                        old_required   = bool(existing[5])
                        old_subscribe  = bool(existing[6])

                        fields.append(("Install ID", f"'{plugin.install_id}' (unchanged)"))
                        fields.append(("Name", f"'{old_name}' (unchanged)"))
                        if old_global != is_global:
                            fields.append(("Global", f"{old_global} -> {is_global}"))
                        else:
                            fields.append(("Global", f"{is_global} (unchanged)"))
                        if old_approver != is_approver:
                            fields.append(("Approver", f"{old_approver} -> {is_approver}"))
                        else:
                            fields.append(("Approver", f"{is_approver} (unchanged)"))
                        if old_required != is_required:
                            fields.append(("Required", f"{old_required} -> {is_required}"))
                        else:
                            fields.append(("Required", f"{is_required} (unchanged)"))
                        if old_subscribe != is_subscribe:
                            fields.append(("Subscribe", f"{old_subscribe} -> {is_subscribe}"))
                        else:
                            fields.append(("Subscribe", f"{is_subscribe} (unchanged)"))
                    else:
                        fields.append(("Install ID", f"'{plugin.install_id}'"))
                        fields.append(("Name", f"'{plugin.name}'"))
                        fields.append(("Global", f"{is_global}"))
                        fields.append(("Approver", f"{is_approver}"))
                        fields.append(("Required", f"{is_required}"))
                        fields.append(("Subscribe", f"{is_subscribe}"))

                    # Insert/update DB with ON CONFLICT (upsert)
                    update_fields = [
                        "ed_key = EXCLUDED.ed_key",
                        "x_key = EXCLUDED.x_key",
                        "name = EXCLUDED.name",
                    ]
                    if args.install_plugin_global is not None:
                        update_fields.append("global = EXCLUDED.global")
                    if args.install_plugin_approver is not None:
                        update_fields.append("approver = EXCLUDED.approver")
                    if args.install_plugin_required is not None:
                        update_fields.append("required = EXCLUDED.required")
                    if args.install_plugin_subscribe is not None:
                        update_fields.append("subscribe = EXCLUDED.subscribe")

                    query(f"""INSERT INTO plugins (install_id, name, ed_key, x_key, global, approver, required, subscribe)
                             VALUES (:install_id, :name, :ed_key, :x_key, :is_global, :is_approver, :is_required, :is_subscribe)
                             ON CONFLICT(install_id) DO UPDATE SET {', '.join(update_fields)}""",
                        install_id=plugin.install_id,
                        name=plugin.name,
                        ed_key=bytes(ed25519_key.verify_key),
                        x_key=bytes(x25519_pkey),
                        is_global=is_global,
                        is_approver=is_approver,
                        is_required=is_required,
                        is_subscribe=is_subscribe)

                    from sogs.utils import pretty_format_key_value_list
                    print("  Plugin installed:\n    " + "\n    ".join(pretty_format_key_value_list(fields)))

                    # Write the files we care about.
                    import io
                    ini_buffer = io.StringIO()
                    _          = ini_parser.write(ini_buffer)

                    # Save .ini file and ed25519 keypair to temporary location
                    _ = tmp_ed25519_key.write(ed25519_key_bytes)
                    _ = tmp_ini_file.write(ini_buffer.getvalue().encode())
                    tmp_ed25519_key.flush()
                    tmp_ini_file.flush()

                    # Move the old files, if they exist, this doubles as a permission check that we
                    # can indeed replace the file. If these fail, an exception is raised
                    if plugin.ed_key_path.exists():
                        _ = shutil.move(src=plugin.ed_key_path, dst=key_bak_path)

                    if plugin.desired_ini_path.exists():
                        _ = shutil.move(src=plugin.desired_ini_path, dst=ini_bak_path)

                    # Now move the files into place
                    _ = shutil.copy(src=tmp_ini_file.name, dst=plugin.desired_ini_path)
                    _ = shutil.copy(src=tmp_ed25519_key.name, dst=plugin.ed_key_path)
        except Exception as e:
            print(f"  Error: Installation failed for {plugin.name}, writing installation to disk did not succeed: {e}", file=sys.stderr)
            try:
                if ini_bak_path.exists():
                    _ = shutil.move(ini_bak_path, plugin.desired_ini_path)
                if key_bak_path.exists():
                    _ = shutil.move(key_bak_path, plugin.ed_key_path)
                print("  Installation reverted", file=sys.stderr)
            except Exception as rollback_e:
                print(f"CRITICAL: Failed to roll back file system changes: {rollback_e}", file=sys.stderr)
                print("Please restore the .bak files ({ini_bak_path} and {key_bak_path}) manually by removing the .bak suffix from them", file=sys.stderr)
else:
    print("Error: no action given", file=sys.stderr)
    ap.print_usage()
    sys.exit(1)
