"""Build a printable A4 PDF of Lorcana card proxies from a decklist."""  # Module docstring, also used as the CLI help text

import argparse  # Command-line argument parsing
import io  # In-memory byte streams for decoding downloaded images
import json  # Reading/writing the card lookup cache
import re  # Regular expressions for parsing decklist lines
import time  # Sleeping between API requests to respect rate limits
from pathlib import Path  # Convenient filesystem path handling

import requests  # HTTP client for the Lorcast API and image downloads
from PIL import Image  # Pillow, used to decode AVIF images and convert them to PNG
from reportlab.lib.pagesizes import A4  # A4 page size in points (595.27 x 841.89)
from reportlab.lib.units import mm  # Millimetre-to-points conversion factor
from reportlab.lib.utils import ImageReader  # Wraps image files so reportlab can draw them
from reportlab.pdfgen import canvas  # Low-level PDF drawing canvas

API = "https://api.lorcast.com/v0"  # Base URL of the Lorcast card API
CACHE = Path("cache")  # Local folder for cached lookups and images
CARD_W, CARD_H = 63 * mm, 88 * mm  # Standard Lorcana card size, converted to points
COLS, ROWS = 3, 3  # Cards per row and rows per page (9 cards fit on A4)
ALT_ART_RARITIES = {"Enchanted", "Epic", "Iconic"}  # Rarities that are alternate-art printings
CROP_MARK_LEN = 5 * mm  # How far crop marks extend into the page margins


def normalize(text):  # Make card names comparable regardless of formatting
    text = text.replace("’", "'").replace("‘", "'")  # Turn curly apostrophes into straight ones
    return re.sub(r"\s+", " ", text).strip().lower()  # Collapse whitespace, trim, and lowercase


def parse_decklist(path):  # Read a decklist file into (quantity, name) pairs
    entries = []  # Collected (quantity, full card name) tuples
    for line in Path(path).read_text(encoding="utf-8").splitlines():  # Go through the file line by line
        match = re.match(r"^(\d+)\s+(.+)$", line.strip())  # Match "<qty> <card name>"
        if match:  # Skip blank or malformed lines
            entries.append((int(match.group(1)), match.group(2).strip()))  # Store quantity and name
    return entries  # Return the parsed decklist


def api_get(url, **params):  # GET a JSON endpoint from the API
    time.sleep(0.1)  # Lorcast asks for 50-100 ms between requests
    resp = requests.get(url, params=params, timeout=30)  # Send the request with query parameters
    resp.raise_for_status()  # Raise an error on non-2xx responses
    return resp.json()  # Decode and return the JSON body


def find_card(full_name, lookup_cache):  # Resolve "Name - Version" to a Lorcast card record
    key = normalize(full_name)  # Normalized key for the cache
    if key in lookup_cache:  # Already looked up on a previous run
        return lookup_cache[key]  # Reuse the cached result (may be None if not found)

    name, _, version = full_name.partition(" - ")  # Split into name and version (version may be empty)
    results = api_get(f"{API}/cards/search", q=f'name:"{name}"')["results"]  # Search the API by card name
    matches = [  # Keep only results whose name and version match exactly
        c for c in results  # Iterate over every search result
        if normalize(c["name"]) == normalize(name)  # Name must match
        and normalize(c.get("version") or "") == normalize(version)  # Version must match (empty for actions/songs)
    ]  # End of the filtered list
    # Prefer the regular printing over alternate-art versions
    matches.sort(key=lambda c: c.get("rarity") in ALT_ART_RARITIES)  # False sorts before True, so regular art comes first
    card = matches[0] if matches else None  # Pick the best match, or None if nothing matched
    lookup_cache[key] = card  # Remember the result for future runs
    return card  # Return the card record


def card_image(card):  # Return a local PNG path for a card, downloading it if needed
    images = CACHE / "images"  # Folder holding cached card images
    images.mkdir(parents=True, exist_ok=True)  # Create the folder if it doesn't exist
    path = images / f"{card['id']}.png"  # One PNG per card id
    if not path.exists():  # Only download images we don't already have
        time.sleep(0.1)  # Be polite to the image server too
        resp = requests.get(card["image_uris"]["digital"]["large"], timeout=60)  # Download the large AVIF image
        resp.raise_for_status()  # Fail loudly on download errors
        Image.open(io.BytesIO(resp.content)).convert("RGB").save(path)  # Decode AVIF, drop alpha, save as PNG
    return path  # Path to the cached image


def draw_cutting_lines(c, x0, y0):  # Draw guide lines and crop marks for the card grid
    grid_w, grid_h = COLS * CARD_W, ROWS * CARD_H  # Total grid width and height
    page_w, page_h = A4  # Page dimensions, used to clamp marks to the page

    # Thin guide lines along every card edge
    c.setStrokeColorRGB(0.7, 0.7, 0.7)  # Light grey
    c.setLineWidth(0.25)  # Hairline width
    for i in range(COLS + 1):  # One vertical line per column boundary
        x = x0 + i * CARD_W  # X position of this boundary
        c.line(x, y0, x, y0 + grid_h)  # Vertical line across the full grid height
    for j in range(ROWS + 1):  # One horizontal line per row boundary
        y = y0 + j * CARD_H  # Y position of this boundary
        c.line(x0, y, x0 + grid_w, y)  # Horizontal line across the full grid width

    # Darker crop marks in the margins, still visible after the first cut
    c.setStrokeColorRGB(0, 0, 0)  # Black
    c.setLineWidth(0.5)  # Slightly thicker than the guide lines
    for i in range(COLS + 1):  # Vertical crop marks at each column boundary
        x = x0 + i * CARD_W  # X position of this boundary
        c.line(x, max(0, y0 - CROP_MARK_LEN), x, y0)  # Mark in the bottom margin
        c.line(x, y0 + grid_h, x, min(page_h, y0 + grid_h + CROP_MARK_LEN))  # Mark in the top margin
    for j in range(ROWS + 1):  # Horizontal crop marks at each row boundary
        y = y0 + j * CARD_H  # Y position of this boundary
        c.line(max(0, x0 - CROP_MARK_LEN), y, x0, y)  # Mark in the left margin
        c.line(x0 + grid_w, y, min(page_w, x0 + grid_w + CROP_MARK_LEN), y)  # Mark in the right margin


def draw_placeholder(c, x, y, label):  # Draw a labelled grey box for a card that wasn't found
    c.setFillColorRGB(0.95, 0.95, 0.95)  # Very light grey fill
    c.rect(x, y, CARD_W, CARD_H, stroke=0, fill=1)  # Filled card-sized rectangle without border
    c.setFillColorRGB(0, 0, 0)  # Switch to black for the text
    c.setFont("Helvetica", 9)  # Small built-in font
    c.drawCentredString(x + CARD_W / 2, y + CARD_H / 2, label)  # Card name centred in the box


def build_pdf(cards, output):  # Lay out all cards on A4 pages and write the PDF
    page_w, page_h = A4  # Page dimensions in points
    x0 = (page_w - COLS * CARD_W) / 2  # Left edge of the grid, centred horizontally
    y0 = (page_h - ROWS * CARD_H) / 2  # Bottom edge of the grid, centred vertically
    per_page = COLS * ROWS  # Number of cards per page

    c = canvas.Canvas(str(output), pagesize=A4)  # Create the PDF canvas
    c.setTitle(Path(output).stem)  # PDF metadata title from the file name
    pages = 0  # Page counter for the summary
    for start in range(0, len(cards), per_page):  # Step through the cards one page at a time
        for idx, (label, image) in enumerate(cards[start:start + per_page]):  # Each card on this page
            col, row = idx % COLS, idx // COLS  # Grid position of this card
            x = x0 + col * CARD_W  # Left edge of the card
            y = y0 + (ROWS - 1 - row) * CARD_H  # fill from the top (PDF origin is bottom-left)
            if image:  # Card image is available
                c.drawImage(ImageReader(str(image)), x, y, CARD_W, CARD_H)  # Draw it scaled to card size
            else:  # Card wasn't found
                draw_placeholder(c, x, y, label)  # Draw a placeholder instead
        draw_cutting_lines(c, x0, y0)  # Draw cutting lines on top of the images
        c.showPage()  # Finish this page and start a new one
        pages += 1  # Count the page
    c.save()  # Write the PDF to disk
    return pages  # Number of pages written


def main():  # Entry point
    parser = argparse.ArgumentParser(description=__doc__)  # CLI parser using the module docstring as help
    parser.add_argument("decklist", type=Path)  # Path to the decklist txt file
    parser.add_argument("-o", "--output", type=Path)  # Optional output PDF path override
    args = parser.parse_args()  # Parse the command line
    if args.output is None:  # No explicit output given
        args.output = args.decklist.with_suffix(".pdf")  # Same name as the decklist, with a .pdf suffix

    CACHE.mkdir(exist_ok=True)  # Make sure the cache folder exists
    lookup_path = CACHE / "cards.json"  # File storing previous card lookups
    lookup_cache = json.loads(lookup_path.read_text()) if lookup_path.exists() else {}  # Load cache or start empty

    entries = parse_decklist(args.decklist)  # Read the decklist
    cards, missing = [], []  # Expanded card list and names that couldn't be resolved
    for qty, full_name in entries:  # Each unique card in the decklist
        card = find_card(full_name, lookup_cache)  # Look up the card record
        if card:  # Found it
            image = card_image(card)  # Get (or download) its image
        else:  # Not found
            image = None  # No image; a placeholder will be drawn
            missing.append(full_name)  # Remember it for the summary
        cards.extend([(full_name, image)] * qty)  # Add one entry per copy

    lookup_path.write_text(json.dumps(lookup_cache, indent=2))  # Save the lookup cache for next time

    pages = build_pdf(cards, args.output)  # Generate the PDF
    print(f"Resolved {len(entries) - len(missing)}/{len(entries)} unique cards, "  # Summary: resolved count...
          f"{len(cards)} total, {pages} pages -> {args.output}")  # ...total cards, pages, and output path
    for name in missing:  # List any cards that weren't found
        print(f"  NOT FOUND: {name}")  # One line per missing card


if __name__ == "__main__":  # Only run when executed directly, not when imported
    main()  # Run the program
