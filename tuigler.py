#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 dmitri sashchenko
"""tuigler — консольный уродец в духе Fuggler.

e — редактор, s — сохранить персонажа, q — выход. Настроение зависит от нагрузки на CPU и заряда батареи.
"""
import argparse
import functools
import glob
import json
import math
import os
import random
import select
import shutil
import sys
import termios
import time
import tty

__version__ = "0.1.0"

PW, PH = 72, 64            # холст в "пикселях" (полублоки: 72x32 знакомест)
CX, CY, R = 36, 33, 22     # центр и радиус тела
# лицо в пикселях фиксированное и от R не зависит — на большом теле оно мелкое
EY = CY - 9                # высота глаз
EDX = 7                    # смещение глаз от центра
MY = CY + 3                # высота рта
AY = CY + 3                # высота плеч
MOUTH_HW, MOUTH_AMP = 8, 3

CONFIG = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
    "tuigler", "config.json")

PALETTE = [
    (122, 158, 84), (64, 160, 150), (92, 150, 214), (98, 96, 190),
    (156, 104, 200), (226, 120, 170), (212, 80, 72), (234, 140, 60),
    (226, 190, 70), (236, 224, 196), (140, 96, 64), (130, 134, 140),
    (52, 52, 60), (245, 245, 240), (170, 214, 80), (140, 220, 190),
]
TEETH = (240, 236, 220)
TONGUE = (236, 110, 130)
GLASS = (18, 22, 30)
DARK = (30, 28, 34)

# порядок сверху вниз — так по ним ходит ↑/↓
ELEMENTS = ["hair", "eyeL", "eyeR", "mouth", "arms", "beard", "body", "legs"]
VARIANTS = {"hair": 7, "eyeL": 6, "eyeR": 6, "body": 5, "mouth": 5, "beard": 5,
            "arms": 5, "legs": 4}
DEFAULT = {
    "hair": {"v": 1, "c": 6}, "eyeL": {"v": 0, "c": 8}, "eyeR": {"v": 2, "c": 2},
    "body": {"v": 4, "c": 1}, "mouth": {"v": 0, "c": 12}, "beard": {"v": 0, "c": 10},
    "arms": {"v": 1, "c": 1}, "legs": {"v": 1, "c": 1},
}

# русская раскладка: те же физические клавиши
RU = {"у": "e", "й": "q", "с": "c", "С": "C", "к": "r", "р": "h", "о": "j",
      "л": "k", "д": "l", "н": "y", "т": "n", "ы": "s"}


def mix(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def clamp(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))


# ---------------------------------------------------------------- рисование

def in_body(kind, x, y):
    u, v = (x - CX) / R, (y - CY) / R
    th, r = math.atan2(v, u), math.hypot(u, v)
    if kind == 0:   # клякса
        return r <= 0.8 + 0.13 * math.sin(5 * th + 0.5) + 0.07 * math.sin(8 * th + 1.7)
    if kind == 1:   # кривой
        u2 = u - 0.28 * v + 0.16 * math.sin(v * 3.2)
        return (u2 / 0.82) ** 2 + v ** 2 <= 1
    if kind == 2:   # круглый
        return r <= 1
    if kind == 3:   # квадратный
        return abs(u / 0.93) ** 6 + abs(v / 0.93) ** 6 <= 1
    # наггетс
    return math.hypot(u / 0.78, v) <= 0.94 + 0.07 * math.sin(3 * th + 0.6) \
        + 0.05 * math.sin(5 * th + 2.0)


@functools.lru_cache(maxsize=None)
def body_mask(kind):
    mask = frozenset((x, y) for y in range(PH) for x in range(PW) if in_body(kind, x, y))
    top, bot = {}, {}
    for x, y in mask:
        top[x] = min(top.get(x, y), y)
        bot[x] = max(bot.get(x, y), y)
    return mask, top, bot


@functools.lru_cache(maxsize=None)
def body_sides(kind):
    """Левый и правый край тела для каждой строки."""
    left, right = {}, {}
    for x, y in body_mask(kind)[0]:
        left[y] = min(left.get(y, x), x)
        right[y] = max(right.get(y, x), x)
    return left, right


def ellipse(px, cx, cy, rx, ry, color):
    for dy in range(-ry, ry + 1):
        for dx in range(-rx, rx + 1):
            if (dx / rx) ** 2 + (dy / ry) ** 2 <= 1:
                px[(cx + dx, cy + dy)] = color


def disc(px, cx, cy, r, color):
    n = int(r) + 1
    for dy in range(-n, n + 1):
        for dx in range(-n, n + 1):
            if dx * dx + dy * dy <= r * r:
                px[(cx + dx, cy + dy)] = color


def draw_body(px, kind, color):
    mask, top, bot = body_mask(kind)
    shade, light = mix(color, DARK, 0.3), mix(color, (255, 255, 255), 0.18)
    for x, y in mask:
        if (x + 1, y) not in mask or (x, y + 1) not in mask:
            px[(x, y)] = shade
        elif (x - 1, y) not in mask or (x, y - 1) not in mask:
            px[(x, y)] = light
        else:
            px[(x, y)] = color


def capsule(px, x0, y0, x1, y1, r, color, bend=0):
    """Мягкая «колбаска» из кружков; bend выгибает её дугой вниз."""
    n = max(abs(x1 - x0), abs(y1 - y0), 1)
    for i in range(n + 1):
        t = i / n
        disc(px, round(x0 + (x1 - x0) * t),
             round(y0 + (y1 - y0) * t + bend * math.sin(math.pi * t)), r, color)


def draw_arms(px, kind, color, body):
    # рисуются до тела, так что всё, что заходит внутрь, оно перекроет
    if not kind:
        return
    left, right = body_sides(body)
    for edge, s in ((left[AY], -1), (right[AY], 1)):
        if kind == 1:    # культяпки
            ellipse(px, edge, AY, 5, 4, color)
        elif kind == 2:  # висят вдоль тела
            capsule(px, edge, AY, edge + s * 3, AY + 10, 2.4, color)
            disc(px, edge + s * 3, AY + 11, 3.3, color)
        elif kind == 3:  # подняты вверх
            capsule(px, edge, AY, edge + s * 7, AY - 9, 2.3, color)
            disc(px, edge + s * 7, AY - 10, 3.3, color)
        elif kind == 4:  # вялые макаронины в стороны
            capsule(px, edge - s, AY, edge + s * 9, AY + 4, 2.0, color, bend=-2)
            disc(px, edge + s * 9, AY + 4, 3.0, color)


def draw_legs(px, kind, color, body):
    if not kind:
        return
    bot = body_mask(body)[2]
    base = max(bot.values())
    for s in (-1, 1):
        fx = CX + s * 9
        hip = bot.get(fx, base) - 2
        if kind == 1:    # лапки-лепёшки
            ellipse(px, fx, hip + 3, 6, 3, color)   # по местному низу, иначе у кляксы висят в воздухе
        elif kind == 2:  # короткие толстые
            capsule(px, fx, hip, fx, base + 3, 3.0, color)
            ellipse(px, fx + s * 2, base + 4, 5, 3, color)
        elif kind == 3:  # длинные тонкие
            capsule(px, fx, hip, fx, base + 5, 1.8, color)
            ellipse(px, fx + s * 2, base + 6, 4, 2, color)


def mouth_y(dx, mood):
    """Верхняя губа в столбце dx: mood > 0 — уголки вверх, mood < 0 — вниз."""
    return MY - round(mood * MOUTH_AMP * (dx / MOUTH_HW) ** 2)


def draw_beard(px, kind, color, body, mood):
    mask, top, bot = body_mask(body)
    if kind == 1:    # усы: повторяют изгиб рта, поэтому всегда лежат над губой
        for dx in range(-9, 10):
            if dx:
                y = mouth_y(max(-MOUTH_HW, min(MOUTH_HW, dx)), mood) - 2
                px[(CX + dx, y)] = color
                if abs(dx) < 8:     # к кончикам усы сужаются
                    px[(CX + dx, y - 1)] = color
    elif kind == 2:  # козлиная бородка
        for i in range(7):
            w = 3 - i // 2
            for dx in range(-w, w + 1):
                px[(CX + dx, MY + 5 + i)] = color
    elif kind == 3:  # борода лопатой
        for x, y in mask:
            if y >= MY - 2 + (x * 7 % 3 == 0):
                px[(x, y)] = color
        for x in range(CX - 15, CX + 16):
            if x in bot:
                for i in range(1, 2 + x * 5 % 3):
                    px[(x, bot[x] + i)] = color
    elif kind == 4:  # щетина
        for x, y in mask:
            if y >= MY - 2 and x % 2 == 0 and y % 2 == 0:
                px[(x, y)] = color


def draw_hair(px, kind, color, body):
    mask, top, bot = body_mask(body)
    t = lambda x: top.get(x, top[CX])
    if kind == 1:    # хохолок
        for dx, h in ((-2, 3), (-1, 5), (0, 7), (1, 5), (2, 4)):
            for i in range(h):
                lean = (i // 3) * (dx > 0) - (i // 3) * (dx < 0)
                px[(CX + dx + lean, t(CX + dx) - 1 - i)] = color
    elif kind == 2:  # ирокез
        for dx in range(-2, 3):
            x = CX + dx
            for y in range(t(x) - (8 - abs(dx) * 2), t(x) + 2):
                px[(x, y)] = color
    elif kind == 3:  # лохматая чёлка
        for x in range(CX - 16, CX + 17):
            if x in top:
                for y in range(top[x] - 2, top[x] + 3 + x * 7 % 4):
                    px[(x, y)] = color
    elif kind == 4:  # кудри
        for x in range(CX - 14, CX + 15, 4):
            disc(px, x, t(x) - 1, 2.6, color)
    elif kind == 5:  # лысина с двумя пучками по бокам
        # пучки ставим на края макушки, а не на фиксированный отступ:
        # у узких голов иначе они сползают по бокам вниз
        crown = min(top.values())
        xs = [x for x in top if top[x] <= crown + 7]
        for bx in (min(xs) + 3, max(xs) - 3):
            for dx in range(-5, 6):
                x = bx + dx
                if top.get(x, PH) > crown + 10:
                    continue
                # каждый столбик растёт от контура головы, поэтому пучок сидит на ней плотно
                h = round(5 * math.sqrt(1 - (dx / 5.5) ** 2)) + x * 7 % 2
                for y in range(top[x] - h, top[x] + 2):
                    px[(x, y)] = color
    elif kind == 6:  # одинокая волосина
        for dx, dy in ((0, 0), (0, -1), (0, -2), (1, -3), (1, -4), (0, -5),
                       (-1, -6), (-1, -7), (0, -8), (1, -8)):
            px[(CX + dx, t(CX) + dy)] = color


def draw_mouth(px, kind, color, mood):
    gap = mix(color, DARK, 0.6)
    for dx in range(-MOUTH_HW, MOUTH_HW + 1):
        x = CX + dx
        y = mouth_y(dx, mood)
        if kind == 0:    # зубастая улыбка
            if abs(dx) == MOUTH_HW:
                for i in range(4):
                    px[(x, y + i)] = color
            else:
                px[(x, y)] = px[(x, y + 3)] = color
                tooth = gap if (dx + MOUTH_HW) % 3 == 0 else TEETH
                px[(x, y + 1)] = px[(x, y + 2)] = tooth
        elif kind in (3, 4):  # открытый рот, внутри темно; 4 — пасть с верхними зубами
            depth = round((8 if kind == 4 else 4) * (1 - (dx / MOUTH_HW) ** 2))
            for i in range(1, depth):
                px[(x, y + i)] = gap
            if kind == 4 and depth >= 4:
                tooth = gap if (dx + MOUTH_HW) % 3 == 0 else TEETH
                px[(x, y + 1)] = px[(x, y + 2)] = tooth
            px[(x, y)] = px[(x, y + depth)] = color
        else:
            px[(x, y)] = px[(x, y + 1)] = color
            if kind == 2 and abs(dx) in (1, 2):   # два передних зуба
                px[(x, y + 2)] = px[(x, y + 3)] = TEETH
    if kind in (3, 4):   # язык растёт изо рта и свисает через нижнюю губу
        ty = MY + (5 if kind == 4 else 2)
        for i in range(8):
            for dx in range(-1, 4):
                if i == 7 and dx in (-1, 3):
                    continue    # скруглённый кончик
                px[(CX + dx, ty + i)] = mix(TONGUE, DARK, 0.3) if dx == 1 and 0 < i < 6 else TONGUE


def draw_eye(px, kind, color, ex):
    rim = mix(color, DARK, 0.35)

    def button(r, holes):
        disc(px, ex, EY, r, rim)
        disc(px, ex, EY, r - 1, color)
        for dx, dy in holes:
            px[(ex + dx, EY + dy)] = DARK

    if kind == 0:    # фетровый кругляш с горизонтальной миндалевидной прорезью
        disc(px, ex, EY, 5.3, rim)                      # плоский ободок
        disc(px, ex, EY, 4.3, color)                    # выпуклая нашивка
        for dy in range(1, 5):                          # нижнее веко чуть в тени
            for dx in range(-4, 5):
                if dx * dx + dy * dy <= 4.3 ** 2:
                    px[(ex + dx, EY + dy)] = mix(color, DARK, 0.15)
        for dx in range(-4, 5):                         # прорезь: 3px в центре, 1px на концах
            for dy in range(-(abs(dx) <= 2), (abs(dx) <= 2) + 1):
                px[(ex + dx, EY + dy)] = GLASS
        px[(ex + 2, EY)] = (255, 255, 255)              # блик на стекле
    elif kind == 1:  # пуговица, 2 дырки
        button(3.5, ((-1, 0), (1, 0)))
    elif kind == 2:  # пуговица, 4 дырки
        button(4.3, ((-1, -1), (1, -1), (-1, 1), (1, 1)))
    elif kind == 3:  # пришита крестом
        button(3.5, ())
        for i in range(-2, 3):
            px[(ex + i, EY + i)] = px[(ex + i, EY - i)] = TEETH
    elif kind == 4:  # бусинка
        disc(px, ex, EY, 2.3, color)
        px[(ex - 1, EY - 1)] = (255, 255, 255)
    elif kind == 5:  # большая пуговица
        button(5.3, ((0, -1), (0, 1)))


def render(cfg, mood, sel=None, pulse=False):
    def color(name):
        c = PALETTE[cfg[name]["c"]]
        return mix(c, (255, 255, 255), 0.45) if name == sel and pulse else c

    body = cfg["body"]["v"]
    px = {}
    # конечности чуть темнее выбранного цвета, чтобы не сливались с телом того же цвета
    draw_legs(px, cfg["legs"]["v"], mix(color("legs"), DARK, 0.3), body)
    draw_arms(px, cfg["arms"]["v"], mix(color("arms"), DARK, 0.3), body)
    draw_body(px, body, color("body"))
    draw_beard(px, cfg["beard"]["v"], color("beard"), body, mood)
    draw_hair(px, cfg["hair"]["v"], color("hair"), body)
    draw_mouth(px, cfg["mouth"]["v"], color("mouth"), mood)
    draw_eye(px, cfg["eyeL"]["v"], color("eyeL"), CX - EDX)
    draw_eye(px, cfg["eyeR"]["v"], color("eyeR"), CX + EDX)
    return px


def pointer_y(cfg, sel):
    """Высота стрелочек-указателей для выбранного элемента (в пикселях холста)."""
    mask, top, bot = body_mask(cfg["body"]["v"])
    return {"hair": top[CX] - 2, "eyeL": EY, "eyeR": EY, "mouth": MY - 1, "arms": AY,
            "beard": MY + 7, "body": CY + 12, "legs": max(bot.values()) + 2}[sel]


def view_box(px, body):
    """Рамка вокруг персонажа: (x0, y0, ширина, высота) в пикселях холста.

    Центр рамки — центр тела, а не всего нарисованного: так персонаж не прыгает
    по экрану, когда в редакторе меняются причёска или ноги. Размер берётся с
    запасом, чтобы влезли все детали, «дыхание», # над головой и указатели.
    """
    mask, top, bot = body_mask(body)
    xs = [x for x, _ in mask]
    cx, cy = (min(xs) + max(xs)) // 2, (min(top.values()) + max(bot.values())) // 2
    px_x = [x for x, _ in px] + [CX + 19]
    px_y = [y for _, y in px] + [min(top.values()) - 5]
    hx = max(cx - min(px_x), max(px_x) - cx) + 3
    hy = max(cy - min(px_y), max(px_y) + 1 - cy)
    return cx - hx, cy - hy, 2 * hx + 1, 2 * hy + 2


def fit(box, cols, rows):
    """Масштаб (не больше 1) и размер в знакоместах, чтобы рамка влезла в cols x rows."""
    x0, y0, w, h = box
    scale = min(1.0, cols / w, 2 * rows / h)
    return scale, max(1, min(cols, int(w * scale))), max(1, min(rows, int(h * scale / 2)))


def canvas_lines(px, marks, box, scale, width, height, yoff=0):
    """Полублочная картинка width x height знакомест; marks — символы поверх, по пикселям холста."""
    x0, y0 = box[0], box[1]

    def sample(X, Y):       # ближайший пиксель холста для пикселя экрана
        if scale >= 1:
            return px.get((x0 + X, y0 + Y - yoff))
        return px.get((x0 + int((X + 0.5) / scale), y0 + int((Y - yoff + 0.5) / scale)))

    text = {}
    for (x, y), mark in marks.items():
        col, row = int((x - x0) * scale), int((y - y0) * scale) // 2
        text[(min(max(row, 0), height - 1), min(max(col, 0), width - 1))] = mark

    lines = []
    for cr in range(height):
        line, last = [], None
        for c in range(width):
            if (cr, c) in text:
                ch, fg = text[(cr, c)]
                sg = "\x1b[0;1;38;2;%d;%d;%dm" % fg
            else:
                t, b = sample(c, 2 * cr), sample(c, 2 * cr + 1)
                if t is None and b is None:
                    ch, sg = " ", "\x1b[0m"
                elif b is None:
                    ch, sg = "▀", "\x1b[0;38;2;%d;%d;%dm" % t
                elif t is None:
                    ch, sg = "▄", "\x1b[0;38;2;%d;%d;%dm" % b
                else:
                    ch, sg = "▀", "\x1b[0;38;2;%d;%d;%d;48;2;%d;%d;%dm" % (t + b)
            if sg != last:
                line.append(sg)
                last = sg
            line.append(ch)
        line.append("\x1b[0m")
        lines.append("".join(line))
    return lines


# ------------------------------------------------------------ состояние ПК

class Stats:
    def __init__(self):
        self.cpu = 0.0
        self.battery = None      # проценты или None, если батареи нет
        self.charging = False
        self._prev = self._read_cpu()
        self._next = 0.0

    @staticmethod
    def _read_cpu():
        try:
            with open("/proc/stat") as f:
                v = [int(x) for x in f.readline().split()[1:9]]
            return sum(v), v[3] + v[4]
        except (OSError, ValueError, IndexError):
            return None

    def poll(self, now):
        if now < self._next:
            return
        self._next = now + 1.0
        cur = self._read_cpu()
        if cur and self._prev and cur[0] > self._prev[0]:
            busy = 1 - (cur[1] - self._prev[1]) / (cur[0] - self._prev[0])
            self.cpu = 0.6 * self.cpu + 0.4 * clamp(busy)
        self._prev = cur
        self.battery = None
        for bat in sorted(glob.glob("/sys/class/power_supply/BAT*")):
            try:
                with open(bat + "/capacity") as f:
                    self.battery = int(f.read())
                with open(bat + "/status") as f:
                    self.charging = f.read().strip() in ("Charging", "Full")
                break
            except (OSError, ValueError):
                continue

    @property
    def stress(self):
        s = clamp((self.cpu - 0.35) / 0.5)
        if self.battery is not None and not self.charging:
            s = max(s, clamp((35 - self.battery) / 25))
        return s

    def line(self):
        s = "cpu %d%%" % round(self.cpu * 100)
        if self.battery is not None:
            s += " · bat %d%%%s" % (self.battery, "+" if self.charging else "")
        return s


# ------------------------------------------------------------------ конфиг

def load_config():
    cfg = json.loads(json.dumps(DEFAULT))
    try:
        with open(CONFIG) as f:
            saved = json.load(f)
    except (OSError, ValueError):
        return cfg
    for name in ELEMENTS:    # поэлементно: в старом конфиге части деталей может не быть
        try:
            cfg[name] = {"v": int(saved[name]["v"]) % VARIANTS[name],
                         "c": int(saved[name]["c"]) % len(PALETTE)}
        except (ValueError, KeyError, TypeError):
            pass
    return cfg


def save_config(cfg):
    try:
        os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
        with open(CONFIG, "w") as f:
            json.dump(cfg, f, indent=2)
        return True
    except OSError:
        return False


# -------------------------------------------------------------------- ввод

def read_keys(fd):
    data = os.read(fd, 64).decode("utf-8", "ignore")
    arrows = {"A": "up", "B": "down", "C": "right", "D": "left"}
    keys, i = [], 0
    while i < len(data):
        ch = data[i]
        if ch == "\x1b":
            if i + 2 < len(data) and data[i + 1] in "[O" and data[i + 2] in arrows:
                keys.append(arrows[data[i + 2]])
                i += 3
                continue
            if i + 1 == len(data):
                keys.append("esc")
            # прочие escape-последовательности пропускаем целиком
            i += 1
            while i < len(data) and not data[i].isalpha() and data[i] != "~":
                i += 1
            i += 1
            continue
        keys.append(RU.get(ch, ch))
        i += 1
    return keys


# --------------------------------------------------------------------- цикл

HINT_VIEW = "e — редактор · q — выход"
HINT_EDIT = "↑↓ элемент · ←→ вид · c цвет · r рандом · s сохранить · e готово"
HINT_CONFIRM = "сохранить этого персонажа для следующих запусков? y — да · n — нет"
HINT_FAILED = "не удалось сохранить в " + CONFIG
HINT_SAVED = "сохранено"


def build_frame(cfg, stats, mood_override, mode, sel, hint, cols, rows, now):
    stress = stats.stress if mood_override is None else clamp((1 - mood_override) / 2)
    mood = 1 - 2 * stress
    editing = mode == "edit"
    name = ELEMENTS[sel] if editing else None
    px = render(cfg, mood, name, editing and int(now * 2.2) % 2 == 0)
    box = view_box(px, cfg["body"]["v"])
    marks = {}
    if editing:     # стрелочки по краям рамки — видно выбор, даже когда вариант «пусто»
        y = pointer_y(cfg, name)
        if name != "eyeR":
            marks[(box[0] + 1, y)] = ("▸", (255, 255, 255))
        if name != "eyeL":
            marks[(box[0] + box[2] - 2, y)] = ("◂", (255, 255, 255))
    if stress >= 0.7:
        crown = min(body_mask(cfg["body"]["v"])[1].values())
        marks[(CX + 18 + int(now * 2) % 2, crown - 5)] = ("#", (230, 60, 60))
    yoff = int(now / 1.2) % 2 if mode == "view" else 0     # лёгкое "дыхание"

    # персонаж — по центру окна; под подсказку резервируем две строки, если окно не крошечное
    reserve = 2 if rows >= 8 else 0
    scale, width, height = fit(box, cols, rows - reserve)
    ox = (cols - width) // 2
    oy = max(0, min((rows - height) // 2, rows - reserve - height))
    canvas = canvas_lines(px, marks, box, scale, width, height, yoff)
    if mode == "confirm":
        label = HINT_CONFIRM
    elif editing:
        label = HINT_EDIT
    elif hint:
        label = {"failed": HINT_FAILED, "saved": HINT_SAVED,
                 "dirty": HINT_VIEW.replace(" · q", " · s — сохранить · q")}.get(hint, HINT_VIEW)
        if hint in (True, "dirty"):
            label += "   " + stats.line()
    else:
        label = ""
    label = label[:cols]
    out = []
    for r in range(rows):
        cr = r - oy
        if 0 <= cr < len(canvas):
            out.append(" " * ox + canvas[cr])
        elif cr == len(canvas) + 1 and label and reserve:
            out.append(" " * max(0, (cols - len(label)) // 2) + "\x1b[2m" + label + "\x1b[0m")
        else:
            out.append("")
    return out


def copy_config(cfg):
    return {name: dict(item) for name, item in cfg.items()}


def handle_key(key, cfg, state):
    """Возвращает False, если пора выходить.

    Правки живут до конца сеанса сами по себе; в конфиг они попадают только
    по явному «s» с подтверждением.
    """
    if key == "\x03":
        return False
    if state["mode"] == "confirm":
        if key in ("y", "\n", "\r"):
            if save_config(cfg):
                state["saved"] = copy_config(cfg)
                state["note"] = "saved"
            else:
                state["note"] = "failed"
            state["mode"] = "view"
            state["hint_until"] = time.monotonic() + (30 if state["note"] == "failed" else 3)
        elif key in ("n", "esc"):
            state["mode"] = state["back"]      # правки остаются, просто не записаны
        return True
    if key == "q":
        return False
    if key == "s":
        if cfg != state["saved"]:
            state["back"], state["mode"] = state["mode"], "confirm"
        return True
    state["note"] = None
    if state["mode"] == "view":
        if key in ("e", "\n", "\r"):
            state["mode"] = "edit"
        else:
            state["hint_until"] = time.monotonic() + 8
        return True
    name = ELEMENTS[state["sel"]]
    item = cfg[name]
    if key in ("e", "esc", "\n", "\r"):
        state["mode"] = "view"
        state["hint_until"] = time.monotonic() + 4
    elif key in ("up", "k"):
        state["sel"] = (state["sel"] - 1) % len(ELEMENTS)
    elif key in ("down", "j"):
        state["sel"] = (state["sel"] + 1) % len(ELEMENTS)
    elif key in ("left", "h"):
        item["v"] = (item["v"] - 1) % VARIANTS[name]
    elif key in ("right", "l"):
        item["v"] = (item["v"] + 1) % VARIANTS[name]
    elif key == "c":
        item["c"] = (item["c"] + 1) % len(PALETTE)
    elif key == "C":
        item["c"] = (item["c"] - 1) % len(PALETTE)
    elif key == "r":
        for n in ELEMENTS:
            cfg[n] = {"v": random.randrange(VARIANTS[n]),
                      "c": random.randrange(len(PALETTE))}
    return True


def main():
    ap = argparse.ArgumentParser(description="tuigler — консольный уродец")
    ap.add_argument("--mood", type=float, metavar="-1..1",
                    help="зафиксировать настроение (для проверки)")
    ap.add_argument("--once", action="store_true", help="напечатать кадр и выйти")
    ap.add_argument("--version", action="version", version="tuigler " + __version__)
    args = ap.parse_args()

    cfg = load_config()
    stats = Stats()
    stats.poll(time.monotonic())

    if args.once or not sys.stdin.isatty() or not sys.stdout.isatty():
        cols, rows = shutil.get_terminal_size((80, 40))
        lines = build_frame(cfg, stats, args.mood, "view", 0, False, cols, rows, 0.0)
        print("\n".join(l for l in lines if l))
        return

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    state = {"mode": "view", "sel": 0, "hint_until": time.monotonic() + 8,
             "saved": copy_config(cfg), "back": "view", "note": None}
    last = None
    sys.stdout.write("\x1b[?1049h\x1b[?25l")
    try:
        tty.setcbreak(fd)
        running = True
        while running:
            now = time.monotonic()
            stats.poll(now)
            cols, rows = shutil.get_terminal_size()
            hint = now < state["hint_until"] and (
                state["note"] or ("dirty" if cfg != state["saved"] else True))
            lines = build_frame(cfg, stats, args.mood, state["mode"], state["sel"],
                                hint, cols, rows, now)
            frame = "\x1b[H" + "\r\n".join(l + "\x1b[K" for l in lines)
            if frame != last:       # не перерисовываем зря — сами нагрузку не создаём
                sys.stdout.write(frame)
                sys.stdout.flush()
                last = frame
            if select.select([fd], [], [], 0.1)[0]:
                for key in read_keys(fd):
                    running = running and handle_key(key, cfg, state)
    except KeyboardInterrupt:
        pass
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        sys.stdout.write("\x1b[0m\x1b[?25h\x1b[?1049l")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
