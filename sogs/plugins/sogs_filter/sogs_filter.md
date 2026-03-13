# SOGS Filter Plugin

Provides message filtering for profanity and arbitrary defined word patterns which are dubbed
"alphabets" in this plugin. When enabled, it can automatically reject messages or reply to users
with warnings when filtered content is detected.

The plugin supports per-room configuration overrides and can be configured to filter moderator
messages or exempt them. Filter responses can be customized with different reply messages for
different filter types (profanity vs alphabet violations).

## Architecture

- Uses better_profanity library for profanity detection
- Uses regex patterns for alphabet/script detection
- Supports hierarchical reply configuration (global -> room -> filter type -> language)
- Maintains filter state per-room with settings inheritance from global config

## Setup

### Standalone

Setup the .ini config (see the configuration section below for more details) with the desired
parameters and then you can run the plugin standalone:

```bash
cd session-pysogs
python3 -m sogs.plugins.sogs_filter --plugin_sogs_filter_ini_path <path/to/plugin/config.ini>
```

### UWSGI Mule

Alternatively you can run the plugin alongside the SOGS server as a UWSGI mule. In your UWSGI .ini config file, add to the [uwsgi] section:

```ini
[uwsgi]
mule = sogs.plugins.sogs_filter:entry_point
env  = PLUGIN_SOGS_FILTER_INI_PATH=<path/to/plugin/config.ini>
```

Note that the plugin can be parameterized via the following methods:

- Pass the `--plugin_sogs_filter_ini_path` flag to the python invocation
- Set the `PLUGIN_SOGS_FILTER_INI_PATH` environment variable
- Otherwise expects "sogs_filter.ini" in the current working directory if omitted

## Registration

Start the plugin via UWSGI or directly and after it has loaded the plugin will generate a Ed25519
keypair and output this information on startup, e.g.:

```
[SOGS FILTER] Plugin loaded:
  SOGS Address (Pubkey):      tcp://127.0.0.1:22028 (cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc)
  Display Name:               SOGS Filter Plugin
  Ed25519 Pubkey:             aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
  X25519 Pubkey:              bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
  Session Account (Blind-15): 15xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

The Ed25519 public key must be registered to the SOGS instance to enable the plugin to establish
a connection to the SOGS server, authenticate and consequently receive messages from SOGS to react
to. The plugin can be registered by invoking on the SOGS instance:

```bash
python3 -m sogs --add-plugin       <ed25519 pubkey hex 64 chars> \
                --plugin-name      'SOGS Filter Plugin' \
                --plugin-global    true \
                --plugin-approver  true \
                --plugin-required  true \
                --plugin-subscribe true
```

## Configuration

Configure how the SOGS Filter plugin's behaviour and how it connects to the SOGS server by adding
the following fields into the .ini file. This can be in your SOGS .ini file or a separate .ini file
if you wish.

This plugin has top-level settings to be configured in `[plugin_sogs_filter]`. Additionally you can
configure the filter specifically for a room and what the plugin should reply when a message is
filtered by adding sections with the following patterns:

```
[plugin_sogs_filter.room.<token>]
...

[plugin_sogs_filter.room.<token>.reply.<filter_name>]
...
```

See `sogs_filter.ini` for the template config.ini and a complete example showing hierarchical
configuration with per-room overrides.

### Filter Names

The filter name in reply sections can be one of the following reserved values:

- `*` - Universal/default replies (lowest precedence)
- `profanity` - Profanity-specific replies
- `alphabet` - General alphabet filter replies

Or any filter name defined in the `[plugin_sogs_filter.alphabets]` section.

### Escape Sequences

The reply value supports the following escape sequences:

- `\@` - @mention of the poster whose message was declined
- `\p` - profile name in plain text
- `\r` - name of the room
- `\t` - token of the room
- `\n` - a line break
- `\\` - a literal `\` character

### Configuration Priority

The plugin uses hierarchical precedence for configuration:

1. Most specific: `[plugin_sogs_filter.room.<token>.reply.<filter>]`
2. Filter-specific: `[plugin_sogs_filter.room.*.reply.<filter>]`
3. Category-specific: `[plugin_sogs_filter.room.<token>.reply.*]` or `[plugin_sogs_filter.room.*.reply.<category>]`
4. Least specific: `[plugin_sogs_filter.room.*.reply.*]` (global defaults)

### Example Result

With the example configuration in `sogs_filter.ini`:

- Messages containing arabic or cyrillic characters would be blocked everywhere with the message
  "Only Latin characters are supported here."
- Profanity would be blocked everywhere except the 'sailors' room
- In sudoku room, profanity violations would trigger one of three random Bot45 messages visible to
  everyone
