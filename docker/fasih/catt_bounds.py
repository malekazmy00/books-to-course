"""Bound normalized/number-expanded CATT input before positional embedding."""
import re
CATT_CONTEXT_CHARS = 900

def context_parts(text, limit=CATT_CONTEXT_CHARS):
    if limit < 2:
        raise ValueError("invalid CATT context limit")
    parts = []
    while len(text) > limit:
        window = text[:limit]
        boundaries = [m.end() for m in re.finditer(r"[.!?\u061f\u061b]\s+", window)]
        cut = boundaries[-1] if boundaries else window.rfind(" ") + 1
        if cut <= 0:
            raise ValueError("Arabic token exceeds CATT context capacity")
        parts.append(text[:cut])
        text = text[cut:]
    if text:
        parts.append(text)
    return parts

class BoundedDiacritizer:
    """Bound after number expansion; rejoin before XTTS, adding no pauses."""
    def __init__(self, delegate):
        self.delegate = delegate
    def diacritize_texts(self, texts, batch_size=16, verbose=False):
        output = []
        for text in texts:
            result = []
            for part in context_parts(text):
                value = self.delegate.diacritize_texts([part], batch_size=1, verbose=verbose)
                if len(value) != 1 or not value[0]:
                    raise ValueError("CATT returned incomplete context")
                result.append(value[0])
            output.append("".join(result))
        return output
