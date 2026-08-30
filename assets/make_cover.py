"""Regenerates the team cover image from a real measured SPY vol smile.

The curve is not decoration -- it is the actual implied-volatility-by-strike
curve the agent measures, with the two 13-delta short strikes marked.
"""
import json
import cairosvg

d = json.load(open("assets/curve.json"))
raw = [p for p in d["pts"] if 722 <= p["k"] <= 818]
# Rolling median of 3. Far-OTM strikes on the free indicative feed carry real
# quote noise; this is the same 3-point sanity filter surface.py applies.
pts = []
for i, q in enumerate(raw):
    win = [x["iv"] for x in raw[max(0, i - 1):i + 2]]
    pts.append({**q, "iv": sorted(win)[len(win) // 2]})
spot = d["spot"]

W, H = 1600, 900
CX0, CX1 = 690, 1500          # chart box
CY0, CY1 = 170, 690
K0, K1 = 722.0, 818.0
V0, V1 = 8.6, 18.5

def px(k): return CX0 + (k - K0) / (K1 - K0) * (CX1 - CX0)
def py(v): return CY1 - (v - V0) / (V1 - V0) * (CY1 - CY0)

line = " ".join(f"{px(p['k']):.1f},{py(p['iv']):.1f}" for p in pts)
area = f"{px(K0):.1f},{CY1} " + line + f" {px(K1):.1f},{CY1}"

def marker(k, label, sub, colour, side="above"):
    p = min(pts, key=lambda q: abs(q["k"] - k))
    x, y = px(p["k"]), py(p["iv"])
    # The put wing descends steeply through its own marker, so its labels go
    # left of the point; the call wing is flat there and takes labels above.
    if side == "left":
        lx, ly, anch = x - 26, y - 6, "end"
    else:
        lx, ly, anch = x, y - 56, "middle"
    return f'''
  <line x1="{x:.1f}" y1="{y:.1f}" x2="{x:.1f}" y2="{CY1}" stroke="{colour}" stroke-width="1.5" stroke-dasharray="3 5" opacity=".55"/>
  <circle cx="{x:.1f}" cy="{y:.1f}" r="7" fill="{colour}"/>
  <circle cx="{x:.1f}" cy="{y:.1f}" r="13" fill="none" stroke="{colour}" stroke-width="1.5" opacity=".45"/>
  <text x="{lx:.1f}" y="{ly:.1f}" fill="{colour}" font-family="DejaVu Sans" font-size="22" font-weight="bold" text-anchor="{anch}">{label}</text>
  <text x="{lx:.1f}" y="{ly + 22:.1f}" fill="#8C8C8C" font-family="DejaVu Sans" font-size="15" text-anchor="{anch}">{sub}</text>'''

svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">
<defs>
  <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#0B0B0C"/><stop offset="1" stop-color="#17171A"/>
  </linearGradient>
  <radialGradient id="glow" cx="0.82" cy="0.12" r="0.6">
    <stop offset="0" stop-color="#FFD426" stop-opacity="0.16"/>
    <stop offset="1" stop-color="#FFD426" stop-opacity="0"/>
  </radialGradient>
  <linearGradient id="fill" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#FFD426" stop-opacity="0.20"/>
    <stop offset="1" stop-color="#FFD426" stop-opacity="0"/>
  </linearGradient>
</defs>

<rect width="{W}" height="{H}" fill="url(#bg)"/>
<rect width="{W}" height="{H}" fill="url(#glow)"/>

<!-- chart -->
<polygon points="{area}" fill="url(#fill)"/>
<polyline points="{line}" fill="none" stroke="#FFD426" stroke-width="3.5" stroke-linejoin="round" stroke-linecap="round"/>
<line x1="{px(spot):.1f}" y1="{CY0 - 20}" x2="{px(spot):.1f}" y2="{CY1}" stroke="#5A5A5F" stroke-width="1.5" stroke-dasharray="2 6"/>
<text x="{px(spot):.1f}" y="{CY0 - 28}" fill="#8C8C8C" font-family="DejaVu Sans" font-size="15" text-anchor="middle">SPY 769.30</text>
<line x1="{CX0}" y1="{CY1}" x2="{CX1}" y2="{CY1}" stroke="#2A2A2E" stroke-width="1.5"/>
{marker(749, "749 P", "short  \u0394 0.13", "#FF8A5C", side="left")}
{marker(785, "785 C", "short  \u0394 0.13", "#5CC8FF")}
<text x="{CX0}" y="{CY1 + 34}" fill="#6E6E73" font-family="DejaVu Sans" font-size="15">implied volatility by strike  ·  SPY  ·  11 Sep 2026 expiry  ·  measured live</text>

<!-- text -->
<text x="96" y="150" fill="#FFD426" font-family="DejaVu Sans" font-size="17" font-weight="bold" letter-spacing="4">FLUFFYMARGINS</text>
<text x="96" y="272" fill="#FFFFFF" font-family="DejaVu Sans" font-size="104" font-weight="bold" letter-spacing="-3">Contour</text>
<text x="96" y="340" fill="#FFD426" font-family="DejaVu Sans" font-size="26" font-weight="bold">The surface picks the structure.</text>

<text x="96" y="418" fill="#A8A8AD" font-family="DejaVu Sans" font-size="21">Everyone sells iron condors — and a condor</text>
<text x="96" y="450" fill="#A8A8AD" font-family="DejaVu Sans" font-size="21">sells both wings unconditionally, so half the</text>
<text x="96" y="482" fill="#A8A8AD" font-family="DejaVu Sans" font-size="21">time you sell the underpriced side.</text>
<text x="96" y="530" fill="#FFFFFF" font-family="DejaVu Sans" font-size="21">Contour measures 25-delta skew first,</text>
<text x="96" y="562" fill="#FFFFFF" font-family="DejaVu Sans" font-size="21">and sells only the rich one.</text>

<rect x="96" y="616" width="212" height="40" rx="8" fill="#FFD426" opacity="0.10"/>
<text x="112" y="643" fill="#FFD426" font-family="DejaVu Sans" font-size="17" font-weight="bold">PUT SPREAD</text>
<rect x="322" y="616" width="150" height="40" rx="8" fill="#FFFFFF" opacity="0.06"/>
<text x="338" y="643" fill="#C8C8CD" font-family="DejaVu Sans" font-size="17" font-weight="bold">CONDOR</text>
<rect x="486" y="616" width="212" height="40" rx="8" fill="#FFFFFF" opacity="0.06"/>
<text x="502" y="643" fill="#C8C8CD" font-family="DejaVu Sans" font-size="17" font-weight="bold">CALL SPREAD</text>

<line x1="96" y1="740" x2="1500" y2="740" stroke="#2A2A2E" stroke-width="1.5"/>
<text x="96" y="790" fill="#6E6E73" font-family="DejaVu Sans" font-size="18">Alpaca Trading API  ·  MCP  ·  CLI  ·  Claude  ·  12 deterministic risk gates</text>
<text x="1500" y="790" fill="#6E6E73" font-family="DejaVu Sans" font-size="18" text-anchor="end">Alpaca AI Trading Agents Hackathon 2026</text>
</svg>'''

open("assets/cover.svg", "w").write(svg)
cairosvg.svg2png(bytestring=svg.encode(), write_to="assets/cover.png",
                 output_width=1600, output_height=900)
print("wrote assets/cover.svg and assets/cover.png")
