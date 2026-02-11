import argparse
from pathlib import Path

from indexing import build_index


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a TF-IDF index over extracted text files")
    parser.add_argument("--text-dir", default="data/text", help="Directory containing .txt files")
    parser.add_argument("--index-dir", default="data/index", help="Directory to store index files")
    parser.add_argument("--include-qp", action="store_true", help="Include question papers in the index")
    args = parser.parse_args()

    text_dir = Path(args.text_dir)
    index_dir = Path(args.index_dir)

    if not text_dir.exists():
        raise SystemExit(f"Text directory not found: {text_dir}")

    ms_only = not args.include_qp
    data_path, model_path = build_index(text_dir, index_dir, ms_only=ms_only)
    print(f"Saved chunks: {data_path}")
    print(f"Saved index: {model_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
