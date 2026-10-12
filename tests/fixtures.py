"""Entirely synthetic PSD and font fixtures, generated only in temporary folders."""

from pathlib import Path

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from PIL import Image, ImageCms
from psd_tools import PSDImage
from psd_tools.api.layers import Group, PixelLayer
from psd_tools.constants import Resource, Tag
from psd_tools.psd import engine_data as ed
from psd_tools.psd.descriptor import DescriptorBlock, RawData, String
from psd_tools.psd.image_resources import ImageResource, ResoulutionInfo
from psd_tools.psd.tagged_blocks import TaggedBlock, TypeToolObjectSetting


def synthetic_font(path):
    builder = FontBuilder(1000, isTTF=True)
    chars = list(range(32, 127)) + [196, 214, 220, 228, 246, 252, 223]
    names = [".notdef"] + [f"uni{x:04X}" for x in chars]
    builder.setupGlyphOrder(names)
    builder.setupCharacterMap({x: f"uni{x:04X}" for x in chars})
    glyphs = {}
    for name in names:
        pen = TTGlyphPen(None)
        if name != "uni0020":
            pen.moveTo((70, 0))
            pen.lineTo((500, 0))
            pen.lineTo((500, 700))
            pen.lineTo((70, 700))
            pen.closePath()
        glyphs[name] = pen.glyph()
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics({n: (600, 0) for n in names})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable(
        {
            "familyName": "Synthetic Test",
            "styleName": "Regular",
            "uniqueFontIdentifier": "SyntheticTest-Regular",
            "fullName": "Synthetic Test Regular",
            "psName": "SyntheticTest-Regular",
        }
    )
    builder.setupOS2(sTypoAscender=800, sTypoDescender=-200, usWinAscent=800, usWinDescent=200)
    builder.setupPost()
    builder.setupMaxp()
    builder.save(path)


def engine_value(value):
    if isinstance(value, dict):
        result = ed.Dict()
        for key, item in value.items():
            result[key] = engine_value(item)
        return result
    if isinstance(value, list):
        return ed.List([engine_value(x) for x in value])
    if isinstance(value, bool):
        return ed.Bool(value)
    if isinstance(value, int):
        return ed.Integer(value)
    if isinstance(value, float):
        return ed.Float(value)
    return ed.String(value)


def make_template(path, font_name="SyntheticTest-Regular", depth=8):
    psd = PSDImage.new("RGB", (240, 120), color=(255, 255, 255), depth=depth)
    PixelLayer.frompil(Image.new("RGB", (240, 120), "#142938"), psd, name="Background")
    group = Group.new(psd, name="Card")
    nested = Group.new(group, name="Content")
    pixel = PixelLayer.frompil(
        Image.new("RGB", (210, 50), "#ffffff"), nested, name="Title", left=15, top=20
    )
    style = {"Font": 0, "FontSize": 20.0, "FillColor": {"Type": 1, "Values": [1.0, 1.0, 0.0, 0.0]}}
    engine = engine_value(
        {
            "EngineDict": {
                "Editor": {"Text": "Example\r"},
                "StyleRun": {
                    "RunLengthArray": [8],
                    "RunArray": [{"StyleSheet": {"StyleSheetData": style}}],
                },
                "ParagraphRun": {
                    "RunLengthArray": [8],
                    "RunArray": [{"ParagraphSheet": {"Properties": {"Justification": 0}}}],
                },
                "Rendered": {
                    "Shapes": {
                        "WritingDirection": 0,
                        "Children": [{"Cookie": {"Photoshop": {"ShapeType": 0}}}],
                    }
                },
            },
            "ResourceDict": {"FontSet": [{"Name": font_name}]},
        }
    )
    descriptor = DescriptorBlock()
    descriptor[b"Txt "] = String("Example\r")
    descriptor[b"EngineData"] = RawData(engine)
    data = TypeToolObjectSetting(
        text_version=50,
        transform=(1.0, 0.0, 0.0, 1.0, 15.0, 20.0),
        text_data=descriptor,
        left=15,
        top=20,
        right=225,
        bottom=70,
    )
    pixel.tagged_blocks[Tag.TYPE_TOOL_OBJECT_SETTING] = TaggedBlock(
        key=Tag.TYPE_TOOL_OBJECT_SETTING, data=data
    )
    psd.image_resources[Resource.RESOLUTION_INFO] = ImageResource(
        key=Resource.RESOLUTION_INFO, data=ResoulutionInfo(300 * 65536, 1, 1, 300 * 65536, 1, 1)
    )
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    psd.image_resources[Resource.ICC_PROFILE] = ImageResource(
        key=Resource.ICC_PROFILE, data=profile
    )
    psd.save(path)
    return profile


def fixture(directory, depth=8):
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    font, template = root / "synthetic.ttf", root / "template.psd"
    synthetic_font(font)
    profile = make_template(template, depth=depth)
    job = {
        "template": str(template),
        "output": str(root / "result.png"),
        "font": str(font),
        "layers": {"Card/Content/Title": "Hello"},
    }
    return job, profile
