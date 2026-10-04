def wrap_text(text: str, width: int) -> list[str]:
    if width < 1:
        raise ValueError("width must be positive")
    lines: list[str] = []
    for word in text.split():
        if lines and len(lines[-1]) + 1 + len(word) <= width:
            lines[-1] += " " + word
        else:
            lines.append(word)
    return lines
