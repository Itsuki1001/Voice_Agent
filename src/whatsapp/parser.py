import re

# --------------------------------------------------
# PARSING HELPERS (PURE FUNCTIONS)
# --------------------------------------------------

def extract_images(text: str) -> list[str]:
    """Extract IMAGE: <url> entries"""
    return re.findall(r"IMAGE:\s*(https?://[^\s]+)", text)

def extract_link(text: str) -> str | None:
    """Extract LINK: <url> entry"""
    match = re.search(r"LINK:\s*(https?://[^\s]+)", text)
    return match.group(1) if match else None

def clean_text(text: str) -> str:
    """
    Remove IMAGE: and LINK: lines
    Keep only user-visible text
    """
    return "\n".join(
        line for line in text.splitlines()
        if not line.strip().startswith(("IMAGE:", "LINK:"))
    ).strip()
