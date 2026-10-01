#!/usr/bin/env python3
"""Render the shipment: a pine crate of bottles nested in wood wool.

The page used to draw this box in CSS, straight down from above, and it read as
what it was — rectangles. This renders the real thing instead: a three-quarter
view through an actual pinhole camera, with the crate's faces warped by the
homography their corners imply, procedural pine for the boards, and excelsior
drawn strand by strand and depth-sorted against the glass.

The bottles are the finished cutouts from tools/cutout_bottles.py, placed as
billboards. That is sound here: a wine bottle is a surface of revolution, so a
straight-on photograph is what it looks like from any angle around it. Only
their size and their height on screen have to follow the perspective, and those
come from projecting each bottle's base and shoulder.

Run from the repo root:  python3 tools/render_shipment.py
Requires Pillow and numpy. Writes images/shipment-crate.png.
"""

import math
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = "images/shipment-crate.webp"
W, H = 1640, 1150          # render canvas; the output is cropped to the subject
OUT_W = 1500               # delivered width
SS = 2                     # supersample factor; everything is drawn at SS×
SEED = 20260401

# ── Scene, in inches ──────────────────────────────────────────────────────
# A six-bottle presentation crate, near enough to the trade's 13.5 × 11 × 7.
IN_W, IN_D, IN_H = 13.4, 9.2, 4.6      # interior
WOOL_TOP = 4.1                         # where the nest crests
BED = 1.7                              # wool the bottles stand on, so the
                                       # labels clear the nest
WALL = 0.62                            # board thickness
BOTTLE_H = 11.9                        # a 750ml Bordeaux, capsule to base

# Five bottles: three across the back, two tucked into the gaps in front.
BOTTLES = [
    ("cabernet",   -4.30, -2.30),
    ("sangiovese",  0.00, -2.45),
    ("zinfandel",   4.30, -2.30),
    ("malbec",     -2.15,  2.35),
    ("pinot",       2.15,  2.35),
]

CAM = np.array([2.4, 14.8, 23.0])      # eye: high enough to see down into the crate
LOOK = np.array([0.0, 6.4, 0.3])       # where it points
FOCAL = 1210.0                         # pixels at SS = 1

# Key light, for shading the boards and the wool.
LIGHT = np.array([-0.52, 0.78, 0.35])
LIGHT /= np.linalg.norm(LIGHT)

rng = np.random.default_rng(SEED)


# ── Camera ────────────────────────────────────────────────────────────────
def view_matrix():
    f = LOOK - CAM
    f = f / np.linalg.norm(f)
    up = np.array([0.0, 1.0, 0.0])
    r = np.cross(f, up)
    r /= np.linalg.norm(r)
    u = np.cross(r, f)
    return np.stack([r, u, -f])        # world -> camera


VIEW = view_matrix()


def to_cam(p):
    return VIEW @ (np.asarray(p, float) - CAM)


def project(p):
    """World point to screen pixels (at SS = 1). Returns (x, y, depth)."""
    c = to_cam(p)
    z = -c[2]
    if z < 0.05:
        z = 0.05
    return (W / 2 + FOCAL * c[0] / z,
            H / 2 - FOCAL * c[1] / z,
            z)


# ── Procedural pine ───────────────────────────────────────────────────────
def value_noise(h, w, cells, octaves=4):
    """Fractal value noise, bilinear between a coarse random grid."""
    out = np.zeros((h, w))
    amp, total = 1.0, 0.0
    for o in range(octaves):
        cy, cx = max(2, int(cells * 2 ** o)), max(2, int(cells * 2 ** o))
        g = rng.random((cy + 1, cx + 1))
        img = Image.fromarray((g * 255).astype(np.uint8)).resize((w, h), Image.BICUBIC)
        out += amp * (np.asarray(img).astype(float) / 255.0)
        total += amp
        amp *= 0.5
    return out / total


def pine_board(h, w, tone=1.0):
    """One sawn board: grain along x, a few knots, sawmill scuff."""
    yy, xx = np.mgrid[0:h, 0:w]

    # Grain lines: near-parallel rings, wandering slowly down the board.
    wander = value_noise(h, w, 3, 3) * 14.0
    rings = np.sin((yy + wander) * (2 * math.pi / rng.uniform(5.5, 9.0)))
    rings = (rings * 0.5 + 0.5) ** 2.2

    fine = value_noise(h, w, 10, 3)
    fine = np.asarray(Image.fromarray((fine * 255).astype(np.uint8))
                      .filter(ImageFilter.GaussianBlur(0.4))).astype(float) / 255

    v = 0.76 + 0.26 * rings + 0.17 * (fine - 0.5)

    # Knots.
    for _ in range(rng.integers(0, 3)):
        ky, kx = rng.uniform(0, h), rng.uniform(0, w)
        kr = rng.uniform(3.5, 7.0)
        d = np.hypot((yy - ky), (xx - kx) * 0.45)
        knot = np.exp(-(d / (kr * 2.4)) ** 2)
        swirl = (np.sin(d / kr * 3.0) * 0.5 + 0.5)
        v -= knot * (0.30 + 0.22 * swirl)

    v = np.clip(v * tone, 0.18, 1.25)

    base = np.array([214.0, 176.0, 120.0])     # raw pine
    warm = np.array([132.0, 88.0, 46.0])       # the darker grain
    t = np.clip((v - 0.60) / 0.48, 0, 1)[..., None]
    rgb = warm * (1 - t) + base * t
    return np.clip(rgb, 0, 255)


def board_panel(h, w, boards, gap_px=2, vertical=False):
    """A panel made of several boards with a shadowed seam between them."""
    if vertical:
        panel = np.zeros((h, w, 3))
        edges = np.linspace(0, w, boards + 1).astype(int)
        for i in range(boards):
            x0, x1 = edges[i], edges[i + 1]
            panel[:, x0:x1] = np.rot90(pine_board(x1 - x0, h, rng.uniform(0.9, 1.08)), -1)
            if i:
                panel[:, x0:x0 + gap_px] *= 0.42
        return panel
    panel = np.zeros((h, w, 3))
    edges = np.linspace(0, h, boards + 1).astype(int)
    for i in range(boards):
        y0, y1 = edges[i], edges[i + 1]
        panel[y0:y1] = pine_board(y1 - y0, w, rng.uniform(0.9, 1.08))
        if i:
            panel[y0:y0 + gap_px] *= 0.42
    return panel


def brand(panel):
    """Burn the winery's mark into a board, the way a crate is stencilled."""
    h, w = panel.shape[:2]
    img = Image.fromarray(np.clip(panel, 0, 255).astype(np.uint8))
    mask = Image.new("L", (w, h), 0)
    md = ImageDraw.Draw(mask)
    try:
        f1 = ImageFont.truetype("/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
                                int(h * 0.145))
        f2 = ImageFont.truetype("/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
                                int(h * 0.075))
    except OSError:
        return panel

    md.text((w / 2, h * 0.47), "WARM SPRINGS", font=f1, fill=235, anchor="mm")
    md.text((w / 2, h * 0.645), "C E L L A R S", font=f1, fill=235, anchor="mm")
    md.text((w / 2, h * 0.79), "EST. 1982   ·   GLEN ELLEN, CALIFORNIA",
            font=f2, fill=200, anchor="mm")

    # A stencil never prints evenly on sawn timber.
    grit = value_noise(h, w, 24, 3)
    m = np.asarray(mask).astype(float) / 255.0
    m *= np.clip(grit * 1.5, 0, 1)
    m = np.asarray(Image.fromarray((m * 255).astype(np.uint8))
                   .filter(ImageFilter.GaussianBlur(0.6))).astype(float) / 255.0

    ink = np.array([54.0, 32.0, 20.0])
    return panel * (1 - m[..., None] * 0.82) + ink * (m[..., None] * 0.82)


# ── Drawing textured quads under perspective ──────────────────────────────
def homography(src, dst):
    """3×3 taking the four src points to the four dst points."""
    a = []
    b = []
    for (sx, sy), (dx, dy) in zip(src, dst):
        a.append([sx, sy, 1, 0, 0, 0, -dx * sx, -dx * sy])
        b.append(dx)
        a.append([0, 0, 0, sx, sy, 1, -dy * sx, -dy * sy])
        b.append(dy)
    h = np.linalg.solve(np.array(a), np.array(b))
    return np.append(h, 1).reshape(3, 3)


def draw_quad(canvas, corners_world, texture, shade=1.0, seed_edge=True):
    """Paint a textured quad. corners_world: 4 points, winding round the face."""
    pts = [project(p) for p in corners_world]
    dst = [(p[0] * SS, p[1] * SS) for p in pts]
    th, tw = texture.shape[:2]
    src = [(0, 0), (tw - 1, 0), (tw - 1, th - 1), (0, th - 1)]

    hm = homography(dst, src)          # screen -> texture, so we can inverse-map

    xs = [p[0] for p in dst]
    ys = [p[1] for p in dst]
    x0, x1 = int(max(0, math.floor(min(xs)))), int(min(canvas.shape[1], math.ceil(max(xs)) + 1))
    y0, y1 = int(max(0, math.floor(min(ys)))), int(min(canvas.shape[0], math.ceil(max(ys)) + 1))
    if x1 <= x0 or y1 <= y0:
        return

    yy, xx = np.mgrid[y0:y1, x0:x1]
    ones = np.ones_like(xx, dtype=float)
    p = np.stack([xx + 0.5, yy + 0.5, ones])
    q = np.tensordot(hm, p, axes=(1, 0))
    u = q[0] / q[2]
    v = q[1] / q[2]

    inside = (u >= 0) & (u <= tw - 1) & (v >= 0) & (v <= th - 1) & (q[2] > 0)
    if not inside.any():
        return

    ui = np.clip(u, 0, tw - 1.001)
    vi = np.clip(v, 0, th - 1.001)
    u0 = ui.astype(int)
    v0 = vi.astype(int)
    fu = (ui - u0)[..., None]
    fv = (vi - v0)[..., None]
    tex = ((texture[v0, u0] * (1 - fu) + texture[v0, u0 + 1] * fu) * (1 - fv) +
           (texture[v0 + 1, u0] * (1 - fu) + texture[v0 + 1, u0 + 1] * fu) * fv)

    tex = tex * shade
    region = canvas[y0:y1, x0:x1]
    region[inside] = np.clip(tex[inside], 0, 255)


def face_shade(normal):
    """Lambert plus a little ambient, so the four walls read apart."""
    n = np.asarray(normal, float)
    n /= np.linalg.norm(n)
    return 0.42 + 0.58 * max(0.0, float(np.dot(n, LIGHT)))


# ── Excelsior ─────────────────────────────────────────────────────────────
def wool_strands(n, y_lo, y_hi, spread=1.0, cling=0.0):
    """Curled wood-wool strands as 3-D polylines, with their mean depth.

    `cling` draws a share of them to the foot of the nearest bottle, which is
    where a packer actually tucks the wool — it keeps the glass from moving and
    stops the nest reading as a flat mat laid over the top.
    """
    out = []
    hw, hd = IN_W / 2, IN_D / 2
    for _ in range(n):
        if cling and rng.random() < cling:
            _, bx, bz = BOTTLES[rng.integers(0, len(BOTTLES))]
            a = rng.uniform(0, 2 * math.pi)
            r = 1.5 + abs(rng.normal(0, 1.0))
            x = np.clip(bx + math.cos(a) * r, -hw * spread, hw * spread)
            z = np.clip(bz + math.sin(a) * r, -hd * spread, hd * spread)
        else:
            x = rng.uniform(-hw * spread, hw * spread)
            z = rng.uniform(-hd * spread, hd * spread)
        # Mounded towards the middle of the range rather than piled on the
        # floor: at this camera angle the front board hides the floor, and a
        # packed crate is filled to just under the rim anyway. The surface
        # undulates with position, so the nest does not top out as a slab.
        swell = (math.sin(x * 0.72 + 0.4) * 0.30 +
                 math.sin(z * 1.05 - 1.1) * 0.22 +
                 math.sin(x * 1.9 + z * 1.3) * 0.16)
        y = y_lo + (y_hi - y_lo) * (rng.random() + rng.random()) / 2 + swell

        length = rng.uniform(1.4, 7.5)
        ang = rng.uniform(0, 2 * math.pi)
        curl = rng.uniform(0.7, 2.6) * rng.choice([-1, 1])
        rise = rng.uniform(-0.25, 0.55)
        steps = 11

        pts = []
        for i in range(steps):
            t = i / (steps - 1)
            a = ang + curl * t
            px = x + math.cos(a) * length * t
            pz = z + math.sin(a) * length * t
            py = y + rise * t + 0.22 * math.sin(t * 6.0 + ang)
            px = max(-hw + 0.1, min(hw - 0.1, px))
            pz = max(-hd + 0.1, min(hd - 0.1, pz))
            pts.append((px, py, pz))

        depth = np.mean([to_cam(p)[2] for p in pts])
        out.append((depth, pts, y))
    return out


WOOL_COLS = np.array([
    [226, 201, 157], [238, 216, 176], [211, 182, 134],
    [246, 229, 195], [198, 168, 120], [231, 208, 166],
], dtype=float)


def draw_wool(draw, strands, dim=1.0):
    for _, pts, y in strands:
        col = WOOL_COLS[rng.integers(0, len(WOOL_COLS))]
        # Higher strands catch more light; the ones down in the crate go dark.
        lift = 0.34 + 0.66 * np.clip((y - 1.2) / 3.0, 0, 1) * rng.uniform(0.72, 1.12)
        c = np.clip(col * lift * dim, 0, 255).astype(int)
        scr = [project(p) for p in pts]
        xy = [(x * SS, y2 * SS) for x, y2, _ in scr]
        wdt = max(1, int(round(SS * rng.uniform(0.9, 1.6) * (24.0 / max(scr[0][2], 1.0)))))
        draw.line(xy, fill=(int(c[0]), int(c[1]), int(c[2])), width=wdt, joint="curve")


# ── Bottles ───────────────────────────────────────────────────────────────
def paste_bottle(canvas_img, name, x, z, floor_y):
    """Composite a bottle standing at (x, z), sized by where it projects."""
    img = Image.open(f"images/{name}-bottle.png").convert("RGBA")
    bx, by, depth = project((x, floor_y, z))
    tx, ty, _ = project((x, floor_y + BOTTLE_H, z))

    px_h = abs(by - ty) * SS
    scale = px_h / img.height
    w = max(2, int(round(img.width * scale)))
    h = max(2, int(round(px_h)))
    img = img.resize((w, h), Image.LANCZOS)

    # Everything further back sits in more of the crate's shade.
    near, far = 20.0, 34.0
    t = np.clip((depth - near) / (far - near), 0, 1)
    if t > 0:
        arr = np.asarray(img).astype(float)
        arr[..., :3] *= (1.0 - 0.26 * t)
        arr[..., :3] += 14.0 * t          # a little atmosphere
        img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))

    canvas_img.alpha_composite(img, (int(round(bx * SS - w / 2)), int(round(by * SS - h))))
    return depth


def bottle_shadow(draw, x, z, surface_y):
    """Contact shadow where the glass enters the nest."""
    cx, cy, depth = project((x, surface_y, z))
    rx = 2.3 * FOCAL / depth * SS
    ry = rx * 0.34
    draw.ellipse([cx * SS - rx, cy * SS - ry, cx * SS + rx, cy * SS + ry],
                 fill=(44, 30, 20, 150))


def subject_box(margin=0.07, shadow_right=0.10):
    """Frame from the geometry, not from the pixels.

    Thresholding against the backdrop sounds easier until the backdrop is a
    gradient. Every point that matters is known in world space, so project the
    crate's corners and the bottle tops and crop to those.
    """
    hw, hd, hh = IN_W / 2, IN_D / 2, IN_H
    ow, od = hw + WALL, hd + WALL

    pts = []
    for x in (-ow, ow):
        for z in (-od, od):
            for y in (-WALL, hh):
                pts.append(project((x, y, z))[:2])
    for _, bx, bz in BOTTLES:
        pts.append(project((bx, BED + BOTTLE_H + 0.3, bz))[:2])

    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)

    mx, my = (x1 - x0) * margin, (y1 - y0) * margin
    return (x0 - mx, y0 - my, x1 + mx + (x1 - x0) * shadow_right, y1 + my)


def crop_to_subject(img, aspect):
    x0, y0, x1, y1 = subject_box()
    x0, y0, x1, y1 = x0 * SS, y0 * SS, x1 * SS, y1 * SS

    w, h = x1 - x0, y1 - y0
    if w / h < aspect:                      # grow the short side, stay centred
        need = h * aspect - w
        x0 -= need / 2
        x1 += need / 2
    else:
        need = w / aspect - h
        y0 -= need / 2
        y1 += need / 2

    box = (int(round(x0)), int(round(y0)), int(round(x1)), int(round(y1)))
    if (box[0] < 0 or box[1] < 0 or box[2] > img.width or box[3] > img.height):
        # Extend with the backdrop's own corner tone rather than black bars.
        fill = (0, 0, 0, 0) if img.mode == "RGBA" else img.getpixel((2, 2))
        pad_l, pad_t = max(0, -box[0]), max(0, -box[1])
        bigger = Image.new(img.mode, (img.width + pad_l + max(0, box[2] - img.width),
                                   img.height + pad_t + max(0, box[3] - img.height)), fill)
        bigger.paste(img, (pad_l, pad_t))
        img = bigger
        box = (box[0] + pad_l, box[1] + pad_t, box[2] + pad_l, box[3] + pad_t)
    return img.crop(box)


# ── Scene ─────────────────────────────────────────────────────────────────
def render_scene(bg):
    """Draw the whole scene over a flat backdrop of value `bg`."""
    global rng
    rng = np.random.default_rng(SEED)      # identical geometry on every pass
    hw, hd, hh = IN_W / 2, IN_D / 2, IN_H
    ow, od = hw + WALL, hd + WALL

    canvas = np.zeros((H * SS, W * SS, 3), dtype=float)

    canvas[:] = float(bg)

    # ── Crate, back to front ──
    # Interior floor.
    floor_tex = board_panel(260, 420, 4)
    draw_quad(canvas, [(-hw, 0, -hd), (hw, 0, -hd), (hw, 0, hd), (-hw, 0, hd)],
              floor_tex, shade=face_shade((0, 1, 0)) * 0.72)

    # Inner faces of the back and side walls.
    back_tex = board_panel(300, 520, 3)
    draw_quad(canvas, [(-hw, hh, -hd), (hw, hh, -hd), (hw, 0, -hd), (-hw, 0, -hd)],
              back_tex, shade=face_shade((0, 0, 1)) * 0.68)

    side_tex = board_panel(300, 360, 3)
    draw_quad(canvas, [(-hw, hh, -hd), (-hw, 0, -hd), (-hw, 0, hd), (-hw, hh, hd)],
              side_tex, shade=face_shade((1, 0, 0)) * 0.80)
    draw_quad(canvas, [(hw, hh, hd), (hw, 0, hd), (hw, 0, -hd), (hw, hh, -hd)],
              board_panel(300, 360, 3), shade=face_shade((-1, 0, 0)) * 0.60)

    img = Image.fromarray(np.clip(canvas, 0, 255).astype(np.uint8)).convert("RGBA")
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    # Wool packed under and behind the glass.
    back_wool = wool_strands(5200, 0.9, 4.0, spread=0.99, cling=0.45)
    back_wool.sort(key=lambda s: -s[0])
    draw_wool(d, back_wool, dim=0.86)
    img.alpha_composite(layer)

    # Bottles and their shadows, far to near.
    order = sorted(BOTTLES, key=lambda b: -to_cam((b[1], 0, b[2]))[2])
    sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ds = ImageDraw.Draw(sh)
    for name, x, z in order:
        bottle_shadow(ds, x, z, WOOL_TOP - 0.5)
    sh = sh.filter(ImageFilter.GaussianBlur(9 * SS))
    img.alpha_composite(sh)

    for name, x, z in order:
        paste_bottle(img, name, x, z, BED)

    # Wool tucked in front of and between the glass.
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    front_wool = wool_strands(3400, 1.1, 4.1, spread=0.97, cling=0.5)
    front_wool = [s for s in front_wool if s[0] < to_cam((0, 0, 0))[2] + 1.0]
    front_wool.sort(key=lambda s: -s[0])
    draw_wool(d, front_wool, dim=1.0)
    img.alpha_composite(layer)

    canvas = np.asarray(img.convert("RGB")).astype(float)

    # ── Front wall last: it stands in front of everything ──
    front_in = board_panel(300, 520, 3)
    draw_quad(canvas, [(-hw, hh, hd), (-hw, 0, hd), (hw, 0, hd), (hw, hh, hd)],
              front_in, shade=face_shade((0, 0, -1)) * 0.74)

    front_out = brand(board_panel(300, 540, 3))
    draw_quad(canvas, [(-ow, hh, od), (ow, hh, od), (ow, -WALL, od), (-ow, -WALL, od)],
              front_out, shade=face_shade((0, 0, 1)) * 1.0)

    left_out = board_panel(300, 380, 3)
    draw_quad(canvas, [(-ow, hh, -od), (-ow, hh, od), (-ow, -WALL, od), (-ow, -WALL, -od)],
              left_out, shade=face_shade((-1, 0, 0)) * 0.88)

    # Top edges of the walls — the sawn thickness you look across.
    rim = pine_board(26, 520, 1.0)
    draw_quad(canvas, [(-ow, hh, od), (ow, hh, od), (hw, hh, hd), (-hw, hh, hd)],
              rim, shade=1.0)
    draw_quad(canvas, [(-ow, hh, -od), (-hw, hh, -hd), (-hw, hh, hd), (-ow, hh, od)],
              pine_board(26, 380, 1.0), shade=0.96)
    draw_quad(canvas, [(ow, hh, od), (hw, hh, hd), (hw, hh, -hd), (ow, hh, -od)],
              pine_board(26, 380, 1.0), shade=0.82)

    img = Image.fromarray(np.clip(canvas, 0, 255).astype(np.uint8)).convert("RGBA")

    # A few strands spilling over the front lip.
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    spill = []
    for _ in range(620):
        x = rng.uniform(-hw * 0.96, hw * 0.96)
        pts = []
        length = rng.uniform(0.9, 2.6)
        drop = rng.uniform(0.35, 1.05)
        ang = rng.uniform(0, 2 * math.pi)
        curl = rng.uniform(0.9, 2.6) * rng.choice([-1, 1])
        for i in range(9):
            t = i / 8
            a = ang + curl * t
            pts.append((x + math.cos(a) * length * t,
                        hh + 0.3 - drop * t ** 1.5,
                        hd + 0.05 + math.sin(a) * 0.75 * t))
        spill.append((np.mean([to_cam(p)[2] for p in pts]), pts, hh))
    spill.sort(key=lambda s: -s[0])
    draw_wool(d, spill, dim=1.05)
    img.alpha_composite(layer)

    # ── The shadow it throws, which must end up in the alpha ──
    arr = np.asarray(img.convert("RGB")).astype(float)

    base = Image.new("L", img.size, 0)
    db = ImageDraw.Draw(base)
    pts = [project(p)[:2] for p in
           [(-ow - 0.3, -WALL, od + 0.4), (ow + 2.6, -WALL, od + 0.2),
            (ow + 3.4, -WALL, -od - 0.6), (-ow - 0.2, -WALL, -od - 0.4)]]
    db.polygon([(x * SS, y * SS) for x, y in pts], fill=120)
    base = base.filter(ImageFilter.GaussianBlur(26 * SS))
    bm = np.asarray(base).astype(float)[..., None] / 255.0
    arr = arr * (1 - 0.55 * bm)

    contact = Image.new("L", img.size, 0)
    dc = ImageDraw.Draw(contact)
    cpts = [project(p)[:2] for p in
            [(-ow, -WALL, od), (ow, -WALL, od), (ow + 0.5, -WALL, -od), (-ow - 0.5, -WALL, -od)]]
    dc.polygon([(x * SS, y * SS) for x, y in cpts], fill=255)
    contact = contact.filter(ImageFilter.GaussianBlur(7 * SS))
    cm = np.asarray(contact).astype(float)[..., None] / 255.0
    arr = arr * (1 - 0.42 * cm)
    return arr


def render():
    """Matte the scene out of its backdrop and write it with alpha.

    Drawing it twice, once on black and once on white, gives the exact coverage
    of every pixel — including the soft edges of the shadow, which a threshold
    could never recover. The page then shows the crate on its own background,
    in either theme, instead of on a pasted-in grey rectangle.
    """
    dark = render_scene(0.0)
    light = render_scene(255.0)

    alpha = np.clip(1.0 - (light - dark) / 255.0, 0.0, 1.0).max(axis=2)
    a3 = alpha[..., None]
    colour = np.where(a3 > 0.004, dark / np.maximum(a3, 1e-6), 0.0)

    rgba = np.concatenate([np.clip(colour, 0, 255), alpha[..., None] * 255], axis=2)
    img = Image.fromarray(rgba.astype(np.uint8), "RGBA")
    img = crop_to_subject(img, aspect=4 / 3)
    img = img.resize((OUT_W, round(OUT_W * 3 / 4)), Image.LANCZOS)

    rgb, a = img.convert("RGB"), img.getchannel("A")
    rgb = rgb.filter(ImageFilter.UnsharpMask(radius=1.6, percent=62, threshold=3))

    # Film grain. Without it the surfaces read as vector art.
    g = np.asarray(rgb).astype(float)
    g += np.random.default_rng(SEED + 1).normal(0, 2.4, g.shape[:2])[..., None]
    out = Image.fromarray(np.clip(g, 0, 255).astype(np.uint8))
    out.putalpha(a)

    # WebP: a photographic image that needs an alpha channel. The PNG is seven
    # times the size for no visible gain.
    out.save(OUT, "WEBP", quality=90, method=6)
    print(f"{OUT}  {out.size[0]}x{out.size[1]}  {os.path.getsize(OUT)//1024} KB")


if __name__ == "__main__":
    os.makedirs("images", exist_ok=True)
    render()
