"""Emoji CAPTCHA Plugin

This plugin generates a CAPTCHA challenge for users that are joining or are in a community and do
not currently have read permissions for the room. The CAPTCHA consists of some randomly draw shapes
and an emoji. The plugin whispers to those users the CAPTCHA challenge where the user must react to
the CAPTCHA image with the correct emoji to gain read and write permissions to the room.

By default the user has 3 attempts at solving the challenge and they may request a new CAPTCHA with
each request consuming an attempt. If the user consumes all 3 attempts then they are unable to join
the server and must contact the room administrator for manual intervention.

Architecture:
 - Runs in a separate Python process and communicates with SOGS via OxenMQ.
 - Subscribes to room read permission requests from SOGS via register_request_read_handler().
 - Periodically gets invoked by SOGs via the subscription with users that are requesting read access
   where the CAPTCHA state machine for that user is iterated (whispering instructions to the user,
   uploading and sending the user the CAPTCHA...).
 - Overrides the emoji reaction_posted() hook to get notified of when the user reacts to a CAPTCHA
   message challenged them with.
"""

import asyncio
import dataclasses
import enum
import math
import os
import random
import typing

from time               import time
from sogs.plugin        import *
from concurrent.futures import ThreadPoolExecutor
from PIL                import Image,  ImageDraw, ImageFont

class ShapeType(enum.Enum):
    Rectangle = 0
    Hexagon   = 1
    Circle    = 2
    Triangle  = 3
    Star      = 4
    Octagon   = 5

# List of default colours (in RGB) used to draw the background shapes in the CAPTCHAs
DEFAULT_COLOUR_SET: list[int] = [
    0x31F196,
    0x57C9FA,
    0xC993FF,
    0xFF95EF,
    0xFF9C8E,
    0xFCB159,
    0xFAD657,
]
assert len(DEFAULT_COLOUR_SET) >= len(ShapeType)

EMOJI_LIST: list[str] = [
    "\U0001F602",           # 😂
    "\U00002764\U0000FE0F", # ❤️
    "\U0001F923",           # 🤣
    "\U0001F44D",           # 👍
    "\U0001F62D",           # 😭
    "\U0001F64F",           # 🙏
    "\U0001F618",           # 😘
    "\U0001F970",           # 🥰
    "\U0001F60D",           # 😍
    "\U0001F60A",           # 😊
    "\U0001F389",           # 🎉
    "\U0001F601",           # 😁
    "\U0001F495",           # 💕
    "\U0001F97A",           # 🥺
    "\U0001F605",           # 😅
    "\U0001F525",           # 🔥
    "\U0000263A\uFE0F",     # ☺️
    "\U0001F926",           # 🤦
    "\U00002665\uFE0F",     # ♡
    "\U0001F937",           # 🤷
    "\U0001F644",           # 🙄
    "\U0001F606",           # 😆
    "\U0001F917",           # 🤗
    "\U0001F609",           # 😉
    "\U0001F382",           # 🎂
    "\U0001F914",           # 🤔
    "\U0001F44F",           # 👏
    "\U0001F642",           # 🙂
    "\U0001F633",           # 😳
    "\U0001F973",           # 🥳
    "\U0001F60E",           # 😎
    "\U0001F44C",           # 👌
    "\U0001F49C",           # 💜
    "\U0001F614",           # 😔
    "\U0001F4AA",           # 💪
    "\U00002728",           # ✨
    "\U0001F496",           # 💖
    "\U0001F440",           # 👀
    "\U0001F60B",           # 😋
    "\U0001F60F",           # 😏
    "\U0001F622",           # 😢
    "\U0001F449",           # 👉
    "\U0001F497",           # 💗
    "\U0001F629",           # 😩
    "\U0001F4AF",           # 💯
    "\U0001F339",           # 🌹
    "\U0001F49E",           # 💞
    "\U0001F388",           # 🎈
    "\U0001F499",           # 💙
    "\U0001F603",           # 😃
    "\U0001F621",           # 😡
    "\U0001F490",           # 💐
    "\U0001F61C",           # 😜
    "\U0001F648",           # 🙈
    "\U0001F91E",           # 🤞
    "\U0001F604",           # 😄
    "\U0001F924",           # 🤤
    "\U0001F64C",           # 🙌
    "\U0001F92A",           # 🤪
    "\U00002763\uFE0F",     # ☣️
    "\U0001F600",           # 😀
    "\U0001F48B",           # 💋
    "\U0001F480",           # 💀
    "\U0001F447",           # 👇
    "\U0001F494",           # 💔
    "\U0001F60C",           # 😌
    "\U0001F493",           # 💓
    "\U0001F929",           # 🤩
    "\U0001F643",           # 🙃
    "\U0001F62C",           # 😬
    "\U0001F631",           # 😱
    "\U0001F634",           # 😴
    "\U0001F92D",           # 🤭
    "\U0001F610",           # 😐
    "\U0001F31E",           # 🌞
    "\U0001F612",           # 😒
    "\U0001F607",           # 😇
    "\U0001F338",           # 🌸
    "\U0001F608",           # 😈
    "\U0001F3B6",           # 🎶
    "\U0000270C\uFE0F",     # ✌️
    "\U0001F38A",           # 🎊
    "\U0001F975",           # 🥵
    "\U0001F61E",           # 😞
    "\U0001F49A",           # 💚
    "\U00002600\uFE0F",     # ☀️
    "\U0001F5A4",           # 🖤
    "\U0001F4B0",           # 💰
    "\U0001F61A",           # 😚
    "\U0001F451",           # 👑
    "\U0001F381",           # 🎁
    "\U0001F4A5",           # 💥
    "\U0001F64B",           # 🙋
    "\U00002639\uFE0F",     # ☹️
    "\U0001F611",           # 😑
    "\U0001F974",           # 🥴
    "\U0001F448",           # 👈
    "\U0001F4A9",           # 💩
    "\U00002705",           # ✅
    "\U0001F44B",           # 👋
    "\U0001F92E",           # 🤮
    "\U0001F624",           # 😤
    "\U0001F922",           # 🤢
    "\U0001F31F",           # 🌟
    "\U00002757",           # ❗
    "\U0001F625",           # 😥
    "\U0001F308",           # 🌈
    "\U0001F49B",           # 💛
    "\U0001F61D",           # 😝
    "\U0001F62B",           # 😫
    "\U0001F632",           # 😲
    "\U0001F595",           # 🖕
    "\U0000203C\uFE0F",     # ‼️
    "\U0001F534",           # 🔴
    "\U0001F33B",           # 🌻
    "\U0001F92F",           # 🤯
    "\U0001F483",           # 💃
    "\U0001F44A",           # 👊
    "\U0001F92C",           # 🤬
    "\U0001F3C3",           # 🏃
    "\U0001F615",           # 😕
    "\U0001F441\uFE0F",     # 👁️
    "\U000026A1",           # ⚡
    "\U00002615",           # ⚕️
    "\U0001F340",           # 🍀
    "\U0001F4A6",           # 💦
    "\U00002B50",           # ⭐
    "\U0001F98B",           # 🦋
    "\U0001F928",           # 🤨
    "\U0001F33A",           # 🌺
    "\U0001F639",           # 😹
    "\U0001F918",           # 🤘
    "\U0001F337",           # 🌷
    "\U0001F49D",           # 💝
    "\U0001F4A4",           # 💤
    "\U0001F91D",           # 🤝
    "\U0001F430",           # 🐰
    "\U0001F613",           # 😓
    "\U0001F498",           # 💘
    "\U0001F37B",           # 🍻
    "\U0001F61F",           # 😟
    "\U0001F623",           # 😣
    "\U0001F9D0",           # 🧐
    "\U0001F620",           # 😠
    "\U0001F920",           # 🤠
    "\U0001F63B",           # 😻
    "\U0001F319",           # 🌙
    "\U0001F61B",           # 😛
    "\U0001F919",           # 🤙
    "\U0001F64A",           # 🙊
    "\U0001F9E1",           # 🧡
    "\U0001F921",           # 🤡
    "\U0001F92B",           # 🤫
    "\U0001F33C",           # 🌼
    "\U0001F942",           # 🥂
    "\U0001F637",           # 🤒
    "\U0001F913",           # 🤓
    "\U00002620\uFE0F",     # ☠️
    "\U0001F976",           # 🥶
    "\U0001F636",           # 😶
    "\U0001F616",           # 😖
    "\U0001F3B5",           # 🎵
    "\U0001F6B6",           # 🚶
    "\U0001F619",           # 😙
    "\U0001F346",           # 🍆
    "\U0001F911",           # 🤑
    "\U0001F485",           # 💅
    "\U0001F617",           # 😗
    "\U0001F436",           # 🐶
    "\U0001F353",           # 🍓
    "\U0000270B",           # ✋
    "\U0001F445",           # 👅
    "\U0001F444",           # 👄
    "\U0001F33F",           # 🌿
    "\U0001F6A8",           # 🚨
    "\U000027A1\uFE0F",     # ➡️
    "\U0001F4E3",           # 📣
    "\U0001F91F",           # 🤟
    "\U0001F351",           # 🍑
    "\U0001F343",           # 🍃
    "\U0001F62E",           # 😮
    "\U0001F48E",           # 💎
    "\U0001F4E2",           # 📢
    "\U0001F331",           # 🌱
    "\U000026A0\uFE0F",     # ⚠️
    "\U0001F641",           # 🙁
    "\U0001F377",           # 🍷
    "\U0001F62A",           # 😪
    "\U0001F31A",           # 🌚
    "\U0001F3C6",           # 🏆
    "\U0001F352",           # 🍒
    "\U00002714\uFE0F",     # ✔️
    "\U0001F489",           # 💉
    "\U0000274C",           # ❌
    "\U0001F4A2",           # 💢
    "\U0001F6D2",           # 🛒
    "\U0001F638",           # 😸
    "\U0001F43E",           # 🐾
    "\U0001F44E",           # 👎
    "\U0001F680",           # 🚀
    "\U0001F3AF",           # 🎯
    "\U0000261D\uFE0F",     # ☝️
    "\U0001F37A",           # 🍺
    "\U0001F4CC",           # 📌
    "\U0001F4F7",           # 📷
    "\U0001F647",           # 🙇
    "\U0001F4A8",           # 💨
    "\U0001F355",           # 🍕
    "\U0001F3E0",           # 🏠
    "\U0001F4F8",           # 📸
    "\U0001F407",           # 🐇
    "\U0001F6A9",           # 🚩
    "\U0001F630",           # 😰
    "\U0001F476",           # 👶
    "\U0001F30A",           # 🌊
    "\U0001F415",           # 🐕
    "\U0001F4AB",           # 💫
    "\U0001F635",           # 😵
    "\U0001F3A4",           # 🎤
    "\U0001F3E1",           # 🏡
    "\U0001F940",           # 🥀
    "\U0001F927",           # 🤧
    "\U0001F37E",           # 🍾
    "\U0001F370",           # 🍰
    "\U0001F341",           # 🍁
    "\U0001F932",           # 🤲
    "\U00002B07\uFE0F",     # 👇
    "\U0001F446",           # ☝️
    "\U0001F62F",           # 😯
    "\U0000270A",           # ✊
    "\U0001F48C",           # 💌
    "\U00002744\uFE0F",     # ❄️
    "\U0001F4B8",           # 💸
    "\U0001F9C1",           # 🧁
    "\U000026BD",           # ⚽
    "\U0001F1FA\U0001F1F8", # 🇺🇸
    "\U00002753",           # ❓
    "\U0001F57A",           # 🕺
    "\U00002049\uFE0F",     # ⁴️
    "\U0001F63A",           # 😺
    "\U0001F4A7",           # 💧
    "\U0001F4A3",           # 💣
    "\U0001F910",           # 🤐
    "\U0001F34E",           # 🍎
    "\U0001F437",           # 🐷
    "\U0001F425",           # 🐥
    "\U0001F481",           # 💁
    "\U0001F4CD",           # 📍
    "\U0001F380",           # 🎀
    "\U0001F645",           # 🙅
    "\U0001F947",           # 🥇
    "\U0001F31D",           # 🌝
    "\U0001F52B",           # 🔫
    "\U0000260E\uFE0F",     # ☎️
    "\U0001F431",           # 🐱
    "\U0001F423",           # 🐣
    "\U0000271D\uFE0F",     # ✝️
    "\U0001F3A7",           # 🎧
    "\U0001F49F",           # 💟
    "\U0001F479",           # 👹
    "\U0001F48D",           # 💍
    "\U0001F37C",           # 🍼
    "\U0001F590\uFE0F",     # 🖐
    "\U0001F4A1",           # 💡
    "\U0001F63D",           # 😽
    "\U0001F34A",           # 🍊
    "\U0001F628",           # 😨
    "\U0001F36B",           # 🍫
    "\U0001F9E2",           # 🧢
    "\U0001F915",           # 🤕
    "\U00002618\uFE0F",     # ☘️
    "\U0001F6AB",           # 🚫
    "\U0001F3BC",           # 🎼
    "\U0001F43B",           # 🐻
    "\U0001F4F2",           # 📲
    "\U0001F47B",           # 👻
    "\U0001F5E3\uFE0F",     # 🗣
    "\U0001F47F",           # 👿
    "\U0001F9DA",           # 🧚
    "\U0001F32E",           # 🌮
    "\U0001F36D",           # 🍭
    "\U0001F41F",           # 🐟
    "\U0001F438",           # 🐸
    "\U0001F41D",           # 🐝
    "\U0001F408",           # 🐈
    "\U0001F535",           # 🔵
    "\U0001F327\uFE0F",     # 🌧
    "\U0001F52A",           # 🔪
    "\U0001F627",           # 😧
    "\U0001F304",           # 🌄
    "\U0001F63E",           # 😾
    "\U00002708\uFE0F",     # ✈️
    "\U0001F938",           # 🤸
    "\U0001F4F1",           # 📱
    "\U0001F347",           # 🍇
    "\U0001F334",           # 🌴
    "\U0001F422",           # 🐢
    "\U0001F303",           # 🌃
    "\U0001F47D",           # 👽
    "\U0001F34C",           # 🍌
]

@dataclasses.dataclass
class Captcha:
    """Base CAPTCHA data class. Subclass for different challenge types."""
    answer:    str # Answer to the CAPTCHA challenge (e.g. the emoji string)
    file_path: str # File path to the CAPTCHA
    async def generate_captcha(self, executor: ThreadPoolExecutor, width: int, height: int, font: ImageFont.FreeTypeFont):  # pyright: ignore[reportUnusedParameter]
        """Generate the CAPTCHA image. Override in subclasses."""
        pass

@dataclasses.dataclass
class EmojiCaptcha(Captcha):
    """Generates emoji-based CAPTCHAs with geometric background shapes."""

    @typing.override
    async def generate_captcha(self, executor: ThreadPoolExecutor, width: int, height: int, font: ImageFont.FreeTypeFont, color_set: list[int] = DEFAULT_COLOUR_SET):
        assert len(color_set) >= len(ShapeType), \
            "The number of colours to select from must be greater the number of shapes we that are to be drawn"

        image: Image.Image = Image.new("RGB", (width, height), 0x626262)
        draw               = ImageDraw.ImageDraw(image)

        random_colors = [random.choice(color_set) for _ in range(len(ShapeType))]
        min_size_x    = int(width * 0.3)
        min_size_y    = int(height * 0.3)

        for index, shape_type in enumerate(ShapeType):
            color = random_colors[index]
            x1    = random.randint(0, width - min_size_x)
            y1    = random.randint(0, height - min_size_y)
            x2    = x1 + random.randint(min_size_x, min(width - x1, int(width / 2)))
            y2    = y1 + random.randint(min_size_y, min(height - y1, int(height / 2)))
            match shape_type:
                case ShapeType.Rectangle:
                    draw.rectangle([x1, y1, x2, y2], fill=color)
                case ShapeType.Hexagon:
                    draw.regular_polygon(
                        [(x1 + x2) // 2, (y1 + y2) // 2, min(x2 - x1, y2 - y1) // 2],
                        6,
                        fill=color)
                case ShapeType.Circle:
                    # Ensure the bounding box is square to draw a perfect circle
                    side_length = min(x2 - x1, y2 - y1)
                    draw.ellipse([x1, y1, x1 + side_length, y1 + side_length], fill=color)
                case ShapeType.Triangle:
                    draw.regular_polygon(
                        [(x1 + x2) // 2, (y1 + y2) // 2, min(x2 - x1, y2 - y1) // 2],
                        3,
                        fill=color)
                case ShapeType.Star:
                    # Parameters for star shape
                    center_x                          = (x1 + x2) // 2
                    center_y                          = (y1 + y2) // 2
                    radius                            = min(x2 - x1, y2 - y1) // 2
                    points: list[tuple[float, float]] = []
                    for i in range(10):  # 5 points for a star, each point needs 2 coordinates (outer and inner)
                        angle = i * (2 * 3.14159 / 10)
                        r     = radius if i % 2 == 0 else radius // 2
                        points.append((center_x + r * math.cos(angle), center_y + r * math.sin(angle)))
                    draw.polygon(points, fill=color)
                case ShapeType.Octagon:
                    draw.regular_polygon(
                        [(x1 + x2) // 2, (y1 + y2) // 2, min(x2 - x1, y2 - y1) // 2],
                        8,
                        fill=color)

        emoji_margin: float = int(font.size * 0.5) + font.size;
        emoji_x:      float = float(random.randint(0, int(width - emoji_margin)))
        emoji_y:      float = float(random.randint(0, int(height - emoji_margin)))
        assert width - emoji_margin > 0 and height - emoji_margin > 0
        _ = draw.text((emoji_x, emoji_y), self.answer, font=font, embedded_color=True)

        await asyncio.get_event_loop().run_in_executor(executor, image.save, self.file_path)


@dataclasses.dataclass
class CaptchaManager:
    """Manages a pool of pre-generated CAPTCHAs for efficient distribution."""
    data_dir:     str           = "./.sogs/plugins/captcha"
    batch_size:   int           = 32
    captcha_list: list[Captcha] = dataclasses.field(default_factory=list)
    font_path:    str           = os.path.dirname(os.path.abspath(__file__)) + '/NotoColorEmoji.ttf'
    font_size:    int           = 109 # Suitable font size specifically for NotoColorEmoji
    width:        int           = 400
    height:       int           = 400

    def __post_init__(self):
        """Initialize and pre-generate CAPTCHA batch."""
        os.makedirs(self.data_dir, exist_ok=True)
        asyncio.run(self.batch_generate_captcha(self.batch_size))

    async def batch_generate_captcha(self, count: int):
        """Generate multiple CAPTCHAs concurrently using thread pool."""
        start_time = time()
        font: ImageFont.FreeTypeFont = ImageFont.truetype(self.font_path, self.font_size, layout_engine=ImageFont.Layout.RAQM)
        with ThreadPoolExecutor(max_workers=8) as executor:
            tasks = []
            for i in range(count):
                captcha = EmojiCaptcha(random.choice(list(EMOJI_LIST)), f"{self.data_dir}/captcha_{i:03}.png")
                self.captcha_list.append(captcha)
                tasks.append(captcha.generate_captcha(executor, width=self.width, height=self.height, font=font, color_set=DEFAULT_COLOUR_SET))
            await asyncio.gather(*tasks)
        log.debug(f"Generated {self.batch_size} CAPTCHAs in {time() - start_time:.4}s")

    def refresh(self) -> Captcha:
        """Get a CAPTCHA from the pool, regenerating if empty."""
        if len(self.captcha_list) == 0:
            asyncio.run(self.batch_generate_captcha(self.batch_size))
        return self.captcha_list.pop()

class RefreshState(enum.Enum):
    """State machine for CAPTCHA refresh requests."""
    Nil     = 0  # No refresh in progress
    Request = 1  # User requested refresh
    Wait    = 2  # Waiting for timeout before allowing refresh
    Ready   = 3  # Ready to generate refreshed CAPTCHA

class CaptchaState(enum.Enum):
    """State machine for CAPTCHA challenge lifecycle."""
    Nil     = 0  # Initial state / reset
    Answer  = 1  # Incorrect answer received, need to show error
    Wait    = 2  # Waiting for retry timeout after failure
    Ready   = 3  # Ready to present new CAPTCHA
    Solved  = 4  # Correctly answered, grant access

@dataclasses.dataclass
class UserCaptchaState:
    """Per-user, per-room state for CAPTCHA lifecycle management.

    Tracks all state needed to manage a user's progress through the CAPTCHA flow.
    Stored in nested dict: users[session_id][room_token] -> UserCaptchaState
    """
    refresh_state:                        RefreshState     = RefreshState.Nil
    refresh_msg_id:                       MessageID | None = None

    captcha_state:                        CaptchaState     = CaptchaState.Nil
    captcha_failed_next_attempt_at_ts:    TimestampS       = 0.0  # Retry timeout after failure
    captcha_attempts:                     int              = 0    # Count of used CAPTCHAs
    captcha_limit_msg_shown:              bool             = False

    captcha_solved_grant_access_at_ts:    TimestampS       = 0.0
    captcha_solved_welcome_msg_shown:     bool             = False
    captcha_solved_grant_access:          bool             = False

    posted_captcha:                       Captcha | None   = None
    posted_captcha_timestamp:             TimestampS       = 0.0
    posted_captcha_msg_id:                MessageID | None = None
    posted_captcha_refresh_emoji_applied: bool             = False

    # Reliable message deletion queues - messages retried until successful deletion
    msgs_to_delete_on_tick:               list[MessageID]  = dataclasses.field(default_factory=list)
    reactions_to_delete_on_tick:          list[MessageID]  = dataclasses.field(default_factory=list)
    msgs_to_delete_on_ready:              list[MessageID]  = dataclasses.field(default_factory=list)

    def clear_posted_captcha(self):
        """Reset current CAPTCHA state after failure or consumption."""
        self.posted_captcha_msg_id                = None
        self.posted_captcha_refresh_emoji_applied = False
        self.posted_captcha                       = None
        self.posted_captcha_timestamp             = 0

@dataclasses.dataclass
class CaptchaPlugin(Plugin):
    """SOGS Plugin implementing emoji CAPTCHA verification for room access"""
    attempt_limit_str: typing.ClassVar[str]                               = "You have hit the attempt limit, solve the CAPTCHA to proceed."
    refresh_emoji:     str                                                = "\U0001F504" # Unicode refresh symbol emoji
    users:             dict[SessionID, dict[RoomToken, UserCaptchaState]] = dataclasses.field(default_factory=dict)

    captcha_limit:     int                                                = 3   # Max CAPTCHA attempts per user/room
    retry_timeout_s:   int                                                = 10  # Seconds to wait after failed attempt
    refresh_timeout_s: int                                                = 10  # Seconds between CAPTCHA refreshes
    write_timeout_s:   int                                                = 10  # Seconds before access grant after solve
    captcha_manager:   CaptchaManager                                     = dataclasses.field(default_factory=CaptchaManager)

    def __post_init__(self):
        super().__post_init__()
        # NOTE: Register our hook which is called by SOGS when a user attempts to read from the
        # community. In this hook we check if the user has solved a captcha before and lets the user
        # read or otherwise require them to solve captcha to proceed.
        self.register_request_read_handler(self.handle_request_read)
        log.info("Plugin initialised: refresh {}s; retry {}s; write {}s; captcha limit {}"
                 .format(self.refresh_timeout_s, self.retry_timeout_s, self.write_timeout_s, self.captcha_limit))

    def get_user(self, session_id: bytes, room_token: bytes) -> UserCaptchaState | None:
        result = None
        if session_id in self.users and room_token in self.users[session_id]:
            result = self.users[session_id][room_token]
        return result

    def get_or_make_user(self, session_id: bytes, room_token: bytes) -> UserCaptchaState:
        result = self.users.setdefault(session_id, {}).setdefault(room_token, UserCaptchaState())
        return result

    def handle_request_read(self, req: RoomReadRequest) -> bt_value:
        """Handles generating a CAPTCHA for the user requesting read permission into a particular
        room as well as rate limiting these attempts and allowing users to refresh the provided
        CAPTCHA. If a user already has read permission, this hook is not called for that user.

        When this plugin is initialised, it hooks into the read requests for the current running
        SOGS. Session users that do not have read permissions for a room periodically request that
        permission. Plugins that are registered have their read function triggered where it can run
        arbitrary code at that point.

        Here we tick the plugin, executing a state machine that takes the user through the CAPTCHA
        challenge lifecycle.
        """
        user: UserCaptchaState = self.get_or_make_user(req.session_id, req.room_token)
        log.debug(f"Room {req.room_token} polled by 0x{req.session_id.hex()} (id={req.user_id}, challenges={user.captcha_attempts}/{self.captcha_limit})")

        result: bt_value = self.tick(room_token=req.room_token, session_id=req.session_id, room_name=req.room_name)
        return result

    def _ensure_refresh_emoji_on_captcha(self, room_token: bytes, user: UserCaptchaState, msg_id: MessageID):
        captchas_remaining: int  = self.captcha_limit - user.captcha_attempts
        if not user.posted_captcha_refresh_emoji_applied and captchas_remaining > 1:
            react_resp: dict[bytes, bt_value] = self.post_reactions(room_token, msg_id, self.refresh_emoji)
            if b'status' in react_resp and react_resp[b'status'] == b'OK':
                user.posted_captcha_refresh_emoji_applied = True

    def tick(self, room_token: bytes, session_id: SessionID, room_name: str) -> bt_value:
        """Executes the CAPTCHA lifecycle for the specified user and room

        This is periodically called to progress the CAPTCHA lifecycle for the user:

          - First time users receive a CAPTCHA immediately to solve
          - The user can request a new CAPTCHA every `refresh_timeout_s` seconds
          - A user must wait `retry_timeout_s` seconds when the CAPTCHA is answered incorrectly
          - Throughout this process the user should be informed of the number of attempts they have,
            when they fail and amount of time they have to wait before taking certain actions.

        This plugin implements reliable message sending by checking that a message send is
        successful before progressing the CAPTCHA lifecycle.
        """
        user: UserCaptchaState = self.get_or_make_user(session_id, room_token)
        now:  float            = time()

        # NOTE: CAPTCHA state-machine
        if 1:
            # Handle an incorrectly answered CAPTCHA. A new CAPTCHA will not be generated until
            # the `retry_timeout_s` delay has transpired since the time of the answer.
            if user.captcha_state == CaptchaState.Ready or user.captcha_attempts >= self.captcha_limit:
              user.captcha_state = CaptchaState.Nil

            if user.captcha_state == CaptchaState.Answer:
                attempts_remaining: int = self.captcha_limit - (user.captcha_attempts + 1)
                if attempts_remaining > 0:
                    remaining               = f"{attempts_remaining} attempt" + ("s" if attempts_remaining > 1 else "")
                    body                    = f"Incorrect emoji, a new CAPTCHA will be available in {self.retry_timeout_s} seconds. {remaining} remaining."
                    msg_id                  = self.post_message(room_token, body, whisper_target=session_id, no_plugins=True)
                    if msg_id:
                        user.captcha_failed_next_attempt_at_ts = now + self.retry_timeout_s
                        user.captcha_state                     = CaptchaState.Wait
                        user.msgs_to_delete_on_ready.append(msg_id)
                        if user.posted_captcha_msg_id: # Delete the failed CAPTCHA
                            user.msgs_to_delete_on_tick.append(user.posted_captcha_msg_id)
                            user.posted_captcha_msg_id = None
                else:
                    user.captcha_state                     = CaptchaState.Wait
                    user.captcha_failed_next_attempt_at_ts = now

            if user.captcha_state == CaptchaState.Wait:
                if now >= user.captcha_failed_next_attempt_at_ts:
                    user.captcha_attempts += 1
                    user.captcha_state     = CaptchaState.Ready

            if user.captcha_state == CaptchaState.Solved:
                s_remaining: float = user.captcha_solved_grant_access_at_ts - now
                if not user.captcha_solved_welcome_msg_shown:
                    welcome_message: str   = ""
                    if s_remaining <= 0:
                        welcome_message = f"Congratulations! You can now read and send messages in '{room_name}'."
                    else:
                        welcome_message = f"Congratulations! You will be able to read and send messages in {int(s_remaining)} seconds."

                    if self.post_message(room_token, welcome_message, whisper_target=session_id, no_plugins=True):
                        user.captcha_solved_welcome_msg_shown = True

                if s_remaining <= 0 and not user.captcha_solved_grant_access:
                    resp = self.set_user_room_permissions(room=room_token, user=session_id, read=True, write=True)
                    assert resp != SetUserRoomPermissionsResponse.InvalidArg
                    user.captcha_solved_grant_access = resp == SetUserRoomPermissionsResponse.Ok

        # NOTE: Refresh state-machine
        if 1:
            # Handle refresh requests. Users must wait `refresh_timeout_s` from last post.
            s_since_refresh: float = now - user.posted_captcha_timestamp

            # Cannot refresh on last attempt (would waste final try).
            if user.refresh_state == RefreshState.Ready or user.captcha_attempts >= self.captcha_limit:
                user.refresh_state = RefreshState.Nil

            if user.refresh_state == RefreshState.Request:
                if user.refresh_msg_id:
                    user.msgs_to_delete_on_tick.append(user.refresh_msg_id)
                    user.refresh_msg_id = None

                if user.captcha_attempts >= (self.captcha_limit - 1):
                    user.refresh_msg_id = self.post_message(room_token, CaptchaPlugin.attempt_limit_str, whisper_target=session_id, no_plugins=True)
                    if user.refresh_msg_id:
                        user.refresh_state = RefreshState.Nil
                else:
                    if s_since_refresh >= self.refresh_timeout_s:
                        user.refresh_state = RefreshState.Wait
                    else:
                        timeout:    float   = self.refresh_timeout_s - s_since_refresh
                        body                = f"You can refresh the CAPTCHA in {int(timeout)} second{'s' if timeout > 1 else ''}."
                        user.refresh_msg_id = self.post_message(room_token, body, whisper_target=session_id, no_plugins=True)
                        if user.refresh_msg_id:
                            user.refresh_state = RefreshState.Wait

            if user.refresh_state == RefreshState.Wait:
                if s_since_refresh >= self.refresh_timeout_s:
                    user.captcha_attempts += 1
                    user.refresh_state  = RefreshState.Ready

        # User exceeded attempt limit - show lockout message
        all_captchas_used = user.captcha_attempts >= self.captcha_limit
        if all_captchas_used and not user.captcha_limit_msg_shown:
            body = "You have reached the maximum number of CAPTCHA attempts. Contact an Administrator of the community for further assistance"
            if self.post_message(room_token, body, whisper_target=session_id, no_plugins=True) is not None:
                user.captcha_limit_msg_shown = True

        # Determine if a new CAPTCHA should be generated
        ready_for_new_captcha = False
        if (user.refresh_state == RefreshState.Ready or  user.captcha_state == CaptchaState.Ready) or \
           (user.refresh_state == RefreshState.Nil   and user.captcha_state == CaptchaState.Nil  and user.posted_captcha_msg_id == None):
           if not all_captchas_used:
               ready_for_new_captcha = True

        # Delete old messages before posting new CAPTCHA. Messages are queued for deleted and
        # retried until successful.
        if ready_for_new_captcha or all_captchas_used or user.captcha_state == CaptchaState.Solved:
            if user.posted_captcha_msg_id: # Delete old CAPTCHA
                user.msgs_to_delete_on_tick.append(user.posted_captcha_msg_id)
                user.posted_captcha_msg_id = None

            if user.refresh_msg_id: # Delete refresh message
                user.msgs_to_delete_on_tick.append(user.refresh_msg_id)
                user.refresh_msg_id = None

            user.msgs_to_delete_on_tick.extend(user.msgs_to_delete_on_ready)
            user.msgs_to_delete_on_ready.clear()

        for msg_id in user.reactions_to_delete_on_tick:
            self.remove_reactions(room_token, msg_id, self.refresh_emoji)
        user.reactions_to_delete_on_tick.clear()

        if self.delete_messages(user.msgs_to_delete_on_tick):
            user.msgs_to_delete_on_tick.clear()

        if user.posted_captcha_msg_id:
            self._ensure_refresh_emoji_on_captcha(room_token, user, user.posted_captcha_msg_id)

        if not ready_for_new_captcha or user.captcha_state == CaptchaState.Solved:
            return oxenc.bt_serialize("OK")

        return self._post_challenge(room_token, session_id, room_name)

    def _post_challenge(self, room_token: bytes, session_id: SessionID, room_name: str) -> bt_value:
        """Generate and post new CAPTCHA challenge to user."""
        user: UserCaptchaState                    = self.get_or_make_user(session_id, room_token)
        user.posted_captcha_msg_id                = None
        user.posted_captcha_refresh_emoji_applied = False

        user.posted_captcha           = self.captcha_manager.refresh();
        user.posted_captcha_timestamp = time()

        captcha_attachment_metadata: dict[str, typing.Any] | None = self.upload_file(user.posted_captcha.file_path, room_token)
        if not captcha_attachment_metadata:
            log.error(f"Failed to create a CAPTCHA for user 0x{session_id.hex()}: CAPTCHA file upload failed")
            user.clear_posted_captcha()
            return oxenc.bt_serialize("ERROR");

        captchas_remaining: int  = self.captcha_limit - user.captcha_attempts
        body: str = (f"Solve this CAPTCHA to read and send messages in {room_name}.\n\n"
                     f"React to this message with the emoji shown in the image.\n\n")

        if captchas_remaining > 1:
            body += (f"You can refresh the CAPTCHA every {self.refresh_timeout_s} seconds by reacting with {self.refresh_emoji}. "
                     f"You have {captchas_remaining} attempt{'s' if captchas_remaining > 1 else ''} remaining.")
        else:
            body += CaptchaPlugin.attempt_limit_str

        msg_id: MessageID | None = self.post_message(room_token=room_token, body=body, whisper_target=session_id, no_plugins=True, attachments_metadata=[captcha_attachment_metadata])
        if not msg_id:
            log.error(f"Failed to create a CAPTCHA for user 0x{session_id.hex()}: Message post failed")
            user.clear_posted_captcha()
            return oxenc.bt_serialize("ERROR");

        # NOTE: Apply refresh emoji react onto message if it's not the last one
        user.posted_captcha_msg_id = msg_id
        self._ensure_refresh_emoji_on_captcha(room_token, user, msg_id)

        return oxenc.bt_serialize("OK")

    @typing.override
    def reaction_posted(self, m: oxenmq.Message):
        """Process reactions on CAPTCHA messages (answer, refresh, or incorrect)."""

        # Example reaction data:
        #  {b'is_admin': 0, b'is_mod': 0, b'msg_id': 8, b'reaction': b'\xf0\x9f\x94\x84',
        #   b'room_id': 1, b'room_name': b'foobar2', b'room_token': b'foobar2',
        #   b'session_id': b'1500784b7c2096f6ed811b25c53a63e551954ee6778c7ae4437cb01c4b01fb4a09',
        #   b'user_id': 3}
        req: dict[bytes, bt_value] = oxenc.bt_deserialize(m.dataview()[0])

        msg_id            = typing.cast(MessageID, req[b'msg_id'])
        session_id: bytes = bytes.fromhex(typing.cast(bytes, req[b'session_id']).decode('utf-8'))
        room_token        = typing.cast(bytes, req[b'room_token'])
        room_name:  str   = typing.cast(bytes, req[b'room_name']).decode('utf-8')
        reaction:   str   = typing.cast(bytes, req[b'reaction']).decode('utf-8')

        user: UserCaptchaState | None = self.get_user(session_id, room_token)
        if not user:
            log.warning(f'Reaction {reaction} from unknown user 0x{session_id.hex()} in room {room_token}')
            return

        if user.posted_captcha_msg_id == msg_id and user.posted_captcha:
            if reaction == user.posted_captcha.answer:
                user.captcha_solved_grant_access_at_ts = time() + self.write_timeout_s
                user.captcha_state                     = CaptchaState.Solved
                log.info(f"Access granted to 0x{session_id.hex()} in room '{room_token}'")
            elif reaction == self.refresh_emoji:
                user.reactions_to_delete_on_tick.append(msg_id) # Enqueue refresh emoji to be deleted
                user.refresh_state = RefreshState.Request
                log.debug(f"Refresh reacted by 0x{session_id.hex()} in room '{room_token}'")
            else:
                user.captcha_state = CaptchaState.Answer
                log.debug(f"Incorrect emoji {reaction} reacted by 0x{session_id.hex()} in room '{room_token}')")

            _ = self.tick(room_token=room_token, session_id=session_id, room_name=room_name);

def entry_point(ini: str = "captcha.ini"):
    import configparser
    from sogs import config as sogs_config

    # Setup and load config file from disk
    log.name              = 'CAPTCHA'
    cp                    = configparser.ConfigParser()
    files_read: list[str] = cp.read(ini)

    log.info(f"Loading captcha plugin config from {ini}")
    if len(files_read) != 1:
        log.warning(f"Captcha .ini config file does not exist, stopping. File was: {ini}")
        return

    # Mandatory configs fields
    sogs_pubkey_hex: str | None = cp.get('sogs', 'sogs_pubkey_hex', fallback=None)
    if not sogs_pubkey_hex:
        log.error(f"Captcha config file field 'sogs_pubkey_hex' is missing. File was: {ini}")
        return

    if sogs_pubkey_hex.startswith("0x"):
        sogs_pubkey_hex = sogs_pubkey_hex[2:]

    sogs_pubkey: bytes = b''
    try:
        sogs_pubkey = bytes.fromhex(sogs_pubkey_hex)
    except Exception as e:
        log.error(f"Captcha config file field 'sogs_pubkey_hex' was not a hex string: {sogs_pubkey_hex}")
        return

    # Overridable config fields
    key_file:          str        = cp.get   ('plugin', 'key_file',          fallback="x25519")
    display_name:      str        = cp.get   ('plugin', 'display_name',      fallback="CAPTCHA")
    limit:             int | None = cp.getint('plugin', 'limit',             fallback=None)
    write_timeout_s:   int | None = cp.getint('plugin', 'write_timeout',     fallback=None)
    refresh_timeout_s: int | None = cp.getint('plugin', 'refresh_timeout_s', fallback=None)
    retry_timeout_s:   int | None = cp.getint('plugin', 'retry_timeout_s',   fallback=None)
    sogs_address:      str        = cp.get   ('sogs',   'sogs_address',      fallback=sogs_config.OMQ_LISTEN)
    ed_privkey:        bytes      = Plugin.get_or_make_ed25519_privkey(key_file)

    import traceback
    try:
        # Instantiate the plugin and configure extra fields in the plugin
        plugin                   = CaptchaPlugin(sogs_address=sogs_address, sogs_pubkey=sogs_pubkey, ed_privkey=ed_privkey, display_name=display_name)
        plugin.captcha_limit     = limit             if limit             else plugin.captcha_limit
        plugin.retry_timeout_s   = retry_timeout_s   if retry_timeout_s   else plugin.retry_timeout_s
        plugin.refresh_timeout_s = refresh_timeout_s if refresh_timeout_s else plugin.refresh_timeout_s
        plugin.write_timeout_s   = write_timeout_s   if write_timeout_s   else plugin.write_timeout_s

        # Register the plugin to the DB. SOGs uses this DB to authenticate incoming requests as long
        # as they are signed by the x25519 key stored here. This table also contains permissions for
        # the SOGs to further discriminate the types of requests the plugin is allowed to make.
        #
        # This step is optional! If you wanted to run plugins on a separate network and to remotely
        # communicate with SOGs then you could imagine manually authorising the plugin by inserting
        # the key into the DB out-of-band.
        #
        # In this example we are running the CAPTCHA plugin on a DB that is local to the application
        # and is trusted so we authorise ourselves directly into the plugins table thus making this
        # plugin completely standalone.
        from sogs.web import app
        with app.app_context():
            import sogs.db
            with sogs.db.transaction():
                sogs.db.query("INSERT OR IGNORE INTO plugins (auth_key, global, approver, subscribe) VALUES (:key, 1, 1, 1)", key=SigningKey(plugin.x_pubkey).encode())

        plugin.run()
    except Exception:
        log.error("Exception raised in plugin. Terminating:\n{}".format(traceback.format_exc()))
        raise

