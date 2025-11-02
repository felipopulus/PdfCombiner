# PdfCombiner

A simple, fast, and local PDF combiner built with PyQt6. Select images and/or PDFs, visually arrange pages, delete what you don't need, and export a single combined PDF.

- Add multiple files (PDFs and images) via the "Add Files…" button or drag-and-drop from Explorer.
- See per-page thumbnails (PDFs are split into individual pages).
- Reorder pages by dragging; remove a page with the trash icon.
- Click "Combine PDF" to export the arranged pages as a single PDF.

## Supported inputs
- PDF files (`.pdf`)
- Image files (`.png`, `.jpg`, `.jpeg`, `.bmp`, `.tif`, `.tiff`, `.webp`)

## Requirements
- Python 3.13 (recommended)

Install Python dependencies:
```
pip install -r requirements.txt
```

If you prefer an isolated environment (recommended):
```
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Run the app
From the project root:
```
python -m pdfcombiner
```
Or if using the virtual environment:
```
.venv\Scripts\python.exe -m pdfcombiner
```

## How it works
- Thumbnails
  - Image thumbnails are generated with Pillow.
  - PDF thumbnails are rendered via `pypdfium2`.
- Combining
  - PDF pages are copied directly using `pypdf`.
  - Image pages are converted to single-page PDFs (in-memory) and then merged.

## Notes
- Encrypted or password-protected PDFs are not supported.
- Very large or complex PDFs may take longer to thumbnail or merge.
- All operations run locally; no network access is required.

## Project layout
```
PdfCombiner/
├─ pdfcombiner/
│  ├─ __init__.py
│  ├─ __main__.py   # Run with: python -m pdfcombiner
│  └─ app.py        # Main PyQt application
├─ requirements.txt
└─ README.md
```

## License
This project is released under the MIT License. See `LICENSE` for details.
