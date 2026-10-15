"""CYBERWALL segment label renderer v0.1. Python 3.10+, Pillow.

Pure layout/raster module: no database, Bluetooth or printer side effects.
Coordinates are drawn directly on the final monochrome pixel grid.
"""
from __future__ import annotations
from dataclasses import dataclass
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
# Latin uppercase are useful for optional Latin names; unknown glyphs fail
# explicitly, never silently disappear or turn into placeholder squares.
LATIN = {'A':'abcefg','B':'abcdefg','C':'afed','D':'abcdhi','E':'afged',
 'F':'afge','G':'afedcg','H':'febcg','I':'adhi','J':'bcd','K':'fekm',
 'L':'fed','M':'febcjk','N':'febcjm','O':'abcdef','P':'abfge',
 'Q':'abcdefm','R':'abfgem','S':'afgcd','T':'ahi','U':'febcd',
 'V':'felk','W':'febclm','X':'jklm','Y':'jki','Z':'adkl'}
GLYPHS = {**DIGITS, **CYRILLIC, **LATIN, '-':'g', '.':'', "'":'', '’':''}
BACKGROUND = tuple('abcdef') + tuple('hijklmno')  # split middle bar

@dataclass(frozen=True)
class LabelConfig:
    width_mm: float = 40.0
    height_mm: float = 30.0
    dpi: float = 203.2  # 8 dots/mm; calibrate against the actual printer
    inactive: bool = True
    decorations: bool = True

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
    missing = sorted(set(result) - set(GLYPHS) - {' '})
    if missing:
        raise ValueError('Нет сегментного рисунка для символов: ' + ', '.join(missing))
    return result


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


def text_width(text,w,gap,space):
    return sum(space if c==' ' else w for c in text)+max(0,len(text)-1)*gap


def line(draw,text,cx,y,w,h,thickness,gap,space,inactive,digit=False,unit=1):
    x=cx-text_width(text,w,gap,space)/2
    for char in text:
        if char==' ': x+=space+gap; continue
        glyph(draw,char,x,y,w,h,thickness,inactive,digit,unit)
        x+=w+gap


def name_layout(name,available,unit):
    # Prefer one line when readable, otherwise word-balanced two-line layout.
    candidates=[(name,)]
    words=name.split()
    candidates.extend((' '.join(words[:i]),' '.join(words[i:])) for i in range(1,len(words)))
    best=None
    for lines in candidates:
        for raw_w in range(21,11,-1):
            w=raw_w*unit; gap=4*unit; space=7*unit
            if max(text_width(s,w,gap,space) for s in lines)<=available:
                score=raw_w-(2 if len(lines)==2 else 0)
                if best is None or score>best[0]: best=(score,lines,w,gap,space)
                break
    if best is None:
        raise ValueError('Имя слишком длинное для читаемой печати: увеличьте этикетку или сократите имя.')
    return best[1:]


def render_label(account_id: str|int, name: str, config: LabelConfig|None=None) -> Image.Image:
    config=config or LabelConfig()
    account_id=format_id(account_id); name=normalize_name(name)
    width,height=config.pixels
    unit=min(width/320,height/240)
    image=Image.new('1',(width,height),1)
    draw=ImageDraw.Draw(image)
    cx=width/2; left=cx-144*unit; top=(height-240*unit)/2
    if config.decorations:
        # Short open corner marks: no full enclosing border or extraneous text.
        for xx,yy,sx,sy in [(left,top+14*unit,1,1),(cx+144*unit,top+14*unit,-1,1),
                            (left,top+226*unit,1,-1),(cx+144*unit,top+226*unit,-1,-1)]:
            draw.line([(round(xx+sx*10*unit),round(yy)),(round(xx),round(yy)),
                       (round(xx),round(yy+sy*7*unit))],fill=0,width=max(1,round(unit)))
        draw.line((round(cx-130*unit),round(top+116*unit),round(cx+130*unit),round(top+116*unit)),fill=0,width=max(1,round(unit)))
    line(draw,account_id,cx,top+34*unit,20*unit,61*unit,4*unit,8*unit,7*unit,config.inactive,True,unit)
    lines,w,gap,space=name_layout(name,270*unit,unit)
    h=min(34*unit,w*1.65)
    ys=[top+158*unit] if len(lines)==1 else [top+139*unit,top+188*unit]
    for text,y in zip(lines,ys):
        line(draw,text,cx,y,w,h,2*unit,gap,space,config.inactive,False,unit)
    return image

