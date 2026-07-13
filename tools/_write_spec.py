content = open(
    __file__.replace("_write_spec.py", "../../docs/superpowers/specs/2026-07-11-questmanager-design.md"),
    encoding="utf-8",
).read()
open(
    __file__.replace("_write_spec.py", "../../docs/superpowers/specs/2026-07-11-questmanager-design.md"),
    "w",
    encoding="utf-8",
).write(content)
print("Read back:", repr(content[:200]))
