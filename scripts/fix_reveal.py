SLUG = "landing-page-multilingua-per-azienda-di-ristrutturazioni-71725609"
PREVIEW = "d036f593-5e30-4ae4-8411-82dc529a19b0"
BASE = f"/data/generated-sites/{SLUG}"

FIX = """    document.addEventListener('DOMContentLoaded', function() {
      var io = new IntersectionObserver(function(entries) {
        entries.forEach(function(e) {
          if (e.isIntersecting) { e.target.classList.add('visible'); io.unobserve(e.target); }
        });
      }, {threshold: 0.12});
      document.querySelectorAll('.reveal').forEach(function(el) { io.observe(el); });
    });"""

OLD = "    document.documentElement.classList.add('js');"
NEW = OLD + "\n" + FIX

files = [
    f"{BASE}/preview/{PREVIEW}/index.html",
    f"{BASE}/index.html",
]

for f in files:
    try:
        txt = open(f).read()
        if "IntersectionObserver" in txt:
            print(f + " - already patched")
            continue
        if OLD not in txt:
            print(f + " - marker not found")
            continue
        open(f, "w").write(txt.replace(OLD, NEW, 1))
        print(f + " - patched OK")
    except Exception as e:
        print(f + " - ERROR: " + str(e))
