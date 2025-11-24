import argparse
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import yaml
from ebooklib import epub
from markdown import markdown
from weasyprint import CSS, HTML


@dataclass
class BookConfig:
    """Represents configurable book settings with sensible defaults."""

    title: str = "Generated Book"
    author: str = "md2pdf"
    language: str = "en"
    font_family: str = "DejaVu Serif"
    heading_font_family: str = "DejaVu Sans"
    base_font_size: str = "12pt"
    heading_color: str = "#222222"
    text_color: str = "#111111"
    background_color: str = "#ffffff"
    link_color: str = "#1a73e8"
    code_background_color: str = "#f5f5f5"
    code_border_color: str = "#e0e0e0"
    line_height: float = 1.6
    margin_top: str = "30mm"
    margin_bottom: str = "30mm"
    margin_left: str = "25mm"
    margin_right: str = "25mm"
    page_size: str = "A4"
    chapter_page_break: bool = True
    toc: bool = True
    extra_css: str = ""
    epub_cover: Optional[str] = None
    metadata: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_yaml(cls, path: Optional[Path]) -> "BookConfig":
        if path is None:
            return cls()
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls(**{**cls().__dict__, **data})


@dataclass
class Chapter:
    title: str
    source_path: Path
    html: str


def discover_markdown_files(paths: Iterable[Path]) -> List[Path]:
    files: List[Path] = []
    for path in paths:
        if path.is_file() and path.suffix.lower() in {".md", ".markdown"}:
            files.append(path)
        elif path.is_dir():
            files.extend(sorted(path.rglob("*.md")))
            files.extend(sorted(path.rglob("*.markdown")))
    unique_files: List[Path] = []
    seen = set()
    for file in files:
        if file not in seen:
            seen.add(file)
            unique_files.append(file)
    return unique_files


def clone_repository(url: str, workspace: Path) -> Path:
    repo_dir = workspace / "repo"
    subprocess.run(["git", "clone", "--depth", "1", url, str(repo_dir)], check=True)
    return repo_dir


def load_chapters(paths: Sequence[Path]) -> List[Chapter]:
    chapters: List[Chapter] = []
    for path in paths:
        markdown_text = path.read_text(encoding="utf-8")
        html = markdown(
            markdown_text,
            extensions=[
                "fenced_code",
                "tables",
                "codehilite",
                "toc",
                "attr_list",
            ],
            output_format="html5",
        )
        title = path.stem.replace("_", " ").title()
        chapters.append(Chapter(title=title, source_path=path, html=html))
    return chapters


def build_css(config: BookConfig) -> str:
    return f"""
    @page {{
        size: {config.page_size};
        margin: {config.margin_top} {config.margin_right} {config.margin_bottom} {config.margin_left};
    }}

    body {{
        font-family: '{config.font_family}', serif;
        font-size: {config.base_font_size};
        color: {config.text_color};
        background: {config.background_color};
        line-height: {config.line_height};
    }}

    h1, h2, h3, h4, h5, h6 {{
        font-family: '{config.heading_font_family}', sans-serif;
        color: {config.heading_color};
        margin-top: 1.4em;
    }}

    a {{
        color: {config.link_color};
        text-decoration: none;
    }}

    pre, code {{
        font-family: 'DejaVu Sans Mono', monospace;
    }}

    pre {{
        background: {config.code_background_color};
        border: 1px solid {config.code_border_color};
        padding: 12px;
        border-radius: 4px;
        overflow-x: auto;
    }}

    blockquote {{
        border-left: 4px solid {config.link_color};
        padding-left: 12px;
        color: #555;
        margin-left: 0;
    }}

    .book-title {{
        text-align: center;
        margin-top: 60px;
        font-size: 2.4em;
    }}

    .book-author {{
        text-align: center;
        color: #666;
        margin-bottom: 40px;
    }}

    .chapter {{
        page-break-after: {"always" if config.chapter_page_break else "auto"};
    }}

    .toc-title {{
        font-size: 1.6em;
        font-weight: bold;
        margin-bottom: 10px;
    }}

    .toc-list {{
        list-style: none;
        padding-left: 0;
    }}

    .toc-list li {{
        margin: 6px 0;
    }}
    {config.extra_css}
    """


def render_html(chapters: Sequence[Chapter], config: BookConfig) -> Tuple[str, Path]:
    if not chapters:
        raise ValueError("No Markdown content found to render")

    base_path = chapters[0].source_path.parent
    toc_entries = []
    body_parts = [
        f"<h1 class='book-title'>{config.title}</h1>",
        f"<p class='book-author'>{config.author}</p>",
    ]

    for idx, chapter in enumerate(chapters, start=1):
        anchor = f"chapter-{idx}"
        if config.toc:
            toc_entries.append(f"<li><a href='#{anchor}'>{chapter.title}</a></li>")
        body_parts.append(
            f"<section id='{anchor}' class='chapter'><h2>{chapter.title}</h2>{chapter.html}</section>"
        )

    if config.toc:
        toc_html = "<div class='toc'><div class='toc-title'>Contents</div><ul class='toc-list'>" + "".join(
            toc_entries
        ) + "</ul></div>"
        body_parts.insert(2, toc_html)

    content = "".join(body_parts)
    return f"<html><head><meta charset='utf-8'></head><body>{content}</body></html>", base_path


def convert_to_pdf(html_content: str, css: str, output_path: Path, base_url: Path) -> None:
    try:
        HTML(string=html_content, base_url=str(base_url)).write_pdf(
            stylesheets=[CSS(string=css)], target=str(output_path)
        )
    except OSError as exc:
        hint = (
            "WeasyPrint failed to load required system libraries (for example libgobject-2.0). "
            "Install the GTK/Cairo/Pango stack such as: libgdk-pixbuf2.0-0 libpango-1.0-0 libpangocairo-1.0-0 libcairo2."
        )
        raise RuntimeError(f"Failed to generate PDF: {hint}") from exc


def convert_to_epub(chapters: Sequence[Chapter], config: BookConfig, css: str, output_path: Path) -> None:
    book = epub.EpubBook()
    book.set_title(config.title)
    book.set_language(config.language)
    book.add_author(config.author)

    for key, value in config.metadata.items():
        book.add_metadata("DC", key, value)

    if config.epub_cover and Path(config.epub_cover).exists():
        book.set_cover(Path(config.epub_cover).name, Path(config.epub_cover).read_bytes())

    style = epub.EpubItem(uid="style_nav", file_name="style/nav.css", media_type="text/css", content=css)
    book.add_item(style)

    epub_chapters = []
    for idx, chapter in enumerate(chapters, start=1):
        item = epub.EpubHtml(
            title=chapter.title,
            file_name=f"chap_{idx}.xhtml",
            lang=config.language,
            content=f"<h1>{chapter.title}</h1>{chapter.html}",
        )
        item.add_item(style)
        book.add_item(item)
        epub_chapters.append(item)

    book.toc = tuple(epub_chapters)
    book.spine = ["nav"] + epub_chapters
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    epub.write_epub(str(output_path), book)


def resolve_sources(sources: Sequence[str]) -> Tuple[List[Path], Optional[tempfile.TemporaryDirectory]]:
    temp_dir: Optional[tempfile.TemporaryDirectory] = None
    paths: List[Path] = []

    for src in sources:
        if src.startswith("http://") or src.startswith("https://"):
            temp_dir = tempfile.TemporaryDirectory()
            repo_path = clone_repository(src, Path(temp_dir.name))
            paths.append(repo_path)
        else:
            paths.append(Path(src).expanduser().resolve())

    return paths, temp_dir


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert Markdown sources into PDF or EPUB books.")
    parser.add_argument(
        "sources",
        nargs="+",
        help="Markdown files, directories, or a GitHub repository URL.",
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=None,
        help="Path to YAML configuration file. If omitted defaults are used.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("book"),
        help="Base output path without extension.",
    )
    parser.add_argument(
        "-f",
        "--format",
        choices=["pdf", "epub", "both"],
        default="pdf",
        help="Choose the output format.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    config = BookConfig.from_yaml(args.config)
    source_paths, temp_dir = resolve_sources(args.sources)

    try:
        markdown_files = discover_markdown_files(source_paths)
        if not markdown_files:
            raise SystemExit("No Markdown files found in provided sources")

        chapters = load_chapters(markdown_files)
        css = build_css(config)
        html_content, base_url = render_html(chapters, config)

        if args.format in {"pdf", "both"}:
            pdf_path = args.output.with_suffix(".pdf")
            convert_to_pdf(html_content, css, pdf_path, base_url)
            print(f"PDF created at {pdf_path}")

        if args.format in {"epub", "both"}:
            epub_path = args.output.with_suffix(".epub")
            convert_to_epub(chapters, config, css, epub_path)
            print(f"EPUB created at {epub_path}")

    finally:
        if temp_dir is not None:
            temp_dir.cleanup()


if __name__ == "__main__":
    main()
