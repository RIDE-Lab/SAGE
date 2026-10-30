"""Lossless nonoverlapping structural ranges shared by evidence protocols."""
import re

def source_blocks(sources, *, newline_only=False):
    """Select lossless structural blocks, not hundreds of adjacent blank-line IDs.

    Markdown section boundaries preserve conditions together with their claims.
    Plain text retains paragraph granularity. Every ID still resolves to exactly
    one authorized source range; the model cannot reverse or cross endpoints.
    """
    choices = {}
    for source_id, content in sources:
        start = None
        lines = content.split("\n") if newline_only else content.splitlines(keepends=True)
        headings = []
        fence = None
        for index, line in enumerate(lines, 1):
            marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
            if marker:
                token = marker.group(1)
                if fence is None:
                    fence = token
                elif token[0] == fence[0] and len(token) >= len(fence) and not line[marker.end():].strip():
                    fence = None
                continue
            if fence is None and re.match(r"^ {0,3}#{1,6}[ \t]+", line):
                headings.append(index)
        if headings:
            boundaries = sorted({1, *headings, len(lines) + 1})
            for first, stop in zip(boundaries, boundaries[1:]):
                if any(line.strip() for line in lines[first-1:stop-1]):
                    choices[f"p{len(choices)}"] = (source_id, first, stop - 1)
            continue
        for index, line in enumerate([*lines, ""], 1):
            if line.strip():
                if start is None:
                    start = index
            elif start is not None:
                choices[f"p{len(choices)}"] = (source_id, start, index - 1)
                start = None
    return choices

