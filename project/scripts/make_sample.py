"""Generate a batch input file: a JSON array of prompt items.

Writes incrementally so it can also produce very large files (e.g. 500,000
items) for scale testing without holding them in memory.

    python scripts/make_sample.py                          # data/sample_batch.json, 1,000 items
    python scripts/make_sample.py --count 500000 --out data/large_500k.json
"""

import argparse
import json
import random
from pathlib import Path

TOPICS = [
    "photosynthesis", "the French Revolution", "black holes", "compound interest",
    "the TCP handshake", "vaccines", "plate tectonics", "the Roman Empire",
    "machine learning", "climate change", "the stock market", "DNA replication",
    "the printing press", "quantum computing", "supply and demand", "volcanoes",
]

TEMPLATES = [
    "Explain {topic} to a ten-year-old in two sentences.",
    "Give one surprising fact about {topic}.",
    "Summarize {topic} in a single sentence.",
    "Write a haiku about {topic}.",
    "List three key terms related to {topic}, comma separated.",
    "What is a common misconception about {topic}? Answer briefly.",
]

# Rows that are valid JSON but invalid prompt items, to exercise partial-failure handling.
CORRUPT_ROWS = [
    {"id": "corrupt-missing-prompt"},
    {"id": "corrupt-empty-prompt", "prompt": "   "},
    {"id": "corrupt-non-string-prompt", "prompt": 12345},
    "this row is a bare string, not an object",
    None,
]


def make_item(index: int, rng: random.Random) -> dict:
    topic = rng.choice(TOPICS)
    return {
        "id": f"prompt-{index:06d}",
        "prompt": rng.choice(TEMPLATES).format(topic=topic),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--count", type=int, default=1000, help="total number of rows (default: 1000)")
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parent.parent / "data" / "sample_batch.json")
    parser.add_argument("--no-corrupt", action="store_true", help="omit the deliberately invalid rows")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    corrupt = [] if args.no_corrupt else CORRUPT_ROWS[: args.count]
    # Spread the corrupt rows evenly through the file.
    corrupt_positions = {
        (i + 1) * args.count // (len(corrupt) + 1): row for i, row in enumerate(corrupt)
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as f:
        f.write("[\n")
        for index in range(args.count):
            row = corrupt_positions.get(index) if index in corrupt_positions else make_item(index, rng)
            f.write(("  " if index == 0 else ",\n  ") + json.dumps(row))
        f.write("\n]\n")

    print(f"wrote {args.count} rows ({len(corrupt)} deliberately invalid) to {args.out}")


if __name__ == "__main__":
    main()
