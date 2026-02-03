import re
from sogs.plugin import Plugin, ReplySettings, FilterResponse
from sogs.model.post import Post

def profanity_check(*args):
    import better_profanity

    for part in args:
        if better_profanity.profanity.contains_profanity(part):
            print(f"Profanity detected in message part: \"{part}\"")
            return True

    return False


class SogsFilterPlugin(Plugin):

    # Character ranges for different filters.  This is ordered because some are subsets of each other
    # (e.g. persian is a subset of the arabic character range).
    alphabet_filter_patterns = [
        (
            'persian',
            re.compile(
                r'[\u0621-\u0628\u062a-\u063a\u0641-\u0642\u0644-\u0648\u064e-\u0651\u0655'
                r'\u067e\u0686\u0698\u06a9\u06af\u06be\u06cc]'
            ),
        ),
        (
            'arabic',
            re.compile(r'[\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff\ufb50-\ufdff\ufe70-\ufefe]'),
        ),
        ('cyrillic', re.compile(r'[\u0400-\u04ff]')),
        ('debug', re.compile(r'debug alphabet test')),
    ]

    """
    Handles profanity filtering and alphabet detection/direction (replacing the functionality
            which was previously built into SOGS directly).

    Pass config_file=path_to_sogs.ini or config_file=True to load config from
    environment SOGS_CONFIG variable or 'sogs.ini' in pwd

    Pass reply_name to override the default Session display name of this plugin (SOGS Plugin)
    """

    def __init__(self, privkey, pubkey, *args, display_name="SOGS Plugin", config_file=None):

        self.room_settings = {}
        self.filter_mods = False

        if isinstance(config_file, str):
            import os

            os.environ['SOGS_CONFIG'] = config_file

        from sogs import config

        self.config = config
        from sogs.crypto import server_pubkey_bytes

        sogs_pubkey = server_pubkey_bytes
        sogs_address = config.OMQ_LISTEN[0].replace('*', '127.0.0.1')
        self.from_sogs_config = True
        self.load_sogs_settings()

        Plugin.__init__(self, sogs_address, sogs_pubkey, privkey, pubkey, display_name)

    def load_sogs_settings(self):
        self.filter_mods = self.config.FILTER_MODS
        settings = {
            'profanity_filter': self.config.PROFANITY_FILTER,
            'profanity_silent': self.config.PROFANITY_SILENT,
            'alphabet_filters': self.config.ALPHABET_FILTERS,
            'alphabet_silent': self.config.ALPHABET_SILENT,
            'reply_settings': None,
        }
        self.room_settings['*'] = {}
        for k in self.config.FILTER_SETTINGS:
            if (
                'profanity' in self.config.FILTER_SETTINGS[k]
                or '*' in self.config.FILTER_SETTINGS[k]
            ):
                self.room_settings[k] = {}

        for k in settings:
            for room in self.room_settings:
                self.room_settings[room][k] = settings[k]

        print(f"overrides:\n{self.config.ROOM_OVERRIDES}\n")
        for room_token in self.config.ROOM_OVERRIDES:
            self.room_settings[room_token] = {}
            for k in settings:
                self.room_settings[room_token][k] = settings[k]
            for k in (
                'profanity_filter',
                'profanity_silent',
                'alphabet_filters',
                'alphabet_silent',
            ):
                if k in self.config.ROOM_OVERRIDES[room_token]:
                    self.room_settings[room_token][k] = self.config.ROOM_OVERRIDES[room_token][k]

        print(self.room_settings)

    def get_reply_settings(self, room_token, *args, filter_type='profanity', filter_lang=None) -> ReplySettings | None:
        if not self.config.FILTER_SETTINGS:
            return None

        reply_format = None
        profile_name = 'SOGS'
        public = False

        # Precedences from least to most specific so that we load values from least specific first
        # then overwrite them if we find a value in a more specific section
        room_precedence = ('*', room_token)
        filter_precedence = ('*', filter_type, filter_lang) if filter_lang else ('*', filter_type)

        for r in room_precedence:
            s1 = self.config.FILTER_SETTINGS.get(r)
            if s1 is None:
                continue
            for f in filter_precedence:
                settings = s1.get(f)
                if settings is None:
                    continue

                rf = settings.get('reply')
                pn = settings.get('profile_name')
                pb = settings.get('public')
                if rf is not None:
                    reply_format = rf
                if pn is not None:
                    profile_name = pn
                if pb is not None:
                    public = pb

        if reply_format is None:
            return None

        return ReplySettings(reply_formats=reply_format, profile_name=profile_name, public=public)

    def filter(self, request):
        # is_mod should be "mod" but is empty if not, so just check len
        if request[b"is_mod"] and not self.filter_mods:
            return FilterResponse.Accept

        if request[b"message_id"] != -1:
            print("message filter request is an edit")

        room_token = request[b"room_token"].decode('utf-8')
        print(f"filtering for room_token: {room_token}")
        if room_token in self.room_settings:
            settings = self.room_settings[room_token]
            print("filter using room-specific settings")
        else:
            settings = self.room_settings['*']
            print("filter using global settings")

        if not (settings['profanity_filter'] or settings['alphabet_filters']):
            return FilterResponse.Accept

        msg = Post(raw=request[b"message_data"])

        prof_result = FilterResponse.Accept
        if settings['profanity_filter'] and profanity_check(msg.text, msg.username):
            reply_settings = self.get_reply_settings(room_token, filter_type='profanity')
            if reply_settings:
                print(f"replying with format: {reply_settings}")
                self.reply(
                    request[b"room_name"],
                    request[b"room_token"],
                    bytes.fromhex(request[b"session_id"].decode()),
                    request[b"message_data"],
                    msg.username,
                    reply_settings=reply_settings,
                )
            prof_result = (
                FilterResponse.Silent if settings['profanity_silent'] else FilterResponse.Reject
            )

        if not settings['alphabet_filters']:
            return prof_result

        alpha_result = FilterResponse.Accept
        for lang, pattern in self.alphabet_filter_patterns:
            if lang not in settings['alphabet_filters']:
                continue

            if not pattern.search(msg.text):
                continue

            # Filter it!
            filter_type, filter_lang = 'alphabet', lang
            reply_settings = self.get_reply_settings(
                request[b"room_token"], filter_type=filter_type, filter_lang=filter_lang
            )
            if reply_settings:
                print(f"replying with format: {reply_settings}")
                self.reply(
                    request[b"room_name"],
                    request[b"room_token"],
                    bytes.fromhex(request[b"session_id"].decode()),
                    request[b"message_data"],
                    msg.username,
                    reply_settings=reply_settings,
                )

            alpha_result = (
                FilterResponse.Reject if settings['alphabet_silent'] else FilterResponse.Reject
            )

            break

        if alpha_result == FilterResponse.Reject or prof_result == FilterResponse.Reject:
            # Example of re-injecting the message later if some other approval process succeeds:
            # msg_id = self.inject_message(room_token, user_session_id, message_data, sig, whisper_target = whisper_target, whisper_mods = whisper_mods)
            return FilterResponse.Reject
        elif alpha_result == FilterResponse.Silent or prof_result == FilterResponse.Silent:
            return FilterResponse.Silent

        return FilterResponse.Accept
