# md2book

A simple Python utility that turns Markdown collections into printable or digital books (PDF and EPUB). It can work with local Markdown files/directories or clone a GitHub repository (for example, a Docsify-powered documentation site) and export the result using configurable typography.

## Features

- Accepts local Markdown files/directories or a GitHub repository URL.
- YAML-based theming for fonts, colors, margins, and page sizing.
- Automatic table of contents and optional page breaks per chapter.
- Generates PDF (via WeasyPrint) and EPUB (via EbookLib) outputs.
- Sensible defaults to minimize required configuration.

## Installation

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> WeasyPrint may require system libraries such as Cairo, Pango, and gdk-pixbuf. Refer to the [WeasyPrint installation guide](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html#installation) if you need to add OS packages on your Linux distribution.

## Usage

```bash
python md2book.py <sources>... [options]
```

- `<sources>`: One or more Markdown files, directories, or a GitHub repository URL (e.g., `https://github.com/DistSysCorp/ddia`). When a repository URL is supplied, it will be cloned into a temporary folder and all Markdown files will be processed.

### Examples

Convert a couple of local Markdown files into a PDF:

```bash
python md2book.py intro.md chapter-*.md -o book
```

Clone a GitHub repository and export both PDF and EPUB with a custom config:

```bash
python md2book.py https://github.com/DistSysCorp/ddia -c my-config.yaml -o ddia --format both
```

Process a directory tree of documentation:

```bash
python md2book.py ./docs --format epub
```

## Configuration

Settings are read from a YAML file. If no file is supplied, defaults are used. See `config.example.yaml` for a full list of options. Key fields include:

- `title`, `author`, `language`
- `font_family`, `heading_font_family`, `base_font_size`, `line_height`
- `text_color`, `background_color`, `link_color`
- `page_size`, `margin_top`, `margin_bottom`, `margin_left`, `margin_right`
- `chapter_page_break` (start each chapter on a new page)
- `toc` (include a generated table of contents)
- `extra_css` for last-mile styling tweaks
- `metadata` (arbitrary EPUB metadata entries)
- `epub_cover` (path to an image for the EPUB cover)

## Output

- `book.pdf` — PDF with CSS-driven styling (page size/margins applied via @page rules).
- `book.epub` — EPUB with embedded CSS and chapters split per Markdown source file.

## Notes

- Markdown conversion relies on the `markdown` package with extensions for fenced code, tables, code highlighting classes, anchors, and attribute lists.
- Local asset paths (images, etc.) will be resolved relative to the first source directory when generating the PDF. Keep assets alongside your Markdown files for portability.

