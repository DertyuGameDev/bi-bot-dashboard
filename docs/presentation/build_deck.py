"""Build editable OOXML slides from real MY screenshots. Run with uv run python."""

from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
from PIL import Image
import html
import json
import hashlib

ROOT = Path(__file__).resolve().parents[2]
B = ROOT / ".local/presentation-build"
A = ROOT / "docs/images"
BASE = Path(__file__).parent / "base"
OUT = Path(__file__).parent / "BI-бот дашборд — история Свята.pptx"
BG = "FFFFFF"
Y = "66C6F8"
FG = "15354A"
M = "52738A"
D = "EDF8FF"
LINE = "D6EAF6"
W, H = 1280, 720
AN = "http://schemas.openxmlformats.org/drawingml/2006/main"
NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
RN = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PK = "http://schemas.openxmlformats.org/package/2006/relationships"


def e(value):
    return html.escape(str(value), quote=True)


def em(value):
    return round(value * 9525)


def xml(value):
    return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' + value


files = {}
media = {}
slides = []
geom = []


def rels(rows):
    return xml(
        '<Relationships xmlns="'
        + PK
        + '">'
        + "".join(f'<Relationship Id="{i}" Type="{t}" Target="{e(tar)}"/>' for i, t, tar in rows)
        + "</Relationships>"
    )


def tr(x, y, w, h, rot=0):
    return (
        "<a:xfrm"
        + (f' rot="{round(rot * 60000)}"' if rot else "")
        + f'><a:off x="{em(x)}" y="{em(y)}"/><a:ext cx="{em(w)}" cy="{em(h)}"/></a:xfrm>'
    )


def asset(p):
    p = Path(p)
    if p not in media:
        name = f"image{len(media) + 1}{p.suffix}"
        media[p] = name
        files["ppt/media/" + name] = p.read_bytes()
    return media[p]


blank = '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'


class Slide:
    def __init__(self, title, source="", note="", bg=BG, brand=True):
        self.title = title
        self.note = note
        self.source = source
        self.bg = bg
        self.sh = []
        self.ids = 1
        self.rr = [("rId1", RN + "/slideLayout", "../slideLayouts/slideLayout1.xml")]
        slides.append(self)
        if brand:
            self.text("Puzzle RPA", 56, 25, 180, 28, 19, FG, True)
            self.shape(56, 61, 36, 3, Y)

    def ident(self):
        self.ids += 1
        return self.ids

    def rid(self, typ, target):
        r = "rId" + str(len(self.rr) + 1)
        self.rr.append((r, typ, target))
        return r

    def text(self, text, x, y, w, h, size=24, color=FG, bold=False, align="l", rot=0):
        n = self.ident()
        paras = []
        for line in text.split("\n"):
            paras.append(
                f'<a:p><a:pPr algn="{align}"><a:lnSpc><a:spcPct val="110000"/></a:lnSpc><a:spcBef><a:spcPts val="0"/></a:spcBef><a:spcAft><a:spcPts val="0"/></a:spcAft></a:pPr><a:r><a:rPr lang="ru-RU" sz="{round(size * 75)}" b="{int(bold)}" dirty="0"><a:solidFill><a:srgbClr val="{color}"/></a:solidFill><a:latin typeface="Golos Text"/><a:ea typeface="Golos Text"/><a:cs typeface="Golos Text"/></a:rPr><a:t>{e(line)}</a:t></a:r><a:endParaRPr lang="ru-RU" sz="{round(size * 75)}"/></a:p>'
            )
        self.sh.append(
            f'<p:sp><p:nvSpPr><p:cNvPr id="{n}" name="{e(text[:55])}"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr><p:spPr>{tr(x, y, w, h, rot)}<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/><a:ln><a:noFill/></a:ln></p:spPr><p:txBody><a:bodyPr wrap="square" lIns="0" tIns="0" rIns="0" bIns="0" anchor="t"><a:noAutofit/></a:bodyPr><a:lstStyle/>{"".join(paras)}</p:txBody></p:sp>'
        )
        geom.append({"slide": len(slides), "text": text, "x": x, "y": y, "w": w, "h": h, "size": size})

    def shape(self, x, y, w, h, fill=Y, kind="rect", stroke=None, sw=1, rot=0, radius=False):
        n = self.ident()
        ln = (
            f'<a:ln w="{em(sw)}"><a:solidFill><a:srgbClr val="{stroke}"/></a:solidFill></a:ln>'
            if stroke
            else "<a:ln><a:noFill/></a:ln>"
        )
        fillxml = f'<a:solidFill><a:srgbClr val="{fill}"/></a:solidFill>' if fill else "<a:noFill/>"
        av = '<a:gd name="adj" fmla="val 7000"/>' if kind == "roundRect" else ""
        self.sh.append(
            f'<p:sp><p:nvSpPr><p:cNvPr id="{n}" name="{kind} {n}"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr>{tr(x, y, w, h, rot)}<a:prstGeom prst="{kind}"><a:avLst>{av}</a:avLst></a:prstGeom>{fillxml}{ln}</p:spPr></p:sp>'
        )

    def line(self, x1, y1, x2, y2, color=FG, sw=2):
        n = self.ident()
        x = min(x1, x2)
        y = min(y1, y2)
        w = abs(x2 - x1)
        h = abs(y2 - y1)
        flip = ' flipV="1"' if (x2 - x1) * (y2 - y1) < 0 else ""
        self.sh.append(
            f'<p:cxnSp><p:nvCxnSpPr><p:cNvPr id="{n}" name="Line {n}"/><p:cNvCxnSpPr/><p:nvPr/></p:nvCxnSpPr><p:spPr><a:xfrm{flip}><a:off x="{em(x)}" y="{em(y)}"/><a:ext cx="{em(w)}" cy="{em(h)}"/></a:xfrm><a:prstGeom prst="line"><a:avLst/></a:prstGeom><a:ln w="{em(sw)}"><a:solidFill><a:srgbClr val="{color}"/></a:solidFill></a:ln></p:spPr><p:style><a:lnRef idx="0"><a:srgbClr val="{color}"/></a:lnRef><a:fillRef idx="0"><a:srgbClr val="{color}"/></a:fillRef><a:effectRef idx="0"><a:srgbClr val="{color}"/></a:effectRef><a:fontRef idx="minor"><a:srgbClr val="{color}"/></a:fontRef></p:style></p:cxnSp>'
        )

    def image(self, p, x, y, w, h, crop=None, rot=0, frame=False):
        p = Path(p)
        if frame:
            self.shape(x - 8, y - 8, w + 16, h + 29, FG, rot=rot)
        if crop:
            cx, cy, cw, ch = crop
            target = B / "crops" / (hashlib.sha256((str(p) + str(crop)).encode()).hexdigest()[:18] + ".png")
            target.parent.mkdir(exist_ok=True)
            if not target.exists():
                Image.open(p).crop((cx, cy, cx + cw, cy + ch)).save(target)
            p = target
        n = self.ident()
        name = asset(p)
        rid = self.rid(RN + "/image", "../media/" + name)
        cropxml = ""
        self.sh.append(
            f'<p:pic><p:nvPicPr><p:cNvPr id="{n}" name="{e(p.stem)}"/><p:cNvPicPr><a:picLocks noChangeAspect="1"/></p:cNvPicPr><p:nvPr/></p:nvPicPr></p:nvPicPr><p:blipFill><a:blip r:embed="{rid}"/>{cropxml}<a:stretch><a:fillRect/></a:stretch></p:blipFill><p:spPr>{tr(x, y, w, h, rot)}<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>'.replace(
                "<p:nvPr/></p:nvPicPr></p:nvPicPr>", "<p:nvPr/></p:nvPicPr>"
            )
        )

    def heading(self, a, b="", x=56, y=105, w=1168, size=53):
        self.text(a, x, y, w, size * 1.2, size, FG)
        if b:
            self.text(b, x, y + size * 1.1, w, size * 1.2, size, "178CC7" if self.bg != Y else FG)

    def pill(self, text, x, y, w=205, fill=Y, color=BG):
        self.shape(x, y, w, 35, fill, "roundRect")
        self.text(text, x + 14, y + 7, w - 28, 22, 16, color, True)

    def icon(self, name, x, y, z=48, color=Y):
        sw = max(2, z / 16)
        if name == "calendar":
            self.shape(x + z * 0.12, y + z * 0.18, z * 0.76, z * 0.69, None, "roundRect", color, sw)
            self.line(x + z * 0.12, y + z * 0.39, x + z * 0.88, y + z * 0.39, color, sw)
            for a in [0.31, 0.67]:
                self.line(x + z * a, y + z * 0.08, x + z * a, y + z * 0.28, color, sw)
            for a, b in [(0.3, 0.55), (0.52, 0.55), (0.3, 0.73), (0.52, 0.73)]:
                self.shape(x + z * a, y + z * b, z * 0.09, z * 0.08, color)
        elif name == "person":
            self.shape(x + z * 0.34, y + z * 0.06, z * 0.32, z * 0.32, color, "ellipse")
            self.shape(x + z * 0.14, y + z * 0.46, z * 0.72, z * 0.44, color, "roundRect")
        elif name == "ticket":
            self.shape(x + z * 0.08, y + z * 0.2, z * 0.84, z * 0.58, None, "roundRect", color, sw)
            self.line(x + z * 0.62, y + z * 0.2, x + z * 0.62, y + z * 0.78, color, sw)
        elif name == "check":
            self.line(x + z * 0.15, y + z * 0.53, x + z * 0.4, y + z * 0.78, color, sw * 1.5)
            self.line(x + z * 0.4, y + z * 0.78, x + z * 0.9, y + z * 0.21, color, sw * 1.5)
        elif name == "screen":
            self.shape(x + z * 0.08, y + z * 0.1, z * 0.84, z * 0.59, None, "roundRect", color, sw)
            self.line(x + z * 0.5, y + z * 0.69, x + z * 0.5, y + z * 0.88, color, sw)
            self.line(x + z * 0.28, y + z * 0.88, x + z * 0.72, y + z * 0.88, color, sw)
        elif name == "play":
            self.shape(x + z * 0.24, y + z * 0.12, z * 0.58, z * 0.75, color, "triangle", rot=90)
        elif name == "copy":
            self.shape(x + z * 0.12, y + z * 0.13, z * 0.58, z * 0.58, None, "roundRect", color, sw)
            self.shape(x + z * 0.3, y + z * 0.3, z * 0.58, z * 0.58, BG, "roundRect", color, sw)
        elif name == "plus":
            self.line(x + z * 0.16, y + z * 0.5, x + z * 0.84, y + z * 0.5, color, sw * 1.4)
            self.line(x + z * 0.5, y + z * 0.16, x + z * 0.5, y + z * 0.84, color, sw * 1.4)
        elif name == "clock":
            self.shape(x + z * 0.1, y + z * 0.1, z * 0.8, z * 0.8, None, "ellipse", color, sw)
            self.line(x + z * 0.5, y + z * 0.5, x + z * 0.5, y + z * 0.25, color, sw)
            self.line(x + z * 0.5, y + z * 0.5, x + z * 0.7, y + z * 0.64, color, sw)
        elif name == "key":
            self.shape(x + z * 0.06, y + z * 0.12, z * 0.43, z * 0.43, None, "ellipse", color, sw)
            self.line(x + z * 0.43, y + z * 0.47, x + z * 0.88, y + z * 0.9, color, sw * 1.4)
            self.line(x + z * 0.67, y + z * 0.69, x + z * 0.79, y + z * 0.57, color, sw * 1.4)
        else:
            self.shape(x + z * 0.13, y + z * 0.13, z * 0.74, z * 0.74, color, "diamond")

    def card(self, title, desc, x, y, w, h=135, icon="check"):
        self.shape(x, y, w, h, Y, "roundRect")
        self.shape(x + 18, y + 21, 46, 46, BG, "roundRect")
        self.icon(icon, x + 25, y + 28, 32, Y)
        self.text(title, x + 81, y + 21, w - 97, 35, 25, BG, True)
        self.text(desc, x + 81, y + 64, w - 100, h - 70, 19, BG)

    def note_text(self, text, x, y, w, h=85, color=M, size=23):
        self.text(text, x, y, w, h, size, color)

    def serialize(self, i):
        self.rid(RN + "/notesSlide", f"../notesSlides/notesSlide{i}.xml")
        return xml(
            f'<p:sld xmlns:a="{AN}" xmlns:r="{RN}" xmlns:p="{NS}"><p:cSld name="{e(self.title)}"><p:bg><p:bgPr><a:solidFill><a:srgbClr val="{self.bg}"/></a:solidFill><a:effectLst/></p:bgPr></p:bg><p:spTree>{blank}{"".join(self.sh)}</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>'
        )


# Story content and final package assembly below.
def fit(s, name, x, y, w, h):
    p = A / name
    iw, ih = Image.open(p).size
    k = min(w / iw, h / ih)
    s.image(p, x + (w - iw * k) / 2, y + (h - ih * k) / 2, iw * k, ih * k)


def portrait(s, x, y, size, thinking=False):
    s.image(A / ("svyat-thinking.png" if thinking else "svyat-working.png"), x, y, size, size)


def copy_text(s, text, x, y, w, h=180, size=25, color=M):
    s.text(text, x, y, w, h, size, color)


story = json.loads((Path(__file__).parent / "storyboard.json").read_text())
for item in story:
    kind = item["kind"]
    title = item["title"]
    body = item.get("body", "")
    s = Slide(
        title,
        note=item["note"],
        bg=Y if kind in ["cover", "chapter", "closing"] else BG,
        brand=kind != "cover",
    )
    if kind == "cover":
        s.text("Puzzle RPA", 56, 36, 480, 42, 29, FG, True)
        s.text("BI-бот дашборд", 56, 114, 580, 46, 28, FG)
        s.text(title, 56, 229, 760, 200, 72, FG, True)
        s.text(
            "Свят хочет понять, как идут продажи.\nИ начинает с обычного вопроса.", 60, 533, 650, 100, 29, FG
        )
        portrait(s, 824, 206, 390, True)
    elif kind == "journey":
        s.heading(*title.split("\n"), size=50)
        for i, (name, desc) in enumerate(item["steps"]):
            x = 56 + i * 302
            s.text(f"{i + 1:02}", x, 327, 100, 82, 61, "178CC7")
            if i < 3:
                s.line(x + 116, 372, x + 266, 372, LINE, 2)
            s.text(name, x, 444, 280, 52, 31, FG, True)
            copy_text(s, desc, x, 522, 260, 108, 23)
    elif kind == "chapter":
        s.text(title, 56, 177, 762, 200, 65, FG, True)
        copy_text(s, body, 60, 472, 630, 150, 29, FG)
        portrait(s, 837, 243, 363)
    elif kind == "screen":
        s.heading(*title.split("\n"), w=1168, size=49)
        copy_text(s, body, 58, 303, 360, 190, 25)
        if item.get("portrait"):
            portrait(s, 68, 519, 145)
        fit(s, item["image"], 449, 257, 775, 424)
    elif kind == "phone":
        s.heading(*title.split("\n"), w=720, size=51)
        copy_text(s, body, 58, 329, 620, 170, 26)
        portrait(s, 68, 540, 140)
        fit(s, item["image"], 850, 100, 350, 580)
    elif kind == "interaction":
        s.text(title.replace("\n", " "), 56, 107, 1168, 77, 44, FG, True)
        copy_text(s, body, 58, 190, 1164, 59, 22)
        s.text("Зум колесом мыши", 56, 278, 565, 43, 25, FG, True)
        s.text("Точные значения", 659, 278, 565, 43, 25, FG, True)
        fit(s, item["image"], 56, 333, 565, 345)
        fit(s, item["second_image"], 659, 333, 565, 345)
    elif kind == "full":
        s.text(title.replace("\n", " "), 56, 107, 1168, 77, 44, FG, True)
        copy_text(s, body, 58, 190, 1164, 59, 22)
        fit(s, item["image"], 56, 263, 1168, 415)
    elif kind == "kpis":
        s.heading(*title.split("\n"), size=51)
        entries = [
            ("Общий план", "1,106 млрд ₽"),
            ("Общий факт", "1,003 млрд ₽"),
            ("Выполнение", "90,71%"),
            ("Отклонение", "−102,7 млн ₽"),
        ]
        for i, (label, value) in enumerate(entries):
            x = 56 + i * 301
            s.text(label, x, 328, 280, 40, 23, M)
            s.text(value, x, 389, 286, 68, 39, "178CC7" if i != 3 else FG, True)
            s.line(x, 483, x + 264, 483, LINE, 2)
        fit(s, item["image"], 56, 528, 1168, 130)
    elif kind == "tech":
        s.heading(*title.split("\n"), size=51)
        for i, (name, desc) in enumerate(item["steps"]):
            x = 56 + i * 405
            s.text(f"{i + 1:02}", x, 306, 200, 99, 72, "178CC7")
            s.text(name, x, 440, 360, 59, 35, FG, True)
            copy_text(s, desc, x, 517, 360, 114, 24)
            if i < 2:
                s.line(x + 223, 356, x + 351, 356, "79CEFA", 3)
    elif kind == "launch":
        s.heading(*title.split("\n"), size=53)
        copy_text(s, body, 58, 307, 1100, 100, 28)
        s.shape(56, 467, 1168, 94, D, "roundRect")
        s.text("./scripts/docker_start.sh", 82, 492, 1110, 54, 34, FG, True)
        copy_text(s, "Docker Compose · инструкция в README · код на GitHub", 58, 600, 1150, 46, 24)
        s.text("github.com/DertyuGameDev/bi-bot-dashboard", 58, 654, 1150, 31, 19, "178CC7")
    elif kind == "closing":
        s.text(title, 56, 167, 779, 220, 65, FG, True)
        copy_text(s, body, 60, 477, 680, 154, 29, FG)
        portrait(s, 842, 220, 363)

# Reuse the valid blank native Office parts; all authored text stays editable.
for part in BASE.rglob("*"):
    if part.is_file():
        files[str(part.relative_to(BASE))] = part.read_bytes()
for i, s in enumerate(slides, 1):
    files[f"ppt/slides/slide{i}.xml"] = s.serialize(i)
    files[f"ppt/slides/_rels/slide{i}.xml.rels"] = rels(s.rr)
    note = (
        "Сценарии: ТЗ Puzzle RPA «Чат-бот с интерактивной визуализацией данных» и приложенные уточнения. "
        "Структура: предоставленная «ТФлоу — третья версия. История хакатона». "
        "Скриншоты: отдельный MY на PostgreSQL, 13 ноября 2026; исходные данные — тестовый архив кейса. "
        + s.note
    )
    notes = f'<p:sp><p:nvSpPr><p:cNvPr id="2" name="Заметки докладчика"/><p:cNvSpPr txBox="1"/><p:nvPr><p:ph type="body" idx="1"/></p:nvPr></p:nvSpPr><p:spPr/><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr lang="ru-RU" sz="1200"><a:latin typeface="Golos Text"/></a:rPr><a:t>{e(note)}</a:t></a:r></a:p></p:txBody></p:sp>'
    files[f"ppt/notesSlides/notesSlide{i}.xml"] = xml(
        f'<p:notes xmlns:a="{AN}" xmlns:r="{RN}" xmlns:p="{NS}"><p:cSld><p:spTree>{blank}{notes}</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:notes>'
    )
    files[f"ppt/notesSlides/_rels/notesSlide{i}.xml.rels"] = rels(
        [("rId1", RN + "/slide", f"../slides/slide{i}.xml")]
    )
pr = [("rId1", RN + "/slideMaster", "slideMasters/slideMaster1.xml")] + [
    (f"rId{i + 1}", RN + "/slide", f"slides/slide{i}.xml") for i in range(1, len(slides) + 1)
]
files["ppt/presentation.xml"] = xml(
    f'<p:presentation xmlns:a="{AN}" xmlns:r="{RN}" xmlns:p="{NS}"><p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst><p:sldIdLst>'
    + "".join(f'<p:sldId id="{255 + i}" r:id="rId{i + 1}"/>' for i in range(1, len(slides) + 1))
    + f'</p:sldIdLst><p:sldSz cx="{em(W)}" cy="{em(H)}" type="screen16x9"/><p:notesSz cx="6858000" cy="9144000"/><p:defaultTextStyle><a:defPPr><a:defRPr><a:latin typeface="Golos Text"/></a:defRPr></a:defPPr></p:defaultTextStyle></p:presentation>'
)
files["ppt/_rels/presentation.xml.rels"] = rels(pr)
files["_rels/.rels"] = rels([("rId1", RN + "/officeDocument", "ppt/presentation.xml")])
overrides = (
    [
        ("/ppt/presentation.xml", "presentation.main"),
        ("/ppt/slideMasters/slideMaster1.xml", "slideMaster"),
        ("/ppt/slideLayouts/slideLayout1.xml", "slideLayout"),
    ]
    + [(f"/ppt/slides/slide{i}.xml", "slide") for i in range(1, len(slides) + 1)]
    + [(f"/ppt/notesSlides/notesSlide{i}.xml", "notesSlide") for i in range(1, len(slides) + 1)]
)
files["[Content_Types].xml"] = xml(
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Default Extension="png" ContentType="image/png"/><Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>'
    + "".join(
        f'<Override PartName="{p}" ContentType="application/vnd.openxmlformats-officedocument.presentationml.{t}+xml"/>'
        for p, t in overrides
    )
    + "</Types>"
)
B.mkdir(parents=True, exist_ok=True)
draft = B / "candidate.pptx"
with ZipFile(draft, "w", ZIP_DEFLATED) as z:
    for k, v in files.items():
        z.writestr(k, v)
(B / "geometry.json").write_text(json.dumps(geom, ensure_ascii=False, indent=2))
print(json.dumps({"draft": str(draft), "slides": len(slides), "bytes": draft.stat().st_size}))
