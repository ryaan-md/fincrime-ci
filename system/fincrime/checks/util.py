def amt_eq(a, b):
    """Equal to the penny, measured in pence: a gap of 1p or more is a mismatch (185.01 vs 185.00 never matches);
    a gap of at most half a penny is rounding/float noise (0.1 + 0.2, 1250.005) and still matches."""
    return a is not None and b is not None and round(abs(float(a) - float(b)) * 100, 6) <= 0.5

def norm_name(s):
    import re
    s = (s or "").lower().replace("limited", "ltd").replace("&", "and")
    return re.sub(r"[^a-z0-9 ]", "", re.sub(r"\s+", " ", s)).strip()

def fmt_gbp(x):
    return f"£{float(x):,.2f}"
