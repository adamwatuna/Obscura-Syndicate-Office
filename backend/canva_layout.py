"""Editable Canva import layouts planned by the assigned designer."""
import io
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import MSO_AUTO_SIZE
from pptx.util import Inches, Pt


class DesignBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=42)
    text: str = Field(min_length=1, max_length=180)


class DesignPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["cover", "chapter", "guide"]
    title: str = Field(min_length=1, max_length=90)
    subtitle: str = Field(max_length=170)
    blocks: list[DesignBlock] = Field(max_length=5)


class Design(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    layout: Literal["editorial", "storyboard", "workbook"]
    pages: list[DesignPage] = Field(min_length=1, max_length=18)


class DesignPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artwork_prompt: str = Field(max_length=1400)
    designs: list[Design] = Field(min_length=1, max_length=3)


THEMES = {
    "editorial": ("FAF2E4", "30231D", "9A263E", "F2E2CE", "Georgia"),
    "storyboard": ("151B26", "F3F6FC", "FFC16F", "222C3A", "Arial"),
    "workbook": ("EDF0DF", "253523", "546833", "DAE4C9", "Georgia"),
}


def render(design, artwork):
    deck = Presentation()
    deck.slide_width, deck.slide_height = Inches(8.27), Inches(11.69)
    background, ink, accent, panel, heading_font = THEMES[design.layout]
    color = RGBColor.from_string

    def text(slide, value, x, y, width, height, size, *, tone=ink,
             font="Arial", bold=False, fill=None):
        shape = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(width), Inches(height))
        if fill:
            shape.fill.solid()
            shape.fill.fore_color.rgb = color(fill)
        frame = shape.text_frame
        frame.word_wrap = True
        frame.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
        frame.margin_left = Pt(9 if fill else 0)
        frame.margin_right = Pt(9 if fill else 0)
        frame.margin_top = Pt(4)
        frame.margin_bottom = Pt(2)
        paragraph = frame.paragraphs[0]
        paragraph.text = value
        paragraph.font.name = font
        paragraph.font.size = Pt(size)
        paragraph.font.bold = bold
        paragraph.font.color.rgb = color(tone)

    def photo(slide, x, y, width, height):
        from PIL import Image
        with Image.open(io.BytesIO(artwork)) as image:
            original_width, original_height = image.size
        placed = slide.shapes.add_picture(io.BytesIO(artwork), Inches(x), Inches(y),
                                          Inches(width), Inches(height))
        source_ratio, frame_ratio = original_width / original_height, width / height
        if source_ratio > frame_ratio:
            placed.crop_left = placed.crop_right = (1 - frame_ratio / source_ratio) / 2
        else:
            placed.crop_top = placed.crop_bottom = (1 - source_ratio / frame_ratio) / 2

    for page in design.pages:
        slide = deck.slides.add_slide(deck.slide_layouts[6])
        slide.background.fill.solid()
        slide.background.fill.fore_color.rgb = color(background)
        text(slide, design.name, .62, .42, 7.03, .44, 16, tone=accent, bold=True)
        text(slide, page.title, .62, .94, 7.03, 1.45, 40, font=heading_font, bold=True)
        if page.kind == "cover":
            if artwork:
                photo(slide, .62, 2.58, 7.03, 6.6)
            text(slide, page.subtitle, .62, 9.38 if artwork else 2.65, 7.03, 1.45, 24)
            continue

        text(slide, page.subtitle, .62, 2.36, 7.03, 1.0, 21)
        rows_at = 3.43
        if artwork and (page.kind == "chapter" or design.layout != "storyboard"):
            height = 3.02 if not page.blocks else 2.55
            photo(slide, .62, rows_at, 7.03, height)
            rows_at += height + .22

        row_size = min(1.36, (11.27 - rows_at) / max(1, len(page.blocks)))
        for index, block in enumerate(page.blocks):
            y = rows_at + index * row_size
            if design.layout == "storyboard" and artwork and page.kind == "guide":
                photo(slide, .62, y, 1.62, row_size - .14)
                left, width = 2.47, 5.18
            else:
                left, width = .62, 7.03
            fill = panel if design.layout == "workbook" else None
            text(slide, block.label, left, y, width, .37, 17, tone=accent, bold=True, fill=fill)
            text(slide, block.text, left, y + .38, width, row_size - .41, 17, fill=fill)

    output = io.BytesIO()
    deck.save(output)
    return output.getvalue()
