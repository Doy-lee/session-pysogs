# Emoji CAPTCHA Plugin

Generates a CAPTCHA challenge for users that are joining or are in a community and do not currently
have read permissions for the room. The CAPTCHA consists of some randomly drawn shapes and an emoji.
The plugin whispers to those users the CAPTCHA challenge where the user must react to the CAPTCHA
image with the correct emoji to gain read and write permissions to the room.

By default the user has 3 attempts at solving the challenge and they may request a new CAPTCHA with
each request consuming an attempt. If the user consumes all 3 attempts then they are unable to join
the server and must contact the room administrator for manual intervention.

## Architecture

- Runs in a separate Python process and communicates with SOGS via OxenMQ
- Subscribes to room read permission requests from SOGS via register_request_read_handler()
- Periodically gets invoked by SOGS via the subscription with users that are requesting read access
  where the CAPTCHA state machine for that user is iterated (whispering instructions to the user,
  uploading and sending the user the CAPTCHA...)
- Overrides the emoji on_reaction_posted() hook to get notified of when the user reacts to a CAPTCHA
  message to provide an answer to the CAPTCHA that is then checked

## Setup

### Standalone

Setup the .ini config file (see the configuration section below for more details) with the desired
parameters and then you can run the plugin standalone:

```bash
cd session-pysogs
python3 -m sogs.plugins.emoji_captcha --plugin_emoji_captcha_ini_path <path/to/plugin/config.ini>
```

### UWSGI Mule

Alternatively you can run the plugin alongside the SOGS server as a UWSGI mule. In your UWSGI .ini config file, add to the [uwsgi] section:

```ini
[uwsgi]
mule = sogs.plugins.emoji_captcha:entry_point
env  = PLUGIN_EMOJI_CAPTCHA_INI_PATH=<path/to/plugin/config.ini>
```

Note that the plugin can be parameterized via the following methods:

- Pass the `--plugin_emoji_captcha_ini_path` flag to the python invocation
- Set the `PLUGIN_EMOJI_CAPTCHA_INI_PATH` environment variable
- Otherwise expects "emoji_captcha.ini" in the current working directory if omitted

## Registration

Start the plugin via UWSGI or directly and after it has initialised the plugin will generate
a Ed25519 keypair and output this information on startup, e.g.:

```
[EMOJI CAPTCHA] Plugin initialised:
  SOGS Address (Pubkey):      tcp://127.0.0.1:22028 (cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc)
  Display Name:               Emoji CAPTCHA Plugin
  Ed25519 Pubkey:             aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
  X25519 Pubkey:              bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
  Session Account (Blind-15): 15xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
  Refresh/Retry/Write:        60s/60s/120s
  CAPTCHA Retries:            3
```

The Ed25519 public key must be registered to the SOGS instance to enable the plugin to establish
a connection to the SOGS server, authenticate and consequently receive messages from SOGS to react
to. The plugin can be registered by invoking on the SOGS instance:

```bash
python3 -m sogs --add-plugin       <ed25519 pubkey hex 64 chars> \
                --plugin-name      'Emoji CAPTCHA Plugin' \
                --plugin-global    true \
                --plugin-approver  true \
                --plugin-required  true \
                --plugin-subscribe true
```

## Configuration

Configure how the Emoji CAPTCHA plugin's behaviour and how it connects to the SOGS server by adding
the following fields into the .ini file. This can be in your SOGS .ini file or a separate .ini file
if you wish.

See `emoji_captcha.ini` for the example configuration.

### Key Settings

- `captcha_limit` - This is the total number of unique CAPTCHAs the SOGS will provide to the user if
  they fail a CAPTCHA or refresh the CAPTCHA. If the user exceeds the retry limit, they will need to
  contact the SOGS operator to have their permissions manually updated (default: 3)
- `write_timeout_s` - This is the number of seconds the SOGS will wait after a user successfully
  solves a CAPTCHA before the user is given write permissions (default: 120)
- `refresh_timeout_s` - This is the number of seconds the SOGS will wait before allowing a user to
  refresh the CAPTCHA (default: 60)
- `retry_timeout_s` - This is the number of seconds the SOGS will wait before providing the next
  CAPTCHA if a user fails the CAPTCHA (default: 60)
- `emoji_list_file` - Set the path to a text file that contains the list of emojis to use in the
  CAPTCHA challenge. If this field is not supplied, a default list of emojis will be used.

### Emoji List File Format

You may specify the emojis using a unicode escaped string, or otherwise the emoji verbatim. Comments
are supported by suffixing the line with '#' followed by the comment.

You may use `\U` syntax for 8 byte, `\u` for 4 byte encoding of emojis or the emoji glyph directly.
The specified path can be absolute or relative. If it's relative, the file is opened relative to the
directory that this .INI file is loaded from.

Example:

```
\U0001F602 # 😂
❤️
```

Would make the plugin generate challenge CAPTCHAs with an answer of either the crying-face or heart
emoji. Duplicate emojis increases the chance of the emoji being selected.
