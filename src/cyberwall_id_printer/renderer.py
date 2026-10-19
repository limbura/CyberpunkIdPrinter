"""Рисование наклейки ID: 4 варианта наклейки. Python 3.11+, Pillow."""

from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from math import hypot, isfinite
import re
import unicodedata
from PIL import Image, ImageDraw

# Normalized segment endpoints. Seven-segment numerals use a..g only.
SEGMENTS = {
    'a': ((0,0),(1,0)), 'b': ((1,0),(1,.5)),
    'c': ((1,.5),(1,1)), 'd': ((0,1),(1,1)),
    'e': ((0,.5),(0,1)), 'f': ((0,0),(0,.5)),
    'g': ((0,.5),(1,.5)),
    'h': ((.5,0),(.5,.5)), 'i': ((.5,.5),(.5,1)),
    'j': ((0,0),(.5,.5)), 'k': ((1,0),(.5,.5)),
    'l': ((0,1),(.5,.5)), 'm': ((1,1),(.5,.5)),
    'n': ((0,.5),(.5,.5)), 'o': ((.5,.5),(1,.5)),
    'p': ((.18,0),(.18,.5)), 'q': ((.18,.5),(0,1)),
    'r': ((1,1),(1,1.13)), 's': ((0,1),(0,1.13)),
    't': ((.5,.5),(.5,1.13)),
}
DIGITS = {'0':'abcdef','1':'bc','2':'abged','3':'abgcd','4':'fgbc',
          '5':'afgcd','6':'afgecd','7':'abc','8':'abcdefg','9':'abfgcd'}
# Cyrillic uppercase, hand-designed segment glyphs. Additional strokes allow
# distinctions such as П/Л, И/Н, Б/Ь and diacritics Ё/Й.
CYRILLIC = {
    'А':'abcefg', 'Б':'afgecd', 'В':'abcdefg', 'Г':'afe',
    'Д':'apqcdsr', 'Е':'afged', 'Ё':'afged', 'Ж':'jklmhi',
    'З':'abgcd', 'И':'febclk', 'Й':'febclk', 'К':'fekm',
    'Л':'apqbc', 'М':'febcjk', 'Н':'febcg', 'О':'abcdef',
    'П':'afebc', 'Р':'abfge', 'С':'afed', 'Т':'ahi',
    'У':'jki', 'Ф':'abfg hi'.replace(' ',''), 'Х':'jklm',
    'Ц':'febcdr', 'Ч':'fgbc', 'Ш':'febcid', 'Щ':'febcidr',
    'Ъ':'anocdi', 'Ы':'fendimc', 'Ь':'fegcd', 'Э':'abgcd',
    'Ю':'feobh icd'.replace(' ',''), 'Я':'abfglc',
}
# Latin uppercase are useful for optional Latin names. Unsupported name
# characters are replaced with a visible underscore by normalize_name().
LATIN = {'A':'abcefg','B':'abcdefg','C':'afed','D':'abcdhi','E':'afged',
 'F':'afge','G':'afedcg','H':'febcg','I':'adhi','J':'bcd','K':'fekm',
 'L':'fed','M':'febcjk','N':'febcjm','O':'abcdef','P':'abfge',
 'Q':'abcdefm','R':'abfgem','S':'afgcd','T':'ahi','U':'febcd',
 'V':'felk','W':'febclm','X':'jklm','Y':'jki','Z':'adkl'}
GLYPHS = {**DIGITS, **CYRILLIC, **LATIN, '_':'d', '-':'g', '.':'', "'":'', '’':''}
BACKGROUND = tuple('abcdef') + tuple('hijklmno')  # split middle bar


class LabelMode(str, Enum):
    """Варианты наклеек"""
    ID_NAME = "id-name"					# ID и имя
    BLANK_ID_NAME = "blank-id-name"		# Пустой шаблон для ID и имени
    ID_ONLY = "id-only"					# Только ID крупно
    BLANK_ID_ONLY = "blank-id-only"		# Пустой шаблон для только ID


@dataclass(frozen=True)
class LabelConfig:
    """Настройки наклейки"""
    width_mm: float = 40.0				# Ширина наклейки
    height_mm: float = 30.0				# Высота наклейки
    dpi: float = 203.2  				# Разрешение. 8 dots/mm; calibrate against the actual printer
    inactive: bool = True				# Отображение неактивных сегментов символов
    decorations: bool = True			# Декоративные элементы наклейки
    mode: LabelMode = LabelMode.ID_NAME

    @property
    def pixels(self) -> tuple[int,int]:
        if not all(isfinite(v) and v > 0 for v in (self.width_mm, self.height_mm, self.dpi)):
            raise ValueError('Размеры и DPI должны быть положительными.')
        size = tuple(round(v*self.dpi/25.4) for v in (self.width_mm,self.height_mm))
        if min(size) < 120 or max(size) > 4096:
            raise ValueError('Допустимый размер изображения: 120..4096 точек по каждой оси.')
        return size


def format_id(value: str | int) -> str:
    text = str(value)
    if not re.fullmatch(r'[0-9]{9}', text):
        raise ValueError('ID должен содержать ровно 9 цифр ASCII, без пробелов.')
    return ' '.join(text[i:i+3] for i in range(0,9,3))


def normalize_name(name: str) -> str:
    result = ' '.join(unicodedata.normalize('NFC', name).upper().split())
    if not result:
        raise ValueError('Имя не должно быть пустым.')
    return ''.join(char if char == ' ' or char in GLYPHS else '_' for char in result)


def stroke(draw, start, end, width):
    """A solid beveled segment with small clear gaps at the endpoints."""
    x0,y0 = start; x1,y1 = end
    length = hypot(x1-x0,y1-y0)
    ux,uy = (x1-x0)/length,(y1-y0)/length
    trim = min(width*.48,length*.17)
    x0+=ux*trim; y0+=uy*trim; x1-=ux*trim; y1-=uy*trim
    nx,ny=-uy*width/2,ux*width/2
    bevel = min(width*.45,(length-2*trim)/3)
    points=[(x0,y0),(x0+ux*bevel+nx,y0+uy*bevel+ny),
            (x1-ux*bevel+nx,y1-uy*bevel+ny),(x1,y1),
            (x1-ux*bevel-nx,y1-uy*bevel-ny),(x0+ux*bevel-nx,y0+uy*bevel-ny)]
    draw.polygon([(round(x),round(y)) for x,y in points],fill=0)


def glyph(draw, char, x, y, w, h, thickness, inactive, digit=False, unit=1):
    if char in '.\'’':
        yy = y+h if char=='.' else y
        draw.rectangle((round(x+w*.5),round(yy),round(x+w*.5+thickness-1),round(yy+thickness-1)),fill=0)
        return
    active = set(DIGITS[char] if digit else GLYPHS[char])
    def points(seg):
        return tuple((x+a*w,y+b*h) for a,b in SEGMENTS[seg])
    if inactive:
        background = tuple('abcdefg') if digit else BACKGROUND
        for seg in background:
            if seg in active or (seg in 'no' and 'g' in active):
                continue
            start,end=points(seg)
            length=hypot(end[0]-start[0],end[1]-start[1])
            count=max(1,round(length/(4.7*unit if digit else 6*unit)))
            dot=max(1,round(unit))
            for j in range(count):
                t=(j+.5)/count
                xx=round(start[0]+(end[0]-start[0])*t)
                yy=round(start[1]+(end[1]-start[1])*t)
                draw.rectangle((xx,yy,xx+dot-1,yy+dot-1),fill=0)
    for seg in active:
        stroke(draw,*points(seg),thickness)
    if char=='Ё':
        for a in (.25,.75):
            xx=round(x+w*a); yy=round(y-4*unit)
            draw.rectangle((xx,yy,xx+max(1,round(unit)),yy+max(1,round(unit))),fill=0)
    elif char=='Й':
        draw.line([(round(x+w*.25),round(y-5*unit)),(round(x+w*.5),round(y-3*unit)),
                   (round(x+w*.75),round(y-5*unit))],fill=0,width=max(1,round(unit)))


# Sparse shared anchors for Cyrillic, including accents and descenders.
NAME_ANCHORS = tuple((u, v) for u in (0, .5, 1) for v in (0, .5, 1)) + (
    (.25, -.14), (.75, -.14), (0, 1.13), (1, 1.13), (.18, .5))


def validate_content(account_id, name, mode):
    """Validate before rendering or opening a Bluetooth connection."""
    mode = LabelMode(mode)
    blank = mode in (LabelMode.BLANK_ID_NAME, LabelMode.BLANK_ID_ONLY)
    has_name = mode in (LabelMode.ID_NAME, LabelMode.BLANK_ID_NAME)
    if blank:
        if account_id is not None or name is not None:
            raise ValueError("Для пустого шаблона не передавайте ID и имя.")
        return "", []
    digits = format_id(account_id).replace(" ", "")
    if has_name:
        if not isinstance(name, str):
            raise ValueError("Для режима id-name требуется имя.")
        words = normalize_name(name).split()
        if len(words) > 3 or any(len(word) > 11 for word in words):
            raise ValueError("Имя: не более трёх слов, не более 11 символов в каждом.")
    else:
        if name is not None:
            raise ValueError("Для режима id-only не передавайте имя.")
        words = []
    return digits, words


def _cell(draw, char, x, y, w, h, unit, inactive, digit=False, segments=None):
    if inactive:
        if digit:
            for seg in 'abcdefg':
                a, b = SEGMENTS[seg]
                start = (x+a[0]*w, y+a[1]*h)
                end = (x+b[0]*w, y+b[1]*h)
                count = max(1, round(hypot(end[0]-start[0], end[1]-start[1]) / (4*unit)))
                for j in range(count):
                    t = (j+.5)/count
                    xx = round(start[0]+(end[0]-start[0])*t)
                    yy = round(start[1]+(end[1]-start[1])*t)
                    dot = max(1, round(unit))
                    draw.rectangle((xx, yy, xx+dot-1, yy+dot-1), fill=0)
        else:
            for u, v in NAME_ANCHORS:
                xx, yy = round(x+u*w), round(y+v*h)
                dot = max(1, round(unit))
                draw.rectangle((xx, yy, xx+dot-1, yy+dot-1), fill=0)
    if segments is not None:
        for seg in segments:
            a, b = SEGMENTS[seg]
            stroke(draw, (x+a[0]*w,y+a[1]*h), (x+b[0]*w,y+b[1]*h), 4*unit)
    elif char:
        glyph(draw, char, x, y, w, h, (4 if digit else 2)*unit, False, digit, unit)


_MICRO = {
    'I': ('111','010','010','010','111'),
    'D': ('110','101','101','101','110'),
    '/': ('001','001','010','100','100'),
    '0': ('111','101','101','101','111'),
    '1': ('010','110','010','010','111'),
    '3': ('111','001','111','001','111'),
}


def _micro_text(draw, text, x, y, sx, sy):
    # Built-in bitmap glyphs: deterministic on Windows/Linux; no font dependency.
    for index, char in enumerate(text):
        for row, bits in enumerate(_MICRO.get(char, ())):
            for col, bit in enumerate(bits):
                if bit == '1':
                    xx, yy = x+index*4+col, y+row
                    draw.rectangle((round(xx*sx), round(yy*sy),
                                    round(xx*sx)+max(1,round(sx))-1,
                                    round(yy*sy)+max(1,round(sy))-1), fill=0)


def render_label(account_id: str|int|None = None, name: str|None = None,
                 config: LabelConfig|None = None) -> Image.Image:
    config = config or LabelConfig()
    mode = LabelMode(config.mode)
    digits, words = validate_content(account_id, name, mode)
    blank = mode in (LabelMode.BLANK_ID_NAME, LabelMode.BLANK_ID_ONLY)
    id_only = mode in (LabelMode.ID_ONLY, LabelMode.BLANK_ID_ONLY)
    if blank and not config.inactive:
        raise ValueError("Пустой шаблон требует inactive=True.")
    width, height = config.pixels
    sx, sy = width/320, height/240
    unit = min(sx, sy)
    image = Image.new('1', (width, height), 1)
    draw = ImageDraw.Draw(image)
    thin = max(1, round(unit))

    def rule(points):
        draw.line([(round(x*sx),round(y*sy)) for x,y in points],fill=0,width=thin)

    def cell(char, x, y, w, h, digit=False, segments=None):
        _cell(draw, char, x*sx, y*sy, w*sx, h*sy, unit,
              config.inactive, digit, segments)

    if config.decorations:
        for x,y,dx,dy in [(16,14,1,1),(304,14,-1,1),(16,226,1,-1),(304,226,-1,-1)]:
            rule([(x+dx*10,y),(x,y),(x,y+dy*7)])
    if id_only:
        for row, start in enumerate((0,3,2)):
            for col in range(6):
                char = digits[row*3+col-start] if digits and start <= col < start+3 else ''
                # Semantic ID marker remains on the blank template as well.
                marker = ('bc','bcdeg')[col] if row == 1 and col < 2 else None
                cell(char,25+col*47,24+row*66,35,58,True,marker)
        if config.decorations:
            for row, count in enumerate((1,2,3)):
                for j in range(count):
                    y = 46+row*66+j*6
                    rule([(14,y),(19,y)])
                    rule([(301,y),(306,y)])
            _micro_text(draw,'ID / 01',245,18,sx,sy)
            _micro_text(draw,'ID / 03',245,219,sx,sy)
    else:
        for i in range(9):
            cell(digits[i] if digits else '',25+i*28+(i//3)*10,31,20,49,True)
        if config.decorations:
            rule([(26,96),(294,96)])
        for row in range(3):
            word = words[row] if row < len(words) else ''
            for col in range(11):
                cell(word[col] if col < len(word) else '',26+col*25,113+row*37,18,25)
    return image

