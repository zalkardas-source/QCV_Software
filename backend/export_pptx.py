import io
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE
from pptx.dml.color import RGBColor

# ── Corporate Color Palette ──────────────────────────────────────────
CORP_BLUE      = RGBColor(0, 86, 156)     # #00569c  – primary brand
CORP_BLUE_DARK = RGBColor(0, 58, 107)     # #003a6b  – dark accent
CORP_BLUE_LIGHT = RGBColor(230, 240, 250) # #e6f0fa  – light tint
WHITE          = RGBColor(255, 255, 255)
NEAR_BLACK     = RGBColor(30, 30, 35)
DARK_GREY      = RGBColor(65, 65, 75)
MID_GREY       = RGBColor(120, 120, 130)
LIGHT_GREY     = RGBColor(220, 220, 225)
SKILL_BAR_BG   = RGBColor(225, 230, 238)

# ── Dimensions (Widescreen 13.333 × 7.5 in) ─────────────────────────
SLIDE_W = Inches(13.333)
SLIDE_H = Inches(7.5)

# ── Helpers ──────────────────────────────────────────────────────────
def _add_rect(slide, left, top, width, height, fill_rgb, border=False):
    """Add a filled rectangle shape."""
    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, left, top, width, height)
    shape.fill.solid()
    shape.fill.fore_color.rgb = fill_rgb
    if not border:
        shape.line.fill.background()
    return shape

def _add_text(slide, left, top, width, height, text, font_size=11,
              bold=False, italic=False, color=NEAR_BLACK, align=PP_ALIGN.LEFT,
              font_name="Calibri", word_wrap=True):
    """Add a simple text box and return the paragraph for chaining."""
    txBox = slide.shapes.add_textbox(left, top, width, height)
    tf = txBox.text_frame
    tf.word_wrap = word_wrap
    p = tf.paragraphs[0]
    p.text = str(text) if text else ""
    p.font.size = Pt(font_size)
    p.font.bold = bold
    p.font.italic = italic
    p.font.color.rgb = color
    p.font.name = font_name
    p.alignment = align
    return tf

def _add_skill_bar(slide, left, top, width, label, rating, max_rating=5):
    """Draw a labelled horizontal skill bar with filled/unfilled segments."""
    bar_h = Inches(0.18)
    label_w = Inches(2.2)
    bar_w = width - label_w - Inches(0.15)

    # Label
    _add_text(slide, left, top - Emu(Inches(0.02).emu), label_w, Inches(0.25),
              label, font_size=9, color=DARK_GREY, bold=True)

    # Background bar (full width)
    bar_left = left + label_w + Inches(0.15)
    _add_rect(slide, bar_left, top, bar_w, bar_h, SKILL_BAR_BG)

    # Filled portion
    try:
        r = int(rating)
    except (ValueError, TypeError):
        r = 0
    if r > 0:
        filled_w = int(bar_w * (r / max_rating))
        if filled_w > 0:
            _add_rect(slide, bar_left, top, filled_w, bar_h, CORP_BLUE)


def create_pptx_summary(data: dict) -> bytes:
    """Creates a polished, professional 1-page PowerPoint CV summary."""

    prs = Presentation()
    prs.slide_width  = SLIDE_W
    prs.slide_height = SLIDE_H

    slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank layout

    personal = data.get("personal_information", {})
    full_name = personal.get("full_name", "Candidate Profile")

    # ╔══════════════════════════════════════════════════════════════╗
    # ║  HEADER BAR – dark blue strip across the top                ║
    # ╚══════════════════════════════════════════════════════════════╝
    header_h = Inches(1.35)
    _add_rect(slide, Emu(0), Emu(0), SLIDE_W, header_h, CORP_BLUE_DARK)

    # Candidate name (large, white)
    _add_text(slide, Inches(0.7), Inches(0.2), Inches(8), Inches(0.6),
              full_name, font_size=30, bold=True, color=WHITE,
              font_name="Calibri Light")

    # Contact row (white, smaller)
    contact_parts = []
    if personal.get("email"):    contact_parts.append(f"✉  {personal['email']}")
    if personal.get("phone"):    contact_parts.append(f"✆  {personal['phone']}")
    if personal.get("location"): contact_parts.append(f"⌂  {personal['location']}")
    if contact_parts:
        _add_text(slide, Inches(0.7), Inches(0.82), Inches(10), Inches(0.35),
                  "     |     ".join(contact_parts),
                  font_size=10, color=RGBColor(200, 215, 235), font_name="Calibri")

    # Brand tag (right side of header)
    _add_text(slide, Inches(9.5), Inches(0.25), Inches(3.5), Inches(0.35),
              "Quatelio CVision", font_size=11, bold=True, color=WHITE,
              align=PP_ALIGN.RIGHT, font_name="Calibri")

    # Thin accent stripe below header
    _add_rect(slide, Emu(0), header_h, SLIDE_W, Inches(0.04), CORP_BLUE)

    # ╔══════════════════════════════════════════════════════════════╗
    # ║  PERSONAL DETAILS – small info badges row                   ║
    # ╚══════════════════════════════════════════════════════════════╝
    info_y = Inches(1.6)
    info_parts = []
    if personal.get("age_or_dob"):     info_parts.append(f"Born: {personal['age_or_dob']}")
    if personal.get("nationality"):    info_parts.append(f"Nationality: {personal['nationality']}")
    if personal.get("marital_status"): info_parts.append(f"Status: {personal['marital_status']}")
    if personal.get("linkedin"):       info_parts.append(f"LinkedIn: {personal['linkedin']}")

    if info_parts:
        _add_text(slide, Inches(0.7), info_y, Inches(12), Inches(0.3),
                  "   •   ".join(info_parts),
                  font_size=9, color=MID_GREY, italic=True, font_name="Calibri")

    # ╔══════════════════════════════════════════════════════════════╗
    # ║  EXECUTIVE SUMMARY                                          ║
    # ╚══════════════════════════════════════════════════════════════╝
    summary = data.get("small_summary", "")
    section_y = Inches(2.05)
    if summary:
        _add_text(slide, Inches(0.7), section_y, Inches(3), Inches(0.3),
                  "EXECUTIVE SUMMARY", font_size=11, bold=True, color=CORP_BLUE,
                  font_name="Calibri")
        # Blue underline
        _add_rect(slide, Inches(0.7), section_y + Inches(0.28), Inches(1.6), Inches(0.025), CORP_BLUE)

        tf_sum = _add_text(slide, Inches(0.7), section_y + Inches(0.4),
                           Inches(11.9), Inches(0.85),
                           summary, font_size=10, color=DARK_GREY, font_name="Calibri")
        tf_sum.paragraphs[0].space_after = Pt(4)

    # ╔══════════════════════════════════════════════════════════════╗
    # ║  TWO-COLUMN LAYOUT                                          ║
    # ║  Left: Professional Experience    Right: Core Skills         ║
    # ╚══════════════════════════════════════════════════════════════╝
    col_y = Inches(3.35)
    left_col_x = Inches(0.7)
    left_col_w = Inches(7.6)
    right_col_x = Inches(8.8)
    right_col_w = Inches(4.0)

    # ── Left Column: Professional Experience ─────────────────────
    projects = data.get("projects", [])
    _add_text(slide, left_col_x, col_y, Inches(4), Inches(0.3),
              "PROFESSIONAL EXPERIENCE", font_size=11, bold=True, color=CORP_BLUE,
              font_name="Calibri")
    _add_rect(slide, left_col_x, col_y + Inches(0.28), Inches(2.0), Inches(0.025), CORP_BLUE)

    proj_y = col_y + Inches(0.45)
    for proj in projects:
        p_name = proj.get("name", "Position")
        p_dur  = proj.get("duration", "")
        p_desc = proj.get("description", "")

        # Vertical accent bar
        _add_rect(slide, left_col_x, proj_y, Inches(0.04), Inches(0.65), CORP_BLUE)

        # Title
        _add_text(slide, left_col_x + Inches(0.18), proj_y - Inches(0.02),
                  Inches(5.5), Inches(0.25),
                  p_name, font_size=11, bold=True, color=NEAR_BLACK, font_name="Calibri")

        # Duration (right-aligned next to title)
        if p_dur:
            _add_text(slide, left_col_x + Inches(5.5), proj_y - Inches(0.02),
                      Inches(2), Inches(0.25),
                      p_dur, font_size=9, color=MID_GREY, align=PP_ALIGN.RIGHT,
                      font_name="Calibri", italic=True)

        # Description
        if p_desc:
            # Truncate long descriptions to keep it on one page
            desc_text = p_desc[:220] + ("..." if len(p_desc) > 220 else "")
            _add_text(slide, left_col_x + Inches(0.18), proj_y + Inches(0.22),
                      Inches(7.2), Inches(0.45),
                      desc_text, font_size=9, color=DARK_GREY, font_name="Calibri")

        proj_y += Inches(0.75)

    # ── Right Column: Core Skills ────────────────────────────────
    skills = data.get("skill_matrix", [])
    _add_text(slide, right_col_x, col_y, Inches(3), Inches(0.3),
              "CORE SKILLS", font_size=11, bold=True, color=CORP_BLUE,
              font_name="Calibri")
    _add_rect(slide, right_col_x, col_y + Inches(0.28), Inches(1.2), Inches(0.025), CORP_BLUE)

    skill_y = col_y + Inches(0.5)
    for skill in skills:
        s_name  = skill.get("skill", "")
        s_rating = skill.get("rating", "")
        if s_name:
            _add_skill_bar(slide, right_col_x, skill_y, right_col_w, s_name, s_rating)
            skill_y += Inches(0.3)

    # ╔══════════════════════════════════════════════════════════════╗
    # ║  FOOTER                                                      ║
    # ╚══════════════════════════════════════════════════════════════╝
    footer_h = Inches(0.35)
    footer_y = SLIDE_H - footer_h
    _add_rect(slide, Emu(0), footer_y, SLIDE_W, footer_h, CORP_BLUE_DARK)
    _add_text(slide, Inches(0.7), footer_y + Inches(0.04), Inches(5), Inches(0.25),
              "Generated by Quatelio CVision", font_size=8, color=RGBColor(160, 180, 200),
              font_name="Calibri", italic=True)
    _add_text(slide, Inches(8), footer_y + Inches(0.04), Inches(5), Inches(0.25),
              "Confidential", font_size=8, color=RGBColor(160, 180, 200),
              align=PP_ALIGN.RIGHT, font_name="Calibri", italic=True)

    # ── Save ─────────────────────────────────────────────────────
    ppt_stream = io.BytesIO()
    prs.save(ppt_stream)
    ppt_stream.seek(0)
    return ppt_stream.read()
