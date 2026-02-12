"""SOGS Content Filter Plugin

  This plugin provides message filtering for profanity and non-Latin alphabets (Persian, Arabic,
  Cyrillic). When enabled, it can automatically reject messages or reply to users with warnings
  when filtered content is detected.

  The plugin supports per-room configuration overrides and can be configured to filter moderator
  messages or exempt them. Filter responses can be customized with different reply messages for
  different filter types (profanity vs alphabet violations).

Getting Started:
  The plugin automatically loads configuration from the SOGS global config (sogs.ini). Enable
  filtering by setting the following in your sogs.ini:

    [messages]
    profanity_filter = true
    profanity_silent = false
    alphabet_filters = persian, arabic, cyrillic
    alphabet_silent = false
    filter_mods = false

  The plugin runs automatically when SOGS starts. No manual registration is required as it
  inherits settings from the legacy SOGS filtering configuration.

  Example filter reply configuration:

    [filter:*:*]
    reply = "Please keep it clean in {room_name}!"
    profile_name = Filter Bot
    public = false

    [filter:profanity:*]
    reply = "No profanity allowed!"

    [filter:alphabet:myroom]
    reply = "Only Latin characters are supported here."

Architecture:
  - Integrates with SOGS as a plugin via the Plugin base class
  - Overrides filter() to intercept and validate all room messages
  - Uses better_profanity library for profanity detection
  - Uses regex patterns for alphabet/script detection
  - Supports hierarchical reply configuration (global -> room -> filter type -> language)
  - Maintains filter state per-room with settings inheritance from global config

Config file (.ini):
  The plugin reads from the main SOGS configuration file. Key settings:

    profanity_filter: Enable/disable profanity checking (default: false)
    profanity_silent: If true, silently reject; if false, send reply (default: true)
    alphabet_filters: Comma-separated list of alphabets to filter (default: empty)
                      Options: persian, arabic, cyrillic
    alphabet_silent:  If true, silently reject alphabet violations (default: true)
    filter_mods:      If true, also filter moderator messages (default: false)

  Filter-specific replies are configured in [filter:<type>:<room>] sections:

    [filter:*:*]           # Global default replies
    [filter:profanity:*]   # Profanity-specific replies
    [filter:alphabet:*]    # General alphabet filter replies
    [filter:persian:*]     # Language-specific replies

  Reply configuration fields:
    reply:          Newline-separated list of reply messages (one chosen randomly)
    profile_name:   Display name for the reply bot (default: SOGS)
    public:         If true, reply publicly; if false, whisper (default: false)

  Reply format placeholders:
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
        alphabet_default_reply: Reply settings for general alphabet filter triggers
        alphabet_other_replies: Language-specific reply settings (e.g., {'persian': ReplySettings()})
    """

    profanity:              bool                     = False
    profanity_silent:       bool                     = False
    alphabets:              Set[str]                 = dataclasses.field(default_factory=set)
    alphabet_silent:        bool                     = False
    universal_reply:        Optional[ReplySettings]  = None
    profanity_reply:        Optional[ReplySettings]  = None
    alphabet_default_reply: Optional[ReplySettings]  = None
    alphabet_other_replies: Dict[str, ReplySettings] = dataclasses.field(default_factory=dict)

@dataclasses.dataclass
class SogsFilterPlugin(Plugin):
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
    rooms:        Dict[sogs.types.RoomTokenStr, Filter] = {}

    # NOTE: Character ranges for different alphabet filters.
    # This is ordered because some are subsets of each other (e.g. persian is a subset of the
    # arabic character range). We check more specific patterns first before falling back to
    # broader categories.
    alphabet_filter_patterns: List[Tuple[str, re.Pattern]] = [
        ('persian',  re.compile(r'[\u0621-\u0628\u062a-\u063a\u0641-\u0642\u0644-\u0648\u064e-\u0651\u0655\u067e\u0686\u0698\u06a9\u06af\u06be\u06cc]')),
        ('arabic',   re.compile(r'[\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff\ufb50-\ufdff\ufe70-\ufefe]')),
        ('cyrillic', re.compile(r'[\u0400-\u04ff]')),
        ('debug',    re.compile(r'debug alphabet test')),
    ]

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
                        self.rooms[room].alphabet_default_reply = ReplySettings()
                        reply_dest                              = self.rooms[room].alphabet_default_reply
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
                if room_filter.alphabet_default_reply:
                    result.load_from(room_filter.alphabet_default_reply)

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
