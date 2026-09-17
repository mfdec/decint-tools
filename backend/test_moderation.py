from app.services.moderation import censor, check_username, clean, normalize

TESTS = [
    # (input, should_be_rejected)
    ("linkdev", False),
    ("cool_user_99", False),
    ("modern_art", False),      # contains "mod" — must NOT be reserved
    ("classic", False),         # contains "ass" as substring — must NOT trip
    ("scunthorpe", True),       # genuinely contains cunt; documents the tradeoff
    ("admin", True),
    ("Admin", True),
    ("AAADMIN", True),
    ("adm1n", True),            # leetspeak
    ("аdmin", True),       # Cyrillic 'а' homoglyph
    ("admin_01", True),
    ("decint_support", True),
    ("sh1t", True),
    ("f.u.c.k", True),
    ("fuuuuck", True),
    ("n1gger", True),
    ("b!tch", True),
    ("PussyCat", True),
    ("ab", True),               # too short
    ("has spaces", True),
    ("way_too_long_username_here_yes", True),
]

print(f"{'input':34} {'folded':20} verdict")
print("-" * 78)
fails = 0
for name, should_reject in TESTS:
    reason = check_username(name)
    rejected = reason is not None
    mark = "ok  " if rejected == should_reject else "FAIL"
    if rejected != should_reject:
        fails += 1
    print(f"{mark} {name!r:29} {normalize(name):20} {reason or 'accepted'}")

print()
print("censor:      ", censor("what the fuck is this sh1t"))
print("censor clean:", censor("a perfectly normal sentence"))
print("clean+staff: ", clean("message from admin about f.u.c.k"))
print()
print("FAILURES:", fails)
