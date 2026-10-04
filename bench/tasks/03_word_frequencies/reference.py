import re
from collections import Counter

def word_frequencies(text: str, top: int) -> list[tuple[str, int]]:
    counts = Counter(re.findall(r"[a-z]+", text.lower()))
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:top]
