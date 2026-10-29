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

API_BASE_URL = "https://api.lorcast.com/v0"  # Base URL of the Lorcast card API
CACHE_DIR = Path("cache")  # Local folder for cached lookups and images
CARD_WIDTH, CARD_HEIGHT = 63 * mm, 88 * mm  # Standard Lorcana card size, converted to points
CARDS_PER_ROW, ROWS_PER_PAGE = 3, 3  # Cards per row and rows per page (9 cards fit on A4)
ALT_ART_RARITIES = {"Enchanted", "Epic", "Iconic"}  # Rarities that are alternate-art printings
ANY_ALT_ART = "alt"  # Decklist tag meaning "any alternate-art printing"
ART_REQUEST_PATTERN = re.compile(  # Trailing tag like "(Enchanted)", "(Epic)", "(Iconic)" or "(alt)"
    r"\s*\((" + "|".join(sorted(ALT_ART_RARITIES) + [ANY_ALT_ART]) + r")\)\s*$",  # Alternatives built from the rarity set
    re.IGNORECASE,  # Accept any capitalisation of the tag
)  # End of the compiled pattern
CROP_MARK_LENGTH = 5 * mm  # How far crop marks extend into the page margins


def normalize(text):  # Make card names comparable regardless of formatting
    text = text.replace("’", "'").replace("‘", "'")  # Turn curly apostrophes into straight ones
    return re.sub(r"\s+", " ", text).strip().lower()  # Collapse whitespace, trim, and lowercase


def parse_decklist(decklist_path):  # Read a decklist file into (quantity, name, art request) tuples
    decklist_entries = []  # Collected (quantity, full card name, art request) tuples
    for line in Path(decklist_path).read_text(encoding="utf-8").splitlines():  # Go through the file line by line
        line_match = re.match(r"^(\d+)\s+(.+)$", line.strip())  # Match "<qty> <card name> [(art tag)]"
        if not line_match:  # Skip blank or malformed lines
            continue  # Move on to the next line
        quantity, card_text = int(line_match.group(1)), line_match.group(2).strip()  # Split quantity from the rest
        art_match = ART_REQUEST_PATTERN.search(card_text)  # Look for a trailing alt-art tag
        art_request = art_match.group(1).lower() if art_match else None  # Requested art, or None for the regular printing
        full_card_name = card_text[:art_match.start()].strip() if art_match else card_text  # Card name without the tag
        decklist_entries.append((quantity, full_card_name, art_request))  # Store quantity, name and art request
    return decklist_entries  # Return the parsed decklist


def api_get(url, **query_params):  # GET a JSON endpoint from the API
    time.sleep(0.1)  # Lorcast asks for 50-100 ms between requests
    response = requests.get(url, params=query_params, timeout=30)  # Send the request with query parameters
    response.raise_for_status()  # Raise an error on non-2xx responses
    return response.json()  # Decode and return the JSON body


def matches_art_request(card, art_request):  # Check whether a printing satisfies the requested art
    rarity = card.get("rarity")  # Rarity of this printing
    if art_request is None:  # No tag: the regular printing was asked for
        return rarity not in ALT_ART_RARITIES  # Accept only non-alt-art printings
    if art_request == ANY_ALT_ART:  # "(alt)" tag: any alternate art will do
        return rarity in ALT_ART_RARITIES  # Accept any alt-art rarity
    return normalize(rarity or "") == art_request  # Specific tag: rarity must match it exactly


def printing_preference(card):  # Sort key ranking printings of the same card
    rarity = card.get("rarity")  # Rarity of this printing
    return (rarity in ALT_ART_RARITIES, rarity == "Promo")  # False sorts before True, so regular set printings come first


def find_card(full_card_name, art_request, lookup_cache):  # Resolve "Name - Version" (+ art) to a Lorcast card record
    cache_key = normalize(full_card_name)  # Normalized key for the cache
    if art_request:  # Alt-art requests are cached separately from the regular printing
        cache_key += f" ({art_request})"  # Append the tag so both versions can coexist in the cache
    if cache_key in lookup_cache:  # Already looked up on a previous run
        return lookup_cache[cache_key]  # Reuse the cached result (may be None if not found)

    card_name, _, card_version = full_card_name.partition(" - ")  # Split into name and version (version may be empty)
    search_results = api_get(f"{API_BASE_URL}/cards/search", q=f'name:"{card_name}"', unique="prints")["results"]  # Search by name, returning every printing (incl. alt art)
    name_matches = [  # Keep only results whose name and version match exactly
        result for result in search_results  # Iterate over every search result
        if normalize(result["name"]) == normalize(card_name)  # Name must match
        and normalize(result.get("version") or "") == normalize(card_version)  # Version must match (empty for actions/songs)
    ]  # End of the filtered list
    name_matches.sort(key=printing_preference)  # Regular set printings first, then promos, then alt art
    art_matches = [card for card in name_matches if matches_art_request(card, art_request)]  # Printings with the requested art
    # Fall back to the most preferred printing if the requested art doesn't exist
    candidates = art_matches or name_matches  # Prefer exact art matches, otherwise any printing of the card
    best_card = candidates[0] if candidates else None  # Pick the best match, or None if nothing matched
    if best_card and art_request and not art_matches:  # Found the card, but not in the requested art
        print(f"  ART NOT FOUND: {full_card_name} ({art_request}), using {best_card.get('rarity')} printing")  # Warn about the fallback
    lookup_cache[cache_key] = best_card  # Remember the result for future runs
    return best_card  # Return the card record


def card_image(card):  # Return a local PNG path for a card, downloading it if needed
    images_dir = CACHE_DIR / "images"  # Folder holding cached card images
    images_dir.mkdir(parents=True, exist_ok=True)  # Create the folder if it doesn't exist
    image_path = images_dir / f"{card['id']}.png"  # One PNG per card id
    if not image_path.exists():  # Only download images we don't already have
        time.sleep(0.1)  # Be polite to the image server too
        response = requests.get(card["image_uris"]["digital"]["large"], timeout=60)  # Download the large AVIF image
        response.raise_for_status()  # Fail loudly on download errors
        Image.open(io.BytesIO(response.content)).convert("RGB").save(image_path)  # Decode AVIF, drop alpha, save as PNG
    return image_path  # Path to the cached image


def draw_cutting_lines(pdf_canvas, grid_left, grid_bottom):  # Draw guide lines and crop marks for the card grid
    grid_width, grid_height = CARDS_PER_ROW * CARD_WIDTH, ROWS_PER_PAGE * CARD_HEIGHT  # Total grid width and height
    page_width, page_height = A4  # Page dimensions, used to clamp marks to the page

    # Thin guide lines along every card edge
    pdf_canvas.setStrokeColorRGB(0.7, 0.7, 0.7)  # Light grey
    pdf_canvas.setLineWidth(0.25)  # Hairline width
    for column_index in range(CARDS_PER_ROW + 1):  # One vertical line per column boundary
        line_x = grid_left + column_index * CARD_WIDTH  # X position of this boundary
        pdf_canvas.line(line_x, grid_bottom, line_x, grid_bottom + grid_height)  # Vertical line across the full grid height
    for row_index in range(ROWS_PER_PAGE + 1):  # One horizontal line per row boundary
        line_y = grid_bottom + row_index * CARD_HEIGHT  # Y position of this boundary
        pdf_canvas.line(grid_left, line_y, grid_left + grid_width, line_y)  # Horizontal line across the full grid width

    # Darker crop marks in the margins, still visible after the first cut
    pdf_canvas.setStrokeColorRGB(0, 0, 0)  # Black
    pdf_canvas.setLineWidth(0.5)  # Slightly thicker than the guide lines
    for column_index in range(CARDS_PER_ROW + 1):  # Vertical crop marks at each column boundary
        line_x = grid_left + column_index * CARD_WIDTH  # X position of this boundary
        pdf_canvas.line(line_x, max(0, grid_bottom - CROP_MARK_LENGTH), line_x, grid_bottom)  # Mark in the bottom margin
        pdf_canvas.line(line_x, grid_bottom + grid_height, line_x, min(page_height, grid_bottom + grid_height + CROP_MARK_LENGTH))  # Mark in the top margin
    for row_index in range(ROWS_PER_PAGE + 1):  # Horizontal crop marks at each row boundary
        line_y = grid_bottom + row_index * CARD_HEIGHT  # Y position of this boundary
        pdf_canvas.line(max(0, grid_left - CROP_MARK_LENGTH), line_y, grid_left, line_y)  # Mark in the left margin
        pdf_canvas.line(grid_left + grid_width, line_y, min(page_width, grid_left + grid_width + CROP_MARK_LENGTH), line_y)  # Mark in the right margin


def draw_placeholder(pdf_canvas, card_left, card_bottom, label):  # Draw a labelled grey box for a card that wasn't found
    pdf_canvas.setFillColorRGB(0.95, 0.95, 0.95)  # Very light grey fill
    pdf_canvas.rect(card_left, card_bottom, CARD_WIDTH, CARD_HEIGHT, stroke=0, fill=1)  # Filled card-sized rectangle without border
    pdf_canvas.setFillColorRGB(0, 0, 0)  # Switch to black for the text
    pdf_canvas.setFont("Helvetica", 9)  # Small built-in font
    pdf_canvas.drawCentredString(card_left + CARD_WIDTH / 2, card_bottom + CARD_HEIGHT / 2, label)  # Card name centred in the box


def build_pdf(card_slots, output_path):  # Lay out all cards on A4 pages and write the PDF
    page_width, page_height = A4  # Page dimensions in points
    grid_left = (page_width - CARDS_PER_ROW * CARD_WIDTH) / 2  # Left edge of the grid, centred horizontally
    grid_bottom = (page_height - ROWS_PER_PAGE * CARD_HEIGHT) / 2  # Bottom edge of the grid, centred vertically
    cards_per_page = CARDS_PER_ROW * ROWS_PER_PAGE  # Number of cards per page

    pdf_canvas = canvas.Canvas(str(output_path), pagesize=A4)  # Create the PDF canvas
    pdf_canvas.setTitle(Path(output_path).stem)  # PDF metadata title from the file name
    page_count = 0  # Page counter for the summary
    for page_start in range(0, len(card_slots), cards_per_page):  # Step through the cards one page at a time
        for slot_index, (label, image_path) in enumerate(card_slots[page_start:page_start + cards_per_page]):  # Each card on this page
            column, row = slot_index % CARDS_PER_ROW, slot_index // CARDS_PER_ROW  # Grid position of this card
            card_left = grid_left + column * CARD_WIDTH  # Left edge of the card
            card_bottom = grid_bottom + (ROWS_PER_PAGE - 1 - row) * CARD_HEIGHT  # fill from the top (PDF origin is bottom-left)
            if image_path:  # Card image is available
                pdf_canvas.drawImage(ImageReader(str(image_path)), card_left, card_bottom, CARD_WIDTH, CARD_HEIGHT)  # Draw it scaled to card size
            else:  # Card wasn't found
                draw_placeholder(pdf_canvas, card_left, card_bottom, label)  # Draw a placeholder instead
        draw_cutting_lines(pdf_canvas, grid_left, grid_bottom)  # Draw cutting lines on top of the images
        pdf_canvas.showPage()  # Finish this page and start a new one
        page_count += 1  # Count the page
    pdf_canvas.save()  # Write the PDF to disk
    return page_count  # Number of pages written


def main():  # Entry point
    parser = argparse.ArgumentParser(description=__doc__)  # CLI parser using the module docstring as help
    parser.add_argument("decklist", type=Path)  # Path to the decklist txt file
    parser.add_argument("-o", "--output", type=Path)  # Optional output PDF path override
    args = parser.parse_args()  # Parse the command line
    if args.output is None:  # No explicit output given
        args.output = args.decklist.with_suffix(".pdf")  # Same name as the decklist, with a .pdf suffix

    CACHE_DIR.mkdir(exist_ok=True)  # Make sure the cache folder exists
    lookup_cache_path = CACHE_DIR / "cards.json"  # File storing previous card lookups
    lookup_cache = json.loads(lookup_cache_path.read_text()) if lookup_cache_path.exists() else {}  # Load cache or start empty

    decklist_entries = parse_decklist(args.decklist)  # Read the decklist
    card_slots, missing_cards = [], []  # Expanded card list and names that couldn't be resolved
    for quantity, full_card_name, art_request in decklist_entries:  # Each unique card in the decklist
        label = f"{full_card_name} ({art_request})" if art_request else full_card_name  # Display name including any art tag
        card = find_card(full_card_name, art_request, lookup_cache)  # Look up the card record
        if card:  # Found it
            image_path = card_image(card)  # Get (or download) its image
        else:  # Not found
            image_path = None  # No image; a placeholder will be drawn
            missing_cards.append(label)  # Remember it for the summary
        card_slots.extend([(label, image_path)] * quantity)  # Add one entry per copy

    lookup_cache_path.write_text(json.dumps(lookup_cache, indent=2))  # Save the lookup cache for next time

    page_count = build_pdf(card_slots, args.output)  # Generate the PDF
    print(f"Resolved {len(decklist_entries) - len(missing_cards)}/{len(decklist_entries)} unique cards, "  # Summary: resolved count...
          f"{len(card_slots)} total, {page_count} pages -> {args.output}")  # ...total cards, pages, and output path
    for missing_label in missing_cards:  # List any cards that weren't found
        print(f"  NOT FOUND: {missing_label}")  # One line per missing card


if __name__ == "__main__":  # Only run when executed directly, not when imported
    main()  # Run the program
