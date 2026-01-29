import math
import random
import time
import asyncio
import os
import dataclasses
import enum
import typing

from concurrent.futures import ThreadPoolExecutor
from PIL import Image, ImageDraw, ImageFont

EMOJI_LIST: list[str] = [
        "\U0001F602",
        "\U00002764\U0000FE0F",
        "\U0001F923",
        "\U0001F44D",
        "\U0001F62D",
        "\U0001F64F",
        "\U0001F618",
        "\U0001F970",
        "\U0001F60D",
        "\U0001F60A",
        "\U0001F389",
        "\U0001F601",
        "\U0001F495",
        "\U0001F97A",
        "\U0001F605",
        "\U0001F525",
        "\U0000263A\uFE0F",
        "\U0001F926",
        "\U00002665\uFE0F",
        "\U0001F937",
        "\U0001F644",
        "\U0001F606",
        "\U0001F917",
        "\U0001F609",
        "\U0001F382",
        "\U0001F914",
        "\U0001F44F",
        "\U0001F642",
        "\U0001F633",
        "\U0001F973",
        "\U0001F60E",
        "\U0001F44C",
        "\U0001F49C",
        "\U0001F614",
        "\U0001F4AA",
        "\U00002728",
        "\U0001F496",
        "\U0001F440",
        "\U0001F60B",
        "\U0001F60F",
        "\U0001F622",
        "\U0001F449",
        "\U0001F497",
        "\U0001F629",
        "\U0001F4AF",
        "\U0001F339",
        "\U0001F49E",
        "\U0001F388",
        "\U0001F499",
        "\U0001F603",
        "\U0001F621",
        "\U0001F490",
        "\U0001F61C",
        "\U0001F648",
        "\U0001F91E",
        "\U0001F604",
        "\U0001F924",
        "\U0001F64C",
        "\U0001F92A",
        "\U00002763\uFE0F",
        "\U0001F600",
        "\U0001F48B",
        "\U0001F480",
        "\U0001F447",
        "\U0001F494",
        "\U0001F60C",
        "\U0001F493",
        "\U0001F929",
        "\U0001F643",
        "\U0001F62C",
        "\U0001F631",
        "\U0001F634",
        "\U0001F92D",
        "\U0001F610",
        "\U0001F31E",
        "\U0001F612",
        "\U0001F607",
        "\U0001F338",
        "\U0001F608",
        "\U0001F3B6",
        "\U0000270C\uFE0F",
        "\U0001F38A",
        "\U0001F975",
        "\U0001F61E",
        "\U0001F49A",
        "\U00002600\uFE0F",
        "\U0001F5A4",
        "\U0001F4B0",
        "\U0001F61A",
        "\U0001F451",
        "\U0001F381",
        "\U0001F4A5",
        "\U0001F64B",
        "\U00002639\uFE0F",
        "\U0001F611",
        "\U0001F974",
        "\U0001F448",
        "\U0001F4A9",
        "\U00002705",
        "\U0001F44B",
        "\U0001F92E",
        "\U0001F624",
        "\U0001F922",
        "\U0001F31F",
        "\U00002757",
        "\U0001F625",
        "\U0001F308",
        "\U0001F49B",
        "\U0001F61D",
        "\U0001F62B",
        "\U0001F632",
        "\U0001F595",
        "\U0000203C\uFE0F",
        "\U0001F534",
        "\U0001F33B",
        "\U0001F92F",
        "\U0001F483",
        "\U0001F44A",
        "\U0001F92C",
        "\U0001F3C3",
        "\U0001F615",
        "\U0001F441\uFE0F",
        "\U000026A1",
        "\U00002615",
        "\U0001F340",
        "\U0001F4A6",
        "\U00002B50",
        "\U0001F98B",
        "\U0001F928",
        "\U0001F33A",
        "\U0001F639",
        "\U0001F918",
        "\U0001F337",
        "\U0001F49D",
        "\U0001F4A4",
        "\U0001F91D",
        "\U0001F430",
        "\U0001F613",
        "\U0001F498",
        "\U0001F37B",
        "\U0001F61F",
        "\U0001F623",
        "\U0001F9D0",
        "\U0001F620",
        "\U0001F920",
        "\U0001F63B",
        "\U0001F319",
        "\U0001F61B",
        "\U0001F919",
        "\U0001F64A",
        "\U0001F9E1",
        "\U0001F921",
        "\U0001F92B",
        "\U0001F33C",
        "\U0001F942",
        "\U0001F637",
        "\U0001F913",
        "\U00002620\uFE0F",
        "\U0001F976",
        "\U0001F636",
        "\U0001F616",
        "\U0001F3B5",
        "\U0001F6B6",
        "\U0001F619",
        "\U0001F346",
        "\U0001F911",
        "\U0001F485",
        "\U0001F617",
        "\U0001F436",
        "\U0001F353",
        "\U0000270B",
        "\U0001F445",
        "\U0001F444",
        "\U0001F33F",
        "\U0001F6A8",
        "\U000027A1\uFE0F",
        "\U0001F4E3",
        "\U0001F91F",
        "\U0001F351",
        "\U0001F343",
        "\U0001F62E",
        "\U0001F48E",
        "\U0001F4E2",
        "\U0001F331",
        "\U000026A0\uFE0F",
        "\U0001F641",
        "\U0001F377",
        "\U0001F62A",
        "\U0001F31A",
        "\U0001F3C6",
        "\U0001F352",
        "\U00002714\uFE0F",
        "\U0001F489",
        "\U0000274C",
        "\U0001F4A2",
        "\U0001F6D2",
        "\U0001F638",
        "\U0001F43E",
        "\U0001F44E",
        "\U0001F680",
        "\U0001F3AF",
        "\U0000261D\uFE0F",
        "\U0001F37A",
        "\U0001F4CC",
        "\U0001F4F7",
        "\U0001F647",
        "\U0001F4A8",
        "\U0001F355",
        "\U0001F3E0",
        "\U0001F4F8",
        "\U0001F407",
        "\U0001F6A9",
        "\U0001F630",
        "\U0001F476",
        "\U0001F30A",
        "\U0001F415",
        "\U0001F4AB",
        "\U0001F635",
        "\U0001F3A4",
        "\U0001F3E1",
        "\U0001F940",
        "\U0001F927",
        "\U0001F37E",
        "\U0001F370",
        "\U0001F341",
        "\U0001F932",
        "\U00002B07\uFE0F",
        "\U0001F446",
        "\U0001F62F",
        "\U0000270A",
        "\U0001F48C",
        "\U00002744\uFE0F",
        "\U0001F4B8",
        "\U0001F9C1",
        "\U000026BD",
        "\U0001F1FA\U0001F1F8",
        "\U00002753",
        "\U0001F57A",
        "\U00002049\uFE0F",
        "\U0001F63A",
        "\U0001F4A7",
        "\U0001F4A3",
        "\U0001F910",
        "\U0001F34E",
        "\U0001F437",
        "\U0001F425",
        "\U0001F481",
        "\U0001F4CD",
        "\U0001F380",
        "\U0001F645",
        "\U0001F947",
        "\U0001F31D",
        "\U0001F52B",
        "\U0000260E\uFE0F",
        "\U0001F431",
        "\U0001F423",
        "\U0000271D\uFE0F",
        "\U0001F3A7",
        "\U0001F49F",
        "\U0001F479",
        "\U0001F48D",
        "\U0001F37C",
        "\U0001F590\uFE0F",
        "\U0001F4A1",
        "\U0001F63D",
        "\U0001F34A",
        "\U0001F628",
        "\U0001F36B",
        "\U0001F9E2",
        "\U0001F915",
        "\U00002618\uFE0F",
        "\U0001F6AB",
        "\U0001F3BC",
        "\U0001F43B",
        "\U0001F4F2",
        "\U0001F47B",
        "\U0001F5E3\uFE0F",
        "\U0001F47F",
        "\U0001F9DA",
        "\U0001F32E",
        "\U0001F36D",
        "\U0001F41F",
        "\U0001F438",
        "\U0001F41D",
        "\U0001F408",
        "\U0001F535",
        "\U0001F327\uFE0F",
        "\U0001F52A",
        "\U0001F627",
        "\U0001F304",
        "\U0001F63E",
        "\U00002708\uFE0F",
        "\U0001F938",
        "\U0001F4F1",
        "\U0001F347",
        "\U0001F334",
        "\U0001F422",
        "\U0001F303",
        "\U0001F47D",
        "\U0001F34C",
]

@dataclasses.dataclass
class Captcha:
    answer:        str # Answer to the CAPTCHA challenge (e.g. the emoji string)
    rel_file_path: str # Relative file path from the CWD to the CAPTCHA image
    async def generate_captcha(self, executor: ThreadPoolExecutor, width: int, height: int, font: ImageFont.FreeTypeFont):  # pyright: ignore[reportUnusedParameter]
        pass

class ShapeType(enum.Enum):
    Rectangle = 0
    Hexagon   = 1
    Circle    = 2
    Triangle  = 3
    Star      = 4
    Octagon   = 5

@dataclasses.dataclass
class Shape:
    type:  ShapeType
    color: int # 24 bit RGB
    x1:    int
    y1:    int
    x2:    int
    y2:    int

    def draw_shape(self, draw: ImageDraw.ImageDraw):
        if self.type == ShapeType.Rectangle:
            draw.rectangle(
                [self.x1, self.y1, self.x2, self.y2],
                fill=self.color
            )
        elif self.type == ShapeType.Hexagon:
            draw.regular_polygon(
                [(self.x1 + self.x2) // 2, (self.y1 + self.y2) // 2,
                 min(self.x2 - self.x1, self.y2 - self.y1) // 2],
                6,
                fill=self.color
            )
        elif self.type == ShapeType.Circle:
            # Ensure the bounding box is square to draw a perfect circle
            side_length = min(self.x2 - self.x1, self.y2 - self.y1)
            x2 = self.x1 + side_length
            y2 = self.y1 + side_length
            draw.ellipse(
                [self.x1, self.y1, x2, y2],
                fill=self.color
            )
        elif self.type == ShapeType.Triangle:
            draw.regular_polygon(
                [(self.x1 + self.x2) // 2, (self.y1 + self.y2) // 2,
                 min(self.x2 - self.x1, self.y2 - self.y1) // 2],
                3,
                fill=self.color
            )
        elif self.type == ShapeType.Star:
            # Parameters for star shape
            center_x                          = (self.x1 + self.x2) // 2
            center_y                          = (self.y1 + self.y2) // 2
            radius                            = min(self.x2 - self.x1, self.y2 - self.y1) // 2
            points: list[tuple[float, float]] = []

            for i in range(10):  # 5 points for a star, each point needs 2 coordinates (outer and inner)
                angle = i * (2 * 3.14159 / 10)
                r = radius if i % 2 == 0 else radius // 2
                x = center_x + r * math.cos(angle)
                y = center_y + r * math.sin(angle)
                points.append((x, y))
            draw.polygon(points, fill=self.color)

        elif self.type == ShapeType.Octagon:
            draw.regular_polygon(
                [(self.x1 + self.x2) // 2, (self.y1 + self.y2) // 2,
                 min(self.x2 - self.x1, self.y2 - self.y1) // 2],
                8,
                fill=self.color
            )

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

@dataclasses.dataclass
class EmojiCaptcha(Captcha):
    @typing.override
    async def generate_captcha(self, executor: ThreadPoolExecutor, width: int, height: int, font: ImageFont.FreeTypeFont, color_set: list[int] = DEFAULT_COLOUR_SET):
        assert len(color_set) >= len(ShapeType), \
            "The number of colours to select from must be greater the number of shapes we that are to be drawn"

        # NOTE: Create a new image with white background
        image: Image.Image = Image.new("RGB", (width, height), 0x626262)
        draw               = ImageDraw.ImageDraw(image)

        # NOTE: Precompute random colors
        random_colors = [random.choice(color_set) for _ in range(len(ShapeType))]
        min_size_x    = int(width * 0.3)
        min_size_y    = int(height * 0.3)

        # NOTE: Draw 6 shapes randomly on the image
        for index, shape_type in enumerate(ShapeType):
            color = random_colors[index]
            x1    = random.randint(0, width - min_size_x)
            y1    = random.randint(0, height - min_size_y)
            x2    = x1 + random.randint(min_size_x, min(width - x1, int(width / 2)))
            y2    = y1 + random.randint(min_size_y, min(height - y1, int(height / 2)))
            shape = Shape(shape_type, color, x1, y1, x2, y2)
            shape.draw_shape(draw)

        # NOTE: Ensure emoji is in bounds and fully visible w/ some padding
        emoji_margin: float = int(font.size * 0.5) + font.size;
        emoji_x:      float = float(random.randint(0, int(width - font.size)))
        emoji_y:      float = float(random.randint(0, int(height - font.size)))
        assert width - emoji_margin > 0 and height - emoji_margin > 0
        _ = draw.text((emoji_x, emoji_y), self.answer, font=font, embedded_color=True)

        # Save the image
        image_path = f"{self.rel_file_path}"
        await asyncio.get_event_loop().run_in_executor(executor, image.save, image_path)


@dataclasses.dataclass
class CaptchaManager:
    data_dir:     str           = "./.sogs/plugins/captcha"
    batch_size:   int           = 200
    captcha_list: list[Captcha] = dataclasses.field(default_factory=list)
    font_path:    str           = os.path.dirname(os.path.abspath(__file__)) + '/NotoColorEmoji.ttf'
    font_size:    int           = 109 # Suitable font size specifically for NotoColorEmoji
    width:        int           = 400
    height:       int           = 400

    def __post_init__(self):
        os.makedirs(self.data_dir, exist_ok=True)
        start_time = time.time()
        asyncio.run(self.batch_generate_captcha(self.batch_size))
        end_time = time.time()
        execution_time = end_time - start_time
        print(f"Execution time: {execution_time} seconds")

    async def batch_generate_captcha(self, count: int):
        font: ImageFont.FreeTypeFont = ImageFont.truetype(self.font_path, self.font_size, layout_engine=ImageFont.Layout.RAQM)
        with ThreadPoolExecutor(max_workers=8) as executor:
            tasks = []
            for i in range(count):
                captcha = EmojiCaptcha(random.choice(list(EMOJI_LIST)), f"{self.data_dir}/captcha_{i:03}.png")
                self.captcha_list.append(captcha)
                tasks.append(captcha.generate_captcha(executor, width=self.width, height=self.height, font=font, color_set=DEFAULT_COLOUR_SET))
            await asyncio.gather(*tasks)

    def refresh(self) -> Captcha:
        if len(self.captcha_list) == 0:
            asyncio.run(self.batch_generate_captcha(20))
        return self.captcha_list.pop()
