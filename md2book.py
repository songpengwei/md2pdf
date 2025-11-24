import argparse
import subprocess
import tempfile
import mimetypes
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import markdown
import yaml
from ebooklib import epub
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
    header_enabled: bool = True
    header_title: Optional[str] = None
    header_chapter: bool = True
    footer_enabled: bool = True
    footer_html: str = (
        "<footer style=\"text-align: center; color: #696773;\"><span><a href=\"https://www.qtmuniao.com/\" style=\"color: #79D9CE;\">作者：木鸟杂记</a>&nbsp; ❄ &nbsp; </span><span><a href=\"https://mp.weixin.qq.com/mp/appmsgalbum?__biz=Mzg5NTcxNzY2OQ==&action=getalbum&album_id=2164896217070206977&scene=126&devicetype=iOS15.4&version=18001d33&lang=zh_CN&nettype=WIFI&ascene=59&session_us=gh_80636260f9f9&fontScale=106&wx_header=3\" style=\"color: #FCD765;\">公众号</a>&nbsp; ❄ &nbsp; </span><span><a href=\"https://distsys.cn/\" style=\"color: #F19A97;\">分布式论坛</a>&nbsp; ❄ &nbsp; </span><span><a href=\"https://xiaobot.net/p/system-thinking\" style=\"color: #77AAC2;\">系统技术专栏</a></span></footer>"
    )
    exclude_pages: List[str] = field(default_factory=lambda: ["readme", "sidebar"])

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
    anchor: str


def slugify_title(title: str) -> str:
    """Create a URL-friendly anchor while keeping non-latin characters intact."""

    slug = re.sub(r"\s+", "-", title.strip())
    slug = re.sub(r"[^\w\-\u0080-\uffff]", "", slug)
    return slug.lower() or "section"


def normalize_heading_ids(html: str, level_one: List[Dict[str, str]], anchor: str) -> Tuple[str, List[Tuple[str, str]]]:
    """Ensure predictable heading ids and return the normalized mapping."""

    normalized: List[Tuple[str, str]] = []
    seen_ids: Dict[str, int] = {}
    for idx, token in enumerate(level_one):
        original_id = token.get("id") or slugify_title(token.get("name", ""))
        heading_text = token.get("name", "")
        base_id = anchor if idx == 0 else f"{anchor}-{original_id}"
        suffix = seen_ids.get(base_id, 0)
        seen_ids[base_id] = suffix + 1
        new_id = base_id if suffix == 0 else f"{base_id}-{suffix}"
        html = re.sub(
            rf'id="{re.escape(original_id)}"',
            f'id="{new_id}"',
            html,
            count=1,
        )
        if idx == 0:
            # Mark the first heading for running headers.
            html = re.sub(
                rf"<h1([^>]*id=\"{re.escape(new_id)}\"[^>]*)>",
                r"<h1\1 class=\"chapter-title\">",
                html,
                count=1,
            )
        normalized.append((new_id, heading_text))

    # Ensure the chapter still exposes a title for headers even without h1.
    if not normalized:
        normalized.append((anchor, ""))

    return html, normalized


def collect_heading_links(html: str) -> List[Tuple[str, str]]:
    """Collect normalized level-one headings for TOC linking."""

    headings = []
    for match in re.finditer(r"<h1[^>]*id=\"([^\"]+)\"[^>]*>(.*?)</h1>", html, flags=re.IGNORECASE | re.DOTALL):
        text = re.sub(r"<[^>]+>", "", match.group(2)).strip()
        headings.append((match.group(1), text))
    return headings


def discover_images(
    html: str,
    base_dir: Path,
    added_resources: Dict[Path, epub.EpubItem],
) -> List[Tuple[str, epub.EpubItem]]:
    """Find image sources in HTML and prepare EpubImage resources."""

    images: List[Tuple[str, epub.EpubItem]] = []
    for match in re.finditer(r"<img[^>]+src=\"([^\"]+)\"", html, flags=re.IGNORECASE):
        src = match.group(1)
        if src.startswith("http://") or src.startswith("https://") or src.startswith("data:"):
            continue
        resolved = (base_dir / src).resolve()
        if not resolved.exists():
            continue
        if resolved in added_resources:
            resource = added_resources[resolved]
        else:
            media_type, _ = mimetypes.guess_type(resolved.name)
            file_name = f"images/{len(added_resources) + 1}_{resolved.name}"
            resource = epub.EpubImage(
                uid=file_name,
                file_name=file_name,
                media_type=media_type or "image/jpeg",
                content=resolved.read_bytes(),
            )
            added_resources[resolved] = resource
        images.append((src, resource))
    return images


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


def prioritize_files(files: List[Path]) -> List[Path]:
    """Place preface-like files first while keeping original order otherwise."""

    preface_indices = [idx for idx, path in enumerate(files) if path.stem.lower() == "preface"]
    if not preface_indices:
        return files

    first_preface = preface_indices[0]
    preface_file = files[first_preface]
    remaining = [file for idx, file in enumerate(files) if idx != first_preface]
    return [preface_file] + remaining


def clone_repository(url: str, workspace: Path) -> Path:
    repo_dir = workspace / "repo"
    subprocess.run(["git", "clone", "--depth", "1", url, str(repo_dir)], check=True)
    return repo_dir


def load_chapters(paths: Sequence[Path]) -> List[Chapter]:
    chapters: List[Chapter] = []
    slug_counts: Dict[str, int] = {}
    for path in paths:
        markdown_text = path.read_text(encoding="utf-8")
        md = markdown.Markdown(
            extensions=[
                "fenced_code",
                "tables",
                "codehilite",
                "toc",
                "attr_list",
            ],
            output_format="html5",
        )
        html = md.convert(markdown_text)
        toc_tokens = md.toc_tokens or []
        level_one = [token for token in toc_tokens if token.get("level") == 1]
        title = level_one[0]["name"] if level_one else path.stem.replace("_", " ").title()
        anchor_base = slugify_title(title)
        anchor_suffix = slug_counts.get(anchor_base, 0)
        slug_counts[anchor_base] = anchor_suffix + 1
        anchor = anchor_base if anchor_suffix == 0 else f"{anchor_base}-{anchor_suffix}"

        html, _ = normalize_heading_ids(html, level_one, anchor)

        chapters.append(
            Chapter(
                title=title,
                source_path=path,
                html=html,
                anchor=anchor,
            )
        )
    return chapters


def build_css(config: BookConfig) -> str:
    header_title = config.header_title or config.title
    escaped_header_title = (header_title or "").replace("'", "\\'")

    header_footer_css = ""
    if config.header_enabled:
        header_footer_css += f"""
    h1.chapter-title {{
        string-set: chapter-title content();
    }}

    @page:left {{
        @top-center {{
            content: '{escaped_header_title}';
        }}
    }}

    @page:right {{
        @top-center {{
            content: {"string(chapter-title)" if config.header_chapter else f"'{escaped_header_title}'"};
        }}
    }}
        """

    if config.footer_enabled:
        header_footer_css += """
    .page-footer {
        position: running(page-footer);
    }

    @page {
        @bottom-center {
            content: element(page-footer);
        }
    }
        """

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
    {header_footer_css}
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

    if config.footer_enabled:
        body_parts.append(f"<div class='page-footer'>{config.footer_html}</div>")

    for idx, chapter in enumerate(chapters, start=1):
        anchor = chapter.anchor
        chapter_html = chapter.html
        if "chapter-title" not in chapter_html:
            chapter_html = f"<h1 id='{anchor}' class='chapter-title'>{chapter.title}</h1>" + chapter_html
        if config.toc:
            chapter_entry = f"<li><a href='#{anchor}'>{chapter.title}</a>"
            chapter_headings = collect_heading_links(chapter_html)
            if chapter_headings:
                subitems = "".join(
                    f"<li><a href='#{hid}'>{htext}</a></li>" for hid, htext in chapter_headings
                )
                chapter_entry += f"<ul>{subitems}</ul>"
            chapter_entry += "</li>"
            toc_entries.append(chapter_entry)
        body_parts.append(
            f"<section id='{anchor}' class='chapter'>{chapter_html}</section>"
        )

    if config.toc:
        toc_html = "<div class='toc'><div class='toc-title'>章节目录</div><ul class='toc-list'>" + "".join(
            toc_entries
        ) + "</ul></div>"
        body_parts.insert(2, toc_html)

    content = "".join(body_parts)
    return f"<html><head><meta charset='utf-8'></head><body>{content}</body></html>", base_path


def convert_to_pdf(html_content: str, css: str, output_path: Path, base_url: Path) -> None:
    HTML(string=html_content, base_url=str(base_url)).write_pdf(stylesheets=[CSS(string=css)], target=str(output_path))


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
    added_resources: Dict[Path, epub.EpubItem] = {}
    for idx, chapter in enumerate(chapters, start=1):
        chapter_html = chapter.html
        for original_src, resource in discover_images(chapter_html, chapter.source_path.parent, added_resources):
            book.add_item(resource)
            chapter_html = chapter_html.replace(original_src, resource.file_name)

        item = epub.EpubHtml(
            title=chapter.title,
            file_name=f"chap_{idx}.xhtml",
            lang=config.language,
            content=chapter_html,
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
        if src.startswith("http://") or src.startswith("https://") or src.startswith("git@"):
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

        markdown_files = [
            path
            for path in markdown_files
            if path.stem.lower() not in {page.lower() for page in config.exclude_pages}
        ]
        if not markdown_files:
            raise SystemExit("No Markdown files found after applying exclusions")
        markdown_files = prioritize_files(markdown_files)

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
