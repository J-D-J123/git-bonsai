#!/usr/bin/env python3
"""Grow a living pixel-art bonsai from a GitHub account's contributions.

The tree is not redrawn from a template. Its shape is saved in tree.json and
grows a little each day: every contribution that day is a growth step that can
extend a shoot, sprout a new branch, or add leaves. Where a step goes is
decided by quantum randomness (ANU's quantum random number generator) when a
QRNG_API_KEY is set, so no two trees are alike and nobody can predict yours.
Shoots that would grow out of the frame or into another branch get "pruned"
and turn into denser foliage instead, like a real bonsai.

Branch thickness follows the pipe model (a limb is as thick as the shoots it
feeds), the season comes from how long since your last contribution, blossoms
from your streak, and falling petals from yesterday's count.

Usage:
  GITHUB_TOKEN=... QRNG_API_KEY=... python bonsai.py --login J-D-J123 --out bonsai
  python bonsai.py --demo 1500 --out demo         # fake history, fresh tree
"""
import argparse
import datetime as dt
import hashlib
import json
import math
import os
import random
import sys
import urllib.request

import numpy as np
from PIL import Image

W = H = 128            # pixel-art canvas
SCALE = 2              # GIF is drawn at 256x256
FRAMES = 32
FRAME_MS = 110
ROOT = (64.0, 106.0)
BOUNDS = (6, 8, 122, 100)      # where shoots may grow: xmin, ymin, xmax, ymax
MAX_NODES = 240                # past this, growth goes into leaves (pruning)
STEPS_PER_DAY = 8              # contributions beyond this in one day add no extra growth
MIN_GAP = 3.4                  # shoots closer than this to another branch are pruned

# ---------------------------------------------------------------- GitHub data

QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    createdAt
    contributionsCollection(from: $from, to: $to) {
      contributionCalendar { weeks { contributionDays { date contributionCount } } }
    }
  }
}"""


def gql(token, variables):
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": QUERY, "variables": variables}).encode(),
        headers={"Authorization": f"bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.load(resp)
    if body.get("errors"):
        raise RuntimeError(body["errors"])
    if not body["data"]["user"]:
        raise RuntimeError(f"no GitHub user {variables['login']!r}")
    return body["data"]["user"]


def fetch_days(login, token):
    """Daily counts from account creation through the last complete UTC day."""
    today = dt.datetime.now(dt.timezone.utc).date()
    end = dt.datetime.combine(today, dt.time(), dt.timezone.utc) - dt.timedelta(seconds=1)
    probe = gql(token, {"login": login, "from": (end - dt.timedelta(days=1)).isoformat(),
                        "to": end.isoformat()})
    start = dt.datetime.fromisoformat(probe["createdAt"].replace("Z", "+00:00"))
    days, cur = {}, start
    while cur < end:
        nxt = min(cur + dt.timedelta(days=365), end)
        user = gql(token, {"login": login, "from": cur.isoformat(), "to": nxt.isoformat()})
        for week in user["contributionsCollection"]["contributionCalendar"]["weeks"]:
            for d in week["contributionDays"]:
                day = dt.date.fromisoformat(d["date"])
                if day < today:
                    days[day] = d["contributionCount"]
        cur = nxt
    return days


def demo_days(total, seed=7, idle=0):
    rng = random.Random(seed)
    today = dt.datetime.now(dt.timezone.utc).date()
    span = 900
    days = {today - dt.timedelta(days=i): 0 for i in range(1, span + 1)}
    keys = sorted(days)[: span - idle] if idle else sorted(days)
    for _ in range(total):
        d = keys[min(len(keys) - 1, int(len(keys) * rng.random() ** 0.5))]
        days[d] += 1
    return days


def stats(days):
    counts = [days[d] for d in sorted(days)]
    longest = run = 0
    for c in counts:
        run = run + 1 if c else 0
        longest = max(longest, run)
    current = 0
    for c in reversed(counts):
        if not c:
            break
        current += 1
    since = next((i for i, c in enumerate(reversed(counts)) if c), len(counts))
    return {"total": sum(counts), "recent": sum(counts[-30:]), "streak": current,
            "longest": longest, "since_last": since, "yesterday": counts[-1] if counts else 0}


# ---------------------------------------------------------------- quantum randomness

def quantum_seed():
    """32 bytes from ANU's quantum RNG, or the OS's RNG if no key / service is down."""
    key = os.environ.get("QRNG_API_KEY")
    if key:
        try:
            req = urllib.request.Request(
                "https://api.quantumnumbers.anu.edu.au?length=32&type=uint8",
                headers={"x-api-key": key})
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = json.load(resp)
            if body.get("success"):
                return bytes(body["data"]), "anu-qrng"
            print(f"QRNG refused: {body.get('message')}; using os.urandom", file=sys.stderr)
        except Exception as e:  # noqa: BLE001 - growth must not fail on a flaky API
            print(f"QRNG unavailable ({e}); using os.urandom", file=sys.stderr)
    return os.urandom(32), "os.urandom"


def day_rng(seed, day):
    """One random stream per day, derived from the run's quantum seed."""
    return random.Random(hashlib.sha256(seed + day.isoformat().encode()).digest())


# ---------------------------------------------------------------- growth

def new_tree():
    x, y = ROOT
    return {"version": 3, "last_day": None, "log": [],
            "nodes": [{"x": x, "y": y, "a": -math.pi / 2, "p": -1, "leaf": 0, "o": 0},
                      {"x": x, "y": y - 4, "a": -math.pi / 2, "p": 0, "leaf": 2, "o": 0}]}


def children_count(nodes):
    kids = [0] * len(nodes)
    for n in nodes[1:]:
        kids[n["p"]] += 1
    return kids


def order(n):
    return n.get("o", 1)


def no_droop(a, limit=0.35):
    """Keep shoots from growing downward: at most `limit` radians below horizontal."""
    dx, dy = math.cos(a), math.sin(a)
    if dy > math.sin(limit):
        return math.atan2(math.sin(limit), math.copysign(math.cos(limit), dx))
    return a


def try_add(nodes, parent_i, angle, length, o, seg=1):
    """Add a shoot if it fits in the dome and doesn't crowd another branch."""
    par = nodes[parent_i]
    x = par["x"] + math.cos(angle) * length
    y = par["y"] + math.sin(angle) * length
    xmin, ymin, xmax, ymax = BOUNDS
    # bonsai silhouette: a wide dome above the pot; anything outside gets pruned
    dome = ((x - ROOT[0]) / 56) ** 2 + ((y - 62) / 52) ** 2 <= 1
    if not (xmin <= x <= xmax and ymin <= y <= ymax and dome) or len(nodes) >= MAX_NODES:
        return False
    for i, n in enumerate(nodes):
        if i != parent_i and (n["x"] - x) ** 2 + (n["y"] - y) ** 2 < MIN_GAP ** 2:
            return False
    nodes.append({"x": round(x, 2), "y": round(y, 2), "a": round(angle, 3),
                  "p": parent_i, "leaf": 1, "o": o, "n": seg})
    return True


SEGMENT = {0: (4.0, 6.0), 1: (3.5, 5.0), 2: (2.6, 3.8), 3: (2.2, 3.0)}
TIP_WEIGHT = {0: 4.0, 1: 2.2, 2: 1.5, 3: 1.0}
MAX_SEGS = {0: 99, 1: 5, 2: 3, 3: 2}      # a branch this long forks instead of reaching further


def grow_day(tree, count, rng):
    """Each contribution is one growth step. Real-tree rules: the leader grows
    upward and gently curves, main branches reach outward, twigs fork off them,
    nothing grows downward, and the thinnest shoots carry the leaves."""
    nodes = tree["nodes"]
    for _ in range(min(count, STEPS_PER_DAY)):
        kids = children_count(nodes)
        tips = [i for i in range(1, len(nodes)) if kids[i] == 0]
        # higher shoots get more light; the leader gets the most energy
        weights = [TIP_WEIGHT[min(3, order(nodes[i]))] * (1 + (ROOT[1] - nodes[i]["y"]) / 60)
                   for i in tips]
        roll = rng.random()
        if roll < 0.5:                                   # extend a shoot
            leader = [i for i in tips if order(nodes[i]) == 0]
            # apical dominance: the leader gets a fixed share of the growth
            t = leader[0] if leader and rng.random() < 0.3 else rng.choices(tips, weights)[0]
            n, o = nodes[t], min(3, order(nodes[t]))
            if n.get("n", 1) >= MAX_SEGS[o]:             # long enough: fork into twigs
                if o < 3:
                    side = rng.choice([-1, 1])
                    a = no_droop(n["a"] + side * rng.uniform(0.35, 0.8))
                    if try_add(nodes, t, a, rng.uniform(*SEGMENT[o + 1]), o + 1):
                        continue
                n["leaf"] = min(14, n["leaf"] + 1)
                continue
            for _try in range(3):                        # a blocked shoot tries another angle
                if o == 0:                               # leader: upright, slow S-curve
                    a = n["a"] + rng.gauss(0, 0.22)
                    a += (-math.pi / 2 - a) * 0.18
                else:                                    # branches keep their heading
                    a = no_droop(n["a"] + rng.gauss(0, 0.16) - 0.04 * math.cos(n["a"]) ** 2)
                if try_add(nodes, t, a, rng.uniform(*SEGMENT[o]), o, n.get("n", 1) + 1):
                    break
            else:
                n["leaf"] = min(14, n["leaf"] + 1)       # pruned -> denser foliage
        elif roll < 0.74:                                # sprout a new branch
            spots = [i for i in range(1, len(nodes))
                     if kids[i] < 3 and order(nodes[i]) < 3 and nodes[i]["y"] < ROOT[1] - 18
                     and not (order(nodes[i]) == 0 and kids[i] == 0)]   # never fork the leader's tip
            if not spots:
                continue
            b = rng.choice(spots)
            n, o = nodes[b], min(3, order(nodes[b]) + 1)
            side = rng.choice([-1, 1])
            if o == 1:                                   # main branches reach out, slightly up
                a = -math.pi / 2 + side * rng.uniform(0.95, 1.4)
            else:                                        # twigs fork off at a gentler angle
                a = no_droop(n["a"] + side * rng.uniform(0.45, 0.9))
            if not try_add(nodes, b, a, rng.uniform(*SEGMENT[min(3, o)]), o):
                n["leaf"] = min(14, n["leaf"] + 1)
        else:                                            # just leaves
            t = rng.choices(tips, weights)[0]
            nodes[t]["leaf"] = min(14, nodes[t]["leaf"] + 1)


def advance(tree, days, seed, source):
    last = dt.date.fromisoformat(tree["last_day"]) if tree["last_day"] else None
    pending = [d for d in sorted(days) if last is None or d > last]
    for d in pending:
        if days[d]:
            grow_day(tree, days[d], day_rng(seed, d))
    if pending:
        tree["last_day"] = pending[-1].isoformat()
        tree["log"] = (tree["log"] + [{"through": pending[-1].isoformat(), "days": len(pending),
                                        "source": source, "seed": seed.hex()}])[-60:]
    return len(pending)


# ---------------------------------------------------------------- palettes

SEASONS = {
    "summer": [(28, 66, 38), (46, 104, 50), (74, 148, 62), (124, 192, 88)],
    "fading": [(62, 74, 30), (104, 112, 40), (150, 150, 52), (198, 188, 86)],
    "autumn": [(98, 36, 24), (156, 62, 30), (210, 108, 42), (240, 168, 66)],
    "winter": [(30, 56, 44), (44, 78, 58), (64, 102, 74), (90, 126, 96)],
}
BARK = [(52, 34, 26), (88, 60, 42), (128, 92, 62)]
POT = [(40, 52, 74), (64, 82, 112), (100, 124, 160), (26, 32, 46)]
SOIL, MOSS, MOSS_HI = (58, 42, 32), (70, 112, 52), (110, 160, 78)
PETAL, PETAL_HI = (238, 140, 176), (255, 214, 228)
SNOW = (236, 242, 248)


def season_for(since):
    if since <= 6:
        return "summer"
    if since <= 20:
        return "fading"
    if since <= 59:
        return "autumn"
    return "winter"


# ---------------------------------------------------------------- drawing

class Canvas:
    def __init__(self):
        self.px = np.zeros((H, W, 4), np.uint8)

    def put(self, x, y, color):
        x, y = math.floor(x + 0.5), math.floor(y + 0.5)   # not round(): it rounds .5 to even
        if 0 <= x < W and 0 <= y < H:
            self.px[y, x] = (*color, 255)


def stamp_limb(cv, p0, p1, r0, r1, bend=0.0):
    """Thick, shaded, slightly curved limb from p0 to p1."""
    (x0, y0), (x1, y1) = p0, p1
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    nx, ny = -(y1 - y0), x1 - x0
    norm = math.hypot(nx, ny) or 1
    cx, cy = mx + nx / norm * bend, my + ny / norm * bend
    steps = int(math.hypot(x1 - x0, y1 - y0) * 2) + 2
    for i in range(steps + 1):
        t = i / steps
        x = (1 - t) ** 2 * x0 + 2 * (1 - t) * t * cx + t * t * x1
        y = (1 - t) ** 2 * y0 + 2 * (1 - t) * t * cy + t * t * y1
        r = r0 + (r1 - r0) * t
        ri = int(math.ceil(r))
        for dy in range(-ri, ri + 1):
            for dx in range(-ri, ri + 1):
                if dx * dx + dy * dy <= r * r + 0.3:
                    shade = BARK[2] if dx < -r * 0.35 else BARK[0] if dx > r * 0.35 else BARK[1]
                    cv.put(x + dx, y + dy, shade)


class Pad:
    """A cloud of foliage, with per-leaf positions precomputed."""

    def __init__(self, rng, cx, cy, rx, ry, density):
        self.cx, self.cy, self.rx, self.ry = cx, cy, rx, ry
        self.phase = rng.random() * math.tau
        self.leaves = []
        for dy in range(-int(ry) - 1, int(ry) + 2):
            for dx in range(-int(rx) - 1, int(rx) + 2):
                d = (dx / rx) ** 2 + (dy / ry) ** 2
                if d > 1 - rng.random() * 0.3:
                    continue
                if d > 0.35 and rng.random() > density:
                    continue
                v = dy / ry - dx / rx * 0.35
                shade = 3 if v < -0.45 else 2 if v < 0.1 else 1 if v < 0.6 else 0
                self.leaves.append((dx, dy, shade))


def scene(tree, st):
    """Turn the saved tree plus today's stats into something drawable."""
    nodes = tree["nodes"]
    season = season_for(st["since_last"])
    lush = min(1.0, st["recent"] / 40)
    density = {"summer": 0.5 + 0.45 * lush, "fading": 0.5, "autumn": 0.35, "winter": 0.22}[season]

    # pipe model: a limb is as thick as the number of shoot tips it feeds
    tips_below = [0] * len(nodes)
    kids = children_count(nodes)
    for i in range(len(nodes) - 1, 0, -1):
        if kids[i] == 0:
            tips_below[i] += 1
        tips_below[nodes[i]["p"]] += tips_below[i]
    radius = [min(7.0, 0.35 + 0.33 * t ** 0.62) for t in tips_below]   # twigs ~1px

    wood = Canvas()
    for i in range(1, len(nodes)):
        n, p = nodes[i], nodes[nodes[i]["p"]]
        stamp_limb(wood, (p["x"], p["y"]), (n["x"], n["y"]), radius[i], radius[i] * 0.92)
    r0 = radius[1] if len(nodes) > 1 else 1
    for side in (-1, 1):                                 # nebari: flared surface roots
        stamp_limb(wood, (ROOT[0], ROOT[1] - 1), (ROOT[0] + side * (r0 + 2.5), ROOT[1] + 1),
                   r0 * 0.7, 0.6)

    pads = []
    for i in range(1, len(nodes)):
        leaf = nodes[i]["leaf"]
        if leaf <= 0 or kids[i]:                         # foliage lives on the shoot tips
            continue
        rx = min(8.5, 3.4 + 1.0 * math.sqrt(leaf))
        pads.append(Pad(random.Random(i * 7919), nodes[i]["x"], nodes[i]["y"] - rx * 0.2,
                        rx, rx * 0.45, density))

    rng = random.Random(len(nodes) * 31 + st["streak"])
    blossoms = []
    if season in ("summer", "fading"):
        tops = [(p, dx, dy) for p in pads for dx, dy, sh in p.leaves if dy < 0 and sh >= 2]
        rng.shuffle(tops)
        blossoms = tops[: min(st["streak"], 40)]
    falling = []
    if st["yesterday"] and pads:
        for _ in range(min(st["yesterday"], 12)):
            p = rng.choice(pads)
            dx, dy, _ = rng.choice(p.leaves) if p.leaves else (0, 0, 0)
            falling.append((p.cx + dx, p.cy + dy, rng.random(), rng.random() * math.tau))

    return {"season": season, "wood": wood, "pads": pads, "blossoms": blossoms,
            "falling": falling, "pot_w": min(66, 30 + 5 * r0)}


def draw_pot(cv, pot_w):
    top, bottom = 107, 118
    half = pot_w / 2
    for y in range(top, bottom + 1):
        inset = (y - top) * 0.35
        for x in range(int(W / 2 - half + inset), int(W / 2 + half - inset) + 1):
            edge_l = x <= W / 2 - half + inset + 1
            edge_r = x >= W / 2 + half - inset - 1
            if y == top:
                c = POT[2]
            elif y == bottom:
                c = POT[3]
            elif edge_r:
                c = POT[0]
            elif edge_l:
                c = POT[2]
            else:
                c = POT[1]
            cv.put(x, y, c)
    for fx in (W / 2 - half + 5, W / 2 + half - 7):       # feet
        for x in range(int(fx), int(fx) + 3):
            cv.put(x, bottom + 1, POT[3])
    for x in range(int(W / 2 - half + 1), int(W / 2 + half)):   # soil & moss
        cv.put(x, top - 1, SOIL)
        if (x * 7) % 5 < 2:
            cv.put(x, top - 2, MOSS_HI if x % 3 else MOSS)


def frame(sc, f):
    cv = Canvas()
    draw_pot(cv, sc["pot_w"])
    mask = sc["wood"].px[..., 3] > 0
    cv.px[mask] = sc["wood"].px[mask]
    pal = SEASONS[sc["season"]]
    wave = math.tau * f / FRAMES
    amp = 0.5 if sc["season"] == "winter" else 1.0
    for p in sc["pads"]:
        for dx, dy, sh in p.leaves:
            ox = round(amp * (1 - dy / p.ry) / 2 * math.sin(wave + p.phase))
            snow = sc["season"] == "winter" and dy < -p.ry * 0.45
            cv.put(p.cx + dx + ox, p.cy + dy, SNOW if snow else pal[sh])
    for p, dx, dy in sc["blossoms"]:
        ox = round(amp * (1 - dy / p.ry) / 2 * math.sin(wave + p.phase))
        cv.put(p.cx + dx + ox, p.cy + dy, PETAL_HI if (dx + dy) % 3 == 0 else PETAL)
    fall_color = PETAL if sc["season"] in ("summer", "fading") else pal[2]
    for x0, y0, off, ph in sc["falling"]:
        t = (f / FRAMES + off) % 1.0
        cv.put(x0 + 3 * math.sin(ph + t * math.tau * 1.5) + t * 6, y0 + t * (104 - y0), fall_color)
    return cv.px


def to_gif(frames, path):
    """Shared palette with index 0 transparent, so the GIF works on light and dark pages."""
    colors = {}
    for fr in frames:
        for c in map(tuple, np.unique(fr[fr[..., 3] > 0][:, :3], axis=0)):
            colors.setdefault(c, len(colors) + 1)
    if len(colors) > 255:
        raise RuntimeError("too many colors for a GIF palette")
    palette = [0, 0, 0] + [v for c in colors for v in c]
    palette += [0] * (768 - len(palette))
    images = []
    for fr in frames:
        idx = np.zeros((H, W), np.uint8)
        ys, xs = np.nonzero(fr[..., 3])
        for y, x in zip(ys, xs):
            idx[y, x] = colors[tuple(fr[y, x, :3])]
        im = Image.fromarray(idx, "P")
        im.putpalette(palette)
        images.append(im.resize((W * SCALE, H * SCALE), Image.NEAREST))
    images[0].save(path, save_all=True, append_images=images[1:], duration=FRAME_MS,
                   loop=0, transparency=0, disposal=2, optimize=False)


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--login", default=os.environ.get("GITHUB_REPOSITORY_OWNER", "octocat"))
    ap.add_argument("--out", default="bonsai")
    ap.add_argument("--demo", type=int, metavar="TOTAL", help="fake history; always starts a fresh tree")
    ap.add_argument("--idle", type=int, default=0, help="demo only: days since last contribution")
    args = ap.parse_args()

    if args.demo is not None:
        days = demo_days(args.demo, idle=args.idle)
    else:
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if not token:
            sys.exit("set GITHUB_TOKEN (the workflow's built-in token is enough)")
        days = fetch_days(args.login, token)

    os.makedirs(args.out, exist_ok=True)
    state_path = os.path.join(args.out, "tree.json")
    tree = new_tree()
    if args.demo is None and os.path.exists(state_path):
        with open(state_path) as fh:
            tree = json.load(fh)

    seed, source = quantum_seed()
    grown = advance(tree, days, seed, source)

    st = stats(days)
    sc = scene(tree, st)
    frames = [frame(sc, f) for f in range(FRAMES)]
    to_gif(frames, os.path.join(args.out, "bonsai.gif"))
    Image.fromarray(frames[0], "RGBA").resize((W * SCALE, H * SCALE), Image.NEAREST) \
        .save(os.path.join(args.out, "bonsai.png"))
    with open(state_path, "w") as fh:
        json.dump(tree, fh, separators=(",", ":"))
    print(f"{args.login}: grew {grown} new day(s) with {source}; "
          f"{len(tree['nodes'])} nodes, season={sc['season']}, total={st['total']}")


if __name__ == "__main__":
    main()
