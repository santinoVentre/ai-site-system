SLUG = "ikeb-pisa"
BASE = f"/data/generated-sites/{SLUG}"

# The route() function activates pages but reveal elements in hidden pages (display:none)
# are never intersecting when IO observes them, so they never get 'visible'.
# Fix: add visible to all .reveal in the newly activated page on route.

OLD = "if(current){current.classList.add('active');window.scrollTo({top:0,behavior:'smooth'});}"
NEW = "if(current){current.classList.add('active');window.scrollTo({top:0,behavior:'smooth'});current.querySelectorAll('.reveal').forEach(function(el){el.classList.add('visible');});}"

import os

files = []
files.append(f"{BASE}/index.html")

# Also find any previews
preview_dir = f"{BASE}/preview"
try:
    for entry in os.listdir(preview_dir):
        p = f"{preview_dir}/{entry}/index.html"
        if os.path.exists(p):
            files.append(p)
except Exception:
    pass

for f in files:
    try:
        txt = open(f).read()
        if NEW in txt:
            print(f + " - already patched")
            continue
        if OLD not in txt:
            print(f + " - marker not found, trying alternative...")
            # Try with spaces around
            alt_old = "if(current){ current.classList.add('active'); window.scrollTo({top:0,behavior:'smooth'}); }"
            alt_new = alt_old.rstrip('}').rstrip() + " current.querySelectorAll('.reveal').forEach(function(el){el.classList.add('visible');}); }"
            if alt_old in txt:
                open(f, "w").write(txt.replace(alt_old, alt_new, 1))
                print(f + " - patched OK (alt)")
            else:
                # Search for the pattern more loosely
                import re
                m = re.search(r'if\(current\)\{current\.classList\.add\(\'active\'\);[^}]*\}', txt)
                if m:
                    old_match = m.group(0)
                    new_match = old_match[:-1] + "current.querySelectorAll('.reveal').forEach(function(el){el.classList.add('visible');});}"
                    open(f, "w").write(txt.replace(old_match, new_match, 1))
                    print(f + " - patched OK (regex)")
                else:
                    print(f + " - marker not found at all, skipping")
            continue
        open(f, "w").write(txt.replace(OLD, NEW, 1))
        print(f + " - patched OK")
    except Exception as e:
        print(f + " - ERROR: " + str(e))
