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
    mule = sogs.plugins.emoji_captcha
    env  = PLUGIN_SOGS_FILTER_INI_PATH=<path/to/plugin/config.ini>

  Note that the plugin can be parameterized via the following methods:

    - Pass the `--plugin_sogs_filter_ini_path` flag to the python invocation
    - Set the PLUGIN_SOGS_FILTER_INI_PATH environment variable
    - Otherwise expects "sogs_filter.ini" in the current working directory if omitted

  Start the plugin via UWSGI or directly and after it has initialised the plugin will generate a
  Ed25519 keypair and output this information on startup, e.g.:

    [SOGS FILTER] Plugin initialised:
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

  Example filter reply configuration:

    [plugin_sogs_filter.room.*.reply.*]
    reply_format = Please keep it clean in {room_name}!
    reply_format = Warning: Inappropriate content detected!
    profile_name = Filter Bot
    public       = false

    [plugin_sogs_filter.room.*.reply.profanity]
    reply_format = No profanity allowed!

    [plugin_sogs_filter.room.myroom.reply.alphabet]
    reply_format = Only Latin characters are supported here.

Architecture:
  - Integrates with SOGS as a plugin via the Plugin base class
  - Overrides filter() to intercept and validate all room messages
  - Uses better_profanity library for profanity detection
  - Uses regex patterns for alphabet/script detection
  - Supports hierarchical reply configuration (global -> room -> filter type -> language)
  - Maintains filter state per-room with settings inheritance from global config

Config file (.ini):
  Configure how the Emoji CAPTCHA plugin's behaviour and how it connects to the SOGS server by
  adding the following fields into the .ini file. This can be in your SOGS .ini file or a separate
  .ini file if you wish.

  This plugin has top-level settings to be configured in `[plugin_sogs_filter]`. Additionally you
  can configure the filter specifically for a room and what the plugin should reply when a message is
  filtered.

    [plugin_sogs_filter.room.<token>]
    ...

    [plugin_sogs_filter.room.<token>.reply.<category>]
    ...

  See the example as follows:

```ini
[plugin]
; sogs_address    = tcp://127.0.0.1:22028
; sogs_pubkey_hex = xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
;

[plugin_sogs_filter]
; name             = SOGS Filter Plugin
; key_file         = plugin_sogs_filter_ed25519

; If true, also filter moderator messages or otherwise moderator messages are always accepted
; filter_mods      = false

[plugin_sogs_filter.room.*]
; Enable profanity detection of messages posted into the room specified by <token>
; profanity        = false

; If true, silently reject messages that trigger the profanity filter, if false, reply with warning
; profanity_silent = false

; Extra alphabets to filter, each extra filter should be specified one per line
; alphabets        = spanish
; alphabets        = custom_language_filter

; If true, silently reject alphabet violations that trigger one of the alphabet filters, if false
; reply with warning
; alphabet_silent  = false

[plugin_sogs_filter.room.*.reply.*]
; Reply message (can specify multiple times for random selection)
; reply_format     = Hey {profile_name}! No swearing in {room_name}.

; Display name that the reply sent on a filtered message will have
; profile_name     = SOGS

; If true, reply publicly; if false, whisper to the user that triggered the filter
; public           = false
```

  The category in reply sections can be one of the following reserved values:
    *          - Universal/default replies (lowest precedence)
    profanity  - Profanity-specific replies
    alphabet   - General alphabet filter replies

  Or any arbitrary category with a custom filter
    <category> - Arbitrary category-specific replies (e.g., persian, arabic, cyrillic, my_custom_filter)

  The reply format can access the following values in the message replied to the user:
    {profile_name}  - User's display name or Session ID
    {profile_at}    - @mention of the user
    {room_name}     - Name of the room
    {room_token}    - Token of the room
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

from collections import OrderedDict
from typing import Optional, Dict, List, Tuple, Set, Union
from sogs.plugin import Plugin, ReplySettings, FilterResponse
from sogs.model.post import Post

class FilterType(enum.Enum):
    Profanity = 0
    Alphabet  = 1

@dataclasses.dataclass
class Filter:
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

    filter_mods:  bool                                  = False
    rooms:        Dict[sogs.types.RoomTokenStr, Filter] = dataclasses.field(default_factory=dict)

    # NOTE: Character ranges for different alphabet filters.
    # This is ordered because some are subsets of each other (e.g. persian is a subset of the
    # arabic character range). We check more specific patterns first before falling back to
    # broader categories.
    alphabet_filter_patterns: List[Tuple[str, re.Pattern]] = dataclasses.field(default_factory=lambda: [
        ('persian',  re.compile(r'[\u0621-\u0628\u062a-\u063a\u0641-\u0642\u0644-\u0648\u064e-\u0651\u0655\u067e\u0686\u0698\u06a9\u06af\u06be\u06cc]')),
        ('arabic',   re.compile(r'[\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff\ufb50-\ufdff\ufe70-\ufefe]')),
        ('cyrillic', re.compile(r'[\u0400-\u04ff]')),
        ('debug',    re.compile(r'debug alphabet test')),
    ])

    def __post_init__(self):
        # Legacy path: import deprecated filtering settings from config.py
        if 1:
            import sogs.config
            self.filter_mods = sogs.config.FILTER_MODS
            legacy_filter    = Filter(profanity        = sogs.config.PROFANITY_FILTER,
                                      profanity_silent = sogs.config.PROFANITY_SILENT,
                                      alphabets        = sogs.config.ALPHABET_FILTERS,
                                      alphabet_silent  = sogs.config.ALPHABET_SILENT)

            # FILTER_SETTINGS is a hash table that maps the room->filter category->reply settings.
            # We migrate those filter categories into our self.rooms hash table. These rooms inherit
            # the filter options specified by the globally defined `legacy_filter`.
            #
            # Example:
            #
            # FILTER_SETTINGS: Dict[RoomTokenStr, Dict[FilterCategoryStr, Dict[str, Union[List[str], str, bool, None]]]] = {
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
            # Where filter category is ('*', 'profanity', 'alphabet', or language like 'persian')
            FILTER_SETTINGS = typing.cast(Dict[str, Dict[str, Dict[str, Union[List[str], str, bool, None]]]], sogs.config.FILTER_SETTINGS)
            for room in FILTER_SETTINGS:
                self.rooms[room] = copy.copy(legacy_filter)
                for room_category in FILTER_SETTINGS[room]:
                    reply_src_dict: Dict[str, Union[List[str], str, bool, None]] = FILTER_SETTINGS[room][room_category]
                    reply_dest:     Optional[ReplySettings]                      = None
                    if room_category == '*':
                        self.rooms[room].universal_reply = ReplySettings()
                        reply_dest                       = self.rooms[room].universal_reply
                    elif room_category == 'profanity':
                        self.rooms[room].profanity_reply = ReplySettings()
                        reply_dest                       = self.rooms[room].profanity_reply
                    elif room_category == 'alphabet':
                        self.rooms[room].alphabet_reply = ReplySettings()
                        reply_dest                              = self.rooms[room].alphabet_reply
                    else:
                        self.rooms[room].alphabet_other_replies[room_category] = ReplySettings()
                        reply_dest                                             = self.rooms[room].alphabet_other_replies[room_category]

                    assert reply_dest
                    reply_dest.public        = typing.cast(Union[bool, None], reply_src_dict.get('public',       None))
                    reply_dest.profile_name  = typing.cast(Union[str,  None], reply_src_dict.get('profile_name', None))
                    reply_dest.reply_formats = typing.cast(List[str],         reply_src_dict.get('reply_formats', []))

            # ROOM_OVERRIDES is a hash table that maps room to the various settings which are
            # different from the legacy variable FILTER_SETTINGS. We migrate these values into the
            # rooms and update this plugin's `Filter` class accordingly.
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
                self.rooms[room_token] = copy.copy(legacy_filter)
                if 'profanity_filter' in ROOM_OVERRIDES[room_token]:
                    self.rooms[room_token].profanity = typing.cast(bool, ROOM_OVERRIDES[room_token]['profanity_filter'])
                if 'profanity_silent' in ROOM_OVERRIDES[room_token]:
                    self.rooms[room_token].profanity_silent = typing.cast(bool, ROOM_OVERRIDES[room_token]['profanity_silent'])
                if 'alphabet_filters' in ROOM_OVERRIDES[room_token]:
                    self.rooms[room_token].alphabets = typing.cast(Set[str], ROOM_OVERRIDES[room_token]['alphabet_filters'])
                if 'alphabet_silent' in ROOM_OVERRIDES[room_token]:
                    self.rooms[room_token].alphabet_silent = typing.cast(bool, ROOM_OVERRIDES[room_token]['alphabet_silent'])

        if '*' not in self.rooms:
            self.rooms['*'] = Filter()

    def get_reply_settings(self, room_token: sogs.types.RoomTokenStr, filter_type: FilterType = FilterType.Profanity, filter_lang: Optional[str] = None) -> Optional[ReplySettings]:
        # Precedences from least to most specific so that we load values from least specific first
        # then overwrite them if we find a value in a more specific section
        room_precedence: List[sogs.types.RoomTokenStr] = ['*', room_token]
        result:          ReplySettings                 = ReplySettings()
        for r in room_precedence:
            room_filter: Optional[Filter] = self.rooms.get(r)
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

    @typing_extensions.override
    def filter(self, req: sogs.types.RoomAddPostRequest) -> FilterResponse:
        result = FilterResponse.Accept
        if req.is_mod and not self.filter_mods:
            return result

        room_token: str = req.room_token.decode('utf-8')
        print(f"filtering for room_token: {room_token}")

        # Retrieve the filter for this room
        room_filter = Filter()
        if room_token in self.rooms:
            room_filter = self.rooms[room_token]
        elif '*' in self.rooms:
            room_filter = self.rooms['*']

        if not room_filter.profanity and len(room_filter.alphabets) == 0:
            return result

        # Decode the message
        msg = Post(raw=req.message_data)

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
                print(f"replying with format: {reply_settings}")
                _ = self.reply(
                    room_name       = req.room_name,
                    room_token      = req.room_token,
                    user_session_id = req.session_id,
                    username        = msg.username,
                    reply_settings  = reply_settings)
            result = FilterResponse.Silent if room_filter.profanity_silent else FilterResponse.Reject

        # Filter for alphabets
        if result == FilterResponse.Accept and len(room_filter.alphabets):
            for lang, pattern in self.alphabet_filter_patterns:
                # Lookup each regex pattern for this language and verify the message
                if lang not in room_filter.alphabets:
                    continue
                if not pattern.search(msg.text):
                    continue

                # Filter the language string
                reply_settings = self.get_reply_settings(room_token, filter_type=FilterType.Alphabet, filter_lang=lang)
                if reply_settings:
                    print(f"replying with format: {reply_settings}")
                    _ = self.reply(room_name       = req.room_name,
                                   room_token      = req.room_token,
                                   user_session_id = req.session_id,
                                   username        = typing.cast(str, msg.username),
                                   reply_settings  = reply_settings,)
                result = FilterResponse.Silent if room_filter.alphabet_silent else FilterResponse.Reject
                break

        return result

def entry_point():
    import argparse

    # Custom dict that accumulates duplicate keys into a list
    class MultiValueDict(OrderedDict):
        """Dictionary that accumulates duplicate 'reply_format' and 'alphabets' keys into lists."""
        _accumulate_keys = {'reply_format', 'alphabets'}
        
        def __setitem__(self, key, value):
            if key in self._accumulate_keys and key in self:
                if not isinstance(self[key], list):
                    self[key] = [self[key]]
                self[key].append(value)
            else:
                super().__setitem__(key, value)

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

    # Load custom INI with support for multiple reply_format keys
    sogs.plugin.log.info(f"Loading SOGS Filter plugin config from {ini_file}")

    try:
        # Use custom parser that accumulates duplicate reply_format keys
        ini_parser = configparser.ConfigParser(dict_type=MultiValueDict, strict=False)
        ini_parser.read(ini_file)

        # Get plugin config from [plugin] section (standard fields)
        sogs_address = ini_parser.get('plugin', 'sogs_address', fallback='tcp://127.0.0.1:22028')
        sogs_pubkey_hex = ini_parser.get('plugin', 'sogs_pubkey_hex', fallback='')
        sogs_pubkey = bytes.fromhex(sogs_pubkey_hex) if sogs_pubkey_hex else b''

        # Get filter-specific config from [plugin_sogs_filter] section
        key_file = ini_parser.get('plugin_sogs_filter', 'key_file', fallback="plugin_sogs_filter_ed25519")
        display_name = ini_parser.get('plugin_sogs_filter', 'display_name', fallback="SOGS Filter Plugin")
        filter_mods = ini_parser.getboolean('plugin_sogs_filter', 'filter_mods', fallback=False)

        ed_privkey = sogs.plugin.Plugin.get_or_make_ed25519_privkey(key_file)

        # Parse room-specific settings from [plugin_sogs_filter.room.<token>] sections
        rooms: Dict[str, Filter] = {}

        for section in ini_parser.sections():
            if not section.startswith('plugin_sogs_filter.'):
                continue

            parts = section.split('.')

            # FATAL: Wrong prefix structure
            if len(parts) < 3:
                raise ValueError(f"Section '{section}' has too few components. Expected format: 'plugin_sogs_filter.room.<token>'")

            if parts[0] != 'plugin_sogs_filter':
                continue  # Not our plugin

            if parts[1] != 'room':
                raise ValueError(f"Invalid section '{section}': expected 'room' after plugin prefix, got '{parts[1]}'")

            # FATAL: Missing room token
            if len(parts) < 4:
                raise ValueError(f"Section '{section}' is missing room token. Expected format: 'plugin_sogs_filter.room.<token>'")

            room_token = parts[3]
            if not room_token:
                raise ValueError(f"Section '{section}' has empty room token")

            # Base room settings: plugin_sogs_filter.room.<token>
            if len(parts) == 4:
                if room_token not in rooms:
                    rooms[room_token] = Filter()

                # Parse boolean flags
                if ini_parser.has_option(section, 'profanity'):
                    rooms[room_token].profanity = ini_parser.getboolean(section, 'profanity')
                if ini_parser.has_option(section, 'profanity_silent'):
                    rooms[room_token].profanity_silent = ini_parser.getboolean(section, 'profanity_silent')
                if ini_parser.has_option(section, 'alphabet_silent'):
                    rooms[room_token].alphabet_silent = ini_parser.getboolean(section, 'alphabet_silent')
                if ini_parser.has_option(section, 'alphabets'):
                    alphabets_value = ini_parser.get(section, 'alphabets')
                    # Must use multiple keys pattern - one alphabet per line
                    if isinstance(alphabets_value, list):
                        rooms[room_token].alphabets = {s.strip() for s in alphabets_value if s.strip()}
                    elif alphabets_value:
                        rooms[room_token].alphabets = {alphabets_value.strip()}

            # Reply settings: plugin_sogs_filter.room.<token>.reply.<category>
            elif len(parts) == 6:
                if parts[4] != 'reply':
                    raise ValueError(f"Invalid section '{section}': expected 'reply' in position 5, got '{parts[4]}'")

                category = parts[5]
                if not category:
                    raise ValueError(f"Section '{section}' has empty category. Expected format: 'plugin_sogs_filter.room.<token>.reply.<category>'")

                if room_token not in rooms:
                    rooms[room_token] = Filter()

                # Get accumulated reply formats (each reply_format key = one message)
                reply_formats: List[str] = []
                if ini_parser.has_option(section, 'reply_format'):
                    value = ini_parser.get(section, 'reply_format')
                    if isinstance(value, list):
                        reply_formats = value
                    elif value:
                        reply_formats = [value]

                # Skip if no reply formats defined (empty reply section is allowed)
                if not reply_formats:
                    continue

                profile_name = ini_parser.get(section, 'profile_name', fallback='SOGS')
                public = ini_parser.getboolean(section, 'public', fallback=False)

                reply_settings = ReplySettings(
                    reply_formats=reply_formats,
                    profile_name=profile_name,
                    public=public
                )

                # Assign to appropriate field based on category (any category is valid)
                if category == '*':
                    rooms[room_token].universal_reply = reply_settings
                elif category == 'profanity':
                    rooms[room_token].profanity_reply = reply_settings
                elif category == 'alphabet':
                    rooms[room_token].alphabet_reply = reply_settings
                else:
                    rooms[room_token].alphabet_other_replies[category] = reply_settings

            # FATAL: Wrong number of parts
            else:
                raise ValueError(f"Invalid section '{section}': unexpected structure. Expected 4 parts (room settings) or 6 parts (reply settings), got {len(parts)}")

        # Instantiate plugin
        plugin = SOGSFilterPlugin(
            sogs_address=sogs_address,
            sogs_pubkey=sogs_pubkey,
            ed_privkey=ed_privkey,
            display_name=display_name
        )
        plugin.filter_mods = filter_mods
        plugin.rooms.update(rooms)

        plugin.run()

    except Exception as e:
        sogs.plugin.log.error(f"Exception raised in plugin. Terminating:\n{e}")

if __name__ == "__main__":
    entry_point()
