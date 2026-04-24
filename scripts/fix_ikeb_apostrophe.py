import re

SLUG = "ikeb-pisa"
PREVIEW = "750885e1-a2f8-4c80-9afd-b2f20b2947c1"
BASE = "/data/generated-sites/" + SLUG

files = [
    BASE + "/index.html",
    BASE + "/preview/" + PREVIEW + "/index.html",
]

# Check what apostrophe char is used
sample_file = files[0]
txt = open(sample_file).read()
idx = txt.find("italiana")
if idx > 0:
    snippet = txt[idx-5:idx+10]
    print("snippet around italiana:", repr(snippet))
    # Print the char codes
    for c in snippet:
        if ord(c) > 127 or c == "'":
            print("  char:", repr(c), "ord:", ord(c))

def fix_js_apostrophes(txt):
    out_lines = []
    in_script = False
    for line in txt.split('\n'):
        if '<script' in line:
            in_script = True
        if '</script>' in line:
            in_script = False
            out_lines.append(line)
            continue
        if in_script:
            # Fix ASCII apostrophe (0x27)
            fixed = re.sub(r"([a-zA-Z])'([a-zA-Z])", r"\1\\'\2", line)
            # Fix Unicode RIGHT SINGLE QUOTATION MARK U+2019 (curly apostrophe)
            fixed = re.sub(u"([a-zA-Z])\u2019([a-zA-Z])", r"\1\\'\2", fixed)
            # Fix Unicode LEFT SINGLE QUOTATION MARK U+2018
            fixed = re.sub(u"([a-zA-Z])\u2018([a-zA-Z])", r"\1\\'\2", fixed)
            out_lines.append(fixed)
        else:
            out_lines.append(line)
    return '\n'.join(out_lines)

for f in files:
    try:
        txt = open(f).read()
        fixed = fix_js_apostrophes(txt)
        if fixed == txt:
            print(f + " - no changes needed")
        else:
            open(f, 'w').write(fixed)
            print(f + " - patched OK")
    except Exception as e:
        print(f + " - ERROR: " + str(e))
