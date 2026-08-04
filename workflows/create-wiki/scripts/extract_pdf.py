#!/usr/bin/env python3
"""Extract a project PDF into page-preserving temporary JSON.

With --text, also writes a page-delimited plain-text rendering beside the
JSON so the source can be read directly without a separate flatten step.
"""

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
TEMP_ROOT = Path(tempfile.gettempdir()) / "book-dragon" / "pdf-extractions"
RUNTIME_SCRIPTS = PROJECT_ROOT / "workflows" / "biblio-tools" / "scripts"

sys.path.insert(0, str(RUNTIME_SCRIPTS))
from runtime import ensure_project_runtime  # noqa: E402


def project_pdf(value: str) -> Path:
    """Resolve and validate a project-relative PDF path."""
    candidate = Path(value)
    if candidate.is_absolute():
        raise argparse.ArgumentTypeError("PDF path must be project-root-relative.")

    resolved = (PROJECT_ROOT / candidate).resolve()
    try:
        resolved.relative_to(PROJECT_ROOT)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "PDF path must stay within the project."
        ) from exc

    if resolved.suffix.lower() != ".pdf":
        raise argparse.ArgumentTypeError("Source file must have a .pdf extension.")
    if not resolved.is_file():
        raise argparse.ArgumentTypeError(f"PDF not found: {value}")
    return resolved


def source_digest(path: Path) -> str:
    """Return a stable digest used for the temporary extraction filename."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def output_path(source: Path, digest: str) -> Path:
    """Return the deterministic temporary JSON path for this PDF version."""
    safe_stem = "".join(
        character if character.isalnum() or character in "-_" else "-"
        for character in source.stem
    ).strip("-") or "document"
    return TEMP_ROOT / f"{safe_stem}-{digest[:16]}.json"


def text_output_path(destination: Path) -> Path:
    """Return the flattened-text path that sits beside the extraction JSON."""
    return destination.with_suffix(".txt")


def flatten_pages(payload: dict) -> str:
    """Render the extracted pages as page-delimited plain text."""
    blocks = []
    for page in payload["pages"]:
        text = page["text"].replace("\r\n", "\n").replace("\r", "\n")
        blocks.append(f"===== PAGE {page['page']} =====\n{text}")
    return "\n".join(blocks)


def write_flattened_text(payload: dict, destination: Path) -> None:
    """Write the page-delimited text as UTF-8 with LF line endings."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(flatten_pages(payload))


def load_reader():
    """Import pypdf with a clear project-specific remediation message."""
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError(
            "pypdf is unavailable. Install the Create Wiki dependencies with "
            "the project .venv interpreter; see AGENT-SETUP.md."
        ) from exc
    return PdfReader


def extract(source: Path, destination: Path, digest: str) -> dict:
    """Extract all PDF pages and write page-preserving JSON."""
    PdfReader = load_reader()
    reader = PdfReader(str(source))
    if reader.is_encrypted:
        raise RuntimeError("Encrypted PDFs are not supported.")

    pages = []
    extraction_errors = []
    for page_number, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception as exc:
            text = ""
            extraction_errors.append(
                {"page": page_number, "error": f"{type(exc).__name__}: {exc}"}
            )
        pages.append(
            {
                "page": page_number,
                "characters": len(text),
                "text": text,
            }
        )

    total_characters = sum(page["characters"] for page in pages)
    empty_pages = [page["page"] for page in pages if page["characters"] < 20]
    ocr_required = bool(pages) and total_characters < max(100, len(pages) * 20)
    partial_ocr_warning = bool(empty_pages) and not ocr_required

    payload = {
        "schema_version": 1,
        "source": source.relative_to(PROJECT_ROOT).as_posix(),
        "source_sha256": digest,
        "metadata": {
            str(key): str(value)
            for key, value in (reader.metadata or {}).items()
        },
        "page_count": len(pages),
        "total_characters": total_characters,
        "empty_or_near_empty_pages": empty_pages,
        "ocr_required": ocr_required,
        "partial_ocr_warning": partial_ocr_warning,
        "extraction_errors": extraction_errors,
        "pages": pages,
    }

    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, indent=2))
    return payload


def main() -> None:
    ensure_project_runtime()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "pdf",
        type=project_pdf,
        help="Project-root-relative path to the source PDF.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-extract even when the cached temporary JSON already exists.",
    )
    parser.add_argument(
        "--text",
        action="store_true",
        help=(
            "Also write a page-delimited plain-text rendering beside the JSON, "
            "for reading the source directly."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the extraction target without reading or writing the PDF.",
    )
    args = parser.parse_args()

    digest = source_digest(args.pdf)
    destination = output_path(args.pdf, digest)

    text_destination = text_output_path(destination)

    if args.dry_run:
        preview = {
            "status": "dry-run",
            "source": args.pdf.relative_to(PROJECT_ROOT).as_posix(),
            "output": str(destination),
            "would_extract": not destination.exists() or args.force,
        }
        if args.text:
            preview["text_output"] = str(text_destination)
            preview["would_write_text"] = (
                not text_destination.exists() or args.force
            )
        print(json.dumps(preview))
        return

    if destination.exists() and not args.force:
        payload = json.loads(destination.read_text(encoding="utf-8"))
        status = "cached"
    else:
        payload = extract(args.pdf, destination, digest)
        status = "extracted"

    summary = {
        "status": status,
        "source": payload["source"],
        "output": str(destination),
        "page_count": payload["page_count"],
        "total_characters": payload["total_characters"],
        "ocr_required": payload["ocr_required"],
        "partial_ocr_warning": payload["partial_ocr_warning"],
        "empty_or_near_empty_pages": payload["empty_or_near_empty_pages"],
        "extraction_error_count": len(payload["extraction_errors"]),
    }

    if args.text:
        if text_destination.exists() and not args.force:
            summary["text_status"] = "cached"
        else:
            write_flattened_text(payload, text_destination)
            summary["text_status"] = "written"
        summary["text_output"] = str(text_destination)

    print(json.dumps(summary, ensure_ascii=False))

    if payload["ocr_required"]:
        raise SystemExit(2)
    if payload["extraction_errors"]:
        raise SystemExit(3)


if __name__ == "__main__":
    main()
