#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Рисует packaging/tuigler.svg из того же пиксельного рендера, что и сама программа."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import tuigler as t  # noqa: E402

CFG = {
    "hair": {"v": 1, "c": 6}, "eyeL": {"v": 0, "c": 10}, "eyeR": {"v": 2, "c": 2},
    "body": {"v": 4, "c": 1}, "mouth": {"v": 0, "c": 12}, "beard": {"v": 0, "c": 10},
    "arms": {"v": 1, "c": 1}, "legs": {"v": 1, "c": 1},
}

px = t.render(CFG, 1)
x0, x1 = min(x for x, _ in px), max(x for x, _ in px)
y0, y1 = min(y for _, y in px), max(y for _, y in px)
size = max(x1 - x0, y1 - y0) + 1 + 4            # квадрат с полями по 2 пикселя
ox = (size - (x1 - x0 + 1)) // 2 - x0
oy = (size - (y1 - y0 + 1)) // 2 - y0

rects = []
for y in range(y0, y1 + 1):
    x = x0
    while x <= x1:
        c = px.get((x, y))
        if c is None:
            x += 1
            continue
        run = 1
        while px.get((x + run, y)) == c:        # соседние пиксели одного цвета — один rect
            run += 1
        rects.append('<rect x="%d" y="%d" width="%d" height="1" fill="#%02x%02x%02x"/>'
                     % ((x + ox, y + oy, run) + c))
        x += run

out = os.path.join(HERE, "tuigler.svg")
with open(out, "w") as f:
    f.write('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" '
            'shape-rendering="crispEdges">\n%s\n</svg>\n' % (size, size, "\n".join(rects)))
print(out, "%dx%d" % (size, size), len(rects), "rects")
