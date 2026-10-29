# Lorcana Decks

Turn a Disney Lorcana decklist (a plain `.txt` file) into a printable A4 PDF of
card proxies. Card data and images come from the [Lorcast API](https://lorcast.com).

Each page holds 9 cards (3 × 3) at the real card size of 63 × 88 mm, with grey
guide lines along every card edge and black crop marks in the margins for cutting.

## Requirements

- [uv](https://docs.astral.sh/uv/) (it installs the right Python version, 3.14, for you)
- An internet connection the first time each card is looked up

Install the dependencies once:

```sh
uv sync
```

## Usage

```sh
uv run python generate_pdf_from_list.py <decklist.txt> [-o <output.pdf>]
```

| Argument | Meaning |
| --- | --- |
| `decklist.txt` | Path to the decklist file (required) |
| `-o`, `--output` | Where to write the PDF. Defaults to the decklist's path with a `.pdf` extension |

When you print the PDF, print at **100% / actual size** (not "fit to page"), or
the cards come out the wrong size.

## Building a decklist

A decklist is a UTF-8 text file with one card per line:

```
<quantity> <Card Name> - <Version>
```

- **Quantity** is the number of copies to print.
- **Card Name - Version** is the full card title, exactly as printed, with ` - `
  (space, dash, space) between the name and the version, e.g.
  `Cheshire Cat - Inexplicable`.
- Actions, songs, items and locations have no version, so write just the name,
  e.g. `Besties, Assemble!` or `The Black Cauldron`.
- Names are matched ignoring capitalisation, extra spaces and curly vs. straight
  apostrophes.
- Blank lines and lines that don't start with a number are ignored, so you can
  use them for headings or notes.

The text export of most online deck builders already uses this format, so you can
usually paste it in directly.

### Alt-art tags

By default each card prints in its regular art. To print an alternate-art
version instead, add a tag in parentheses at the end of the line:

| Tag | Prints |
| --- | --- |
| *(no tag)* | The regular set printing |
| `(Enchanted)` | The Enchanted printing |
| `(Epic)` | The Epic printing |
| `(Iconic)` | The Iconic printing |
| `(alt)` | Any alternate-art printing (Enchanted, Epic or Iconic) |

Tags are case-insensitive (`(enchanted)` works too). The same card can appear
on two lines, one tagged and one not, to mix regular and alt-art copies.

If the card exists but not in the art you asked for, the script uses the
regular printing instead and prints a warning:

```
  ART NOT FOUND: Mickey Mouse - Brave Little Tailor (alt), using Legendary printing
```

## Example

[`example_deck.txt`](example_deck.txt) uses regular cards and every kind of
alt-art tag:

```
4 Cheshire Cat - Inexplicable
4 Demona - Scourge of the Wyvern Clan
2 Dumbo - The Flying Elephant
4 Gaston - Superior Archer
3 Hamm - Piggy Bank
4 Stitch - Carefree Surfer
4 Besties, Assemble!
1 The Black Cauldron
2 Elsa - Spirit of Winter (Enchanted)
2 Aladdin - Barreling Through (Epic)
1 Ariel - Ethereal Voice (Iconic)
2 Mickey Mouse - Wayward Sorcerer (alt)
```

Generate it with:

```sh
uv sync
uv run python generate_pdf_from_list.py example_deck.txt
```

Output:

```
Resolved 12/12 unique cards, 33 total, 4 pages -> example_deck.pdf
```

To choose the output file yourself:

```sh
uv run python generate_pdf_from_list.py example_deck.txt -o prints/my_deck.pdf
```

The folder you write to must already exist.

## Cards that aren't found

If a line can't be matched to a card, usually because of a typo or a missing
` - Version`, the PDF gets a grey placeholder with the card's name in its place,
and the summary lists it:

```
Resolved 11/12 unique cards, 33 total, 4 pages -> example_deck.pdf
  NOT FOUND: Cheshire Cat - Inexplicabel
```

Fix the line and run the script again.

## Cache

Card lookups and images are saved in `cache/` (`cache/cards.json` and
`cache/images/`), so runs after the first are fast and work offline for cards
you've already used. "Not found" results are cached too. If a card was missing
because it had only just been released, or you want fresh data, delete
`cache/cards.json` (or the whole `cache/` folder) and run again.
