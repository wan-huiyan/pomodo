"""Annotated screenshots of a change, for the board's Details: an orange ring and a numbered badge on each changed
element, a title above and the numbered notes below. See "Screenshots of robot-vouched work" in README.md.

Usage: uv run --quiet --with playwright --with pillow python3 -I scripts/annotate_shots.py <config.json>

Config (paths may start with ~):
{
  "page": "<a local URL such as http://localhost:8765/, or a saved .html>",
  "out": "<folder for the PNGs, outside the repo>",
  "viewport": [1440, 1000],                       optional
  "wait_for": "<selector that exists once the page has drawn>",   optional
  "hide": ["<selector>", ...],                    optional: anything floating over the change
  "menu": "#rail a[href=\\"#{slide}\\"]",           optional: a left-menu link; {slide} is filled in. Omit for no menu.
  "shots": [
    {"file": "1 New switch.png", "slide": "<id of the section the change is in>", "query": "?a=b",   query optional
     "title": "New switch: ...",
     "menu_note": "In the left menu: ...",         needed when "menu" is set
     "extend": "<selector the picture must reach down to>",   optional
     "from": "<selector the picture starts at>",  optional: on a long section, start here, not at its top
     "click": ["<selector>", ...],                optional: clicked in turn first, e.g. a Details toggle to open
     "notes": [["<selector>", "<one sentence>"], ...]}
  ]
}
Selector prefixes: "head:<sel>" rings an element's text up to its first .more child (a headline without the paragraph
under it); "kids:<sel>" rings the union of its children (a legend whose box is wider than what it shows).
Notes are numbered top to bottom, then left to right, as a reader meets them; the menu note is always 1."""
import json, sys, pathlib
from playwright.sync_api import sync_playwright
from PIL import Image, ImageDraw, ImageFont

CFG = json.loads(pathlib.Path(sys.argv[1]).read_text())
ex = lambda p: pathlib.Path(p).expanduser()
PAGE = CFG['page'] if CFG['page'].startswith('http') else ex(CFG['page']).resolve().as_uri()
OUT = ex(CFG['out']); OUT.mkdir(parents=True, exist_ok=True)
W, H = CFG.get('viewport', [1440, 1000])
ORANGE = '#e8590c'
FONT = next((f for f in ('/System/Library/Fonts/Helvetica.ttc', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
             if pathlib.Path(f).exists()), None)
CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'

JS_MARK = """([sel, n, colour]) => {
  const head = sel.startsWith('head:'), kids = sel.startsWith('kids:'), el = document.querySelector(head || kids ? sel.slice(5) : sel);
  if (!el) return null;
  let r = el.getBoundingClientRect();
  if (kids) { const rs = Array.from(el.children).map(c => c.getBoundingClientRect()).filter(q => q.width);
    r = {left: Math.min(...rs.map(q => q.left)), top: Math.min(...rs.map(q => q.top)), right: Math.max(...rs.map(q => q.right)),
         bottom: Math.max(...rs.map(q => q.bottom))}; r.width = r.right - r.left; r.height = r.bottom - r.top; }
  if (head) { const rg = document.createRange(), more = el.querySelector('.more'); rg.setStart(el, 0);
    if (more) rg.setEndBefore(more); else rg.setEnd(el, el.childNodes.length); r = rg.getBoundingClientRect(); }
  const pad = 6, y = r.top + scrollY - pad;
  // a ring at the page's left edge (the menu) would lose its edge and badge outside the picture: keep both inside
  let x = r.left + scrollX - pad, w = r.width + 2 * pad; const atEdge = x < 4; if (atEdge) { w -= 4 - x; x = 4; }
  const ring = document.createElement('div');
  Object.assign(ring.style, {position: 'absolute', left: x + 'px', top: y + 'px', width: w + 'px',
    height: (r.height + 2 * pad) + 'px', border: '3px solid ' + colour, borderRadius: '14px', zIndex: 9999, pointerEvents: 'none',
    boxSizing: 'border-box'});
  const badge = document.createElement('div');
  badge.textContent = n;
  Object.assign(badge.style, {position: 'absolute', left: (atEdge ? x + w - 14 : x - 14) + 'px', top: (y - 14) + 'px', width: '28px',
    height: '28px', borderRadius: '50%', background: colour, color: '#fff', font: '700 15px/28px Helvetica, Arial, sans-serif',
    textAlign: 'center', zIndex: 10000, pointerEvents: 'none', boxShadow: '0 0 0 2px #fff'});
  document.body.append(ring, badge);
  return [r.left + scrollX, r.top + scrollY, r.right + scrollX, r.bottom + scrollY];
}"""
JS_BOX = """(sel) => { const r = document.querySelector(sel).getBoundingClientRect(); return [r.left + scrollX, r.top + scrollY, r.right + scrollX, r.bottom + scrollY]; }"""


def caption(raw, title, notes, path):
    shot = Image.open(raw)
    sw = shot.width
    font = lambda size, **kw: ImageFont.truetype(FONT, size, **kw) if FONT else ImageFont.load_default(size)  # noqa: E731
    f_title, f_note, f_num = font(44), font(30), font(28, **({'index': 1} if FONT and FONT.endswith('.ttc') else {}))
    margin, line_h, wrap_w = 60, 42, sw - 2 * 60 - 70

    def wrap(text):
        lines, cur = [], ''
        for w in text.split():
            t = (cur + ' ' + w).strip()
            if f_note.getlength(t) > wrap_w and cur:
                lines.append(cur); cur = w
            else:
                cur = t
        return lines + [cur]
    wrapped = [wrap(t) for t in notes]
    top_h = 110
    out = Image.new('RGB', (sw, top_h + shot.height + sum(len(w) * line_h + 26 for w in wrapped) + 50), 'white')
    d = ImageDraw.Draw(out)
    d.text((margin, 36), title, font=f_title, fill='#161616')
    out.paste(shot, (0, top_h))
    d.line([(margin, top_h + shot.height + 10), (sw - margin, top_h + shot.height + 10)], fill='#d0d0d0', width=2)
    y = top_h + shot.height + 40
    for i, lines in enumerate(wrapped, 1):
        d.ellipse([margin, y - 2, margin + 40, y + 38], fill=ORANGE)
        d.text((margin + 20, y + 18), str(i), font=f_num, fill='white', anchor='mm')
        for j, ln in enumerate(lines):
            d.text((margin + 62, y + j * line_h), ln, font=f_note, fill='#161616')
        y += len(lines) * line_h + 26
    out.save(path)


errs = []
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=CHROME) if pathlib.Path(CHROME).exists() else p.chromium.launch()
    for s in CFG['shots']:
        ctx = b.new_context(viewport={'width': W, 'height': H}, device_scale_factor=2, reduced_motion='reduce', color_scheme='light',
                            bypass_csp=True)  # a strict page refuses the injected rings and hiding styles otherwise
        pg = ctx.new_page()
        pg.on('pageerror', lambda e: errs.append(str(e)))
        pg.goto(f'{PAGE}{s.get("query", "")}#{s["slide"]}')
        if CFG.get('wait_for'):
            pg.wait_for_selector(CFG['wait_for'], state='attached')
        pg.wait_for_timeout(900)
        if CFG.get('hide'):
            pg.add_style_tag(content=','.join(CFG['hide']) + '{display:none!important}')
        for sel in s.get('click', []):
            pg.click(sel)
            pg.wait_for_timeout(500)
        notes = [tuple(n) for n in s['notes']]
        tops = [pg.evaluate("(sel) => { const p = sel.startsWith('head:') || sel.startsWith('kids:'), e = document.querySelector(p ? sel.slice(5) : sel); if (!e) return null; const r = e.getBoundingClientRect(); return [Math.round(r.top / 8), r.left]; }", sel) for sel, _ in notes]
        if None in tops:
            sys.exit(f'{s["file"]}: nothing matches {notes[tops.index(None)][0]}')
        notes = [n for _, n in sorted(zip(tops, notes))]
        if CFG.get('menu'):
            notes.insert(0, (CFG['menu'].replace('{slide}', s['slide']), s['menu_note']))
        boxes = []
        for i, (sel, _) in enumerate(notes, 1):
            bx = pg.evaluate(JS_MARK, [sel, str(i), ORANGE])
            if bx is None:
                sys.exit(f'{s["file"]}: nothing matches {sel}')
            boxes.append(bx)
        sl = pg.evaluate(JS_BOX, f'#{s["slide"]}')
        if s.get('extend'):
            boxes.append(pg.evaluate(JS_BOX, s['extend']))
        bottom = max(bx[3] for bx in boxes) + 30
        # side paragraphs cut by the bottom edge read as broken: hide them
        pg.evaluate("([id, bottom]) => document.querySelectorAll('#' + id + ' p').forEach(e => { const r = e.getBoundingClientRect(); if (r.top + scrollY < bottom && r.bottom + scrollY > bottom - 8) e.style.visibility = 'hidden'; })", [s['slide'], bottom])
        start = pg.evaluate(JS_BOX, s['from'])[1] - 20 if s.get('from') else sl[1] + 10
        top = min(start, min(bx[1] for bx in boxes) - 30)
        left = 0 if CFG.get('menu') else max(0, sl[0])
        right = min(W, max([sl[2]] + [bx[2] + 12 for bx in boxes]))  # a ring at the section's right edge stays whole
        clip = {'x': left, 'y': max(0, top), 'width': right - left, 'height': bottom - max(0, top)}
        raw = OUT / ('.raw-' + s['file'])
        pg.screenshot(path=str(raw), clip=clip, full_page=True)  # a clip below the first screen needs the full page
        ctx.close()
        caption(raw, s['title'], [t for _, t in notes], OUT / s['file'])
        raw.unlink()
        print('saved', OUT / s['file'])
    b.close()
print('page errors:', errs or 'none')
