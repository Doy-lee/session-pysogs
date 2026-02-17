"""SOGS Filter Plugin

  This plugin provides message filtering for profanity and non-Latin alphabets (Persian, Arabic,
  Cyrillic). When enabled, it can automatically reject messages or reply to users with warnings
  when filtered content is detected.

  The plugin supports per-room configuration overrides and can be configured to filter moderator
  messages or exempt them. Filter responses can be customized with different reply messages for
  different filter types (profanity vs alphabet violations).

Getting Started:
  Setup the .ini config (see the configuration section below for more details) with the desired
  parameters and then you can run the plugin standalone

    cd session-pysogs
    python3 -m sogs.plugins.sogs_filter --plugin_sogs_filter_ini_path <path/to/plugin/config.ini>

  Alternatively you can run the plugin alongside the SOGS server as a UWSGI mule. In your UWSGI .ini
  config file, add to the [uwsgi] section:

    [uwsgi]
    mule = sogs.plugins.sogs_filter
    env  = PLUGIN_SOGS_FILTER_INI_PATH=<path/to/plugin/config.ini>

  Note that the plugin can be parameterized via the following methods:

    - Pass the `--plugin_sogs_filter_ini_path` flag to the python invocation
    - Set the PLUGIN_SOGS_FILTER_INI_PATH environment variable
    - Otherwise expects "sogs_filter.ini" in the current working directory if omitted

  Start the plugin via UWSGI or directly and after it has loaded the plugin will generate a
  Ed25519 keypair and output this information on startup, e.g.:

    [SOGS FILTER] Plugin loaded:
      SOGS Address (Pubkey):      tcp://127.0.0.1:22028 (cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc)
      Display Name:               SOGS Filter Plugin
      Ed25519 Pubkey:             aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
      X25519 Pubkey:              bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
      Session Account (Blind-15): 15xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

  The Ed25519 public key must be registered to the SOGS instance to enable the plugin to establish
  a connection to the SOGS server, authenticate and consequently receive messages from SOGS to react
  to. The plugin can be registered by invoking on the SOGS instance:

    python3 -m sogs --add-plugin       <ed25519 pubkey hex 64 chars> \
                    --plugin-name      'SOGS Filter Plugin' \
                    --plugin-global    true \
                    --plugin-approver  true \
                    --plugin-required  true \
                    --plugin-subscribe true

Architecture:
  - Integrates with SOGS as a plugin via the Plugin base class
  - Overrides filter() to intercept and validate all room messages
  - Uses better_profanity library for profanity detection
  - Uses regex patterns for alphabet/script detection
  - Supports hierarchical reply configuration (global -> room -> filter type -> language)
  - Maintains filter state per-room with settings inheritance from global config

Config file (.ini):
  Configure how the SOGS Filter plugin's behaviour and how it connects to the SOGS server by
  adding the following fields into the .ini file. This can be in your SOGS .ini file or a separate
  .ini file if you wish.

  This plugin has top-level settings to be configured in `[plugin_sogs_filter]`. Additionally you
  can configure the filter specifically for a room and what the plugin should reply when a message is
  filtered by adding sections with the following patterns:

    [plugin_sogs_filter.room.<token>]
    ...

    [plugin_sogs_filter.room.<token>.reply.<filter_name>]
    ...

  The following example sets a reply message for various rooms and filter categories with a custom
  reply for profanities and a specific reply for `myroom` where messages trigger the
  `my_custom_alphabet` or the `alphabet` filter.

    [plugin_sogs_filter.room.*.reply.*] ; Reply message for all rooms for all messages that trigger any filter
    reply = Please keep it clean in \r!
     Warning: Inappropriate content detected!
    profile_name = Filter Bot
    public       = false

    [plugin_sogs_filter.room.*.reply.profanity] ; Reply message for all rooms that trigger a `profanity` filter (overrides the previous section)
    reply = No profanity allowed!

    [plugin_sogs_filter.room.myroom.reply.alphabet] ; Reply message for `myroom` that triggered an `alphabet` filter (overrides the previous sections)
    reply = Only Latin characters are supported here.

    [plugin_sogs_filter.room.myroom.reply.my_custom_alphabet] ; Reply message for `myroom` that triggered the `my_custom_alphabet` filter (overrides the previous sections)
    reply = Hey this message was disallowed

  See the example as follows:

```ini
[plugin]
; sogs_address    = tcp://127.0.0.1:22028
; sogs_pubkey_hex = xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
;

[plugin_sogs_filter]
; display_name     = SOGS Filter Plugin
; key_file         = plugin_sogs_filter_ed25519

; If true, also filter moderator messages or otherwise moderator messages are always accepted
; filter_mods      = false

[plugin_sogs_filter.alphabets]
; Define alphabet filter patterns as key-value pairs: filter_name = regex_pattern
; Order matters: more specific patterns should come before broader ones (e.g., persian before arabic)
; persian  = [\u0621-\u0628\u062a-\u063a\u0641-\u0642\u0644-\u0648\u064e-\u0651\u0655\u067e\u0686\u0698\u06a9\u06af\u06be\u06cc]
; arabic   = [\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff\ufb50-\ufdff\ufe70-\ufefe]
; cyrillic = [\u0400-\u04ff]
; debug    = debug alphabet test

[plugin_sogs_filter.room.*]
; Enable profanity detection of messages posted into the room specified by <token>
; profanity        = false

; If true, silently reject messages that trigger the profanity filter, if false, reply with warning
; profanity_silent = false

; Alphabets to filter (space-separated list of filter names defined in [plugin_sogs_filter.alphabets])
; alphabets        = persian arabic cyrillic

; If true, silently reject alphabet violations that trigger one of the alphabet filters, if false
; reply with warning
; alphabet_silent  = false

[plugin_sogs_filter.room.*.reply.*]
; Reply message. Multiple lines specify random reply options (one is chosen randomly).
; Use escape sequences for substitutions:
;   \@ - @mention of the user
;   \p - profile name in plain text
;   \r - room name
;   \t - room token
;   \n - line break within a single reply
;   \\ - literal backslash
; reply = Hey \p! No swearing in \r.
;  Watch your language, \@!
;  This is a family-friendly group.

; Display name that the reply sent on a filtered message will have
; profile_name     = SOGS

; If true, reply publicly; if false, whisper to the user that triggered the filter
; public           = false
```

  The filter name in reply sections can be one of the following reserved values:
    *          - Universal/default replies (lowest precedence)
    profanity  - Profanity-specific replies
    alphabet   - General alphabet filter replies

  Or any filter name defined in the [plugin_sogs_filter.alphabets] section.

  The reply value supports the following escape sequences:
    \\@  - @mention of the poster whose message was declined
    \\p  - profile name in plain text
    \\r  - name of the room
    \\t  - token of the room
    \\n  - a line break
    \\\\  - a literal \\ character
"""

import re
import typing
import typing_extensions
import sogs.types
import dataclasses
import enum
import copy
import sogs.plugin
import os
import configparser

from typing import Optional, Dict, List, Tuple, Set, Union
from sogs.plugin import Plugin, FilterResult
from sogs.model.post import Post

@dataclasses.dataclass
class ReplySettings:
    """Settings controlling how the plugin replies to a filtered message.
    Attributes:
        reply_formats: List of format strings where one is chosen at random to use as the reply. In
                       the reply, the following python placeholders are supported:
                       {profile_name}, {profile_at}, {room_name}, {room_token}.

                       e.g. reply_format_str = "Hey {profile_name}! No swearing in {room_name}."

        profile_name:  Display name for the reply
        public:        If True the reply is posted publicly; if False it is whispered to the user.
    """
    reply_formats: List[str]      = dataclasses.field(default_factory=list)
    profile_name:  Optional[str]  = 'SOGS'
    public:        Optional[bool] = False

    def load_from(self, other: "ReplySettings"):
        if len(other.reply_formats):
            self.reply_formats = other.reply_formats
        if other.profile_name:
            self.profile_name = other.profile_name
        if other.public:
            self.public = other.public

class FilterType(enum.Enum):
    Profanity = 0
    Alphabet  = 1

@dataclasses.dataclass
class RoomFilter:
    """Per-room filter configuration holding boolean flags and reply settings.

    Attributes:
        profanity:              Enable profanity detection for this room
        profanity_silent:       If True, silently reject profanity; if False, reply with warning
        alphabets:              Set of alphabet names to filter (e.g., {'persian', 'arabic'})
        alphabet_silent:        If True, silently reject alphabet violations; if False, reply
        universal_reply:        Default reply settings for any filter trigger (lowest precedence)
        profanity_reply:        Reply settings specifically for profanity violations
        alphabet_reply:         Reply settings for general alphabet filter triggers
        alphabet_other_replies: Language-specific reply settings (e.g., {'persian': ReplySettings()})
    """

    profanity:              bool                     = False
    profanity_silent:       bool                     = False
    alphabets:              Set[str]                 = dataclasses.field(default_factory=set)
    alphabet_silent:        bool                     = False
    universal_reply:        Optional[ReplySettings]  = None
    profanity_reply:        Optional[ReplySettings]  = None
    alphabet_reply:         Optional[ReplySettings]  = None
    alphabet_other_replies: Dict[str, ReplySettings] = dataclasses.field(default_factory=dict)

@dataclasses.dataclass
class SOGSFilterPlugin(Plugin):
    """SOGS message filter plugin for profanity and alphabet detection.

    Automatically loads configuration from SOGS global config and supports per-room overrides.

    Attributes:
        filter_mods:              If True, moderator messages are also filtered; if False, mods bypass
        rooms:                    Dictionary mapping room tokens to their Filter configuration.
                                  '*' key holds global defaults, specific room tokens hold overrides
        alphabet_filter_patterns: Ordered list of (language_name, regex_pattern) tuples for alphabet
                                  detection. Ordered so specific languages (e.g., persian) are checked
                                  before broader categories (e.g., arabic) that contain them
    """

    filter_mods:  bool                                      = False
    rooms:        Dict[sogs.types.RoomTokenStr, RoomFilter] = dataclasses.field(default_factory=dict)

    # NOTE: Character ranges for different alphabet filters loaded from the .ini file.
    # This is ordered because some are subsets of each other (e.g. persian is a subset of the
    # arabic character range). We check more specific patterns first before falling back to
    # broader categories. Configure filters in the [plugin_sogs_filter.filters] section.
    alphabet_patterns: List[Tuple[str, re.Pattern[str]]] = dataclasses.field(default_factory=list)

    def __post_init__(self):
        super().__post_init__()

        # NOTE: Create the default room
        if '*' not in self.rooms:
            self.rooms['*'] = RoomFilter()

        # Legacy path: import deprecated filtering settings from config.py into a temporary
        # dictionary. We generate the migration .ini from this temporary dictionary, then merge
        # it into self.rooms (which may already have settings from the new .ini format).
        if 1:
            import sogs.config
            legacy_filter_mods: bool = sogs.config.FILTER_MODS
            legacy_rooms: Dict[sogs.types.RoomTokenStr, RoomFilter] = {}

            legacy_filter = RoomFilter(profanity        = sogs.config.PROFANITY_FILTER,
                                       profanity_silent = sogs.config.PROFANITY_SILENT,
                                       alphabets        = sogs.config.ALPHABET_FILTERS,
                                       alphabet_silent  = sogs.config.ALPHABET_SILENT)

            # FILTER_SETTINGS is a hash table that maps the room_token->room_filter->reply settings.
            # We migrate those filter categories into our legacy_rooms hash table. These rooms inherit
            # the filter options specified by the globally defined `legacy_filter`.
            #
            # Example:
            #
            # FILTER_SETTINGS: Dict[RoomTokenStr, Dict[RoomFilter, Dict[str, Union[List[str], str, bool, None]]]] = {
            #   '*': {
            #       '*': {'reply': ['...'], 'profile_name': 'SOGS', 'public': False},
            #       'profanity': {'reply': ['...']},
            #   },
            #   'specific_room': {
            #       'alphabet': {'reply': ['...']},
            #       'persian': {'reply': ['...']},
            #   }
            # }
            #
            # Where room filter is ('*', 'profanity', 'alphabet', a custom filter defined by the user)
            FILTER_SETTINGS = typing.cast(Dict[str, Dict[str, Dict[str, Union[List[str], str, bool, None]]]], sogs.config.FILTER_SETTINGS)
            for room in FILTER_SETTINGS:
                legacy_rooms[room] = copy.copy(legacy_filter)
                for room_filter in FILTER_SETTINGS[room]:
                    reply_src_dict: Dict[str, Union[List[str], str, bool, None]] = FILTER_SETTINGS[room][room_filter]
                    reply_dest:     Optional[ReplySettings]                      = None
                    if room_filter == '*':
                        legacy_rooms[room].universal_reply = ReplySettings()
                        reply_dest                         = legacy_rooms[room].universal_reply
                    elif room_filter == 'profanity':
                        legacy_rooms[room].profanity_reply = ReplySettings()
                        reply_dest                         = legacy_rooms[room].profanity_reply
                    elif room_filter == 'alphabet':
                        legacy_rooms[room].alphabet_reply = ReplySettings()
                        reply_dest                        = legacy_rooms[room].alphabet_reply
                    else:
                        legacy_rooms[room].alphabet_other_replies[room_filter] = ReplySettings()
                        reply_dest                                             = legacy_rooms[room].alphabet_other_replies[room_filter]

                    assert reply_dest
                    reply_dest.public        = typing.cast(Union[bool, None], reply_src_dict.get('public',       None))
                    reply_dest.profile_name  = typing.cast(Union[str,  None], reply_src_dict.get('profile_name', None))
                    reply_dest.reply_formats = typing.cast(List[str],         reply_src_dict.get('reply', []))

            # ROOM_OVERRIDES is a hash table that maps room to the various settings which are
            # different from the legacy variable FILTER_SETTINGS. We migrate these values into the
            # legacy_rooms and update this plugin's `Filter` class accordingly.
            #
            # Example:
            #
            # ROOM_OVERRIDES: Dict[RoomTokenStr, Dict[str, Union[bool, Set[str]]]] = { # {'profanity_filter': bool, 'alphabet_filters': Set[str], ...}
            #   'room_token': {
            #     'profanity_filter': True,
            #     'alphabet_filters': {'persian'},
            #   }
            # }
            ROOM_OVERRIDES = typing.cast(Dict[str, Dict[str, Union[bool, Set[str]]]], sogs.config.ROOM_OVERRIDES)
            for room_token in ROOM_OVERRIDES:
                legacy_rooms[room_token] = copy.copy(legacy_filter)
                if 'profanity_filter' in ROOM_OVERRIDES[room_token]:
                    legacy_rooms[room_token].profanity = typing.cast(bool, ROOM_OVERRIDES[room_token]['profanity_filter'])
                if 'profanity_silent' in ROOM_OVERRIDES[room_token]:
                    legacy_rooms[room_token].profanity_silent = typing.cast(bool, ROOM_OVERRIDES[room_token]['profanity_silent'])
                if 'alphabet_filters' in ROOM_OVERRIDES[room_token]:
                    legacy_rooms[room_token].alphabets = typing.cast(Set[str], ROOM_OVERRIDES[room_token]['alphabet_filters'])
                if 'alphabet_silent' in ROOM_OVERRIDES[room_token]:
                    legacy_rooms[room_token].alphabet_silent = typing.cast(bool, ROOM_OVERRIDES[room_token]['alphabet_silent'])

            legacy_data_loaded = len(ROOM_OVERRIDES) > 0 or len(FILTER_SETTINGS) > 0

            if legacy_data_loaded:
                # Generate migration .ini from legacy settings before merging
                migration_ini = self.generate_migration_ini(legacy_filter_mods, legacy_rooms)
                migration_message = (
                    "\n"
                    "================================================================================\n"
                    "LEGACY FILTER SETTINGS DETECTED - MIGRATION RECOMMENDED\n"
                    "================================================================================\n"
                    "\n"
                    "Legacy filter settings ([room:*], [filter:*:*], [messages] filter options) were\n"
                    "detected and loaded. It is recommended to migrate to the new plugin configuration\n"
                    "format for better maintainability and future compatibility.\n"
                    "\n"
                    "To migrate:\n"
                    "  1. Copy the configuration below into the config file used for the plugin\n"
                    "     (sogs_filter.ini or PLUGIN_SOGS_FILTER_INI_PATH if it was set)\n"
                    "  2. Remove the legacy settings from your old config file:\n"
                    "     - [messages] section filter options (profanity_filter, alphabet_filters, etc.)\n"
                    "     - [room:<token>] sections\n"
                    "     - [filter:<type>:<room>] sections\n"
                    "\n"
                    "--- BEGIN MIGRATED CONFIGURATION ---\n"
                    f"{migration_ini}"
                    "--- END MIGRATED CONFIGURATION ---\n"
                    "\n"
                    "================================================================================"
                )
                sogs.plugin.log.warning(migration_message)

                # Merge legacy settings into self (legacy settings take lower precedence than new .ini)
                if legacy_filter_mods and not self.filter_mods:
                    self.filter_mods = legacy_filter_mods  # pyright: ignore[reportUnreachable]

                for room_token, legacy_room_filter in legacy_rooms.items():
                    if room_token not in self.rooms:
                        self.rooms[room_token] = legacy_room_filter
                    # If room exists in new config, the new config takes precedence (no merge)


        # NOTE: Print some startup diagnostics
        alphabet_desc: str = ""
        for it in self.alphabet_patterns:
            if len(alphabet_desc) > 100:
                alphabet_desc += ".."
                break
            if len(alphabet_desc):
                alphabet_desc += ", "
            alphabet_desc += it[0]

        room_desc: str = ""
        for it in self.rooms:
            if len(room_desc) > 100:
                room_desc += ".."
                break
            if len(room_desc):
                room_desc += ", "
            room_desc += it

        desc_lines: List[Tuple[str, str]] = self.describe_config()
        desc_lines.extend([
            ("Filter Mods", f"{self.filter_mods}"),
            ("Alphabets",   f"({len(self.alphabet_patterns)}) [{alphabet_desc}]"),
            ("Rooms",       f"({len(self.rooms)}) [{room_desc}]"),
        ])

        import sogs.utils
        log_line: str = "Plugin loaded:\n  " + "\n  ".join(sogs.utils.pretty_format_key_value_list(desc_lines))
        sogs.plugin.log.info(log_line)

    def get_reply_settings(self, room_token: sogs.types.RoomTokenStr, filter_type: FilterType = FilterType.Profanity, filter_lang: Optional[str] = None) -> Optional[ReplySettings]:
        # Precedences from least to most specific so that we load values from least specific first
        # then overwrite them if we find a value in a more specific section
        room_precedence: List[sogs.types.RoomTokenStr] = ['*', room_token]
        result:          ReplySettings                 = ReplySettings()
        for r in room_precedence:
            room_filter: Optional[RoomFilter] = self.rooms.get(r)
            if room_filter is None:
                continue

            # Use room universal settings
            if room_filter.universal_reply:
                result.load_from(room_filter.universal_reply)

            # Or use the settings for the specified filter type
            if filter_type == FilterType.Profanity:
                if room_filter.profanity_reply:
                    result.load_from(room_filter.profanity_reply)

            if filter_type == FilterType.Alphabet:
                if room_filter.alphabet_reply:
                    result.load_from(room_filter.alphabet_reply)

            # Or use the language filters if it was specified
            if filter_lang:
                if filter_lang in room_filter.alphabet_other_replies:
                    lang_filter: ReplySettings = room_filter.alphabet_other_replies[filter_lang]
                    result.load_from(lang_filter)

        if len(result.reply_formats) == 0:
            return None

        return result

    def generate_migration_ini(self, legacy_filter_mods: bool, legacy_rooms: Dict[sogs.types.RoomTokenStr, RoomFilter]) -> str:
        """Generate a .ini file string with the migrated settings from legacy config.

        This converts the legacy data structures (from FILTER_SETTINGS and ROOM_OVERRIDES)
        into the new [plugin_sogs_filter.*] .ini format.

        Args:
            legacy_filter_mods: The legacy filter_mods setting value.
            legacy_rooms: Dictionary of room tokens to RoomFilter configurations loaded
                          from legacy settings.

        Returns:
            A string containing the complete .ini file content that can be copied
            into the user's plugin configuration file.
        """
        lines: List[str] = []

        # Header comment
        lines.append("; =============================================================================")
        lines.append("; SOGS Filter Plugin - Migrated Configuration")
        lines.append("; =============================================================================")
        lines.append("; This configuration was auto-generated from legacy filter settings.")
        lines.append("; Copy this content into the file specified by PLUGIN_SOGS_FILTER_INI_PATH")
        lines.append("; environment variable (as per the plugin documentation).")
        lines.append(";")
        lines.append("; After migrating, remove the following legacy sections from your old config:")
        lines.append(";   - [messages] filter settings (profanity_filter, alphabet_filters, etc.)")
        lines.append(";   - [room:<token>] sections")
        lines.append(";   - [filter:<type>:<room>] sections")
        lines.append("; =============================================================================")
        lines.append("")

        # [plugin_sogs_filter] section
        lines.append("[plugin_sogs_filter]")
        lines.append(f"filter_mods = {str(legacy_filter_mods).lower()}")
        lines.append("")

        # [plugin_sogs_filter.alphabets] section - commented out with directive
        lines.append("[plugin_sogs_filter.alphabets]")
        lines.append("; Define your alphabet filter patterns below as: filter_name = regex_pattern")
        lines.append("; You must manually add the regex patterns for each alphabet filter you wish to use.")
        lines.append("; Order matters: more specific patterns should come before broader ones.")
        lines.append("; Example patterns:")
        lines.append("; persian  = [\\u0621-\\u0628\\u062a-\\u063a\\u0641-\\u0642\\u0644-\\u0648\\u064e-\\u0651\\u0655\\u067e\\u0686\\u0698\\u06a9\\u06af\\u06be\\u06cc]")
        lines.append("; arabic   = [\\u0600-\\u06ff\\u0750-\\u077f\\u08a0-\\u08ff\\ufb50-\\ufdff\\ufe70-\\ufefe]")
        lines.append("; cyrillic = [\\u0400-\\u04ff]")
        lines.append("")

        # Helper function to generate reply settings section
        def write_reply_settings(section_name: str, reply_settings: ReplySettings) -> None:
            lines.append(f"[{section_name}]")
            if reply_settings.reply_formats:
                # Convert each reply format back to .ini escape sequences
                # Multiple lines in .ini = multiple random reply options
                for i, fmt in enumerate(reply_settings.reply_formats):
                    if i == 0:
                        lines.append(f"reply = {fmt}")
                    else:
                        # Continuation lines (indented)
                        lines.append(f" {fmt}")
            if reply_settings.profile_name and reply_settings.profile_name != 'SOGS':
                lines.append(f"profile_name = {reply_settings.profile_name}")
            if reply_settings.public:
                lines.append(f"public = true")
            lines.append("")

        # Generate room sections
        for room_token, room_filter in legacy_rooms.items():
            # [plugin_sogs_filter.room.<token>] section
            lines.append(f"[plugin_sogs_filter.room.{room_token}]")
            lines.append(f"profanity = {str(room_filter.profanity).lower()}")
            if room_filter.profanity_silent:
                lines.append(f"profanity_silent = {str(room_filter.profanity_silent).lower()}")
            if room_filter.alphabets:
                lines.append(f"alphabets = {' '.join(sorted(room_filter.alphabets))}")
            if room_filter.alphabet_silent:
                lines.append(f"alphabet_silent = {str(room_filter.alphabet_silent).lower()}")
            lines.append("")

            # Reply settings sections
            if room_filter.universal_reply and room_filter.universal_reply.reply_formats:
                write_reply_settings(f"plugin_sogs_filter.room.{room_token}.reply.*", room_filter.universal_reply)

            if room_filter.profanity_reply and room_filter.profanity_reply.reply_formats:
                write_reply_settings(f"plugin_sogs_filter.room.{room_token}.reply.profanity", room_filter.profanity_reply)

            if room_filter.alphabet_reply and room_filter.alphabet_reply.reply_formats:
                write_reply_settings(f"plugin_sogs_filter.room.{room_token}.reply.alphabet", room_filter.alphabet_reply)

            for lang_name, lang_reply in room_filter.alphabet_other_replies.items():
                if lang_reply.reply_formats:
                    write_reply_settings(f"plugin_sogs_filter.room.{room_token}.reply.{lang_name}", lang_reply)

        return '\n'.join(lines)

    @typing_extensions.override
    def filter(self, req: sogs.types.RoomAddPostRequest) -> FilterResult:
        if req.is_mod and not self.filter_mods:
            return FilterResult.accept()

        room_token: str = req.room_token.decode('utf-8')
        print(f"filtering for room_token: {room_token}")

        # Retrieve the filter for this room
        room_filter = RoomFilter()
        if room_token in self.rooms:
            room_filter = self.rooms[room_token]
        elif '*' in self.rooms:
            room_filter = self.rooms['*']

        if not room_filter.profanity and len(room_filter.alphabets) == 0:
            return FilterResult.accept()

        # Decode the message
        msg = Post(raw=req.message_data)

        def reply(plugin: Plugin, req: sogs.types.RoomAddPostRequest, username: Optional[str], reply_settings: ReplySettings,):
            from random import choice
            rf = choice(reply_settings.reply_formats)

            print(f"replying with format: {reply_settings}")
            session_id_hex: str = req.session_id.hex()
            body:           str = rf.format(profile_name = session_id_hex if username is None else username,
                                            profile_at   = f"@{session_id_hex}",
                                            room_name    = req.room_name,
                                            room_token   = req.room_token)
            _ = plugin.post_message(room_token=req.room_token, body=body, whisper_to=None if reply_settings.public else req.user_id)

        msg_username = typing.cast(Optional[str], msg.username)
        msg_text     = typing.cast(str, msg.text)

        # Filter for profanities
        def profanity_check(*args: str):
            import better_profanity
            for part in args:
                if better_profanity.profanity.contains_profanity(part):
                    return True
            return False

        if room_filter.profanity and profanity_check(typing.cast(str, msg.text), typing.cast(str, msg.username)):
            reply_settings = self.get_reply_settings(room_token, filter_type=FilterType.Profanity)
            if reply_settings:
                reply(self, req, msg_username, reply_settings)
            if room_filter.profanity_silent:
                return FilterResult.silent(reason="profanity")
            else:
                return FilterResult.reject(reason="profanity")

        # Filter for alphabets
        if len(room_filter.alphabets):
            for lang, pattern in self.alphabet_patterns:
                if lang not in room_filter.alphabets:
                    continue
                if not pattern.search(msg_text):
                    continue

                reply_settings = self.get_reply_settings(room_token, filter_type=FilterType.Alphabet, filter_lang=lang)
                if reply_settings:
                    reply(self, req, msg_username, reply_settings)

                if room_filter.alphabet_silent:
                    return FilterResult.silent(reason=f"alphabet: {lang}")
                else:
                    return FilterResult.reject(reason=f"alphabet: {lang}")

        return FilterResult.accept()

def process_reply_escapes(text: str) -> str:
    """Convert escape sequences to format strings or literals.

    Supported escape sequences (matching sogs.ini.filter-sample):
        \\@ - the profile name, in @tag form, of the poster whose message was declined
        \\p - the profile name in plain text
        \\r - the name of the room
        \\t - the token of the room
        \\n - a line break
        \\\\ - a literal \\ character
    """
    result = text.replace('\\\\', '\x00')  # Temp placeholder for \\
    result = result.replace('\\@', '{profile_at}')
    result = result.replace('\\p', '{profile_name}')
    result = result.replace('\\r', '{room_name}')
    result = result.replace('\\t', '{room_token}')
    result = result.replace('\\n', '\n')
    result = result.replace('\x00', '\\')  # Restore \\
    return result

def entry_point():
    import argparse

    # Argument parser
    parser = argparse.ArgumentParser(description='SOGS Filter')
    _ = parser.add_argument('--plugin_sogs_filter_ini_path', type=str,
                            default=os.environ.get('PLUGIN_SOGS_FILTER_INI_PATH', 'sogs_filter.ini'),
                            help='Path to the configuration .ini file (default: sogs_filter.ini or set PLUGIN_SOGS_FILTER_INI_PATH env)')
    args     = parser.parse_args()
    ini_file = typing.cast(str, args.plugin_sogs_filter_ini_path)

    # Setup logger
    sogs.plugin.log.name = '[SOGS FILTER]'
    sogs.plugin.log.addHandler(sogs.plugin.console_log_handler)

    # Load common INI configuration
    sogs.plugin.log.info(f"Loading SOGS Filter plugin config from {ini_file}")
    config: sogs.plugin.PluginConfigFromINI = sogs.plugin.Plugin.load_ini_from_path(ini_path=ini_file, default_display_name='SOGS Filter Plugin')
    if not config.success:
        return

    try:
        ini_parser = configparser.RawConfigParser(strict=False)
        _          = ini_parser.read(ini_file)

        # Get filter-specific config from [plugin_sogs_filter] section
        key_file    = ini_parser.get('plugin_sogs_filter',        'key_file',    fallback="plugin_sogs_filter_ed25519")
        filter_mods = ini_parser.getboolean('plugin_sogs_filter', 'filter_mods', fallback=False)
        ed_privkey  = sogs.plugin.Plugin.get_or_make_ed25519_privkey(key_file)

        # Parse room-specific settings from [plugin_sogs_filter.room.<token>] sections
        rooms: Dict[str, RoomFilter] = {}
        alphabet_patterns: List[Tuple[str, re.Pattern[str]]] = []
        for section in ini_parser.sections():
            if not section.startswith('plugin_sogs_filter.'):
                continue

            if section.startswith('plugin_sogs_filter.alphabets'):
                # Parse alphabet filter patterns from [plugin_sogs_filter.alphabets] section
                # Format: filter_name = regex_pattern (one per line)
                # Order matters: more specific patterns (e.g., persian) should come before broader ones (e.g., arabic)
                for filter_name in ini_parser.options(section):
                    pattern_str = ini_parser.get(section, filter_name)
                    try:
                        compiled_pattern = re.compile(pattern_str)
                        alphabet_patterns.append((filter_name, compiled_pattern))
                    except re.error as e:
                        raise ValueError(f"Invalid regex pattern for alphabet filter '{filter_name}': {pattern_str}\nError: {e}")
            else:
                # FATAL: Wrong prefix structure
                parts = section.split('.')
                if len(parts) < 3:
                    raise ValueError(f"Section '{section}' has too few components. Expected format: 'plugin_sogs_filter.room.<token>'")

                if parts[1] != 'room':
                    raise ValueError(f"Invalid section '{section}': expected 'room' or 'alphabets' after plugin prefix, got '{parts[1]}'")

                room_token: str = parts[2]
                if len(room_token) == 0:
                    raise ValueError(f"Section '{section}' has empty room token")

                # Base room settings: plugin_sogs_filter.room.<token>
                if len(parts) == 3:
                    if room_token not in rooms:
                        rooms[room_token] = RoomFilter()

                    # Parse boolean flags
                    if ini_parser.has_option(section, 'profanity'):
                        rooms[room_token].profanity = ini_parser.getboolean(section, 'profanity')
                    if ini_parser.has_option(section, 'profanity_silent'):
                        rooms[room_token].profanity_silent = ini_parser.getboolean(section, 'profanity_silent')
                    if ini_parser.has_option(section, 'alphabet_silent'):
                        rooms[room_token].alphabet_silent = ini_parser.getboolean(section, 'alphabet_silent')

                    if ini_parser.has_option(section, 'alphabets'):
                        alphabets_value = ini_parser.get(section, 'alphabets')
                        rooms[room_token].alphabets = {s.strip() for s in alphabets_value.split() if s.strip()}
                # Reply settings: plugin_sogs_filter.room.<token>.reply.<filter_name>
                else:
                    if len(parts) == 5:
                        if parts[3] != 'reply':
                            raise ValueError(f"Invalid section '{section}': expected 'reply' in position 3, got '{parts[3]}'")

                        filter_name: str = parts[4]
                        if not filter_name:
                            raise ValueError(f"Section '{section}' has an empty filter name. Expected format: 'plugin_sogs_filter.room.<token>.reply.<filter_name>'")

                        if room_token not in rooms:
                            rooms[room_token] = RoomFilter()

                        # Get reply formats: each line is a separate random reply option
                        # Process escape sequences: \@ \p \r \t \n \\
                        reply_formats: List[str] = []
                        if ini_parser.has_option(section, 'reply'):
                            value = ini_parser.get(section, 'reply')
                            reply_formats = [process_reply_escapes(s.strip()) for s in value.split('\n') if s.strip()]

                        # Skip if no reply formats defined (empty reply section is allowed)
                        if not reply_formats:
                            continue

                        profile_name = ini_parser.get(section, 'profile_name', fallback='SOGS')
                        public       = ini_parser.getboolean(section, 'public', fallback=False)

                        reply_settings = ReplySettings(reply_formats = reply_formats,
                                                       profile_name  = profile_name,
                                                       public        = public)

                        # Assign to appropriate field based on filter name (any category is valid)
                        if filter_name == '*':
                            rooms[room_token].universal_reply = reply_settings
                        elif filter_name == 'profanity':
                            rooms[room_token].profanity_reply = reply_settings
                        elif filter_name == 'alphabet':
                            rooms[room_token].alphabet_reply = reply_settings
                        else:
                            rooms[room_token].alphabet_other_replies[filter_name] = reply_settings

                    else:
                        raise ValueError(f"Section '{section}' has unexpected section name. Expected format: 'plugin_sogs_filter.room.<token>.reply.<filter_name>'")

        # Instantiate plugin
        plugin = SOGSFilterPlugin(sogs_address      = config.sogs_address,
                                  sogs_pubkey       = config.sogs_pubkey,
                                  ed_privkey        = ed_privkey,
                                  display_name      = config.display_name,
                                  filter_mods       = filter_mods,
                                  alphabet_patterns = alphabet_patterns,
                                  rooms             = rooms)
        plugin.run()

    except Exception as e:
        sogs.plugin.log.error(f"Exception raised in plugin. Terminating:\n{e}")

if __name__ == "__main__":
    entry_point()
